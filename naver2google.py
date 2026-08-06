"""Naver Map → Google/Apple Maps 轉換器

用法：
    python naver2google.py [--port 8585]

Web UI:  http://<LAN-IP>:8585
API:     GET /convert?url=NAVER_URL  → JSON (含 google_url + apple_url)
Redirect: GET /go?url=NAVER_URL[&target=apple]  → 302 to Google/Apple Maps
"""

from __future__ import annotations

import argparse
import re
from functools import lru_cache
from urllib.parse import urlparse, parse_qs, quote, unquote

import requests as http_client
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask, request, jsonify, redirect, Response

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
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    if "lat" in params and "lng" in params:
        try:
            return float(params["lat"][0]), float(params["lng"][0])
        except (ValueError, IndexError):
            pass
    return None


def _extract_place_id(url: str) -> str | None:
    """Extract numeric place ID from /place/12345 in the URL path."""
    m = re.search(r"/place/(\d+)", url)
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
    detail = data.get("data", {}).get("placeDetail", {})
    coord = detail.get("coordinate", {})
    lat = coord.get("latitude")
    lng = coord.get("longitude")
    if lat is None or lng is None:
        return None
    name = detail.get("name", "")
    return float(lat), float(lng), name


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
    m = re.search(r"(https?://(?:naver\.me|map\.naver\.com|m\.map\.naver\.com)\S+)", text)
    if m:
        return m.group(1)
    # Also match nmap:// scheme
    m = re.search(r"(nmap://\S+)", text)
    if m:
        return m.group(1)
    return text


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

    # Step 3.5: address entry URL (/entry/address/CODE,CODE,address)
    addr_match = re.search(r"/entry/address/[^,]+,[^,]+,(.+?)(?:\?|$)", url)
    if addr_match:
        query = unquote(addr_match.group(1)).strip()
        return {
            "lat": None, "lng": None, "name": query,
            "google_url": f"https://www.google.com/maps/search/{quote(query)}",
            "apple_url": f"https://maps.apple.com/?q={quote(query)}",
        }

    # Step 4: fallback — pass as search query
    query = unquote(url)
    return {
        "lat": None, "lng": None, "name": query,
        "google_url": f"https://www.google.com/maps/search/{quote(query)}",
        "apple_url": f"https://maps.apple.com/?q={quote(query)}",
    }


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
</div>
<script>
const LINKRE=/(https?:[/][/](?:naver[.]me|m?[.]?map[.]naver[.]com)\\S+|nmap:[/][/]\\S+)/g;
function linkLines(text){
  // one entry per line that contains a Naver link; if none, treat whole box as 1
  const lines=text.split('\n').map(s=>s.trim()).filter(Boolean);
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
        except NaverUnavailable as e:
            out.append({"input": u, "error": f"Naver 暫時無法連線：{e}"})
        except Exception as e:  # noqa: BLE001
            out.append({"input": u, "error": str(e)})
    return jsonify({"results": out, "count": len(out)})


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
