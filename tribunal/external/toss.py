"""토스증권 Open API (2026-11-24, 사용자): 시세·종목 정보만 읽는다 (읽기 전용).

계좌·자산·주문·조건주문 API 는 이 파일에서 부를 수 없다 (허용 경로 목록 밖이면 바로 오류).
키: .env 의 TOSS_CLIENT_ID, TOSS_CLIENT_SECRET (get_key 로만 읽고 화면·로그에 찍지 않는다).
토큰은 클라이언트당 1개만 유효해서(새로 받으면 앞 토큰이 무효) 만료 전까지 .cache/toss/token.json 에 두고 다시 쓴다.
허용 IP 에 없는 곳에서 부르면 403 이다.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Dict, List, Optional

from ..llm import ROOT, get_key

BASE = "https://openapi.tossinvest.com"
CACHE = ROOT / ".cache" / "toss"
EXTRA_FILE = ROOT / "library" / "_common" / "extra_sectors.json"   # 사용자가 종목에 직접 붙인 섹터 (2026-11-26, 지주사로만 묶인 HD한국조선해양 등)


def extra_sectors(ticker: str) -> List[str]:
    from .dart import load_json
    d = load_json(EXTRA_FILE) if EXTRA_FILE.exists() else None
    return [str(x) for x in ((d or {}).get(ticker) or [])]


def set_extra_sectors(ticker: str, ids: List[str]) -> List[str]:
    """종목에 직접 붙인 섹터를 저장한다 (최대 6개, 섹터 목록에 있는 것만)"""
    from .dart import load_json, save_atomic
    if not re.fullmatch(r"[0-9A-Z]{6}", ticker or ""):
        raise TossError("종목코드가 올바르지 않습니다.")
    known = {s["id"] for s in sector_index().get("sectors", [])}
    clean = list(dict.fromkeys(str(x) for x in ids if str(x) in known))[:6]
    d = (load_json(EXTRA_FILE) if EXTRA_FILE.exists() else None) or {}
    if clean:
        d[ticker] = clean
    else:
        d.pop(ticker, None)
    EXTRA_FILE.parent.mkdir(parents=True, exist_ok=True)
    save_atomic(EXTRA_FILE, json.dumps(d, ensure_ascii=False))
    return clean


def sector_list() -> List[dict]:
    """섹터 직접 추가용 전체 목록: id, 이름, 경로(대분류 > 중분류 > …), 국내 구성 종목 수"""
    idx = sector_index()
    by = {s["id"]: s for s in idx.get("sectors", [])}

    def path(x):
        names = [x["name"]]
        while x.get("parent") and x["parent"] in by:
            x = by[x["parent"]]
            names.insert(0, x["name"])
        return names
    mem = idx.get("members") or {}
    return [{"id": x["id"], "name": x["name"], "path": path(x), "count": len(mem.get(x["id"]) or [])} for x in idx.get("sectors", []) if mem.get(x["id"])]
# 읽기 전용 시세·종목 정보 경로만 (GET)
ALLOWED = re.compile(r"^/api/v1/(stocks(/all|/[0-9A-Za-z.]+/(warnings|investor-trading|program-trades|short-selling|credit-trades|securities-lending))?"
                     r"|prices|orderbook|trades|price-limits|candles|exchange-rate|market-calendar/(KR|US)"
                     r"|rankings(/investor-trading)?|market-indicators/[^/]+(/(candles|investor-trading))?|market-indicators/prices"
                     r"|sectors(/[^/]+(/(stocks|etfs))?)?|sectors/rankings)$")


class TossError(RuntimeError):
    """연결·권한 문제. 메시지에 키·토큰을 넣지 않는다."""


def _token(force: bool = False) -> str:
    f = CACHE / "token.json"
    if not force and f.exists():
        try:
            saved = json.loads(f.read_text(encoding="utf-8"))
            if saved.get("expires_at", 0) - time.time() > 60:
                return saved["access_token"]
        except (ValueError, KeyError):
            pass
    body = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": get_key("TOSS_CLIENT_ID"),
                                   "client_secret": get_key("TOSS_CLIENT_SECRET")}).encode()
    req = urllib.request.Request(BASE + "/oauth2/token", data=body, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=20) as res:      # noqa: S310 (정해진 주소만)
            out = json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise TossError(f"토스증권 토큰을 받지 못했습니다 (HTTP {e.code}{', 허용 IP 확인' if e.code == 403 else ''}).") from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise TossError(f"토스증권에 연결하지 못했습니다 ({type(e).__name__}).") from None
    tok = out.get("access_token")
    if not tok:
        raise TossError("토스증권 토큰 응답에 토큰이 없습니다.")
    CACHE.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"access_token": tok, "expires_at": time.time() + float(out.get("expires_in") or 3600),
                             "fetched_at": datetime.now().isoformat(timespec="seconds")}), encoding="utf-8")
    return tok


def get(path: str, **params) -> dict:
    """시세·종목 정보 GET. path 예: '/api/v1/stocks'. 허용 목록 밖 경로(계좌·주문 등)는 부르지 않는다"""
    if not ALLOWED.match(path):
        raise TossError(f"읽기 전용 시세·종목 정보 경로가 아닙니다: {path}")
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    for attempt in range(3):
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {_token(force=attempt > 0)}"})
        try:
            with urllib.request.urlopen(req, timeout=20) as res:  # noqa: S310
                return json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            code = ""
            try:
                code = json.loads(e.read().decode("utf-8")).get("error", {}).get("code", "")
            except ValueError:
                pass
            if e.code == 401 and attempt < 2:                     # 토큰 만료·교체: 새로 받아 한 번 더
                continue
            if e.code == 429 and attempt < 2:
                time.sleep(float(e.headers.get("Retry-After") or 1))
                continue
            raise TossError(f"토스증권 오류 HTTP {e.code} {code}".strip()) from None
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == 2:
                raise TossError(f"토스증권에 연결하지 못했습니다 ({type(e).__name__}).") from None
            time.sleep(1 + attempt)
    raise TossError("토스증권 응답을 받지 못했습니다.")


# ---------- 수급 (2026-11-24, 사용자 3단계): 투자자별·프로그램·공매도·신용·대차 일별, 매수 유의사항 ----------
FLOW_EPS = ("investor-trading", "program-trades", "short-selling", "credit-trades", "securities-lending")


def _n(v) -> Optional[float]:
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def _net(x) -> Optional[float]:
    return _n((x or {}).get("netBuyVolume")) if x else None


def _pct(v) -> Optional[float]:
    v = _n(v)
    return v * 100 if v is not None else None


def _records(ticker: str, ep: str, days: int, fetch) -> List[dict]:
    out: List[dict] = []
    until = None
    for _ in range(max(1, days // 100 + 1)):
        p = {"count": 100}
        if until:
            p["until"] = until
        res = fetch(f"/api/v1/stocks/{ticker}/{ep}", **p).get("result") or {}
        out += res.get("records") or []
        until = res.get("nextUntil")
        if not until or len(out) >= days:
            break
    return out[:days]


def flow_rows(raw: Dict[str, List[dict]]) -> List[dict]:
    """API 기록 -> 날짜별 한 줄 (오래된 날부터). 비율은 % (API 는 0.0468 같은 소수). 없는 값은 None"""
    by: Dict[str, dict] = {}

    def row(rec):
        d = str(rec.get("date", ""))[:10].replace("-", "")
        return by.setdefault(d, {"d": d})
    for r in raw.get("investor-trading", []):
        x = row(r)
        inst = r.get("institution") or {}
        x.update(ind=_net(r.get("individual")), frn=_net(r.get("foreigner")), inst=_net(inst), corp=_net(r.get("otherCorporation")),
                 brk={k: _net(v) for k, v in (inst.get("breakdown") or {}).items()} or None,
                 fhold=_pct((r.get("foreignerHolding") or {}).get("holdingRate")))
    for r in raw.get("program-trades", []):
        row(r).update(arb=_net(r.get("arbitrage")), nonarb=_net(r.get("nonArbitrage")))
    for r in raw.get("short-selling", []):
        row(r).update(short_vol=_n(r.get("shortSellingVolume")), short_amt=_n(r.get("shortSellingAmount")),
                      short_rate=_pct(r.get("shortSellingVolumeRate")))
    for r in raw.get("credit-trades", []):
        m, s = r.get("marginLoan") or {}, r.get("stockLoan") or {}
        row(r).update(margin_bal=_n(m.get("balanceQuantity")), margin_rate=_pct(m.get("balanceRate")),
                      sloan_bal=_n(s.get("balanceQuantity")))
    for r in raw.get("securities-lending", []):
        row(r).update(lend_bal=_n(r.get("balanceQuantity")), lend_amt=_n(r.get("balanceAmount")))
    return [by[d] for d in sorted(by) if d]


def flows(ticker: str, days: int = 250, fetch=None, fresh_sec: int = 1800) -> dict:
    """한 종목의 수급 1년치. .cache/toss/flows/<종목>.json 에 두고 30분 안이면 그대로 쓴다"""
    from .dart import load_json, save_atomic
    if not re.fullmatch(r"[0-9A-Z]{6}", ticker or ""):
        raise TossError("종목코드가 올바르지 않습니다.")
    f = CACHE / "flows" / f"{ticker}.json"
    saved = load_json(f) if f.exists() else None
    if saved and time.time() - float(saved.get("fetched_ts") or 0) < fresh_sec:
        return saved
    fetch = fetch or get
    raw = {ep: _records(ticker, ep, days, fetch) for ep in FLOW_EPS}
    warn = fetch(f"/api/v1/stocks/{ticker}/warnings").get("result") or []
    out = {"ticker": ticker, "fetched_ts": time.time(), "fetched_at": datetime.now().isoformat(timespec="seconds"),
           "rows": flow_rows(raw),
           "warnings": [{"type": w.get("warningType"), "exchange": w.get("exchange"), "start": w.get("startDate"), "end": w.get("endDate")}
                        for w in warn if isinstance(w, dict)]}
    CACHE.joinpath("flows").mkdir(parents=True, exist_ok=True)
    save_atomic(f, json.dumps(out, ensure_ascii=False))
    return out


# ---------- 섹터 (2026-11-24, 사용자 4단계): 토스증권 자체 산업 분류. 종목 -> 섹터 주소가 없어 전체를 훑어 색인 ----------
def _rate(v) -> Optional[float]:
    return _pct(v)


def sector_index(fetch=None, max_age_days: int = 7) -> dict:
    """섹터 목록 + 섹터마다 구성 종목 코드 (국내). .cache/toss/sectors.json 에 7일. 약 300번 호출 (처음 2~3분)"""
    from .dart import load_json, save_atomic
    f = CACHE / "sectors.json"
    saved = load_json(f) if f.exists() else None
    if saved and time.time() - float(saved.get("fetched_ts") or 0) < max_age_days * 86400:
        return saved
    fetch = fetch or get
    secs = (fetch("/api/v1/sectors").get("result") or {}).get("sectors") or []
    members: Dict[str, List[str]] = {}
    for s in secs:
        res = fetch(f"/api/v1/sectors/{s['sectorId']}/stocks", marketCountry="KR").get("result") or {}
        members[str(s["sectorId"])] = [x["symbol"] for x in res.get("stocks") or [] if x.get("symbol")]
    out = {"fetched_ts": time.time(), "fetched_at": datetime.now().isoformat(timespec="seconds"),
           "sectors": [{"id": str(s["sectorId"]), "name": s.get("name"), "depth": s.get("depth"),
                        "parent": str(s["parentSectorId"]) if s.get("parentSectorId") is not None else None} for s in secs],
           "members": members}
    CACHE.mkdir(parents=True, exist_ok=True)
    save_atomic(f, json.dumps(out, ensure_ascii=False))
    return out


def own_sectors(index: dict, ticker: str) -> List[dict]:
    """그 종목이 속한 가장 작은 섹터들 (구성 종목 목록이 하위 섹터를 포함하므로, 자식 섹터에도 있으면 부모는 뺀다)"""
    by = {s["id"]: s for s in index.get("sectors", [])}
    has = {sid for sid, m in (index.get("members") or {}).items() if ticker in m}
    parents = {by[sid]["parent"] for sid in has if sid in by and by[sid]["parent"]}
    leaf = [by[sid] for sid in has if sid in by and sid not in parents]

    def path(s):
        names = [s["name"]]
        while s.get("parent") and s["parent"] in by:
            s = by[s["parent"]]
            names.insert(0, s["name"])
        return names
    return sorted(({**s, "path": path(s)} for s in leaf), key=lambda s: (-(s["depth"] or 0), s["name"] or ""))


def sector_view(ticker: str, fetch=None, index: Optional[dict] = None, fresh_sec: int = 6 * 3600) -> dict:
    """종목의 토스 섹터: 섹터마다 한 줄 설명, 기간별 등락률(%), 구성 종목(시가총액 순, 오늘 값), 이 종목 순위.
    한 종목이 여러 섹터에 속한다 (삼성전자 6곳). .cache/toss/sector_view/<종목>.json 에 6시간"""
    from .dart import load_json, save_atomic
    if not re.fullmatch(r"[0-9A-Z]{6}", ticker or ""):
        raise TossError("종목코드가 올바르지 않습니다.")
    f = CACHE / "sector_view" / f"{ticker}.json"
    saved = load_json(f) if f.exists() and index is None else None
    if saved and time.time() - float(saved.get("fetched_ts") or 0) < fresh_sec and (saved.get("extra") or []) == extra_sectors(ticker):
        return saved
    try:
        return _sector_view(ticker, fetch, index, f)
    except TossError:
        if saved:                                     # 토스가 막히면(허용 IP 등) 받아 둔 자료 (섹터 구성은 자주 바뀌지 않는다)
            return {**saved, "stale": True}
        raise


def _sector_view(ticker: str, fetch, index: Optional[dict], f) -> dict:
    from .dart import save_atomic
    fetch = fetch or get
    index = index or sector_index(fetch=fetch)
    out = []
    own = own_sectors(index, ticker)[:8]
    have = {s["id"] for s in own}
    extra = extra_sectors(ticker)
    by = {s["id"]: s for s in index.get("sectors", [])}
    for sid in extra:                                  # 사용자가 직접 붙인 섹터 (종목이 그 섹터 구성 종목이 아니어도)
        if sid in by and sid not in have:
            x, names = by[sid], [by[sid]["name"]]
            while x.get("parent") and x["parent"] in by:
                x = by[x["parent"]]
                names.insert(0, x["name"])
            own.append({**by[sid], "path": names, "extra": True})
    for s in own:
        d = fetch(f"/api/v1/sectors/{s['id']}", marketCountry="KR").get("result") or {}
        st = (fetch(f"/api/v1/sectors/{s['id']}/stocks", marketCountry="KR").get("result") or {}).get("stocks") or []
        mem = [{"ticker": x.get("symbol"), "name": x.get("name"), "mktcap": _n(x.get("marketCapitalization")),
                "amount": _n(x.get("tradingAmount"))} for x in st]
        rank = next((i + 1 for i, x in enumerate(mem) if x["ticker"] == ticker), None)
        out.append({"id": s["id"], "name": s["name"], "path": s["path"], "summary": d.get("summary"), "extra": bool(s.get("extra")),
                    "perf": {r.get("duration"): _rate(r.get("changeRate")) for r in ((d.get("performance") or {}).get("changeRates") or [])},
                    "members": mem, "rank": rank, "count": len(mem)})
    res = {"ticker": ticker, "index_at": index.get("fetched_at"), "fetched_ts": time.time(), "extra": extra,
           "fetched_at": datetime.now().isoformat(timespec="seconds"), "sectors": out}
    CACHE.joinpath("sector_view").mkdir(parents=True, exist_ok=True)
    save_atomic(f, json.dumps(res, ensure_ascii=False))
    return res


# ---------- 시장 화면 (2026-11-24, 사용자 4단계): 지수·국채 금리·환율·시장 투자자별 매매대금·섹터 등락 ----------
INDICES = ("KOSPI", "KOSDAQ")
BONDS = ("KR_BOND_2Y", "KR_BOND_3Y", "KR_BOND_5Y", "KR_BOND_10Y", "KR_BOND_20Y", "KR_BOND_30Y")


def _ind_daily(sym: str, pages: int, fetch) -> List[dict]:
    out: Dict[str, dict] = {}
    before = None
    for _ in range(pages):
        p = {"interval": "1d", "count": 200}
        if before:
            p["before"] = before
        res = fetch(f"/api/v1/market-indicators/{sym}/candles", **p).get("result") or {}
        for c in res.get("candles") or []:
            b = _bar(c)
            if b and b["close"]:
                out[b["date"]] = {"d": b["date"], "o": b["open"], "h": b["high"], "l": b["low"], "c": b["close"]}
        before = res.get("nextBefore")
        if not before:
            break
    return [out[d] for d in sorted(out)]


def _ind_history(sym: str, fetch) -> List[dict]:
    """지수·국채 금리 일봉 전체 (2026-11-25 사용자: 지수 '전체 기간'). 토스는 지수 2014-07부터, 국채 2019부터.
    처음 한 번 전부(지수 약 16번 호출), 그 뒤에는 최근 200일만 받아 합친다. .cache/toss/indices/<기호>.json"""
    from .dart import load_json, save_atomic
    f = CACHE / "indices" / f"{sym}.json"
    saved = (load_json(f) or {}).get("rows") if f.exists() else None
    if saved:
        new = _ind_daily(sym, 1, fetch)
        by = {r["d"]: r for r in saved}
        by.update({r["d"]: r for r in new})
        rows = [by[d] for d in sorted(by)]
    else:
        rows = _ind_daily(sym, 80, fetch)
    if rows:
        CACHE.joinpath("indices").mkdir(parents=True, exist_ok=True)
        save_atomic(f, json.dumps({"rows": rows, "fetched_at": datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False))
    return rows


def _amounts(rec: dict) -> dict:
    def net(x):
        b, s = _n((x or {}).get("buyAmount")), _n((x or {}).get("sellAmount"))
        return b - s if b is not None and s is not None else None
    inst = rec.get("institution") or {}
    return {"d": str(rec.get("date", ""))[:10].replace("-", ""), "ind": net(rec.get("individual")), "frn": net(rec.get("foreigner")),
            "inst": net(inst), "corp": net(rec.get("otherCorporation")),
            "pension": net((inst.get("breakdown") or {}).get("pensionFund"))}


SECTOR_DUR = ("1d", "1w", "1mo", "3mo", "6mo", "1y")


def _fill_sector_perf(ranks: Dict[str, List[dict]], fetch, fresh_sec: int = 3600) -> Dict[str, dict]:
    """섹터 × 기간 등락률 (2026-11-26 사용자 '빈칸은 뭐야'): 순위 목록은 기간마다 오른 순 99개까지라 많이 내린 섹터가 잘린다
    (예: 화장품 3개월 −25.99%, 목록 끝 −24.49%). 한 기간이라도 빠진 섹터는 섹터 상세(/sectors/{id})의 기간별 등락률로 채운다.
    상세 값은 .cache/toss/sector_perf.json 에 1시간. {섹터 id: {'name', '1d': %, ...}}"""
    from .dart import load_json, save_atomic
    out: Dict[str, dict] = {}
    for dur, rk in ranks.items():
        for r in rk:
            out.setdefault(r["id"], {"name": r["name"]})[dur] = r["rate"]
    f = CACHE / "sector_perf.json"
    saved = load_json(f) if f.exists() else None
    cache = (saved or {}).get("perf") or {} if saved and time.time() - float(saved.get("fetched_ts") or 0) < fresh_sec else {}
    for sid, e in out.items():
        if all(e.get(d) is not None for d in SECTOR_DUR):
            continue
        if sid not in cache:
            try:
                d = fetch(f"/api/v1/sectors/{sid}", marketCountry="KR").get("result") or {}
            except TossError:
                continue
            cache[sid] = {r.get("duration"): _rate(r.get("changeRate")) for r in ((d.get("performance") or {}).get("changeRates") or [])}
        for dur in SECTOR_DUR:
            if e.get(dur) is None and cache[sid].get(dur) is not None:
                e[dur] = cache[sid][dur]
    CACHE.mkdir(parents=True, exist_ok=True)
    save_atomic(f, json.dumps({"fetched_ts": (saved or {}).get("fetched_ts") if cache and saved and time.time() - float(saved.get("fetched_ts") or 0) < fresh_sec else time.time(),
                               "perf": cache}, ensure_ascii=False))
    return out


def market(fetch=None, fresh_sec: int = 600) -> dict:
    """시장 화면 자료. .cache/toss/market.json 에 10분. 약 30번 호출"""
    from .dart import load_json, save_atomic
    f = CACHE / "market.json"
    saved = load_json(f) if f.exists() else None
    if saved and time.time() - float(saved.get("fetched_ts") or 0) < fresh_sec:
        return saved
    fetch = fetch or get
    now = {x.get("symbol"): _n(x.get("lastPrice")) for x in
           (fetch("/api/v1/market-indicators/prices", symbols=",".join(INDICES + BONDS)).get("result") or [])}
    series = {s: _ind_history(s, fetch) for s in INDICES + BONDS}            # 전체 기간 (지수 2014-07부터, 국채 2019부터)
    fx = fetch("/api/v1/exchange-rate", baseCurrency="USD", quoteCurrency="KRW").get("result") or {}
    flows = {}
    for s in INDICES:
        flows[s] = {iv: [_amounts(r) for r in reversed(
            (fetch(f"/api/v1/market-indicators/{s}/investor-trading", interval=iv, count=n).get("result") or {}).get("records") or [])]
            for iv, n in (("1d", 60), ("1mo", 24))}
    ranks = {}
    for dur in ("1d", "1w", "1mo", "3mo", "6mo", "1y"):
        rk = (fetch("/api/v1/sectors/rankings", type="TOP_GAINERS", marketCountry="KR", duration=dur).get("result") or {}).get("rankings") or []
        ranks[dur] = [{"id": str(r.get("sectorId")), "name": r.get("name"), "rate": _rate(r.get("changeRate")),
                       "mktcap": _n(r.get("marketCapitalization"))} for r in rk]
    perf = _fill_sector_perf(ranks, fetch)
    out = {"fetched_ts": time.time(), "fetched_at": datetime.now().isoformat(timespec="seconds"),
           "now": now, "series": series, "sector_perf": perf,
           "fx": {"rate": _n(fx.get("rate")), "mid": _n(fx.get("midRate")), "change": fx.get("rateChangeType"), "at": fx.get("validFrom")},
           "flows": flows, "sector_ranks": ranks}
    CACHE.mkdir(parents=True, exist_ok=True)
    save_atomic(f, json.dumps(out, ensure_ascii=False))
    return out


# ---------- 일봉 (2026-11-24, 사용자 1단계): 수정주가 일봉을 종목별로 저장해 두고 새 날만 더 받는다 ----------
def _bar(c: dict) -> Optional[dict]:
    try:
        return {"date": c["timestamp"][:10].replace("-", ""), "open": float(c["openPrice"]), "high": float(c["highPrice"]),
                "low": float(c["lowPrice"]), "close": float(c["closePrice"]), "volume": float(c.get("volume") or 0)}
    except (KeyError, TypeError, ValueError):
        return None


def _pages(ticker: str, stop: Optional[str] = None, fetch=None) -> List[dict]:
    """최근 봉부터 과거로 200개씩. stop(YYYYMMDD) 날짜까지 받으면 멈춘다 (그 날 포함). 오래된 날부터 돌려준다"""
    fetch = fetch or get
    out: Dict[str, dict] = {}
    before = None
    for _ in range(100):                                   # 200 x 100 = 2만 거래일 (약 80년) 넘으면 멈춤
        p = {"symbol": ticker, "interval": "1d", "count": 200, "adjusted": "true"}
        if before:
            p["before"] = before
        res = fetch("/api/v1/candles", **p).get("result") or {}
        for c in res.get("candles") or []:
            b = _bar(c)
            if b and b["close"] > 0:
                out[b["date"]] = b
        before = res.get("nextBefore")
        if not before or (stop and out and min(out) <= stop):
            break
    return [out[d] for d in sorted(out)]


def last_prices(tickers: List[str], fetch=None, fresh_sec: int = 600) -> Dict[str, dict]:
    """경쟁사 표 현재가 (2026-11-26 사용자: PER = 현재 주가 ÷ EPS 를 경쟁사 표에도). 종목마다 최근 일봉 2개만
    (1번 호출, 장중이면 지금 가격). .cache/toss/last/<종목>.json 에 10분. {종목: {d, c(현재가), prev(전일 종가)}}. 못 받은 종목은 뺀다"""
    from .dart import load_json, save_atomic
    fetch = fetch or get
    out: Dict[str, dict] = {}
    for t in tickers[:40]:
        if not re.fullmatch(r"[0-9A-Z]{6}", t or ""):
            continue
        f = CACHE / "last" / f"{t}.json"
        saved = load_json(f) if f.exists() else None
        if saved and time.time() - float(saved.get("fetched_ts") or 0) < fresh_sec:
            out[t] = {k: saved[k] for k in ("d", "c", "prev")}
            continue
        try:
            res = fetch("/api/v1/candles", symbol=t, interval="1d", count=2, adjusted="true").get("result") or {}
        except TossError:
            if saved:                                     # 막히면 받아 둔 값
                out[t] = {k: saved[k] for k in ("d", "c", "prev")}
            continue
        bars = sorted((b for b in (_bar(c) for c in res.get("candles") or []) if b and b["close"] > 0), key=lambda b: b["date"])
        if not bars:
            continue
        hit = {"d": bars[-1]["date"], "c": bars[-1]["close"], "prev": bars[-2]["close"] if len(bars) > 1 else None}
        out[t] = hit
        CACHE.joinpath("last").mkdir(parents=True, exist_ok=True)
        save_atomic(f, json.dumps({**hit, "fetched_ts": time.time()}, ensure_ascii=False))
    return out


def daily(ticker: str, fetch=None, fresh_sec: int = 600) -> List[dict]:
    """수정주가 일봉 전체 (오래된 날부터). .cache/toss/candles/<종목>.json 에 두고, 10분 안에 받았으면 그대로 쓴다.
    그 뒤에는 저장된 마지막 날부터 새로 받고(보통 1번 호출, 장중이면 오늘 봉이 바뀜), 그 날 종가가 저장 값과 다르면
    (그 사이 분할·증자로 수정주가가 바뀜) 전체를 다시 받는다"""
    from .dart import load_json, save_atomic
    if not re.fullmatch(r"[0-9A-Z]{6}", ticker or ""):
        raise TossError("종목코드가 올바르지 않습니다.")
    f = CACHE / "candles" / f"{ticker}.json"
    saved = load_json(f) if f.exists() else None
    rows: List[dict] = (saved or {}).get("rows") or []
    if rows and time.time() - float(saved.get("fetched_ts") or 0) < fresh_sec:
        return rows
    if rows:
        last = rows[-1]
        new = _pages(ticker, stop=last["date"], fetch=fetch)
        same = next((r for r in new if r["date"] == last["date"]), None)
        if same and abs(same["close"] - last["close"]) <= max(1.0, last["close"] * 0.0005):
            rows = rows[:-1] + [r for r in new if r["date"] >= last["date"]]
        else:
            rows = _pages(ticker, fetch=fetch)
    else:
        rows = _pages(ticker, fetch=fetch)
    if rows:
        CACHE.joinpath("candles").mkdir(parents=True, exist_ok=True)
        save_atomic(f, json.dumps({"ticker": ticker, "fetched_ts": time.time(), "adjusted": True, "rows": rows}, ensure_ascii=False))
    return rows
