"""핵심 재무제표 매핑 규칙 (tribunal/external/core_fs.py). 가상 숫자"""
from tribunal.external import core_fs as cf


def _row(aid, name, v):
    return (aid, name, v, {})


def test_same_name_rows_pick_the_one_that_adds_up():
    """'이자비용' 이 두 줄(보험금융 포함 합계 / 금융상품만)이면 순이자이익 식이 맞는 줄을 고른다 (KB 모양)"""
    rows = [_row("", "순이자손익", 130.0), _row("", "이자비용", 145.0), _row("", "이자비용", 160.0), _row("", "이자수익", 290.0)]
    got = cf.map_year(cf.BANK, {"IS": rows, "BS": [], "CF": []})
    assert got["int_exp"]["v"] == 160.0 and got["net_int"]["v"] == 130.0


def test_standard_code_first():
    rows = [_row("-표준계정코드 미사용-", "이자수익", 1.0), _row("ifrs-full_RevenueFromInterest", "이자수익(합계)", 9.0)]
    assert cf.map_year(cf.BANK, {"IS": rows, "BS": [], "CF": []})["int_rev"]["v"] == 9.0


def test_expenses_written_negative_are_turned_positive_per_item():
    """원문 표가 비용을 괄호(음수)로 적어도 DART 재무 API 처럼 양수로. 같은 표 안에서 항목마다 다르게 적어도 각각 판단 (신한 모양)"""
    got = {"net_int": {"v": 50.0}, "int_rev": {"v": 80.0}, "int_exp": {"v": -30.0},
           "ni": {"v": 70.0}, "pretax": {"v": 100.0}, "tax": {"v": 30.0}, "provision": {"v": -5.0}, "sga": {"v": -20.0}}
    cf._fix_signs(got)
    assert got["int_exp"]["v"] == 30.0 and got["tax"]["v"] == 30.0           # 법인세는 이미 양수 → 그대로
    assert got["provision"]["v"] == 5.0 and got["sga"]["v"] == 20.0


def test_old_basis_rows_are_marked_and_unknown_rows_stay_blank():
    """보험서비스결과처럼 회계기준이 바뀐 항목(since=2023)은 그 전 해 자기 보고서 값에 '옛 기준' 표시"""
    it = next(x for x in cf.HOLDING if x["key"] == "ins_result")
    assert it["since"] == 2023 and it["need"] is False
    assert all(x["need"] is False for x in cf.HOLDING if x["key"] in ("loans", "provision", "deposits"))   # 은행 없는 지주


def test_zero_code_row_does_not_hide_real_row():
    """코드가 맞는 줄이 0(머리줄)이면 이름으로 찾은 실제 값 줄을 쓴다 (신한 2023 '보험손익' 0 / '순보험손익' 1.1조)"""
    rows = [_row("dart_InsuranceRevenueExpense", "보험손익", 0.0), _row("-표준계정코드 미사용-", "순보험손익", 1113.0)]
    got = cf.map_year(cf.HOLDING, {"IS": rows, "BS": [], "CF": []})
    assert got["ins_result"]["v"] == 1113.0


class _Rep:
    """나중 보고서: 전기 칸(frmtrm_amount)만 쓰는 가짜"""
    @staticmethod
    def make(vals):
        return {"rows": [{"sj_div": "IS", "account_id": "", "account_nm": n, "ord": str(i), "thstrm_amount": "1", "frmtrm_amount": str(v)}
                         for i, (n, v) in enumerate(vals)]}


def test_later_comparative_value():
    rep = _Rep.make([("순이자손익", 143.0), ("이자수익", 194.0), ("이자비용", 51.0)])
    it = next(x for x in cf.BANK if x["key"] == "int_rev")
    assert cf._later(rep, it, "frmtrm_amount") == 194.0


