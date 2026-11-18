"""통계청 KOSIS Open API 호출. 키는 .env 의 KOSIS_API_KEY 에서 llm.get_key() 로만 읽는다.
KOSIS 는 키를 주소에 넣어야 하므로 기록하는 주소(api_url)에서는 키를 '{인증키}'로 바꿔 둔다.

쓰는 API
- statisticsSearch.do              통계표 검색 (이름으로)
- Param/statisticsParameterData.do 통계표 값 (기간 하나, 항목·분류 전부)
응답은 .cache/kosis/ 에 저장한다 (7일).
"""
from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from ..llm import ROOT, get_key

BASE = "https://kosis.kr/openapi/"
CACHE = ROOT / ".cache" / "kosis"
KEY_ENV = "KOSIS_API_KEY"
TIMEOUT = 60
TTL = timedelta(days=7)
SITE = "https://kosis.kr"
FATAL = {"10": "KOSIS 인증키가 맞지 않습니다. .env 의 KOSIS_API_KEY 를 확인하세요.",
         "11": "KOSIS 인증키 기간이 끝났습니다.", "21": "KOSIS 호출 한도를 넘었습니다. 잠시 뒤 다시 하세요."}


class KosisError(RuntimeError):
    """키·한도·연결처럼 대조 전체를 멈춰야 하는 문제. 메시지에 키를 넣지 않는다."""


def api_url(path: str, params: dict) -> str:
    return BASE + path + "?" + urllib.parse.urlencode({"apiKey": "{인증키}", **params})


class Kosis:
    def __init__(self, key: Optional[str] = None, cache: Path = CACHE, offline: bool = False):
        self._key = key
        self.cache = cache
        self.offline = offline
        self.calls = 0

    def get(self, path: str, params: dict) -> dict:
        """돌려주는 값: {'rows': [...], 'error': KOSIS 안내(있으면), 'fetched_at', 'api_url'}"""
        full = {"format": "json", "jsonVD": "Y", **params}
        tag = json.dumps([path, sorted(full.items())], ensure_ascii=False)
        f = self.cache / f"{path.split('/')[-1].split('.')[0]}-{hashlib.sha1(tag.encode()).hexdigest()[:16]}.json"
        if f.exists():
            saved = json.loads(f.read_text(encoding="utf-8"))
            if self.offline or datetime.now() - datetime.fromisoformat(saved["fetched_at"]) < TTL:
                return saved
        if self.offline:
            raise KosisError("오프라인 모드라 KOSIS를 부르지 않습니다.")
        key = self._key or get_key(KEY_ENV)
        url = BASE + path + "?" + urllib.parse.urlencode({"apiKey": key, **full})
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT) as res:    # noqa: S310
                self.calls += 1
                raw = res.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raise KosisError(f"KOSIS 응답 오류 (HTTP {e.code}). 잠시 뒤 다시 하세요.") from None
        except (urllib.error.URLError, TimeoutError) as e:
            raise KosisError(f"KOSIS에 연결하지 못했습니다 ({type(e).__name__}).") from None
        try:
            body = json.loads(raw)
        except ValueError:
            body = {"err": "?", "errMsg": "JSON 이 아닌 응답"}
        err = None
        if isinstance(body, dict):
            code = str(body.get("err", ""))
            if code in FATAL:
                raise KosisError(FATAL[code])
            err = f"{code} {str(body.get('errMsg', ''))[:60]}".strip()
            rows: List[dict] = []
        else:
            rows = body
        saved = {"rows": rows, "error": err, "fetched_at": datetime.now().isoformat(timespec="seconds"),
                 "api_url": api_url(path, full)}
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
        return saved

    def search(self, words: str, n: int = 8) -> dict:
        return self.get("statisticsSearch.do", {"method": "getList", "searchNm": words, "startCount": "1",
                                                "resultCount": str(n), "sort": "RANK"})

    def data(self, org: str, tbl: str, cycle: str, period: str) -> dict:
        """그 기간의 표 전체. 분류가 여러 겹인 표는 한 번 더 넓게 부른다."""
        base = {"method": "getList", "orgId": org, "tblId": tbl, "itmId": "ALL", "prdSe": cycle,
                "startPrdDe": period, "endPrdDe": period}
        res = self.get("Param/statisticsParameterData.do", {**base, "objL1": "ALL"})
        if res["error"] and "objL" in res["error"]:
            res = self.get("Param/statisticsParameterData.do", {**base, "objL1": "ALL", "objL2": "ALL", "objL3": "ALL"})
        return res
