"""PDF에서 주장과 숫자를 뽑는다. 뽑는 것은 AI(추출 담당)가 하고, 맞는지 확인하는 것은 코드가 한다.

코드가 확인하는 것
- 주장 문장이 PDF에 실제로 있는가 (없으면 그 주장을 버린다)
- 숫자가 적힌 쪽에 그 글자가 실제로 있는가 (없으면 그 숫자를 버린다)
- 표의 값이 그 쪽에 실제로 있는가 (하나라도 없으면 그 줄을 버린다)
- 버려진 숫자를 가리키는 근거·검증 식·핵심 수치도 함께 정리한다
"""
from __future__ import annotations
import re
from pathlib import Path
from typing import List, Optional, Tuple

import pymupdf

from . import llm
from .pdf_locate import locate, norm
from .schemas import Report


_squash = norm


def _spaced_text(page) -> str:
    """띄어쓰기를 글자 사이 간격으로만 표현한 PDF용. 글자 높이의 18%보다 넓은 간격을 띄어쓰기로 본다.
    (원문 대조는 띄어쓰기를 무시하므로 결과에 영향이 없고, 사람이 읽는 주장·인용 문장만 읽기 쉬워진다)"""
    lines = []
    for b in page.get_text("rawdict")["blocks"]:
        for ln in b.get("lines", []):
            chars = [c for sp in ln["spans"] for c in sp["chars"]]
            out = []
            for a, nxt in zip(chars, chars[1:] + [None]):
                out.append(a["c"])
                if nxt and a["c"] != " " and nxt["c"] != " " and nxt["bbox"][0] - a["bbox"][2] > 0.18 * (a["bbox"][3] - a["bbox"][1]):
                    out.append(" ")
            lines.append("".join(out))
    return "\n".join(lines)


def read_pages(pdf: Path) -> List[dict]:
    pymupdf.TOOLS.mupdf_display_errors(False)
    doc = pymupdf.open(pdf)
    out = []
    for i, p in enumerate(doc, 1):
        text = p.get_text()
        body = re.sub(r"\s", "", text)
        if len(body) > 200 and text.count(" ") < len(body) * 0.10:     # 띄어쓰기 문자가 거의 없는 쪽만 (보통 한글 글은 15% 이상) 간격으로 다시 읽는다
            text = _spaced_text(p)
        out.append({"page": i, "text": text})
    return out


def respace(text: str, pages: List[dict], page: Optional[int] = None) -> str:
    """띄어쓰기가 빠진 문장을 원문 쪽 글의 띄어쓰기대로 되살린다 (글자는 바꾸지 않는다). 원문에서 못 찾으면 그대로 둔다.
    원문에 띄어쓰기 문자가 있는 곳은 띄운다. 줄바꿈 자리는 단어 중간에서 줄이 넘어간 것일 수 있으므로,
    AI가 적은 문장에 그 자리 띄어쓰기가 있을 때만 띄운다."""
    from .pdf_locate import norm
    q, gap = [], set()                          # gap: AI 문장에서 q[j] 뒤에 띄어쓰기가 있던 자리
    for ch in text or "":
        n = norm(ch)
        if n:
            q.append(n)
        elif q:
            gap.add(len(q) - 1)
    qs = "".join(q)
    if not qs:
        return text
    for p in sorted(pages, key=lambda x: x["page"] != page):
        src, flat, at = p["text"], [], []
        for i, ch in enumerate(src):
            n = norm(ch)
            flat.append(n)
            at.extend([i] * len(n))
        k = "".join(flat).find(qs)
        if k < 0:
            continue
        out = []
        for jj in range(len(qs)):
            out.append(src[at[k + jj]])
            if jj + 1 < len(qs):
                between = src[at[k + jj] + 1: at[k + jj + 1]]
                if " " in between or "　" in between or (("\n" in between or "\t" in between) and jj in gap):
                    out.append(" ")
        return "".join(out)
    return text


def _value_forms(v) -> List[str]:
    """표의 숫자가 리포트에 적힐 수 있는 모양들."""
    if isinstance(v, bool):
        return []
    if isinstance(v, int) or (isinstance(v, float) and v.is_integer()):
        n = int(v)
        return [f"{abs(n):,}", f"({abs(n):,})", str(abs(n))] if n < 0 else [f"{n:,}", str(n)]
    a = abs(v)
    forms = [f"{a:,.1f}", f"{a:,.2f}", f"{a:.1f}", f"{a:.2f}"]      # 천 단위 쉼표가 있는 소수(21,996.5)도 찾는다
    return forms + [f"({x})" for x in forms[:2]] if v < 0 else forms


