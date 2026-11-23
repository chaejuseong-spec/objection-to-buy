"""OpenDART 공시 대조 규칙 테스트. 실제 DART는 부르지 않고, DART 응답 모양의 가짜 데이터를 쓴다."""
import pytest

from tribunal.external import dart_check
from tribunal.external.dart import Dart, rcept_date, original_url
from tribunal.schemas import DataRow, Report

HOLD = {"corp_code": "00100001", "corp_name": "가상식품홀딩스", "stock_code": "900010"}
NONG = {"corp_code": "00100002", "corp_name": "가상식품", "stock_code": "900020"}


def acc(account_id, name, amount, sj="IS", rno="20260312000123"):
    return {"rcept_no": rno, "sj_div": sj, "sj_nm": "손익계산서" if sj == "IS" else "재무상태표",
            "account_id": account_id, "account_nm": name, "thstrm_nm": "제63기", "thstrm_amount": str(amount)}


class FakeDart(Dart):
    """corp_code -> 연도 -> CFS/OFS -> 계정 목록"""
    def __init__(self, data, filings=None):
        super().__init__(key="x", offline=True)
        self.data, self.filings = data, filings or {}

    def corps(self):
        return [HOLD, NONG]

    def statements(self, corp_code, year, fs_div, reprt_code="11011"):
        rows = self.data.get(corp_code, {}).get(year, {}).get(fs_div)
        if rows is None:
            return {"status": "013", "list": [], "fetched_at": "2026-11-14T10:00:00"}
        return {"status": "000", "list": rows, "fetched_at": "2026-11-14T10:00:00"}

    def annual_filings(self, corp_code, year):
        return self.filings.get((corp_code, year), [{"rcept_no": "20260312000123", "rcept_dt": "20260312",
                                                     "report_nm": f"사업보고서 ({year}.12)"}])


DATA = {
    "00100001": {2025: {
        "CFS": [acc("ifrs-full_Assets", "자산총계", 1_836_712_000_000, "BS"), acc("ifrs-full_Equity", "자본총계", 1_400_000_000_000, "BS")],
        "OFS": [acc("ifrs-full_Assets", "자산총계", 900_000_000_000, "BS"), acc("ifrs-full_Equity", "자본총계", 1_320_940_000_000, "BS"),
                acc("", "배당금수익", 31_721_000_000)],
    }},
    "00100002": {2025: {
        "CFS": [acc("ifrs-full_Revenue", "매출액", 3_514_316_000_000), acc("dart_OperatingIncomeLoss", "영업이익", 190_000_000_000)],
    }},
}


def report(rows):
    return Report.model_validate({
        "meta": {"ticker": "900010", "name": "가상식품홀딩스", "report_date": "2026-06-04", "snapshot_date": "2026-06-04", "opinion": "매수"},
        "claims": [], "data_rows": rows})


def row(rid, item, value, unit, as_of="", page=1):
    return {"rid": rid, "item": item, "value": value, "unit": unit, "as_of": as_of, "page": page}


def by_rid(ext):
    return {r["stated_rid"]: r for r in ext["results"]}


def test_연결_매출액_자회사_일치_와_출처():
    ext = dart_check.run(report([row("R-13", "가상식품 연결 매출액 2025년", "3,514,316", "백만원")]), FakeDart(DATA))
    r = by_rid(ext)["R-13"]
    assert r["status"] == "일치" and r["hue"] == "green"
    s = r["source"]
    assert s["corp_name"] == "가상식품" and s["fs"] == "연결" and s["rcept_dt"] == "2026-03-12"
    assert s["timing"] == "작성 당시 공개" and s["url"].endswith("rcpNo=20260312000123")
    assert s["fetched_at"] and s["report_nm"] == "사업보고서 (2025.12)"


def test_작성_당시_공시와_다르면_불일치():
    ext = dart_check.run(report([row("R-15", "가상식품 연결 영업이익 2025년", "183,933", "백만원")]), FakeDart(DATA))
    r = by_rid(ext)["R-15"]
    assert r["status"] == "불일치" and r["hue"] == "red" and r["diff_pct"] > 3


