"""리포트의 거시경제 숫자(환율, 금리, 물가)를 한국은행 ECOS 공식 통계와 대조한다. AI를 쓰지 않는다.

대상 지표 (코드는 ECOS StatisticItemList 로 확인함, 2026-11)
- 원/달러, 원/위안, 원/100엔 환율 (매매기준율): 공식 월·분기·연 평균 731Y004, 일별 731Y001
- 한국은행 기준금리: 일별 722Y001 (시점 값. 달·분기·연으로 적었으면 그 기간에 적용된 값과 비교)
- 국고채 3년·10년, 회사채 3년(AA-): 일별 817Y002 (기간이면 일별 평균)
- 소비자물가 상승률: 소비자물가지수 901Y009 로 전년 같은 기간 대비 계산

기간: 연(2025년), 분기(1Q26, 2026년 1분기), 월(2026년 5월), 여러 달(26년 1~5월 평균), 날짜(2026-06-03).
      기간이 없으면 작성일 기준 값(시점 지표만).

판정
- 일치      : 공식 값과 리포트 표시 자릿수 1단위 이내
- 불일치    : 작성 당시 이미 끝난 기간인데 1% 넘게 다르다
- 검증 불가 : 1% 안의 차이(계산 방식 차이일 수 있다), 기간이 작성일 뒤에 끝난다, 자료가 없다, 기간을 특정할 수 없다
추정치(2026E 등)는 대조하지 않는다. 단 "26년 1~5월 평균"처럼 지난 기간을 밝힌 가정은 대조한다.
"""
from __future__ import annotations

import re
from calendar import monthrange
from datetime import date, datetime, timedelta
from statistics import mean
from typing import List, Optional, Tuple

from ..number_check import MATCH, MISMATCH, parse_number
from ..schemas import DataRow, Report
from .common import UNVERIFIABLE, make_result
from .ecos import SITE, Ecos, EcosError

PREFIX = "E"
NEAR = 1.0      # % : 이 안의 차이는 계산 방식 차이일 수 있어 불일치로 보지 않는다
NEAR_DAYS = 30  # 기간을 밝히지 않은 금리·환율: 작성일 전 몇 영업일의 값과 견줄지

# (이름, 찾을 말, 빼는 말, 평균 통계표, 일별 통계표, 항목, 단위, 종류)
INDICATORS = [
    ("원/달러 환율", r"원\s*/\s*(미국\s*)?달러|원달러|달러\s*환율|USD\s*/?\s*KRW", r"달러\s*/|엔\s*/", "731Y004", "731Y001", "0000001", "원", "level"),
    ("원/위안 환율", r"원\s*/\s*위안|위안\s*환율|CNY\s*/?\s*KRW", r"", "731Y004", "731Y001", "0000053", "원", "level"),
    ("원/100엔 환율", r"원\s*/\s*(100)?\s*엔|엔\s*환율|JPY\s*/?\s*KRW", r"", "731Y004", "731Y001", "0000002", "원", "level"),
    ("한국은행 기준금리", r"기준\s*금리", r"미국|연준|Fed|FOMC|ECB|일본|중국", None, "722Y001", "0101000", "%", "point"),
    ("국고채 3년 금리", r"국고채\s*\(?\s*3\s*년", r"", None, "817Y002", "010200000", "%", "level"),
    ("국고채 10년 금리", r"국고채\s*\(?\s*10\s*년", r"", None, "817Y002", "010210000", "%", "level"),
    ("회사채 3년(AA-) 금리", r"회사채.*AA-|AA-.*회사채", r"", None, "817Y002", "010300000", "%", "level"),
    ("소비자물가 상승률", r"소비자\s*물가\s*(상승률|상승|증가율)|CPI\s*(상승률|YoY|증가율)|물가\s*상승률", r"미국|유럽|일본|중국|식품|근원", "901Y009", None, "0", "%", "yoy"),
]
_ISO = re.compile(r"(20\d\d)[-./](\d{1,2})[-./](\d{1,2})")
_EST = re.compile(r"(20\d\d|\d\d)\s*[EeFf](?![a-z])|전망|예상|추정|목표|컨센서스")


