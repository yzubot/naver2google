"""The failure landing page: does ?why=/?q= actually explain and auto-convert?"""
from playwright.sync_api import sync_playwright
from urllib.parse import quote
B = "http://127.0.0.1:8585"
fails = []
def chk(l, c, e=""):
    print(("PASS " if c else "FAIL ") + l + ("" if c else f"  <- {e}")); c or fails.append(l)

with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 390, "height": 844})
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))

    why = "捷徑沒有把分享的內容傳過來。"
    page.goto(f"{B}/?why={quote(why)}", wait_until="networkidle")
    chk("banner shown", page.is_visible("#why-card"))
    chk("banner text", why in page.inner_text("#why-msg"), page.inner_text("#why-msg"))

    q = "https://map.naver.com/p/entry/place/13140708"
    page.goto(f"{B}/?why={quote('查不到座標')}&q={quote(q)}", wait_until="networkidle")
    page.wait_for_selector("#result-area .name", state="visible", timeout=30000)
    chk("auto-converted", "34.8679683" in page.inner_text("#result-area"),
        page.inner_text("#result-area")[:100])
    chk("input prefilled", q in page.input_value("#url-input"))
    chk("no JS error", not errs, errs[:2])
    b.close()
print("\nALL PASS" if not fails else f"\nFAILED: {fails}")
