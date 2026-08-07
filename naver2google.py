"""Naver Map → Google/Apple Maps 轉換器

用法：
    python naver2google.py [--port 8585]

Web UI:   http://<LAN-IP>:8585
捷徑教學: /shortcut                        → iPhone 捷徑建立步驟
API:      GET  /convert?url=NAVER_URL      → JSON (含 google_url + apple_url)
          POST /convert_batch {"urls":[…]} → 批次
純文字:   GET|POST /apple、/google         → 只回一行網址（給 iOS 捷徑「打開 URL」）
Redirect: GET  /go?url=NAVER_URL[&target=apple] → 302 到 Google/Apple Maps
"""

from __future__ import annotations

import argparse
import os
import re
from functools import lru_cache
from urllib.parse import urlparse, parse_qs, quote, unquote

import requests as http_client
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask, request, jsonify, redirect, Response
from werkzeug.routing import PathConverter

# ---------------------------------------------------------------------------
# Naver Place Summary API (no API key needed)
# ---------------------------------------------------------------------------

PLACE_API = "https://map.naver.com/p/api/place/summary/{}"
NAVER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://map.naver.com/",
}


class NaverUnavailable(RuntimeError):
    """Raised when Naver itself is unreachable / rate-limiting (vs. a link we
    simply can't parse) — lets the API return a distinct 503 instead of 502."""


class ConversionFailed(RuntimeError):
    """Raised when we reached Naver fine but could not pin down a location.

    Why this exists: the old behaviour was to fall back to "search for whatever
    text we have". When that text is the *URL itself* the map app searches a
    meaningless string and quietly drops the user somewhere near **their own**
    location (Taiwan) — a wrong answer that looks like a right one. Failing
    loudly is the only honest option."""


# Shared session: connection pooling + automatic retry with backoff on the
# transient statuses Naver's internal API throws under load.
def _make_session() -> http_client.Session:
    s = http_client.Session()
    s.headers.update(NAVER_HEADERS)
    retry = Retry(
        total=3, connect=2, read=2, backoff_factor=0.4,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "HEAD"]),
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


SESSION = _make_session()


# ---------------------------------------------------------------------------
# Coordinate extraction
# ---------------------------------------------------------------------------

def _resolve_short_link(url: str) -> str:
    """Follow naver.me redirect to get the full URL.

    Uses GET (not HEAD): some naver.me short links return 405/no Location on
    HEAD but redirect correctly on GET. stream=True avoids downloading the body.
    """
    try:
        resp = SESSION.get(url, allow_redirects=True, timeout=10, stream=True)
        final = resp.url
        resp.close()
        return final
    except http_client.RequestException as exc:
        raise NaverUnavailable(f"無法解析短連結: {exc}") from exc


def _coords_from_params(url: str) -> tuple[float, float] | None:
    """Extract lat/lng from URL query parameters."""
    try:
        parsed = urlparse(url)
    except ValueError:
        # 分享文字含「[NAVER 地图]」這種方括號時，urlparse 會把它當 IPv6 主機
        # 而丟 ValueError（Invalid IPv6 URL）。這裡沒有座標可取，往下一招走。
        return None
    params = parse_qs(parsed.query)
    if "lat" in params and "lng" in params:
        try:
            return float(params["lat"][0]), float(params["lng"][0])
        except (ValueError, IndexError):
            pass
    return None


# m.place.naver.com uses the *category* as the path segment instead of "place":
# /restaurant/1234/home, /accommodation/1234/home, /hairshop/…, /attraction/…
# The numeric id is the same global place id the summary API takes, so match any
# segment — anchored to the place host so we don't grab digits out of some other
# path (e.g. /entry/address/…).
_PLACE_HOST_ID = re.compile(r"(?:m\.)?place\.naver\.com/[a-z]+/(\d+)")


def _extract_place_id(url: str) -> str | None:
    """Extract the numeric place ID from a Naver Map / Naver Place URL."""
    m = re.search(r"/place/(\d+)", url)
    if m:
        return m.group(1)
    m = _PLACE_HOST_ID.search(url)
    return m.group(1) if m else None


def _coords_from_place_api(place_id: str) -> tuple[float, float, str] | None:
    """Call Naver Place Summary API to get coordinates and name.

    Retries transient failures via the session adapter. A 403/429 (Naver
    blocking us) is surfaced as NaverUnavailable so the caller can 503; a clean
    200-with-no-coords just returns None (fall through to other strategies)."""
    try:
        resp = SESSION.get(PLACE_API.format(place_id), timeout=10)
    except http_client.RequestException as exc:
        raise NaverUnavailable(f"Place API 連線失敗: {exc}") from exc
    if resp.status_code in (403, 429):
        raise NaverUnavailable(f"Naver 擋下請求 (HTTP {resp.status_code})，稍後再試")
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    # Unknown/deleted ids still answer 200 but with every field null, so every
    # hop needs `or {}` — plain .get(k, {}) returns the None that's actually there.
    detail = ((data or {}).get("data") or {}).get("placeDetail") or {}
    coord = detail.get("coordinate") or {}
    lat = coord.get("latitude")
    lng = coord.get("longitude")
    if lat is None or lng is None:
        return None
    name = detail.get("name") or ""
    return float(lat), float(lng), name


