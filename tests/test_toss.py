"""토스증권 Open API (tribunal/external/toss.py). 네트워크 없이 가짜 응답으로"""
import pytest

from tribunal.external import toss


def test_only_read_only_market_paths_are_allowed():
    for ok in ("/api/v1/candles", "/api/v1/stocks", "/api/v1/stocks/all", "/api/v1/stocks/005930/short-selling", "/api/v1/sectors/rankings"):
        assert toss.ALLOWED.match(ok), ok
    for bad in ("/api/v1/holdings", "/api/v1/orders", "/api/v1/accounts", "/api/v1/stocks/005930/orders"):
        assert not toss.ALLOWED.match(bad), bad
    with pytest.raises(toss.TossError):
        toss.get("/api/v1/holdings")


def _candle(d, close):
    return {"timestamp": f"{d[:4]}-{d[4:6]}-{d[6:]}T00:00:00.000+09:00", "openPrice": str(close), "highPrice": str(close),
            "lowPrice": str(close), "closePrice": str(close), "volume": "10", "currency": "KRW"}


class _Fake:
    """최신순 봉 목록을 200개씩 나눠 주는 가짜 토스 (before 는 그 날 포함)"""
    def __init__(self, bars):
        self.bars = sorted(bars, key=lambda b: b[0], reverse=True)
        self.calls = 0

    def __call__(self, path, **p):
        assert path == "/api/v1/candles" and p["adjusted"] == "true"
        self.calls += 1
        lim = p.get("before", "9999")[:10].replace("-", "")
        rest = [b for b in self.bars if b[0] <= lim]
        page, more = rest[:p["count"]], rest[p["count"]:]
        nb = f"{more[0][0][:4]}-{more[0][0][4:6]}-{more[0][0][6:]}T00:00:00.000+09:00" if more else None
        return {"result": {"candles": [_candle(d, c) for d, c in page], "nextBefore": nb}}


def _days(n, start=20000101):
    from datetime import date, timedelta
    d0 = date(2000, 1, 3)
    return [(d0 + timedelta(days=i)).strftime("%Y%m%d") for i in range(n)]


def test_daily_pages_back_then_adds_only_new_days(tmp_path, monkeypatch):
    monkeypatch.setattr(toss, "CACHE", tmp_path)
    days = _days(450)
    first = _Fake([(d, 100.0) for d in days])
    rows = toss.daily("005930", fetch=first)
    assert [r["date"] for r in rows] == days and first.calls == 3            # 200 + 200 + 50
    assert toss.daily("005930", fetch=first) == rows and first.calls == 3   # 10분 안: 저장 값 그대로
    more = _days(453)
    second = _Fake([(d, 100.0) for d in more])
    rows2 = toss.daily("005930", fetch=second, fresh_sec=0)
    assert [r["date"] for r in rows2] == more and second.calls == 1         # 새 날 3개만 한 번에


def test_daily_refetches_all_when_adjusted_prices_change(tmp_path, monkeypatch):
    """그 사이 분할로 과거 수정주가가 바뀌면 (저장된 마지막 날 종가가 다르면) 전체를 다시 받는다"""
    monkeypatch.setattr(toss, "CACHE", tmp_path)
    days = _days(250)
    toss.daily("005930", fetch=_Fake([(d, 100.0) for d in days]))
    split = _Fake([(d, 20.0) for d in _days(251)])
    rows = toss.daily("005930", fetch=split, fresh_sec=0)
    assert all(r["close"] == 20.0 for r in rows) and len(rows) == 251


def test_bad_ticker_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(toss, "CACHE", tmp_path)
    with pytest.raises(toss.TossError):
        toss.daily("../x")


