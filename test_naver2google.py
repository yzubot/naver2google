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


def test_convert_address_fallback():
    n.convert.cache_clear()
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
