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
        try:
            with urllib.request.urlopen(url, timeout=30) as res:          # noqa: S310 (정해진 주소만 부른다)
                self.calls += 1
                body = json.loads(res.read().decode("utf-8", "ignore"))
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            raise FisisError(f"FISIS 에 연결하지 못했습니다 ({type(e).__name__}).") from None
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

    def stat(self, finance_cd: str, list_no: str, month: str) -> Dict[str, dict]:
        """{계정 이름: 줄}. 줄의 a = 그 분기 값(또는 비율), b = 연초부터 누적 값(손익)"""
        rows = self.get("statisticsInfoSearch.json", financeCd=finance_cd, listNo=list_no, term="Q",
                        startBaseMm=month, endBaseMm=month)["list"]
        return {r["account_nm"]: r for r in rows}


def _f(v) -> Optional[float]:
    try:
        return float(str(v).replace(",", "")) if v not in (None, "", "-") else None
    except ValueError:
        return None


def _pick(rows: Dict[str, dict], name: str, field: str = "a") -> Optional[float]:
    r = next((v for k, v in rows.items() if k == name or k.startswith(name)), None)
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
            "op_margin": _pick(eff, "영업이익률"), "inv_yield": _pick(eff, "운용자산이익율"), "new_rate": _pick(eff, "신계약율"),
            "lapse_rate": _pick(eff, "효력상실해약율"), "roa": _pick(eff, "총자산순이익률"),
        })
    return {"made_at": datetime.now().isoformat(timespec="seconds"), "part": part, "part_name": PART_NAME.get(part, part),
            "month": month, "rows": rows, "source": "금융감독원 금융통계정보시스템(FISIS) Open API", "basis": "회사 개별(별도) 기준, 업무보고서",
            "calls": fs.calls}



