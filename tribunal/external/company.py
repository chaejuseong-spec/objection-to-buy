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
    ("ni_owner", "지배주주 순이익", "IS", ["ifrs-full_ProfitLossAttributableToOwnersOfParent"], ["지배기업의 소유주에게 귀속되는 당기순이익(손실)"]),
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
FINANCIAL_INDUSTRY = ("64", "65", "66")          # 표준산업분류: 금융(은행·지주), 보험, 금융 지원 서비스


def _num(v) -> Optional[float]:
    t = str(v if v is not None else "").replace(",", "").strip()
    if t in ("", "-"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _find(rows: List[dict], sj: str, ids: List[str], names: List[str]) -> Optional[dict]:
    rows = [r for r in rows if r.get("sj_div") in SJ[sj]]
    for i in ids:
        hit = next((r for r in rows if r.get("account_id") == i), None)
        if hit:
            return hit
    for n in names:
        hit = next((r for r in rows if (r.get("account_nm") or "").replace(" ", "") == n.replace(" ", "")), None)
        if hit:
            return hit
    return None


def _report(dart: Dart, corp_code: str, year: int, q: int) -> Optional[dict]:
    """그 기간 보고서의 재무제표. 연결이 없으면 별도. {fs, rows, rcept_no}"""
    for fs in ("CFS", "OFS"):
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


def _check_liab(p: dict) -> None:
    """부채총계 = 자산총계 − 자본총계. DART API 가 다른 분기 값을 되풀이해 주는 일이 있어(2025 분기 보고서들), 맞지 않으면 자산−자본으로 바꾸고 표시"""
    a, e, l = p.get("assets"), p.get("equity"), p.get("liab")
    if a is not None and e is not None and l is not None and abs(a - e - l) > abs(a) * 0.001:
        p["liab"] = a - e
        p.setdefault("derived", []).append("liab")


def statements(dart: Dart, corp_code: str, years: int = 6, today: Optional[date] = None) -> dict:
    """최근 years 년의 보고서들을 모아 분기·연간 핵심 계정과 재무제표 전체를 만든다."""
    today = today or date.today()
    reps: Dict[tuple, dict] = {}
    for y in range(today.year - years + 1, today.year + 1):
        for q in (1, 2, 3, 4):
            if date(y, int(QEND[q][:2]), int(QEND[q][3:])) >= today:
                continue
            r = _report(dart, corp_code, y, q)
            if r:
                reps[(y, q)] = r
    if not reps:
        return {"periods": [], "annual": [], "quarter": [], "tables": {}, "fs": None}
    fs = reps[max(reps)]["fs"]
    reps = {k: v for k, v in reps.items() if v["fs"] == fs}          # 연결·별도를 섞지 않는다

    def val(rep, key, field):
        k = next(x for x in KEYS if x[0] == key)
        row = _find(rep["rows"], k[2], k[3], k[4])
        return _num(row.get(field)) if row else None

    annual, quarter = [], []
    for (y, q), rep in sorted(reps.items()):
        if q == 4:
            a = {"period": f"{y}", "end": f"{y}-12-31", "rcept_no": rep["rcept_no"], "derived": [],
                 **{k[0]: val(rep, k[0], "thstrm_amount") for k in KEYS}}
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
        quarter.append(p)
    _drop_repeats(quarter)
    _fill_from_xbrl(dart, reps, quarter)
    for p in quarter:
        _check_liab(p)

    # 재무제표 상세: 기간마다 계정 값 (계정 순서는 가장 최근 보고서의 순서)
    tables = {}
    for sj in ("IS", "BS", "CF"):
        latest = reps[max(reps)]["rows"]
        order = [(r["account_id"], r["account_nm"]) for r in sorted((r for r in latest if r.get("sj_div") in SJ[sj]), key=lambda r: int(r.get("ord") or 0))]
        seen, accts = set(), []
        for aid, nm in order:
            if (aid, nm) not in seen:
                seen.add((aid, nm))
                accts.append({"id": aid, "name": nm, "key": next((k[0] for k in KEYS if k[2] == sj and (aid in k[3] or nm in k[4])), None)})
        cols = {"annual": {}, "quarter": {}}
        for (y, q), rep in sorted(reps.items()):
            rows = [r for r in rep["rows"] if r.get("sj_div") in SJ[sj]]
            by = {(r["account_id"], r["account_nm"]): r for r in rows}
            if q == 4:
                cols["annual"][f"{y}"] = {f"{a['id']}|{a['name']}": _num(by[(a["id"], a["name"])].get("thstrm_amount")) if (a["id"], a["name"]) in by else None for a in accts}
            if sj == "IS" and q == 4:
                prev = {(r["account_id"], r["account_nm"]): r for r in reps[(y, 3)]["rows"]} if (y, 3) in reps else {}
                qv = {}
                for a in accts:
                    k = (a["id"], a["name"])
                    tot = _num(by[k].get("thstrm_amount")) if k in by else None
                    cum = _num(prev[k].get("thstrm_add_amount")) if k in prev else None
                    qv[f"{a['id']}|{a['name']}"] = tot - cum if tot is not None and cum is not None else None
                cols["quarter"][f"{y}.12"] = qv
            else:
                cols["quarter"][f"{y}.{q * 3:02d}"] = {f"{a['id']}|{a['name']}": _num(by[(a["id"], a["name"])].get("thstrm_amount")) if (a["id"], a["name"]) in by else None for a in accts}
        # 주요 계정은 차트와 같은 값으로: 해마다 계정 id·이름이 바뀐 회사가 있어(예: '매출액' → '수익(매출액)') 이름이 정확히 같은 줄만 보면 옛 해가 빈다
        for a in accts:
            if not a["key"]:
                continue
            acc = f"{a['id']}|{a['name']}"
            for p in annual:
                if p["period"] in cols["annual"] and p.get(a["key"]) is not None:
                    cols["annual"][p["period"]][acc] = p[a["key"]]
            for p in quarter:
                if p["period"] in cols["quarter"] and p.get(a["key"]) is not None:
                    cols["quarter"][p["period"]][acc] = p[a["key"]]
        missing = []
        if sj == "BS":                                # 앞 분기와 1조원 넘는 값이 원 단위까지 같으면 API 가 되풀이한 값으로 보고 비운다(_drop_repeats 와 같은 이유)
            ks = list(cols["quarter"])
            orig = {k: dict(cols["quarter"][k]) for k in ks}
            for a0, a1 in zip(ks, ks[1:]):
                same = [acc for acc, v in orig[a1].items() if v is not None and abs(v) >= 1e12 and v == orig[a0].get(acc)]
                if len(same) < 2:                     # 자본조정처럼 원래 잘 안 바뀌는 계정 하나는 정상
                    continue
                for acc in same:
                    cols["quarter"][a1][acc] = None
                    missing.append([a1, acc])
        if sj == "BS":                                # 원본(XBRL)에서 바로잡은 합계는 표에도 넣는다
            for p in quarter:
                for k in p.get("fixed", []):
                    acc = next((f"{a['id']}|{a['name']}" for a in accts if a["key"] == k), None)
                    if acc and p["period"] in cols["quarter"]:
                        cols["quarter"][p["period"]][acc] = p[k]
                        missing = [m for m in missing if m != [p["period"], acc]]
        tables[sj] = {"accounts": accts, "annual": cols["annual"], "quarter": cols["quarter"], "missing": missing}
    return {"fs": fs, "annual": annual, "quarter": quarter, "tables": tables,
            "sources": [{"period": f"{y}.{q * 3:02d}", "rcept_no": r["rcept_no"], "url": original_url(r["rcept_no"])} for (y, q), r in sorted(reps.items())]}


# ---------- 매출 구성 (2026-11-22): 사업보고서 '사업의 내용'에서 회사가 적은 제품·서비스별 매출 비중 표 ----------
# 회사마다 표 이름이 달라 차례로 찾는다. 보험사는 매출 대신 '보험종목별 수입보험료'. 표를 못 찾으면 None (지어내지 않는다)
MIX_ANCHORS = ["주요 제품 등의 현황", "주요 제품 및 서비스", "주요 제품등의 현황", "보험종목별 수입보험료 내역", "주요 상품 및 서비스", "매출실적", "매출 실적"]
_SHARE = re.compile(r"^%$|비\s*율|비\s*중|구성비")
_NAMECOL = re.compile(r"품\s*목|제\s*품|구\s*분|상\s*품|종\s*목|사업\s*부문")
_TOTAL = re.compile(r"^(합\s*계|소\s*계|계|총\s*계|합계)$")


# 부문별 매출 (2026-11-22): 모든 상장사가 같은 형식으로 내는 재무제표 원본(XBRL) 주석 '영업부문'의 매출액.
# 회사마다 표 모양이 달라도 항목 이름표(Revenue)와 축(SegmentsAxis·ProductsAndServicesAxis)은 같다
SEG_AXES = ("SegmentsAxis", "SegmentConsolidationItemsAxis", "ProductsAndServicesAxis")   # 부문을 둘째 축에 적는 회사도 있다(LG화학)
SEG_NAMES = ("Revenue", "RevenueFromContractsWithCustomers")
SEG_PROFIT = ("ProfitLossFromOperatingActivities",)          # 매출액 계정이 없는 금융회사: 부문별 영업이익 (모두 흑자일 때만)
_COMPANY = re.compile(r"\(주\)|㈜|주식회사|\b(Inc|Ltd|LLC|Co|Corp|GmbH|B\.V|S\.A|SAS|Pvt|Limited|LIMITED|LTD|INC|Ltda|BHD|NANJING|LLP)\b", re.I)   # 자회사 목록 표는 부문이 아니다
SEG_SKIP = {"OperatingSegmentsMember", "ReportableSegmentsMember", "SegmentsMember"}
_SEG_DROP = re.compile(r"Elimination|Reconcil|Adjust|Unallocated|Total|Aggregat|Corporate", re.I)
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
        return re.sub(r",\s*(영업|보고)부문$", "", t)

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
            total.setdefault(f["dims"].get(FS, ""), f["value"])
    groups: Dict[tuple, Dict[str, float]] = {}
    for f in facts:
        for ax in SEG_AXES:
            if ax in f["dims"]:
                fixed = tuple(sorted((k, v) for k, v in f["dims"].items() if k != ax))
                groups.setdefault((ax, fixed, f["name"]), {})[f["dims"][ax]] = f["value"]
    best = None
    for (ax, fixed, _), mem in groups.items():
        fx = dict(fixed)
        if "GeographicalAreasAxis" in fx or any(_SEG_DROP.search(v) or _SEG_DROP_KO.search(lab(v)) for k, v in fixed if k != FS):
            continue
        keep = {m: v for m, v in mem.items() if m not in SEG_SKIP and not _SEG_DROP.search(m) and not _SEG_DROP_KO.search(lab(m))}
        if names is SEG_PROFIT and any(v is None or v <= 0 for v in keep.values()):
            continue
        items = [(lab(m), v) for m, v in keep.items() if v and v > 0]
        if len(items) < 2 or len({n for n, _ in items}) < len(items) or sum(bool(_COMPANY.search(n)) for n, _ in items) * 2 > len(items):
            continue
        sm = sum(v for _, v in items)
        tot = total.get(fx.get(FS, ""))
        if tot and not (0.9 * tot <= sm <= 2.0 * tot):
            continue
        top = max(v for _, v in items) / sm
        score = (0 if "Consolidated" in fx.get(FS, "Consolidated") else 1, top >= 0.9, 1 if ax == "ProductsAndServicesAxis" else 0, -len(items))
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
            mix = segment_groups(res) or segment_groups(res, SEG_PROFIT) or region_groups(res)
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


INSURANCE = ("651", "652")                           # 표준산업분류: 보험업(생명·손해), 재보험


def insurance(dart: Dart, corp_code: str, quarter: List[dict], name: str = "") -> List[dict]:
    """보험사 지표: CSM 잔액·신계약 CSM·CSM 상각액(XBRL 주석), 지급여력비율 K-ICS(공시 본문 표). 분기마다.
    신계약·상각은 3개월 값이고 12월은 연간 − 3분기 누적. 값이 없으면 None"""
    from . import dart_check as dc
    out, cum, kics = [], {}, {}
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

        def pick(acc, start):
            tag, kind, extra = dc.XBRL[acc]
            want = {**base, **extra}
            for f in facts:
                if f["name"] == tag and f["dims"] == want and                         ((kind == "stock" and f.get("instant") == p["end"]) or (kind == "flow" and f.get("start") == start and f.get("end") == p["end"])):
                    return f["value"]
            return None
        row["csm"] = pick("CSM 잔액", None)
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
    if name:
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
            ok = knd in ("보통주", "", "-")                # 보통주가 먼저 나오고 우선주는 뒤에 같은 이름으로 나온다(setdefault 로 첫 줄)
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


def metrics(st: dict, price: Optional[dict]) -> dict:
    """투자 지표(코드 계산). EPS·PER 는 최근 4분기 지배주주 순이익, BPS·PBR 은 최근 분기말 지배주주 자본, 주식 수는 KRX 상장주식수"""
    q = st.get("quarter", [])
    last4 = q[-4:]
    ni4 = sum(p["ni_owner"] for p in last4) if len(last4) == 4 and all(p.get("ni_owner") is not None for p in last4) else None
    eq = q[-1].get("equity_owner") if q else None
    eq_prev = q[-5].get("equity_owner") if len(q) >= 5 else None
    shares = price.get("shares") if price else None
    close = price.get("close") if price else None
    eps = ni4 / shares if ni4 is not None and shares else None
    bps = eq / shares if eq is not None and shares else None
    roe = ni4 / ((eq + eq_prev) / 2) * 100 if ni4 is not None and eq and eq_prev else None
    return {"eps": eps, "bps": bps, "per": close / eps if close and eps and eps > 0 else None,
            "pbr": close / bps if close and bps else None, "roe": roe,
            "basis": {"eps": f"최근 4분기({last4[0]['period']}~{last4[-1]['period']}) 지배주주 순이익 ÷ 상장주식수" if len(last4) == 4 else "",
                      "bps": f"{q[-1]['period']} 지배주주 자본 ÷ 상장주식수" if q else "",
                      "roe": "최근 4분기 지배주주 순이익 ÷ (지금과 1년 전 지배주주 자본의 평균)"}}


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
    px = prices(krx, ticker, days=260, today=today, fetch=fetch_prices)
    if not px:                                       # 받아 둔 시세가 없으면 최근 거래일 하나만 받는다
        px = prices(krx, ticker, days=1, today=today, fetch=True)
    return {
        "made_at": datetime.now().isoformat(timespec="seconds"),
        "ticker": ticker, "name": corp["corp_name"], "info": info,
        "financial": str(info.get("induty_code") or "").startswith(FINANCIAL_INDUSTRY),
        **_price_part(px, st, today),
        "statements": st, "dividends": dividends(dart, corp["corp_code"], today=today),
        "insurance": insurance(dart, corp["corp_code"], st.get("quarter", []), corp["corp_name"]) if str(info.get("induty_code") or "").startswith(INSURANCE) else None,
        "sector": _sector(corp["corp_name"]),
        "sales_mix": sales_mix(dart, corp["corp_code"], today, insurer=str(info.get("induty_code") or "").startswith(INSURANCE)),
        "sources": {"dart": "OpenDART 재무제표·기업개황·배당에 관한 사항", "krx": "KRX 유가증권·코스닥 일별매매정보"},
    }



PEER_CACHE = Path(__file__).resolve().parents[2] / ".cache" / "peers"
PEER_V = 3                                           # 저장 형식이 바뀌면 올린다 (2: 금감원 손익 지표, 3: DART 보험손익·투자손익·배당금수익)


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
    sec = _sector(corp["corp_name"]) if induty.startswith(FINANCIAL_INDUSTRY) and not light else None
    out = {"v": PEER_V, "made_at": datetime.now().isoformat(timespec="seconds"), "ticker": ticker or corp.get("stock_code") or corp["corp_code"], "name": corp["corp_name"],
           "financial": induty.startswith(FINANCIAL_INDUSTRY), "fs": st.get("fs"),
           "annual": st.get("annual", []), "quarter": st.get("quarter", []),
           "insurance": insurance(dart, corp["corp_code"], st.get("quarter", []), corp["corp_name"]) if induty.startswith(INSURANCE) and not light else None,
           "sector": ({k: sec.get(k) for k in ("part", "sector", "company", "month", "metrics", "series")} if sec and not sec.get("error") else None)}
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
    """주가 차트 자료: {'rows': [{'d','o','h','l','c','v'}] 오래된 날부터, 'adjusted': 보정했나, 'first': 첫 날}. 받아 둔 날 + 빠진 최근 날"""
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
    return {"ticker": ticker, "adjusted": any(r.get("adjusted") for r in rows), "first": rows[0]["date"] if rows else None, "old_source": src,
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
    elif not co or co.get("error") or datetime.now() - datetime.fromisoformat(co["made_at"]) > timedelta(days=7):
        co = refresh({"meta": {"ticker": ticker}, "company": None})
    save_atomic(f, json.dumps(co, ensure_ascii=False))
    return co


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


def _price_part(px: List[dict], st: dict, today: Optional[date] = None) -> dict:
    """시세에서 나오는 칸: 최근 거래일, 52주 범위, 투자 지표, 확인 시각"""
    cur = px[0] if px else None
    year = [p for p in px if p["date"] >= ((today or date.today()) - timedelta(days=365)).strftime("%Y%m%d")]
    return {"price": cur,
            "range52": ({"high": max(p["high"] or p["close"] for p in year), "low": min(p["low"] or p["close"] for p in year),
                         "days": len(year)} if len(year) >= 200 else None),
            "price_days": len([p for p in px if p.get("close")]),
            "metrics": metrics(st, cur),
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
    old = not co or co.get("error") or datetime.now() - datetime.fromisoformat(co["made_at"]) > timedelta(days=7) or new_filing(dart, co)
    if old:
        co = build(ticker, dart, krx, fetch_prices=True)
        try:
            from . import peers
            co["peers"] = peers.build(ticker, dart, krx) if peers.INDEX.exists() else None
            fill_peers_from_industry(co)
        except Exception as e:                                       # noqa: BLE001 (경쟁사 비교는 참고라 실패해도 나머지는 그대로)
            co["peers"] = {"error": type(e).__name__}
        return co
    px = prices(krx, ticker, days=260, fetch=True)
    co.update(_price_part(px, co["statements"]))
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
            out = fisis.company_series(argv[1], argv[2]) if len(argv) > 2 else {"error": "업종·회사 코드가 없습니다."}
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
        result["company"] = refresh(result)
        res_path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        sys.stdout.write(json.dumps(result["company"], ensure_ascii=False))
        return 0
    comp = build(ticker, fetch_prices="--fetch-prices" in argv)
    if "--peers" in argv or "peers" in (result.get("company") or {}):
        from . import peers
        comp["peers"] = peers.build(ticker)
        fill_peers_from_industry(comp)
    result["company"] = comp
    res_path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    st = comp.get("statements", {})
    print(f"{comp.get('name')} ({ticker}) · 재무 {st.get('fs')} · 분기 {len(st.get('quarter', []))}개 · 연간 {len(st.get('annual', []))}개 · "
          f"배당 {len(comp.get('dividends', []))}년 · 시세 {comp.get('price_days')}거래일 · 52주 범위 {'있음' if comp.get('range52') else '없음(시세 부족)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
