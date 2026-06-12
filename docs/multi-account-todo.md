# 多帳號重構 TODO

- 狀態：Planning
- 日期：2026-06-09
- 架構文件：[multi-account-framework.md](architecture/multi-account-framework.md)
- ADR：[0001-multi-account-runtime.md](architecture/0001-multi-account-runtime.md)

## 使用方式

- 一次只進行一個 phase。
- 每個 checkbox 對應可驗證的結果，不以「有寫程式」視為完成。
- 每個建議 commit 完成後先跑該 phase 測試，再跑完整 suite。
- 不可跳過 single-account migration 直接啟用並行。
- 不可在 `AccountWorker` / `AccountSupervisor` 尚未接線前，把 client/console/bot 改成宣稱「真正多帳號」。
- client、dashboard、bot 必須讀同一個 supervisor/runtime snapshot，不各自重新解析或切換 active profile。
- README 在功能端到端通過前不得宣稱真正多帳號已完成。

## 目前狀態快照

已完成的是多帳號 runtime 的地基：

- account domain models
- target registry
- per-account state/cookie repository
- account context
- account-scoped auth
- account-scoped polling/progress
- account event identity

尚未完成的是「真正同時監控與簽到」：

- Number/Radar/QR executor 尚未全面 account-scoped
- `AccountWorker` 尚未建立
- `AccountSupervisor` 尚未建立
- `app_main()` 尚未改走 worker/supervisor
- fake server 尚未支援多 authenticated students
- client/console/dashboard/bot 尚未接 live supervisor

## 優先順序守則

1. 先讓單帳號完整走新 worker。
2. 再讓多帳號 supervisor 同時跑 Number/Radar。
3. 再接 QR fan-out / teacher QR coordinator。
4. 最後才改 client、dashboard、bot、README。

原因：client 如果先做，只會接到舊 global runtime 或半成品 wrapper，後面 supervisor 行為一改會整批重做。

## 全程守則

- [x] 新核心模組不 import `troTHU.runtime_context`（account_models / account_registry / account_state_repository，AST 測試驗證）
- [ ] 並行路徑不呼叫 `switch_profile`（並行路徑尚未建立）
- [ ] 並行路徑不讀寫全域 active provider（並行路徑尚未建立）
- [ ] 每個 account event 都有 `profile` 與 `provider_key`（event 系統 Phase 2.3）
- [ ] 密碼、cookie、QR data 不進 log、snapshot、exception message（snapshot 已達成；log/exception 待 Phase 2）
- [ ] 單帳號 CLI 行為保持相容（目前未接線，574 baseline 維持通過；Phase 2 接線時再確認）
- [x] 每個 commit 通過 `git diff --check`
- [x] 每個 phase 結束跑 `uv run python -m unittest discover -v`

## Phase 0：基線與設計

狀態：完成

- [x] 初始化 Git repository
- [x] 建立重構前 baseline commit `c6ffdc6`
- [x] 確認 `config.yaml`、`state/`、`log/`、`.venv/` 被忽略
- [x] 完整執行 574 項測試
- [x] 建立 ADR 0001
- [x] 定義模組框架
- [x] 建立分階段 TODO

驗收：

- [x] 工作樹沒有未追蹤敏感檔案
- [x] baseline 可獨立 checkout
- [x] 架構決策與目前限制有文件

## Phase 1：Domain 與 Repository

目標：建立新框架的純模型與 persistence boundary，不接入監控。

### 1.1 Account models

- [x] 新增 `troTHU/account_models.py`
- [x] 定義 `AccountSpec`
- [x] 定義 `CredentialRef` / `CredentialSource`
- [x] 定義 `AccountConfig`
- [x] 定義 `AccountRuntimeState`
- [x] 定義 `AccountStateSnapshot`
- [x] 定義 `AccountWorkerSnapshot`
- [x] 定義 `SubmissionStatus`
- [x] 定義 `SubmissionResult`
- [x] 定義 `GroupSubmissionResult`
- [x] 確認 secret 欄位不在 model 中

測試：

- [x] dataclass equality / immutability
- [x] snapshot serialization
- [x] result aggregation
- [x] repr 不含敏感值

建議 commit：

```text
refactor: add account runtime domain models
```

### 1.2 Account registry

- [x] 新增 `troTHU/account_registry.py`
- [x] 從 normalized config 建立 `AccountSpec`
- [x] 單帳號 target resolution
- [x] 空白 now 單帳號推斷
- [x] group target resolution
- [x] mixed-provider group
- [x] missing credential skipped reason
- [x] unknown account/provider skipped reason
- [x] 保留輸入順序並去除重複帳號
- [x] 不在 resolution result 放密碼