def _yr(s: str) -> int:
    v = int(s)
    return v if v > 1000 else 2000 + v


def parse_period(text: str, report_date: str) -> Optional[dict]:
    """글에서 기간을 찾는다. 돌려주는 값: {'cycle': A|Q|M|R|D, 'start': date, 'end': date, 'label'} 또는 None"""
    t = text.replace("’", "'").replace("‘", "'")
    # '국고채 10년', '회사채 3년물', '10년 만기'의 '10년'은 만기라서 연도(2010년)로 읽지 않는다
    t = re.sub(r"(국고채|국채|회사채|통안채|채권|금리)\s*\(?\s*\d{1,2}\s*년\s*물?", r"\1 ", t)
    t = re.sub(r"(?<![\d'])\d{1,2}\s*년\s*(물|만기)", " ", t)
    m = re.search(r"'?(\d{2,4})\s*년?\s*(\d{1,2})\s*[~∼\-–]\s*(\d{1,2})\s*월", t)
    if m:
        y, a, b = _yr(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= a <= b <= 12:
            return {"cycle": "R", "start": date(y, a, 1), "end": date(y, b, monthrange(y, b)[1]), "label": f"{y}년 {a}~{b}월"}
    m = re.search(r"([1-4])\s*Q\s*'?(\d{2,4})|'?(\d{2,4})\s*년?\s*([1-4])\s*(분기|Q)", t)
    if m:
        q, y = (int(m.group(1)), _yr(m.group(2))) if m.group(1) else (int(m.group(4)), _yr(m.group(3)))
        return {"cycle": "Q", "start": date(y, 3 * q - 2, 1), "end": date(y, 3 * q, monthrange(y, 3 * q)[1]), "label": f"{y}년 {q}분기"}
    for m in _ISO.finditer(t):
        d = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        if d != report_date:                             # 작성일은 기간을 밝힌 것이 아니다
            dd = date.fromisoformat(d)
            return {"cycle": "D", "start": dd, "end": dd, "label": d}
    m = re.search(r"'?(\d{2,4})\s*년\s*(\d{1,2})\s*월", t)
    if m:
        y, mo = _yr(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12:
            return {"cycle": "M", "start": date(y, mo, 1), "end": date(y, mo, monthrange(y, mo)[1]), "label": f"{y}년 {mo}월"}
    m = re.search(r"(20\d\d)\s*(년|$|\s|평균|연평균|FY)|'(\d\d)\s*년?|(?<!\d)(\d\d)\s*년", t)
    if m:
        y = _yr(next(g for g in (m.group(1), m.group(3), m.group(4)) if g))
        return {"cycle": "A", "start": date(y, 1, 1), "end": date(y, 12, 31), "label": f"{y}년"}
    return None


def classify(row: DataRow, report_date: str) -> dict:
    item = row.item or ""
    spec = next((s for s in INDICATORS if re.search(s[1], item) and not (s[2] and re.search(s[2], item))), None)
    if not spec:
        return {"skip": "거시 지표가 아님"}
    ctx = " ".join(x for x in (item, row.as_of, row.basis) if x)
    per = parse_period(ctx, report_date)
    if _EST.search(ctx) and not (per and per["cycle"] in ("R", "M", "D")):
        return {"skip": f"{spec[0]} 추정·전망치라 대조할 공식 값이 없음"}
    return {"spec": spec, "period": per}


def _ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def _values(rows: List[dict]) -> List[Tuple[str, float]]:
    """(시점, 값) 목록. 평균 통계표는 같은 시점에 '평균자료'와 '말일자료'가 함께 오므로 평균만 쓴다."""
    out = []
    for r in rows:
        if r.get("ITEM_NAME2") and r["ITEM_NAME2"] != "평균자료":
            continue
        try:
            out.append((r["TIME"], float(str(r["DATA_VALUE"]).replace(",", ""))))
        except (KeyError, ValueError):
            pass
    return out


def official(ecos: Ecos, spec: tuple, per: dict, report_date: str) -> Tuple[List[Tuple[str, float]], str, str, str, str]:
    """공식 값 후보들 [(방법, 값)…]과 (통계표, 주기, 기록용 주소, 조회 시점). 자료가 없으면 빈 목록."""
    name, _, _, avg_stat, day_stat, item, unit, kind = spec
    cyc, s, e = per["cycle"], per["start"], per["end"]
    cands: List[Tuple[str, float]] = []
    url, table = "", ""
    seen: List[str] = []                                     # 쓴 응답의 조회 시점

    def fetch(stat, c, a, b):
        res = ecos.series(stat, c, a, b, item)
        seen.append(res["fetched_at"])
        return res

    def daily(a: date, b: date):
        res = fetch(day_stat, "D", _ymd(a), _ymd(b))
        return _values(res["rows"]), res

    if kind == "yoy":                                       # 물가 상승률: 지수로 계산
        key = {"A": ("A", str(s.year), str(s.year - 1)), "Q": ("Q", f"{s.year}Q{(s.month + 2) // 3}", f"{s.year - 1}Q{(s.month + 2) // 3}"),
               "M": ("M", s.strftime("%Y%m"), f"{s.year - 1}{s.month:02d}")}.get(cyc)
        if not key:
            return [], "", "", "", ""
        c, now, before = key
        res = fetch(avg_stat, c, before, now)
        v = dict(_values(res["rows"]))
        if now in v and before in v and v[before]:
            cands.append(("소비자물가지수 전년 같은 기간 대비", (v[now] / v[before] - 1) * 100))
        return cands, avg_stat, c, res["api_url"], max(seen)

    if cyc == "D" or (kind == "point" and cyc is None):
        target = per["start"] if cyc == "D" else date.fromisoformat(report_date)
        vals, res = daily(target - timedelta(days=10), target)
        if vals:
            t, v = vals[-1]
            cands.append((f"{t[:4]}-{t[4:6]}-{t[6:]} 값" + ("" if t == _ymd(target) else " (그날 자료가 없어 직전 영업일)"), v))
        return cands, day_stat, "D", res["api_url"], max(seen)
    if kind == "point":
        # 기준금리를 달·분기·연으로 적은 경우: 그 기간에 적용된 값으로 비교한다 (평균은 내지 않는다).
        # 기간 내내 같았으면 그 값 하나, 기간 중에 바뀌었으면 기간 첫 값과 마지막 값 (리포트가 어느 쪽을 썼는지 모르므로 둘 다)
        vals, res = daily(s - timedelta(days=400), e)           # 바뀐 날에만 자료가 있어도 기간 시작 때 값을 알 수 있게 앞을 넉넉히
        start, end = _ymd(s), _ymd(e)
        before = [v for t, v in vals if t < start]
        inside = [v for t, v in vals if start <= t <= end]
        applied = ([before[-1]] if before else []) + inside
        if applied:
            if len(set(applied)) == 1:
                cands.append(("그 기간 내내 적용된 값", applied[0]))
            else:
                cands += [("기간 첫 값 (기간 중 금리가 바뀜)", applied[0]),
                          ("기간 마지막 값 (기간 중 금리가 바뀜)", applied[-1])]
        return cands, day_stat, "D", res["api_url"], max(seen)

    if avg_stat and cyc in ("A", "Q", "M"):
        code = {"A": str(s.year), "Q": f"{s.year}Q{(s.month + 2) // 3}", "M": s.strftime("%Y%m")}[cyc]
        res = fetch(avg_stat, cyc, code, code)
        v = _values(res["rows"])
        if v:
            cands.append((f"공식 {'연' if cyc == 'A' else '분기' if cyc == 'Q' else '월'}평균", v[0][1]))
            return cands, avg_stat, cyc, res["api_url"], max(seen)
    if avg_stat and cyc == "R":
        res = fetch(avg_stat, "M", s.strftime("%Y%m"), e.strftime("%Y%m"))
        v = _values(res["rows"])
        months = (e.year - s.year) * 12 + e.month - s.month + 1
        if len(v) == months:
            cands.append((f"공식 월평균 {months}개월의 평균", mean(x for _, x in v)))
        url, table = res["api_url"], avg_stat
    vals, res = daily(s, e)                                 # 일별 값의 평균 (공식 평균이 없을 때, 또는 함께 비교)
    if vals:
        cands.append((f"일별 값 {len(vals)}영업일 평균", mean(x for _, x in vals)))
    return cands, table or day_stat, "M" if table else "D", url or res["api_url"], max(seen) if seen else ""


def near_report(ecos: Ecos, spec: tuple, report_date: str) -> Tuple[List[Tuple[str, float]], str, str, str, str]:
    """기간을 밝히지 않은 수준 지표(금리·환율): 작성일 무렵의 값 후보. 직전 30영업일 하나하나, 직전 1개월·3개월 일별 평균.
    (2026-11-21 실제 리포트 분석: CAPM 무위험수익률이 작성일 2주 전(10/26) 값이었다)"""
    end = date.fromisoformat(report_date)
    res = ecos.series(spec[4], "D", _ymd(end - timedelta(days=95)), _ymd(end), spec[5])
    vals = [(t, v) for t, v in _values(res["rows"]) if t <= _ymd(end)]
    cands: List[Tuple[str, float]] = []
    for t, v in vals[-NEAR_DAYS:][::-1]:
        cands.append((f"{t[:4]}-{t[4:6]}-{t[6:]} 값", v))
    for days, label in ((31, "직전 1개월"), (92, "직전 3개월")):
        xs = [v for t, v in vals if t > _ymd(end - timedelta(days=days))]
        if xs:
            cands.append((f"{label} 일별 평균", mean(xs)))
    return cands, spec[4], "D", res["api_url"], res["fetched_at"]


def check_row(ecos: Ecos, row: DataRow, info: dict, report_date: str, cid: str) -> dict:
    spec, per = info["spec"], info["period"]
    name_ = spec[0]
    label = per["label"] if per else ("작성일" if spec[7] == "point" else "기간 미상")
    name = f"{name_} ({label}), 통계 대조"

    def res(status, reason, **extra):
        return make_result("ecos", cid, row, name, status, reason, **extra)

    stated = parse_number(row.value, row.unit)
    if stated is None:
        return res(UNVERIFIABLE, f"리포트 값 '{row.value}'를 숫자 하나로 읽지 못했습니다.")
    near = not per and spec[7] != "point"              # 기간을 밝히지 않음: 작성일 무렵 값과 견주어 본다 (2026-11-21)
    if near and not spec[4]:
        return res(UNVERIFIABLE, "어느 기간의 값인지 리포트에서 찾지 못해 대조하지 않았습니다.")
    if per and per["end"].isoformat() > report_date:
        return res(UNVERIFIABLE, f"{per['label']}은 리포트 작성일({report_date})에 아직 끝나지 않은 기간입니다.")
    if near:
        cands, table, cycle, url, fetched = near_report(ecos, spec, report_date)
        label = "기간 미상, 작성일 무렵 값과 비교"
        name = f"{name_} ({label}), 통계 대조"
    else:
        cands, table, cycle, url, fetched = official(ecos, spec, per or {"cycle": None, "start": None, "end": None}, report_date)
    if not cands:
        why = "ECOS에 그 기간 자료가 없습니다"
        return res(UNVERIFIABLE, f"{why}.")

    m = re.search(r"\.(\d+)", re.sub(r"[\s,]", "", row.value))
    dec = len(m.group(1)) if m else 0
    tol = 10 ** -dec
    fmt = lambda v: f"{v:,.{max(dec, 2 if spec[6] == '%' else 1)}f}{spec[6]}"   # noqa: E731
    best = min(cands, key=lambda c: abs(c[1] - stated))
    diff = abs(best[1] - stated)
    diff_pct = round(diff / abs(best[1]) * 100, 3) if best[1] else None
    src = {
        "display": [["출처", "한국은행 경제통계시스템(ECOS)"],
                    ["지표", f"{name_} (통계표 {table}, 항목 {spec[5]})"],
                    ["기간", label],
                    ["비교 값", ", ".join(f"{k} {fmt(v)}" for k, v in (cands if not near else [best] + [c for c in cands if "평균" in c[0] and c != best]))]],
        "short": f"ECOS {name_}, {label}, {best[0]}",
        "provider": "한국은행 ECOS", "stat_code": table, "item_code": spec[5], "cycle": cycle,
        "period": label, "url": SITE, "api_url": url,
        "fetched_at": fetched or datetime.now().isoformat(timespec="seconds"), "report_date": report_date, "timing": "작성 당시 공개",
    }
    extra = dict(computed=fmt(best[1]), diff_pct=diff_pct, source=src, timing=src["timing"])
    if diff <= tol + 1e-9:
        if near:
            return res(MATCH, f"리포트가 기간을 밝히지 않아 작성일 무렵 값과 견주었습니다. {best[0]}({fmt(best[1])})와 같습니다(표시 자릿수 이내).", **extra)
        return res(MATCH, f"{label} {best[0]}({fmt(best[1])})와 같습니다(표시 자릿수 이내).", **extra)
    if near:                                           # 기준을 모르므로 틀렸다고 하지 않는다
        close = f" 가장 가까운 값은 {best[0]} {fmt(best[1])}({diff_pct}% 차이)로, 평균 낸 구간이 달라서일 수 있습니다." if diff_pct is not None and diff_pct <= NEAR else ""
        return res(UNVERIFIABLE, f"리포트가 어느 기간의 값인지 밝히지 않았고, 작성일 무렵 값 가운데 같은 것이 없어 판단하지 않았습니다.{close}", **extra)
    if diff_pct is not None and diff_pct <= NEAR:
        return res(UNVERIFIABLE, f"{label} {best[0]}({fmt(best[1])})와 {diff_pct}% 다릅니다. 평균 내는 방식이나 기준(매매기준율·종가)의 차이일 수 있어 판단하지 않았습니다.", **extra)
    return res(MISMATCH, f"작성 당시 이미 끝난 기간({label})의 공식 값 {fmt(best[1])}과 {diff_pct}% 다릅니다.", **extra)


def run(report: Report, ecos: Optional[Ecos] = None) -> dict:
    ecos = ecos or Ecos()
    out = {"enabled": True, "provider": "ECOS", "results": [], "skipped": [], "error": None}
    report_date = report.meta.report_date or report.meta.snapshot_date
    try:
        n = 0
        for row in report.data_rows:
            info = classify(row, report_date)
            if "skip" in info:
                if info["skip"] != "거시 지표가 아님":
                    out["skipped"].append({"rid": row.rid, "item": row.item, "why": info["skip"]})
                continue
            n += 1
            out["results"].append(check_row(ecos, row, info, report_date, f"{PREFIX}-{n}"))
    except EcosError as e:
        out["error"] = str(e)
    except RuntimeError as e:
        out.update(enabled=False, error=str(e))
    out["calls"] = ecos.calls
    return out


def maybe_run(report: Report, cfg: dict) -> Optional[dict]:
    if not (cfg.get("external") or {}).get("ecos", False):
        return None
    return run(report)
