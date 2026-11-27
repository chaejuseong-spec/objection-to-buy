"""고정 산식 검산 테스트. AI와 외부 API를 쓰지 않는다."""
import pytest

from tribunal import formula_check as fc, number_check
from tribunal.schemas import DataRow, Report


def report(rows, name="가상식품홀딩스"):
    return Report.model_validate({"meta": {"ticker": "900010", "name": name, "report_date": "2026-06-04", "snapshot_date": "2026-06-04", "opinion": "매수"},
                                  "claims": [], "data_rows": rows})


def row(rid, item, value, unit="", as_of="", page=1):
    return {"rid": rid, "item": item, "value": value, "unit": unit, "as_of": as_of, "page": page}


def by_name(results):
    return {r["name"].split(" = ")[0].split()[-1] if " = " in r["name"] else r["name"]: r for r in results}


@pytest.mark.parametrize("item,as_of,expected", [
    ("가상식품 연결 매출액 2026E", "", ("revenue", "가상식품", "2026E", "연결")),
    ("2027E OPM", "", ("opm", "", "2027E", "")),
    ("가상식품홀딩스 자산 총계", "2025년 기준", ("assets", "", "2025", "")),
    ("2027E 지배지분 기말자본", "", ("equity", "", "2027E", "")),
    ("가상식품홀딩스 현 배당성향", "2025년 결산", ("payout", "", "2025", "")),
    ("가상식품홀딩스 발행주식 수", "2026.06.04", ("shares", "", "", "")),
    ("코스피200 기업 PBR 2024년 결산 기준", "", ("pbr", "코스피200 기업", "2024", "")),
])
def test_이름표(item, as_of, expected):
    t = fc.tag(DataRow(rid="R-1", item=item, value="100", unit="%" if "성향" in item or "OPM" in item else "주" if "주식" in item else "원", as_of=as_of), "가상식품홀딩스")
    assert (t["metric"], t["company"], t["period"], t["fs"]) == expected


@pytest.mark.parametrize("item", ["가상식품홀딩스 목표 배당성향", "선진국 23개국 평균 PBR", "Implied PBR", "SOTP 목표 시가총액", "석유화학부문 매출 비중 2027E",
                                  "전사 OPM에 더해지는 폭", "가상식품홀딩스 주당 순자산가치", "매출액", "대주주 지분율"])
def test_산식에_쓰지_않는_숫자(item):
    assert fc.tag(DataRow(rid="R-1", item=item, value="10", unit="%"), "가상식품홀딩스") is None


def test_자산과_시가총액_검산():
    rows = [row("R-1", "가상식품홀딩스 자산 총계", "1 조 8,367 억 원", "조 원", "2025년 기준"), row("R-2", "가상식품홀딩스 부채 총계", "5,158 억 원", "억 원", "2025년 기준"),
            row("R-3", "가상식품홀딩스 자본 총계", "1 조 3,209 억 원", "조 원", "2025년 기준"), row("R-4", "가상식품홀딩스 현재주가", "90,200 원", "원"),
            row("R-5", "가상식품홀딩스 시가총액", "4,178 억 원", "억 원"), row("R-6", "가상식품홀딩스 발행주식 수", "4,637,790", "주")]
    got = by_name(fc.run(report(rows)))
    assert got["자산총계"]["status"] == "일치" and got["자산총계"]["expr"] == "R-2 , R-3"
    assert got["시가총액"]["status"] == "일치" and got["시가총액"]["diff_pct"] < 0.5


def test_틀린_값은_불일치():
    rows = [row("R-1", "자산 총계", "1 조 9,455 억 원", "원", "2025년"), row("R-2", "부채 총계", "5,158 억 원", "원", "2025년"),
            row("R-3", "자본 총계", "1 조 3,209 억 원", "원", "2025년"), row("R-4", "2027E 매출액", "2,400", "십억 원"),
            row("R-5", "2027E 영업이익", "264", "십억 원"), row("R-6", "2027E OPM", "9.9", "%")]
    got = by_name(fc.run(report(rows)))
    assert got["자산총계"]["status"] == "불일치" and got["영업이익률"]["status"] == "불일치" and got["영업이익률"]["computed"] == 11.0


def test_기준이_여럿인_산식은_작은_차이를_확인_불가로_둔다():
    rows = [row("R-1", "2027E 당기순이익", "198", "십억 원"), row("R-2", "2027E 지배지분 기말자본", "1,050", "십억 원"),
            row("R-3", "2027E ROE", "20.0", "%"), row("R-4", "2026E 당기순이익", "1,000", "십억 원"),
            row("R-5", "2026E 자본총계", "9,000", "십억 원"), row("R-6", "2026E ROE", "25.0", "%")]
    res = fc.run(report(rows, "한빛소재(가상)"))
    st = {r["stated_rid"]: r["status"] for r in res}
    assert st == {"R-3": "확인 불가", "R-6": "불일치"}        # 18.9% 대 20.0% 는 평균자본 차이일 수 있다. 11.1% 대 25% 는 아니다