def _coords_from_map_params(url: str) -> tuple[float, float] | None:
    """Coordinates from Naver's own map-viewport params.

    Two shapes in the wild:
      * `?c=126.9784,37.5665,15,0,0,0,dh`  → lng,lat,zoom,…  (7 parts)
      * `?x=126.9784&y=37.5665`            → older v5 links
    The current short-link format is `?c=15.00,0,0,0,dh` — zoom only, no
    coordinates — hence the 6-part minimum before trusting `c`.
    """
    try:
        params = parse_qs(urlparse(url).query)
    except ValueError:
        return None

    parts = (params.get("c") or [""])[0].split(",")
    if len(parts) >= 6:
        try:
            lng, lat = float(parts[0]), float(parts[1])
        except ValueError:
            pass
        else:
            # Reject the zoom-only form (leading zeros) and out-of-range junk.
            if abs(lng) > 1 and abs(lat) > 1 and abs(lng) <= 180 and abs(lat) <= 90:
                return lat, lng

    try:
        return float(params["y"][0]), float(params["x"][0])
    except (KeyError, ValueError, IndexError):
        return None


def _coords_from_at_pattern(url: str) -> tuple[float, float] | None:
    """Extract coordinates from @lat,lng pattern in URL."""
    m = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", url)
    if m:
        return float(m.group(1)), float(m.group(2))
    return None


def _extract_url(text: str) -> str:
    """Extract a Naver Map URL from pasted text that may contain extra lines.

    Handles share text like:
        [NAVER 地图]
        Store Name
        Address line
        https://naver.me/XXXXX
    """
    # Any naver host — share sheets also hand out m.place.naver.com/<類別>/<id>
    # and pcmap.place.naver.com, not just map.naver.com / naver.me.
    m = re.search(r"(https?://(?:naver\.me|[\w.-]*\.?naver\.com)/\S+)", text)
    if m:
        return m.group(1)
    # Also match nmap:// scheme
    m = re.search(r"(nmap://\S+)", text)
    if m:
        return m.group(1)
    return text


def _app_scheme(apple_url: str) -> str:
    """`https://maps.apple.com/?…` → `maps://?…`（直接叫醒「地圖」App）。

    為什麼需要：iOS 的 universal link **不會**因為跨網域 302 而觸發，所以
    「打開 URL → 我們的 /a/ → 302 到 maps.apple.com」只會停在 Safari，
    而且 Apple 現在有網頁版地圖，會渲染成 maps.apple/p/xxxx 的網頁。
    改丟 `maps://` 這個 App scheme，Safari 會直接把它交給「地圖」App。
    """
    return re.sub(r"^https://maps\.apple\.com/", "maps://", apple_url)


def _clean_search_text(text: str) -> str:
    """把 Naver 分享文字整理成適合當搜尋字串的樣子。

    分享出來長這樣（沒抓到座標時會退回搜尋，直接整段丟出去會很醜也搜不準）：

        [NAVER 地图]
        N285酒店仁寺洞
        首尔特别市 钟路区 乐园洞 285

    → 丟掉「[NAVER 地图]」這種括號標頭列，其餘用空白接起來。
    """
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines
             if ln and not re.fullmatch(r"[\[【(（].{0,20}[\]】)）]", ln)]
    return " ".join(lines) if lines else text.strip()


_URL_TOKEN = re.compile(r"\S*(?:https?://|nmap://|naver\.me/|naver\.com/)\S*")


def _strip_urls(text: str) -> str:
    """Drop every URL-ish token — what's left is the店名／地址 worth searching."""
    return " ".join(_URL_TOKEN.sub(" ", text).split()).strip()


def _name_from_share_text(text: str) -> str:
    """First human line of a Naver share blob — used as the map pin's label."""
    for line in text.splitlines():
        line = line.strip()
        if not line or re.fullmatch(r"[\[【(（].{0,20}[\]】)）]", line):
            continue  # 「[NAVER 地图]」這種標頭列
        line = _strip_urls(line)
        if line:
            return line
    return ""


def _build_result(lat: float, lng: float, name: str) -> dict:
    """Build result dict with both Google and Apple Maps URLs.

    When we have a name, use Google's `/place/<name>/@lat,lng` form so the pin
    carries the place label *and* sits on the exact coordinates; otherwise fall
    back to a bare coordinate pin.
    """
    if name:
        label = quote(name)
        google_url = f"https://www.google.com/maps/place/{label}/@{lat},{lng},17z"
        apple_url = f"https://maps.apple.com/?ll={lat},{lng}&q={label}"
    else:
        google_url = f"https://www.google.com/maps/search/?api=1&query={lat},{lng}"
        apple_url = f"https://maps.apple.com/?ll={lat},{lng}&q={lat},{lng}"
    return {
        "lat": lat, "lng": lng, "name": name,
        "google_url": google_url, "apple_url": apple_url,
    }