def test_flow_rows_merge_endpoints_by_date_and_turn_ratios_into_percent():
    raw = {"investor-trading": [{"date": "2026-11-20", "individual": {"netBuyVolume": "-18964"}, "foreigner": {"netBuyVolume": "3911"},
                                 "institution": {"netBuyVolume": "15128", "breakdown": {"pensionFund": {"netBuyVolume": "783"}}},
                                 "otherCorporation": None, "foreignerHolding": {"holdingRate": "0.0468"}}],
           "short-selling": [{"date": "2026-11-20", "shortSellingVolume": "10", "shortSellingVolumeRate": "0.01234"}],
           "credit-trades": [{"date": "2026-11-19", "marginLoan": {"balanceQuantity": "32417", "balanceRate": "0.0002"}, "stockLoan": None}],
           "securities-lending": [], "program-trades": []}
    rows = toss.flow_rows(raw)
    assert [r["d"] for r in rows] == ["20261119", "20261120"]
    x = rows[1]
    assert x["ind"] == -18964 and x["inst"] == 15128 and x["corp"] is None and x["brk"] == {"pensionFund": 783}
    assert round(x["fhold"], 4) == 4.68 and round(x["short_rate"], 3) == 1.234
    assert rows[0]["margin_bal"] == 32417 and round(rows[0]["margin_rate"], 4) == 0.02 and rows[0]["sloan_bal"] is None


def test_flows_pages_each_endpoint_and_reuses_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(toss, "CACHE", tmp_path)
    calls = []

    def fake(path, **p):
        calls.append(path)
        if path.endswith("/warnings"):
            return {"result": [{"warningType": "OVERHEATED", "exchange": "KRX", "startDate": "2026-11-20", "endDate": None}]}
        page = 0 if "until" not in p else 1
        return {"result": {"records": [{"date": f"2026-0{9 - page}-{i + 1:02d}"} for i in range(100)] if page < 2 else [],
                           "nextUntil": "2026-08-31" if page == 0 else None}}
    out = toss.flows("029780", days=150, fetch=fake)
    assert len(calls) == 5 * 2 + 1 and out["warnings"][0]["type"] == "OVERHEATED"
    assert toss.flows("029780", fetch=fake)["fetched_at"] == out["fetched_at"] and len(calls) == 11


INDEX = {"fetched_at": "x", "sectors": [{"id": "1", "name": "금융", "depth": 0, "parent": None},
                                        {"id": "2", "name": "카드", "depth": 1, "parent": "1"},
                                        {"id": "3", "name": "자동차", "depth": 0, "parent": None}],
         "members": {"1": ["029780", "005380", "105560"], "2": ["029780", "999990"], "3": ["005380"]}}


def test_own_sectors_keep_only_smallest_sector_and_its_path():
    """구성 종목 목록은 하위 섹터를 포함한다: 자식 섹터에도 있으면 부모는 빼고, 자식이 없으면 부모 그대로 (현대차 '금융')"""
    assert [(s["name"], s["path"]) for s in toss.own_sectors(INDEX, "029780")] == [("카드", ["금융", "카드"])]
    assert sorted(s["name"] for s in toss.own_sectors(INDEX, "005380")) == ["금융", "자동차"]
    assert toss.own_sectors(INDEX, "000000") == []


def test_sector_view_ranks_by_market_cap_and_turns_rates_into_percent(tmp_path, monkeypatch):
    monkeypatch.setattr(toss, "CACHE", tmp_path)

    def fake(path, **p):
        if path == "/api/v1/sectors/2":
            return {"result": {"summary": "카드사", "performance": {"changeRates": [{"duration": "1y", "changeRate": "0.4785"}]}}}
        return {"result": {"stocks": [{"symbol": "999990", "name": "가상카드", "marketCapitalization": "9"},
                                      {"symbol": "029780", "name": "삼성카드", "marketCapitalization": "5"}]}}
    v = toss.sector_view("029780", fetch=fake, index=INDEX)
    s = v["sectors"][0]
    assert s["rank"] == 2 and s["count"] == 2 and round(s["perf"]["1y"], 2) == 47.85 and s["summary"] == "카드사"


def test_market_amounts_are_buy_minus_sell_and_pension_is_inside_institution():
    rec = {"date": "2026-11-24", "individual": {"buyAmount": "100", "sellAmount": "250"}, "foreigner": {"buyAmount": "90", "sellAmount": "40"},
           "institution": {"buyAmount": "70", "sellAmount": "20", "breakdown": {"pensionFund": {"buyAmount": "30", "sellAmount": "10"}}},
           "otherCorporation": None}
    assert toss._amounts(rec) == {"d": "20261124", "ind": -150.0, "frn": 50.0, "inst": 50.0, "corp": None, "pension": 20.0}


