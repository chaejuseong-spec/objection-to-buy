"""규칙 테스트. API를 부르지 않는다. 실행: pytest"""
import hashlib
import json
from pathlib import Path

from tribunal import rules
from tribunal.runner import run
from tribunal.schemas import Objection, Report, Vote

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo" / "sample_report.json"     # 교육·시연용 가상 리포트 (demo/make_sample_report.py)


def report():
    return Report.model_validate_json(DEMO.read_text(encoding="utf-8"))


def obj(oid="O-1", cid="C-1", ids=("R-15",), kind="사실 오류", added_by=None):
    o = Objection(objection_id=oid, claim_id=cid, doubt_type="논리 연결", text="t", kind=kind,
                  counter_ids=list(ids), added_by=added_by)
    return rules.filter_counter_ids([o], report())[0]


def votes(*vs):
    """('동의', 중요도) / '비동의' / '판단 불가' / None(기권)"""
    out = []
    for v in vs:
        if v is None:
            out.append(None)
        elif isinstance(v, tuple):
            out.append(Vote(objection_id="O-1", vote=v[0], importance=v[1], reason="r"))
        else:
            out.append(Vote(objection_id="O-1", vote=v, reason="r"))
    return out


T, G, U = ("동의", "중"), "비동의", "판단 불가"          # T: 동의(중요도 중)
TS, TL, TN = ("동의", "상"), ("동의", "하"), ("동의", None)   # 동의(상), 동의(하), 동의(중요도 빠짐)


def verdict(o, vs, n):
    return rules.objection_verdict(o, vs, n)["verdict"]


# --- 5명 기준 ---
def test_타당3표_근거있음_수정():
    assert verdict(obj(), votes(T, T, T, G, U), 5) == "수정"


def test_타당3표_근거없음_확인필요():
    assert verdict(obj(ids=()), votes(T, T, T, G, G), 5) == "확인 필요"


def test_과장3표_유지():
    assert verdict(obj(), votes(G, G, G, T, T), 5) == "유지"


def test_표가_갈리면_확인필요():
    assert verdict(obj(), votes(T, T, G, G, None), 5) == "확인 필요"


def test_유효표2개_판정보류():
    assert verdict(obj(), votes(T, T, None, None, None), 5) == "판정 보류"


# --- '확인 불가'는 판단을 맡긴 표: 판단한 표(타당·과장) 3표 이상의 과반으로 정한다 (2026-11-17) ---
def test_확인불가는_판정을_막지_않는다_판단한_3표의_과반():
    assert verdict(obj(), votes(T, T, G, U, U), 5) == "수정"         # 예전 규칙이면 타당 2표라 확인 필요
    assert verdict(obj(), votes(G, G, T, U, U), 5) == "유지"


def test_판단한_표가_3표_미만이면_확인필요():
    """두 명이 리포트 평가를 바꾸지 못하게: 전문가 둘만 판단한 이의는 사람이 본다"""
    assert verdict(obj(), votes(T, T, U, U, U), 5) == "확인 필요"
    assert verdict(obj(), votes(U, U, U, U, U), 5) == "확인 필요"


def test_판단한_표가_같으면_확인필요():
    assert verdict(obj(), votes(T, T, G, G, U), 5) == "확인 필요"


# --- 3명 모드: 같은 규칙이 2표 기준 ---
def test_3명_타당2표_수정():
    assert verdict(obj(), votes(T, T, G), 3) == "수정"


def test_3명_유효표1개_판정보류():
    assert verdict(obj(), votes(T, None, None), 3) == "판정 보류"


# --- 근거 id ---
def test_입력에_없는_근거id는_지운다():
    o = obj(ids=("R-15", "R-999", "http://fake"))
    assert o.counter_ids == ["R-15"] and o.removed_ids == ["R-999", "http://fake"] and not o.no_evidence


def test_근거가_다_지워지면_근거없음():
    assert obj(ids=("R-999",)).no_evidence


# --- 주장별 판정과 리포트 평가 ---
def test_한_주장에_이의2개면_더_엄한_판정():
    cv = rules.claim_verdicts(report(), [
        {"objection_id": "O-1", "claim_id": "C-1", "verdict": "유지"},
        {"objection_id": "O-2", "claim_id": "C-1", "verdict": "수정"}])
    assert cv["C-1"] == "수정" and cv["C-5"] == "유지"


def test_배심원_추가_이의는_확인필요까지만():
    assert verdict(obj(oid="J-x", added_by="회계사"), [], 5) == "확인 필요"


def test_핵심주장_수정이면_재검토_필요():
    cv = {c.claim_id: "유지" for c in report().claims}
    cv["C-2"] = "수정"
    assert rules.report_grade(report(), cv) == "재검토 필요"


