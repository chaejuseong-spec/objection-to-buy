"""평가기록지.md 와 verdict_form.csv 만들기."""
from __future__ import annotations
import csv
from pathlib import Path
from typing import Dict, List, Optional

from .schemas import JUROR_NAMES, JudgeOut, JurorOut, Objection, Report


def render_markdown(report: Report, objections: List[Objection], jurors: Dict[str, Optional[JurorOut]],
                    judge: Optional[JudgeOut], ev: dict, mock: bool) -> str:
    m = report.meta
    tag = ""
    if m.demo:
        tag = "DEMO · 과거 리포트" if m.name != "가나다소재" else "DEMO"
    L = []
    L.append(f"# 평가 기록지 — {m.name} ({m.ticker})" + (f"  [{tag}]" if tag else ""))
    if mock:
        L.append("\n> MOCK 실행: AI를 부르지 않고 정해진 가짜 답으로 만든 기록지입니다. 내용을 판단 근거로 쓰지 마세요.")
    L.append(f"\n- 리포트: {m.title} ({m.report_date}), 투자의견 {m.opinion}")
    L.append(f"- 스냅샷 날짜: {m.snapshot_date} · 배심원 {ev['n_jurors']}명 (과반 {ev['majority']}표)")
    if not report.external_data:
        L.append("- 외부 대조 자료 없음: 리포트 안의 숫자와 논리만 따졌습니다.")
    L.append(f"\n## 리포트 평가: {ev['report_grade']}\n")
    L.append("| 주장 | 핵심 | 판정 |\n| --- | --- | --- |")
    for c in report.claims:
        L.append(f"| {c.claim_id} | {'예' if c.is_core else ''} | {ev['claim_verdicts'][c.claim_id]} |")
    if judge and judge.key_issues:
        L.append("\n**핵심 쟁점**\n")
        for k in judge.key_issues:
            L.append(f"- {k.objection_id}: {k.title}")

    L.append("\n## 반대신문\n")
    ov = {o["objection_id"]: o for o in ev["objection_verdicts"]}
    rulings = {r.claim_id: r for r in (judge.claim_rulings if judge else [])}

    def write_objection(o) -> None:
        v = ov[o.objection_id]
        who = f"배심원({o.added_by}) 추가 이의" if o.added_by else "리스크 검토자"
        L.append(f"**{o.objection_id} · {who} · {o.doubt_type} · {o.kind}** → {v['verdict']}\n")
        if o.quote:
            L.append(f"> {o.page or '?'}쪽: \"{o.quote}\"" + ("" if o.quote_found else " (원문에서 찾지 못함)") + "\n")
        L.append(f"- 지적: {o.text}")
        ev_txt = ", ".join(o.counter_ids) if o.counter_ids else ("원문 인용" if o.quote_found else "근거 없음")
        if o.removed_ids:
            ev_txt += f" (입력에 없어 지운 id: {', '.join(o.removed_ids)})"
        L.append(f"- 반대 근거: {ev_txt}")
        if o.already_in_report:
            L.append("- 리포트에 이미 있던 내용")
        if not o.added_by:
            t = v["tally"]
            L.append(f"- 표: 타당 {t['타당']} · 과장 {t['과장']} · 확인 불가 {t['확인 불가']} · 기권 {t['기권']}")
            for key, out in jurors.items():
                name = JUROR_NAMES[key]
                if out is None:
                    L.append(f"  - {name}: 기권")
                    continue
                vote = next((x for x in out.votes if x.objection_id == o.objection_id), None)
                L.append(f"  - {name}: {vote.vote} — {vote.view + ' / ' if vote.view else ''}{vote.reason}" if vote else f"  - {name}: 기권")
        L.append("")

    for c in report.claims:
        L.append(f"### {c.claim_id}{' (투자의견 핵심)' if c.is_core else ''} — 판정: {ev['claim_verdicts'][c.claim_id]}\n")
        L.append(f"> {c.text}\n")
        objs = [o for o in objections if o.claim_id == c.claim_id]
        if not objs:
            L.append("리스크 검토자: 이의 없음\n")
        for o in objs:
            write_objection(o)
        r = rulings.get(c.claim_id)
        if r:
            L.append(f"판사: {r.reason} (투자의견 영향: {r.impact}" + (f", 검토할 부분: {r.fix_part}" if r.fix_part else "") + ")\n")
        elif judge is None:
            L.append("판사: 출력 없음 (판정은 코드 규칙으로만 계산됨)\n")

    others = [o for o in objections if not o.claim_id]
    if others:
        L.append("### 그 밖의 지적 (추출된 주장과 묶이지 않음)\n")
        for o in others:
            write_objection(o)

    L.append("## 직접 확인할 것\n")
    if not ev["checklist"]:
        L.append("없음\n")
    for it in ev["checklist"]:
        src = ", ".join(it["sources"]) if it["sources"] else "출처 없음"
        L.append(f"- [ ] ({it['type']}) {it['what']} — 왜: {it['why']} · 출처: {src}")

    L.append("\n## 판결 기록 (사람이 적는다)\n")
    L.append("| 시점 | 의견(매수/보유/매도) | 확신도(0~100) | 이유 |\n| --- | --- | --- | --- |")
    for t in ["AI 보기 전", "리포트 본 뒤", "검증 본 뒤"]:
        L.append(f"| {t} |  |  |  |")
    L.append("\n쟁점별 응답은 verdict_form.csv 에 적습니다. 이 기록지는 평가 초안이며 투자 권유가 아닙니다.")
    return "\n".join(L) + "\n"


def write_verdict_form(path: Path, ev: dict, demo: bool) -> None:
    issues = [o["objection_id"] for o in ev["objection_verdicts"] if o["verdict"] != "유지"]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["DEMO" if demo else "", "시점", "의견(매수/보유/매도)", "확신도(0~100)", "이유", "기록 시각"]
                   + [f"{i} 응답(받아들임/반박함/추가 확인함)" for i in issues])
        for t in ["AI 보기 전", "리포트 본 뒤", "검증 본 뒤"]:
            w.writerow(["DEMO" if demo else "", t, "", "", "", ""] + ["" for _ in issues])
