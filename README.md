# LINE AI 助理

這是給自己使用的 LINE 排程助理。LINE 官方帳號收到你的私訊後，會把事件送到這台 Windows 電腦。Python 驗證 LINE 簽章與發送者，再呼叫 VS Code 擴充套件附帶的 Codex CLI 來理解文字、補問缺少的行程資料，最後透過 LINE Messaging API 把答案回到原聊天室。

```text
LINE 私訊 → LINE 官方帳號 → 公開 HTTPS /webhook → 本機 Python
                                                      ↓
LINE 聊天室 ← LINE Messaging API ← Python ← Codex CLI → OpenAI
```

## 用 LINE 記錄行程

1. 傳送「明天下午三點開會」。助理會保留日期、時間和事項，詢問地點及參加對象。
2. 回覆「在七樓會議室」，再回覆「跟小王和小陳」，或把地點和對象寫在同一則訊息。尚未決定的資料可以說「地點先寫未定」。
3. 資料填齊後，助理會回傳已存的日期、時間、事項、地點和對象。用「今天行程」「明天行程」或「接下來有哪些行程」查詢。想放棄還在補資料的這筆行程，可傳送「取消記錄」。

日期以台灣時間 UTC+8 為準。只有時間時，若今天該時間尚未過，先使用今天；已過則會追問要記在哪一天。行程和未完成的草稿保存在本機 `.runtime/agenda.sqlite3`，服務重新啟動後仍可查詢或接續補資料。

## 1. 測試 VS Code 的 Codex

