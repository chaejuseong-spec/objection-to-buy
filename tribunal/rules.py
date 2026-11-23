"""코드가 정하는 규칙. AI가 고르지 않는다.

- 없는 근거 id 지우기
- 표 집계 -> 이의별 판정 -> 주장별 판정
- 리포트 평가
- 투자 전에 직접 확인할 사항
"""
from __future__ import annotations
import re
from collections import Counter
from typing import Dict, List, Optional

from .schemas import Objection, Report, Vote

MODIFY, CHECK, HOLD, KEEP = "수정", "확인 필요", "판정 보류", "유지"
SEVERITY = {MODIFY: 3, CHECK: 2, HOLD: 1, KEEP: 0}
RETRIAL, INCOMPLETE, CONDITIONAL, PASS = "재검토 필요", "판정 불완전", "조건부 통과", "통과"
AGREE, DISAGREE, UNSURE = "동의", "비동의", "판단 불가"
NOT_ACCEPTED, MINOR = "이의 불인정", "경미한 지적"      # 유지의 사유
IMP_RANK = {"상": 0, "중": 1, None: 2, "하": 3}            # 확인 사항 정렬 (미확인은 중과 하 사이)


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
    - 입력에 없는 근거 id는 지운다.
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


MIN_JUDGED = 3      # '판단한 표'(동의·비동의)가 이보다 적으면 확정하지 않는다 (두 명이 리포트 평가를 바꾸지 못하게)


def judged_votes(n_jurors: int) -> int:
    """판정을 확정하는 데 필요한 '판단한 표'의 최소 수 (5명이면 3, 3명이면 3)"""
    return min(MIN_JUDGED, n_jurors)


