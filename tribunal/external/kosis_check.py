"""리포트의 산업 통계(생산량, 출하량, 판매액, 수출액, 물가지수 등)를 통계청 KOSIS 와 대조한다. AI를 쓰지 않는다.

KOSIS 에는 통계표가 수만 개라 어느 표와 맞춰야 하는지 미리 알 수 없다. 그래서
1. 숫자 이름으로 KOSIS 를 검색해 그 기간이 들어 있는 표를 몇 개 고르고
2. 그 기간의 표 값 전체에서 리포트 값과 같은 값(단위를 맞춘 뒤 표시 자릿수 1단위 이내)을 찾는다.
판정은 두 가지뿐이다.
- 일치      : 같은 기간·같은 단위의 같은 값을 찾았다. 어느 표의 어느 항목인지 적는다.
- 검증 불가 : 찾지 못했다. 엉뚱한 표와 비교할 수 있어서 '불일치'라고 하지 않는다.
재무제표 계정(DART), 환율·금리·물가(ECOS), 주가(KRX)는 여기서 다루지 않는다.
"""
from __future__ import annotations

import re
from datetime import date
from typing import List, Optional, Tuple

from ..number_check import MATCH, parse_number
from ..schemas import DataRow, Report
from .common import UNVERIFIABLE, make_result
from .ecos_check import parse_period
from .kosis import SITE, Kosis, KosisError

PREFIX = "S"
MAX_ROWS, MAX_TABLES = 10, 3
_STAT = re.compile(r"생산량|생산\s*실적|생산액|출하량|출하액|판매량|판매액|소매\s*판매|수출액|수출량|수입액|수입량|시장\s*규모|소비량|"
                   r"재고|사업체\s*수|종사자\s*수|가구\s*수|인구|물가지수|가격지수|생산자물가|생산지수|출하지수")
_SKIP = re.compile(r"환율|금리|소비자\s*물가\s*상승률|CPI|주가|시가총액|매출액|영업이익|순이익|자산|부채|자본|목표|전망|예상|추정|가정|점유율|비중|%")
_EST = re.compile(r"(20\d\d|\d\d)\s*[EeFf](?![a-z])")
MAG = {"": 1, "천": 1e3, "만": 1e4, "십만": 1e5, "백만": 1e6, "천만": 1e7, "억": 1e8, "십억": 1e9, "백억": 1e10, "천억": 1e11, "조": 1e12}
BASES = ("원", "달러", "톤", "kg", "개", "명", "대", "가구", "건", "개소", "식", "봉", "상자", "리터", "kl", "배럴")


def split_unit(u: str) -> Tuple[Optional[float], str]:
    """'백만원' -> (1e6, '원'), '천톤' -> (1e3, '톤'), '2020=100' -> (1, 지수). 모르면 (None, '')"""
    u = re.sub(r"[\s()（）]", "", u or "").replace("＝", "=").replace("US$", "달러").replace("$", "달러").replace("천달러", "천달러")
    if re.search(r"\d{4}=100|지수", u) or u in ("", "pt", "p"):
        return 1.0, "지수"
    for base in sorted(BASES, key=len, reverse=True):
        if u.endswith(base):
            head = u[: -len(base)]
            if head in MAG:
                return MAG[head], base
    return None, ""


_TOTAL = re.compile(r"^(총지수|총계|합계|계|전체|전국|소계)$")


GENERIC = re.compile(r"^(지수|물가|가격|실적|금액|수량|규모|총|합계|국내|해외|전국|연간|월간|분기|기준|품목별|생산자|소비자|"
                     r"국내외|우리나라|한국|전체|평균|동향|현황|통계|자료)$")


def subjects(words: str) -> List[str]:
    """검색어에서 '무엇의' 통계인지 나타내는 말 (통계 종류와 흔한 말을 뺀 나머지). '라면 생산자물가지수' -> ['라면']"""
    rest = _STAT.sub(" ", words)
    return [w for w in re.split(r"\s+", rest) if len(w) >= 2 and not GENERIC.match(w)]


def _row_ok(x: dict, subj: List[str]) -> bool:
    """찾은 값의 줄이 리포트 숫자와 같은 대상인가. 대상 말이 있으면 분류·항목 이름에 들어 있어야 하고, 없으면 합계 줄만."""
    names = " ".join(str(x.get(k) or "") for k in ("C1_NM", "C2_NM", "C3_NM", "ITM_NM"))
    if subj:
        return any(w in names for w in subj)
    return bool(_TOTAL.match(str(x.get("C1_NM") or "").strip()))


def classify(row: DataRow, report_date: str) -> Optional[dict]:
    item = row.item or ""
    if not _STAT.search(item):
        return None
    if _SKIP.search(item):
        return {"skip": "다른 대조(DART·ECOS·KRX) 대상이거나 비율·전망이라 KOSIS 와 대조하지 않음"}
    ctx = " ".join(x for x in (item, row.as_of, row.basis) if x)
    if _EST.search(ctx):
        return {"skip": "추정치라 대조할 공식 통계가 없음"}
    per = parse_period(ctx, report_date)
    if not per or per["cycle"] not in ("A", "M", "Q"):
        return {"skip": "연·월·분기 가운데 어느 기간인지 읽지 못함"}
    words = re.sub(r"'?\d+\s*(년도|년|월|분기|일|Q|E)|[1-4]\s*Q\s*'?\d*|\d+|[()\[\],·/~\-]|기준|누적|평균", " ", item)
    words = re.sub(r"\s+", " ", words).strip()
    return {"period": per, "words": words}


