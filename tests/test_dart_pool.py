"""DART 공시 통째로 모아 값·기간으로 짝 찾기(dart_pool) 테스트. 실제 DART는 부르지 않는다. 회사와 숫자는 가상."""
from tribunal.external import dart_pool as dp
from tribunal.schemas import DataRow, Report


def row(rid, item, value, unit, as_of=""):
    return DataRow(rid=rid, item=item, value=value, unit=unit, as_of=as_of, page=1)


def rep(rows, when="2026-10-29"):
    return Report.model_validate({"meta": {"ticker": "900030", "name": "가상생명", "report_date": when, "snapshot_date": when, "opinion": "매수"},
                                  "claims": [], "data_rows": [r.model_dump() for r in rows]})


F = [dp._fact("주식의 총수", "주식의 총수 · 보통주 · 자기주식수", 1_234_567, "2026-06-30"),
     dp._fact("타법인 출자 현황", "타법인 출자 현황 · 가상전자 · 기말 보유 수량", 497_660_000, "2026-06-30"),
     dp._fact("재무제표", "포괄손익계산서 보험서비스결과", 974_716_000_000, "2025-12-31", "2025-01-01", "별도"),
     dp._fact("재무제표", "포괄손익계산서 보험서비스결과", 975_400_000_000, "2024-12-31", "2024-01-01", "별도"),
     dp._fact("XBRL", "비율 [별도]", 0.413, "2025-12-31", "2025-01-01", "별도")]


def test_단위와_자릿수를_읽는다():
    assert dp.row_value(row("R", "보유 주식", "497,660", "천주")) == (497_660_000, 1000, "count")
    v, tol, kind = dp.row_value(row("R", "이익", "975", "십억 원"))
    assert (v, tol, kind) == (975e9, 1e9, "money")
    assert dp.row_value(row("R", "목표 배수", "0.73", "배")) is None


def test_값과_기간이_같은_공시_숫자를_이름과_상관없이_찾는다():
    out = dp.match(rep([row("R-1", "가상생명 자기주식 수", "1,234,567", "주"),                 # 기간 없음 -> 작성일 전 최근 시점
                        row("R-2", "가상전자 보유 주식", "497,660", "천주", "2026.6말"),
                        row("R-3", "가상생명 보험손익 2025", "975", "십억 원"),               # 2024년 값(975.4)과는 기간이 달라 짝이 아니다
                        row("R-4", "배당성향 2025", "41.3%", "%")]), F, "2026-10-29")
    by = {r["rid"]: r for r in out["rows"]}
    assert by["R-1"]["status"] == "짝 1개" and "자기주식수" in by["R-1"]["candidates"][0]["label"]
    assert by["R-2"]["status"] == "짝 1개"
    assert by["R-3"]["status"] == "짝 1개" and by["R-3"]["candidates"][0]["end"] == "2025-12-31"
    assert by["R-4"]["status"] == "짝 1개"                                                    # XBRL 은 0.413 처럼 소수로 적는다


def test_추정치는_건너뛴다():
    out = dp.match(rep([row("R-1", "보험손익 2026E", "1,000", "십억 원")]), F, "2026-10-29")
    assert out["rows"][0]["status"] == "건너뜀"


def test_XBRL_한국어_이름표를_읽는다():
    text = ('<link:label xml:lang="ko" xlink:label="Label_label_ifrs-full_ContractualServiceMargin_ko" xlink:title="x" '
            'xlink:role="http://www.xbrl.org/2003/role/label" xlink:type="resource" id="a">보험계약마진</link:label>'
            '<link:label xml:lang="ko" xlink:label="Label_label_ifrs-full_ContractualServiceMargin_ko_1" xlink:title="x" '
            'xlink:role="http://www.xbrl.org/2003/role/terseLabel" xlink:type="resource" id="b">CSM</link:label>')
    assert dp.parse_labels(text) == {"ContractualServiceMargin": "보험계약마진"}


# ---------- ③ AI 로 짝 고르기 (가짜 답) ----------
from tribunal.schemas import MatchOut, MatchPick


