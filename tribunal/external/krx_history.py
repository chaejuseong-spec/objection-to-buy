"""주가 긴 기록 (2026-11-23): KRX 무료 일별 자료(2010년 1월부터)를 한 번 받아 종목별로 작게 저장한다.

원본 하루치 파일(전 종목, 약 300KB)을 다 두면 2GB 가 넘어서, 날마다 종목별 [시가, 고가, 저가, 종가, 거래량, 상장주식수]만
연도 파일(.json.gz)에 모으고, 다 받으면 종목 파일(by_ticker/<코드>.json)로 다시 나눈다. 중간에 멈춰도 이어 받는다.
최근 1년은 원래 받아 두는 하루치 파일(.cache/krx)을 그대로 쓴다 (company.prices).

사용:
  python -m tribunal.external.krx_history run          # 받기 (이어 받기). 진행: .cache/krx_hist/progress.json
  python -m tribunal.external.krx_history split        # 종목 파일로 나누기 (run 끝에 자동)
"""
from __future__ import annotations

import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from .krx import BASE, MARKETS, TIMEOUT, CACHE as DAY_CACHE, KEY_ENV, KrxAuthError, num
from ..llm import ROOT, get_key

HIST = ROOT / ".cache" / "krx_hist"
START = date(2010, 1, 4)                      # KRX 무료 자료가 있는 첫 날 (2026-11-23 확인: 2009년 이전은 빈 응답)
ENDPOINTS = [m[0] for m in MARKETS[:2]]        # 코스피, 코스닥 (코넥스는 제외)


def _save(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    if path.suffix == ".gz":
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    else:
        tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _load(path: Path, default):
    try:
        if path.suffix == ".gz":
            with gzip.open(path, "rt", encoding="utf-8") as f:
                return json.load(f)
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def code_of(row: dict) -> str:
    c = str(row.get("ISU_CD", ""))
    return c[3:9] if len(c) == 12 else c


def compact(rows: List[dict]) -> Dict[str, list]:
    """하루치 전 종목 -> {종목코드: [시가, 고가, 저가, 종가, 거래량, 상장주식수]}"""
    out = {}
    for r in rows:
        c = num(r.get("TDD_CLSPRC"))
        if not c:
            continue
        out[code_of(r)] = [num(r.get("TDD_OPNPRC")) or c, num(r.get("TDD_HGPRC")) or c, num(r.get("TDD_LWPRC")) or c, c,
                           num(r.get("ACC_TRDVOL")), num(r.get("LIST_SHRS"))]
    return out


def fetch_day(endpoint: str, day: str, key: str) -> List[dict]:
    """하루치 (원본 파일로 저장하지 않는다). 휴장일이면 빈 목록"""
    req = urllib.request.Request(f"{BASE}sto/{endpoint}?basDd={day}", headers={"AUTH_KEY": key, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as res:      # noqa: S310 (정해진 주소만)
            body = json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise KrxAuthError("KRX 키 또는 이용 승인 문제") from None
        raise
    if "OutBlock_1" not in body:
        raise RuntimeError(str(body.get("respMsg") or body.get("message") or "KRX 안내 응답")[:80])
    return body.get("OutBlock_1") or []


def recent_start() -> date:
    """원래 받아 두는 하루치 파일 중 가장 오래된 날 (그 전부터 거꾸로 받으면 된다)"""
    days = sorted((datetime.strptime(p.stem.split("-")[-1], "%Y%m%d").date() for p in DAY_CACHE.glob("stk_bydd_trd-*.json")), reverse=True)
    if not days:
        return date.today()
    start = days[0]
    for d in days[1:]:                              # 최근부터 거꾸로, 열흘 넘게 비는 곳(따로 받아 둔 옛날 날짜)에서 멈춘다
        if (start - d).days > 10:
            break
        start = d
    return start


def run(limit: Optional[int] = None) -> dict:
    """2010-01-04 부터 원래 하루치 파일이 시작하는 날 전까지 받는다. 받은 날은 건너뛴다 (이어 받기).
    연속 실패 5번이면 멈춘다 (하루 호출 한도 등) - 다음에 다시 부르면 이어서"""
    key = get_key(KEY_ENV)
    end = recent_start()
    days = [START + timedelta(days=i) for i in range((end - START).days)]
    days = [d for d in days if d.weekday() < 5]
    years: Dict[tuple, dict] = {}
    done = calls = fails = 0
    prog = HIST / "progress.json"
    t0 = time.time()
    for d in days:
        for ep in ENDPOINTS:
            yk = (ep, d.year)
            if yk not in years:
                years[yk] = _load(HIST / f"{ep}-{d.year}.json.gz", {"days": {}})
            book = years[yk]["days"]
            ds = d.strftime("%Y%m%d")
            if ds in book:
                continue
            try:
                book[ds] = compact(fetch_day(ep, ds, key))
                calls += 1
                fails = 0
            except KrxAuthError:
                raise
            except Exception as e:                       # noqa: BLE001 (잠시 뒤 이어 받기)
                fails += 1
                if fails >= 5:
                    _flush(years)
                    _save(prog, {"state": "stopped", "reason": str(e)[:120], "day": ds, "calls": calls, "at": datetime.now().isoformat(timespec="seconds")})
                    return {"stopped": str(e)[:120], "day": ds, "calls": calls}
                time.sleep(2)
                continue
            if calls % 40 == 0:
                _flush(years)
                n_done = sum(len(v["days"]) for v in years.values())
                _save(prog, {"state": "running", "day": ds, "calls": calls, "elapsed_s": round(time.time() - t0),
                             "total_days": len(days) * len(ENDPOINTS), "seen_in_open_years": n_done,
                             "at": datetime.now().isoformat(timespec="seconds")})
            if limit and calls >= limit:
                _flush(years)
                return {"partial": calls}
        done += 1
    _flush(years)
    split()
    out = {"state": "done", "calls": calls, "days": len(days), "at": datetime.now().isoformat(timespec="seconds")}
    _save(prog, out)
    return out


def _flush(years: Dict[tuple, dict]) -> None:
    for (ep, y), obj in years.items():
        _save(HIST / f"{ep}-{y}.json.gz", obj)


def split() -> int:
    """연도 파일 -> 종목 파일 by_ticker/<코드>.json : {"rows": [[날짜, 시가, 고가, 저가, 종가, 거래량, 주식수], ...]} 오래된 날부터"""
    per: Dict[str, list] = {}
    for f in sorted(HIST.glob("*-*.json.gz")):
        days = _load(f, {"days": {}})["days"]
        for ds in sorted(days):
            for code, v in days[ds].items():
                per.setdefault(code, []).append([ds, *v])
    out = HIST / "by_ticker"
    for code, rows in per.items():
        rows.sort(key=lambda r: r[0])
        _save(out / f"{code}.json", {"rows": rows})
    return len(per)


def old_rows(ticker: str) -> List[dict]:
    """한 종목의 긴 기록 (받아 둔 것만). company.history 가 최근 1년과 이어 붙인다"""
    data = _load(HIST / "by_ticker" / f"{ticker}.json", {"rows": []})
    return [{"date": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4], "volume": r[5], "shares": r[6]} for r in data["rows"]]


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "split":
        print(split())
    else:
        print(json.dumps(run(int(sys.argv[2]) if len(sys.argv) > 2 else None), ensure_ascii=False))
