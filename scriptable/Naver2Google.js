// Naver Map → Google Maps
// Scriptable 腳本：從 Share Sheet 接收 Naver Map 連結，自動跳轉到 Google Maps
//
// 設定方式：
// 1. 在 Scriptable app 中建立新腳本，貼上此程式碼
// 2. 在 Naver Map app 按分享 → 選 Scriptable → 選這個腳本

const API = "https://naver2google.onrender.com";

// 從 Share Sheet 取得輸入
let input = args.plainTexts?.[0] || args.urls?.[0] || "";

if (!input && args.shortcutParameter) {
  input = args.shortcutParameter;
}

if (!input) {
  // 如果沒有 Share Sheet 輸入，顯示輸入框
  let alert = new Alert();
  alert.title = "Naver → Google Maps";
  alert.message = "貼上 Naver Map 分享的內容";
  alert.addTextField("Naver Map 連結");
  alert.addAction("轉換");
  alert.addCancelAction("取消");
  let idx = await alert.present();
  if (idx === -1) return;
  input = alert.textFieldValue(0);
}

if (!input.trim()) {
  let err = new Alert();
  err.title = "錯誤";
  err.message = "沒有收到任何內容";
  err.addAction("OK");
  await err.present();
  return;
}

// 從多行分享文字中擷取 URL
function extractUrl(text) {
  let m = text.match(/https?:\/\/(?:naver\.me|map\.naver\.com|m\.map\.naver\.com)\S+/);
  if (m) return m[0];
  m = text.match(/nmap:\/\/\S+/);
  if (m) return m[0];
  return text.trim();
}

let cleanInput = extractUrl(input);

// 呼叫 API
let url = `${API}/convert?url=${encodeURIComponent(cleanInput)}`;
let req = new Request(url);
req.timeoutInterval = 15;

try {
  let result = await req.loadJSON();

  if (result.error) {
    let err = new Alert();
    err.title = "轉換失敗";
    err.message = result.error;
    err.addAction("OK");
    await err.present();
    return;
  }

  // 直接打開 Google Maps
  Safari.open(result.google_url);

} catch (e) {
  let err = new Alert();
  err.title = "連線失敗";
  err.message = "無法連接轉換服務：" + e.message;
  err.addAction("OK");
  await err.present();
}

Script.complete();
