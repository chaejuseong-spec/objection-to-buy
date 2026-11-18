"""표 검산 테스트. AI와 외부 API를 쓰지 않는다."""
from tribunal import formula_check, number_check, table_check
from tribunal.schemas import Report

YEARS = ["2023", "2024", "2025", "1Q26", "2026E"]


def report(tables, rows=()):
    return Report.model_validate({
        "meta": {"ticker": "900020", "name": "가상식품홀딩스", "report_date": "2026-06-04", "snapshot_date": "2026-06-04", "opinion": "매수"},
        "claims": [], "data_rows": list(rows), "tables": tables})


def is_table(name="추정 연결포괄손익계산서 (가상식품)", page=30, rev=(3410559, 3438731, 3514316, 934029, 3719950),
             yoy=(9.0, 0.8, 2.2, 7.0, 5.9), op=(212065, 163074, 183933, 67425, 201094), opm=(6.2, 4.7, 5.2, 7.2, 5.4)):
    return {"id": "T-1", "title": name, "page": page, "years": YEARS, "rows": [
        {"name": "매출액", "unit": "백만원", "kind": "amount", "values": list(rev)},
        {"name": "YoY(%)", "unit": "%", "kind": "ratio", "values": list(yoy)},
        {"name": "영업이익(손실)", "unit": "백만원", "kind": "amount", "values": list(op)},
        {"name": "OPM(%)", "unit": "%", "kind": "ratio", "values": list(opm)}]}


def checks(rep):
    return formula_check.extend(number_check.run_checks(rep), rep)["results"]


def bad(rep):
    return [r for r in checks(rep) if r["status"] == "불일치"]


def test_맞는_표에서는_불일치가_없다():
    assert bad(report([is_table()])) == []


def test_표의_칸이_회사_연결_기간_이름표를_달고_숫자가_된다():
    rows = {r.item: r for r in table_check.virtual_rows(report([is_table()]))}
    r = rows["가상식품 연결 매출액 2025"]
    assert r.value == "3,514,316" and r.unit == "백만원" and r.page == 30
    assert "가상식품 연결 영업이익 2026E" in rows                      # '(손실)'은 떼고 읽는다
    assert not any("YoY" in k for k in rows)                       # 증감률 행은 따로 검산


def test_증감률이_틀리면_잡는다():
    res = bad(report([is_table(yoy=(9.0, 0.8, 5.2, 7.0, 5.9))]))   # 2025 YoY 2.2% -> 5.2%
    assert [r["name"] for r in res] == ["매출액 2025 증감률 = 올해 ÷ 작년 − 1"]


def test_분기_열과_건너뛴_연도는_증감률을_보지_않는다():
    g = table_check.growth_checks(report([is_table()]))
    assert {r["name"].split()[1] for r in g} == {"2024", "2025"}   # 1Q26, 2026E(앞이 분기 열)는 뺀다


def test_표_안의_영업이익률이_틀리면_잡는다():
    res = bad(report([is_table(opm=(6.2, 4.7, 6.5, 7.2, 5.4))]))   # 2025 OPM 5.2% -> 6.5%
    assert len(res) == 1 and "영업이익률" in res[0]["name"] and "2025" in res[0]["name"]


def test_본문_숫자와_표가_다르면_쪽마다_다름으로_잡는다():
    row = {"rid": "R-01", "item": "가상식품 연결 매출액 2025", "value": "3조 6,143억 원", "unit": "원", "page": 5}
    res = bad(report([is_table()], [row]))
    assert len(res) == 1 and "쪽마다" in res[0]["name"] and set(res[0]["rids"]) == {"R-01", "T-1:0:2"}


def test_다른_회사_표와는_섞지_않는다():
    """가상식품홀딩스 리포트의 '가상식품' 표는 가상식품홀딩스 숫자와 비교하지 않는다"""
    row = {"rid": "R-01", "item": "가상식품홀딩스 매출액 2025", "value": "31,721", "unit": "백만원", "page": 2}
    assert bad(report([is_table()], [row])) == []


def test_적자_기저면_증감률을_보지_않는다():
    t = is_table(rev=(-100, 50, 3514316, 934029, 3719950), yoy=(0, 999.0, 2.2, 7.0, 5.9))
    names = [r["name"] for r in table_check.growth_checks(report([t]))]
    assert not any("2024" in n for n in names)