def _code(per: dict) -> Tuple[str, str]:
    s = per["start"]
    return {"A": ("Y", str(s.year)), "M": ("M", s.strftime("%Y%m")), "Q": ("Q", f"{s.year}{(s.month + 2) // 3:02d}")}[per["cycle"]]


def _covers(tbl: dict, code: str) -> bool:
    a, b = str(tbl.get("STRT_PRD_DE") or ""), str(tbl.get("END_PRD_DE") or "")
    n = len(a)
    return bool(a) and a <= code[:n] <= (b or "9999")[:n]


def check_row(kosis: Kosis, row: DataRow, info: dict, report_date: str, cid: str) -> dict:
    per, words = info["period"], info["words"]
    name = f"{words} ({per['label']}), 산업 통계 대조"

    def res(status, reason, **extra):
        return make_result("kosis", cid, row, name, status, reason, **extra)

    if per["end"].isoformat() >= report_date:
        return res(UNVERIFIABLE, f"{per['label']}은 리포트 작성일({report_date})에 아직 끝나지 않은 기간입니다.")
    stated = parse_number(row.value, row.unit)
    mult, base = split_unit(row.unit or re.sub(r"[\d,.\s]", "", row.value or ""))
    if stated is None or mult is None:
        return res(UNVERIFIABLE, f"리포트 값 '{row.value}'(단위 '{row.unit}')를 숫자와 단위로 읽지 못했습니다.")
    m = re.search(r"\.(\d+)", re.sub(r"[\s,]", "", row.value or ""))
    tol = (10 ** -len(m.group(1)) if m else 1.0) * mult
    target = stated * mult
    cycle, code = _code(per)
    found = kosis.search(words)
    tables = [t for t in found["rows"] if _covers(t, code)][:MAX_TABLES]
    if not tables:
        return res(UNVERIFIABLE, f"KOSIS 에서 '{words}'로 {per['label']} 자료가 있는 통계표를 찾지 못했습니다.")
    subj = subjects(words)
    looked: List[str] = []
    for t in tables:
        data = kosis.data(t["ORG_ID"], t["TBL_ID"], cycle, code)
        looked.append(t["TBL_NM"])
        for x in data["rows"]:
            km, kb = split_unit(x.get("UNIT_NM", ""))
            if km is None or kb != base:
                continue
            try:
                v = float(str(x.get("DT", "")).replace(",", "")) * km
            except ValueError:
                continue
            if abs(v - target) <= tol and _row_ok(x, subj):
                cat = " · ".join(str(x.get(k)) for k in ("C1_NM", "C2_NM", "C3_NM") if x.get(k))
                src = {
                    "display": [["출처", f"통계청 KOSIS, {t.get('ORG_NM', '')}"], ["통계표", f"{t['TBL_NM']} ({t['TBL_ID']})"],
                                ["항목", f"{cat} / {x.get('ITM_NM', '')}"], ["기간", f"{x.get('PRD_DE')} ({x.get('UNIT_NM', '')})"]],
                    "short": f"KOSIS {t['TBL_NM']}, {cat}, {x.get('PRD_DE')}",
                    "provider": "통계청 KOSIS", "org": t.get("ORG_NM", ""), "tbl_id": t["TBL_ID"], "tbl_nm": t["TBL_NM"],
                    "item": cat, "period": x.get("PRD_DE"), "url": t.get("LINK_URL") or SITE, "api_url": data["api_url"],
                    "fetched_at": data["fetched_at"], "report_date": report_date, "timing": "작성 당시 공개",
                }
                shown = f"{x.get('DT')} ({x.get('UNIT_NM')})" if kb == "지수" else f"{x.get('DT')}{x.get('UNIT_NM', '')}"
                return res(MATCH, f"KOSIS '{t['TBL_NM']}'의 {cat} {x.get('PRD_DE')} 값 {shown}과 같습니다.",
                           computed=shown, diff_pct=round(abs(v - target) / abs(v) * 100, 3) if v else None,
                           source=src, timing=src["timing"])
    who = f"'{', '.join(subj)}' 항목에서 " if subj else "합계 줄에서 "
    return res(UNVERIFIABLE, f"KOSIS 통계표 {len(looked)}개({', '.join(looked)[:120]})의 {who}같은 기간·같은 단위의 같은 값을 찾지 못했습니다. "
               "다른 표에 있을 수 있어 불일치로 보지 않았습니다.")


def run(report: Report, kosis: Optional[Kosis] = None) -> dict:
    kosis = kosis or Kosis()
    out = {"enabled": True, "provider": "KOSIS", "results": [], "skipped": [], "error": None}
    report_date = report.meta.report_date or report.meta.snapshot_date or date.today().isoformat()
    try:
        n = 0
        for row in report.data_rows:
            info = classify(row, report_date)
            if info is None:
                continue
            if "skip" in info:
                out["skipped"].append({"rid": row.rid, "item": row.item, "why": info["skip"]})
                continue
            if n >= MAX_ROWS:
                out["skipped"].append({"rid": row.rid, "item": row.item, "why": f"한 리포트에 {MAX_ROWS}개까지만 대조"})
                continue
            n += 1
            out["results"].append(check_row(kosis, row, info, report_date, f"{PREFIX}-{n}"))
    except KosisError as e:
        out["error"] = str(e)
    except RuntimeError as e:
        out.update(enabled=False, error=str(e))
    out["calls"] = kosis.calls
    return out


def maybe_run(report: Report, cfg: dict) -> Optional[dict]:
    if not (cfg.get("external") or {}).get("kosis", False):
        return None
    return run(report)
