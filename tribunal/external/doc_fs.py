"""사업보고서 원문의 재무제표 표 읽기 (2026-11-24, 사용자).

금융회사는 DART 재무 API 숫자가 2023년분부터라, 2016~2022년은 사업보고서·반기·분기보고서 원문의
'III. 재무에 관한 사항' 안 재무제표 표(연결: '연결재무제표', 별도: '재무제표')를 읽는다.
- 재무상태표: 그 기간 말 칸. 손익: 사업보고서는 1년, 분기·반기는 '3개월' 칸(없으면 누적 차이, 계산 표시).
  4분기 손익 = 연간 − 3분기 누적(계산 표시). 현금흐름: 연초부터 누적.
- 안전장치: 같은 방식으로 DART 재무 API 가 있는 첫 해(보통 2023)의 원문을 읽어 API 값과 0.5% 안에서 맞아야만 옛 해를 채운다.
- 단위를 못 찾은 표, 자산 ≠ 부채 + 자본인 기간, 이웃 기간과 자산이 3배 넘게 다른 기간(단위 오독)은 쓰지 않는다.
숫자는 표에 적힌 값 그대로다. 없는 값은 None.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Dict, List, Optional, Tuple

from .dart import Dart, DartError, original_url
from .dart_pool import _CELL, _TR, _plain

QEND = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}
_TITLE = re.compile(r"<TITLE[^>]*>(.*?)</TITLE>", re.S)
_SECTION = {"연결": re.compile(r"^\s*\d+\s*\.\s*연\s*결\s*재\s*무\s*제\s*표\s*$"),
            "별도": re.compile(r"^\s*\d+\s*\.\s*(별\s*도\s*)?재\s*무\s*제\s*표\s*$")}
_ROWSPAN = re.compile(r"ROWSPAN=\"?(\d+)", re.I)
_COLSPAN = re.compile(r"COLSPAN=\"?(\d+)", re.I)
_AMT = re.compile(r"[△▲\-]?\(?\d[\d,]*(\.\d+)?\)?")
_UNIT = {"원": 1.0, "천원": 1e3, "백만원": 1e6, "억원": 1e8}
_UNIT_RX = re.compile(r"단\s*위\s*[:：]?\s*(백\s*만\s*원|천\s*원|억\s*원|원)")


# ---------- 표 → 칸 ----------
def grid(table: str) -> List[List[str]]:
    """행·열 합치기(ROWSPAN·COLSPAN)를 풀어 칸 위치를 맞춘 표"""
    out: List[List[str]] = []
    carry: Dict[int, list] = {}                        # 열 -> [글, 남은 줄 수] (위에서 내려온 합친 칸)
    for tr in _TR.findall(table):
        row: Dict[int, str] = {}
        c = 0

        def take():
            nonlocal c
            while c in carry:
                row[c] = carry[c][0]
                carry[c][1] -= 1
                if carry[c][1] <= 0:
                    del carry[c]
                c += 1
        for _, attrs, body in _CELL.findall(tr):
            take()
            cs = min(int((_COLSPAN.search(attrs) or [0, 1])[1]), 20)
            rs = min(int((_ROWSPAN.search(attrs) or [0, 1])[1]), 200)
            t = _plain(body).replace("&cr;", " ")
            for k in range(max(cs, 1)):
                row[c + k] = t
                if rs > 1:
                    carry[c + k] = [t, rs - 1]
            c += max(cs, 1)
        take()
        for k in sorted(list(carry)):
            if k not in row:
                row[k] = carry[k][0]
                carry[k][1] -= 1
                if carry[k][1] <= 0:
                    del carry[k]
        if row:
            out.append([row.get(i, "") for i in range(max(row) + 1)])
    return out


def amt(t: str) -> Optional[float]:
    """'1,234' -> 1234, '(1,234)'·'△1,234'·'-1,234' -> -1234, '-'·빈칸 -> None"""
    t = (t or "").replace(" ", "").replace("\xa0", "").replace("　", "")
    t = re.sub(r"[=_]{2,}|-{2,}", "", t)                 # 합계 밑줄 '=====' (삼성생명 옛 공시)
    if not t or not _AMT.fullmatch(t):
        return None
    neg = t[0] in "△▲-(" and len(t) > 1
    try:
        v = float(re.sub(r"[△▲\-(),]", "", t))
    except ValueError:
        return None
    return -v if neg else v


def _is_data_row(r: List[str]) -> bool:
    return bool(r and r[0].strip()) and any(amt(x) is not None and ("," in x or not re.fullmatch(r"(19|20)\d\d", x.strip())) for x in r[1:])


def norm(t: str) -> str:
    """계정 이름 맞추기용: 번호·공백·'(주석 …)'·'(손실)'·'연결' 빼고, 분기·반기 순이익은 당기 순이익으로"""
    t = re.sub(r"\s|&cr;|　", "", t or "")
    t = re.sub(r"\(?(대손|비상위험)준비금.*$", "", t)                # '당기순이익(대손준비금 반영후 조정이익 당기: …)' 설명 (은행·보험·증권)
    t = re.sub(r"당\((분|반)\)기|(분|반)\(당\)기", "당기", t)       # '당(반)기순이익'(삼성카드), '반(당)기순이익'(KB 2016)
    t = re.sub(r"^[\[(]?([IVXLlⅠ-Ⅻ]+|\d+|[가-하])[.)\]]", "", t)       # 'Vl.'(로마 숫자를 소문자 l 로 쓴 옛 공시)
    t = re.sub(r"\(주\s*석?[^)]*\)|\(주\d[^)]*\)|\(\*\d*\)|\*\d*$", "", t)
    t = re.sub(r"\((순)?(손실|이익|손익)\)|\(감소\)|\(증가\)", "", t)
    t = t.replace("연결", "")
    t = re.sub(r"당?(분기|반기)(?=순|총포괄|연결순)", "당기", t)
    return t.strip("[]():.,·")


def loose(t: str) -> str:
    """같은 계정인지 볼 때 쓰는 느슨한 이름: norm 에 더해 '_', '-', '·', 괄호와 '당기·분기·반기·3분기' 같은 기간 말을 뺀다
    ('총분기포괄손익' = '(분기)총포괄손익' = '총포괄손익', '법인세비용차감전 반기순이익' = '법인세차감전순이익')"""
    t = norm(t).replace("법인세비용차감전", "법인세차감전")
    t = re.sub(r"[_\-·ㆍ()\[\]<>'\"]", "", t)
    return re.sub(r"당?(\d분기|분기|반기|당기)", "", t)


def lkeys(names: List[str]) -> List[str]:
    """표 한 개의 줄 이름들 -> 줄마다 맞춤 열쇠. 같은 이름이 또 나오면 '이름#2' (위아래 다른 줄을 섞지 않는다)"""
    seen: Dict[str, int] = {}
    out = []
    for n in names:
        k = loose(n)
        seen[k] = seen.get(k, 0) + 1
        out.append(k if seen[k] == 1 else f"{k}#{seen[k]}")
    return out


def show(t: str) -> str:
    """화면에 보일 계정 이름: 번호와 '(주석 …)'만 뺀다"""
    t = re.sub(r"\s+", " ", (t or "").replace("&cr;", " ")).strip()
    t = re.sub(r"^[\[(]?([IVXLlⅠ-Ⅻ]+|\d+|[가-하])[.)\]]\s*", "", t)
    t = re.sub(r"\s*\(주\s*석?[^)]*\)|\s*\(\*\d*\)", "", t)
    t = re.sub(r"\s*\(\s*(대손|비상위험)\s*준비금.*$", "", t)
    return t.strip()


def _columns(rows: List[List[str]]) -> List[dict]:
    """머리 줄에서 기간 칸 묶음: [{'label', 'kind': '3m'|'cum'|'plain', 'cols': [열]}] 왼쪽부터 (첫 묶음 = 이번 기간)"""
    h = next((i for i, r in enumerate(rows) if _is_data_row(r)), 0)
    head = rows[:h]
    if not head:
        return []
    n = max(len(r) for r in rows)
    groups: List[dict] = []
    for j in range(1, n):
        path = [r[j] if j < len(r) else "" for r in head]
        if not any(p.strip() for p in path) or any(re.search(r"주\s*석|비\s*고|^\s*과\s*목\s*$", p) for p in path):
            continue
        top = re.sub(r"\s", "", path[0]) or re.sub(r"\s", "", " ".join(path))
        rest = " ".join(path)
        kind = "3m" if re.search(r"3\s*개\s*월", rest) else "cum" if re.search(r"누\s*[적계]|1\s*월\s*1\s*일\s*부\s*터|연\s*초", rest) else "plain"
        g = next((g for g in groups if g["label"] == top and g["kind"] == kind and g["cols"][-1] == j - 1), None)
        if g:
            g["cols"].append(j)
        else:
            groups.append({"label": top, "kind": kind, "cols": [j]})
    return groups


def _first_period(groups: List[dict]) -> Dict[str, List[int]]:
    """이번 기간의 칸들: {'3m': [...], 'cum': [...], 'plain': [...]}. 첫 묶음과 같은 머리 이름을 가진 바로 뒤 묶음까지"""
    if not groups:
        return {}
    out: Dict[str, List[int]] = {}
    top = groups[0]["label"]
    for g in groups:
        if g["label"] != top or g["kind"] in out:
            break
        out[g["kind"]] = g["cols"]
    if set(out) == {"plain"} or "plain" not in out:
        return out
    if "3m" in out and "cum" not in out:                  # '3개월' 옆의 이름 없는 칸 = 누적 (삼성카드 '누계')
        out["cum"] = out["plain"]
    return {k: v for k, v in out.items() if k != "plain"}


# ---------- 문서 → 재무제표 ----------
_NOTE = re.compile(r"^\(?(대손|비상위험)준비금|^(당|전)(분|반)?기:|^(당|전)\d?분기:")
def _kind(title: str, rows: List[List[str]]) -> Optional[str]:
    """표 종류: 표 바로 위에 적힌 재무제표 이름 중 가장 가까운(마지막) 것. 이름이 없으면 계정으로"""
    labels = " ".join(norm(r[0]) for r in rows if r)
    head = " ".join(" ".join(r) for r in rows[:3])
    if re.search(r"자\s*본\s*금", head) and re.search(r"이\s*익\s*잉\s*여\s*금|총\s*계", head):
        return None                                        # 자본변동표 (열이 자본 항목)
    t = re.sub(r"\s", "", title)
    hits = [(m.end(), k) for k, rx in (("SCE", r"자본변동표"), ("CF", r"현금흐름표"), ("BS", r"재무상태표|대차대조표"), ("IS", r"손익계산서"))
            for m in re.finditer(rx, t)]
    if hits:
        k = max(hits)[1]
        return None if k == "SCE" else k
    if "자산총계" in labels:
        return "BS"
    if "영업활동" in labels:
        return "CF"
    if re.search(r"당기순(이익|손익)", labels):
        return "IS"
    return None


def read(text: str, basis: str) -> Dict[str, Dict[str, List[Tuple[str, float]]]]:
    """원문 한 개 -> {'BS'|'IS'|'CF': {'3m'|'cum'|'plain': [(표에 적힌 계정 이름, 원)]}} (이번 기간 칸만).
    basis: '연결'(연결재무제표) 또는 '별도'(재무제표). 못 읽으면 {}"""
    titles = [(m.start(), _plain(m.group(1))) for m in _TITLE.finditer(text)]
    start = next((p for p, t in titles if _SECTION[basis].match(t)), None)
    if start is None:
        return {}
    # 다음 큰 제목('5.', 'IV.')까지. 요즘 원문은 '2-1. 연결 재무상태표', '가. 재무상태표'처럼 표마다 작은 제목이 있어 그건 건너뛴다
    end = next((p for p, t in titles if p > start and re.match(r"^\s*(\d+|[IVXⅠ-Ⅻ]+)\s*\.", t)), len(text))
    sec = text[start:end]
    out: Dict[str, Dict[str, List[Tuple[str, float]]]] = {}
    last = 0
    for m in re.finditer(r"<TABLE.*?</TABLE>", sec, re.S):
        rows = grid(m.group(0))
        if not any(_is_data_row(r) for r in rows):
            continue                                       # 기간·단위만 적은 머리 표는 다음 표의 앞 글
        gap = _plain(sec[last:m.start()])
        last = m.end()
        kind = _kind(gap[-300:], rows)
        if not kind:
            continue
        u = _UNIT_RX.findall(gap)
        unit = _UNIT.get(re.sub(r"\s", "", u[-1])) if u else None
        if not unit:
            continue                                       # 단위를 모르면 쓰지 않는다
        cols = _first_period(_columns(rows))
        if not cols:
            continue
        if kind == "IS" and set(cols) == {"plain"}:
            # 칸이 하나뿐인 손익: 표 위 기간 글의 첫 시작일로 누적인지 3개월인지 정한다 ('2020년 1월 1일부터' = 누적, '4월 1일부터' = 3개월)
            g = re.sub(r"\s", "", gap)
            at = max((g.rfind(w) for w in ("손익계산서", "포괄손익")), default=-1)
            sm = re.search(r"(?:20\d\d)(?:년|\.)(\d{1,2})(?:월|\.)\d{1,2}일?부터", g[at + 1:] if at >= 0 else g)
            if sm and int(sm.group(1)) == 1:
                cols = {"cum": cols["plain"]}
            elif sm and int(sm.group(1)) in (4, 7, 10):
                cols = {"3m": cols["plain"]}
        got = out.setdefault(kind, {})
        if kind != "IS" and got:
            continue                                       # 같은 종류 두 번째 표(전기 표 따로 등)는 쓰지 않는다
        for k, cs in cols.items():
            lst = got.setdefault(k, [])
            empty = None                                   # 값 없이 이름만 있는 바로 앞 줄
            for r in rows:
                v = next((amt(r[c]) for c in cs if c < len(r) and amt(r[c]) is not None), None)
                note = bool(_NOTE.match(re.sub(r"\s", "", r[0])))
                if note:
                    # '당기순이익' 줄 아래 '(비상위험준비금 반영후 …', '당 반 기: …원' 설명 줄에 값 칸이 합쳐져 있는 표(현대해상):
                    # 값은 위의 이름 줄 것. 설명 줄 자체는 계정이 아니다
                    if empty is not None and v is not None:
                        lst.append((show(empty), v * unit))
                    empty = None
                    continue
                if v is not None and r[0].strip():
                    lst.append((show(r[0]), v * unit))
                    empty = None
                elif r[0].strip():
                    empty = r[0]
    return out


# ---------- 계정 이름 -> 핵심 숫자 ----------
_NI = re.compile(r"(당기)?총?(당기)?순(이익|손익)")
_PRETAX = re.compile(r"법인세(비용)?(차감|공제)전(당기)?순?(이익|손익)|법인세비용차감전계속영업(이익|손익)")


def _owner(t: str) -> bool:
    return ("지배주주" in t or "지배기업" in t or "지배회사" in t) and not t.startswith("비지배") and not re.search(r"포괄|주당|희석", t)


def pick(sts: Dict[str, List[Tuple[str, float]]]) -> dict:
    """{'BS': [...], 'IS': [...], 'CF': [...]} (한 기간 칸) -> 핵심 숫자 {assets, liab, ..., ni, ni_owner, ...}"""
    out: dict = {}
    bs = [(norm(a), v) for a, v in sts.get("BS", [])]
    is_ = [(norm(a), v) for a, v in sts.get("IS", [])]
    cf = [(norm(a), v) for a, v in sts.get("CF", [])]

    def first(rows, pred):
        return next((v for a, v in rows if pred(a)), None)
    out["assets"] = first(bs, lambda a: a == "자산총계")
    out["liab"] = first(bs, lambda a: a == "부채총계")
    out["equity"] = first(bs, lambda a: a == "자본총계")
    out["cur_assets"] = first(bs, lambda a: a == "유동자산")
    out["cur_liab"] = first(bs, lambda a: a == "유동부채")
    out["inventory"] = first(bs, lambda a: a == "재고자산")
    eq_i = next((i for i, (a, _) in enumerate(bs) if a == "자본총계"), len(bs))
    liab_i = next((i for i, (a, _) in enumerate(bs) if a == "부채총계"), 0)
    out["equity_owner"] = next((v for i, (a, v) in enumerate(bs) if liab_i < i < eq_i and _owner(a) and not re.search(r"이익|손익|손실", a)), None)
    nci = next((v for i, (a, v) in enumerate(bs) if liab_i < i <= eq_i and a.startswith("비지배") and not re.search(r"이익|손익|손실|포괄", a)), None)
    if out["equity_owner"] is None and nci is not None and out["equity"] is not None:
        out["equity_owner"] = out["equity"] - nci              # 지배 몫 줄 없이 비지배지분만 적은 회사: 자본총계 − 비지배지분 (계산 값 표시)
        out["_derived"] = ["equity_owner"]
    ni_i = next((i for i, (a, _) in enumerate(is_) if _NI.fullmatch(a)), None)
    out["ni"] = is_[ni_i][1] if ni_i is not None else None
    out["ni_owner"] = next((v for i, (a, v) in enumerate(is_) if ni_i is not None and i > ni_i and _owner(a)), None)
    out["revenue"] = first(is_, lambda a: a in ("매출액", "영업수익", "수익(매출액)", "매출", "매출액(영업수익)"))
    out["op"] = first(is_, lambda a: a == "영업이익")
    out["pretax"] = first(is_, lambda a: bool(_PRETAX.fullmatch(a)))
    out["cogs"] = first(is_, lambda a: a == "매출원가")
    out["gross"] = first(is_, lambda a: a == "매출총이익")
    out["sga"] = first(is_, lambda a: a == "판매비와관리비")
    out["ins_profit"] = first(is_, lambda a: a in ("보험서비스결과", "보험손익"))
    out["inv_profit"] = first(is_, lambda a: a == "투자손익")
    out["div_rev"] = first(is_, lambda a: a in ("배당금수익", "배당수익"))
    out["cfo"] = first(cf, lambda a: bool(re.fullmatch(r"영업활동(으로인한|으로부터의|에서창출된|으로인한순)?(순)?현금흐름", a)))
    out["cfi"] = first(cf, lambda a: bool(re.fullmatch(r"투자활동(으로인한|으로부터의|으로인한순)?(순)?현금흐름", a)))
    out["cff"] = first(cf, lambda a: bool(re.fullmatch(r"재무활동(으로인한|으로부터의|으로인한순)?(순)?현금흐름", a)))
    out["capex"] = first(cf, lambda a: a in ("유형자산의취득", "유형자산취득"))
    return {k: v for k, v in out.items() if v is not None}


# ---------- 한 기간 원문 ----------
def _filings(dart: Dart, corp_code: str, y: int, q: int) -> List[dict]:
    fl = dart.annual_filings(corp_code, y) if q == 4 else dart.period_filings(corp_code, y, q * 3)
    return list(reversed(fl or []))                       # 최신 정정본부터


def period_doc(dart: Dart, corp_code: str, y: int, q: int, basis: str) -> Optional[dict]:
    """그 기간 보고서 원문의 재무제표. {'rcept_no', 'BS', 'IS', 'CF'} (각각 {'3m'|'cum'|'plain': [...]}) 또는 None"""
    for f in _filings(dart, corp_code, y, q):
        try:
            text = dart.document_text(f["rcept_no"])
        except DartError:
            continue
        got = read(text, basis)
        if got.get("BS") and got.get("IS"):               # 일부만 고친 정정 공시에는 표가 없다 → 그 앞 공시
            return {"rcept_no": f["rcept_no"], **got}
    return None


def _one(d: dict, sj: str, *kinds: str) -> List[Tuple[str, float]]:
    part = d.get(sj) or {}
    return next((part[k] for k in kinds if part.get(k)), [])


# ---------- 안전장치 + 옛 해 채우기 ----------
CHECK_KEYS = ("assets", "liab", "equity", "ni")


def check(dart: Dart, corp_code: str, st: dict, basis: str, year: int) -> dict:
    """year(재무 API 첫 해) 원문을 같은 방식으로 읽어 재무 API 값과 비교. {'year', 'ok', 'pairs': [[기간, 항목, 원문, API]], 'why'}"""
    ann = next((a for a in st.get("annual", []) if a["period"] == str(year)), None)
    qs = {p["period"]: p for p in st.get("quarter", [])}
    pairs, why = [], ""
    docs = {q: period_doc(dart, corp_code, year, q, basis) for q in (1, 2, 3, 4)}
    if not ann or not docs[4]:
        return {"year": year, "ok": None, "pairs": [], "why": "비교할 사업보고서 원문 표를 찾지 못함"}
    a = pick({sj: _one(docs[4], sj, "plain", "cum") for sj in ("BS", "IS", "CF")})
    pairs += [[str(year), k, a.get(k), ann.get(k)] for k in CHECK_KEYS if ann.get(k) is not None]
    prev_cum = None
    for q in (1, 2, 3):
        d, p = docs[q], qs.get(f"{year}.{q * 3:02d}")
        if not d or not p:
            prev_cum = None
            continue
        bs = pick({"BS": _one(d, "BS", "plain", "cum")})
        pairs += [[p["period"], k, bs.get(k), p.get(k)] for k in ("assets", "liab", "equity") if p.get(k) is not None]
        three = pick({"IS": _one(d, "IS", "3m")}).get("ni")
        cum = pick({"IS": _one(d, "IS", "cum", "plain")}).get("ni")
        mine = three if three is not None else (cum if q == 1 else (cum - prev_cum if cum is not None and prev_cum is not None else None))
        if p.get("ni") is not None and mine is not None:
            pairs.append([p["period"], "ni", mine, p["ni"]])
        prev_cum = cum
    bad = [x for x in pairs if x[2] is None or abs(x[2] - x[3]) > abs(x[3]) * 0.005]
    if bad:
        why = ", ".join(f"{x[0]} {x[1]}" for x in bad[:4]) + " 다름"
    ok = bool(pairs) and not bad and sum(1 for x in pairs if x[0] == str(year)) >= 3
    return {"year": year, "ok": ok, "pairs": pairs, "why": why or ("" if ok else "비교할 값이 모자람")}


def _bs_ok(v: dict) -> bool:
    a, l, e = v.get("assets"), v.get("liab"), v.get("equity")
    return None in (a, l, e) or abs(a - l - e) <= abs(a) * 0.005


def fill_old(dart: Dart, corp_code: str, st: dict, basis: Optional[str] = None, years: int = 10, today: Optional[date] = None) -> None:
    """재무 API 가 없는 옛 해(today.year - years 부터)의 연간·분기 재무를 원문 재무제표 표로 채운다.
    안전장치(check)가 맞을 때만 채운다. 채운 기간은 'doc': True. 결과 요약은 st['doc_check']"""
    today = today or date.today()
    basis = basis or st.get("fs") or "연결"
    have = sorted(int(a["period"]) for a in st.get("annual", []))
    if not have:
        return
    first = have[0]
    lo = today.year - years
    if first <= lo:
        return
    res = check(dart, corp_code, st, basis, first)
    st["doc_check"] = res
    if not res["ok"]:
        return
    docs: Dict[tuple, dict] = {}
    api_q = {p["period"] for p in st.get("quarter", [])}
    for y in range(lo, first + 1):
        for q in (1, 2, 3, 4):
            if y == first and (q == 4 or f"{y}.{q * 3:02d}" in api_q):
                continue                                   # 재무 API 첫 해: API 에 없는 분기(금융회사 2023년 1·2분기)만
            d = period_doc(dart, corp_code, y, q, basis)
            if d:
                docs[(y, q)] = d
    annual, quarter = _periods(docs)
    _sanity(annual, quarter)
    st["annual"] = sorted(annual + st.get("annual", []), key=lambda p: p["period"])
    st["quarter"] = sorted(quarter + st.get("quarter", []), key=lambda p: p["period"])
    _tables(st, docs, annual, quarter)
    st["sources"] = sorted([{"period": f"{y}.{q * 3:02d}", "rcept_no": d["rcept_no"], "url": original_url(d["rcept_no"]), "doc": True}
                            for (y, q), d in docs.items()] + st.get("sources", []), key=lambda s: s["period"])
    res["filled"] = sorted({p["period"] for p in annual})


def _quarter_is(docs: Dict[tuple, dict], y: int, q: int) -> Tuple[List[Tuple[str, float]], bool, List[Tuple[str, float]]]:
    """그 분기 손익 3개월 값 [(이름, 원)], 계산 값인가, 그 분기 누적 [(이름, 원)]"""
    d = docs.get((y, q))
    if not d:
        return [], False, []
    cum = _one(d, "IS", "cum", "plain") if q < 4 else _one(d, "IS", "plain", "cum")
    if q == 1:
        return _one(d, "IS", "3m") or cum, False, cum
    three = _one(d, "IS", "3m") if q < 4 else []
    if three:
        return three, False, cum
    prev = docs.get((y, q - 1))
    if not prev:
        return [], True, cum
    pc = _one(prev, "IS", "cum", "plain")
    return _minus(cum, pc), True, cum


def _minus(a: List[Tuple[str, float]], b: List[Tuple[str, float]]) -> List[Tuple[str, float]]:
    """이름이 같은 줄끼리 a − b (이름이 겹치면 순서대로 짝). 짝이 없는 줄은 뺀다"""
    pool: Dict[str, List[float]] = {}
    for n, v in b:
        pool.setdefault(norm(n), []).append(v)
    out = []
    for n, v in a:
        lst = pool.get(norm(n))
        if lst:
            out.append((n, v - lst.pop(0)))
    return out


def _periods(docs: Dict[tuple, dict]) -> Tuple[List[dict], List[dict]]:
    annual, quarter = [], []
    for (y, q), d in sorted(docs.items()):
        bs = _one(d, "BS", "plain", "cum")
        cf = _one(d, "CF", "plain", "cum")
        if q == 4:
            v = pick({"BS": bs, "IS": _one(d, "IS", "plain", "cum"), "CF": cf})
            annual.append({"period": f"{y}", "end": f"{y}-12-31", "rcept_no": d["rcept_no"], "derived": v.pop("_derived", []), "doc": True, **v})
        three, derived, _ = _quarter_is(docs, y, q)
        if q == 4:
            c3 = docs.get((y, 3))
            three = _minus(_one(d, "IS", "plain", "cum"), _one(c3, "IS", "cum", "plain")) if c3 else []
            derived = True
        v = pick({"BS": bs, "IS": three, "CF": cf})
        isk = [k for k in v if k in IS_KEYS]
        dv = v.pop("_derived", [])
        p = {"period": f"{y}.{q * 3:02d}", "end": f"{y}-{QEND[q]}", "rcept_no": d["rcept_no"], "derived": (isk if derived else []) + dv, "doc": True, **v}
        quarter.append(p)
    return annual, quarter


IS_KEYS = ("revenue", "op", "pretax", "ni", "ni_owner", "cogs", "gross", "sga", "ins_profit", "inv_profit", "div_rev")
BS_KEYS = ("assets", "liab", "equity", "equity_owner", "cur_assets", "cur_liab", "inventory")


def _sanity(annual: List[dict], quarter: List[dict]) -> None:
    """자산 ≠ 부채 + 자본이면 그 기간 재무상태 값을 비운다. 이웃 기간과 자산이 3배 넘게 다르면(단위 오독) 그 기간 전부 비운다"""
    for lst in (annual, quarter):
        for p in lst:
            if not _bs_ok(p):
                for k in BS_KEYS:
                    p.pop(k, None)
                p.setdefault("missing", []).append("bs")
        seq = [p for p in lst if p.get("assets")]
        for i, p in enumerate(seq):
            nb = [x["assets"] for x in seq[max(0, i - 1): i + 2] if x is not p]
            if nb and all(not (1 / 3 <= p["assets"] / x <= 3) for x in nb):
                for k in list(p):
                    if k in IS_KEYS or k in BS_KEYS or k in ("cfo", "cfi", "cff", "capex"):
                        p.pop(k)
                p.setdefault("missing", []).append("unit")


def _tables(st: dict, docs: Dict[tuple, dict], annual: List[dict], quarter: List[dict]) -> None:
    """화면 '재무제표' 표에 옛 기간 칸을 넣는다. 재무 API 계정과 이름이 같으면 같은 줄, 다르면 원문 이름으로 새 줄"""
    tables = st.setdefault("tables", {})
    keymap = {"IS": IS_KEYS, "BS": BS_KEYS, "CF": ("cfo", "cfi", "cff", "capex")}
    bad = {p["period"] for p in annual + quarter if "unit" in p.get("missing", [])}
    bad_bs = {p["period"] for p in annual + quarter if "bs" in p.get("missing", [])}
    for sj in ("IS", "BS", "CF"):
        t = tables.setdefault(sj, {"accounts": [], "annual": {}, "quarter": {}, "missing": []})
        byk: Dict[str, dict] = {}
        for a in t["accounts"]:
            byk.setdefault(a.get("lk") or loose(a["name"]), a)
        keyacc = {a["key"]: f"{a['id']}|{a['name']}" for a in t["accounts"] if a.get("key")}

        def place(rows: List[Tuple[str, float]]) -> Dict[str, float]:
            out: Dict[str, float] = {}
            for (n, v), lk in zip(rows, lkeys([n for n, _ in rows])):
                a = byk.get(lk)
                if a is None:                             # 재무 API 에 없는 이름(옛 계정): 원문 이름으로 새 줄
                    a = {"id": "doc", "name": n, "key": None, "lk": lk}
                    byk[lk] = a
                    t["accounts"].append(a)
                out.setdefault(f"{a['id']}|{a['name']}", v)
            return out
        for (y, q), d in sorted(docs.items()):
            if q == 4:
                per = f"{y}"
                if per not in bad and not (sj == "BS" and per in bad_bs):
                    rows = _one(d, sj, "plain", "cum")
                    col = place(rows)
                    ap = next((p for p in annual if p["period"] == per), {})
                    for k in keymap[sj]:
                        if k in keyacc and ap.get(k) is not None:
                            col[keyacc[k]] = ap[k]
                    t["annual"][per] = col
            per = f"{y}.{q * 3:02d}"
            if per in bad or (sj == "BS" and per in bad_bs):
                continue
            if sj == "IS":
                rows, _, _ = _quarter_is(docs, y, q)
                if q == 4:
                    c3 = docs.get((y, 3))
                    rows = _minus(_one(d, "IS", "plain", "cum"), _one(c3, "IS", "cum", "plain")) if c3 else []
            else:
                rows = _one(d, sj, "plain", "cum")
            col = place(rows)
            qp = next((p for p in quarter if p["period"] == per), {})
            for k in keymap[sj]:
                if k in keyacc and qp.get(k) is not None:
                    col[keyacc[k]] = qp[k]
            t["quarter"][per] = col
        t["annual"] = dict(sorted(t["annual"].items()))
        t["quarter"] = dict(sorted(t["quarter"].items()))
