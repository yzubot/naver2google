"""Drive the real web UI: click convert, watch for JS errors, check mobile layout."""
from playwright.sync_api import sync_playwright

B = "http://127.0.0.1:8585"
PLACE = "https://map.naver.com/p/entry/place/13140708"
SHARE = "[NAVER 지도]\n명동교자 본점\n서울특별시 중구 충무로2가 64-6\nhttps://naver.me/zzdead9"

fails = []
def chk(label, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + label + ("" if cond else f"  <- {extra}"))
    if not cond:
        fails.append(label)

with sync_playwright() as pw:
    b = pw.chromium.launch()
    for name, vp in (("desktop", {"width": 1280, "height": 900}),
                     ("iphone", {"width": 390, "height": 844})):
        page = b.new_page(viewport=vp)
        errors, console = [], []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: console.append(m.text) if m.type == "error" else None)

        page.goto(B, wait_until="networkidle")
        chk(f"[{name}] no JS error on load", not errors, errors[:2])

        # single link
        page.fill("#url-input", PLACE)
        page.click(".btn-convert")
        page.wait_for_selector("#result-area .name", state="visible", timeout=30000)
        txt = page.inner_text("#result-area")
        chk(f"[{name}] single convert shows coords", "34.8679683" in txt, txt[:120])
        href = page.get_attribute("#result-area a.btn-apple", "href")
        chk(f"[{name}] apple link points at coords", "ll=34.8679683" in (href or ""), href)
        chk(f"[{name}] no JS error after click", not errors, errors[:2])

        # share text (multi-line)
        page.fill("#url-input", SHARE)
        page.click(".btn-convert")
        page.wait_for_timeout(4000)
        txt = page.inner_text("#result-area")
        chk(f"[{name}] share text resolves", "37.561" in txt, txt[:160])

        # batch: two links, one per line
        page.fill("#url-input", PLACE + "\n" + "https://map.naver.com/p/entry/place/11571707")
        page.click(".btn-convert")
        page.wait_for_timeout(6000)
        txt = page.inner_text("#result-area")
        chk(f"[{name}] batch shows both", "34.8679683" in txt and "37.5788408" in txt, txt[:200])

        # error path must not blow up the page
        page.fill("#url-input", "https://naver.me/zzdead9")
        page.click(".btn-convert")
        page.wait_for_timeout(6000)
        err = page.inner_text("#error-area")
        chk(f"[{name}] dead link shows error text", "座標" in err or "失敗" in err, err[:120])
        chk(f"[{name}] still no JS error", not errors, errors[:2])
        # 422 是伺服器對死連結的正確回應（頁面有顯示錯誤文字），不算 UI 壞掉
        real = [c for c in console if "422" not in c]
        chk(f"[{name}] no console errors", not real, real[:2])

        # no horizontal overflow on phone
        ow = page.evaluate("document.documentElement.scrollWidth")
        cw = page.evaluate("document.documentElement.clientWidth")
        chk(f"[{name}] no horizontal scroll ({ow}<={cw})", ow <= cw + 1)

        # shortcut guide page
        page.goto(B + "/shortcut", wait_until="networkidle")
        chk(f"[{name}] /shortcut no JS error", not errors, errors[:2])
        ow = page.evaluate("document.documentElement.scrollWidth")
        cw = page.evaluate("document.documentElement.clientWidth")
        chk(f"[{name}] /shortcut no h-scroll ({ow}<={cw})", ow <= cw + 1)
        page.close()
    b.close()

print("\nALL PASS" if not fails else f"\nFAILED: {fails}")
