"""회사 상징색 (2026-11-23, 엑셀 내보내기용).

DART 기업개황의 홈페이지에서 로고 그림을 찾아 주된 색을 뽑는다.
찾는 순서: 'logo' 가 붙은 그림(img·인라인 svg) -> 아이폰 아이콘 -> 사이트 아이콘(파비콘).
흰색·검정·회색(채도 낮은 색)은 빼고 가장 많은 색. 못 찾으면 None (엑셀은 짙은 네이비).
30일 저장해 두고 다시 쓴다. 회사 홈페이지에서 그림 몇 개만 받는다 (요청에 사용자 정보를 넣지 않는다).
"""
from __future__ import annotations

import colorsys
import io
import json
import re
import ssl
import sys
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

CACHE = Path(__file__).resolve().parents[2] / ".cache" / "brand"
TTL_DAYS = 30
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
LIMIT = 3_000_000
TIMEOUT = 10


def _get(url: str) -> Tuple[bytes, str, str]:
    """(내용, 형식, 최종 주소). http(s) 만, 3MB 까지"""
    if not re.match(r"^https?://", url):
        raise ValueError("http(s) 주소가 아닙니다")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    res = urllib.request.urlopen(req, timeout=TIMEOUT, context=ssl.create_default_context())   # noqa: S310 (회사 홈페이지만)
    with res:
        data = res.read(LIMIT + 1)
        if len(data) > LIMIT:
            raise ValueError("너무 큽니다")
        return data, res.headers.get("Content-Type", ""), res.geturl()


def _home(hm_url: str) -> Optional[Tuple[str, str]]:
    """(HTML, 최종 주소). 주소에 http 가 없으면 https, http 차례로"""
    u = (hm_url or "").strip()
    if not u:
        return None
    tries = [u] if re.match(r"^https?://", u) else [f"https://{u}", f"http://{u}"]
    for t in tries:
        try:
            data, ctype, final = _get(t)
            return data.decode("utf-8", "ignore"), final
        except Exception:                                   # noqa: BLE001 (다음 주소로)
            continue
    return None


_ATTR = r'([a-zA-Z\-:]+)\s*=\s*("[^"]*"|\'[^\']*\'|[^\s>]+)'


def _attrs(tag: str) -> dict:
    return {k.lower(): v.strip("\"'") for k, v in re.findall(_ATTR, tag)}


def candidates(html: str, base: str) -> List[Tuple[str, str]]:
    """[(종류, 주소 또는 svg 글)]. 로고 그림 먼저"""
    out: List[Tuple[str, str]] = []
    for m in re.finditer(r"<svg\b[^>]*>.*?</svg>", html, re.S | re.I):        # 인라인 svg 로고
        head = m.group(0)[:300].lower()
        if "logo" in head:
            out.append(("svg-inline", m.group(0)))
    for tag in re.findall(r"<img\b[^>]*>", html, re.I):
        a = _attrs(tag)
        hint = " ".join(a.get(k, "") for k in ("src", "alt", "class", "id", "data-src")).lower()
        src = a.get("src") or a.get("data-src") or ""
        name = src.lower().split("?")[0].rsplit("/", 1)[-1]
        if re.search(r"banner|visual|bg|background|main|slide|thumb", name):     # 로고가 아닌 큰 그림
            continue
        if ("logo" in hint or re.search(r"(^|[_\-])ci([_\-.]|$)", name)) and src and not src.startswith("data:"):
            out.append(("img", urllib.parse.urljoin(base, src)))
    links = re.findall(r"<link\b[^>]*>", html, re.I)
    for want in ("apple-touch-icon", "icon"):
        for tag in links:
            a = _attrs(tag)
            if want in a.get("rel", "").lower().split() and a.get("href") and not a["href"].startswith("data:"):
                out.append(("icon", urllib.parse.urljoin(base, a["href"])))
    out.append(("icon", urllib.parse.urljoin(base, "/favicon.ico")))
    seen, uniq = set(), []
    for k, v in out:
        if v not in seen:
            seen.add(v)
            uniq.append((k, v))
    return uniq[:8]


