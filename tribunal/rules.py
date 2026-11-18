"""코드가 정하는 규칙. AI가 고르지 않는다.

- 없는 근거 id 지우기
- 표 집계 -> 이의별 판정 -> 주장별 판정
- 리포트 평가
- 직접 확인할 것
"""
from __future__ import annotations
import re
from collections import Counter
from typing import Dict, List, Optional

from .schemas import Objection, Report, Vote

MODIFY, CHECK, HOLD, KEEP = "수정", "확인 필요", "판정 보류", "유지"
SEVERITY = {MODIFY: 3, CHECK: 2, HOLD: 1, KEEP: 0}
RETRIAL, CONDITIONAL, PASS = "재심리", "조건부 통과", "통과"


def valid_ids(report: Report) -> set:
    """입력에 실제로 있는 근거 id 모음 (rid, M#, S#)."""
    ids = {r.rid for r in report.data_rows}
    ids |= {m.mid for m in report.metrics}
    ids |= {s.sid for s in report.sources}
    return ids


MAX_OBJECTIONS = 30      # 기준이 아니라 폭주를 막는 안전 상한


def find_quote(quote: str, page: Optional[int], pages: List[dict]) -> Optional[int]:
    """인용문이 원문에 있는 쪽. 띄어쓰기·줄바꿈은 무시한다. 적힌 쪽을 먼저 보고, 없으면 전체에서 찾는다. 못 찾으면 None"""
    from .pdf_locate import norm
    q = norm(quote or "")
    if len(q) < 6:                       # 너무 짧은 인용은 어디에나 있을 수 있어 확인으로 치지 않는다
        return None
    order = sorted(pages, key=lambda p: p["page"] != page)
    return next((p["page"] for p in order if q in norm(p["text"])), None)


def filter_counter_ids(objections: List[Objection], report: Report, pages: Optional[List[dict]] = None,
                       limit: int = MAX_OBJECTIONS) -> List[Objection]:
    """이의를 코드로 정리한다.
    - 입력에 없는 반대 근거 id는 지운다.
    - 없는 주장 id는 '관련 주장 없음'으로 바꾼다 (원문 전체에서 찾은 이의는 주장과 묶이지 않을 수 있다).
    - 원문(pages)이 있으면 인용문이 실제로 있는지 대조한다. 쪽이 틀렸으면 맞는 쪽으로 고친다.
    - 근거 숫자도 없고 인용도 원문에 없으면 '근거 없는 이의'라 버린다. 원문이 없을 때(JSON 입력)는 예전처럼 남긴다.
    - 남은 근거(숫자 id 또는 확인된 인용)가 없으면 '근거 없음'으로 표시한다(타당 과반이어도 확인 필요까지만).
    - 안전 상한(30개)을 넘는 이의는 버린다."""
    ok = valid_ids(report)
    claim_ids = {c.claim_id for c in report.claims}
    out = []
    for o in objections:
        kept = [i for i in o.counter_ids if i in ok]
        removed = [i for i in o.counter_ids if i not in ok]
        found = find_quote(o.quote, o.page, pages) if pages else None
        if pages and not kept and found is None:
            continue
        out.append(o.model_copy(update={
            "claim_id": o.claim_id if o.claim_id in claim_ids else None,
            "page": found or o.page, "quote_found": found is not None,
            "counter_ids": kept, "removed_ids": removed, "no_evidence": not kept and found is None}))
    return out[:limit]


def majority(n_jurors: int) -> int:
    """과반 = 배심원 수의 절반을 넘는 표 (5명이면 3, 3명이면 2)."""
    return n_jurors // 2 + 1


MIN_JUDGED = 3      # '판단한 표'(타당·과장)가 이보다 적으면 확정하지 않는다 (두 명이 리포트 평가를 바꾸지 못하게)


def judged_votes(n_jurors: int) -> int:
    """판정을 확정하는 데 필요한 '판단한 표'의 최소 수 (5명이면 3, 3명이면 3)"""
    return min(MIN_JUDGED, n_jurors)


def objection_verdict(o: Objection, votes: List[Optional[Vote]], n_jurors: int) -> dict:
    """이의 하나의 판정. votes 안의 None은 기권(호출 실패).
    '확인 불가'는 '내 분야 밖이라 판단을 맡긴다'는 표로 보고 판정을 막지 않는다(2026-11-17).
    판단한 표(타당+과장)가 3표 이상일 때 그 가운데 과반으로 정한다.
    - 낸 표(기권 제외)가 과반에 못 미침 -> 판정 보류
    - 판단한 표가 3표 미만 -> 확인 필요 (전문가 몇 명만 판단할 수 있는 이의는 사람이 본다)
    - 판단한 표 가운데 타당이 과반 -> 수정 (근거 없으면 확인 필요) / 과장이 과반 -> 유지 / 같으면 확인 필요"""
    valid = [v for v in votes if v is not None]
    count = Counter(v.vote for v in valid)
    judged = count["타당"] + count["과장"]
    if o.added_by:                       # 배심원이 추가한 이의: 표가 없으므로 확인 필요까지만
        verdict = CHECK
    elif len(valid) < majority(n_jurors):
        verdict = HOLD
    elif judged < judged_votes(n_jurors):
        verdict = CHECK
    elif count["타당"] * 2 > judged:
        verdict = CHECK if o.no_evidence else MODIFY
    elif count["과장"] * 2 > judged:
        verdict = KEEP
    else:
        verdict = CHECK
    return {
        "objection_id": o.objection_id, "claim_id": o.claim_id, "verdict": verdict,
        "tally": {"타당": count["타당"], "과장": count["과장"], "확인 불가": count["확인 불가"],
                  "기권": n_jurors - len(valid) if not o.added_by else 0},
    }