# ---------- 업종별 지표 (2026-11-22): 금융 업종마다 금감원이 보는 핵심 지표. 표 이름(앞부분)과 항목 이름(앞부분)으로 찾는다 ----------
# (키, 이름, 무리, 통계표, 항목, 단위). 무리: 안정성 / 수익성 / 건전성 / 유동성 / 성장성
SECTORS: Dict[str, dict] = {
    "A": {"name": "은행", "metrics": [
        ("bis", "BIS 자기자본비율", "안정성", "자본적정성", "BIS기준 자기자본비율", "%"),
        ("cet1", "보통주자본비율", "안정성", "자본적정성", "보통주자본비율", "%"),
        ("nim", "순이자마진(NIM)", "수익성", "수익성", "명목순이자마진(NIM)", "%"),
        ("roe", "ROE", "수익성", "수익성", "자기자본순이익률(ROE)", "%"),
        ("roa", "ROA", "수익성", "수익성", "총자산순이익률(ROA)", "%"),
        ("delinq", "연체율", "건전성", "연체율(원화대출금 및 신탁대출금 합계 기준)", "총대출채권_연체율", "%"),
        ("lcr", "유동성커버리지비율", "유동성", "유동성(유동성)", "유동성커버리지비율", "%")]},
    "L": {"name": "금융지주", "metrics": [
        ("bis", "BIS 자기자본비율", "안정성", "자본적정성", "BIS기준 자기자본비율", "%"),
        ("roe", "ROE", "수익성", "수익성", "자기자본순이익률(ROE)", "%"),
        ("roa", "ROA", "수익성", "수익성", "총자산순이익률(ROA)", "%"),
        ("npl", "고정이하여신비율", "건전성", "여신건전성", "(고정이하여신비율)", "%"),
        ("cover", "대손충당금 적립비율", "건전성", "여신건전성", "(대손충당금등 적립비율(고정이하여신대비))", "%")]},
    "F": {"name": "증권", "metrics": [
        ("ncr", "순자본비율", "안정성", "자본적정성(개별기준 순자본비율", "순자본비율", "%"),
        ("lev", "레버리지비율", "안정성", "자본적정성(레버리지 비율)", "레버리지비율", "%"),
        ("roe", "ROE", "수익성", "수익성(09.06월 이후)", "자기자본이익률", "%")]},
    "C": {"name": "카드", "metrics": [
        ("adj", "조정자기자본비율", "안정성", "자본적정성", "조정자기자본비율", "%"),
        ("roe", "ROE", "수익성", "수익성", "자기자본순이익률(ROE)", "%"),
        ("roa", "ROA", "수익성", "수익성", "총자산순이익률(ROA)", "%"),
        ("delinq", "연체채권비율(1개월 이상)", "건전성", "여신건전성(여신건전성(연체채권비율))", "연체채권비율(1개월 이상)", "%")]},
    "K": {"name": "캐피탈(리스)", "metrics": [
        ("adj", "조정자기자본비율", "안정성", "자본적정성", "조정자기자본비율", "%"),
        ("roe", "ROE", "수익성", "수익성", "자기자본순이익률(ROE)", "%"),
        ("roa", "ROA", "수익성", "수익성", "총자산순이익률(ROA)", "%"),
        ("delinq", "연체채권비율", "건전성", "여신건전성(여신건전성(연체채권비율))", "연체채권비율", "%")]},
    "H": {"name": "생명보험", "metrics": [
        ("kics", "K-ICS", "안정성", "자본적정성", "지급여력비율(경과조치 적용 전)", "%"),
        ("roa", "ROA", "수익성", "경영효율지표", "총자산순이익률", "%"),
        ("inv_yield", "운용자산이익률", "수익성", "경영효율지표", "운용자산이익율", "%"),
        ("new_rate", "신계약률", "성장성", "경영효율지표", "신계약율", "%"),
        ("lapse", "효력상실해약률", "건전성", "경영효율지표", "효력상실해약율", "%"),
        # 손익 (2026-11-23): 요약손익계산서. 분기 값(a)과 연초부터 누적(b, '<키>_ytd')
        ("ins_profit", "보험손익", "손익", "요약손익계산서(전체(23.3월 이후))", "보험손익", "원"),
        ("inv_profit", "투자손익", "손익", "요약손익계산서(전체(23.3월 이후))", "투자손익", "원"),
        ("ins_rev", "보험수익", "손익", "요약손익계산서(전체(23.3월 이후))", "보험손익_보험수익", "원"),
        ("inv_rev", "투자수익", "손익", "요약손익계산서(전체(23.3월 이후))", "투자손익_투자수익", "원"),
        ("int_rev", "이자수익", "손익", "요약손익계산서(전체(23.3월 이후))", "투자손익_투자수익_이자수익", "원"),
        ("div_rev", "배당수익", "손익", "요약손익계산서(전체(23.3월 이후))", "투자손익_투자수익_배당수익", "원")]},
}
SECTORS["T"] = {**SECTORS["K"], "name": "캐피탈(할부금융)"}
SECTORS["I"] = {"name": "손해보험", "metrics": SECTORS["H"]["metrics"]}
# DART 이름과 금감원 이름이 다른 회사 (한글 표기, 줄임)
ALIAS = {"신한지주": "신한금융지주", "기업은행": "중소기업은행", "iM금융지주": "디지비금융지주",
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
        pre = [c for c in comps if norm(c["finance_nm"]).startswith(want) or (len(want) >= 4 and want.startswith(norm(c["finance_nm"])))]
        hit = (exact or pre or [None])[0]
        if hit:
            return {**hit, "part": part}
    return None


def _metric_values(fs: "Fisis", names: Dict[str, str], cd: str, part: str, month: str) -> Dict[str, Optional[float]]:
    out, cache = {}, {}
    for key, _, _, table, item, unit in SECTORS[part]["metrics"]:
        no = next((v for n, v in names.items() if n.startswith(table)), None)
        if not no:
            out[key] = None
            continue
        rows = cache.setdefault(no, fs.stat(cd, no, month))
        v = _pick(rows, item)
        if unit == "원":                                   # 손익 금액: 0 도 값, 연초부터 누적도 같이 (연간 화면은 12월 누적)
            out[key] = v
            out[f"{key}_ytd"] = _pick(rows, item, "b")
            continue
        out[key] = v if v not in (None, 0.0) or item.endswith("연체율") else None
    return out


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


def sector(name: str, fs: Optional["Fisis"] = None, quarters: int = 12, today: Optional[date] = None) -> Optional[dict]:
    """회사 하나의 업종 지표 분기 추이 + 같은 업종 전체의 최근 분기 비교. 금감원에 없는 회사(비금융)는 None"""
    fs = fs or Fisis()
    comp = find_company(fs, name)
    if not comp:
        return None
    part = comp["part"]
    names = fs.tables(part)
    series = []
    for month in months_back(quarters, today):
        vals = _metric_values(fs, names, comp["finance_cd"], part, month)
        if any(v is not None for v in vals.values()):
            series.append({"period": f"{month[:4]}.{month[4:]}", **vals})
    latest = series[-1]["period"].replace(".", "") if series else None
    peers = []
    if latest:
        for c in fs.companies(part):
            vals = _metric_values(fs, names, c["finance_cd"], part, latest)
            if any(v is not None for v in vals.values()):
                peers.append({"finance_cd": c["finance_cd"], "name": short_name(c["finance_nm"]).strip(), "full_name": c["finance_nm"].strip(),
                              "me": c["finance_cd"] == comp["finance_cd"], **vals})
    return {"made_at": datetime.now().isoformat(timespec="seconds"), "part": part, "sector": SECTORS[part]["name"],
            "company": comp["finance_nm"], "month": latest,
            "metrics": [{"key": k, "label": lb, "group": g, "unit": u} for k, lb, g, _, _, u in SECTORS[part]["metrics"]],
            "series": series, "peers": peers}

def company_series(part: str, finance_cd: str, fs: Optional["Fisis"] = None, quarters: int = 12, today: Optional[date] = None) -> dict:
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
            "metrics": [{"key": k, "label": lb, "group": g, "unit": u} for k, lb, g, _, _, u in SECTORS[part]["metrics"]],
            "series": series, "fs_quarter": fq, "fs_annual": fa}


if __name__ == "__main__":
    out = build(sys.argv[1] if len(sys.argv) > 1 else "H", sys.argv[2] if len(sys.argv) > 2 else "")
    print(json.dumps({k: v for k, v in out.items() if k != "rows"}, ensure_ascii=False))
    for r in out.get("rows", []):
        print(f"  {r['name']:<14} K-ICS {r['kics']} 자산 {round((r['assets'] or 0) / 1e12, 1)}조 순이익 {round((r['ni'] or 0) / 1e8)}억 운용수익률 {r['inv_yield']} {'<-' if r['me'] else ''}")
