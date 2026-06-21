# Auto-Rollcall-thu-Tronclass

TronClass 校園點名的全自動工具：登入後在你設定的時段盯著課程，偵測到點名就自動完成簽到。支援東海 (THU)、輔大 (FJU)、淡江 (TKU)、中山 (NSYSU)、朝陽 (CYUT)、海大 (NTOU) 與 TronClass 公有雲。

![License](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)
![Status](https://img.shields.io/badge/status-alpha-orange.svg)
![Schools](https://img.shields.io/badge/providers-7-green.svg)

> ⚠️ 僅在你自己有權限、且符合學校與課程規範時使用。**不要把填好帳密的 `config.yaml`、cookie、`state/`、`log/` 外流給任何人。**

## 目錄

- [這個工具可以幹嘛](#這個工具可以幹嘛)
- [怎麼開始用](#怎麼開始用)
- [設定檔教學](#設定檔教學)
- [聊天機器人通知](#聊天機器人通知)
- [指令速查](#指令速查)
- [原理](#原理)
- [技術細節](#技術細節)
- [開發與測試](#開發與測試)
- [目前限制](#目前限制)
- [授權](#授權)

## 這個工具可以幹嘛

三種點名，偵測到就自動簽到——送出後都會回查確認真的簽到成功才算數：

| 點名類型 | 狀態 | 做法 |
| --- | --- | --- |
| **數字點名** | ✅ 全自動 | 直接從學生端 API 讀出正確點名碼送出 |
| **雷達點名** | ✅ 全自動 | 送「空答案」即過；備援為自寫的 WGS84 定位反推 |
| **QR Code 點名** | ⚠️ 半自動 | 手動貼上 / 剪貼簿；設教師帳號可「教師輔助」全自動 |

預設帶 **15% 假點名門檻**：偵測到點名後先確認已有約 15% 同學簽到，才出手，避免老師誤觸又馬上關掉的空點名把你簽進去（可關）。

此外還支援：

- **真多帳號並行** — `now: class A` 一次跑整組帳號，各自獨立 session / cookie / 狀態，互不影響。
- **聊天機器人** — Discord 可雙向控制，LINE / Telegram 通知；可用 Docker 24/7 部署。
- **本機唯讀面板**、**跨校 provider**、**時區與多時段排程**、**去敏日誌**、**環境自我檢查**。

### 支援的學校（provider）

七個站台共用同一套點名引擎，數字 / 雷達 / QR / 教師輔助全支援；差別只在登入方式：

| 學校 / 站台 | `school` | 網域 | 登入方式 |
| --- | --- | --- | --- |
| 東海「iLearn」 | `THU` | `ilearn.thu.edu.tw` | Keycloak CAS |
| 輔仁 | `FJU` | `elearn2.fju.edu.tw` | 帳密 + 圖形驗證碼（ddddocr 自動辨識） |
| 淡江「iClass」 | `TKU` | `iclass.tku.edu.tw` | SSO（HTTP 快速登入，必要時瀏覽器輔助） |
| 中山 | `NSYSU` | `elearn.nsysu.edu.tw` | Keycloak CAS（同 THU） |
| 朝陽 | `CYUT` | `tronclass.cyut.edu.tw` | Keycloak CAS（同 THU） |
| 海洋 | `NTOU` | `tronclass.ntou.edu.tw` | Apereo CAS + 圖形驗證碼（同 FJU） |
| TronClass 公有雲 | `TRONCLASS` | `www.tronclass.com.tw` | Email / 密碼 |

`school` 大小寫不分，也吃中文別名（`東海`、`輔仁`、`淡江`、`中山`、`朝陽`、`海大`、`官方`）。TronClass 是多校共用系統、各校自取品牌名（iLearn / iClass…），底層同一套 API，所以換網域與登入流程就能套到新學校。

> 中山 / 朝陽 / 海大為新加入：登入流程已查證、離線測試通過，但**尚未用真實帳號端到端驗證**，歡迎回報。

## 怎麼開始用

依情境選一條：只想用 → Windows；想改程式 → 原始碼；要 24/7 多帳號 → Discord bot。

### Windows（免安裝）

到 Releases 下載 Windows zip，**整包解壓**到固定資料夾，執行 `tron-roll-call-hero.exe`。首次啟動會在旁邊建立 `config.yaml`、`state/`、`log/`，並直接進入監控；**按任意鍵**用記事本開 `config.yaml` 填帳密，存檔後自動重載。

### 原始碼（開發者）

```bash
python -m pip install -r requirements.txt
python -m tron_roll_call_hero.tron            # 啟動即監控，按任意鍵編輯 config.yaml
python -m tron_roll_call_hero.tron run --no-input   # 背景 / 排程，不監聽按鍵
```

啟動後不清螢幕、不跳全螢幕，只逐行印出當前狀態。FJU / NTOU 帳號建議加裝 OCR：`pip install -e .[ocr]`（沒裝則手動輸入驗證碼）。

### Discord bot（推薦：真多帳號、24/7）

部署到常開機器，用 Discord 控制多帳號。**預設走 Gateway 長連線，不需公開網址。**

```bash
cp config.example.yaml config.yaml   # 多帳號 + 時段
cp .env.example .env                  # Discord bot 金鑰
docker compose up -d                  # crash 自動拉起（restart: unless-stopped）
```

容器內跑的是 `bot discord-gateway --supervisor`。完整步驟（建 bot、systemd、安全）見 **[docs/deploy.md](docs/deploy.md)**；版本與遷移見 **[docs/release-notes.md](docs/release-notes.md)**。

## 設定檔教學

`config.yaml` **不是標準 YAML**，是本專案自訂的簡易格式：冒號後空格可有可無、學校大小寫不分、`//` 為註解、`(...)` 視為未填。一般只改 `now`、`account`、`group`、`operating`；QR 教師輔助再加 `teacher`；其餘進階項放 `config.advanced.yaml`，平常不用碰。

```text
now:(填帳號或 class A)

account:
  user:(帳號1)
  passwd:(密碼1)
  school:THU

  user:(帳號2)
  passwd:(密碼2)
  school:FJU

teacher:
  user:(教師帳號)
  passwd:(教師密碼)
  school:TRONCLASS
  course:(留空自動偵測)

group:
  class:A
    school:THU
    user:(帳號1)

operating:
  1:
    enable:true
    range:
    - 09:10 - 12:00
    - 13:20 - 17:30
```

- **`now`** — 目前要用哪個帳號或群組（`now:s1234567` 或 `now:class A`）。只有一個帳號時可留空。
- **`account`** — 帳號清單，每組三行 `user` / `passwd` / `school`（`THU`、`FJU`、`TKU`、`NSYSU`、`CYUT`、`NTOU`、`TRONCLASS`）。
- **`teacher`** —（選用）QR 教師輔助帳號；`course` 留空時自動取第一門課。
- **`group`** —（選用）帳號分組；`now:class A` 會**同時並行**啟動整組。
- **`operating`** — 上課時段。星期為數字（`0`=日 … `6`=六），`enable:true` 啟用，`range:` 下可列多段。

不想把明碼寫進檔案：用環境變數 `TRON_USER` / `TRON_PASS`（教師為 `TRON_TEACHER_*`），或裝 `.[keyring]` 用系統金鑰圈。解析優先序：執行期 > 環境變數 > keyring > `config.yaml`。

進階檔 `config.advanced.yaml` 是真 YAML，放時區、number / radar 微調、Bot 設定等：

```yaml
time: { timezone: Asia/Taipei }
monitor: { ignore_attendance_rate_gate: false }   # true = 關掉 15% 門檻
radar: { strategy: empty_answer }                 # empty_answer | global_wgs84
```

用 `config show` 看設定、`config doctor` 檢查、`config advanced` 開進階檔。

## 聊天機器人通知

把點名結果丟到聊天軟體。token / 密鑰一律只從環境變數讀，不寫進 log。

| 平台 | 能力 | 部署 |
| --- | --- | --- |
| **Discord**（推薦） | 雙向控制：查狀態、`start` / `stop`、強制檢查、重新登入、貼 QR 簽到 | Gateway 長連線（預設，免公開網址）或 HTTP Interactions webhook |
| **LINE** | webhook + 簽章驗證 + reply / push | 需對外 HTTPS（`LINE_CHANNEL_ACCESS_TOKEN` / `LINE_CHANNEL_SECRET`） |
| **Telegram** | 單向通知（不收指令） | `account bind telegram <CHAT_ID> default` |

本機試 webhook：`bot serve --adapter generic`，送 `{"source_user_id":"u","channel_id":"local","text":"status"}`。

## 指令速查

原始碼用 `python -m tron_roll_call_hero.tron <...>`；Windows 版用 `tron-roll-call-hero.exe` 加同樣子指令。

| 類別 | 指令 |
| --- | --- |
| 監控 | `run`、`run --no-input`、`run --ignore-attendance-rate-gate` |
| 設定 | `config show` / `doctor` / `advanced` / `compact --write` |
| 帳號 | `account list` / `add` / `switch` / `state` / `doctor` / `bind` / `unbind` |
| 狀態診斷 | `status --json`、`doctor`、`dashboard`、`logs tail` / `summarize` / `export` |
| 課程 / 學校 | `courses`、`provider list` / `show` |
| QR | `qr <payload>` / `paste` / `image <檔>` / `scan` / `pending` |
| 教師端 | `teacher rollcall create` / `start` / `stop` |
| Bot | `bot discord-gateway --supervisor`、`discord-schema`、`discord-sync --apply`、`serve --adapter <discord\|line\|generic>` |
| 面板 / 發佈 | `app serve --open`、`release-build --dry-run` / `--execute` |

## 原理

TronClass 把一些**本不該讓學生取得的資料，透過學生自己就能呼叫的 API 漏了出來**，本工具只是把這些漏洞自動化。以下講「為什麼做得到」，對應的端點見[技術細節](#技術細節)。

**先等再簽（假點名門檻）。** 偵測到點名後不立即送出，先回查簽到率，達 15% 才動作，濾掉老師誤觸的空點名。數字 / 雷達 / QR 皆適用。要立即簽到就把 `monitor.ignore_attendance_rate_gate` 設 `true`（或 `run --ignore-attendance-rate-gate`）。

**數字點名。** 學生端 API `student_rollcalls` 會直接回傳正確點名碼，讀到即送出。若哪天不回碼，後備是 `0000`–`9999` 限流暴力試碼，依然會成功。

**雷達點名。** 伺服器漏洞：送出**空答案 `{}`**（不帶座標）即被判定到場，實測 100% 成功，為主力做法。

**雷達備援（自寫定位）。** 萬一空答案被修掉：送錯座標時伺服器會回傳「離目標多遠」，程式把這個距離當觀測量、朝多方位多距離撒探測點，再用穩健最小平方法在 WGS84 上做**多點定位**反推教室座標（pattern-search + Levenberg–Marquardt 收斂，再不行則無限棋盤格逐格掃描）。整套**純 Python、零數學套件依賴**（不靠 numpy / scipy），可直接打包進 exe。

**QR 點名。** 學生端 API 收 `data` + `deviceId` 但不回 `data`，得從別處取得：未設教師帳號時走手動（貼上 / 掃描器 / 剪貼簿）；設了 `teacher` 則自動——用教師帳號預備一場 QR 點名，讀取每約 15 秒輪換的 `data`，立即以學生帳號送出並反覆確認直到 `on_call_fine`。

## 技術細節

給想移植到其他 TronClass 學校的開發者。`{base}` 為學校網域，所有請求帶登入後的 session cookie。換掉 base URL 與登入流程即可套用到新站台。

### 列出點名

```http
GET {base}/api/radar/rollcalls?api_version=1.1.0   # 回傳進行中點名與類型(number/radar/qr)
```

### 數字（越權讀碼 + 後備暴力）

```http
GET {base}/api/rollcall/{id}/student_rollcalls          # 回應含 number_code
PUT {base}/api/rollcall/{id}/answer_number_rollcall      # body: {"deviceId":"<隨機>","numberCode":"0837"}
```

讀不到碼則對 `answer_number_rollcall` 批次試碼 `0000`–`9999`（限流冷卻 + 降併發）。送出後回查 `on_call_fine` 才採信。

### 雷達（空答案 + 距離反推）

```http
PUT {base}/api/rollcall/{id}/answer                      # 主力：body {}
PUT {base}/api/rollcall/{id}/answer?api_version=1.76     # 備援：帶座標，答錯時回應夾帶距離
GET {base}/api/rollcall/{id}/lite                        # beacon / 訊號附帶資訊
```

策略鏈 `empty_answer → global_wgs84`（`config.advanced.yaml` 的 `radar.strategy` 選擇）；求解器在 `tron_roll_call_hero/global_radar_solver.py`。

### QR（教師輔助取得 data）

```http
POST {teacher_base}/api/course/{course_id}/rollcall
POST {teacher_base}/api/rollcall/{tid}/start-rollcall
GET  {teacher_base}/api/course/{course_id}/rollcall/{tid}/qr_code    # 回應含 data(約 15 秒輪換)
PUT  {student_base}/api/rollcall/{sid}/answer_qr_rollcall            # body: {"data":"<data>","deviceId":"<隨機>"}
PUT  {teacher_base}/api/rollcall/{tid}/stop_qr_rollcall              # 收尾
```

送出後讀 `student_rollcalls` / `answers` 確認。教師帳號失敗時只停用 QR 輔助，數字 / 雷達照常。

### 程式結構

| 模組 | 職責 |
| --- | --- |
| `runtime_context.py` | 全域執行狀態樞紐 + 懶載入命名空間 |
| `monitor_runtime.py` | 監控主迴圈（登入 → 排程 → 偵測 → 分流） |
| `account_supervisor.py` / `account_worker.py` | 多帳號並行的 supervisor 與單帳號 worker |
| `number_runtime.py` / `radar_runtime.py` / `global_radar_solver.py` | 數字 / 雷達實作與純 Python 定位求解器 |
| `qr_runtime.py` / `qr_teacher_runtime.py` | QR 手動與教師輔助 |
| `providers.py` | 學校登錄表（加新學校的起點） |
| `tron_http.py` | 端點驅動的 HTTP client 與各校登入流程 |

選用功能：`pip install -e .[packaging|browser|keyring|qr-image|ocr]`（打包 / 後備瀏覽器登入 / 金鑰圈 / QR 影像解碼 / 驗證碼 OCR）。帳密自動登入的學校（FJU / NTOU）未裝 `.[ocr]` 或辨識失敗時，互動式 CLI 請你手動輸入驗證碼，背景監控則回報 `captcha_required`、不偽裝成功。

## 開發與測試

測試全離線（假 TronClass 伺服器模擬），不碰真實學校：

```bash
python -m unittest discover -v
python -m tron_roll_call_hero.tron release-build --dry-run --json
```

## 目前限制

- QR 教師輔助需可登入且能發起點名的教師帳號；否則僅手動貼上 / 剪貼簿。
- Telegram 僅單向通知；雙向控制請用 Discord。
- 中山 / 朝陽 / 海大尚未用真實帳號端到端驗證。
- Windows zip 為精簡包，不含 Playwright / keyring / QR 影像解碼，需要時用原始碼裝對應 extras。

## 授權

本專案衍生自 [hot-YUser/auto-rollcall-thu-tronclass](https://github.com/hot-YUser/auto-rollcall-thu-tronclass)（更上游 [silvercow002/tronclass-script](https://github.com/silvercow002/tronclass-script)），已大幅改寫，依上游採 **GNU AGPL-3.0-or-later** 釋出，完整條款見 [LICENSE](LICENSE)。散布或以網路服務提供修改版時，須依 AGPL 提供對應原始碼並保留相同授權。
