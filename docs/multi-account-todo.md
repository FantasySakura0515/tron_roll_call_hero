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

- [ ] manual payload submit 接受 account context
- [ ] `finalize_qr_submission` 寫 account state
- [ ] pending QR 以 profile/provider 記錄
- [ ] clipboard 路徑只作用於指定 account
- [ ] completed QR 寫 account state
- [ ] teacher assist 暫時維持 single-account adapter

測試：

- [ ] manual payload account A/B 各自提交
- [ ] pending registry account isolation
- [ ] completed QR isolation
- [ ] raw payload 不進 snapshot/log

建議 commit：

```text
refactor: scope student QR execution to account context
```

### 2.8 AccountWorker 單帳號接線

- [ ] 新增 `troTHU/account_worker.py`
- [ ] worker 建立/關閉 session
- [ ] worker login/retry state machine
- [ ] worker schedule loop
- [ ] worker poll/execute loop
- [ ] worker runtime heartbeat
- [ ] worker graceful stop
- [ ] `app_main()` 單帳號改由 worker 執行
- [ ] legacy console input/edit 行為保持

測試：

- [ ] worker lifecycle
- [ ] retry backoff
- [ ] shutdown closes session
- [ ] 單帳號 fake server E2E
- [ ] 現有 monitor tests 維持通過

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

- [ ] 新增 `troTHU/account_supervisor.py`
- [ ] worker task registry
- [ ] start all desired specs
- [ ] isolated stop
- [ ] isolated failure/restart
- [ ] exponential backoff
- [ ] aggregate snapshot
- [ ] graceful global shutdown

測試：

- [ ] 啟動兩 worker
- [ ] 一個 worker crash，另一個持續
- [ ] restart backoff
- [ ] shutdown 全部完成

建議 commit：

```text
feat: supervise independent account workers
```

### 3.2 Fake server 多帳號模型

- [ ] fake server 支援 credential map
- [ ] 每帳號不同 session cookie
- [ ] request 可識別 authenticated account
- [ ] 每帳號 student rollcall status
- [ ] shared rollcall definition
- [ ] 可指定某帳號 login/session/submit failure

測試：

- [ ] 不同帳號 cookie 不可互換
- [ ] server request record 有 account
- [ ] 狀態更新只影響該帳號

建議 commit：

```text
test: model multiple authenticated students in fake server
```

### 3.3 Number artifact coordinator

- [ ] 新增 `troTHU/rollcall_artifact_coordinator.py`
- [ ] key 為 provider + rollcall ID
- [ ] direct code cache
- [ ] brute-force single-flight
- [ ] result TTL
- [ ] error 不永久 cache
- [ ] shutdown cancellation

測試：

- [ ] 兩 worker 同時請求只 resolve 一次
- [ ] 每帳號 submit 一次
- [ ] resolver failure 可重試
- [ ] 不同 provider 不共享

建議 commit：

```text
feat: coordinate shared number code discovery
```

### 3.4 開啟 group monitor

- [ ] `application_runtime.py` 組裝 supervisor
- [ ] `now:class A` 啟動全部 worker
- [ ] mixed-provider group
- [ ] per-account startup report
- [ ] partial login failure report
- [ ] group Number 真實提交
- [ ] group Radar 真實提交
- [ ] 移除/棄用 `planned` fan-out 成功訊息

E2E：

- [ ] 兩帳號同時監測 Number
- [ ] 兩帳號各自 confirmed
- [ ] 兩帳號同時監測 Radar
- [ ] 兩帳號各自 confirmed
- [ ] 一帳號 login fail，另一帳號成功
- [ ] 一帳號 session expired，只重登該帳號

建議 commit：

```text
feat: monitor and answer number and radar for account groups
```

Phase 3 驗收：

- [ ] 真正多帳號 Number/Radar 可運作
- [ ] 每帳號 session/state/cookie 隔離
- [ ] partial failure 不停止全組
- [ ] 完整測試通過

