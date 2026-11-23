"""이의의 원문 인용을 화면에서 문장 전체로 보여 주기 위한 앞뒤 글.

AI는 문제가 되는 구절만 짧게 인용한다(원문 대조와 형광펜이 정확해진다). 화면에서는 그 구절이 든
문장 전체를 보여 주고 구절만 굵게 표시한다. 글자는 원문 그대로이며 띄어쓰기·줄바꿈만 한 칸으로 줄인다.
- 문장: 마침표·물음표·느낌표에서 다음 마침표까지. 603.2 처럼 숫자 사이의 점은 문장 끝이 아니다.
- 표처럼 마침표가 없어 문장이 너무 길어지면(220자 넘음) 인용이 있는 줄만 쓴다.

끝난 분석에 더하기 (AI 호출 없음):  python -m tribunal.quote_context library/<리포트 id>/run
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import List, Optional

from .pdf_locate import norm

MAX_SENTENCE = 220


def _squash(t: str) -> str:
    return re.sub(r"\s+", " ", t)


def context(pages: List[dict], quote: str, page: Optional[int] = None) -> Optional[dict]:
    """{'before', 'quote', 'after', 'page'} 또는 원문에서 못 찾으면 None"""
    q = norm(quote or "")
    if not q:
        return None
    for p in sorted(pages, key=lambda x: x["page"] != page):
        src = p["text"]
        flat, at = [], []
        for i, ch in enumerate(src):
            n = norm(ch)
            flat.append(n)
            at.extend([i] * len(n))
        k = "".join(flat).find(q)
        if k < 0:
            continue
        qs, qe = at[k], at[k + len(q) - 1]

        def end(i: int) -> bool:
            return src[i] in ".!?" and not (src[i] == "." and 0 < i < len(src) - 1
                                            and src[i - 1].isdigit() and src[i + 1].isdigit())
        s, e = qs, qe
        while s > 0 and not end(s - 1) and qs - s < 300:
            s -= 1
        while e < len(src) - 1 and not end(e) and e - qe < 300:
            e += 1
        if e - s > MAX_SENTENCE:                       # 표 같은 곳: 인용이 있는 줄만
            s, e = qs, qe
            while s > 0 and src[s - 1] != "\n":
                s -= 1
            while e < len(src) - 1 and src[e + 1] != "\n":
                e += 1
        return {"before": _squash(src[s:qs]).strip(), "quote": _squash(src[qs:qe + 1]).strip(),
                "after": _squash(src[qe + 1:e + 1]).strip(), "page": p["page"]}
    return None


def add_to_result(result: dict, pages: List[dict]) -> int:
    """result.json 의 모든 이의에 quote_ctx 를 붙인다. 붙인 개수를 돌려준다."""
    n = 0
    objs = [o for c in result.get("claims", []) for o in c.get("objections", [])] + list(result.get("other_objections") or [])
    for o in objs:
        ctx = context(pages, o.get("quote", ""), o.get("page")) if o.get("quote") else None
        o["quote_ctx"] = ctx
        n += ctx is not None
    return n


def main(argv: List[str]) -> int:
    from .extract import read_pages
    if not argv:
        print(__doc__)
        return 2
    run = Path(argv[0])
    pdf = run.parent / "report.pdf"
    result_path = run / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    n = add_to_result(result, read_pages(pdf))
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{result_path}: 문장 전체를 붙인 인용 {n}개")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
