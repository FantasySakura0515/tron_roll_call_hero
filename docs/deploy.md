# 部署指南：用 Discord bot 跑多帳號點名伺服器

把 `tron-roll-call-hero` 部署到一台 24/7 的機器上，用 Discord bot 控制多帳號自動點名（數字／雷達／QR）。預設走 **Discord Gateway 長連線**，不需要公開網址。

> **多帳號 live 監控需要 supervisor 模式**（`bot discord-gateway --supervisor`）：bot 才會啟動每個帳號的 live worker、指令（`status / force / reauth / qr`）才會操作真實 worker。本指南的 **Dockerfile `CMD`** 與 **systemd `ExecStart`（`deploy/tron-bot.service`）已預設帶上 `--supervisor`**，照本指南部署即為真多帳號並行；若你自訂啟動指令，記得保留這個旗標（沒帶的話 bot 只會讀靜態 runtime 狀態）。

---

## 1. 建立 Discord application 與 bot

1. 到 <https://discord.com/developers/applications> → **New Application**，取個名字。
2. 左側 **Bot** → **Add Bot**；按 **Reset Token** 取得 **Bot Token**（這就是 `DISCORD_BOT_TOKEN`，只會顯示一次，複製好）。
3. 在 **Bot** 頁把 **Message Content Intent** 打開（若你用文字指令）。
4. 左側 **General Information** 拿 **Application ID**（`DISCORD_APPLICATION_ID`）與 **Public Key**（`DISCORD_PUBLIC_KEY`）。
5. 左側 **OAuth2 → URL Generator**：勾 `bot`（與 `applications.commands`），複製產生的邀請連結，在瀏覽器開啟，把 bot 邀進你的伺服器。

> Gateway 模式下不需要設定 Interactions Endpoint URL；bot 主動連 Discord。

---

## 2. 準備 config.yaml（帳號與時段）

```bash
cp config.example.yaml config.yaml
chmod 600 config.yaml          # 只有你能讀（含密碼）
nano config.yaml               # 填入你的帳號密碼
```

重點欄位：

- `accounts`：每組 `user` / `passwd` / `school`（`FJU` / `THU` / `TKU` / `TRONCLASS`）。多帳號就多列幾組。
- `group` + `now`：把帳號分組，`now: class A` 一次啟動整組。
- `teacher`（選用）：填了才會**自動完成 QR 點名**；留空則 QR 走 Discord 手動 `/qr`。
- `operating`：上課時段，`0`=週日 … `6`=週六。

> 輔大（FJU）是帳密 + 圖形驗證碼登入。Docker image 預設裝了 ddddocr 自動辨識，輔大帳號可以無人值守自動登入；萬一辨識不出會回報 `captcha_required`（在 Discord `status` 看得到）。

---

## 3. 準備 .env（Discord 金鑰）

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

填入第 1 步拿到的：

```
DISCORD_BOT_TOKEN=...
DISCORD_PUBLIC_KEY=...
DISCORD_APPLICATION_ID=...
```

> `.env` 與 `config.yaml` 都已被 `.gitignore` 擋住，不會進版本庫；密碼也不會被打包進 Docker image（見 `.dockerignore`）。

---

## 4A. 用 Docker 跑（推薦）

需要 Docker 與 docker compose。

```bash
docker compose up -d            # 第一次會 build image（含 OCR，較久）
docker compose logs -f          # 看 log，確認登入與監控啟動
```

- 容器設 `restart: unless-stopped`，機器重開或程式 crash 會自動拉起。
- `config.yaml` / `state/` / `log/` 以 volume 掛載，更新程式不會動到你的資料。
- 不需要 OCR（沒有 captcha 學校）想要精簡 image：`docker compose build --build-arg INSTALL_OCR=0`。

更新版本：

```bash
git pull
docker compose build && docker compose up -d
```

---

## 4B. 用 systemd 跑（不想用 Docker 的替代方案）

適合 Linux VPS、直接從原始碼跑。

```bash
sudo mkdir -p /opt/tron-roll-call-hero
sudo cp -r . /opt/tron-roll-call-hero        # 或 git clone 到這裡
cd /opt/tron-roll-call-hero
python -m pip install '.[ocr]'               # 含輔大 captcha 自動辨識
cp config.example.yaml config.yaml && chmod 600 config.yaml   # 填好帳密
cp .env.example .env && chmod 600 .env                        # 填好 Discord 金鑰

sudo cp deploy/tron-bot.service /etc/systemd/system/tron-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now tron-bot
journalctl -u tron-bot -f                    # 看 log
```

`deploy/tron-bot.service` 預設 `Restart=always`、`WorkingDirectory=/opt/tron-roll-call-hero`、`EnvironmentFile=.../.env`。路徑不同請自行修改。

---

## 5. 在 Discord 控制它

bot 上線後，在伺服器用指令（兩平台一致）：

| 指令 | 行為 |
| --- | --- |
| `status [profile]` | 顯示帳號 worker 即時狀態（phase / login / polls） |
| `accounts` | 全部帳號摘要 |
| `start` / `stop` | 啟動 / 停止自己的 worker |
| `force [profile\|all]` | 立刻輪詢一次並執行偵測到的點名（`all` 限 admin） |
| `reauth [profile]` | 只清該帳號 session cookie，worker 會自動重新登入 |
| `qr <payload>` | 手動把 QR 內容送出（沒設 teacher 帳號時用） |
| `qr all <payload>` | fan-out 給所有 running 帳號（限 admin） |

平時你什麼都不用做：到上課時段 worker 自動盯點名、自動完成數字／雷達／（有 teacher 的）QR。

---

## 6. 安全與維運

- `config.yaml` / `.env` 權限設 `600`，不要 commit、不要外傳。
- 回覆與 log 不含密碼、cookie、QR raw data。
- 定期備份 `state/`（裡面是每帳號的 cookie 快取與 runtime 狀態）：`tar czf state-backup.tgz state/`。
- 看某帳號卡住（`status` 顯示 `login_failed` / `captcha_required`）：先 `reauth <profile>`；輔大若一直 `captcha_required` 代表自動辨識不順，需人工處理該帳號。
