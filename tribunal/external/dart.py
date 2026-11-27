"""OpenDART 호출. 키는 .env 의 DART_API_KEY 에서 llm.get_key() 로만 읽고, 화면·로그·캐시 파일에 남기지 않는다.

쓰는 API (모두 무료, 하루 호출 한도 있음)
- corpCode.xml        회사 고유번호 목록 (종목코드 -> corp_code)
- fnlttSinglAcntAll   단일회사 전체 재무제표 (연결 CFS / 별도 OFS)
- list.json           공시 검색 (보고서 이름, 접수일. 정정 전 원본이 언제 나왔는지 확인)
- fnlttXbrl.xml       재무제표 원본 파일(XBRL). 주석 숫자까지 태그가 붙어 있다 (보험사 CSM)
- document.xml        공시 원문. 사업의 내용에만 있는 숫자 (보험사 지급여력비율)

응답은 .cache/dart/ 에 저장해 같은 것을 다시 부르지 않는다 (조회 시점도 같이 저장).
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from ..llm import ROOT, get_key

BASE = "https://opendart.fss.or.kr/api/"
CACHE = ROOT / ".cache" / "dart"
KEY_ENV = "DART_API_KEY"
TTL = timedelta(days=7)              # 공시는 정정될 수 있어 일주일 지나면 다시 받는다
TIMEOUT = 30

STATUS = {   # OpenDART 응답 코드 -> 쉬운 말
    "010": "등록되지 않은 DART 키입니다. .env 의 DART_API_KEY 를 확인하세요.",
    "011": "사용할 수 없는 DART 키입니다.",
    "012": "접근할 수 없는 IP입니다.",
    "020": "DART 하루 호출 한도를 넘었습니다. 내일 다시 하세요.",
    "100": "DART 요청 값이 잘못되었습니다.",
    "101": "DART 요청 값이 잘못되었습니다.",
    "800": "DART가 점검 중입니다. 잠시 뒤 다시 하세요.",
    "900": "DART 쪽 오류입니다. 잠시 뒤 다시 하세요.",
}
NO_DATA = "013"


class DartError(RuntimeError):
    """키나 한도처럼 대조 전체를 멈춰야 하는 문제. 메시지에 키를 넣지 않는다."""


def save_atomic(f, text: str) -> None:
    """저장 파일을 한 번에 바꾼다 (2026-11-23): 같은 회사를 동시에 받을 때 반쯤 쓴 파일을 읽지 않도록 임시 파일에 쓰고 바꿔치기"""
    import os
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f"{f.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, f)


def load_json(f):
    """저장 파일 읽기. 없거나 깨졌으면 None (다시 받는다)"""
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def original_url(rcept_no: str) -> str:
    return f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"


def rcept_date(rcept_no: str) -> str:
    """접수번호 앞 8자리가 접수일이다 (20260312000123 -> 2026-03-12)."""
    d = (rcept_no or "")[:8]
    return f"{d[:4]}-{d[4:6]}-{d[6:]}" if len(d) == 8 and d.isdigit() else ""


class Dart:
    def __init__(self, key: Optional[str] = None, cache: Path = CACHE, offline: bool = False):
        self._key = key            # 없으면 처음 부를 때 .env 에서 읽는다
        self.cache = cache
        self.offline = offline     # 테스트용: 캐시에 없으면 부르지 않는다
        self.calls = 0

    # ---------- 공통 ----------
    def _cache_file(self, endpoint: str, params: dict) -> Path:
        tag = json.dumps([endpoint, sorted(params.items())], ensure_ascii=False)
        return self.cache / f"{endpoint.split('.')[0]}-{hashlib.sha1(tag.encode()).hexdigest()[:16]}.json"

    def _raw(self, endpoint: str, params: dict) -> bytes:
        if self.offline:
            raise DartError("오프라인 모드라 DART를 부르지 않습니다.")
        key = self._key or get_key(KEY_ENV)
        url = BASE + endpoint + "?" + urllib.parse.urlencode({"crtfc_key": key, **params})
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT) as res:   # noqa: S310 (정해진 주소만 부른다)
                self.calls += 1
                return res.read()
        except urllib.error.HTTPError as e:
            raise DartError(f"DART 응답 오류 (HTTP {e.code}). 잠시 뒤 다시 하세요.") from None
        except (urllib.error.URLError, TimeoutError) as e:
            raise DartError(f"DART에 연결하지 못했습니다 ({type(e).__name__}). 인터넷 연결을 확인하세요.") from None

    def get(self, endpoint: str, params: dict) -> dict:
        """JSON API 호출. 돌려주는 값: {'status', 'list', 'fetched_at', ...}. 데이터 없음(013)은 오류가 아니다."""
        f = self._cache_file(endpoint, params)
        saved = load_json(f) if f.exists() else None
        if saved is not None:
            if self.offline or datetime.now() - datetime.fromisoformat(saved["fetched_at"]) < TTL:
                return saved
        body = json.loads(self._raw(endpoint, params).decode("utf-8"))
        status = str(body.get("status", ""))
        if status not in ("000", NO_DATA):
            raise DartError(STATUS.get(status, f"DART 오류 코드 {status}"))
        saved = {**body, "fetched_at": datetime.now().isoformat(timespec="seconds"), "endpoint": endpoint, "params": params}
        f.parent.mkdir(parents=True, exist_ok=True)
        save_atomic(f, json.dumps(saved, ensure_ascii=False))
        return saved

    # ---------- 회사 ----------
    def corps(self) -> List[dict]:
        """상장회사 목록 [{corp_code, corp_name, stock_code}]. 하루에 한 번만 새로 받는다."""
        f = self.cache / "corpcode.json"
        if f.exists():
            saved = json.loads(f.read_text(encoding="utf-8"))
            if self.offline or datetime.now() - datetime.fromisoformat(saved["fetched_at"]) < timedelta(days=1):
                return saved["list"]
        raw = self._raw("corpCode.xml", {})
        if raw[:2] != b"PK":                       # 오류면 zip 이 아니라 JSON/XML 이 온다
            try:
                status = json.loads(raw.decode("utf-8")).get("status", "")
            except ValueError:
                status = (ET.fromstring(raw).findtext("status") or "") if raw.strip().startswith(b"<") else ""
            raise DartError(STATUS.get(status, "DART 회사 목록을 받지 못했습니다."))
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            root = ET.fromstring(z.read(z.namelist()[0]))
        rows = [{"corp_code": e.findtext("corp_code", "").strip(), "corp_name": e.findtext("corp_name", "").strip(),
                 "stock_code": e.findtext("stock_code", "").strip()} for e in root.iter("list")]
        now = datetime.now().isoformat(timespec="seconds")
        f.parent.mkdir(parents=True, exist_ok=True)
        save_atomic(self.cache / "corpcode-all.json", json.dumps({"fetched_at": now, "list": rows}, ensure_ascii=False))
        rows = [r for r in rows if r["stock_code"]]   # 상장사만
        save_atomic(f, json.dumps({"fetched_at": now, "list": rows}, ensure_ascii=False))
        return rows

    def all_corps(self) -> List[dict]:
        """비상장사까지 전체 회사 목록 (2026-11-23, 동종사 비교용). 상장사 목록과 같은 파일에서 하루에 한 번"""
        f = self.cache / "corpcode-all.json"
        if f.exists():
            saved = json.loads(f.read_text(encoding="utf-8"))
            if self.offline or datetime.now() - datetime.fromisoformat(saved["fetched_at"]) < timedelta(days=1):
                return saved["list"]
        (self.cache / "corpcode.json").unlink(missing_ok=True)   # 다시 받게 한다
        self.corps()
        return json.loads(f.read_text(encoding="utf-8"))["list"]

    def corp_by_code(self, corp_code: str) -> Optional[dict]:
        return next((c for c in self.all_corps() if c["corp_code"] == corp_code), None)

    def unlisted_by_name(self, name: str) -> List[dict]:
        """이름이 같은 회사(띄어쓰기 무시). 비상장 포함"""
        n = name.replace(" ", "")
        return [c for c in self.all_corps() if c["corp_name"].replace(" ", "") == n]

    def corp_by_stock(self, stock_code: str) -> Optional[dict]:
        code = (stock_code or "").strip().zfill(6)
        return next((c for c in self.corps() if c["stock_code"] == code), None)

    def corps_by_name(self, name: str) -> List[dict]:
        n = name.replace(" ", "")
        return [c for c in self.corps() if c["corp_name"].replace(" ", "") == n]

    # ---------- 재무제표, 공시 ----------
    def statements(self, corp_code: str, year: int, fs_div: str, reprt_code: str = "11011") -> dict:
        """사업보고서(11011)의 전체 재무제표. fs_div: CFS(연결) / OFS(별도)."""
        return self.get("fnlttSinglAcntAll.json", {"corp_code": corp_code, "bsns_year": str(year),
                                                   "reprt_code": reprt_code, "fs_div": fs_div})

    def dividends(self, corp_code: str, year: int, reprt_code: str = "11011") -> dict:
        """사업보고서의 '배당에 관한 사항': 배당성향, 주당 배당금, 주당순이익, 배당금 총액 (당기·전기·전전기)"""
        return self.get("alotMatter.json", {"corp_code": corp_code, "bsns_year": str(year), "reprt_code": reprt_code})

    def period_filings(self, corp_code: str, year: int, month: int) -> List[Dict[str, str]]:
        """그 기간(예: 2026.03)의 정기보고서들(분기·반기·사업, 정정 포함), 접수일 순."""
        detail = {3: "A003", 6: "A002", 9: "A003"}.get(month, "A001")
        res = self.get("list.json", {"corp_code": corp_code, "bgn_de": f"{year}0101", "end_de": f"{year + 1}1231",
                                     "pblntf_detail_ty": detail, "page_count": "100"})
        items = [x for x in res.get("list", []) if f"({year}.{month:02d})" in x.get("report_nm", "")]
        return sorted(items, key=lambda x: x.get("rcept_dt", ""))

    def annual_filings(self, corp_code: str, year: int) -> List[Dict[str, str]]:
        """그 회계연도의 사업보고서 공시들 (정정 포함), 접수일 순."""
        res = self.get("list.json", {"corp_code": corp_code, "bgn_de": f"{year}0101", "end_de": f"{year + 2}1231",
                                     "pblntf_detail_ty": "A001", "page_count": "100"})
        items = [x for x in res.get("list", []) if f"({year}." in x.get("report_nm", "")]
        return sorted(items, key=lambda x: x.get("rcept_dt", ""))

    # ---------- 공시 원본 파일 (2026-11-21: 보험사 CSM·K-ICS) ----------
    def _zip(self, endpoint: str, params: dict, tag: str) -> bytes:
        """zip 으로 오는 원본 파일. .cache/dart/<tag>.zip 에 그대로 둔다 (공시 원본은 접수번호마다 바뀌지 않는다)."""
        f = self.cache / f"{tag}.zip"
        if f.exists():
            return f.read_bytes()
        raw = self._raw(endpoint, params)
        if raw[:2] != b"PK":
            try:
                status = json.loads(raw.decode("utf-8")).get("status", "")
            except ValueError:
                status = (ET.fromstring(raw).findtext("status") or "") if raw.strip().startswith(b"<") else ""
            if status == NO_DATA:
                return b""
            raise DartError(STATUS.get(status, "DART 원본 파일을 받지 못했습니다."))
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(raw)
        return raw

    def xbrl_facts(self, rcept_no: str, reprt_code: str) -> dict:
        """정기보고서의 재무제표 원본(XBRL) 안의 숫자 전부. 주석(CSM 등)까지 이름표(태그)가 붙어 있다.
        돌려주는 값: {'facts': [{'name', 'dims': {축: 값}, 'instant' | 'start'+'end', 'value', 'decimals'}], 'fetched_at'}.
        없으면 facts 가 빈 목록이다."""
        f = self.cache / f"xbrl-{rcept_no}.json"
        saved = load_json(f) if f.exists() else None
        if saved is not None:
            return saved
        raw = self._zip("fnlttXbrl.xml", {"rcept_no": rcept_no, "reprt_code": reprt_code}, f"xbrl-{rcept_no}")
        facts: List[dict] = []
        labels: Dict[str, str] = {}
        if raw:
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                name = next((n for n in z.namelist() if n.endswith(".xbrl")), None)
                if name:
                    facts = parse_xbrl(z.read(name).decode("utf-8", "ignore"))
                lab = next((n for n in z.namelist() if n.endswith("_lab-ko.xml")), None)
                if lab:                                  # 한국어 이름표 (dart_pool 이 숫자에 이름을 붙일 때 쓴다)
                    from .dart_pool import parse_labels
                    labels = parse_labels(z.read(lab).decode("utf-8", "ignore"))
        out = {"facts": facts, "labels": labels, "fetched_at": datetime.now().isoformat(timespec="seconds"), "rcept_no": rcept_no}
        f.parent.mkdir(parents=True, exist_ok=True)
        save_atomic(f, json.dumps(out, ensure_ascii=False))
        return out

    def document_text(self, rcept_no: str) -> str:
        """공시 원문(본문 XML). 사업의 내용에만 있는 숫자(지급여력비율 등)를 읽을 때 쓴다. 없으면 빈 글."""
        raw = self._zip("document.xml", {"rcept_no": rcept_no}, f"doc-{rcept_no}")
        if not raw:
            return ""
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            main = sorted(z.namelist(), key=len)[0]          # 본문은 '접수번호.xml', 첨부는 '접수번호_00760.xml'
            data = z.read(main)
        try:                                                            # 옛 원문(2021년 무렵까지)은 머리말엔 utf-8 이라 적고 실제는 EUC-KR(cp949)
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode("cp949", "ignore")


_CTX = re.compile(r"<xbrli:context id=\"([^\"]+)\">(.*?)</xbrli:context>", re.S)
_FACT = re.compile(r"<([A-Za-z\-]+):([A-Za-z0-9_]+) contextRef=\"([^\"]+)\"([^>]*)>([^<]*)</")


def parse_xbrl(text: str) -> List[dict]:
    """XBRL 인스턴스 글 -> 숫자 목록. 축과 값은 접두어를 뺀 이름(예: ConsolidatedAndSeparateFinancialStatementsAxis: SeparateMember)."""
    ctx: Dict[str, dict] = {}
    for m in _CTX.finditer(text):
        body = m.group(2)
        c: dict = {"dims": {d.split(":")[-1]: v.split(":")[-1] for d, v in re.findall(r'dimension="([^"]+)">\s*([^<\s]+)\s*<', body)}}
        inst = re.search(r"<xbrli:instant>([^<]+)<", body)
        if inst:
            c["instant"] = inst.group(1).strip()
        else:
            s, e = re.search(r"<xbrli:startDate>([^<]+)<", body), re.search(r"<xbrli:endDate>([^<]+)<", body)
            if s and e:
                c["start"], c["end"] = s.group(1).strip(), e.group(1).strip()
        ctx[m.group(1)] = c
    out = []
    for m in _FACT.finditer(text):
        prefix, name, cref, attrs, value = m.groups()
        if prefix in ("xbrli", "link", "xlink") or "unitRef" not in attrs:
            continue                                       # 숫자만 (글 주석은 뺀다)
        try:
            v = float(value.strip().replace(",", ""))
        except ValueError:
            continue
        c = ctx.get(cref)
        if not c:
            continue
        dec = re.search(r'decimals="([^"]+)"', attrs)
        out.append({"name": name, **c, "value": v, "decimals": dec.group(1) if dec else ""})
    return out
