# 多帳號 Runtime 框架設計

- 狀態：Draft
- 日期：2026-06-09
- 依據：[ADR 0001](0001-multi-account-runtime.md)
- 實作起點：`46a53db`

## 目標

本文件把 ADR 的方向落成可實作的模組框架。這一階段只定義：

- 模組責任
- 物件生命週期
- 依賴方向
- 核心介面
- 狀態所有權
- 舊程式遷移位置

這一階段不新增並行監控，也不改變目前單帳號行為。

## 設計原則

1. 帳號敏感狀態必須由單一 `AccountWorker` 擁有。
2. 核心流程不得讀取或切換全域 active profile。
3. 每個帳號必須有獨立 session、cookie jar、完成紀錄和重試狀態。
4. 共享服務只能保存真正跨帳號的資料。
5. 先讓單帳號走新框架，再啟用多帳號並行。
6. 舊 CLI 與現有測試透過 compatibility wrapper 漸進遷移。
7. 新核心模組不得 import `tron_roll_call_hero.runtime_context`。
8. 密碼、cookie、QR data 不進入 dataclass repr、log 或 runtime snapshot。

## 模組配置

維持目前專案的平鋪模組結構，先新增下列檔案：

```text
tron_roll_call_hero/
  account_models.py
  account_registry.py
  account_context.py
  account_worker.py
  account_supervisor.py
  runtime_services.py
  account_state_repository.py
  rollcall_artifact_coordinator.py
  teacher_qr_coordinator.py
  application_runtime.py
```

暫時不建立 `tron_roll_call_hero/runtime/` 子套件。現有程式大量透過
`runtime_context` lazy export；先平鋪可降低一次性 import 變動。

## 依賴方向

```text
cli_main / monitor_runtime
          |
          v
application_runtime
          |
          v
account_supervisor
          |
          v
account_worker
          |
          +--> auth_runtime
          +--> rollcall_runtime
          +--> number_runtime
          +--> radar_runtime
          +--> qr_runtime
          +--> rollcall_progress
          |
          v
tron_http / pure rollcall modules

shared services:
account_state_repository
rollcall_artifact_coordinator
teacher_qr_coordinator
notification_bus
```

禁止方向：

```text
account_models -> runtime_context
account_context -> runtime_context
account_worker -> switch_profile()
worker A -> worker B mutable state
rollcall executor -> global active provider
```

## 核心模型

### AccountSpec

不可變、無密碼的帳號執行規格。

```python
@dataclass(frozen=True)
class AccountSpec:
    profile: str
    user: str
    provider_key: str
    schedule: ScheduleSpec
    credential_ref: CredentialRef
    enabled: bool = True
```

欄位規則：

- `profile`：本機唯一識別，沿用目前 profile name。
- `user`：學校帳號，可用於登入與本人簽到狀態比對。
- `provider_key`：`thu`、`tku`、`fju`、`tronclass`。
- `credential_ref`：只描述來源，不包含密碼值。
- `schedule`：第一版沿用全域 operating，未來可支援帳號覆寫。

### CredentialRef

```python
class CredentialSource(str, Enum):
    CONFIG = "config"
    KEYRING = "keyring"
    ENVIRONMENT = "environment"
    MANUAL_COOKIE = "manual_cookie"


@dataclass(frozen=True)
class CredentialRef:
    source: CredentialSource
    profile: str
    user: str
```

真正密碼只在登入前由 `CredentialResolver.resolve()` 暫時取得。

### AccountRuntimeState

```python
@dataclass
class AccountRuntimeState:
    login_result: LoginResult
    phase: MonitorPhase
    poll_count: int = 0
    login_in_progress: bool = False
    completed_number: dict[str, str] = field(default_factory=dict)
    completed_radar: set[str] = field(default_factory=set)
    completed_qr: set[str] = field(default_factory=set)
    unsupported_rollcall: UnsupportedRollcallState = field(
        default_factory=UnsupportedRollcallState
    )
    last_progress: dict[str, Any] = field(default_factory=dict)
    retry: LoginRetryState = field(default_factory=LoginRetryState)
    last_error: RuntimeErrorRecord | None = None
```

這個物件不直接負責寫檔。worker 更新記憶體狀態後，由 repository 儲存安全快照。

### AccountContext

