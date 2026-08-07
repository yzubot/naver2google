"""Tests for naver2google — pure/parsing logic runs offline; network paths are
monkeypatched so the suite is deterministic and CI-safe.

    python -m pytest test_naver2google.py -q
"""
import naver2google as n


# -- URL extraction from share text --------------------------------------
def test_extract_url_short_link():
    txt = "[NAVER 지도]\n某某餐廳\n서울 중구...\nhttps://naver.me/xABC123"
    assert n._extract_url(txt) == "https://naver.me/xABC123"


def test_extract_url_full_link():
    assert n._extract_url("看這 https://map.naver.com/p/entry/place/1234?c=15") \
        == "https://map.naver.com/p/entry/place/1234?c=15"


def test_extract_url_nmap_scheme():
    assert n._extract_url("nmap://place?id=999&appMenu=location").startswith("nmap://")


def test_extract_url_plain_address_passthrough():
    assert n._extract_url("서울특별시 중구 저동2가 89") == "서울특별시 중구 저동2가 89"


# -- coordinate parsers ---------------------------------------------------
def test_coords_from_params():
    assert n._coords_from_params("https://x?lat=37.5&lng=127.0") == (37.5, 127.0)


def test_coords_from_at_pattern():
    assert n._coords_from_at_pattern("https://x/@37.5665,126.9780,17z") == (37.5665, 126.9780)


def test_place_id_from_entry_place():
    # modern share links resolve to /entry/place/<id> — must still be caught
    assert n._extract_place_id("https://map.naver.com/p/entry/place/1928374") == "1928374"


def test_place_id_from_legacy_place():
    assert n._extract_place_id("https://map.naver.com/v5/place/12345") == "12345"


# -- URL builders ---------------------------------------------------------
def test_build_result_with_name_pins_coords_and_label():
    r = n._build_result(37.5, 127.0, "명동교자")
    assert "37.5,127.0" in r["google_url"] and "%" in r["google_url"]  # name url-encoded
    assert r["apple_url"].startswith("https://maps.apple.com/?ll=37.5,127.0")


def test_build_result_without_name():
    r = n._build_result(37.5, 127.0, "")
    assert "query=37.5,127.0" in r["google_url"]


# -- convert() end-to-end with network mocked ----------------------------
def test_convert_place_link(monkeypatch):
    n.convert.cache_clear()
    monkeypatch.setattr(n, "_resolve_short_link", lambda u: "https://map.naver.com/p/entry/place/777")
    monkeypatch.setattr(n, "_coords_from_place_api", lambda pid: (37.5, 127.0, "테스트") if pid == "777" else None)
    r = n.convert("https://naver.me/short")
    assert r["lat"] == 37.5 and r["name"] == "테스트"


def test_convert_address_fallback(monkeypatch):
    """Naver 也查不到時，才退回讓地圖 App 自己搜文字。"""
    n.convert.cache_clear()
    monkeypatch.setattr(n, "_search_naver", lambda q: None)
    r = n.convert("서울특별시 중구 저동2가 89")
    assert r["lat"] is None and "google.com/maps/search" in r["google_url"]


def test_convert_naver_down_raises(monkeypatch):
    n.convert.cache_clear()
    def boom(_u):
        raise n.NaverUnavailable("blocked")
    monkeypatch.setattr(n, "_resolve_short_link", boom)
    import pytest
    with pytest.raises(n.NaverUnavailable):
        n.convert("https://naver.me/x")


# -- /apple, /google plain-text endpoints (iOS 捷徑用) ---------------------
def _client(monkeypatch):
    n.convert.cache_clear()
    monkeypatch.setattr(
        n, "_resolve_short_link", lambda u: "https://map.naver.com/p/entry/place/777"
    )
    monkeypatch.setattr(
        n, "_coords_from_place_api",
        lambda pid: (37.5, 127.0, "테스트") if pid == "777" else None,
    )
    n.app.config["TESTING"] = True
    return n.app.test_client()


def test_apple_endpoint_get(monkeypatch):
    c = _client(monkeypatch)
    r = c.get("/apple?url=https://naver.me/short")
    assert r.status_code == 200
    assert r.mimetype == "text/plain"
    assert r.get_data(as_text=True).startswith("https://maps.apple.com/?ll=37.5,127.0")


