"""기본 종목정보(tribunal/external/company.py)의 계산 규칙 (가상 숫자)"""
from tribunal.external import company as cp


def test_repeated_balance_sheet_totals_are_blanked():
    """앞 분기와 원 단위까지 같은 자산·부채 합계는 API 가 되풀이한 값으로 보고 비운다. 비운 값은 다음 비교에 쓰지 않는다"""
    q = [{"period": "2025.03", "assets": 300e12, "liab": 280e12, "equity": 20e12, "derived": []},
         {"period": "2025.06", "assets": 300e12, "liab": 280e12, "equity": 22e12, "derived": []},
         {"period": "2025.09", "assets": 300e12, "liab": 280e12, "equity": 25e12, "derived": []},
         {"period": "2025.12", "assets": 330e12, "liab": 300e12, "equity": 30e12, "derived": []}]
    cp._drop_repeats(q)
    assert [p["assets"] for p in q] == [300e12, None, None, 330e12]
    assert q[1]["missing"] == ["assets", "liab"] and q[3].get("missing") is None


def test_liabilities_follow_assets_minus_equity():
    p = {"assets": 100.0e12, "equity": 10.0e12, "liab": 95.0e12, "derived": []}
    cp._check_liab(p)
    assert p["liab"] == 90.0e12 and "liab" in p["derived"]
    ok = {"assets": 100.0e12, "equity": 10.0e12, "liab": 90.0e12, "derived": []}
    cp._check_liab(ok)
    assert ok["derived"] == []


def test_metrics_use_last_four_quarters_and_skip_missing():
    """EPS = 회사 공시 기본주당이익 최근 4분기 합 (가상 값). 하나라도 없으면 빈칸 (순이익 ÷ 주식 수로 채우지 않는다)"""
    q = [{"period": f"2026.{m:02d}", "ni_owner": 1e11, "equity_owner": 2e12, "eps_disc": 1000.0} for m in (3, 6, 9)]
    q = [{"period": "2025.09", "ni_owner": 1e11, "equity_owner": 2e12, "eps_disc": 1000.0},
         {"period": "2025.12", "ni_owner": 1e11, "equity_owner": 2e12, "eps_disc": 1000.0}] + q
    cp.add_returns(q, [])
    m = cp.metrics({"quarter": q}, {"close": 10000.0, "shares": 1e8})
    assert m["eps"] == 4000.0 and m["per"] == 2.5 and m["bps"] == 20000.0 and m["pbr"] == 0.5 and round(m["roe"], 6) == 20.0
    assert "공시 기본주당이익" in m["basis"]["eps"]
    q[-1]["eps_disc"] = None
    assert cp.metrics({"quarter": q}, {"close": 10000.0, "shares": 1e8})["eps"] is None     # 하나라도 없으면 지어내지 않는다


def _q(**kw):
    return [{"period": p, **kw} for p in ("2025.09", "2025.12", "2026.03", "2026.06", "2026.09")]


def test_no_owner_line_and_no_minority_uses_total_net_income():
    """삼성카드 모양: 지배주주 순이익 줄이 없고 비지배지분도 없으면 전체 순이익. 비지배지분이 있으면 지어내지 않는다"""
    q = _q(ni=1e11, ni_owner=None, equity=2e12, equity_owner=None, nci=None)
    cp.add_returns(q, [])
    m = cp.metrics({"quarter": q}, {"close": 10000.0, "shares": 1e8})
    assert round(m["roe"], 6) == 20.0 and "비지배지분 없음" in m["basis"]["roe"]
    q = _q(ni=1e11, ni_owner=None, equity=2.2e12, equity_owner=None, nci=2e11)          # 비지배지분이 있는데 지배주주 값이 없으면 빈칸
    cp.add_returns(q, [])
    assert cp.metrics({"quarter": q}, {"close": 10000.0, "shares": 1e8})["roe"] is None


def test_float_shares_are_used_and_dividend_yield_uses_close():
    q = _q(ni_owner=1e11, equity_owner=2e12, eps_disc=1250.0)
    m = cp.metrics({"quarter": q}, {"close": 10000.0, "shares": 1e8}, {"n": 8e7, "label": "2026년 반기보고서"},
                   [{"year": 2024, "dps": 300.0}, {"year": 2025, "dps": 500.0}])
    assert m["eps"] == 5000.0 and m["bps"] == 25000.0 and "유통주식수" in m["basis"]["bps"]
    assert m["div_yield"] == 5.0 and m["div_year"] == 2025


class _Dart:
    def __init__(self, by):
        self.by = by

    def get(self, ep, p):
        hit = self.by.get((p["bsns_year"], p["reprt_code"]))
        if hit is None:
            raise cp.DartError("없음")
        return {"list": hit}


def test_float_shares_take_latest_report_with_numbers():
    """3분기보고서 값이 '-' 이면 그 앞 반기보고서. 합계 줄(보통주+우선주, 자기주식 제외)"""
    from datetime import date
    d = _Dart({("2026", "11014"): [{"se": "합계", "distb_stock_co": "-"}],
               ("2026", "11012"): [{"se": "보통주", "distb_stock_co": "90"}, {"se": "우선주", "distb_stock_co": "10"},
                                    {"se": "합계", "distb_stock_co": "100", "rcept_no": "r1"}]})
    got = cp.float_shares(d, "x", date(2026, 11, 24))
    assert got == {"n": 100.0, "label": "2026년 반기보고서", "rcept_no": "r1"}
    assert cp.float_shares(_Dart({}), "x", date(2026, 11, 24)) is None


