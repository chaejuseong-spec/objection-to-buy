"""KOSIS 산업 통계 대조 테스트. 실제 KOSIS 는 부르지 않는다."""
import pytest

from tribunal.external import kosis_check
from tribunal.external.kosis import Kosis, api_url
from tribunal.schemas import Report

TABLE = {"ORG_ID": "301", "ORG_NM": "한국은행", "TBL_ID": "DT_404Y016", "TBL_NM": "생산자물가지수(품목별)", "STRT_PRD_DE": "1965", "END_PRD_DE": "2026",
         "LINK_URL": "http://kosis.kr/statHtml/statHtml.do?orgId=301&tblId=DT_404Y016"}
SHIP = {"ORG_ID": "101", "ORG_NM": "통계청", "TBL_ID": "DT_SHIP", "TBL_NM": "품목별 출하량", "STRT_PRD_DE": "2015", "END_PRD_DE": "2025", "LINK_URL": "http://kosis.kr/x"}


def cell(c1, dt, unit, prd="202609", itm="생산자물가지수(품목별)"):
    return {"C1_NM": c1, "ITM_NM": itm, "DT": str(dt), "UNIT_NM": unit, "PRD_DE": prd}


class FakeKosis(Kosis):
    def __init__(self):
        super().__init__(key="x", offline=True)

    def search(self, words, n=8):
        return {"rows": [SHIP] if "출하" in words else [TABLE], "error": None, "fetched_at": "t", "api_url": "u"}

    def data(self, org, tbl, cycle, period):
        rows = {"DT_404Y016": [cell("총지수", 129.78, "2020＝100"), cell("라면", 127.2, "2020＝100"), cell("산소", 131.43, "2020＝100")],
                "DT_SHIP": [cell("라면", 1250, "천톤", prd="2025", itm="출하량")]}[tbl]
        return {"rows": rows, "error": None, "fetched_at": "2026-11-15T10:00:00", "api_url": api_url("Param/x", {"tblId": tbl})}


def run(rows, when="2026-11-10"):
    rep = Report.model_validate({"meta": {"ticker": "1", "name": "x", "report_date": when, "snapshot_date": when, "opinion": "매수"},
                                 "claims": [], "data_rows": rows})
    ext = kosis_check.run(rep, FakeKosis())
    return {r["stated_rid"]: r for r in ext["results"]}, ext


def row(rid, item, value, unit=""):
    return {"rid": rid, "item": item, "value": value, "unit": unit, "page": 1}


def test_같은_대상의_같은_값이면_일치():
    got, _ = run([row("R-1", "라면 생산자물가지수 2026년 9월", "127.2")])
    r = got["R-1"]
    assert r["status"] == "일치" and r["source"]["item"] == "라면" and r["source"]["url"].endswith("DT_404Y016")


def test_값만_같고_대상이_다르면_일치로_보지_않는다():
    got, _ = run([row("R-1", "라면 생산자물가지수 2026년 9월", "131.5")])      # 산소 131.43 과 가깝지만 라면이 아니다
    assert got["R-1"]["status"] == "검증 불가"


def test_못_찾으면_불일치가_아니라_검증_불가():
    got, _ = run([row("R-1", "라면 생산자물가지수 2026년 9월", "150.0")])
    assert got["R-1"]["status"] == "검증 불가" and "불일치로 보지 않았습니다" in got["R-1"]["reason"]


def test_대상이_없으면_합계_줄만_본다():
    got, _ = run([row("R-1", "생산자물가지수 2026년 9월", "129.78"), row("R-2", "생산자물가지수 2026년 9월", "127.2")])
    assert got["R-1"]["status"] == "일치" and got["R-2"]["status"] == "검증 불가"


def test_단위를_맞춰_비교한다():
    got, _ = run([row("R-1", "라면 국내 출하량 2025년", "125", "만톤"), row("R-2", "라면 국내 출하량 2025년", "1,250", "천톤"),
                  row("R-3", "라면 국내 출하량 2025년", "1,250", "천개")])
    assert got["R-1"]["status"] == "일치" and got["R-2"]["status"] == "일치" and got["R-3"]["status"] == "검증 불가"


def test_추정치와_다른_대조_대상은_건너뛴다():
    _, ext = run([row("R-1", "라면 출하량 2027E", "1,300", "천톤"), row("R-2", "가상식품 연결 매출액 2025년", "3,514,316", "백만원"),
                  row("R-3", "라면 시장 점유율 2025년", "55", "%")])
    assert ext["results"] == [] and {s["rid"] for s in ext["skipped"]} == {"R-1"}      # 점유율·매출액은 KOSIS 대상이 아니라 조용히 넘어간다


@pytest.mark.parametrize("unit,expected", [("백만원", (1e6, "원")), ("천톤", (1e3, "톤")), ("2020＝100", (1.0, "지수")), ("천달러", (1e3, "달러")), ("모름", (None, ""))])
def test_단위_읽기(unit, expected):
    assert kosis_check.split_unit(unit) == expected


def test_기록용_주소에는_키가_없다():
    assert "x" not in api_url("statisticsSearch.do", {"searchNm": "라면"}).split("apiKey=")[1].split("&")[0].replace("%7B%EC%9D%B8%EC%A6%9D%ED%82%A4%7D", "")
