"""사업보고서 원문 재무제표 표 읽기 (tribunal/external/doc_fs.py). 가상 숫자"""
from datetime import date

from tribunal.external import doc_fs as df


def _t(rows, head=None):
    h = "".join("<TR>" + "".join(f"<TH{a}>{c}</TH>" for c, a in r) + "</TR>" for r in (head or []))
    return "<TABLE>" + h + "".join("<TR>" + "".join(f"<TD>{c}</TD>" for c in r) + "</TR>" for r in rows) + "</TABLE>"


def test_grid_unfolds_row_and_column_spans():
    """'과 목'·'주 석'이 두 줄을 차지하고 기간이 두 칸을 차지해도 칸 위치가 맞는다 (삼성카드 모양)"""
    tb = ('<TABLE><TR><TH ROWSPAN="2">과 목</TH><TH ROWSPAN="2">주 석</TH><TH COLSPAN="2">제9기 3분기</TH></TR>'
          '<TR><TH>3개월</TH><TH>누계</TH></TR><TR><TD>영업수익</TD><TD></TD><TD>10</TD><TD>30</TD></TR></TABLE>')
    g = df.grid(tb)
    assert g[1] == ["과 목", "주 석", "3개월", "누계"]
    cols = df._first_period(df._columns(g))
    assert cols == {"3m": [2], "cum": [3]}


def test_amounts_and_names():
    assert df.amt("(1,234)") == -1234 and df.amt("△5") == -5 and df.amt("268,409,129 =====") == 268409129
    assert df.amt("-") is None and df.amt("") is None
    assert df.norm("Ⅴ. 반(당)기순이익(주석 12)") == "당기순이익" == df.norm("당분기순이익") == df.norm("연결당기순이익(손실)")
    assert df.show("XIV. 이연법인세자산(주석 20)") == "이연법인세자산"


def _doc(q_label, period_line, unit="백만원", ni="50"):
    return ("<TITLE>III. 재무에 관한 사항</TITLE><TITLE>2. 연결재무제표</TITLE><TITLE>2-1. 연결 재무상태표</TITLE>"
            f"<P>연결 재무상태표 (단위 : {unit})</P>"
            + _t([["과 목", "제 9 기", "제 8 기"], ["자 산 총 계", "1,000", "900"], ["부 채 총 계", "600", "550"],
                  ["지배기업소유주지분", "380", "330"], ["자 본 총 계", "400", "350"]])
            + f"<TITLE>2-2. 연결 포괄손익계산서</TITLE><P>연결 포괄손익계산서 {period_line} (단위 : {unit})</P>"
            + _t([["과 목", q_label, "전기"], ["영업수익", "200", "150"], ["당기순이익", ni, "40"], ["지배기업 소유주지분", "45", "38"]])
            + "<TITLE>3. 연결재무제표 주석</TITLE>")


def test_read_takes_this_period_column_and_unit():
    out = df.read(_doc("제 9 기", "제 9 기 : 2020년 1월 1일부터 2020년 12월 31일까지"), "연결")
    v = df.pick({sj: df._one(out, sj, "plain", "cum") for sj in ("BS", "IS")})
    assert v == {"assets": 1000e6, "liab": 600e6, "equity": 400e6, "equity_owner": 380e6, "ni": 50e6, "ni_owner": 45e6, "revenue": 200e6}
    assert df.read(_doc("제 9 기", "", unit="달러"), "연결").get("BS") is None     # 단위를 모르면 쓰지 않는다


def test_single_income_column_is_year_to_date_or_three_months_by_start_date():
    cum = df.read(_doc("제 9 기 반기", "제 9 기 반기 : 2020년 1월 1일부터 2020년 6월 30일까지"), "연결")
    three = df.read(_doc("제 9 기 2분기", "제 9 기 2분기 : 2020년 4월 1일부터 2020년 6월 30일까지"), "연결")
    assert set(cum["IS"]) == {"cum"} and set(three["IS"]) == {"3m"}


