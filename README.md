# Naver Map → Google / Apple Maps

Naver Map 網址轉換器 — 將韓國 Naver 地圖連結轉換為 Google Maps 或 Apple Maps。

## Live Demo

**https://naver2google.onrender.com**

## 功能

- 支援多種 Naver Map URL 格式：
  - `naver.me/` 短連結
  - `map.naver.com/p/` 完整連結
  - `nmap://` scheme
  - 純韓文地址（fallback 搜尋）
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

## iPhone 使用方式（Scriptable）

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
