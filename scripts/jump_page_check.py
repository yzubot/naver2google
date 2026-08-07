"""The /m/ interstitial: does it hand Safari an intact maps:// URL?"""
import os, re, sys
from urllib.parse import quote
from playwright.sync_api import sync_playwright

B = os.environ.get("N2G_BASE", "http://127.0.0.1:8585")
IOS_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
          "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")
CASES = [("N285酒店仁寺洞 1186111517", 37.5724089, 126.987433),
         ("https://map.naver.com/p/entry/place/13140708", 34.8679683, 126.3073927)]

fails = []
with sync_playwright() as pw:
    b = pw.webkit.launch()
    for payload, lat, lng in CASES:
        page = b.new_page(viewport={"width": 390, "height": 844}, user_agent=IOS_UA)
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(f"{B}/m/{quote(payload, safe='')}", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)
        app = page.get_attribute("a.app", "href") or ""
        web = page.get_attribute("a.web", "href") or ""
        m = re.search(r"ll=(-?\d+\.\d+),(-?\d+\.\d+)", app)
        got = (float(m.group(1)), float(m.group(2))) if m else None
        ok = (app.startswith("maps://?ll=")            # authority 沒被砍成 maps:?
              and got and abs(got[0]-lat) < 0.003 and abs(got[1]-lng) < 0.003
              and web.startswith("https://maps.apple.com/")
              and not errs)
        print(f"{'PASS' if ok else 'FAIL'} {payload[:28]:30s} app={app[:58]} errs={errs[:1]}")
        if not ok:
            fails.append(payload)
        page.close()
    b.close()
print("\nALL PASS" if not fails else f"\nFAILED: {fails}")
sys.exit(1 if fails else 0)