測試：

- [x] THU + THU group
- [x] THU + TKU mixed group
- [x] 缺密碼帳號
- [x] keyring credential ref
- [x] manual-cookie provider
- [x] 不存在的 group/account
- [x] JSON encode 不含密碼

建議 commit：

```text
refactor: resolve monitor targets into account specs
```

### 1.3 Per-account repository

- [x] 新增 `troTHU/account_state_repository.py`
- [x] 定義 repository Protocol
- [x] 實作 file repository
- [x] runtime path：`state/accounts/<profile>/runtime.json`
- [x] cookie path：`state/accounts/<profile>/cookies.json`
- [x] temp file + `os.replace`
- [x] profile path normalization
- [x] corrupt file safe fallback
- [x] 舊 `account_runtime.json` migration reader
- [x] 舊 `state/cookies/<profile>.json` migration reader
- [x] snapshot sanitizer

測試：

- [x] 兩帳號寫入互不覆蓋
- [x] atomic write shape
- [x] corrupt runtime
- [x] legacy migration
- [x] traversal profile name
- [x] sensitive fields redacted/rejected

建議 commit：

```text
refactor: isolate persisted state by account
```

Phase 1 驗收：

- [x] `AccountRegistry.desired_specs()` 可產生群組所有帳號
- [x] repository 可獨立保存兩帳號
- [x] 未修改 `monitor_runtime.app_main`
- [x] 未啟用任何多帳號並行
- [x] 完整測試通過（632 項：574 baseline + 58 新增）

## Phase 2：單帳號走新 Context

目標：移除單帳號核心流程對 active profile globals 的依賴。

### 2.1 Runtime services 與 context

- [x] 新增 `troTHU/runtime_services.py`
- [x] 新增 `troTHU/account_context.py`
- [x] 定義 `CredentialResolver`
- [x] 定義 `CookieRepository`
- [x] 定義 `RuntimeEventSink`
- [x] 定義可測試 `Clock`
- [x] 建立 `RuntimeServices`
- [x] 建立單帳號 `AccountContextFactory`

測試：

- [x] config 不被 context factory 修改
- [x] endpoints 依 account provider 正確
- [x] credential resolver precedence
- [x] context 不持有明碼密碼

建議 commit：

```text
refactor: introduce explicit account context
```

### 2.2 Auth 與 session

- [x] 新增 `login_account(account)`（新模組 `auth_account.py`）
- [x] provider guard 改讀 `account.spec.provider_key`（manual-cookie / provider 由 spec 判斷；research/daily gate 尚未移植）
- [ ] browser-assisted config 改讀 `account.config`（延後：account 路徑尚未重現 browser-assist）
- [x] cookie save 走 repository（load/restore 待 worker 接線時補）
- [x] login result 寫入 `account.state`
- [x] 保留 legacy `login(session)` wrapper
- [ ] session expiry 只清該帳號 cookie（各帳號已有獨立 jar；明確 expiry-relogin 待 worker）

測試：

- [x] THU login context
- [ ] TKU login context（fake server 目前單一帳密；待 Phase 3.2 多帳密 server）
- [x] manual-cookie context
- [x] 兩帳號 cookie 路徑隔離
- [x] legacy auth tests 維持通過

建議 commit：

```text
refactor: scope authentication to account context
```

### 2.3 Event、log 與 notification

- [x] 定義 `RuntimeEvent`
- [x] account event 強制 profile/provider
- [ ] legacy `log()` 轉成 event adapter（延後：worker 接線時雙寫 console + event）
- [x] notification dedupe key 加 profile
- [x] per-account event identity
- [x] group-level event 規則（`group:<name>`）
- [x] secret sanitizer 套用 event data

測試：

- [x] 同 rollcall 兩帳號通知不互相 dedupe
- [x] event JSON 有 profile/provider
- [x] QR data/cookie/password 不可出現
- [x] 舊 log tests 維持通過

建議 commit：

```text
refactor: add account identity to runtime events
```

### 2.4 Poll 與 progress

- [x] `poll_rollcall_decision(account)`
- [x] `fetch_account_progress(account, rollcall_id)`
- [x] progress 寫入 `account.state.last_progress`
- [x] `my_user_no` 使用 `account.spec.user`
- [x] completed state 改讀 account state（`account_completed()`）
- [x] 保留 legacy wrapper（`rollcall_progress`/`rollcall_runtime` 不動）

