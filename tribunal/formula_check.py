"""고정 산식 검산. AI가 식을 만들어 주지 않아도, 필요한 숫자가 뽑혀 있으면 코드가 정해진 산식을 항상 검산한다.
같은 입력이면 언제나 같은 결과가 나온다.

하는 일
1. 뽑힌 숫자마다 이름표를 붙인다: 지표(매출액, PER …), 회사, 기간(2025, 2026E, 없음), 연결/별도
2. 같은 회사·같은 기간·같은 연결/별도끼리 묶어 아래 산식을 계산하고, 리포트에 적힌 값과 비교한다
3. 같은 이름표의 숫자가 쪽마다 다르게 적혀 있으면 알린다

산식
  상승여력 = 목표주가 ÷ 현재주가 − 1          자산총계 = 부채총계 + 자본총계
  시가총액 = 현재주가 × 주식수                 PER = 현재주가 ÷ EPS,  PBR = 현재주가 ÷ BPS
  영업이익률 = 영업이익 ÷ 매출액               순이익률 = 순이익 ÷ 매출액
  BPS = 자본총계 ÷ 주식수
  ROE = 순이익 ÷ 자본총계, ROA = 순이익 ÷ 자산총계, 배당성향 = DPS × 주식수 ÷ 순이익, EPS = 순이익 ÷ 주식수
    (아래 넷은 평균/기말 자본, 지배/전체 순이익, 가중평균 주식수처럼 기준이 여럿이라, 차이가 작으면 '확인 불가'로 둔다)

판정: 차이 0.5% 이내이거나 표시 자릿수 1단위 이내면 일치, 그 밖은 불일치.
      기준이 여럿인 산식은 25% 안의 차이를 확인 불가로, 그보다 크면 불일치로 본다.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from .external.dart_check import _fmt, _tolerance, _won
from .number_check import MATCH, MISMATCH, UNKNOWN, _show, parse_number
from .schemas import DataRow, Report

TOL_PCT = 0.5
BAND_PCT = 25.0
SHARE_MULT = {"주": 1, "천주": 1e3, "만주": 1e4, "백만주": 1e6, "": 1}

# (지표, 찾는 말, 빼는 말, 값 종류). 위에서부터 먼저 맞는 것을 쓴다.
METRICS = [
    ("target", r"목표\s*주가|적정\s*주가|Target\s*Price", r"", "won"),
    ("upside", r"상승\s*여력|업사이드|[Uu]pside", r"", "pct"),
    ("mktcap", r"시가\s*총액|시총", r"목표|적정|SOTP|우선주|평가|합산", "won"),
    # 유통주식수(자기주식을 뺀 수)는 상장주식수와 다른 항목이라 따로 묶는다. 시가총액 산식에는 상장주식수만 쓴다.
    ("shares_float", r"유통\s*(가능\s*)?주식\s*수", r"자기|자사주|우선|희석|비율|비중", "shares"),
    ("shares", r"(상장|발행|보통|총)?\s*주식\s*수", r"자기|자사주|우선|희석|유통", "shares"),
    ("eps", r"\bEPS\b|주당\s*순이익", r"성장|증가|YoY", "won"),
    ("bps", r"\bBPS\b|주당\s*순자산(?!가치)", r"성장|증가|YoY", "won"),
    ("dps", r"\bDPS\b|주당\s*배당금", r"성장|증가|YoY", "won"),
    ("per", r"\bPER\b|P/E", r"Target|타깃|목표|평균|업종|Multiple|Implied|밴드|상단|하단", "x"),
    ("pbr", r"\bPBR\b|P/B", r"Target|타깃|목표|평균|업종|Multiple|Implied|회귀|밴드|상단|하단", "x"),
    ("roe", r"\bROE\b", r"회귀|평균|목표", "pct"),
    ("roa", r"\bROA\b", r"평균|목표", "pct"),
    ("opm", r"영업\s*이익률|\bOPM\b", r"더해지는|개선|상승폭|평균|목표", "pct"),
    ("npm", r"순이익률|\bNPM\b", r"개선|상승폭|평균|목표", "pct"),
    ("payout", r"배당\s*성향", r"목표|평균", "pct"),
    ("price", r"현재\s*주가|현재가|종가", r"목표|적정", "won"),
    ("ni_owner", r"지배\s*(주주|지분|기업)?\s*(귀속\s*)?(당기)?\s*순이익", r"률|성장|증가|YoY", "won"),
    ("ni", r"당기\s*순이익|순이익", r"률|성장|증가|YoY|주당", "won"),
    ("op", r"영업\s*이익", r"률|마진|성장|증가|YoY|발생할", "won"),
    ("revenue", r"매출액|매출(?=\s|$)", r"비중|대비|성장|증가|YoY|원가|단위당|총이익", "won"),
    ("assets", r"자산\s*총계|총\s*자산", r"회전|이익률", "won"),
    ("liab", r"부채\s*총계|총\s*부채", r"비율", "won"),
    ("equity", r"자본\s*총계|총\s*자본|기말\s*자본", r"이익률|비율", "won"),
]
POINT = {"target", "price", "upside", "mktcap", "shares", "shares_float"}          # 기간이 아니라 지금 시점의 값
LABEL = {"target": "목표주가", "price": "현재주가", "upside": "상승여력", "mktcap": "시가총액", "shares": "주식수",
         "shares_float": "유통주식수", "eps": "EPS",
         "bps": "BPS", "dps": "DPS", "per": "PER", "pbr": "PBR", "roe": "ROE", "roa": "ROA", "opm": "영업이익률", "npm": "순이익률",
         "payout": "배당성향", "ni_owner": "지배주주순이익", "ni": "순이익", "op": "영업이익", "revenue": "매출액",
         "assets": "자산총계", "liab": "부채총계", "equity": "자본총계"}
_PERIOD = re.compile(r"(?<![\d.])([1-4]Q\s*'?\d\d|(?:20)?\d\d\s*(?:년\s*)?[1-4]분기)|(?<![\d.,])'?(20\d\d|\d\d)\s*(년)?\s*([EeFfPp])?(?![\d.,%Q~\-–]|\s*[~\-–]\s*\d|\s*개|\s*[월일주])")
_QUAL = re.compile(r"연결|별도|지배\s*(주주|지분|기업)?|보통주|동사|당사|전사|기준|결산|예상|추정|\(|\)|,")


def period_of(text: str) -> Optional[str]:
    """'가상식품 연결 매출액 2026E' -> '2026E', '27년 …' -> '2027', '1Q26' -> '1Q26'. 날짜(2026.06.04)는 기간이 아니다."""
    t = re.sub(r"20\d\d[-./]\d{1,2}[-./]\d{1,2}", " ", text or "")
    m = _PERIOD.search(t)
    if not m:
        return None
    if m.group(1):
        return re.sub(r"\s|'", "", m.group(1))
    y = int(m.group(2))
    y = y if y > 1000 else 2000 + y
    if not (2000 <= y <= 2100) or (len(m.group(2)) == 2 and not (m.group(3) or m.group(4))):
        return None                                        # '25' 처럼 연도인지 알 수 없는 두 자리 수는 쓰지 않는다
    return f"{y}{'E' if m.group(4) else ''}"


def tag(row: DataRow, target_name: str) -> Optional[dict]:
    """숫자 하나의 이름표. 산식에 쓸 수 없는 숫자면 None"""
    item = row.item or ""
    for key, inc, exc, kind in METRICS:
        m = re.search(inc, item)
        if not m or (exc and re.search(exc, item)):
            continue
        head = _QUAL.sub(" ", _PERIOD.sub(" ", item[: m.start()]))
        head = re.sub(r"(^|\s)(현|현재|당기|올해|최근)(?=\s|$)", " ", head).strip()
        tail = _QUAL.sub(" ", _PERIOD.sub(" ", item[m.end():])).strip()
        if tail and not re.fullmatch(r"[\s\-·:]*(\(?(원|배|x|%|주|백만\s*원|십억\s*원|억\s*원)\)?)?[\s\-·:]*", tail):
            return None                                    # '매출액 대비 …', 'PBR 밴드 상단' 처럼 뒤에 다른 말이 붙으면 다른 지표다
        company = "" if not head or head.replace(" ", "") == target_name.replace(" ", "") else re.sub(r"\s+", " ", head)
        per = None if key in POINT else (period_of(item) or period_of(row.as_of))
        if key not in POINT and per is None:
            return None                                    # 실적·비율은 어느 기간인지 알아야 묶을 수 있다
        fs = "연결" if "연결" in item else "별도" if "별도" in item else ""
        val = _value(row, kind)
        if val is None:
            return None
        return {"metric": key, "company": company, "period": per or "", "fs": fs, "kind": kind, "value": val[0], "mult": val[1], "row": row}
    return None


def _value(row: DataRow, kind: str) -> Optional[Tuple[float, float]]:
    """(계산에 쓸 값, 단위 배수). 금액과 주가는 원, 주식수는 주, 비율과 배수는 적힌 숫자 그대로"""
    if kind == "won":
        won, mult = _won(row)
        return (won, mult) if won is not None else None
    v = parse_number(row.value, row.unit)
    if v is None:
        return None
    if kind == "shares":
        mult = SHARE_MULT.get(re.sub(r"\s", "", row.unit))
        return (v * mult, mult) if mult else None
    return v, 1.0


# (이름, 적힌 값의 지표, 입력 지표들, 계산, 사람이 읽는 식, 기준이 여럿인가)
FORMULAS = [
    ("상승여력", "upside", ("target", "price"), lambda a: (a["target"] / a["price"] - 1) * 100, "목표주가 ÷ 현재주가 − 1", False),
    ("자산총계", "assets", ("liab", "equity"), lambda a: a["liab"] + a["equity"], "부채총계 + 자본총계", False),
    ("시가총액", "mktcap", ("price", "shares"), lambda a: a["price"] * a["shares"], "현재주가 × 주식수", False),
    ("PER", "per", ("price", "eps"), lambda a: a["price"] / a["eps"], "현재주가 ÷ EPS", False),
    ("PBR", "pbr", ("price", "bps"), lambda a: a["price"] / a["bps"], "현재주가 ÷ BPS", False),
    ("영업이익률", "opm", ("op", "revenue"), lambda a: a["op"] / a["revenue"] * 100, "영업이익 ÷ 매출액", False),
    ("순이익률", "npm", ("ni", "revenue"), lambda a: a["ni"] / a["revenue"] * 100, "순이익 ÷ 매출액", False),
    ("BPS", "bps", ("equity", "shares"), lambda a: a["equity"] / a["shares"], "자본총계 ÷ 주식수", False),
    ("ROE", "roe", ("ni", "equity"), lambda a: a["ni"] / a["equity"] * 100, "순이익 ÷ 자본총계", True),
    ("ROA", "roa", ("ni", "assets"), lambda a: a["ni"] / a["assets"] * 100, "순이익 ÷ 자산총계", True),
    ("배당성향", "payout", ("dps", "shares", "ni"), lambda a: a["dps"] * a["shares"] / a["ni"] * 100, "DPS × 주식수 ÷ 순이익", True),
    ("EPS", "eps", ("ni", "shares"), lambda a: a["ni"] / a["shares"], "순이익 ÷ 주식수", True),
]


def _table_of(t: dict) -> Optional[str]:
    """표의 칸이면 표 id ('T-1:2:3' -> 'T-1'), 본문 숫자면 None"""
    rid = t["row"].rid
    return rid.split(":")[0] if ":" in rid else None


def _pick(tags: List[dict], metric: str, company: str, period: str, fs: str, table: Optional[str] = None) -> Optional[dict]:
    """같은 회사, (시점 값이 아니면) 같은 기간, 연결/별도가 어긋나지 않는 숫자. 순이익은 지배주주 것을 먼저 쓴다.
    표의 칸을 검산할 때는 같은 표의 숫자를 먼저 쓴다 (표 안의 이익률은 그 표의 매출액으로 계산한 것이므로)."""
    names = ("ni_owner", "ni") if metric == "ni" else (metric,)
    for name in names:
        hits = []
        for t in tags:
            if t["metric"] != name or t["company"] != company:
                continue
            if name not in POINT and t["period"] != period:
                continue
            if name not in POINT and fs and t["fs"] and t["fs"] != fs:
                continue
            hits.append(t)
        if hits:
            return next((t for t in hits if table and _table_of(t) == table), hits[0])
    return None


def _num(v: float) -> str:
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def run(report: Report, existing: Optional[List[dict]] = None) -> List[dict]:
    """돌려주는 값: 숫자 검증 목록에 붙일 결과들 (check_id F-1 …)"""
    from .table_check import virtual_rows          # 확인된 표의 칸도 숫자로 넣는다 (표 안 이익률, 표·본문 사이 쪽마다 다름)
    tags = [t for t in (tag(r, report.meta.name) for r in list(report.data_rows) + virtual_rows(report)) if t]
    done = {c.get("stated_rid") for c in (existing or [])                                            # AI가 만든 식으로 이미 검산한 값
            if c.get("status") in (MATCH, MISMATCH) and c.get("kind") in (None, "recompute")}         # (공시·시세 대조는 다른 검증이라 겹쳐도 한다)
    out: List[dict] = []

    def add(name, stated_row, expr, note, status, computed, diff_pct, reason, extra_rids=()):
        out.append({"check_id": f"F-{len(out) + 1}", "kind": "formula", "name": name, "expr": expr, "stated_rid": stated_row.rid,
                    "rids": [stated_row.rid, *extra_rids], "note": note, "status": status, "computed": computed,
                    "stated": _show(stated_row.value, stated_row.unit), "diff_pct": diff_pct,
                    "pages": [stated_row.page] if stated_row.page else [], "reason": reason})

    for fname, stated_m, inputs, fn, text, loose in FORMULAS:
        for st in [t for t in tags if t["metric"] == stated_m]:
            if st["row"].rid in done:
                continue
            got = {m: _pick(tags, m, st["company"], st["period"], st["fs"], _table_of(st)) for m in inputs}
            if any(v is None for v in got.values()) or any(v["row"].rid == st["row"].rid for v in got.values()):
                continue
            try:
                calc = fn({m: v["value"] for m, v in got.items()})
            except ZeroDivisionError:
                continue
            stated = st["value"]
            diff = abs(calc - stated)
            diff_pct = round(diff / abs(stated) * 100, 3) if stated else None
            unit_tol = _tolerance(st["row"], st["mult"]) if st["kind"] == "won" else _display_unit(st["row"])
            who = " ".join(x for x in (st["company"] or report.meta.name, st["period"], st["fs"]) if x)
            shown = _fmt(calc, st["row"], st["mult"]) if st["kind"] == "won" else round(calc, 2)
            note = f"{text}: " + ", ".join(f"{LABEL[v['metric']]} {_show(v['row'].value, v['row'].unit)}" for v in got.values())
            expr = " , ".join(v["row"].rid for v in got.values())       # 식에 들어간 숫자 id (형광펜과 품질 평가가 쓴다)
            if diff <= unit_tol or (diff_pct is not None and diff_pct <= TOL_PCT):
                status, reason = MATCH, ""
            elif loose and diff_pct is not None and diff_pct <= BAND_PCT:
                status, reason = UNKNOWN, (f"{text}로 계산하면 {_num(calc) if st['kind'] != 'won' else shown}이라 {diff_pct}% 다릅니다. "
                                           "평균·기말, 지배주주·전체, 가중평균 주식수처럼 기준이 달라 생긴 차이일 수 있어 판단하지 않았습니다.")
            else:
                status, reason = MISMATCH, f"{text}로 다시 계산한 값과 {diff_pct}% 차이 (허용 {TOL_PCT}%)"
            add(f"{who} {fname} = {text}", st["row"], expr, note, status, shown, diff_pct, reason)

    # 같은 이름표의 숫자가 서로 다르게 적힌 경우
    groups: Dict[tuple, List[dict]] = {}
    for t in tags:
        groups.setdefault((t["metric"], t["company"], t["period"], t["fs"]), []).append(t)
    for (metric, company, period, fs), ts in groups.items():
        base = ts[0]
        for other in ts[1:]:
            unit_tol = max(_tolerance(x["row"], x["mult"]) if x["kind"] == "won" else _display_unit(x["row"]) for x in (base, other))
            if abs(base["value"] - other["value"]) <= unit_tol or _rounded_same(base["value"], other["value"]):
                continue
            who = " ".join(x for x in (company or report.meta.name, period, fs) if x)
            add(f"{who} {LABEL[metric]} 표기가 쪽마다 같은가", other["row"], base["row"].rid,
                f"{base['row'].page}쪽 {_show(base['row'].value, base['row'].unit)} / {other['row'].page}쪽 {_show(other['row'].value, other['row'].unit)}",
                MISMATCH, None, None, "같은 항목이 서로 다르게 적혀 있습니다", extra_rids=(base["row"].rid,))
    return out


def _rounded_same(a: float, b: float) -> bool:
    """한쪽이 다른 쪽을 반올림하거나 끊어 적은 값인가. 예: 목표주가 287,108원을 287,000원으로 적음.
    끝자리 0의 개수로 단위를 정하고(287,000 -> 1,000원 단위), 차이가 그 단위보다 작고 1% 이내일 때만."""
    def zeros(x: float) -> int:
        if x != int(x) or x == 0:
            return 0
        n, x = 0, int(abs(x))
        while x % 10 == 0:
            n, x = n + 1, x // 10
        return n
    step = 10 ** max(zeros(a), zeros(b))
    diff = abs(a - b)
    return step > 1 and diff < step and diff <= 0.01 * max(abs(a), abs(b))


def _display_unit(row: DataRow) -> float:
    m = re.search(r"\.(\d+)", re.sub(r"[\s,]", "", row.value or ""))
    return 10 ** -len(m.group(1)) if m else 1.0


def extend(nc: dict, report: Report) -> dict:
    """숫자 검증 결과에 고정 산식 검산을 붙이고 개수를 다시 센다."""
    from .table_check import growth_checks
    results = [r for r in nc["results"] if r.get("kind") != "formula"]
    results += run(report, results)
    results += growth_checks(report)               # 표의 증감률(YoY) 행
    count = {MATCH: 0, MISMATCH: 0, UNKNOWN: 0}
    for r in results:
        count[r["status"] if r["status"] in count else UNKNOWN] += 1
    checked = {rid for r in results for rid in r.get("rids", [])} | {x for r in results for x in re.findall(r"\b[RM]-\d+\b", r.get("expr") or "")}
    out = dict(nc, results=results, count=count)
    if "n_unchecked" in nc:
        out["n_unchecked"] = len([r for r in report.data_rows if r.rid not in checked])
    return out
