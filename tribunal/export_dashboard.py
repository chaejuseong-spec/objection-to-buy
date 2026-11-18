"""대시보드가 읽는 result.json 을 만든다. 대시보드는 이 파일을 보여주기만 하고 계산하지 않는다."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Dict, List, Optional

from . import rules
from .number_check import _show
from .schemas import JUROR_NAMES, JudgeOut, JurorOut, Objection, Report


def build_result(report: Report, objections: List[Objection], jurors: Dict[str, Optional[JurorOut]],
                 judge: Optional[JudgeOut], ev: dict, nc: dict, mock: bool, run_id: str,
                 marks: Optional[list] = None) -> dict:
    rows = {r.rid: r for r in report.data_rows}
    ov = {o["objection_id"]: o for o in ev["objection_verdicts"]}
    rulings = {r.claim_id: r for r in (judge.claim_rulings if judge else [])}
    check_of = {x["stated_rid"]: x for x in nc["results"] if x.get("stated_rid")}
    cv = ev["claim_verdicts"]

    def obj(o: Objection) -> dict:
        votes = []
        if not o.added_by:
            for k, out in jurors.items():
                v = next((x for x in out.votes if x.objection_id == o.objection_id), None) if out else None
                votes.append({"juror": JUROR_NAMES[k], "vote": v.vote if v else "기권", "reason": v.reason if v else "", "view": v.view if v else ""})
        return {"id": o.objection_id, "who": f"배심원({o.added_by})" if o.added_by else "리스크 검토자",
                "doubt_type": o.doubt_type, "kind": o.kind, "text": o.text, "verdict": ov[o.objection_id]["verdict"],
                "tally": ov[o.objection_id]["tally"], "votes": votes,
                "evidence": [{"id": i, "label": f"{rows[i].item} {_show(rows[i].value, rows[i].unit)}" if i in rows else i,
                              "page": rows[i].page if i in rows else None} for i in o.counter_ids],
                "removed_ids": o.removed_ids, "already_in_report": o.already_in_report,
                "claim_id": o.claim_id, "page": o.page, "quote": o.quote, "quote_found": o.quote_found}

    claims = []
    for c in report.claims:
        r = rulings.get(c.claim_id)
        claims.append({"id": c.claim_id, "text": c.text, "is_core": c.is_core, "page": c.page,
                       "verdict": cv[c.claim_id],
                       "objections": [obj(o) for o in objections if o.claim_id == c.claim_id],
                       "code": ev.get("code_findings", {}).get(c.claim_id, []),       # 판정을 올린 코드 검증 (공식 데이터 불일치 등)
                       "ruling": {"reason": r.reason, "impact": r.impact, "fix_part": r.fix_part} if r else None})
    by_id = {o.objection_id: o for o in objections}
    issues = [{"id": k.objection_id, "title": k.title, "kind": "논리",
               "claim_id": by_id[k.objection_id].claim_id if k.objection_id in by_id else None}
              for k in (judge.key_issues if judge else [])]
    issues += [{"id": x["check_id"], "title": x["name"], "kind": "숫자", "claim_id": None, "pages": x["pages"]}
               for x in nc["results"] if x["status"] == "불일치"]
    kpis = []
    for rid in report.kpis:
        if rid in rows:
            r = rows[rid]; ck = check_of.get(rid)
            kpis.append({"rid": rid, "item": r.item, "value": r.value, "unit": r.unit, "page": r.page, "basis": r.basis,
                         "check": {"id": ck["check_id"], "status": ck["status"], "computed": ck["computed"],
                                   "diff_pct": ck["diff_pct"]} if ck else None})
    count = {k: sum(1 for v in cv.values() if v == k) for k in (rules.MODIFY, rules.CHECK, rules.HOLD, rules.KEEP)}
    core_mod = [c.claim_id for c in report.claims if c.is_core and cv[c.claim_id] == rules.MODIFY]
    return {
        "run": {"id": run_id, "mock": mock, "demo": report.meta.demo},
        "meta": report.meta.model_dump(),
        "grade": {"value": ev["report_grade"],
                  "why": (f"투자의견 핵심 주장에 '수정' 판정이 있습니다: {', '.join(core_mod)}" if core_mod else
                          "핵심 주장에는 '수정' 판정이 없습니다.")},
        "logic": {"n_jurors": ev["n_jurors"], "majority": ev["majority"], "n_claims": len(cv),
                  "count": {"수정": count[rules.MODIFY], "확인 필요": count[rules.CHECK] + count[rules.HOLD],
                            "유지": count[rules.KEEP]}},
        "numbers": {"count": nc["count"], "n_checks": len(nc["results"]), "n_numbers": nc["n_numbers"],
                    "n_unchecked": nc["n_unchecked"], "scope": nc["scope"], "results": nc["results"]},
        "external_data": report.external_data,
        "summary": [s.model_dump() for s in report.summary],
        "tables": report.tables,
        "kpis": kpis, "claims": claims, "key_issues": issues, "checklist": ev["checklist"],
        "other_objections": [obj(o) for o in objections if not o.claim_id],   # 원문에서 찾았고 추출된 주장과 묶이지 않은 이의
        "rows": [r.model_dump() for r in report.data_rows],
        "marks": marks or [],
    }


def write_result(path: Path, result: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