def test_float_history_uses_each_quarter_report_and_nearest_when_missing():
    d = _Dart({("2025", "11011"): [{"se": "합계", "distb_stock_co": "100"}],
               ("2026", "11013"): [{"se": "합계", "distb_stock_co": "-"}],
               ("2026", "11012"): [{"se": "합계", "distb_stock_co": "90"}]})
    got = cp.float_history(d, "x", ["2025.12", "2026.03", "2026.06"])
    assert got == {"2025.12": 100.0, "2026.03": 100.0, "2026.06": 90.0}    # 1분기 값이 없으면 가까운 기간 (거리 같으면 앞)
    assert cp.float_history(_Dart({}), "x", ["2026.06"]) == {}


def test_fill_blanked_totals_from_xbrl_when_they_add_up():
    """비운 자산·부채는 원본(XBRL)에서 '자산 − 자본 = 부채'가 맞는 값으로 채운다. 원본에 틀린 값이 섞여 있어도 맞는 조합만 (가상 숫자)"""
    class FakeDart:
        def xbrl_facts(self, rno, reprt):
            f = lambda n, v: {"name": n, "dims": {}, "instant": "2025-06-30", "value": v}
            return {"facts": [f("Assets", 300.0), f("Assets", 310.0), f("Liabilities", 280.0), f("Liabilities", 285.0), f("Equity", 25.0)]}
    q = [{"period": "2025.06", "end": "2025-06-30", "assets": None, "liab": None, "equity": 25.0, "missing": ["assets", "liab"], "derived": []}]
    cp._fill_from_xbrl(FakeDart(), {(2025, 2): {"rcept_no": "X", "fs": "연결"}}, q)
    assert (q[0]["assets"], q[0]["liab"], q[0]["missing"], q[0]["fixed"]) == (310.0, 285.0, [], ["assets", "liab"])


def test_fisis_short_names():
    from tribunal.external.fisis import short_name
    assert short_name("가상생명보험주식회사") == "가상생명"
    assert short_name("가나다생명보험(주)") == "가나다생명"
    assert short_name("가상손해보험주식회사") == "가상손해"


def test_peers_choose_same_industry_by_market_cap():
    """같은 업종 코드(5자리, 모자라면 넓힘)의 상장사를 시가총액 순으로, 자기 회사는 꼭 넣는다 (가상 회사)"""
    from tribunal.external.peers import choose
    index = {"000001": {"induty": "65110"}, "000002": {"induty": "65110"}, "000003": {"induty": "65121"},
             "000004": {"induty": "65110"}, "000005": {"induty": "26110"}}
    market = {t: {"MKTCAP": str(m)} for t, m in (("000001", 10), ("000002", 30), ("000003", 20), ("000004", 5), ("000005", 99))}
    assert choose(index, "000001", market, limit=3) == ["000002", "000001", "000004"]   # 같은 5자리 업종이 3곳이면 넓히지 않는다
    index["000004"]["induty"] = "65999"
    assert choose(index, "000001", market, limit=3) == ["000002", "000003", "000001"]   # 2곳뿐이면 앞 3자리로 넓힌다
    assert "000005" not in choose(index, "000001", market)


# ---------- 부문별 매출 (XBRL 영업부문 주석) ----------
def _rev(value, start="2025-01-01", end="2025-12-31", name="Revenue", **dims):
    return {"name": name, "dims": {"ConsolidatedAndSeparateFinancialStatementsAxis": "ConsolidatedMember", **dims},
            "start": start, "end": end, "value": value}


def test_segment_groups_picks_segments_and_drops_eliminations():
    res = {"labels": {"AMember": "가 부문 [구성요소]", "BMember": "나 부문"}, "facts": [
        _rev(100.0),
        _rev(70.0, SegmentsAxis="AMember"), _rev(40.0, SegmentsAxis="BMember"),
        _rev(-10.0, SegmentsAxis="EliminationOfIntersegmentAmountsMember"),
        _rev(65.0, start="2024-01-01", end="2024-12-31", SegmentsAxis="AMember"),
    ]}
    m = cp.segment_groups(res)
    assert m["title"] == "부문별 매출" and m["year"] == 2025
    assert [(i["name"], i["share"]) for i in m["items"]] == [("가 부문", 63.64), ("나 부문", 36.36)]


def test_segment_groups_rejects_partial_and_subsidiary_lists():
    part = {"facts": [_rev(100.0), _rev(30.0, SegmentsAxis="A"), _rev(20.0, SegmentsAxis="B")]}     # 합이 전체의 절반
    assert cp.segment_groups(part) is None
    subs = {"labels": {"A": "(주)가나", "B": "Dara Inc."}, "facts": [_rev(100.0), _rev(60.0, SegmentsAxis="A"), _rev(40.0, SegmentsAxis="B")]}
    assert cp.segment_groups(subs) is None


def test_segment_profit_only_when_all_positive():
    P = "ProfitLossFromOperatingActivities"
    ok = {"facts": [_rev(10.0, name=P), _rev(7.0, name=P, SegmentsAxis="A"), _rev(3.0, name=P, SegmentsAxis="B")]}
    assert cp.segment_groups(ok, cp.SEG_PROFIT)["title"] == "부문별 영업이익"
    bad = {"facts": [_rev(10.0, name=P), _rev(12.0, name=P, SegmentsAxis="A"), _rev(-2.0, name=P, SegmentsAxis="B")]}
    assert cp.segment_groups(bad, cp.SEG_PROFIT) is None