def test_중요도_상_수정이면_핵심이_아니어도_재검토_필요():
    cv = {c.claim_id: "유지" for c in report().claims}
    cv["C-3"] = "수정"
    ovs = [{"objection_id": "O-1", "claim_id": "C-3", "verdict": "수정", "importance": "상"}]
    assert rules.report_grade(report(), cv, ovs) == "재검토 필요"
    ovs[0]["importance"] = "중"
    assert rules.report_grade(report(), cv, ovs) == "조건부 통과"


def test_핵심주장_판정보류면_판정_불완전():
    cv = {c.claim_id: "유지" for c in report().claims}
    cv["C-2"] = "판정 보류"
    assert rules.report_grade(report(), cv) == "판정 불완전"


def test_핵심아닌_주장만_수정이면_조건부통과():
    cv = {c.claim_id: "유지" for c in report().claims}
    cv["C-1"] = "수정"
    assert rules.report_grade(report(), cv) == "조건부 통과"


def test_핵심이_아닌_판정보류와_확인필요는_등급을_깎지_않는다():
    cv = {c.claim_id: "유지" for c in report().claims}
    cv["C-3"] = "판정 보류"
    cv["C-1"] = "확인 필요"
    assert rules.report_grade(report(), cv) == "통과"
    cv["C-2"] = "확인 필요"                                          # 핵심 주장의 확인 필요는 조건부 통과
    assert rules.report_grade(report(), cv) == "조건부 통과"


def test_모두_유지면_통과():
    assert rules.report_grade(report(), {c.claim_id: "유지" for c in report().claims}) == "통과"


def test_직접확인할것_최대5개_검증이_먼저():
    objs = [obj(oid=f"O-{i}", cid="C-3", ids=(), kind="해석 차이") for i in range(1, 7)]
    objs.append(obj(oid="O-7", cid="C-2", kind="사실 오류"))
    ovs = [{"objection_id": o.objection_id, "claim_id": o.claim_id,
            "verdict": "수정" if o.objection_id == "O-7" else "확인 필요"} for o in objs]
    items = rules.checklist(report(), objs, ovs)
    assert len(items) == 5 and items[0]["why"] == "O-7" and items[0]["type"] == "검증"


# --- 끝까지 돌리기 (mock) ---
def test_mock_실행_원본_미변경_DEMO_표시(tmp_path):
    before = hashlib.sha256(DEMO.read_bytes()).hexdigest()
    out = run(DEMO, mock=True, n_jurors=5, out_root=tmp_path, run_id="t")
    assert hashlib.sha256(DEMO.read_bytes()).hexdigest() == before
    md = (out / "평가기록지.md").read_text(encoding="utf-8")
    assert "DEMO" in md and "MOCK" in md
    ev = json.loads((out / "evaluation.json").read_text(encoding="utf-8"))
    assert ev["demo"] is True and ev["report_grade"] == "재검토 필요"
    assert "DEMO" in (out / "verdict_form.csv").read_text(encoding="utf-8-sig")
    for f in ["case_file.json", "prosecutor.json", "judge.json", "juror_accountant.json"]:
        assert (out / f).exists()


def test_mock_3명_모드(tmp_path):
    out = run(DEMO, mock=True, n_jurors=3, out_root=tmp_path, run_id="t3")
    ev = json.loads((out / "evaluation.json").read_text(encoding="utf-8"))
    assert ev["majority"] == 2 and not (out / "juror_statistician.json").exists()


def test_배심원_답이_없으면_기권(tmp_path):
    import shutil
    mock = tmp_path / "mock"
    shutil.copytree(ROOT / "mock", mock)
    (mock / "juror_short.json").write_text("형식이 틀린 답", encoding="utf-8")
    out = run(DEMO, mock=True, n_jurors=5, out_root=tmp_path, mock_dir=mock, run_id="a")
    j = json.loads((out / "juror_short.json").read_text(encoding="utf-8"))
    assert j["abstained"] is True
    assert "공매도 투자자: 기권" in (out / "평가기록지.md").read_text(encoding="utf-8")


def test_사건파일은_중립적이고_스냅샷_규칙이_있다():
    from tribunal.case_file import build_case_file
    case = build_case_file(report())
    assert any("2026-03-10" in r for r in case["case_rules"])
    assert any("외부 대조 자료 없음" in r for r in case["case_rules"])


