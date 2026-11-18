"""PDF에서 인용문의 위치를 찾고, 쪽을 그림으로 바꾼다. (pymupdf 사용)

인용문은 줄바꿈과 띄어쓰기를 무시하고 글자 단위로 맞춘다.
옆 단(사이드바)이 본문 줄 사이에 끼어 추출되는 PDF도 있어, 줄 단위로 이어 붙이며 찾는다.
"""
from __future__ import annotations
import base64
import re
from typing import List, Optional, Tuple

import pymupdf

Rect = Tuple[float, float, float, float]   # 쪽 크기에 대한 비율(0~1): x, y, w, h

# 모양은 같은데 코드가 다른 글자를 하나로 맞춘다 (가운뎃점, 따옴표, 줄표). 글자 수는 그대로다.
_SAME = str.maketrans({"\u0387": "·", "\u2027": "·", "\u30fb": "·", "\u2022": "·", "\u318d": "·",
                       "\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'",
                       "\u2013": "-", "\u2014": "-", "\u2212": "-", "\uff5e": "~", "\u223c": "~"})


def norm(s: str) -> str:
    """띄어쓰기와 줄바꿈을 없애고 닮은 글자를 하나로 맞춘다."""
    return re.sub(r"\s+", "", s).translate(_SAME)


def _lines(page):
    out = []
    for b in page.get_text("rawdict")["blocks"]:
        for ln in b.get("lines", []):
            chars = [(c["c"], c["bbox"]) for sp in ln["spans"] for c in sp["chars"] if not c["c"].isspace()]
            if chars:
                out.append({"text": "".join(c for c, _ in chars).translate(_SAME), "chars": chars,
                            "x0": chars[0][1][0], "y0": min(c[1][1] for c in chars)})
    return out


def _rect(chars, page) -> Rect:
    x0 = min(c[1][0] for c in chars); y0 = min(c[1][1] for c in chars)
    x1 = max(c[1][2] for c in chars); y1 = max(c[1][3] for c in chars)
    W, H = page.rect.width, page.rect.height
    return (x0 / W, y0 / H, (x1 - x0) / W, (y1 - y0) / H)


def _find_on_page(page, needle: str) -> Optional[List[Rect]]:
    lines = _lines(page)
    for i, ln in enumerate(lines):
        t = ln["text"]
        for j in range(len(t)):
            tail = t[j:]
            if tail.startswith(needle):                       # 한 줄 안에서 끝남
                return [_rect(ln["chars"][j:j + len(needle)], page)]
            if not needle.startswith(tail):
                continue
            rects, rem, cur, used = [_rect(ln["chars"][j:], page)], needle[len(tail):], ln, {i}
            while rem:
                cands = [(k, c) for k, c in enumerate(lines) if k not in used and c["y0"] > cur["y0"] - 1
                         and (rem.startswith(c["text"]) or c["text"].startswith(rem))]
                if not cands:
                    break
                k, c = min(cands, key=lambda kc: (kc[1]["y0"] - cur["y0"]) + abs(kc[1]["x0"] - ln["x0"]) * 0.2)
                n = min(len(c["text"]), len(rem))
                rects.append(_rect(c["chars"][:n], page)); rem = rem[n:]; cur = c; used.add(k)
            if not rem:
                return rects
    return None


def locate(doc, quote: str, page_hint: Optional[int] = None) -> List[dict]:
    """인용문이 있는 곳을 [{page, rects}]로 돌려준다. 못 찾으면 빈 목록.
    문장이 여러 개면 문장별로 따로 찾는다(리포트에서 떨어져 있을 수 있음)."""
    needle = norm(quote)
    order = list(range(len(doc)))
    if page_hint and 1 <= page_hint <= len(doc):
        order.remove(page_hint - 1); order.insert(0, page_hint - 1)
    for p in order:
        r = _find_on_page(doc[p], needle)
        if r:
            return [{"page": p + 1, "rects": r}]
    parts = [s for s in re.split(r"(?<=[.!?])\s+", quote.strip()) if len(s) > 8]
    if len(parts) > 1:
        found = []
        for s in parts:
            found += locate(doc, s, page_hint)
        return found
    return []


def page_images(doc, dpi: int = 100, quality: int = 62) -> List[dict]:
    out = []
    for page in doc:
        pix = page.get_pixmap(dpi=dpi)
        out.append({"w": pix.width, "h": pix.height,
                    "b64": base64.b64encode(pix.tobytes("jpeg", jpg_quality=quality)).decode()})
    return out