def claim_verdicts(report: Report, obj_verdicts: List[dict]) -> Dict[str, str]:
    """주장별 판정: 이의가 없으면 유지, 여러 개면 가장 엄한 판정."""
    result = {c.claim_id: KEEP for c in report.claims}
    for ov in obj_verdicts:
        cid = ov["claim_id"]
        if cid not in result:              # 주장과 묶이지 않은 이의는 리포트 평가에서 따로 본다
            continue
        if SEVERITY[ov["verdict"]] > SEVERITY[result[cid]]:
            result[cid] = ov["verdict"]
    return result


def unlinked(obj_verdicts: Optional[List[dict]]) -> List[str]:
    """주장과 묶이지 않은 이의의 판정들"""
    return [ov["verdict"] for ov in (obj_verdicts or []) if not ov.get("claim_id")]


def report_grade(report: Report, cverdicts: Dict[str, str], other: Optional[List[str]] = None) -> str:
    """핵심 주장에 수정 -> 재심리. 그 밖의 수정·확인 필요·판정 보류(주장과 묶이지 않은 이의 포함) -> 조건부 통과."""
    core = {c.claim_id for c in report.claims if c.is_core}
    if any(v == MODIFY and cid in core for cid, v in cverdicts.items()):
        return RETRIAL
    if any(v in (MODIFY, CHECK, HOLD) for v in list(cverdicts.values()) + list(other or [])):
        return CONDITIONAL
    return PASS


def checklist(report: Report, objections: List[Objection], obj_verdicts: List[dict], limit: int = 5) -> List[dict]:
    """직접 확인할 것: '확인 필요'(판정 보류 포함)와 핵심 주장의 '수정'에서 최대 5개.
    검증 항목(사실 오류)이 분석 항목(해석 차이)보다 앞."""
    core = {c.claim_id for c in report.claims if c.is_core}
    sids = {s.sid for s in report.sources}
    by_id = {o.objection_id: o for o in objections}
    row_source = {r.rid: r.source for r in report.data_rows}
    items = []
    for ov in obj_verdicts:
        o = by_id[ov["objection_id"]]
        pick = ov["verdict"] in (CHECK, HOLD) or (ov["verdict"] == MODIFY and o.claim_id in core)
        if not pick:
            continue
        srcs = sorted({i for i in o.counter_ids if i in sids} |
                      {row_source[i] for i in o.counter_ids if row_source.get(i) in sids})
        items.append({
            "what": o.text, "why": o.objection_id, "claim_id": o.claim_id,
            "type": "검증" if o.kind == "사실 오류" else "분석",
            "sources": srcs, "verdict": ov["verdict"],
        })
    items.sort(key=lambda x: (x["type"] != "검증", -SEVERITY[x["verdict"]]))
    return items[:limit]


def evaluate(report: Report, objections: List[Objection], votes_by_juror: Dict[str, Optional[List[Vote]]],
             n_jurors: int) -> dict:
    """집계부터 리포트 평가까지 한 번에. votes_by_juror 값이 None이면 그 배심원은 기권."""
    obj_verdicts = []
    for o in objections:
        votes = []
        for juror, vs in votes_by_juror.items():
            if vs is None:
                votes.append(None)
                continue
            found = next((v for v in vs if v.objection_id == o.objection_id), None)
            votes.append(found)   # 그 이의에 표를 안 낸 경우도 기권
        obj_verdicts.append(objection_verdict(o, votes, n_jurors))
    cv = claim_verdicts(report, obj_verdicts)
    return {
        "n_jurors": n_jurors, "majority": majority(n_jurors),
        "objection_verdicts": obj_verdicts, "claim_verdicts": cv,
        "report_grade": report_grade(report, cv, unlinked(obj_verdicts)),
        "checklist": checklist(report, objections, obj_verdicts),
    }


# ---------- 코드 검증(공식 데이터, 다시 계산) 결과를 판정에 반영 ----------
OFFICIAL_KINDS = {"dart", "krx", "ecos", "kosis"}
_RID = re.compile(r"\b[RM]-\d+\b")


