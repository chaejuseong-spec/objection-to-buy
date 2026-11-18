"""표 검산. AI를 쓰지 않고 코드가 계산한다.

표는 추출 담당이 옮기고, 코드가 값마다 그 쪽 원문에 글자로 있는지 이미 대조했다(extract.verify).
여기서는 그렇게 확인된 표의 칸을 '숫자 한 줄'(가상 data_row)로 바꿔 고정 산식 검산(formula_check)에 넣는다.
그러면 따로 만들지 않아도 아래가 된다.
  - 표 안의 영업이익률·순이익률 = 영업이익·순이익 ÷ 매출액
  - 같은 항목·같은 기간이 다른 표나 본문에 다르게 적혀 있는지 (쪽마다 다름)
그리고 이 파일에서 증감률(YoY) 행을 직접 검산한다: 올해 ÷ 작년 − 1.

가상 줄의 id는 'T-1:3:2'(표 id : 행 번호 : 열 번호) 모양이라 AI가 쓰는 R-# 와 겹치지 않는다.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from .number_check import MATCH, MISMATCH
from .schemas import DataRow, Report

_GROWTH = re.compile(r"YoY|증감률|증가율|성장률|growth", re.I)
_ANNUAL = re.compile(r"^(20\d\d)([EeFfPp])?$")
_DROP = re.compile(r"\((손실|손익|적자)\)")       # '영업이익(손실)' -> '영업이익'


def _company(title: str, name: str) -> str:
    """표 제목 괄호 안의 회사 이름 ('추정 연결포괄손익계산서 (가상식품)' -> '가상식품'). 리포트 대상 회사면 빈 글자"""
    for m in re.finditer(r"\(([^()]{1,12})\)", title or ""):
        t = m.group(1).strip()
        if re.search(r"단위|\d|%|원|배|주", t):
            continue
        return "" if t.replace(" ", "") == (name or "").replace(" ", "") else t
    return ""


def _fs(title: str) -> str:
    return "연결" if "연결" in (title or "") else "별도" if "별도" in (title or "") else ""


def _show(v) -> str:
    """표의 값을 리포트에 적히는 모양으로 (천 단위 쉼표). 소수 자릿수는 그대로 둔다"""
    if isinstance(v, bool) or v is None:
        return ""
    if isinstance(v, int) or (isinstance(v, float) and v.is_integer()):
        return f"{int(v):,}"
    return str(v)


def virtual_rows(report: Report) -> List[DataRow]:
    """확인된 표의 칸마다 숫자 한 줄. 이름은 '회사 연결/별도 행이름 기간'"""
    out = []
    for t in report.tables:
        title, years = t.get("title", ""), t.get("years", [])
        head = " ".join(x for x in (_company(title, report.meta.name), _fs(title)) if x)
        for i, row in enumerate(t.get("rows", [])):
            name = _DROP.sub("", row.get("name", "")).strip()
            if not name or _GROWTH.search(name):           # 증감률 행은 아래 growth_checks 가 따로 본다
                continue
            for j, (year, v) in enumerate(zip(years, row.get("values", []))):
                val = _show(v)
                if not val:
                    continue
                out.append(DataRow(rid=f"{t.get('id', 'T')}:{i}:{j}", item=f"{head} {name} {year}".strip(), value=val,
                                   unit=row.get("unit", ""), basis="리포트 표", as_of=str(year),
                                   page=t.get("page"), location=t.get("title", "")))
    return out


def all_rows(report: Report) -> Dict[str, DataRow]:
    """AI가 옮긴 숫자 + 표의 칸. 형광펜과 품질 평가가 id로 숫자를 찾을 때 쓴다"""
    return {**{r.rid: r for r in virtual_rows(report)}, **{r.rid: r for r in report.data_rows}}


def _decimals(v) -> int:
    s = str(v)
    return len(s.split(".")[1]) if "." in s else 0


def growth_checks(report: Report, start: int = 1) -> List[dict]:
    """증감률(YoY) 행 검산: 바로 위 금액 행의 올해 ÷ 작년 − 1.
    연도가 이어진 연간 열끼리만 본다(분기 열, 건너뛴 연도는 빼고). 작년 값이 0 이하(적자)면 증감률의 뜻이 달라 보지 않는다.
    허용: 표시 자릿수 반올림 (소수 한 자리면 0.1%p 이내)."""
    out = []
    for t in report.tables:
        years, rows = t.get("years", []), t.get("rows", [])
        for i, row in enumerate(rows):
            if not _GROWTH.search(row.get("name", "")) or i == 0:
                continue
            base = rows[i - 1]
            if _GROWTH.search(base.get("name", "")) or base.get("kind") == "ratio":
                continue
            for j in range(1, min(len(years), len(row.get("values", [])), len(base.get("values", [])))):
                a, b = _ANNUAL.match(str(years[j - 1])), _ANNUAL.match(str(years[j]))
                prev, cur, stated = base["values"][j - 1], base["values"][j], row["values"][j]
                if not (a and b) or int(b.group(1)) - int(a.group(1)) != 1:
                    continue
                if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (prev, cur, stated)) or prev <= 0:
                    continue
                calc = (cur / prev - 1) * 100
                tol = 0.5 * 10 ** -_decimals(stated) + 1e-9 + 0.05       # 반올림 + 원값이 반올림된 몫
                ok = abs(calc - stated) <= tol
                bname = _DROP.sub("", base.get("name", "")).strip()
                out.append({"check_id": f"G-{start + len(out)}", "kind": "formula",
                            "name": f"{bname} {years[j]} 증감률 = 올해 ÷ 작년 − 1", "expr": "",
                            "stated_rid": f"{t.get('id')}:{i}:{j}g", "rids": [f"{t.get('id')}:{i - 1}:{j}", f"{t.get('id')}:{i - 1}:{j - 1}"],
                            "note": f"{t.get('title', '')}: {bname} {years[j - 1]} {_show(prev)} → {years[j]} {_show(cur)}",
                            "status": MATCH if ok else MISMATCH, "computed": round(calc, 2), "stated": f"{stated}%",
                            "diff_pct": round(abs(calc - stated), 2), "pages": [t.get("page")] if t.get("page") else [],
                            "reason": "" if ok else f"표의 {bname}로 다시 계산하면 {calc:.1f}%인데 {stated}%로 적혀 있습니다"})
    return out
