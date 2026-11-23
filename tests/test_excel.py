"""엑셀 내보내기(tribunal/external/excel.py): 서식 규칙과 단위 (가상 숫자)"""
import io

from openpyxl import load_workbook

from tribunal.external import excel


def _payload():
    return {"title": "가나생명 추이 비교", "basis": "연결 기준", "periods": ["2025.06", "2025.09", "2025.12"],
            "rows": [{"label": "가나생명", "fmt": "won", "kind": "line", "strong": True, "values": [1.2e13, 1.3e13, None], "src": "DART"},
                     {"label": "다라생명", "fmt": "won", "kind": "line", "values": [9e12, -5e11, 8.8e12], "src": "FISIS"},
                     {"label": "ROE", "fmt": "pct", "kind": "line", "calc": True, "values": [7.5, 8.25, 9.0]}],
            "notes": ["ROE: 지배주주 순이익 ÷ 평균 지배주주 자본"], "sources": ["OpenDART", "금융감독원 금융통계정보시스템"]}


def test_units_formats_and_style():
    wb = load_workbook(io.BytesIO(excel.build(_payload())))
    ws = wb.active
    assert ws.sheet_view.showGridLines is False
    assert ws["B2"].value == "가나생명 추이 비교 (단위: 십억원)"
    assert [ws.cell(row=5, column=c).value for c in range(2, 7)] == ["항목", "구분", "2Q25", "3Q25", "4Q25"]
    assert ws["D6"].value == 12000 and ws["F6"].value is None              # 원 -> 십억원, 빈 값은 빈 칸
    assert ws["E7"].value == -500 and ws["E7"].number_format == "#,##0;(#,##0);-"   # 음수 괄호
    assert ws["D8"].value == 0.075 and ws["D8"].number_format == "0.0%;(0.0%);-"
    assert ws["B8"].value == "ROE (%)" and ws["C8"].value == "계산" and ws["C7"].value == "FISIS"
    assert ws["B6"].font.bold and ws["B6"].fill.fgColor.rgb.endswith(excel.palette(None)[2])    # 강조행 (상징색을 옅게)
    assert ws["B5"].font.color.rgb.endswith("1E3A5F") and ws["B5"].font.name == "맑은 고딕"          # 상징색 없으면 짙은 네이비
    assert ws["D8"].border.bottom.style == "medium"                           # 표 맨 아래 굵은 선
    assert len(ws._charts) == 2                                              # 금액 차트, 비율 차트  # noqa: SLF001


def test_nice_range_keeps_zero_for_bars():
    assert excel.nice_range(3, 9, True)[0] == 0
    lo, hi, _ = excel.nice_range(160, 210, False)
    assert lo > 0 and hi >= 210


def test_bright_brand_color_is_darkened_for_text():
    main, ink, tint = excel.palette("#FFBC00")                  # 밝은 노랑: 막대는 그대로, 글자·선은 읽히게 어둡게
    assert main == "FFBC00" and ink != main
    assert excel.palette("#1428A0")[1] == "1428A0"              # 충분히 어두우면 그대로


def test_brand_svg_and_logo_candidates():
    from tribunal.external import brand
    assert brand.svg_color('<svg><path fill="#1428A0"/><path fill="#ffffff"/><path fill="#1428a0"/></svg>') == "#1428A0"
    html = '<img src="/img/main_banner_logo.png" alt="logo"><img src="/img/gnb_logo.svg" class="logo"><link rel="icon" href="/fav.png">'
    kinds = brand.candidates(html, "https://example.com/")
    assert kinds[0] == ("img", "https://example.com/img/gnb_logo.svg")        # 배너 그림은 빼고 로고 먼저


def test_peer_compare_makes_one_chart_per_metric():
    rows = [{"label": "순이익 · 가나생명", "legend": "가나생명", "group": "ni", "fmt": "won", "kind": "bar", "strong": True, "values": [1e12, 2e12]},
            {"label": "순이익 · 다라생명", "legend": "다라생명", "group": "ni", "fmt": "won", "kind": "bar", "values": [5e11, 6e11]},
            {"label": "자본 · 가나생명", "legend": "가나생명", "group": "eq", "fmt": "won", "kind": "line", "strong": True, "values": [9e12, 9.5e12]}]
    ws = load_workbook(io.BytesIO(excel.build({"title": "t", "periods": ["2025", "2026"], "rows": rows}))).active
    assert len(ws._charts) == 2                                              # 같은 단위라도 지표마다 차트  # noqa: SLF001


def test_together_makes_one_dual_axis_chart():
    p = _payload()
    p["together"] = True
    ws = load_workbook(io.BytesIO(excel.build(p))).active
    assert len(ws._charts) == 1                                              # 금액(좌) + 비율(우) 한 차트  # noqa: SLF001