# --- 숫자 검증 (코드가 계산) ---
def test_숫자검증_목표주가_불일치와_나머지_일치():
    from tribunal.number_check import run_checks
    nc = run_checks(report())
    by = {x["check_id"]: x for x in nc["results"]}
    assert by["K-1"]["status"] == "불일치" and 62000 < by["K-1"]["computed"] < 63000
    assert by["K-2"]["status"] == "일치" and by["K-5"]["status"] == "일치"
    assert by["K-8"]["status"] == "불일치"
    assert nc["count"] == {"일치": 6, "불일치": 2, "확인 불가": 0}


def test_숫자검증_읽을수없는_값은_확인불가():
    from tribunal.number_check import parse_number, run_checks
    from tribunal.schemas import Check
    assert parse_number("9,690") == 9690.0 and parse_number("약 70") is None and parse_number("60~65") is None
    r = report()
    r.checks = [Check(check_id="K-x", name="x", kind="recompute", expr="R-15 * 2", stated="R-16")]
    assert run_checks(r)["results"][0]["status"] == "확인 불가"


def test_숫자검증_식은_사칙연산만():
    import pytest
    from tribunal.number_check import safe_eval
    assert safe_eval("(R-01 + 2) * 3", {"R-01": 1.0}) == 9.0
    with pytest.raises(Exception):
        safe_eval("__import__('os')", {})


# --- PDF 추출 결과를 원문과 대조 (원문 PDF가 있을 때만) ---
PDF = ROOT / "demo" / "sample_report.pdf"
needs_pdf = __import__("pytest").mark.skipif(not PDF.exists(), reason="원문 PDF가 없음")


@needs_pdf
def test_추출_확인_데모는_버려지는_것이_없다():
    from tribunal.extract import verify
    clean, stats = verify(report(), PDF)
    assert stats["dropped_total"] == 0 and len(clean.claims) == 5


@needs_pdf
def test_추출_확인_지어낸_주장과_숫자는_버린다():
    from tribunal.extract import verify
    from tribunal.schemas import Check, Claim, DataRow, Evidence
    r = report()
    r.claims.append(Claim(claim_id="C-9", text="동사의 2027년 영업이익은 5조 원을 넘을 것이 확실하다.", page=1,
                          evidence=[Evidence(ref_id="R-99", value="5", unit="조 원", page=1)]))
    r.data_rows.append(DataRow(rid="R-99", item="지어낸 숫자", value="987,654", unit="원", page=5))
    r.checks.append(Check(check_id="K-9", name="x", kind="recompute", expr="R-99 * 2", stated="R-02"))
    r.kpis.append("R-99")
    r.tables[0]["rows"].append({"name": "지어낸 줄", "unit": "십억 원", "kind": "amount", "values": [1, 2, 3, 4, 5, 6, 777777]})
    clean, stats = verify(r, PDF)
    assert "C-9" not in {c.claim_id for c in clean.claims}
    assert "R-99" not in {x.rid for x in clean.data_rows} and "R-99" not in clean.kpis
    assert "K-9" not in {k.check_id for k in clean.checks}
    assert all(row["name"] != "지어낸 줄" for row in clean.tables[0]["rows"])
    assert stats["dropped_total"] == 4


@needs_pdf
def test_PDF_하나로_끝까지_mock(tmp_path):
    from tribunal.runner import run_pdf
    out = run_pdf(PDF, mock=True, n_jurors=5, out_root=tmp_path, run_id="p", demo=True)
    res = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert res["grade"]["value"] == "재검토 필요" and res["extraction"]["dropped_total"] == 0
    assert len(res["marks"]) >= 8 and res["figures"] == []      # 가상 리포트에는 도표가 없다
    assert (out / "claims.json").exists() and (out / "extraction.json").exists()


# --- AI 회사 바꾸기 (claude / gemini). 실제 호출은 하지 않는다 ---
def _cfg(provider):
    from tribunal.runner import load_config
    cfg = load_config()
    cfg["provider"] = provider
    return cfg


def test_회사별_설정을_읽는다():
    from tribunal import llm
    assert llm.settings(_cfg("claude"))["key_env"] == "ANTHROPIC_API_KEY"
    g = llm.settings(_cfg("gemini"))
    assert g["key_env"] == "GEMINI_API_KEY" and g["model"].startswith("gemini")


def test_없는_회사는_멈춘다():
    import pytest
    from tribunal import llm
    with pytest.raises(RuntimeError):
        llm.settings(_cfg("없는회사"))


def test_키가_없으면_어느_키인지_알려준다(monkeypatch):
    import pytest
    from tribunal import llm
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(llm, "ROOT", ROOT / "tests")     # .env 를 읽지 않게
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        llm.get_key("GEMINI_API_KEY")