class FakeDart:
    """기간 -> 원문. 2023년 원문 순이익만 바꿔 안전장치를 시험한다"""
    def __init__(self, ni_2023="50"):
        self.ni_2023 = ni_2023

    def annual_filings(self, corp, y):
        return [{"rcept_no": f"A{y}"}]

    def period_filings(self, corp, y, m):
        return []

    def document_text(self, rno):
        y = int(rno[1:])
        return _doc("제 9 기", f"{y}년 1월 1일부터 {y}년 12월 31일까지", ni=self.ni_2023 if y == 2023 else "50")


def _st():
    return {"fs": "연결", "annual": [{"period": "2023", "end": "2023-12-31", "assets": 1000e6, "liab": 600e6, "equity": 400e6, "ni": 50e6}],
            "quarter": [], "tables": {}, "sources": []}


def test_old_years_filled_only_when_2023_document_matches_api():
    st = _st()
    df.fill_old(FakeDart(), "x", st, today=date(2026, 1, 1))
    assert st["doc_check"]["ok"] is True
    assert [a["period"] for a in st["annual"]] == [str(y) for y in range(2016, 2024)]
    old = st["annual"][0]
    assert old["doc"] and old["ni"] == 50e6 and old["equity_owner"] == 380e6
    assert "doc|영업수익" in st["tables"]["IS"]["annual"]["2016"]

    st = _st()
    df.fill_old(FakeDart(ni_2023="70"), "x", st, today=date(2026, 1, 1))   # 원문 70 ≠ API 50 → 옛 해를 채우지 않는다
    assert st["doc_check"]["ok"] is False and [a["period"] for a in st["annual"]] == ["2023"]


def test_balance_sheet_that_does_not_add_up_is_blanked():
    a = [{"period": "2018", "assets": 100.0, "liab": 50.0, "equity": 40.0, "ni": 5.0},
         {"period": "2019", "assets": 110.0, "liab": 60.0, "equity": 50.0, "ni": 6.0}]
    df._sanity(a, [])
    assert "assets" not in a[0] and a[0]["ni"] == 5.0 and a[1]["assets"] == 110.0


def test_value_on_note_line_belongs_to_name_above():
    """'Ⅷ. 반기순이익' 줄은 비어 있고 값이 아래 '(비상위험준비금 반영후 조정이익 …' 설명 줄에 합쳐진 표 (현대해상 모양)"""
    text = ("<TITLE>2. 연결재무제표</TITLE><P>연결 포괄손익계산서 2020년 1월 1일부터 2020년 6월 30일까지 (단위 : 원)</P>"
            + _t([["과 목", "제 9 기 반기"], ["Ⅶ. 법인세비용", "10"], ["Ⅷ. 반기순이익", ""], ["(비상위험준비금 반영후 조정이익", "90"],
                  ["당 반 기: 150원", "90"], ["Vl. 기타포괄손익", "5"]])
            + "<TITLE>3. 연결재무제표 주석</TITLE>")
    rows = df.read(text, "연결")["IS"]["cum"]
    assert rows == [("법인세비용", 10.0), ("반기순이익", 90.0), ("기타포괄손익", 5.0)]
    assert df.pick({"IS": rows})["ni"] == 90.0


def test_loose_names_join_spelling_variants_only():
    """띄어쓰기·'_'·분기/반기 표기만 다른 이름은 같은 줄, 이름이 실제로 다르면 다른 줄"""
    assert df.loose("총분기포괄손익") == df.loose("(분기)총포괄손익") == df.loose("총포괄손익")
    assert df.loose("법인세비용차감전 반기순이익") == df.loose("법인세차감전순이익")
    assert df.loose("공정가치측정 _기타포괄손익금융자산 평가손익") == df.loose("공정가치측정_기타포괄손익금융자산 평가손익")
    assert df.loose("영업수익") != df.loose("순이자손익")
    assert df.lkeys(["기타", "이자수익", "기타"]) == ["기타", "이자수익", "기타#2"]      # 같은 표 안 같은 이름 두 번째 줄은 따로
