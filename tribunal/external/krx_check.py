"""리포트의 현재주가, 시가총액, 상장주식수를 KRX 시장 데이터와 대조한다. AI를 쓰지 않는다.

기준일
- 리포트에 가격 기준일이 적혀 있으면(작성일과 다른 날짜) 그날 하루와 대조한다.
- 없으면 작성일(포함)부터 거꾸로 6거래일과 대조한다. 리포트는 보통 전 거래일 종가를 쓴다.

판정
- 일치      : 그 기간 어느 날의 종가(시가총액·상장주식수)와 표시 자릿수 1단위 이내. 어느 날인지 적는다.
- 불일치    : 주가가 그 기간에 거래된 범위(최저가~최고가) 밖이다. 그런 가격은 나올 수 없었다.
- 검증 불가 : 범위 안이지만 종가와 다르다(장중 가격일 수 있다), 시가총액·주식수가 다르다(우선주 포함 등
              정의가 다를 수 있다), 자료가 없다. 짐작하지 않는다.
목표주가, 목표·적정 시가총액, 52주 최고·최저, 주가 배수(PER·PBR)는 대조하지 않는다.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import List, Optional

from ..number_check import MATCH, MISMATCH, parse_number
from ..schemas import DataRow, Report
from .common import UNVERIFIABLE, make_result
from .dart_check import _fmt, _tolerance, _won
from .krx import MARKETS, SITE, Krx, KrxError, api_url, num

PREFIX = "X"
KINDS = [  # (이름, 찾을 말, 빼는 말, KRX 칸)
    ("시가총액", ["시가총액", "시총"], r"목표|적정|SOTP|평가|합산|순자산|NAV", "MKTCAP"),
    ("상장주식수", ["상장주식수", "발행주식수", "보통주식수", "총주식수", "주식수"], r"유통|자기|자사주|우선|주당|희석", "LIST_SHRS"),
    ("현재주가", ["현재주가", "현재 주가", "현재가", "종가", "주가"], r"목표|적정|52주|최고|최저|평균|고가|저가|상승|하락|수익비율|PER|PBR|EPS|BPS|배수|누르기|부양|변동", "TDD_CLSPRC"),
]
SHARE_MULT = {"주": 1, "천주": 1e3, "만주": 1e4, "백만주": 1e6}
_DATE = re.compile(r"(20\d\d)[-./년\s]+(\d{1,2})[-./월\s]+(\d{1,2})")


def _iso(yyyymmdd: str) -> str:
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"


def classify(row: DataRow, target_name: str, report_date: str) -> dict:
    item = row.item or ""
    hit = next(((k, kw) for k in KINDS for kw in k[1] if kw in item), None)
    if not hit:
        return {"skip": "시장 데이터 항목이 아님"}
    kind, kw = hit
    if re.search(kind[2], item):
        return {"skip": "목표·적정·추정 같은 값이라 시세와 대조하지 않음"}
    exact = None
    for text in (item, row.as_of, row.basis):
        m = _DATE.search(text or "")
        if m:
            d = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
            if d != report_date:                      # 작성일이면 기준일을 따로 밝힌 것이 아니다
                exact = d
            break
    head = re.sub(r"보통주|우선주", " ", item.split(kw)[0]).strip()
    # '가상생명 기준주가 2026-11-10 종가' 처럼 회사 이름 뒤에 붙은 날짜·'기준주가' 같은 말은 회사 이름이 아니다
    head = re.sub(r"(기준|당일|작성일|최근)?\s*주가|기준|당일|작성일|최근", " ", _DATE.sub(" ", head)).strip()
    company = None if not head or head.replace(" ", "") == target_name.replace(" ", "") else head
    if "우선주" in item:          # KRX 는 우선주를 '삼성전자우'처럼 따로 올린다. 보통주 시세와 대조하면 틀렸다고 나온다(2026-11-17 실제 리포트 분석)
        company = (company or target_name).replace(" ", "") + "우"
    return {"kind": kind[0], "field": kind[3], "exact": exact, "company": company}


def _value(row: DataRow, kind: str):
    """리포트 값 -> (KRX 단위의 수, 허용 차이, 단위 배수). 읽지 못하면 None"""
    v = parse_number(row.value, row.unit)
    if v is None:
        return None
    if kind == "상장주식수":
        u = row.unit.replace(" ", "") or "주"
        mult = SHARE_MULT.get(u)
        if mult is None:
            return None
        return v * mult, mult, mult
    won, mult = _won(row)
    if won is None:
        return None
    return won, _tolerance(row, mult), mult


def _source(rows: List[dict], used: Optional[dict], kind: str, field: str, report_date: str, exact: Optional[str]) -> dict:
    market = rows[0]["_market"]
    first, last = _iso(rows[-1]["BAS_DD"]), _iso(rows[0]["BAS_DD"])
    span = f"{_iso(exact.replace('-', ''))} 하루" if exact else f"{first} ~ {last}, {len(rows)}거래일"
    base = _iso(used["BAS_DD"]) if used else ""
    label = {"TDD_CLSPRC": "종가", "MKTCAP": "시가총액", "LIST_SHRS": "상장주식수"}[field]
    return {
        "display": [["출처", f"KRX 정보데이터시스템 OPEN API, {market[1]}"],
                    ["종목", f"{rows[0].get('ISU_NM', '')} ({rows[0].get('ISU_CD', '')}, {market[2]})"],
                    ["비교 기간", span],
                    ["기준일", f"{base} {label}" if used else "맞는 날 없음"]],
        "short": f"KRX {market[2]}, {base + ' ' + label if used else span}",
        "provider": "KRX (한국거래소 정보데이터시스템)", "api": market[1],
        "isu_nm": rows[0].get("ISU_NM", ""), "isu_cd": rows[0].get("ISU_CD", ""), "market": market[2],
        "bas_dd": base, "field": field, "period": span,
        "url": SITE, "api_url": api_url(market[0], (used or rows[0])["BAS_DD"]),
        "fetched_at": (used or rows[0])["_fetched_at"], "report_date": report_date, "timing": "작성 당시 공개",
    }


def check_row(krx: Krx, row: DataRow, info: dict, ticker: str, target_name: str, report_date: str, cid: str) -> dict:
    kind, field = info["kind"], info["field"]
    who = info["company"] or target_name
    name = f"{who} {kind}, 시장 데이터 대조"

    def res(status, reason, **extra):
        return make_result("krx", cid, row, name, status, reason, **extra)

    val = _value(row, kind)
    if val is None:
        return res(UNVERIFIABLE, f"리포트 값 '{row.value}'(단위 '{row.unit}')를 숫자 하나로 읽지 못했습니다.")
    stated, tol, mult = val
    if info["exact"] and info["exact"] > report_date:
        return res(UNVERIFIABLE, f"기준일 {info['exact']}이 리포트 작성일 뒤라 대조하지 않았습니다.")
    rows = krx.window(report_date, ticker="" if info["company"] else ticker, name=info["company"] or "",
                      exact=info["exact"].replace("-", "") if info["exact"] else None)
    if not rows:
        where = f"{info['exact']}" if info["exact"] else f"{report_date} 이전 거래일"
        return res(UNVERIFIABLE, f"{who}의 {where} 시세를 KRX에서 찾지 못했습니다.")

    def fmt(v):
        if kind == "상장주식수":
            return f"{v / mult:,.0f}{row.unit.strip() or '주'}"
        return _fmt(v, row, mult)

    for r in rows:
        official = num(r.get(field))
        if official is not None and abs(stated - official) <= tol:
            src = _source(rows, r, kind, field, report_date, info["exact"])
            when = "기준일" if info["exact"] else ("작성일 당일" if _iso(r["BAS_DD"]) == report_date else f"{_iso(r['BAS_DD'])}")
            return res(MATCH, f"{when} {src['display'][3][1]}와 같습니다(표시 자릿수 이내).",
                       computed=fmt(official), diff_pct=round(abs(stated - official) / official * 100, 3) if official else None,
                       source=src, timing=src["timing"])

    src = _source(rows, None, kind, field, report_date, info["exact"])
    closest = min((num(r.get(field)) for r in rows if num(r.get(field)) is not None), key=lambda v: abs(v - stated), default=None)
    extra = dict(source=src, timing=src["timing"],
                 computed=fmt(closest) if closest is not None else None,
                 diff_pct=round(abs(stated - closest) / closest * 100, 3) if closest else None)
    if kind == "현재주가":
        lo = min((num(r.get("TDD_LWPRC")) for r in rows if num(r.get("TDD_LWPRC"))), default=None)
        hi = max((num(r.get("TDD_HGPRC")) for r in rows if num(r.get("TDD_HGPRC"))), default=None)
        if lo is not None and hi is not None and not (lo - tol <= stated <= hi + tol):
            return res(MISMATCH, f"비교 기간({src['period']})에 거래된 가격 범위 {lo:,.0f}~{hi:,.0f}원 밖의 값입니다.", **extra)
        return res(UNVERIFIABLE, f"비교 기간의 종가와 같지 않지만 거래 범위 {lo:,.0f}~{hi:,.0f}원 안이라 장중 가격일 수 있어 판단하지 않았습니다. 가장 가까운 종가는 {extra['computed']}입니다.", **extra)
    why = "우선주 포함 여부나 기준일이 달라 생긴 차이일 수 있어" if kind == "시가총액" else "발행주식수와 상장주식수, 우선주 포함 여부가 다를 수 있어"
    return res(UNVERIFIABLE, f"비교 기간의 {kind}와 같지 않습니다. {why} 판단하지 않았습니다. 가장 가까운 값은 {extra['computed']}({extra['diff_pct']}% 차이)입니다.", **extra)


def run(report: Report, krx: Optional[Krx] = None) -> dict:
    krx = krx or Krx()
    out = {"enabled": True, "provider": "KRX", "results": [], "skipped": [], "error": None}
    report_date = report.meta.report_date or report.meta.snapshot_date
    try:
        datetime.strptime(report_date, "%Y-%m-%d")
    except ValueError:
        out["error"] = f"리포트 작성일({report_date})을 읽지 못해 대조하지 않았습니다."
        return out
    try:
        n = 0
        for row in report.data_rows:
            info = classify(row, report.meta.name, report_date)
            if "skip" in info:
                if info["skip"] != "시장 데이터 항목이 아님":
                    out["skipped"].append({"rid": row.rid, "item": row.item, "why": info["skip"]})
                continue
            n += 1
            out["results"].append(check_row(krx, row, info, report.meta.ticker.strip().zfill(6), report.meta.name,
                                            report_date, f"{PREFIX}-{n}"))
    except KrxError as e:
        out["error"] = str(e)
    except RuntimeError as e:          # 키 없음
        out.update(enabled=False, error=str(e))
    out["calls"] = krx.calls
    denied = getattr(krx, "denied", None) or {}
    if denied and not out["error"]:
        names = ", ".join(m[1] for m in MARKETS if m[0] in denied)
        out["note"] = f"이용 승인이 없는 KRX API({names})는 건너뛰었습니다. 그 시장의 종목은 대조하지 못했습니다."
    return out


def maybe_run(report: Report, cfg: dict) -> Optional[dict]:
    if not (cfg.get("external") or {}).get("krx", False):
        return None
    return run(report)
