"""숫자 검증. AI를 쓰지 않고 코드가 계산한다.

리포트 안의 숫자끼리 맞는지만 본다(공통 엔진이 붙기 전까지 외부 대조는 없음).
결과: 일치 / 불일치 / 확인 불가
"""
from __future__ import annotations
import ast
import operator
import re
from typing import Dict, List, Optional

from .schemas import Report

MATCH, MISMATCH, UNKNOWN = "일치", "불일치", "확인 불가"
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.USub: operator.neg}


_NUM = r"\d+(?:\.\d+)?"
_MAG = {"조": 1e12, "억": 1e8, "만": 1e4}
_UNITS = ("%p", "%", "원", "배", "x", "X", "주", "달러")


def parse_number(text: str, unit: str = "") -> Optional[float]:
    """리포트에 적힌 숫자 하나를 읽는다. 값은 unit(단위 칸) 기준이다.
    '9,690' -> 9690, '90,200 원' -> 90200, '54.7%' -> 54.7,
    '1 조 8,367 억 원'(단위 칸 '원') -> 1836700000000 (값 안의 조·억·만은 곱한다).
    '약 70', '60~65', '4 이상'처럼 하나의 숫자가 아니면 None."""
    t = re.sub(r"[\s,]", "", text or "")
    u = re.sub(r"\s", "", unit or "")
    if re.search(r"\d[조억만]", t):                         # '48조9,000억원'처럼 값 안에 조·억·만이 있으면
        mag = _unit_mag(u)                                 # 원 단위로 읽은 뒤 단위 칸(억원 등) 기준으로 바꾼다
        won = _parse(re.sub(r"(원|주|달러)$", "", t), "") if mag else None
        if won is not None:
            return won / mag
    return _parse(t, u)


def _parse(t: str, u: str) -> Optional[float]:
    if u and t.endswith(u) and len(t) > len(u):          # 단위 칸과 같은 글자가 값 끝에 붙어 있으면 뗀다
        t = t[: -len(u)]
    for x in _UNITS:                                       # 흔한 단위 하나를 뗀다
        if t.endswith(x) and len(t) > len(x):
            t = t[: -len(x)]
            break
    sign = -1.0 if t.startswith("-") else 1.0
    t = t.lstrip("-")
    if re.fullmatch(_NUM, t):
        return sign * float(t)
    m = re.fullmatch(rf"(?:({_NUM})조)?(?:({_NUM})억)?(?:({_NUM})만)?({_NUM})?", t)
    if m and any(m.group(i) for i in (1, 2, 3)):           # 조·억·만이 하나라도 있어야 한다
        total = sum(float(g) * mag for g, mag in zip(m.groups()[:3], _MAG.values()) if g)
        return sign * (total + float(m.group(4) or 0))
    return None


def _unit_mag(unit: str) -> Optional[float]:
    """단위 칸의 크기. '억원' -> 1e8, '십억원' -> 1e9, '백만원' -> 1e6, '원'·'주'·'' -> 1. 모르는 단위면 None"""
    m = re.fullmatch(r"([십백천]?)([만억조]?)(원|주|달러)?", unit or "")
    if not m:
        return None
    return {"": 1, "십": 10, "백": 100, "천": 1000}[m.group(1)] * {"": 1, "만": 1e4, "억": 1e8, "조": 1e12}[m.group(2)]


def _show(value: str, unit: str) -> str:
    """화면에 보일 값. 값 끝에 이미 단위 글자가 붙어 있으면 더 붙이지 않는다 ('54.7%%', '1조 8,367억 원조 원' 방지)."""
    v, u = (value or "").strip(), (unit or "").strip()
    return v if not u or not v or not v[-1].isdigit() else f"{v}{u}"


def _eval(node, env: Dict[str, float]) -> float:
    if isinstance(node, ast.Expression):
        return _eval(node.body, env)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.Name):
        return env[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.left, env), _eval(node.right, env))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand, env))
    raise ValueError("허용하지 않는 식")


def safe_eval(expr: str, values: Dict[str, float]) -> float:
    """사칙연산만 계산한다. rid의 '-'는 '_'로 바꿔 변수로 쓴다 (R-04 -> R_04)."""
    env = {k.replace("-", "_"): v for k, v in values.items()}
    py = re.sub(r"\b([RM])-(\d+)\b", r"\1_\2", expr)
    return _eval(ast.parse(py, mode="eval"), env)


def run_checks(report: Report) -> dict:
    rows = {r.rid: r for r in report.data_rows}
    nums = {rid: parse_number(r.value, r.unit) for rid, r in rows.items()}
    results: List[dict] = []
    for c in report.checks:
        res = {"check_id": c.check_id, "name": c.name, "kind": c.kind, "expr": c.expr, "stated_rid": c.stated,
               "note": c.note, "status": UNKNOWN, "computed": None, "stated": None, "diff_pct": None,
               "pages": [], "reason": ""}
        try:
            if c.kind == "same":
                a, b = rows[c.compare[0]], rows[c.compare[1]]
                res["stated"] = f"{_show(a.value, a.unit)} ({a.page}쪽) / {_show(b.value, b.unit)} ({b.page}쪽)"
                res["pages"] = [a.page, b.page]
                res["rids"] = list(c.compare)
                same = a.value.replace(" ", "") == b.value.replace(" ", "")
                res["status"] = MATCH if same else MISMATCH
                res["reason"] = "" if same else "같은 항목의 표기가 서로 다르다"
            else:
                used = set(re.findall(r"\b[RM]-\d+\b", c.expr))
                missing = [u for u in used | {c.stated} if nums.get(u) is None]
                st = rows[c.stated]
                res["pages"] = [st.page]
                res["rids"] = [c.stated]
                res["stated"] = _show(st.value, st.unit)
                if missing:
                    res["reason"] = "숫자 하나로 읽을 수 없는 값: " + ", ".join(sorted(missing))
                else:
                    val = safe_eval(c.expr, {k: v for k, v in nums.items() if v is not None})
                    stated = nums[c.stated]
                    pct = (st.unit.strip() in ("%", "%p") or st.value.strip().endswith("%"))

                    def gap(x: float) -> float:
                        return abs(x - stated) / abs(stated) * 100 if stated else abs(x)
                    diff, note = gap(val), ""
                    if pct and gap(val * 100) < diff:    # 식에 ×100이 빠진 백분율 (0.547 과 54.7%)
                        val, diff, note = val * 100, gap(val * 100), "식에 ×100이 빠져 있어 백분율로 맞춰 비교했다"
                    res.update(computed=round(val, 2), diff_pct=round(diff, 2),
                               status=MATCH if diff <= c.tolerance_pct else MISMATCH)
                    res["reason"] = note
                    if res["status"] == MISMATCH:
                        res["reason"] = f"다시 계산한 값과 {diff:.2f}% 차이 (허용 {c.tolerance_pct}%)" + (f". {note}" if note else "")
        except (KeyError, IndexError, ValueError, ZeroDivisionError, SyntaxError) as e:
            res["reason"] = f"계산할 수 없음: {type(e).__name__}"
        results.append(res)
    checked = {r for res in results for r in res.get("rids", [])}
    count = {s: sum(1 for r in results if r["status"] == s) for s in (MATCH, MISMATCH, UNKNOWN)}
    return {"results": results, "count": count, "n_numbers": len(rows),
            "n_unchecked": len([r for r in rows if r not in checked]),
            "scope": "리포트 안의 숫자끼리만 대조" if not report.external_data else "외부 자료 포함"}
