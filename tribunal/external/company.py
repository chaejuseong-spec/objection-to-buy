"""기본 종목정보 (2026-11-22): 대시보드 '기본 종목정보' 탭에 보일 회사의 현재 재무·실적·배당·시세.

리포트 검증과는 별개로 '지금' 기준이다(리포트 작성일 기준이 아님). AI 를 쓰지 않는다.
- DART: 기업개황, 재무제표 전체(연결, 없으면 별도) 최근 6년의 사업·분기·반기 보고서, 배당에 관한 사항
- KRX: 가장 최근 거래일 시세, 최근 52주(약 250거래일) 종가 범위 (하루치 파일을 받아 모아 둔다. 처음 한 번만 느림)
숫자는 공시·시세 값을 그대로 쓰고, 비율(부채비율, ROE 등)은 그 값으로 코드가 계산한다. 없는 값은 None(화면에서 '-').

분기 값: 손익은 분기 보고서의 3개월 값, 4분기는 '연간 − 3분기 누적'(derived 표시). 재무상태는 그 시점 값.
현금흐름은 분기 보고서에 누적 값만 있어 분기 표에서는 '누적'으로 표시한다.

실행: python -m tribunal.external.company <분석 결과 폴더>      -> result.json 의 company 칸을 채운다 (무료)
      python -m tribunal.external.company --prices <종목코드>  -> KRX 52주 시세만 미리 모으기 (처음 약 10분)
"""
from __future__ import annotations

import html
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from .dart import Dart, DartError, original_url
from .krx import Krx, KrxError, num

REPRT = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}
QEND = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}

# 핵심 계정: (키, 이름, 표, 후보 account_id, 후보 이름). 회사·업종마다 쓰는 id 가 달라 여러 개를 차례로 본다
KEYS = [
    ("revenue", "매출액(영업수익)", "IS", ["ifrs-full_Revenue", "ifrs-full_RevenueFromContractsWithCustomers"], ["매출액", "영업수익", "수익(매출액)", "매출"]),
    ("op", "영업이익", "IS", ["dart_OperatingIncomeLoss", "ifrs-full_ProfitLossFromOperatingActivities"], ["영업이익", "영업이익(손실)"]),
    ("pretax", "법인세차감전순이익", "IS", ["ifrs-full_ProfitLossBeforeTax"], ["법인세비용차감전순이익"]),
    ("ni", "당기순이익", "IS", ["ifrs-full_ProfitLoss"], ["당기순이익", "당기순이익(손실)"]),
    ("ni_owner", "지배주주 순이익", "IS", ["ifrs-full_ProfitLossAttributableToOwnersOfParent"], ["지배기업의 소유주에게 귀속되는 당기순이익(손실)",
                                                                                          "지배기업의 소유주지분", "지배기업소유주지분"]),   # 코드 없는 옛 공시: 손익 귀속이 포괄손익 귀속보다 먼저 나온다
    ("assets", "자산총계", "BS", ["ifrs-full_Assets"], ["자산총계"]),
    ("liab", "부채총계", "BS", ["ifrs-full_Liabilities"], ["부채총계"]),
    ("equity", "자본총계", "BS", ["ifrs-full_Equity"], ["자본총계"]),
    ("equity_owner", "지배주주 자본", "BS", ["ifrs-full_EquityAttributableToOwnersOfParent"], ["지배기업의 소유주에게 귀속되는 자본", "지배기업지분"]),
    ("cur_assets", "유동자산", "BS", ["ifrs-full_CurrentAssets"], ["유동자산"]),
    ("cur_liab", "유동부채", "BS", ["ifrs-full_CurrentLiabilities"], ["유동부채"]),
    ("cogs", "매출원가", "IS", ["ifrs-full_CostOfSales"], ["매출원가"]),
    ("gross", "매출총이익", "IS", ["ifrs-full_GrossProfit"], ["매출총이익", "매출총이익(손실)"]),
    ("sga", "판매비와관리비", "IS", ["dart_TotalSellingGeneralAdministrativeExpenses", "ifrs-full_SellingGeneralAndAdministrativeExpense"], ["판매비와관리비"]),
    ("inventory", "재고자산", "BS", ["ifrs-full_Inventories"], ["재고자산"]),
    ("capex", "설비투자(유형자산 취득)", "CF", ["ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"], ["유형자산의 취득", "유형자산의취득"]),
    ("cfo", "영업활동현금흐름", "CF", ["ifrs-full_CashFlowsFromUsedInOperatingActivities"], ["영업활동현금흐름", "영업활동으로 인한 현금흐름"]),
    ("cfi", "투자활동현금흐름", "CF", ["ifrs-full_CashFlowsFromUsedInInvestingActivities"], ["투자활동현금흐름"]),
    ("cff", "재무활동현금흐름", "CF", ["ifrs-full_CashFlowsFromUsedInFinancingActivities"], ["재무활동현금흐름"]),
    # 보험사 손익 (2026-11-23): 손익계산서의 보험서비스결과·투자손익, 배당금수익 (연결이 있으면 연결)
    ("ins_profit", "보험손익(보험서비스결과)", "IS", ["ifrs-full_InsuranceServiceResult"], ["보험서비스결과", "보험손익"]),
    ("inv_profit", "투자손익", "IS", ["dart_InvestmentIncomeExpenses"], ["투자손익"]),
    ("div_rev", "배당금수익", "IS", ["ifrs-full_RevenueFromDividends"], ["배당금수익", "배당수익"]),
]
SJ = {"IS": ("IS", "CIS"), "BS": ("BS",), "CF": ("CF",)}
OLD_FILL = False                                 # 전면 개편 1차: 10년용 손질(원문 표·옛 양식·핵심 재무제표 표) 끔. True 면 예전처럼
RATIO_YEARS = 11                                 # 전면 개편 1차: 핵심 숫자(자산·순이익 등 표준 태그 몇 줄)는 10년 → 비금융 지표 비율 10년 (금융은 API 가 2023년부터라 저절로 3년)
FS_YEARS = 4                                     # 전면 개편 1차 (2026-11-25): 최근 3년 + 올해 (2026년이면 2023~2026)
FINANCIAL_INDUSTRY = ("64", "65", "66")          # 표준산업분류: 금융(은행·지주), 보험, 금융 지원 서비스