def test_region_groups_nets_eliminations():
    SC = "SegmentConsolidationItemsAxis"
    res = {"labels": {"US": "미국"}, "facts": [
        _rev(100.0),
        _rev(90.0, GeographicalAreasAxis="CountryOfDomicileMember", **{SC: "OperatingSegmentsMember"}),
        _rev(-50.0, GeographicalAreasAxis="CountryOfDomicileMember", **{SC: "EliminationOfIntersegmentAmountsMember"}),
        _rev(60.0, GeographicalAreasAxis="US", **{SC: "OperatingSegmentsMember"}),
    ]}
    m = cp.region_groups(res)
    assert m["title"] == "지역별 매출"
    assert [(i["name"], i["share"]) for i in m["items"]] == [("국내", 40.0), ("미국", 60.0)]



def test_split_adjustment_divides_earlier_prices():
    rows = [{"date": "1", "close": 2_500_000.0, "open": 2_500_000.0, "shares": 1e8, "volume": 10.0},
            {"date": "2", "close": 50_000.0, "open": 50_000.0, "shares": 50e8, "volume": 500.0},    # 50:1 액면분할
            {"date": "3", "close": 51_000.0, "open": 51_000.0, "shares": 50e8, "volume": 400.0}]
    out = cp.adjust_splits(rows)
    assert out[0]["close"] == 50_000.0 and out[0]["volume"] == 500.0 and out[0]["adjusted"]
    assert out[1]["close"] == 50_000.0 and not out[1].get("adjusted")
    same = [{"date": "1", "close": 100.0, "shares": 1e8}, {"date": "2", "close": 60.0, "shares": 1.05e8}]   # 주식수 조금 늘고 가격 급락: 보정 안 함
    assert cp.adjust_splits(same)[0]["close"] == 100.0



def test_new_filing_triggers_rebuild():
    class FakeDart:
        def corp_by_stock(self, t):
            return {"corp_code": "00000001"}

        def get(self, ep, params):
            return {"list": [{"rcept_no": "A1", "report_nm": "분기보고서 (2026.09)"}, {"rcept_no": "B2", "report_nm": "주요사항보고서"}]}
    co = {"ticker": "000001", "made_at": "2026-11-01T10:00:00", "statements": {"sources": [{"rcept_no": "OLD"}]}}
    assert cp.new_filing(FakeDart(), co) is True
    co["statements"]["sources"].append({"rcept_no": "A1"})
    assert cp.new_filing(FakeDart(), co) is False                     # 이미 반영한 보고서, 다른 공시는 무시


def test_krx_history_compact_keeps_needed_fields():
    from tribunal.external import krx_history as kh
    rows = [{"ISU_CD": "005930", "TDD_OPNPRC": "100", "TDD_HGPRC": "110", "TDD_LWPRC": "90", "TDD_CLSPRC": "105", "ACC_TRDVOL": "7", "LIST_SHRS": "1000"},
            {"ISU_CD": "KR7000660001", "TDD_CLSPRC": "50", "TDD_OPNPRC": "", "ACC_TRDVOL": "1", "LIST_SHRS": "10"},
            {"ISU_CD": "999999", "TDD_CLSPRC": "0"}]                        # 거래 없는 종목은 뺀다
    out = kh.compact(rows)
    assert out["005930"] == [100.0, 110.0, 90.0, 105.0, 7.0, 1000.0]
    assert out["000660"][0] == 50.0 and "999999" not in out             # 표준코드는 단축코드로, 빈 시가는 종가로


# ---------- 공시 본문 사업 지표 (2026-11-24) ----------
def _tbl(rows):
    return "<TABLE>" + "".join("<TR>" + "".join(f"<TD>{c}</TD>" for c in r) + "</TR>" for r in rows) + "</TABLE>"


def test_rnd_table_aligns_columns_even_without_trailing_note_cell():
    # HD한국조선해양 2023 모양: 이름 칸 2개, 첫 줄만 끝에 빈 '비고' 칸
    from tribunal.external import company as co
    text = "<P>연구개발비용</P><P>(단위 : 백만원)</P>" + _tbl([
        ["과 목", "과 목", "2023년", "2022년", "2021년", "비고"],
        ["원 재 료 비", "원 재 료 비", "16,678", "14,282", "8,025", ""],
        ["연구개발비용 계(*)", "연구개발비용 계(*)", "162,437", "125,165", "92,480"],
        ["연구개발비/매출액 비율", "연구개발비/매출액 비율", "0.8%", "0.7%", "0.6%"]])
    out = co.rnd_table(text, 2023)
    assert [(r["year"], r["amount"], r["ratio"]) for r in out] == [
        (2023, 162437e6, 0.8), (2022, 125165e6, 0.7), (2021, 92480e6, 0.6)]


def test_rnd_table_uses_report_year_for_period_labels():
    from tribunal.external import company as co
    text = "<P>연구개발비용</P><P>(단위 : 천원)</P>" + _tbl([
        ["과 목", "제35기", "제34기"], ["연구개발비용 계", "482,424,987", "419,965,671"],
        ["연구개발비 / 매출액 비율", "11.59%", "11.81%"]])
    out = co.rnd_table(text, 2025)
    assert [(r["year"], r["ratio"]) for r in out] == [(2025, 11.59), (2024, 11.81)]
    assert out[0]["amount"] == 482424987e3