def test_연결_별도_표기가_없으면_맞는_쪽을_찾고_없으면_검증_불가():
    rows = [row("R-07", "자본 총계", "1 조 3,209 억 원", "원", as_of="2025년 결산 기준"),    # 별도 1조 3,209억과 맞음
            row("R-05", "자산 총계", "1 조 8,000 억 원", "원", as_of="2025년 결산 기준")]    # 연결 1조 8,367억, 별도 9,000억 둘 다 안 맞음
    got = by_rid(dart_check.run(report(rows), FakeDart(DATA)))
    assert got["R-07"]["status"] == "일치" and got["R-07"]["source"]["fs"] == "별도" and "표기가 없어" in got["R-07"]["reason"]
    assert got["R-05"]["status"] == "검증 불가" and "연결/별도 표기가 없고" in got["R-05"]["reason"]


def test_별도_배당금수익():
    r = by_rid(dart_check.run(report([row("R-11", "가상식품홀딩스 별도 배당금수익 2025년", "31,721", "백만 원")]), FakeDart(DATA)))["R-11"]
    assert r["status"] == "일치" and r["source"]["corp_name"] == "가상식품홀딩스" and r["source"]["fs"] == "별도"


def test_추정치_비율_분기는_건너뛰고_이유를_남긴다():
    rows = [row("R-14", "가상식품 연결 매출액 2026E", "3,719,950", "백만원"),
            row("R-30", "영업이익률 2025년", "5.2", "%"),
            row("R-31", "2025년 하반기 매출액", "900,000", "백만원"),
            row("R-01", "현재주가", "90,200 원", "원")]
    ext = dart_check.run(report(rows), FakeDart(DATA))
    assert ext["results"] == []
    why = {s["rid"]: s["why"] for s in ext["skipped"]}
    assert "추정치" in why["R-14"] and "비율" in why["R-30"] and "하반기" in why["R-31"] and "R-01" not in why


def test_데이터가_없으면_추정하지_않고_검증_불가():
    rows = [row("R-40", "가상식품 연결 매출액 2024년", "3,400,000", "백만원"),      # 2024년 자료 없음
            row("R-41", "오뚜기 연결 매출액 2025년", "3,000,000", "백만원"),    # 목록에 없는 회사
            row("R-42", "매출액", "3,514,316", "백만원")]                      # 연도 없음
    got = by_rid(dart_check.run(report(rows), FakeDart(DATA)))
    assert all(got[k]["status"] == "검증 불가" for k in ("R-40", "R-41", "R-42"))
    assert "찾지 못했습니다" in got["R-40"]["reason"] and "오뚜기" in got["R-41"]["reason"] and "찾지 못했습니다" in got["R-42"]["reason"]


def test_작성_이후_공시와만_다르면_불일치가_아니라_검증_불가():
    late = {k: {2025: {"CFS": [acc("dart_OperatingIncomeLoss", "영업이익", 190_000_000_000, rno="20260801000999")]}} for k in ["00100002"]}
    filings = {("00100002", 2025): [{"rcept_no": "20260801000999", "rcept_dt": "20260801", "report_nm": "사업보고서 (2025.12)"}]}
    r = by_rid(dart_check.run(report([row("R-15", "가상식품 연결 영업이익 2025년", "183,933", "백만원")]), FakeDart(late, filings)))["R-15"]
    assert r["status"] == "검증 불가" and r["source"]["timing"] == "작성 이후 공개" and "공시 전" in r["reason"]


def test_정정_공시는_원본이_작성_전이면_따로_표시():
    late = {"00100002": {2025: {"CFS": [acc("ifrs-full_Revenue", "매출액", 3_514_316_000_000, rno="20260801000999")]}}}
    filings = {("00100002", 2025): [{"rcept_no": "20260312000123", "rcept_dt": "20260312", "report_nm": "사업보고서 (2025.12)"},
                                    {"rcept_no": "20260801000999", "rcept_dt": "20260801", "report_nm": "[기재정정]사업보고서 (2025.12)"}]}
    r = by_rid(dart_check.run(report([row("R-13", "가상식품 연결 매출액 2025년", "3,514,316", "백만원")]), FakeDart(late, filings)))["R-13"]
    assert r["status"] == "일치" and r["source"]["timing"] == "작성 당시 공개, 이후 정정"
    assert r["source"]["report_nm"].startswith("[기재정정]")