def _ok(rgb: Tuple[int, int, int]) -> bool:
    """상징색 후보: 채도가 있고 너무 밝거나 어둡지 않은 색"""
    h, l, s = colorsys.rgb_to_hls(*(x / 255 for x in rgb))
    return s >= 0.3 and 0.12 <= l <= 0.85


def _hex(rgb) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def svg_color(text: str) -> Optional[str]:
    """svg 의 fill·stroke·style 색 중 가장 많이 쓴 상징색 후보"""
    cols: Counter = Counter()
    for m in re.finditer(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b", text):
        h = m.group(1)
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        rgb = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
        if _ok(rgb):
            cols[rgb] += 1
    for m in re.finditer(r"rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", text):
        rgb = tuple(min(255, int(x)) for x in m.groups())
        if _ok(rgb):
            cols[rgb] += 1
    return _hex(cols.most_common(1)[0][0]) if cols else None


def raster_color(data: bytes) -> Optional[str]:
    """그림(png·jpg·ico·webp·gif)의 불투명 화소 중 상징색 후보로 가장 많은 색. 후보가 3% 미만이면 None"""
    from PIL import Image
    try:
        im = Image.open(io.BytesIO(data))
        im.seek(0)
        im = im.convert("RGBA")
    except Exception:                                       # noqa: BLE001
        return None
    im.thumbnail((96, 96))
    flat = im.get_flattened_data() if hasattr(im, "get_flattened_data") else im.getdata()
    px = [p for p in flat if p[3] >= 200]
    if not px:
        return None
    buckets: dict = {}
    for r, g, b, _ in px:
        if not _ok((r, g, b)):
            continue
        k = (r // 24, g // 24, b // 24)
        t = buckets.setdefault(k, [0, 0, 0, 0])
        t[0] += r; t[1] += g; t[2] += b; t[3] += 1         # noqa: E702
    if not buckets:
        return None
    best = max(buckets.values(), key=lambda t: t[3])
    if best[3] < len(px) * 0.03:
        return None
    return _hex(tuple(round(best[i] / best[3]) for i in range(3)))


def find_color(hm_url: str) -> Tuple[Optional[str], Optional[str]]:
    """(색, 어디서 뽑았나)"""
    home = _home(hm_url)
    if not home:
        return None, None
    html, base = home
    for kind, v in candidates(html, base):
        try:
            if kind == "svg-inline":
                c = svg_color(v)
                if c:
                    return c, f"{base} (로고 svg)"
                continue
            data, ctype, _ = _get(v)
            c = svg_color(data.decode("utf-8", "ignore")) if ("svg" in ctype or v.lower().split("?")[0].endswith(".svg")) else raster_color(data)
            if c:
                return c, v
        except Exception:                                   # noqa: BLE001 (다음 후보로)
            continue
    return None, None


def color(ticker: str) -> dict:
    """{'color': '#RRGGBB' 또는 None, 'from': 뽑은 그림 주소, 'made_at'}. 30일 저장"""
    if not re.fullmatch(r"[0-9A-Z]{6}", ticker or ""):
        return {"color": None}
    f = CACHE / f"{ticker}.json"
    if f.exists():
        saved = json.loads(f.read_text(encoding="utf-8"))
        if (datetime.now() - datetime.fromisoformat(saved["made_at"])).days < TTL_DAYS:
            return saved
    out = {"color": None, "from": None, "made_at": datetime.now().isoformat(timespec="seconds")}
    try:
        from .dart import Dart
        d = Dart()
        corp = d.corp_by_stock(ticker)
        hm = d.get("company.json", {"corp_code": corp["corp_code"]}).get("hm_url") if corp else None
        out["color"], out["from"] = find_color(hm or "")
    except Exception:                                       # noqa: BLE001 (색은 꾸밈이라 실패해도 짙은 네이비)
        pass
    CACHE.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


if __name__ == "__main__":
    for t in sys.argv[1:]:
        print(t, color(t))
