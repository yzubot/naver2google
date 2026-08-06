#!/usr/bin/env python3
"""產生 .shortcut 檔（未簽章）——讓使用者直接匯入，不必手動拉動作。

捷徑內容只有一個動作「打開 URL」：
    https://naver2google.onrender.com/a/<捷徑輸入>
伺服器端 /a/ 會 302 到 Apple 地圖（/g/ 則是 Google）。

未簽章的捷徑要在 設定 → 捷徑 → 允許不受信任的捷徑 打開才能匯入；
若使用者不想開那個開關，網頁上仍有「手動建一個動作」的步驟。
"""
import plistlib
from pathlib import Path

BASE = "https://naver2google.onrender.com"
OUT = Path(__file__).parent


def build(prefix: str, name: str, glyph: int, color: int) -> dict:
    text = f"{BASE}{prefix}￼"          # ￼ = 變數佔位符
    idx = len(text) - 1
    return {
        "WFWorkflowClientVersion": "2605.0.5",
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowIcon": {
            "WFWorkflowIconStartColor": color,
            "WFWorkflowIconGlyphNumber": glyph,
        },
        "WFWorkflowImportQuestions": [],
        "WFWorkflowTypes": ["ActionExtension"],
        "WFWorkflowInputContentItemClasses": ["WFURLContentItem"],
        "WFWorkflowHasShortcutInputVariables": True,
        "WFWorkflowName": name,
        "WFWorkflowActions": [
            {
                "WFWorkflowActionIdentifier": "is.workflow.actions.openurl",
                "WFWorkflowActionParameters": {
                    "WFInput": {
                        "WFSerializationType": "WFTextTokenString",
                        "Value": {
                            "string": text,
                            "attachmentsByRange": {
                                f"{{{idx}, 1}}": {"Type": "ExtensionInput"}
                            },
                        },
                    }
                },
            }
        ],
    }


def main() -> None:
    for prefix, fname, name, glyph, color in [
        ("/a/", "naver-to-apple-maps.shortcut", "用 Apple 地圖開啟", 59511, 431817727),
        ("/g/", "naver-to-google-maps.shortcut", "用 Google 地圖開啟", 59511, 4292093695),
    ]:
        p = OUT / fname
        p.write_bytes(plistlib.dumps(build(prefix, name, glyph, color),
                                     fmt=plistlib.FMT_BINARY))
        print(f"{p.name}  {p.stat().st_size}B")


if __name__ == "__main__":
    main()