def test_키가_없으면_대조_없이_넘어간다(monkeypatch, tmp_path):
    monkeypatch.delenv("DART_API_KEY", raising=False)
    monkeypatch.setattr("tribunal.llm.ROOT", tmp_path)          # .env 를 읽지 못하게
    ext = dart_check.run(report([row("R-13", "가상식품 연결 매출액 2025년", "3,514,316", "백만원")]), Dart(cache=tmp_path))
    assert ext["enabled"] is False and "DART_API_KEY" in ext["error"] and ext["results"] == []


def test_숫자_검증_목록에_붙고_검증_불가는_확인_불가_칸에_센다():
    nc = {"results": [{"check_id": "K-1", "status": "일치"}], "count": {"일치": 1, "불일치": 0, "확인 불가": 0}}
    ext = {"results": [{"check_id": "D-1", "kind": "dart", "status": "불일치"}, {"check_id": "D-2", "kind": "dart", "status": "검증 불가"}]}
    m = dart_check.merge(nc, ext)
    assert [r["check_id"] for r in m["results"]] == ["K-1", "D-1", "D-2"]
    assert m["count"] == {"일치": 1, "불일치": 1, "확인 불가": 1}


def test_접수번호와_원문_링크():
    assert rcept_date("20260312000123") == "2026-03-12"
    assert original_url("20260312000123") == "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260312000123"


@pytest.mark.parametrize("item,as_of,expected", [
    ("가상식품 연결 매출액 2025년", "", (2025, "CFS", "가상식품")),
    ("자산 총계", "2025년 결산 기준", (2025, None, None)),
    ("가상식품홀딩스 별도 배당금수익 FY2024", "", (2024, "OFS", None)),
    ("매출액", "2026-06-04", (None, None, None)),          # 날짜(작성일)는 기준 연도로 보지 않는다
])
def test_기준_연도_연결별도_회사_읽기(item, as_of, expected):
    info = dart_check.classify(DataRow(rid="R-1", item=item, value="1", unit="백만원", as_of=as_of), "가상식품홀딩스")
    assert (info["year"], info["fs"], info["company"]) == expected


def test_값에_조억이_적혀_있으면_단위_칸과_상관없이_원으로_읽는다():
    rows = [row("R-05", "가상식품홀딩스 연결 자산 총계 2025년", "1 조 8,367 억 원", "조 원"),
            row("R-06", "가상식품홀딩스 연결 자산 총계 2025년", "18,367", "억 원")]
    got = by_rid(dart_check.run(report(rows), FakeDart(DATA)))
    assert got["R-05"]["status"] == "일치" and got["R-06"]["status"] == "일치"
    assert got["R-05"]["stated"] == "1 조 8,367 억 원"            # '…억 원조 원' 으로 찍지 않는다


# ---------- 연결/별도 표기가 없는 숫자가 틀렸을 때 (사각지대) ----------
def test_표기가_없어도_한쪽에_훨씬_가까우면_그_기준으로_불일치를_잡는다():
    # 연결 1조 8,367억, 별도 9,000억. 리포트 1조 9,455억은 연결과 6%, 별도와 116% 차이
    r = by_rid(dart_check.run(report([row("R-05", "자산 총계", "1 조 9,455 억 원", "원", as_of="2025년 결산 기준")]), FakeDart(DATA)))["R-05"]
    assert r["status"] == "불일치" and r["basis"] == {"fs": "연결", "how": "가까운 값", "year": "표기"}
    assert "훨씬 가까워" in r["reason"] and r["source"]["fs"] == "연결"