def _search_result(query: str) -> dict:
    """No coordinates — hand the map app a *text search*, biased to Korea.

    Naver Map only covers Korea, so every fallback query is a Korean place. The
    bias matters because Apple/Google otherwise rank results near the phone
    (Taiwan) and happily return an unrelated local address.
    """
    q = quote(query)
    return {
        "lat": None, "lng": None, "name": query,
        # `ll` + `z` bias Google's search viewport to the Korean peninsula.
        "google_url": f"https://www.google.com/maps/search/{q}/@36.5,127.9,7z",
        # `sll`/`sspn` = "search around here, this wide" (Apple's search-region params).
        "apple_url": f"https://maps.apple.com/?q={q}&sll=36.5,127.9&sspn=6.0,6.0",
    }


@lru_cache(maxsize=512)
def convert(naver_url: str) -> dict:
    """Main conversion: Naver URL → {lat, lng, name, google_url, apple_url}.

    Cached per input (identical link → instant repeat). NaverUnavailable
    propagates (transient — not cached by lru_cache); unparseable links fall
    through to a text-search result rather than erroring.
    """
    raw = naver_url.strip()
    if not raw:
        return {"error": "空的輸入"}

    # Step 0a: extract URL from multi-line share text
    url = _extract_url(raw)

    # Step 0b: resolve short links
    if "naver.me/" in url:
        url = _resolve_short_link(url)

    # Step 1: try lat/lng from URL params
    coords = _coords_from_params(url)
    if coords:
        lat, lng = coords
        name = ""
        place_id = _extract_place_id(url)
        if place_id:
            result = _coords_from_place_api(place_id)
            if result:
                name = result[2]
        return _build_result(lat, lng, name)

    # Step 2: try Place ID → API
    place_id = _extract_place_id(url)
    if place_id:
        result = _coords_from_place_api(place_id)
        if result:
            lat, lng, name = result
            return _build_result(lat, lng, name)

    # Step 3: try @lat,lng pattern
    coords = _coords_from_at_pattern(url)
    if coords:
        lat, lng = coords
        return _build_result(lat, lng, "")

    # Step 3.4: Naver's own viewport params (?c=lng,lat,… / ?x=&y=)
    coords = _coords_from_map_params(url)
    if coords:
        lat, lng = coords
        return _build_result(lat, lng, _name_from_share_text(raw))

    # Step 3.5: address entry URL (/entry/address/CODE,CODE,address)
    addr_match = re.search(r"/entry/address/[^,]+,[^,]+,(.+?)(?:\?|$)", url)
    if addr_match:
        return _search_result(unquote(addr_match.group(1)).strip())

    # Step 4: fallback — search the *human* part of the share text (店名+地址).
    # Never the URL itself: searching "https://naver.me/xxxx" makes the map app
    # match some unrelated place near the user (Taiwan) instead of Korea.
    query = _clean_search_text(unquote(raw))
    query = _strip_urls(query)
    if not query:
        raise ConversionFailed(
            "這條連結抓不到座標，也沒有店名／地址可以搜尋。"
            "請改用 Naver 地圖的「分享」整段文字，或換一條 place 連結。")
    return _search_result(query)


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(__name__)

