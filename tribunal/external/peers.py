"""상장 경쟁사 비교 (2026-11-22). AI 를 쓰지 않는다.

① 업종 목록: 상장사 전체의 DART 기업개황(업종 코드)을 한 번 훑어 .cache/dart/industry.json 에 둔다 (처음 수십 분, 이후 30일마다).
② 같은 업종 코드(표준산업분류 5자리, 모자라면 앞 3자리)의 상장사를 KRX 시가총액 순으로 고른다.
③ 회사마다 최근 공시 재무(최근 4분기 순이익, 최근 분기 자본·부채)와 KRX 종가로 지표를 계산한다. 보험사는 CSM·K-ICS 도.
실행: python -m tribunal.external.peers --index        업종 목록만 만들기
"""
from __future__ import annotations

import json
import re
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


def row(dart: Dart, ticker: Optional[str], info: dict, mk: dict, insurer: bool) -> dict:
    from . import company as cp
    st = cp.statements(dart, info["corp_code"], years=2)
    q = st.get("quarter", [])
    last = q[-1] if q else {}
    price = {"close": num(mk.get("TDD_CLSPRC")), "shares": num(mk.get("LIST_SHRS"))}
    try:                                                   # 회사 화면과 같은 기준: 유통주식수 (2026-11-24, 사용자)
        sf = cp.float_shares(dart, info["corp_code"])
    except Exception:                                      # noqa: BLE001
        sf = None
    try:                                                   # EPS 분할 보정: 기업개요와 같은 함수·같은 자료 (최근 5개 분기 유통주식수, 2026-11-26 사용자 A)
        per5 = [p["period"] for p in q[-5:]]
        fac = cp.split_factors(per5, {k: v for k, v in cp.float_history(dart, info["corp_code"], per5).items() if v})
    except Exception:                                      # noqa: BLE001
        fac = None
    m = cp.metrics(st, price, sf, fac=fac)
    out = {
        "ticker": ticker, "corp_code": info["corp_code"], "listed": bool(ticker), "name": info["name"], "induty": info.get("induty"), "close": price["close"],
        "mktcap": num(mk.get("MKTCAP")), "rate": num(mk.get("FLUC_RT")), "day": mk.get("_day"),
        "fs": st.get("fs"), "period": last.get("period"),
        "per": m["per"], "pbr": m["pbr"], "roe": m["roe"], "eps": m["eps"], "bps": m["bps"],
        "debt": (last["liab"] / last["equity"] * 100) if last.get("liab") is not None and last.get("equity") else None,
        "op": last.get("op"), "ni4": (sum(cp.owner_vals(p)[0] for p in q[-4:]) if len(q) >= 4 and all(cp.owner_vals(p)[0] is not None for p in q[-4:]) else None),   # 지배주주 순이익 (PER 과 같은 기준, 2026-11-26)
        "equity": last.get("equity"),
    }
    if insurer and q:
        ins = cp.insurance(dart, info["corp_code"], q[-2:])            # 최근 두 분기만 (원본 파일을 많이 받지 않게)
        latest = next((r for r in reversed(ins) if r.get("csm") is not None), {})
        out.update(csm=latest.get("csm"), csm_new=next((r.get("csm_new") for r in reversed(ins) if r.get("csm_new") is not None), None),
                   kics=next((r.get("kics") for r in reversed(ins) if r.get("kics") is not None), None))
    return out


def _assets(dart: Dart, corp_code: str, today: Optional[date] = None) -> Optional[float]:
    """가장 최근 사업보고서의 자산총계 (금융회사 경쟁사를 규모 순으로 줄 세우기용). 연결이 없으면 별도"""
    from . import company as cp
    y = (today or date.today()).year
    for yy in (y - 1, y - 2):
        rep = cp._report(dart, corp_code, yy, 4)
        if rep:
            r = cp._find(rep["rows"], "BS", ["ifrs-full_Assets"], ["자산총계"])
            v = cp._num(r.get("thstrm_amount")) if r else None
            if v:
                return v
    return None