測試：

- [x] progress identity 使用 user，不使用 profile 猜測
- [x] account state 隔離
- [x] 同 rollcall ID 可在兩帳號分別完成（per-account completed 集合）

建議 commit：

```text
refactor: scope polling and progress to account state
```

### 2.5 Number

- [x] 拆出 `NumberCodeResolver`（`troTHU/number_account.py`）
- [x] 拆出 `NumberSubmissionExecutor`
- [x] direct lookup 接受 account context
- [x] brute force 接受 account context
- [x] submit/verify 回傳 `SubmissionResult`
- [x] completed number 寫 account state
- [x] legacy `number(session, rcid)` wrapper（`number_runtime` 原路徑不動，即為 legacy 入口）

測試：

- [x] direct code success
- [x] brute force fallback
- [x] unauthorized
- [x] submitted-unconfirmed
- [x] account A 完成不跳過 account B

建議 commit：

```text
refactor: split number resolution from account submission
```

### 2.6 Radar

- [x] `answer_radar_rollcall(account, rollcall)`（`troTHU/radar_account.py`）
- [x] empty answer 使用 account endpoints/session
- [x] global solver 使用 account config
- [x] fallback 使用 account state
- [x] submit/verify 回傳 `SubmissionResult`
- [x] completed radar 寫 account state
- [x] legacy wrapper（`radar_runtime` 原路徑不動，即為 legacy 入口）

測試：

- [x] empty answer success
- [x] global solver fallback
- [x] account-scoped provider endpoints（context endpoints 驅動全部請求；AST 測試確認不讀 active globals）
- [x] account A 完成不跳過 account B

建議 commit：

```text
refactor: scope radar execution to account context
```

### 2.7 QR student path

- [x] manual payload submit 接受 account context（`troTHU/qr_account.py`）
- [x] `finalize_qr_submission` 寫 account state（account 路徑由 `submit_qr_payload_account` 內 verify + completed 寫入取代）
- [x] pending QR 以 profile/provider 記錄（`pending_qr` 既有 key；account 路徑只清自己的 profile/provider）
- [x] clipboard 路徑只作用於指定 account（clipboard 解碼為純 I/O；提交一律走指定 account 的 `submit_qr_payload_account`，worker 接線於 2.8）
- [x] completed QR 寫 account state
- [x] teacher assist 暫時維持 single-account adapter

測試：

- [x] manual payload account A/B 各自提交
- [x] pending registry account isolation
- [x] completed QR isolation
- [x] raw payload 不進 snapshot/log（snapshot/event/result JSON 皆驗證）

建議 commit：

```text
refactor: scope student QR execution to account context
```

### 2.8 AccountWorker 單帳號接線

- [x] 新增 `troTHU/account_worker.py`
- [x] worker 建立/關閉 session
- [x] worker login/retry state machine
- [x] worker schedule loop
- [x] worker poll/execute loop
- [x] worker runtime heartbeat（phase 轉換時持久化 per-account snapshot）
- [x] worker graceful stop
- [ ] `app_main()` 單帳號改由 worker 執行（延後：legacy monitor_loop 內建 teacher assist／console status／attendance gate，需先有 2.3 延後的 log/event adapter 與 Phase 4 teacher coordinator 才能不回歸地切換；屆時 worker 雙寫 console + event）
- [ ] legacy console input/edit 行為保持（同上，隨 app_main 接線一併驗證）

測試：

- [x] worker lifecycle
- [x] retry backoff
- [x] shutdown closes session
- [x] 單帳號 fake server E2E（number 偵測→直讀→confirmed→後續輪詢 skip）
- [x] 現有 monitor tests 維持通過（697 項全綠）

建議 commit：

```text
refactor: run single-account monitor through account worker
```

Phase 2 驗收：

- [ ] 單帳號 monitor 不依賴 active profile globals
- [ ] `rg` 新路徑無 `switch_profile`
- [ ] `rg` 新路徑無 `get_active_profile(ctx.CONFIG)`
- [ ] 單帳號 Number/Radar/QR E2E 通過
- [ ] 完整測試通過

## Phase 3：Supervisor 與 Number/Radar 多帳號

### 3.1 Supervisor 基礎

- [x] 新增 `troTHU/account_supervisor.py`
- [x] worker task registry
- [x] start all desired specs
- [x] isolated stop
- [x] isolated failure/restart
- [x] exponential backoff
- [x] aggregate snapshot
- [x] graceful global shutdown