def test_같은_표의_다른_숫자가_맞은_기준을_따른다():
    data = {"00100001": {2025: {
        "CFS": [acc("ifrs-full_Assets", "자산총계", 1_000_000_000_000, "BS"), acc("ifrs-full_Liabilities", "부채총계", 400_000_000_000, "BS"),
                acc("ifrs-full_Equity", "자본총계", 600_000_000_000, "BS")],
        "OFS": [acc("ifrs-full_Assets", "자산총계", 800_000_000_000, "BS"), acc("ifrs-full_Liabilities", "부채총계", 300_000_000_000, "BS"),
                acc("ifrs-full_Equity", "자본총계", 500_000_000_000, "BS")]}}}
    rows = [row("R-1", "자산 총계", "1 조 원", "원", as_of="2025년"), row("R-2", "부채 총계", "4,000 억 원", "원", as_of="2025년"),
            row("R-3", "자본 총계", "5,500 억 원", "원", as_of="2025년")]     # 연결 6,000억과 8%, 별도 5,000억과 10%: 가까운 쪽으로는 못 가른다
    got = by_rid(dart_check.run(report(rows), FakeDart(data)))
    assert got["R-1"]["status"] == "일치" and got["R-2"]["status"] == "일치"
    assert got["R-3"]["status"] == "불일치" and got["R-3"]["basis"] == {"fs": "연결", "how": "같은 표", "year": "표기"}
    assert "_group" not in got["R-3"] and "_open" not in got["R-3"]


def test_가를_수_없으면_여전히_검증_불가():
    data = {"00100001": {2025: {"CFS": [acc("ifrs-full_Assets", "자산총계", 1_000_000_000_000, "BS")],
                                "OFS": [acc("ifrs-full_Assets", "자산총계", 950_000_000_000, "BS")]}}}
    r = by_rid(dart_check.run(report([row("R-1", "자산 총계", "1 조 1,000 억 원", "원", as_of="2025년")]), FakeDart(data)))["R-1"]
    assert r["status"] == "검증 불가"          # 연결과 10%, 별도와 16%: 어느 기준인지 알 수 없다


def test_두_근거가_서로_다른_쪽을_가리키면_판정하지_않는다():
    # 자본은 별도와 맞고(같은 표 근거: 별도), 자산은 연결에 가깝다(가까운 값 근거: 연결) -> 검증 불가
    rows = [row("R-07", "자본 총계", "1 조 3,209 억 원", "원", as_of="2025년 결산 기준"),
            row("R-05", "자산 총계", "1 조 8,000 억 원", "원", as_of="2025년 결산 기준")]
    got = by_rid(dart_check.run(report(rows), FakeDart(DATA)))
    assert got["R-07"]["status"] == "일치" and got["R-05"]["status"] == "검증 불가"


# ---------- 배당에 관한 사항 ----------
def _div(se, th, fr, lw, knd="-"):
    return {"se": se, "stock_knd": knd, "thstrm": th, "frmtrm": fr, "lwfr": lw, "rcept_no": "20260318001210"}


class DivDart(FakeDart):
    def dividends(self, corp_code, year, reprt_code="11011"):
        if year != 2025:
            return {"status": "013", "list": [], "fetched_at": "2026-11-14T10:00:00"}
        return {"status": "000", "fetched_at": "2026-11-14T10:00:00", "list": [
            _div("(연결)현금배당성향(%)", "12.14", "15.51", "17.27"), _div("주당 현금배당금(원)", "3,000", "2,500", "2,500", "보통주"),
            _div("주당 현금배당금(원)", "-", "-", "-", "우선주"), _div("(연결)주당순이익(원)", "24,706", "16,118", "14,478"),
            _div("현금배당금총액(백만원)", "13,913", "11,594", "11,594")]}


def div_run(rows):
    return by_rid(dart_check.run(report(rows), DivDart(DATA)))


def test_배당성향_주당배당금_주당순이익_대조():
    got = div_run([row("R-1", "가상식품홀딩스 현 배당성향", "12.14%", "%", as_of="2025년 결산"), row("R-2", "가상식품홀딩스 DPS 2025년", "3,000원", "원"),
                   row("R-3", "가상식품홀딩스 연결 EPS 2025년", "24,706", "원"), row("R-4", "가상식품홀딩스 배당금 총액 2025년", "139 억 원", "원")])
    assert [got[k]["status"] for k in ("R-1", "R-2", "R-3", "R-4")] == ["일치"] * 4
    s = got["R-1"]["source"]
    assert s["api"] == "배당에 관한 사항" and s["fs"] == "연결" and s["timing"] == "작성 당시 공개" and s["url"].endswith("20260318001210")


