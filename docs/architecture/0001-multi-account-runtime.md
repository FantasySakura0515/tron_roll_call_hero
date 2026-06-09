# ADR 0001: Multi-account monitoring runtime

- Status: Proposed
- Date: 2026-06-09
- Baseline: `c6ffdc6`

## Context

The current application can store several account profiles, but the monitor is
still a single-account runtime:

- `CONFIG` has one active profile and one active provider.
- `app_main()` creates one student `ClientSession`.
- `monitor_loop()` owns one login state and one poll counter.
- completed rollcalls, progress, console status, teacher state, and retry state
  are module-level globals.
- group number/radar/QR helpers currently produce plans rather than performing
  account-specific submissions.
- changing `now` switches the global profile and clears the current session.

Running multiple monitor tasks against these globals would allow one account to
change another account's provider, credentials, completion cache, or login
status. Adding concurrency before removing those ownership ambiguities is not
acceptable.

## Decision

Refactor the runtime around explicit per-account objects, then run one async
worker per selected account under a single supervisor.

Do not use profile switching inside concurrent monitor paths. Do not use one OS
process per account as the primary architecture. Process isolation would hide
the global-state problem while duplicating coordination, logging, Bot, and
packaging behavior.

## Target architecture

```text
ApplicationRuntime
  |
  +-- AccountSupervisor
  |     |
  |     +-- AccountWorker[S1] -> ClientSession[S1] -> Monitor -> Executor
  |     +-- AccountWorker[S2] -> ClientSession[S2] -> Monitor -> Executor
  |     +-- AccountWorker[S3] -> ClientSession[S3] -> Monitor -> Executor
  |
  +-- TeacherQrCoordinator (shared, single-flight)
  +-- RollcallArtifactCoordinator (shared number/QR artifacts)
  +-- RuntimeStateRepository
  +-- NotificationBus
  +-- ShutdownController
```

### ApplicationRuntime

Owns process-wide services only:

- immutable normalized application configuration
- account supervisor
- repositories
- notification bus
- teacher QR coordinator
- shared shutdown event

It must not expose a mutable "current account".

### AccountSpec

Immutable configuration for one account:

```python
@dataclass(frozen=True)
class AccountSpec:
    profile: str
    user: str
    provider_key: str
    credential_ref: CredentialRef
    schedule: ScheduleSpec
```

Passwords remain behind `CredentialResolver`; they are not copied into status
models, logs, execution plans, or persisted runtime snapshots.

### AccountRuntimeState

Mutable state owned by exactly one worker:

```python
@dataclass
class AccountRuntimeState:
    login_result: LoginResult
    monitor_phase: str
    poll_count: int
    completed_number: dict[str, str]
    completed_radar: set[str]
    completed_qr: set[str]
    unsupported_rollcall: UnsupportedRollcallState
    last_progress: dict[str, Any]
    retry_state: LoginRetryState
```

This replaces account-sensitive globals such as:

- `LAST_LOGIN_RESULT`
- `IS_LOGGING_IN`
- `cnt`
- `COMPLETED_NUMBER_ROLLCALLS`
- `COMPLETED_RADAR_ROLLCALLS`
- `COMPLETED_QR_ROLLCALLS`
- `UNSUPPORTED_ROLLCALL_STATE`
- `LAST_ROLLCALL_PROGRESS`
- the account-specific portion of `MONITOR_STATUS`

### AccountContext

Passed explicitly through login, polling, progress, execution, logging, and
notification paths:

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

Functions that perform account-sensitive work receive `AccountContext` instead
of reading `ctx.CONFIG` or `get_active_profile(ctx.CONFIG)`.

### AccountWorker

Each worker owns:

- one `aiohttp.ClientSession`
- one cookie jar
- one `AccountRuntimeState`
- login/retry lifecycle
- schedule evaluation
- rollcall polling
- account-specific submission and confirmation

A worker failure is recorded for that profile and restarted with backoff without
stopping healthy workers.

### AccountSupervisor

The supervisor resolves `now` into desired accounts:

- `now:<user>` starts one worker.
- blank `now` with exactly one valid account starts one worker.
- `now:class A` starts every valid account in group A concurrently.

It starts, stops, and reconciles workers. Configuration reload computes a diff:

- unchanged account: keep worker and session
- changed credentials/provider: restart that worker
- removed account: stop that worker
- added account: start a new worker

Groups may contain accounts from different providers. Provider ownership belongs
to `AccountSpec`, not to a process-wide `provider.current`. The existing group
`school` field becomes optional validation metadata and should eventually be
deprecated.

## Rollcall execution

### Number

Every account submits and verifies independently.

Directly discovered number codes may be shared through
`RollcallArtifactCoordinator`, keyed by `(provider_key, rollcall_id)`. Brute
force must be single-flight so multiple accounts do not multiply request load.
After a code is found, each account performs its own answer request and
confirmation.

### Radar

Every account submits and verifies independently. A successful result for one
account never marks another account complete.

Reusable geometric observations may be shared later, but this is not required
for the first multi-account release.

### QR