def _rids(check: dict) -> set:
    """판정을 옮길 숫자: 확인한 그 값(stated). 계산에 들어간 입력값은 틀렸다고 단정할 수 없어 넣지 않는다.
    쪽마다 다른 표기를 비교한 검증은 비교한 두 값 모두."""
    rel = {check.get("stated_rid")}
    if check.get("kind") == "same" or "쪽마다" in (check.get("name") or ""):
        rel |= set(check.get("rids") or []) | set(check.get("compare") or [])
    return rel - {None}


def code_verdict(check: dict) -> Optional[str]:
    """숫자 검증 한 건이 불일치일 때 주장에 줄 판정.
    - 공식 데이터(공시·시세·통계)와 다르고 기준이 분명함 -> 수정
    - 연결/별도를 표기가 아니라 값으로 짐작해 판정함 -> 확인 필요
    - 리포트 안의 숫자끼리 안 맞음(다시 계산, 쪽마다 다른 표기) -> 확인 필요 (어느 쪽이 틀렸는지 코드는 모른다)"""
    if check.get("status") != "불일치":
        return None
    if check.get("kind") in OFFICIAL_KINDS:
        basis = check.get("basis") or {}
        return CHECK if basis.get("how") in ("가까운 값", "같은 표") or basis.get("year") == "추정" else MODIFY
    return CHECK


def code_findings(report: Report, number_results: List[dict]) -> Dict[str, List[dict]]:
    """주장마다, 그 주장이 근거로 쓴 숫자에서 나온 불일치. {claim_id: [{check_id, name, verdict, reason}]}"""
    out: Dict[str, List[dict]] = {}
    for c in report.claims:
        mine = {e.ref_id for e in c.evidence}
        for x in number_results:
            v = code_verdict(x)
            if v and _rids(x) & mine:
                out.setdefault(c.claim_id, []).append({"check_id": x["check_id"], "name": x.get("name", ""),
                                                       "verdict": v, "reason": x.get("reason", ""), "kind": x.get("kind") or "recompute"})
    return out


def apply_code_findings(report: Report, ev: dict, findings: Dict[str, List[dict]], limit: int = 5) -> dict:
    """배심원 판정과 코드 검증 가운데 더 엄한 쪽을 주장 판정으로 쓴다. 리포트 평가와 직접 확인할 것도 다시 정한다."""
    if not findings:
        return dict(ev, code_findings={})
    cv = dict(ev["claim_verdicts"])
    for cid, fs in findings.items():
        worst = max((f["verdict"] for f in fs), key=lambda v: SEVERITY[v])
        if SEVERITY[worst] > SEVERITY[cv.get(cid, KEEP)]:
            cv[cid] = worst
    core = {c.claim_id for c in report.claims if c.is_core}
    items = [{"what": f"{f['name']}: {f['reason']}", "why": f["check_id"], "claim_id": cid, "type": "검증",
              "sources": [], "verdict": f["verdict"]}
             for cid, fs in findings.items() for f in fs if f["verdict"] == CHECK or cid in core]
    seen = {i["why"] for i in items}
    merged = items + [i for i in ev["checklist"] if i["why"] not in seen]
    merged.sort(key=lambda x: (x["type"] != "검증", -SEVERITY[x["verdict"]]))
    return dict(ev, claim_verdicts=cv, report_grade=report_grade(report, cv, unlinked(ev.get("objection_verdicts"))),
                checklist=merged[:limit], code_findings=findings)


def refresh_result(report: Report, result: dict) -> None:
    """이미 끝난 분석(result.json)에 코드 검증 판정을 다시 반영한다. 배심원 판정은 이의별 판정에서 다시 모은다."""
    vote_cv = {}
    for c in result.get("claims", []):
        vs = [o["verdict"] for o in c.get("objections", [])]
        vote_cv[c["id"]] = max(vs, key=lambda v: SEVERITY[v]) if vs else KEEP
    base = {"claim_verdicts": vote_cv, "checklist": [i for i in result.get("checklist", []) if not str(i.get("why", "")).startswith(("D-", "E-", "X-", "F-", "K-"))]}
    ev = apply_code_findings(report, base, code_findings(report, result["numbers"]["results"]))
    for c in result.get("claims", []):
        c["verdict"] = ev["claim_verdicts"][c["id"]]
        c["code"] = ev["code_findings"].get(c["id"], [])
    cv = ev["claim_verdicts"]
    core_mod = [c.claim_id for c in report.claims if c.is_core and cv.get(c.claim_id) == MODIFY]
    other = [o["verdict"] for o in result.get("other_objections", [])]
    result["grade"] = {"value": report_grade(report, cv, other),
                       "why": (f"투자의견 핵심 주장에 '수정' 판정이 있습니다: {', '.join(core_mod)}" if core_mod else "핵심 주장에는 '수정' 판정이 없습니다.")}
    result.setdefault("logic", {})["count"] = {"수정": sum(v == MODIFY for v in cv.values()),
                                               "확인 필요": sum(v in (CHECK, HOLD) for v in cv.values()),
                                               "유지": sum(v == KEEP for v in cv.values())}
    result["checklist"] = ev["checklist"]
    result["code_findings"] = ev["code_findings"]