INDEX_HTML = """\
<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Naver Map → Google / Apple Maps</title>
<style>
:root{--bg:#0f172a;--card:#1e293b;--border:#334155;--text:#e2e8f0;
      --dim:#94a3b8;--green:#22c55e;--blue:#3b82f6;--red:#ef4444}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',system-ui,sans-serif;background:var(--bg);
     color:var(--text);min-height:100vh;display:flex;justify-content:center;
     align-items:flex-start;padding:40px 16px}
.wrap{max-width:560px;width:100%}
h1{font-size:1.4rem;margin-bottom:24px;text-align:center}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;
      padding:20px;margin-bottom:16px}
label{display:block;font-size:.85rem;color:var(--dim);margin-bottom:6px}
textarea{width:100%;padding:10px 12px;border:1px solid var(--border);
      border-radius:8px;background:#0f172a;color:var(--text);font-size:.95rem;
      outline:none;resize:vertical;min-height:80px;font-family:inherit}
textarea:focus{border-color:var(--blue)}
button{width:100%;padding:10px;border:none;border-radius:8px;cursor:pointer;
       font-size:.95rem;font-weight:600;margin-top:12px}
.btn-convert{background:var(--blue);color:#fff}
.btn-convert:hover{opacity:.9}
.btn-open{color:#fff;text-decoration:none;
          display:block;text-align:center;padding:10px;border-radius:8px;
          font-weight:600;margin-top:10px}
.btn-google{background:var(--green)}
.btn-apple{background:#007AFF}
.result{margin-top:16px}
.result .name{font-size:1.1rem;font-weight:700;margin-bottom:6px}
.result .coords{font-size:.85rem;color:var(--dim);margin-bottom:10px}
.error{color:var(--red);margin-top:12px;font-size:.9rem}
.hint{font-size:.8rem;color:var(--dim);margin-top:8px;line-height:1.5}
#result-area{display:none}
#error-area{display:none}
.loading{display:none;text-align:center;color:var(--dim);margin-top:12px}
</style>
</head>
<body>
<div class="wrap">
  <h1>Naver Map → Google / Apple Maps</h1>
  <div class="card">
    <label for="url-input">貼上 Naver Map 連結、或直接輸入地址</label>
    <textarea id="url-input"
           placeholder="https://naver.me/xxxxx&#10;或直接輸入地址：&#10;首尔特别市 中区 苎洞二街 89"></textarea>
    <button class="btn-convert" onclick="doConvert()">轉換</button>
    <div class="loading" id="loading">轉換中...</div>
    <div id="error-area" class="error"></div>
    <div id="result-area" class="result">
      <div class="name" id="r-name"></div>
      <div class="coords" id="r-coords"></div>
      <a class="btn-open btn-google" id="r-link" href="#" target="_blank">
        在 Google Maps 開啟
      </a>
      <a class="btn-open btn-apple" id="r-apple" href="#" target="_blank">
        在 Apple Maps 開啟
      </a>
    </div>
    <div class="hint">
      支援：Naver Map 連結、分享的完整內容、中/韓/英文地址
    </div>
  </div>
  <div class="card" style="text-align:center">
    <a href="/shortcut" style="color:#3b82f6;font-weight:600;text-decoration:none">
      📱 iPhone 捷徑：在 Naver Map 按分享，一鍵開 Apple 地圖 →</a>
    <div class="hint">2 個動作、約 1 分鐘設定，不需要越獄或信任外部捷徑</div>
  </div>
</div>
<script>
const LINKRE=/(https?:[/][/](?:naver[.]me|m?[.]?map[.]naver[.]com)\\S+|nmap:[/][/]\\S+)/g;
function linkLines(text){
  // one entry per line that contains a Naver link; if none, treat whole box as 1
  const lines=text.split('\\n').map(s=>s.trim()).filter(Boolean);
  const withLinks=lines.filter(l=>LINKRE.test(l));
  LINKRE.lastIndex=0;
  return withLinks.length>=2?withLinks:null;
}
function card(d){
  const name=(d.name||'(無名稱)');
  const coords=d.lat!=null?`${d.lat}, ${d.lng}`:'(以文字搜尋)';
  if(d.error) return `<div class="card"><div class="name">⚠️ ${d.input||''}</div>
    <div class="error" style="display:block">${d.error}</div></div>`;
  return `<div class="card"><div class="name">${name}</div>
    <div class="coords">${coords}</div>
    <a class="btn-open btn-google" target="_blank" href="${d.google_url}">在 Google Maps 開啟</a>
    <a class="btn-open btn-apple" target="_blank" href="${d.apple_url}">在 Apple Maps 開啟</a></div>`;
}
async function doConvert(){
  const input=document.getElementById('url-input').value.trim();
  if(!input)return;
  const ra=document.getElementById('result-area');
  const ea=document.getElementById('error-area');
  const ld=document.getElementById('loading');
  const multi=linkLines(input);
  ra.style.display='none';ea.style.display='none';ld.style.display='block';
  try{
    if(multi){ // batch mode
      const r=await fetch('/convert_batch',{method:'POST',
        headers:{'Content-Type':'application/json'},body:JSON.stringify({urls:multi})});
      const d=await r.json();ld.style.display='none';
      if(d.error){ea.textContent=d.error;ea.style.display='block';return}
      ra.innerHTML=`<div class="hint" style="margin:4px 0 10px">共 ${d.count} 筆</div>`
        +d.results.map(card).join('');
      ra.style.display='block';return;
    }
    const r=await fetch('/convert?url='+encodeURIComponent(input));
    const d=await r.json();ld.style.display='none';
    if(d.error){ea.textContent=d.error;ea.style.display='block';return}
    ra.innerHTML=`<div class="name" id="r-name">${d.name||'(無名稱)'}</div>
      <div class="coords">${d.lat!=null?d.lat+', '+d.lng:'(以文字搜尋)'}</div>
      <a class="btn-open btn-google" target="_blank" href="${d.google_url}">在 Google Maps 開啟</a>
      <a class="btn-open btn-apple" target="_blank" href="${d.apple_url}">在 Apple Maps 開啟</a>`;
    ra.style.display='block';
  }catch(e){
    ld.style.display='none';
    ea.textContent='轉換失敗：'+e.message;ea.style.display='block';
  }
}
document.getElementById('url-input').addEventListener('keydown',function(e){
  if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();doConvert();}
});
</script>
</body>
</html>
"""