def _num(v) -> Optional[float]:
    t = str(v if v is not None else "").replace(",", "").strip()
    if t in ("", "-"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _aid(x: str) -> str:
    """계정 코드: 옛 공시(2019년 무렵까지)는 'ifrs_ProfitLoss', 요즘은 'ifrs-full_ProfitLoss'. 같은 계정으로 본다"""
    return (x or "").replace("ifrs-full_", "ifrs_")


def _find(rows: List[dict], sj: str, ids: List[str], names: List[str]) -> Optional[dict]:
    rows = [r for r in rows if r.get("sj_div") in SJ[sj]]
    for i in ids:
        hit = next((r for r in rows if _aid(r.get("account_id")) == _aid(i)), None)
        if hit:
            return hit
    # 이름으로: 분기·반기 보고서는 '분기순이익'·'반기순이익(손실)'로 적는다 → '당기순이익'과 같게 본다 (코드 없는 옛 공시, HD한국조선해양 2020)
    # '연결당기순이익', '당기연결순이익', '연결분기순이익(손실)'(현대차·SK텔레콤·SK이노베이션 옛 공시)도 '당기순이익'으로 본다
    nm = lambda x: re.sub(r"(분기|반기)(?=순|연결순)", "당기", re.sub(r"연결", "", (x or "").replace(" ", "")))
    for n in names:
        hit = next((r for r in rows if nm(r.get("account_nm")) == nm(n)), None)
        if hit:
            return hit
    return None


EPS_ID = "ifrs-full_BasicEarningsLossPerShare"
EPS_NAMES = ("기본주당이익", "기본주당순이익", "기본주당이익(손실)", "기본주당순이익(손실)", "기본주당손익")


def _eps_row(rows: List[dict]) -> Optional[dict]:
    """회사가 공시한 기본주당이익 줄 (2026-11-26 사용자: EPS 는 계산하지 않고 공시값). 보통주 줄 먼저,
    우선주·희석·중단영업 줄은 뺀다. '계속영업' 줄은 다른 줄이 없을 때만"""
    rows = [r for r in rows if r.get("sj_div") in SJ["IS"]]
    c = [r for r in rows if _aid(r.get("account_id")) == _aid(EPS_ID)]
    if not c:
        c = [r for r in rows if re.sub(r"\s", "", r.get("account_nm") or "") in EPS_NAMES]
    c = [r for r in c if not re.search(r"우선|희석|중단", r.get("account_nm") or "")]
    c = [r for r in c if "계속" not in (r.get("account_nm") or "")] or c
    common = [r for r in c if "보통" in (r.get("account_nm") or "")]
    return (common or c or [None])[0]


NCI_EQ = ("ifrs-full_NoncontrollingInterests", ("비지배지분",))
NCI_NI = ("ifrs-full_ProfitLossAttributableToNoncontrollingInterests", ("비지배지분에귀속되는당기순이익", "비지배지분에귀속되는당기순이익(손실)", "비지배지분순이익", "비지배지분"))


def _nci_row(rows: List[dict], sj: str, spec) -> Optional[dict]:
    """비지배지분 줄 (2026-11-26: ROE 지배주주 규칙). 태그 먼저, 없으면 이름(띄어쓰기 무시). 포괄손익 귀속 줄은 뺀다"""
    aid, names = spec
    rows = [r for r in rows if r.get("sj_div") in SJ[sj]]
    hit = [r for r in rows if _aid(r.get("account_id")) == _aid(aid)]
    if not hit:
        hit = [r for r in rows if re.sub(r"\s", "", r.get("account_nm") or "") in names]
    hit = [r for r in hit if "포괄" not in (r.get("account_nm") or "")]
    return hit[0] if hit else None


def owner_vals(p: dict) -> tuple:
    """(지배주주 순이익, 지배주주 자본, 사유). 지배주주 줄이 있으면 그 값. 지배주주·비지배지분 줄이 모두 없으면(비지배지분 없음)
    전체 값이 곧 지배주주 몫. 비지배지분이 있는데 지배주주 값이 없으면 None (다른 값으로 채우지 않는다)"""
    eo, eq = p.get("equity_owner"), p.get("equity")
    # 비지배지분 없음 = 비지배지분(자본·순이익)이 0 이거나 줄이 없고, 지배주주 자본 줄이 있으면 전체 자본과 같다 (삼성카드·S-Oil: 순이익 귀속 줄만 없음)
    no_nci = (p.get("nci") in (None, 0) and p.get("ni_nci") in (None, 0)
              and (eo is None or (eq is not None and abs(eo - eq) < 1)))
    n = p.get("ni_owner") if p.get("ni_owner") is not None else (p.get("ni") if no_nci else None)
    e = eo if eo is not None else (eq if no_nci else None)
    calc = False
    # 비지배지분이 있는데 지배주주 줄이 없으면: 전체 − 비지배지분 (둘 다 공시값, 회계상 항상 성립, 2026-11-26 사용자 'B'. DB손해보험 2023 3·4분기)
    if n is None and p.get("ni") is not None and p.get("ni_nci") is not None:
        n, calc = p["ni"] - p["ni_nci"], True
    if e is None and eq is not None and p.get("nci") is not None:
        e, calc = eq - p["nci"], True
    why = ("지배주주" if p.get("ni_owner") is not None and eo is not None else "비지배지분 없음" if no_nci
           else "전체 − 비지배지분 (계산)" if calc and n is not None and e is not None else "지배주주 값 없음")
    return n, e, why


def _year_ago(period: str) -> str:
    return f"{int(period[:4]) - 1}{period[4:]}"


def add_returns(quarter: List[dict], annual: List[dict], prior: Optional[Dict[str, dict]] = None) -> None:
    """ROE·ROA (2026-11-26 사용자, 모든 화면 같은 값):
    분기 = 최근 4분기 지배주주 순이익 ÷ ((그 분기 말 + 1년 전 같은 분기 말 지배주주 자본) / 2) × 100,
    ROA = 최근 4분기 순이익 ÷ ((그 분기 말 + 1년 전 총자산) / 2) × 100. 4분기가 이어지고 값이 다 있을 때만, 평균이 0 이하면 None.
    연간 = 그해 순이익 ÷ ((전년 말 + 그해 말) / 2) × 100"""
    prior = prior or {}                                   # 기간 칸 밖의 1년 전 말 재무상태 (다음 해 보고서의 전기말 칸)
    by = {**prior, **{x["period"]: x for x in quarter}}
    for i, x in enumerate(quarter):
        x["roe"] = x["roa"] = None
        last4 = quarter[i - 3:i + 1] if i >= 3 else []
        if len(last4) != 4 or not all(_qnext(a["period"]) == b["period"] for a, b in zip(last4, last4[1:])):
            continue
        prev = by.get(_year_ago(x["period"]))
        ns = [owner_vals(y)[0] for y in last4]
        e1, e0 = owner_vals(x)[1], (owner_vals(prev)[1] if prev else None)
        if all(v is not None for v in ns) and e1 is not None and e0 is not None and (e1 + e0) > 0:
            x["roe"] = round(sum(ns) / ((e1 + e0) / 2) * 100, 4)
            if any(owner_vals(y)[2].endswith("(계산)") for y in [*last4, prev]):
                x.setdefault("derived", []).append("roe")          # * : 지배주주 값을 전체 − 비지배지분으로 계산
        nt = [y.get("ni") for y in last4]
        a1, a0 = x.get("assets"), (prev.get("assets") if prev else None)
        if all(v is not None for v in nt) and a1 is not None and a0 is not None and (a1 + a0) > 0:
            x["roa"] = round(sum(nt) / ((a1 + a0) / 2) * 100, 4)
    ay = {a["period"]: a for a in annual}
    for a in annual:
        a["roe"] = a["roa"] = None
        prev = ay.get(str(int(a["period"]) - 1)) or prior.get(f"{int(a['period']) - 1}.12")
        if not prev:
            continue
        n, e1, _ = owner_vals(a)
        e0 = owner_vals(prev)[1]
        if n is not None and e1 is not None and e0 is not None and (e1 + e0) > 0:
            a["roe"] = round(n / ((e1 + e0) / 2) * 100, 4)
            if owner_vals(a)[2].endswith("(계산)") or owner_vals(prev)[2].endswith("(계산)"):
                a.setdefault("derived", []).append("roe")
        if a.get("ni") is not None and a.get("assets") is not None and prev.get("assets") is not None and (a["assets"] + prev["assets"]) > 0:
            a["roa"] = round(a["ni"] / ((a["assets"] + prev["assets"]) / 2) * 100, 4)


def bps_of(p: dict, shares: Optional[float]) -> tuple:
    """BPS = 지배주주 자본 ÷ 유통주식수(자기주식 제외·우선주 포함) (2026-11-26 사용자, 모든 화면 같은 값).
    지배주주 자본은 ROE 와 같은 규칙(owner_vals): 지배주주 줄 → 비지배지분 없음이면 전체 → 전체 − 비지배지분(계산) → 없음.
    (BPS, 계산 여부)"""
    e = owner_vals(p)[1]
    calc = p.get("equity_owner") is None and e is not None and p.get("nci") not in (None, 0)   # 자본을 전체 − 비지배지분으로 구했을 때만 *
    return (round(e / shares) if e is not None and shares else None), calc


def per_of(price: Optional[float], eps: Optional[float]) -> Optional[float]:
    """PER = 주가 ÷ EPS(회사 공시 기본주당이익) (2026-11-26 사용자). EPS 가 0 이하이거나 없으면 계산하지 않는다"""
    return round(price / eps, 2) if price and eps is not None and eps > 0 else None


def split_factors(periods: List[str], shares: Dict[str, float]) -> Dict[str, float]:
    """액면분할·병합 배수 (2026-11-26 사용자 A: 기업개요·재무 추이·경쟁사 같은 함수). 기간마다 유통주식수(DART 분기 보고서)가
    바로 다음 기간보다 1.8배 넘게 다르면 분할로 보고, 그 앞 기간 값에 쌓인 배수를 준다 (가장 최근 기간 = 1). 주식 수가 없으면 1"""
    ps = sorted(set(periods))
    out: Dict[str, float] = {ps[-1]: 1.0} if ps else {}
    f = 1.0
    for i in range(len(ps) - 1, 0, -1):
        a, b = shares.get(ps[i - 1]), shares.get(ps[i])
        if a and b:
            r = b / a
            if r >= 1.8:
                f *= round(r)
            elif r <= 1 / 1.8:
                f *= 1 / round(1 / r)
        out[ps[i - 1]] = f
    return out


def eps_ttm_adj(quarter: List[dict], i: int, fac: Optional[Dict[str, float]] = None) -> Optional[float]:
    """최근 4분기 공시 EPS 합, 분기마다 지금 주식 수 기준으로 맞춘 뒤 더한다 (fac = split_factors). 4분기가 이어지고 다 있을 때만"""
    if eps_ttm(quarter, i) is None:
        return None
    fac = fac or {}
    return round(sum(x["eps_disc"] / (fac.get(x["period"]) or 1.0) for x in quarter[i - 3:i + 1]))


def eps_ttm(quarter: List[dict], i: int) -> Optional[float]:
    """i 번째 분기까지 최근 4분기 공시 기본주당이익 합. 4분기가 이어지고 모두 있을 때만 (없으면 None, 계산 EPS 로 채우지 않는다)"""
    last4 = quarter[i - 3:i + 1] if i >= 3 else []
    if len(last4) != 4 or not all(_qnext(a["period"]) == b["period"] for a, b in zip(last4, last4[1:])):
        return None
    if any(x.get("eps_disc") is None for x in last4):
        return None
    return sum(x["eps_disc"] for x in last4)


def _report(dart: Dart, corp_code: str, year: int, q: int, only: Optional[str] = None) -> Optional[dict]:
    """그 기간 보고서의 재무제표. 연결이 없으면 별도 (only='OFS' 면 별도만). {fs, rows, rcept_no}"""
    for fs in ((only,) if only else ("CFS", "OFS")):
        try:
            res = dart.statements(corp_code, year, fs, REPRT[q])
        except DartError:
            return None
        if res.get("status") == "000" and res.get("list"):
            return {"fs": "연결" if fs == "CFS" else "별도", "rows": res["list"], "rcept_no": res["list"][0].get("rcept_no", "")}
    return None


BS_KEYS = [k[0] for k in KEYS if k[2] == "BS"]


def _drop_repeats(quarter: List[dict]) -> None:
    """재무상태 합계가 바로 앞 분기와 원 단위까지 똑같으면 DART API 가 앞 보고서 값을 되풀이한 것으로 보고 비운다
    (2026-11-22 확인: 한 보험사의 2025 1·2·3분기 보고서가 자산총계·부채총계를 같은 값으로 줌, 자본만 바뀜)"""
    orig = [{k: p.get(k) for k in BS_KEYS} for p in quarter]
    for i in range(1, len(quarter)):
        for k in BS_KEYS:
            v = orig[i][k]
            if v is not None and v == orig[i - 1][k] and abs(v) > 1e9:
                quarter[i][k] = None
                quarter[i].setdefault("missing", []).append(k)


XBRL_BS = {"assets": "Assets", "liab": "Liabilities", "equity": "Equity"}


def _fill_from_xbrl(dart: Dart, reps: Dict[tuple, dict], quarter: List[dict]) -> None:
    """API 가 되풀이해 비운 자산·부채·자본 합계를 그 보고서의 원본 파일(XBRL)에서 다시 찾는다.
    원본에도 같은 날짜 값이 둘 있을 수 있어(2026-11-22 확인), 자산 − 자본 = 부채가 맞는 조합만 쓴다."""
    for p in quarter:
        if not p.get("missing"):
            continue
        y, m = int(p["period"][:4]), int(p["period"][5:7])
        rep = reps.get((y, m // 3))
        if not rep:
            continue
        try:
            facts = dart.xbrl_facts(rep["rcept_no"], REPRT[m // 3]).get("facts", [])
        except DartError:
            continue
        want = "SeparateMember" if rep["fs"] == "별도" else "ConsolidatedMember"
        cand = {k: sorted({f["value"] for f in facts if f["name"] == tag and f.get("instant") == p["end"]
                           and f["dims"] in ({}, {"ConsolidatedAndSeparateFinancialStatementsAxis": want})})
                for k, tag in XBRL_BS.items()}
        for a in cand["assets"] or [p.get("assets")]:
            for e in cand["equity"] or [p.get("equity")]:
                for l in cand["liab"] or [p.get("liab")]:
                    if None not in (a, e, l) and abs(a - e - l) <= abs(a) * 1e-6:
                        for k, v in (("assets", a), ("equity", e), ("liab", l)):
                            if k in p["missing"]:
                                p[k] = v
                                p["missing"].remove(k)
                                p.setdefault("fixed", []).append(k)
                        break
                else:
                    continue
                break
            else:
                continue
            break


def _da(facts: List[dict], end: str, fs: str, start: Optional[str] = None) -> Optional[tuple]:
    """감가상각비 + 무형자산상각비 (EBITDA 용, 2026-11-24 사용자): 재무제표 원본(XBRL) 주석의 그해 값. (금액, 쓴 항목) 또는 None.
    고르는 순서: 현금흐름 조정의 감가상각·상각 합 > 감가상각·상각비 합 한 줄 > 감가상각비 + 무형자산상각비"""
    want = "SeparateMember" if fs == "별도" else "ConsolidatedMember"
    val: Dict[str, float] = {}
    for f in facts:
        if f.get("end", "")[:10] != end or not f.get("start") or f.get("value") is None:
            continue
        if start and f["start"][:10] != start:                # 분기·누적: 시작일까지 같아야
            continue
        if not start and (date.fromisoformat(end) - date.fromisoformat(f["start"][:10])).days < 300:
            continue
        if f["dims"] not in ({}, {"ConsolidatedAndSeparateFinancialStatementsAxis": want}):
            continue
        val.setdefault(f["name"], f["value"])
    for names, label in ((("AdjustmentsForDepreciationAndAmortisationExpense",), "현금흐름 조정의 감가상각비와 상각비"),
                         (("DepreciationAndAmortisationExpense",), "감가상각비와 상각비"),
                         (("AdjustmentsForDepreciationExpense", "AdjustmentsForAmortisationExpense"), "현금흐름 조정의 감가상각비 + 무형자산상각비"),
                         (("DepreciationExpense", "AmortisationExpense"), "감가상각비 + 무형자산상각비")):
        if all(n in val for n in names):
            return sum(val[n] for n in names), label
    # 나눠서만 적은 회사(SK하이닉스·현대차·NAVER·LG화학): 유형자산 + 사용권자산 + 투자부동산 감가상각비 + 무형자산상각비
    parts = ("DepreciationPropertyPlantAndEquipment", "DepreciationRightofuseAssets", "DepreciationInvestmentProperty", "AmortisationIntangibleAssetsOtherThanGoodwill")
    if "DepreciationPropertyPlantAndEquipment" in val:
        return sum(val[n] for n in parts if n in val), "유형자산·사용권자산·투자부동산 감가상각비 + 무형자산상각비"
    return None


def _fill_da(dart: Dart, reps: Dict[tuple, dict], annual: List[dict], quarter: Optional[List[dict]] = None) -> None:
    """감가상각비·상각비(da)를 붙인다 (EBITDA 용). 연간: 사업보고서 주석. 분기: 분기·반기 보고서 주석의 3개월 값,
    4분기 = 연간 − 3분기 누적(계산 표시). 주석 숫자가 없는 해(옛 공시)는 비워 둔다"""
    for a in annual:
        rep = reps.get((int(a["period"]), 4))
        if not rep:
            continue
        try:
            got = _da(dart.xbrl_facts(rep["rcept_no"], "11011").get("facts", []), a["end"][:10], rep["fs"])
        except DartError:
            continue
        if got:
            a["da"], a["da_basis"] = got
    if not quarter:
        return
    _fill_da_quarters(dart, reps, annual, quarter)
    # 검사: 회사가 원본 파일에 다른 기간 값을 잘못 적은 경우가 있다(SK하이닉스 2025년 1분기에 2024년 연간 값).
    # 네 분기 합이 연간과 0.5% 넘게 다르거나, 한 분기가 연간의 45%를 넘거나 0 이하면 그해 분기 값은 비운다.
    # 연간이 아직 없는 해는 앞 해 분기 평균의 0.5~2배 안일 때만 쓴다
    ann = {int(a["period"]): a.get("da") for a in annual}
    byy: Dict[int, List[dict]] = {}
    for p in quarter:
        if p.get("da") is not None:
            byy.setdefault(int(p["period"][:4]), []).append(p)
    prev_avg = None
    for y in sorted(byy):
        ps, tot = byy[y], ann.get(y)
        vals = [p["da"] for p in ps]
        if tot:
            ok = len(ps) == 4 and abs(sum(vals) - tot) <= abs(tot) * 0.005 and all(0 < v <= tot * 0.45 for v in vals)
        else:
            ok = prev_avg is not None and all(prev_avg * 0.5 <= v <= prev_avg * 2 for v in vals)
        if not ok:
            for p in ps:
                p["da"] = None
                if "da" in p.get("derived", []):
                    p["derived"].remove("da")
        else:
            prev_avg = sum(vals) / len(vals)


def _fill_da_quarters(dart: Dart, reps: Dict[tuple, dict], annual: List[dict], quarter: List[dict]) -> None:
    years = {int(a["period"]) for a in annual if a.get("da") is not None}
    if not years:
        return
    first = min(years)
    ann = {int(a["period"]): a for a in annual}
    for p in quarter:
        y, m = int(p["period"][:4]), int(p["period"][5:7])
        if y < first:
            continue
        q = m // 3
        if q == 4:                                        # 4분기 = 연간 − 3분기 누적
            rep3 = reps.get((y, 3))
            a = ann.get(y)
            if not rep3 or not a or a.get("da") is None:
                continue
            try:
                cum = _da(dart.xbrl_facts(rep3["rcept_no"], REPRT[3]).get("facts", []), f"{y}-09-30", rep3["fs"], f"{y}-01-01")
            except DartError:
                continue
            if cum:
                p["da"] = a["da"] - cum[0]
                p["derived"].append("da")
            continue
        rep = reps.get((y, q))
        if not rep:
            continue
        try:
            facts = dart.xbrl_facts(rep["rcept_no"], REPRT[q]).get("facts", [])
        except DartError:
            continue
        got = _da(facts, p["end"][:10], rep["fs"], f"{y}-{(q - 1) * 3 + 1:02d}-01")
        if got:
            p["da"] = got[0]
            continue
        if q > 1:                                         # 3개월 값이 없으면: 이번 누적 − 앞 분기 누적 (HD한국조선해양)
            cum = _da(facts, p["end"][:10], rep["fs"], f"{y}-01-01")
            prev = reps.get((y, q - 1))
            if cum and prev:
                try:
                    pf = dart.xbrl_facts(prev["rcept_no"], REPRT[q - 1]).get("facts", [])
                except DartError:
                    continue
                pend = f"{y}-{QEND[q - 1]}"
                pc = _da(pf, pend, prev["fs"], f"{y}-01-01")
                if pc:
                    p["da"] = cum[0] - pc[0]
                    p["derived"].append("da")


def _check_liab(p: dict) -> None:
    """부채총계 = 자산총계 − 자본총계. DART API 가 다른 분기 값을 되풀이해 주는 일이 있어(2025 분기 보고서들), 맞지 않으면 자산−자본으로 바꾸고 표시"""
    a, e, l = p.get("assets"), p.get("equity"), p.get("liab")
    if a is not None and e is not None and l is not None and abs(a - e - l) > abs(a) * 0.001:
        p["liab"] = a - e
        p.setdefault("derived", []).append("liab")


def _fill_early(dart: Dart, corp_code: str, reps: Dict[tuple, dict], fs: str, min_year: int = 0) -> None:
    """첫 해 앞쪽 분기가 DART 재무 API 에 없을 때 (금융회사: 2023년 3분기부터, 2026-11-25 사용자 'B'):
    손익·현금흐름 = 다음 해 같은 분기 보고서의 '전기' 칸 (API, 태그 그대로), 재무상태 = 그 분기 보고서 원문 표 (doc_fs).
    원문 줄은 그해 첫 API 보고서의 같은 이름 줄 태그를 붙여 한 줄로 잇는다. 채운 기간은 rep['early'] 에 적는다"""
    from . import doc_fs
    y0 = min(y for y, _ in reps)
    if y0 < min_year:                                 # 재무제표 표 기간보다 옛 해는 채우지 않는다
        return
    for q in (1, 2, 3):
        if (y0, q) in reps or (y0 + 1, q) not in reps or not any((y0, k) in reps for k in range(q + 1, 5)):
            continue
        nxt = reps[(y0 + 1, q)]
        rows = []
        for r in nxt["rows"]:
            if r.get("sj_div") in ("IS", "CIS"):
                rows.append({**r, "thstrm_amount": r.get("frmtrm_q_amount"), "thstrm_add_amount": r.get("frmtrm_add_amount")})
            elif r.get("sj_div") == "CF":
                rows.append({**r, "thstrm_amount": r.get("frmtrm_amount")})
        bs_ok = False
        try:
            doc = doc_fs.period_doc(dart, corp_code, y0, q, fs)
        except (DartError, ValueError, IndexError, KeyError):
            doc = None
        bs = doc_fs._one(doc, "BS", "plain", "cum", "3m") if doc else []
        if bs and doc_fs._bs_ok(doc_fs.pick({"BS": bs, "IS": [], "CF": []})):
            ref_q = min(k for k in range(q + 1, 5) if (y0, k) in reps)
            ref = [r for r in reps[(y0, ref_q)]["rows"] if r.get("sj_div") == "BS"]
            lk2 = lambda names: [k.replace("와", "및") for k in doc_fs.lkeys(names)]     # '부채와자본총계' = '부채 및 자본 총계'
            by = dict(zip(lk2([r.get("account_nm") or "" for r in ref]), ref))
            for k in sorted((k for k in reps if not reps[k].get("early")), reverse=True):   # 그 보고서에 없으면 다른 해 API 보고서의 같은 이름 줄
                rr = [r for r in reps[k]["rows"] if r.get("sj_div") == "BS"]
                for kk, r in zip(lk2([r.get("account_nm") or "" for r in rr]), rr):
                    by.setdefault(kk, r)
            for i, (lk, (nm, v)) in enumerate(zip(lk2([n for n, _ in bs]), bs)):
                hit = by.get(lk)
                if not hit:                               # 합계 줄은 이름이 회사·해마다 다르다 → 뜻으로 태그를 찾는다
                    want = ("ifrs-full_EquityAndLiabilities" if all(w in lk for w in ("부채", "자본", "총계"))
                            else "ifrs-full_EquityAttributableToOwnersOfParent" if "지배" in lk and "비지배" not in lk and ("자본" in lk or "지분" in lk) and "기타" not in lk
                            else None)
                    hit = next((r for r in by.values() if want and r.get("account_id") == want), None)
                rows.append({"sj_div": "BS", "ord": str(i + 1), "thstrm_amount": str(v),
                             "account_id": hit["account_id"] if hit else "-표준계정코드 미사용-",
                             "account_nm": hit["account_nm"] if hit else doc_fs.show(nm)})
            bs_ok = True
        reps[(y0, q)] = {"fs": fs, "rows": rows, "rcept_no": (doc or {}).get("rcept_no") or nxt["rcept_no"],
                         "early": {"prior_from": nxt["rcept_no"], "bs_doc": (doc or {}).get("rcept_no") if bs_ok else None}}


def statements(dart: Dart, corp_code: str, years: int = RATIO_YEARS, today: Optional[date] = None, only: Optional[str] = None,
               table_years: int = FS_YEARS) -> dict:
    """최근 years 년의 보고서들로 분기·연간 핵심 계정(지표 비율용), 최근 table_years 년으로 재무제표 전체 표를 만든다."""
    today = today or date.today()
    reps: Dict[tuple, dict] = {}
    for y in range(today.year - years + 1, today.year + 1):
        for q in (1, 2, 3, 4):
            if date(y, int(QEND[q][:2]), int(QEND[q][3:])) >= today:
                continue
            r = _report(dart, corp_code, y, q, only)
            if r:
                reps[(y, q)] = r
    if not reps:
        return {"periods": [], "annual": [], "quarter": [], "tables": {}, "fs": None}
    fs = reps[max(reps)]["fs"]
    reps = {k: v for k, v in reps.items() if v["fs"] == fs}          # 연결·별도를 섞지 않는다
    try:
        _fill_early(dart, corp_code, reps, fs, today.year - table_years + 1)
    except Exception:                                  # noqa: BLE001 (덧붙임이라 실패해도 나머지 재무는 그대로)
        pass

    trep = {k: v for k, v in reps.items() if k[0] >= today.year - table_years + 1}   # 재무제표 표·EBITDA 기간 (최근 3년 + 올해)

    def val(rep, key, field):
        k = next(x for x in KEYS if x[0] == key)
        row = _find(rep["rows"], k[2], k[3], k[4])
        return _num(row.get(field)) if row else None

    def eps(rep, field):
        row = _eps_row(rep["rows"])
        return _num(row.get(field)) if row else None

    def nci(rep, sj, spec, field):
        row = _nci_row(rep["rows"], sj, spec)
        return _num(row.get(field)) if row else None

    annual, quarter = [], []
    for (y, q), rep in sorted(reps.items()):
        if q == 4:
            a = {"period": f"{y}", "end": f"{y}-12-31", "rcept_no": rep["rcept_no"], "derived": [],
                 **{k[0]: val(rep, k[0], "thstrm_amount") for k in KEYS}, "eps_disc": eps(rep, "thstrm_amount"),
                 "nci": nci(rep, "BS", NCI_EQ, "thstrm_amount"), "ni_nci": nci(rep, "IS", NCI_NI, "thstrm_amount")}
            _check_liab(a)
            annual.append(a)
    for (y, q), rep in sorted(reps.items()):
        p = {"period": f"{y}.{q * 3:02d}", "end": f"{y}-{QEND[q]}", "rcept_no": rep["rcept_no"], "derived": []}
        for key, _, sj, _, _ in KEYS:
            if sj == "BS":
                p[key] = val(rep, key, "thstrm_amount")
            elif sj == "IS":
                if q < 4:
                    p[key] = val(rep, key, "thstrm_amount")                  # 분기 보고서의 3개월 값
                else:
                    a, c = val(rep, key, "thstrm_amount"), (val(reps[(y, 3)], key, "thstrm_add_amount") if (y, 3) in reps else None)
                    p[key] = a - c if a is not None and c is not None else None
                    p["derived"].append(key)
            else:                                                            # 현금흐름: 누적
                p[key] = val(rep, key, "thstrm_amount")
        p["nci"] = nci(rep, "BS", NCI_EQ, "thstrm_amount")                  # 비지배지분 (ROE 지배주주 규칙)
        if q < 4:
            p["ni_nci"] = nci(rep, "IS", NCI_NI, "thstrm_amount")
        else:
            a9, c9 = nci(rep, "IS", NCI_NI, "thstrm_amount"), (nci(reps[(y, 3)], "IS", NCI_NI, "thstrm_add_amount") if (y, 3) in reps else None)
            p["ni_nci"] = a9 - c9 if a9 is not None and c9 is not None else a9
        if q < 4:                                                            # 공시 기본주당이익: 분기 3개월 값, 4분기 = 연간 − 3분기 누적
            p["eps_disc"] = eps(rep, "thstrm_amount")
        else:
            a, c = eps(rep, "thstrm_amount"), (eps(reps[(y, 3)], "thstrm_add_amount") if (y, 3) in reps else None)
            p["eps_disc"] = a - c if a is not None and c is not None else None
            p["derived"].append("eps_disc")
        quarter.append(p)
    _drop_repeats(quarter)
    _fill_from_xbrl(dart, reps, quarter)
    try:
        _fill_da(dart, trep, annual, quarter)        # EBITDA 는 주석(XBRL)이 있는 최근 해만 (옛 해 원본 파일은 받지 않는다)
    except Exception:                                  # noqa: BLE001 (EBITDA 는 덧붙임이라 실패해도 나머지 재무는 그대로)
        pass
    for p in quarter:
        _check_liab(p)
    # 가장 이른 해의 1년 전 말(예: 금융회사 2022.12) 자본·총자산: 그 해 사업보고서 재무상태표 '전기말' 칸 (2026-11-26 사용자 1단계).
    # 보험사는 2023 보고서의 전기 칸이 새 회계기준(IFRS17)으로 다시 계산된 값이라 2023 값과 기준이 맞다. 화면 기간 칸에는 넣지 않는다
    prior_bal: Dict[str, dict] = {}
    y0 = min(y for y, _ in reps)
    pk = f"{y0 - 1}.12"
    if pk not in {x["period"] for x in quarter}:
        src = reps.get((y0, 4)) or next((reps[k] for k in sorted(reps) if k[0] == y0 and not reps[k].get("early")), None)
        if src:
            rec = {"period": pk, "rcept_no": src["rcept_no"], **{k: val(src, k, "frmtrm_amount") for k in ("equity", "equity_owner", "assets")},
                   "nci": nci(src, "BS", NCI_EQ, "frmtrm_amount")}
            if rec.get("equity") is not None or rec.get("equity_owner") is not None:
                prior_bal[pk] = rec
    add_returns(quarter, annual, prior_bal)

    # 재무제표 상세 (전면 개편 1차, 2026-11-25 사용자): 공시된 줄 그대로. 줄은 계정 태그(코드)로 맞춘다 —
    # 같은 태그 = 같은 뜻이라 회사가 해마다 이름을 바꿔도 한 줄 (이름은 가장 최근 보고서 것). 태그 없는 줄은 이름(띄어쓰기 무시)으로.
    # 한 보고서 안에 같은 태그가 여러 줄이면(계속영업·전체 순이익 등) 두 번째부터 이름까지 붙여 따로 둔다.
    # 다른 보고서의 비교(전기) 숫자로 빈칸을 채우지 않는다
    def row_ids(rows):
        """줄 열쇠: 표(손익/포괄손익 따로) + 태그. 같은 표에 같은 태그가 또 나오면 이름을 붙인다.
        이름 비교는 띄어쓰기·'(손실)'·분기/반기/당기 차이를 무시한다 (분기순이익 = 반기순이익 = 당기순이익)"""
        seen, out = set(), []
        for r in rows:
            aid = r.get("account_id") or ""
            nm = re.sub(r"(분기|반기)(?=순|총포괄|연결순)", "당기", re.sub(r"\s|\(손실\)", "", r.get("account_nm") or ""))
            k = f"{r.get('sj_div')}:" + (_aid(aid) if aid.startswith(("ifrs", "dart_")) else f"nm:{nm}")
            base, n = k, 1
            while k in seen:
                n += 1
                k = f"{base}#{nm}" if n == 2 else f"{base}#{nm}#{n}"
            seen.add(k)
            out.append(k)
        return out
    tables = {}
    for sj in ("IS", "BS", "CF"):
        accts: List[dict] = []
        bykey: Dict[str, dict] = {}
        per: Dict[tuple, Dict[str, dict]] = {}
        # 줄 이름·순서는 가장 최근 사업보고서(연간) 먼저 → '분기순이익' 대신 '당기순이익'. 분기 보고서에만 있는 줄은 뒤에
        for yq in sorted(trep, key=lambda k: (k[1] == 4, k), reverse=True):
            rows = sorted((r for r in reps[yq]["rows"] if r.get("sj_div") in SJ[sj]),
                          key=lambda r: (SJ[sj].index(r.get("sj_div")), int(r.get("ord") or 0)))   # 손익계산서 다음 포괄손익계산서
            per[yq] = dict(zip(row_ids(rows), rows))
            for lk, r in per[yq].items():
                if lk not in bykey:
                    aid, nm = r["account_id"], r["account_nm"]
                    key = next((k[0] for k in KEYS if k[2] == sj and (_aid(aid) in map(_aid, k[3]) or nm in k[4])), None)
                    if key and any(a["key"] == key for a in accts):
                        key = None                         # 핵심 숫자 줄은 하나만
                    tagged = aid.startswith(("ifrs", "dart_")) and "#" not in lk
                    uid = aid if tagged and not any(a["id"] == aid for a in accts) else lk
                    bykey[lk] = {"id": uid, "name": nm, "key": key, "lk": lk}
                    accts.append(bykey[lk])
        acc = lambda a: f"{a['id']}|{a['name']}"
        val = lambda yq, a, f="thstrm_amount": _num(per[yq][a["lk"]].get(f)) if a["lk"] in per.get(yq, {}) else None
        cols = {"annual": {}, "quarter": {}}
        for (y, q) in sorted(trep):
            if q == 4:
                cols["annual"][f"{y}"] = {acc(a): val((y, 4), a) for a in accts}
            if sj == "IS" and q == 4:                     # 4분기 3개월 = 연간 − 3분기 누적 (계산)
                qv = {}
                for a in accts:
                    tot, cum = val((y, 4), a), val((y, 3), a, "thstrm_add_amount")
                    qv[acc(a)] = tot - cum if tot is not None and cum is not None else None
                cols["quarter"][f"{y}.12"] = qv
            else:
                cols["quarter"][f"{y}.{q * 3:02d}"] = {acc(a): val((y, q), a) for a in accts}
        # 주요 계정은 차트와 같은 값으로 (같은 보고서 값이라 다른 해 숫자를 가져오지 않는다)
        for a in accts:
            if not a["key"]:
                continue
            ak = acc(a)
            for p in annual:
                if p["period"] in cols["annual"] and p.get(a["key"]) is not None:
                    cols["annual"][p["period"]][ak] = p[a["key"]]
            for p in quarter:
                if p["period"] in cols["quarter"] and p.get(a["key"]) is not None:
                    cols["quarter"][p["period"]][ak] = p[a["key"]]
        missing = []
        if sj == "BS":                                # 앞 분기와 1조원 넘는 값이 원 단위까지 같으면 API 가 되풀이한 값으로 보고 비운다(_drop_repeats 와 같은 이유)
            ks = list(cols["quarter"])
            orig = {k: dict(cols["quarter"][k]) for k in ks}
            for a0, a1 in zip(ks, ks[1:]):
                same = [k for k, v in orig[a1].items() if v is not None and abs(v) >= 1e12 and v == orig[a0].get(k)]
                if len(same) < 2:                     # 자본조정처럼 원래 잘 안 바뀌는 계정 하나는 정상
                    continue
                for k in same:
                    cols["quarter"][a1][k] = None
                    missing.append([a1, k])
        if sj == "BS":                                # 원본(XBRL)에서 바로잡은 합계는 표에도 넣는다
            for p in quarter:
                for k in p.get("fixed", []):
                    ak = next((f"{a['id']}|{a['name']}" for a in accts if a["key"] == k), None)
                    if ak and p["period"] in cols["quarter"]:
                        cols["quarter"][p["period"]][ak] = p[k]
                        missing = [m for m in missing if m != [p["period"], ak]]
        prior, doc = [], []                              # 첫 해 앞 분기 (_fill_early): 손익·현금흐름 = 다음 해 보고서 전기 칸, 재무상태 = 그 분기 원문
        for (y, q), r in trep.items():
            if not r.get("early") or q == 4:
                continue
            pk = f"{y}.{q * 3:02d}"
            for a in accts:
                if cols["quarter"].get(pk, {}).get(acc(a)) is not None:
                    (doc if sj == "BS" else prior).append([pk, acc(a)])
        tables[sj] = {"accounts": accts, "annual": cols["annual"], "quarter": cols["quarter"], "missing": missing,
                      "prior": prior, "doc": doc}
    early = [{"period": f"{y}.{q * 3:02d}", "prior_from": r["early"]["prior_from"], "bs_doc": r["early"]["bs_doc"]}
             for (y, q), r in sorted(reps.items()) if r.get("early")]
    return {"fs": fs, "annual": annual, "quarter": quarter, "tables": tables, "early": early, "prior_bs": prior_bal,
            "sources": [{"period": f"{y}.{q * 3:02d}", "rcept_no": r["rcept_no"], "url": original_url(r["rcept_no"])} for (y, q), r in sorted(reps.items())]}


# ---------- 매출 구성 (2026-11-22): 사업보고서 '사업의 내용'에서 회사가 적은 제품·서비스별 매출 비중 표 ----------
# 회사마다 표 이름이 달라 차례로 찾는다. 보험사는 매출 대신 '보험종목별 수입보험료'. 표를 못 찾으면 None (지어내지 않는다)
MIX_ANCHORS = ["주요 제품 등의 현황", "주요 제품 및 서비스", "주요 제품등의 현황", "보험종목별 수입보험료 내역", "주요 상품 및 서비스", "매출실적", "매출 실적"]
_SHARE = re.compile(r"^%$|비\s*율|비\s*중|구성비")
_NAMECOL = re.compile(r"품\s*목|제\s*품|구\s*분|상\s*품|종\s*목|사업\s*부문")
_TOTAL = re.compile(r"^(합\s*계|소\s*계|계|총\s*계|합계)$")


# ---------- 옛 연간 재무 채우기 (2026-11-24, 사용자): 금융회사는 DART 재무 숫자 파일이 2023년분부터라
# 사업보고서 본문 'III. 재무에 관한 사항 - 요약재무정보' 표(연결, 3년치)에서 자산·부채·자본·순이익을 읽는다 ----------
def _lab(t: str) -> str:
    t = re.sub(r"\s|&cr;", "", t or "")
    t = re.sub(r"^[\[(]?([IVXⅠ-Ⅻ]+|\d+|[가-하])[.)\]]", "", t)       # 'Ⅰ.', 'Ⅺ.', '1.', '(1)', '가.' 같은 번호
    return t.strip("[]()")


def _owner(t: str) -> bool:
    """지배주주 몫 줄: '지배주주지분', '지배기업주주지분', '지배기업소유주지분', '지배기업의소유주(에게귀속되는자본)' 등. 비지배·포괄·주당 줄은 아니다"""
    return ("지배주주" in t or "지배기업" in t) and not t.startswith("비지배") and not re.search(r"포괄|주당|희석", t)


_NI = re.compile(r"(당기)?(연결)?총?(당기)?순(이익|손익)(\(손실\))?")   # '연결총당기순이익'(삼성바이오로직스)


def summary_fin(text: str, year: int) -> Dict[int, dict]:
    """요약재무정보 표 -> {연도: {assets, liab, equity, equity_owner, ni, ni_owner, op}} (원). 못 읽으면 {}.
    첫 재무상태 표(자산총계가 있는 표)와, 다음 재무상태 표가 나오기 전까지의 손익 줄만 쓴다 (뒤의 별도 기준 표와 섞지 않는다).
    재무상태와 손익이 한 표에 같이 있는 회사도 있다(삼성화재)"""
    from . import dart_pool as dp
    for m in re.finditer(r"요\s*약\s*(연\s*결\s*)?재\s*무\s*정\s*보", text):
        tbs = re.findall(r"<TABLE.*?</TABLE>", text[m.start(): m.start() + 60000], re.S)[:10]
        parsed = [(tb, dp._rows(tb)) for tb in tbs]
        is_bs = [any(x and _lab(x[0]) == "자산총계" for x in r) and any(x and _lab(x[0]) in ("부채총계", "자본총계") for x in r) for _, r in parsed]   # 자산만 있는 작은 표는 아니다
        if True not in is_bs:
            continue
        i0 = is_bs.index(True)
        group = [parsed[i0]] + [pr for pr, b in zip(parsed[i0 + 1:], is_bs[i0 + 1:]) if not b][:3]
        nxt = next((j for j in range(i0 + 1, len(parsed)) if is_bs[j]), len(parsed))
        group = [parsed[i0]] + parsed[i0 + 1:nxt][:3]
        out: Dict[int, dict] = {}
        unit0 = None
        for tb, rows in group:
            cols = _cols(rows)
            unit = _unit_before(text, text.find(tb, m.start())) or unit0     # 손익 표 앞에 단위가 없으면 재무상태 표의 단위
            unit0 = unit
            if not cols or not unit:
                continue
            yrs = cols if None not in cols and cols == sorted(cols, reverse=True) else [year - i for i in range(len(cols))]
            labs = [(_lab(x[0]) if x else "", x) for x in rows]
            first = lambda pred: next((x for t, x in labs if pred(t)), None)
            ni_i = next((i for i, (t, _) in enumerate(labs) if _NI.fullmatch(re.sub(r"\(.*", "", t))), None)   # 뒤에 붙은 '(대손준비금 …)' 설명은 뺀다
            pick = {"assets": first(lambda t: t == "자산총계"), "liab": first(lambda t: t == "부채총계"), "equity": first(lambda t: t == "자본총계"),
                    # 지배주주 자본: 자본 쪽(순이익 줄보다 앞)의 지배 몫 줄
                    "equity_owner": next((x for i, (t, x) in enumerate(labs) if _owner(t) and not re.search(r"이익|손익|손실", t)
                                          and (ni_i is None or i < ni_i)), None),
                    "ni": labs[ni_i][1] if ni_i is not None else None,
                    # 지배주주 순이익: 순이익 줄 뒤의 첫 지배 몫 줄 ('지배주주지분당기순이익', '당기순이익의 귀속 - 지배기업의 소유주')
                    "ni_owner": next((x for i, (t, x) in enumerate(labs) if ni_i is not None and i > ni_i and _owner(t)), None),
                    "op": first(lambda t: t in ("영업이익", "영업이익(손실)")),
                    "revenue": first(lambda t: t in ("매출액", "영업수익", "매출", "수익(매출액)", "매출액(영업수익)")),
                    "nci": next((x for i, (t, x) in enumerate(labs) if t.startswith("비지배") and not re.search(r"이익|손익|손실|포괄", t)
                                 and (ni_i is None or i < ni_i)), None)}
            for k, r in pick.items():
                for y, v in zip(yrs, _vals(r, len(cols)) if r else []):
                    if v is not None:
                        out.setdefault(y, {}).setdefault(k, v * unit)
        for v in out.values():                          # 지배주주 자본 줄이 없고 비지배지분만 있으면: 자본총계 − 비지배지분 (공시 값끼리 뺄셈, 계산 값 표시)
            nci = v.pop("nci", None)
            if v.get("equity_owner") is None and nci is not None and v.get("equity") is not None:
                v["equity_owner"] = v["equity"] - nci
                v["_derived"] = ["equity_owner"]
        if out:
            return out
    return {}


def fill_old_annual(dart: Dart, corp_code: str, st: dict, years: int = 10, today: Optional[date] = None) -> None:
    """연간 재무가 years 년보다 짧으면, 빈 해를 사업보고서 본문 요약재무정보로 채운다 (첫 표 = 연결, 자회사 없는 회사는 별도). 채운 해는 'body': True"""
    today = today or date.today()
    have = {int(p["period"]) for p in st.get("annual", [])}
    want = [y for y in range(today.year - years, today.year) if y not in have]
    if not want or not have:
        return
    first = min(have)
    got: Dict[int, dict] = {}
    # 검사 (2026-11-24): first 해 보고서의 본문 표에도 first 해 값이 있다 → 숫자 파일 값과 0.5% 안에서 같아야 본문 표를 믿고 쓴다
    a0 = next(p for p in st["annual"] if int(p["period"]) == first)
    check = None
    for f in reversed(dart.annual_filings(corp_code, first) or []):
        try:
            found = summary_fin(dart.document_text(f["rcept_no"]), first)
        except DartError:
            continue
        if found:
            v = found.get(first, {})
            pairs = [(k, v[k], a0[k]) for k in ("assets", "equity", "ni") if v.get(k) is not None and a0.get(k)]
            check = bool(pairs) and all(abs(x - z) <= abs(z) * 0.005 for _, x, z in pairs)
            for yy, vals in found.items():
                if yy in want and yy not in got:
                    got[yy] = {**vals, "rcept_no": f["rcept_no"]}
            break
    st["body_check"] = {"year": first, "ok": check}
    if check is False:                                    # 본문 표와 숫자 파일이 다르면 본문 표로 채우지 않는다 (빈칸으로 둔다)
        return
    y = first - 3
    while y >= today.year - years and any(w <= y for w in want):
        fl = dart.annual_filings(corp_code, y)
        for f in reversed(fl or []):
            try:
                text = dart.document_text(f["rcept_no"])
            except DartError:
                continue
            found = summary_fin(text, y)
            for yy, vals in found.items():
                if yy in want and yy not in got:
                    got[yy] = {**vals, "rcept_no": f["rcept_no"]}
            if found:                                     # 일부만 고친 정정 공시에는 표가 없을 수 있다 → 그 앞 공시
                break
        y -= 3                                           # 보고서 하나에 3년치
    for yy, vals in got.items():
        st["annual"].append({"period": str(yy), "end": f"{yy}-12-31", "rcept_no": vals.pop("rcept_no"), "derived": vals.pop("_derived", []), "body": True, **vals})
    st["annual"].sort(key=lambda p: p["period"])


# ---------- 옛 감가상각비 (EBITDA, 2026-11-24 사용자): 숫자 파일(XBRL) 주석은 2023년분부터라, 그 전 해는 공시 문서의 주석 표에서 읽는다 ----------
# 문서에는 '감가상각비' 줄이 있는 표가 많다(연구개발비 내역, 부문별, 별도 기준 …). 그래서 연결 고리로 고른다:
# 숫자 파일로 아는 해 Y 의 값과 '당기' 칸이 같은 표를 Y 년 문서에서 찾고, 그 표의 '전기' 칸이 Y-1 년 값. 다음엔 Y-1 년 문서에서
# 그 값과 '당기'가 같은 표를 찾는다. 모든 해가 두 문서에서 같은 값으로 확인된다. 맞는 표가 없으면 거기서 멈춘다(빈칸)
_DEP = re.compile(r"^(유형자산)?감가상각비$")
_ROU = re.compile(r"^사용권자산(감가)?상각비$")
_INV = re.compile(r"^투자부동산(감가)?상각비$")
_AMO = re.compile(r"^무형자산(상각비)?$|^무형자산상각비$")
_DA = re.compile(r"^감가상각비(와|및)(무형자산)?상각비$|^감가상각비및무형자산상각비$")


def _da_candidates(text: str) -> List[tuple]:
    """문서의 표마다 (조합 이름, 당기, 전기) 감가상각비 후보. 당기·전기가 한 표의 두 칸이면 그대로,
    한 칸짜리 표면(삼성전자: 당기 표 다음에 전기 표) 바로 뒤의 같은 조합 한 칸짜리 표를 전기로 짝짓는다"""
    from . import dart_pool as dp
    combos = {"da": ("da",), "dep+amo": ("dep", "amo"), "dep+rou+amo": ("dep", "rou", "amo"), "dep+rou+inv+amo": ("dep", "rou", "inv", "amo")}
    tables = []
    for tb in re.findall(r"<TABLE.*?</TABLE>", text, re.S):
        if "상각" not in tb:
            continue
        rows = [r for r in dp._rows(tb) if r]
        got: Dict[str, List[float]] = {}
        for r in rows:
            t = re.sub(r"\(.*", "", _lab(r[0]))
            for key, rx in (("dep", _DEP), ("rou", _ROU), ("inv", _INV), ("amo", _AMO), ("da", _DA)):
                if key not in got and rx.match(t):
                    two = _vals(r, 2)
                    one = _vals(r, 1)
                    if two[0] is not None and two[1] is not None:
                        got[key] = [abs(two[0]), abs(two[1])]
                    elif one[0] is not None:
                        got[key] = [abs(one[0])]
        if not got:
            continue
        unit = _unit_before(text, text.find(tb)) or 1
        for name, keys in combos.items():
            if all(k in got for k in keys):
                n = min(len(got[k]) for k in keys)
                tables.append([name, sum(got[k][0] for k in keys) * unit, sum(got[k][1] for k in keys) * unit if n == 2 else None])
    out = []
    for i, (name, cur, prev) in enumerate(tables):
        if prev is None:                                  # 한 칸짜리: 뒤에 오는 같은 조합 한 칸짜리 표가 전기
            nxt = next((t for t in tables[i + 1:i + 6] if t[0] == name and t[2] is None), None)
            prev = nxt[1] if nxt else None
        if prev is not None:
            out.append((name, cur, prev))
    return out


def fill_old_da(dart: Dart, corp_code: str, st: dict, years: int = 10, today: Optional[date] = None) -> None:
    """숫자 파일로 감가상각비를 아는 가장 이른 해부터 거꾸로, 공시 문서의 주석 표로 옛 해 감가상각비를 채운다 ('da_doc').
    Y 년 문서의 '전기' 값이 Y-1 년 문서의 '당기' 값과 같을 때만(두 문서가 같은 값) Y-1 년에 넣는다"""
    today = today or date.today()
    ann = {int(a["period"]): a for a in st.get("annual", [])}
    known = sorted(y for y, a in ann.items() if a.get("da") is not None)
    if not known:
        return

    def find(y: int, target: float, combo: Optional[str]):
        for f in reversed(dart.annual_filings(corp_code, y) or []):
            try:
                text = dart.document_text(f["rcept_no"])
            except DartError:
                continue
            c = [x for x in _da_candidates(text) if abs(x[1] - target) <= abs(target) * 0.005 and (combo is None or x[0] == combo)]
            if c:
                return c[0], f["rcept_no"]
        return None

    y = known[0]
    hit = find(y, ann[y]["da"], None)
    while hit and y - 1 > today.year - years - 1:
        (combo, _, prev), _ = hit
        a = ann.get(y - 1)
        if a is None or prev <= 0:
            return
        nxt = find(y - 1, prev, combo)                     # Y-1 년 문서에서도 같은 값이어야
        if not nxt:
            return
        if a.get("da") is None:
            a["da"], a["da_basis"], a["da_doc"] = prev, f"공시 문서 주석 표 ({_COMBO_KO.get(combo, combo)})", nxt[1]
        y, hit = y - 1, nxt


_COMBO_KO = {"da": "감가상각비와 상각비", "dep+amo": "감가상각비 + 무형자산상각비", "dep+rou+amo": "감가상각비 + 사용권자산상각비 + 무형자산상각비",
             "dep+rou+inv+amo": "유형·사용권·투자부동산 감가상각비 + 무형자산상각비"}


# 부문별 매출 (2026-11-22): 모든 상장사가 같은 형식으로 내는 재무제표 원본(XBRL) 주석 '영업부문'의 매출액.
# 회사마다 표 모양이 달라도 항목 이름표(Revenue)와 축(SegmentsAxis·ProductsAndServicesAxis)은 같다
SEG_AXES = ("SegmentsAxis", "SegmentConsolidationItemsAxis", "ProductsAndServicesAxis")   # 부문을 둘째 축에 적는 회사도 있다(LG화학)
SEG_NAMES = ("Revenue", "RevenueFromContractsWithCustomers")
SEG_PROFIT = ("ProfitLossFromOperatingActivities", "OperatingIncomeLoss")   # 매출액 계정이 없는 금융회사: 부문별 영업이익 (모두 흑자일 때만). 하나금융은 뒤쪽 이름
_COMPANY = re.compile(r"\(주\)|㈜|주식회사|\b(Inc|Ltd|LLC|Co|Corp|GmbH|B\.V|S\.A|SAS|Pvt|Limited|LIMITED|LTD|INC|Ltda|BHD|NANJING|LLP)\b", re.I)   # 자회사 목록 표는 부문이 아니다
SEG_SKIP = {"OperatingSegmentsMember", "ReportableSegmentsMember", "SegmentsMember"}
_SEG_DROP = re.compile(r"Elimination|Reconcil|Adjust|Unallocated|Total|Aggregat|Corporate(?!Banking|Financ)", re.I)   # KB금융 기업금융(CorporateBanking)은 부문
_SEG_DROP_KO = re.compile(r"제거|조정|합\s*계|소\s*계|총\s*계|내부\s*거래|미배분|전사|연결실체")


def _strip_label(s: str) -> str:
    return re.sub(r"\s*\[[^\]]*\]\s*$", "", s or "").strip()


def segment_groups(res: dict, names: tuple = SEG_NAMES) -> Optional[dict]:
    """XBRL 숫자 -> 가장 알맞은 부문별 매출 한 묶음 {'items', 'title', 'year', 'axis'} 또는 None.
    고르는 순서: 연결 > 별도, 한 항목이 90% 넘지 않는 묶음, 사업부문 > 제품, 항목 많은 것.
    내부거래 제거·조정·합계 줄은 뺀다. 항목 합이 전체 매출의 0.9~2배(부문 간 거래 포함)가 아니면 쓰지 않는다"""
    labels = res.get("labels") or {}

    def lab(v: str) -> str:
        t = html.unescape(_strip_label(labels.get(v.replace("Member", "") + "Member", labels.get(v, v))))
        t = re.sub(r",\s*(영업|보고)부문$", "", t)
        return "은행 기타" if t == "기타" and "OfBankingBusiness" in v else t   # KB금융 은행 안의 '기타'를 회사 전체 '기타부문'과 구별

    facts = [f for f in res.get("facts", []) if f.get("name") in names and f.get("start") and f.get("end")]
    facts = [f for f in facts if (date.fromisoformat(f["end"][:10]) - date.fromisoformat(f["start"][:10])).days > 300]
    if not facts:
        return None
    end = max(f["end"] for f in facts)
    facts = [f for f in facts if f["end"] == end]
    FS = "ConsolidatedAndSeparateFinancialStatementsAxis"
    total = {}
    for f in facts:
        if set(f["dims"]) <= {FS}:
            total.setdefault((f["name"], f["dims"].get(FS, "")), f["value"])
    groups: Dict[tuple, Dict[str, float]] = {}
    for f in facts:
        for ax in SEG_AXES:
            if ax in f["dims"]:
                fixed = tuple(sorted((k, v) for k, v in f["dims"].items() if k != ax))
                groups.setdefault((ax, fixed, f["name"]), {})[f["dims"][ax]] = f["value"]
    best = None
    for (ax, fixed, name), mem in groups.items():
        fx = dict(fixed)
        if "GeographicalAreasAxis" in fx or any(_SEG_DROP.search(v) or _SEG_DROP_KO.search(lab(v)) for k, v in fixed if k != FS):
            continue
        keep = {m: v for m, v in mem.items()       # 'BankSegmentsMemberOfEntitysTotal...'(하나금융)처럼 뒤에 붙은 표 이름은 보지 않는다
                if m not in SEG_SKIP and not _SEG_DROP.search(m.split("MemberOf")[0]) and "Disaggregat" not in m   # 매출 세분 표는 부문 표와 겹친다(POSCO홀딩스)
                and not _SEG_DROP_KO.search(lab(m))}
        if names is SEG_PROFIT and any(v is None or v <= 0 for v in keep.values()):
            continue
        items = [(lab(m), v) for m, v in keep.items() if v and v > 0]
        if len(items) < 2 or len({n for n, _ in items}) < len(items) or sum(bool(_COMPANY.search(n)) for n, _ in items) * 2 > len(items):
            continue
        sm = sum(v for _, v in items)
        fs = fx.get(FS, "")
        tot = total.get((name, fs)) or next((total[(n, fs)] for n in names if (n, fs) in total), None)
        if tot and not (0.9 * tot <= sm <= 2.0 * tot):
            continue
        top = max(v for _, v in items) / sm
        outside = not tot or abs(sm / tot - 1) > 0.03          # 같은 부문이라도 외부 매출 묶음(합 = 전체 매출)을 부문 간 거래 포함 묶음보다 먼저 (POSCO홀딩스)
        score = (0 if "Consolidated" in fx.get(FS, "Consolidated") else 1, top >= 0.9, 1 if ax == "ProductsAndServicesAxis" else 0, outside, -len(items))
        if best is None or score < best[0]:
            best = (score, ax, items, sm, tot, fx.get(FS, ""))
    if not best:
        return None
    _, ax, items, sm, tot, fs = best
    return {"items": [{"name": n, "amount": v, "share": round(v / sm * 100, 2)} for n, v in items],
            "title": "부문별 영업이익" if names is SEG_PROFIT else "제품별 매출" if ax == "ProductsAndServicesAxis" else "부문별 매출",
            "year": int(end[:4]), "axis": ax, **_reconcile(sm, tot, ("별도 " if "Separate" in fs else "연결 ") + ("영업이익" if names is SEG_PROFIT else "매출"))}


def _reconcile(sm: float, tot: Optional[float], label: str) -> dict:
    """손익계산서 합계와 맞추는 줄: 조정 = 손익계산서 값 − 항목 합 (부문 간 거래 제거 등). 0.1% 안이면 조정 없음"""
    if not tot:
        return {}
    adj = tot - sm
    return {"adjust": adj if abs(adj) > abs(tot) * 0.001 else None, "total": tot, "total_label": label}



def region_groups(res: dict) -> Optional[dict]:
    """사업부문이 하나뿐인 회사(기아 등): 영업부문 주석의 지역별 매출. 지역마다 부문 합계 + 내부거래 제거를 더한 외부 매출.
    지역 합이 전체 매출의 0.97~1.03배일 때만 (겹치는 지역 묶음 방지)"""
    labels = res.get("labels") or {}
    FS, SC, GA = "ConsolidatedAndSeparateFinancialStatementsAxis", "SegmentConsolidationItemsAxis", "GeographicalAreasAxis"
    facts = [f for f in res.get("facts", []) if f.get("name") in SEG_NAMES and f.get("start") and f.get("end")
             and (date.fromisoformat(f["end"][:10]) - date.fromisoformat(f["start"][:10])).days > 300
             and f["dims"].get(FS, "ConsolidatedMember") == "ConsolidatedMember"]
    if not facts:
        return None
    end = max(f["end"] for f in facts)
    facts = [f for f in facts if f["end"] == end]
    total = next((f["value"] for f in facts if set(f["dims"]) <= {FS}), None)
    geo = [f for f in facts if GA in f["dims"] and set(f["dims"]) <= {FS, SC, GA}]
    plain = [f for f in geo if SC not in f["dims"]]
    use = plain or [f for f in geo if f["dims"].get(SC) in ("OperatingSegmentsMember", "EliminationOfIntersegmentAmountsMember")]
    net: Dict[str, float] = {}
    for f in use:
        net[f["dims"][GA]] = net.get(f["dims"][GA], 0) + f["value"]
    items = [("국내" if m == "CountryOfDomicileMember" else html.unescape(_strip_label(labels.get(m, m))), v)
             for m, v in net.items() if v > 0 and not _SEG_DROP.search(m)]
    sm = sum(v for _, v in items)
    if len(items) < 2 or not total or not (0.97 * total <= sm <= 1.03 * total) or len({n for n, _ in items}) < len(items):
        return None
    return {"items": [{"name": n, "amount": v, "share": round(v / sm * 100, 2)} for n, v in items],
            "title": "지역별 매출", "year": int(end[:4]), "axis": GA, **_reconcile(sm, total, "연결 매출")}

def segment_mix(dart: Dart, corp_code: str, today: Optional[date] = None) -> Optional[dict]:
    today = today or date.today()
    for y in (today.year - 1, today.year - 2):
        fl = dart.annual_filings(corp_code, y)
        if not fl:
            continue
        for f in reversed(fl):                        # 정정 공시에 원본 파일이 없으면 그 앞 공시
            rno = f["rcept_no"]
            try:
                res = dart.xbrl_facts(rno, "11011")
            except DartError:
                continue
            if not res.get("facts"):
                continue
            mix = segment_groups(res) or segment_groups(res, SEG_PROFIT)
            if not mix or (mix["axis"] == "ProductsAndServicesAxis" and max(i["share"] for i in mix["items"]) >= 90):   # 기아 '재화의 판매' 95%보다 지역별이 낫다
                mix = region_groups(res) or mix
            if mix:
                mix.update(rcept_no=rno, table="재무제표 주석 영업부문 (XBRL)")
            return mix
        return None
    return None


# 보험사 영업수익 (2026-11-22): 손익계산서의 보험서비스수익 + 투자(영업)수익. 투자수익은 세부 계정으로 나누고,
# 이름 붙은 계정에 안 든 나머지는 '기타 투자수익' = 투자수익 합계 − 세부 계정 합
INS_REV = ("dart_OperatingIncomeInsurance",)                      # 보험서비스수익(보험영업수익)
INV_REV = ("ifrs-full_InvestmentIncome",)                          # 투자서비스수익(투자영업수익)
INV_PARTS = [("이자수익", re.compile(r"^ifrs-full_RevenueFromInterest$")),
             ("배당금수익", re.compile(r"^ifrs-full_RevenueFromDividends$")),
             ("금융상품·파생 관련 이익", re.compile(r"^dart_Gain(From|Arising)")),
             ("외환거래이익", re.compile(r"^ifrs-full_ForeignExchangeGain$")),
             ("수수료수익", re.compile(r"^ifrs-full_FeeAndCommissionIncome$")),
             ("보험금융수익", re.compile(r"^dart_(InsuranceFinanceIncome|FinanceIncomeFromReinsurance)")),
             ("임대료수익", re.compile(r"^ifrs-full_RentalIncome$"))]


def income_mix(dart: Dart, corp_code: str, today: Optional[date] = None) -> Optional[dict]:
    today = today or date.today()
    for y in (today.year - 1, today.year - 2):
        for fs in ("CFS", "OFS"):
            try:
                lst = dart.statements(corp_code, y, fs).get("list", [])
            except DartError:
                return None
            acc = {}
            for a in lst:
                if a.get("sj_div") in ("IS", "CIS") and a["account_id"] not in acc:
                    acc[a["account_id"]] = (a["account_nm"], _num(a.get("thstrm_amount")), a.get("rcept_no"))
            ins = next((acc[k] for k in INS_REV if k in acc and acc[k][1]), None)
            inv = next((acc[k] for k in INV_REV if k in acc and acc[k][1]), None)
            if not ins or not inv:
                continue
            items = [{"name": ins[0], "amount": ins[1]}]
            parts: Dict[str, float] = {}
            for k, (_, v, _) in acc.items():
                for name, rx in INV_PARTS:
                    if v and v > 0 and rx.search(k):
                        parts[name] = parts.get(name, 0) + v
            rest = inv[1] - sum(parts.values())
            if rest < -abs(inv[1]) * 0.001:                    # 세부 계정이 합계보다 크면 나누지 않는다
                items.append({"name": inv[0], "amount": inv[1]})
            else:
                items += [{"name": n, "amount": v} for n, v in parts.items()]
                if rest > abs(inv[1]) * 0.001:
                    items.append({"name": "기타 투자수익", "amount": rest})
            tot = ins[1] + inv[1]
            for i in items:
                i["share"] = round(i["amount"] / tot * 100, 2)
            prof = [(n, acc[k][1]) for n, k in (("보험손익", "ifrs-full_InsuranceServiceResult"), ("투자손익", "dart_InvestmentIncomeExpenses"))
                    if k in acc and acc[k][1] is not None]
            op = acc.get("ifrs-full_ProfitLossFromOperatingActivities", (None, None))[1]
            return {"items": items, "title": "영업수익 구성", "year": y, "rcept_no": ins[2] or "",
                    "table": "손익계산서", "adjust": None, "total": tot, "total_label": ("연결" if fs == "CFS" else "별도") + " 영업수익",
                    "profit": {"items": [{"name": n, "amount": v} for n, v in prof], "total": op} if len(prof) == 2 else None}
    return None


def sales_mix(dart: Dart, corp_code: str, today: Optional[date] = None, insurer: bool = False) -> Optional[dict]:
    """보험사는 손익계산서 영업수익 구성. 그 밖은 부문별 매출이 먼저, 없거나 한 항목이 95% 넘으면 사업보고서의 제품별 비중 표"""
    if insurer:
        mix = income_mix(dart, corp_code, today)
        if mix:
            return mix
    seg = segment_mix(dart, corp_code, today)
    if seg and max(i["share"] for i in seg["items"]) < 95:
        return seg
    return _table_mix(dart, corp_code, today) or seg


def _table_mix(dart: Dart, corp_code: str, today: Optional[date] = None) -> Optional[dict]:
    from . import dart_pool as dp
    today = today or date.today()
    for y in (today.year - 1, today.year - 2):
        fl = dart.annual_filings(corp_code, y)
        if not fl:
            continue
        rno = fl[-1]["rcept_no"]
        try:
            text = dart.document_text(rno)
        except DartError:
            return None
        for anchor in MIX_ANCHORS:
            k = text.find(anchor)
            if k < 0:
                continue
            for tb in re.findall(r"<TABLE.*?</TABLE>", text[k:k + 30000], re.S)[:4]:
                rows = dp._rows(tb)
                head = next((r for r in rows if any(_SHARE.search(c) for c in r)), None)
                top = rows[0] if rows else []
                if not head or not any(re.search(r"매\s*출|금\s*액|수입보험료", c) for c in top + head):
                    continue
                name_i = max((i for i, c in enumerate(top) if _NAMECOL.search(c)), default=None)
                items = []
                for r in rows:
                    nums = [(j, dp._cell_value(c)) for j, c in enumerate(r)]
                    nums = [(j, v) for j, v in nums if v is not None]
                    if len(nums) < 2:
                        continue
                    first = nums[0][0]
                    if name_i is not None:
                        ni = name_i - (len(top) - len(r)) if len(r) != len(top) else name_i
                        name = r[ni] if 0 <= ni < first else (r[first - 1] if first else "")
                    else:
                        name = r[first - 1] if first else ""
                    name = re.sub(r"(?<=[가-힣]) (?=[가-힣])", "", name).strip()
                    if not name or _TOTAL.match(name) or re.match(r"^제\s*\d+\s*기", name):
                        continue
                    amount, share = nums[0][1], nums[1][1]
                    if share is None or not (0 <= share <= 100):
                        continue
                    items.append({"name": name, "amount": amount, "share": share})
                if len(items) >= 2 and 90 <= sum(i["share"] for i in items) <= 110:
                    period = next((c for c in top + head if re.search(r"FY\s*20\d\d|20\d\d\s*년", c)), "")
                    m = re.search(r"(20\d\d)", period)
                    return {"items": items, "title": "수입보험료 구성" if "보험료" in anchor else "매출 구성",
                            "year": int(m.group(1)) if m else y, "rcept_no": rno, "table": anchor}
        return None
    return None


# ---------- 공시 본문 사업 지표 (2026-11-24): 연구개발비(제조업), 수주잔고(건설·조선·방산). AI 를 쓰지 않는다 ----------
# 사업보고서 'II. 사업의 내용'의 표를 그대로 읽는다. 표 모양은 회사마다 달라도 줄 이름(연구개발비용 계, 합계)은 같다
_UNIT = {"원": 1, "천원": 1e3, "백만원": 1e6, "억원": 1e8, "십억원": 1e9, "조원": 1e12}
_RND_ROW = re.compile(r"연구\s*개발\s*비용?\s*(\(.*?\))?\s*(총\s*계|합\s*계|계)")
_RND_RATIO = re.compile(r"연구\s*개발\s*비\s*/\s*매출액|매출액\s*대비")
_BAL = re.compile(r"수\s*주\s*잔\s*(고|액)|계\s*약\s*잔\s*(액|고)")
_SUM_ROW = re.compile(r"^(국내\s*/?\s*해외\s*)?(총\s*)?(합\s*계|총\s*계|계)$")   # 한화에어로스페이스 2022 는 '계'


def _unit_before(text: str, pos: int) -> Optional[float]:
    m = re.findall(r"단위\s*[:：]\s*([^)\s,]+)", re.sub(r"<[^>]+>", " ", text[max(0, pos - 400):pos]))
    return _UNIT.get(m[-1]) if m else None             # 달러(억불·USD) 등 원화가 아니면 쓰지 않는다


def _cols(rows: List[List[str]]) -> List[Optional[int]]:
    """머리 줄의 연도 칸 -> [연도 또는 None] (왼쪽부터 = 최근 해부터)"""
    for r in rows[:3]:
        hits = [c for i, c in enumerate(r) if re.search(r"제\s*\d+\s*(\(\S{1,3}\))?\s*기|20\d\d\s*년|20\d\d\.\d|FY\s*20\d\d", c) and (i == 0 or c != r[i - 1])]   # 합쳐진 칸 되풀이
        if len(hits) >= 2:                               # '2019년', '2019.12.31', '1.1~2019.12.31'(기업은행) 모두 연도로
            return [int(m.group(1) or m.group(2)) if (m := re.search(r"FY\s*(20\d\d)|(20\d\d)(?=\s*년|\.\d)", c)) else None for c in hits]
    return []


def _vals(r: Optional[List[str]], n: int) -> List[Optional[float]]:
    """줄의 숫자 칸('-' 포함)을 왼쪽부터 n개. 이름 칸 수나 끝의 빈 '비고' 칸이 줄마다 달라도 숫자 칸 순서는 같다"""
    from . import dart_pool as dp
    if not r:
        return [None] * n
    cells = [c.strip() for c in r]
    num = [c for c in cells if c in ("-", "－") or dp._cell_value(c.replace("%", "")) is not None]
    vals = [None if c in ("-", "－") else dp._cell_value(c.replace("%", "")) for c in num]
    return vals[:n] if len(vals) >= n else [None] * n


def rnd_table(text: str, year: int) -> List[dict]:
    """'연구개발 활동' 표 -> [{'year', 'amount'(원), 'ratio'(%)}]. 머리 줄의 연도 칸에 맞춰 읽는다. 비율은 공시에 적힌 값 그대로"""
    from . import dart_pool as dp
    for m in re.finditer(r"연\s*구\s*개\s*발\s*비\s*용", text):
        for tb in re.findall(r"<TABLE.*?</TABLE>", text[m.start(): m.start() + 20000], re.S)[:2]:
            rows = dp._rows(tb)
            cols = _cols(rows)
            amt = next((r for r in rows if any(_RND_ROW.match(c.strip()) for c in r[:2])), None)
            rat = next((r for r in rows if any(_RND_RATIO.search(c) for c in r[:2])), None)
            unit = _unit_before(text, text.find(tb, m.start()))
            if not cols or not amt or not unit:
                continue
            yrs = cols if None not in cols and cols == sorted(cols, reverse=True) else [year - i for i in range(len(cols))]
            out = [{"year": y, "amount": a * unit, "ratio": p if p is not None and 0 <= p < 100 else None}
                   for y, a, p in zip(yrs, _vals(amt, len(cols)), _vals(rat, len(cols))) if a and a > 0]
            if out:
                return out
    return []


def backlog_table(text: str, year: int) -> Optional[dict]:
    """'수주상황' -> {'year', 'amount'(원), 'basis'}. '연결기준 계약잔액은 …' 문장이 있으면 그 값,
    없으면 수주 표의 합계 줄 마지막 칸(수주잔고). 합계가 위 항목들의 합과 1% 넘게 다르면(공시 오타) 쓰지 않는다.
    회사별로 표가 나뉜 경우(1) 본사 2) 자회사 …)에 연결 문장이 없으면 쓰지 않는다 (본사만의 값과 섞이지 않게)"""
    for m in re.finditer(r"수\s*주\s*상\s*황", text):           # '수주상황을 참조' 같은 앞쪽 언급은 건너뛴다
        got = _backlog_at(text, m.start(), year)
        if got is not None:
            return got or None
    return None


def _backlog_at(text: str, k: int, year: int):
    """None: 이 자리에 수주 표 없음(다음 자리로), {}: 표는 있으나 못 씀, dict: 값"""
    from . import dart_pool as dp
    plain = html.unescape(re.sub(r"<[^>]+>", " ", text[k:k + 200000]))
    sm = re.search(r"연결\s*기준\s*(수주|계약)\s*잔(액|고)[은는이가]?\s*([\d,]+)\s*(백만원|억원|천원|원)", plain)
    if sm:
        return {"year": year, "amount": float(sm.group(3).replace(",", "")) * _UNIT[sm.group(4)], "basis": "연결 (공시 문장)"}
    for tb in re.findall(r"<TABLE.*?</TABLE>", text[k:k + 60000], re.S)[:4]:
        rows = dp._rows(tb)
        if not any(_BAL.search(" ".join(r)) for r in rows[:3]):
            continue
        before = html.unescape(re.sub(r"<[^>]+>", " ", text[max(k, text.find(tb, k) - 400): text.find(tb, k)]))
        if re.search(r"1\)\s*\S", before):
            return {}
        unit = _unit_before(text, text.find(tb, k))
        last = lambda r: next((v for v in (dp._cell_value(c) for c in reversed(r[1:])) if v is not None), None)
        tot = [r for r in rows if r and _SUM_ROW.match(r[0].strip())]
        data = [r for r in rows[1:] if r and not _SUM_ROW.match(r[0].strip()) and last(r) is not None]
        if tot:
            v = last(tot[-1])
            parts = [last(r) for r in data]
            if v and parts and not 0.99 * v <= sum(parts) <= 1.01 * v and not any(abs(p - v) < 1 for p in parts):
                return {}
        else:
            v = last(data[0]) if len(data) == 1 else None
        if not unit or not v or v <= 0:
            return {}
        return {"year": year, "amount": v * unit, "basis": "공시 표 합계"}
    return None


def largest_holder(dart: Dart, corp_code: str, today: Optional[date] = None) -> Optional[dict]:
    """최대주주 (2026-11-24, 사용자): 최근 사업보고서 '최대주주 및 특수관계인의 주식소유 현황'. 이름·보통주 지분율·특수관계인 포함 지분율(공시 값)"""
    today = today or date.today()
    for y in (today.year - 1, today.year - 2):
        try:
            res = dart.get("hyslrSttus.json", {"corp_code": corp_code, "bsns_year": str(y), "reprt_code": "11011"})
        except DartError:
            continue
        rows = res.get("list") or []
        if not rows:
            continue
        common = lambda r: "보통" in (r.get("stock_knd") or "")
        me = next((r for r in rows if r.get("nm") != "계" and "최대주주" in (r.get("relate") or "") and "특수" not in (r.get("relate") or "") and common(r)), None) \
            or next((r for r in rows if r.get("nm") != "계" and common(r)), None)
        if not me:
            continue
        tot = next((r for r in rows if r.get("nm") == "계" and common(r)), None) or next((r for r in rows if r.get("nm") == "계" and "합계" in (r.get("stock_knd") or "")), None)
        return {"name": re.sub(r"\s+", " ", me["nm"]).strip(), "share": _num(me.get("trmend_posesn_stock_qota_rt")),
                "group": _num(tot.get("trmend_posesn_stock_qota_rt")) if tot else None, "year": y, "rcept_no": me.get("rcept_no", "")}
    return None


def _safe_valuation(krx: Krx, ticker: str, st: dict, dart: Optional[Dart] = None, corp_code: str = "") -> List[dict]:
    try:
        sh: Dict[str, float] = {}
        if dart and corp_code:
            qs = {p["period"] for p in st.get("quarter", [])}
            periods = sorted(qs | {f"{a['period']}.12" for a in st.get("annual", [])})
            try:
                sh = float_history(dart, corp_code, periods)
            except Exception:                          # noqa: BLE001 (못 받으면 시가총액 기준)
                sh = {}
        return valuation(krx, ticker, st, sh)
    except Exception:                                  # noqa: BLE001 (참고 추이라 실패해도 나머지는 그대로)
        return []


def _safe_holder(dart: Dart, corp_code: str, today: Optional[date] = None) -> Optional[dict]:
    try:
        return largest_holder(dart, corp_code, today)
    except Exception:                                  # noqa: BLE001 (참고 정보)
        return None


def _safe_business(dart: Dart, corp_code: str, today: Optional[date] = None) -> Optional[dict]:
    try:
        return business_metrics(dart, corp_code, today)
    except (DartError, ValueError, IndexError):
        return None


def business_metrics(dart: Dart, corp_code: str, today: Optional[date] = None, reports: int = 5) -> Optional[dict]:
    """최근 사업보고서 몇 개에서 연구개발비·수주잔고 추이. 둘 다 없으면 None"""
    today = today or date.today()
    rnd: Dict[int, dict] = {}
    bl: Dict[int, dict] = {}
    seen = 0
    for y in range(today.year - 1, today.year - 6, -1):
        if seen >= reports:
            break
        fl = dart.annual_filings(corp_code, y)
        if not fl:
            continue
        text = ""
        for f in reversed(fl):                        # 정정 공시에 원본 파일이 없으면 그 앞 공시
            try:
                text = dart.document_text(f["rcept_no"])
                rno = f["rcept_no"]
                break
            except DartError:
                continue
        if not text:
            continue
        seen += 1
        for r in rnd_table(text, y):
            rnd.setdefault(r["year"], {**r, "rcept_no": rno})      # 최근 보고서 값 먼저 (정정·재작성 반영)
        b = backlog_table(text, y)
        if b:
            bl.setdefault(y, {**b, "rcept_no": rno})
        if seen == 1 and not rnd and not bl:               # 최근 보고서에 둘 다 없으면 더 찾지 않는다
            return None
    if not rnd and not bl:
        return None
    return {"rnd": [rnd[k] for k in sorted(rnd)], "backlog": [bl[k] for k in sorted(bl)], "source": "사업보고서 II. 사업의 내용 (연구개발 활동, 수주상황)"}


INSURANCE = ("651", "652")                           # 표준산업분류: 보험업(생명·손해), 재보험


DART_KICS = False
# CSM 합계와 같은 뜻으로 보는 덧붙은 꼬리표 (손보사가 붙여 낸다, 2026-11-25 삼성화재·DB손보 원본 확인)
CSM_TOTAL_DIMS = {"InsuranceContractsByComponentsAxis": "ContractualServiceMarginMember",
                  "InsuranceContractsAxis": "InsuranceContractsOtherThanThoseToWhichPremiumAllocationApproachHasBeenAppliedMember",
                  "TypesOfContractsAxis": "LongtermInsuranceMember"}


def insurance(dart: Dart, corp_code: str, quarter: List[dict], name: str = "") -> List[dict]:
    """보험사 지표: CSM 잔액·신계약 CSM·CSM 상각액(XBRL 주석), 지급여력비율 K-ICS(공시 본문 표). 분기마다.
    신계약·상각은 3개월 값이고 12월은 연간 − 3분기 누적. 값이 없으면 None"""
    from . import dart_check as dc
    out, cum, kics, prior_end, prior_q = [], {}, {}, {}, {}
    for p in quarter:
        y, m = int(p["period"][:4]), int(p["period"][5:7])
        q = m // 3
        row = {"period": p["period"], "end": p["end"], "rcept_no": p["rcept_no"], "derived": []}
        try:
            facts = dart.xbrl_facts(p["rcept_no"], REPRT[q]).get("facts", [])
        except DartError:
            facts = []
        base = {"ConsolidatedAndSeparateFinancialStatementsAxis": "SeparateMember",       # 별도: 금감원 FISIS 와 같은 기준
                "DisaggregationOfInsuranceContractsAxis": "InsuranceContractsIssuedMember"}

        def pick(acc, start, end=None):
            """꼬리표가 정확히 '별도 + 발행한 보험계약'인 값 먼저. 없으면 합계와 같은 뜻의 꼬리표가 더 붙은 값 (손보사, 2026-11-25):
            '구성요소: CSM', '보험료배분접근법 아닌 계약'(CSM 은 그 계약에만 있다), '장기보험'(손보 CSM 은 거의 장기보험). 덜 붙은 값을 먼저"""
            tag, kind, extra = dc.XBRL[acc]
            want = {**base, **extra}
            end = end or p["end"]
            best, best_n = None, 99
            for f in facts:
                if f["name"] != tag or not ((kind == "stock" and f.get("instant") == end) or (kind == "flow" and f.get("start") == start and f.get("end") == end)):
                    continue
                d = f["dims"]
                if any(d.get(k) != v for k, v in want.items()):
                    continue
                more = {k: v for k, v in d.items() if k not in want}
                if all(CSM_TOTAL_DIMS.get(k) == v for k, v in more.items()) and len(more) < best_n:
                    best, best_n = f["value"], len(more)
            return best
        row["csm"] = pick("CSM 잔액", None)
        if y - 1 not in prior_end:                    # 이 보고서의 지난 연말 CSM (그 연말 보고서에 주석이 없을 때 쓴다)
            v = pick("CSM 잔액", None, f"{y - 1}-12-31")
            if v is not None:
                prior_end[y - 1] = (v, p["rcept_no"])
        if q in (1, 2, 3) and p.get("end"):            # 작년 같은 분기 말 CSM: 변동 주석의 '전기' 칸 기말 (2026-11-26 사용자, 2025.03 등 주석 없던 분기)
            v = pick("CSM 잔액", None, f"{y - 1}{p['end'][4:]}")
            if v is not None:
                prior_q.setdefault((y - 1, q), (v, p["rcept_no"]))
        for key, acc in (("csm_new", "신계약 CSM"), ("csm_amort", "CSM 상각액")):
            ytd = pick(acc, f"{y}-01-01")
            if q in (1, 2, 3):
                v = pick(acc, f"{y}-{q * 3 - 2:02d}-01") if q > 1 else ytd
                if v is None and q > 1 and ytd is not None and cum.get((y, q - 1, key)) is not None:
                    v = ytd - cum[(y, q - 1, key)]     # 3개월 값이 없고 연초 누적만 있으면 앞 분기 누적을 뺀다
                    row["derived"].append(key)
                cum[(y, q, key)] = ytd
            else:
                prev = cum.get((y, 3, key))
                v = ytd - prev if ytd is not None and prev is not None else None
                row["derived"].append(key)
            row[key] = v
        t = None
        if DART_KICS:                                 # 2026-11-25 사용자: DART 본문 표 K-ICS 는 뺀다 (K-ICS 는 금감원 값만). 원문도 받지 않는다
            try:
                t = dc.kics_table(dart.document_text(p["rcept_no"]))
            except DartError:
                t = None
        if t and len(t["ratio"]) == len(t["cols"]):   # 빈 칸이 빠져 칸이 밀린 표는 쓰지 않는다
            for yy in range(y - 3, y + 1):           # 그 보고서 표의 모든 칸(지난 연말 값 포함)을 모은다
                for qq in (1, 2, 3, 4):
                    i = dc._kics_col(t["cols"], yy, qq)
                    v = _num(str(t["ratio"][i]).replace("%", "")) if i is not None else None
                    if v is not None:
                        kics.setdefault((yy, qq), (v, p["rcept_no"]))
        out.append(row)
    for row in out:                                   # 같은 시점은 그 시점 보고서 값을 먼저, 없으면 나중 보고서의 지난 기간 칸
        y, q = int(row["period"][:4]), int(row["period"][5:7]) // 3
        hit = kics.get((y, q))
        row["kics"] = hit[0] if hit else None
        row["src"] = {"csm": "DART" if row.get("csm") is not None else None, "kics": "DART" if hit else None}
        hit_p = (prior_end.get(y) if q == 4 else prior_q.get((y, q))) if row.get("csm") is None else None
        if hit_p:                                     # 빈 칸: 다음 해 같은 분기 보고서의 '전기' 값 (연말 = 지난 연말 칸)
            row["csm"] = hit_p[0]
            row["src"]["csm"] = "DART 다음 보고서"
            row["derived"].append("csm_prior")
    from . import fisis
    if name and fisis.NUMBERS:                        # 전면 개편 1차: FISIS 숫자로 채우지 않는다
        _fill_from_fisis(out, name)
    return out


def _fill_from_fisis(rows: List[dict], name: str) -> None:
    """금감원 FISIS(회사 개별 기준)로 채운다: 지급여력비율은 FISIS 값을 먼저(공시 본문 표 칸이 어긋나 읽기 어려운 분기가 있다),
    CSM 잔액은 DART 원본(XBRL)에 없을 때만(2025년 1분기 이전 분기 보고서는 주석을 원본 파일에 넣지 않았다). FISIS 는 2023년 3월부터"""
    try:
        from . import fisis
        fs = fisis.Fisis()
        comp = next((c for c in fs.companies("H") + fs.companies("I") if fisis.short_name(c["finance_nm"]) == name), None)
        if not comp:
            return
        part = "H" if "생명" in comp.get("finance_path", "") else "I"
        names = fs.tables(part)
        cap = next((v for n, v in names.items() if n.startswith("자본적정성")), None)
        res = next((v for n, v in names.items() if n.startswith("책임준비금 적립현황(23.3월 이후)")), None)
    except Exception:                                  # noqa: BLE001 (키가 없거나 연결이 안 되면 DART 값만)
        return
    for r in rows:
        month = r["period"].replace(".", "")
        try:
            if cap:
                v = fisis._pick(fs.stat(comp["finance_cd"], cap, month), "지급여력비율(경과조치 적용 전)")
                if v:
                    r["kics"], r["src"]["kics"] = v, "FISIS"
            if res and r.get("csm") is None:
                v = fisis._pick(fs.stat(comp["finance_cd"], res, month), "책임준비금_보험계약부채_잔여보장요소_보험계약마진")
                if v is not None:
                    r["csm"], r["src"]["csm"] = v, "FISIS"
        except Exception:                              # noqa: BLE001
            continue


def dividends(dart: Dart, corp_code: str, today: Optional[date] = None) -> List[dict]:
    """배당에 관한 사항(사업보고서): 연도별 주당 현금배당금, 시가배당률, 배당성향. 최근 두 사업보고서로 6년치"""
    today = today or date.today()
    out: Dict[int, dict] = {}
    for y in (today.year - 1, today.year - 2, today.year - 4):
        try:
            res = dart.dividends(corp_code, y)
        except DartError:
            continue
        for row in res.get("list", []):
            se, knd = row.get("se", ""), row.get("stock_knd", "")
            ok = knd.startswith("보통") or knd in ("", "-")   # '보통주'·'보통주식'(HD한국조선해양 2025) 모두. 우선주·종류주식은 뺀다
            field = ("dps" if "주당 현금배당금" in se and ok else
                     "yield" if "현금배당수익률" in se and ok else
                     "payout" if "현금배당성향" in se and "연결" in se else None)
            if not field:
                continue
            for k, yy in (("thstrm", y), ("frmtrm", y - 1), ("lwfr", y - 2)):
                v = _num(row.get(k))
                if v is not None:
                    out.setdefault(yy, {"year": yy, "rcept_no": row.get("rcept_no", "")}).setdefault(field, v)
    return [out[y] for y in sorted(out)]


def prices(krx: Krx, ticker: str, days: int = 260, today: Optional[date] = None, fetch: bool = True) -> List[dict]:
    """최근 거래일부터 거꾸로 days 거래일의 종가. fetch=False 면 받아 둔 파일만 쓴다."""
    today = today or date.today()
    out, d, market, misses = [], today, None, 0
    while len(out) < days and misses < 400:
        if d.weekday() < 5:
            day = d.strftime("%Y%m%d")
            f = krx.cache / f"stk_bydd_trd-{day}.json"
            if fetch or f.exists():
                try:
                    if market:
                        res = krx.day(market[0], day)
                        hit = next((r for r in res["rows"] if krx._match(r, ticker)), None)
                    else:
                        hit, market, res = krx.find(day, ticker)
                except KrxError:
                    break
                if hit:
                    out.append({"date": day, "close": num(hit.get("TDD_CLSPRC")), "high": num(hit.get("TDD_HGPRC")),
                                "low": num(hit.get("TDD_LWPRC")), "open": num(hit.get("TDD_OPNPRC")),
                                "change": num(hit.get("CMPPREVDD_PRC")), "rate": num(hit.get("FLUC_RT")),
                                "mktcap": num(hit.get("MKTCAP")), "shares": num(hit.get("LIST_SHRS")), "volume": num(hit.get("ACC_TRDVOL")),
                                "market": market[2] if market else ""})
            misses += 1
        d -= timedelta(days=1)
    return out


def float_shares(dart: Dart, corp_code: str, today: Optional[date] = None) -> Optional[dict]:
    """유통주식수 (2026-11-24, 사용자): DART 공시 항목 API '주식의 총수' 합계 줄(보통주+우선주, 자기주식 제외).
    가장 최근 정기보고서부터 찾는다 (값이 '-' 인 보고서는 건너뜀). 못 찾으면 None"""
    today = today or date.today()
    names = {"11014": "3분기보고서", "11012": "반기보고서", "11013": "1분기보고서", "11011": "사업보고서"}
    for y in (today.year, today.year - 1):
        for rc in ("11014", "11012", "11013", "11011"):
            got = _float_in(dart, corp_code, y, rc)
            if got:
                return {"n": got[0], "label": f"{y}년 {names[rc]}", "rcept_no": got[1]}
    return None


def _float_in(dart: Dart, corp_code: str, year: int, reprt_code: str):
    """그 보고서의 유통주식수 합계 (보통주+우선주, 자기주식 제외)와 접수번호. 없으면 None"""
    try:
        res = dart.get("stockTotqySttus.json", {"corp_code": corp_code, "bsns_year": str(year), "reprt_code": reprt_code})
    except Exception:                                        # noqa: BLE001 (그 보고서가 없으면 None)
        return None
    rows = res.get("list", [])
    tot = next((r for r in rows if r.get("se") == "합계"), None)
    n = _num(tot.get("distb_stock_co")) if tot else None
    if n is None:
        parts = [_num(r.get("distb_stock_co")) for r in rows if r.get("se") in ("보통주", "우선주")]
        n = sum(p for p in parts if p) or None
    return (n, (tot or rows[0]).get("rcept_no", "")) if n else None


QUARTER_REPORT = {"03": "11013", "06": "11012", "09": "11014", "12": "11011"}


def float_history(dart: Dart, corp_code: str, periods: List[str]) -> Dict[str, float]:
    """기간('2024.06')마다 그 분기 정기보고서의 유통주식수 (PER·PBR 추이, 2026-11-24 사용자).
    그 보고서에 값이 없으면 가장 가까운 기간 값 (자사주 매입·소각이 없으면 같다). 하나도 없으면 빈 dict"""
    got: Dict[str, float] = {}
    for p in periods:
        r = _float_in(dart, corp_code, int(p[:4]), QUARTER_REPORT.get(p[5:7], "11011"))
        if r:
            got[p] = r[0]
    if not got:
        return {}
    known = sorted(got)

    def near(p: str) -> float:
        before = [k for k in known if k <= p]
        return got[before[-1]] if before else got[known[0]]   # 앞 보고서 먼저 (2026-11-26), 앞이 없으면 가장 이른 값
    return {p: got.get(p) or near(p) for p in periods}


def metrics(st: dict, price: Optional[dict], shares_float: Optional[dict] = None, divs: Optional[List[dict]] = None,
            fac: Optional[Dict[str, float]] = None) -> dict:
    """투자 지표(코드 계산). EPS·PER 는 최근 4분기 지배주주 순이익, BPS·PBR 은 최근 분기말 지배주주 자본, 가격은 KRX 종가.
    주식 수는 유통주식수(DART, 자기주식 제외·우선주 포함, 2026-11-24 사용자), 없으면 KRX 상장주식수.
    지배주주 순이익 줄이 없고 비지배지분도 없는 회사(삼성카드)는 전체 순이익이 곧 지배주주 몫이라 전체 순이익을 쓴다 (PER·PBR 추이와 같은 규칙)"""
    q = st.get("quarter", [])
    last4 = q[-4:]
    ni4, who = None, "지배주주 순이익"
    if len(last4) == 4 and all(p.get("ni_owner") is not None for p in last4):
        ni4 = sum(p["ni_owner"] for p in last4)
    elif (len(last4) == 4 and all(p.get("ni_owner") is None and p.get("ni") is not None for p in last4)
          and q[-1].get("nci") in (None, 0) and q[-1].get("equity_owner") == q[-1].get("equity")):
        ni4, who = sum(p["ni"] for p in last4), "순이익(비지배지분 없음)"
    eq_prev = None
    if shares_float and shares_float.get("n"):
        shares, sname = shares_float["n"], f"유통주식수({shares_float.get('label', '')}, 자기주식 제외)"
    else:
        shares, sname = (price.get("shares") if price else None), "상장주식수"
    close = price.get("close") if price else None
    eps = eps_ttm_adj(q, len(q) - 1, fac) if q else None  # 회사 공시 기본주당이익 최근 4분기 합, 분할 보정 (fac: 분기별 유통주식수, 2026-11-26)
    bps = bps_of(q[-1], shares)[0] if q else None         # valuation 과 같은 함수 (지배주주 규칙)
    roe = q[-1].get("roe") if q else None                 # add_returns 와 같은 값 (모든 화면 같은 ROE, 2026-11-26)
    dv = next((d for d in reversed(divs or []) if d.get("dps")), None)          # 최근 사업연도 주당배당금 (회사 공시)
    return {"eps": eps, "bps": bps, "per": per_of(close, eps),
            "pbr": close / bps if close and bps else None, "roe": roe, "shares": shares,
            "div_yield": dv["dps"] / close * 100 if dv and close else None, "div_year": dv["year"] if dv else None,
            "basis": {"eps": f"최근 4분기({last4[0]['period']}~{last4[-1]['period']}) 회사 공시 기본주당이익 합 (4분기 = 연간 − 3분기 누적)" if len(last4) == 4 else "",
                      "bps": f"{q[-1]['period']} 지배주주 자본 ÷ {sname}" if q else "",
                      "roe": f"최근 4분기 {owner_vals(q[-1])[2] if q else ''} 순이익 ÷ ((지금 + 1년 전 지배주주 자본) / 2)",
                      "div_yield": f"{dv['year']}년 주당배당금 ÷ 최근 종가(KRX)" if dv else ""}}


CO_V = 51                                           # 저장 형식이 바뀌면 올린다 (2: 재무 10년·사업 지표, 3: PER·PBR 추이, 4: 옛 계정 코드 ifrs_ 읽기, 2026-11-24). 다르면 다시 만든다


def _qnext(period: str) -> str:
    y, m = int(period[:4]), int(period[5:])
    return f"{y + 1}.03" if m == 12 else f"{y}.{m + 3:02d}"


def valuation(krx: Krx, ticker: str, st: dict, shares: Optional[Dict[str, float]] = None) -> List[dict]:
    """분기 말 PER·PBR 추이 (2026-11-24, 사용자): 분기 마지막 거래일의 시가총액(KRX) ÷ 최근 4분기 지배주주 순이익 / 분기 말 지배주주 자본(DART).
    지배주주 구분이 없으면 전체 순이익·자본. 순이익 합이 0 이하이면 PER 없음. 그날 시세를 못 받으면 그 분기는 뺀다.
    shares(기간 -> 유통주식수)가 있으면 시가총액 대신 종가 × 그 분기 유통주식수 (투자 지표 표와 같은 기준, 우선주 포함·자기주식 제외)"""
    q = st.get("quarter", [])
    out: List[dict] = []
    shares = shares or {}

    def cap(period: str, hit: dict) -> Optional[float]:
        """시가총액 = 그날 KRX 공식값 (보통주 종가 × 상장주식수, 자기주식 포함·우선주 제외, 2026-11-26 사용자 통일)"""
        return num(hit.get("MKTCAP"))
    qset = {p["period"] for p in q}
    for a in st.get("annual", []):                       # 분기 자료가 없는 옛 해(금융회사 등): 연간 순이익·연말 자본으로 12월 말 하나
        if f"{a['period']}.12" in qset:
            continue
        end = date(int(a["period"]), 12, 31)
        hit, day = None, None
        for k in range(8):
            d = end - timedelta(days=k)
            if d.weekday() >= 5:
                continue
            try:
                hit, _, _ = krx.find(d.strftime("%Y%m%d"), ticker)
            except KrxError:
                return out
            if hit:
                day = d.strftime("%Y%m%d")
                break
        if not hit:
            continue
        mc = cap(f"{a['period']}.12", hit)
        own = a.get("ni_owner") is not None and a.get("equity_owner") is not None
        ni, eq = (a.get("ni_owner"), a.get("equity_owner")) if own else (a.get("ni"), a.get("equity"))
        n = shares.get(f"{a['period']}.12")
        close = num(hit.get("TDD_CLSPRC"))
        out.append({"period": f"{a['period']}.12", "date": day, "close": close, "mktcap": mc,
                    "per": per_of(close, a.get("eps_disc")), "per_y": per_of(close, a.get("eps_disc")),   # 그때 주가 ÷ 그해 공시 EPS
                    "pbr": round(mc / eq, 2) if mc and eq and eq > 0 else None,
                    "shares": n, "eps": a.get("eps_disc"), "eps_y": a.get("eps_disc"), "bps": bps_of(a, n)[0], "bps_calc": bps_of(a, n)[1]})
    for i, p in enumerate(q):
        end = date.fromisoformat(p["end"][:10])
        hit, day = None, None
        for k in range(8):                                  # 분기 말이 휴일이면 그 앞 거래일
            d = end - timedelta(days=k)
            if d.weekday() >= 5:
                continue
            try:
                hit, _, _ = krx.find(d.strftime("%Y%m%d"), ticker)
            except KrxError:
                return out
            if hit:
                day = d.strftime("%Y%m%d")
                break
        if not hit:
            continue
        mc = cap(p["period"], hit)
        last4 = q[i - 3:i + 1] if i >= 3 else []
        seq = len(last4) == 4 and all(_qnext(a["period"]) == b["period"] for a, b in zip(last4, last4[1:]))
        own = seq and all(x.get("ni_owner") is not None for x in last4) and p.get("equity_owner") is not None
        ni4 = sum(x["ni_owner"] if own else (x.get("ni") or 0) for x in last4) if seq and (own or all(x.get("ni") is not None for x in last4)) else None
        eq = p.get("equity_owner") if own or p.get("equity_owner") is not None else p.get("equity")
        if ni4 is None and p["period"].endswith(".12"):     # 그해 분기 자료가 모자라면 12월 말은 연간 순이익으로
            a = next((x for x in st.get("annual", []) if x["period"] == p["period"][:4]), None)
            if a:
                ni4 = a.get("ni_owner") if a.get("ni_owner") is not None and p.get("equity_owner") is not None else a.get("ni")
        n = shares.get(p["period"])
        close = num(hit.get("TDD_CLSPRC"))
        ey = next((x.get("eps_disc") for x in st.get("annual", []) if x["period"] == p["period"][:4]), None) if p["period"].endswith(".12") else None
        out.append({"period": p["period"], "date": day, "close": close, "mktcap": mc,
                    "shares": n, "eps": None, "bps": bps_of(p, n)[0], "bps_calc": bps_of(p, n)[1],
                    **({"eps_y": ey} if p["period"].endswith(".12") else {})})
    out.sort(key=lambda r: r["period"])
    adjust_eps_splits(out)                                  # bps·eps_y·옛 연간 eps 를 지금 주식 수 기준으로 (split = 나눈 배수)
    # 최근 4분기 EPS: 분기 공시 EPS 를 하나씩 지금 주식 수 기준으로 맞춘 뒤 더한다 (4분기 사이에 액면분할이 있어도 맞게)
    fac = split_factors([x["period"] for x in q], {k: v for k, v in shares.items() if v})   # 경쟁사 표와 같은 함수
    idx = {x["period"]: j for j, x in enumerate(q)}
    for r in out:
        j = idx.get(r["period"])
        if j is not None:
            r["eps"] = eps_ttm_adj(q, j, fac)
    apply_known_rule(out, known_dates(st))
    return out


def apply_div_yield(rows: List[dict], dividends: Optional[List[dict]], known: Dict[str, str]) -> None:
    """배당수익률 (2026-11-26 사용자, 모든 화면 같은 규칙) = 그날까지 확정된 최근 연간 주당배당금 ÷ 그날 주가 × 100.
    확정 = 그 해 사업보고서 공시일(known['YYYY.12'] = min(접수일, 3월 31일)). 분할 전 배당·주가는 지금 주식 수 기준으로 맞춰 나눈다.
    분기배당은 사업보고서의 연간 합계로만 (그때그때 반영은 2차)"""
    fac = {r["period"]: (r.get("split") or 1.0) for r in rows}
    dv = []
    for d in dividends or []:
        if d.get("dps") is None:
            continue
        k = known.get(f"{d['year']}.12") or f"{d['year'] + 1}0331"
        later = [r for r in rows if r["period"] >= f"{d['year']}.12"]
        f = fac.get(f"{d['year']}.12") or ((later[0].get("split") or 1.0) if later else 1.0)
        dv.append((k, d["year"], d["dps"] / f))
    dv.sort()
    for r in rows:
        day, close = r.get("date") or "", r.get("close")
        hit = None
        for x in dv:
            if x[0] <= day:
                hit = x
        adj = close / (r.get("split") or 1.0) if close else None
        r["divy"] = round(hit[2] / adj * 100, 4) if hit and adj else None
        r["div_from"] = hit[1] if hit else None


def known_dates(st: dict) -> Dict[str, str]:
    """기간 → 그 실적이 공개된 날 (YYYYMMDD) = min(보고서 접수일, 법정 제출 기한). 접수번호가 정정 보고서 것이면 늦게 찍혀서
    기한(분기·반기 45일, 사업보고서 90일)으로 자른다 — 원래 보고서는 기한 안에 냈으므로 그날엔 공개돼 있었다"""
    out: Dict[str, str] = {}

    def one(period: str, end: str, rcept: str) -> None:
        r = str(rcept or "")[:8]
        try:
            dl = (date.fromisoformat(end[:10]) + timedelta(days=90 if period.endswith(".12") else 45)).strftime("%Y%m%d")
        except ValueError:
            return
        out[period] = min(r, dl) if len(r) == 8 and r.isdigit() else dl
    for p in st.get("quarter", []):
        one(p["period"], p.get("end") or "", p.get("rcept_no"))
    for a in st.get("annual", []):
        if f"{a['period']}.12" not in out:
            one(f"{a['period']}.12", f"{a['period']}-12-31", a.get("rcept_no"))
    return out


def apply_known_rule(rows: List[dict], known: Dict[str, str]) -> None:
    """PER·PBR = 그날 주가 ÷ 그날까지 공시된 가장 최근 EPS·BPS (2026-11-26 사용자: 일별 차트와 재무 추이를 같은 규칙으로).
    known = 기간 → 그 보고서 접수일(YYYYMMDD). 가장 최근 공시의 EPS 가 없거나 0 이하면 빈칸 (더 옛 값으로 채우지 않는다).
    액면분할이 사이에 있어도 맞게 분할 보정한 주가(종가 ÷ 그 기간 split)와 보정 EPS·BPS 끼리 나눈다"""
    for r in rows:
        k = known.get(r["period"])
        r["known"] = k if k and len(k) == 8 and k.isdigit() else None
    by_known = sorted((r for r in rows if r.get("known")), key=lambda r: (r["known"], r["period"]))
    for r in rows:
        day, close = r.get("date"), r.get("close")
        last = None
        for x in by_known:                                  # 그날까지 접수된 보고서 중 가장 최근
            if x["known"] <= (day or ""):
                last = x
            else:
                break
        adj = close / (r.get("split") or 1.0) if close else None
        r["per"] = per_of(adj, last.get("eps")) if last else None
        bps = last.get("bps") if last else None
        r["pbr"] = round(adj / bps, 2) if adj and bps is not None and bps > 0 else None
        r["per_from"] = last["period"] if last else None


def adjust_eps_splits(rows: List[dict]) -> None:
    """EPS·BPS 추이 (전면 개편 1차, 2026-11-25 사용자): 액면분할·병합으로 주식 수가 한 분기에 1.8배 넘게 바뀌면
    그 앞 기간 EPS·BPS 를 지금 주식 수 기준으로 나눠 맞추고 표시한다 (예: 삼성전자 2018년 50:1 → 그 전 값 ÷ 50).
    'split' = 나눈 배수, 'split_at' = 분할이 있었던 기간. PER·PBR 은 그때 주가와 그때 주식 수라 바꾸지 않는다"""
    factor = 1.0
    for i in range(len(rows) - 1, 0, -1):
        a, b = rows[i - 1].get("shares"), rows[i].get("shares")
        if a and b:
            r = b / a
            if r >= 1.8 or r <= 1 / 1.8:
                factor *= round(r) if r >= 1.8 else 1 / round(1 / r)
                rows[i]["split_at"] = round(r, 2)
        if factor != 1.0:
            x = rows[i - 1]
            for k in ("eps", "eps_y", "bps"):
                if x.get(k) is not None:
                    x[k] = round(x[k] / factor)
            x["split"] = factor


def fill_old_financial(dart: Dart, corp_code: str, st: dict, induty: str, basis: Optional[str] = None, today: Optional[date] = None) -> None:
    """금융회사 (2026-11-24, 사용자): DART 재무 API 가 2023년분부터라 2016~2022년은 사업보고서 원문의 재무제표 표로 채운다
    (doc_fs). 2023년 원문 값이 재무 API 와 맞을 때만 채운다. 보험사의 2022년 이전은 옛 회계기준(IFRS4) 표시"""
    from . import doc_fs
    try:
        doc_fs.fill_old(dart, corp_code, st, basis=basis, today=today)
    except (DartError, ValueError, IndexError, KeyError, ZeroDivisionError):
        return
    if induty.startswith(INSURANCE) and (st.get("doc_check") or {}).get("filled"):
        st["old_rule"] = {"before": "2023", "label": "옛 회계기준(IFRS4)"}


# 핵심 재무제표 (2026-11-24 사용자): 금감원 업권 -> 항목 목록. 부동산신탁·자산운용은 아직 목록이 없다
CORE_TEMPLATE = {"A": "bank", "E": "bank", "L": "holding", "H": "insurance", "I": "insurance",
                 "C": "card", "K": "card", "T": "card", "N": "card", "F": "securities", "W": "securities"}


def core_statements(dart: Dart, corp_code: str, part: Optional[str], basis: Optional[str], today: Optional[date] = None) -> Optional[dict]:
    """업권별 핵심 재무제표 10년 (연간). 업권을 모르면 None"""
    tpl = CORE_TEMPLATE.get(part or "")
    if not tpl:
        return None
    try:
        from . import core_fs
        return core_fs.build(dart, corp_code, tpl, basis or "연결", today=today)
    except Exception:                                  # noqa: BLE001 (덧붙임이라 실패해도 나머지 재무는 그대로)
        return None


def build(ticker: str, dart: Optional[Dart] = None, krx: Optional[Krx] = None, fetch_prices: bool = False,
          today: Optional[date] = None) -> dict:
    dart, krx = dart or Dart(), krx or Krx()
    corp = dart.corp_by_stock(ticker)
    if not corp:
        return {"error": "DART 에서 회사를 찾지 못했습니다."}
    info = {}
    try:
        r = dart.get("company.json", {"corp_code": corp["corp_code"]})
        info = {k: r.get(k) for k in ("corp_name", "corp_name_eng", "ceo_nm", "est_dt", "hm_url", "induty_code", "acc_mt", "adres")}
    except DartError:
        pass
    st = statements(dart, corp["corp_code"], today=today)
    induty = str(info.get("induty_code") or "")
    if OLD_FILL and induty.startswith(FINANCIAL_INDUSTRY):
        fill_old_financial(dart, corp["corp_code"], st, induty, today=today)
    elif OLD_FILL:
        try:
            fill_old_annual(dart, corp["corp_code"], st, today=today)
        except (DartError, ValueError, IndexError):
            pass
    if OLD_FILL:
        try:
            fill_old_da(dart, corp["corp_code"], st, today=today)
        except (DartError, ValueError, IndexError):
            pass
    sec = _sector(corp["corp_name"])
    core = core_statements(dart, corp["corp_code"], (sec or {}).get("part") if sec and not sec.get("error") else None, st.get("fs"), today) if OLD_FILL else None
    px = prices(krx, ticker, days=260, today=today, fetch=fetch_prices)
    if not px:                                       # 받아 둔 시세가 없으면 최근 거래일 하나만 받는다
        px = prices(krx, ticker, days=1, today=today, fetch=True)
    divs = dividends(dart, corp["corp_code"], today=today)
    try:
        sf = float_shares(dart, corp["corp_code"], today)
    except Exception:                                  # noqa: BLE001 (없으면 상장주식수)
        sf = None
    co = {
        "made_at": datetime.now().isoformat(timespec="seconds"), "v": CO_V,
        "ticker": ticker, "name": corp["corp_name"], "info": info,
        "financial": str(info.get("induty_code") or "").startswith(FINANCIAL_INDUSTRY),
        **_price_part(px, st, today, shares_float=sf, divs=divs),
        "shares_float": sf,
        "statements": st, "dividends": divs,
        "insurance": insurance(dart, corp["corp_code"], st.get("quarter", []), corp["corp_name"]) if str(info.get("induty_code") or "").startswith(INSURANCE) else None,
        "sector": sec, "core": core,
        "sales_mix": sales_mix(dart, corp["corp_code"], today, insurer=str(info.get("induty_code") or "").startswith(INSURANCE)),
        "valuation": _safe_valuation(krx, ticker, st, dart, corp["corp_code"]),
        "holder": _safe_holder(dart, corp["corp_code"], today),
        "business": _safe_business(dart, corp["corp_code"], today),   # 지주회사(HD한국조선해양)도 금융 업종 번호라 업종으로 거르지 않는다
        "sources": {"dart": "OpenDART 재무제표·기업개황·배당에 관한 사항", "krx": "KRX 유가증권·코스닥 일별매매정보"},
    }
    if co.get("valuation"):
        apply_div_yield(co["valuation"], co.get("dividends"), known_dates(st))
    _sync_eps(co)
    return co


def _sync_eps(co: dict) -> None:
    """현재 EPS 를 PER·PBR 추이의 마지막 분기 값과 같게 (최근 4분기 안에 액면분할이 있어도 분기마다 맞춘 값). PER 도 다시"""
    v = co.get("valuation") or []
    q = (co.get("statements") or {}).get("quarter") or []
    m = co.get("metrics")
    close = (co.get("price") or {}).get("close")
    if m is not None and v and q and v[-1]["period"] == q[-1]["period"]:
        if v[-1].get("eps") is not None:
            m["eps"] = v[-1]["eps"]
            m["per"] = per_of(close, m["eps"])
        if v[-1].get("bps") is not None:                  # BPS 도 같은 분기 주식 수 기준 (2026-11-26)
            m["bps"] = v[-1]["bps"]
            m["pbr"] = round(close / m["bps"], 4) if close and m["bps"] > 0 else None



PEER_CACHE = Path(__file__).resolve().parents[2] / ".cache" / "peers"
PEER_V = 20                                           # 저장 형식이 바뀌면 올린다 (2: 금감원 손익 지표, 3: DART 보험손익·투자손익·배당금수익, 4: 10년, 9: 상장사 분기 말 종가·시가총액·PER·PBR)


def peer_series(ticker: str = "", dart: Optional[Dart] = None, today: Optional[date] = None, corp_code: str = "", light: bool = False) -> dict:
    """동종사 추이 비교용 (2026-11-22): 그 회사의 분기·연간 재무, 보험사면 CSM·K-ICS, 금융사면 금감원 업종 지표 추이.
    화면에서 동종사를 체크하는 순간 부른다. 7일 동안 저장해 두고 다시 쓴다 (무료: DART·FISIS).
    corp_code 로 부르면 비상장사(정기보고서를 내는 회사)도 된다. light: 재무제표만 (보험·업종 지표는 금감원 값을 따로 쓸 때)"""
    if corp_code:
        if not re.fullmatch(r"[0-9]{8}", corp_code):
            return {"error": "회사 코드가 올바르지 않습니다."}
    elif not re.fullmatch(r"[0-9A-Z]{6}", ticker or ""):
        return {"error": "종목코드가 올바르지 않습니다."}
    f = PEER_CACHE / f"{corp_code or ticker}{'-fs' if light else ''}.json"
    from .dart import load_json, save_atomic
    saved = load_json(f) if f.exists() else None
    if saved is not None:
        if saved.get("v") == PEER_V and (datetime.now() - datetime.fromisoformat(saved.get("made_at", "2000-01-01T00:00:00"))).days < 7:
            return saved
    dart = dart or Dart()
    corp = dart.corp_by_code(corp_code) if corp_code else dart.corp_by_stock(ticker)
    if not corp:
        return {"error": "DART 에서 회사를 찾지 못했습니다."}
    induty = ""
    try:
        induty = str(dart.get("company.json", {"corp_code": corp["corp_code"]}).get("induty_code") or "")
    except DartError:
        pass
    st = statements(dart, corp["corp_code"], today=today)
    if OLD_FILL and induty.startswith(FINANCIAL_INDUSTRY):
        fill_old_financial(dart, corp["corp_code"], st, induty, today=today)
    sec = _sector(corp["corp_name"]) if induty.startswith(FINANCIAL_INDUSTRY) and not light else None
    out = {"v": PEER_V, "made_at": datetime.now().isoformat(timespec="seconds"), "ticker": ticker or corp.get("stock_code") or corp["corp_code"], "name": corp["corp_name"],
           "financial": induty.startswith(FINANCIAL_INDUSTRY), "fs": st.get("fs"),
           "annual": st.get("annual", []), "quarter": st.get("quarter", []),
           "insurance": insurance(dart, corp["corp_code"], st.get("quarter", []), corp["corp_name"]) if induty.startswith(INSURANCE) and not light else None,
           "sector": ({k: sec.get(k) for k in ("part", "sector", "company", "month", "metrics", "series")} if sec and not sec.get("error") else None)}
    if corp.get("stock_code") and not corp_code:      # 상장사: 분기 말 종가·시가총액·PER·PBR (추이 비교, 2026-11-26 사용자). 종목정보와 같은 계산
        out["valuation"] = _safe_valuation(Krx(), corp["stock_code"], st, dart, corp["corp_code"])
    save_atomic(f, json.dumps(out, ensure_ascii=False))
    return out


# ---------- 주가 차트 (2026-11-23): 받아 둔 KRX 일별 파일에서 한 종목의 시가·고가·저가·종가·거래량 ----------
def adjust_splits(rows: List[dict]) -> List[dict]:
    """액면분할·병합 보정(수정주가): 상장주식수가 하루 새 k배(1.9배 이상) 바뀌고 종가가 약 1/k 로 움직인 날 이전 가격을 1/k 배,
    거래량을 k 배 한다. 유상증자·배당 같은 다른 보정은 하지 않는다. rows 는 오래된 날부터"""
    out = [dict(r) for r in rows]
    for i in range(len(out) - 1, 0, -1):
        a, b = out[i - 1], out[i]
        if not (a.get("shares") and b.get("shares") and a.get("close") and b.get("close")):
            continue
        k = b["shares"] / a["shares"]
        move = a["close"] / b["close"]
        if (k >= 1.9 or k <= 1 / 1.9) and 0.6 < move / k < 1.6:
            for r in out[:i]:
                for f in ("open", "high", "low", "close"):
                    if r.get(f) is not None:
                        r[f] = r[f] / k
                if r.get("volume") is not None:
                    r["volume"] = r["volume"] * k
                r["adjusted"] = True
    return out


def history(ticker: str, days: int = 260, krx: Optional[Krx] = None) -> dict:
    """주가 차트 자료: {'rows': [{'d','o','h','l','c','v'}] 오래된 날부터, 'adjusted': 보정했나, 'first': 첫 날}.
    먼저 토스증권 수정주가 일봉(2026-11-24, 1995년부터). 토스를 못 쓰면 받아 둔 KRX 일별 시세 + 액면분할 보정"""
    try:
        from .toss import daily
        tr = daily(ticker)
    except Exception:                                  # noqa: BLE001 (키 없음·연결 실패면 KRX 로)
        tr = []
    if tr:
        return {"ticker": ticker, "adjusted": True, "first": tr[0]["date"], "source": "토스증권", "old_source": "토스증권",
                "rows": [{"d": r["date"], "o": r["open"] or r["close"], "h": r["high"] or r["close"], "l": r["low"] or r["close"],
                          "c": r["close"], "v": r["volume"]} for r in tr]}
    krx = krx or Krx()
    px = prices(krx, ticker, days=days, fetch=True)
    recent = list(reversed([p for p in px if p.get("close")]))
    first = recent[0]["date"] if recent else "99999999"
    # 최근 1년보다 앞: 공공데이터포털(키가 있으면, 종목별 저장) -> 없으면 KRX 에서 받아 둔 긴 기록
    old: List[dict] = []
    src = "KRX"
    try:
        from .datagokr import history as dg_history
        got = dg_history(ticker, first) if recent else None
        if got:
            old, src = got, "공공데이터포털"
    except Exception:                                  # noqa: BLE001 (과거 기록은 덧붙임이라 실패해도 최근 1년은 보인다)
        old = []
    if not old:
        from .krx_history import old_rows
        old = old_rows(ticker)
    rows = adjust_splits([r for r in old if r["date"] < first and r.get("close")] + recent)
    return {"ticker": ticker, "adjusted": any(r.get("adjusted") for r in rows), "first": rows[0]["date"] if rows else None, "source": "KRX", "old_source": src,
            "rows": [{"d": r["date"], "o": r.get("open") or r["close"], "h": r.get("high") or r["close"], "l": r.get("low") or r["close"],
                      "c": r["close"], "v": r.get("volume")} for r in rows]}


# ---------- 리포트 없이 검색한 종목 (2026-11-23): 리포트와 같은 기본 종목정보를 종목코드만으로 ----------
STOCK_CACHE = Path(__file__).resolve().parents[2] / ".cache" / "company"


def stock(ticker: str, refresh_price: bool = False) -> dict:
    """저장해 둔 종목정보. 없거나 7일이 지났으면 새로 만들고(1~2분), refresh_price 면 시세만 새로 받는다"""
    from .dart import load_json, save_atomic
    if not re.fullmatch(r"[0-9A-Z]{6}", ticker or ""):
        return {"error": "종목코드가 올바르지 않습니다."}
    f = STOCK_CACHE / f"{ticker}.json"
    co = load_json(f) if f.exists() else None
    if co and not co.get("error") and refresh_price:
        co = refresh({"meta": {"ticker": ticker}, "company": co})
    elif not co or co.get("error") or co.get("v") != CO_V or datetime.now() - datetime.fromisoformat(co["made_at"]) > timedelta(days=7):
        co = refresh({"meta": {"ticker": ticker}, "company": None})
    if co and co.get("financial") and (co.get("sector") or {}).get("error"):   # 금감원 연결이 잠깐 끊겨 실패한 업종 지표는 열 때마다 다시
        co["sector"] = _sector(co.get("name", ""))
        fill_peers_from_industry(co)                  # 다시 받은 업계 값으로 경쟁사 K-ICS 칸도 채운다
    save_atomic(f, json.dumps(co, ensure_ascii=False))
    return co


def separate(ticker: str) -> dict:
    """별도(회사 단독) 기준 재무 (2026-11-24, 사용자): 화면에서 '별도'를 누를 때 만든다. 7일 저장"""
    from .dart import load_json, save_atomic
    if not re.fullmatch(r"[0-9A-Z]{6}", ticker or ""):
        return {"error": "종목코드가 올바르지 않습니다."}
    f = STOCK_CACHE / f"{ticker}-separate.json"
    saved = load_json(f) if f.exists() else None
    if saved and saved.get("v") == CO_V and datetime.now() - datetime.fromisoformat(saved["made_at"]) < timedelta(days=7):
        return saved
    dart = Dart()
    corp = dart.corp_by_stock(ticker)
    if not corp:
        return {"error": "DART 에서 회사를 찾지 못했습니다."}
    st = statements(dart, corp["corp_code"], only="OFS")
    induty = ""
    try:
        induty = str(dart.get("company.json", {"corp_code": corp["corp_code"]}).get("induty_code") or "")
    except DartError:
        pass
    if OLD_FILL and induty.startswith(FINANCIAL_INDUSTRY):
        fill_old_financial(dart, corp["corp_code"], st, induty, basis="별도")
    part = None                                       # 업권은 저장해 둔 종목정보에서 (별도 화면 때문에 금감원을 다시 부르지 않는다)
    main = STOCK_CACHE / f"{ticker}.json"
    if main.exists():
        part = ((load_json(main) or {}).get("sector") or {}).get("part")
    out = {"made_at": datetime.now().isoformat(timespec="seconds"), "v": CO_V, "statements": st,
           "core": core_statements(dart, corp["corp_code"], part, "별도") if OLD_FILL else None}
    save_atomic(f, json.dumps(out, ensure_ascii=False))
    return out


def _sector(name: str) -> Optional[dict]:
    """금융회사면 금감원 FISIS 업종 지표(추이 + 업계 전체 비교). 비금융·키 없음·실패는 None"""
    try:
        from . import fisis
        return fisis.sector(name)
    except Exception as e:                                           # noqa: BLE001 (참고 정보라 실패해도 나머지는 그대로)
        return {"error": type(e).__name__}


def fill_peers_from_industry(co: dict) -> None:
    """경쟁사 줄의 K-ICS 가 비면(회사마다 공시 표 모양이 달라 못 읽음) 금감원 FISIS 업계 표의 값으로 채운다. 출처를 적는다"""
    pe, ind = co.get("peers") or {}, co.get("sector") or {}
    if not pe.get("rows") or not ind.get("peers"):
        return
    by = {r["name"]: r for r in ind["peers"]}
    for r in pe["rows"]:
        hit = by.get(r.get("name", "")) or next((v for k, v in by.items() if r.get("name", "").startswith(k) or k.startswith(r.get("name", "")[:4])), None)
        if hit and r.get("kics") is None and hit.get("kics") is not None:
            r["kics"] = hit["kics"]
            r["kics_src"] = f"FISIS {ind.get('month', '')}"


def _price_part(px: List[dict], st: dict, today: Optional[date] = None, shares_float: Optional[dict] = None,
                divs: Optional[List[dict]] = None) -> dict:
    """시세에서 나오는 칸: 최근 거래일, 52주 범위, 투자 지표, 확인 시각"""
    cur = px[0] if px else None
    year = [p for p in px if p["date"] >= ((today or date.today()) - timedelta(days=365)).strftime("%Y%m%d")]
    return {"price": cur,
            "range52": ({"high": max(p["high"] or p["close"] for p in year), "low": min(p["low"] or p["close"] for p in year),
                         "days": len(year)} if len(year) >= 200 else None),
            "price_days": len([p for p in px if p.get("close")]),
            "metrics": metrics(st, cur, shares_float, divs),
            "price_checked_at": datetime.now().isoformat(timespec="seconds")}


def new_filing(dart: Dart, co: dict) -> bool:
    """만든 뒤 새 정기보고서(분기·반기·사업보고서, 정정 포함)가 DART 에 올라왔나 (2026-11-23). 하루 한 번, 목록 한 번만 묻는다.
    있으면 재무·지표를 그날 바로 다시 만든다. 묻지 못하면 False (시세만 새로)"""
    try:
        corp = dart.corp_by_stock(co.get("ticker") or "")
        if not corp:
            return False
        known = {x.get("rcept_no") for x in (co.get("statements") or {}).get("sources", [])}
        res = dart.get("list.json", {"corp_code": corp["corp_code"], "bgn_de": co["made_at"][:10].replace("-", ""),
                                     "end_de": date.today().strftime("%Y%m%d"), "pblntf_ty": "A", "page_count": "20"})
        return any(x.get("rcept_no") not in known and re.search(r"(분기|반기|사업)보고서", x.get("report_nm", "")) for x in res.get("list", []))
    except Exception:                                                # noqa: BLE001 (확인 실패는 다음 날 다시)
        return False


def refresh(result: dict, dart: Optional[Dart] = None, krx: Optional[Krx] = None) -> dict:
    """화면을 열 때 부른다: 시세만 새로 받는다(빠진 거래일만, 몇 초). 재무가 7일보다 오래됐거나 없으면 통째로 다시 만든다.
    경쟁사 줄의 종가·시가총액·PER·PBR 도 그날 시세로 고친다."""
    dart, krx = dart or Dart(), krx or Krx()
    ticker = (result.get("meta") or {}).get("ticker") or ""
    co = result.get("company")
    old = not co or co.get("error") or co.get("v") != CO_V or datetime.now() - datetime.fromisoformat(co["made_at"]) > timedelta(days=7) or new_filing(dart, co)
    if old:
        co = build(ticker, dart, krx, fetch_prices=True)
        try:
            from . import peers
            part = (co.get("sector") or {}).get("part") if co.get("financial") or (co.get("sector") or {}).get("part") else None
            co["peers"] = peers.build(ticker, dart, krx, fin_part=part) if peers.INDEX.exists() else None
            fill_peers_from_industry(co)
        except Exception as e:                                       # noqa: BLE001 (경쟁사 비교는 참고라 실패해도 나머지는 그대로)
            co["peers"] = {"error": type(e).__name__}
        return co
    px = prices(krx, ticker, days=260, fetch=True)
    co.update(_price_part(px, co["statements"], shares_float=co.get("shares_float"), divs=co.get("dividends")))
    _sync_eps(co)
    pe = co.get("peers") or {}
    if pe.get("rows"):
        from . import peers
        market = peers.latest_market(krx)
        for r in pe["rows"]:
            mk = market.get(r.get("ticker"))
            if not mk or r.get("error"):
                continue
            close = num(mk.get("TDD_CLSPRC"))
            r.update(close=close, mktcap=num(mk.get("MKTCAP")), rate=num(mk.get("FLUC_RT")), day=mk.get("_day"),
                     per=close / r["eps"] if close and r.get("eps") and r["eps"] > 0 else None,
                     pbr=close / r["bps"] if close and r.get("bps") else None)
    return co


def main(argv) -> int:
    if not argv:
        print(__doc__)
        return 2
    if argv[0] == "--fisis-peer":                    # 화면이 부른다: 금감원에 있는 회사(비상장 포함) 하나의 업종 지표 추이
        from . import fisis
        try:
            out = ({"error": "금감원 숫자는 지금 쓰지 않습니다 (DART만)."} if not fisis.NUMBERS
                   else fisis.company_series(argv[1], argv[2]) if len(argv) > 2 else {"error": "업종·회사 코드가 없습니다."})
            if not out.get("error"):                 # DART 에 정기보고서를 내는 비상장사면 재무제표도 (매출·순이익·ROE 등)
                d = Dart()
                full = out["full_name"]
                names = [full, re.sub(r"\s*(주식회사|\(주\)|㈜)\s*", "", full).strip()]
                hit = next((h for n in names for h in d.unlisted_by_name(n)), None)
                if hit:
                    ds = peer_series(dart=d, corp_code=hit["corp_code"], light=True)
                    if not ds.get("error") and (ds.get("annual") or ds.get("quarter")):
                        out["dart"] = ds
        except Exception as e:                       # noqa: BLE001 (키·연결 문제는 화면에 짧게)
            out = {"error": f"금감원 자료를 받지 못했습니다 ({type(e).__name__})."}
        sys.stdout.write(json.dumps(out, ensure_ascii=False))
        return 0
    if argv[0] == "--peer-corp":                     # 화면이 부른다: DART 정기보고서를 내는 비상장사 하나의 재무 추이
        sys.stdout.write(json.dumps(peer_series(corp_code=argv[1] if len(argv) > 1 else ""), ensure_ascii=False))
        return 0
    if argv[0] == "--peer":                          # 화면이 부른다: 동종사 하나의 추이를 JSON 으로
        sys.stdout.write(json.dumps(peer_series(argv[1] if len(argv) > 1 else ""), ensure_ascii=False))
        return 0
    if argv[0] == "--separate":                      # 화면이 부른다: 별도 기준 재무
        sys.stdout.write(json.dumps(separate(argv[1] if len(argv) > 1 else ""), ensure_ascii=False))
        return 0
    if argv[0] == "--market":                        # 화면이 부른다: 시장 화면 (토스증권)
        try:
            from .toss import market
            out = market()
        except Exception as e:                       # noqa: BLE001
            out = {"error": str(e) if type(e).__name__ == "TossError" else "토스증권 시장 자료를 받지 못했습니다."}
        sys.stdout.write(json.dumps(out, ensure_ascii=False))
        return 0
    if argv[0] == "--sector":                        # 화면이 부른다: 토스증권 섹터 (동종업계 비교)
        try:
            from .toss import sector_view
            out = sector_view(argv[1] if len(argv) > 1 else "")
        except Exception as e:                       # noqa: BLE001
            out = {"error": str(e) if type(e).__name__ == "TossError" else "토스증권 섹터 자료를 받지 못했습니다."}
        sys.stdout.write(json.dumps(out, ensure_ascii=False))
        return 0
    if argv[0] == "--last-prices":                   # 화면이 부른다: 경쟁사 표 현재가 (토스, 종목당 1번, 10분 저장)
        try:
            from . import toss
            out = {"prices": toss.last_prices([x for x in (argv[1] if len(argv) > 1 else "").split(",") if x])}
        except Exception as e:                       # noqa: BLE001
            out = {"error": str(e) if type(e).__name__ == "TossError" else "현재가를 받지 못했습니다.", "prices": {}}
        sys.stdout.write(json.dumps(out, ensure_ascii=False))
        return 0
    if argv[0] in ("--sector-list", "--sector-extra"):   # 화면이 부른다: 섹터 직접 추가 (2026-11-26 사용자)
        try:
            from . import toss
            if argv[0] == "--sector-list":
                out = {"sectors": toss.sector_list()}
            else:
                tk = argv[1] if len(argv) > 1 else ""
                toss.set_extra_sectors(tk, [x for x in (argv[2] if len(argv) > 2 else "").split(",") if x])
                out = toss.sector_view(tk)
        except Exception as e:                       # noqa: BLE001
            out = {"error": str(e) if type(e).__name__ == "TossError" else "섹터를 저장하지 못했습니다."}
        sys.stdout.write(json.dumps(out, ensure_ascii=False))
        return 0
    if argv[0] == "--peers-sector":                  # 화면이 부른다: 고른 토스 섹터의 경쟁사 표 (사업이 여럿인 회사)
        try:
            from . import peers
            out = peers.for_sector(argv[1] if len(argv) > 1 else "", argv[2] if len(argv) > 2 else "")
        except Exception:                            # noqa: BLE001
            out = {"error": "그 섹터의 경쟁사 표를 만들지 못했습니다."}
        sys.stdout.write(json.dumps(out, ensure_ascii=False))
        return 0
    if argv[0] == "--flows":                         # 화면이 부른다: 수급 (토스증권)
        try:
            from .toss import flows
            out = flows(argv[1] if len(argv) > 1 else "")
        except Exception as e:                       # noqa: BLE001 (메시지에 키·토큰 없음)
            out = {"error": str(e) if type(e).__name__ == "TossError" else "토스증권 수급 자료를 받지 못했습니다."}
        sys.stdout.write(json.dumps(out, ensure_ascii=False))
        return 0
    if argv[0] == "--history":                       # 화면이 부른다: 주가 차트 자료
        sys.stdout.write(json.dumps(history(argv[1] if len(argv) > 1 else "", days=int(argv[2]) if len(argv) > 2 else 260), ensure_ascii=False))
        return 0
    if argv[0] == "--stock":                         # 화면이 부른다: 리포트 없이 검색한 종목의 기본 종목정보
        sys.stdout.write(json.dumps(stock(argv[1] if len(argv) > 1 else "", refresh_price="--refresh" in argv), ensure_ascii=False))
        return 0
    if argv[0] == "--prices":
        px = prices(Krx(), argv[1], days=260, fetch=True)
        print(f"KRX 시세 {len(px)}거래일 모음 ({px[-1]['date'] if px else '-'} ~ {px[0]['date'] if px else '-'})")
        return 0
    run = Path(argv[0])
    res_path = run / "result.json"
    result = json.loads(res_path.read_text(encoding="utf-8"))
    ticker = (result.get("meta") or {}).get("ticker") or ""
    if "--refresh" in argv:                          # 화면이 부른다: 결과 파일을 고치고 company 칸만 JSON 으로 내보낸다
        # 리포트 종목정보 = 종목 검색과 같은 저장 자료 (2026-11-24, 사용자): 한쪽을 고치면 다른 쪽도 같다
        result["company"] = stock(ticker, refresh_price=True) if re.fullmatch(r"[0-9A-Z]{6}", ticker) and ticker != "000000" else refresh(result)
        res_path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        sys.stdout.write(json.dumps(result["company"], ensure_ascii=False))
        return 0
    comp = build(ticker, fetch_prices="--fetch-prices" in argv)
    if "--peers" in argv or "peers" in (result.get("company") or {}):
        from . import peers
        comp["peers"] = peers.build(ticker, fin_part=(comp.get("sector") or {}).get("part"))
        fill_peers_from_industry(comp)
    result["company"] = comp
    res_path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    st = comp.get("statements", {})
    print(f"{comp.get('name')} ({ticker}) · 재무 {st.get('fs')} · 분기 {len(st.get('quarter', []))}개 · 연간 {len(st.get('annual', []))}개 · "
          f"배당 {len(comp.get('dividends', []))}년 · 시세 {comp.get('price_days')}거래일 · 52주 범위 {'있음' if comp.get('range52') else '없음(시세 부족)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