def test_backlog_prefers_consolidated_sentence():
    from tribunal.external import company as co
    text = "<P>4. 매출 및 수주상황</P>" + _tbl([["구분", "계약잔액"], ["합계", "100"]]) + \
           "<P>연결기준 계약잔액은 95,038,562백만원입니다.</P>"
    assert co.backlog_table(text, 2025)["amount"] == 95038562e6


def test_backlog_total_row_last_cell_and_typo_guard():
    from tribunal.external import company as co
    ok = "<P>수주상황</P><P>(단위 : 억원)</P>" + _tbl([
        ["품목", "수주총액", "기납품액", "수주잔고"], ["조선해양", "504,298", "231,414", "272,884"],
        ["토건", "32,437", "25,016", "7,421"], ["합 계", "536,735", "256,430", "280,305"]])
    assert co.backlog_table(ok, 2025)["amount"] == 280305e8
    typo = ok.replace("280,305", "280.305")                 # 공시 오타(쉼표 대신 점): 항목 합과 달라 쓰지 않는다
    assert co.backlog_table(typo, 2024) is None


def test_backlog_skips_per_company_tables_without_consolidated_sentence():
    from tribunal.external import company as co
    text = "<P>수주상황</P><P>1) 본사</P><P>(단위 : 백만원)</P>" + _tbl([["구분", "계약잔액"], ["국내", "10"], ["합계", "10"]])
    assert co.backlog_table(text, 2025) is None


def test_backlog_skips_non_won_units():
    from tribunal.external import company as co
    text = "<P>수주상황</P><P>(단위 : 억불)</P>" + _tbl([["품목", "수주잔고"], ["선박", "300"], ["합계", "300"]])
    assert co.backlog_table(text, 2025) is None


def test_find_reads_old_codes_and_quarter_names():
    """옛 공시: 계정 코드 'ifrs_'(요즘 'ifrs-full_'), 코드 없이 '분기순이익'·'반기순이익(손실)'만 적은 줄도 당기순이익으로 읽는다"""
    old = [{"sj_div": "CIS", "account_id": "ifrs_ProfitLoss", "account_nm": "분기순이익", "thstrm_amount": "100"}]
    assert cp._find(old, "IS", ["ifrs-full_ProfitLoss"], ["당기순이익"])["thstrm_amount"] == "100"
    nocode = [{"sj_div": "CIS", "account_id": "-표준계정코드 미사용-", "account_nm": "반기순이익(손실)", "thstrm_amount": "-5"}]
    assert cp._find(nocode, "IS", ["ifrs-full_ProfitLoss"], ["당기순이익(손실)"])["thstrm_amount"] == "-5"


def test_summary_fin_reads_body_tables():
    """요약재무정보(본문): 재무상태 표 다음 손익 표. 번호·띄어쓰기·[ ]가 붙은 줄 이름도 읽는다 (가상 숫자)"""
    def tbl(rows):
        return "<TABLE>" + "".join("<TR>" + "".join(f"<TD>{c}</TD>" for c in r) + "</TR>" for r in rows) + "</TABLE>"
    text = ("<P>1. 요약재무정보</P><P>(단위 : 백만원)</P>" + tbl([
        ["구 분", "제 10 기", "제 9 기"], ["자 산 총 계", "1,000", "900"], ["부 채 총 계", "600", "550"],
        ["Ⅰ. 지배기업 소유주지분", "380", "330"], ["자 본 총 계", "400", "350"]])
        + "<P>(단위 : 백만원)</P>" + tbl([["구 분", "제 10 기", "제 9 기"], ["[당기연결순이익]", "50", "-"],
                                         ["지배기업 소유주지분", "45", "-"]]))
    out = cp.summary_fin(text, 2019)
    assert out[2019] == {"assets": 1000e6, "liab": 600e6, "equity": 400e6, "equity_owner": 380e6, "ni": 50e6, "ni_owner": 45e6}
    assert "ni" not in out[2018] and out[2018]["equity"] == 350e6


def _t(rows):
    return "<TABLE>" + "".join("<TR>" + "".join(f"<TD>{c}</TD>" for c in r) + "</TR>" for r in rows) + "</TABLE>"


def test_da_candidates_pairs_single_column_tables():
    """당기 표 다음 전기 표가 따로 있는 문서(삼성전자 모양): 같은 조합 한 칸짜리 표를 전기로 짝짓는다 (가상 숫자)"""
    text = ("<P>(단위 : 백만원)</P>" + _t([["", "공시금액"], ["감가상각비", "300"], ["무형자산상각비", "20"]])
            + "<P>(단위 : 백만원)</P>" + _t([["", "공시금액"], ["감가상각비", "280"], ["무형자산상각비", "15"]]))
    assert ("dep+amo", 320e6, 295e6) in cp._da_candidates(text)