SHORTCUT_HTML = """\
<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>iPhone 捷徑：Naver Map → Apple 地圖</title>
<style>
:root{--bg:#0f172a;--card:#1e293b;--border:#334155;--text:#e2e8f0;
      --dim:#94a3b8;--green:#22c55e;--blue:#3b82f6;--amber:#f59e0b}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',system-ui,-apple-system,sans-serif;background:var(--bg);
     color:var(--text);min-height:100vh;display:flex;justify-content:center;
     align-items:flex-start;padding:28px 16px;line-height:1.7}
.wrap{max-width:640px;width:100%}
h1{font-size:1.3rem;margin-bottom:6px}
.sub{color:var(--dim);font-size:.92rem;margin-bottom:22px}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;
      padding:18px 20px;margin-bottom:16px}
.card.hero{border-color:#2f5133;background:#16241a}
h2{font-size:1.05rem;margin-bottom:4px}
.tag{display:inline-block;font-size:.72rem;font-weight:700;padding:2px 8px;
     border-radius:99px;margin-bottom:10px}
.tag-a{background:var(--green);color:#04240f}
.tag-b{background:#334155;color:var(--dim)}
ol.steps{list-style:none;counter-reset:s;margin-top:12px}
ol.steps>li{counter-increment:s;position:relative;padding-left:32px;
            margin-bottom:12px;font-size:.95rem}
ol.steps>li::before{content:counter(s);position:absolute;left:0;top:3px;
     width:22px;height:22px;border-radius:50%;background:var(--blue);color:#fff;
     font-size:.78rem;font-weight:700;display:flex;align-items:center;
     justify-content:center}
code{background:#0f172a;border:1px solid var(--border);border-radius:6px;
     padding:2px 6px;font-size:.85em;word-break:break-word;
     font-family:ui-monospace,Menlo,Consolas,monospace}
pre{background:#0f172a;border:1px solid var(--border);border-radius:8px;
    padding:12px;font-size:.85rem;margin:10px 0;
    white-space:pre-wrap;word-break:break-all;
    font-family:ui-monospace,Menlo,Consolas,monospace}
.dl{display:block;text-align:center;background:var(--green);color:#04240f;
    text-decoration:none;font-weight:700;font-size:1rem;padding:13px;
    border-radius:10px;margin:12px 0 6px}
.dl.g{background:var(--blue);color:#fff}
.copy{background:var(--blue);color:#fff;border:none;border-radius:8px;
      padding:8px 14px;font-size:.85rem;font-weight:600;cursor:pointer}
.note{background:#1c2c1e;border-left:3px solid var(--green);
      border-radius:8px;padding:11px 13px;font-size:.88rem;margin-top:12px}
.warn{background:#2c2416;border-left:3px solid var(--amber);
      border-radius:8px;padding:11px 13px;font-size:.88rem;margin-top:12px}
.dim{color:var(--dim);font-size:.86rem}
a{color:var(--blue)}
table{width:100%;border-collapse:collapse;margin-top:10px;font-size:.88rem;
      display:block;overflow-x:auto}
th,td{border:1px solid var(--border);padding:7px 9px;text-align:left;
      vertical-align:top}
td:first-child{white-space:nowrap}
th{background:#0f172a;color:var(--dim);font-weight:600}
hr{border:none;border-top:1px solid var(--border);margin:22px 0 18px}
.back{display:inline-block;margin-top:8px;font-size:.9rem}
</style>
</head>
<body>
<div class="wrap">
  <h1>在 Naver Map 按分享，一鍵開 Apple 地圖</h1>
  <div class="sub">捷徑本身只有<b>一個動作</b>，建一次就永久能用。跟著下面 6 步做。</div>

  <div class="card hero">
    <span class="tag tag-a">照著做 ・ 6 步 ・ 約 1 分鐘</span>
    <h2>手動建立（只有 1 個動作）</h2>
    <ol class="steps">
      <li>打開 iPhone 內建的「<b>捷徑</b>」App → 右上角 <b>+</b></li>
      <li>上面有個搜尋框，打「<b>打開 URL</b>」→ 點<b>「打開 URL」</b>那一項
        <div class="warn" style="margin-top:8px">⚠️ 搜尋結果裡還有一個很像的
        「<b>展開 URL</b>」——<b>那個是錯的</b>，它只會把短網址還原成長網址，
        不會打開任何東西。要選的是「<b>打開</b> URL」。</div></li>
      <li>畫面上會出現一格「打開 URL <span class="dim">（空白欄位）</span>」。
        先按下面的複製鈕，再點那格空白欄位貼上：
        <pre id="ep">https://naver2google.onrender.com/m/</pre>
        <button class="copy" onclick="cp('ep',this)">複製這段網址</button>
        <div class="dim" style="margin-top:8px">結尾是 <code>/m/</code>——
        會直接叫醒「地圖」App。如果用 <code>/a/</code> 會停在 Safari 的
        網頁版地圖（iOS 的 universal link 不吃跨網域轉址）。</div>
      </li>
      <li><b>最關鍵的一步：</b>貼完後游標會停在網址最後面，
        <b>不要移動它</b>，直接點鍵盤<b>正上方那一排</b>裡的
        「<b>捷徑輸入</b>」。<br>
        <span class="dim">點下去會多出一個藍色小方塊，變成
        <code>…/a/ 捷徑輸入</code>。如果那排沒看到「捷徑輸入」，
        往左右滑一下，或先點一下網址欄位讓鍵盤出來。<br>
        網址結尾必須就是 <code>/a/</code> 後面直接接藍色方塊——中間<b>不能有
        引號、反引號或空白</b>（貼上時很容易黏到）。</span></li>
      <li>點畫面最上面的捷徑名稱 → <b>重新命名</b> → 打「<b>用 Apple 地圖開啟</b>」</li>
      <li>點名稱旁邊的 <b>ⓘ</b> → 把「<b>在分享表單中顯示</b>」打開 →
        下面「分享表單類型」要勾 <b>URL</b> <u>和</u> <b>文字</b>
        → 右上角<b>完成</b>
        <div class="warn" style="margin-top:8px">⚠️ <b>「文字」一定要勾。</b>
        Naver Map 分享出來的其實是一整段文字（店名＋地址＋短連結），
        不是單純一個網址——<b>只勾 URL 的話，捷徑根本不會出現在分享表單裡</b>。
        伺服器會自己從那段文字裡把連結挑出來，所以勾了不會有副作用。</div></li>
    </ol>
    <div class="note"><b>好了。</b>到 Naver Map 開任一地點 → <b>分享</b> →
    往下滑找到「用 Apple 地圖開啟」→ 直接跳進 Apple 地圖。<br>
    想要 Google 版就再建一個一模一樣的，只是網址結尾改成 <code>/g/</code>。</div>
    <div class="warn">已經建好、但打開後停在 <b>Safari 的網頁地圖</b>
    （網址變成 <code>maps.apple/p/xxxx</code>）？
    把動作裡的網址從 <code>/a/</code> 改成 <code>/m/</code> 就好，其他都不用動。</div>
  </div>

  <div class="card">
    <h2>為什麼沒有「下載就好」的捷徑檔？</h2>
    <p class="dim">我本來做了一個 <code>.shortcut</code> 檔給你直接匯入，
    但 iOS 會擋下來說「<b>不支援輸入未簽署的捷徑檔案</b>」——
    現在的 iOS 只接受 Apple 官方 iCloud 連結格式的捷徑，
    而那種連結<b>只能從 Apple 裝置上傳產生</b>，我的 Linux 主機做不出來。
    「允許不受信任的捷徑」那個開關也已經救不了這種檔案。</p>
    <p class="dim" style="margin-top:8px">所以上面那 6 步是唯一可靠的做法。
    好消息是它真的只有一個動作，建一次就永久有效。</p>
  </div>

  <div class="card">
    <h2>不想弄捷徑？</h2>
    <p class="dim">在 Naver Map 按分享 → <b>拷貝</b>，然後到
    <a href="/">網頁版</a>貼上，一樣會給你 Apple／Google 地圖的按鈕。
    把網頁版「加入主畫面」就跟一個 App 差不多。</p>
  </div>

  <div class="card">
    <h2>怪怪的時候</h2>
    <table>
      <tr><th>狀況</th><th>怎麼辦</th></tr>
      <tr><td>捷徑沒出現在分享表單</td><td>回捷徑的 ⓘ 確認「在分享表單中顯示」有開、而且勾了 URL</td></tr>
      <tr><td>第一次比較慢</td><td>雲端主機在醒過來，通常 1~2 秒；已設每 8 分鐘保溫</td></tr>
      <tr><td>在家 Wi-Fi 想更快</td><td>把網址換成 <code>http://192.168.50.210:8585/a/</code>（自架版，只有家裡網路通）</td></tr>
      <tr><td>開出來位置怪怪的</td><td>那個地點抓不到座標，會退回用店名搜尋；先在<a href="/">網頁版</a>貼一次看結果</td></tr>
    </table>
    <div class="dim" style="margin-top:12px">技術上：<code>/m/</code>、<code>/a/</code> 和 <code>/g/</code>
    會把後面接的 Naver 網址轉好，再 302 轉到 Apple／Google 地圖，所以捷徑只要「打開 URL」一個動作。
    另有回純文字網址的 <code>/apple</code>、<code>/google</code> 端點可用。</div>
  </div>

  <a class="back" href="/">← 回到網頁版轉換器</a>
</div>
<script>
function cp(id,btn){
  const t=document.getElementById(id).innerText;
  navigator.clipboard.writeText(t).then(()=>{
    const o=btn.innerText;btn.innerText='已複製 ✓';setTimeout(()=>btn.innerText=o,1500);
  });
}
</script>
</body>
</html>
"""


