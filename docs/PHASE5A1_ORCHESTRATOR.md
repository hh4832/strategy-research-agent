# Phase 5-A1 — Research Orchestrator MVP

`Research Request → Validation → Governance → Approval → Execution Dispatch → Evaluation → Registry / Archive`

Deterministic, fail-closed task lifecycle on top of MVP v1 (`main` `95b6ec5`). **No unattended real-market backtest is
enabled**: `RESEARCH_ONLY_REAL` is defined but DISABLED; only `ARCHIVE_REPLAY` (read-only) and `SYNTHETIC_TEST`
(isolated test orchestrator) execute.

## 1. Shared contract (Phase 5-A2 baseline)

`schemas/RESEARCH_REQUEST_SCHEMA.json` v1.0.0 + `orchestration/request.py`, committed alone as
**`5d088c4de362add925344d8e48b36c2e9d728d96`** (21 contract tests). See `docs/RESEARCH_REQUEST_CONTRACT.md`. Any change
goes through a separate contract PR coordinated with Codex.

## 2. State machine (`orchestration/tasks.py`)

| To | Allowed from | Preconditions |
|---|---|---|
| PROPOSED | — | request hash not seen before (same request ⇒ same task) |
| VALIDATED | PROPOSED | schema + semantics valid |
| PENDING_APPROVAL | VALIDATED | governance PASS (or TEST_MODE_ONLY in a test orchestrator) |
| APPROVED | PENDING_APPROVAL | allow-listed human research lead, https approval_ref, governance re-check PASS |
| QUEUED | APPROVED, FAILED (explicit retry, non-consuming adapters only) | adapter enabled |
| RUNNING | QUEUED | fresh governance re-check, adapter enabled, single claim + lease |
| EVALUATING | RUNNING, EVALUATING (crash resume) | execution reference recorded; lease owner or expired lease |
| EVALUATED | EVALUATING | evaluation archive written + research decision |
| ARCHIVED | EVALUATED | 7-file task archive written (O_EXCL) |
| BLOCKED | PROPOSED … QUEUED | reason |
| FAILED | QUEUED … EVALUATED | reason + diagnostics |
| CANCELLED | PROPOSED … QUEUED | reason |

Every event records from/to, timestamp, actor and actor kind (HUMAN | SYSTEM | TEST_FIXTURE), reason, data and an idempotency key.

Concurrency and safety guarantees:
- The log is the hash-chained, append-only `registry.chain.ChainedLog` (fcntl lock; `append_if` compare-and-append).
- Illegal transitions, stale expected-state, held leases and missing reasons all raise `IllegalTransition`.
- A repeated idempotency key returns the original event; nothing is appended twice.
- Approval cannot be skipped: RUNNING is reachable only from QUEUED, which is reachable only from APPROVED.

## 3. Governance (`orchestration/governance.py`)

Governance is read-only and reuses the existing modules. It never approves, consumes, revokes or edits anything.

| Check | Source |
|---|---|
| G1 request schema | `RESEARCH_REQUEST_SCHEMA.json` |
| G2 strategy spec | file exists, sha256 = request, `spec.validate` no ERROR, id/version match |
| G3 dataset | full snapshot id + manifest sha (scope via RA04) |
| G4 Core + costs | `engine_benchmark_status` (run in a **subprocess**, so the orchestrator process never loads an execution module) = BENCHMARK_PASS; `research_only.costs.CostSet` loads (CO01–CO06), sha matches |
| G5 authorization | `ResearchAuthorizationBook.get` (approved, not revoked); `PENDING` ⇒ BLOCKED |
| G6 scope | `ResearchAuthorizationGuard.check` RA03–RA08, RA10, RA12 (+RA02 for real runs); `consume()` is never called |
| G7 budget | real: slot free; replay: exactly one use, made by the replayed run; replay consumes 0 |
| G8 holdout | lock id matches the authorization, RA09, access `NO_ACCESS` |
| G9 formal gate | recorded; `trading_permission` is always false; NO_GO is never read as a trading permission |
| G10 execution mode | `ARCHIVE_REPLAY` enabled; `RESEARCH_ONLY_REAL` DISABLED |

