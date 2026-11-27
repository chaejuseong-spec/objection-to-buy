"""핵심 재무제표 (2026-11-24 사용자, 시험 단계): 업권별로 정한 20~30개 항목만 10년치로 맞춘다.

항목마다: 표준 계정 코드 → 이름 후보(띄어쓰기·기간 표기 무시) 순서로 찾는다. 같은 이름이 여러 줄이면 검산이 맞는 줄을 고른다.
칸마다 상태를 남긴다:
- reported: 그 해 보고서에 적힌 값 (DART 재무 API, 금융회사 2022년 이전은 사업보고서 원문 표)
- derived: 구성 항목으로 계산 (예: 순이자이익 = 이자수익 − 이자비용). 회사가 직접 적은 해에서 같은 식이 맞는 것을 확인한 회사만
- restated: 그 해 보고서에 없어 다음 해 보고서의 비교(전기·전전기) 숫자로 채운 값 (재작성 값일 수 있다)
- na: 조건부 항목인데 그 회사 재무제표에 그 줄이 한 해도 없음 (해당 없음)
- missing: 못 찾음 (빈칸)
숫자를 만들지 않는다. 검산이 안 맞는 계산은 하지 않는다.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Dict, List, Optional, Tuple

from .dart import Dart
from . import doc_fs

TOL = 0.005                                           # 검산 허용 오차 0.5%


def _it(key, label, sj, ids=(), names=(), need=True, parent=None, rx=None, since=None, old=(), sum_of=None, new_form=False,
        old_calc=None, until=None):
    """since: 회계기준이 바뀐 해 (그 앞 해 값은 '옛 기준' 표시, 예: 보험 IFRS17 = 2023)
    old: 회사가 양식을 바꾸기 전 같은 뜻의 줄 이름 (새 이름·코드가 없는 해에만 쓴다)
    sum_of: 따로 적힌 줄들을 더한 것이 이 항목 (예: 판매비 + 일반관리비 = 판매비와관리비). 모두 있는 해에만
    new_form: 회사의 새 양식에만 있는 줄. 이 줄이 처음 나온 해가 양식 경계 (그 앞 해는 '양식 변경 전')
    old_calc: 옛 양식 줄로 계산하는 식들 [[(이름, +1/−1), ...], ...] (첫 식부터, 줄이 모두 있을 때만)
    until: 옛 기준에만 있는 줄의 마지막 해 (보험 IFRS4 보험료수익 = 2022). 그 뒤 해는 '새 기준에 없음'"""
    return {"key": key, "label": label, "sj": sj, "ids": list(ids), "names": [doc_fs.loose(n) for n in names],
            "need": need, "parent": parent, "rx": rx, "since": since, "old": [doc_fs.loose(n) for n in old],
            "sum_of": [doc_fs.loose(n) for n in sum_of] if sum_of else None, "new_form": new_form,
            "old_calc": [[(doc_fs.loose(n), t) for n, t in f] for f in old_calc] if old_calc else None, "until": until}


EXPENSE = ("int_exp", "fee_exp", "provision", "sga", "tax", "fv_loss", "ins_exp", "claims_old", "opex_old")   # 비용: DART 재무 API 처럼 양수로 맞춘다


# 은행 (은행·저축은행). ids 는 'ifrs-full_' 를 뺀 이름도 같은 것으로 본다
BANK = [
    _it("net_int", "순이자이익", "IS", ["ifrs-full_InterestRevenueExpense"], ["순이자손익", "순이자이익", "이자손익"]),
    _it("int_rev", "이자수익", "IS", ["ifrs-full_RevenueFromInterest"], ["이자수익"], parent="net_int"),
    _it("int_exp", "이자비용", "IS", ["ifrs-full_InterestExpense"], ["이자비용"], parent="net_int"),
    _it("net_fee", "순수수료이익", "IS", ["ifrs-full_FeeAndCommissionIncomeExpense"], ["순수수료손익", "순수수료이익", "수수료손익"]),
    _it("fee_rev", "수수료수익", "IS", ["ifrs-full_FeeAndCommissionIncome"], ["수수료수익"], parent="net_fee"),
    _it("fee_exp", "수수료비용", "IS", ["ifrs-full_FeeAndCommissionExpense"], ["수수료비용"], parent="net_fee"),
    _it("provision", "신용손실충당금 전입액", "IS", ["ifrs-full_ImpairmentLossImpairmentGainAndReversalOfImpairmentLossDeterminedInAccordanceWithIFRS9"],
        ["신용손실충당금전입액", "신용손실충당금(전)환입액", "신용손실충당금전입", "금융자산손상차손", "대손상각비", "충당금전입액", "대손충당금전입액",
         "신용손실충당금순전입액", "신용손실충당금환입(전입)액", "대출채권등손상차손", "금융자산신용손실충당금전입액",
         "금융상품자산손상차손", "금융자산관련손상차손", "신용손실에대한손상차손", "신용손실에대한손상차손전입"]),
    _it("sga", "일반관리비", "IS", ["ifrs-full_SellingGeneralAndAdministrativeExpense"], ["일반관리비", "판매비와관리비", "판매비와일반관리비"]),
    _it("op", "영업이익", "IS", ["ifrs-full_ProfitLossFromOperatingActivities", "dart_OperatingIncomeLoss"], ["영업이익"]),
    _it("pretax", "법인세비용차감전순이익", "IS", ["ifrs-full_ProfitLossBeforeTax"], ["법인세비용차감전순이익", "법인세차감전순이익", "법인세비용차감전계속영업순이익"], rx=doc_fs._PRETAX),
    _it("tax", "법인세비용", "IS", ["ifrs-full_IncomeTaxExpenseContinuingOperations"], ["법인세비용", "법인세비용(수익)", "계속영업법인세비용", "법인세수익(비용)"]),
    _it("ni", "당기순이익", "IS", ["ifrs-full_ProfitLoss"], ["당기순이익"], rx=doc_fs._NI),
    _it("ni_owner", "지배주주 순이익", "IS", ["ifrs-full_ProfitLossAttributableToOwnersOfParent"], [], need=False),
    _it("cash", "현금 및 예치금", "BS", ["dart_CashAndDuefromBanks"], ["현금및예치금", "현금및현금성자산", "현금및상각후원가측정예치금"]),
    _it("loans", "대출채권", "BS", ["dart_LoansAtAmortisedCost"], ["상각후원가측정대출채권", "상각후원가대출채권", "대출채권", "대출채권및수취채권",
                                                                 "상각후원가측정대출채권및기타채권", "상각후원가측정대출채권및기타금융자산"]),
    _it("sec_pl", "당기손익-공정가치 금융자산", "BS", ["ifrs-full_FinancialAssetsAtFairValueThroughProfitOrLoss"],
        ["당기손익-공정가치측정금융자산", "당기손익인식금융자산", "당기손익-공정가치금융자산", "당기손익금융자산"], need=False),
    _it("sec_oci", "기타포괄손익-공정가치 금융자산", "BS", ["ifrs-full_FinancialAssetsAtFairValueThroughOtherComprehensiveIncome"],
        ["기타포괄손익-공정가치측정금융자산", "기타포괄손익인식금융자산", "매도가능금융자산", "투자금융자산", "기타포괄손익금융자산", "기타포괄손익-공정가치측정유가증권"], need=False),
    _it("sec_ac", "상각후원가 유가증권", "BS", ["dart_SecuritiesAtAmortisedCost"], ["상각후원가유가증권", "상각후원가측정유가증권", "만기보유금융자산"], need=False),
    _it("assets", "자산총계", "BS", ["ifrs-full_Assets"], ["자산총계"]),
    _it("deposits", "예수부채", "BS", ["ifrs-full_DepositsFromCustomers"], ["예수부채", "예수금"]),
    _it("borrow", "차입부채", "BS", ["ifrs-full_OtherBorrowings"], ["차입부채", "차입금"], need=False),
    _it("bonds", "사채", "BS", ["ifrs-full_BondsIssued"], ["사채", "발행사채"], need=False),
    _it("liab", "부채총계", "BS", ["ifrs-full_Liabilities"], ["부채총계"]),
    _it("equity_owner", "지배주주 자본", "BS", ["ifrs-full_EquityAttributableToOwnersOfParent"], [], need=False),
    _it("equity", "자본총계", "BS", ["ifrs-full_Equity"], ["자본총계"]),
    _it("cfo", "영업활동 현금흐름", "CF", ["ifrs-full_CashFlowsFromUsedInOperatingActivities"], [],
        rx=re.compile(r"영업활동(으로인한|으로부터의|에서창출된|으로인한순)?(순)?현금흐름")),
    _it("cfi", "투자활동 현금흐름", "CF", ["ifrs-full_CashFlowsFromUsedInInvestingActivities"], [],
        rx=re.compile(r"투자활동(으로인한|으로부터의|으로인한순)?(순)?현금흐름")),
    _it("cff", "재무활동 현금흐름", "CF", ["ifrs-full_CashFlowsFromUsedInFinancingActivities"], [],
        rx=re.compile(r"재무활동(으로인한|으로부터의|으로인한순)?(순)?현금흐름")),
    _it("div_paid", "배당금 지급", "CF", ["ifrs-full_DividendsPaidClassifiedAsFinancingActivities"], ["배당금지급", "배당금의지급", "보통주배당지급", "보통주배당금지급"]),
    _it("cash_end", "기말 현금 및 현금성자산", "CF", ["ifrs-full_CashAndCashEquivalents"], ["기말현금및현금성자산", "기말의현금및현금성자산", "당기말현금및현금성자산"],
        rx=re.compile(r"(당기|기|반기|분기)?말의?현금및현금성자산.*")),      # '당기말의 …', '기말의 …(V+Ⅵ)', 사업보고서인데 '분기말의 …'로 적은 해
]
# 금융지주 = 은행 + 자회사 손익 줄 (그 회사 재무제표에 있을 때만)
HOLDING = BANK[:7] + [
    _it("ins_result", "보험서비스결과(보험손익)", "IS", ["ifrs-full_InsuranceServiceResult", "dart_InsuranceRevenueExpense"], ["보험서비스결과", "보험손익", "순보험손익"], need=False, since=2023),
    _it("fv_pl", "당기손익-공정가치 금융상품 순손익", "IS", ["dart_GainLossFromFinancialInstrumentsAtFairValueThroughProfitOrLoss"],
        ["당기손익-공정가치측정금융상품순손익", "당기손익-공정가치측정금융상품관련순손익", "당기손익인식금융상품관련순손익", "당기손익금융상품관련순손익",
         "당기손익인식금융상품순손익", "당기손익-공정가치측정금융상품관련손익"], need=False),
    _it("other_op", "기타영업손익", "IS", ["ifrs-full_OtherOperatingIncomeExpense"], ["기타영업손익"], need=False),
] + BANK[7:21] + [
    _it("ins_liab", "보험계약부채", "BS", ["ifrs-full_InsuranceContractsIssuedThatAreLiabilities"], ["보험계약부채"], need=False, since=2023),
] + BANK[21:]
HOLDING = [{**it, "need": False} if it["key"] in ("loans", "provision", "deposits") else it for it in HOLDING]
_B = {it["key"]: it for it in BANK}                     # 은행 목록의 공통 줄(영업이익·순이익·합계·현금흐름)을 다른 업권에서도 쓴다
_TAIL_IS = [_B[k] for k in ("op", "pretax", "tax", "ni", "ni_owner")]
_TAIL_BS = [_B[k] for k in ("liab", "equity_owner", "equity")]
_CF = [_B[k] for k in ("cfo", "cfi", "cff", "div_paid", "cash_end")]
_CASH = _it("cash", "현금 및 현금성자산", "BS", ["ifrs-full_CashAndCashEquivalents", "dart_CashAndDuefromBanks"],
            ["현금및현금성자산", "현금및예치금", "현금및상각후원가측정예치금"])
_FVPL = _it("sec_pl", "당기손익-공정가치 금융자산", "BS", ["ifrs-full_FinancialAssetsAtFairValueThroughProfitOrLoss"],
            ["당기손익-공정가치측정금융자산", "당기손익인식금융자산", "당기손익-공정가치금융자산"], need=False)
_FVOCI = {**_B["sec_oci"]}
_AC = _it("fin_ac", "상각후원가 금융자산", "BS", ["ifrs-full_FinancialAssetsAtAmortisedCost"], ["상각후원가측정금융자산"], need=False)
_BORROW = _it("borrow", "차입부채", "BS", ["ifrs-full_Borrowings", "ifrs-full_OtherBorrowings", "ifrs-full_LoansReceived"], ["차입부채", "차입금"], need=False)
_BONDS = _it("bonds", "사채", "BS", ["ifrs-full_BondsIssued"], ["사채", "발행사채"], need=False)
# 보험 (2026-11-25 사용자): 2023년 IFRS17 전 해는 옛 기준(IFRS4) 줄. 손익 소계(보험영업수익·비용, 투자영업수익·비용)를 적은 회사
# (삼성화재·현대해상)는 옛 보험손익·투자손익을 계산해 '기준 변경 전' 표시, 소계가 없는 회사(삼성생명·한화생명·DB손보)는 '옛 기준에 없음'.
# 모든 회사에 있는 옛 기준 줄(보험료수익·지급보험금·사업비)은 따로 보여 준다
INSURANCE = [
    _it("ins_result", "보험서비스결과(보험손익)", "IS", ["ifrs-full_InsuranceServiceResult"], ["보험서비스결과", "보험손익"], since=2023, new_form=True,
        old_calc=[[("보험영업수익", 1), ("보험영업비용", -1)]]),
    _it("ins_rev", "보험수익", "IS", ["ifrs-full_InsuranceRevenue"], ["보험수익", "일반보험서비스수익"], parent="ins_result", since=2023, new_form=True,
        old=["보험영업수익"]),
    _it("ins_exp", "보험서비스비용", "IS", ["ifrs-full_InsuranceServiceExpensesFromInsuranceContractsIssued"], ["보험서비스비용", "일반보험서비스비용"],
        parent="ins_result", since=2023, new_form=True, old=["보험영업비용"]),
    _it("inv_result", "투자손익", "IS", ["dart_InvestmentIncomeExpenses"], ["투자손익"], since=2023, new_form=True,
        old_calc=[[("투자영업수익", 1), ("투자영업비용", -1)]]),
    _it("int_rev", "이자수익", "IS", ["ifrs-full_RevenueFromInterest"], ["이자수익"], parent="inv_result"),
    _it("div_rev", "배당금수익", "IS", ["ifrs-full_RevenueFromDividends"], ["배당금수익", "배당수익"], parent="inv_result", need=False),
    _it("prem_old", "보험료수익 (옛 기준)", "IS", [], ["보험료수익"], need=False, until=2022),
    _it("claims_old", "지급보험금 (옛 기준)", "IS", [], ["지급보험금", "보험금비용", "지급보험금및환급금"], need=False, until=2022),
    _it("opex_old", "사업비 (옛 기준)", "IS", [], ["사업비"], need=False, until=2022),
] + _TAIL_IS + [
    _CASH, _FVPL, _FVOCI, _AC,
    {**_B["loans"], "need": False}, {**_B["sec_ac"], "need": False},
    _it("inv_prop", "투자부동산", "BS", ["ifrs-full_InvestmentProperty"], ["투자부동산"], need=False),
    _B["assets"],
    _it("ins_liab", "보험계약부채", "BS", ["ifrs-full_InsuranceContractsIssuedThatAreLiabilities"], ["보험계약부채"], since=2023),
    _it("inv_contract", "투자계약부채", "BS", ["ifrs-full_InvestmentContractsLiabilities"], ["투자계약부채"], need=False),
    _BORROW,
] + _TAIL_BS + _CF
# 카드 (2026-11-25 사용자): 토스처럼 모든 해가 영업이익까지 이어지게. 삼성카드는 2025년 보고서에서 양식을 바꿨다
# (옛 양식: 영업수익 총액 − 영업비용, 새 양식: 순이자·순수수료…). 옛 해는 같은 뜻의 옛 줄로 채우고 '양식 변경 전' 표시
CARD = [
    _it("net_op_rev", "순영업이익", "IS", need=False),            # 계산: 영업이익 + 판매비와관리비 (두 양식 모두 같은 식)
    {**_B["net_int"], "new_form": True},
    {**_B["int_rev"], "old": [doc_fs.loose(n) for n in ("금융수익", "금융상품수익")]},
    {**_B["int_exp"], "old": [doc_fs.loose(n) for n in ("금융비용",)]},
    {**_B["net_fee"], "new_form": True},
    {**_B["fee_rev"], "old": [doc_fs.loose(n) for n in ("신용판매수익",)]},
    {**_B["fee_exp"], "new_form": True},
    _it("lease_net", "순리스손익", "IS", ["dart_NetLeaseProfitLoss"], ["순리스손익", "리스손익"], need=False, new_form=True),
    _B["provision"],
    {**_B["sga"], "label": "판매비와관리비", "sum_of": [doc_fs.loose(n) for n in ("판매비", "일반관리비")]},
] + _TAIL_IS + [
    _CASH, _AC, _FVPL, _B["assets"], _BONDS, _BORROW,
] + _TAIL_BS + _CF
SECURITIES = [
    _it("op_rev", "영업수익", "IS", ["ifrs-full_Revenue"], ["영업수익"]),
    _B["net_fee"], _B["fee_rev"], _B["fee_exp"], _B["net_int"],
    {**_B["int_rev"], "old_calc": [[(doc_fs.loose(n), 1) for n in ("상각후원가측정금융자산관련이자수익", "기타포괄손익-공정가치측정금융자산관련이자수익",
                                                                   "당기손익-공정가치측정금융자산관련이자수익")]]},
    _B["int_exp"],
    _it("fv_net", "금융상품 관련 순손익", "IS", [], ["당기손익-공정가치측정금융상품관련손익", "금융상품관련순손익"], need=False),
    _it("fv_gain", "금융상품 관련 이익", "IS", ["dart_GainFromFinancialInstrumentsAtFairValueThroughProfitOrLoss"],
        ["당기손익-공정가치측정금융상품관련이익", "당기손익인식금융상품관련이익"], parent="fv_net", need=False),
    _it("fv_loss", "금융상품 관련 손실", "IS", ["dart_LossFromFinancialInstrumentsAtFairValueThroughProfitOrLoss"],
        ["당기손익-공정가치측정금융상품관련손실", "당기손익인식금융상품관련손실"], parent="fv_net", need=False),
    {**_B["provision"], "need": False},
    _it("sga", "판매비와관리비", "IS", ["ifrs-full_SellingGeneralAndAdministrativeExpense"], ["판매비와관리비", "판매관리비", "일반관리비"]),
    _it("personnel", "인건비", "IS", ["dart_PersonalExpense"], ["인건비"], need=False),
] + _TAIL_IS + [
    _CASH, _FVPL, _FVOCI, _AC, _B["assets"],
    {**_B["deposits"], "need": False},
    _it("fv_liab", "당기손익-공정가치 금융부채", "BS", ["ifrs-full_FinancialLiabilitiesAtFairValueThroughProfitOrLoss"],
        ["당기손익-공정가치측정금융부채", "당기손익인식금융부채"], need=False),
    _BORROW, _BONDS,
] + _TAIL_BS + _CF
# 항상 계산하는 줄: (결과, 식). 회사가 적지 않는 요약 줄
DERIVE = [("net_op_rev", [("op", 1), ("sga", 1)])]
TEMPLATES = {"bank": BANK, "holding": HOLDING, "insurance": INSURANCE, "card": CARD, "securities": SECURITIES}
SJ = {"IS": ("IS", "CIS"), "BS": ("BS",), "CF": ("CF",)}
# 검산: (결과, 식, 설명). 식의 항목은 + 또는 −
CHECKS = [("net_int", [("int_rev", 1), ("int_exp", -1)]), ("net_fee", [("fee_rev", 1), ("fee_exp", -1)]),
          ("fv_net", [("fv_gain", 1), ("fv_loss", -1)]),
          ("assets", [("liab", 1), ("equity", 1)]), ("ni", [("pretax", 1), ("tax", -1)])]


def _aid(x: str) -> str:
    return (x or "").replace("ifrs-full_", "ifrs_")


def _api_rows(rep: dict, sj: str) -> List[Tuple[str, str, Optional[float], dict]]:
    """API 보고서 -> [(코드, 이름, 당기 값, 원래 줄)] (재무제표 순서대로)"""
    rows = sorted((r for r in rep["rows"] if r.get("sj_div") in SJ[sj]), key=lambda r: int(r.get("ord") or 0))
    out = []
    for r in rows:
        try:
            v = float(str(r.get("thstrm_amount") or "").replace(",", ""))
        except ValueError:
            v = None
        out.append((r.get("account_id") or "", r.get("account_nm") or "", v, r))
    return out


def _doc_rows(doc: dict, sj: str) -> List[Tuple[str, str, Optional[float], dict]]:
    return [("", n, v, {}) for n, v in doc_fs._one(doc, sj, "plain", "cum")]


def _cands(it: dict, rows) -> List[tuple]:
    """그 항목에 맞는 줄 후보들 (코드가 맞는 줄 먼저, 없으면 이름)"""
    by_id = [r for r in rows if r[0] and _aid(r[0]) in {_aid(i) for i in it["ids"]}]
    if any(r[2] for r in by_id):
        return [r for r in by_id if r[2]] + [r for r in by_id if not r[2]]
    hits = []
    for r in rows:
        n = doc_fs.loose(r[1])
        if n in it["names"] or (it["rx"] is not None and it["rx"].fullmatch(doc_fs.norm(r[1]))):
            hits.append(r)
    if any(r[2] for r in hits):
        return [r for r in hits if r[2]] + by_id
    return by_id + hits


def _later(rep: dict, it: dict, field: str) -> Optional[float]:
    """나중 보고서의 비교 칸('frmtrm_amount' 전기, 'bfefrmtrm_amount' 전전기) 값"""
    for r in _cands(it, _api_rows(rep, it["sj"])):
        try:
            v = float(str(r[3].get(field) or "").replace(",", ""))
        except ValueError:
            continue
        return abs(v) if it["key"] == "div_paid" else v
    return None


def _owner_pick(rows, sj: str) -> Optional[tuple]:
    """지배주주 줄 (코드가 없는 옛 공시): 순이익 줄 뒤 / 부채총계와 자본총계 사이의 지배 몫 줄"""
    names = [doc_fs.norm(r[1]) for r in rows]
    if sj == "IS":
        i = next((k for k, n in enumerate(names) if doc_fs._NI.fullmatch(n)), None)
        return next((rows[k] for k in range(i + 1, len(rows)) if doc_fs._owner(names[k])), None) if i is not None else None
    lo = next((k for k, n in enumerate(names) if n == "부채총계"), 0)
    hi = next((k for k, n in enumerate(names) if n == "자본총계"), len(names))
    return next((rows[k] for k in range(lo + 1, hi) if doc_fs._owner(names[k]) and not re.search(r"이익|손익|손실", names[k])), None)


def _close(a: Optional[float], b: Optional[float]) -> bool:
    return a is not None and b is not None and abs(a - b) <= max(abs(b), 1.0) * TOL


def map_year(items: List[dict], src: Dict[str, list]) -> Dict[str, dict]:
    """한 해 보고서의 줄들 -> {항목: {'v', 'st', 'name'}}. 후보가 여럿이면 검산이 맞는 것"""
    out: Dict[str, dict] = {}
    cands: Dict[str, list] = {}
    for it in items:
        rows = src.get(it["sj"], [])
        if it.get("sum_of"):                          # 따로 적힌 줄을 모두 찾으면 그 합 (삼성카드 옛 양식 판매비 + 일반관리비)
            parts = [next((r for r in rows if doc_fs.loose(r[1]) == n and r[2]), None) for n in it["sum_of"]]
            if all(parts):
                out[it["key"]] = {"v": sum(abs(x[2]) for x in parts), "st": "reported", "name": " + ".join(x[1] for x in parts)}
                cands[it["key"]] = []
                continue
        c = [r for r in _cands(it, rows) if r[2] is not None]
        if not c and it.get("old"):                   # 새 이름·코드가 없는 해: 같은 뜻의 옛 양식 줄
            c = [r for r in rows if doc_fs.loose(r[1]) in it["old"] and r[2] is not None]
            if c:
                out[it["key"]] = {"v": c[0][2], "st": "reported", "name": c[0][1], "via_old": True}
                cands[it["key"]] = c
                continue
        if not c and it.get("old_calc"):              # 옛 양식 소계로 계산 (보험영업수익 − 보험영업비용)
            for f in it["old_calc"]:
                parts = [next((r for r in rows if doc_fs.loose(r[1]) == n and r[2] is not None), None) for n, _ in f]
                if all(parts):
                    plus = all(t > 0 for _, t in f)            # 나눠 적은 줄의 합 (미래에셋 이자수익 세 줄) = 회사가 적은 값
                    out[it["key"]] = {"v": sum(abs(x[2]) * t for x, (_, t) in zip(parts, f)), "st": "reported" if plus else "derived", "verified": True,
                                      "name": " ".join(("− " if t < 0 else ("+ " if i else "")) + x[1] for i, (x, (_, t)) in enumerate(zip(parts, f))),
                                      "via_old": True}
                    break
            if it["key"] in out:
                cands[it["key"]] = []
                continue
        if not c and it["key"] in ("ni_owner", "equity_owner"):
            o = _owner_pick(rows, it["sj"])
            c = [o] if o and o[2] is not None else []
        cands[it["key"]] = c
        if c:
            out[it["key"]] = {"v": c[0][2], "st": "reported", "name": c[0][1]}
    if "equity_owner" not in out and "equity" in out and any(it["key"] == "equity_owner" for it in items):
        rows = src.get("BS", [])
        names = [doc_fs.norm(r[1]) for r in rows]
        lo = next((k for k, n in enumerate(names) if n == "부채총계"), 0)
        nci = next((rows[k][2] for k in range(lo + 1, len(rows)) if names[k].startswith("비지배") and not re.search(r"이익|손익|손실|포괄", names[k])
                    and rows[k][2] is not None), None)
        if nci is not None:                           # 지배 몫 줄 없이 비지배지분만 적은 해 (한화생명·미래에셋 옛 공시)
            out["equity_owner"] = {"v": out["equity"]["v"] - nci, "st": "derived", "name": "자본총계 − 비지배지분", "verified": True}
    for res, parts in CHECKS:                         # 같은 이름이 여러 줄이면 식이 맞는 조합으로 바꾼다 (예: KB '이자비용' 두 줄)
        if res not in out or any(p not in out for p, _ in parts):
            continue
        if _close(sum(out[p]["v"] * s for p, s in parts), out[res]["v"]):
            continue
        for p, s in parts:
            for alt in cands.get(p, [])[1:]:
                trial = sum((alt[2] if q == p else out[q]["v"]) * t for q, t in parts)
                if _close(trial, out[res]["v"]):
                    out[p] = {"v": alt[2], "st": "reported", "name": alt[1]}
                    break
    return out


def _fix_signs(got: Dict[str, dict]) -> None:
    """비용 줄을 DART 재무 API 처럼 양수로 맞춘다. 검산 식에서 '빼기' 항목이 더해서 맞으면 그 항목만 부호를 바꾼다.
    충당금은 이자비용이 음수로 적힌 표면 같이 바꾼다. 일반관리비는 늘 비용이라 양수"""
    flipped = set()
    for res, parts in CHECKS:
        if res not in got or not all(p in got for p, _ in parts):
            continue
        if _close(sum(got[p]["v"] * t for p, t in parts), got[res]["v"]):
            continue
        for p, t in parts:
            if t < 0 and _close(sum(got[q]["v"] * (1 if q == p else u) for q, u in parts), got[res]["v"]):
                got[p]["v"] = -got[p]["v"]
                flipped.add(p)
                break
    if "provision" in got and got["provision"]["v"] < 0 and ("int_exp" in flipped or "fee_exp" in flipped):
        got["provision"]["v"] = -got["provision"]["v"]
    if "sga" in got:
        got["sga"]["v"] = abs(got["sga"]["v"])


def build(dart: Dart, corp_code: str, template: str, basis: str = "연결", years: int = 10, today: Optional[date] = None) -> dict:
    """10년치 핵심 재무제표 (연간). {'items': [...], 'years': [...], 'cells': {항목: {연도: 칸}}, 'checks': {...}}"""
    from . import company as cp
    today = today or date.today()
    items = TEMPLATES[template]
    ys = list(range(today.year - years, today.year))
    fsdiv = "CFS" if basis == "연결" else "OFS"
    srcs: Dict[int, dict] = {}
    reps: Dict[int, dict] = {}
    for y in ys:
        rep = cp._report(dart, corp_code, y, 4, fsdiv)
        if rep:
            reps[y] = rep
            srcs[y] = {sj: _api_rows(rep, sj) for sj in ("IS", "BS", "CF")}
            srcs[y]["_from"] = "DART 재무 API"
        else:
            doc = doc_fs.period_doc(dart, corp_code, y, 4, basis)
            if doc:
                srcs[y] = {sj: _doc_rows(doc, sj) for sj in ("IS", "BS", "CF")}
                srcs[y]["_from"] = "사업보고서 원문"
    cells: Dict[str, Dict[int, dict]] = {it["key"]: {} for it in items}
    by_key0 = {it["key"]: it for it in items}
    disc = {y for y, src in srcs.items() if any("중단영업" in (r[1] or "") and r[2] for r in src.get("IS", []))}
    for y, src in srcs.items():
        got = map_year(items, src)
        _fix_signs(got)                               # 원문 표는 비용을 (괄호) 음수로 적는 회사가 많다 → 양수로
        if "div_paid" in got:
            got["div_paid"]["v"] = abs(got["div_paid"]["v"])      # 배당금 지급은 나간 금액
        for k, c in got.items():
            if by_key0[k].get("until") and y > by_key0[k]["until"]:
                continue
            cells[k][y] = {**c, "from": src["_from"]}
    # 다음 해 보고서의 비교 숫자 (DART 재무 API 보고서만: 전기·전전기 칸)
    for it in items:
        for y in ys:
            if it.get("until") and y > it["until"]:
                continue
            if y in cells[it["key"]] and not (it["since"] and cells[it["key"]][y].get("via_old")):
                continue                                   # 회계기준이 바뀐 항목의 옛 줄 칸은 공식 재작성 숫자가 있으면 바꾼다
            if (it["since"] and y < it["since"] - 1) or (template == "insurance" and y < 2022):   # 보험사는 2023년에 IFRS17·IFRS9 를 같이 바꿨다
                continue                                   # 회계기준이 바뀐 항목: 바로 전 해(공식 재작성 비교 숫자)까지만. 그 앞 칸은 믿을 수 없다
                                                           # (삼성생명 2023 보고서 '전전기' 보험서비스결과 −7.1조, 보험수익 0)
            for later, field in ((y + 1, "frmtrm_amount"), (y + 2, "bfefrmtrm_amount")):
                v = _later(reps[later], it, field) if later in reps else None
                if v is not None:
                    cells[it["key"]][y] = {"v": v, "st": "restated", "name": it["label"], "from": f"{later}년 보고서 비교 숫자"}
                    break
    by_key = {it["key"]: it for it in items}
    floor = lambda it, y: (it["since"] and y < it["since"] - 1) or (template == "insurance" and y < 2022)
    # 재작성 확인 (2026-11-24 사용자): 그 해 값과 다음 해 보고서 '전기' 칸이 0.5% 넘게 다르면 두 값을 다 남긴다 (보이는 값은 처음 공시 값)
    for it in items:
        for y, c in cells[it["key"]].items():
            if c["st"] != "reported" or y + 1 not in reps or floor(it, y) or c.get("via_old") or " + " in (c.get("name") or ""):
                continue                                   # 합친 줄·옛 이름 줄은 나중 보고서의 한 줄과 바로 비교할 수 없다
            lv = _later(reps[y + 1], it, "frmtrm_amount")
            if lv is not None and not _close(abs(c["v"]), abs(lv)):
                c["later_v"], c["later_from"] = lv, f"{y + 1}년 보고서"
    # 검산 묶음은 한 해 안에서 같은 보고서 값으로 (예: 순이자이익·이자수익·이자비용). 그 해 값끼리 식이 안 맞고
    # 나중 보고서 비교 숫자끼리는 맞으면 묶음 전체를 나중 보고서 값으로 (삼성카드 2023·2024: 2025년 새 양식)
    moved: Dict[int, Tuple[int, str]] = {}
    for res, parts in CHECKS:
        grp = [res] + [q for q, _ in parts]
        if any(k not in cells for k in grp):
            continue
        for y in ys:
            cs = {k: cells[k].get(y) for k in grp}
            if any(c is None or c.get("v") is None for c in cs.values()) or any(floor(by_key[k], y) for k in grp):
                continue
            if _close(sum(cs[q]["v"] * t for q, t in parts), cs[res]["v"]):
                continue
            for later, field in ((y + 1, "frmtrm_amount"), (y + 2, "bfefrmtrm_amount")):
                if later not in reps:
                    continue
                vals = {k: _later(reps[later], by_key[k], field) for k in grp}
                if any(v is None for v in vals.values()) or not _close(sum(vals[q] * t for q, t in parts), vals[res]):
                    continue
                for k in grp:
                    old = cs[k]
                    big = old["v"] and abs(abs(vals[k]) - abs(old["v"])) > abs(old["v"]) * 0.2      # 20% 넘게 다르면 정의가 바뀐 것
                    cells[k][y] = {"v": vals[k], "st": "restated", "name": by_key[k]["label"], "from": f"{later}년 보고서 비교 숫자",
                                   "first_v": old["v"] if old["st"] == "reported" else None, "def_change": bool(big)}
                if cs[res]["st"] != "reported" and by_key[res]["sj"] == "IS":
                    moved[y] = (later, field)              # 그 해 보고서에 결과 줄 자체가 없었다 = 양식이 다른 보고서
                break
    # 한 열은 한 보고서 (2026-11-25 사용자): 양식이 바뀐 나중 보고서로 옮긴 해는 다른 손익 줄도 그 보고서 비교 숫자로
    # (삼성카드 2023·2024 판매비와관리비: 옛 양식 19,006억 → 2025년 보고서 7,995억)
    for y, (later, field) in moved.items():
        for it in items:
            c = cells[it["key"]].get(y)
            if it["sj"] != "IS" or (c and c["st"] == "restated"):
                continue
            lv = _later(reps[later], it, field)
            if lv is None or (c and c.get("v") is not None and _close(abs(c["v"]), abs(lv))):
                continue
            if it["key"] in EXPENSE:
                lv = abs(lv)
            big = bool(c and c.get("v") and abs(abs(lv) - abs(c["v"])) > abs(c["v"]) * 0.2)
            cells[it["key"]][y] = {"v": lv, "st": "restated", "name": it["label"], "from": f"{later}년 보고서 비교 숫자",
                                   "first_v": c["v"] if c and c.get("st") == "reported" else None, "def_change": big}
    # 정의가 바뀐 줄: 새 정의로 바꾼 가장 이른 해보다 앞 해 값은 '정의 변경 전' 표시 (비교 구간 끊김)
    for it in items:
        sw = [y for y, c in cells[it["key"]].items() if c.get("def_change")]
        if sw:
            for y, c in cells[it["key"]].items():
                if y < min(sw) and c.get("v") is not None:
                    c["old_def"] = True
    # 계산: 회사가 결과를 직접 적은 해 중 식이 맞은 해가 있고, 틀린 해가 없을 때만
    checks = {}
    for res, parts in CHECKS:
        if res not in cells or any(p not in cells for p, _ in parts):
            continue                                       # 이 업권 목록에 없는 식
        ok = bad = 0
        for y in ys:
            c = cells[res].get(y)
            vs = [cells[p].get(y) for p, _ in parts]
            if res == "ni" and y in disc:
                continue                                   # 중단영업이 있으면 세전이익 − 법인세 ≠ 순이익 (공시가 맞다)
            if c and c["st"] == "reported" and all(v and v["st"] == "reported" for v in vs):
                if _close(sum(v["v"] * s for v, (_, s) in zip(vs, parts)), c["v"]):
                    ok += 1
                else:
                    bad += 1
        checks[res] = {"ok": ok, "bad": bad}
        never = not any(c.get("st") == "reported" for c in cells[res].values())
        if (ok and not bad) or (never and not bad):
            for y in ys:
                vs = [cells[p].get(y) for p, _ in parts]
                if y not in cells[res] and all(v and v["st"] == "reported" for v in vs):
                    cells[res][y] = {"v": sum(v["v"] * s for v, (_, s) in zip(vs, parts)), "st": "derived",
                                     "name": " ".join(("+ " if s > 0 else "− ") + p for p, s in parts), "from": "계산",
                                     "verified": bool(ok)}     # False: 회사가 결과 줄을 적은 해가 없어 식을 확인하지 못함
    for it in items:                                  # 회계기준이 바뀌기 전 해: 같은 줄이라도 정의가 달라 '옛 기준' (비교 구간 끊김)
        if it["since"]:
            for y, c in cells[it["key"]].items():
                if y < it["since"] and c.get("v") is not None and c["st"] == "reported":
                    c["old_basis"] = True
    # 요약 줄 계산 (순영업이익 = 영업이익 + 판매비와관리비): 두 값이 있는 해마다
    for res, parts in DERIVE:
        if res not in cells or any(p not in cells for p, _ in parts):
            continue
        for y in ys:
            vs = [cells[p].get(y) for p, _ in parts]
            if all(v and v.get("v") is not None for v in vs):
                cells[res][y] = {"v": sum(v["v"] * t for v, (_, t) in zip(vs, parts)), "st": "derived", "from": "계산",
                                 "name": " + ".join(by_key[p]["label"] for p, _ in parts)}
    # 비지배지분이 없는 회사 (삼성카드·카카오뱅크): 지배주주 몫 = 전체
    nci = any(doc_fs.norm(r[1]).startswith("비지배") and r[2] for src in srcs.values() for sj in ("IS", "BS") for r in src.get(sj, []))
    if not nci:
        for own, tot in (("ni_owner", "ni"), ("equity_owner", "equity")):
            if own in cells and tot in cells and not any(c.get("v") is not None for c in cells[own].values()):
                cells[own] = {y: {"v": c["v"], "st": "derived", "from": "계산", "name": f"{by_key[tot]['label']} (비지배지분 없음)"}
                              for y, c in cells[tot].items() if c.get("v") is not None}
    # 양식 경계 (2026-11-25 사용자): 새 양식에만 있는 줄이 처음 나온 해. 그 앞 해 값은 '양식 변경 전',
    # 새 양식 줄인데 옛 해에 같은 뜻의 줄이 없으면 빈칸 대신 '옛 양식에 없음'
    nf = [it for it in items if it.get("new_form")]
    first = min((y for it in nf for y, c in cells[it["key"]].items()
                 if c.get("v") is not None and c["st"] in ("reported", "restated") and not c.get("via_old")), default=None)
    form_year = first if first and srcs and first > min(srcs) else None
    if form_year:
        # 표시는 뜻이 달라진 줄만: 옛 이름·합친 줄을 쓴 항목, 경계에서 값이 20% 넘게 달라진 항목, 그런 항목으로 계산한 줄
        # (영업이익·순이익처럼 두 양식에서 같은 줄은 표시하지 않는다)
        changed = {it["key"] for it in items if it.get("old") or it.get("sum_of") or it.get("old_calc")
                   or any(c.get("def_change") for c in cells[it["key"]].values())}
        for res, parts in CHECKS + DERIVE:
            if any(p in changed for p, _ in parts):
                changed.add(res)
        for it in items:
            if it["sj"] != "IS":
                continue
            later_has = any((cells[it["key"]].get(z) or {}).get("v") is not None for z in ys if z >= form_year)
            before_has = any((cells[it["key"]].get(z) or {}).get("v") is not None for z in ys if z < form_year)
            if before_has and not later_has:          # 옛 양식·기준에만 있던 줄 (현대해상 이자수익): 그 뒤 해는 '새 양식에 없음'
                for y in ys:
                    if y >= form_year and y in srcs and (cells[it["key"]].get(y) or {}).get("v") is None:
                        cells[it["key"]][y] = {"v": None, "st": "new_none", "form_year": form_year}
            for y in ys:
                if y >= form_year or y not in srcs:
                    continue
                c = cells[it["key"]].get(y)
                if c and c.get("v") is not None:
                    if it["key"] in changed:
                        c["old_form"] = form_year
                elif later_has:
                    cells[it["key"]][y] = {"v": None, "st": "old_none", "form_year": form_year, "why": "basis" if it["since"] else "form"}
    for it in items:                                  # 그 해 보고서에 같은 뜻의 줄이 없음 (2026-11-25 사용자: 빈칸 대신 이유)
        cs = cells[it["key"]]
        if not any(c.get("v") is not None for c in cs.values()):
            continue
        for y in ys:
            if y in srcs and (y not in cs or (cs[y].get("v") is None and cs[y].get("st") not in ("old_none", "new_none", "na"))):
                if it.get("until") and y > it["until"]:
                    continue
                cs[y] = {"v": None, "st": "absent", "why": "basis" if (it["since"] and y < it["since"]) else ""}
    for it in items:                                  # 옛 기준에만 있는 줄: 그 뒤 해는 '새 기준에 없음'
        if it.get("until") and any(c.get("v") is not None for c in cells[it["key"]].values()):
            for y in ys:
                if y > it["until"] and y in srcs:
                    cells[it["key"]][y] = {"v": None, "st": "new_none", "form_year": it["until"] + 1}
    # 해당 없음: 조건부 항목인데 한 해도 없음
    for it in items:
        if not it["need"] and not cells[it["key"]]:
            for y in ys:
                cells[it["key"]][y] = {"v": None, "st": "na"}
    return {"template": template, "basis": basis, "years": ys, "items": [{k: it[k] for k in ("key", "label", "sj", "need", "parent")} for it in items],
            "cells": cells, "checks": checks, "sources": {y: s["_from"] for y, s in srcs.items()}, "form_year": form_year}
