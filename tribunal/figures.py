"""원문 도표의 위치를 찾는다. '도표 N-M. 제목' 줄과 그 아래 '출처:' 줄 사이를 도표 영역으로 본다.
도표를 다시 그리지 않는다. 대시보드가 이 영역을 원문에서 그대로 잘라 보여준다."""
from __future__ import annotations
import re
from pathlib import Path
from typing import List

import pymupdf

CAPTION = re.compile(r"^(도표\s*\d+-\d+)\.?\s*(.*)$")


def find_figures(pdf: Path) -> List[dict]:
    pymupdf.TOOLS.mupdf_display_errors(False)
    doc = pymupdf.open(pdf)
    out = []
    for pno, page in enumerate(doc, 1):
        W, H = page.rect.width, page.rect.height
        caps, srcs = [], []
        for b in page.get_text("dict")["blocks"]:
            for ln in b.get("lines", []):
                t = "".join(s["text"] for s in ln["spans"]).strip()
                m = CAPTION.match(t)
                if m:
                    caps.append((m.group(1).replace(" ", " "), m.group(2).strip(), ln["bbox"]))
                elif t.startswith("출처"):
                    srcs.append((t, ln["bbox"]))
        for fid, title, cb in caps:
            below = [s for s in srcs if s[1][1] > cb[1] and abs(s[1][0] - cb[0]) < 8]
            if not below:
                continue
            src, sb = min(below, key=lambda s: s[1][1])
            # 같은 줄에 나란히 있는 다음 도표가 있으면 거기까지, 없으면 오른쪽 여백까지
            right = [c[2][0] for c in caps if abs(c[2][1] - cb[1]) < 6 and c[2][0] > cb[0] + 20]
            x0, x1 = cb[0] - 4, (min(right) - 6 if right else W - 36)
            y0, y1 = cb[1] - 3, sb[3] + 3
            out.append({"id": fid, "title": title, "page": pno, "source": src.replace("출처:", "").strip(),
                        "rect": [round(x0 / W, 5), round(y0 / H, 5), round((x1 - x0) / W, 5), round((y1 - y0) / H, 5)]})
    return out