def test_틀린_배당성향은_불일치():
    r = div_run([row("R-1", "가상식품홀딩스 현 배당성향", "13.72%", "%", as_of="2025년 결산")])["R-1"]
    assert r["status"] == "불일치" and r["computed"] == "12.14%"


def test_연도가_없으면_최근_3개_연도에서_찾고_없으면_검증_불가():
    got = div_run([row("R-1", "가상식품홀딩스 주당 배당금", "2,500", "원"), row("R-2", "가상식품홀딩스 주당 배당금", "2,800", "원")])
    assert got["R-1"]["status"] == "일치" and "2024년" in got["R-1"]["name"]
    assert got["R-2"]["status"] == "검증 불가"


def test_목표_배당성향과_추정치는_대조하지_않는다():
    ext = dart_check.run(report([row("R-1", "가상식품홀딩스 목표 배당성향", "25%", "%"), row("R-2", "2027E DPS", "5,000", "원")]), DivDart(DATA))
    assert ext["results"] == [] and len(ext["skipped"]) == 2


def test_주당순이익을_당기순이익_금액으로_읽지_않는다():
    info = dart_check.classify(DataRow(rid="R-1", item="가상식품홀딩스 주당순이익 2025년", value="24,706", unit="원"), "가상식품홀딩스")
    assert info == {"skip": "재무제표 계정이 아님"}



# ---------- 분기·반기 실적 ----------
def qacc(account_id, name, q3m, cum, sj="IS", rno="20260515000865"):
    return {"rcept_no": rno, "sj_div": sj, "sj_nm": "손익계산서", "account_id": account_id, "account_nm": name,
            "thstrm_nm": "제63기", "thstrm_amount": str(q3m), "thstrm_add_amount": str(cum)}


class QuarterDart(FakeDart):
    """(연도, 보고서 코드) -> 계정. 1분기 11013, 반기 11012, 3분기 11014, 사업 11011"""
    Q = {(2026, "11013"): [qacc("ifrs-full_Revenue", "매출액", 934_029_000_000, 934_029_000_000),
                           qacc("dart_OperatingIncomeLoss", "영업이익", 67_424_701_255, 67_424_701_255)],
         (2025, "11012"): [qacc("ifrs-full_Revenue", "매출액", 867_725_000_000, 1_760_761_000_000, rno="20250814002291")],
         (2025, "11014"): [qacc("dart_OperatingIncomeLoss", "영업이익", 54_375_000_000, 150_579_000_000, rno="20251114001491")],
         (2025, "11011"): [qacc("dart_OperatingIncomeLoss", "영업이익", 183_933_000_000, 183_933_000_000, rno="20260311003892")]}

    def statements(self, corp_code, year, fs_div, reprt_code="11011"):
        rows = self.Q.get((year, reprt_code)) if corp_code == NONG["corp_code"] and fs_div == "CFS" else None
        if rows is None:
            return {"status": "013", "list": [], "fetched_at": "2026-11-14T10:00:00"}
        return {"status": "000", "list": rows, "fetched_at": "2026-11-14T10:00:00"}

    def period_filings(self, corp_code, year, month):
        return []


def qrun(rows, when="2026-06-04"):
    rep = Report.model_validate({"meta": {"ticker": "900010", "name": "가상식품홀딩스", "report_date": when, "snapshot_date": when, "opinion": "매수"},
                                 "claims": [], "data_rows": rows})
    return by_rid(dart_check.run(rep, QuarterDart(DATA)))