def test_gemini_로_바꿔도_같은_길을_탄다(monkeypatch):
    from tribunal import llm
    from tribunal.schemas import ProsecutorOut
    seen = {}

    def fake(system, user, st, max_tokens, timeout):
        seen.update(provider=st["provider"], model=st["model"], max_tokens=max_tokens)
        return '{"objections": []}'

    monkeypatch.setitem(llm._CALLERS, "gemini", fake)
    out = llm.call("prosecutor", {"claims": []}, ProsecutorOut, _cfg("gemini"), mock=False)
    assert out is not None and out.objections == []
    assert seen["provider"] == "gemini" and seen["max_tokens"] == 16000     # 8000 x 2


def test_단가가_비면_비용을_지어내지_않는다():
    from tribunal import llm
    assert llm.estimate(1000, 7, _cfg("gemini"))["usd_max"] is None
    assert llm.estimate(1000, 7, _cfg("claude"))["usd_max"] > 0


def test_역할마다_모델과_생각_깊이를_따로_쓴다():
    from tribunal import llm
    cfg = _cfg("claude")
    p = llm.settings(cfg, "prosecutor")
    j = llm.settings(cfg, "juror_accountant")
    # 2026-11-22 기본값: 리스크 검토자 Sonnet high, 배심원 Sonnet medium, 판사 Sonnet low, 추출 Sonnet high, 공시 짝 Haiku
    assert p["model"] == "claude-sonnet-5-5" and p["effort"] == "high" and p["price_out"] == 10.0
    assert j["model"] == "claude-sonnet-5-5" and j["effort"] == "medium" and j["price_out"] == 10.0
    assert llm.settings(cfg, "judge")["model"] == llm.settings(cfg, "extractor")["model"] == "claude-sonnet-5-5"
    assert llm.settings(cfg, "judge")["effort"] == "low" and llm.settings(cfg, "extractor")["effort"] == "high"
    assert llm.settings(cfg, "dart_matcher")["model"] == "claude-haiku-5-5"


def test_단가표에_없는_모델은_비용을_지어내지_않는다():
    from tribunal import llm
    cfg = _cfg("claude")
    cfg["providers"]["claude"] = dict(cfg["providers"]["claude"], roles={"judge": {"model": "없는-모델"}})
    assert llm.settings(cfg, "judge")["price_out"] == 0
    assert llm.estimate(1000, 2, cfg, ["prosecutor", "judge"])["usd_max"] is None


