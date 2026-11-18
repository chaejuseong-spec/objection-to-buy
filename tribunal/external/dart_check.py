"""리포트의 과거 실적 숫자를 OpenDART 공시 재무제표와 대조한다. AI를 쓰지 않는다.

판정
- 일치      : 공시 값과 차이가 리포트 표시 자릿수 1단위 이내
- 불일치    : 리포트 작성 당시 이미 공시된 값과 다르다
- 검증 불가 : 데이터가 없거나, 기준(연도·회사·연결/별도·단위)을 특정할 수 없거나,
              리포트 작성 이후에 나온 공시와만 다르다. 추정하지 않는다.
추정치(2026E 등), 비율(%·배), 분기 수치는 이번 대조 대상이 아니다(건너뛴 이유를 남긴다).

이미 끝난 분석에 더하기: python -m tribunal.external <분석 결과 폴더>  (Gemini 를 부르지 않는다)
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from ..number_check import MATCH, MISMATCH, parse_number
from ..schemas import DataRow, Report
from .common import UNVERIFIABLE, external_marks, make_result, merge, summary  # noqa: F401 (runner 가 여기서 부른다)
from .dart import Dart, DartError, NO_DATA, original_url, rcept_date

PREFIX = "D"
FS_NAME = {"CFS": "연결", "OFS": "별도"}

# (이름, 리포트에서 찾을 말, DART account_id, DART 계정 이름, 재무제표 구분). 긴 말을 먼저 찾는다.
ACCOUNTS = [
    ("지배주주순이익", ["지배주주순이익", "지배순이익", "지배주주 순이익", "지배기업 순이익"],
     ["ifrs-full_ProfitLossAttributableToOwnersOfParent"], ["지배기업의소유주에게귀속되는당기순이익", "지배기업소유주지분", "지배기업의소유주지분"], ("IS", "CIS")),
    ("영업이익", ["영업이익"], ["dart_OperatingIncomeLoss"], ["영업이익", "영업이익(손실)"], ("IS", "CIS")),
    ("당기순이익", ["당기순이익", "순이익"], ["ifrs-full_ProfitLoss"], ["당기순이익", "당기순이익(손실)", "연결당기순이익"], ("IS", "CIS")),
    ("배당금수익", ["배당금수익"], [], ["배당금수익"], ("IS", "CIS")),
    ("매출액", ["매출액", "매출"], ["ifrs-full_Revenue"], ["매출액", "수익(매출액)", "영업수익", "매출"], ("IS", "CIS")),
    ("자산총계", ["자산총계", "자산 총계", "총자산"], ["ifrs-full_Assets"], ["자산총계"], ("BS",)),
    ("부채총계", ["부채총계", "부채 총계", "총부채"], ["ifrs-full_Liabilities"], ["부채총계"], ("BS",)),
    ("자본총계", ["자본총계", "자본 총계", "총자본"], ["ifrs-full_Equity"], ["자본총계"], ("BS",)),
]
UNIT_MULT = {"원": 1, "천원": 1e3, "백만원": 1e6, "억원": 1e8, "십억원": 1e9, "조원": 1e12}
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")
_EST = re.compile(r"(20\d\d|'?\d\d)\s*[EeFfPp](?![a-z])|예상|전망|추정|목표|컨센서스|가이던스")
_QTR = re.compile(r"[1-4]\s*Q|분기|반기|상반기|하반기|[12]H|H[12]")
_RATIO = re.compile(r"률|율|마진|성향|비중|증가|감소|성장|YoY|QoQ|%|배$")
_NOW = re.compile(r"(^|\s)(현|현재|최근|직전|당기)(\s|$)")
_YEAR = re.compile(r"(20\d\d)\s*(?:년|년도|FY|결산|$|\s)|FY\s*(20\d\d)|'(\d\d)(?!\d)")


# ---------- 리포트 숫자 읽기 ----------
def _norm(s: str) -> str:
    return re.sub(r"\s", "", s or "")


REPRT = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}       # 1분기, 반기, 3분기, 사업보고서
_Q = re.compile(r"(?<![\d.])([1-4])\s*Q\s*'?(\d{2,4})|'?(\d{2,4})\s*[.\s]?\s*([1-4])\s*Q(?![a-z])|'?(\d{2,4})\s*년?\s*([1-4])\s*분기")
_H = re.compile(r"'?(\d{2,4})\s*년?\s*(상반기|1H|H1|반기)|(1H|H1)\s*'?(\d{2,4})")
_CUM = re.compile(r"누적|누계|YTD|1~[2-4]\s*Q|1~[2-4]\s*분기|[1-9]\s*~\s*[1-9]\s*월")


def _yy(s: str) -> int:
    v = int(s)
    return v if v > 1000 else 2000 + v


def quarter(text: str) -> Optional[dict]:
    """'1Q26', '2026년 1분기', '26.2Q', '2026년 상반기', '3Q26 누적' -> {'q': 1~4, 'year', 'cum': 누적 값인가, 'label'}"""
    m = _H.search(text)
    if m:
        y = _yy(m.group(1) or m.group(4))
        return {"q": 2, "year": y, "cum": True, "label": f"{y}년 상반기"}
    m = _Q.search(text)
    if not m:
        return None
    q, y = (int(m.group(1)), m.group(2)) if m.group(1) else (int(m.group(4)), m.group(3)) if m.group(4) else (int(m.group(6)), m.group(5))
    y = _yy(y)
    cum = bool(_CUM.search(text)) and q > 1
    return {"q": q, "year": y, "cum": cum, "label": f"{y}년 {q}분기" + (" 누적" if cum else "")}


def classify(row: DataRow, target_name: str) -> dict:
    """리포트 숫자 하나가 공시와 대조할 수 있는 과거 실적 금액인지 본다.
    돌려주는 값: {'skip': 이유} 또는 {'account', 'year', 'fs': CFS|OFS|None, 'company': 이름|None}"""
    item = row.item or ""
    clean_ctx = " ".join(_ISO.sub(" ", x) for x in (item, row.as_of, row.basis))
    acc = next(((a, kw) for a in ACCOUNTS for kw in a[1] if kw in item), None)
    if not acc or re.search(r"주당|EPS|BPS|DPS", item):
        return {"skip": "재무제표 계정이 아님"}
    if _RATIO.search(item) or row.unit.strip() in ("%", "%p", "배", "x"):
        return {"skip": "비율·배수라 이번 대조 대상이 아님"}
    if _EST.search(clean_ctx):
        return {"skip": "추정치라 공시와 대조할 값이 없음"}
    per = None
    if _QTR.search(clean_ctx):
        if "하반기" in clean_ctx or re.search(r"2H|H2", clean_ctx):
            return {"skip": "하반기 값은 공시에 따로 없어 대조하지 않음"}
        per = quarter(clean_ctx)
        if not per:
            return {"skip": "분기·반기라는 것은 보이지만 어느 분기인지 읽지 못함"}
    year = per["year"] if per else None
    for text in (item, row.as_of, row.basis):
        if year:
            break
        m = _YEAR.search(_ISO.sub(" ", text or ""))
        if m:
            g = next(x for x in m.groups() if x)
            year = int(g) if len(g) == 4 else 2000 + int(g)
            break
    year_how = "표기" if year else None
    if not year:
        year_how = "현재 표기" if _NOW.search(item) else "추정"   # 연도가 없으면 run() 이 직전 결산연도를 넣는다
    fs = "CFS" if "연결" in item else "OFS" if "별도" in item else None
    head = item.split(acc[1])[0]
    head = re.sub(r"연결|별도|(20\d\d|'\d\d)\s*년?|FY|[1-4]\s*Q\s*'?\d{2,4}|\d{2,4}\s*년?\s*[1-4]\s*분기|상반기|반기", " ", head).strip()
    company = None if not head or _norm(head) == _norm(target_name) else head
    return {"account": acc[0][0], "year": year, "fs": fs, "company": company, "period": per, "year_how": year_how}


def _won(row: DataRow) -> Tuple[Optional[float], Optional[float]]:
    """리포트 금액 -> (원 단위 값, 단위 배수). 값에 조·억·만이 적혀 있으면 값만으로 원 단위로 읽고
    단위 칸은 쓰지 않는다 ('1 조 8,367 억 원'의 단위 칸이 '조 원'이어도 1조 8,367억 원)."""
    if re.search(r"\d\s*[조억만]", row.value or ""):
        v = parse_number(row.value, "")
        return (v, 1.0) if v is not None else (None, None)
    mult = _unit_mult(row)
    v = parse_number(row.value, row.unit)
    return (v * mult, mult) if v is not None and mult is not None else (None, None)


def _unit_mult(row: DataRow) -> Optional[float]:
    u = _norm(row.unit)
    if u in UNIT_MULT:
        return UNIT_MULT[u]
    if not u and _norm(row.value).endswith("원"):
        return 1.0
    return None


def _tolerance(row: DataRow, mult: float) -> float:
    """리포트 표시 자릿수 1단위 (반올림·버림 모두 허용). '1조 8,367억'이면 1억, '3,514,316'(백만원)이면 1백만원."""
    v = _norm(row.value)
    for word, mag in (("만", 1e4), ("억", 1e8), ("조", 1e12)):   # 값 안에 쓴 가장 작은 단위
        if word in v:
            return mag * mult
    m = re.search(r"\.(\d+)", v)
    return (10 ** -len(m.group(1)) if m else 1.0) * mult


def _fmt(won: float, row: DataRow, mult: float) -> str:
    v = _norm(row.value)
    if any(w in v for w in "조억만"):
        eok = won / 1e8
        jo, rest = divmod(round(eok), 10000)
        return (f"{jo:,}조 {rest:,}억" if jo else f"{rest:,}억") + "원"
    m = re.search(r"\.(\d+)", v)
    d = len(m.group(1)) if m else 0
    return f"{won / mult:,.{d}f}{row.unit.strip()}"


# ---------- 공시 값 찾기 ----------
def find_account(items: List[dict], account: str) -> Optional[dict]:
    spec = next(a for a in ACCOUNTS if a[0] == account)
    _, _, ids, names, sjs = spec
    rows = [x for x in items if x.get("sj_div") in sjs]
    rows.sort(key=lambda x: sjs.index(x["sj_div"]))
    for x in rows:                                     # 표준 계정 id 가 가장 정확하다
        if x.get("account_id") in ids and x.get("thstrm_amount") not in (None, "", "-"):
            return x
    want = {_norm(n) for n in names}
    for x in rows:
        if _norm(x.get("account_nm", "")) in want and x.get("thstrm_amount") not in (None, "", "-"):
            return x
    return None


def _amount(x: dict, field: str = "thstrm_amount") -> Optional[float]:
    t = str(x.get(field, "")).replace(",", "").strip()
    try:
        return float(t)
    except ValueError:
        return None


def _period_amount(dart: Dart, corp: dict, year: int, fs: str, account: str, per: Optional[dict]):
    """(계정 줄, 응답, 그 기간의 금액, 계산 설명) 또는 None.
    분기 보고서의 thstrm_amount 는 그 분기 3개월, thstrm_add_amount 는 연초부터 누적. 재무상태표는 분기 말 잔액.
    4분기 단독 값은 공시에 없어서 사업보고서 연간 값 - 3분기 누적으로 계산한다."""
    q = per["q"] if per else None
    res = dart.statements(corp["corp_code"], year, fs, REPRT[q] if q else "11011")
    if res.get("status") == NO_DATA:
        return None
    acc = find_account(res.get("list", []), account)
    if not acc:
        return None
    bs = acc.get("sj_div") == "BS"
    if not q or bs or (q == 1):
        amt = _amount(acc)
        return (acc, res, amt, "") if amt is not None else None
    if q in (2, 3):
        amt = _amount(acc, "thstrm_add_amount" if per["cum"] else "thstrm_amount")
        return (acc, res, amt, "연초부터 누적한 값" if per["cum"] else "") if amt is not None else None
    # 4분기
    if per["cum"]:
        amt = _amount(acc)
        return (acc, res, amt, "연간 값") if amt is not None else None
    r3 = dart.statements(corp["corp_code"], year, fs, REPRT[3])
    a3 = find_account(r3.get("list", []), account) if r3.get("status") != NO_DATA else None
    year_amt, cum3 = _amount(acc), (_amount(a3, "thstrm_add_amount") if a3 else None)
    if year_amt is None or cum3 is None:
        return None
    return acc, res, year_amt - cum3, "사업보고서 연간 값에서 3분기 보고서 누적 값을 뺀 값"


# ---------- 대조 한 건 ----------
def _result(cid: str, row: DataRow, name: str, status: str, reason: str, **extra) -> dict:
    return make_result("dart", cid, row, name, status, reason, **extra)


NEAR_MAX, FAR_MIN, HINT_MAX = 0.20, 0.40, 0.50


def _nearest(rel: Dict[str, float]) -> Optional[str]:
    """연결/별도 표기가 없을 때: 한쪽과 20% 이내이고 다른 쪽과는 40% 이상이면서 2배 넘게 더 멀면 가까운 쪽.
    한쪽 재무제표만 있으면 20% 이내일 때 그쪽. 가를 수 없으면 None"""
    if not rel:
        return None
    best = min(rel, key=rel.get)
    if rel[best] > NEAR_MAX:
        return None
    if all(rel[o] >= max(FAR_MIN, 2 * rel[best]) for o in rel if o != best):
        return best
    return None


def check_row(dart: Dart, row: DataRow, info: dict, target: dict, report_date: str, cid: str,
              hint: Optional[str] = None, decide: bool = False) -> dict:
    """표기가 없고 값도 안 맞는 숫자는 먼저 '미정'으로 돌려준다(decide=False). 리포트 전체를 본 뒤 run() 이
    hint(같은 회사·같은 연도의 다른 숫자들이 맞은 재무제표)와 함께 decide=True 로 다시 불러 기준을 정한다."""
    year, account, per = info["year"], info["account"], info.get("period")
    year_how = info.get("year_how") or "표기"
    if not year and not per and year_how in ("현재 표기", "추정"):
        year = int(report_date[:4]) - 1                  # 직전 결산연도
    when = per["label"] if per else f"{year or '?'}년"
    label = f"{info['company'] or target['corp_name']} {when} {FS_NAME.get(info['fs'] or '', '')} {account}".replace("  ", " ")
    name = f"{label.strip()}, 공시 대조"
    if not year:
        return _result(cid, row, name, UNVERIFIABLE, "기준 연도를 리포트에서 찾지 못해 대조하지 않았습니다.")
    if per:
        end = f"{year}-{per['q'] * 3:02d}-{[31, 30, 30, 31][per['q'] - 1]}"
        if end >= report_date:
            return _result(cid, row, name, UNVERIFIABLE, f"{per['label']}은 리포트 작성일({report_date})에 아직 끝나지 않은 기간입니다.")
    elif year >= int(report_date[:4]):
        return _result(cid, row, name, UNVERIFIABLE, f"{year}년은 리포트 작성 연도라 확정 공시가 없습니다.")
    won, mult = _won(row)
    if won is None:
        return _result(cid, row, name, UNVERIFIABLE, f"리포트 값 '{row.value}'(단위 '{row.unit}')를 금액 하나로 읽지 못했습니다.")

    corp = target
    if info["company"]:
        found = dart.corps_by_name(info["company"])
        if len(found) != 1:
            why = "상장회사 목록에 없습니다" if not found else "같은 이름의 회사가 여럿입니다"
            return _result(cid, row, name, UNVERIFIABLE, f"'{info['company']}'를 특정하지 못했습니다({why}).")
        corp = found[0]

    tries = [info["fs"]] if info["fs"] else ["CFS", "OFS"]
    got: Dict[str, Tuple[dict, dict]] = {}
    amt: Dict[str, float] = {}
    calc_note = ""
    for fs in tries:
        hit = _period_amount(dart, corp, year, fs, account, per)
        if hit:
            got[fs] = (hit[0], hit[1])
            amt[fs] = hit[2]
            calc_note = hit[3]
    if not got:
        fs_txt = "/".join(FS_NAME[f] for f in tries)
        doc = f"{per['label']} 보고서" if per else f"{year}년 사업보고서"
        return _result(cid, row, name, UNVERIFIABLE, f"{corp['corp_name']} {doc} {fs_txt} 재무제표에서 '{account}' 값을 찾지 못했습니다.")

    tol = _tolerance(row, mult)
    diffs = {fs: abs(won - amt[fs]) for fs in got}
    group = [corp["corp_code"], year, when]           # 같은 회사·같은 기간끼리 연결/별도 기준을 맞춰 본다
    if info["fs"]:
        fs, how = info["fs"], "표기"
    else:
        ok = [fs for fs, d in diffs.items() if d <= tol]
        if len(ok) >= 1:
            fs, how = ok[0], "값 일치"
        else:
            # 표기도 없고 값도 안 맞는다. 기준을 정할 근거가 있을 때만 판정하고, 없으면 검증 불가.
            rel = {f: abs(won - amt[f]) / abs(amt[f]) for f in got if amt[f]}
            near = _nearest(rel)
            fs = how = None
            if not decide:
                pass
            elif hint and hint in rel and rel[hint] <= HINT_MAX and near in (None, hint):
                fs, how = hint, "같은 표"
            elif near and not (hint and hint in rel and hint != near):
                fs, how = near, "가까운 값"
            if not fs:
                vals = ", ".join(f"{FS_NAME[f]} {_fmt(amt[f], row, mult)}" for f in got)
                acc, res = next(iter(got.values()))
                return _result(cid, row, name, UNVERIFIABLE,
                               f"리포트에 연결/별도 표기가 없고 어느 쪽과도 맞지 않아 판단하지 않았습니다 (공시: {vals}).",
                               source=_source(dart, corp, year, next(iter(got)), acc, res, report_date, per=per),
                               _group=group, _open=True)
    acc, res = got[fs]
    official = amt[fs]
    if year_how == "추정" and diffs[fs] > tol and abs(won - official) / abs(official or 1) > NEAR_MAX:
        return _result(cid, row, name, UNVERIFIABLE, f"리포트에 기준 연도가 없고, 직전 결산연도({year}년) 공시 값({_fmt(official, row, mult)})과도 크게 달라 "
                       "어느 연도의 값인지 알 수 없어 판단하지 않았습니다.")
    src = _source(dart, corp, year, fs, acc, res, report_date, per=per)
    diff_pct = round(abs(won - official) / abs(official) * 100, 3) if official else None
    base = dict(source=src, computed=_fmt(official, row, mult), diff_pct=diff_pct, timing=src["timing"],
                name=f"{corp['corp_name']} {when} {FS_NAME[fs]} {account}, 공시 대조",
                basis={"fs": FS_NAME[fs], "how": how, "year": year_how}, _group=group)
    note = {"표기": "",
            "값 일치": f" 리포트에 연결/별도 표기가 없어, 값이 맞는 {FS_NAME[fs]} 재무제표로 대조했습니다.",
            "같은 표": f" 리포트에 연결/별도 표기가 없지만, 같은 회사·같은 연도의 다른 숫자가 {FS_NAME[fs]} 재무제표와 맞아 {FS_NAME[fs]} 기준으로 판정했습니다.",
            "가까운 값": f" 리포트에 연결/별도 표기가 없지만, 값이 {FS_NAME[fs]} 쪽에 훨씬 가까워 {FS_NAME[fs]} 기준으로 판정했습니다 (공시: "
                         + ", ".join(f"{FS_NAME[f]} {_fmt(amt[f], row, mult)}" for f in got) + ")."}[how]
    if calc_note:
        note += f" 공시 값은 {calc_note}입니다."
    if year_how == "현재 표기":
        note += f" 리포트가 '현재' 값이라고 해 직전 결산연도({year}년)와 대조했습니다."
    elif year_how == "추정":
        note += f" 리포트에 기준 연도가 없어 직전 결산연도({year}년)로 보고 대조했습니다."
    if diffs[fs] <= tol:
        later = " 리포트 작성 이후에 나온 공시와 같은 값입니다." if src["timing"] == "작성 이후 공개" else ""
        return _result(cid, row, base.pop("name"), MATCH, f"공시 값과 같습니다(표시 자릿수 이내).{note}{later}", **base)
    if src["timing"] == "작성 당시 공개":
        return _result(cid, row, base.pop("name"), MISMATCH,
                       f"리포트 작성 당시 공시된 값({base['computed']})과 {diff_pct}% 다릅니다.{note}", **base)
    why = ("리포트 작성 당시에는 아직 공시 전이었습니다" if src["timing"] == "작성 이후 공개"
           else "작성 뒤 정정 공시가 나와, 작성 당시 원본 수치를 확인할 수 없습니다")
    return _result(cid, row, base.pop("name"), UNVERIFIABLE, f"지금 공시 값({base['computed']})과 다르지만 {why}.{note}", **base)


def _source(dart: Dart, corp: dict, year: int, fs: Optional[str], acc: dict, res: dict, report_date: str,
            api: str = "단일회사 전체 재무제표", per: Optional[dict] = None) -> dict:
    """출처, 기준일, 조회 시점, 원문 링크, 작성 당시 공개 여부"""
    fs_name = FS_NAME.get(fs or "", "")
    rno = acc.get("rcept_no", "")
    rdt = rcept_date(rno)
    filings = []
    try:
        filings = (dart.period_filings(corp["corp_code"], year, per["q"] * 3) if per and per["q"] < 4
                   else dart.annual_filings(corp["corp_code"], year))
    except DartError:
        pass
    this = next((f for f in filings if f.get("rcept_no") == rno), None)
    first = filings[0] if filings else None
    first_dt = rcept_date(first["rcept_no"]) if first else rdt
    if rdt and rdt <= report_date:
        timing = "작성 당시 공개"
    elif first_dt and first_dt <= report_date:
        timing = "작성 당시 공개, 이후 정정"
    else:
        timing = "작성 이후 공개"
    default_nm = (f"{'반기' if per['q'] == 2 else '분기'}보고서 ({year}.{per['q'] * 3:02d})" if per and per["q"] < 4 else f"사업보고서 ({year})")
    report_nm = (this or {}).get("report_nm") or default_nm
    received = rdt + (f" (처음 공시 {first_dt})" if first_dt and first_dt != rdt else "")
    return {
        "display": [["출처", f"OpenDART (금융감독원 전자공시), {api}"],
                    ["공시", f"{corp['corp_name']} {report_nm} (" + ", ".join(x for x in (fs_name, f"{acc.get('sj_nm', '')} {acc.get('account_nm', '')}".strip()) if x) + ")"],
                    ["접수일", received]],
        "short": f"{report_nm}, {rdt} 접수, {timing}",
        "provider": "OpenDART (금융감독원 전자공시)",
        "api": api,
        "corp_name": corp["corp_name"], "corp_code": corp["corp_code"], "stock_code": corp["stock_code"],
        "report_nm": report_nm,
        "fs": fs_name, "statement": acc.get("sj_nm", ""), "account_nm": acc.get("account_nm", ""),
        "period": acc.get("thstrm_nm", "") or f"{year}년",
        "rcept_no": rno, "rcept_dt": rdt, "first_rcept_dt": first_dt,
        "url": original_url(rno) if rno else "",
        "fetched_at": res.get("fetched_at", ""), "report_date": report_date, "timing": timing,
    }


# ---------- 배당에 관한 사항 (배당성향, 주당 배당금, 주당순이익, 배당금 총액) ----------
# (이름, 리포트에서 찾는 말, 빼는 말, 공시 '구분'에서 찾는 말, 값 종류)
DIVIDENDS = [
    ("배당성향", r"배당\s*성향", r"목표|평균|가정|업종", "현금배당성향", "pct"),
    ("주당 배당금", r"주당\s*(현금\s*)?배당금|\bDPS\b", r"목표|평균|가정", "주당현금배당금", "won"),
    ("주당순이익", r"주당\s*순이익|\bEPS\b", r"목표|평균|가정|성장|증가", "주당순이익", "won"),
    ("현금배당금 총액", r"배당금\s*총액|배당\s*총액|총\s*배당금", r"목표|평균|가정", "현금배당금총액", "won_million"),
]


def classify_dividend(row: DataRow, target_name: str) -> Optional[dict]:
    """배당 공시와 대조할 숫자면 {'kind', 'year', 'fs', 'company'} 또는 {'skip'}. 아니면 None"""
    item = row.item or ""
    spec = next((d for d in DIVIDENDS if re.search(d[1], item)), None)
    if not spec:
        return None
    if re.search(spec[2], item):
        return {"skip": f"{spec[0]}이지만 목표·평균·가정 값이라 공시와 대조하지 않음"}
    ctx = " ".join(_ISO.sub(" ", x or "") for x in (item, row.as_of, row.basis))
    if _EST.search(ctx):
        return {"skip": "추정치라 공시와 대조할 값이 없음"}
    if _QTR.search(ctx):
        return {"skip": "분기·반기 수치는 아직 대조하지 않음"}
    year = None
    for text in (item, row.as_of, row.basis):
        m = _YEAR.search(_ISO.sub(" ", text or ""))
        if m:
            g = next(x for x in m.groups() if x)
            year = int(g) if len(g) == 4 else 2000 + int(g)
            break
    head = re.split(spec[1], item)[0]
    head = re.sub(r"연결|별도|보통주|(20\d\d|'\d\d)\s*년?|FY|(^|\s)(현|현재|당기|최근)(?=\s|$)", " ", head).strip()
    company = None if not head or _norm(head) == _norm(target_name) else head
    now = not year and bool(_NOW.search(item))
    return {"kind": spec, "year": year, "now": now, "fs": "CFS" if "연결" in item else "OFS" if "별도" in item else None, "company": company}


def _div_value(text) -> Optional[float]:
    t = str(text or "").replace(",", "").strip()
    try:
        return float(t)
    except ValueError:
        return None


def check_dividend(dart: Dart, row: DataRow, info: dict, target: dict, report_date: str, cid: str) -> dict:
    name_, _, _, se_key, kind = info["kind"]
    who = info["company"] or target["corp_name"]
    name = f"{who} {str(info['year']) + '년' if info['year'] else '최근'} {name_}, 공시 대조"
    corp = target
    if info["company"]:
        found = dart.corps_by_name(info["company"])
        if len(found) != 1:
            return _result(cid, row, name, UNVERIFIABLE, f"'{info['company']}'를 특정하지 못했습니다.")
        corp = found[0]
    stated = parse_number(row.value, row.unit)
    if stated is None:
        return _result(cid, row, name, UNVERIFIABLE, f"리포트 값 '{row.value}'를 숫자 하나로 읽지 못했습니다.")
    mult = 1.0
    if kind == "won_million":                       # 공시는 백만원 단위. 리포트 값을 원으로 읽어 백만원으로 맞춘다
        won, m_ = _won(row)
        if won is None:
            return _result(cid, row, name, UNVERIFIABLE, f"리포트 값 '{row.value}'(단위 '{row.unit}')를 금액 하나로 읽지 못했습니다.")
        stated, mult = won / 1e6, _tolerance(row, m_ or 1.0) / 1e6      # 허용 차이: 리포트 표시 자릿수 1단위 (139억 원이면 1억)
    report_year = int(report_date[:4])
    if info["year"] and info["year"] >= report_year:
        return _result(cid, row, name, UNVERIFIABLE, f"{info['year']}년은 리포트 작성 연도라 확정 공시가 없습니다.")
    # 연도를 밝혔으면 그 해 보고서, 아니면 가장 최근 보고서의 당기·전기·전전기 가운데 맞는 해를 찾는다
    base_year = info["year"] or report_year - 1
    if info.get("now"):                                   # '현 배당성향': 직전 결산연도 값이라고 밝힌 것
        info = dict(info, year=base_year)
    res = dart.dividends(corp["corp_code"], base_year)
    rows = [x for x in res.get("list", []) if se_key in x.get("se", "").replace(" ", "")]
    rows = [x for x in rows if x.get("stock_knd", "-") in ("-", "보통주", "")]
    if info["fs"] and any(x["se"].startswith(("(연결)", "(별도)")) for x in rows):
        rows = [x for x in rows if x["se"].startswith("(연결)" if info["fs"] == "CFS" else "(별도)")]
    if not rows:
        return _result(cid, row, name, UNVERIFIABLE, f"{corp['corp_name']} {base_year}년 사업보고서의 배당 공시에서 '{name_}' 항목을 찾지 못했습니다.")
    m = re.search(r"\.(\d+)", re.sub(r"[\s,]", "", row.value))
    dec = len(m.group(1)) if m else 0
    tol = mult if kind == "won_million" else 10 ** -dec
    unit = {"pct": "%", "won": "원", "won_million": "백만원"}[kind]

    def fmt(v: float) -> str:
        return f"{v:,.{max(dec, 2) if kind == 'pct' else 0}f}{unit}"

    cols = [("thstrm", base_year)] if info["year"] else [("thstrm", base_year), ("frmtrm", base_year - 1), ("lwfr", base_year - 2)]
    cands = [(x, y, _div_value(x.get(col))) for x in rows for col, y in cols]
    cands = [c for c in cands if c[2] is not None]
    if not cands:
        return _result(cid, row, name, UNVERIFIABLE, f"{corp['corp_name']} 배당 공시에 '{name_}' 값이 비어 있습니다.")
    x, y, official = min(cands, key=lambda c: abs(c[2] - stated))
    fs = "CFS" if x["se"].startswith("(연결)") else "OFS" if x["se"].startswith("(별도)") else None
    acc = {"rcept_no": x.get("rcept_no", ""), "sj_nm": "배당에 관한 사항", "account_nm": x["se"], "thstrm_nm": f"{y}년"}
    src = _source(dart, corp, base_year, fs, acc, res, report_date, api="배당에 관한 사항")
    diff = abs(official - stated)
    diff_pct = round(diff / abs(official) * 100, 3) if official else None
    base = dict(source=src, computed=fmt(official), diff_pct=diff_pct, timing=src["timing"])
    name = f"{corp['corp_name']} {y}년 {name_}, 공시 대조"
    which = (f" 리포트가 '현재' 값이라고 해 직전 결산연도({y}년)와 대조했습니다." if info.get("now")
             else "" if info["year"] else f" 리포트에 연도가 없어 최근 3개 연도 가운데 값이 맞는 {y}년과 대조했습니다.")
    if diff <= tol:
        return _result(cid, row, name, MATCH, f"공시 값({x['se']})과 같습니다.{which}", **base)
    if not info["year"]:
        return _result(cid, row, name, UNVERIFIABLE,
                       f"리포트에 연도가 없고 최근 3개 연도 공시 어느 값과도 맞지 않아 판단하지 않았습니다 (가장 가까운 값 {y}년 {fmt(official)}).", **base)
    if src["timing"] == "작성 당시 공개":
        return _result(cid, row, name, MISMATCH, f"리포트 작성 당시 공시된 값({x['se']} {fmt(official)})과 {diff_pct}% 다릅니다.", **base)
    return _result(cid, row, name, UNVERIFIABLE, f"지금 공시 값({fmt(official)})과 다르지만, 리포트 작성 당시 공개된 값인지 확인할 수 없습니다.", **base)


# ---------- 리포트 전체 ----------
def run(report: Report, dart: Optional[Dart] = None) -> dict:
    """돌려주는 값: {'enabled', 'provider', 'results': [...], 'skipped': [...], 'error'}"""
    dart = dart or Dart()
    out = {"enabled": True, "provider": "OpenDART", "results": [], "skipped": [], "error": None}
    report_date = report.meta.report_date or report.meta.snapshot_date
    try:
        target = dart.corp_by_stock(report.meta.ticker)
        if not target:
            out["error"] = f"종목코드 {report.meta.ticker} 를 DART 상장회사 목록에서 찾지 못했습니다."
            return out
        out["target"] = target
        n = 0
        todo = []
        for row in report.data_rows:
            div = classify_dividend(row, report.meta.name)
            if div is not None:
                if "skip" in div:
                    out["skipped"].append({"rid": row.rid, "item": row.item, "why": div["skip"]})
                    continue
                n += 1
                todo.append((row, None, f"D-{n}"))
                out["results"].append(check_dividend(dart, row, div, target, report_date, f"D-{n}"))
                continue
            info = classify(row, report.meta.name)
            if "skip" in info:
                if info["skip"] != "재무제표 계정이 아님":
                    out["skipped"].append({"rid": row.rid, "item": row.item, "why": info["skip"]})
                continue
            n += 1
            todo.append((row, info, f"D-{n}"))
            out["results"].append(check_row(dart, row, info, target, report_date, f"D-{n}"))
        # 표기가 없는데 값도 안 맞은 숫자: 같은 회사·같은 연도에서 '값이 맞아' 기준이 드러난 숫자가 있으면 그 기준으로 다시 본다.
        votes: Dict[tuple, Dict[str, int]] = {}
        for r in out["results"]:
            if (r.get("basis") or {}).get("how") == "값 일치":
                fs = next(k for k, v in FS_NAME.items() if v == r["basis"]["fs"])
                votes.setdefault(tuple(r["_group"]), {}).setdefault(fs, 0)
                votes[tuple(r["_group"])][fs] += 1
        for i, r in enumerate(out["results"]):
            if not r.get("_open"):
                continue
            top = sorted(votes.get(tuple(r["_group"]), {}).items(), key=lambda x: -x[1])
            hint = top[0][0] if top and (len(top) == 1 or top[0][1] > top[1][1]) else None   # 한쪽이 분명히 많을 때만
            row, info, cid = todo[i]
            out["results"][i] = check_row(dart, row, info, target, report_date, cid, hint=hint, decide=True)
    except DartError as e:
        out["error"] = str(e)
    except RuntimeError as e:          # 키 없음 (llm.get_key)
        out.update(enabled=False, error=str(e))
    for r in out["results"]:           # 안에서만 쓰는 표시는 결과에 남기지 않는다
        r.pop("_group", None)
        r.pop("_open", None)
    out["calls"] = dart.calls
    return out


def maybe_run(report: Report, cfg: dict) -> Optional[dict]:
    """config.yaml 의 external.dart 가 켜져 있을 때만. 키가 없으면 대조 없이 넘어간다."""
    if not (cfg.get("external") or {}).get("dart", False):
        return None
    return run(report)


def main(argv) -> int:
    from .__main__ import main as run_all
    return run_all(list(argv) + ["--only", "dart"])