@app.route("/health")
def health():
    return "ok"


@app.route("/")
def index():
    return Response(INDEX_HTML, content_type="text/html; charset=utf-8")


@app.route("/convert")
def api_convert():
    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"error": "缺少 url 參數"}), 400
    try:
        return jsonify(convert(url))
    except ConversionFailed as e:
        return jsonify({"error": str(e)}), 422
    except NaverUnavailable as e:
        return jsonify({"error": f"Naver 暫時無法連線：{e}"}), 503
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"解析失敗：{e}"}), 502


@app.route("/convert_batch", methods=["POST"])
def api_convert_batch():
    """Convert many links at once. Body: {"urls": [...]} or newline text.
    Returns {"results": [{input, ...convert()} | {input, error}]}."""
    payload = request.get_json(silent=True) or {}
    urls = payload.get("urls")
    if urls is None:
        text = payload.get("text") or request.get_data(as_text=True) or ""
        urls = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not urls:
        return jsonify({"error": "沒有可轉換的連結"}), 400
    out = []
    for u in urls[:50]:  # cap to protect the free host
        try:
            out.append({"input": u, **convert(u)})
        except ConversionFailed as e:
            out.append({"input": u, "error": str(e)})
        except NaverUnavailable as e:
            out.append({"input": u, "error": f"Naver 暫時無法連線：{e}"})
        except Exception as e:  # noqa: BLE001
            out.append({"input": u, "error": str(e)})
    return jsonify({"results": out, "count": len(out)})


