"""숫자 읽기와 AI 답 형식 바로잡기 테스트. 실제 API는 부르지 않는다."""
import pytest

from tribunal import llm
from tribunal.number_check import parse_number, run_checks
from tribunal.schemas import JurorOut, Report


@pytest.mark.parametrize("value,unit,expected", [
    ("9,690", "십억 원", 9690),
    ("90,200 원", "원", 90200),
    ("54.7%", "%", 54.7),
    ("1 조 8,367 억 원", "원", 1_836_700_000_000),
    ("4,178 억 원", "원", 417_800_000_000),
    ("-3.2", "%", -3.2),
    ("10.7%p", "%p", 10.7),
    ("0.49", "배", 0.49),
    ("48조9,000억원", "억원", 489_000),        # 값 안의 조·억을 단위 칸(억원) 기준으로 바꾼다
    ("4,178억원", "억원", 4_178),
    ("51.56조원", "조원", 51.56),
    ("1조 8,367억 원", "십억원", 1_836.7),
])
def test_단위가_붙은_숫자도_읽는다(value, unit, expected):
    assert parse_number(value, unit) == pytest.approx(expected)


@pytest.mark.parametrize("value", ["약 70", "60~65", "4 이상", "26년 6월", ""])
def test_숫자_하나가_아니면_읽지_않는다(value):
    assert parse_number(value, "%") is None


def _report(rows, checks):
    return Report.model_validate({
        "meta": {"ticker": "000000", "name": "테스트", "report_date": "2026-01-01", "snapshot_date": "2026-01-01", "opinion": "매수"},
        "claims": [], "data_rows": rows, "checks": checks})


def test_백분율_식에_100이_빠져도_맞춰_비교한다():
    rows = [{"rid": "R-01", "item": "현재주가", "value": "90,200 원", "unit": "원"},
            {"rid": "R-02", "item": "목표주가", "value": "139,500 원", "unit": "원"},
            {"rid": "R-03", "item": "상승여력", "value": "54.7%", "unit": "%"}]
    checks = [{"check_id": "K-1", "name": "상승여력", "kind": "recompute", "expr": "(R-02 - R-01) / R-01", "stated": "R-03"}]
    res = run_checks(_report(rows, checks))["results"][0]
    assert res["status"] == "일치" and "×100" in res["reason"]
    assert res["stated"] == "54.7%"              # '54.7%%' 로 찍지 않는다


def test_자산은_부채와_자본의_합():
    rows = [{"rid": "R-05", "item": "자산", "value": "1 조 8,367 억 원", "unit": "원"},
            {"rid": "R-06", "item": "부채", "value": "5,158 억 원", "unit": "원"},
            {"rid": "R-07", "item": "자본", "value": "1 조 3,209 억 원", "unit": "원"}]
    checks = [{"check_id": "K-1", "name": "자산", "kind": "recompute", "expr": "R-06 + R-07", "stated": "R-05"}]
    assert run_checks(_report(rows, checks))["results"][0]["status"] == "일치"


def test_틀린_추가_이의만_빼고_표는_살린다():
    data = {"votes": [{"objection_id": "O-1", "vote": "타당", "reason": "r"},
                      {"objection_id": "O-2", "vote": "확인불가", "reason": "r"}],
            "extra_objection": {"claim_id": "C-1", "doubt_type": "일회성 손익", "text": "t", "kind": "사실 오류"}}
    data = llm._normalize(data)
    with pytest.raises(Exception):
        JurorOut.model_validate(data)
    fixed, dropped = llm._salvage(data, JurorOut)
    assert dropped == 1 and fixed.extra_objection is None
    assert [v.vote for v in fixed.votes] == ["타당", "확인 불가"]


def test_띄어쓰기만_다른_값은_바로잡는다():
    assert llm._normalize({"kind": "사실오류", "vote": "타당함", "text": "사실오류"}) == \
        {"kind": "사실 오류", "vote": "타당", "text": "사실오류"}     # 정해진 칸만 바꾼다


@pytest.mark.parametrize("given,expected", [("Buy", "매수"), ("BUY (유지)", "매수"), ("매수 유지", "매수"), ("Hold", "보유"), ("중립", "보유"), ("Sell", "매도")])
def test_투자의견을_여러_표기로_적어도_읽는다(given, expected):
    from tribunal.schemas import Meta
    assert Meta.model_validate({"ticker": 72710, "name": "x", "report_date": "2026-06-04", "snapshot_date": None, "opinion": given}).opinion == expected


def test_스냅샷_날짜와_빈_칸을_채운다():
    from tribunal.schemas import DataRow, Meta
    m = Meta.model_validate({"ticker": "900010", "name": "x", "report_date": "2026-06-04", "opinion": "매수"})
    assert m.snapshot_date == "2026-06-04"
    r = DataRow.model_validate({"rid": "R-1", "item": "x", "value": 1474, "unit": None, "source": None, "as_of": None})
    assert (r.value, r.unit, r.source, r.as_of) == ("1474", "", "", "")


def test_모르는_투자의견은_그대로_두어_걸러진다():
    from pydantic import ValidationError
    from tribunal.schemas import Meta
    with pytest.raises(ValidationError):
        Meta.model_validate({"ticker": "1", "name": "x", "report_date": "2026-06-04", "opinion": "잘 모르겠음"})



def test_AI가_식에_숫자를_직접_적으면_버린다(tmp_path):
    import pymupdf
    from tribunal.extract import verify
    pdf = tmp_path / "r.pdf"
    doc = pymupdf.open(); page = doc.new_page(); page.insert_text((72, 72), "Revenue 37,011 Target 139,500 Price 90,200 Upside 54.7"); doc.save(pdf)
    rep = _report([{"rid": "R-1", "item": "영업수익", "value": "37,011", "page": 1}, {"rid": "R-2", "item": "목표주가", "value": "139,500", "page": 1},
                   {"rid": "R-3", "item": "현재주가", "value": "90,200", "page": 1}, {"rid": "R-4", "item": "상승여력", "value": "54.7", "page": 1}],
                  [{"check_id": "K-1", "name": "합계", "kind": "recompute", "expr": "15814 + 1980 + 13200", "stated": "R-1"},
                   {"check_id": "K-2", "name": "상승여력", "kind": "recompute", "expr": "(R-2 / R-3 - 1) * 100", "stated": "R-4"},
                   {"check_id": "K-3", "name": "단위", "kind": "recompute", "expr": "R-2 * 1000000000 / R-3", "stated": "R-4"}])
    kept, stats = verify(rep, pdf)
    assert [k.check_id for k in kept.checks] == ["K-2", "K-3"]           # 100, 10억 같은 자릿수 맞춤은 허용
    assert "숫자를 직접 적음" in stats["dropped"]["checks"][0]["reason"]
