"""검증 품질 평가(오류 삽입 테스트)의 규칙 테스트. Gemini 와 공식 데이터 API 는 부르지 않는다."""
import pytest

from tribunal.quality import harness, inject
from tribunal.schemas import Report


@pytest.mark.parametrize("value,token", [("1 조 8,367 억 원", "8,367"), ("54.7%", "54.7"), ("3,514,316", "3,514,316"), ("약 70", "70"), ("5", None)])
def test_바꿀_숫자_토큰_고르기(value, token):
    assert inject.main_token(value) == token


@pytest.mark.parametrize("tok", ["90,200", "54.7", "3,514,316", "214", "0.49"])
def test_같은_모양으로_다른_숫자를_만든다(tok):
    new = inject.mutate_token(tok, "seed")
    assert new and new != tok and len(new) == len(tok) and ("," in new) == ("," in tok)
    a, b = float(tok.replace(",", "")), float(new.replace(",", ""))
    assert 0.05 < abs(a - b) / a < 0.2                       # 반올림 오차로 묻히지 않을 만큼 다르다
    assert inject.mutate_token(tok, "seed") == new           # 같은 입력이면 항상 같은 결과


def test_한_사본에서_같은_검증의_숫자는_하나만_바꾼다():
    t = lambda rid, checks, old="1": {"rid": rid, "checks": checks, "page": 1, "old": old}   # noqa: E731
    cases = inject.split_cases([t("R-1", ["K-1"], "11"), t("R-2", ["K-1"], "22"), t("R-3", ["D-1"], "33")])
    assert [[x["rid"] for x in c] for c in cases] == [["R-1", "R-3"], ["R-2"]]


def _report(rows):
    return Report.model_validate({"meta": {"ticker": "000000", "name": "테스트", "report_date": "2026-01-01", "snapshot_date": "2026-01-01", "opinion": "매수"},
                                  "claims": [], "data_rows": rows})


def _mut(rid, new, expect="dart"):
    return {"rid": rid, "item": rid, "page": 1, "old": "x", "new": new, "value_old": "x", "value_new": new, "expect": expect, "check_id": ""}


def test_채점_잡음과_놓친_이유_세_가지():
    rep = _report([{"rid": "R-1", "item": "매출액", "value": "3,795,461", "unit": "백만원", "page": 1},
                   {"rid": "R-2", "item": "자산", "value": "1 조 9,455 억 원", "unit": "원", "page": 1},
                   {"rid": "R-3", "item": "PBR", "value": "0.7배", "unit": "배", "page": 4}])
    checks = [{"check_id": "D-1", "name": "매출", "status": "불일치", "rids": ["R-1"], "expr": ""},
              {"check_id": "D-2", "name": "자산", "status": "검증 불가", "rids": ["R-2"], "expr": ""},
              {"check_id": "K-9", "name": "딴 것", "status": "불일치", "rids": ["R-8"], "expr": "R-9 / R-8"}]
    s = harness.score_case(rep, checks, [_mut("R-1", "3,795,461"), _mut("R-2", "9,455"), _mut("R-3", "0.7", "none"), _mut("R-4", "82,082")])
    got = {m["rid"]: (m["result"], m["why"].split(":")[0]) for m in s["mutations"]}
    assert got == {"R-1": ("잡음", "D-1 매출"), "R-2": ("놓침", "검증했지만 통과"), "R-3": ("놓침", "검증 수단 없음"), "R-4": ("놓침", "뽑지 않음")}
    assert [o["check_id"] for o in s["other_mismatches"]] == ["K-9"]      # 바꾼 숫자와 상관없는 불일치는 따로 센다


def test_식에_들어간_숫자를_바꿔도_그_검증이_잡은_것으로_본다():
    rep = _report([{"rid": "R-1", "item": "현재주가", "value": "82,082 원", "unit": "원", "page": 1}])
    checks = [{"check_id": "K-1", "name": "상승여력", "status": "불일치", "rids": ["R-3"], "expr": "(R-2 / R-1 - 1) * 100"}]
    assert harness.score_case(rep, checks, [_mut("R-1", "82,082", "recompute")])["mutations"][0]["result"] == "잡음"


def test_요약_지표():
    cases = [{"id": "clean", "mutations": [], "other_mismatches": [{"kind": "recompute", "name": "원래 있던 것", "check_id": "K-2"}],
              "checks": [{"status": "일치"}, {"status": "검증 불가"}, {"status": "불일치"}, {"status": "확인 불가"}]},
             {"id": "A", "checks": [], "other_mismatches": [{"kind": "recompute", "name": "원래 있던 것", "check_id": "K-2"}, {"kind": "dart", "name": "새로 생긴 것", "check_id": "D-9"}],
              "mutations": [{"result": "잡음", "expect": "dart", "extracted": True, "why": ""}, {"result": "놓침", "expect": "dart", "extracted": True, "why": "검증했지만 통과: D-1 검증 불가"},
                            {"result": "놓침", "expect": "none", "extracted": True, "why": "검증 수단 없음"}, {"result": "놓침", "expect": "ecos", "extracted": False, "why": "뽑지 않음"}]}]
    cfg = {"provider": "gemini", "providers": {"gemini": {"model": "m", "key_env": "X"}}}
    m = harness.summarize({"name": "s", "report": "r"}, cases, cfg, 2, 5, 10.0)["metrics"]
    assert (m["injected"], m["checkable"], m["caught"]) == (4, 3, 1)
    assert m["detect_rate_checkable"] == pytest.approx(0.333) and m["detect_rate_all"] == 0.25 and m["extracted_rate"] == 0.75
    assert m["missed_why"] == {"검증했지만 통과": 1, "검증 수단 없음": 1, "뽑지 않음": 1}
    assert m["clean_mismatches"] == 1 and m["extra_false_alarms"] == 1        # 원본에도 있던 불일치는 오탐으로 세지 않는다
    assert m["unverifiable_rate"] == 0.5 and m["gemini_calls"] == 2


def test_저장해_둔_결과가_없으면_허락_없이_AI를_부르지_않는다(tmp_path, monkeypatch):
    monkeypatch.setattr(harness, "CACHE", tmp_path)
    cfg = {"provider": "gemini", "extract_max_output_tokens": 1, "providers": {"gemini": {"model": "m", "key_env": "X"}}}
    called = []
    monkeypatch.setattr(harness, "extract", lambda *a, **k: called.append(1))
    rep, stats, how = harness.get_extraction(tmp_path / "x.pdf", [{"page": 1, "text": "글"}], cfg, allow_call=False)
    assert how == "need" and rep is None and not called