def test_fill_old_da_needs_two_documents_to_agree():
    """Y 년 문서의 전기 값이 Y-1 년 문서의 당기 값과 같을 때만 넣는다. 다르면 그 해부터 비운다 (가상 숫자)"""
    docs = {2023: _t([["구분", "당기", "전기"], ["감가상각비", "100", "90"], ["무형자산상각비", "10", "8"]]),
            2022: _t([["구분", "당기", "전기"], ["감가상각비", "90", "70"], ["무형자산상각비", "8", "6"]]),
            2021: _t([["구분", "당기", "전기"], ["감가상각비", "75", "60"], ["무형자산상각비", "6", "5"]])}   # 2021 문서 당기(81) ≠ 2022 문서 전기(76)

    class FakeDart:
        def annual_filings(self, corp, y):
            return [{"rcept_no": str(y)}] if y in docs else []

        def document_text(self, rno):
            return "<P>(단위 : 원)</P>" + docs[int(rno)]

    st = {"annual": [{"period": str(y), "end": f"{y}-12-31"} for y in range(2019, 2024)]}
    st["annual"][-1]["da"] = 110.0
    cp.fill_old_da(FakeDart(), "x", st, today=__import__("datetime").date(2026, 1, 1))
    got = {a["period"]: a.get("da") for a in st["annual"]}
    assert got["2022"] == 98.0 and got["2021"] is None and got["2020"] is None


def test_dashboard_company_version_matches():
    """화면의 COMPANY_V 가 company.py CO_V 와 같아야 옛 자료를 다시 만든다"""
    import re
    from pathlib import Path
    ts = (Path(__file__).resolve().parents[1] / "dashboard" / "components" / "workspace" / "company-view.tsx").read_text(encoding="utf-8")
    assert int(re.search(r"const COMPANY_V = (\d+);", ts).group(1)) == cp.CO_V


def test_table_rows_from_all_years_no_prior_fill():
    """전면 개편 1차 (2026-11-25): 옛 해에만 있는 줄도 남기되, 다음 해 보고서의 비교(전기) 숫자로 빈칸을 채우지 않는다"""
    from datetime import date

    def row(nm, cur, prev=None):
        return {"sj_div": "IS", "account_id": "-표준계정코드 미사용-", "account_nm": nm, "ord": "1",
                "thstrm_amount": cur, "frmtrm_amount": prev, "rcept_no": "R"}

    class FakeDart:
        def statements(self, corp, y, fs, reprt):
            if fs != "CFS" or reprt != "11011":
                return {"status": "013"}
            return {"status": "000", "list": {2024: [row("영업수익", "100"), row("당기순이익", "10")],
                                              2025: [row("순이자손익", "70", "60"), row("당기순이익", "12", "10")]}.get(y, [])}

        def xbrl_facts(self, *a):
            return {"facts": []}
    st = cp.statements(FakeDart(), "x", years=3, today=date(2026, 1, 1))
    t = st["tables"]["IS"]
    names = [a["name"] for a in t["accounts"]]
    assert "영업수익" in names and "순이자손익" in names                 # 2024 년에만 있는 옛 계정도 줄로 남는다
    assert t["annual"]["2024"]["IS:nm:순이자손익|순이자손익"] is None       # 2025 보고서 '전기' 칸으로 채우지 않는다
    assert t["prior"] == []
    assert t["annual"]["2025"]["IS:nm:영업수익|영업수익"] is None


def test_table_rows_matched_by_tag():
    """같은 태그 = 한 줄 (회사가 이름을 바꿔도). 이름은 최근 보고서 것. 한 보고서 안 같은 태그 두 줄은 따로"""
    from datetime import date

    def row(aid, nm, cur, ord_):
        return {"sj_div": "IS", "account_id": aid, "account_nm": nm, "ord": ord_, "thstrm_amount": cur, "rcept_no": "R"}

    class FakeDart:
        def statements(self, corp, y, fs, reprt):
            if fs != "CFS" or reprt != "11011":
                return {"status": "013"}
            return {"status": "000", "list": {
                2024: [row("ifrs-full_SellingGeneralAndAdministrativeExpense", "판매비와관리비", "30", "1"),
                       row("ifrs-full_ProfitLoss", "계속영업이익", "9", "2"), row("ifrs-full_ProfitLoss", "당기순이익", "10", "3")],
                2025: [row("ifrs-full_SellingGeneralAndAdministrativeExpense", "판매및일반관리비", "33", "1"),
                       row("ifrs-full_ProfitLoss", "계속영업이익", "11", "2"), row("ifrs-full_ProfitLoss", "당기순이익", "12", "3")]}.get(y, [])}

        def xbrl_facts(self, *a):
            return {"facts": []}
    t = cp.statements(FakeDart(), "x", years=3, today=date(2026, 1, 1))["tables"]["IS"]
    sga = [a for a in t["accounts"] if a["id"] == "ifrs-full_SellingGeneralAndAdministrativeExpense"]
    assert len(sga) == 1 and sga[0]["name"] == "판매및일반관리비"
    k = f"{sga[0]['id']}|{sga[0]['name']}"
    assert t["annual"]["2024"][k] == 30.0 and t["annual"]["2025"][k] == 33.0
    assert len([a for a in t["accounts"] if "ProfitLoss" in a["id"]]) == 2