def test_1분기_값은_분기보고서와_대조한다():
    got = qrun([row("R-1", "가상식품 연결 영업이익 1Q26", "67,425", "백만원"), row("R-2", "가상식품 연결 매출액 1Q26", "950,000", "백만원")])
    assert got["R-1"]["status"] == "일치" and "1분기" in got["R-1"]["name"]
    assert got["R-1"]["source"]["report_nm"] == "분기보고서 (2026.03)" and got["R-1"]["source"]["timing"] == "작성 당시 공개"
    assert got["R-2"]["status"] == "불일치"


def test_반기_누적과_분기_단독을_구분한다():
    got = qrun([row("R-1", "가상식품 연결 매출액 2025년 상반기", "1,760,761", "백만원"),     # 누적과 같다
                row("R-2", "가상식품 연결 매출액 2Q25", "867,725", "백만원"),                # 2분기 3개월과 같다
                row("R-3", "가상식품 연결 매출액 2Q25", "1,760,761", "백만원")])             # 2분기라고 하고 누적 값을 적음
    assert got["R-1"]["status"] == "일치" and "누적" in got["R-1"]["reason"]
    assert got["R-2"]["status"] == "일치"
    assert got["R-3"]["status"] == "불일치"


def test_4분기는_연간에서_3분기_누적을_빼서_대조한다():
    r = qrun([row("R-1", "가상식품 연결 영업이익 4Q25", "33,354", "백만원")])["R-1"]        # 183,933 - 150,579 = 33,354
    assert r["status"] == "일치" and "3분기 보고서 누적 값을 뺀" in r["reason"]


def test_작성일에_끝나지_않은_분기는_대조하지_않는다():
    r = qrun([row("R-1", "가상식품 연결 영업이익 2Q26", "60,000", "백만원")])["R-1"]
    assert r["status"] == "검증 불가" and "끝나지 않은" in r["reason"]



# ---------- 기준 연도가 없는 숫자 ----------
def test_연도가_없으면_직전_결산연도로_보고_틀리면_확인_필요까지만():
    rows = [row("R-1", "가상식품홀딩스 자산 총계", "1 조 8,367 억 원", "조 원"),          # 연도 표기 없음, 값은 맞음
            row("R-2", "가상식품홀딩스 자본 총계", "1 조 2,920 억 원", "조 원")]          # 연도 표기 없음, 값이 틀림
    got = by_rid(dart_check.run(report(rows), FakeDart(DATA)))
    assert got["R-1"]["status"] == "일치" and "직전 결산연도(2025년)" in got["R-1"]["reason"]
    assert got["R-2"]["status"] == "불일치" and got["R-2"]["basis"]["year"] == "추정"
    from tribunal import rules
    assert rules.code_verdict(got["R-2"]) == "확인 필요"          # 연도를 짐작했으니 '수정'까지는 가지 않는다


def test_연도도_없고_값도_크게_다르면_검증_불가():
    r = by_rid(dart_check.run(report([row("R-1", "가상식품홀딩스 자산 총계", "3 조 원", "원")]), FakeDart(DATA)))["R-1"]
    assert r["status"] == "검증 불가"          # 연결·별도 어느 쪽, 어느 연도와도 크게 달라 판단하지 않는다


def test_현_배당성향은_직전_결산연도():
    got = div_run([row("R-1", "가상식품홀딩스 현 배당성향", "13.72%", "%"), row("R-2", "가상식품홀딩스 현 배당성향", "12.14%", "%")])
    assert got["R-1"]["status"] == "불일치" and got["R-2"]["status"] == "일치" and "'현재' 값" in got["R-2"]["reason"]


# ---------- 보험사: 보험손익, CSM(XBRL 주석), 지급여력비율(공시 본문) (2026-11-21) ----------
LIFE = {"corp_code": "00100003", "corp_name": "가상생명", "stock_code": "900030"}
SEP = {"ConsolidatedAndSeparateFinancialStatementsAxis": "SeparateMember", "DisaggregationOfInsuranceContractsAxis": "InsuranceContractsIssuedMember"}
CON = {**SEP, "ConsolidatedAndSeparateFinancialStatementsAxis": "ConsolidatedMember"}
CSM = "ContractualServiceMargin"
NEW = "IncreaseDecreaseThroughEffectsOfContractsInitiallyRecognisedInPeriodInsuranceContractsLiabilityAsset"
AMORT = "InsuranceRevenueContractualServiceMarginRecognisedInProfitOrLossBecauseOfTransferOfServices"
COMP = {"InsuranceContractsByComponentsAxis": "ContractualServiceMarginMember"}