## Phase 4：QR Coordinator

### 4.1 Manual QR fan-out

- [ ] QR payload 解析一次
- [ ] 路由到 matching active workers
- [ ] 每 worker 自己 submit/verify
- [ ] 回傳 `GroupSubmissionResult`
- [ ] partial failure 顯示 profile
- [ ] 未啟動 worker 可選擇 temporary account execution

測試：

- [ ] 兩帳號 submitted/confirmed
- [ ] 一成功一失敗
- [ ] provider mismatch
- [ ] raw payload 不進 result/log

建議 commit：

```text
feat: submit manual QR through account workers
```

### 4.2 Teacher QR coordinator

- [ ] 新增 `troTHU/teacher_qr_coordinator.py`
- [ ] teacher session ownership
- [ ] teacher login retry
- [ ] course resolution
- [ ] prepare single-flight
- [ ] rotating data memory distribution
- [ ] interested profile tracking
- [ ] stop teacher rollcall lifecycle
- [ ] shutdown cleanup
- [ ] coordinator failure 不影響 Number/Radar

測試：

- [ ] 兩 student 只 create 一次 teacher rollcall
- [ ] 兩 student 使用各自 session submit
- [ ] QR data rotation
- [ ] all complete 後 stop
- [ ] source rollcall close 後 stop
- [ ] teacher login fail
- [ ] raw data 不落地

建議 commit：

```text
feat: coordinate teacher-assisted QR across accounts
```

Phase 4 驗收：

- [ ] Number/Radar/Manual QR/Teacher QR 都有 per-account result
- [ ] teacher-side operation single-flight
- [ ] QR secret 不持久化
- [ ] 完整測試通過

## Phase 5：Reload、Bot、狀態與發佈

### 5.1 Config reload

- [ ] watcher 讀新 config
- [ ] registry 建新 desired specs
- [ ] supervisor reconcile
- [ ] add/remove/restart/keep report
- [ ] 修改單一帳號只重啟該 worker
- [ ] 無效 config 保留現有 worker
- [ ] reload report 包含 added/removed/restarted/kept/skipped
- [ ] reload 不清掉未受影響帳號 session/cookie
- [ ] reload 失敗事件帶 profile/provider 或 group identity

測試：

- [ ] add account 只啟動新增 worker
- [ ] remove account 只停止移除 worker
- [ ] change credential/provider 只重啟該 worker
- [ ] invalid config 保留既有 workers
- [ ] group membership change reconciliation

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

- [ ] Discord gateway adapter 接 live supervisor
- [ ] LINE webhook adapter 接 live supervisor
- [ ] 兩平台指令集一致（同一 command mapping 層）
- [ ] LINE tunnel 啟動方式文件化並提供啟動指令
- [ ] `start/stop` 真正控制 worker
- [ ] `force` 路由 live worker
- [ ] `reauth` 只重登指定 account
- [ ] `qr all` 路由 supervisor
- [ ] authorization 規則保持
- [ ] status 顯示真實 worker state
- [ ] `force <profile>` 只觸發指定 worker
- [ ] `force all` 觸發所有 running workers 並回傳 group result
- [ ] `reauth <profile>` 只清該 profile cookie/session
- [ ] `status <profile>` 顯示單帳號狀態
- [ ] `status all` 顯示群組摘要
- [ ] partial failure 回覆包含 profile
- [ ] bot command 不呼叫 `switch_profile`
- [ ] adapter binding 使用 profile name，不使用 user 猜測 profile

測試：

- [ ] regular user 只能控制授權 profile
- [ ] admin 可控制 all profiles
- [ ] force routes to correct worker
- [ ] reauth does not touch other workers
- [ ] qr all returns per-account results
- [ ] bot responses redact secrets
- [ ] Discord adapter E2E（fake supervisor + 完整指令流程）
- [ ] LINE adapter E2E（fake supervisor + 完整指令流程）

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
