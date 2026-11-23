"""공공데이터포털 금융위원회_주식시세정보 (2026-11-23): 한 종목의 과거 일별 시세를 기간으로 받는다.

주가 차트의 '전체'(최근 1년보다 앞)에 쓴다. 키: .env 의 DATA_GO_KR_KEY (일반 인증키, Decoding).
키가 없으면 None 을 돌려주고 차트는 최근 1년만 보인다. 받은 기록은 종목마다 저장하고, 다음에는 빠진 앞뒤만 더 받는다.
요청에 사용자 정보를 넣지 않는다. 키는 화면·로그에 찍지 않는다.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from typing import List, Optional

from ..llm import ROOT

URL = "https://apis.data.go.kr/1160100/service/GetStockSecuritiesInfoService/getStockPriceInfo"
KEY_ENV = "DATA_GO_KR_KEY"
CACHE = ROOT / ".cache" / "datagokr"
TIMEOUT = 30
FIRST = "20100101"


def _key() -> Optional[str]:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    k = os.environ.get(KEY_ENV, "").strip()
    return k or None


def _num(v) -> Optional[float]:
    try:
        return float(str(v).replace(",", "")) if v not in (None, "", "-") else None
    except ValueError:
        return None


def _page(key: str, ticker: str, begin: str, end: str, page: int) -> dict:
    q = urllib.parse.urlencode({"serviceKey": key, "resultType": "json", "numOfRows": 1000, "pageNo": page,
                                "likeSrtnCd": ticker, "beginBasDt": begin, "endBasDt": end})
    try:
        with urllib.request.urlopen(f"{URL}?{q}", timeout=TIMEOUT) as res:      # noqa: S310 (정해진 주소만)
            body = res.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"공공데이터포털 응답 오류 (HTTP {e.code})") from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise RuntimeError(f"공공데이터포털에 연결하지 못했습니다 ({type(e).__name__})") from None
    if not body.lstrip().startswith("{"):                # 키 오류 등은 XML 안내로 온다
        msg = "SERVICE_KEY_IS_NOT_REGISTERED" if "SERVICE_KEY" in body else body[:80]
        raise RuntimeError(f"공공데이터포털 안내: {msg} (키 승인 직후면 1~2시간 뒤 다시)")
    return json.loads(body)


def fetch(ticker: str, begin: str, end: str, key: str) -> List[dict]:
    """[{'date','open','high','low','close','volume','shares'}] 오래된 날부터. 종목코드가 정확히 같은 줄만"""
    out, page = [], 1
    while True:
        body = _page(key, ticker, begin, end, page)
        res = (body.get("response") or {})
        head = res.get("header") or {}
        if str(head.get("resultCode", "00")) not in ("00", "0"):
            raise RuntimeError(f"공공데이터포털 오류: {head.get('resultMsg', '')[:60]}")
        items = ((res.get("body") or {}).get("items") or {}).get("item") or []
        if isinstance(items, dict):
            items = [items]
        for it in items:
            if str(it.get("srtnCd", "")).zfill(6) != ticker:
                continue
            c = _num(it.get("clpr"))
            if not c:
                continue
            out.append({"date": str(it.get("basDt")), "open": _num(it.get("mkp")) or c, "high": _num(it.get("hipr")) or c,
                        "low": _num(it.get("lopr")) or c, "close": c, "volume": _num(it.get("trqu")), "shares": _num(it.get("lstgStCnt"))})
        total = int((res.get("body") or {}).get("totalCount") or 0)
        if page * 1000 >= total or not items:
            break
        page += 1
    return sorted({r["date"]: r for r in out}.values(), key=lambda r: r["date"])


def history(ticker: str, until: str) -> Optional[List[dict]]:
    """until(그날 제외) 전까지의 과거 기록. 키가 없으면 None. 저장한 기록이 있으면 빠진 뒤쪽만 더 받는다"""
    key = _key()
    if not key:
        return None
    f = CACHE / f"{ticker}.json"
    saved = None
    try:
        saved = json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
    except (OSError, ValueError):
        saved = None
    rows = saved["rows"] if saved else []
    have_to = saved["to"] if saved else None
    end = (datetime.strptime(until, "%Y%m%d").date() - timedelta(days=1)).strftime("%Y%m%d")
    if not saved:
        rows = fetch(ticker, FIRST, end, key)
    elif have_to < end:
        nxt = (datetime.strptime(have_to, "%Y%m%d").date() + timedelta(days=1)).strftime("%Y%m%d")
        rows = sorted({r["date"]: r for r in rows + fetch(ticker, nxt, end, key)}.values(), key=lambda r: r["date"])
    if not saved or have_to < end:
        from .dart import save_atomic
        save_atomic(f, json.dumps({"to": end, "fetched_at": datetime.now().isoformat(timespec="seconds"), "rows": rows}, ensure_ascii=False))
    return [r for r in rows if r["date"] < until]


if __name__ == "__main__":
    import sys
    t = sys.argv[1] if len(sys.argv) > 1 else "005930"
    rows = history(t, date.today().strftime("%Y%m%d"))
    print("키 없음" if rows is None else f"{len(rows)}일, {rows[0]['date'] if rows else '-'} ~ {rows[-1]['date'] if rows else '-'}")
