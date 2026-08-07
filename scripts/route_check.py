"""Hit every endpoint the shortcut / web UI can use, with every input style."""
import json, os, sys, time
from urllib.parse import quote
import requests

B = "http://127.0.0.1:8585"
PLACE = "https://map.naver.com/p/entry/place/13140708"   # 압해도성당 34.8679683,126.3073927
TRUTH = "34.8679683,126.3073927"
SHARE = "[NAVER 지도]\n명동교자 본점\n서울특별시 중구 충무로2가 64-6\nhttps://naver.me/zzdead9"

def chk(label, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + label + ("" if cond else f"  <- {extra}"))
    return cond

ok = True
s = requests.Session()

r = s.get(f"{B}/health", timeout=20);            ok &= chk("/health", r.text.strip() == "ok", r.text[:80])
r = s.get(f"{B}/", timeout=20);                  ok &= chk("/ (web ui)", r.status_code == 200 and "轉換" in r.text)
r = s.get(f"{B}/shortcut", timeout=20);          ok &= chk("/shortcut", r.status_code == 200 and "/a/" in r.text)

r = s.get(f"{B}/convert", params={"url": PLACE}, timeout=30)
ok &= chk("/convert json", r.json().get("lat") == 34.8679683, r.text[:120])

r = s.post(f"{B}/convert_batch", json={"urls": [PLACE, SHARE]}, timeout=60)
d = r.json(); ok &= chk("/convert_batch", d.get("count") == 2 and all(x.get("lat") for x in d["results"]), r.text[:200])

for path, host in (("/apple", "maps.apple.com"), ("/google", "google.com/maps")):
    r = s.get(f"{B}{path}", params={"url": PLACE}, timeout=30)
    ok &= chk(f"GET {path}", host in r.text and TRUTH in r.text, r.text[:120])
    r = s.post(f"{B}{path}", json={"url": PLACE}, timeout=30)
    ok &= chk(f"POST {path} json", host in r.text, r.text[:120])
    r = s.post(f"{B}{path}", data=PLACE.encode(), headers={"Content-Type": "text/plain"}, timeout=30)
    ok &= chk(f"POST {path} raw", host in r.text, r.text[:120])

for path, want in (("/a/", "maps.apple.com"), ("/g/", "google.com/maps")):
    for variant in (PLACE, PLACE.replace("https://", "https:/"), PLACE.replace("https://", ""),
                    f"`{PLACE}`", f"<{PLACE}>"):
        r = s.get(f"{B}{path}{variant}", allow_redirects=False, timeout=30)
        ok &= chk(f"{path}{variant[:34]}…", r.status_code == 302 and want in r.headers.get("Location", "")
                  and TRUTH in r.headers.get("Location", ""), f"{r.status_code} {r.headers.get('Location','')[:90]}")

r = s.get(f"{B}/m/{PLACE}", timeout=30)
ok &= chk("/m/ app-scheme page", "maps://" in r.text and TRUTH in r.text, r.text[:160])

r = s.get(f"{B}/a/" + quote(SHARE, safe=""), allow_redirects=False, timeout=40)
ok &= chk("/a/ share text", r.status_code == 302 and "ll=37.561" in r.headers.get("Location", ""),
          f"{r.status_code} {r.headers.get('Location','')[:110]}")

r = s.get(f"{B}/go", params={"url": PLACE, "target": "apple"}, allow_redirects=False, timeout=30)
ok &= chk("/go?target=apple", r.status_code == 302 and "maps.apple.com" in r.headers.get("Location", ""),
          f"{r.status_code} {r.headers.get('Location','')[:90]}")

r = s.get(f"{B}/a/https://naver.me/zzdead9", allow_redirects=False, timeout=40)
ok &= chk("dead link → 422 (no redirect)", r.status_code == 422 and "Location" not in r.headers,
          f"{r.status_code} {r.headers.get('Location','')}")

r = s.get(f"{B}/apple", timeout=20);             ok &= chk("/apple no url → 400", r.status_code == 400)
r = s.get(f"{B}/dl/x.shortcut", timeout=20);     ok &= chk("/dl → 410", r.status_code == 410)
r = s.get(f"{B}/nope", timeout=20);              ok &= chk("unknown path → 404", r.status_code == 404)

print("\nALL PASS" if ok else "\nSOME FAILED")