def test_fill_early_quarters_from_next_year_and_doc(monkeypatch):
    """금융회사 2023년 1·2분기 (재무 API 가 3분기부터): 손익 = 다음 해 같은 분기 보고서 전기 칸, 재무상태 = 그 분기 원문 표 (가상 숫자)"""
    from datetime import date
    from tribunal.external import doc_fs

    def is_row(cur, prev_q, prev_add):
        return {"sj_div": "CIS", "account_id": "ifrs-full_ProfitLoss", "account_nm": "당기순이익", "ord": "1",
                "thstrm_amount": cur, "thstrm_add_amount": cur, "frmtrm_q_amount": prev_q, "frmtrm_add_amount": prev_add, "rcept_no": "R"}

    def bs_row(v):
        return {"sj_div": "BS", "account_id": "ifrs-full_Assets", "account_nm": "자산총계", "ord": "1", "thstrm_amount": v, "rcept_no": "R"}

    class FakeDart:
        def statements(self, corp, y, fs, reprt):
            if fs != "CFS":
                return {"status": "013"}
            have = {(2023, "11014"): [is_row("30", None, None), bs_row("1000")], (2023, "11011"): [is_row("120", None, None), bs_row("1100")],
                    (2024, "11013"): [is_row("40", "25", "25"), bs_row("1200")], (2024, "11012"): [is_row("45", "28", "53"), bs_row("1300")]}
            rows = have.get((y, reprt))
            return {"status": "000", "list": rows} if rows else {"status": "013"}

        def xbrl_facts(self, *a):
            return {"facts": []}
    monkeypatch.setattr(doc_fs, "period_doc", lambda d, c, y, q, b: {"rcept_no": f"DOC{q}", "BS": {"plain": [("자 산 총 계", 900.0 + q)]}})
    st = cp.statements(FakeDart(), "x", years=2, today=date(2024, 11, 1))
    q = {p["period"]: p for p in st["quarter"]}
    assert q["2023.03"]["ni"] == 25.0 and q["2023.06"]["ni"] == 28.0          # 2024년 보고서 전기 3개월 값
    assert q["2023.03"]["assets"] == 901.0 and q["2023.06"]["assets"] == 902.0   # 원문 (자산총계 태그로 한 줄)
    t = st["tables"]["BS"]
    assert len(t["accounts"]) == 1 and ["2023.06", "ifrs-full_Assets|자산총계"] in t["doc"]
    assert ["2023.03", "ifrs-full_ProfitLoss|당기순이익"] in st["tables"]["IS"]["prior"]
    assert [e["period"] for e in st["early"]] == ["2023.03", "2023.06"]


def test_adjust_eps_splits():
    """주식 수가 한 분기에 50배 → 그 앞 EPS·BPS 를 50으로 나누고 표시 (가상 숫자)"""
    rows = [{"period": "2017.12", "shares": 100, "eps": 5000, "bps": 50000},
            {"period": "2018.03", "shares": 100, "eps": 5100, "bps": 51000},
            {"period": "2018.06", "shares": 5000, "eps": 104, "bps": 1040},
            {"period": "2018.09", "shares": 5100, "eps": 105, "bps": 1050}]
    cp.adjust_eps_splits(rows)
    assert rows[0]["eps"] == 100 and rows[1]["bps"] == 1020 and rows[1]["split"] == 50
    assert rows[2]["split_at"] == 50.0 and "split" not in rows[2] and rows[3]["eps"] == 105


def test_insurance_csm_with_extra_tags_and_prior_year_end():
    """손보사 CSM (가상 값): 합계에 '장기보험'·'구성요소: CSM' 꼬리표가 더 붙어도 찾는다. 다른 꼬리표(일반보험)는 아니다.
    주석이 없는 연말은 다음 해 보고서의 지난 연말 값"""
    base = {"ConsolidatedAndSeparateFinancialStatementsAxis": "SeparateMember", "DisaggregationOfInsuranceContractsAxis": "InsuranceContractsIssuedMember"}

    def fact(v, end, **more):
        return {"name": "ContractualServiceMargin", "dims": {**base, **more}, "instant": end, "value": v}

    facts = {"R24": [],
             "R25": [fact(130.0, "2025-06-30", TypesOfContractsAxis="LongtermInsuranceMember"),
                     fact(5.0, "2025-06-30", TypesOfContractsAxis="GeneralInsuranceMember"),
                     fact(120.0, "2024-12-31", InsuranceContractsByComponentsAxis="ContractualServiceMarginMember")]}

    class FakeDart:
        def xbrl_facts(self, rcept, reprt):
            return {"facts": facts[rcept]}
    q = [{"period": "2024.12", "end": "2024-12-31", "rcept_no": "R24"}, {"period": "2025.06", "end": "2025-06-30", "rcept_no": "R25"}]
    rows = cp.insurance(FakeDart(), "x", q)
    assert rows[1]["csm"] == 130.0                                       # 장기보험 꼬리표 (일반보험 5 는 아님)
    assert rows[0]["csm"] == 120.0 and "csm_prior" in rows[0]["derived"]  # 다음 보고서의 지난 연말 값


