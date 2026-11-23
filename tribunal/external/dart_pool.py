"""DART 공시를 통째로 모아(①) 리포트 숫자와 값·기간으로 짝을 찾는다(②). AI를 쓰지 않는다. (2026-11-21)

지금의 dart_check 는 '리포트 숫자 이름 -> 공시 항목'을 사람이 정해 둔 것만 대조한다. 여기서는 정해 두지 않고
① 그 회사·그 기간의 공시 숫자를 모두 모은다: 재무제표 전체 계정(연결·별도), 재무제표 원본(XBRL)의 숫자 전부(주석 포함, 한국어 이름표),
   정기보고서 주요 정보(주식 총수, 배당, 최대주주, 자기주식, 타법인 출자, 직원, 소액주주, 회사채 잔액)
② 리포트 숫자마다 같은 기간에 같은 값(리포트 표시 자릿수 안)인 공시 숫자를 찾는다. 이름은 보지 않는다.
③ 후보가 있는 숫자는 AI(가장 싼 Haiku)가 후보 가운데 뜻이 같은 것의 번호만 고른다(값이 우연히 같은 다른 항목을 거른다).
   AI는 숫자를 만들지 않고, 값 비교는 ②에서 코드가 이미 했다.
④ AI 가 고른 짝은 보통의 DART 대조 결과(일치)로 바꿔 숫자 검증 목록에 붙인다(extend). 화면과 배심원이 그대로 쓴다.
   값이 같은 후보만 고르므로 이 방식은 '일치'만 낸다. 틀린 숫자(불일치)는 이름으로 찾는 dart_check 가 계속 잡는다.
   그래서 dart_check·KRX·ECOS·KOSIS 가 이미 본 숫자는 건너뛰고, 아무도 안 본 숫자만 더한다.

실행: python -m tribunal.external.dart_pool <분석 결과 폴더>            ①②만 (무료)  -> <폴더>/dart_pool.json
      python -m tribunal.external.dart_pool <분석 결과 폴더> --dry-run  ③을 부르지 않고 양식 점검·예상 비용만
      python -m tribunal.external.dart_pool <분석 결과 폴더> --ai       ③까지 (유료, 허락받고)
"""
from __future__ import annotations

import io
import json
import re
import sys
import zipfile
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..number_check import parse_number
from ..schemas import DataRow, Report
from . import dart_check as dc
from .dart import Dart, DartError

# ---------- ① 모으기 ----------
FS = {"CFS": "연결", "OFS": "별도"}

# 정기보고서 주요 정보: (API, 이름, 줄을 가리키는 칸, {숫자 칸: 이름}). 적지 않은 숫자 칸은 칸 이름 그대로 쓴다
MAJOR = [
    ("stockTotqySttus", "주식의 총수", ["se"], {"istc_totqy": "발행주식총수", "tesstk_co": "자기주식수", "distb_stock_co": "유통주식수",
                                              "isu_stock_totqy": "발행한 주식의 총수", "now_to_isu_stock_totqy": "현재까지 발행한 주식의 총수",
                                              "redc_stock_totqy": "감소한 주식의 총수", "profit_incnr": "이익소각"}),
    ("hyslrSttus", "최대주주 현황", ["nm", "relate", "stock_knd"], {"trmend_posesn_stock_co": "기말 보유주식수", "trmend_posesn_stock_qota_rt": "기말 지분율(%)",
                                                                    "bsis_posesn_stock_co": "기초 보유주식수", "bsis_posesn_stock_qota_rt": "기초 지분율(%)"}),
    ("tesstkAcqsDspsSttus", "자기주식 취득·처분", ["stock_knd", "acqs_mth1", "acqs_mth2", "acqs_mth3"],
     {"bsis_qy": "기초 수량", "trmend_qy": "기말 수량", "change_qy_acqs": "취득 수량", "change_qy_dsps": "처분 수량", "change_qy_incnr": "소각 수량"}),
    ("otrCprInvstmntSttus", "타법인 출자 현황", ["inv_prm"], {"trmend_blce_qy": "기말 보유 수량", "trmend_blce_qota_rt": "기말 지분율(%)",
                                                           "trmend_blce_acntbk_amount": "기말 장부가액", "bsis_blce_qy": "기초 보유 수량",
                                                           "bsis_blce_qota_rt": "기초 지분율(%)", "bsis_blce_acntbk_amount": "기초 장부가액",
                                                           "frst_acqs_amount": "최초 취득금액",
                                                           "recent_bsns_year_fnnr_sttus_tot_assets": "피투자회사 최근 총자산",
                                                           "recent_bsns_year_fnnr_sttus_thstrm_ntpf": "피투자회사 최근 당기순이익"}),
    ("empSttus", "직원 현황", ["sexdstn", "fo_bbm"], {"sm": "직원 수 합계", "rgllbr_co": "정규직 수", "cnttk_co": "기간제 수",
                                                    "avrg_cnwk_sdytrn": "평균 근속연수", "fyer_salary_totamt": "연간 급여 총액", "jan_salary_am": "1인 평균 급여"}),
    ("mrhlSttus", "소액주주 현황", ["se"], {"shrholdr_co": "소액주주 수", "hold_stock_co": "소액주주 보유주식수", "hold_stock_rate": "소액주주 보유비율(%)",
                                          "shrholdr_rate": "소액주주 비율(%)", "stock_tot_co": "총발행주식수"}),
    ("cprndNrdmpBlce", "회사채 미상환 잔액", ["remndr_exprtn1", "remndr_exprtn2"], {"sm": "합계"}),
]
SKIP_KEYS = {"rcept_no", "corp_cls", "corp_code", "corp_name", "stlm_dt", "rm"}


