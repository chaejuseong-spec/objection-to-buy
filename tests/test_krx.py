"""KRX 시장 데이터 대조 규칙 테스트. 실제 KRX는 부르지 않고, KRX 응답 모양의 가짜 데이터를 쓴다."""
import pytest

from tribunal.external import common, krx_check
from tribunal.external.krx import Krx, api_url
from tribunal.schemas import Report

SHARES = 4_631_000


def day_row(close, lo, hi, code="900010", name="가상식품홀딩스"):
    return {"BAS_DD": "", "ISU_CD": code, "ISU_NM": name, "MKT_NM": "KOSPI", "TDD_CLSPRC": str(close),
            "TDD_LWPRC": str(lo), "TDD_HGPRC": str(hi), "MKTCAP": str(close * SHARES), "LIST_SHRS": str(SHARES)}


# 2026-06-03 은 휴장일(빈 날)로 둔다
PRICES = {"20260604": (91_000, 89_500, 92_000), "20260602": (90_200, 88_900, 90_800), "20260601": (88_800, 88_000, 89_900),
          "20260529": (88_000, 87_100, 88_700), "20260528": (87_500, 86_000, 88_000), "20260527": (86_900, 85_500, 87_200)}


class FakeKrx(Krx):
    def __init__(self, prices=PRICES):
        super().__init__(key="x", offline=True)
        self.prices = prices

    def day(self, endpoint, day):
        rows = []
        if endpoint == "stk_bydd_trd" and day in self.prices:
            c, lo, hi = self.prices[day]
            rows = [{**day_row(c, lo, hi), "BAS_DD": day}, {**day_row(50_000, 49_000, 51_000, "900020", "가상식품"), "BAS_DD": day}]
        return {"rows": rows, "fetched_at": "2026-11-14T10:00:00"}


def report(rows):
    return Report.model_validate({
        "meta": {"ticker": "900010", "name": "가상식품홀딩스", "report_date": "2026-06-04", "snapshot_date": "2026-06-04", "opinion": "매수"},
        "claims": [], "data_rows": rows})


def row(rid, item, value, unit, as_of="2026-06-04"):
    return {"rid": rid, "item": item, "value": value, "unit": unit, "as_of": as_of, "page": 1}


def one(rows, krx=None):
    ext = krx_check.run(report(rows), krx or FakeKrx())
    return {r["stated_rid"]: r for r in ext["results"]}, ext


def test_전_거래일_종가와_같으면_일치하고_날짜를_적는다():
    got, _ = one([row("R-01", "현재주가", "90,200 원", "원")])
    r = got["R-01"]
    assert r["status"] == "일치" and "2026-06-02" in r["reason"]
    s = r["source"]
    assert s["bas_dd"] == "2026-06-02" and s["market"] == "코스피" and s["timing"] == "작성 당시 공개"
    assert "basDd=20260602" in s["api_url"] and s["fetched_at"]


def test_거래된_범위_밖의_가격은_불일치():
    r = one([row("R-01", "현재주가", "120,000 원", "원")])[0]["R-01"]
    assert r["status"] == "불일치" and r["hue"] == "red" and "범위" in r["reason"]


def test_범위_안이지만_종가가_아니면_검증_불가():
    r = one([row("R-01", "현재주가", "89,000 원", "원")])[0]["R-01"]
    assert r["status"] == "검증 불가" and "장중" in r["reason"]


def test_시가총액은_억원_단위로_맞추고_다르면_검증_불가():
    got, _ = one([row("R-04", "시가총액", "4,177 억 원", "원"),         # 90,200 x 4,631,000 = 4,177.2억
                  row("R-05", "시가총액", "5,000 억 원", "원")])
    assert got["R-04"]["status"] == "일치"
    assert got["R-05"]["status"] == "검증 불가" and "우선주" in got["R-05"]["reason"]


def test_상장주식수():
    got, _ = one([row("R-06", "상장주식수", "4,631,000", "주"), row("R-07", "발행주식수", "4,631", "천주")])
    assert got["R-06"]["status"] == "일치" and got["R-07"]["status"] == "일치"


def test_기준일을_밝히면_그날과만_대조한다():
    got, _ = one([row("R-01", "현재주가", "88,000 원", "원", as_of="2026.05.29 종가 기준"),
                  row("R-02", "현재주가", "88,000 원", "원", as_of="2026.06.02 종가 기준")])
    assert got["R-01"]["status"] == "일치" and got["R-01"]["source"]["bas_dd"] == "2026-05-29"
    assert got["R-02"]["status"] == "불일치"          # 6/2 의 거래 범위 88,900~90,800 밖


def test_목표주가와_목표_시가총액은_대조하지_않는다():
    got, ext = one([row("R-02", "목표주가", "139,500 원", "원"), row("R-26", "SOTP 목표 시가총액", "647,068", "백만원"),
                    row("R-17", "주가누르기방지법 시행 전 증여세", "1,081억", "원")])
    assert got == {}
    assert {s["rid"] for s in ext["skipped"]} == {"R-02", "R-26", "R-17"}


