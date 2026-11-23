"""기본 종목정보 화면의 표를 엑셀 파일로 (2026-11-22).

화면이 보고 있는 숫자(공시 값과 코드가 계산한 비율)를 그대로 받아, 사용자의 보고서 서식 규칙대로 표와 엑셀 차트를 만든다.
숫자를 새로 만들지 않는다. 단위만 바꾼다(원 -> 십억원·억원, % -> 엑셀 백분율).

서식 규칙 (사용자 제공):
- 맑은 고딕 9pt, 눈금선 끔, A열 여백(약 2), 항목 열 넓게, 숫자 열 같은 너비
- 메인(동사) 회사 상징색(로고에서, 없으면 짙은 네이비 #1E3A5F), 비교 대상 회색 #7F7F7F/#A6A6A6, 강조행 배경 #F3F6FB, 행 구분선 #D9D9D9, 숫자는 검정
- 음수는 괄호, 빨간색 안 씀. 단위는 표 제목 옆에 한 번만. 숫자 오른쪽, 항목 왼쪽 정렬
- 머리글: 배경 없음, 남색 굵은 글자, 위 굵은 남색 선 / 아래 얇은 남색 선. 본문: 행 사이 연회색 얇은 선. 강조행: 옅은 파랑 + 위 얇은 남색 선 + 굵게
  표 맨 아래 굵은 남색 선. 행 높이 약 17pt
- 차트: 12 × 7 cm, 제목 없음, 맑은 고딕 8pt, 축선 회색 0.75pt·눈금 바깥, 가로 눈금선 없음, 범례 아래 한 줄·테두리 없음,
  간격 너비 70%, 선 1.75pt·표식 없음, 데이터 레이블은 강조할 값(동사 최근 기간)에만, 세로축 범위는 코드가 정한다

사용: 표준 입력으로 JSON, 표준 출력으로 xlsx.  python -m tribunal.external.excel < payload.json > out.xlsx
"""
from __future__ import annotations

import io
import json
import math
import sys
from typing import List, Optional

from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference, Series
from openpyxl.chart.data_source import NumFmt
from openpyxl.chart.label import DataLabel, DataLabelList
from openpyxl.chart.shapes import GraphicalProperties
from openpyxl.chart.text import RichText
from openpyxl.drawing.line import LineProperties
from openpyxl.drawing.text import CharacterProperties, Font as DFont, Paragraph, ParagraphProperties
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

NAVY = "1E3A5F"                                             # 회사 상징색을 못 찾았을 때 (짙은 네이비)
GRAYS = ["7F7F7F", "A6A6A6", "595959", "BFBFBF", "404040", "D0D0D0"]
FONT = "맑은 고딕"
RULE = "D9D9D9"
FMT = {
    "amount": '#,##0;(#,##0);-',
    "amount1": '#,##0.0;(#,##0.0);-',
    "pct": '0.0%;(0.0%);-',
    "krw": '#,##0;(#,##0);-',
    "days": '#,##0;(#,##0);-',
}


def period_label(p: str) -> str:
    """'2025' -> '2025', '2025.09' -> '3Q25'"""
    if "." in p:
        y, m = p.split(".")
        return f"{int(m) // 3}Q{y[2:]}"
    return p


def money_unit(rows: List[dict]) -> tuple:
    """금액 줄의 단위: 큰 값 기준으로 십억원·억원·원"""
    vals = [abs(v) for r in rows if r["fmt"] == "won" for v in r["values"] if v is not None]
    if not vals:
        return 1, ""
    top = max(vals)
    if top >= 1e11:
        return 1e9, "십억원"
    if top >= 1e8:
        return 1e8, "억원"
    return 1, "원"