def verify(report: Report, pdf: Path, pages: Optional[List[dict]] = None) -> Tuple[Report, dict]:
    """추출 결과를 원문과 대조해 확인된 것만 남긴다.
    pages 를 주면 PDF 대신 그 글(AI가 읽은 글)과 대조한다 (품질 평가용)."""
    pymupdf.TOOLS.mupdf_display_errors(False)
    doc = pymupdf.open(pdf)
    text = ({p["page"]: _squash(p["text"]) for p in pages} if pages
            else {i: _squash(p.get_text()) for i, p in enumerate(doc, 1)})
    dropped = {"claims": [], "claim_sentences": [], "data_rows": [], "table_rows": [], "checks": [], "summary_pages": []}
    spaced = pages or read_pages(pdf)          # 사람이 읽는 주장 문장은 원문의 띄어쓰기를 따른다

    # 숫자: 적힌 쪽에 그 글자가 있어야 한다
    rows = []
    for r in report.data_rows:
        if r.page in text and _squash(r.value) and _squash(r.value) in text[r.page]:
            rows.append(r)
        else:
            dropped["data_rows"].append({"rid": r.rid, "value": r.value, "page": r.page})
    ok_ids = {r.rid for r in rows} | {m.mid for m in report.metrics}

    # 주장: 원문에서 찾은 문장만 남긴다
    claims = []
    for c in report.claims:
        sentences = [s for s in re.split(r"(?<=[.!?])\s+", c.text.strip()) if s]
        kept = [s for s in sentences if locate(doc, s, c.page)]
        if not kept:
            dropped["claims"].append({"claim_id": c.claim_id, "text": c.text[:80]})
            continue
        if len(kept) < len(sentences):
            dropped["claim_sentences"].append({"claim_id": c.claim_id, "n": len(sentences) - len(kept)})
        claims.append(c.model_copy(update={"text": respace(" ".join(kept), spaced, c.page),   # 원문의 띄어쓰기대로
                                           "evidence": [e for e in c.evidence if e.ref_id in ok_ids]}))

    # 검증 식: 쓰는 rid가 모두 남아 있어야 한다
    checks = []
    for k in report.checks:
        if k.kind == "same" and len(k.compare) != 2:   # '같은지' 검증인데 비교할 두 숫자가 없으면 쓸 수 없다
            dropped["checks"].append({"check_id": k.check_id, "missing": [], "reason": "비교할 숫자 두 개가 없음"})
            continue
        literals = [x for x in re.findall(r"(?<![\w-])\d+(?:\.\d+)?(?![\w-])", re.sub(r"\b[RM]-\d+\b", " ", k.expr))
                    if not re.fullmatch(r"10*|0?\.0*1", x)]
        if literals:                                       # 숫자를 지어내지 않는다: 식에는 리포트 숫자의 id 만
            dropped["checks"].append({"check_id": k.check_id, "missing": [], "reason": f"식에 숫자를 직접 적음: {', '.join(literals[:5])}"})
            continue
        used = set(re.findall(r"\b[RM]-\d+\b", k.expr)) | set(k.compare) | ({k.stated} if k.stated else set())
        if used and used <= ok_ids:
            checks.append(k)
        else:
            dropped["checks"].append({"check_id": k.check_id, "missing": sorted(used - ok_ids)})

    # 표: 값이 그 쪽에 글자로 있어야 한다
    tables = []
    for t in report.tables:
        page_text = text.get(t.get("page"), "")
        good = []
        for row in t.get("rows", []):
            missing = [v for v in row.get("values", []) if not any(f in page_text for f in _value_forms(v))]
            if missing or len(row.get("values", [])) != len(t.get("years", [])):
                dropped["table_rows"].append({"table": t.get("id"), "row": row.get("name"), "missing": missing[:3]})
            else:
                good.append(row)
        if good:
            tables.append({**t, "rows": good})

    summary = []
    for s in report.summary:
        pages = [p for p in s.pages if p in text]
        if len(pages) < len(s.pages):
            dropped["summary_pages"].append(s.text[:40])
        # 요약 줄이 원문을 그대로 옮긴 것이면 원문의 띄어쓰기대로 (풀어 쓴 줄은 원문에 없으므로 그대로 둔다)
        summary.append(s.model_copy(update={"pages": pages, "text": respace(s.text, spaced, pages[0] if pages else None)}))

    meta = report.meta.model_copy(update={"title": respace(report.meta.title, spaced, 1)}) if report.meta.title else report.meta
    clean = report.model_copy(update={
        "meta": meta,
        "claims": claims, "data_rows": rows, "checks": checks, "tables": tables, "summary": summary,
        "kpis": [k for k in report.kpis if k in ok_ids],
    })
    n_drop = sum(len(v) for v in dropped.values())
    stats = {"pages": len(doc), "claims": len(claims), "data_rows": len(rows), "checks": len(checks),
             "tables": len(tables), "dropped_total": n_drop, "dropped": dropped}
    return clean, stats


def extract(pdf: Path, cfg: dict, mock: bool, mock_dir: Optional[Path] = None, demo: bool = False,
            pages: Optional[List[dict]] = None, raw_path: Optional[Path] = None) -> Tuple[Optional[Report], dict]:
    """PDF -> (확인된 Report, 추출 기록). AI가 답을 못 내면 (None, 기록).
    pages 를 주면 PDF 대신 그 글을 AI에게 읽힌다 (품질 평가용: 숫자를 바꾼 사본을 원본 글 순서로 읽히기 위해)."""
    given = pages
    pages = pages or read_pages(pdf)
    chars = sum(len(p["text"]) for p in pages)
    if chars < 200 * len(pages) * 0.2:      # 쪽당 글자가 거의 없으면 스캔본으로 본다
        return None, {"error": "글자를 읽을 수 없는 PDF입니다(스캔본일 수 있음). OCR이 필요합니다.", "pages": len(pages)}
    big = dict(cfg, max_output_tokens=cfg.get("extract_max_output_tokens", 16000),   # 답이 길어 한도와 기다리는 시간을 따로 둔다
               timeout_sec=cfg.get("extract_timeout_sec", 900))
    raw = llm.call("extractor", {"pages": pages}, Report, big, mock, mock_dir)
    if raw is not None and raw_path is not None and not mock:   # 원문 대조 전 답을 남긴다: 대조 코드를 고친 뒤 AI 호출 없이 다시 대조할 수 있다
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(raw.model_dump_json(indent=1), encoding="utf-8")
    if raw is None:
        return None, {"error": "추출 담당이 형식에 맞는 답을 내지 못했습니다.", "pages": len(pages)}
    raw = raw.model_copy(update={"external_data": False,
                                 "meta": raw.meta.model_copy(update={"demo": demo or raw.meta.demo, "source_file": pdf.name})})
    return verify(raw, pdf, given)
