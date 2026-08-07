"""Harvest real Naver places (id/name/coords/address) as ground truth."""
import json, os, sys, time
from urllib.parse import quote
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import naver2google as n

QUERIES = [
    "명동교자 본점", "N285호텔 인사동", "경복궁", "롯데월드타워", "신라호텔 서울",
    "스타벅스 더종로점", "국립중앙박물관", "홍대입구역", "부산 해운대해수욕장",
    "제주 성산일출봉", "인천공항 제2여객터미널", "더현대 서울", "광장시장",
    "남산서울타워", "전주 한옥마을", "속초 중앙시장", "대구 서문시장",
]

out = []
for q in QUERIES:
    r = n.SESSION.get(n.SEARCH_PAGE.format(quote(q)), timeout=15)
    items = []
    for blob in n._iter_state_blobs(r.text):
        def walk(o):
            if isinstance(o, dict):
                if o.get("latitude") and o.get("longitude") and o.get("name"):
                    items.append({k: o.get(k) for k in
                                  ("id", "name", "latitude", "longitude", "address", "roadAddress")})
                for v in o.values(): walk(v)
            elif isinstance(o, list):
                for v in o: walk(v)
        walk(blob)
    best = max(items, key=lambda p: n._score_place(
        {"name": p["name"], "address": str(p.get("address") or "")}, q)) if items else None
    if best and best.get("id"):
        out.append({"query": q, **best})
        print(f"{q:24s} -> {best['id']:>12} {best['name']} @ {best['latitude']},{best['longitude']}")
    else:
        print(f"{q:24s} -> NONE")
    time.sleep(0.4)

json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "places.json"), "w"),
          ensure_ascii=False, indent=1)
print("saved", len(out))
