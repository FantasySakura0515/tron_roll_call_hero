# Auto-Rollcall-thu-Tronclass

**TronClass 校園點名系統的全自動點名工具｜支援東海 THU、輔大 FJU、淡江 TKU、中山 NSYSU、朝陽 CYUT、海大 NTOU 與 TronClass 公有雲**

登入學校帳號後，它會在你設定的上課時段自動盯著課程，一偵測到點名就替你完成簽到——你不用一直盯著手機，也不用手忙腳亂找點名碼。

> ⚠️ 請只在你自己有權限、且符合學校與課程規範的情況下使用。**不要把填好帳密的 `config.yaml`、cookie、`state/`、`log/` 傳給任何人。**

---

## Fork 來源（這是一個衍生專案）

本專案是一個 **fork**，並非從零開始。直接 fork 來源（上游）為：

- **上游 repo**：[hot-YUser/auto-rollcall-thu-tronclass](https://github.com/hot-YUser/auto-rollcall-thu-tronclass)
- **上游作者**：hot-YUser
- **上游授權**：GNU AGPL-3.0-or-later

上游提供了整套核心：數字 / 雷達 / QR 點名的偵測與分流、跨校 provider、假點名門檻，以及雷達的 WGS84 全球定位備援求解器。本 fork 在這個基礎上繼續開發。

完整衍生鏈（fork 來源往上追）：

- [silvercow002/tronclass-script](https://github.com/silvercow002/tronclass-script)（最初源頭，MIT 授權）
- → [hot-YUser/auto-rollcall-thu-tronclass](https://github.com/hot-YUser/auto-rollcall-thu-tronclass)（改以 AGPL-3.0 授權，本專案的直接上游）
- → 本專案（衍生自 AGPL 上游，沿用 AGPL-3.0）

> 💡 上游當時的 Python 套件名是 `troTHU`，指令是 `python -m troTHU.tron`。本 fork 已將套件更名為 `tron_roll_call_hero`，所以下面所有指令都是 `python -m tron_roll_call_hero.tron`。

### 本 fork 相對上游的主要改動

- **套件更名**：`troTHU` → `tron_roll_call_hero`（CLI 進入點同步更名）。
- **真正多帳號並行**：`now: class A` 一次啟動群組內每個有效帳號，各自獨立 session / cookie / 狀態，一帳號失敗不影響其他；本機 CLI（`tron run`）與 Discord bot 走同一套 worker supervisor。
- **新增輔大（FJU）provider**：帳密 + 圖形驗證碼自動登入（ddddocr OCR，選用），辨識失敗回退人工輸入、不偽裝成功。
- **再擴充學校**：新增中山 (NSYSU)、朝陽 (CYUT) —— 與東海同屬 WisdomGarden Keycloak CAS，直接重用 `thu_cas`；以及海大 (NTOU) —— Apereo CAS + 圖形驗證碼，重用 `tronclass_form_captcha`（同輔大）。
- **Discord bot 24/7 部署**：`docker compose up -d` 一鍵起服務、crash 自動拉起、預設走 Gateway 長連線，不需公開網址（見 [docs/deploy.md](docs/deploy.md)）。
- **設定檔自動重載 watcher**，以及多帳號 worker 的穩定性 / 可觀測性強化。

完整版本變更與升級遷移見 [docs/release-notes.md](docs/release-notes.md)。授權細節見文末「[授權](#授權)」。

---

## 這個工具可以幹嘛

- ✅ **數字點名** — 完整支援。已經過無數次實際課堂驗收與打磨，是成熟、穩定的全自動完成版：偵測到點名 → 自動拿到點名碼 → 自動簽到，全程零操作。
- ✅ **雷達點名** — 完整支援。同樣經過大量實戰驗收，偵測到雷達點名後會自動完成定位簽到，不需要你開地圖、不需要對座標。就算哪天伺服器補掉了現在的捷徑，背後還有一套自己寫的**「全球定位演算法」（WGS84 多點定位）**能反推教室座標頂上，不會因此失效。
- ⚠️ **QR Code 點名** — 預設支援手動貼上 / 剪貼簿輔助；若你另外提供一個有權限發起 QR 點名的 TronClass 教師帳號，可啟用「教師輔助」自動完成、全程零操作。

> 順帶一提，它不會「搶當第一個簽到的人」：偵測到點名後，會先確認這是一場真的、全班性的點名（已經有一定比例的同學陸續簽到）才出手，避免老師只是手滑誤開、又馬上關掉的「假點名」也把你簽進去。這是一道貼心的容錯保險，預設就開著、你什麼都不用設。

關於 QR：學生端 API 不會提供 QR 的 `data` token，所以未設定教師帳號時，程式只會提示你貼上 QR 內容或嘗試剪貼簿輔助。教師輔助模式會使用你自備的教師帳號即時發起一場 QR 點名取得 `data`，再用學生帳號送出；教師登入失敗不會影響數字 / 雷達點名。

### 支援的學校（provider）

四個 provider 都已是可日常使用的成熟狀態，數字 / 雷達 / QR / 教師輔助全部支援；差別只在「怎麼登入」：

| 學校 / 站台 | `school` 代碼 | 網域 | 登入方式 |
| --- | --- | --- | --- |
| 東海大學「iLearn」 | `THU` | `ilearn.thu.edu.tw` | 校園 CAS（Keycloak）聯合登入 |
| 輔仁大學 | `FJU` | `elearn2.fju.edu.tw` | 帳密 + 圖形驗證碼（ddddocr 自動辨識，失敗回退人工輸入） |
| 淡江大學「iClass」 | `TKU` | `iclass.tku.edu.tw` | 校園 SSO（HTTP 快速登入，登入頁改版時自動改用瀏覽器輔助） |
| 中山大學 | `NSYSU` | `elearn.nsysu.edu.tw` | 校園 Keycloak CAS（同 THU 流程） |
| 朝陽科技大學 | `CYUT` | `tronclass.cyut.edu.tw` | 校園 Keycloak CAS（同 THU 流程） |
| 海洋大學 | `NTOU` | `tronclass.ntou.edu.tw` | 校園 Apereo CAS + 圖形驗證碼（同 FJU；裝 `.[ocr]` 自動辨識、否則人工） |
| TronClass 公有雲 | `TRONCLASS` | `www.tronclass.com.tw` | Email / 密碼表單登入 |

`school` 大小寫不分，也吃中文別名（`東海`、`輔仁`、`淡江`、`中山`、`朝陽`、`海大`、`官方` 等）。

**中山、朝陽、海大是最新加入的**:中山/朝陽與東海同屬 WisdomGarden Keycloak CAS,直接重用 `thu_cas`;海大 (NTOU) 是 Apereo CAS **＋圖形驗證碼**(同輔大,重用 `tronclass_form_captcha`;裝 `.[ocr]` 自動辨識、否則人工輸入),且它的 CAS 主機(`tccas.ntou.edu.tw`)與 app 主機不同。三校的登入流程都已查證、離線測試通過,但**尚未用真實帳號跑過端到端登入**;歡迎這幾校的同學回報實際結果。

> 補充一個常見的誤會：TronClass 是一套被很多學校採用的校園系統，但**各校上架時都會自己取名**——在東海它叫「iLearn」、在淡江叫「iClass」、TronClass 公有雲官網則直接叫「TronClass」。名字不一樣，骨子裡卻是同一套 API；所以同一套登入＋點名流程，只要換掉網域和登入方式，就能套到不同學校。

---

## 怎麼開始用

### 我只是想用（Windows，最簡單）

1. 到 Releases 下載 Windows 版的 zip。
2. **整包解壓縮**到一個固定資料夾（不要在 zip 裡直接雙擊）。
3. 進到資料夾，執行 `tron-roll-call-hero.exe`。

第一次啟動會在 exe 旁邊自動建立 `config.yaml`、`state/`、`log/` 三樣東西。程式一啟動就直接進入監控；**按任意鍵**就會用記事本打開 `config.yaml` 讓你填帳號密碼，存檔關掉記事本後它會自動重新讀取設定。

### 我想用原始碼跑（開發者）

裝好相依套件就能直接跑，不用自己打包：

```bash
python -m pip install -r requirements.txt
python -m tron_roll_call_hero.tron
```

就這樣。一樣是啟動即監控、按任意鍵用記事本開 `config.yaml`。

如果你要放在工作排程器或背景服務、不希望它監聽按鍵：

```bash
python -m tron_roll_call_hero.tron run --no-input
```

> 啟動後它**不會清螢幕、不會跳全螢幕介面**，只會在視窗裡一行一行印出目前在做什麼（正在登入、目前時段、偵測到點名、簽到成功…），讓你一眼看出它還活著。
>
> 輔大（FJU）帳號建議多裝一個 OCR 套件來自動辨識登入驗證碼：`python -m pip install -e .[ocr]`（沒裝也能用，只是要手動輸入驗證碼）。

### 在伺服器上用 Discord bot 跑（推薦：真多帳號、24/7）

把它部署到一台一直開著的機器，用 Discord bot 控制多個帳號同時自動點名——數字／雷達全自動，QR 在設好教師帳號時也全自動（否則用 Discord `qr` 指令手動送）。**預設走 Discord Gateway 長連線，不需要公開網址。**

```bash
cp config.example.yaml config.yaml   # 填多帳號 + 時段（THU / FJU / TKU 都可）
cp .env.example .env                  # 填 Discord bot 金鑰
docker compose up -d                  # 一鍵起服務，crash/重開自動拉起（restart: unless-stopped）
docker compose logs -f
```

容器內實際跑的是 `bot discord-gateway --supervisor`（多帳號 supervisor 模式）。完整步驟（建 Discord bot、systemd 替代方案、指令用法、安全）見 **[docs/deploy.md](docs/deploy.md)**；版本變更、升級遷移、跨校 provider 與 partial-failure 行為見 **[docs/release-notes.md](docs/release-notes.md)**。

- 真正多帳號並行：每帳號獨立 session / cookie / 狀態，一帳號失敗不影響其他；worker 會用遞增退避自動重連。
- 預設帶 15% 假點名門檻（等班上開始有人簽到才出手）。
- Docker image 預設裝 ddddocr，輔大（FJU）帳密 + 圖形驗證碼可自動登入（要關掉用 `--build-arg INSTALL_OCR=0`）。

---

## 設定檔教學（最重要的一步）

九成的人會卡在這裡，所以講仔細一點。

### 先說一個容易誤會的點

`config.yaml` 雖然副檔名是 `.yaml`，但它**其實不是標準 YAML**，而是這個專案自己設計、給人手動編輯用的超簡單格式（是的，副檔名取得有點名不符實，我們自己也吐槽過）。所以：

- 冒號後面**有沒有空格都可以**：`user:s123` 和 `user: s123` 都行。
- 學校**大小寫不分**：`THU`、`thu`、`東海` 都認得。
- 行尾可以用 `//` 寫註解；`(...)` 或 `（...）` 包起來的內容會被當成「還沒填」。
- 你**不需要**懂 YAML 縮排規則，照著下面的範例填就好。

一般使用者主要改四塊：`now`、`account`、`group`、`operating`。如果要啟用 QR 教師輔助，再填 `teacher`。其他進階設定都放在另一個檔 `config.advanced.yaml`，平常完全不用碰。

### `config.yaml` 範例與逐塊說明

```text
now:(填帳號或 class A)

account:
  user:(帳號1)
  passwd:(密碼1)
  school:THU

  user:(帳號2)
  passwd:(密碼2)
  school:FJU

  user:(帳號3)
  passwd:(密碼3)
  school:TKU

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
  0:
    enable:true
    range:
    - 00:00 - 00:00
```

**`now`** — 現在要用哪個帳號。可以填某個帳號的學號（例如 `now:s1234567`），也可以填一個群組（例如 `now:class A`）。
> 小撇步：如果你整份 `account` 只填了一個有效帳號，`now` 可以**留空**，程式會自動用那一個，不會逼你再填一次。

**`account`** — 你的帳號清單，可以放很多組。每組三行：`user`（學號）、`passwd`（密碼）、`school`（`THU`、`FJU`、`TKU`、`NSYSU`、`CYUT`、`NTOU` 或 `TRONCLASS`）。

**`teacher`** — （選用）QR 教師輔助帳號。`user` / `passwd` 是教師帳密，`school` 可填 `TRONCLASS`、`THU`、`FJU`、`TKU`；`course` 留空時會用 `/api/my-courses` 自動挑第一個課程，也可以手動填課程 ID。

**`group`** — （進階／選用）把帳號分組。`class:A` 開一個叫 A 的群組，底下用 `user` 列出屬於這組的帳號。搭配 `now:class A` 會**同時**啟動整組帳號並行監控。只有一個帳號的話這塊可以不用管。

**`operating`** — 上課時段，也就是「什麼時候才需要自動盯點名」。

- 星期是數字：**`0` = 星期日、`1` = 星期一 … `6` = 星期六**。
- `enable:true` 代表這天啟用。
- `range:` 底下用 `- 開始 - 結束` 列時段，**同一天可以列很多段**：

```text
operating:
  1:
    enable:true
    range:
    - 09:10 - 12:00
    - 13:20 - 17:30
```

（上面是「星期一 09:10–12:00 和 13:20–17:30 都自動盯點名」。）

### 不想把明碼寫進檔案？

填密碼那關如果不想把明碼直接寫進 `config.yaml`，有幾種做法：

- **環境變數**（單帳號最快）：`TRON_USER` / `TRON_PASS`（教師帳號是 `TRON_TEACHER_USER` / `TRON_TEACHER_PASS`）。
- **系統金鑰圈**：安裝 `.[keyring]` 後改用 OS keyring 保存（見下方選用功能）。
- 解析優先序：執行期傳入 > 環境變數 > keyring > `config.yaml`。

### 改完設定後

填好帳密、存檔、關掉記事本，程式就會自動重新讀取（背景服務也有設定檔 watcher）。如果你改了 `now`，它會清掉目前的登入狀態並切換到新帳號或新群組。

### 常用設定指令

```bash
python -m tron_roll_call_hero.tron config show       # 看目前讀到的設定
python -m tron_roll_call_hero.tron config doctor      # 檢查設定有沒有問題
python -m tron_roll_call_hero.tron config advanced    # 用記事本打開 config.advanced.yaml
python -m tron_roll_call_hero.tron config compact --write   # 把舊版設定檔整理成新版格式（會先自動備份）
```

`config.advanced.yaml` 是真正的 YAML，預設是空的，放時區、number/radar 細部調整、Bot 設定等進階項。例如：

```yaml
time:
  timezone: Asia/Taipei

monitor:
  ignore_attendance_rate_gate: false   # 設 true 可關掉 15% 假點名門檻

radar:
  strategy: empty_answer               # empty_answer 或 global_wgs84
```

---

## 聊天機器人通知（選用，但很好用）

不想一直開著視窗看？可以把點名結果丟到聊天軟體。Bot 這塊目前做得相當完整，token / 密鑰一律只從環境變數讀，不會寫進 log。

### Discord（推薦，可雙向控制）

部署服務時**預設、也推薦走 Gateway 長連線**——掛著一條到 Discord 的連線即可，**不需要公開網址**，最適合放在家用機 / VPS 24/7 跑：

```bash
python -m tron_roll_call_hero.tron bot discord-gateway --supervisor   # 長連線 + 多帳號 supervisor（Docker 預設）
python -m tron_roll_call_hero.tron bot discord-schema --json          # 看要註冊哪些 slash 指令
python -m tron_roll_call_hero.tron bot discord-sync --apply            # 把 slash 指令同步到 Discord（預設 dry-run）
```

Discord 可以**雙向操作**：查狀態、`start` / `stop`、強制檢查、重新登入、貼 QR 內容簽到等（指令清單用 `discord-schema` 看）。

如果你偏好不掛長連線、改用 webhook，也保留了 **HTTP Interactions** 模式（需要一個對外的 HTTPS 端點）：

```bash
python -m tron_roll_call_hero.tron bot serve --adapter discord        # 本機起 webhook 服務（預設 127.0.0.1）
```

### LINE

走 webhook（需要對外 HTTPS），支援 `X-Line-Signature` 簽章驗證、回覆（reply）與推播（push）。常用環境變數：

```text
LINE_CHANNEL_ACCESS_TOKEN
LINE_CHANNEL_SECRET
```

### Telegram

目前是**單向通知**（程式 → 你），把結果推給你看；不提供從 Telegram 反向下指令。綁定方式：

```bash
python -m tron_roll_call_hero.tron account bind telegram <你的 TELEGRAM_CHAT_ID> default
```

### 想先在本機試 webhook？

```bash
python -m tron_roll_call_hero.tron bot serve --adapter generic
```

送個最簡單的測試請求：

```json
{"source_user_id":"user-id","channel_id":"local","text":"status"}
```

---

## 其他功能與常用指令

`python -m tron_roll_call_hero.tron <command>`，常用的有：

- **多帳號 / 群組（真正並行）**：`now: <學號>` 監控單一帳號；`now: class A` 會**同時**啟動群組內每個有效帳號，各自獨立 session / cookie / 狀態，一帳號失敗不影響其他，數字／雷達／QR 各自簽到。本機 CLI（`tron run`）與 Discord bot 走的是同一套 supervisor（worker 架構）。
- **帳號管理**：`account list` / `account add <名稱>` / `account switch <名稱>` / `account state` / `account doctor`，以及 bot 綁定 `account bind` / `account unbind`。
- **狀態與診斷**：`status --json` 印出本機狀態；`doctor` 一鍵檢查環境、設定、登入來源；`dashboard` 開一個會更新的輕量狀態面板；`logs tail` / `logs summarize` / `logs export` 看與打包（去敏）日誌。
- **課程與 provider**：`courses` 探索本學期課程；`provider list` / `provider show` 看支援的學校與能力旗標。
- **QR 工具**：`qr <payload>`（直接送）、`qr paste`（剪貼簿）、`qr image <檔案>`（從圖片解碼，需 `.[qr-image]`）、`qr scan`（本機掃描器）、`qr pending`（列出待簽）。
- **教師端工具**：`teacher rollcall create/start/stop`（需設教師帳號）。
- **本機唯讀面板**：`app serve --open` 在 localhost 開一個唯讀小面板，只能「看」狀態（不會送點名、不會匯入 cookie、不會改帳號）。
- **時區排程**：`config.advanced.yaml` 裡可設 IANA 時區（如 `Asia/Taipei`），每天可有多個時段。

---

## 原理：它到底是怎麼自動簽到的？

這段用白話講「為什麼做得到」。本質上，TronClass 這套系統把一些**本來不該讓學生拿到的東西，透過學生自己就能呼叫的 API 漏掉了**，這個工具就是把這些漏洞自動化而已。

### 偵測到點名後，為什麼先等一下再簽

預設情況下，程式偵測到點名後**不會立刻送出**，而是先回查這堂課的簽到率，等到「全班到課率達 15%」（已經有 15% 的同學簽到）才出手。這是一道刻意設計的容錯保險：萬一老師只是手滑誤開、開了又馬上關掉，這種根本沒人簽的「假點名」就不會把你簽進去；等到班上開始有人陸續簽到、確認是真的在點名了，程式才動作。數字 / 雷達 / QR 三種都適用（QR 會在等待期間先用教師帳號把點名預備好，門檻一過立刻送出）。

如果你不想要這道保險、希望一偵測到就立刻簽到，到 `config.advanced.yaml` 把 `monitor.ignore_attendance_rate_gate` 設成 `true` 即可（開發 / 排程場景也可以用 `python -m tron_roll_call_hero.tron run --ignore-attendance-rate-gate` 臨時關閉這一輪）。

### 數字點名：點名碼其實藏在 API 回應裡

老師按下數字點名後，會在螢幕投影一組四位數字要大家輸入。問題是：**學生端有一支 API（`student_rollcalls`）會直接把這組正確的點名碼回給你**。所以這個工具偵測到數字點名後，直接去讀那組碼、一發送出就完成——正常情況下一次點名只要極少的請求。

萬一哪天那支 API 不給碼了，還有後備方案：四位數字也才 0000–9999 一萬種，直接暴力試碼（有限流冷卻、降併發，不會把伺服器打爆），所以**不會退化、依然會成功**。送出後會再回查一次，確認真的變成「已簽到」（`on_call_fine`）才算數。

### 雷達點名：送一個「空答案」就過了

雷達點名理論上要驗證你的 GPS 座標在教室範圍內。但實測發現一個明確的伺服器漏洞：**對點名送出一個完全空的答案 `{}`（不帶任何座標），伺服器就直接把你判定為「到場」。** 這招實測 100% 成功，所以是預設、也是主力做法——送出後再回查一次確認真的簽到成功才算數。

### 雷達備援：自己寫的全球定位演算法

萬一哪天「空答案」這個捷徑被伺服器補掉，雷達點名也不會就此失效——後面還接著一套自己刻的定位備援，這也是這個專案裡花最多心思的一塊：

它利用一個有趣的特性：**當你送出的座標答錯時，伺服器會好心地回傳「你離目標還有多遠」。** 程式把這個「距離」當成觀測量，朝不同方位、不同距離撒出多圈探測點，收集到一組「在這個點距離教室約 N 公尺」的資料後，就能在 WGS84 地球橢球座標系上用最小平方法做**多點定位（multilateration，多邊測距定位）**，反推出教室的精確經緯度，再把那個座標送出去簽到。求解用的是抗離群值的穩健最小平方搭配 pattern-search 與 Levenberg–Marquardt 迭代收斂；真的還收斂不出來，才退到最後一招——以估計點為中心、一圈一圈無限往外擴的棋盤格逐格掃描，直到命中或點名結束。

特別說明：這整套定位是**純手工打造、零外部數學套件**（不依賴 numpy / scipy），所以能直接打包進單一個 exe 裡跑。它平常幾乎輪不到出場（空答案就解決了），但它是貨真價實、能獨立運作的定位引擎，不是擺著好看的。

### QR 點名：手動內容或教師帳號輔助

QR 點名的學生端 API 只接受 `data` + `deviceId`，但**不會**把 `data` 回給學生，所以一定得從別的地方拿到那串 `data`。

未設定教師帳號時，程式保留三條手動路徑：直接貼上 QR 內容、用本機掃描器、或從剪貼簿自動帶入送出（要從圖片解碼 QR 需另裝 `qr-image` 套件）。

設定 `teacher` 後就能全自動：程式一偵測到 QR 點名，會先用教師帳號**預備好**一場教師端 QR 點名（趁等待簽到率門檻的同時就先備著）；輪到可以送出時，讀取教師端 `qr_code` API 那串**會定時輪換（約每 15 秒）**的 `data`，立刻送出學生端 QR answer，並在確認窗口內反覆刷新、重送，直到回查 `student_rollcalls` 確認自己已 `on_call_fine`（簽到成功），最後把教師端那場點名關掉。整個過程不需要你動手。

---

## 技術細節（給想複製到其他學校的開發者）

TronClass 是不少學校共用的底層校園系統（各校自行命名上架：東海＝iLearn、淡江＝iClass、公有雲＝TronClass…），下面整理核心 API 與做法，方便其他同樣用 TronClass 的學校快速理解、自行實作。除了 THU / FJU / TKU，這套 runtime 也能套用在 **TronClass 公有雲官網**以及其他基於 TronClass 的學校（換掉 base URL 與登入流程即可）。

> 端點以 `{base}` 代表學校的 TronClass 網域（東海 `https://ilearn.thu.edu.tw`、輔大 `https://elearn2.fju.edu.tw`、淡江 `https://iclass.tku.edu.tw`…）。所有請求都帶登入後的 session cookie。

### 列出目前的點名

```http
GET {base}/api/radar/rollcalls?api_version=1.1.0
```

回傳目前進行中的點名清單與類型（number / radar / qr），程式據此分流處理。

### 數字點名（越權讀碼 + 後備暴力）

```http
# 1) 直接讀出正確點名碼（關鍵：這支學生就能呼叫）
GET {base}/api/rollcall/{rollcall_id}/student_rollcalls
    → 回應內含 number_code 欄位

# 2) 送出簽到
PUT {base}/api/rollcall/{rollcall_id}/answer_number_rollcall
    body: {"deviceId": "<隨機>", "numberCode": "0837"}
```

讀不到 `number_code` 時，就對 `answer_number_rollcall` 以 `0000`–`9999` 批次併發試碼（含限流冷卻與降併發）。送出後再回查狀態，確認 `on_call_fine` 才採信。

### 雷達點名（空答案漏洞 + 距離反推備援）

```http
# 主力：空答案即過（伺服器漏洞）
PUT {base}/api/rollcall/{rollcall_id}/answer
    body: {}
# 送出後回查 rollcall 狀態，確認為 on_call_fine（已簽到）才採信。

# 備援：帶座標的答案；答錯時回應會夾帶「距離目標多遠」
PUT {base}/api/rollcall/{rollcall_id}/answer?api_version=1.76
    body: { ...座標、device、user 等... }
GET {base}/api/rollcall/{rollcall_id}/lite   # 取得 beacon / 訊號等附帶資訊
```

備援解法把「距離」當觀測量，用穩健最小平方法在 WGS84 上做多點定位反推教室座標，再不行則以無限棋盤格逐格覆蓋。雷達策略鏈為 **`empty_answer → global_wgs84`**（由 `config.advanced.yaml` 的 `radar.strategy` 選擇，預設 `empty_answer`）；全球定位求解器在 `tron_roll_call_hero/global_radar_solver.py`，是零數學套件依賴的純 Python 實作。

### QR 點名（教師輔助取得 data）

```http
# 教師帳號建立 / 啟動一場 QR 點名
POST {teacher_base}/api/course/{course_id}/rollcall
POST {teacher_base}/api/rollcall/{teacher_rollcall_id}/start-rollcall

# 教師端讀取動態 QR data（約每 15 秒輪換）
GET {teacher_base}/api/course/{course_id}/rollcall/{teacher_rollcall_id}/qr_code
    → 回應內含 data

# 學生帳號送出原本課堂的 QR 點名
PUT {student_base}/api/rollcall/{student_rollcall_id}/answer_qr_rollcall
    body: {"data": "<teacher data>", "deviceId": "<隨機>"}

# 不論成功失敗都關閉教師端點名
PUT {teacher_base}/api/rollcall/{teacher_rollcall_id}/stop_qr_rollcall
```

送出後會再讀學生端 `student_rollcalls` / `answers` 確認狀態。教師帳號登入失敗或找不到課程時，只會停用 QR 教師輔助，數字與雷達點名仍照常監控。

### 程式結構速覽

- `tron_roll_call_hero/runtime_context.py`：中央樞紐，持有全域執行狀態，並把扁平的函式命名空間懶載入到各模組。新增要能用 `ctx.foo` 呼叫的函式時，要在這裡的 `_LEGACY_EXPORTS` 註冊。
- `tron_roll_call_hero/monitor_runtime.py`：預設的監控主迴圈（登入 → 依排程 → 偵測點名 → 分流）。
- `tron_roll_call_hero/account_supervisor.py`、`account_worker.py`：多帳號並行的 supervisor 與單帳號 worker（獨立 session / cookie / 狀態、失敗隔離、重連退避）。
- `tron_roll_call_hero/number_runtime.py`、`radar_runtime.py`：兩種點名的實作核心（上面的 API 就在這裡）；雷達的全球定位求解器另放在 `global_radar_solver.py`（純 Python WGS84 多點定位）。
- `tron_roll_call_hero/qr_runtime.py`、`qr_teacher_runtime.py`：QR 手動 / 剪貼簿送出與教師帳號輔助流程。
- `tron_roll_call_hero/providers.py`：支援的學校登錄表（base URL、登入流程、能力旗標），加新學校從這裡開始。
- `tron_roll_call_hero/tron_http.py`：端點驅動的 HTTP client 與登入流程（THU CAS / FJU 表單+驗證碼 / TKU SSO / 公有雲 email 登入）。

### 安裝選用功能（原始碼）

```bash
python -m pip install -e .[packaging]   # PyInstaller 打包
python -m pip install -e .[browser]     # Playwright（登入頁改版時的後備登入）
python -m pip install -e .[keyring]     # 用系統金鑰圈存帳密
python -m pip install -e .[qr-image]    # 從圖片解碼 QR（opencv + Pillow）
python -m pip install -e .[ocr]         # 輔大（FJU）登入驗證碼自動辨識（ddddocr）
```

> 輔大採帳密自動登入：登入頁圖形驗證碼會先用 ddddocr 自動辨識，未安裝
> `.[ocr]` 或辨識失敗時，互動式 CLI 會請你手動輸入驗證碼；背景監控辨識不出
> 則回報 `captcha_required`，不偽裝成功。

---

## 開發與測試

測試全部離線執行（用假的 TronClass 伺服器模擬），不會碰到任何真實學校：

```bash
python -m py_compile tron_roll_call_hero/tron.py tron_roll_call_hero/runtime_context.py tron_roll_call_hero/cli_main.py
python -m unittest discover -v
python -m tron_roll_call_hero.tron release-build --dry-run --json
```

---

## 目前限制

- **QR 教師輔助需要可登入且可發起點名的教師帳號**；未設定或登入失敗時，只保留手動貼上 / 剪貼簿輔助。
- **Telegram 只做單向通知**，不接收指令；雙向控制請用 Discord。
- 預設的 Windows zip 是精簡包，不內建 Playwright、keyring、QR 影像解碼等選用功能；需要的話請用原始碼安裝對應 extras。

---

## 授權

本專案 fork 自 [hot-YUser/auto-rollcall-thu-tronclass](https://github.com/hot-YUser/auto-rollcall-thu-tronclass)，該上游以 **GNU Affero General Public License v3.0 或更新版本（`AGPL-3.0-or-later`）** 授權（其更上游 [silvercow002/tronclass-script](https://github.com/silvercow002/tronclass-script) 原為 MIT，於上游 fork 時改採 AGPL）。

作為其衍生作品，本 fork 同樣以 **AGPL-3.0-or-later** 釋出（完整條款見 [LICENSE](LICENSE)）：

- 你可以自由使用、研究、修改、散布本專案。
- 若你**散布**本專案（或其修改版），或將修改版**透過網路提供服務**，必須依 AGPL 條款向使用者提供對應的完整原始碼。
- 衍生作品必須保留相同授權，並標示出對原始碼的修改與來源。

致謝：核心點名 runtime、跨校 provider 與 WGS84 全球定位求解器源自上游 hot-YUser 及更上游 silvercow002 的原始工作。
