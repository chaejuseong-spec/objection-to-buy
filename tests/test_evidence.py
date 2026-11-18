"""근거 종합표와 배심원 의견 갈림 표시 테스트. AI와 외부 API를 쓰지 않는다."""
import pytest

from tribunal import evidence
from tribunal.schemas import DataRow, Report


def report(rows, claims, sources=()):
    return Report.model_validate({
        "meta": {"ticker": "900010", "name": "가상식품홀딩스", "report_date": "2026-06-04", "snapshot_date": "2026-06-04", "opinion": "매수"},
        "claims": claims, "data_rows": rows, "sources": list(sources)})


def row(rid, item, value, unit="", as_of="", source=""):
    return {"rid": rid, "item": item, "value": value, "unit": unit, "as_of": as_of, "page": 1, "source": source}


def claim(cid, text, rids, core=False):
    return {"claim_id": cid, "text": text, "page": 1, "is_core": core, "evidence": [{"ref_id": r, "value": ""} for r in rids]}


def check(cid, kind, status, rids, expr=""):
    return {"check_id": cid, "kind": kind, "name": cid, "status": status, "stated_rid": rids[0], "rids": rids, "expr": expr,
            "computed": None, "reason": "이유", "source": {"short": "출처", "url": "u", "fetched_at": "t", "timing": "작성 당시 공개"}}


def build(rows, claims, checks, objs=None, sources=()):
    rep = report(rows, claims, sources)
    result = {"numbers": {"results": checks}, "checklist": [],
              "claims": [{"id": c["claim_id"], "verdict": "유지", "objections": (objs or {}).get(c["claim_id"], [])} for c in claims]}
    return {c["claim_id"]: c for c in evidence.build(rep, result)["claims"]}


@pytest.mark.parametrize("item,as_of,unit,expected", [
    ("가상식품 연결 매출액 2025년", "2025년 결산", "백만원", "사실"),
    ("가상식품 연결 매출액 2026E", "2026E", "백만원", "추정"),
    ("목표주가", "", "원", "추정"),
    ("원/달러 환율 2026E 가정", "2026E (26년 1~5월 평균치)", "원", "가정"),
    ("상승여력", "", "%", "계산"),
    ("현재주가", "2026.06.04", "원", "사실"),
])
def test_숫자의_성격(item, as_of, unit, expected):
    assert evidence.nature(DataRow(rid="R-1", item=item, value="1", unit=unit, as_of=as_of)) == expected


def test_근거_상태_다섯_가지():
    rows = [row("R-1", "매출액 2025년", "100", "억 원"), row("R-2", "영업이익 2025년", "10", "억 원"), row("R-3", "상승여력", "50%", "%"),
            row("R-4", "신라면 가격", "880원", "원"), row("R-5", "매출액 2026E", "120", "억 원")]
    claims = [claim("C-1", "매출이 늘었다", ["R-1"]), claim("C-2", "이익이 늘었다", ["R-2"]), claim("C-3", "싸다", ["R-1", "R-4"]),
              claim("C-4", "가격이 싸다", ["R-4"]), claim("C-5", "브랜드가 강하다", [])]
    checks = [check("D-1", "dart", "일치", ["R-1"]), check("D-2", "dart", "불일치", ["R-2"])]
    got = build(rows, claims, checks)
    assert {k: v["status"] for k, v in got.items()} == {"C-1": "근거 확인", "C-2": "실제 수치 불일치", "C-3": "일부 확인",
                                                      "C-4": "근거 미확인", "C-5": "숫자 근거 없음"}
    assert got["C-1"]["confidence"] == "높음" and got["C-2"]["confidence"] == "낮음"
    assert got["C-5"]["kinds"] == ["해석"]
    assert any("직접 확인" in t for t in got["C-2"]["todo"])


def test_추정치는_확인_대상에서_빼고_신뢰도는_보통():
    rows = [row("R-1", "매출액 2025년", "100", "억 원"), row("R-2", "매출액 2026E", "120", "억 원", "2026E")]
    got = build(rows, [claim("C-1", "매출이 늘 것이다", ["R-1", "R-2"])], [check("D-1", "dart", "일치", ["R-1"])])["C-1"]
    assert got["status"] == "근거 확인" and got["confidence"] == "보통" and "추정·가정 숫자 1개" in got["confidence_why"]
    assert "추정" in got["kinds"]
    assert not any("매출액 2026E" in t for t in got["todo"])         # 추정치는 확인할 것에 올리지 않는다


def test_계산으로만_확인되면_공식_자료와는_대조하지_못했다고_적는다():
    rows = [row("R-1", "목표주가", "139,500 원", "원"), row("R-2", "현재주가", "90,200 원", "원"), row("R-3", "상승여력", "54.7%", "%")]
    got = build(rows, [claim("C-1", "목표주가 139,500원", ["R-1", "R-2", "R-3"])],
                [check("K-1", "recompute", "일치", ["R-3"], "(R-01 / R-02 - 1) * 100".replace("R-01", "R-1").replace("R-02", "R-2"))])["C-1"]
    assert got["status"] == "근거 확인" and "공식 자료와는 대조하지 못했습니다" in got["confidence_why"]


def test_출처가_리포트_자신이면_자체_자료라고_안내한다():
    rows = [row("R-1", "신라면 가격", "880원", "원", source="S1"), row("R-2", "코스피200 PBR 2024년", "0.8배", "배", source="S2")]
    srcs = [{"sid": "S1", "title": "Equity Research Report | 가상식품홀딩스 (900010)"}, {"sid": "S2", "title": "한국거래소 (KRX)"}]
    got = build(rows, [claim("C-1", "싸다", ["R-1", "R-2"])], [], sources=srcs)["C-1"]
    assert "자체 자료" in got["todo"][0] and "한국거래소 (KRX)" in got["todo"][1]


@pytest.mark.parametrize("tally,split", [
    ({"타당": 2, "과장": 2, "확인 불가": 1}, True),
    ({"타당": 2, "과장": 1, "확인 불가": 0}, True),
    ({"타당": 3, "과장": 2, "확인 불가": 0}, True),      # 판정은 나지만 1표 차라 갈림으로 알린다
    ({"타당": 4, "과장": 1, "확인 불가": 0}, False),
    ({"타당": 2, "과장": 0, "확인 불가": 3}, False),     # 판단한 표가 너무 적다 (확인 불가는 갈림에 넣지 않는다)
    ({"타당": 0, "과장": 0, "확인 불가": 0}, False),     # 배심원이 추가한 이의
])
def test_배심원_의견_갈림(tally, split):
    result = {"logic": {"n_jurors": 5}, "claims": [{"objections": [{"tally": tally}]}]}
    evidence.mark_split(result)
    assert result["claims"][0]["objections"][0]["split"] is split