def nice_range(lo: float, hi: float, zero: bool) -> tuple:
    """세로축 최솟값·최댓값·눈금 간격. 칸 수 4~6 중 데이터 범위에 가장 딱 맞는 간격 (위아래로 쏠리지 않게). 막대가 있으면 0 포함"""
    if zero:
        lo, hi = min(0.0, lo), max(0.0, hi)
    if lo == hi:
        lo, hi = lo - (abs(lo) * 0.1 or 1), hi + (abs(hi) * 0.1 or 1)
    best = None
    for n in (4, 5, 6):
        raw = (hi - lo) / n
        p = 10 ** math.floor(math.log10(raw))
        step = next((m * p for m in (1, 2, 2.5, 5, 10) if m * p >= raw * 0.999), 10 * p)
        a, b = math.floor(lo / step + 1e-9) * step, math.ceil(hi / step - 1e-9) * step
        if (b - a) / step > 7:
            continue
        waste = (b - a) / (hi - lo)
        if best is None or waste < best[3] - 1e-9:
            best = (a, b, step, waste)
    return best[0], best[1], best[2]


def _text(size: int = 8, bold: bool = False, color: str = "000000") -> RichText:
    cp = CharacterProperties(latin=DFont(typeface=FONT), ea=DFont(typeface=FONT), sz=size * 100, b=bold, solidFill=color)
    return RichText(p=[Paragraph(pPr=ParagraphProperties(defRPr=cp), endParaRPr=cp)])


def _axis(ax, fmt: Optional[str] = None) -> None:
    ax.delete = False
    ax.majorTickMark = "out"
    ax.minorTickMark = "none"
    ax.majorGridlines = None
    ax.spPr = GraphicalProperties(ln=LineProperties(solidFill="808080", w=9525))     # 0.75pt
    ax.txPr = _text(8)
    if fmt:
        ax.numFmt = NumFmt(formatCode=fmt, sourceLinked=False)