Checks run three times: at submit, at approval and immediately before RUNNING.

## 4. Execution adapters (`orchestration/adapters.py`)

- **ArchiveReplayAdapter.** Uses `phase4c.read_archive` with the request's independently pinned `MANIFEST.json` sha, checks every file hash, and checks that the identity (snapshot, dataset manifest, Core, authorization, strategy, spec sha) matches the request. Reads only: `strategy_executed=false`, `consumed_batches=0`.
- **SyntheticExecutionAdapter.** Accepted only by a test orchestrator (state and archive outside the repository; approver `TEST_FIXTURE`; outputs under `test_tasks/`, labelled SYNTHETIC). A production orchestrator BLOCKS `SYNTHETIC_TEST` requests and refuses fixtures.
- **ResearchOnlyRunnerAdapter.** Interface only. `enabled=False`, `consumes_budget=True`; `execute` raises `AdapterDisabled` without importing the runner.

## 5. Evaluation

Evaluation calls `phase4c.accept` and `write_acceptance`, i.e. the Phase 4-C acceptance over the Phase 4-B evaluator. There is no new PnL, metric or verdict logic. If acceptance is not PASS (missing or inconsistent evidence), the decision is engineering BLOCKED or the evaluator's own status, evidence UNDETERMINED, and never KEEP.

## 6. Persistence, recovery, archive

- **State and log location.** `--state-dir/task_events.jsonl` plus `executions/<task>/attempt_<n>/` (synthetic only). The existing registry is not touched, and the orchestrator writes nothing inside the repository.
- **Duplicate submit.** Returns the existing task.
- **Crash during RUNNING.** After the lease expires, `recover` moves the task to FAILED; it is never re-executed. An explicit `retry` is allowed only for non-consuming adapters.
- **Crash during EVALUATING.** Evaluation is resumed, since it is read-only and consumes nothing.
- **EVALUATED after restart.** The task is archived.
- **Archive-name collision.** The task stays EVALUATED and is archived on the next recovery; an existing directory is never reused.
- **Task archive.** `<archive-root>/tasks/YYYYMMDD_HHMMSS_<git7>/` (Asia/Taipei) contains:
  - `research_request.json`
  - `task_events.jsonl`
  - `governance_decision.json`
  - `execution_reference.json`
  - `evaluation_reference.json`
  - `research_decision.json`
  - `run_info.txt`
- **Evaluation archive.** `<archive-root>/evaluations/<YYYYMMDD_HHMMSS>_<git7>_<task8>_a<n>/`.
- **Drive.** For real use, `--archive-root` is `MyDrive/Quant_Research/strategy-research-agent/orchestrator`. This phase verified the output contract locally only; no cloud archive was produced.

## 7. CLI

`python -m strategy_agent.orchestration` provides these commands:

| Command | Purpose |
|---|---|
| `validate` | validate a request |
| `submit` | submit a request |
| `approve` | human research lead approval |
| `status` | show a task's state and history |
| `replay` | run an `ARCHIVE_REPLAY` task only |
| `recover` | recover tasks after a crash |
| `cancel` | cancel a task |

There is no command or flag that skips governance, approves on the orchestrator's behalf, consumes a batch or starts a real run.

## 8. Tests

| File | Tests |
|---|---|
| `test_orchestrator_request_contract.py` | 21 |
| `test_orchestrator_lifecycle.py` | 20: brief items 1–18, plus concurrent workers (8 threads, 1 claim) and the CLI |

Full suite:

| Environment | Result |
|---|---|
| No Core | 403 passed, 54 skipped |
| Core 699e3ad | 497 passed, 3 skipped |
| Core b16179a | known Phase 3 `test_15` SHA-pin failure only |

## 9. Not done / out of scope

- Real-archive replay of `20261107_081300_9439f8c` through the orchestrator on Drive has not been executed. Governance for that request was verified locally: G1–G10 PASS.
- `RESEARCH_ONLY_REAL` stays disabled. Enabling it needs a separate user authorization and an independent acceptance.
- Phase 5-C was not started.
- Approver identity is asserted by the CLI caller, the same model as the existing `*_admin` tools. There is no cryptographic identity.
