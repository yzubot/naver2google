# Naver Map → Google / Apple Maps

Naver Map 網址轉換器 — 將韓國 Naver 地圖連結轉換為 Google Maps 或 Apple Maps。

## Live Demo

**https://naver2google.onrender.com**

## 功能

- 支援多種輸入格式：
  - `naver.me/` 短連結
  - `map.naver.com/p/` 完整連結
  - `nmap://` scheme
  - 中文地址（例：`首尔特别市 中区 苎洞二街 89`）
  - 韓文地址（例：`서울특별시 중구 저동2가 89`）
  - 英文地址
- 自動解析 Naver Map 分享的多行文字，擷取網址
- 同時產生 Google Maps 和 Apple Maps 連結
- 深色主題 Web UI，支援手機操作

## API

### `GET /convert?url=NAVER_URL`

回傳 JSON：

```json
{
  "lat": 37.5665,
  "lng": 126.9780,
  "name": "地點名稱",
  "google_url": "https://www.google.com/maps?q=37.5665,126.9780",
  "apple_url": "https://maps.apple.com/?ll=37.5665,126.9780&q=..."
}
```

### `GET /go?url=NAVER_URL[&target=apple]`

302 redirect 到 Google Maps（預設）或 Apple Maps（`target=apple`）。

### `GET|POST /apple`、`GET|POST /google`

**只回一行純文字網址**（不是 JSON），專門給 iOS 捷徑的「打開 URL」直接吃，
省掉「取得字典值」那一步。四種傳法都收：

```bash
curl "https://naver2google.onrender.com/apple?url=<NAVER_URL>"
curl -X POST https://naver2google.onrender.com/apple \
     -H 'Content-Type: application/json' -d '{"url":"<NAVER_URL>"}'   # 捷徑用這種
curl -X POST https://naver2google.onrender.com/apple --data-urlencode "url=<NAVER_URL>"
curl -X POST https://naver2google.onrender.com/apple -d '<NAVER_URL>'
```

失敗時回純文字訊息 + 400/502/503。

### `GET /m/<NAVER_URL>`、`GET /a/<NAVER_URL>`、`GET /g/<NAVER_URL>`

把 Naver 網址**直接接在路徑後面** → 302 到 Apple / Google 地圖。
scheme 可省略，被壓成單斜線（`https:/`）也收得到：

```
/a/https://naver.me/xxxxx
/a/naver.me/xxxxx
/g/map.naver.com/p/entry/place/13140708
```

- `/m/` → 回一頁 HTML，用 JS 跳 `maps://?…` **直接叫醒「地圖」App**（捷徑用這個）
- `/a/` → 302 到 `https://maps.apple.com/…`
- `/g/` → 302 到 Google Maps

**為什麼捷徑要用 `/m/` 而不是 `/a/`**：iOS 的 universal link **不會**因為跨網域
302 而觸發，所以 `/a/` 只會停在 Safari，而且 Apple 現在有網頁版地圖，會渲染成
`maps.apple/p/xxxx`。`/m/` 改丟 `maps://` App scheme 就會直接開 App。
不能用 302 送 `maps://`——werkzeug 的 `iri_to_uri` 會把空 authority 砍掉變成
`maps:?…`，所以改回一頁 HTML 由 JS 跳轉，並附一顆備援按鈕。

存在的意義：iOS 捷徑只要**一個動作**（打開 URL），不必 POST、不必 URL 編碼。

也吃 Naver Map 分享出來的**整段文字**（店名＋地址＋短連結）——換行會被編成
`%0A`，Flask 預設的 `<path:>` 比對不到（`.` 不匹配換行）會 404，所以自訂了
`anytext` 轉換器用 `[\s\S]*`。

## iPhone 使用方式 A：捷徑（推薦）

線上圖文步驟：**<https://naver2google.onrender.com/shortcut>**

手動建，只有 **1 個動作**：

1. 捷徑 App → **+** → 加入動作「**打開 URL**」
2. URL 欄位貼 `https://naver2google.onrender.com/m/`，游標留在最後，
   從鍵盤上方變數列插入「**捷徑輸入**」
3. 重新命名 → ⓘ 打開「在分享表單中顯示」，類型勾 **URL** 和 **文字**
   （Naver Map 分享的是整段文字而非單一網址，只勾 URL 捷徑不會出現在分享表單）

Google 版把 `/m/` 換成 `/g/` 即可。