def objection_verdict(o: Objection, votes: List[Optional[Vote]], n_jurors: int) -> dict:
    """이의 하나의 판정 (표결 판정 규칙, docs/판정_규칙.md 3장). votes 안의 None은 기권(호출 실패).
    배심원 표: 동의 / 비동의 / 판단 불가, 동의면 중요도 상 / 중 / 하. '판단 불가'는 판단을 맡긴 표라 세지 않는다.
    1 유효표가 과반 미만 -> 판정 보류
    2 판단한 표(동의 + 비동의)가 3표 미만 -> 확인 필요
    3 비동의가 과반 -> 유지 (이의 불인정)
    4 동의가 과반 + 동의표 중 상·중이 과반 -> 수정 (근거가 확인 안 되면 확인 필요)
    5 동의가 과반 + 하가 과반 -> 유지 (경미한 지적)
    6 동의가 과반인데 중요도 어느 쪽도 과반이 아님(빠진 중요도는 '미확인') -> 확인 필요
    7 동의와 비동의가 같음 -> 확인 필요
    배심원이 추가한 이의는 이의를 낸 배심원을 뺀 나머지가 2차 표결한다. 그때 n_jurors 는 표결한 사람 수다.
    2차 표결이 없으면(예전 결과, 가짜 실행) 확인 필요."""
    valid = [v for v in votes if v is not None]
    agree = [v for v in valid if v.vote == AGREE]
    n_dis = sum(v.vote == DISAGREE for v in valid)
    n_uns = sum(v.vote == UNSURE for v in valid)
    imp = Counter(v.importance for v in agree)              # None = 미확인
    judged = len(agree) + n_dis
    high = imp["상"] + imp["중"]
    level = ("상" if imp["상"] * 2 > len(agree) else "중" if high * 2 > len(agree)
             else "하" if imp["하"] * 2 > len(agree) else None) if agree else None
    reason = ""
    if o.added_by and not valid:
        verdict, reason = CHECK, "2차 표결 없음"
    elif len(valid) < majority(n_jurors):
        verdict, reason = HOLD, "유효표 부족"
    elif judged < judged_votes(n_jurors):
        verdict, reason = CHECK, "판단한 배심원이 적음"
    elif n_dis * 2 > judged:
        verdict, reason = KEEP, NOT_ACCEPTED
    elif len(agree) * 2 > judged:
        if high * 2 > len(agree):
            verdict, reason = (CHECK, "근거가 확인되지 않음") if o.no_evidence else (MODIFY, "")
        elif imp["하"] * 2 > len(agree):
            verdict, reason = KEEP, MINOR
        else:
            verdict, reason = CHECK, "중요도 의견 갈림"
    else:
        verdict, reason = CHECK, "의견 갈림"
    return {
        "objection_id": o.objection_id, "claim_id": o.claim_id, "verdict": verdict, "reason": reason, "kind": o.kind,
        "importance": level if agree and len(agree) * 2 > judged else None,     # 동의가 과반일 때만 이의의 중요도로 본다
        "tally": {"동의": len(agree), "비동의": n_dis, "판단 불가": n_uns,
                  "기권": n_jurors - len(valid) if (valid or not o.added_by) else 0,
                  "중요도": {"상": imp["상"], "중": imp["중"], "하": imp["하"], "미확인": imp[None]}},
        "importance_split": imp["상"] > 0 and imp["하"] > 0,
        **({"round2": True} if o.added_by and valid else {}),
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


# 결론 주장: 목표주가·적정주가·투자의견을 말하는 핵심 주장. 나머지 핵심 주장은 이 결론의 근거로 본다 (docs/판정_규칙.md 9장 (다), 2026-11-21)
_CONCLUSION = re.compile(r"목표\s*주가|적정\s*주가|투자\s*의견|목표가|target\s*price", re.I)
FACT_ERROR = "사실 오류"


def fact_error_claims(obj_verdicts: Optional[List[dict]], findings: Optional[Dict[str, List[dict]]] = None) -> set:
    """'수정'이 숫자·계산이 실제로 틀린 데서 나온 주장: 사실 오류 이의가 수정됐거나, 공식 데이터와 달라 코드가 수정으로 정함"""
    out = {ov["claim_id"] for ov in (obj_verdicts or []) if ov.get("claim_id") and ov["verdict"] == MODIFY and ov.get("kind") == FACT_ERROR}
    out |= {cid for cid, fs in (findings or {}).items() if any(f["verdict"] == MODIFY for f in fs)}
    return out


def propagate(report: Report, cv: Dict[str, str], fact_claims: set) -> tuple:
    """근거 주장이 수정이면 결론 주장(목표주가 등)도 따라간다. 숫자·계산이 틀린 수정이면 결론도 수정,
    근거가 부족하다는 수정(해석 차이)이면 결론은 확인 필요. 결론 주장의 원래 판정이 더 엄하면 그대로.
    돌려주는 값: (새 주장 판정, {결론 주장: {'from': [근거 주장], 'verdict': 판정}})"""
    core = [c for c in report.claims if c.is_core]
    heads = [c.claim_id for c in core if _CONCLUSION.search(c.text or "")]
    if not heads:
        return cv, {}
    supports = [c.claim_id for c in core if c.claim_id not in heads and cv.get(c.claim_id) == MODIFY]
    if not supports:
        return cv, {}
    to = MODIFY if any(cid in fact_claims for cid in supports) else CHECK
    out, dep = dict(cv), {}
    for h in heads:
        dep[h] = {"from": supports, "verdict": to}
        if SEVERITY[to] > SEVERITY[out.get(h, KEEP)]:
            out[h] = to
    return out, dep


def unlinked(obj_verdicts: Optional[List[dict]]) -> List[str]:
    """주장과 묶이지 않은 이의의 판정들"""
    return [ov["verdict"] for ov in (obj_verdicts or []) if not ov.get("claim_id")]


def report_grade(report: Report, cverdicts: Dict[str, str], obj_verdicts: Optional[List[dict]] = None) -> str:
    """리포트 등급 판정 규칙 (docs/판정_규칙.md 9장). 위에서부터 처음 맞는 것.
    1 핵심 주장이 수정, 또는 중요도 '상' 수정 이의가 있음(주장과 묶이지 않은 이의 포함) -> 재검토 필요
    2 핵심 주장이 판정 보류 -> 판정 불완전
    3 수정 이의(상이 아닌 것)가 있거나 핵심 주장이 확인 필요 -> 조건부 통과
    4 그 밖 -> 통과
    판정 보류는 등급을 깎지 않는다(기술 문제). 핵심이 아닌 곳의 확인 필요와 경미한 지적은 등급에 넣지 않는다."""
    core = {c.claim_id for c in report.claims if c.is_core}
    ovs = obj_verdicts or []
    if any(cverdicts.get(cid) == MODIFY for cid in core) or \
            any(ov["verdict"] == MODIFY and ov.get("importance") == "상" for ov in ovs):
        return RETRIAL
    if any(cverdicts.get(cid) == HOLD for cid in core):
        return INCOMPLETE
    if any(v == MODIFY for v in cverdicts.values()) or any(ov["verdict"] == MODIFY for ov in ovs) or \
            any(cverdicts.get(cid) == CHECK for cid in core):
        return CONDITIONAL
    return PASS


def grade_detail(report: Report, cverdicts: Dict[str, str], obj_verdicts: Optional[List[dict]] = None) -> dict:
    """화면에 보일 등급, 이유, 판정 보류 수"""
    value = report_grade(report, cverdicts, obj_verdicts)
    core = [c.claim_id for c in report.claims if c.is_core]
    core_mod = [cid for cid in core if cverdicts.get(cid) == MODIFY]
    top = [ov["objection_id"] for ov in (obj_verdicts or []) if ov["verdict"] == MODIFY and ov.get("importance") == "상"]
    text = {c.claim_id: c.text.rstrip(".") for c in report.claims}
    if core_mod:
        why = "투자의견 핵심 주장에 걸린 지적이 수정됐습니다: " + " / ".join(text[c] for c in core_mod)
    elif top:
        why = f"중요도 '상'으로 판정된 지적이 수정됐습니다({len(top)}건)."
    elif value == INCOMPLETE:
        why = "핵심 주장의 판정을 내리지 못했습니다(AI 호출 실패 등). 다시 분석해 보세요."
    elif value == CONDITIONAL:
        why = "핵심 주장에 '수정'은 없지만, 고칠 점이 있거나 핵심 주장에 확인이 필요합니다."
    else:
        why = "핵심 주장과 중요한 이의에 문제가 없습니다."
    holds = sum(1 for ov in (obj_verdicts or []) if ov["verdict"] == HOLD)
    return {"value": value, "why": why, "holds": holds}


def checklist(report: Report, objections: List[Objection], obj_verdicts: List[dict], limit: int = 5) -> List[dict]:
    """투자 전에 직접 확인할 사항: '확인 필요'(판정 보류 포함), 핵심 주장의 '수정', '경미한 지적'에서 최대 5개.
    검증 항목(사실 오류)이 분석 항목(해석 차이)보다 앞, 같은 판정이면 중요도가 높은 것이 앞."""
    core = {c.claim_id for c in report.claims if c.is_core}
    sids = {s.sid for s in report.sources}
    by_id = {o.objection_id: o for o in objections}
    row_source = {r.rid: r.source for r in report.data_rows}
    items = []
    for ov in obj_verdicts:
        o = by_id[ov["objection_id"]]
        pick = ov["verdict"] in (CHECK, HOLD) or (ov["verdict"] == MODIFY and o.claim_id in core) or \
            (ov["verdict"] == KEEP and ov.get("reason") == MINOR)
        if not pick:
            continue
        srcs = sorted({i for i in o.counter_ids if i in sids} |
                      {row_source[i] for i in o.counter_ids if row_source.get(i) in sids})
        items.append({
            "what": o.text, "why": o.objection_id, "claim_id": o.claim_id,
            "type": "검증" if o.kind == "사실 오류" else "분석",
            "sources": srcs, "verdict": ov["verdict"], "importance": ov.get("importance"),
        })
    items.sort(key=lambda x: (x["type"] != "검증", -SEVERITY[x["verdict"]], IMP_RANK.get(x.get("importance"), 2)))
    return items[:limit]


def evaluate(report: Report, objections: List[Objection], votes_by_juror: Dict[str, Optional[List[Vote]]],
             n_jurors: int) -> dict:
    """집계부터 리포트 평가까지 한 번에. votes_by_juror 값이 None이면 그 배심원은 기권."""
    obj_verdicts = []
    for o in objections:
        votes = []
        for juror, vs in votes_by_juror.items():
            if o.added_by and o.objection_id == f"J-{juror}":   # 이의를 낸 배심원은 자기 이의에 표결하지 않는다
                continue
            if vs is None:
                votes.append(None)
                continue
            found = next((v for v in vs if v.objection_id == o.objection_id), None)
            votes.append(found)   # 그 이의에 표를 안 낸 경우도 기권
        obj_verdicts.append(objection_verdict(o, votes, len(votes) if o.added_by else n_jurors))
    cv, dep = propagate(report, claim_verdicts(report, obj_verdicts), fact_error_claims(obj_verdicts))
    return {
        "n_jurors": n_jurors, "majority": majority(n_jurors),
        "objection_verdicts": obj_verdicts, "claim_verdicts": cv, "dependency": dep,
        "report_grade": report_grade(report, cv, obj_verdicts),
        "holds": sum(1 for ov in obj_verdicts if ov["verdict"] == HOLD),
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
    """배심원 판정과 코드 검증 가운데 더 엄한 쪽을 주장 판정으로 쓴다. 리포트 평가와 투자 전에 직접 확인할 사항도 다시 정한다."""
    if not findings:
        return dict(ev, code_findings={})
    cv = dict(ev["claim_verdicts"])
    for cid, fs in findings.items():
        worst = max((f["verdict"] for f in fs), key=lambda v: SEVERITY[v])
        if SEVERITY[worst] > SEVERITY[cv.get(cid, KEEP)]:
            cv[cid] = worst
    cv, dep = propagate(report, cv, fact_error_claims(ev.get("objection_verdicts"), findings))
    core = {c.claim_id for c in report.claims if c.is_core}
    items = [{"what": f"{f['name']}: {f['reason']}", "why": f["check_id"], "claim_id": cid, "type": "검증",
              "sources": [], "verdict": f["verdict"]}
             for cid, fs in findings.items() for f in fs if f["verdict"] == CHECK or cid in core]
    seen = {i["why"] for i in items}
    merged = items + [i for i in ev["checklist"] if i["why"] not in seen]
    merged.sort(key=lambda x: (x["type"] != "검증", -SEVERITY[x["verdict"]], IMP_RANK.get(x.get("importance"), 2)))
    return dict(ev, claim_verdicts=cv, report_grade=report_grade(report, cv, ev.get("objection_verdicts")),
                checklist=merged[:limit], code_findings=findings, dependency=dep or ev.get("dependency", {}))


def refresh_result(report: Report, result: dict) -> None:
    """이미 끝난 분석(result.json)에 코드 검증 판정을 다시 반영한다. 배심원 판정은 이의별 판정에서 다시 모은다."""
    vote_cv = {}
    for c in result.get("claims", []):
        vs = [o["verdict"] for o in c.get("objections", [])]
        vote_cv[c["id"]] = max(vs, key=lambda v: SEVERITY[v]) if vs else KEEP
    ovs0 = [{"objection_id": o["id"], "claim_id": c["id"], "verdict": o["verdict"], "kind": o.get("kind")}
            for c in result.get("claims", []) for o in c.get("objections", [])]
    vote_cv, dep = propagate(report, vote_cv, fact_error_claims(ovs0))
    base = {"claim_verdicts": vote_cv, "objection_verdicts": ovs0, "dependency": dep,
            "checklist": [i for i in result.get("checklist", []) if not str(i.get("why", "")).startswith(("D-", "E-", "X-", "F-", "K-"))]}
    ev = apply_code_findings(report, base, code_findings(report, result["numbers"]["results"]))
    for c in result.get("claims", []):
        c["verdict"] = ev["claim_verdicts"][c["id"]]
        c["code"] = ev["code_findings"].get(c["id"], [])
        if ev.get("dependency", {}).get(c["id"]):     # 근거 주장을 따라 바뀐 결론 주장 (화면·판사에 이유로 보인다)
            c["dependency"] = ev["dependency"][c["id"]]
        else:
            c.pop("dependency", None)
    cv = ev["claim_verdicts"]
    ovs = [{"objection_id": o["id"], "claim_id": c["id"], "verdict": o["verdict"], "importance": o.get("importance")}
           for c in result.get("claims", []) for o in c.get("objections", [])] + \
          [{"objection_id": o["id"], "claim_id": None, "verdict": o["verdict"], "importance": o.get("importance")}
           for o in result.get("other_objections", [])]
    result["grade"] = grade_detail(report, cv, ovs)
    result.setdefault("logic", {})["count"] = {"수정": sum(v == MODIFY for v in cv.values()),
                                               "확인 필요": sum(v in (CHECK, HOLD) for v in cv.values()),
                                               "유지": sum(v == KEEP for v in cv.values())}
    result["checklist"] = ev["checklist"]
    result["code_findings"] = ev["code_findings"]
