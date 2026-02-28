"""Naver Map → Google Maps 轉換器

用法：
    python naver2google.py [--port 8585]

Web UI:  http://<LAN-IP>:8585
API:     GET /convert?url=NAVER_URL  → JSON
Redirect: GET /go?url=NAVER_URL      → 302 to Google Maps
"""

from __future__ import annotations

import argparse
import re
from urllib.parse import urlparse, parse_qs, quote, unquote

import requests as http_client
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


# ---------------------------------------------------------------------------
# Coordinate extraction
# ---------------------------------------------------------------------------

def _resolve_short_link(url: str) -> str:
    """Follow naver.me redirect to get the full URL."""
    resp = http_client.head(
        url, allow_redirects=True, timeout=10, headers=NAVER_HEADERS,
    )
    return resp.url


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
    """Call Naver Place Summary API to get coordinates and name."""
    try:
        resp = http_client.get(
            PLACE_API.format(place_id),
            headers=NAVER_HEADERS,
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        detail = data.get("data", {}).get("placeDetail", {})
        coord = detail.get("coordinate", {})
        lat = coord.get("latitude")
        lng = coord.get("longitude")
        if lat is None or lng is None:
            return None
        name = detail.get("name", "")
        return float(lat), float(lng), name
    except Exception:
        return None


def _coords_from_at_pattern(url: str) -> tuple[float, float] | None:
    """Extract coordinates from @lat,lng pattern in URL."""
    m = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", url)
    if m:
        return float(m.group(1)), float(m.group(2))
    return None


def convert(naver_url: str) -> dict:
    """Main conversion: Naver URL → {lat, lng, name, google_url}."""
    url = naver_url.strip()
    if not url:
        return {"error": "空的輸入"}

    # Step 0: resolve short links
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
        return {
            "lat": lat, "lng": lng, "name": name,
            "google_url": f"https://www.google.com/maps?q={lat},{lng}",
        }

    # Step 2: try Place ID → API
    place_id = _extract_place_id(url)
    if place_id:
        result = _coords_from_place_api(place_id)
        if result:
            lat, lng, name = result
            return {
                "lat": lat, "lng": lng, "name": name,
                "google_url": f"https://www.google.com/maps?q={lat},{lng}",
            }

    # Step 3: try @lat,lng pattern
    coords = _coords_from_at_pattern(url)
    if coords:
        lat, lng = coords
        return {
            "lat": lat, "lng": lng, "name": "",
            "google_url": f"https://www.google.com/maps?q={lat},{lng}",
        }

    # Step 4: fallback — pass as Google Maps search query
    query = unquote(url)
    return {
        "lat": None, "lng": None, "name": query,
        "google_url": f"https://www.google.com/maps/search/{quote(query)}",
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
<title>Naver Map → Google Maps</title>
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
input[type=text]{width:100%;padding:10px 12px;border:1px solid var(--border);
      border-radius:8px;background:#0f172a;color:var(--text);font-size:.95rem;
      outline:none}
input[type=text]:focus{border-color:var(--blue)}
button{width:100%;padding:10px;border:none;border-radius:8px;cursor:pointer;
       font-size:.95rem;font-weight:600;margin-top:12px}
.btn-convert{background:var(--blue);color:#fff}
.btn-convert:hover{opacity:.9}
.btn-open{background:var(--green);color:#fff;text-decoration:none;
          display:block;text-align:center;padding:10px;border-radius:8px;
          font-weight:600;margin-top:10px}
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
  <h1>Naver Map → Google Maps</h1>
  <div class="card">
    <label for="url-input">貼上 Naver Map 連結或韓文地址</label>
    <input type="text" id="url-input"
           placeholder="https://naver.me/xxxxx 或 韓文地址">
    <button class="btn-convert" onclick="doConvert()">轉換</button>
    <div class="loading" id="loading">轉換中...</div>
    <div id="error-area" class="error"></div>
    <div id="result-area" class="result">
      <div class="name" id="r-name"></div>
      <div class="coords" id="r-coords"></div>
      <a class="btn-open" id="r-link" href="#" target="_blank">
        在 Google Maps 開啟
      </a>
    </div>
    <div class="hint">
      支援格式：naver.me 短網址、map.naver.com 連結、nmap:// 連結、韓文地址
    </div>
  </div>
</div>
<script>
async function doConvert(){
  const input=document.getElementById('url-input').value.trim();
  if(!input)return;
  const ra=document.getElementById('result-area');
  const ea=document.getElementById('error-area');
  const ld=document.getElementById('loading');
  ra.style.display='none';ea.style.display='none';ld.style.display='block';
  try{
    const r=await fetch('/convert?url='+encodeURIComponent(input));
    const d=await r.json();
    ld.style.display='none';
    if(d.error){ea.textContent=d.error;ea.style.display='block';return}
    document.getElementById('r-name').textContent=d.name||'(無名稱)';
    document.getElementById('r-coords').textContent=
      d.lat!=null?`${d.lat}, ${d.lng}`:'(以文字搜尋)';
    document.getElementById('r-link').href=d.google_url;
    ra.style.display='block';
  }catch(e){
    ld.style.display='none';
    ea.textContent='轉換失敗：'+e.message;ea.style.display='block';
  }
}
document.getElementById('url-input').addEventListener('keydown',function(e){
  if(e.key==='Enter')doConvert();
});
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return Response(INDEX_HTML, content_type="text/html; charset=utf-8")


@app.route("/convert")
def api_convert():
    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"error": "缺少 url 參數"}), 400
    try:
        result = convert(url)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/go")
def api_go():
    url = request.args.get("url", "").strip()
    if not url:
        return "缺少 url 參數", 400
    try:
        result = convert(url)
        return redirect(result["google_url"])
    except Exception as e:
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