def _rgb(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _lum(rgb) -> float:
    def ch(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(x) for x in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def palette(main: Optional[str]) -> tuple:
    """(상징색, 글자·선 색, 강조행 바탕). 상징색이 흰 바탕에서 글자로 읽히지 않으면(대비 4.5:1 미만) 글자·선에는 어둡게 낮춘 색"""
    base = (main or NAVY).lstrip("#").upper()
    rgb = _rgb(base)
    ink = rgb
    while (1.05 / (_lum(ink) + 0.05)) < 4.5:
        ink = tuple(int(x * 0.9) for x in ink)
    tint = tuple(round(255 - (255 - x) * 0.07) for x in rgb)
    return base, "{:02X}{:02X}{:02X}".format(*ink), "{:02X}{:02X}{:02X}".format(*tint)


def build(payload: dict, brand: Optional[str] = None) -> bytes:
    rows: List[dict] = payload["rows"]
    MAIN, INK, LIGHT = palette(brand)                      # noqa: N806
    periods = [period_label(p) for p in payload["periods"]]
    div, unit = money_unit(rows)

    wb = Workbook()
    ws = wb.active
    ws.title = (payload.get("sheet") or "자료")[:31]
    ws.sheet_view.showGridLines = False
    base = Font(name=FONT, size=9)
    try:                                                    # 새 셀의 기본 글꼴도 맑은 고딕 9pt
        wb._named_styles["Normal"].font = base             # noqa: SLF001
    except Exception:                                       # noqa: BLE001
        pass

    navy_thick, navy_thin, rule = Side("medium", color=INK), Side("thin", color=INK), Side("thin", color=RULE)
    ws.column_dimensions["A"].width = 2
    ws.column_dimensions["B"].width = 26
    ws.column_dimensions["C"].width = 8
    first = 4                                               # 숫자 첫 열 (D)
    for j in range(len(periods)):
        ws.column_dimensions[ws.cell(row=1, column=first + j).column_letter].width = 10

    # 제목 줄: 제목 + 단위 (한 번만)
    title = payload.get("title") or ""
    units = [f"단위: {unit}" for _ in [0] if unit and any(r["fmt"] == "won" for r in rows)]
    ws.cell(row=2, column=2, value=title + (f" ({', '.join(units)})" if units else "")).font = Font(name=FONT, size=9, bold=True)
    if payload.get("basis"):
        ws.cell(row=3, column=2, value=payload["basis"]).font = Font(name=FONT, size=8, color="7F7F7F")

    # 머리글
    h = 5
    head = ["항목", "구분", *periods]
    for j, v in enumerate(head):
        cell = ws.cell(row=h, column=2 + j, value=v)
        cell.font = Font(name=FONT, size=9, bold=True, color=INK)
        cell.alignment = Alignment(horizontal="left" if j < 2 else "right", vertical="center")
        cell.border = Border(top=navy_thick, bottom=navy_thin)
    ws.row_dimensions[h].height = 17

    # 본문
    for i, r in enumerate(rows):
        y = h + 1 + i
        strong = bool(r.get("strong"))
        suffix = " (%)" if r["fmt"] == "pct" else " (원)" if r["fmt"] == "krw" else " (일)" if r["fmt"] == "days" else ""
        kind = "계산" if r.get("calc") else (r.get("src") or "공시")
        ws.cell(row=y, column=2, value=r["label"] + suffix)
        ws.cell(row=y, column=3, value=kind)
        for j, v in enumerate(r["values"]):
            if v is None:
                val = None
            elif r["fmt"] == "won":
                val = v / div
            elif r["fmt"] == "pct":
                val = v / 100
            else:
                val = v
            c = ws.cell(row=y, column=first + j, value=val)
            c.number_format = FMT["pct"] if r["fmt"] == "pct" else FMT["amount1"] if r["fmt"] == "won" and div > 1 and val is not None and abs(val) < 100 else FMT["amount"]
        last_row = i == len(rows) - 1
        for col in range(2, first + len(periods)):
            c = ws.cell(row=y, column=col)
            c.font = Font(name=FONT, size=9, bold=strong and col != 3, color="7F7F7F" if col == 3 else "000000")
            c.alignment = Alignment(horizontal="left" if col < first else "right", vertical="center")
            c.border = Border(top=navy_thin if strong else None, bottom=navy_thick if last_row else rule)
            if strong:
                c.fill = PatternFill("solid", fgColor=LIGHT)
        ws.row_dimensions[y].height = 17
    end = h + len(rows)

    # 주석·출처
    y = end + 1
    for note in payload.get("notes") or []:
        ws.cell(row=y, column=2, value=note).font = Font(name=FONT, size=8, color="595959")
        y += 1
    if payload.get("sources"):
        ws.cell(row=y, column=2, value="자료: " + ", ".join(payload["sources"])).font = Font(name=FONT, size=8, color="595959")
        y += 1

    # 차트: 단위가 같은 줄끼리 하나 (화면과 같다). '함께 보기'면 전부 한 차트: 왼쪽 축(금액, 막대 가능) + 오른쪽 축(다른 단위, 선만)
    # 12 x 7 cm, 표 아래에 차례로
    together = bool(payload.get("together"))
    groups: List[List[int]] = []
    if together:
        groups = [list(range(len(rows)))]
    else:
        for i, r in enumerate(rows):
            g = next((g for g in groups if (rows[g[0]].get("group") or rows[g[0]]["fmt"]) == (r.get("group") or r["fmt"])), None)   # 동종사 비교는 지표마다
            if g:
                g.append(i)
            else:
                groups.append([i])
    anchor = y + 1
    cats = Reference(ws, min_col=first, max_col=first + len(periods) - 1, min_row=h)

    def scaled(i: int) -> List[float]:
        f = rows[i]["fmt"]
        return [v / div if f == "won" else v / 100 if f == "pct" else v for v in rows[i]["values"] if v is not None]

    def axis_format(f: str, step: float) -> str:
        if f == "pct":
            return "0%" if abs(step * 100 - round(step * 100)) < 1e-9 else "0.0%"
        return "#,##0" if step >= 1 else "#,##0.0"

    for g in groups:
        fmts = list(dict.fromkeys(rows[i]["fmt"] for i in g))
        dual = together and len(fmts) >= 2
        lfmt = (next((rows[i]["fmt"] for i in g if rows[i].get("kind") == "bar"), "won" if "won" in fmts else fmts[0])) if dual else fmts[0]
        rfmt = next((f for f in fmts if f != lfmt), None) if dual else None
        left = [i for i in g if rows[i]["fmt"] == lfmt]
        right = [i for i in g if rows[i]["fmt"] == rfmt] if dual else []      # 단위 3가지째부터는 그리지 않는다 (화면에서 막는다)
        bars = [i for i in left if rows[i].get("kind") == "bar"]
        lines = [i for i in left if rows[i].get("kind") != "bar"]
        lv = [v for i in left for v in scaled(i)]
        lo, hi, step = nice_range(min(lv, default=0), max(lv, default=1), bool(bars))
        gray = iter(GRAYS)
        color = {i: (MAIN if (rows[i].get("strong") or (k == 0 and not any(rows[x].get("strong") for x in g))) else next(gray, "BFBFBF"))
                 for k, i in enumerate(g)}

        def add(chart, idx: List[int], bar: bool, side: str = ""):
            for i in idx:
                ref = Reference(ws, min_col=first, max_col=first + len(periods) - 1, min_row=h + 1 + i)
                s = Series(ref, title=(rows[i].get("legend") or rows[i]["label"]) + (f" ({side})" if side else ""))
                if bar:
                    s.graphicalProperties = GraphicalProperties(solidFill=color[i])
                    s.graphicalProperties.line = LineProperties(noFill=True)
                else:
                    ln = LineProperties(solidFill=color[i], w=22225, prstDash="dash" if rows[i].get("dashed") else None)   # 1.75pt
                    s.graphicalProperties = GraphicalProperties(ln=ln)
                    s.marker.symbol = "none"
                    s.smooth = False
                if color[i] == MAIN:                        # 강조할 값: 동사의 최근 기간에만 데이터 레이블
                    last = max((k for k, v in enumerate(rows[i]["values"]) if v is not None), default=None)
                    if last is not None:
                        one = DataLabel(idx=last, showVal=True, showSerName=False, showCatName=False, showLegendKey=False,
                                        showPercent=False, showBubbleSize=False, txPr=_text(8, True, INK), dLblPos="outEnd" if bar else "t")
                        s.dLbls = DataLabelList(dLbl=[one], showVal=False, showSerName=False, showCatName=False, showLegendKey=False,
                                                showPercent=False, showBubbleSize=False, showLeaderLines=False)
                chart.series.append(s)

        side_l = "좌" if right else ""
        main = BarChart() if bars else LineChart()
        if bars:
            main.type = "col"
            main.gapWidth = 70
            main.overlap = 0 if len(bars) > 1 else None
            add(main, bars, True, side_l)
        else:
            add(main, lines, False, side_l)
        main.set_categories(cats)
        main.title = None
        main.legend.position = "b"
        main.legend.txPr = _text(8)
        main.legend.spPr = GraphicalProperties(ln=LineProperties(noFill=True))
        _axis(main.x_axis)
        _axis(main.y_axis, axis_format(lfmt, step))
        main.y_axis.scaling.min, main.y_axis.scaling.max, main.y_axis.majorUnit = lo, hi, step
        main.x_axis.crosses = "autoZero"
        if bars and lines:                                  # 같은 단위의 선을 같은 축 위에
            ln = LineChart()
            add(ln, lines, False, side_l)
            ln.y_axis.axId = main.y_axis.axId
            ln.x_axis = main.x_axis
            main += ln
        if right:                                           # 오른쪽 축: 다른 단위, 선만
            rv = [v for i in right for v in scaled(i)]
            rlo, rhi, rstep = nice_range(min(rv, default=0), max(rv, default=1), False)
            sec = LineChart()
            add(sec, right, False, "우")
            sec.y_axis.axId = 200
            sec.y_axis.crosses = "max"
            _axis(sec.y_axis, axis_format(rfmt, rstep))
            sec.y_axis.scaling.min, sec.y_axis.scaling.max, sec.y_axis.majorUnit = rlo, rhi, rstep
            main += sec
        if len(g) == 1:
            main.legend = None
        main.width, main.height = 12, 7                     # cm
        if main.legend:
            main.legend.overlay = False
        main.graphical_properties = GraphicalProperties(ln=LineProperties(noFill=True))
        ws.add_chart(main, f"B{anchor}")
        anchor += 15                                        # 7 cm = 행 높이 15pt 기준 14~15행

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def main() -> int:
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    brand = None
    if payload.get("ticker"):                               # 회사 상징색 (홈페이지 로고, 30일 저장). 실패하면 짙은 네이비
        try:
            from .brand import color
            brand = color(payload["ticker"]).get("color")
        except Exception:                                   # noqa: BLE001
            brand = None
    sys.stdout.buffer.write(build(payload, brand))
    return 0


if __name__ == "__main__":
    sys.exit(main())