def _out():
    return {"rows": [
        {"rid": "R-1", "item": "가상생명 자기주식 수", "value": "1,234,567", "unit": "주", "period": "기간 미상",
         "candidates": [{"source": "주식의 총수", "label": "주식의 총수 · 보통주 · 자기주식수", "value": 1234567.0, "fs": "", "end": "2026-06-30", "rcept_no": "1"}]},
        {"rid": "R-2", "item": "가상물산 지분가치", "value": "2,507", "unit": "십억 원", "period": "기간 미상",
         "candidates": [{"source": "XBRL", "label": "파생상품 금융부채", "value": 2.5078e12, "fs": "연결", "end": "2026-06-30", "rcept_no": "1"}]},
        {"rid": "R-3", "item": "목표주가", "value": "287,000", "unit": "원", "status": "건너뜀"},
    ]}


def test_후보가_있는_숫자만_AI에게_묻는다():
    p = dp.ai_payload(_out())
    assert [r["rid"] for r in p["rows"]] == ["R-1", "R-2"] and p["rows"][0]["candidates"][0]["n"] == 1


def test_AI가_고른_번호만_쓰고_후보에_없는_번호는_버린다():
    out = _out()
    dp.apply_picks(out, MatchOut(picks=[MatchPick(rid="R-1", pick=1, reason="같은 항목"), MatchPick(rid="R-2", pick=5, reason="?")]))
    assert out["rows"][0]["ai"] == "고름" and out["rows"][0]["picked"]["label"].endswith("자기주식수")
    assert out["rows"][1]["ai"] == "같은 것 없음" and out["rows"][1]["picked"] is None
    assert "ai" not in out["rows"][2] and out["ai_count"] == {"고름": 1, "같은 것 없음": 1}


def test_AI가_답하지_않은_숫자는_답_없음():
    out = _out()
    dp.apply_picks(out, MatchOut(picks=[]))
    assert out["rows"][0]["ai"] == "답 없음"


def test_가짜_답으로_끝까지_돈다():
    out = _out()
    dp.ai_pick(out, {"provider": "claude", "providers": {"claude": {"model": "m", "key_env": "X"}}}, mock=True)
    assert out["ai_count"] == {"답 없음": 2}


def test_부르기_전_점검_양식과_예상_비용():
    from tribunal.runner import load_config
    d = dp.dry_run(_out(), load_config())
    assert d["form_ok"] and d["model"] == "claude-haiku-5-5" and d["rows"] == 2 and d["usd"] < 0.01


def test_Haiku에는_서버_예비_모델_옵션을_보내지_않는다(monkeypatch):
    import sys, types
    from tribunal import llm
    seen = []

    class _S:
        def __init__(self, kw): seen.append(kw)
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get_final_message(self):
            return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text="{}")], stop_reason="end_turn", model="m",
                                         usage=types.SimpleNamespace(input_tokens=1, output_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0))

    class Anthropic:
        def __init__(self, **kw):
            self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(stream=lambda **kw: _S(kw)))

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=Anthropic, BadRequestError=Exception))
    monkeypatch.setattr(llm, "get_key", lambda env="": "x")
    monkeypatch.setattr(llm, "_record_usage", lambda *a, **k: None)
    for model in ("claude-haiku-5-5", "claude-sonnet-5-5"):
        llm._call_claude("s", "u", {"model": model, "key_env": "X"}, 100, 10)
    assert "fallbacks" not in seen[0] and "betas" not in seen[0]
    assert seen[1]["fallbacks"] == "default"


# ---------- ④ 고른 짝을 DART 대조 결과로 (가짜 DART, 가짜 AI 답) ----------
class _FakeDart:
    calls = 0

    def annual_filings(self, corp_code, year):
        return [{"rcept_no": "20260310000111", "rcept_dt": "20260310", "report_nm": f"사업보고서 ({year}.12)"}]

    def period_filings(self, corp_code, year, month):
        return [{"rcept_no": "20260814000222", "rcept_dt": "20260814", "report_nm": f"반기보고서 ({year}.{month:02d})"}]