def fisis_candidates(dart: Dart, part: str, index: Dict[str, dict], market: Optional[Dict[str, dict]] = None) -> List[dict]:
    """금융회사 경쟁사 후보 (전면 개편 1차, 2026-11-25 사용자): 금감원 FISIS 같은 업권 회사 목록 (이름만 쓴다, 숫자는 DART).
    상장사는 DART 상장사 목록, 비상장사는 DART 회사 이름으로 찾는다. DART 에 없는 회사(외국 지점 등)는 뺀다.
    [{ticker 또는 None, corp_code, name}]"""
    from . import fisis
    by: Dict[str, tuple] = {}
    for t, v in index.items():
        if market and t not in market:                # 상장폐지된 옛 회사(이름이 같은 옛 미래에셋증권 등)로 잇지 않는다
            continue
        for n in (v.get("name") or "", fisis.ALIAS.get(v.get("name") or "", "")):
            if n:
                by.setdefault(fisis.norm(n), (t, v))
    out, seen = [], set()
    allc: Optional[Dict[str, List[dict]]] = None
    for c in fisis.Fisis().companies(part):
        full = (c.get("finance_nm") or "").strip()
        fn = fisis.norm(full)
        hit = by.get(fn) or next((v for k, v in by.items()               # 'KB금융' = '케이비금융지주' (앞부분이 같은 금융 업종 상장사)
                                  if len(k) >= 4 and fn.startswith(k) and (v[1].get("induty") or "")[:2] in ("64", "65", "66")), None)
        if hit:
            item = {"ticker": hit[0], "corp_code": hit[1]["corp_code"], "name": hit[1]["name"]}
        else:
            names = [full, re.sub(r"\s*(주식회사|\(주\)|㈜)\s*", "", full).strip()]
            if allc is None:                          # DART 전체 회사를 금감원과 같은 방식으로 이름 맞춤 ('KB증권' = '케이비증권')
                allc = {}
                for h in dart.all_corps():
                    allc.setdefault(fisis.norm(h["corp_name"]), []).append(h)
            same = [h for n in names for h in dart.unlisted_by_name(n)] or allc.get(fn, [])
            # 같은 이름이 여럿이면 지금 상장·비상장 회사 먼저. 상장폐지 코드가 남은 회사(메리츠증권·KB증권처럼 합병 뒤 비상장 자회사)도 받는다
            same.sort(key=lambda h: 0 if not h.get("stock_code") or (market and h["stock_code"] in market) else 1)
            # 이름이 같은 DART 회사가 여럿(국민은행 3곳)이면 최근 사업보고서가 있는 회사
            d, assets = None, None
            for h in same[:4]:
                assets = _assets(dart, h["corp_code"])
                if assets:
                    d = h
                    break
            if not d:
                continue
            item = {"ticker": d.get("stock_code") or None, "corp_code": d["corp_code"], "name": d["corp_name"], "assets": assets}
        if item["corp_code"] not in seen:
            seen.add(item["corp_code"])
            out.append(item)
    return out


def main_sector(view: Optional[dict]) -> Optional[dict]:
    """처음 보여 줄 토스 섹터 (전면 개편 1차, 2026-11-25): 구성 종목 3곳 이상인 섹터 중 가장 작은(가장 세분된) 섹터.
    '가장 많은 섹터'는 넓은 분류가 잡혀(삼성생명 → 증권 27곳, 삼성전자 → 컴퓨터와 주변기기 28곳) 맞지 않았다.
    3곳 이상이 없으면 가장 많은 섹터. 크기가 같으면 이 회사 비중이 작은 섹터(여러 회사가 겨루는 섹터)"""
    secs = (view or {}).get("sectors") or []
    if not secs:
        return None
    me = (view or {}).get("ticker")

    def share(s):
        tot = sum(m.get("mktcap") or 0 for m in s.get("members") or [])
        mine = next((m.get("mktcap") or 0 for m in s.get("members") or [] if m.get("ticker") == me), 0)
        return mine / tot if tot else 1
    big = [s for s in secs if (s.get("count") or 0) >= 3]
    return min(big, key=lambda s: (s.get("count") or 0, share(s))) if big else max(secs, key=lambda s: s.get("count") or 0)


def choose_toss(sector: dict, ticker: str, market: Dict[str, dict], index: Dict[str, dict], limit: int = 8) -> List[str]:
    """토스 섹터 구성 종목(자신 포함) 중 KRX 시세·DART 회사가 있는 곳을 시가총액 큰 순으로"""
    mem = [m["ticker"] for m in sorted(sector.get("members") or [], key=lambda m: -(m.get("mktcap") or 0))
           if m.get("ticker") in market and m.get("ticker") in index]
    picked = mem[:limit]
    if ticker not in picked:
        picked = [ticker] + picked[:limit - 1]
    return picked


