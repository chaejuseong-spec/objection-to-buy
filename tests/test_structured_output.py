"""Claude 답 양식 강제(structured outputs) 테스트. 실제 API는 부르지 않는다 (가짜 Claude 클라이언트)."""
import json
import sys
import types

import pytest

from tribunal import llm
from tribunal.schemas import JudgeOut, JurorOut, ProsecutorOut, Report


def _objects(n):
    if isinstance(n, dict):
        if n.get("type") == "object" or "properties" in n:
            yield n
        for v in n.values():
            yield from _objects(v)
    elif isinstance(n, list):
        for v in n:
            yield from _objects(v)


@pytest.mark.parametrize("model", [ProsecutorOut, JurorOut, JudgeOut])
def test_역할별_답_양식은_API가_받는_모양이다(model):
    js = llm.api_schema(model)
    assert js is not None
    text = json.dumps(js)
    assert '"default"' not in text and '"minimum"' not in text and '"maxLength"' not in text
    assert all(o.get("additionalProperties") is False for o in _objects(js))
    for o in _objects(js):                       # 모든 칸이 '꼭 쓰기'다. 선택 칸은 양식 강제에서 AI가 건너뛴다
        assert set(o.get("required", [])) == set(o.get("properties", {})), o


def test_선택_칸도_꼭_쓰되_없음을_적을_수_있다():
    js = llm.api_schema(JurorOut)
    assert "extra_objection" in js["required"] and {"type": "null"} in js["properties"]["extra_objection"]["anyOf"]
    vote = js["$defs"]["Vote"]
    assert "importance" in vote["required"] and {"type": "null"} in vote["properties"]["importance"]["anyOf"]


def test_title_이라는_칸은_지우지_않는다():
    ki = llm.api_schema(JudgeOut)["$defs"]["KeyIssue"]
    assert "title" in ki["properties"] and "title" in ki["required"]


def test_자유_형식_칸이_있으면_양식을_강제하지_않는다():
    assert llm.api_schema(Report) is None      # 추출 담당의 표(tables)는 칸 이름이 정해져 있지 않다


class _Msg:
    def __init__(self, text):
        self.content = [types.SimpleNamespace(type="text", text=text)]
        self.usage = types.SimpleNamespace(input_tokens=1, output_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0)
        self.stop_reason, self.model = "end_turn", "fake-model"


@pytest.fixture
def fake_claude(monkeypatch):
    """보낸 output_config 를 기록한다. refuse_format=True 면 양식이 붙은 요청을 400으로 거절한다."""
    sent, opt = [], {"refuse_format": False}

    class BadRequestError(Exception):
        status_code = 400

    class _Stream:
        def __init__(self, kw):
            self.kw = kw

        def __enter__(self):
            oc = self.kw.get("output_config") or {}
            sent.append(oc)
            if opt["refuse_format"] and "format" in oc:
                raise BadRequestError("output_config.format: schema not supported")
            return self

        def __exit__(self, *a):
            return False

        def get_final_message(self):
            return _Msg('{"ok": 1}')

    class Anthropic:
        def __init__(self, **kw):
            self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(stream=lambda **kw: _Stream(kw)))

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=Anthropic, BadRequestError=BadRequestError))
    monkeypatch.setattr(llm, "get_key", lambda env="": "x")
    monkeypatch.setattr(llm, "_record_usage", lambda *a, **k: None)
    llm._NO_FORMAT.clear()
    return sent, opt


def _cfg(**kw):
    cfg = {"provider": "claude", "max_output_tokens": 100, "retries": 0, "retry_base_sec": 0, "max_concurrent_calls": 1,
           "providers": {"claude": {"model": "m", "key_env": "X", "effort": "high"}}}
    cfg.update(kw)
    return cfg


def test_Claude에는_답_양식을_같이_보낸다(fake_claude):
    sent, _ = fake_claude
    assert llm._real_call("s", "u", _cfg(), "judge", schema=JudgeOut) == '{"ok": 1}'
    assert sent[0]["effort"] == "high" and sent[0]["format"]["type"] == "json_schema"
    assert sent[0]["format"]["schema"] == llm.api_schema(JudgeOut)


def test_설정으로_끄면_양식을_보내지_않는다(fake_claude):
    sent, _ = fake_claude
    llm._real_call("s", "u", _cfg(structured_output=False), "judge", schema=JudgeOut)
    assert "format" not in sent[0]


def test_API가_양식을_거절하면_양식_없이_다시_부르고_그다음부터는_보내지_않는다(fake_claude):
    sent, opt = fake_claude
    opt["refuse_format"] = True
    assert llm._real_call("s", "u", _cfg(), "judge", schema=JudgeOut) == '{"ok": 1}'
    assert "format" in sent[0] and "format" not in sent[1]
    llm._real_call("s", "u", _cfg(), "judge", schema=JudgeOut)
    assert "format" not in sent[2]


def test_배심원과_모델이_다른_리스크_검토자는_원문을_캐시에_저장하지_않는다():
    # 캐시는 같은 모델끼리만 읽는다. Opus 리스크 검토자가 저장해도 Sonnet 배심원은 못 읽어 저장 비용(1.25배)만 든다
    cfg = _cfg()
    cfg["providers"]["claude"]["roles"] = {"prosecutor": {"model": "opus"}}
    assert llm.cache_reused(cfg, "prosecutor") is False
    assert llm.cache_reused(cfg, "juror_accountant") is True
    cfg["providers"]["claude"]["roles"] = {}
    assert llm.cache_reused(cfg, "prosecutor") is True          # 같은 모델이면 배심원이 원문 부분을 다시 읽는다
    assert "cache_control" not in llm.claude_content("지시", ["원문"], cache=False)[0]


def test_보낼_때_역할에_맞게_캐시_표시를_붙인다(fake_claude, monkeypatch):
    seen = []
    real = llm.claude_content
    monkeypatch.setattr(llm, "claude_content", lambda user, doc=None, cache=True: seen.append(cache) or real(user, doc, cache))
    cfg = _cfg()
    cfg["providers"]["claude"]["roles"] = {"prosecutor": {"model": "opus"}}
    llm._real_call("s", "u", cfg, "prosecutor", doc="원문")
    llm._real_call("s", "u", cfg, "juror_short", doc="원문")
    assert seen == [False, True]