def test_기간이나_회사가_다르면_묶지_않는다():
    rows = [row("R-1", "2027E 매출액", "2,400", "십억 원"), row("R-2", "2026E 영업이익", "121", "십억 원"), row("R-3", "2027E OPM", "11.0", "%"),
            row("R-4", "가상식품 연결 매출액 2025년", "3,514,316", "백만원"), row("R-5", "가상화학 연결 영업이익 2025년", "100", "백만원"), row("R-6", "가상식품 연결 영업이익률 2025년", "5.2", "%")]
    assert fc.run(report(rows)) == []


def test_AI가_이미_검산한_값은_다시_하지_않는다():
    rows = [row("R-1", "목표주가", "139,500 원", "원"), row("R-2", "현재주가", "90,200 원", "원"), row("R-3", "상승여력", "54.7%", "%")]
    rep = report(rows)
    assert len(fc.run(rep)) == 1
    assert fc.run(rep, [{"stated_rid": "R-3", "status": "일치"}]) == []
    assert len(fc.run(rep, [{"stated_rid": "R-3", "status": "확인 불가"}])) == 1      # AI 식이 계산되지 못했으면 코드가 한다


def test_같은_항목이_다르게_적혀_있으면_알린다():
    rows = [row("R-1", "가상식품 연결 매출액 2025년", "3,514,316", "백만원", page=1), row("R-2", "가상식품 연결 매출액 2025년", "3,541,316", "백만원", page=20),
            row("R-3", "현재주가", "90,200 원", "원", page=1), row("R-4", "현재주가", "90,200", "원", page=3)]
    res = fc.run(report(rows))
    assert len(res) == 1 and res[0]["status"] == "불일치" and set(res[0]["rids"]) == {"R-1", "R-2"}


def test_숫자_검증_목록에_붙이고_개수를_다시_센다():
    rows = [row("R-1", "자산 총계", "100", "억 원", "2025년"), row("R-2", "부채 총계", "40", "억 원", "2025년"), row("R-3", "자본 총계", "60", "억 원", "2025년"),
            row("R-4", "대주주 지분율", "42.92%", "%")]
    rep = report(rows)
    nc = fc.extend(number_check.run_checks(rep), rep)
    assert nc["count"] == {"일치": 1, "불일치": 0, "확인 불가": 0} and nc["n_unchecked"] == 1
    assert fc.extend(nc, rep)["count"] == nc["count"]            # 두 번 붙여도 겹치지 않는다


def test_공시_대조가_있어도_산식_검산은_따로_한다():
    rows = [row("R-1", "자산 총계", "100", "억 원", "2025년"), row("R-2", "부채 총계", "40", "억 원", "2025년"), row("R-3", "자본 총계", "60", "억 원", "2025년")]
    assert len(fc.run(report(rows), [{"stated_rid": "R-1", "status": "일치", "kind": "dart"}])) == 1


def test_상장주식수와_유통주식수는_다른_항목이라_쪽마다_다름으로_보지_않는다():
    """사례: 상장 100,000,000주, 유통 91,234,567주(자기주식 제외). 둘을 비교하면 오탐이다."""
    rows = [row("R-1", "가상생명 상장주식수", "100,000,000", "주", page=1),
            row("R-2", "가상생명 유통주식수", "91,234,567주", "주", page=25)]
    res = fc.run(report(rows, name="가상생명"), [])
    assert not [r for r in res if "쪽마다" in r["name"]]
    assert fc.tag(DataRow(**rows[1]), "가상생명")["metric"] == "shares_float"


def test_반올림해_적은_값은_쪽마다_다름으로_보지_않는다():
    """사례: 1쪽 목표주가 186,000원, 25쪽 계산표 186,108원 (반올림)."""
    rows = [row("R-1", "가상생명 목표주가", "186,000", "원", page=1),
            row("R-2", "가상생명 목표주가", "186,108", "원", page=25)]
    assert not [r for r in fc.run(report(rows, name="가상생명"), []) if "쪽마다" in r["name"]]


@pytest.mark.parametrize("a,b,same", [
    (186000, 186108, True), (186000, 185600, True),     # 1,000원 단위 반올림·버림
    (186000, 187200, False),                            # 단위보다 큰 차이
    (200000, 186108, False),                            # 단위 안이지만 1%를 넘음
    (186100, 186108, True), (186108, 186109, False),    # 끝자리 0이 없으면 반올림으로 보지 않는다
])
def test_반올림_판별(a, b, same):
    assert fc._rounded_same(a, b) is same