def test_apple_endpoint_post_json(monkeypatch):
    """捷徑「取得 URL 內容」POST + JSON 內文的走法。"""
    c = _client(monkeypatch)
    r = c.post("/apple", json={"url": "https://naver.me/short"})
    assert r.status_code == 200
    assert "maps.apple.com" in r.get_data(as_text=True)


def test_apple_endpoint_post_raw_body(monkeypatch):
    c = _client(monkeypatch)
    r = c.post("/apple", data="https://naver.me/short", content_type="text/plain")
    assert r.status_code == 200
    assert "maps.apple.com" in r.get_data(as_text=True)


def test_apple_endpoint_post_bare_form_body(monkeypatch):
    """裸網址被當成 form 的「鍵」時也要能取到（curl -d '<url>'）。"""
    c = _client(monkeypatch)
    r = c.post("/apple", data="https://naver.me/short",
               content_type="application/x-www-form-urlencoded")
    assert r.status_code == 200
    assert "maps.apple.com" in r.get_data(as_text=True)


def test_google_endpoint_post_json(monkeypatch):
    c = _client(monkeypatch)
    r = c.post("/google", json={"url": "https://naver.me/short"})
    assert r.status_code == 200
    assert "google.com/maps" in r.get_data(as_text=True)


def test_plain_endpoint_missing_url(monkeypatch):
    c = _client(monkeypatch)
    assert c.get("/apple").status_code == 400


def test_plain_endpoint_naver_down(monkeypatch):
    c = _client(monkeypatch)
    def boom(_u):
        raise n.NaverUnavailable("blocked")
    monkeypatch.setattr(n, "_resolve_short_link", boom)
    n.convert.cache_clear()
    assert c.get("/apple?url=https://naver.me/x").status_code == 503


def test_shortcut_guide_page(monkeypatch):
    c = _client(monkeypatch)
    r = c.get("/shortcut")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "打開 URL" in body and "/a/" in body
    assert "未簽署的捷徑檔案" in body        # 說明為什麼沒有下載版


# -- /a/ /g/ 一個動作用的路徑轉址 -------------------------------------------
def test_path_redirect_apple(monkeypatch):
    c = _client(monkeypatch)
    r = c.get("/a/https://naver.me/short")
    assert r.status_code == 302
    assert r.headers["Location"].startswith("https://maps.apple.com/?ll=37.5,127.0")


def test_path_redirect_google(monkeypatch):
    c = _client(monkeypatch)
    r = c.get("/g/https://naver.me/short")
    assert r.status_code == 302
    assert "google.com/maps" in r.headers["Location"]


def test_path_redirect_collapsed_slashes(monkeypatch):
    """Safari/Werkzeug 會把 https:// 壓成 https:/ ，也要能還原。"""
    c = _client(monkeypatch)
    r = c.get("/a/https:/naver.me/short")
    assert r.status_code == 302
    assert "maps.apple.com" in r.headers["Location"]


def test_path_redirect_without_scheme(monkeypatch):
    c = _client(monkeypatch)
    r = c.get("/a/naver.me/short")
    assert r.status_code == 302
    assert "maps.apple.com" in r.headers["Location"]


def test_download_shortcut_is_gone(monkeypatch):
    """iOS 擋未簽章捷徑檔，下載已停用——要回 410 + 說明，不能再送出檔案。"""
    c = _client(monkeypatch)
    r = c.get("/dl/naver-to-apple-maps.shortcut")
    assert r.status_code == 410
    assert "未簽署" in r.get_data(as_text=True)


def test_download_shortcut_unknown_name(monkeypatch):
    c = _client(monkeypatch)
    assert c.get("/dl/whatever.shortcut").status_code == 410


def test_path_redirect_strips_stray_quotes(monkeypatch):
    """使用者貼上時常黏到反引號/引號，不該因此壞掉。"""
    c = _client(monkeypatch)
    for junk in ("`https://naver.me/short", "'https://naver.me/short'",
                 "<https://naver.me/short>", "%60https://naver.me/short"):
        r = c.get(f"/a/{junk}")
        assert r.status_code == 302, junk
        assert "maps.apple.com" in r.headers["Location"], junk