def build(ticker: str, dart: Optional[Dart] = None, krx: Optional[Krx] = None, limit: int = 8, today: Optional[date] = None,
          sector_view=None, fin_part: Optional[str] = None, sector_id: Optional[str] = None) -> dict:
    """경쟁사 표. 전면 개편 1차 (2026-11-25 사용자): 비금융 = 토스증권 섹터 구성 종목, 금융(fin_part = 금감원 업권) =
    금감원 같은 업권 회사(비상장 포함, 숫자는 DART, 자산 순). 토스 섹터에 없는 비금융만 예전처럼 같은 표준산업분류 코드"""
    from .company import INSURANCE
    dart, krx = dart or Dart(), krx or Krx()
    index = industry_index(dart)
    market = latest_market(krx, today)
    if not market:
        return {"error": "KRX 시세를 받지 못했습니다."}
    if fin_part:
        try:
            return _build_fin(ticker, dart, market, index, fin_part, limit, today)
        except Exception:                              # noqa: BLE001 (금감원 목록을 못 받으면 토스·업종 코드로)
            pass
    sec = None
    try:
        if sector_view is None:
            from . import toss
            sector_view = toss.sector_view(ticker)
        sec = (next((s for s in (sector_view or {}).get("sectors") or [] if str(s.get("id")) == str(sector_id)), None)
               if sector_id else main_sector(sector_view))
    except Exception:                                  # noqa: BLE001 (토스를 못 부르면 업종 코드로)
        sec = None
    picked = choose_toss(sec, ticker, market, index, limit) if sec else choose(index, ticker, market, limit)
    me = index.get(ticker, {})
    insurer = me.get("induty", "").startswith(INSURANCE)
    rows = []
    for t in picked:
        try:
            rows.append(row(dart, t, index[t], market[t], insurer))
        except (DartError, KeyError) as e:
            rows.append({"ticker": t, "name": index.get(t, {}).get("name", t), "error": type(e).__name__})
    return {"made_at": datetime.now().isoformat(timespec="seconds"), "induty": me.get("induty"), "insurer": insurer,
            "sector": {"id": sec["id"], "name": sec["name"], "path": sec.get("path")} if sec else None,
            "basis": (f"토스증권 '{sec['name']}' 섹터 종목, 시가총액 순" if sec else "토스 섹터에 없어 같은 표준산업분류 코드의 상장사, KRX 시가총액 순"),
            "rows": rows}


def _build_fin(ticker: str, dart: Dart, market: Dict[str, dict], index: Dict[str, dict], part: str, limit: int,
               today: Optional[date]) -> dict:
    from .company import INSURANCE
    from . import fisis
    cands = fisis_candidates(dart, part, index, market)
    if not any(c["ticker"] == ticker for c in cands):
        raise ValueError("이 회사가 금감원 업권 목록에 없음")
    for c in cands:
        if "assets" not in c:
            c["assets"] = _assets(dart, c["corp_code"], today)
    cands.sort(key=lambda c: -(c["assets"] or 0))
    picked = cands[:limit]
    if not any(c["ticker"] == ticker for c in picked):
        picked = [next(c for c in cands if c["ticker"] == ticker)] + picked[:limit - 1]
    insurer = (index.get(ticker, {}).get("induty") or "").startswith(INSURANCE)
    rows = []
    for c in picked:
        info = index.get(c["ticker"]) if c["ticker"] in index else {"corp_code": c["corp_code"], "name": c["name"], "induty": ""}
        mk = market.get(c["ticker"], {}) if c["ticker"] else {}
        try:
            rows.append({**row(dart, c["ticker"] if c["ticker"] in market else None, info, mk, insurer), "assets": c["assets"]})
        except (DartError, KeyError) as e:
            rows.append({"ticker": c["ticker"], "corp_code": c["corp_code"], "name": c["name"], "error": type(e).__name__})
    name = fisis.SECTORS.get(part, {}).get("name", part)
    return {"made_at": datetime.now().isoformat(timespec="seconds"), "induty": index.get(ticker, {}).get("induty"), "insurer": insurer,
            "sector": None, "fin": {"part": part, "name": name, "count": len(cands)},
            "basis": f"금감원 '{name}' 업권 회사 (비상장 포함, 재무는 DART), 자산총계 순", "rows": rows}


SECTOR_CACHE = CACHE.parent / "peers" / "sector"


def for_sector(ticker: str, sector_id: str, dart: Optional[Dart] = None, krx: Optional[Krx] = None) -> dict:
    """화면에서 고른 토스 섹터의 경쟁사 표 (사업이 여럿인 회사, 2026-11-25 사용자 'A'). 섹터마다 7일 저장"""
    from .dart import load_json, save_atomic
    if not re.fullmatch(r"[0-9A-Z]{6}", ticker or "") or not re.fullmatch(r"[0-9A-Za-z_-]{1,40}", sector_id or ""):
        return {"error": "종목코드나 섹터가 올바르지 않습니다."}
    f = SECTOR_CACHE / f"{ticker}-{sector_id}.json"
    saved = load_json(f) if f.exists() else None
    from . import company as cp
    if (saved and saved.get("v") == cp.CO_V          # 계산 기준이 바뀌면(EPS 공시값 등, 2026-11-26) 다시 만든다
            and datetime.now() - datetime.fromisoformat(saved.get("made_at", "2000-01-01T00:00:00")) < timedelta(days=7)):
        return saved
    out = build(ticker, dart, krx, sector_id=sector_id)
    out["v"] = cp.CO_V
    if out.get("sector") and str(out["sector"]["id"]) == str(sector_id):
        SECTOR_CACHE.mkdir(parents=True, exist_ok=True)
        save_atomic(f, json.dumps(out, ensure_ascii=False))
        return out
    return {"error": "그 섹터 자료를 받지 못했습니다."}


if __name__ == "__main__":
    if "--unlisted" in sys.argv:
        print(len(unlisted_index(Dart(), refresh=True, progress=True)), "곳")
        sys.exit(0)
    if "--index" in sys.argv:
        idx = industry_index(Dart(), refresh="--refresh" in sys.argv, progress=True)
        print(f"업종 목록 {len(idx)}개 회사")