帳號相關函式唯一允許取得執行環境的入口。

```python
@dataclass
class AccountContext:
    spec: AccountSpec
    config: AccountConfig
    endpoints: TronHttpEndpoints
    session: aiohttp.ClientSession
    state: AccountRuntimeState
    services: RuntimeServices
```

`AccountConfig` 是從 normalized config 建出的帳號唯讀 view，不共享 mutable
dict：

```python
@dataclass(frozen=True)
class AccountConfig:
    http: HttpConfig
    monitor: MonitorConfig
    number: NumberConfig
    radar: RadarConfig
    notifications: NotificationConfig
    timezone: str
```

### RuntimeServices

```python
@dataclass(frozen=True)
class RuntimeServices:
    credentials: CredentialResolver
    cookies: CookieRepository
    states: AccountStateRepository
    events: RuntimeEventSink
    notifications: NotificationBus
    artifacts: RollcallArtifactCoordinator
    teacher_qr: TeacherQrCoordinator
    clock: Clock
```

服務經由 constructor 注入，測試可替換成 memory fake。

## AccountRegistry

`account_registry.py` 負責把設定解析為執行規格，不啟動任何網路操作。

建議介面：

```python
class AccountRegistry:
    def list_specs(self) -> tuple[AccountSpec, ...]: ...
    def resolve_target(self, now: str) -> TargetResolution: ...
    def desired_specs(self, now: str) -> tuple[AccountSpec, ...]: ...
```

`TargetResolution` 必須包含：

```python
@dataclass(frozen=True)
class TargetResolution:
    kind: Literal["account", "group", "empty", "invalid"]
    requested: str
    profiles: tuple[str, ...]
    skipped: tuple[SkippedAccount, ...]
    warnings: tuple[str, ...]
```

規則：

- 單一帳號：回傳一個 `AccountSpec`。
- 空白且只有一個有效帳號：推斷該帳號。
- 群組：回傳全部有效成員。
- 同群組可有不同 provider。
- 缺 user、credential 或未知 provider 的成員進入 `skipped`。
- 不再使用「第一個群組帳號就是 monitor account」的概念。

## AccountWorker

### 責任

- 建立並關閉該帳號的 `ClientSession`
- 載入該帳號 cookie
- 管理登入與重試
- 判斷該帳號排程
- 輪詢點名
- 執行並確認該帳號的簽到
- 發出帶 profile/provider 的事件
- 儲存該帳號 runtime snapshot

### 不負責

- 切換全域設定
- 控制其他 worker
- 建立第二個教師 session
- 決定 group 成員
- 聚合 console dashboard

### 介面

```python
class AccountWorker:
    def __init__(
        self,
        spec: AccountSpec,
        config: AccountConfig,
        services: RuntimeServices,
        shutdown: asyncio.Event,
    ) -> None: ...

    async def run(self) -> None: ...
    async def stop(self) -> None: ...
    async def force_check(self) -> RollcallCheckResult: ...
    async def submit_qr(self, payload: QrPayload) -> SubmissionResult: ...
    def snapshot(self) -> AccountWorkerSnapshot: ...
```

### 狀態機

```text
CREATED
  -> RESTORING_SESSION
  -> AUTHENTICATING
  -> STANDBY
  -> MONITORING
  -> EXECUTING
  -> MONITORING

AUTHENTICATING -> RETRY_WAIT
RETRY_WAIT -> AUTHENTICATING
any state -> STOPPING -> STOPPED
fatal worker error -> FAILED -> supervisor restart policy
```

`FAILED` 只代表該 worker，不能直接設定全域 shutdown。

## AccountSupervisor

### 責任

- 根據 `TargetResolution` 建立 desired worker set
- 啟動、停止與監看 worker task
- 管理 worker restart/backoff
- 聚合狀態
- 執行 config reload reconciliation
- 將 Bot/CLI 指令路由到指定 worker

### 介面

```python
class AccountSupervisor:
    async def start(self, specs: Sequence[AccountSpec]) -> None: ...
    async def shutdown(self) -> None: ...
    async def reconcile(self, specs: Sequence[AccountSpec]) -> ReconcileResult: ...
    async def force_check(self, profile: str) -> RollcallCheckResult: ...
    async def submit_qr(
        self,
        profiles: Sequence[str],
        payload: QrPayload,
    ) -> GroupSubmissionResult: ...
    def snapshot(self) -> SupervisorSnapshot: ...
```