測試：

- [x] 啟動兩 worker
- [x] 一個 worker crash，另一個持續
- [x] restart backoff
- [x] shutdown 全部完成

建議 commit：

```text
feat: supervise independent account workers
```

### 3.2 Fake server 多帳號模型

- [x] fake server 支援 credential map（`FakeTronServer(credentials={...})`，預設 user1/pass1 保持相容）
- [x] 每帳號不同 session cookie
- [x] request 可識別 authenticated account（number/radar/qr record 含 `user`）
- [x] 每帳號 student rollcall status（`per_account_state = True` 開啟隔離；預設 legacy 全域行為）
- [x] shared rollcall definition（feed 共用定義，per-account 只 overlay status）
- [x] 可指定某帳號 login/session/submit failure（`fail_login_users` / `expire_account_session` / `fail_submit_users`）

測試：

- [x] 不同帳號 cookie 不可互換
- [x] server request record 有 account
- [x] 狀態更新只影響該帳號

建議 commit：

```text
test: model multiple authenticated students in fake server
```

### 3.3 Number artifact coordinator

- [x] 新增 `troTHU/rollcall_artifact_coordinator.py`
- [x] key 為 provider + rollcall ID
- [x] direct code cache（`CoordinatedNumberCodeResolver` 可直接作為 `answer_number_rollcall` 的 resolver）
- [x] brute-force single-flight（暴力猜碼成功後 publish，其他帳號單發提交）
- [x] result TTL
- [x] error 不永久 cache
- [x] shutdown cancellation

測試：

- [x] 兩 worker 同時請求只 resolve 一次
- [x] 每帳號 submit 一次
- [x] resolver failure 可重試
- [x] 不同 provider 不共享

建議 commit：

```text
feat: coordinate shared number code discovery
```

### 3.4 開啟 group monitor

- [x] `application_runtime.py` 組裝 supervisor（`MonitorApplication`）
- [x] `now:class A` 啟動全部 worker
- [x] mixed-provider group（registry/factory 依各 spec provider 推導 endpoints；E2E 受限於單一 fake host，mixed 解析由 registry 測試覆蓋）
- [x] per-account startup report（`StartupReport`：started/skipped/warnings）
- [x] partial login failure report（status_report 顯示 per-account login_status/phase/healthy）
- [x] group Number 真實提交
- [x] group Radar 真實提交
- [x] 移除/棄用 `planned` fan-out 成功訊息（`submit_group_*` 改回傳 `ok: False, status: "deprecated"`，rollcall_runtime 的 ok 守門使訊息不再出現）

E2E：

- [x] 兩帳號同時監測 Number
- [x] 兩帳號各自 confirmed
- [x] 兩帳號同時監測 Radar
- [x] 兩帳號各自 confirmed
- [x] 一帳號 login fail，另一帳號成功
- [x] 一帳號 session expired，只重登該帳號

建議 commit：

```text
feat: monitor and answer number and radar for account groups
```

Phase 3 驗收：

- [x] 真正多帳號 Number/Radar 可運作（E2E：兩帳號各自 confirmed）
- [x] 每帳號 session/state/cookie 隔離（per-account session jar / runtime state / cookie repository）
- [x] partial failure 不停止全組
- [x] 完整測試通過（723 項）

## Phase 4：QR Coordinator

### 4.1 Manual QR fan-out

- [x] QR payload 解析一次（`troTHU/qr_fanout.py`，parsed 後分送 `submit_parsed_qr_account`）
- [x] 路由到 matching active workers
- [x] 每 worker 自己 submit/verify
- [x] 回傳 `GroupSubmissionResult`
- [x] partial failure 顯示 profile
- [ ] 未啟動 worker 可選擇 temporary account execution（延後：目前回報 `worker_not_running` skip；臨時帳號執行待 bot/client 需求明確時再做）

測試：

- [x] 兩帳號 submitted/confirmed
- [x] 一成功一失敗
- [x] provider mismatch
- [x] raw payload 不進 result/log

建議 commit：

```text
feat: submit manual QR through account workers
```

### 4.2 Teacher QR coordinator

