# Phase 3 M1 — Core Integration Adapter & Independent Benchmark Acceptance

**M1.2 verdict: `INTEGRATION_PENDING`** (not GO, not NO-GO).

Core is pinned at `market-research-core@298956f78522a9f2d13efbd38645178b1b477691`, the only SHA allowed this round.

- **B01–B12: 12 PASS, 0 FAIL, 0 NOT_EXECUTABLE** on the real `ExecutionEngine.run`, with exact Decimal comparison (`TOL = 0`, CostProfile rounding `NONE`).
- **G16 chronology acceptance: PASS** (6 of 6 checks).
- Engine status `BENCHMARK_PASS` for `298956f` is now derived from the committed evidence record. It needs 12/12 PASS **and** G16 PASS; nothing is hand-set.
- The synthetic Runner → Adapter → Core → BacktestReport → Registry run ends **COMPLETED**, in isolated test governance with the test holdout lock.
- **Why not GO:** the same acceptance found two localizable Core-vs-Oracle-v1 differences outside B01–B12:
  - **G17** — target supersession while an earlier order is still pending.
  - **G14b** — the DAY-expiry boundary is inclusive.
  Both are one-function fixes in Core. Until then, the adapter refuses any run that touches either situation.
- **Why not NO-GO:** no look-ahead and no fill out of time order. Accounting reconciles. Every divergence is detected and fails closed.

Formal market backtests stay fail-closed. TW equities additionally need G03b (tick ladder) and an approved CostProfile.

Record files are in `benchmark/results/mrc_298956f78522/`:
- `crosscheck.json`
- `report.md`
- `contract_gaps.json`

Earlier records for `a50f3902c3a0` (M1.1: 1 PASS / 11 FAIL) and `f4276cb081ae` (M1) are kept unchanged.

## 1. Baseline