安裝 VS Code 的 Codex 擴充套件，在側欄用你的 ChatGPT 帳號登入。在同一個 Windows 帳號開啟 `line-ai-assistant` 資料夾並在 PowerShell 執行下列命令。需要 Python 3.11 或以上。 [Codex 擴充套件](https://learn.chatgpt.com/docs/codex/ide) · [Python 下載](https://www.python.org/downloads/windows/)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -X utf8 bot.py ask '請用一句話自我介紹'
```

程式優先從 `%USERPROFILE%\.vscode\extensions` 找 Codex 執行檔，再搜尋 `PATH`。CLI 可以使用和擴充套件共用的 ChatGPT 登入，此路徑無需在專案內申請或填入 OpenAI API Key。VS Code 視窗可以關閉。本機服務和網路需持續運作。 [Codex 登入與共用授權](https://learn.chatgpt.com/docs/auth#login-caching) · [Codex 非互動模式](https://learn.chatgpt.com/docs/non-interactive-mode)

如果 CLI 路徑不同，在 `.env` 設定 `CODEX_BIN` 為完整執行檔路徑。如果要指定與 VS Code 一樣的模型，在 `.env` 的 `CODEX_MODEL` 填入你的帳號可用的模型 ID。留空時由 CLI 選擇預設模型。每則 LINE 訊息開啟獨立的 Codex 請求。

## 2. 建立 LINE 官方帳號與授權

1. 用 LINE 帳號在 LINE Official Account Manager 建立官方帳號。選擇帳號的 **設定 → Messaging API → 啟用 Messaging API**，建立或選擇 Provider。請先確認 Provider 選對，LINE 建立後無法改配。 [LINE 官方帳號管理後台](https://manager.line.biz/) · [開通 Messaging API](https://developers.line.biz/en/docs/messaging-api/getting-started/)
2. 用同一個 Business ID 登入 **LINE Developers Console**，打開上述 Provider 裡的 Messaging API Channel。取得下表三項資料，填進本專案的 `.env`。如果看不到 **Your user ID**，先把手機的 LINE 帳號連結到 Business ID。 [LINE Developers Console](https://developers.line.biz/console/) · [取得自己的 User ID](https://developers.line.biz/en/docs/messaging-api/getting-user-ids/)

| `.env` 欄位 | LINE 後台位置 | 用途 |
| --- | --- | --- |
| `LINE_CHANNEL_SECRET` | **Basic settings → Channel secret** | 驗證收到的 webhook 確實來自 LINE |
| `LINE_CHANNEL_ACCESS_TOKEN` | **Messaging API → Channel access token → Issue** | 讓後端能用官方帳號回覆訊息 |
| `LINE_OWNER_USER_ID` | **Basic settings → Your user ID** | 只接受這個使用者的一對一私訊。格式為 `U` 加 32 個十六進位字元 |

在專案開啟 PowerShell，複製設定範本，再用 VS Code 開啟 `.env` 填入這三項。

```powershell
if (-not (Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
code .env
```

請將 Channel Secret 和 Access Token 留在你的 `.env`。此檔已由專案的 `.gitignore` 排除。個人私訊流程使用 Messaging API 的簽章與存取權杖，LINE Login 或 LIFF 可等到需要網頁登入時再加入。 [LINE 簽章驗證](https://developers.line.biz/en/docs/messaging-api/verify-webhook-signature/) · [Channel Access Token 說明](https://developers.line.biz/en/docs/basics/channel-access-token/)

## 3. 啟動服務與設定 Webhook URL

1. 在專案的第一個 PowerShell 視窗啟動後端。

   ```powershell
   .\.venv\Scripts\python.exe -X utf8 bot.py serve
   ```

2. 安裝 `cloudflared` 並在第二個 PowerShell 視窗開啟測試用 HTTPS 通道。將畫面顯示的 `trycloudflare.com` 網址加上 `/webhook`，例如 `https://你的臨時網址.trycloudflare.com/webhook`。 [cloudflared 下載](https://developers.cloudflare.com/tunnel/downloads/) · [Quick Tunnel](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)

   ```powershell
   cloudflared tunnel --url http://127.0.0.1:8000
   ```

3. 在 **LINE Developers Console → Messaging API → Webhook settings**，貼上完整 **Webhook URL**，點 **Verify**，成功後開啟 **Use webhook**。如果官方帳號的內建自動回覆已開啟，在管理後台的回應設定關閉它，以免收到兩份回覆。 [LINE Webhook 設定](https://developers.line.biz/en/docs/messaging-api/building-bot/)
4. 掃描同一個 **Messaging API** 頁面的 QR Code，用你的手機 LINE 加官方帳號為好友，傳送「明天下午三點開會」。收到地點和對象的追問後，補完資料並用「明天行程」確認存檔。

Quick Tunnel 重新啟動後會換網址。每次換網址都要更新 LINE 的 Webhook URL。這個版本每次最多處理一則模型請求，Codex 有 40 秒的回覆期限，以便在 LINE 的 reply token 有效期內回覆。收到忙碌通知時，等上一則完成後再傳。 [LINE Reply token 規則](https://developers.line.biz/en/reference/messaging-api/#send-reply-message)

| 目前卡住的地方 | 檢查方式 |
| --- | --- |
| `bot.py ask` 無法取得回覆 | 在同一個 Windows 帳號、VS Code 終端機執行 `codex login status`。若需要，從擴充套件重新登入。使用自訂模型時先清空 `CODEX_MODEL` 測試。 |
| LINE Verify 失敗 | 確認 Python 和 HTTPS 通道已開啟、網址結尾是 `/webhook`、Channel Secret 屬於該 Channel。`Invoke-RestMethod http://127.0.0.1:8000/health` 可檢查本機服務。 |
| Verify 成功但手機收不到回覆 | 確認已啟用 **Use webhook**、手機帳號和 `LINE_OWNER_USER_ID` 對應，查看後端終端機是否有 LINE API 錯誤。回覆 API 的 `401` 通常是存取權杖無效。 |
| 收到官方帳號的罐頭文字 | 在 LINE Developers Console 的 **Messaging API** 分頁，於 **Auto-reply messages** 點 **Edit**，到管理後台的回應設定關閉自動回覆。如果罐頭文字是在加好友時收到，可以在同處關閉 **Greeting messages**。 [LINE 自動回覆設定](https://developers.line.biz/en/docs/messaging-api/building-bot/#settings-on-line-official-account-manager) |

## 專案接下來可以做什麼

| 階段 | 功能與授權 |
| --- | --- |
| 排程紀錄 | 已支援口語新增、補問、取消草稿與查詢。可在 `assistant.txt` 修改一般回答的風格。 |
| 任務與提醒 | 依已記錄的行程設定 LINE 主動推播。推播計入官方帳號訊息用量，即時回覆不計入。 [LINE 訊息計費說明](https://developers.line.biz/en/docs/messaging-api/pricing) |
| 行事曆或待辦 | 選定服務後新增各自的 OAuth 授權，從讀取行事曆或待辦起步，再按需求加入建立和變更事件。 |
| 長時間運作 | 使用固定 HTTPS 網址、常駐主機與程序管理。若繼續使用本機 Codex 登入，`.env`、服務程序與可使用 Codex 的 Windows 帳號需在該主機就緒。 |

程式在本機儲存行程資訊、待補欄位及近期已處理的排程事件 ID。通過授權的訊息文字與當前行程草稿會由本機 Codex 送往 OpenAI，供它判斷應如何回覆。Codex 以唯讀模式執行，程式停用終端機、網頁搜尋和擴充工具。
