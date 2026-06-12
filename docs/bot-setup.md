# Bot 交互界面設定（Discord + LINE）

本系統的 bot 指令層是平台無關的：LINE 與 Discord 都經過同一個
command mapping（`adapter_bridge`）、同一個授權/audit 層（`BotRuntime`）、
同一個 supervisor bridge（`bot_supervisor_bridge`），所以兩個平台的指令
行為完全一致。

## 啟動方式

```bash
# Webhook server（LINE + Discord interactions + generic），並在同一個
# process 內啟動多帳號監控 supervisor：
tron bot serve --supervisor --host 127.0.0.1 --port 8787

# 或只跑 Discord Gateway（長連線，不需要公開 URL）：
tron bot discord-gateway --supervisor
```

不加 `--supervisor` 時，bot 只能讀取靜態 runtime 狀態（舊行為）；
加上後，`status / start / stop / force / reauth / qr` 都直接操作
live `AccountWorker`。

## 本機監控儀表板

`tron bot serve --supervisor` 啟動時會自動掛上本機儀表板，
console 會印出一次性網址：

```text
Dashboard: http://127.0.0.1:8787/dashboard?token=<隨機 token>
```

- token 每次啟動隨機產生；所有 `/dashboard` 路由都要求 `?token=` 或
  `X-Dashboard-Token` header，錯誤回 401。
- 頁面每 3 秒輪詢：帳號卡片（phase / login / polls / 最近錯誤 +
  Force / Reauth 按鈕）、點名事件流、QR 提交（全部或單帳號）、近 7 天統計。
- 事件歷史存於 `state/dashboard/events-YYYY-MM-DD.jsonl`，內容經過
  secret sanitizer，不含密碼 / cookie / QR data。
- server 預設綁 127.0.0.1；如需遠端存取請走 tunnel，不要直接對外開放。

## 指令集（兩平台一致）

| 指令 | 行為 |
| --- | --- |
| `status [profile]` | 顯示綁定（或指定）帳號的 worker 即時狀態（phase / login / polls） |
| `accounts` | 全部帳號摘要 |
| `start` / `stop` | 啟動 / 停止自己的 worker |
| `force [profile\|all]` | 立刻輪詢一次並執行偵測到的點名（`all` 限 admin） |
| `reauth [profile]` | 只清除該帳號的 session cookie，worker 會自動重新登入 |
| `qr <payload>` | 用自己的帳號送出 QR 內容 |
| `qr all <payload>` | fan-out 給所有 running workers，回覆每個帳號的結果（限 admin） |

回覆一律不包含密碼、cookie、QR raw data；部分失敗時逐帳號顯示
profile 與狀態。

## Discord（建議的日常主通道）

Discord 有兩種接法，擇一即可：

1. **Gateway 長連線（最簡單，本機可用）**：不需要公開 URL。
   設定 bot token 後執行 `tron bot discord-gateway --supervisor`。
2. **HTTP Interactions（webhook）**：需要公開 HTTPS URL（見下方 tunnel），
   在 Discord Developer Portal 將 Interactions Endpoint 指到
   `https://<你的網址>/discord/interactions`。

Slash command schema 用 `tron bot discord-schema` 輸出、
`tron bot discord-sync --apply` 同步。

## LINE（需要 tunnel）

LINE Messaging API 只支援 webhook，本機部署必須有公開 HTTPS endpoint。
建議用 Cloudflare Tunnel（免費、免帳號可快速測試）：

```bash
# 1. 安裝 cloudflared
brew install cloudflared        # macOS
# winget install Cloudflare.cloudflared   # Windows

# 2. 啟動 bot server
tron bot serve --supervisor --port 8787

# 3. 另開終端，把本機 8787 開成公開 HTTPS URL
cloudflared tunnel --url http://127.0.0.1:8787
# 會印出一個 https://xxxx.trycloudflare.com 網址
```

然後在 [LINE Developers Console](https://developers.line.biz/) 的
Messaging API 設定：

- Webhook URL：`https://xxxx.trycloudflare.com/line/webhook`
- 開啟「Use webhook」
- 把 channel secret / access token 設成環境變數，並在 config 的
  `integrations.line` 指定對應的 env 名稱（`secret_env` / `token_env`）

> 注意：trycloudflare 的臨時網址每次重啟會變。長期使用請建立
> named tunnel（`cloudflared tunnel create ...`）或改用 ngrok 付費
> 固定網域，否則每次都要回 LINE console 更新 Webhook URL。

ngrok 替代方案：

```bash
ngrok http 8787
# Webhook URL: https://<隨機>.ngrok-free.app/line/webhook
```

## 帳號綁定與授權

- 使用者必須先綁定：`tron account bind <adapter> <external_user_id> <profile>`
  （bot 指令路由用 profile name，不用 user 推測）。
- 一般使用者只能控制自己綁定的 profile；admin（config
  `integrations.admins.<adapter>` 列表）可以控制所有 profile 與
  `force all` / `qr all`。
- 危險指令（force / reauth）有 cooldown，全部操作可寫入 audit log。