def _num(text) -> Optional[float]:
    t = str(text or "").replace(",", "").replace("%", "").strip()
    if t in ("", "-"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _y_end(y: int) -> str:
    return f"{y}-12-31"


def _q_end(y: int, q: int) -> str:
    return dc._quarter_end(y, q)


def _fact(source, label, value, end, start=None, fs="", rcept="", unit="") -> dict:
    return {"source": source, "label": label, "value": value, "end": end, "start": start, "fs": fs, "rcept_no": rcept, "unit": unit}


def statements_facts(dart: Dart, corp: dict, year: int, q: int) -> List[dict]:
    """재무제표 전체 계정. 사업보고서는 당기·전기·전전기, 분기·반기는 그 분기(3개월·누적)와 전년 같은 기간"""
    out = []
    reprt = dc.REPRT[q]
    for fs in ("CFS", "OFS"):
        res = dart.statements(corp["corp_code"], year, fs, reprt)
        if res.get("status") != "000":
            continue
        for a in res.get("list", []):
            name = f"{a.get('sj_nm', '')} {a.get('account_nm', '')}".strip()
            bs = a.get("sj_div") == "BS"
            rno = a.get("rcept_no", "")
            if q == 4:
                for k, y in (("thstrm_amount", year), ("frmtrm_amount", year - 1), ("bfefrmtrm_amount", year - 2)):
                    v = _num(a.get(k))
                    if v is not None:
                        out.append(_fact("재무제표", name, v, _y_end(y), None if bs else f"{y}-01-01", FS[fs], rno, "원"))
                continue
            end, py_end = _q_end(year, q), _q_end(year - 1, q)
            qs = f"{year}-{q * 3 - 2:02d}-01"
            if bs:
                for k, e in (("thstrm_amount", end), ("frmtrm_amount", _y_end(year - 1))):
                    v = _num(a.get(k))
                    if v is not None:
                        out.append(_fact("재무제표", name, v, e, None, FS[fs], rno, "원"))
                continue
            pairs = [("thstrm_add_amount", f"{year}-01-01", end), ("frmtrm_add_amount", f"{year - 1}-01-01", py_end)]
            if a.get("sj_div") != "CF":            # 현금흐름표의 당기 값은 누적이다
                pairs += [("thstrm_amount", qs, end), ("frmtrm_amount", f"{year - 1}-{q * 3 - 2:02d}-01", py_end)]
            else:
                pairs += [("thstrm_amount", f"{year}-01-01", end)]
            for k, s, e in pairs:
                v = _num(a.get(k))
                if v is not None:
                    out.append(_fact("재무제표", name, v, e, s, FS[fs], rno, "원"))
    return out


def xbrl_facts(dart: Dart, rcept_no: str, reprt: str) -> List[dict]:
    """XBRL 숫자 전부에 한국어 이름표를 붙인다. 이름: 항목 이름표 + 축 값 이름표 (예: '보험계약마진 [별도재무제표, 발행한 보험계약]')"""
    res = dart.xbrl_facts(rcept_no, reprt)
    labels = res.get("labels")
    if labels is None:                            # 예전 캐시에는 이름표가 없다: 저장해 둔 zip 에서 읽는다
        labels = {}
        zf = dart.cache / f"xbrl-{rcept_no}.zip"
        if zf.exists():
            with zipfile.ZipFile(io.BytesIO(zf.read_bytes())) as z:
                lab = next((n for n in z.namelist() if n.endswith("_lab-ko.xml")), None)
                if lab:
                    labels = parse_labels(z.read(lab).decode("utf-8", "ignore"))
    out = []
    for f in res.get("facts", []):
        name = labels.get(f["name"], f["name"])
        dims = [labels.get(v.replace("Member", "") + "Member", labels.get(v, v)) for v in f["dims"].values()]
        label = name + (f" [{', '.join(dims)}]" if dims else "")
        fs = "별도" if "SeparateMember" in f["dims"].values() else "연결" if "ConsolidatedMember" in f["dims"].values() else ""
        out.append(_fact("XBRL", label, f["value"], f.get("end") or f.get("instant"), f.get("start"), fs, rcept_no,
                         "" if f.get("decimals") in ("", None) else "원"))
    return out


_LAB = re.compile(r'<link:label [^>]*xlink:label="Label_label_((?:[a-z\-]+)|(?:entity\d+))_([A-Za-z0-9_]+?)_ko"[^>]*'
                  r'xlink:role="http://www\.xbrl\.org/2003/role/label"[^>]*>([^<]*)<')


def parse_labels(text: str) -> Dict[str, str]:
    """XBRL 한국어 이름표 파일 -> {항목 이름: 한국어 이름} (기본 이름표만)"""
    return {m.group(2): m.group(3).strip() for m in _LAB.finditer(text)}


def major_facts(dart: Dart, corp: dict, year: int, q: int) -> List[dict]:
    out = []
    for api, title, ids, fields in MAJOR:
        try:
            res = dart.get(f"{api}.json", {"corp_code": corp["corp_code"], "bsns_year": str(year), "reprt_code": dc.REPRT[q]})
        except DartError:
            continue
        for row in res.get("list", []):
            who = " ".join(str(row.get(k, "")).strip() for k in ids if str(row.get(k, "")).strip() not in ("", "-"))
            end = (row.get("stlm_dt") or (_y_end(year) if q == 4 else _q_end(year, q)))[:10]
            for k, v in row.items():
                if k in SKIP_KEYS or k in ids:
                    continue
                n = _num(v)
                if n is None:
                    continue
                name = fields.get(k, k)
                when = _y_end(year - 1) if k.startswith("bsis") else end      # '기초' 값은 전년 말 시점
                out.append(_fact(title, f"{title} · {who} · {name}" if who else f"{title} · {name}", n, when, None, "", row.get("rcept_no", "")))
    # 배당에 관한 사항: 당기·전기·전전기 칸
    try:
        res = dart.dividends(corp["corp_code"], year, dc.REPRT[q])
        for row in res.get("list", []):
            for k, y in (("thstrm", year), ("frmtrm", year - 1), ("lwfr", year - 2)):
                n = _num(row.get(k))
                if n is not None:
                    knd = row.get("stock_knd", "")
                    out.append(_fact("배당에 관한 사항", f"배당 · {row.get('se', '')}{'' if knd in ('', '-') else ' · ' + knd}", n, _y_end(y), None, "",
                                     row.get("rcept_no", "")))
    except DartError:
        pass
    return out


def filings_before(dart: Dart, corp: dict, report_date: str) -> List[Tuple[int, int, str]]:
    """리포트 작성일 전에 나온 정기보고서: 직전 사업보고서 + 그 뒤 가장 최근 분기·반기 보고서. [(연도, 분기, 접수번호)]"""
    y = int(report_date[:4])
    out = []
    for year in (y - 1, y - 2):
        fs = [f for f in dart.annual_filings(corp["corp_code"], year) if f.get("rcept_dt", "") <= report_date.replace("-", "")]
        if fs:
            out.append((year, 4, fs[-1]["rcept_no"]))
            break
    for q in (3, 2, 1):
        fs = [f for f in dart.period_filings(corp["corp_code"], y, q * 3) if f.get("rcept_dt", "") <= report_date.replace("-", "")]
        if fs:
            out.append((y, q, fs[-1]["rcept_no"]))
            break
    return out


# ---------- 공시 본문의 표 (2026-11-21) ----------
# 재무제표 API·XBRL·주요 정보에 없는 숫자(주석의 종속기업 요약 재무, 사업의 내용 표 등)는 공시 본문 표에만 있다.
# 본문의 모든 표를 '표 제목 · 줄 이름 · 열 이름 = 값'으로 풀어 넣는다. 표 종류를 사람이 정하지 않는다.
_TABLE = re.compile(r"<TABLE\b.*?</TABLE>", re.S | re.I)
_TR = re.compile(r"<TR\b.*?</TR>", re.S | re.I)
_CELL = re.compile(r"<T([DHEU])\b([^>]*)>(.*?)</T\1>", re.S | re.I)
_SPAN = re.compile(r"COLSPAN=\"?(\d+)", re.I)
_UNIT = re.compile(r"단\s*위\s*[:：]?\s*([^)\]<]{1,12})")
_CELLNUM = re.compile(r"[△▲\-]?\(?\d[\d,]*(?:\.\d+)?\)?")
_PRIOR_Q = re.compile(r"전\s*반\s*기|전\s*분\s*기|전년\s*동기")
_PRIOR_Y = re.compile(r"(?<![가-힣])전\s*기(?:\s*말)?(?![가-힣])|전\s*년\s*말|전\s*사업연도|전기말")
_PCT_NAME = re.compile(r"%|율|비율|비중")
_COUNT_NAME = re.compile(r"수량|주식\s*수|주식수|\(주\)")


def _plain(html: str) -> str:
    import html as _h
    return re.sub(r"\s+", " ", _h.unescape(re.sub(r"<[^>]+>", " ", html))).strip()


def _unit_of(text: str) -> Optional[Tuple[float, str]]:
    """'(단위: 백만원)' -> (배수, 원|주|%). 가장 가까운(마지막) 표기. 적지 않았으면 None"""
    ms = list(_UNIT.finditer(text or ""))
    if not ms:
        return None
    u = ms[-1].group(1).replace(" ", "")
    for word, mult, kind in (("십억원", 1e9, "원"), ("백만원", 1e6, "원"), ("억원", 1e8, "원"), ("천원", 1e3, "원"),
                             ("천주", 1e3, "주"), ("원", 1.0, "원"), ("주", 1.0, "주"), ("%", 1.0, "%")):
        if word in u:
            return mult, kind
    return None


def _cell_value(t: str) -> Optional[float]:
    t = t.replace(" ", "")
    if not _CELLNUM.fullmatch(t):
        return None
    neg = t.startswith(("△", "▲", "-", "(")) and len(t) > 1
    v = _num(re.sub(r"[△▲\-()]", "", t))
    return None if v is None else (-v if neg else v)


def _rows(table: str) -> List[List[str]]:
    out = []
    for tr in _TR.findall(table):
        cells = []
        for _, attrs, body in _CELL.findall(tr):
            span = int((_SPAN.search(attrs) or [0, 1])[1])
            cells += [_plain(body)] * max(1, min(span, 20))
        if cells:
            out.append(cells)
    return out


def table_facts(text: str, year: int, q: int, rcept: str) -> List[dict]:
    """공시 본문 글 -> 표의 숫자 전부. 기간: 열 이름이나 표 앞 글에 '전기·전년'이 있으면 전년 말, '전반기·전분기'면 전년 같은 분기 말"""
    end = _y_end(year) if q == 4 else _q_end(year, q)
    prior_y, prior_q = _y_end(year - 1), (_y_end(year - 1) if q == 4 else _q_end(year - 1, q))
    out, last = [], 0
    cur = (1.0, "")                                   # 단위를 적지 않은 표는 문서에서 앞서 적은 단위를 따른다
    for m in _TABLE.finditer(text):
        before = _plain(text[max(last, m.start() - 1500):m.start()])[-200:]
        rows = _rows(m.group(0))
        if not any(_cell_value(c) is not None for r in rows for c in r):
            continue                                  # 숫자 없는 표(단위만 적은 작은 표 등)는 다음 표의 앞 글로 남긴다
        last = m.end()
        cur = _unit_of(before + " " + " ".join(rows[0])) or cur
        mult, ukind = cur
        title = re.split(r"다\.\s*", re.sub(r"\(?\s*단\s*위.*$", "", before).strip())[-1][-50:]
        head: List[str] = []
        for r in rows:
            if any(_cell_value(c) is not None for c in r):
                break
            head = [((head[i] + " ") if i < len(head) and head[i] != c else "") + c for i, c in enumerate(r)] if head else r
        for r in rows:
            vals = [(j, _cell_value(c)) for j, c in enumerate(r)]
            if not any(v is not None for _, v in vals):
                continue
            first = next(j for j, v in vals if v is not None)
            name = " ".join(dict.fromkeys(c for c in r[:first] if c)).strip()
            if not name:
                continue
            for j, v in vals:
                if v is None:
                    continue
                k = j if len(r) == len(head) else len(head) - (len(r) - j)          # 길이가 다르면 오른쪽 끝을 맞춘다
                col = head[k] if 0 <= k < len(head) else ""
                near = title[-20:]                    # 표 바로 앞 제목 ('② 전기말')
                when = prior_q if _PRIOR_Q.search(col) else prior_y if _PRIOR_Y.search(col) else \
                    (prior_q if _PRIOR_Q.search(near) else prior_y if _PRIOR_Y.search(near) else end)
                raw = bool(_PCT_NAME.search(f"{col} {name}")) or ukind == "%" or \
                    (ukind == "원" and bool(_COUNT_NAME.search(col)))
                out.append(_fact("공시 본문 표", f"본문 표 · {title} · {name} · {col}".replace(" ·  · ", " · "),
                                 v if raw else v * mult, when, None, "", rcept, "" if raw else ukind))
    return out


def gather(dart: Dart, corp: dict, report_date: str) -> Tuple[List[dict], List[dict]]:
    facts, used = [], []
    for year, q, rno in filings_before(dart, corp, report_date):
        used.append({"year": year, "q": q, "rcept_no": rno})
        facts += statements_facts(dart, corp, year, q)
        facts += major_facts(dart, corp, year, q)
        facts += xbrl_facts(dart, rno, dc.REPRT[q])
        try:
            facts += table_facts(dart.document_text(rno), year, q, rno)
        except DartError:
            pass
    return facts, used


# ---------- ② 값·기간으로 짝 찾기 ----------
def row_value(row: DataRow) -> Optional[Tuple[float, float, str]]:
    """(값, 허용 차이, 종류: money|count|pct|plain) 또는 None. 금액은 원 단위로 바꾼다"""
    unit = (row.unit or "").replace(" ", "")
    val = row.value or ""
    if re.search(r"배|x|bp", unit) or re.search(r"\d\s*(배|x)\s*$", val):
        return None
    if "%" in unit or val.strip().endswith("%"):
        v = parse_number(val, "")
        if v is None:
            return None
        m = re.search(r"\.(\d+)", val)
        return v, (10 ** -len(m.group(1)) if m else 1.0), "pct"
    if "주" in unit and "원" not in unit:
        mult = 1e3 if "천주" in unit else 1e4 if "만주" in unit else 1.0     # '497,660 천주' = 497,660,000주
        v = parse_number(val, "")
        return (v * mult, mult, "count") if v is not None else None
    if dc._unit_mult(row) is not None or re.search(r"\d\s*[조억만]|원", val):
        won, mult = dc._won(row)
        return (won, dc._tolerance(row, mult), "money") if won is not None else None
    v = parse_number(val, "")
    if v is None:
        return None
    m = re.search(r"\.(\d+)", val)
    return v, (10 ** -len(m.group(1)) if m else 1.0), "plain"


def row_period(row: DataRow) -> Optional[dict]:
    """{'end', 'start'(기간 값이면 연초 또는 분기 초, 모르면 None), 'label'} 또는 None(기간을 밝히지 않음)"""
    ctx = " ".join(dc._ISO.sub(" ", x or "") for x in (row.item, row.as_of, row.basis))
    per = dc.quarter(ctx) if (dc._QTR.search(ctx) or dc._MEND.search(ctx)) else None
    if per:
        y, q = per["year"], per["q"]
        start = f"{y}-01-01" if (per["cum"] or q == 1) else f"{y}-{q * 3 - 2:02d}-01"
        return {"end": _q_end(y, q), "start": start, "label": per["label"]}
    m = dc._YEAR.search(ctx)
    if m:
        g = next(x for x in m.groups() if x)
        y = int(g) if len(g) == 4 else 2000 + int(g)
        return {"end": _y_end(y), "start": f"{y}-01-01", "label": f"{y}년"}
    return None


def _same_period(per: Optional[dict], f: dict, latest: str) -> bool:
    if per is None:                                  # 기간을 밝히지 않은 숫자: 작성일 전 가장 최근 시점 값끼리만
        return f["end"] == latest
    if f["end"] != per["end"]:
        return False
    return f["start"] is None or f["start"] == per["start"]


def _matches(v: float, tol: float, kind: str, f: dict) -> bool:
    cands = [f["value"]]
    if kind == "pct":
        cands.append(f["value"] * 100)               # XBRL 비율은 0.305 처럼 소수로 적는다
    return any(abs(v - c) <= tol + 1e-9 for c in cands)


def _sig(v: float) -> int:
    digits = re.sub(r"[^0-9]", "", f"{abs(v):.10g}").strip("0")
    return len(digits)


# 이름이 리포트의 평가·계산 결과임을 말하는 숫자. 공시에 없는 숫자라 짝을 찾지 않는다 (업종과 무관한 낱말).
# 2026-11-21 시험: '금융그룹 가치 9.98조'(4개 지분가치의 합)·'신사업 가치 0.47조'(자기자본×PBR)를 AI가 값이 비슷한 다른 공시 항목과 짝지음
_CALC = re.compile(r"가치|적정|목표|SOTP|할인|역산|조정|상승여력|괴리")


def match(report: Report, facts: List[dict], report_date: str) -> dict:
    latest = max((f["end"] for f in facts if f["end"] and f["end"] <= report_date), default="")
    rows_out = []
    for row in report.data_rows:
        ctx = " ".join(x or "" for x in (row.item, row.as_of, row.basis))
        base = {"rid": row.rid, "item": row.item, "value": row.value, "unit": row.unit, "page": row.page}
        if dc._EST.search(" ".join(dc._ISO.sub(" ", x or "") for x in (row.item, row.as_of, row.basis))):
            rows_out.append(dict(base, status="건너뜀", why="추정·목표·전망 값"))
            continue
        if _CALC.search(row.item or ""):
            rows_out.append(dict(base, status="건너뜀", why="리포트가 계산한 평가 값(이름에 가치·적정·목표·할인·조정 등)"))
            continue
        rv = row_value(row)
        if rv is None:
            rows_out.append(dict(base, status="건너뜀", why="값을 숫자 하나로 읽지 못함(배수·범위 등)"))
            continue
        v, tol, kind = rv
        per = row_period(row)
        hits = [f for f in facts if _same_period(per, f, latest) and _matches(v, tol, kind, f)]
        seen, uniq = set(), []
        for f in sorted(hits, key=lambda f: {"재무제표": 0, "XBRL": 2}.get(f["source"], 1)):
            k = (re.sub(r"\s|\[.*?\]", "", f["label"])[:40], f["fs"], round(f["value"], 4))
            if k not in seen:
                seen.add(k)
                uniq.append(f)
        weak = _sig(v) < 3                            # 자릿수가 적은 값(예: 11.5)은 우연히 같을 수 있다
        status = "짝 없음" if not uniq else "짝 1개" if len(uniq) == 1 else "후보 여러 개"
        rows_out.append(dict(base, status=status, kind=kind, period=(per or {}).get("label", f"기간 미상 → {latest} 값과 비교"),
                             weak=weak, candidates=[{k: f[k] for k in ("source", "label", "value", "fs", "end", "rcept_no")} for f in uniq[:8]],
                             n_candidates=len(uniq), context=ctx[:120]))
    rows_out += material_rows(report, facts, latest)
    count: Dict[str, int] = {}
    for r in rows_out:
        count[r["status"]] = count.get(r["status"], 0) + 1
    return {"rows": rows_out, "count": count, "latest": latest}


# ---------- ②-2 계산 재료 짝 찾기 (2026-11-21) ----------
# 리포트가 계산한 값(지분가치 등)은 공시에 없지만, 계산식(basis)에 넣은 재료(보유 주식 수, 자본 등)는 공시에 있을 수 있다.
# basis 속 숫자를 하나씩 꺼내 같은 방법으로 후보를 찾는다. 숫자 종류마다 사람이 정하지 않는다.
_TOKEN = re.compile(r"(?<![\d.,])(\d[\d,]*(?:\.\d+)?)\s*(천주|만주|주|십억\s*원|백만\s*원|억\s*원|조\s*원|원|조|억|%|배|bp|x)?\s*(?:\(([^()]{1,20})\))?")
MAT = "~"                                             # 재료 줄의 rid: 'R-31~1'


def material_tokens(row: DataRow) -> List[dict]:
    """basis 속 재료 숫자: [{'value','unit','label','text'}]. 비율·배수·연도·리포트 값 자신은 뺀다"""
    out, own = [], re.sub(r"[^\d.]", "", row.value or "")
    for m in _TOKEN.finditer(dc._ISO.sub(" ", row.basis or "")):
        num, unit, label = m.group(1), (m.group(2) or "").replace(" ", ""), (m.group(3) or "").strip()
        if unit in ("%", "배", "bp", "x") or re.fullmatch(r"(19|20)\d\d", num.replace(",", "")) or num.replace(",", "") == own:
            continue
        if not unit:                                  # 단위 없는 숫자는 리포트 값의 금액 단위를 따른다
            if dc._unit_mult(row) is None or "%" in (row.unit or ""):
                continue
            unit = row.unit
        if _sig(float(num.replace(",", ""))) < (2 if label else 3):   # 자릿수가 적으면 우연히 같은 값이 많다 (이름이 붙은 재료는 2자리까지)
            continue
        out.append({"value": num, "unit": unit, "label": label, "text": m.group(0).strip()})
    return out


def _calc_values(report: Report) -> List[float]:
    """리포트가 계산·추정한 금액 값들(원). 계산식 속 숫자가 이것과 같으면 재료가 아니라 리포트의 다른 계산 결과다
    (2026-11-21: 'SOTP 금융그룹 가치 = 2.51(화재) + 1.91(증권)…'의 1.91은 리포트가 계산한 삼성증권 지분가치)"""
    out = []
    for r in report.data_rows:
        ctx = " ".join(dc._ISO.sub(" ", x or "") for x in (r.item, r.as_of, r.basis))
        if not (_CALC.search(r.item or "") or dc._EST.search(ctx) or re.search(r"[×÷=+]|\d\s*[−-]\s*\d", r.basis or "")):
            continue
        rv = row_value(r)
        if not rv or rv[2] != "money":
            continue
        # 결과가 자기 계산식 속 재료와 같은 값이면(예: '자기자본 0.47조 × 1.0배') 사실상 재료라 거르는 데 쓰지 않는다
        own = re.sub(r"[^\d.,]", "", r.value or "")
        if own and re.search(rf"(?<![\d.,]){re.escape(own)}(?![\d])", dc._ISO.sub(" ", r.basis or "")):
            continue
        out.append(rv[0])
    return out


def is_report_result(v: float, tol: float, kind: str, calc: List[float]) -> bool:
    return kind == "money" and any(abs(v - c) <= max(tol, abs(c) * 0.005) for c in calc)


def material_rows(report: Report, facts: List[dict], latest: str) -> List[dict]:
    rows_out = []
    calc = _calc_values(report)
    for row in report.data_rows:
        est = dc._EST.search(" ".join(dc._ISO.sub(" ", x or "") for x in (row.item, row.as_of, row.basis)))
        per = None if est else row_period(row)
        for i, t in enumerate(material_tokens(row), 1):
            sub = DataRow(rid=row.rid, item=row.item, value=t["value"], unit=t["unit"], page=row.page)
            rv = row_value(sub)
            if rv is None:
                continue
            v, tol, kind = rv
            if is_report_result(v, tol, kind, calc):  # 리포트의 다른 계산 결과를 가져다 쓴 숫자: 공시에 없다
                continue
            hits = [f for f in facts if _same_period(per, f, latest) and _matches(v, tol, kind, f)]
            seen, uniq = set(), []
            for f in sorted(hits, key=lambda f: {"재무제표": 0, "XBRL": 2}.get(f["source"], 1)):
                k = (re.sub(r"\s|\[.*?\]", "", f["label"])[:40], f["fs"], round(f["value"], 4))
                if k not in seen:
                    seen.add(k)
                    uniq.append(f)
            if not uniq:
                continue
            uniq.sort(key=lambda f: abs(f["value"] - v))  # 가까운 값부터
            rows_out.append({"rid": f"{row.rid}{MAT}{i}", "of": row.rid, "item": f"{row.item}의 계산 재료 '{t['text']}'",
                             "value": t["value"], "unit": t["unit"], "page": row.page, "material": t,
                             "status": "짝 1개" if len(uniq) == 1 else "후보 여러 개", "kind": kind,
                             "period": (per or {}).get("label", f"기간 미상 → {latest} 값과 비교"), "weak": False,
                             "candidates": [{k: f[k] for k in ("source", "label", "value", "fs", "end", "rcept_no")} for f in uniq[:8]],
                             "n_candidates": len(uniq)})
    return rows_out


# ---------- ②-3 비율을 공시 숫자 두 개로 다시 계산 (2026-11-21) ----------
# 공시에 같은 값이 없는 비율(예: CSM 상각률)은 분자·분모가 공시에 있으면 나눠 볼 수 있다.
# AI(Haiku)는 분자·분모 공시 이름에 들어 있을 낱말만 적고(ratio_planner), 공시 숫자를 찾아 나누는 것은 코드가 한다.
# 분모가 잔액이면 연말·연초·평균을 모두 계산한다. 값이 맞는 조합만 후보가 되고, 뜻이 맞는 것은 ③에서 AI가 고른다.
CAP = 2000                                            # 분자·분모 후보 수 상한 (조합이 너무 많아지지 않게)


def _dims(label: str) -> frozenset:
    """'이름 [연결재무제표 [구성요소], 발행한 보험계약 [구성요소]]' -> {'연결재무제표', '발행한 보험계약'}"""
    m = re.search(r"\[(.*)\]\s*$", label or "")
    if not m:
        return frozenset()
    return frozenset(x.strip() for x in re.sub(r"\[구성요소\]", "", m.group(1)).split(",") if x.strip())


def _same_scope(a: frozenset, b: frozenset) -> bool:
    """분자와 분모가 같은 범위(같은 회사 구분·같은 상품 묶음)인가. 한쪽에만 구성요소 하나가 더 있는 것은 허용
    (예: 분모 '보험계약부채 [.., 보험계약마진]'처럼 잔액 항목을 가리키는 구성요소)"""
    return a == b or (a < b and len(b - a) == 1) or (b < a and len(a - b) == 1)


def ratio_targets(out: dict) -> List[dict]:
    """공시에서 같은 값을 못 찾은 비율 숫자"""
    return [r for r in out["rows"] if r.get("status") != "건너뜀" and r.get("kind") == "pct" and MAT not in r["rid"]]


def ratio_payload(rows: List[dict]) -> dict:
    return {"rows": [{"rid": r["rid"], "item": r["item"], "value": r["value"], "unit": r["unit"], "as_of": r.get("as_of", ""),
                      "basis": r.get("basis", ""), "period": r.get("period", "")} for r in rows]}


def _norm_label(s: str) -> str:
    return re.sub(r"\s", "", s or "").lower()


def _has(label: str, groups: List[str]) -> bool:
    n = _norm_label(label)
    return any(g.split() and all(_norm_label(w) in n for w in g.split()) for g in groups)


def _prior_end(end: str) -> str:
    return f"{int(end[:4]) - 1}-12-31"


def ratio_candidates(row: DataRow, plan, facts: List[dict], latest: str) -> List[dict]:
    rv = row_value(row)
    if rv is None or not plan.is_ratio or not plan.numerator or not plan.denominator:
        return []
    v, tol, _ = rv
    per = row_period(row)
    end = per["end"] if per else latest
    start = per["start"] if per else None
    begin = _prior_end(end)
    facts = [f for f in facts if f["source"] != "공시 본문 표"]   # 본문 표는 범위 표시가 없어 엉뚱한 조합이 많다: 비율 계산에는 정리된 공시만
    nums = [f for f in facts if f["end"] == end and (f["start"] is None or start is None or f["start"] == start)
            and f["value"] and _has(f["label"], plan.numerator)][:CAP]
    dens: Dict[Tuple[str, str], Dict[str, dict]] = {}
    for f in facts:
        if f["start"] is None and f["end"] in (end, begin) and f["value"] and _has(f["label"], plan.denominator):
            dens.setdefault((f["label"], f["fs"]), {})[f["end"]] = f
    dens = dict(list(dens.items())[:CAP])
    out, seen = [], set()
    for n in nums:
        nd = _dims(n["label"])
        for (dlabel, dfs), by in dens.items():
            if n["fs"] and dfs and n["fs"] != dfs:
                continue
            if not _same_scope(nd, _dims(dlabel)):
                continue
            bases = []
            if end in by:
                bases.append(("연말", by[end]["value"]))
            if begin in by:
                bases.append(("연초", by[begin]["value"]))
            if end in by and begin in by:
                bases.append(("평균", (by[end]["value"] + by[begin]["value"]) / 2))
            for basis, d in bases:
                r = n["value"] / d * 100
                if abs(r - v) > tol + 1e-9:
                    continue
                key = (n["label"], dlabel, basis)
                if key in seen:
                    continue
                seen.add(key)
                den = by[end] if basis != "연초" else by[begin]
                out.append({"source": "공시 숫자 두 개로 계산", "label": f"{n['label']} ÷ {dlabel} ({basis} 잔액)",
                            "value": round(r, 4), "fs": n["fs"] or dfs, "end": end, "rcept_no": n["rcept_no"],
                            "ratio": {"num": n, "den": den, "den_begin": by.get(begin), "den_end": by.get(end), "basis": basis,
                                      "num_source": n["source"], "den_source": den["source"]}})
    # 구성요소가 적은(합계에 가까운) 조합, 그다음 값이 가까운 조합부터
    return sorted(out, key=lambda c: (len(_dims(c["ratio"]["num"]["label"])) + len(_dims(c["ratio"]["den"]["label"])),
                                      abs(c["value"] - v)))[:8]


def add_ratios(report: Report, out: dict, facts: List[dict], cfg: dict, mock: bool = False, mock_dir=None,
               plans=None) -> None:
    """비율 숫자에 '공시 두 숫자로 계산한 값' 후보를 붙인다. plans 를 주면 AI 를 부르지 않는다(시험용)"""
    targets = ratio_targets(out)
    if not targets:
        return
    if plans is None:
        from .. import llm
        from ..schemas import RatioOut
        res = llm.call("ratio_planner", ratio_payload(targets), RatioOut, cfg, mock, mock_dir)
        plans = res.plans if res else []
    by = {p.rid: p for p in plans}
    latest = out.get("latest") or max((f["end"] for f in facts if f["end"]), default="")
    rows = {r.rid: r for r in report.data_rows}
    for r in targets:
        p, row = by.get(r["rid"]), rows.get(r["rid"])
        if p is None or row is None:
            continue
        r["ratio_plan"] = {"numerator": p.numerator, "denominator": p.denominator, "reason": p.reason, "is_ratio": p.is_ratio}
        cands = ratio_candidates(row, p, facts, latest)
        if cands:                                     # 값이 우연히 같은 공시 비율 후보가 있어도 계산 후보를 앞에 둔다
            both = (cands + r.get("candidates", []))[:10]
            r.update(status="계산 후보", candidates=both, n_candidates=len(both))


# ---------- ③ AI 로 짝 고르기 ----------
def ai_payload(out: dict) -> dict:
    rows = []
    for r in out["rows"]:
        if not r.get("candidates"):
            continue
        rows.append({"rid": r["rid"], "item": r["item"], "value": r["value"], "unit": r["unit"], "period": r["period"],
                     "as_of": r.get("as_of", ""), "basis": r.get("basis", ""), "location": r.get("location", ""),
                     "candidates": [{"n": i + 1, "label": c["label"], "value": c["value"], "fs": c["fs"], "end": c["end"], "source": c["source"]}
                                    for i, c in enumerate(r["candidates"])]})
    return {"rows": rows}


def apply_picks(out: dict, picks) -> None:
    """AI 가 고른 번호를 붙인다. 후보에 없는 번호는 버린다. 답하지 않은 숫자는 'AI 답 없음'."""
    by = {p.rid: p for p in (picks.picks if picks else [])}
    for r in out["rows"]:
        if not r.get("candidates"):
            continue
        p = by.get(r["rid"])
        if p is None:
            r.update(ai="답 없음", picked=None, pick_reason="")
        elif p.pick is not None and 1 <= p.pick <= len(r["candidates"]):
            r.update(ai="고름", picked=r["candidates"][p.pick - 1], pick_reason=p.reason)
        else:
            r.update(ai="같은 것 없음", picked=None, pick_reason=p.reason)
    count: Dict[str, int] = {}
    for r in out["rows"]:
        if "ai" in r:
            count[r["ai"]] = count.get(r["ai"], 0) + 1
    out["ai_count"] = count


def ai_pick(out: dict, cfg: dict, mock: bool = False, mock_dir=None) -> None:
    from .. import llm
    from ..schemas import MatchOut
    payload = ai_payload(out)
    if not payload["rows"]:
        out["ai_count"] = {}
        return
    picks = llm.call("dart_matcher", payload, MatchOut, cfg, mock, mock_dir)
    apply_picks(out, picks)
    out["ai_usd"] = round(sum(u["usd"] for u in llm.USAGE if u["role"] in ("dart_matcher", "ratio_planner")), 4)


def dry_run(out: dict, cfg: dict) -> dict:
    """③을 부르지 않고: 보낼 글의 크기, 예상 비용, 답 양식이 API 가 받는 모양인지(모든 칸 꼭 쓰기)를 점검한다"""
    from .. import llm
    from ..schemas import MatchOut
    payload = ai_payload(out)
    text = llm.load_prompt("dart_matcher") + json.dumps(payload, ensure_ascii=False, indent=1)
    st = llm.settings(cfg, "dart_matcher")
    tin = len(text)                                    # 한글은 글자당 약 1토큰으로 넉넉히
    tout = 60 * len(payload["rows"]) + 2000            # 숫자 하나에 답 약 60토큰 + 생각 여유
    fmt = llm.api_schema(MatchOut)

    def objects(n):
        if isinstance(n, dict):
            if "properties" in n:
                yield n
            for v in n.values():
                yield from objects(v)
        elif isinstance(n, list):
            for v in n:
                yield from objects(v)
    form_ok = fmt is not None and all(set(o.get("required", [])) == set(o["properties"]) and o.get("additionalProperties") is False
                                      for o in objects(fmt))
    from ..schemas import RatioOut
    rt = ratio_targets(out)
    ptxt = llm.load_prompt("ratio_planner") + json.dumps(ratio_payload(rt), ensure_ascii=False, indent=1)
    pst = llm.settings(cfg, "ratio_planner")
    pin, pout = len(ptxt), 80 * len(rt) + 2000
    extra = 10 * 400                                   # 비율 계산 후보가 붙으면 ③에 보낼 글이 늘어난다 (넉넉히 10개)
    pfmt = llm.api_schema(RatioOut)
    form_ok = form_ok and pfmt is not None and all(set(o.get("required", [])) == set(o["properties"]) and o.get("additionalProperties") is False
                                                   for o in objects(pfmt))
    usd = (tin + extra) / 1e6 * st["price_in"] + tout / 1e6 * st["price_out"] + pin / 1e6 * pst["price_in"] + pout / 1e6 * pst["price_out"]
    return {"rows": len(payload["rows"]), "ratio_rows": len(rt), "model": st["model"], "effort": st["effort"],
            "input_tokens": tin + extra + pin, "output_tokens": tout + pout, "usd": round(usd, 4), "form_ok": form_ok,
            "note": "Haiku 두 번(비율 낱말 적기, 짝 고르기). 캐시 없음. 단가는 config.yaml prices"}


# ---------- ④ 고른 짝을 DART 대조 결과로 ----------
def _row_for(report: Report, rid: str) -> Optional[DataRow]:
    return next((r for r in report.data_rows if r.rid == rid), None)


def _shown(v: float, row: DataRow, kind: str) -> str:
    """공시 값을 리포트와 같은 단위로 보여 준다"""
    if kind == "money":
        mult = dc._unit_mult(row) or 1.0
        return dc._fmt(v, row, mult)
    if kind == "count":
        return f"{v:,.0f}주"
    if kind == "pct":
        stated = (row_value(row) or (v, 0, ""))[0]
        x = v * 100 if abs(stated - v * 100) < abs(stated - v) else v      # XBRL 비율은 소수(0.413)
        return f"{x:g}%"
    return f"{v:,.6g}"


def to_results(report: Report, out: dict, dart: Dart, corp: dict, report_date: str, start: int) -> List[dict]:
    """AI 가 '고름'으로 답한 숫자를 DART 대조 결과(일치)로. 번호는 D-<start+1>부터 (dart_check 다음)"""
    res, n = [], start
    for r in out["rows"]:
        f = r.get("picked")
        row = _row_for(report, r.get("of", r["rid"]))
        if not f or row is None:
            continue
        n += 1
        x = f.get("ratio")
        if x:                                         # 비율: 공시 숫자 두 개를 코드가 나눈 값이 리포트와 같다
            num, den = x["num"], x["den"]
            y, m = int(num["end"][:4]), int(num["end"][5:7])
            per = None if m == 12 else {"q": m // 3, "year": y, "cum": True, "label": f"{y}년 {m}월 말"}
            acc = {"rcept_no": num["rcept_no"], "sj_nm": num["source"], "account_nm": num["label"], "thstrm_nm": ""}
            src = dc._source(dart, corp, y, {"연결": "CFS", "별도": "OFS"}.get(num["fs"]), acc, {"fetched_at": out.get("made_at", "")},
                             report_date, per=per, api=f"{num['source']} (분자·분모 공시 항목을 AI가 고르고 코드가 나눔)")
            if x["basis"] == "평균":
                how = (f"(연초 {x['den_begin']['value'] / 1e8:,.0f}억 + 연말 {x['den_end']['value'] / 1e8:,.0f}억) ÷ 2"
                       if abs(num["value"]) >= 1e8 else "연초·연말 평균")
            else:
                how = f"{den['value'] / 1e8:,.0f}억 ({x['basis']} {den['end']})" if abs(den["value"]) >= 1e8 else f"{den['value']:,.6g} ({x['basis']})"
            num_txt = f"{num['value'] / 1e8:,.0f}억" if abs(num["value"]) >= 1e8 else f"{num['value']:,.6g}"
            res.append(dc._result(
                f"D-{n}", row, f"{corp['corp_name']} {row.item}, 공시 숫자로 다시 계산", dc.MATCH,
                f"공시의 '{num['label'][:60]}' {num_txt} ÷ '{den['label'][:60]}' {how} = {f['value']:.2f}%로, 리포트 값과 같습니다. "
                f"분모는 {x['basis']} 잔액 기준입니다. 어느 공시 항목을 쓸지는 AI가 골랐고({r.get('pick_reason', '')}), 나눗셈과 비교는 코드가 했습니다.",
                source=src, computed=f"{f['value']:.2f}%", diff_pct=0.0, timing=src["timing"],
                basis={"fs": num["fs"], "how": f"공시 두 숫자로 계산({x['basis']} 잔액)", "year": "표기"}, matched_by="ai_ratio"))
            continue
        if r.get("material"):                         # 계산 재료: 리포트 값이 아니라 재료 숫자가 공시와 같다
            t = r["material"]
            sub = DataRow(rid=row.rid, item=r["item"], value=t["value"], unit=t["unit"], page=row.page)
            y, m = int(f["end"][:4]), int(f["end"][5:7])
            per = None if m == 12 else {"q": m // 3, "year": y, "cum": True, "label": f"{y}년 {m}월 말"}
            acc = {"rcept_no": f["rcept_no"], "sj_nm": f["source"], "account_nm": f["label"], "thstrm_nm": ""}
            src = dc._source(dart, corp, y, {"연결": "CFS", "별도": "OFS"}.get(f["fs"]), acc, {"fetched_at": out.get("made_at", "")},
                             report_date, per=per, api=f"{f['source']} (계산에 쓴 숫자와 값이 같은 공시 항목을 AI가 골라 짝지음)")
            res.append(dc._result(
                f"D-{n}", sub, f"{row.item}에 쓴 {t['text']}, 공시 대조", dc.MATCH,
                f"리포트 값 '{row.value}{row.unit}'은 리포트가 계산한 추정이라 공시에 없습니다. 계산에 쓴 숫자 '{t['text']}'이 "
                f"공시의 '{f['label']}'({f['source']}{', ' + f['fs'] if f['fs'] else ''}, {f['end']} 기준)과 같은 값입니다. "
                f"짝은 AI가 골랐고({r.get('pick_reason', '')}), 값 비교는 코드가 했습니다.",
                source=src, computed=_shown(f["value"], sub, r.get("kind", "")), diff_pct=0.0, timing=src["timing"],
                basis={"fs": f["fs"], "how": "AI 짝", "year": "표기"}, matched_by="ai_pool", material=t["text"]))
            continue
        y, m = int(f["end"][:4]), int(f["end"][5:7])
        per = None if m == 12 else {"q": m // 3, "year": y, "cum": True, "label": f"{y}년 {m}월 말"}
        acc = {"rcept_no": f["rcept_no"], "sj_nm": f["source"], "account_nm": f["label"], "thstrm_nm": ""}
        src = dc._source(dart, corp, y, {"연결": "CFS", "별도": "OFS"}.get(f["fs"]), acc, {"fetched_at": out.get("made_at", "")},
                         report_date, per=per, api=f"{f['source']} (값이 같은 공시 항목을 AI가 골라 짝지음)")
        res.append(dc._result(
            f"D-{n}", row, f"{corp['corp_name']} {f['label'][:40]}, 공시 대조", dc.MATCH,
            f"공시의 '{f['label']}'({f['source']}{', ' + f['fs'] if f['fs'] else ''}, {f['end']} 기준)과 같은 값입니다. "
            f"어느 공시 항목과 맞출지는 AI가 값이 같은 후보 가운데 골랐고({r.get('pick_reason', '')}), 값 비교는 코드가 했습니다.",
            source=src, computed=_shown(f["value"], row, r.get("kind", "")), diff_pct=0.0, timing=src["timing"],
            basis={"fs": f["fs"], "how": "AI 짝", "year": "표기"}, matched_by="ai_pool"))
    return res


def extend(report: Report, ext: dict, covered: set, cfg: dict, mock: bool = False, dart: Optional[Dart] = None,
           mock_dir=None) -> dict:
    """dart_check 결과(ext)에 ①~④를 더한다. covered: 이미 공식 데이터와 대조한 숫자(rid). 반환: 요약"""
    dart = dart or Dart()
    corp = ext.get("target")
    report_date = report.meta.report_date or report.meta.snapshot_date
    if not corp or not report_date:
        return {"skipped": "회사나 작성일을 몰라 건너뜀"}
    facts, used = gather(dart, corp, report_date)
    out = match(report, facts, report_date)
    rows = {r.rid: r for r in report.data_rows}
    out["rows"] = [r for r in out["rows"] if r.get("of", r["rid"]) not in covered]
    for r in out["rows"]:
        d = rows.get(r.get("of", r["rid"]))
        if d:
            r.update(as_of=d.as_of or "", basis=d.basis or "", location=getattr(d, "location", "") or "")
    add_ratios(report, out, facts, cfg, mock, mock_dir)
    ai_pick(out, cfg, mock, mock_dir)
    start = max([int(x["check_id"].split("-")[1]) for x in ext.get("results", []) if x["check_id"].startswith("D-")] or [0])
    new = to_results(report, out, dart, corp, report_date, start)
    ext.setdefault("results", []).extend(new)
    add_fact_values(report, ext, facts)
    info = {"facts": len(facts), "filings": used, "asked": sum(1 for r in out["rows"] if r.get("candidates")),
            "picked": len(new), "ai_count": out.get("ai_count", {}), "ai_usd": out.get("ai_usd", 0.0)}
    ext["pool"] = info
    return info


def run(run_dir: Path, dart: Optional[Dart] = None, ai: bool = False, cfg: Optional[dict] = None) -> dict:
    dart = dart or Dart()
    report = Report.model_validate_json((run_dir / "claims.json").read_text(encoding="utf-8"))
    report_date = report.meta.report_date or report.meta.snapshot_date
    corp = dart.corp_by_stock(report.meta.ticker)
    facts, used = gather(dart, corp, report_date)
    out = match(report, facts, report_date)
    rows = {r.rid: r for r in report.data_rows}
    for r in out["rows"]:                              # AI 에게 넘길 리포트 쪽 설명
        d = rows.get(r.get("of", r["rid"]))
        if d:
            r.update(as_of=d.as_of or "", basis=d.basis or "", location=getattr(d, "location", "") or "")
    if ai:
        from ..runner import load_config
        cfg = cfg or load_config()
        add_ratios(report, out, facts, cfg)
        ai_pick(out, cfg)
    by_src: Dict[str, int] = {}
    for f in facts:
        by_src[f["source"]] = by_src.get(f["source"], 0) + 1
    out.update(report_date=report_date, filings=used, n_facts=len(facts), facts_by_source=by_src, calls=dart.calls,
               made_at=date.today().isoformat())
    (run_dir / "dart_pool.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main(argv) -> int:
    if not argv:
        print(__doc__)
        return 2
    if "--dry-run" in argv:
        from ..runner import load_config
        out = run(Path(argv[0]))
        print(json.dumps(dry_run(out, load_config()), ensure_ascii=False, indent=1))
        return 0
    out = run(Path(argv[0]), ai="--ai" in argv)
    print(f"공시 숫자 {out['n_facts']:,}개 ({', '.join(f'{k} {v:,}' for k, v in out['facts_by_source'].items())}), 보고서 {out['filings']}")
    print("리포트 숫자:", out["count"])
    for r in out["rows"]:
        line = f"  {r['status']:<6} {r['rid']} {r['item']} = {r['value']} {r['unit']}"
        if r.get("candidates"):
            c = r["candidates"][0]
            line += f"  →  [{c['source']}] {c['label'][:70]} = {c['value']:,.6g} ({c['fs']} {c['end']})" + (" 외" if r["n_candidates"] > 1 else "")
            if r.get("weak"):
                line += "  (자릿수 적음: 우연일 수 있음)"
        elif r.get("why"):
            line += f"  ({r['why']})"
        if r.get("ai"):
            line += "\n           " + f"AI: {r['ai']}" +(f" → {r['picked']['label'][:60]}" if r.get("picked") else "") + f" ({r.get('pick_reason', '')[:80]})"
        print(line)
    if out.get("ai_count") is not None:
        print("AI 짝 고르기:", out["ai_count"], f"비용 약 {out.get('ai_usd', 0)}달러")
    return 0


# ---------- ⑤ 공식 데이터만으로 계산되는 값 (2026-11-21) ----------
# '보유 주식 수 × 주가'(여러 개를 더한 것 포함)는 가정이 없는 사실이다: 주식 수는 DART(타법인 출자 현황), 주가는 KRX 종가.
# 둘 다 공식 데이터에서 그대로 찾고 코드가 곱해 리포트 값과 비교한다. 할인율·배수 같은 리포트 가정이 들어간 값은 하지 않는다.
_TERM = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(천주|만주|주)\s*[×x]\s*(\d[\d,]*)\s*원")
_DAY = re.compile(r"(20\d\d)[.\-/](\d{1,2})[.\-/](\d{1,2})")
_HOLD = re.compile(r"^타법인 출자 현황 · (.+?) · 기말 보유 수량$")


def fact_values(report: Report, facts: List[dict], krx, start: int) -> List[dict]:
    from ..number_check import MATCH, MISMATCH
    res, n = [], start
    holds: Dict[float, List[Tuple[str, dict]]] = {}
    for f in facts:
        m = _HOLD.match(f["label"])
        if m:
            holds.setdefault(round(f["value"]), []).append((m.group(1), f))
    for row in report.data_rows:
        basis = row.basis or ""
        head = re.split(r"=", basis)[0]                # 'A × B = 시가 − 차감' 이면 등호 앞(시가)까지만 사실
        terms = _TERM.findall(head)
        d = _DAY.search(f"{row.as_of} {row.item} {basis}")
        if not terms or not d or re.search(r"[%배]|할인", head):
            continue
        rest = _TERM.sub("", head)
        if re.search(r"\d", rest):                    # 주식 수 × 주가 말고 다른 숫자가 섞이면 하지 않는다
            continue
        day = f"{d.group(1)}{int(d.group(2)):02d}{int(d.group(3)):02d}"
        total, parts, bad, ok = 0.0, [], [], True
        for qty, unit, price in terms:
            shares = float(qty.replace(",", "")) * {"천주": 1e3, "만주": 1e4}.get(unit, 1.0)
            cand = holds.get(round(shares), [])
            if not cand:
                ok = False
                break
            co, hf = max(cand, key=lambda c: c[1]["end"])     # 가장 최근 공시의 보유 수량
            try:
                hit, _, kres = krx.find(day, name=co)
            except Exception:                         # KRX 키·연결 문제면 이 대조는 건너뛴다
                return res
            close = float(hit["TDD_CLSPRC"].replace(",", "")) if hit and hit.get("TDD_CLSPRC") else None
            if close is None:
                ok = False
                break
            rp = float(price.replace(",", ""))
            if abs(rp - close) > 0.5:
                bad.append(f"{co} 주가: 리포트 {rp:,.0f}원, KRX 종가 {close:,.0f}원")
            total += shares * close
            parts.append(f"{co} {shares:,.0f}주(DART {hf['end']}) × {close:,.0f}원(KRX {day[:4]}.{day[4:6]}.{day[6:]} 종가)")
        if not ok:
            continue
        rv = row_value(row)
        if rv is None or rv[2] != "money":
            continue
        v, tol, _ = rv
        same = abs(total - v) <= max(tol, abs(v) * 0.001)
        if not same and "=" in basis:                 # 등호 뒤에 리포트 가정(차감 등)이 붙은 값: 시가 부분만 사실이라 판정하지 않는다
            continue
        n += 1
        shown = dc._fmt(total, row, dc._unit_mult(row) or 1.0)
        status = MATCH if same and not bad else MISMATCH
        why = (" + ".join(parts) + f" = {shown}. ") + ("리포트 값과 같습니다." if same and not bad else
                                                        "리포트 값과 다릅니다. " + "; ".join(bad))
        res.append(dc._result(f"D-{n}", row, f"{row.item}, 공시 보유 주식 수 × KRX 종가", status,
                              why + " 주식 수는 DART, 주가는 KRX에서 그대로 가져왔고 곱셈과 비교는 코드가 했습니다.",
                              computed=shown, diff_pct=round((v - total) / total * 100, 2) if total else None,
                              matched_by="official_calc", via="DART·KRX"))
    return res


def add_fact_values(report: Report, ext: dict, facts: Optional[List[dict]] = None, dart: Optional[Dart] = None) -> int:
    """ext(DART 결과)에 ⑤를 더한다. 예전 ⑤ 결과는 바꿔 끼운다. 더한 수"""
    from .krx import Krx
    corp = ext.get("target")
    report_date = report.meta.report_date or report.meta.snapshot_date
    if not corp or not report_date:
        return 0
    if facts is None:
        facts, _ = gather(dart or Dart(), corp, report_date)
    ext["results"] = [x for x in ext.get("results", []) if x.get("matched_by") != "official_calc"]
    start = max([int(x["check_id"].split("-")[1]) for x in ext["results"] if x["check_id"].startswith("D-")] or [0])
    new = fact_values(report, facts, Krx(), start)
    ext["results"].extend(new)
    return len(new)


def prune(report: Report, ext: dict) -> List[str]:
    """저장된 DART 결과에서 지금 규칙으로는 재료가 아닌 것(리포트의 다른 계산 결과)을 뺀다. AI 를 다시 부르지 않는다. 뺀 번호"""
    calc, rows = _calc_values(report), {r.rid: r for r in report.data_rows}
    keep, gone = [], []
    for x in ext.get("results", []):
        row = rows.get(x.get("stated_rid"))
        if x.get("material") and row is not None:
            m = _TOKEN.match(x["material"])
            unit = (m.group(2) or "").replace(" ", "") if m else ""
            sub = DataRow(rid=row.rid, item=row.item, value=m.group(1) if m else "", unit=unit or row.unit)
            rv = row_value(sub)
            if rv and is_report_result(*rv, calc):
                gone.append(x["check_id"])
                continue
        if x.get("material"):                         # 예전 이름('계산 재료')을 쉬운 말로 (2026-11-21 사용자 요청)
            x["name"] = re.sub(r"의 계산 재료 ", "에 쓴 ", x.get("name", ""))
            x["reason"] = x.get("reason", "").replace("계산식에 넣은 재료", "계산에 쓴 숫자")
            if isinstance(x.get("source"), dict) and x["source"].get("api"):
                x["source"]["api"] = x["source"]["api"].replace("계산 재료와", "계산에 쓴 숫자와")
        keep.append(x)
    ext["results"] = keep
    return gone

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