### Reconcile 規則

以不含密碼值的 `AccountSpec` fingerprint 判斷：

- 新增：建立 worker。
- 移除：graceful stop。
- provider/user/credential ref 改變：restart。
- schedule 或 monitor config 改變：更新或 restart，第一版可保守 restart。
- 無改變：保留 session 與 task。

## Rollcall 執行框架

### 統一結果模型

```python
class SubmissionStatus(str, Enum):
    CONFIRMED = "confirmed"
    SUBMITTED_UNCONFIRMED = "submitted_unconfirmed"
    SKIPPED_ALREADY_COMPLETE = "skipped_already_complete"
    SKIPPED_NOT_APPLICABLE = "skipped_not_applicable"
    LOGIN_FAILED = "login_failed"
    FAILED = "failed"


@dataclass(frozen=True)
class SubmissionResult:
    profile: str
    provider_key: str
    rollcall_id: str
    attendance_type: AttendanceType
    status: SubmissionStatus
    error_code: str = ""
```

群組結果必須由真實帳號結果組成：

```python
@dataclass(frozen=True)
class GroupSubmissionResult:
    rollcall_id: str
    results: tuple[SubmissionResult, ...]
```

禁止再把 `planned` 當成成功。

### Number executor

拆成兩個責任：

```python
class NumberCodeResolver:
    async def resolve(
        self,
        account: AccountContext,
        rollcall_id: str,
    ) -> NumberCodeResult: ...


class NumberSubmissionExecutor:
    async def submit(
        self,
        account: AccountContext,
        rollcall_id: str,
        code: str,
    ) -> SubmissionResult: ...
```

`RollcallArtifactCoordinator` 提供：

```python
async def get_or_resolve_number_code(
    key: RollcallKey,
    resolver: Callable[[], Awaitable[NumberCodeResult]],
) -> NumberCodeResult
```

同一 `(provider, rollcall_id)` 只能有一個暴力讀碼工作；每個帳號仍各自 submit。

### Radar executor

```python
class RadarSubmissionExecutor:
    async def submit(
        self,
        account: AccountContext,
        rollcall: Mapping[str, Any],
    ) -> SubmissionResult: ...
```

第一版不共享 radar 完成狀態。空答案、global solver、legacy fallback 都在帳號 context
內執行。

### QR executor

```python
class QrSubmissionExecutor:
    async def submit_payload(
        self,
        account: AccountContext,
        payload: QrPayload,
    ) -> SubmissionResult: ...

    async def submit_data(
        self,
        account: AccountContext,
        rollcall_id: str,
        data: SecretQrData,
    ) -> SubmissionResult: ...
```

`SecretQrData` 必須：

- `repr=False`
- 不可序列化到 snapshot
- 不可進入 event `extra`
- 使用完成後移除 coordinator cache

## TeacherQrCoordinator

這是 process-wide service，不屬於任何學生 worker。

### Key

```python
TeacherAssistKey = tuple[teacher_provider, teacher_identity, student_rollcall_id]
```

### 介面

```python
class TeacherQrCoordinator:
    async def acquire(
        self,
        request: TeacherQrRequest,
    ) -> TeacherQrLease: ...

    async def release(
        self,
        lease: TeacherQrLease,
        profile: str,
    ) -> None: ...

    async def shutdown(self) -> None: ...
```

### Single-flight

- 同 key 的第一個請求建立 teacher task。
- 後續 worker await 同一 task。
- coordinator 持續更新記憶體中的 QR data。
- 每個 worker 使用自己的 student session submit。
- 所有 interested profiles 完成、來源點名結束或 shutdown 時停止 teacher rollcall。
- coordinator 失敗只影響 QR，不停止 Number/Radar worker。

## Persistence

### AccountStateRepository

```python
class AccountStateRepository(Protocol):
    async def load(self, profile: str) -> AccountStateSnapshot: ...
    async def save(self, snapshot: AccountStateSnapshot) -> None: ...
    async def list(self) -> tuple[AccountStateSnapshot, ...]: ...
```

檔案實作：

```text
state/accounts/<normalized-profile>/runtime.json
state/accounts/<normalized-profile>/cookies.json
```

