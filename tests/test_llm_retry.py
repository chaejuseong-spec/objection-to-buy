"""AI 호출의 재시도, 모델 바꾸기, 단계 저장(이어서 하기) 테스트. 실제 API는 부르지 않는다."""
import json

import pytest

from tribunal import llm, runner
from tribunal.schemas import JudgeOut


class FakeErr(Exception):
    def __init__(self, code):
        super().__init__(f"fake {code}")
        self.code = code


def _cfg(**kw):
    cfg = {"provider": "gemini", "max_output_tokens": 100, "retries": 3, "retry_base_sec": 0,
           "max_concurrent_calls": 2,
           "providers": {"gemini": {"model": "m-main", "key_env": "X", "fallback_models": ["m-backup"]}}}
    cfg.update(kw)
    return cfg


@pytest.fixture
def calls(monkeypatch):
    """가짜 호출기: 모델별로 정해 둔 오류를 차례로 던지고, 다 쓰면 '{}'를 돌려준다."""
    plan, seen = {}, []

    def fake(system, user, st, max_tokens, timeout):
        seen.append(st["model"])
        queue = plan.get(st["model"], [])
        if queue:
            raise FakeErr(queue.pop(0))
        return "{}"

    monkeypatch.setitem(llm._CALLERS, "gemini", fake)
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    return plan, seen


def test_503_then_success_retries_same_model(calls):
    plan, seen = calls
    plan["m-main"] = [503, 503]
    assert llm._real_call("s", "u", _cfg()) == "{}"
    assert seen == ["m-main"] * 3


def test_backoff_doubles(calls, monkeypatch):
    plan, _ = calls
    waits = []
    monkeypatch.setattr(llm.time, "sleep", waits.append)
    monkeypatch.setattr(llm.random, "uniform", lambda a, b: 0)
    plan["m-main"] = [503, 503, 503]
    llm._real_call("s", "u", _cfg(retry_base_sec=5))
    assert waits == [5, 10, 20]


def test_falls_back_after_retries(calls):
    plan, seen = calls
    plan["m-main"] = [504] * 4            # 처음 1번 + 다시 3번 모두 실패
    assert llm._real_call("s", "u", _cfg()) == "{}"
    assert seen == ["m-main"] * 4 + ["m-backup"]


def test_all_models_fail_gives_friendly_error(calls):
    plan, _ = calls
    plan["m-main"] = [503] * 4
    plan["m-backup"] = [503] * 4
    with pytest.raises(RuntimeError, match="503"):
        llm._real_call("s", "u", _cfg())


def test_key_error_is_not_retried(calls):
    plan, seen = calls
    plan["m-main"] = [403]
    with pytest.raises(RuntimeError, match="API 키"):
        llm._real_call("s", "u", _cfg())
    assert seen == ["m-main"]


def test_step_cache_skips_second_call(tmp_path):
    n = {"calls": 0}

    def ask():
        n["calls"] += 1
        return JudgeOut(key_issues=[], claim_rulings=[])

    runner._step(tmp_path, "judge", JudgeOut, ask, mock=False)
    runner._step(tmp_path, "judge", JudgeOut, ask, mock=False)
    assert n["calls"] == 1
    assert json.loads((tmp_path / "steps" / "judge.json").read_text(encoding="utf-8"))["key_issues"] == []


def test_step_cache_does_not_save_failures(tmp_path):
    assert runner._step(tmp_path, "judge", JudgeOut, lambda: None, mock=False) is None
    assert not (tmp_path / "steps" / "judge.json").exists()


def test_사용_한도_429는_오래_쉰다(calls, monkeypatch):
    plan, _ = calls
    waits = []
    monkeypatch.setattr(llm.time, "sleep", waits.append)
    monkeypatch.setattr(llm.random, "uniform", lambda a, b: 0)
    plan["m-main"] = [429, 429]
    llm._real_call("s", "u", _cfg(retry_base_sec=5))
    assert waits == [30, 60]


def test_예비_모델이_없는_모델이면_원래_오류를_알린다(calls):
    plan, seen = calls
    plan["m-main"] = [503] * 4
    plan["m-backup"] = [404]
    with pytest.raises(RuntimeError, match=r"m-main.*503"):
        llm._real_call("s", "u", _cfg())
    assert seen.count("m-backup") == 1


class DailyErr(FakeErr):
    def __init__(self):
        super().__init__(429)
        self.details = {"error": {"details": [{"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier", "quotaValue": "20"}]},
                                              {"retryDelay": "60169s"}]}}


def test_하루_한도는_다시_시도하지_않고_바로_알린다(monkeypatch):
    seen, waits = [], []

    def fake(system, user, st, max_tokens, timeout):
        seen.append(st["model"])
        raise DailyErr()

    monkeypatch.setitem(llm._CALLERS, "gemini", fake)
    monkeypatch.setattr(llm.time, "sleep", waits.append)
    with pytest.raises(RuntimeError, match=r"무료 등급의 하루 사용 한도\(하루 20번\).*16시간 42분"):
        llm._real_call("s", "u", _cfg())
    assert seen == ["m-main"] and waits == []          # 한 번만 부르고, 쉬지도 예비 모델로 넘어가지도 않는다