def test_insurance_csm_prior_year_same_quarter():
    """주석이 없는 분기(가상 값): 다음 해 같은 분기 보고서의 '전기' 칸(작년 같은 분기 말) CSM 으로 채우고 * 표시.
    그 분기 보고서에 값이 있으면 그 값이 먼저"""
    base = {"ConsolidatedAndSeparateFinancialStatementsAxis": "SeparateMember", "DisaggregationOfInsuranceContractsAxis": "InsuranceContractsIssuedMember"}

    def fact(v, end):
        return {"name": "ContractualServiceMargin", "dims": dict(base), "instant": end, "value": v}

    facts = {"A": [], "B": [fact(90.0, "2025-06-30")],
             "C": [fact(110.0, "2026-03-31"), fact(100.0, "2025-03-31")],
             "D": [fact(120.0, "2026-06-30"), fact(77.0, "2025-06-30")]}

    class FakeDart:
        def xbrl_facts(self, rcept, reprt):
            return {"facts": facts[rcept]}
    q = [{"period": "2025.03", "end": "2025-03-31", "rcept_no": "A"}, {"period": "2025.06", "end": "2025-06-30", "rcept_no": "B"},
         {"period": "2026.03", "end": "2026-03-31", "rcept_no": "C"}, {"period": "2026.06", "end": "2026-06-30", "rcept_no": "D"}]
    rows = {r["period"]: r for r in cp.insurance(FakeDart(), "x", q)}
    assert rows["2025.03"]["csm"] == 100.0 and "csm_prior" in rows["2025.03"]["derived"]   # 2026년 1분기 보고서 전기 칸
    assert rows["2025.06"]["csm"] == 90.0 and "csm_prior" not in rows["2025.06"]["derived"]  # 그 분기 값이 먼저 (77 아님)


def test_eps_row_picks_common_basic_eps():
    """공시 기본주당이익 줄 (가상 값): 보통주 줄 먼저, 우선주·희석·중단영업은 빼고, 계속영업은 다른 줄이 없을 때만"""
    def r(nm, v, aid=cp.EPS_ID, sj="IS"):
        return {"sj_div": sj, "account_id": aid, "account_nm": nm, "thstrm_amount": str(v)}
    rows = [r("우선주 기본주당이익", 120), r("보통주 기본주당이익", 100), r("희석주당이익", 99, "ifrs-full_DilutedEarningsLossPerShare")]
    assert cp._eps_row(rows)["thstrm_amount"] == "100"
    assert cp._eps_row([r("계속영업기본주당이익", 90), r("기본주당이익", 80)])["thstrm_amount"] == "80"
    assert cp._eps_row([r("기본주당이익", 70, "-표준계정코드 미사용-", "CIS")])["thstrm_amount"] == "70"   # 태그 없으면 이름으로
    assert cp._eps_row([r("영업이익", 1, "dart_OperatingIncomeLoss")]) is None


def test_eps_ttm_needs_four_consecutive_quarters():
    q = [{"period": p, "eps_disc": 100.0} for p in ("2025.09", "2025.12", "2026.03", "2026.06", "2026.09")]
    assert cp.eps_ttm(q, 4) == 400.0
    assert cp.eps_ttm(q, 2) is None                                   # 4분기가 안 됨
    q2 = [x for x in q if x["period"] != "2026.03"]
    assert cp.eps_ttm(q2, 3) is None                                  # 중간 분기가 빠지면 합하지 않는다


def test_per_is_price_over_disclosed_eps():
    """PER = 주가 ÷ 공시 EPS (가상 값). EPS 0 이하·없으면 빈칸"""
    assert cp.per_of(10000.0, 4000.0) == 2.5
    assert cp.per_of(10000.0, 0.0) is None and cp.per_of(10000.0, -10.0) is None and cp.per_of(10000.0, None) is None
    assert cp.per_of(None, 4000.0) is None


def test_known_dates_cap_corrected_filings_by_deadline():
    """정정 보고서 접수번호면 늦게 찍히므로 법정 기한(분기 45일, 연간 90일)으로 자른다 (가상 값)"""
    st = {"quarter": [{"period": "2025.09", "end": "2025-09-30", "rcept_no": "20251113000001"},
                      {"period": "2025.12", "end": "2025-12-31", "rcept_no": "20260619000001"}], "annual": []}
    k = cp.known_dates(st)
    assert k["2025.09"] == "20251113" and k["2025.12"] == "20260331"


def test_per_uses_latest_disclosed_eps_on_that_day():
    """그날 주가 ÷ 그날까지 공시된 최근 EPS (가상 값). 분기 말에는 그 분기 실적이 아직 공시 전"""
    rows = [{"period": "2026.03", "date": "20260331", "close": 1000.0, "eps": 100.0, "bps": 500.0},
            {"period": "2026.06", "date": "20260630", "close": 1200.0, "eps": 200.0, "bps": 600.0}]
    cp.apply_known_rule(rows, {"2026.03": "20260515", "2026.06": "20260814"})
    assert rows[0]["per"] is None                                   # 3월 말: 공시된 EPS 없음 (2026.03 실적은 5/15 공시)
    assert rows[1]["per"] == 12.0 and rows[1]["pbr"] == 2.4 and rows[1]["per_from"] == "2026.03"