def _extract_url_arg() -> str:
    """Accept the link from ?url=, a JSON body {"url": ...}, a form field, or a
    raw text body — iOS 捷徑的「取得 URL 內容」用哪種都能通。"""
    url = (request.args.get("url") or "").strip()
    if url or request.method != "POST":
        return url
    # 先讀原始 body（Flask 會快取），否則 form 解析會把 stream 吃掉，
    # 之後 get_data() 就變空字串——純文字 body 那條路會斷掉。
    raw = (request.get_data(as_text=True) or "").strip()
    payload = request.get_json(silent=True)
    if isinstance(payload, dict):
        url = str(payload.get("url") or "").strip()
    if not url:
        url = (request.form.get("url") or "").strip()
    if not url and request.form and len(request.form) == 1:
        # `curl -d 'https://...'` 這種裸網址會被當成「鍵=網址、值=空」
        only_key = next(iter(request.form))
        if not request.form[only_key]:
            url = only_key.strip()
    return url or raw


@app.route("/apple", methods=["GET", "POST"])
@app.route("/google", methods=["GET", "POST"])
def api_plain():
    """回傳「純文字的一行網址」，給 iOS 捷徑直接餵進「打開 URL」用。

    刻意不回 JSON：捷徑要多一個「取得字典值」動作才拿得到，純文字最少步驟。
    """
    target = "apple" if request.path.rstrip("/").endswith("apple") else "google"
    url = _extract_url_arg()
    if not url:
        return Response("缺少 url 參數", status=400,
                        content_type="text/plain; charset=utf-8")
    try:
        result = convert(url)
    except ConversionFailed as e:
        return Response(str(e), status=422,
                        content_type="text/plain; charset=utf-8")
    except NaverUnavailable as e:
        return Response(f"Naver 暫時無法連線：{e}", status=503,
                        content_type="text/plain; charset=utf-8")
    except Exception as e:  # noqa: BLE001
        return Response(f"解析失敗：{e}", status=502,
                        content_type="text/plain; charset=utf-8")
    return Response(result[f"{target}_url"],
                    content_type="text/plain; charset=utf-8")


class _AnyTextConverter(PathConverter):
    """像 <path:> 但也吃換行。

    Naver Map 的「分享」給出來的是**整段文字**（標題+地址+短連結），不是單一
    網址。捷徑把它塞進 URL 後換行會編成 %0A，Flask 解碼後預設的 <path:> 比對
    不到（它的 `.` 不匹配換行）→ 整條路由 404。用 [\\s\\S] 明確納入換行。
    """

    regex = r"[^/][\s\S]*"


app.url_map.converters["anytext"] = _AnyTextConverter


