"""한국은행 경제통계시스템(ECOS) Open API 호출. 키는 .env 의 ECOS_API_KEY 에서 llm.get_key() 로만 읽는다.
ECOS 는 키를 주소 안에 넣어야 하므로, 기록하는 주소(api_url)에서는 키를 '{인증키}'로 바꿔 둔다.
화면·로그·캐시 파일에 키를 남기지 않는다.

쓰는 API
- StatisticSearch    통계 값 (통계표 코드, 주기 D/M/Q/A, 기간, 항목 코드)
- StatisticItemList  통계표의 항목 목록 (코드 확인용)
응답은 .cache/ecos/ 에 저장한다 (끝난 기간의 통계는 거의 바뀌지 않는다. 7일 뒤 다시 받는다).
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

BASE = "https://ecos.bok.or.kr/api/"
CACHE = ROOT / ".cache" / "ecos"
KEY_ENV = "ECOS_API_KEY"
TIMEOUT = 30
TTL = timedelta(days=7)
SITE = "https://ecos.bok.or.kr"
NO_DATA = "INFO-200"
MESSAGES = {
    "INFO-100": "ECOS 인증키가 맞지 않습니다. .env 의 ECOS_API_KEY 를 확인하세요.",
    "ERROR-602": "ECOS 호출 한도를 넘었습니다. 잠시 뒤 다시 하세요.",
}


class EcosError(RuntimeError):
    """키·한도·연결처럼 대조 전체를 멈춰야 하는 문제. 메시지에 키를 넣지 않는다."""


def _path(service: str, parts: List[str], key: str) -> str:
    return BASE + "/".join([service, key, "json", "kr"] + [urllib.parse.quote(p, safe="") for p in parts])


def api_url(service: str, parts: List[str]) -> str:
    """기록용 주소. 키 자리는 '{인증키}' 로 둔다."""
    return _path(service, parts, "{인증키}")


class Ecos:
    def __init__(self, key: Optional[str] = None, cache: Path = CACHE, offline: bool = False):
        self._key = key
        self.cache = cache
        self.offline = offline
        self.calls = 0

    def get(self, service: str, parts: List[str]) -> dict:
        """돌려주는 값: {'rows': [...], 'fetched_at', 'api_url'}. 자료가 없으면 rows 가 빈다."""
        tag = json.dumps([service, parts], ensure_ascii=False)
        f = self.cache / f"{service}-{hashlib.sha1(tag.encode()).hexdigest()[:16]}.json"
        if f.exists():
            saved = json.loads(f.read_text(encoding="utf-8"))
            if self.offline or datetime.now() - datetime.fromisoformat(saved["fetched_at"]) < TTL:
                return saved
        if self.offline:
            raise EcosError("오프라인 모드라 ECOS를 부르지 않습니다.")
        key = self._key or get_key(KEY_ENV)
        try:
            with urllib.request.urlopen(_path(service, parts, key), timeout=TIMEOUT) as res:   # noqa: S310
                self.calls += 1
                body = json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise EcosError(f"ECOS 응답 오류 (HTTP {e.code}). 잠시 뒤 다시 하세요.") from None
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            raise EcosError(f"ECOS에 연결하지 못했습니다 ({type(e).__name__}).") from None
        if service in body:
            rows = body[service].get("row", [])
        else:
            res = body.get("RESULT", {})
            code = str(res.get("CODE", ""))
            if code != NO_DATA:
                raise EcosError(MESSAGES.get(code, f"ECOS 오류 {code}: {str(res.get('MESSAGE', ''))[:60]}"))
            rows = []
        saved = {"rows": rows, "fetched_at": datetime.now().isoformat(timespec="seconds"), "api_url": api_url(service, parts)}
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
        return saved

    def series(self, stat: str, cycle: str, start: str, end: str, item: str) -> dict:
        """예: series('731Y001', 'D', '20230101', '20231231', '0000001')"""
        return self.get("StatisticSearch", ["1", "10000", stat, cycle, start, end, item])

    def items(self, stat: str) -> dict:
        return self.get("StatisticItemList", ["1", "1000", stat])