def test_peers_from_toss_sector():
    """경쟁사 = 토스 섹터 (전면 개편 1차): 구성 종목이 가장 많은 섹터, 시세·DART 회사가 있는 종목만 시가총액 순 (가상 값)"""
    from tribunal.external import peers
    view = {"sectors": [{"id": "1", "name": "작은섹터", "count": 2, "members": [{"ticker": "000001", "mktcap": 5}, {"ticker": "000002", "mktcap": 9}]},
                        {"id": "2", "name": "큰섹터", "count": 4, "members": [{"ticker": "000001", "mktcap": 5}, {"ticker": "000003", "mktcap": 30},
                                                                          {"ticker": "000004", "mktcap": 20}, {"ticker": "000005", "mktcap": 99}]}]}
    sec = peers.main_sector(view)
    assert sec["name"] == "큰섹터"                                       # 3곳 이상인 섹터 중 가장 작은 것 (작은섹터는 2곳)
    view2 = {"ticker": "000001", "sectors": [view["sectors"][1], {"id": "3", "name": "넓은섹터", "count": 9, "members": []}]}
    assert peers.main_sector(view2)["name"] == "큰섹터"
    market = {t: {} for t in ("000001", "000003", "000004")}            # 000005 는 시세 없음
    index = {t: {"name": t} for t in ("000001", "000003", "000004", "000005")}
    assert peers.choose_toss(sec, "000001", market, index, limit=3) == ["000003", "000004", "000001"]
    assert peers.choose_toss(sec, "000001", market, index, limit=2) == ["000001", "000003"]
    assert peers.main_sector({"sectors": []}) is None


def test_build_peers_for_chosen_sector(monkeypatch):
    """화면에서 고른 섹터(sector_id)로 경쟁사 표 (가상 값)"""
    from tribunal.external import peers
    view = {"ticker": "000001", "sectors": [
        {"id": "1", "name": "반도체", "count": 3, "members": [{"ticker": "000001", "mktcap": 9}, {"ticker": "000002", "mktcap": 5}, {"ticker": "000009", "mktcap": 1}]},
        {"id": "2", "name": "가전", "count": 4, "members": [{"ticker": "000001", "mktcap": 9}, {"ticker": "000003", "mktcap": 7},
                                                              {"ticker": "000004", "mktcap": 2}, {"ticker": "000009", "mktcap": 1}]}]}
    index = {t: {"corp_code": t, "name": t, "induty": "26"} for t in ("000001", "000002", "000003", "000004", "000009")}
    monkeypatch.setattr(peers, "industry_index", lambda d: index)
    monkeypatch.setattr(peers, "latest_market", lambda k, today=None: {t: {"MKTCAP": "1"} for t in index})
    monkeypatch.setattr(peers, "row", lambda d, t, info, mk, ins: {"ticker": t, "name": info["name"]})
    out = peers.build("000001", dart=object(), krx=object(), sector_view=view, sector_id="2")
    assert out["sector"]["name"] == "가전" and [r["ticker"] for r in out["rows"]] == ["000001", "000003", "000004", "000009"]
    assert peers.build("000001", dart=object(), krx=object(), sector_view=view)["sector"]["name"] == "반도체"


def test_sector_perf_fills_cut_off_rankings(monkeypatch, tmp_path):
    """순위 목록에서 잘린 기간(많이 내린 섹터)은 섹터 상세의 등락률로 채운다 (가상 값)"""
    monkeypatch.setattr(toss, "CACHE", tmp_path)
    ranks = {d: [{"id": "1", "name": "가상섹터", "rate": 1.0}] for d in toss.SECTOR_DUR}
    ranks["3mo"] = []                                                     # 3개월 목록에서 잘림
    calls = []

    def fake(path, **kw):
        calls.append(path)
        return {"result": {"performance": {"changeRates": [{"duration": "3mo", "changeRate": "-0.2599"}]}}}
    out = toss._fill_sector_perf(ranks, fake)
    assert round(out["1"]["3mo"], 2) == -25.99 and out["1"]["1d"] == 1.0 and calls == ["/api/v1/sectors/1"]
    toss._fill_sector_perf(ranks, fake)                                   # 1시간 안: 상세를 다시 부르지 않는다
    assert len(calls) == 1
