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

### `GET /a/<NAVER_URL>`、`GET /g/<NAVER_URL>`

把 Naver 網址**直接接在路徑後面** → 302 到 Apple / Google 地圖。
scheme 可省略，被壓成單斜線（`https:/`）也收得到：

```
/a/https://naver.me/xxxxx
/a/naver.me/xxxxx
/g/map.naver.com/p/entry/place/13140708
```

存在的意義：iOS 捷徑只要**一個動作**（打開 URL），不必 POST、不必 URL 編碼。

也吃 Naver Map 分享出來的**整段文字**（店名＋地址＋短連結）——換行會被編成
`%0A`，Flask 預設的 `<path:>` 比對不到（`.` 不匹配換行）會 404，所以自訂了
`anytext` 轉換器用 `[\s\S]*`。

## iPhone 使用方式 A：捷徑（推薦）

線上圖文步驟：**<https://naver2google.onrender.com/shortcut>**

手動建，只有 **1 個動作**：

1. 捷徑 App → **+** → 加入動作「**打開 URL**」
2. URL 欄位貼 `https://naver2google.onrender.com/a/`，游標留在最後，
   從鍵盤上方變數列插入「**捷徑輸入**」
3. 重新命名 → ⓘ 打開「在分享表單中顯示」，類型勾 **URL** 和 **文字**
   （Naver Map 分享的是整段文字而非單一網址，只勾 URL 捷徑不會出現在分享表單）

Google 版把 `/a/` 換成 `/g/` 即可。

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

1. **短連結展開** — `naver.me/*` → HTTP HEAD follow redirect
2. **URL 參數** — 解析 `lat`/`lng` query params
3. **Place API** — 從路徑取 `/place/{ID}` → 呼叫 Naver Place Summary API（免 API key）
4. **@座標格式** — regex `@lat,lng`
5. **Fallback** — 直接傳文字到 Google/Apple Maps 搜尋

## 自架

```bash
pip install -r requirements.txt
python naver2google.py --port 8585
```

## 部署

已設定 Render 自動部署（`render.yaml`），push 到 GitHub 即自動更新。

## License

MIT
