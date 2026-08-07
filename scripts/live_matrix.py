"""Run every Naver URL shape against convert() and check it lands on the real place."""
import importlib, json, math, os, sys, time
from urllib.parse import quote
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import naver2google as n
importlib.reload(n)

HERE = os.path.dirname(os.path.abspath(__file__))
places = json.load(open(f"{HERE}/places.json"))

def dist_m(a, b):
    (la1, lo1), (la2, lo2) = a, b
    R = 6371000
    p1, p2 = math.radians(la1), math.radians(la2)
    dp, dl = math.radians(la2 - la1), math.radians(lo2 - lo1)
    h = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(h))

def shapes(p):
    pid, name = p["id"], p["name"]
    addr = p.get("roadAddress") or p.get("address") or ""
    return [
        ("entry/place",        f"https://map.naver.com/p/entry/place/{pid}"),
        ("entry/place+c",      f"https://map.naver.com/p/entry/place/{pid}?c=15.00,0,0,0,dh&placePath=%2Fhome"),
        ("m.place/restaurant", f"https://m.place.naver.com/restaurant/{pid}/home"),
        ("m.place/accommod.",  f"https://m.place.naver.com/accommodation/{pid}/home"),
        ("pcmap.place",        f"https://pcmap.place.naver.com/restaurant/{pid}/home"),
        ("v5/entry/place",     f"https://map.naver.com/v5/entry/place/{pid}"),
        ("p/search/name",      f"https://map.naver.com/p/search/{quote(name)}"),
        ("p/search/name+c",    f"https://map.naver.com/p/search/{quote(name)}?c=15.00,0,0,0,dh"),
        ("nmap://place?id",    f"nmap://place?id={pid}&appMenu=location"),
        ("share text (dead)",  f"[NAVER 지도]\n{name}\n{addr}\nhttps://naver.me/zzdead9"),
        ("share text+m.place", f"[NAVER 지도]\n{name}\n{addr}\nhttps://m.place.naver.com/restaurant/{pid}/home"),
        ("bare name",          name),
        ("bare address",       addr),
        ("url + spaces",       f"  https://map.naver.com/p/entry/place/{pid}  "),
        ("directions (dest)",  f"https://map.naver.com/p/directions/126.9779692,37.5662952,출발,,/"
                               f"{p['longitude']},{p['latitude']},{quote(name)},{pid},PLACE_POI/-/transit"),
    ]

fails, total = [], 0
for p in places:
    truth = (p["latitude"], p["longitude"])
    for label, url in shapes(p):
        if not url.strip():
            continue
        total += 1
        n.convert.cache_clear()
        try:
            r = n.convert(url)
        except Exception as e:
            fails.append((p["name"], label, f"{type(e).__name__}: {e}"))
            continue
        if r.get("lat") is None:
            fails.append((p["name"], label, f"no coords (blind search: {r['name'][:40]!r})"))
            continue
        d = dist_m(truth, (r["lat"], r["lng"]))
        if d > 300:
            fails.append((p["name"], label, f"{d:.0f}m off → {r['name']!r}"))
        time.sleep(0.35)

print(f"\n=== {total - len(fails)}/{total} ok, {len(fails)} FAIL ===")
seen = {}
for name, label, why in fails:
    seen.setdefault(label, []).append((name, why))
for label, rows in sorted(seen.items(), key=lambda kv: -len(kv[1])):
    print(f"\n## {label}  ({len(rows)} fail)")
    for name, why in rows[:4]:
        print(f"   {name}: {why}")