def fact(name, dims, value, instant=None, start=None, end=None):
    f = {"name": name, "dims": dims, "value": float(value), "decimals": "-6"}
    f.update({"instant": instant} if instant else {"start": start, "end": end})
    return f


XB = {
    "20260310000111": [   # 2025 사업보고서
        fact(CSM, SEP, 13_217_874e6, instant="2025-12-31"), fact(CSM, CON, 13_452_893e6, instant="2025-12-31"),
        fact(CSM, {**SEP, "TypesOfContractsAxis": "HealthInsuranceMember"}, 7_950_196e6, instant="2025-12-31"),   # 상품군 하나: 고르면 안 된다
        fact(CSM, {**SEP, "DisaggregationOfInsuranceContractsAxis": "ReinsuranceContractsHeldMember"}, 705_243e6, instant="2025-12-31"),
        fact(NEW, {**SEP, **COMP}, 3_059_472e6, start="2025-01-01", end="2025-12-31"),
        fact(NEW, {**SEP, "InsuranceContractsByComponentsAxis": "RiskAdjustmentForNonfinancialRiskMember"}, 139_396e6, start="2025-01-01", end="2025-12-31"),
        fact(AMORT, SEP, 1_479_600e6, start="2025-01-01", end="2025-12-31"),
    ],
    "20260814000222": [   # 2026 반기보고서
        fact(CSM, SEP, 13_741_297e6, instant="2026-06-30"),
        fact(NEW, {**SEP, **COMP}, 1_717_464e6, start="2026-01-01", end="2026-06-30"),
        fact(NEW, {**SEP, **COMP}, 880_000e6, start="2026-04-01", end="2026-06-30"),
    ],
}
KICS_DOC = """<P>마. 지급여력비율</P><TABLE><TBODY>
<TR><TD>구 분</TD><TD>제 71 기 반기(FY2026 2Q)</TD><TD>제 70 기(FY2025)</TD></TR>
<TR><TD>지급여력(A)</TD><TD>129,552,503</TD><TD>65,739,719</TD></TR>
<TR><TD>지급여력비율(A/B)</TD><TD>208.2</TD><TD>198.0</TD></TR></TBODY></TABLE>
<P>[주요 종속회사]</P><TABLE><TR><TD>구 분</TD><TD>FY2026 2Q</TD></TR><TR><TD>지급여력비율</TD><TD>270.1</TD></TR></TABLE>"""


class LifeDart(FakeDart):
    def corps(self):
        return [LIFE]

    def statements(self, corp_code, year, fs_div, reprt_code="11011"):
        if (year, reprt_code, fs_div) == (2025, "11011", "OFS"):
            rows = [acc("ifrs-full_InsuranceServiceResult", "보험서비스결과", 974_716_000_000, "CIS", rno="20260310000111"),
                    acc("dart_InvestmentIncomeExpenses", "투자손익", 691_745_000_000, "CIS", rno="20260310000111")]
            return {"status": "000", "list": rows, "fetched_at": "2026-11-14T10:00:00"}
        return {"status": "013", "list": [], "fetched_at": "2026-11-14T10:00:00"}

    def annual_filings(self, corp_code, year):
        return [{"rcept_no": "20260310000111", "rcept_dt": "20260310", "report_nm": f"사업보고서 ({year}.12)"}] if year == 2025 else []

    def period_filings(self, corp_code, year, month):
        return [{"rcept_no": "20260814000222", "rcept_dt": "20260814", "report_nm": "반기보고서 (2026.06)"}] if (year, month) == (2026, 6) else []

    def xbrl_facts(self, rcept_no, reprt_code):
        return {"facts": XB.get(rcept_no, []), "fetched_at": "2026-11-14T10:00:00"}

    def document_text(self, rcept_no):
        return KICS_DOC if rcept_no == "20260814000222" else ""


