"""근거 종합표. 주장마다 어떤 숫자에 기대고, 그 숫자가 무엇으로 확인됐는지를 코드가 정리한다. AI를 쓰지 않는다.

숫자 하나의 성격
- 사실   : 지난 기간의 실적, 시세, 공시처럼 이미 일어난 값
- 계산   : 다른 숫자로 계산한 값 (상승여력, 비율, 배수)
- 추정   : 2026E 처럼 앞으로의 값, 전망·예상
- 가정   : 리포트가 '가정'이라고 밝힌 값 (환율 가정, 목표 배당성향 등)

숫자 하나의 검증 상태
- 공식 일치   : 공시·시세·공식 통계와 같다
- 계산 일치   : 리포트의 다른 숫자로 다시 계산해 같다
- 불일치      : 공식 데이터나 다시 계산한 값과 다르다
- 검증 불가   : 대조했지만 기준을 특정할 수 없었다
- 근거 미확인 : 대조할 수단이 없었다 (틀렸다는 뜻이 아니다)

주장의 근거 상태 (숫자 기준. 투자 논리가 옳다는 뜻이 아니다)
- 실제 수치 불일치 / 근거 확인 / 일부 확인 / 근거 미확인 / 숫자 근거 없음
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from .number_check import MATCH, MISMATCH, _show
from .schemas import Report

OFFICIAL = {"dart", "krx", "ecos", "kosis"}
_RID = re.compile(r"\b[RM]-\d+\b")
_EST = re.compile(r"(20\d\d|\d\d)\s*[EeFf](?![a-z])|전망|예상|추정|목표|컨센서스|가이던스")
_ASSUME = re.compile(r"가정|적용")
_CALC = re.compile(r"상승\s*여력|률|율|성향|비율|비중|배수|\bPER\b|\bPBR\b|\bROE\b|\bROA\b|\bEPS\b|\bBPS\b|할인율|프리미엄|YoY|증감")
_FUTURE = re.compile(r"것이다|것으로\s*(보인다|예상|전망|판단)|전망|예상|기대|가능성|할\s*것|될\s*것|목표")


def involved(check: dict) -> set:
    return {check.get("stated_rid")} | set(check.get("rids") or []) | set(_RID.findall(check.get("expr") or "")) - {None}


def nature(row) -> str:
    text = " ".join(x or "" for x in (row.item, row.as_of, row.basis))
    if "가정" in text or (_ASSUME.search(text) and _EST.search(text)):
        return "가정"
    if _EST.search(text):
        return "추정"
    if _CALC.search(row.item or "") or (row.unit or "").strip() in ("%", "%p", "배", "x"):
        return "계산"
    return "사실"


def number_status(checks: List[dict]) -> str:
    if any(c["status"] == MISMATCH for c in checks):
        return "불일치"
    if any(c["status"] == MATCH and c.get("kind") in OFFICIAL for c in checks):
        return "공식 일치"
    if any(c["status"] == MATCH for c in checks):
        return "계산 일치"
    if checks:
        return "검증 불가"
    return "근거 미확인"


def _check_view(c: dict) -> dict:
    src = c.get("source") or {}
    return {"check_id": c["check_id"], "kind": c.get("kind") or "recompute", "name": c["name"], "status": c["status"],
            "computed": c.get("computed"), "reason": c.get("reason", ""), "source": src.get("short", ""), "url": src.get("url", ""),
            "fetched_at": src.get("fetched_at", ""), "timing": src.get("timing", "")}


def build(report: Report, result: dict) -> dict:
    """돌려주는 값: {'claims': [주장별 근거 정리], 'count': {근거 상태: 개수}, 'note'}"""
    rows = {r.rid: r for r in report.data_rows}
    sources = {s.sid: s for s in report.sources}
    checks = result["numbers"]["results"]
    by_rid: Dict[str, List[dict]] = {}
    for c in checks:
        for rid in involved(c):
            by_rid.setdefault(rid, []).append(c)
    shown = {c["id"]: c for c in result.get("claims", [])}
    todo_of: Dict[str, List[str]] = {}
    for it in result.get("checklist", []):
        todo_of.setdefault(it.get("claim_id"), []).append(it["what"])

    out = []
    for i, c in enumerate(report.claims, 1):
        nums = []
        for e in c.evidence:
            r = rows.get(e.ref_id)
            if not r:
                continue
            cs = by_rid.get(r.rid, [])
            src = sources.get(r.source)
            own = bool(src) and any(k and k in (src.title or "") for k in (report.meta.name, report.meta.ticker, "추정", "Research Report"))
            nums.append({"rid": r.rid, "item": r.item, "value": _show(r.value, r.unit), "page": r.page, "nature": nature(r),
                         "status": number_status(cs), "checks": [_check_view(x) for x in cs],
                         "report_source": "" if own else ((src.title if src else "") or ""), "own_source": own})
        status = _claim_status(nums)
        conf, why = _confidence(nums, status)
        sc = shown.get(c.claim_id, {})
        objs = sc.get("objections", [])
        kinds = sorted({n["nature"] for n in nums} | ({"해석"} if any(o.get("kind") == "해석 차이" for o in objs) or not nums else set())
                       | ({"추정"} if _FUTURE.search(c.text) else set()), key=["사실", "계산", "추정", "가정", "해석"].index)
        todo = []
        for n in nums:
            if n["status"] == "불일치":
                bad = next(x for x in n["checks"] if x["status"] == MISMATCH)
                todo.append(f"{n['item']} {n['value']}({n['page']}쪽): {bad['name']} 결과와 다릅니다. 원문과 공식 자료를 직접 확인하세요.")
            elif n["status"] == "검증 불가":
                todo.append(f"{n['item']} {n['value']}({n['page']}쪽): 대조했지만 기준을 특정하지 못했습니다. {n['checks'][0]['reason'][:80]}")
            elif n["status"] == "근거 미확인" and n["nature"] != "추정":
                if n["own_source"]:
                    how = "리포트 작성자의 자체 자료·추정이라 바깥 자료로 확인할 수 없습니다. 계산 근거가 리포트에 있는지 보세요."
                elif n["report_source"]:
                    how = f"리포트가 밝힌 출처({n['report_source']})를 직접 확인하세요."
                else:
                    how = "리포트가 출처를 밝히지 않았습니다. 출처를 직접 찾아 확인하세요."
                todo.append(f"{n['item']} {n['value']}({n['page']}쪽): 대조할 공식 자료가 없었습니다. {how}")
        todo += todo_of.get(c.claim_id, [])
        out.append({"claim_id": c.claim_id, "no": i, "text": c.text, "page": c.page, "is_core": c.is_core,
                    "kinds": kinds, "numbers": nums, "status": status, "confidence": conf, "confidence_why": why,
                    "logic_verdict": sc.get("verdict"), "objections": len(objs),
                    "split": sum(1 for o in objs if o.get("split")), "todo": todo})
    count: Dict[str, int] = {}
    for x in out:
        count[x["status"]] = count.get(x["status"], 0) + 1
    return {"claims": out, "count": count,
            "note": "근거 상태는 주장에 쓰인 숫자가 맞는지만 본 것입니다. 숫자가 맞아도 투자 논리가 옳다는 뜻은 아닙니다. 논리는 반대신문 판정을 보세요."}


def _claim_status(nums: List[dict]) -> str:
    if not nums:
        return "숫자 근거 없음"
    st = [n["status"] for n in nums]
    if "불일치" in st:
        return "실제 수치 불일치"
    good = ("공식 일치", "계산 일치")
    checkable = [n for n in nums if n["nature"] != "추정"] or nums      # 추정치는 확인 대상이 아니다 (모두 추정이면 그대로 본다)
    ok = [n for n in checkable if n["status"] in good]
    if ok and len(ok) == len(checkable):
        return "근거 확인"
    if ok or any(s in good for s in st):
        return "일부 확인"
    return "근거 미확인"


def _confidence(nums: List[dict], status: str):
    """숫자 근거의 신뢰도. 높음/보통/낮음과 그 이유"""
    if status == "실제 수치 불일치":
        return "낮음", "근거 숫자 가운데 공식 자료나 다시 계산한 값과 다른 것이 있습니다."
    if status == "숫자 근거 없음":
        return "낮음", "숫자 근거 없이 서술로만 된 주장입니다."
    est = sum(n["nature"] in ("추정", "가정") for n in nums)
    official = sum(n["status"] == "공식 일치" for n in nums)
    calc = sum(n["status"] == "계산 일치" for n in nums)
    tail = f" 추정·가정 숫자 {est}개는 확인할 수 없습니다." if est else ""
    if status == "근거 확인" and official:
        return ("보통" if est else "높음"), f"확인할 수 있는 근거 숫자 {official + calc}개가 모두 공식 자료나 계산으로 확인됐습니다.{tail}"
    if status == "근거 확인":
        return "보통", f"확인할 수 있는 근거 숫자 {calc}개가 모두 리포트 안의 다른 숫자로 다시 계산해 맞았습니다. 공식 자료와는 대조하지 못했습니다.{tail}"
    if status == "일부 확인":
        return "보통", f"근거 숫자 {len(nums)}개 중 {official + calc}개가 확인됐습니다" + (" (공식 자료가 아닌 계산으로만)" if not official else "") + f".{tail}"
    return "낮음", f"근거 숫자 {len(nums)}개 가운데 확인된 것이 없습니다" + (f" (추정·가정 {est}개)" if est else "") + "."


def mark_split(result: dict, n_jurors: Optional[int] = None) -> None:
    """배심원 의견이 크게 갈린 이의에 split=True. 판단한 표(동의·비동의)가 3표 이상이고 두 쪽 차이가 1표 이하일 때.
    ('판단 불가'는 판단을 맡긴 표라 갈림에 넣지 않는다. rules.objection_verdict 와 같은 기준)"""
    for c in result.get("claims", []):
        for o in c.get("objections", []):
            t = o.get("tally") or {}
            if "동의" in t:
                a, b = t["동의"], t.get("비동의", 0)
            else:                                    # 예전 결과: 과장은 동의(하)라 비동의 표가 없다
                a, b = t.get("타당", 0) + t.get("과장", 0), 0
            o["split"] = bool(a + b >= 3 and abs(a - b) <= 1)