def test_roe_roa_ttm_and_annual():
    """ROE = 최근 4분기 지배주주 순이익 ÷ ((지금 + 1년 전 지배주주 자본)/2), ROA 는 전체 순이익 ÷ 평균 총자산 (가상 값)"""
    q = [{"period": p, "ni": 30.0, "ni_owner": 25.0, "equity_owner": e, "assets": a}
         for p, e, a in (("2025.03", 900.0, 4000.0), ("2025.06", 950.0, 4100.0), ("2025.09", 1000.0, 4200.0),
                         ("2025.12", 1050.0, 4300.0), ("2026.03", 1100.0, 4400.0))]
    an = [{"period": "2024", "ni": 100.0, "ni_owner": 90.0, "equity_owner": 800.0, "assets": 3900.0},
          {"period": "2025", "ni": 120.0, "ni_owner": 100.0, "equity_owner": 1050.0, "assets": 4300.0}]
    cp.add_returns(q, an)
    assert q[3]["roe"] is None                                        # 1년 전 자본(2024.12) 없음
    assert q[4]["roe"] == round(100.0 / ((1100.0 + 900.0) / 2) * 100, 4)   # 2025.06~2026.03 순이익 ÷ (2026.03 + 2025.03)/2
    assert q[4]["roa"] == round(120.0 / ((4400.0 + 4000.0) / 2) * 100, 4)
    assert an[1]["roe"] == round(100.0 / ((1050.0 + 800.0) / 2) * 100, 4) and an[0]["roe"] is None


def test_owner_rule_no_minority_vs_minority():
    """지배주주 순이익 줄이 없어도 비지배지분이 0이고 지배주주 자본 = 전체 자본이면 전체 순이익 (삼성카드 모양, 가상 값).
    비지배지분이 있으면 빈칸"""
    assert cp.owner_vals({"ni": 10.0, "equity": 100.0, "equity_owner": 100.0, "nci": 0.0})[:2] == (10.0, 100.0)
    n, e, why = cp.owner_vals({"ni": 10.0, "equity": 100.0, "equity_owner": 95.0, "nci": 5.0, "ni_nci": 0.5})
    assert (n, e) == (9.5, 95.0) and why.endswith("(계산)")          # 비지배지분 있음: 전체 − 비지배지분 (사용자 'B')
    assert cp.owner_vals({"ni": 10.0, "equity": 100.0, "equity_owner": 95.0, "nci": 5.0})[0] is None   # 비지배 순이익 줄도 없으면 빈칸


def test_roe_uses_prior_year_end_from_next_report():
    """첫해 12월 ROE·연간 ROE: 1년 전 자본은 그 해 사업보고서 전기말 칸 값(prior)으로 (가상 값)"""
    q = [{"period": p, "ni": 30.0, "ni_owner": 25.0, "equity_owner": 1000.0 + i * 10, "assets": 4000.0}
         for i, p in enumerate(("2023.03", "2023.06", "2023.09", "2023.12"))]
    an = [{"period": "2023", "ni": 120.0, "ni_owner": 100.0, "equity_owner": 1030.0, "assets": 4000.0}]
    prior = {"2022.12": {"period": "2022.12", "equity_owner": 970.0, "assets": 3800.0}}
    cp.add_returns(q, an, prior)
    assert q[3]["roe"] == round(100.0 / ((1030.0 + 970.0) / 2) * 100, 4) and q[2]["roe"] is None
    assert an[0]["roe"] == round(100.0 / ((1030.0 + 970.0) / 2) * 100, 4)
    assert q[3]["roa"] == round(120.0 / ((4000.0 + 3800.0) / 2) * 100, 4)


def test_bps_uses_owner_rule():
    """BPS = 지배주주 자본 ÷ 유통주식수, 지배주주 규칙은 ROE 와 같다 (가상 값)"""
    assert cp.bps_of({"equity_owner": 800.0, "equity": 1000.0}, 10.0) == (80, False)
    assert cp.bps_of({"equity": 1000.0, "nci": 200.0}, 10.0) == (80, True)           # 전체 − 비지배지분 (계산)
    assert cp.bps_of({"equity": 1000.0, "nci": 0.0}, 10.0) == (100, False)           # 비지배지분 없음
    assert cp.bps_of({"equity": 1000.0}, None) == (None, False)


def test_div_yield_uses_confirmed_dividend_on_that_day():
    """배당수익률 = 그날까지 확정된(사업보고서 공시) 최근 연간 배당 ÷ 그날 주가 (가상 값)"""
    rows = [{"period": "2025.12", "date": "20251230", "close": 1000.0}, {"period": "2026.03", "date": "20260331", "close": 2000.0}]
    divs = [{"year": 2024, "dps": 30.0}, {"year": 2025, "dps": 50.0}]
    cp.apply_div_yield(rows, divs, {"2024.12": "20250320", "2025.12": "20260318"})
    assert rows[0]["divy"] == 3.0 and rows[0]["div_from"] == 2024        # 12월 말: 2025년 배당은 아직 확정 전
    assert rows[1]["divy"] == 2.5 and rows[1]["div_from"] == 2025


def test_eps_ttm_adj_with_split_factors():
    """4분기 안에 1주→50주 분할 (가상 값): 분기별 유통주식수가 50배 → 앞 분기 EPS 를 50으로 나눈다. 기업개요·경쟁사 같은 함수"""
    q = [{"period": p, "eps_disc": e} for p, e in (("2017.09", 5000.0), ("2017.12", 5000.0), ("2018.03", 5000.0), ("2018.06", 100.0))]
    fac = cp.split_factors([x["period"] for x in q], {"2017.09": 1e6, "2017.12": 1e6, "2018.03": 1e6, "2018.06": 5e7})
    assert fac == {"2018.06": 1.0, "2018.03": 50.0, "2017.12": 50.0, "2017.09": 50.0}
    assert cp.eps_ttm(q, 3) == 15100.0 and cp.eps_ttm_adj(q, 3, fac) == 400
    assert cp.eps_ttm_adj(q, 3, None) == 15100                # 주식 수가 없으면 보정 없음
