"""상장 경쟁사 비교 (2026-11-22). AI 를 쓰지 않는다.

① 업종 목록: 상장사 전체의 DART 기업개황(업종 코드)을 한 번 훑어 .cache/dart/industry.json 에 둔다 (처음 수십 분, 이후 30일마다).
② 같은 업종 코드(표준산업분류 5자리, 모자라면 앞 3자리)의 상장사를 KRX 시가총액 순으로 고른다.
③ 회사마다 최근 공시 재무(최근 4분기 순이익, 최근 분기 자본·부채)와 KRX 종가로 지표를 계산한다. 보험사는 CSM·K-ICS 도.
실행: python -m tribunal.external.peers --index        업종 목록만 만들기
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from .dart import CACHE, Dart, DartError
from .krx import Krx, KrxError, num

INDEX = CACHE / "industry.json"
INDEX_TTL = timedelta(days=30)


def industry_index(dart: Dart, refresh: bool = False, progress: bool = False) -> Dict[str, dict]:
    """{종목코드: {corp_code, name, induty}}. 받아 둔 것이 30일 안이면 그대로"""
    if INDEX.exists() and not refresh:
        saved = json.loads(INDEX.read_text(encoding="utf-8"))
        if datetime.now() - datetime.fromisoformat(saved["made_at"]) < INDEX_TTL and saved.get("complete"):
            return saved["list"]
    out: Dict[str, dict] = {}
    corps = dart.corps()
    for i, c in enumerate(corps, 1):
        try:
            r = dart.get("company.json", {"corp_code": c["corp_code"]})
        except DartError:
            continue
        out[c["stock_code"]] = {"corp_code": c["corp_code"], "name": c["corp_name"], "induty": str(r.get("induty_code") or "")}
        if progress and i % 200 == 0:
            print(f"  업종 목록 {i}/{len(corps)}", flush=True)
            INDEX.write_text(json.dumps({"made_at": datetime.now().isoformat(timespec="seconds"), "complete": False, "list": out},
                                        ensure_ascii=False), encoding="utf-8")
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    INDEX.write_text(json.dumps({"made_at": datetime.now().isoformat(timespec="seconds"), "complete": True, "list": out},
                                ensure_ascii=False), encoding="utf-8")
    return out


UNLISTED = CACHE / "industry_unlisted.json"


def unlisted_index(dart: Dart, refresh: bool = False, today: Optional[date] = None, progress: bool = False) -> Dict[str, dict]:
    """비상장 정기보고서 제출사 업종 목록 (2026-11-23): {corp_code: {name, induty}}. 30일마다 다시.
    최근 사업보고서 철(1~6월)에 사업보고서를 낸 '기타법인'(비상장)만. DART 목록은 기간 3개월씩 나눠 묻는다"""
    if UNLISTED.exists() and not refresh:
        saved = json.loads(UNLISTED.read_text(encoding="utf-8"))
        if datetime.now() - datetime.fromisoformat(saved["made_at"]) < INDEX_TTL and saved.get("complete"):
            return saved["list"]
    y = (today or date.today()).year
    if (today or date.today()).month < 4:
        y -= 1
    corps: Dict[str, str] = {}
    for b, e in ((f"{y}0101", f"{y}0331"), (f"{y}0401", f"{y}0630")):
        page = 1
        while True:
            r = dart.get("list.json", {"bgn_de": b, "end_de": e, "pblntf_detail_ty": "A001", "corp_cls": "E",
                                       "page_count": "100", "page_no": str(page)})
            for x in r.get("list", []):
                corps.setdefault(x["corp_code"], x["corp_name"])
            if page >= int(r.get("total_page") or 1):
                break
            page += 1
    out: Dict[str, dict] = {}
    for i, (cc, name) in enumerate(corps.items(), 1):
        try:
            info = dart.get("company.json", {"corp_code": cc})
        except DartError:
            continue
        out[cc] = {"name": name, "induty": str(info.get("induty_code") or "")}
        if progress and i % 100 == 0:
            print(f"  비상장 업종 목록 {i}/{len(corps)}", flush=True)
    UNLISTED.parent.mkdir(parents=True, exist_ok=True)
    UNLISTED.write_text(json.dumps({"made_at": datetime.now().isoformat(timespec="seconds"), "complete": True, "year": y, "list": out},
                                   ensure_ascii=False), encoding="utf-8")
    return out


def latest_market(krx: Krx, today: Optional[date] = None) -> Dict[str, dict]:
    """가장 최근 거래일 전 종목 시세 {종목코드: 줄} (유가증권 + 코스닥)"""
    d = today or date.today()
    for _ in range(10):
        if d.weekday() < 5:
            day = d.strftime("%Y%m%d")
            rows = {}
            for ep in ("stk_bydd_trd", "ksq_bydd_trd"):
                try:
                    res = krx.day(ep, day)
                except KrxError:
                    continue
                for r in res.get("rows", []):
                    code = str(r.get("ISU_CD", ""))
                    rows[code[3:9] if len(code) > 6 else code] = {**r, "_day": day}
            if rows:
                return rows
        d -= timedelta(days=1)
    return {}


def choose(index: Dict[str, dict], ticker: str, market: Dict[str, dict], limit: int = 8) -> List[str]:
    """같은 업종 상장사(자신 포함)를 시가총액 큰 순으로. 5자리 코드가 같은 회사가 3곳 미만이면 앞 3자리로 넓힌다"""
    me = index.get(ticker)
    if not me or not me["induty"]:
        return [ticker]
    for n in (5, 4, 3):
        code = me["induty"][:n]
        same = [t for t, v in index.items() if v["induty"].startswith(code) and t in market]
        if len(same) >= 3 or n == 3:                   # 자기 포함 3곳이면 그 업종 안에서 (생명보험이 손해보험까지 넓어지지 않게)
            break
    same.sort(key=lambda t: -(num(market[t].get("MKTCAP")) or 0))
    picked = same[:limit]
    if ticker not in picked:
        picked = [ticker] + picked[:limit - 1]
    return picked


def row(dart: Dart, ticker: str, info: dict, mk: dict, insurer: bool) -> dict:
    from . import company as cp
    st = cp.statements(dart, info["corp_code"], years=2)
    q = st.get("quarter", [])
    last = q[-1] if q else {}
    price = {"close": num(mk.get("TDD_CLSPRC")), "shares": num(mk.get("LIST_SHRS"))}
    m = cp.metrics(st, price)
    out = {
        "ticker": ticker, "name": info["name"], "induty": info["induty"], "close": price["close"],
        "mktcap": num(mk.get("MKTCAP")), "rate": num(mk.get("FLUC_RT")), "day": mk.get("_day"),
        "fs": st.get("fs"), "period": last.get("period"),
        "per": m["per"], "pbr": m["pbr"], "roe": m["roe"], "eps": m["eps"], "bps": m["bps"],
        "debt": (last["liab"] / last["equity"] * 100) if last.get("liab") is not None and last.get("equity") else None,
        "op": last.get("op"), "ni4": sum(p["ni"] for p in q[-4:]) if len(q) >= 4 and all(p.get("ni") is not None for p in q[-4:]) else None,
        "equity": last.get("equity"),
    }
    if insurer and q:
        ins = cp.insurance(dart, info["corp_code"], q[-2:])            # 최근 두 분기만 (원본 파일을 많이 받지 않게)
        latest = next((r for r in reversed(ins) if r.get("csm") is not None), {})
        out.update(csm=latest.get("csm"), csm_new=next((r.get("csm_new") for r in reversed(ins) if r.get("csm_new") is not None), None),
                   kics=next((r.get("kics") for r in reversed(ins) if r.get("kics") is not None), None))
    return out


def build(ticker: str, dart: Optional[Dart] = None, krx: Optional[Krx] = None, limit: int = 8, today: Optional[date] = None) -> dict:
    from .company import INSURANCE
    dart, krx = dart or Dart(), krx or Krx()
    index = industry_index(dart)
    market = latest_market(krx, today)
    if not market:
        return {"error": "KRX 시세를 받지 못했습니다."}
    picked = choose(index, ticker, market, limit)
    me = index.get(ticker, {})
    insurer = me.get("induty", "").startswith(INSURANCE)
    rows = []
    for t in picked:
        try:
            rows.append(row(dart, t, index[t], market[t], insurer))
        except (DartError, KeyError) as e:
            rows.append({"ticker": t, "name": index.get(t, {}).get("name", t), "error": type(e).__name__})
    return {"made_at": datetime.now().isoformat(timespec="seconds"), "induty": me.get("induty"), "insurer": insurer,
            "basis": "같은 표준산업분류 코드의 상장사, KRX 시가총액 순", "rows": rows}


if __name__ == "__main__":
    if "--unlisted" in sys.argv:
        print(len(unlisted_index(Dart(), refresh=True, progress=True)), "곳")
        sys.exit(0)
    if "--index" in sys.argv:
        idx = industry_index(Dart(), refresh="--refresh" in sys.argv, progress=True)
        print(f"업종 목록 {len(idx)}개 회사")