def lrun(rows, when="2026-10-29"):
    rep = Report.model_validate({"meta": {"ticker": "900030", "name": "가상생명", "report_date": when, "snapshot_date": when, "opinion": "매수"},
                                 "claims": [], "data_rows": rows})
    ext = dart_check.run(rep, LifeDart({}))
    return by_rid(ext), {s["rid"]: s["why"] for s in ext["skipped"]}


def test_보험손익은_공시의_보험서비스결과와_대조한다():
    got, _ = lrun([row("R-1", "가상생명 보험손익 2025", "975", "십억 원"), row("R-2", "가상생명 투자손익 2025", "700", "십억 원")])
    assert got["R-1"]["status"] == "일치" and got["R-1"]["source"]["account_nm"] == "보험서비스결과"
    assert got["R-2"]["status"] == "불일치"


def test_CSM은_XBRL_주석의_합계와_대조한다():
    got, _ = lrun([row("R-1", "가상생명 CSM 잔액 2025", "13,218", "십억 원"),
                   row("R-2", "가상생명 신계약 CSM 2025", "3,060", "십억 원"),
                   row("R-3", "가상생명 CSM 상각액 2025", "1,480", "십억 원"),
                   row("R-4", "가상생명 CSM 잔액 2026년 6월 말", "13.74조원", "조원"),
                   row("R-5", "가상생명 신계약 CSM 2026년 상반기", "1조 7,175억원", "억 원")])
    assert all(got[r]["status"] == "일치" for r in ("R-1", "R-2", "R-3", "R-4", "R-5")), {k: v["reason"] for k, v in got.items()}
    assert got["R-1"]["basis"]["fs"] == "별도" and "XBRL" in got["R-1"]["source"]["api"]
    assert "6월 말" in got["R-4"]["name"] and "누적" in got["R-5"]["reason"]


def test_CSM_상품군_하나나_재보험_값과는_맞추지_않는다():
    got, _ = lrun([row("R-1", "가상생명 CSM 잔액 2025", "7,950", "십억 원"),     # 건강보험만의 값
                   row("R-2", "가상생명 별도 신계약 CSM 2Q26", "1,717", "십억 원")])     # 2분기라 해 놓고 상반기 누적 값
    assert got["R-1"]["status"] != "일치"
    assert got["R-2"]["status"] == "불일치"


def test_조_단위_소수는_그_자릿수까지_본다():
    got, _ = lrun([row("R-1", "가상생명 별도 CSM 잔액 2026년 6월 말", "13.70조원", "조원")])   # 공시 13.74조
    assert got["R-1"]["status"] == "불일치"


def test_지급여력비율은_공시_본문_표와_대조하고_기본자본은_건너뛴다():
    got, why = lrun([row("R-1", "가상생명 K-ICS 비율", "208.2%", "%", as_of="2026년 6월 말"),
                     row("R-2", "가상생명 지급여력비율", "210.5%", "%", as_of="2026년 6월 말"),
                     row("R-3", "가상생명 기본자본 K-ICS 비율", "177.2%", "%", as_of="2026년 6월 말"),
                     row("R-4", "가상생명 K-ICS 비율 2026E", "215%", "%")])
    assert got["R-1"]["status"] == "일치" and got["R-1"]["computed"] == "208.2%"
    assert got["R-2"]["status"] == "불일치"
    assert "기본자본" in why["R-3"] and "추정" in why["R-4"]


def test_FY_표기가_겹쳐도_추정치로_읽지_않는다():
    # 항목 'FY2025' + 기준 'FY2025'를 이으면 '2025 FY…'가 되어 예전에는 '2025F'(추정)로 잘못 읽었다
    info = dart_check.classify_dividend(DataRow(rid="R-1", item="가상생명 주당배당금 FY2025", value="5,300원", unit="원", as_of="FY2025",
                                                basis="FY2025에만 18% 증가", page=1), "가상생명")
    assert "skip" not in info and info["year"] == 2025
    assert dart_check._EST.search("2026F 순이익") and dart_check._EST.search("26E")