| Item | Value |
|---|---|
| Agent | `feature/m1-core-adapter`, PR #3, base `b66e289`. Stacked on PR #2 and PR #1. **No PR merged.** |
| Core (read-only) | `298956f…` (Core PR #2, Core CI 41 passed). Not modified. |
| Oracle / fixtures | `benchmark/oracle`, `benchmark/cases`. **0 lines changed.** |
| Governance | `governance/`, `registry/`, holdout locks: **0 lines changed**. |

## 2. Adapter (`src/strategy_agent/adapters/mrc_adapter.py`)

| Responsibility | Function | Notes |
|---|---|---|
| Decisions → targets | `decisions_to_targets` | Decision time (= `available_at`) passes through unchanged. |
| Spec roll schedule → explicit orders | `expand_roll_schedule` | Uses the *intended* target only; never fills, never stitches prices. |
| Targets → Core orders | `targets_to_orders` | `ExecutionOrder` target-position MARKET orders, TIF `DAY`. Each order uses `signal_time = signal_available_at = order_time` = the decision time (convention v1 §1.2). |
| Pins → Core config | `core_config` | Builds `ExecutionConfig` with Core commit, snapshot, producer SHA, verdict, scope, spec sha, parameters, cost multiplier, seed and window. `same_timestamp_open_assumption=True` is set **only** for START-labelled, CONFIRMED-session data (see C1). |
| Agent CostProfile → Core | `core_cost_profile` | Field mapping only. FLOOR_INTEGER and NONE now map directly (G12/G12b closed). A price-banded tick ladder still raises `CoreContractGap` K03 (G03b). |
| Bars | `check_core_bars` | Checks that Core's execution columns are present. Eligibility, sessions and availability come from Core's own data adapters (`taiwan.py`). |
| `ExecutionResult` → `BacktestReport` | `core_result_to_report` | Values are copied, not recomputed. Provenance is checked: full 40-hex engine SHA equal to the pin, convention, spec sha, snapshot and producer. Core `FORMAL_ELIGIBLE` maps to `RESEARCH_BACKTEST`; anything else maps to `RESEARCH_INTEGRATION_DEMO`. |
| Reasons | `map_reason` | Oracle codes map 1:1. Core-only codes are reviewed and passed through under their own names. Vague codes (`EXECUTION_INELIGIBLE`, `NO_LATER_ELIGIBLE_BAR`) and unknown codes fail closed. |
| Port | `MrcBacktestPort.run` | Validates dataset pins, CostProfile, convention and market semantics, then calls `ExecutionEngine.run`. Production runners reach it only with `BENCHMARK_PASS`. |
| Divergence guard (M1.2) | `known_divergences` / `guard_known_divergences` | **Read-only** check on the finished run. Refuses the report (`CoreContractGap` K17 / K14b) when the run touched a G17 or G14b situation. Changes no fill, price or PnL. 0 hits on B01–B12. |

The adapter never simulates fills, computes PnL or fees, edits eligibility, moves signal times, fills missing bars, stitches futures prices or calls the Oracle. This is enforced by AST import tests and the narrowed M2 isolation test.


## 3. Capability matrix on Core 298956f

The matrix is measured, not assumed:

- **SUPPORTED:** K01–K08, K10, K12 (NONE and FLOOR_INTEGER, Decimal costs), K13, K14 (DAY expiry at the first `session_close > order_time`), K15 (per-bar rejection with `bar_ts`), K16 (fill-time ordering probe).
- **PARTIAL:**
  - K09 — by design, the adapter expands the roll schedule.
  - K11 — `engine_git_sha` comes from `config.core_commit_sha`.

## 4. B01–B12 cross-check

All 12 cases are **PASS**. The comparison covers 12 dimensions:

1. signal time and `available_at`
2. order time
3. fill time
4. fill price
5. side
6. fee and tax (Decimal strings, exact)
7. realized and unrealized PnL
8. per-bar rejections `(bar_ts, reason)`
9. exclusion invalidation
10. open position
11. contract transition
12. provenance

The comparator was checked against negative controls on B01:

| Control | Result |
|---|---|
| HALF_UP rounding | FAILS (2 differences) |
| Core default `same_timestamp_open_assumption=False` | FAILS (7 differences) |

So the PASS result is not produced by a lenient comparator.

## 5. G16 independent acceptance (`probes.G16_acceptance`)

| Check | Result |
|---|---|
| Fills in fill-time order (B01–B12 + scenario `X16`) | PASS |
| FIFO: no `exit_ts < entry_ts` (valid + invalidated round trips) | PASS |
| Equity = initial cash before the first fill; equity − cash₀ = realized when flat | PASS |
| Prefix invariance (bars cut at each fill time give identical earlier fills → no future-dependent fills/PnL) | PASS |
| Unfilled order does not move the target state | PASS |
| Same-timestamp determinism (B09: 2 orders at the same `order_time`, input order shuffled 3× + rerun) | PASS |

Scenario `X16`:

- A BUY is placed at 09:10. The buy side is locked on the 09:10, 09:15 and 09:20 bars.
- A target-0 decision follows at 09:20.
- Core fills the BUY at 09:25. Its state is consistent: chronological, LONG 1000 marked at 60.0, and the target-0 order is a no-op.

## 6. Remaining contract gaps (`contract_gaps.json`)

| Gap | Status | Blocks | Responsible repo / location | Minimal fix |
|---|---|---|---|---|
| G12, G12b, G14, G15, G16 | **CLOSED** | — | Core | — |
| **G17** target supersession | OPEN | yes (Oracle probe `X16`) | Core `execution.py:243-244`, `:328` | When an order becomes active, size it as `target − (filled position + remaining signed qty of still-live earlier orders with the same target_key)`. Oracle: SELL 09:20 → BUY 09:25 → flat (realized −550.7000000). Core: BUY 09:25 only → ends LONG 1000 against a latest target of 0. |
| **G14b** DAY expiry boundary | OPEN | yes (Oracle probe `X14b`) | Core `execution.py:211` (`bar_start <= expiry`), `:232` (`event_ts <= expiry`) | Use an exclusive `<`. With back-to-back sessions (close 09:20 = next open 09:20), Core fills a session-1 DAY order on the session-2 opening bar; the Oracle expires it. |
| G03b TW tick ladder | OPEN | formal TW-equity runs only (not a B-case failure) | Core `costs.py:29`, `:98-100` | Tick ladder or a per-bar `tick_size`. |
| G11 engine self-identification | OPEN | no | Core `execution.py:477` | Take the SHA from package metadata and reject a disagreeing config. The adapter already rejects a provenance SHA ≠ pin, and the runner rejects a report SHA ≠ benchmarked SHA (C05). |
| G09 roll / C1 next-bar open | BY_DESIGN / MAPPED | no | Agent adapter | — |

The G17 and G14b expectations come from running the unchanged Oracle on the two probe scenarios. They are not hand-calculated and not added to `benchmark/cases`.

## 7. Runner → Core → Registry (synthetic)

The test is `tests/test_m1_core_compat.py::test_synthetic_runner_core_registry_completed`.

Setup:
- Isolated tmp governance from `tests/m2_helpers.env`, including the test holdout lock `HOLDOUT-TW-5M-v1` and a tmp `experiments.jsonl`.
- `EngineInfo` comes from `engine_info()`: `BENCHMARK_PASS`, derived from the record.
- Synthetic START-labelled bars, Agent `sma_crossover(2,3)`, and the test-only approved TW CostProfile (FLOOR_INTEGER).
- A TEST-ONLY constant tick of 0.5.

The COMPLETED record contains:
- `code.engine_git_sha = 298956f…`, `engine_benchmark_status = BENCHMARK_PASS` and `strategy_agent_sha`;
- `data[]` with snapshot and producer SHA;
- `metrics.execution` with engine SHA, `core_status`, adapter version, CostProfile id and sha256, and report status;
- `holdout_access = false`.

A missing record, the M1.1 record, a record without G16 evidence, or a record with G16 FAIL all stay `NOT_VERIFIED`. Such a run is `REJECTED_CONTRACT` C00 and Core is never called.

## 8. CI / pytest

- With Core `298956f`: **225 passed**.
- Without Core: **194 passed, 1 skipped**.
- The CI `core-compat` job installs Core `@298956f…` from GitHub, then asserts:
  - 12/12 PASS;
  - G16 PASS;
  - engine kind `REAL_CORE` with the pinned SHA.

## 9. Minimal fix list to reach M1 GO

1. **market-research-core:**
   - G17 — compute the target delta at activation from position + live pending;
   - G14b — exclusive DAY-expiry boundary.
   Pin the resulting commit.
2. **strategy-research-agent:**
   - bump `MRC_SHA` and `PINNED`;
   - re-run `python -m benchmark.harness.mrc_crosscheck`;
   - the Oracle probes must turn PASS, and the guard can then stay as a regression check.
   No other Agent change is expected.
3. Real TW-equity research additionally needs G03b and a human-approved CostProfile.
