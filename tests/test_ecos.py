"""한국은행 ECOS 거시 통계 대조 규칙 테스트. 실제 ECOS는 부르지 않고, ECOS 응답 모양의 가짜 데이터를 쓴다."""
from datetime import date

import pytest

from tribunal.external import ecos_check
from tribunal.external.ecos import Ecos, api_url
from tribunal.schemas import Report


def r(time, value, avg=True):
    return {"TIME": time, "DATA_VALUE": str(value), "ITEM_NAME2": "평균자료" if avg else "말일자료"}


class FakeEcos(Ecos):
    """(통계표, 주기) -> 시점별 값"""
    TABLE = {
        ("731Y004", "A"): [r("2023", 1305.41), r("2023", 1288.0, avg=False), r("2025", 1422.16)],
        ("731Y004", "Q"): [r("2026Q1", 1465.2)],
        ("731Y004", "M"): [r(f"20260{m}", v) for m, v in zip(range(1, 6), (1456.51, 1449.32, 1486.64, 1487.39, 1490.0))],
        ("731Y001", "D"): [r(f"202606{d:02d}", 1480 + d) for d in (1, 2)],
        ("722Y001", "D"): [r("20250226", 2.75), r("20250529", 2.5), r("20260602", 2.5)],     # 2025-05-29 에 2.75 -> 2.50
        ("901Y009", "A"): [r("2024", 114.18), r("2025", 116.46)],
    }

    def __init__(self):
        super().__init__(key="x", offline=True)

    def series(self, stat, cycle, start, end, item):
        rows = [x for x in self.TABLE.get((stat, cycle), []) if start <= x["TIME"][:len(start)] <= end]
        return {"rows": rows, "fetched_at": "2026-11-14T10:00:00", "api_url": api_url("StatisticSearch", [stat, cycle, start, end, item])}


def report(rows, when="2026-06-04"):
    return Report.model_validate({"meta": {"ticker": "900010", "name": "가상식품홀딩스", "report_date": when, "snapshot_date": when, "opinion": "매수"},
                                  "claims": [], "data_rows": rows})


def row(rid, item, value, unit="원", as_of=""):
    return {"rid": rid, "item": item, "value": value, "unit": unit, "as_of": as_of, "page": 21}


def got(rows, **kw):
    ext = ecos_check.run(report(rows, **kw), FakeEcos())
    return {x["stated_rid"]: x for x in ext["results"]}, ext


def test_연평균_환율은_공식_평균자료와_대조하고_말일자료는_쓰지_않는다():
    g, _ = got([row("R-1", "환율(원/달러) 2023", "1,306"), row("R-2", "원/달러 환율 2023년 평균", "1,288")])
    assert g["R-1"]["status"] == "일치" and "연평균" in g["R-1"]["reason"]
    assert g["R-2"]["status"] == "불일치"          # 말일자료 1,288 과 같지만 평균은 1,305.4


def test_분기와_여러_달_평균():
    g, _ = got([row("R-1", "환율(원/달러) 1Q26", "1,465"), row("R-2", "원/달러 환율 가정 (26년 1~5월 평균치 적용)", "1,474", as_of="2026E")])
    assert g["R-1"]["status"] == "일치"
    assert g["R-2"]["status"] == "일치" and "월평균 5개월" in g["R-2"]["reason"]


def test_1퍼센트_안의_차이는_검증_불가():
    g, _ = got([row("R-1", "환율(원/달러) 2025", "1,430")])         # 1,422.2 와 0.55% 차이
    assert g["R-1"]["status"] == "검증 불가" and "방식" in g["R-1"]["reason"]


def test_추정치와_끝나지_않은_기간은_대조하지_않는다():
    g, ext = got([row("R-1", "환율(원/달러) 2027E", "1,474"), row("R-2", "원/달러 환율 2026년 평균", "1,470")])
    assert "R-1" not in g and "추정" in ext["skipped"][0]["why"]
    assert g["R-2"]["status"] == "검증 불가" and "끝나지 않은" in g["R-2"]["reason"]


def test_기준금리는_날짜_값이고_휴일이면_직전_영업일():
    g, _ = got([row("R-1", "한국은행 기준금리", "2.50", "%", as_of="2026-06-03"), row("R-2", "기준금리 2025년", "2.75", "%")])
    assert g["R-1"]["status"] == "일치" and "직전 영업일" in g["R-1"]["reason"] or "직전 영업일" in dict(g["R-1"]["source"]["display"])["비교 값"]
    assert g["R-2"]["status"] == "일치"            # 2025년 중 2.75 -> 2.50 으로 바뀜: 첫 값 2.75 와 맞는다


def test_기준금리를_달로_적으면_그_달에_적용된_값과_비교한다():
    """리포트: '2025년 8월 기준금리 2.50%'. 기준금리는 바뀐 날에만 값이 있어도 그 달에 적용된 값을 찾는다"""
    g, _ = got([row("R-1", "한국은행 기준금리 2025년 8월", "2.50", "%"), row("R-2", "기준금리 2025년 8월", "3.00", "%")])
    assert g["R-1"]["status"] == "일치"
    assert g["R-2"]["status"] == "불일치"


def test_물가_상승률은_지수로_계산한다():
    g, _ = got([row("R-1", "소비자물가 상승률 2025년", "2.0", "%")])     # 116.46/114.18-1 = 2.0%
    assert g["R-1"]["status"] == "일치"


def test_다른_나라_금리와_지표가_아닌_것은_건너뛴다():
    g, ext = got([row("R-1", "미국 기준금리", "4.25", "%", as_of="2026-06-03"), row("R-2", "현재주가", "90,200 원")])
    assert g == {} and ext["skipped"] == []


def test_기록용_주소에는_키가_없다():
    u = api_url("StatisticSearch", ["1", "10", "731Y004", "A", "2023", "2023", "0000001"])
    assert "{인증키}" in u and u.startswith("https://ecos.bok.or.kr/api/StatisticSearch/")


@pytest.mark.parametrize("text,cycle,start,end", [
    ("환율(원/달러) 2023", "A", date(2023, 1, 1), date(2023, 12, 31)),
    ("1Q26", "Q", date(2026, 1, 1), date(2026, 3, 31)),
    ("2026년 2분기", "Q", date(2026, 4, 1), date(2026, 6, 30)),
    ("26년 1~5월 평균", "R", date(2026, 1, 1), date(2026, 5, 31)),
    ("2026년 5월", "M", date(2026, 5, 1), date(2026, 5, 31)),
    ("2026-06-03 기준", "D", date(2026, 6, 3), date(2026, 6, 3)),
])
def test_기간_읽기(text, cycle, start, end):
    p = ecos_check.parse_period(text, "2026-06-04")
    assert (p["cycle"], p["start"], p["end"]) == (cycle, start, end)


@pytest.mark.parametrize("text", ["무위험수익률(국고채 10년)", "국고채 3년물 금리", "회사채 3년 만기 AA-"])
def test_채권_만기는_연도로_읽지_않는다(text):
    """'국고채 10년'의 10년은 만기다. 2010년으로 읽으면 엉뚱한 해와 비교한다(실제 리포트 분석에서 생긴 오탐)."""
    assert ecos_check.parse_period(text, "2026-11-10") is None


def test_만기를_빼고도_기간은_읽는다():
    p = ecos_check.parse_period("국고채 10년물 월평균 2025년 4월", "2026-11-10")
    assert (p["cycle"], p["start"]) == ("M", date(2025, 4, 1))
