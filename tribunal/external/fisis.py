"""금감원 금융통계정보시스템(FISIS) Open API (2026-11-22). 금융회사(보험·은행 등)가 감독기관에 내는 업무보고서 통계.
상장사뿐 아니라 비상장 보험사(교보생명, 하나생명 등)까지 같은 기준으로 비교할 수 있다. AI 를 쓰지 않는다.

키: .env 의 FISIS_API_KEY (get_key 로만 읽고 화면·로그에 찍지 않는다)
회사 구분(partDiv): H 생명보험, I 손해보험. 통계표 번호는 이름으로 찾는다(생보 SH…, 손보 SI…).
값은 회사 개별(별도) 기준이고, 분기 말 기준. 공개는 분기 끝나고 두세 달 뒤.
실행: python -m tribunal.external.fisis H     생명보험 업계 표를 만들어 출력
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from ..llm import ROOT, get_key

BASE = "http://fisis.fss.or.kr/openapi/"
CACHE = ROOT / ".cache" / "fisis"
TTL = timedelta(days=7)
KEY_ENV = "FISIS_API_KEY"
PART_NAME = {"H": "생명보험", "I": "손해보험"}
# 전면 개편 1차 (2026-11-25 사용자): 숫자는 DART 만. FISIS 는 금융회사·업권 판단(회사 목록)에만 쓴다.
# True 로 바꾸면 예전처럼 업권 지표·업계 비교·보험 K-ICS/CSM 채우기·비상장 금융회사 비교가 다시 켜진다 (코드는 그대로 둠)
NUMBERS = False
# 사업지표 개편 (2026-11-25 사용자 '켜줘'): 금감원 업권 지표(BIS·NIM·연체율·NCR·K-ICS 등)와 업계 전체 비교만 다시 켠다.
# 화면에서는 '금감원 업권 지표 (개별)' 묶음으로 DART 숫자와 나눠 보인다. 보험 CSM·K-ICS 빈칸 채우기·비상장 금감원 회사 비교는 계속 끔(NUMBERS)
METRICS = True
# 업계 표에 쓰는 통계표 (이름의 앞부분으로 찾는다)
TABLES = {"capital": "자본적정성", "pl": "요약손익계산서(전체(23.3월 이후))", "assets": "요약재무상태표(자산-전체(23.3월 이후))",
          "liab": "요약재무상태표(부채 및 자본(23.3월 이후))", "eff": "경영효율지표"}


class FisisError(RuntimeError):
    """키·연결 문제. 메시지에 키를 넣지 않는다."""


class Fisis:
    def __init__(self, key: Optional[str] = None, cache=CACHE):
        self._key = key
        self.cache = cache
        self.calls = 0

    def get(self, endpoint: str, **params) -> dict:
        tag = json.dumps([endpoint, sorted(params.items())], ensure_ascii=False)
        f = self.cache / f"{endpoint.split('.')[0]}-{hashlib.sha1(tag.encode()).hexdigest()[:16]}.json"
        from .dart import load_json, save_atomic
        saved = load_json(f) if f.exists() else None
        if saved is not None and datetime.now() - datetime.fromisoformat(saved["fetched_at"]) < TTL:
            return saved
        key = self._key or get_key(KEY_ENV)
        url = BASE + endpoint + "?" + urllib.parse.urlencode({"lang": "kr", "auth": key, **params})
        for attempt in range(3):                                           # 연결이 잠깐 끊기는 일이 있어 두 번 더 시도
            try:
                with urllib.request.urlopen(url, timeout=30) as res:      # noqa: S310 (정해진 주소만 부른다)
                    self.calls += 1
                    body = json.loads(res.read().decode("utf-8", "ignore"))
                break
            except (urllib.error.URLError, TimeoutError, ValueError) as e:
                if attempt == 2:
                    raise FisisError(f"FISIS 에 연결하지 못했습니다 ({type(e).__name__}).") from None
                time.sleep(2 * (attempt + 1))
        r = body.get("result") or {}
        if str(r.get("err_cd")) not in ("000", "101"):          # 101: 자료 없음
            raise FisisError(f"FISIS 오류: {str(r.get('err_msg') or '')[:60]}")
        saved = {"list": r.get("list") or [], "err_cd": r.get("err_cd"), "fetched_at": datetime.now().isoformat(timespec="seconds")}
        save_atomic(f, json.dumps(saved, ensure_ascii=False))
        return saved

    def companies(self, part: str) -> List[dict]:
        """영업 중인 회사(이름에 [폐] 없는 것)"""
        return [c for c in self.get("companySearch.json", partDiv=part)["list"] if "[폐]" not in c["finance_nm"]]

    def tables(self, part: str) -> Dict[str, str]:
        """통계표 이름 -> 번호"""
        out = {}
        for sml in "ABCDEF":
            for x in self.get("statisticsListSearch.json", lrgDiv=part, smlDiv=sml)["list"]:
                out[x["list_nm"]] = x["list_no"]
        return out

    def stat(self, finance_cd: str, list_no: str, month: str, term: str = "Q") -> Dict[str, dict]:
        """{계정 이름: 줄}. 줄의 a = 그 분기 값(또는 비율), b = 연초부터 누적 값(손익). term: 'Q' 분기, 'H' 반기(보험 유지율)"""
        rows = self.get("statisticsInfoSearch.json", financeCd=finance_cd, listNo=list_no, term=term,
                        startBaseMm=month, endBaseMm=month)["list"]
        return {r["account_nm"]: r for r in rows}


def _f(v) -> Optional[float]:
    try:
        return float(str(v).replace(",", "")) if v not in (None, "", "-") else None
    except ValueError:
        return None


def _pick(rows: Dict[str, dict], name: str, field: str = "a") -> Optional[float]:
    r = rows.get(name) or next((v for k, v in rows.items() if k.startswith(name)), None)    # 똑같은 이름 먼저 ('BIS기준 자기자본비율' ≠ '…_보통주자본 비율')
    return _f(r.get(field)) if r else None


def short_name(n: str) -> str:
    """'삼성생명보험주식회사' -> '삼성생명', '메트라이프생명보험(주)' -> '메트라이프생명'"""
    n = re.sub(r"\s*(주식회사|\(주\)|㈜)\s*", "", n)
    return re.sub(r"(생명|화재|손해)보험$", r"\1", n).replace("보험", "") if re.search(r"(생명|화재|손해)보험$", n) else n


def latest_month(fs: Fisis, part: str, finance_cd: str, list_no: str, today: Optional[date] = None) -> Optional[str]:
    """그 회사 통계가 있는 가장 최근 분기 말(YYYYMM)"""
    d = today or date.today()
    y, m = d.year, (d.month - 1) // 3 * 3
    for _ in range(8):
        if m == 0:
            y, m = y - 1, 12
        month = f"{y}{m:02d}"
        if fs.stat(finance_cd, list_no, month):
            return month
        m -= 3
    return None


def build(part: str, me_name: str = "", fs: Optional[Fisis] = None, today: Optional[date] = None) -> dict:
    """업계 전체 표: 회사마다 지급여력비율, 손익(누적), 총자산·자본, 경영효율 지표"""
    fs = fs or Fisis()
    names = fs.tables(part)
    no = {k: next((v for n, v in names.items() if n.startswith(t)), None) for k, t in TABLES.items()}
    comps = fs.companies(part)
    if not comps or not no["capital"]:
        return {"error": "FISIS 에서 회사나 통계표를 찾지 못했습니다."}
    me = next((c for c in comps if me_name and short_name(c["finance_nm"]).startswith(me_name[:2]) and me_name in c["finance_nm"]), None) \
        or next((c for c in comps if me_name and short_name(c["finance_nm"]) == me_name), None)
    month = latest_month(fs, part, (me or comps[0])["finance_cd"], no["capital"], today)
    if not month:
        return {"error": "FISIS 에 최근 통계가 없습니다."}
    rows = []
    for c in comps:
        cd = c["finance_cd"]
        cap = fs.stat(cd, no["capital"], month)
        if not cap:                                    # 그 분기 통계를 내지 않은 회사(새로 생기거나 합병 중)는 뺀다
            continue
        pl = fs.stat(cd, no["pl"], month) if no["pl"] else {}
        asset = fs.stat(cd, no["assets"], month) if no["assets"] else {}
        liab = fs.stat(cd, no["liab"], month) if no["liab"] else {}
        eff = fs.stat(cd, no["eff"], month) if no["eff"] else {}
        before = _pick(cap, "지급여력비율(경과조치 적용 전)")
        after = _pick(cap, "지급여력비율(경과조치 적용 후)")
        rows.append({
            "finance_cd": cd, "name": short_name(c["finance_nm"]), "full_name": c["finance_nm"], "me": me is not None and cd == me["finance_cd"],
            "kics": before if before else after, "kics_basis": "경과조치 적용 전" if before else ("경과조치 적용 후" if after else ""),
            "kics_after": after or None,
            "assets": _pick(asset, "자산총계"), "equity": _pick(liab, "자본총계"),
            "ins_profit": _pick(pl, "보험손익", "b"), "inv_profit": _pick(pl, "투자손익", "b"), "ni": _pick(pl, "당기순이익", "b"),
            "op_margin": _pick(eff, "영업이익률"), "inv_yield": _pick(eff, "운용자산이익율") or _pick(eff, "운용자산이익률"), "new_rate": _pick(eff, "신계약율"),
            "lapse_rate": _pick(eff, "효력상실해약율"), "roa": _pick(eff, "총자산순이익률"),
        })
    return {"made_at": datetime.now().isoformat(timespec="seconds"), "part": part, "part_name": PART_NAME.get(part, part),
            "month": month, "rows": rows, "source": "금융감독원 금융통계정보시스템(FISIS) Open API", "basis": "회사 개별(별도) 기준, 업무보고서",
            "calls": fs.calls}



# ---------- 업권별 사업 지표 (2026-11-24 사용자 확정, 10개 업권). 표 이름(앞부분)과 항목 이름으로 찾는다 ----------
# 무리(group): '사업 지표' / '이익 구성'. 단위: '%' 비율, '원' 흐름 금액(a = 그 분기, b = 연초부터 누적), '잔액' 시점 금액.
# term: 'Q' 분기마다, 'H' 반기마다만 공시(보험 유지율). ROE·ROA 는 모든 회사 같은 식으로 DART 숫자로 계산하므로 여기 넣지 않는다.
def _m(key: str, label: str, table: str, item: str, unit: str = "%", group: str = "사업 지표", term: str = "Q", desc: str = "") -> dict:
    return {"key": key, "label": label, "table": table, "item": item, "unit": unit, "group": group, "term": term, "desc": desc}


_BIS = "자기자본 ÷ 위험가중자산"
_CET1 = "보통주자본 ÷ 위험가중자산"
_NPL = "고정·회수의문·추정손실 여신 ÷ 전체 여신"
_ADJ = "조정자기자본 ÷ 조정총자산"
_SIMPLE = "자기자본 ÷ 총자산"
_KEEP = "보험료를 {}개월째까지 계속 낸 계약의 비율 (반기마다 공시)"
_USE = "카드 영업활동(신용카드이용실적)"
_FEE = "부문별 손익(수수료)"
_PLG = "요약손익계산서(11.06월 이후)"
SECTORS: Dict[str, dict] = {
    "A": {"name": "은행", "metrics": [
        _m("bis", "BIS 자기자본비율", "자본적정성", "BIS기준 자기자본비율", desc=_BIS),
        _m("cet1", "보통주자본비율(CET1)", "자본적정성", "보통주자본비율", desc=_CET1),
        _m("nim", "순이자마진(NIM)", "수익성", "명목순이자마진(NIM)", desc="순이자이익 ÷ 이자 받는 자산"),
        _m("delinq", "연체율", "연체율(원화대출금 및 신탁대출금 합계 기준)", "총대출채권_연체율", desc="1개월 이상 연체 대출 ÷ 전체 대출"),
        _m("npl", "고정이하여신비율", "여신건전성(여신건전성)", "고정이하여신비율", desc=_NPL),
        _m("lcr", "유동성커버리지비율(LCR)", "유동성(유동성)", "유동성커버리지비율", desc="바로 팔 수 있는 자산 ÷ 30일 동안 빠져나갈 돈"),
        # 사업지표 개편 추가 (2026-11-25 사용자 '굵은 것')
        _m("spread", "원화예대금리차", "수익성", "원화예대금리차", desc="원화대출 평균금리 − 원화예수금 평균금리"),
        # 금감원 수익성 표는 분기마다 1년 기준 값(분기 값이 아님, 2026-11-25 확인: 기업은행 26.3월·6월·25.12월 모두 4.9조) → 잔액처럼 그 시점 값으로
        _m("ppop", "충당금적립전이익(1년 기준)", "수익성", "충당금적립전이익(퇴직급여 제외)", "잔액", "이익 구성",
           desc="대손충당금을 쌓기 전 이익 (퇴직급여 제외). 금감원 수익성 표의 1년 기준 값"),
        _m("cover", "대손충당금 적립비율", "여신건전성(대손충당금 적립비율)", "대손충당금적립비율(고정이하여신대비)", desc="쌓아 둔 대손충당금 ÷ 고정이하여신"),
        _m("dep_krw", "원화예수금", "형태별 예수금", "원화예수금", "잔액", desc="원화 예금 잔액 (요구불·저축성 등)"),
        _m("nii", "이자순수익", "부문별 손익(이자부문)", "이자순수익", "원", "이익 구성", desc="이자수익 − 이자비용"),
        _m("fee_net", "수수료순수익", "부문별 손익(수수료부문)", "수수료순수익", "원", "이익 구성", desc="수수료수익 − 수수료비용")]},
    "L": {"name": "금융지주", "metrics": [
        _m("bis", "BIS 자기자본비율", "자본적정성", "BIS기준 자기자본비율", desc=_BIS),
        _m("cet1", "보통주자본비율(CET1)", "자본적정성", "BIS기준 자기자본비율_보통주자본 비율", desc=_CET1),
        _m("req", "필요자본 대비 자기자본비율", "자본적정성", "필요자본에 대한 자기자본비율", desc="자기자본 ÷ 필요자본 (은행이 없는 금융지주)"),
        _m("npl", "고정이하여신비율", "여신건전성", "(고정이하여신비율)", desc=_NPL),
        _m("cover", "대손충당금 적립비율", "여신건전성", "(대손충당금등 적립비율(고정이하여신대비))", desc="쌓아 둔 대손충당금 ÷ 고정이하여신"),
        _m("dlev", "이중레버리지비율", "재무구조 안정성", "이중레버리지비율", desc="자회사 출자 총액 ÷ 지주회사 자기자본"),
        # 부문별 손익현황 표는 지주회사 혼자(개별) 숫자라 그룹 이자이익이 아니다(KB 26.6월 −308억) → 요약연결손익계산서로 계산
        _m("nii", "이자이익(연결)", "요약연결손익계산서", "이자수익 − 이자비용", "원", "이익 구성", desc="그룹 연결 이자수익 − 이자비용 (금감원 표 값으로 계산)"),
        _m("fee_net", "수수료이익(연결)", "요약연결손익계산서", "수수료수익 − 지급수수료", "원", "이익 구성", desc="그룹 연결 수수료수익 − 지급수수료 (금감원 표 값으로 계산)")]},
    "F": {"name": "증권", "metrics": [
        _m("ncr", "순자본비율(NCR)", "자본적정성(개별기준 순자본비율", "순자본비율", desc="(영업용순자본 − 총위험액) ÷ 필요유지자기자본"),
        _m("lev", "레버리지비율", "자본적정성(레버리지 비율)", "레버리지비율", desc="총자산 ÷ 자기자본"),
        _m("fee_broker", "수탁수수료(위탁매매)", _FEE, "수수료수익_수탁수수료", "원", "이익 구성", desc="주식 등 매매를 중개하고 받은 수수료"),
        _m("fee_ib", "인수·주선 수수료(IB)", _FEE, "수수료수익_인수 및 주선수수료", "원", "이익 구성", desc="주식·채권 발행을 맡아 받은 수수료"),
        _m("fee_net", "수수료손익", _FEE, "수수료손익", "원", "이익 구성", desc="수수료수익 − 수수료비용"),
        _m("fee_wm", "자산관리수수료", _FEE, "수수료수익_자산관리수수료", "원", "이익 구성", desc="랩어카운트 등 고객 자산을 맡아 관리하고 받은 수수료"),
        _m("fee_ma", "매수·합병 수수료(M&A)", _FEE, "수수료수익_매수및합병수수료", "원", "이익 구성", desc="기업 인수·합병 자문으로 받은 수수료"),
        _m("cust_dep", "투자자예수금", "부채현황(예수부채)", "투자자예수금", "잔액", desc="고객이 주식 매매 등을 위해 맡겨 둔 돈")]},
    "C": {"name": "카드", "metrics": [
        _m("adj", "조정자기자본비율", "자본적정성", "조정자기자본비율", desc=_ADJ),
        _m("simple", "단순자기자본비율", "자본적정성", "단순자기자본비율", desc=_SIMPLE),
        _m("delinq", "연체채권비율(1개월 이상)", "여신건전성(여신건전성(연체채권비율))", "연체채권비율(1개월 이상)", desc="1개월 이상 연체 채권 ÷ 전체 채권"),
        _m("use_total", "신용카드 이용실적", _USE, "총계_이용실적", "원", desc="일시불·할부·현금서비스·카드론 이용 금액 합계"),
        _m("use_lump", "이용실적: 일시불", _USE, "총계_일시불", "원"),
        _m("use_inst", "이용실적: 할부", _USE, "총계_할부", "원"),
        _m("use_cash", "이용실적: 현금서비스", _USE, "총계_현금서비스", "원"),
        _m("use_loan", "이용실적: 카드론", _USE, "총계_카드론", "원"),
        _m("card_pl", "카드손익", "부문별 손익(카드부문)", "카드손익", "원", "이익 구성", desc="카드수익 − 카드비용"),
        _m("merchant", "가맹점수수료", "부문별 손익(카드부문)", "카드수익_가맹점수수료", "원", "이익 구성", desc="가맹점에서 받은 결제 수수료"),
        _m("acq_cost", "모집비용", "부문별 손익(카드부문)", "카드비용_모집비용", "원", "이익 구성", desc="회원을 모으는 데 쓴 비용"),
        _m("card_asset", "카드자산", "부문별 자산현황(카드자산)", "카드자산", "잔액", desc="일시불·할부·현금서비스·카드론 채권 잔액"),
        _m("loan_bal", "카드론 잔액", "부문별 자산현황(카드자산)", "카드자산_카드론", "잔액"),
        _m("short_fund", "단기조달비중", "유동성", "단기조달비중", desc="1년 안에 갚아야 할 조달 ÷ 전체 조달")]},
    "K": {"name": "캐피탈", "metrics": [
        _m("adj", "조정자기자본비율", "자본적정성", "조정자기자본비율", desc=_ADJ),
        _m("simple", "단순자기자본비율", "자본적정성", "단순자기자본비율", desc=_SIMPLE),
        _m("delinq", "연체채권비율", "여신건전성(여신건전성(연체채권비율))", "연체채권비율", desc="연체 채권 ÷ 전체 채권"),
        _m("inst_pl", "할부금융 부문 손익", "부문별 손익(할부금융부문)", "할부금융부문 손익", "원", "이익 구성", desc="자동차 등 할부금융에서 낸 손익"),
        _m("lease_pl", "리스 부문 손익", "부문별 손익(리스부문)", "리스부문 손익", "원", "이익 구성", desc="리스·렌탈에서 낸 손익"),
        _m("ntf_pl", "신기술금융 부문 손익", "부문별 손익(신기술금융부문)", "신기술금융부문 손익", "원", "이익 구성", desc="벤처 투자 등 신기술금융에서 낸 손익"),
        _m("nii", "이자손익", "부문별 손익(이자부문)", "이자손익", "원", "이익 구성",
           desc="이자수익 − 이자비용. 할부·리스 수익은 각 부문 손익에 들어가 이자부문은 조달 비용 때문에 마이너스일 수 있음"),
        _m("short_fund", "단기조달비중", "유동성", "단기조달비중", desc="1년 안에 갚아야 할 조달 ÷ 전체 조달")]},
    "H": {"name": "생명보험", "metrics": [
        _m("kics", "K-ICS", "자본적정성", "지급여력비율(경과조치 적용 전)", desc="가용자본 ÷ 요구자본 (경과조치 적용 전)"),
        _m("inv_yield", "운용자산이익률", "경영효율지표", "운용자산이익율", desc="투자 이익 ÷ 평균 운용자산"),
        _m("new_rate", "신계약률", "경영효율지표", "신계약율", desc="신계약 금액 ÷ 연초 보유계약 (연초부터 누적)"),
        _m("lapse", "효력상실해약률", "경영효율지표", "효력상실해약율", desc="해약·효력상실 금액 ÷ (연초 보유계약 + 신계약)"),
        _m("keep13", "13회차 유지율", "보험계약 유지율", "13회차 계약유지율", term="H", desc=_KEEP.format(13)),
        _m("keep25", "25회차 유지율", "보험계약 유지율", "25회차 계약유지율", term="H", desc=_KEEP.format(25)),
        _m("ins_pl", "보험손익", "손익발생원천별 실적(23.3월 이후)", "보험손익", "원", "이익 구성", desc="보험수익 − 보험서비스비용 등 (금감원 손익발생원천)"),
        _m("inv_pl", "투자손익", "손익발생원천별 실적(23.3월 이후)", "투자손익", "원", "이익 구성", desc="투자수익 − 투자비용"),
        _m("avail", "지급여력금액", "자본적정성", "지급여력금액(경과조치 적용 전)", "잔액", desc="K-ICS 가용자본 (경과조치 적용 전)")]},
    "E": {"name": "저축은행", "metrics": [
        _m("bis", "BIS 자기자본비율", "자본적정성(자본적정성지표)", "BIS기준 자기자본비율", desc=_BIS),
        _m("delinq", "연체율", "자산건전성", "연체율", desc="연체 대출 ÷ 전체 대출"),
        _m("npl", "고정이하여신비율", "여신건전성", "고정이하여신비율", desc=_NPL),
        _m("nii", "이자 순수익", "부문별 손익현황(이자부문)", "이자 순수익", "원", "이익 구성", desc="이자수익 − 이자비용"),
        _m("micro", "소액신용대출 잔액", "대출금 운용(소액신용대출 취급현황)", "소액신용대출", "잔액", desc="300만 원 이하 신용대출 잔액"),
        _m("micro_dq", "소액신용대출 연체액", "대출금 운용(소액신용대출 취급현황)", "소액신용대출연체액", "잔액"),
        _m("loan_re", "부동산업 대출", "대출금 운용(업종별 기업대출금)", "부동산업", "잔액", desc="부동산업 기업대출 잔액 (PF 위험을 볼 때)"),
        _m("loan_con", "건설업 대출", "대출금 운용(업종별 기업대출금)", "건설업", "잔액", desc="건설업 기업대출 잔액")]},
    "M": {"name": "부동산신탁", "metrics": [
        _m("ncr", "영업용순자본비율", "자본적정성(영업용순자본비율)", "영업용순자본비율", desc="영업용순자본 ÷ 총위험액"),
        _m("trust", "수탁고(신탁계정 자산)", "요약재무상태표(신탁계정)", "자산총계", "잔액", desc="맡아 관리하는 신탁재산 (신탁계정 자산총계)"),
        _m("fee_net", "수수료손익", "부문별 손익(수수료)", "수수료손익", "원", "이익 구성", desc="수수료수익 − 수수료비용"),
        _m("trust_fee", "신탁보수", "부문별 손익(수수료)", "신탁보수", "원", "이익 구성", desc="신탁을 맡아 받은 보수 (토지·담보·분양관리신탁 등 합계)"),
        _m("trust_land", "신탁보수: 토지신탁", "부문별 손익(수수료)", "토지신탁", "원", "이익 구성", desc="개발 사업을 맡는 토지신탁(차입형·관리형) 보수"),
        _m("trust_coll", "신탁보수: 담보신탁", "부문별 손익(수수료)", "담보신탁", "원", "이익 구성"),
        _m("trust_loan", "신탁계정대", "자산현황(대출채권)", "신탁계정대", "잔액", desc="신탁 사업에 회사 돈을 빌려준 잔액 (차입형 토지신탁 위험)")]},
    "G": {"name": "자산운용", "metrics": [
        _m("discr", "투자일임 계약자산(국내)", "투자일임 현황(투자일임 재산 현황)", "국내 계약자산", "잔액", desc="고객이 맡긴 투자일임 자산"),
        _m("discr_f", "투자일임 계약자산(해외)", "투자일임 현황(투자일임 재산 현황)", "해외 계약자산", "잔액"),
        _m("op_rev", "영업수익", _PLG, "영업수익", "원", "이익 구성"),
        _m("fee_fund", "운용보수", _FEE, "집합투자기구운용보수", "원", "이익 구성", desc="펀드를 운용하고 받은 보수"),
        _m("fee_mgmt", "자산관리수수료(일임·자문)", _FEE, "자산관리수수료", "원", "이익 구성", desc="투자일임·자문 계약에서 받은 수수료"),
        _m("sga", "판매비와 관리비", _PLG, "판매비와 관리비", "원", "이익 구성"),
        _m("op", "영업이익", _PLG, "영업이익", "원", "이익 구성"),
        _m("fee_net", "수수료손익", _FEE, "수수료손익", "원", "이익 구성", desc="수수료수익 − 수수료비용"),
        _m("discr_cust", "투자일임 고객 수", "투자일임 현황(투자일임 계약 현황)", "고객수", "수", desc="투자일임 계약을 맺은 고객 수"),
        _m("discr_n", "투자일임 계약 건수", "투자일임 현황(투자일임 계약 현황)", "일임계약 건수", "수")]},
}
# 손해보험: 표 이름은 생보와 같고 운용자산이익률 항목 이름만 다르다. 신계약률·효력상실해약률은 없고 손해율은 22.12월까지만이라 넣지 않는다
SECTORS["I"] = {"name": "손해보험", "metrics": [
    {**x, "item": "운용자산이익률"} if x["key"] == "inv_yield" else x for x in SECTORS["H"]["metrics"] if x["key"] not in ("new_rate", "lapse")]}
# 붙이기 (2026-11-24 사용자): 할부·신기술금융 = 캐피탈 목록, 선물 = 증권 목록
SECTORS["T"] = {**SECTORS["K"]}
SECTORS["N"] = {**SECTORS["K"]}
SECTORS["W"] = {**SECTORS["F"], "name": "증권(선물)"}
EXACT_ONLY = ("E", "M", "G", "W", "N")              # 회사가 수백 곳인 업권은 이름이 똑같을 때만 (다른 회사로 잘못 찾지 않게)
# DART 이름과 금감원 이름이 다른 회사 (한글 표기, 줄임)
ALIAS = {"신한지주": "신한금융지주", "한국금융지주": "한국투자금융지주", "기업은행": "중소기업은행", "iM금융지주": "디지비금융지주",
         "DGB금융지주": "디지비금융지주", "제주은행": "제주은행", "케이뱅크": "케이뱅크"}
SPELL = {"케이비": "KB", "엔에이치": "NH", "비엔케이": "BNK", "디지비": "DGB", "제이비": "JB", "아이비케이": "IBK", "아이엠": "iM",
         "에스케이": "SK", "케이티": "KT", "엘지": "LG", "지에스": "GS", "디비": "DB"}


def norm(n: str) -> str:
    n = re.sub(r"\s|주식회사|\(주\)|㈜|보험|회사$", "", n or "")
    for k, v in SPELL.items():
        n = n.replace(k, v)
    return n.upper()


def find_company(fs: "Fisis", name: str) -> Optional[dict]:
    """DART 회사 이름으로 금감원 회사를 찾는다. {finance_cd, finance_nm, part} 또는 None"""
    want = norm(ALIAS.get(name, name))
    for part in SECTORS:
        try:
            comps = fs.companies(part)
        except FisisError:
            continue
        exact = [c for c in comps if norm(c["finance_nm"]) == want]
        pre = [] if part in EXACT_ONLY else [c for c in comps if norm(c["finance_nm"]).startswith(want) or (len(want) >= 4 and want.startswith(norm(c["finance_nm"])))]
        hit = (exact or pre or [None])[0]
        if hit:
            return {**hit, "part": part}
    return None


def _metric_values(fs: "Fisis", names: Dict[str, str], cd: str, part: str, month: str) -> Dict[str, Optional[float]]:
    out, cache = {}, {}
    for m in SECTORS[part]["metrics"]:
        key, unit = m["key"], m["unit"]
        no = next((v for n, v in names.items() if n.startswith(m["table"])), None)
        if not no or (m["term"] == "H" and month[4:] not in ("06", "12")):
            out[key] = None
            continue
        rows = cache.setdefault((no, m["term"]), fs.stat(cd, no, month, m["term"]))
        if " − " in m["item"]:                            # 'A − B': 같은 표 두 항목의 차 (금융지주 연결 이자이익 등)
            a_, b_ = m["item"].split(" − ")
            sub = lambda f: (lambda x, y: x - y if x is not None and y is not None else None)(_pick(rows, a_, f), _pick(rows, b_, f))
            out[key], out[f"{key}_ytd"] = sub("a"), sub("b")
            continue
        v = _pick(rows, m["item"])
        if unit == "원":                                   # 흐름 금액: 그 분기 값과 연초부터 누적 (연간 화면은 12월 누적)
            out[key] = v
            out[f"{key}_ytd"] = _pick(rows, m["item"], "b")
            continue
        out[key] = v if v not in (None, 0.0) or "연체" in m["item"] else None
    return out


def metric_list(part: str) -> List[dict]:
    """화면에 넘길 지표 정의"""
    return [{k: m[k] for k in ("key", "label", "group", "unit", "term", "desc")} for m in SECTORS[part]["metrics"]]


def months_back(n: int, today: Optional[date] = None) -> List[str]:
    d = today or date.today()
    y, m = d.year, (d.month - 1) // 3 * 3
    out = []
    while len(out) < n:
        if m == 0:
            y, m = y - 1, 12
        out.append(f"{y}{m:02d}")
        m -= 3
    return out[::-1]


def sector(name: str, fs: Optional["Fisis"] = None, quarters: int = 40, today: Optional[date] = None) -> Optional[dict]:
    """회사 하나의 업종 지표 분기 추이 + 같은 업종 전체의 최근 분기 비교. 금감원에 없는 회사(비금융)는 None"""
    fs = fs or Fisis()
    comp = find_company(fs, name)
    if not comp:
        return None
    part = comp["part"]
    if not (NUMBERS or METRICS):                      # 업권 판단만: 숫자는 받지 않는다
        return {"made_at": datetime.now().isoformat(timespec="seconds"), "part": part, "sector": SECTORS[part]["name"],
                "company": comp["finance_nm"], "month": None, "metrics": [], "series": [], "peers": []}
    names = fs.tables(part)
    series = []
    for month in months_back(quarters, today):
        vals = _metric_values(fs, names, comp["finance_cd"], part, month)
        if any(v is not None for v in vals.values()):
            series.append({"period": f"{month[:4]}.{month[4:]}", **vals})
    latest = series[-1]["period"].replace(".", "") if series else None
    peers = []
    comps = fs.companies(part)
    if latest and len(comps) > 150:                   # 자산운용(500곳 넘음): 업계 전체 값은 받지 않고 비교 회사 후보 이름만
        peers = [{"finance_cd": c["finance_cd"], "name": short_name(c["finance_nm"]).strip(), "full_name": c["finance_nm"].strip(),
                  "me": c["finance_cd"] == comp["finance_cd"]} for c in comps]
    elif latest:
        for c in comps:
            vals = _metric_values(fs, names, c["finance_cd"], part, latest)
            if any(v is not None for v in vals.values()):
                peers.append({"finance_cd": c["finance_cd"], "name": short_name(c["finance_nm"]).strip(), "full_name": c["finance_nm"].strip(),
                              "me": c["finance_cd"] == comp["finance_cd"], **vals})
    return {"made_at": datetime.now().isoformat(timespec="seconds"), "part": part, "sector": SECTORS[part]["name"],
            "company": comp["finance_nm"], "month": latest,
            "metrics": metric_list(part), "series": series, "peers": peers}

def company_series(part: str, finance_cd: str, fs: Optional["Fisis"] = None, quarters: int = 40, today: Optional[date] = None) -> dict:
    """동종사 추이 비교용 (2026-11-22): 금감원에 있는 회사 하나(비상장 포함)의 업종 지표 분기 추이.
    보험사(생보 H, 손보 I)는 CSM 잔액도 (책임준비금 표의 보험계약마진). 회사 개별 기준"""
    fs = fs or Fisis()
    if part not in SECTORS:
        return {"error": "지원하지 않는 업종입니다."}
    comp = next((c for c in fs.companies(part) if c["finance_cd"] == finance_cd), None)
    if not comp:
        return {"error": "금감원 자료에서 회사를 찾지 못했습니다."}
    names = fs.tables(part)
    res = next((v for n, v in names.items() if n.startswith("책임준비금 적립현황(23.3월 이후)")), None) if part in ("H", "I") else None
    series, fq = [], []
    no = {k: next((v for n, v in names.items() if n.startswith(t)), None) for k, t in TABLES.items()} if part in ("H", "I") else {}
    for month in months_back(quarters, today):
        vals = _metric_values(fs, names, finance_cd, part, month)
        if res:
            vals["csm"] = _pick(fs.stat(finance_cd, res, month), "책임준비금_보험계약부채_잔여보장요소_보험계약마진")
        if any(v is not None for v in vals.values()):
            series.append({"period": f"{month[:4]}.{month[4:]}", **vals})
        if no.get("pl"):                                   # 보험사 재무 요약 (DART 정기보고서가 없는 회사용): a = 그 분기, b = 연초부터 누적
            pl, ast, lb = fs.stat(finance_cd, no["pl"], month), fs.stat(finance_cd, no["assets"], month), fs.stat(finance_cd, no["liab"], month)
            row = {"period": f"{month[:4]}.{month[4:]}", "assets": _pick(ast, "자산총계"), "liab": _pick(lb, "부채총계"), "equity": _pick(lb, "자본총계"),
                   "ni": _pick(pl, "당기순이익"), "op": _pick(pl, "영업이익"), "pretax": _pick(pl, "법인세비용차감전순이익"),
                   "ni_ytd": _pick(pl, "당기순이익", "b"), "op_ytd": _pick(pl, "영업이익", "b"), "pretax_ytd": _pick(pl, "법인세비용차감전순이익", "b"),
                   "derived": []}
            if any(row[k] is not None for k in ("assets", "equity", "ni")):
                fq.append(row)
    fa = [{"period": r["period"][:4], "assets": r["assets"], "liab": r["liab"], "equity": r["equity"],
           "ni": r["ni_ytd"], "op": r["op_ytd"], "pretax": r["pretax_ytd"], "derived": []} for r in fq if r["period"].endswith(".12")]
    fq = [{k: v for k, v in r.items() if not k.endswith("_ytd")} for r in fq]
    return {"made_at": datetime.now().isoformat(timespec="seconds"), "finance_cd": finance_cd, "part": part,
            "name": short_name(comp["finance_nm"]).strip(), "full_name": comp["finance_nm"].strip(), "sector": SECTORS[part]["name"],
            "metrics": metric_list(part), "series": series, "fs_quarter": fq, "fs_annual": fa}


if __name__ == "__main__":
    out = build(sys.argv[1] if len(sys.argv) > 1 else "H", sys.argv[2] if len(sys.argv) > 2 else "")
    print(json.dumps({k: v for k, v in out.items() if k != "rows"}, ensure_ascii=False))
    for r in out.get("rows", []):
        print(f"  {r['name']:<14} K-ICS {r['kics']} 자산 {round((r['assets'] or 0) / 1e12, 1)}조 순이익 {round((r['ni'] or 0) / 1e8)}억 운용수익률 {r['inv_yield']} {'<-' if r['me'] else ''}")