# -- 回歸：HTML 樣板不是 raw string，JS 裡的 \n 會被 Python 先吃掉 -----------
def _script_blocks(html: str) -> list[str]:
    import re
    return re.findall(r"(?is)<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html)


def _has_unterminated_string_literal(js: str) -> bool:
    """字串字面值裡出現真正的換行 → JS 直接 SyntaxError，整頁腳本失效。"""
    for line in js.split("\n"):
        for quote in ("'", '"'):
            n, esc = 0, False
            for ch in line:
                if esc:
                    esc = False
                    continue
                if ch == "\\":
                    esc = True
                elif ch == quote:
                    n += 1
            if n % 2 == 1:
                return True
    return False


def test_index_script_has_no_broken_string_literal(monkeypatch):
    """曾經 split('\\n') 的 \\n 被 Python 解成真換行，doConvert 整個沒定義，
    網頁按「轉換」完全沒反應。這條測試就是防它再發生。"""
    c = _client(monkeypatch)
    for path in ("/", "/shortcut"):
        body = c.get(path).get_data(as_text=True)
        for js in _script_blocks(body):
            assert not _has_unterminated_string_literal(js), path


def test_index_newline_split_is_escaped(monkeypatch):
    c = _client(monkeypatch)
    body = c.get("/").get_data(as_text=True)
    assert r"split('\n')" in body      # 送到瀏覽器的必須是反斜線+n


def test_path_redirect_accepts_full_share_text(monkeypatch):
    """Naver Map 分享出來的是整段文字（標題+地址+短連結），不是單一網址。
    換行會被編成 %0A，預設的 <path:> 比對不到 → 曾整條路由 404。"""
    c = _client(monkeypatch)
    text = ("[NAVER 地图]\nN285酒店仁寺洞\n首尔特别市 钟路区 乐园洞 285\n"
            "https://naver.me/short")
    from urllib.parse import quote
    r = c.get("/a/" + quote(text, safe=""))
    assert r.status_code == 302
    assert "maps.apple.com" in r.headers["Location"]


# -- 分享文字沒有連結時的退路 ---------------------------------------------
def test_convert_share_text_without_link_does_not_crash():
    """含「[NAVER 地图]」的文字被補上 https:// 後，urlparse 會丟
    'Invalid IPv6 URL'（方括號被當成 IPv6 主機）。要退回搜尋而不是 502。"""
    n.convert.cache_clear()
    r = n.convert("[NAVER 地图]\nN285酒店仁寺洞\n首尔特别市 钟路区 乐园洞 285")
    assert r["lat"] is None
    assert "[NAVER" not in r["name"]          # 括號標頭列要被丟掉
    assert "N285酒店仁寺洞" in r["name"]


def test_coords_from_params_survives_bad_url():
    assert n._coords_from_params("https://[NAVER 地图]/x") is None


def test_clean_search_text_drops_bracket_header():
    assert n._clean_search_text("[NAVER 地图]\nA\nB") == "A B"
    assert n._clean_search_text("【地圖】\n首爾") == "首爾"
    assert n._clean_search_text("只有一行") == "只有一行"


def test_path_redirect_share_text_without_link(monkeypatch):
    c = _client(monkeypatch)
    from urllib.parse import quote
    r = c.get("/a/" + quote("[NAVER 地图]\nN285酒店仁寺洞", safe=""))
    assert r.status_code == 302
    loc = r.headers["Location"]
    assert "maps.apple.com" in loc and "%5BNAVER" not in loc


def test_path_redirect_does_not_prepend_scheme_to_plain_text(monkeypatch):
    """純文字不能被當網域補 https://，否則 urlparse 直接炸。"""
    c = _client(monkeypatch)
    from urllib.parse import quote
    r = c.get("/a/" + quote("首尔特别市 钟路区 乐园洞 285", safe=""))
    assert r.status_code == 302


# -- /m/ App scheme（universal link 不會因跨網域 302 而觸發）----------------
def test_app_scheme_conversion():
    assert n._app_scheme("https://maps.apple.com/?ll=1,2&q=x") == "maps://?ll=1,2&q=x"
    assert n._app_scheme("https://www.google.com/maps") == "https://www.google.com/maps"


def test_m_route_serves_jump_page(monkeypatch):
    """/m/ 不能用 302——werkzeug 會把 maps://?… 正規化成 maps:?…。
    改回 HTML 由 JS 跳轉，字串要原封不動。"""
    c = _client(monkeypatch)
    r = c.get("/m/https://naver.me/short")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert 'location.href="maps://?ll=37.5,127.0' in body
    assert "maps:?" not in body                      # 沒被砍掉 authority
    assert 'href="https://maps.apple.com/' in body   # 網頁版備援按鈕還在


def test_a_route_still_plain_redirect(monkeypatch):
    c = _client(monkeypatch)
    r = c.get("/a/https://naver.me/short")
    assert r.status_code == 302
    assert r.headers["Location"].startswith("https://maps.apple.com/")


# -- regression: 抓不到座標時「亂跳到台灣某地址」 -------------------------------
# 舊行為：抓不到座標就把「整條網址」當搜尋字串丟給地圖 App，地圖搜不到就在使用者
# 附近（台灣）隨便給一個結果 —— 錯得像對的。現在改成寧可報錯。
def test_place_id_from_m_place_category_path():
    """m.place.naver.com 用「類別」當路徑段，不是 /place/ —— 以前完全抓不到。"""
    for path in ("restaurant", "accommodation", "hairshop", "attraction", "cafe"):
        url = f"https://m.place.naver.com/{path}/1093936086/home"
        assert n._extract_place_id(url) == "1093936086", url
    assert n._extract_place_id("https://pcmap.place.naver.com/restaurant/777/home") == "777"


def test_extract_url_picks_up_place_host():
    txt = "[NAVER 지도]\nN285호텔\nhttps://m.place.naver.com/accommodation/555/home"
    assert n._extract_url(txt) == "https://m.place.naver.com/accommodation/555/home"


def test_place_api_survives_all_null_payload(monkeypatch):
    """不存在的 id 仍回 200，但每個欄位都是 null → 以前 NoneType 直接炸。"""
    class _Resp:
        status_code = 200
        @staticmethod
        def json():
            return {"data": {"placeDetail": {"name": None, "coordinate": None}}}
    monkeypatch.setattr(n.SESSION, "get", lambda *a, **k: _Resp())
    assert n._coords_from_place_api("1093936086") is None


def test_coords_from_map_params_c_param():
    assert n._coords_from_map_params(
        "https://map.naver.com/p/?c=126.9784,37.5665,15,0,0,0,dh") == (37.5665, 126.9784)


def test_coords_from_map_params_ignores_zoom_only_c():
    # 現行短連結格式 `c=15.00,0,0,0,dh` 沒有座標，別把 15/0 當成經緯度
    assert n._coords_from_map_params(
        "https://map.naver.com/p/entry/place/1?c=15.00,0,0,0,dh") is None


def test_coords_from_map_params_xy():
    assert n._coords_from_map_params("https://map.naver.com/v5/?x=126.9784&y=37.5665") \
        == (37.5665, 126.9784)


def test_convert_refuses_to_search_a_bare_url(monkeypatch):
    """短連結解不開時，不可以拿網址本身去搜尋 —— 要報錯。"""
    import pytest
    n.convert.cache_clear()
    monkeypatch.setattr(n, "_resolve_short_link", lambda u: u)  # 404 短連結：原樣回來
    with pytest.raises(n.ConversionFailed):
        n.convert("https://naver.me/deadlink")


def test_convert_share_text_falls_back_to_name_not_url(monkeypatch):
    n.convert.cache_clear()
    monkeypatch.setattr(n, "_search_naver", lambda q: None)
    monkeypatch.setattr(n, "_resolve_short_link", lambda u: u)
    r = n.convert("[NAVER 지도]\nN285호텔 인사동\n서울특별시 종로구 낙원동 285\nhttps://naver.me/dead")
    assert "naver" not in r["name"] and "http" not in r["name"]
    assert "N285" in r["name"] and "종로구" in r["name"]


def test_search_fallback_is_biased_to_korea():
    r = n._search_result("종로구 낙원동 285")
    assert "sll=36.5,127.9" in r["apple_url"]      # Apple 否則會在使用者附近搜
    assert "@36.5,127.9" in r["google_url"]


def test_path_redirect_reports_failure_instead_of_jumping(monkeypatch):
    n.convert.cache_clear()
    monkeypatch.setattr(n, "_resolve_short_link", lambda u: u)
    n.app.config["TESTING"] = True
    r = n.app.test_client().get("/a/https://naver.me/deadlink")
    assert r.status_code == 422           # 不是 302 —— 絕不能把人送到錯的地方
    assert "Location" not in r.headers


# -- Naver 反查座標（fallback 從「猜」變成「查」） ------------------------------
_SEARCH_HTML = """<html><script>
  window.__RQ_STREAMING_STATE__.push({"queries":[{"state":{"data":{
    "item":{"myLocation":{"latitude":37.5664267,"longitude":126.9778715}},
    "items":[{"name":"N285호텔 인사동","latitude":37.5724089,"longitude":126.987433,
              "address":"서울특별시 종로구 낙원동 285"}]}}}]})
</script></html>"""


def test_collect_places_skips_my_location():
    """myLocation 排在結果前面且是「首爾市中心」預設值 —— 撿到它=每次都釘錯點。"""
    blob = next(n._iter_state_blobs(_SEARCH_HTML))
    places = n._collect_places(blob)
    assert [p["name"] for p in places] == ["N285호텔 인사동"]
    assert (places[0]["lat"], places[0]["lng"]) == (37.5724089, 126.987433)


def test_search_naver_returns_exact_coords(monkeypatch):
    class _Resp:
        status_code = 200
        text = _SEARCH_HTML
    monkeypatch.setattr(n.SESSION, "get", lambda *a, **k: _Resp())
    n._search_naver.cache_clear()
    assert n._search_naver("N285호텔 인사동") == (37.5724089, 126.987433, "N285호텔 인사동")


def test_resolve_by_text_prefers_geocode_over_blind_search(monkeypatch):
    monkeypatch.setattr(n, "_search_naver", lambda q: (37.5724089, 126.987433, "N285"))
    r = n._resolve_by_text("N285호텔 인사동")
    assert r["lat"] == 37.5724089                       # 精確座標，不是丟去搜
    assert r["apple_url"].startswith("https://maps.apple.com/?ll=37.5724089,126.987433")


def test_search_naver_network_error_is_not_fatal(monkeypatch):
    import requests
    def boom(*a, **k):
        raise requests.RequestException("down")
    monkeypatch.setattr(n.SESSION, "get", boom)
    n._search_naver.cache_clear()
    assert n._search_naver("아무거나") is None          # 加分路徑失敗不該炸掉整條轉換


_AD_FIRST_HTML = """<html><script>
  window.__RQ_STREAMING_STATE__.push({"queries":[{"state":{"data":{"items":[
    {"name":"강남교자 센터원점","latitude":37.5674482,"longitude":126.9851758,
     "address":"서울특별시 중구 수하동 67"},
    {"name":"명동교자 1호점","latitude":37.5634828,"longitude":126.9851666,
     "address":"서울특별시 중구 명동2가 33-4"}]}}}]})
</script></html>"""


def test_search_naver_skips_the_paid_listing(monkeypatch):
    """Naver 把廣告排第一：搜「명동교자」第一筆是「강남교자」——照抄第一筆就送錯地方。"""
    class _Resp:
        status_code = 200
        text = _AD_FIRST_HTML
    monkeypatch.setattr(n.SESSION, "get", lambda *a, **k: _Resp())
    n._search_naver.cache_clear()
    assert n._search_naver("명동교자") == (37.5634828, 126.9851666, "명동교자 1호점")


def test_search_naver_keeps_naver_order_when_nothing_matches(monkeypatch):
    """全部都不匹配時，尊重 Naver 自己的排序（不要亂挑）。"""
    class _Resp:
        status_code = 200
        text = _AD_FIRST_HTML
    monkeypatch.setattr(n.SESSION, "get", lambda *a, **k: _Resp())
    n._search_naver.cache_clear()
    assert n._search_naver("zzz")[2] == "강남교자 센터원점"


def test_score_prefers_full_name_inside_share_text():
    share = "N285호텔 인사동 서울특별시 종로구 낙원동 285"
    exact = {"name": "N285호텔 인사동", "address": "서울특별시 종로구 낙원동 285"}
    nearby = {"name": "더하노이풋앤바디 N285호텔 인사동점",
              "address": "서울특별시 종로구 낙원동 285 지하2층"}
    assert n._score_place(exact, share) > n._score_place(nearby, share)