def test_AI가_고른_짝은_일치_결과가_되고_이미_대조한_숫자와_고르지_않은_숫자는_빠진다(monkeypatch):
    facts = [dp._fact("주식의 총수", "주식의 총수 · 보통주 · 자기주식수", 1_234_567, "2026-06-30", rcept="20260814000222"),
             dp._fact("XBRL", "파생상품 금융부채", 2.507e12, "2026-06-30", fs="연결", rcept="20260814000222"),
             dp._fact("재무제표", "포괄손익계산서 보험서비스결과", 974_716_000_000, "2025-12-31", "2025-01-01", "별도", "20260310000111")]
    monkeypatch.setattr(dp, "gather", lambda dart, corp, d: (facts, []))

    def fake_pick(out, cfg, mock=False, mock_dir=None):
        dp.apply_picks(out, MatchOut(picks=[MatchPick(rid="R-1", pick=1, reason="같은 항목"),
                                            MatchPick(rid="R-2", pick=None, reason="리포트 계산 값")]))
    monkeypatch.setattr(dp, "ai_pick", fake_pick)
    report = rep([row("R-1", "가상생명 자기주식 수", "1,234,567", "주"),
                  row("R-2", "가상화재 지분가치", "2,507", "십억 원"),
                  row("R-3", "가상생명 보험손익 2025", "975", "십억 원")])        # 이미 dart_check 가 대조함
    ext = {"target": {"corp_code": "1", "corp_name": "가상생명", "stock_code": "900030"},
           "results": [{"check_id": "D-4", "stated_rid": "R-3", "status": "일치"}]}
    info = dp.extend(report, ext, {"R-3"}, {}, dart=_FakeDart())
    new = [r for r in ext["results"] if r.get("matched_by") == "ai_pool"]
    assert [r["stated_rid"] for r in new] == ["R-1"] and new[0]["check_id"] == "D-5"       # dart_check 번호 다음부터
    assert new[0]["status"] == "일치" and new[0]["kind"] == "dart" and new[0]["computed"] == "1,234,567주"
    assert "자기주식수" in new[0]["reason"] and new[0]["source"]["timing"] == "작성 당시 공개"
    assert info["picked"] == 1 and info["asked"] == 1          # "지분가치"는 평가 값이라 코드가 AI에게 묻기 전에 뺀다


def test_리포트가_계산한_평가_값은_짝을_찾지_않는다():
    facts = [dp._fact("XBRL", "금융자산 평가이익 조정", 9.976e12, "2026-06-30", fs="별도"),
             dp._fact("타법인 출자 현황", "타법인 출자 현황 · 가상전자 · 최초 취득금액", 544.4e9, "2026-06-30")]
    out = dp.match(rep([row("R-1", "SOTP ③ 금융그룹 가치", "9.98", "조원"),
                        row("R-2", "가상전자 지분 취득금액", "544.4", "십억 원")]), facts, "2026-10-29")
    by = {r["rid"]: r for r in out["rows"]}
    assert by["R-1"]["status"] == "건너뜀" and "평가 값" in by["R-1"]["why"]
    assert by["R-2"]["status"] == "짝 1개"                 # 계산에 넣은 재료는 그대로 찾는다


def test_material_tokens_from_basis():
    """계산 재료: 보유 주식 수·금액은 꺼내고, 주가·비율·배수·연도는 단위로 거른다 (가상 사례)"""
    from tribunal.external.dart_pool import material_tokens
    from tribunal.schemas import DataRow
    row = DataRow(rid="R-1", item="가상화재 지분가치", value="2,507", unit="십억 원",
                  basis="7,099천주 × 599,000원 = 4,252(시가) − 1,063(계약자 몫) × 27.5% · 1.62배 · 2026")
    got = [(t["value"], t["unit"], t["label"]) for t in material_tokens(row)]
    assert ("7,099", "천주", "") in got
    assert ("4,252", "십억 원", "시가") in got
    assert not any(u in ("%", "배") for _, u, _ in got)
    assert not any(v == "2026" for v, _, _ in got)


