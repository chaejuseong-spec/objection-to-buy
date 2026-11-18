"""OpenDART 호출. 키는 .env 의 DART_API_KEY 에서 llm.get_key() 로만 읽고, 화면·로그·캐시 파일에 남기지 않는다.

쓰는 API (모두 무료, 하루 호출 한도 있음)
- corpCode.xml        회사 고유번호 목록 (종목코드 -> corp_code)
- fnlttSinglAcntAll   단일회사 전체 재무제표 (연결 CFS / 별도 OFS)
- list.json           공시 검색 (보고서 이름, 접수일. 정정 전 원본이 언제 나왔는지 확인)

응답은 .cache/dart/ 에 저장해 같은 것을 다시 부르지 않는다 (조회 시점도 같이 저장).
"""
from __future__ import annotations

import hashlib
import io
import json
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
        if f.exists():
            saved = json.loads(f.read_text(encoding="utf-8"))
            if self.offline or datetime.now() - datetime.fromisoformat(saved["fetched_at"]) < TTL:
                return saved
        body = json.loads(self._raw(endpoint, params).decode("utf-8"))
        status = str(body.get("status", ""))
        if status not in ("000", NO_DATA):
            raise DartError(STATUS.get(status, f"DART 오류 코드 {status}"))
        saved = {**body, "fetched_at": datetime.now().isoformat(timespec="seconds"), "endpoint": endpoint, "params": params}
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
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
        rows = [r for r in rows if r["stock_code"]]   # 상장사만
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"fetched_at": datetime.now().isoformat(timespec="seconds"), "list": rows}, ensure_ascii=False), encoding="utf-8")
        return rows

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
