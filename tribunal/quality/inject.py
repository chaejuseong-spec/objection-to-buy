"""오류 삽입: 끝난 분석(run 폴더)에서 테스트 세트를 만든다. AI를 부르지 않는다.

고르는 숫자
- 공식 데이터 대조(DART, KRX, ECOS)나 다시 계산에서 '일치'로 확인된 숫자  -> 바꾸면 그 검증이 잡아야 한다
- 어떤 검증에도 걸리지 않은 숫자 몇 개                                      -> 지금은 잡을 수단이 없다 (사각지대 측정)

바꾸는 법
- 숫자 토큰 하나를 8~13% 바꾼다 (같은 자릿수 모양 유지). 그 쪽에 한 번만 나오는 토큰만 쓴다.
- PDF 사본에서 그 자리를 지우고 새 숫자를 써 넣는다. AI에게는 원본 글 순서에서 그 토큰만 바꾼 글을 읽힌다.
- 한 사본 안에서 같은 검증에 속한 숫자는 하나만 바꾼다 (서로 상쇄되지 않게).
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional

import pymupdf

from ..llm import ROOT
from ..number_check import MATCH, parse_number

SETS = ROOT / "eval" / "quality" / "sets"
FACTORS = [1.08, 0.91, 1.13, 0.87]
_TOKEN = re.compile(r"\d[\d,]*(?:\.\d+)?")
_RID = re.compile(r"\b[RM]-\d+\b")


def involved(check: dict) -> set:
    """검증 한 건이 쓰는 숫자 id (적힌 값, 식에 들어간 값, 비교한 값)"""
    return set(check.get("rids") or []) | set(_RID.findall(check.get("expr") or "")) | set(check.get("compare") or [])


def main_token(value: str) -> Optional[str]:
    """값에서 바꿀 숫자 토큰. '1 조 8,367 억 원' -> '8,367', '54.7%' -> '54.7'. 가장 긴 것을 고른다."""
    toks = [t for t in _TOKEN.findall(value or "") if len(re.sub(r"\D", "", t)) >= 2]
    return max(toks, key=len) if toks else None


def mutate_token(tok: str, seed: str) -> Optional[str]:
    """같은 모양(쉼표, 소수 자릿수)으로 8~13% 다른 숫자. 만들 수 없으면 None"""
    plain = tok.replace(",", "")
    dec = len(plain.split(".")[1]) if "." in plain else 0
    v = float(plain)
    order = int(hashlib.sha1(seed.encode()).hexdigest(), 16)
    for i in range(len(FACTORS)):
        nv = round(v * FACTORS[(order + i) % len(FACTORS)], dec)
        s = f"{nv:,.{dec}f}" if "," in tok else f"{nv:.{dec}f}"
        if s != tok and len(s) == len(tok):
            return s
    return None


def _unique_on_page(page, text: str, tok: str) -> bool:
    return len(re.findall(rf"(?<![\d,.]){re.escape(tok)}(?![\d,]|\.\d)", text)) == 1 and len(page.search_for(tok)) == 1


def pick_targets(run_dir: Path, pdf: Path, extra_unchecked: int = 3) -> List[dict]:
    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    rows = {r["rid"]: r for r in result["rows"]}
    checks = result["numbers"]["results"]
    doc = pymupdf.open(pdf)
    texts = {i: p.get_text() for i, p in enumerate(doc, 1)}
    by_rid: Dict[str, List[dict]] = {}
    for c in checks:
        for rid in involved(c):
            by_rid.setdefault(rid, []).append(c)

    def target(rid: str, expect: str, check_id: str) -> Optional[dict]:
        r = rows.get(rid)
        if not r or not r.get("page") or r["page"] not in texts:
            return None
        tok = main_token(r["value"])
        if not tok or parse_number(tok) is None:
            return None
        page = doc[r["page"] - 1]
        if not _unique_on_page(page, texts[r["page"]], tok):
            return None
        new = mutate_token(tok, rid + r["value"])
        if not new or new in texts[r["page"]]:
            return None
        return {"rid": rid, "item": r["item"], "page": r["page"], "old": tok, "new": new,
                "value_old": r["value"], "value_new": r["value"].replace(tok, new, 1),
                "expect": expect, "check_id": check_id,
                "checks": sorted(c["check_id"] for c in by_rid.get(rid, []))}

    out, used = [], set()
    for c in checks:                                   # 1) 확인된 검증에 걸린 숫자
        if c["status"] != MATCH:
            continue
        kind = c.get("kind") if c.get("kind") in ("dart", "krx", "ecos") else "recompute"
        for rid in sorted(involved(c)):
            if rid in used:
                continue
            t = target(rid, kind, c["check_id"])
            if t:
                out.append(t)
                used.add(rid)
    n = 0
    for rid, r in rows.items():                        # 2) 어떤 검증에도 걸리지 않은 숫자
        if n >= extra_unchecked:
            break
        if rid in used or rid in by_rid or parse_number(r["value"], r.get("unit", "")) is None:
            continue
        t = target(rid, "none", "")
        if t:
            out.append(t)
            used.add(rid)
            n += 1
    return out


def split_cases(targets: List[dict], max_cases: int = 2) -> List[List[dict]]:
    """한 사본 안에서 같은 검증에 속한 숫자는 하나만. 같은 쪽의 같은 토큰도 겹치지 않게."""
    cases: List[List[dict]] = [[] for _ in range(max_cases)]
    for t in targets:
        for c in cases:
            clash = any(set(t["checks"]) & set(x["checks"]) or (t["page"] == x["page"] and (t["old"] in x["old"] or x["old"] in t["old"])) for x in c)
            if not clash:
                c.append(t)
                break
    return [c for c in cases if c]


def write_pdf(src: Path, dst: Path, muts: List[dict]) -> None:
    doc = pymupdf.open(src)
    for m in muts:
        page = doc[m["page"] - 1]
        rect = page.search_for(m["old"])[0]
        size = next((s["size"] for b in page.get_text("dict")["blocks"] for ln in b.get("lines", []) for s in ln["spans"]
                     if m["old"] in s["text"]), rect.height * 0.8)
        page.add_redact_annot(rect, fill=(1, 1, 1))
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE)
        page.insert_text((rect.x0, rect.y1 - size * 0.22), m["new"], fontsize=size, fontname="helv")
    doc.save(dst)


def make_set(run_dir: Path, name: Optional[str] = None, pdf: Optional[Path] = None) -> Path:
    """돌려주는 값: 세트 폴더. 안에 clean.pdf, A.pdf, B.pdf, cases.json"""
    pdf = pdf or run_dir.parent / "report.pdf"
    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    name = name or re.sub(r"[^0-9A-Za-z가-힣_-]", "", result["meta"]["name"]) or run_dir.parent.name
    out = SETS / name
    out.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(pdf, out / "clean.pdf")
    cases = [{"id": "clean", "pdf": "clean.pdf", "mutations": []}]
    for label, muts in zip("ABCD", split_cases(pick_targets(run_dir, pdf))):
        write_pdf(pdf, out / f"{label}.pdf", muts)
        cases.append({"id": label, "pdf": f"{label}.pdf", "mutations": muts})
    meta = {"name": name, "report": result["meta"]["name"], "ticker": result["meta"]["ticker"],
            "report_date": result["meta"]["report_date"], "source_run": str(run_dir), "test_material": True, "cases": cases}
    (out / "cases.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return out