def test_자회사_주가는_이름으로_찾는다():
    r = one([row("R-30", "가상식품 주가", "50,000 원", "원")])[0]["R-30"]
    assert r["status"] == "일치" and r["source"]["isu_nm"] == "가상식품"


def test_시세가_없으면_검증_불가():
    r = one([row("R-01", "현재주가", "90,200 원", "원")], FakeKrx(prices={}))[0]["R-01"]
    assert r["status"] == "검증 불가" and "찾지 못했습니다" in r["reason"]


def test_키가_없으면_대조_없이_넘어간다(monkeypatch, tmp_path):
    monkeypatch.delenv("KRX_API_KEY", raising=False)
    monkeypatch.setattr("tribunal.llm.ROOT", tmp_path)
    ext = krx_check.run(report([row("R-01", "현재주가", "90,200 원", "원")]), Krx(cache=tmp_path))
    assert ext["enabled"] is False and "KRX_API_KEY" in ext["error"]


def test_기록용_주소에는_키가_없다():
    assert api_url("stk_bydd_trd", "20260602") == "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd?basDd=20260602"


def test_공시와_시장_대조를_함께_붙인다():
    nc = {"results": [{"check_id": "K-1", "status": "일치"}], "count": {}}
    d = {"results": [{"check_id": "D-1", "kind": "dart", "status": "일치", "source": {"provider": "OpenDART (금융감독원 전자공시)"}}]}
    k = {"results": [{"check_id": "X-1", "kind": "krx", "status": "검증 불가", "source": {"provider": "KRX (한국거래소 정보데이터시스템)"}}]}
    m = common.merge(nc, d, k)
    assert [r["check_id"] for r in m["results"]] == ["K-1", "D-1", "X-1"]
    assert m["count"] == {"일치": 2, "불일치": 0, "확인 불가": 1}
    assert "OpenDART" in m["scope"] and "KRX" in m["scope"]
    m2 = common.merge(m, {"results": [{"check_id": "X-1", "kind": "krx", "status": "일치"}]})   # 다시 돌리면 바꿔 끼운다
    assert [r["status"] for r in m2["results"] if r["kind" if "kind" in r else "check_id"] == "krx"] == ["일치"]


@pytest.mark.parametrize("item,expected", [("현재주가", "현재주가"), ("시가총액", "시가총액"), ("목표주가", None), ("52주 최고 주가", None)])
def test_항목_구분(item, expected):
    from tribunal.schemas import DataRow
    info = krx_check.classify(DataRow(rid="R-1", item=item, value="1", unit="원"), "가상식품홀딩스", "2026-06-04")
    assert info.get("kind") == expected


def test_승인_안_된_시장은_건너뛰고_나머지_대조는_계속한다():
    """코스피만 승인된 상태에서 코스피에 없는 종목을 찾느라 코스닥 API를 부르면 401이 난다.
    그때 대조 전체를 멈추지 않고 그 시장만 건너뛴다(실제 리포트 분석에서 생긴 문제)."""
    from tribunal.external.krx import KrxAuthError

    class OnlyKospi(FakeKrx):
        def day(self, endpoint, day):
            if endpoint != "stk_bydd_trd":
                raise KrxAuthError("승인 없음")
            return super().day(endpoint, day)

    got_, ext = one([row("R-1", "다른회사 현재주가", "10,000원", "원"), row("R-2", "가상식품홀딩스 현재주가", "91,000원", "원")], OnlyKospi())
    assert ext["error"] is None and "건너뛰었습니다" in ext["note"]
    assert got_["R-2"]["status"] == "일치"


def test_우선주는_우선주_종목으로_찾는다():
    """'삼성전자 우선주 종가'를 보통주 시세와 대조해 불일치로 잡은 오탐(실제 리포트 분석)"""
    from tribunal.schemas import DataRow
    info = krx_check.classify(DataRow(rid="R-1", item="삼성전자 우선주 종가 2026-11-10", value="159,000", unit="원"), "가상생명", "2026-11-10")
    assert info["kind"] == "현재주가" and info["company"] == "삼성전자우"
    info = krx_check.classify(DataRow(rid="R-2", item="삼성전자 종가", value="198,900", unit="원"), "가상생명", "2026-11-10")
    assert info["company"] == "삼성전자"
    info = krx_check.classify(DataRow(rid="R-3", item="가상생명 기준주가 2026-11-10", value="244,500", unit="원"), "가상생명", "2026-11-10")
    assert info["kind"] == "현재주가" and info["company"] is None
    info = krx_check.classify(DataRow(rid="R-4", item="가상생명 기준주가 2026-11-10 종가", value="244,500", unit="원"), "가상생명", "2026-11-10")
    assert info["kind"] == "현재주가" and info["company"] is None