- [x] 新增 `troTHU/teacher_qr_coordinator.py`
- [x] teacher session ownership（coordinator 擁有獨立 teacher session）
- [x] teacher login retry（失敗回報 `teacher_not_ready`，下次 assist 重試；unauthorized 清 login 狀態）
- [x] course resolution（config course_id 或 fetch_my_courses 第一筆）
- [x] prepare single-flight
- [x] rotating data memory distribution（每次 submit 前重新 fetch data，僅存在記憶體）
- [x] interested profile tracking
- [x] stop teacher rollcall lifecycle（all complete → stop；`stop_assist` 可由 source close 觸發）
- [x] shutdown cleanup
- [x] coordinator failure 不影響 Number/Radar（失敗以 per-account SubmissionResult 回報）

測試：

- [x] 兩 student 只 create 一次 teacher rollcall
- [x] 兩 student 使用各自 session submit
- [x] QR data rotation
- [x] all complete 後 stop
- [x] source rollcall close 後 stop（`stop_assist` 行為驗證；worker 接 feed close 偵測待 Phase 5 整合）
- [x] teacher login fail
- [x] raw data 不落地（snapshot/event/status 皆驗證）

建議 commit：

```text
feat: coordinate teacher-assisted QR across accounts
```

Phase 4 驗收：

- [x] Number/Radar/Manual QR/Teacher QR 都有 per-account result
- [x] teacher-side operation single-flight
- [x] QR secret 不持久化
- [x] 完整測試通過（735 項）

## Phase 5：Reload、Bot、狀態與發佈

### 5.1 Config reload

- [ ] watcher 讀新 config（檔案 watcher 留待 app_main 接線；reload API 已就緒）
- [x] registry 建新 desired specs
- [x] supervisor reconcile（`AccountSupervisor.reconcile`）
- [x] add/remove/restart/keep report（`MonitorApplication.reload` → `ReloadReport`）
- [x] 修改單一帳號只重啟該 worker（spec 變更或密碼變更 force-restart）
- [x] 無效 config 保留現有 worker
- [x] reload report 包含 added/removed/restarted/kept/skipped
- [x] reload 不清掉未受影響帳號 session/cookie
- [x] reload 失敗事件帶 profile/provider 或 group identity（`group:config` 事件）

測試：

- [x] add account 只啟動新增 worker
- [x] remove account 只停止移除 worker
- [x] change credential/provider 只重啟該 worker
- [x] invalid config 保留既有 workers
- [x] group membership change reconciliation（add/remove 即群組成員變更）

### 5.2 Status 與 console

- [ ] supervisor summary
- [ ] 每帳號 phase/login/check/error
- [ ] interactive console 不互相覆寫
- [ ] JSON status 包含 accounts
- [ ] dashboard 聚合 per-account repository
- [ ] console renderer 從 supervisor snapshot 讀資料
- [ ] console 顯示 partial failure，不把單一帳號失敗當全域失敗
- [ ] status JSON 顯示 desired accounts / running accounts / skipped accounts
- [ ] status JSON 不包含 password/cookie/QR data
- [ ] legacy single-account status 保持可讀

測試：

- [ ] two-account snapshot formatting
- [ ] one failed / one healthy status
- [ ] skipped account warning formatting
- [ ] status JSON secret redaction
- [ ] dashboard cards read per-account repository

### 5.3 Bot

平台決策（2026-06-11）：LINE 與 Discord 都要完整接上 supervisor。

- Discord 為日常主通道：gateway 長連線，本機執行不需公開 URL。
- LINE 走 webhook：本機部署需 tunnel（Cloudflare Tunnel 或 ngrok）打進 `adapter_server.py`，設定步驟寫入文件。
- 兩平台共用 `bot_runtime.py` 的授權 / audit / command mapping 層，adapter 只負責平台 I/O。
- 兩平台指令集一致：`status / start / stop / force / reauth / qr` 全部對應 supervisor 操作。

- [x] Discord gateway adapter 接 live supervisor（`tron bot discord-gateway --supervisor`；gateway 與 interactions webhook 共用同一 BotRuntime）
- [x] LINE webhook adapter 接 live supervisor（`tron bot serve --supervisor` 啟動 in-process monitor + webhook server）
- [x] 兩平台指令集一致（同一 `adapter_bridge` command mapping + `BotRuntime` 分派，bridge 與平台無關）
- [ ] LINE tunnel 啟動方式文件化並提供啟動指令
- [x] `start/stop` 真正控制 worker（`troTHU/bot_supervisor_bridge.py`；BotRuntime 新增 start/stop handler hooks）
- [x] `force` 路由 live worker（`AccountWorker.force_check()`）
- [x] `reauth` 只重登指定 account（`AccountWorker.request_reauth()` 只清自己 cookie jar）
- [x] `qr all` 路由 supervisor（經 `qr_fanout`）
- [x] authorization 規則保持（授權/binding/cooldown/audit 全留在 BotRuntime，未改動）
- [x] status 顯示真實 worker state
- [x] `force <profile>` 只觸發指定 worker
- [x] `force all` 觸發所有 running workers 並回傳 group result（admin 限定）
- [x] `reauth <profile>` 只清該 profile cookie/session
- [x] `status <profile>` 顯示單帳號狀態
- [x] `status all` 顯示群組摘要（`accounts` 指令回傳全帳號 phase/login 摘要）
- [x] partial failure 回覆包含 profile
- [x] bot command 不呼叫 `switch_profile`（source 掃描測試驗證）
- [x] adapter binding 使用 profile name，不使用 user 猜測 profile（沿用既有 binding）

