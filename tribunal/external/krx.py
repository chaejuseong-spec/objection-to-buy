"""KRX(한국거래소) 정보데이터시스템 OPEN API 호출. 키는 .env 의 KRX_API_KEY 에서 llm.get_key() 로만 읽고,
요청 머리글(AUTH_KEY)로만 보낸다. 화면·로그·캐시 파일에 남기지 않는다.

쓰는 API (openapi.krx.co.kr 에서 API마다 이용 신청과 승인이 필요하다)
- sto/stk_bydd_trd   유가증권(코스피) 일별매매정보
- sto/ksq_bydd_trd   코스닥 일별매매정보
- sto/knx_bydd_trd   코넥스 일별매매정보
하루치를 부르면 그날 전 종목의 종가, 고가, 저가, 시가총액, 상장주식수가 온다.
응답은 .cache/krx/ 에 저장한다 (지난 날짜의 시세는 바뀌지 않는다).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

from ..llm import ROOT, get_key

BASE = "https://data-dbg.krx.co.kr/svc/apis/"
CACHE = ROOT / ".cache" / "krx"
KEY_ENV = "KRX_API_KEY"
TIMEOUT = 30
MARKETS = [("stk_bydd_trd", "유가증권 일별매매정보", "코스피"),
           ("ksq_bydd_trd", "코스닥 일별매매정보", "코스닥"),
           ("knx_bydd_trd", "코넥스 일별매매정보", "코넥스")]
SITE = "https://data.krx.co.kr"


class KrxError(RuntimeError):
    """키·승인·연결처럼 대조 전체를 멈춰야 하는 문제. 메시지에 키를 넣지 않는다."""


class KrxAuthError(KrxError):
    """키가 틀렸거나 그 API(시장)의 이용 승인이 없음 (401, 403)"""


def api_url(endpoint: str, day: str) -> str:
    """기록용 주소 (키는 머리글로 보내므로 주소에 없다)"""
    return f"{BASE}sto/{endpoint}?basDd={day}"


def num(v) -> Optional[float]:
    t = str(v if v is not None else "").replace(",", "").strip()
    try:
        return float(t)
    except ValueError:
        return None


class Krx:
    def __init__(self, key: Optional[str] = None, cache: Path = CACHE, offline: bool = False):
        self._key = key
        self.cache = cache
        self.offline = offline
        self.calls = 0

    def day(self, endpoint: str, day: str) -> dict:
        """하루치 전 종목. 돌려주는 값: {'rows': [...], 'fetched_at'}. 휴장일이면 rows 가 빈다."""
        f = self.cache / f"{endpoint}-{day}.json"
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
        if self.offline:
            raise KrxError("오프라인 모드라 KRX를 부르지 않습니다.")
        key = self._key or get_key(KEY_ENV)
        req = urllib.request.Request(api_url(endpoint, day), headers={"AUTH_KEY": key, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as res:    # noqa: S310 (정해진 주소만 부른다)
                self.calls += 1
                body = json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                name = next((m[1] for m in MARKETS if m[0] == endpoint), "일별매매정보")
                raise KrxAuthError(f"KRX 키가 맞지 않거나, 이 API({name})의 이용 승인이 아직 나지 않았습니다. "
                                   "openapi.krx.co.kr 의 마이페이지에서 승인 상태를 확인하세요.") from None
            raise KrxError(f"KRX 응답 오류 (HTTP {e.code}). 잠시 뒤 다시 하세요.") from None
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            raise KrxError(f"KRX에 연결하지 못했습니다 ({type(e).__name__}).") from None
        if "OutBlock_1" not in body:
            msg = str(body.get("respMsg") or body.get("message") or "")
            raise KrxError(f"KRX가 자료 대신 안내를 보냈습니다: {msg[:80] or '알 수 없음'}")
        saved = {"rows": body.get("OutBlock_1") or [], "fetched_at": datetime.now().isoformat(timespec="seconds"),
                 "endpoint": endpoint, "day": day}
        if saved["rows"] or date.today().strftime("%Y%m%d") > day:    # 오늘 자료가 아직 없을 때는 저장하지 않는다
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
        return saved

    @staticmethod
    def _match(row: dict, ticker: str = "", name: str = "") -> bool:
        code = str(row.get("ISU_CD", ""))
        if ticker:
            return code == ticker or code[3:9] == ticker     # 단축코드(900010) 또는 표준코드(KR7900010002)
        return str(row.get("ISU_NM", "")).replace(" ", "") == name.replace(" ", "")

    def find(self, day: str, ticker: str = "", name: str = "") -> Tuple[Optional[dict], Optional[tuple], Optional[dict]]:
        """그날 그 종목의 한 줄과 시장. 돌려주는 값: (줄, 시장 정보, 응답)
        이용 승인이 없는 시장(예: 코스닥만 미신청)은 건너뛰고 기록해 둔다. 모든 시장이 거절되면 그때 멈춘다."""
        denied = getattr(self, "denied", None)
        if denied is None:
            denied = self.denied = {}
        for m in MARKETS:
            if m[0] in denied:
                continue
            try:
                res = self.day(m[0], day)
            except KrxAuthError as e:
                denied[m[0]] = str(e)
                if len(denied) == len(MARKETS):
                    raise
                continue
            hit = next((r for r in res["rows"] if self._match(r, ticker, name)), None)
            if hit:
                return hit, m, res
        return None, None, None

    def window(self, report_date: str, ticker: str = "", name: str = "", n: int = 6, exact: Optional[str] = None) -> List[dict]:
        """작성일(포함)부터 거꾸로 n 거래일의 시세. exact 를 주면 그날 하루만.
        돌려주는 줄마다 _market, _fetched_at 를 붙인다."""
        days = [exact] if exact else []
        if not exact:
            d = datetime.strptime(report_date, "%Y-%m-%d").date()
            for _ in range(n * 3):
                if d.weekday() < 5:
                    days.append(d.strftime("%Y%m%d"))
                d -= timedelta(days=1)
        out, market = [], None
        for day in days:
            if market:                                       # 시장을 알면 그 시장만 부른다
                res = self.day(market[0], day)
                hit = next((r for r in res["rows"] if self._match(r, ticker, name)), None)
            else:
                hit, market, res = self.find(day, ticker, name)
                if not hit and self.day(MARKETS[0][0], day)["rows"]:
                    break                                    # 거래일인데 어느 시장에도 없으면 더 찾지 않는다
            if hit:
                out.append({**hit, "_market": market, "_fetched_at": res["fetched_at"]})
                if len(out) >= n:
                    break
        return out