@app.route("/a/<anytext:rest>")
@app.route("/g/<anytext:rest>")
@app.route("/m/<anytext:rest>")
def api_path_redirect(rest: str):
    """把 Naver 網址直接接在路徑後面 → 302 到 Apple/Google 地圖。

        /a/https://naver.me/xxxxx      → Apple 地圖（https 連結）
        /m/https://naver.me/xxxxx      → Apple 地圖（maps:// 直接開 App）
        /g/naver.me/xxxxx              → Google 地圖（scheme 可省略）

    這樣 iOS 捷徑只要**一個動作**（打開 URL），不必 POST、不必 URL 編碼。
    注意：Safari/Werkzeug 會把連續斜線壓成一個，所以 https:/ 也要收。
    """
    if request.path.startswith("/g/"):
        target, app_scheme = "google", False
    else:
        # /m/ = 直接叫醒「地圖」App（maps://），/a/ = 一般 https 連結
        target, app_scheme = "apple", request.path.startswith("/m/")
    url = rest.strip()
    if request.query_string:
        url += "?" + request.query_string.decode("utf-8", "replace")
    # 使用者手打／貼上時常黏到反引號、引號、角括號或全形空白，先刮掉再判斷。
    url = url.strip("`'\"<> \t\u3000")
    url = re.sub(r"^(https?):/{1,2}", r"\1://", url)      # https:/x → https://x
    if not url.startswith(("http://", "https://", "nmap://")):
        # 只有「看起來像裸網域」才補 scheme。分享出來的整段文字（店名、地址、
        # 甚至「[NAVER 地图]」開頭）不能補——補了會變成 https://[NAVER 地图]…，
        # urlparse 當成 IPv6 主機直接 ValueError（Invalid IPv6 URL）。
        # 讓它以原樣進 convert()：裡面會先撈連結，撈不到就當地址搜尋。
        if re.match(r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?:[/:?#]|$)", url):
            url = "https://" + url
    try:
        result = convert(url)
    except ConversionFailed as e:
        return Response(str(e), status=422,
                        content_type="text/plain; charset=utf-8")
    except NaverUnavailable as e:
        return Response(f"Naver 暫時無法連線：{e}", status=503,
                        content_type="text/plain; charset=utf-8")
    except Exception as e:  # noqa: BLE001
        return Response(f"解析失敗：{e}", status=502,
                        content_type="text/plain; charset=utf-8")
    dest = result[f"{target}_url"]
    if not app_scheme:
        return redirect(dest)
    # 不能用 302：werkzeug 會對 Location 做 iri_to_uri 正規化，把 `maps://?…`
    # 的空 authority 砍成 `maps:?…`。改回一頁 HTML 用 JS 跳轉，字串原封不動
    # 交給 Safari，順便留一顆按鈕給自動跳轉被擋下來的情況。
    return Response(
        _APP_JUMP_HTML.replace("__APP__", _app_scheme(dest)).replace("__WEB__", dest),
        content_type="text/html; charset=utf-8")


_APP_JUMP_HTML = """\
<!DOCTYPE html><html lang="zh-TW"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>正在打開地圖…</title>
<style>body{font-family:-apple-system,system-ui,sans-serif;background:#0f172a;
color:#e2e8f0;display:flex;flex-direction:column;align-items:center;
justify-content:center;min-height:100vh;margin:0;padding:24px;text-align:center}
a{display:block;width:100%;max-width:320px;margin:8px 0;padding:14px;
border-radius:10px;text-decoration:none;font-weight:700}
.app{background:#22c55e;color:#04240f}.web{background:#1e293b;color:#e2e8f0}
p{color:#94a3b8;font-size:.9rem;margin:0 0 18px}</style></head><body>
<p id="msg">正在打開「地圖」App…</p>
<a class="app" href="__APP__">打開「地圖」App</a>
<a class="web" href="__WEB__">改用網頁版地圖</a>
<script>
location.href="__APP__";
setTimeout(function(){document.getElementById('msg').textContent='沒自動跳過去的話，按下面的按鈕';},1200);
</script>
</body></html>
"""


SHORTCUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shortcuts")


@app.route("/dl/<name>.shortcut")
def download_shortcut(name: str):
    """已停用。

    iOS 現在直接拒絕未簽章的捷徑檔（「不支援輸入未簽署的捷徑檔案」），
    連「允許不受信任的捷徑」也救不了；能匯入的只有 Apple 官方 iCloud 連結，
    而那種連結只能從 Apple 裝置上傳產生。檔案與產生器留在 shortcuts/ 供參考，
    但不再提供下載，免得使用者踩到那個錯誤訊息。
    """
    return Response(
        "iOS 不接受未簽署的捷徑檔（會顯示「不支援輸入未簽署的捷徑檔案」），\n"
        "所以這個下載已停用。請改用 /shortcut 頁面上的手動步驟——只有一個動作。",
        status=410, content_type="text/plain; charset=utf-8")


@app.route("/shortcut")
def shortcut_guide():
    return Response(SHORTCUT_HTML, content_type="text/html; charset=utf-8")


@app.route("/go")
def api_go():
    url = request.args.get("url", "").strip()
    if not url:
        return "缺少 url 參數", 400
    target = request.args.get("target", "google").strip().lower()
    try:
        result = convert(url)
        if "error" in result:
            return f"Error: {result['error']}", 422
        return redirect(result["apple_url"] if target == "apple"
                        else result["google_url"])
    except NaverUnavailable as e:
        return f"Naver 暫時無法連線：{e}", 503
    except Exception as e:  # noqa: BLE001
        return f"Error: {e}", 502


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Naver Map → Google Maps 轉換器")
    parser.add_argument("--port", type=int, default=8585)
    args = parser.parse_args()
    app.run(host="0.0.0.0", port=args.port, debug=False)


if __name__ == "__main__":
    main()
