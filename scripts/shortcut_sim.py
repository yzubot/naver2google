"""Simulate exactly what the iOS shortcut sends, for every share shape we've seen.

The user's Naver app is in Chinese and its share sheet hands Shortcuts *two*
items: a Chinese place name and a numeric place id. Naver's own search returns
zero results for the Chinese text, so the id is the only usable signal — these
cases exist so that stays true.
"""
import json, os, re, sys
from urllib.parse import quote
import requests

B = os.environ.get("N2G_BASE", "http://127.0.0.1:8585")

# (label, what Shortcuts would put after /a/ , expected lat, lng)
CASES = [
    ("中文店名+id（空白接）", "N285酒店仁寺洞 1186111517", 37.5724089, 126.987433),
    ("中文店名+id（換行接）", "N285酒店仁寺洞\n1186111517", 37.5724089, 126.987433),
    ("只有 id",              "1186111517", 37.5724089, 126.987433),
    ("韓文分享文字+短連結",   "[NAVER 지도]\n경복궁\n서울특별시 종로구 사직로 161\nhttps://naver.me/zzdead9", 37.5788408, 126.9770162),
    ("place 連結",           "https://map.naver.com/p/entry/place/13140708", 34.8679683, 126.3073927),
    ("m.place 連結",         "https://m.place.naver.com/accommodation/1186111517/home", 37.5724089, 126.987433),
    ("nmap:// scheme",       "nmap://place?id=11571707&appMenu=location", 37.5788408, 126.9770162),
    ("中文店名+id+連結",      "N285酒店仁寺洞\nhttps://naver.me/zzdead9\n1186111517", 37.5724089, 126.987433),
]

def coords_of(text):
    m = re.search(r"ll=(-?\d+\.\d+),(-?\d+\.\d+)", text)
    return (float(m.group(1)), float(m.group(2))) if m else None

def near(got, lat, lng):
    return got and abs(got[0]-lat) < 0.003 and abs(got[1]-lng) < 0.003

fails = []
for label, payload, lat, lng in CASES:
    enc = quote(payload, safe="")
    # /m/ = 一個動作版（回 HTML 跳轉頁）
    r = requests.get(f"{B}/m/{enc}", timeout=60)
    got = coords_of(r.text)
    ok_m = r.status_code == 200 and near(got, lat, lng) and 'href="maps://?ll=' in r.text
    # /a/ = 302 版
    r2 = requests.get(f"{B}/a/{enc}", allow_redirects=False, timeout=60)
    got2 = coords_of(r2.headers.get("Location", ""))
    ok_a = r2.status_code == 302 and near(got2, lat, lng)
    # /aj/ = JSON 版
    r3 = requests.get(f"{B}/aj/{enc}", timeout=60)
    got3 = coords_of(r3.json().get("url", ""))
    ok_j = r3.status_code == 200 and near(got3, lat, lng)
    print(f"{'PASS' if ok_m and ok_a and ok_j else 'FAIL'} {label:22s} /m/={got} /a/={got2} /aj/={got3}")
    if not (ok_m and ok_a and ok_j):
        fails.append(label)

print("\nALL PASS" if not fails else f"\nFAILED: {fails}")
sys.exit(1 if fails else 0)
