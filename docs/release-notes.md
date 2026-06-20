# Release notes

## 1.3a1 — 真正多帳號並行（CLI 與 bot 同套 supervisor）

### 重點

- **CLI 預設走 worker**：`tron run` / `python -m tron_roll_call_hero.tron` 現在預設透過 `AccountWorker`/supervisor 執行（`worker_enabled` 預設 ON）。`now: <學號>` 跑單一帳號；`now: class A` **同時**啟動群組內每個有效帳號。每帳號獨立 session / cookie / runtime state，一帳號失敗不影響其他。
- 舊的單帳號 `monitor_loop` 仍保留（`worker_enabled=False` 可達），相容既有行為與測試。
- Worker 事件雙寫回每日 JSONL 稽核與 console；新增 `worker_phase` 事件（上課/待機/登入失敗等轉換），bot/console 可訂閱。
- `config.yaml` 變更於非互動 worker 路徑會自動套用（`config_reload_watcher` → `app.reload()`），只重啟受影響帳號，不需重啟整支程式。
- `tron status`（`--json`）新增 `multi_account` 區段：每帳號 bot/monitor 狀態 + 目前 `now` 會啟動／略過哪些帳號（不含密碼／cookie）。
- 本機 dashboard（`bot serve --supervisor`）卡片新增「略過」帳號顯示。

### 支援的點名類型

數字（Number）、雷達（Radar）、手動 QR、教師輔助 QR（Teacher QR，需設教師帳號）。每種都各帳號獨立 confirmed。

### Partial failure（部分失敗）行為

群組中單一帳號登入失敗 / session 過期 / 提交失敗 **只影響該帳號**，其餘帳號照常監控與簽到。受影響帳號會在 `tron status` 的 `multi_account.accounts`、bot `accounts` 指令、dashboard 卡片顯示其 `phase` / `login` 狀態。

### Mixed-provider（跨校 provider）限制

- 同一群組可混不同學校的 provider；各帳號的 endpoints 依其 `provider_key` 推導（不依賴單一全域 active provider）。
- 數字／雷達／手動 QR 完全 per-account，混 provider 無限制。
- **Teacher QR coordinator 以單一教師身分運作**：跨 provider 的群組做 teacher 輔助 QR 時，僅限該教師帳號在其 provider 上可見的課程；不同 provider 的學生若需 teacher QR，需各自的教師設定。

### Migration（舊狀態遷移）

啟動時會自動讀取並遷移舊版狀態，不需手動處理：

- 舊 `state/cookies/<profile>.json` → `state/accounts/<profile>/cookies.json`
- 舊 `account_runtime.json` → `state/accounts/<profile>/runtime.json`

寫入一律走「temp 檔 + `os.replace`」原子寫入，且各帳號只寫自己的目錄，不會互相覆蓋。

### 打包（PyInstaller）

`tron-roll-call-hero.spec` 的 hidden imports 已涵蓋全部 runtime 模組與動態 dispatch 目標；打包檔不含 `state/` `config.yaml` `log/` `tests/`。Windows console / config reload / shutdown 的 frozen-build smoke 需在 Windows 實機驗證。