def test_card_old_form_lines_and_sum_of_two_expense_lines():
    """삼성카드 옛 양식 모양: 판매비·일반관리비가 따로 있으면 합, 새 이름이 없으면 옛 이름(금융수익·금융비용·신용판매수익)"""
    rows = [_row("", "신용판매수익", 170.0), _row("", "금융수익", 89.0), _row("", "금융비용", 32.0),
            _row("", "판매비", 130.0), _row("", "일반관리비", 70.0), _row("", "영업이익", 45.0)]
    got = cf.map_year(cf.CARD, {"IS": rows, "BS": [], "CF": []})
    assert got["sga"]["v"] == 200.0 and got["sga"]["name"] == "판매비 + 일반관리비"
    assert got["int_rev"]["v"] == 89.0 and got["int_exp"]["v"] == 32.0 and got["fee_rev"]["v"] == 170.0
    new = [_row("ifrs-full_SellingGeneralAndAdministrativeExpense", "일반관리비", 87.0), _row("", "이자수익", 203.0), _row("", "금융수익", 1.0)]
    got = cf.map_year(cf.CARD, {"IS": new, "BS": [], "CF": []})
    assert got["sga"]["v"] == 87.0 and got["int_rev"]["v"] == 203.0          # 새 양식: 한 줄, 새 이름 먼저


def _rep(year_rows):
    """DART 재무 API 보고서 모양: [(이름, 당기, 전기, 전전기)]"""
    return {"rows": [{"sj_div": "IS", "account_id": "", "account_nm": n, "ord": str(i), "thstrm_amount": str(a),
                      "frmtrm_amount": str(b), "bfefrmtrm_amount": str(c)} for i, (n, a, b, c) in enumerate(year_rows)]}


def test_build_keeps_one_report_per_column_and_marks_old_form(monkeypatch):
    """새 양식 보고서(2025)가 2023·2024 를 다시 적었으면 그 해 손익은 모두 그 보고서 숫자 (판매비와관리비 포함).
    그 앞 해는 옛 양식 값에 '양식 변경 전', 새 양식에만 있는 줄은 '옛 양식에 없음'. 순영업이익 = 영업이익 + 판매비와관리비"""
    from datetime import date
    from tribunal.external import company as cp
    old = lambda op: [("신용판매수익", 240, 0, 0), ("금융상품수익", 100, 0, 0), ("금융비용", 48, 0, 0),
                      ("판매비", 115, 0, 0), ("일반관리비", 75, 0, 0), ("영업이익", op, 0, 0), ("당기순이익", 60, 0, 0)]
    reps = {2022: _rep(old(85)), 2023: _rep(old(81)), 2024: _rep(old(88)),
            2025: _rep([("순이자손익", 143, 143, 144), ("이자수익", 203, 194, 192), ("이자비용", 60, 51, 48),
                        ("순수수료손익", 85, 85, 77), ("수수료수익", 181, 175, 171), ("수수료비용", 96, 90, 94),
                        ("일반관리비", 87, 84, 80), ("영업이익", 85, 88, 81), ("당기순이익", 65, 66, 61)])}
    monkeypatch.setattr(cp, "_report", lambda d, c, y, q, f: reps.get(y))
    monkeypatch.setattr(cf.doc_fs, "period_doc", lambda *a, **k: None)
    core = cf.build(None, "x", "card", years=4, today=date(2026, 1, 1))
    cell = lambda k, y: core["cells"][k][y]
    assert core["form_year"] == 2023
    assert cell("sga", 2023)["v"] == 80 and cell("sga", 2023)["first_v"] == 190 and cell("sga", 2023)["def_change"]
    assert cell("sga", 2022)["v"] == 190 and cell("sga", 2022)["old_form"] == 2023
    assert cell("net_fee", 2022)["st"] == "old_none" and cell("net_int", 2023)["v"] == 144
    assert cell("net_op_rev", 2025)["v"] == 85 + 87 and cell("net_op_rev", 2022)["v"] == 85 + 190
    assert "old_form" not in cell("op", 2022)                                  # 두 양식에서 같은 줄은 표시하지 않는다
    assert cell("ni_owner", 2025)["v"] == 65 and cell("ni_owner", 2025)["st"] == "derived"   # 비지배지분 없음