原子性：

1. 寫入同目錄 temporary file。
2. flush。
3. `os.replace`。

不同 worker 不寫同一檔案，因此不需要全域 account runtime lock。

### SharedStateRepository

只存不含秘密的共享資料，例如 pending QR metadata。所有 mutation 經過 process-local
`asyncio.Lock`。

## Event 與觀測

統一 event envelope：

```python
@dataclass(frozen=True)
class RuntimeEvent:
    event: str
    profile: str
    provider_key: str
    status: str
    message: str
    rollcall_id: str = ""
    attendance_type: str = ""
    data: Mapping[str, SafeValue] = field(default_factory=dict)
```

規則：

- account event 的 `profile` 不可為空。
- group event 使用 `profile="group:<name>"`。
- notification dedupe key 包含 profile。
- console supervisor view 讀 snapshot，不讀 worker mutable state。

## ApplicationRuntime

`application_runtime.py` 是唯一組裝入口：

```python
class ApplicationRuntime:
    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        base_dir: Path,
    ) -> "ApplicationRuntime": ...

    async def run(self) -> None: ...
    async def reload(self, config: Mapping[str, Any]) -> ReconcileResult: ...
    async def shutdown(self) -> None: ...
```

組裝順序：

1. normalize config
2. 建立 `AccountRegistry`
3. resolve `now`
4. 建立 repositories 與 shared services
5. 建立 supervisor
6. supervisor start desired specs
7. 啟動 config watcher / console renderer
8. 等待 shutdown

## 舊模組遷移對照

| 現有位置 | 目標責任 |
| --- | --- |
| `simple_config.py` | 保留文字格式；輸出交給 `AccountRegistry` |
| `group_runtime.py` | 只保留 target resolution compatibility；移除假 fan-out |
| `account_store.py` | profile CRUD、credential/keyring、cookie migration |
| `account_runtime_store.py` | 改成 `AccountStateRepository` adapter |
| `config_runtime.py` | 建立 immutable `AccountConfig` |
| `status_reports.py` | 從 supervisor/repository 聚合，不讀 active profile |
| `auth_runtime.py` | `login(account: AccountContext)` |
| `monitor_runtime.py` | worker loop 與 legacy CLI adapter 分離 |
| `rollcall_runtime.py` | account-scoped decision/execution orchestration |
| `rollcall_progress.py` | progress 寫入 `account.state` |
| `number_runtime.py` | resolver 與 account submit 分離 |
| `radar_runtime.py` | 接受 `AccountContext` |
| `qr_runtime.py` | manual payload 與 account submit 分離 |
| `qr_teacher_runtime.py` | 移入 `TeacherQrCoordinator` |
| `logging_runtime.py` | 接受 `RuntimeEvent` |
| `bot_handlers.py` | 指令路由到 supervisor |
| `runtime_context.py` | 最後只留 compatibility facade |

## Compatibility 策略

遷移期間保留舊函式簽名，但 wrapper 只能用在單帳號：

```python
async def login(session, *, research_context=False):
    account = legacy_single_account_context(session)
    return await login_account(account, research_context=research_context)
```

規則：

- wrapper 必須標記 `legacy`.
- supervisor worker 不可呼叫 legacy wrapper。
- Phase 2 完成後以 `rg` 檢查新模組不存在 `ctx.CONFIG`、`switch_profile`、
  `get_active_profile(ctx.CONFIG)`。

## 測試分層

### Unit

- model validation
- target resolution
- worker state transitions
- repository path/atomic write
- supervisor reconcile diff
- coordinator single-flight

### Component

- worker + fake HTTP client
- supervisor + fake workers
- teacher coordinator + fake teacher client
- event sink + secret redaction

### End-to-end

- enhanced `FakeTronServer`
- two real `aiohttp.ClientSession`
- two account workers
- shared rollcall
- per-account confirmation

## 第一個實作切片

第一個 code commit 只新增：

- `account_models.py`
- `account_registry.py`
- `account_state_repository.py`
- 對應 unit tests

不接入 `monitor_runtime`，不修改登入與簽到流程。

完成條件：

- 可從目前 config 產生一個或多個 `AccountSpec`
- 不暴露密碼
- 可讀寫 per-account runtime snapshot
- 現有 574 項測試維持通過
- 新增測試全部通過