測試：

- [x] regular user 只能控制授權 profile
- [x] admin 可控制 all profiles
- [x] force routes to correct worker
- [x] reauth does not touch other workers
- [x] qr all returns per-account results
- [x] bot responses redact secrets
- [x] Discord adapter E2E（interactions webhook → BotRuntime → live worker，status 反映真實 phase）
- [x] LINE adapter E2E（LINE webhook → BotRuntime → live worker；status 與 qr fan-out 完整鏈路、回覆 redaction 驗證）

### 5.4 Client / local app

- [ ] app shell 讀 supervisor snapshot
- [ ] accounts panel 顯示 running/stopped/skipped
- [ ] per-account detail panel 顯示 phase/login/last check/last error
- [ ] group action preview 顯示會影響哪些 profiles
- [ ] QR manual submit 可選 profile / all matching profiles
- [ ] client mutation routes 轉交 supervisor，不直接操作 global context
- [ ] client 不暴露 cookie、QR raw payload、password
- [ ] local dashboard 對 stopped supervisor 有安全 fallback

測試：

- [ ] app API `/status` 或等效 route 回傳 accounts array
- [ ] profile detail redacts secrets
- [ ] QR preview 不 echo raw payload
- [ ] QR submit routes through supervisor fake
- [ ] stopped supervisor fallback view
- [ ] unauthorized client request rejected

### 5.5 Packaging

- [ ] PyInstaller hidden imports
- [ ] Windows console smoke
- [ ] config reload smoke
- [ ] shutdown smoke
- [ ] 打包檔不含 state/config/log
- [ ] worker shutdown 無 unclosed session warning
- [ ] packaged app 可讀寫 per-account state layout
- [ ] packaged app 不包含測試 fake server artifacts

### 5.6 文件

- [ ] README 修正現有「多帳號」描述
- [ ] 寫 group 實際執行方式
- [ ] 寫 per-account status/partial failure
- [ ] 寫 mixed-provider 限制
- [ ] 寫 migration 注意事項
- [ ] release notes
- [ ] 文件明確區分「多 profile 管理」與「真正多帳號並行監控」
- [ ] 文件列出目前支援的簽到類型：Number/Radar/Manual QR/Teacher QR
- [ ] 文件列出 partial failure 範例
- [ ] 文件列出 bot/client 指令範例
- [ ] 文件列出舊 state/cookie migration 行為

建議 commits：

```text
feat: reconcile account workers after config reload
feat: route bot controls to account supervisor
feat: expose aggregate multi-account runtime status
feat: connect local app to account supervisor
docs: document real multi-account monitoring
```

Phase 5 驗收：

- [ ] config reload 不需重啟整個程式
- [ ] Bot 與 console 使用同一 supervisor
- [ ] Windows release smoke 通過
- [ ] README 與實際行為一致
- [ ] release checklist 通過

## 最終 Definition of Done

- [ ] `now:class A` 會啟動群組所有有效帳號
- [ ] 每帳號有獨立 session、cookie、runtime state
- [ ] Number 每帳號 independently confirmed
- [ ] Radar 每帳號 independently confirmed
- [ ] Manual QR 每帳號 independently confirmed
- [ ] Teacher QR single-flight 且每帳號 independently confirmed
- [ ] 一帳號失敗不停止其他帳號
- [ ] mixed-provider group 有測試
- [ ] config reload 有測試
- [ ] shutdown 無未關閉 session/task
- [ ] 日誌、通知、狀態不洩漏 secret
- [ ] 原有單帳號流程相容
- [ ] 完整 unittest suite 通過
- [ ] release checklist 通過
- [ ] README 不再把 planned fan-out 描述成已完成
