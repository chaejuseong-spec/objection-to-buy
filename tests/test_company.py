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
    q = [{"period": f"2026.{m:02d}", "ni_owner": 1e11, "equity_owner": 2e12} for m in (3, 6, 9)]
    q = [{"period": "2025.09", "ni_owner": 1e11, "equity_owner": 2e12}, {"period": "2025.12", "ni_owner": 1e11, "equity_owner": 2e12}] + q
    m = cp.metrics({"quarter": q}, {"close": 10000.0, "shares": 1e8})
    assert m["eps"] == 4000.0 and m["per"] == 2.5 and m["bps"] == 20000.0 and m["pbr"] == 0.5 and round(m["roe"], 6) == 20.0
    q[-1]["ni_owner"] = None
    assert cp.metrics({"quarter": q}, {"close": 10000.0, "shares": 1e8})["eps"] is None     # 하나라도 없으면 지어내지 않는다


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
