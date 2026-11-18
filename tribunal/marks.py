"""원문 위에 표시할 위치를 찾는다. 대시보드의 '검증 원문' 화면이 쓴다.
좌표는 쪽 크기에 대한 비율(0~1)이라 화면 크기와 상관없이 맞는다."""
from __future__ import annotations
from pathlib import Path
from typing import List, Tuple

import pymupdf

from . import rules
from .number_check import MISMATCH
from .pdf_locate import locate
from .table_check import all_rows

HUE = {rules.MODIFY: "red", rules.CHECK: "amber", rules.HOLD: "amber", rules.KEEP: "blue"}


def compute_marks(pdf: Path, report, objections, ev: dict, nc: dict) -> Tuple[List[dict], List[str], int]:
    """돌려주는 것: (표시 목록, 위치를 못 찾은 id, 전체 쪽 수)"""
    pymupdf.TOOLS.mupdf_display_errors(False)
    doc = pymupdf.open(pdf)
    marks, not_found = [], []
    with_obj = {o.claim_id for o in objections}
    for c in report.claims:
        if c.claim_id not in with_obj:
            continue                       # 이의가 없던 주장은 칠하지 않는다
        found = locate(doc, c.text, c.page)
        if not found:
            not_found.append(c.claim_id)
        for f in found:
            marks.append({"ref": c.claim_id, "kind": "claim", "hue": HUE[ev["claim_verdicts"][c.claim_id]],
                          "page": f["page"], "rects": [[round(v, 5) for v in r] for r in f["rects"]]})
    ov = {o["objection_id"]: o["verdict"] for o in ev["objection_verdicts"]}
    for o in objections:                   # 이의가 가리킨 원문 문장 (코드가 원문에 있다고 확인한 것만)
        if not o.quote_found or ov.get(o.objection_id) == rules.KEEP:
            continue
        for f in locate(doc, o.quote, o.page)[:1]:
            marks.append({"ref": o.objection_id, "kind": "objection", "hue": HUE[ov[o.objection_id]],
                          "page": f["page"], "rects": [[round(v, 5) for v in r] for r in f["rects"]]})
    rows = all_rows(report)                # AI가 옮긴 숫자 + 표의 칸
    for x in nc["results"]:
        if x["status"] != MISMATCH:
            continue
        for rid in x.get("rids", []):
            if rid not in rows:
                continue
            found = locate(doc, rows[rid].value, rows[rid].page)[:1]
            if not found:
                not_found.append(f"{x['check_id']}:{rid}")
            for f in found:
                marks.append({"ref": x["check_id"], "kind": "number", "hue": "red",
                              "page": f["page"], "rects": [[round(v, 5) for v in r] for r in f["rects"]]})
    marks.sort(key=lambda m: (m["page"], m["rects"][0][1]))
    return marks, not_found, len(doc)


def pass_marks(pdf: Path, report, result: dict) -> List[dict]:
    """통과한 곳(초록): 이의가 없던 주장, 일치로 확인된 숫자. pass=True 로 표시해 화면에서 켜고 끌 수 있게 한다.
    이미 칠해진 곳(같은 ref)은 다시 칠하지 않는다."""
    pymupdf.TOOLS.mupdf_display_errors(False)
    doc = pymupdf.open(pdf)
    done = {m["ref"] for m in result.get("marks", []) if not m.get("pass")}
    verdict = {c["id"]: c for c in result.get("claims", [])}
    out = []
    for c in report.claims:
        shown = verdict.get(c.claim_id, {})
        if c.claim_id in done or shown.get("objections") or shown.get("verdict") != rules.KEEP:
            continue
        for f in locate(doc, c.text, c.page):
            out.append({"ref": c.claim_id, "kind": "claim", "hue": "green", "pass": True,
                        "page": f["page"], "rects": [[round(v, 5) for v in r] for r in f["rects"]]})
    rows = all_rows(report)
    for x in result["numbers"]["results"]:
        rid = x.get("stated_rid")
        if x["status"] != "일치" or x["check_id"] in done or rid not in rows or ":" in rid:
            continue                       # 표의 칸(T-1:2:3)은 수가 많고, 통과한 곳은 화면에 칠하지 않으므로 찾지 않는다
        for f in locate(doc, rows[rid].value, rows[rid].page)[:1]:
            out.append({"ref": x["check_id"], "kind": "number", "hue": "green", "pass": True,
                        "page": f["page"], "rects": [[round(v, 5) for v in r] for r in f["rects"]]})
    return out