def test_table_facts_unit_period_and_name():
    """공시 본문 표: 앞 글의 단위를 따르고, '② 전기말' 표는 전년 말 값으로 읽는다 (가상 회사)"""
    from tribunal.external.dart_pool import table_facts
    html = ("<P>2) 주요 종속기업의 요약 재무정보 ① 당반기말</P><TABLE><TR><TD>(단위: 백만원)</TD></TR></TABLE>"
            "<TABLE><TR><TD>회사명</TD><TD>자 산</TD><TD>자 본</TD></TR>"
            "<TR><TD>가상운용</TD><TD>2,000</TD><TD>1,300</TD></TR></TABLE>"
            "<P>② 전기말</P><TABLE><TR><TD>(단위: 백만원)</TD></TR></TABLE>"
            "<TABLE><TR><TD>회사명</TD><TD>자 산</TD><TD>자 본</TD></TR>"
            "<TR><TD>가상운용</TD><TD>1,900</TD><TD>(1,100)</TD></TR></TABLE>")
    fs = table_facts(html, 2026, 2, "R1")
    cap = [f for f in fs if f["label"].endswith("자 본")]
    assert [(f["value"], f["end"]) for f in cap] == [(1.3e9, "2026-06-30"), (-1.1e9, "2025-12-31")]
    assert "가상운용" in cap[0]["label"]


def test_ratio_candidates_same_scope_and_bases():
    """비율: 분자 ÷ 분모를 연말·연초·평균으로 계산하고, 범위(구성요소)가 다른 조합은 버린다 (가상 숫자)"""
    from tribunal.external.dart_pool import ratio_candidates, _fact
    from tribunal.schemas import DataRow, RatioPlan
    facts = [
        _fact("XBRL", "당기손익에 인식한 마진 [별도재무제표, 발행 계약]", 115.0, "2025-12-31", "2025-01-01", "별도"),
        _fact("XBRL", "마진 [별도재무제표, 발행 계약]", 1000.0, "2024-12-31", None, "별도"),
        _fact("XBRL", "마진 [별도재무제표, 발행 계약]", 1200.0, "2025-12-31", None, "별도"),
        _fact("XBRL", "마진 [별도재무제표, 건강, 사망]", 1000.0, "2024-12-31", None, "별도"),   # 범위가 다름
    ]
    row = DataRow(rid="R-1", item="가상생명 마진 상각률 2025", value="11.5", unit="%", as_of="2025")
    plan = RatioPlan(rid="R-1", is_ratio=True, numerator=["마진 인식"], denominator=["마진"])
    cs = ratio_candidates(row, plan, facts, "2025-12-31")
    assert [c["label"].split("(")[-1] for c in cs] == ["연초 잔액)"]                  # 115/1000 = 11.5%, 115/1200·평균은 안 맞음
    assert cs[0]["ratio"]["den"]["value"] == 1000.0


def test_fact_values_shares_times_close():
    """보유 주식 수(DART) × 종가(KRX) = 리포트 값이면 일치, 주가가 다르면 불일치, 가정(=뒤 차감)이 붙으면 판정 안 함 (가상)"""
    from tribunal.external.dart_pool import fact_values, _fact
    from tribunal.schemas import DataRow, Report, Meta
    facts = [_fact("타법인 출자 현황", "타법인 출자 현황 · 가상증권 · 기말 보유 수량", 1_000_000, "2026-06-30")]

    class FakeKrx:
        def find(self, day, ticker="", name=""):
            return ({"ISU_NM": name, "TDD_CLSPRC": "50,000"}, None, {"fetched_at": ""}) if name == "가상증권" else (None, None, None)
    rows = [DataRow(rid="R-1", item="가상증권 지분가치", value="50", unit="십억 원", as_of="2026.11.10 종가", basis="1,000천주 × 50,000원"),
            DataRow(rid="R-2", item="가상증권 지분가치2", value="49", unit="십억 원", as_of="2026.11.10 종가", basis="1,000천주 × 49,000원"),
            DataRow(rid="R-3", item="가상증권 지분가치3", value="35", unit="십억 원", as_of="2026.11.10 종가",
                    basis="1,000천주 × 50,000원 = 50(시가) − 15(세금)")]
    rep = Report.model_construct(meta=Meta(name="가상지주", ticker="000000", report_date="2026-11-10", opinion="매수"), claims=[], data_rows=rows)
    got = {r["stated_rid"]: r["status"] for r in fact_values(rep, facts, FakeKrx(), 0)}
    assert got == {"R-1": "일치", "R-2": "불일치"}