Manual QR payloads may fan out to all matching account workers.

Teacher-assisted QR uses one `TeacherQrCoordinator` per teacher identity and
provider:

1. First worker requesting `(provider, student_rollcall_id)` starts a
   single-flight preparation.
2. The coordinator owns the teacher session and teacher rollcall lifecycle.
3. It publishes rotating QR data in memory to waiting account workers.
4. Each account submits and confirms with its own student session.
5. The teacher rollcall is stopped when the source rollcall ends or all
   interested workers complete.

Raw QR data must never enter logs or persistent runtime state.

## Persistence ownership

Avoid a shared read-modify-write JSON file for concurrent workers.

Target layout:

```text
state/
  accounts/
    S1/
      runtime.json
      cookies.json
    S2/
      runtime.json
      cookies.json
  shared/
    pending_qr.json
```

- Account workers write only their own directory.
- Writes use temp file plus `os.replace`.
- Shared repositories serialize mutations with an `asyncio.Lock`.
- Readers aggregate per-account snapshots for status, dashboard, and Bot.
- Existing `state/cookies/<profile>.json` and `account_runtime.json` receive a
  one-time backward-compatible migration.

## Logging and notifications

Every event carries `profile` and `provider`.

Console output uses a supervisor summary instead of multiple workers rewriting
one global status line. Detailed account events remain append-only.

Notification deduplication keys include profile unless the event is explicitly
group-level. One account's success must not suppress another account's result.

## Compatibility

The public CLI remains compatible:

```text
python -m troTHU.tron
python -m troTHU.tron run --no-input
```

Single-account behavior remains the first migration target and must continue to
pass existing tests before concurrency is enabled.

`submit_group_number`, `submit_group_radar`, and `submit_group_qr` will be
removed or replaced by supervisor/executor APIs. A group operation must report
per-account submitted, confirmed, failed, or skipped results; `planned` is not a
successful execution status.

## Migration plan

### Phase 0: Baseline

- Initialize Git and commit the existing project.
- Confirm the full test suite passes.
- Record current behavior and architecture.

### Phase 1: Domain and repository boundaries

- Add `AccountSpec`, `AccountRuntimeState`, `AccountContext`, and repository
  interfaces.
- Build account specs from current simple config.
- Add per-account state persistence.
- Keep the old single-account monitor as the caller.

Exit condition: no user-visible behavior change; all existing tests pass.

### Phase 2: Single-account dependency injection

- Refactor auth, provider selection, polling, progress, number, radar, QR,
  logging, and notifications to consume `AccountContext`.
- Remove account-sensitive reads from global `CONFIG`.
- Keep compatibility wrappers for CLI and existing tests.

Exit condition: one account runs entirely through `AccountWorker`.

### Phase 3: Supervisor and concurrent number/radar

- Add `AccountSupervisor`.
- Start one worker per resolved group member.
- Add isolated restart/backoff and aggregate status.
- Implement real number and radar execution per account.

Exit condition: two fake accounts concurrently detect, submit, and confirm the
same number and radar rollcalls with isolated sessions and state.

### Phase 4: QR coordination

- Add manual QR fan-out through active workers.
- Add `TeacherQrCoordinator` single-flight lifecycle.
- Add per-account QR confirmation and partial-failure reporting.

Exit condition: one teacher QR preparation serves two student accounts, both
confirm independently, and raw QR data is absent from persisted/logged output.

### Phase 5: Reload, Bot, packaging, and documentation

- Reconcile workers after config reload.
- Route Bot commands to live workers.
- Update dashboard/status output.
- Verify PyInstaller spawn/runtime behavior on Windows.
- Update README claims and usage examples.

## Test strategy

Extend the fake TronClass server to model:

- multiple credential pairs
- distinct session cookies per account
- per-account attendance state
- shared rollcall definitions
- request records containing the authenticated account

Required tests:

- two accounts log in with different cookies
- one failed login does not stop another worker
- both accounts answer and confirm number rollcall
- both accounts answer and confirm radar rollcall
- completed state is isolated by profile
- session expiry relogs only the affected account
- teacher QR preparation is single-flight
- QR partial failure reports the failed profile
- mixed-provider group builds correct endpoints
- state writes cannot erase another account's snapshot
- config reload adds/removes/restarts only affected workers
- graceful shutdown closes every session and teacher rollcall

## Acceptance criteria

Multi-account support is considered complete only when:

1. A group starts concurrent workers for every valid member.
2. Each worker owns an independent student session and cookie cache.
3. Number, radar, and QR produce per-account confirmed results.
4. One account's login, retry, completion, or error state cannot alter another.
5. Teacher-assisted QR creates at most one teacher-side rollcall per source
   rollcall/provider.
6. Status, logs, and notifications identify the account without leaking
   credentials or QR data.
7. Existing single-account behavior remains compatible.
8. End-to-end fake-server tests cover at least two accounts concurrently.

## Consequences

This is a larger change than adding fan-out calls, but it removes the current
global active-account constraint instead of working around it. The staged
migration keeps each commit reviewable and preserves a working single-account
path throughout the refactor.