def test_실제로_부른_모델과_역할로_비용을_적는다(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from tribunal import llm, runner
    from tribunal.schemas import JurorOut
    llm.USAGE.clear()

    def fake(system, user, st, max_tokens, timeout, doc=None):
        u = SimpleNamespace(input_tokens=1_000_000, output_tokens=1_000_000,
                            cache_read_input_tokens=0, cache_creation_input_tokens=0)
        llm._record_usage(st, st["model"], u)
        return '{"votes": [], "extra_objection": null}'

    monkeypatch.setitem(llm._CALLERS, "claude", fake)
    llm.call("juror_short", {"a": 1}, JurorOut, _cfg("claude"), mock=False)
    data = runner.save_usage(tmp_path)
    assert data["calls"][0]["model"] == "claude-sonnet-5-5" and data["calls"][0]["usd"] == 12.0   # 2 + 10
    assert data["by_role"]["juror"]["usd"] == 12.0 and (tmp_path / "usage.json").exists()
    llm.call("prosecutor", {"a": 1}, JurorOut, _cfg("claude"), mock=False)
    assert runner.save_usage(tmp_path)["total_usd"] == 24.0                                        # 이어 쓰기: 12 + (2 + 10), 리스크 검토자도 Sonnet


# ---------- 코드 검증 결과를 판정에 반영 ----------
def _code_report():
    from tribunal.schemas import Report
    return Report.model_validate({
        "meta": {"ticker": "1", "name": "x", "report_date": "2026-06-04", "snapshot_date": "2026-06-04", "opinion": "매수"},
        "claims": [{"claim_id": "C-1", "text": "핵심", "is_core": True, "evidence": [{"ref_id": "R-1", "value": ""}]},
                   {"claim_id": "C-2", "text": "보조", "evidence": [{"ref_id": "R-2", "value": ""}]},
                   {"claim_id": "C-3", "text": "무관", "evidence": [{"ref_id": "R-9", "value": ""}]}],
        "data_rows": []})


def _ev(cv):
    return {"claim_verdicts": cv, "checklist": [], "report_grade": "통과"}


def test_공식_데이터와_다르면_수정_리포트_안끼리_안맞으면_확인_필요():
    from tribunal import rules
    checks = [{"check_id": "D-1", "kind": "dart", "status": "불일치", "stated_rid": "R-1", "name": "자산", "reason": "다름"},
              {"check_id": "K-1", "kind": "recompute", "status": "불일치", "stated_rid": "R-5", "expr": "R-2 / R-3", "name": "상승여력", "reason": "다름"}]
    rep = _code_report()
    f = rules.code_findings(rep, checks)
    assert {k: [x["verdict"] for x in v] for k, v in f.items()} == {"C-1": ["수정"]}     # K-1 은 R-5 를 확인한 것: R-2 는 입력값일 뿐
    f = rules.code_findings(rep, checks + [{"check_id": "K-2", "kind": "recompute", "status": "불일치", "stated_rid": "R-2", "name": "비율", "reason": "다름"}])
    assert {k: [x["verdict"] for x in v] for k, v in f.items()} == {"C-1": ["수정"], "C-2": ["확인 필요"]}
    ev = rules.apply_code_findings(rep, _ev({"C-1": "유지", "C-2": "유지", "C-3": "유지"}), f)
    assert ev["claim_verdicts"] == {"C-1": "수정", "C-2": "확인 필요", "C-3": "유지"}
    assert ev["report_grade"] == "재검토 필요"                 # 핵심 주장이 공식 데이터와 달라 수정
    assert ev["checklist"][0]["why"] == "D-1"


def test_짐작한_연결별도_기준이면_확인_필요까지만():
    from tribunal import rules
    checks = [{"check_id": "D-1", "kind": "dart", "status": "불일치", "stated_rid": "R-1", "basis": {"fs": "연결", "how": "가까운 값"}}]
    assert rules.code_findings(_code_report(), checks)["C-1"][0]["verdict"] == "확인 필요"


def test_배심원_판정이_더_엄하면_그대로_둔다():
    from tribunal import rules
    checks = [{"check_id": "K-1", "kind": "recompute", "status": "불일치", "stated_rid": "R-2"}]
    rep = _code_report()
    ev = rules.apply_code_findings(rep, _ev({"C-1": "유지", "C-2": "수정", "C-3": "유지"}), rules.code_findings(rep, checks))
    assert ev["claim_verdicts"]["C-2"] == "수정"


def test_일치나_검증_불가는_판정을_바꾸지_않는다():
    from tribunal import rules
    checks = [{"check_id": "D-1", "kind": "dart", "status": "일치", "stated_rid": "R-1"},
              {"check_id": "D-2", "kind": "dart", "status": "검증 불가", "stated_rid": "R-2"}]
    assert rules.code_findings(_code_report(), checks) == {}


def test_쪽마다_다른_표기는_두_값을_쓴_주장_모두에_반영한다():
    from tribunal import rules
    checks = [{"check_id": "F-9", "kind": "formula", "status": "불일치", "stated_rid": "R-2", "rids": ["R-2", "R-1"], "name": "x 표기가 쪽마다 같은가"}]
    assert set(rules.code_findings(_code_report(), checks)) == {"C-1", "C-2"}


# ---------- 원문 전체에서 이의 찾기 (쪽·인용, 근거 없는 이의 버리기, 안전 상한) ----------
PAGES = [{"page": 1, "text": "요약\n목표주가 287,000원을 제시한다."},
         {"page": 12, "text": "보험본업 이익은 2026년에도\n두 자릿수 성장이 확정적이다."}]


def raw(oid="O-1", cid=None, ids=(), quote="", page=None):
    return Objection(objection_id=oid, claim_id=cid, page=page, quote=quote, doubt_type="가정의 근거",
                     text="t", kind="해석 차이", counter_ids=list(ids))


def test_인용이_원문에_있으면_근거로_인정하고_쪽을_바로잡는다():
    o = rules.filter_counter_ids([raw(quote="두 자릿수 성장이 확정적이다", page=3)], report(), PAGES)[0]
    assert o.quote_found and o.page == 12 and not o.no_evidence      # 줄바꿈·띄어쓰기는 무시하고 찾는다


def test_근거_숫자도_없고_인용도_원문에_없으면_버린다():
    assert rules.filter_counter_ids([raw(quote="원문에 없는 문장을 지어냄")], report(), PAGES) == []


def test_인용이_없어도_근거_숫자가_있으면_남긴다():
    o = rules.filter_counter_ids([raw(ids=("R-15",), quote="원문에 없는 문장을 지어냄")], report(), PAGES)[0]
    assert not o.quote_found and o.counter_ids == ["R-15"] and not o.no_evidence


def test_원문이_없으면_예전처럼_남기고_근거_없음으로_표시한다():
    o = rules.filter_counter_ids([raw()], report())[0]
    assert o.no_evidence


def test_없는_주장_id는_관련_주장_없음으로_바꾼다():
    o = rules.filter_counter_ids([raw(cid="C-99", ids=("R-15",))], report(), PAGES)[0]
    assert o.claim_id is None


def test_안전_상한을_넘는_이의는_버린다():
    many = [raw(oid=f"O-{i}", ids=("R-15",)) for i in range(40)]
    assert len(rules.filter_counter_ids(many, report(), PAGES)) == rules.MAX_OBJECTIONS


def test_주장과_묶이지_않은_이의도_리포트_평가에_들어간다():
    o = rules.filter_counter_ids([raw(quote="두 자릿수 성장이 확정적이다")], report(), PAGES)[0]
    yes = [Vote(objection_id=o.objection_id, vote="동의", importance="중", reason="r")]
    ev = rules.evaluate(report(), [o], {"a": yes, "b": yes, "c": yes}, 3)
    assert ev["objection_verdicts"][0]["verdict"] == "수정"           # 확인된 인용이 근거라 수정까지 간다
    assert set(ev["claim_verdicts"].values()) == {"유지"}              # 어느 주장에도 묶이지 않는다
    assert ev["report_grade"] == "조건부 통과"                          # 핵심 주장이 아니고 중요도 '중'이라 재검토 필요까지는 아니다


def test_원문과_검토_자료는_맨_앞에_캐시_표시를_달아_보낸다():
    from tribunal import llm
    blocks = llm.claude_content("지시", ["원문", "검토 자료"])
    assert [x["text"] for x in blocks] == ["원문", "검토 자료", "지시"]
    assert blocks[0]["cache_control"] == blocks[1]["cache_control"] == {"type": "ephemeral"} and "cache_control" not in blocks[2]
    assert llm.claude_content("지시", "원문")[0]["text"] == "원문"
    assert llm.claude_content("u") == [{"type": "text", "text": "u"}]


def test_원문을_줄_때는_모든_역할의_앞부분이_같다(monkeypatch):
    """캐시가 맞으려면 system(공통 규칙)과 원문이 역할마다 글자까지 같아야 한다."""
    from tribunal import llm
    from tribunal.schemas import JurorOut, ProsecutorOut
    seen = []
    monkeypatch.setitem(llm._CALLERS, "gemini", lambda s, u, st, m, t, doc=None: seen.append((s, doc, u)) or '{"objections": [], "votes": []}')
    cfg = {"provider": "gemini", "max_output_tokens": 10, "retries": 0,
           "providers": {"gemini": {"model": "x", "key_env": "K"}}}
    llm.call("prosecutor", {"a": 1}, ProsecutorOut, cfg, mock=False, document="원문")
    llm.call("juror_accountant", {"a": 1}, JurorOut, cfg, mock=False, document="원문")
    (s1, d1, u1), (s2, d2, u2) = seen
    assert s1 == s2 == llm.load_common() and d1[0] == d2[0] == "원문"
    assert d1[1] == d2[1] and '"a": 1' in d1[1]                    # 같은 검토 자료면 배심원끼리 캐시가 맞는다
    assert "리스크 검토자" in u1 and "회계사" in u2


def test_배심원_지시서에_공통_규칙과_점검_질문이_붙는다():
    """다섯 배심원은 자기 점검 질문 + 공통 판단 순서·표 기준을 받는다. 리스크 검토자에게는 붙지 않는다"""
    from tribunal import llm
    from tribunal.schemas import JUROR_KEYS
    for k in JUROR_KEYS:
        text = llm.load_role(f"juror_{k}")
        assert "1. 공통 점검 질문" in text and "2. 업종별 심화 점검" in text and "판단 순서" in text and '"view"' in text
    assert "판단 순서" not in llm.load_role("prosecutor")


def test_배심원_표의_view는_없어도_된다():
    """예전 결과(view 없음)도 그대로 읽힌다"""
    from tribunal.schemas import Vote
    assert Vote(objection_id="O-1", vote="타당", reason="r").view == ""


def test_배심원에게_코드의_숫자_확인_결과를_짧게_준다():
    from tribunal.runner import brief_checks
    nc = {"results": [{"check_id": "K-1", "name": "ROE", "rids": ["R-1"], "status": "일치", "stated": "8.5%",
                       "computed": 8.5, "reason": "같음", "expr": "R-2/R-3", "pages": [26], "source": {"big": 1}}]}
    assert brief_checks(nc) == [{"check_id": "K-1", "name": "ROE", "rids": ["R-1"], "status": "일치",
                                 "stated": "8.5%", "computed": 8.5, "reason": "같음", "timing": None}]


def test_표의_쉼표_있는_소수도_원문에서_찾는다():
    """'21,996.5'처럼 천 단위 쉼표가 있는 소수를 '21996.5'로만 찾아 표 줄이 버려지던 문제 (2026-11-20)"""
    from tribunal.extract import _value_forms
    assert "21,996.5" in _value_forms(21996.5) and "21996.5" in _value_forms(21996.5)
    assert "(1,234.5)" in _value_forms(-1234.5) and "1,650" in _value_forms(1650)


def test_인용을_문장_전체로_넓히고_짚은_구절은_따로_둔다():
    from tribunal.quote_context import context
    pages = [{"page": 3, "text": "앞 문장이다. 조정 순이익 603.2를 평균\n자본 7,096.5로 나눴다. 뒤 문장이다."},
             {"page": 4, "text": "항목 값\n할인율 33% 시장 52.8%\n합계 100"}]
    c = context(pages, "603.2를 평균 자본", 3)
    assert c == {"before": "조정 순이익", "quote": "603.2를 평균 자본", "after": "7,096.5로 나눴다.", "page": 3}
    t = context(pages, "33%", 4)                       # 마침표 없는 표: 그 줄만 (52.8 의 점은 문장 끝이 아님)
    assert t["before"] + " " + t["quote"] + " " + t["after"] == "할인율 33% 시장 52.8%" or t["page"] == 4
    assert context(pages, "없는 구절", 3) is None


# --- 배심원 추가 이의의 2차 표결 (2026-11-20) ---
def test_배심원_추가_이의는_2차_표결로_수정까지_간다():
    o = obj(oid="J-short", added_by="공매도 투자자")
    assert verdict(o, votes(T, T, T, G), 4) == "수정"                 # 이의를 낸 사람을 뺀 4명
    assert verdict(o, votes(T, T, U, U), 4) == "확인 필요"           # 판단한 표 3표 미만
    assert verdict(o, votes(G, G, G, T), 4) == "유지"
    assert verdict(o, [None, None, None, None], 4) == "확인 필요"    # 2차 표결이 없으면 예전처럼


def test_이의를_낸_배심원의_표는_세지_않는다():
    from tribunal.schemas import Vote
    o = obj(oid="J-short", cid="C-1", added_by="공매도 투자자")
    v = lambda x: [Vote(objection_id="J-short", vote=x[0], importance=x[1], reason="r") if isinstance(x, tuple)
                   else Vote(objection_id="J-short", vote=x, reason="r")]     # noqa: E731
    ev = rules.evaluate(report(), [o], {"accountant": v(T), "industry": v(T), "short": v(T),
                                       "statistician": v(G), "behavioral": v(U)}, 5)
    t = ev["objection_verdicts"][0]
    assert {k: t["tally"][k] for k in ("동의", "비동의", "판단 불가", "기권")} == {"동의": 2, "비동의": 1, "판단 불가": 1, "기권": 0}
    assert t["round2"] and t["verdict"] == "수정"


def test_mock_배심원_추가_이의가_2차_표결을_받는다(tmp_path):
    out = run(DEMO, mock=True, n_jurors=5, out_root=tmp_path, run_id="r2")
    ev = json.loads((out / "evaluation.json").read_text(encoding="utf-8"))
    j = next(x for x in ev["objection_verdicts"] if x["objection_id"] == "J-behavioral")
    assert j["round2"] and j["tally"]["동의"] == 3 and j["verdict"] == "수정"
    res = json.loads((out / "juror_round2.json").read_text(encoding="utf-8"))
    assert res["objections"] == ["J-behavioral"] and "행동경제학자" not in res["votes"]
    md = (out / "평가기록지.md").read_text(encoding="utf-8")
    assert "2차 표결, 이의를 낸 배심원 제외" in md



# --- 표결 판정 규칙 (docs/판정_규칙.md 3~4장, 2026-11-20) ---
def test_비동의가_과반이면_유지_이의_불인정():
    r = rules.objection_verdict(obj(), votes(G, G, G, T, U), 5)
    assert r["verdict"] == "유지" and r["reason"] == "이의 불인정"


def test_동의가_과반인데_중요도_하가_과반이면_유지_경미한_지적():
    r = rules.objection_verdict(obj(), votes(TL, TL, TS, G, U), 5)
    assert r["verdict"] == "유지" and r["reason"] == "경미한 지적" and r["importance_split"]


def test_동의가_과반이고_상중이_과반이면_수정_중요도는_다수():
    r = rules.objection_verdict(obj(), votes(TS, TS, T, G, U), 5)
    assert r["verdict"] == "수정" and r["importance"] == "상"
    assert rules.objection_verdict(obj(), votes(TS, T, T, G, U), 5)["importance"] == "중"


def test_중요도가_갈리면_확인필요():
    r = rules.objection_verdict(obj(), votes(TS, TL, T, TL, G), 5)       # 상·중 2, 하 2
    assert r["verdict"] == "확인 필요" and r["reason"] == "중요도 의견 갈림"


def test_빠진_중요도는_미확인으로_세고_확인된_것만으로_과반이면_적용():
    r = rules.objection_verdict(obj(), votes(TS, TS, TS, TN, G), 5)      # 동의 4 중 상 3 -> 과반
    assert r["verdict"] == "수정" and r["tally"]["중요도"]["미확인"] == 1
    r = rules.objection_verdict(obj(), votes(TS, TN, TN, G, U), 5)       # 동의 3 중 상 1 -> 과반 아님
    assert r["verdict"] == "확인 필요"


def test_확인_사항은_같은_판정이면_중요도가_높은_것이_먼저():
    objs = [obj(oid="O-1", cid="C-3", ids=(), kind="해석 차이"), obj(oid="O-2", cid="C-3", ids=(), kind="해석 차이")]
    ovs = [{"objection_id": "O-1", "claim_id": "C-3", "verdict": "확인 필요", "importance": "하"},
           {"objection_id": "O-2", "claim_id": "C-3", "verdict": "확인 필요", "importance": "상"}]
    assert [i["why"] for i in rules.checklist(report(), objs, ovs)] == ["O-2", "O-1"]


def test_mock_판사가_모든_지적에_판정_근거를_쓴다(tmp_path):
    """주장에 묶인 지적(O-1)과 묶이지 않은 지적(O-6) 모두 판사 판정 근거를 받는다 (2026-11-20)."""
    out = run(DEMO, mock=True, n_jurors=5, out_root=tmp_path, run_id="or")
    md = (out / "평가기록지.md").read_text(encoding="utf-8")
    assert "판사(이 지적): (mock) 경쟁사 증설 철회" in md and "판사(이 지적): (mock) 이 지적(O-1)" in md
    from tribunal.schemas import JudgeOut
    assert JudgeOut.model_validate({"key_issues": [], "claim_rulings": []}).objection_rulings == []   # 예전 판사 답도 읽는다


# ---------- 결론 주장 따라가기 (docs/판정_규칙.md 9장 (다), 2026-11-21) ----------
def _dep_report():
    from tribunal.schemas import Claim, Report
    return Report.model_construct(meta=None, data_rows=[], claims=[
        Claim(claim_id="C-1", text="가상지주의 목표주가로 10,000원을 제시한다.", is_core=True),
        Claim(claim_id="C-2", text="지분 할인율은 30%다.", is_core=True),
        Claim(claim_id="C-3", text="배당이 늘었다.", is_core=False)])


def test_conclusion_follows_support_interpretation_gap():
    """근거 주장이 '해석 차이'로 수정이면 목표주가는 확인 필요 (계산이 틀렸다는 증거는 없음)"""
    from tribunal.rules import propagate, fact_error_claims
    ovs = [{"objection_id": "O-1", "claim_id": "C-2", "verdict": "수정", "kind": "해석 차이"}]
    cv, dep = propagate(_dep_report(), {"C-1": "유지", "C-2": "수정", "C-3": "유지"}, fact_error_claims(ovs))
    assert cv["C-1"] == "확인 필요" and dep["C-1"] == {"from": ["C-2"], "verdict": "확인 필요"}


def test_conclusion_follows_support_fact_error_and_official_mismatch():
    """근거 주장이 사실 오류로 수정이거나 공식 데이터와 달라 수정이면 목표주가도 수정"""
    from tribunal.rules import propagate, fact_error_claims
    ovs = [{"objection_id": "O-1", "claim_id": "C-2", "verdict": "수정", "kind": "사실 오류"}]
    assert propagate(_dep_report(), {"C-1": "유지", "C-2": "수정", "C-3": "유지"}, fact_error_claims(ovs))[0]["C-1"] == "수정"
    findings = {"C-2": [{"check_id": "D-1", "verdict": "수정"}]}
    assert propagate(_dep_report(), {"C-1": "유지", "C-2": "수정", "C-3": "유지"}, fact_error_claims([], findings))[0]["C-1"] == "수정"


def test_conclusion_unchanged_when_support_kept_or_not_core():
    """근거 주장이 유지면 그대로, 핵심 아닌 주장의 수정은 결론에 옮기지 않음, 결론이 이미 더 엄하면 그대로"""
    from tribunal.rules import propagate
    r = _dep_report()
    assert propagate(r, {"C-1": "유지", "C-2": "유지", "C-3": "수정"}, set()) == ({"C-1": "유지", "C-2": "유지", "C-3": "수정"}, {})
    assert propagate(r, {"C-1": "수정", "C-2": "수정", "C-3": "유지"}, set())[0]["C-1"] == "수정"