> ### ⚠️ 不能提供「下載就好」的捷徑檔
> 曾用 `shortcuts/build_shortcuts.py` 產生未簽章的 `.shortcut` 檔給
> `/dl/<name>.shortcut` 下載，**實測 iOS 直接拒絕**：
> 「不支援輸入未簽署的捷徑檔案。請使用其他分享選項。」
> 「允許不受信任的捷徑」開關也救不了——iOS 只吃 Apple 官方 iCloud 連結格式，
> 而那種連結只能從 Apple 裝置上傳產生（簽章需要 macOS 的 `shortcuts sign`）。
> `/dl/` 現在回 410 + 說明，產生器留著供日後有 Mac 時使用。

## iPhone 使用方式 B（Scriptable）

1. 安裝 [Scriptable](https://apps.apple.com/app/scriptable/id1405459188) app
2. 建立新腳本，貼上 [`scriptable/Naver2Google.js`](scriptable/Naver2Google.js) 的內容
3. 開啟腳本設定 → 打開 **Share Sheet**
4. 在 Naver Map app 按分享 → 選 Scriptable → 選 Naver2Google
5. 選擇要開啟 Google Maps 或 Apple Maps

## 座標解析邏輯

依優先順序嘗試：

1. **短連結展開** — `naver.me/*` → GET follow redirect
2. **URL 參數** — 解析 `lat`/`lng` query params
3. **Place ID → Place API**（免 API key）。id 來源三種：
   `/place/{id}`、`m.place.naver.com/{類別}/{id}`（`restaurant`/`accommodation`/…）、
   以及 Naver **App** 分享的 `nmap://place?id={id}`
4. **@座標格式** — regex `@lat,lng`
5. **Naver 視窗參數** — `?c=經度,緯度,…`、`?x=&y=`
6. **文字反查 Naver**（`/entry/address/…`、`/p/search/…`、分享文字）→ 精確座標
7. **最後才是** 把文字丟給 Google/Apple 自己搜（標記 `verified: false`）

### 兩條非顯而易見的坑（都實測踩過）

* **Naver 搜尋把付費廣告排第一** — 搜「명동교자」第一筆回「강남교자 센터원점」。
  照抄第一筆會把人送到一間真的存在、但不是他要的店。`_score_place()` 依
  「店名 vs 分享文字」重疊度排序，平手才用 Naver 原順序。
* **「店名 + 完整地址」黏成一串，Naver 回 0 筆** — 而分享文字正是這個形狀。
  `_search_candidates()` 做階梯式退化：整段 → 只用店名 → 只用地址。

### 不要用 `maps://` App scheme（實測踩過）

`/m/` 以前會丟 `maps://?ll=…&q=…` 想「直接叫醒地圖 App」。**使用者實測：每一個
地點最後都開在 37.56649,126.98104（首爾市中心預設點）** —— 地圖 App 把參數整個
丟掉、停在它上次的畫面。同一組座標走 `https://maps.apple.com/?ll=…&q=…`
（iOS Safari UA、WebKit 實測）誤差只有 4m，Apple 自己會轉成
`maps.apple.com/place?coordinate=…`。

所以 `/m/` 現在等同 `/a/`，舊捷徑不用改就會自己變正確。

⚠️ 另一個實測：`maps.apple.com/?q=<韓文店名>`（**沒有** `ll`）從台灣打開會落在
**台南**。這就是 `verified` 旗標存在的理由。

### verified 旗標

`verified: false` = 沒人確認過這個位置（只是把文字丟去搜）。實測一個真實分享
文字的盲搜尋在 Apple 地圖上落在 **300 公里外**。因此：

* `/convert`、`/convert_batch`（網頁版）**會**回傳，UI 標明「位置可能不對」
* `/a/`、`/g/`、`/m/`、`/apple`、`/google`、`/go`（捷徑會直接把人傳送過去）
  **一律回 422 不轉址**

## 測試

```bash
python -m pytest test_naver2google.py -q     # 69 條，全離線
python scripts/route_check.py                # 每個 endpoint × 每種輸入格式（要先起服務）
python scripts/ui_check.py                   # Playwright 實際點網頁（桌機 + 手機）
python scripts/live_matrix.py                # 17 個真實地點 × 14 種連結形狀，比對座標誤差
python scripts/harvest_places.py             # 重新抓 live_matrix 用的標準答案
```

## 自架

```bash
pip install -r requirements.txt
python naver2google.py --port 8585
```

## 部署

已設定 Render 自動部署（`render.yaml`），push 到 GitHub 即自動更新。

## License

MIT
