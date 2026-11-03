# Phase 3 M1 — Core Integration Adapter & Independent Benchmark Acceptance

**M1 final verdict: `GO`** (pinned Core `market-research-core@2c6ba9ab7c336e7a4908744abef617e0dce713fd`).

All checks below ran on the real `ExecutionEngine.run`, with exact Decimal comparison (`TOL = 0`, CostProfile rounding `NONE`):

| Acceptance | Result |
|---|---|
| B01–B12 | **12 PASS / 0 FAIL / 0 NOT_EXECUTABLE** |
| G16 fill chronology and FIFO (6 checks) | **PASS** |
| G17 pending-target rebalance vs Oracle v1 (`X16`) | **PASS** — SELL 09:20, BUY 09:25, flat, realized −550.7 |
| G14b half-open DAY expiry vs Oracle v1 (`X14b`) | **PASS** — no fill on the next-session open; `ORDER_EXPIRED` |
| Engine status | `BENCHMARK_PASS`, derived from `benchmark/results/mrc_2c6ba9ab7c33/crosscheck.json`. It requires all of the above; `298956f` now derives `NOT_VERIFIED` (G17/G14b FAIL). |
| Synthetic Spec → Signal → Adapter → Core → BacktestReport → Registry | **COMPLETED**, with SHA, CostProfile chain and dataset pins verified |
| CI (test 3.11 / 3.12, append-only, core-compat @ `2c6ba9a`) | green |

Scope of GO: M1, the synthetic execution contract. Formal market backtests stay fail-closed:
- TW equities need G03b (tick ladder) and a human-approved CostProfile.
- Every real run still needs approved holdout locks and confirmed market semantics.

## 1. Baseline

| Item | Value |
|---|---|
| Agent | `feature/m1-core-adapter`, PR #3 (previous head `ff2c51c`). Stacked on PR #2 / #1. **Not merged.** |
| Core (read-only) | `2c6ba9a` = `298956f` + `2eb1458` (pending-target reconcile, exclusive expiry) + tests + docs. Not modified here. |
| Oracle / fixtures / hand answers | **0 lines changed.** |
| Governance / formal registry / holdout locks | **0 lines changed.** |
| History | `mrc_f4276cb081ae` (M1), `mrc_a50f3902c3a0` (M1.1: 1/12), `mrc_298956f78522` (M1.2: 12/12, G17/G14b FAIL). Kept unchanged. |

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
| Divergence guard | `known_divergences` / `guard_known_divergences` / `unsupported_divergences` | **Read-only** check on the finished run. It is enforced only for gaps whose capability (K17, K14b) the installed Core does **not** demonstrate in the measured probes. On `2c6ba9a` both are SUPPORTED, so the guard is a no-op. On `298956f` it still refuses. It changes no fill, price or PnL. |
| CostProfile chain | `core_result_to_report` | Checks that Core's `cost_profile_sha256` equals the hash of the Core profile that was submitted, and that the Agent profile sha equals the spec pin. Both hashes go into the provenance. |

The adapter never simulates fills, computes PnL or fees, edits eligibility, moves signal times, fills missing bars, stitches futures prices or calls the Oracle. This is enforced by AST import tests and the narrowed M2 isolation test.



## 3. Capability matrix on Core 2c6ba9a (measured)

- **SUPPORTED:** K01–K08, K10, K12–K16, **K17** (probe: SELL 09:20, BUY 09:25, flat) and **K14b** (probe: no fill, `ORDER_EXPIRED`).
- **PARTIAL:**
  - K09 — by design, the adapter expands rolls.
  - K11 — `engine_git_sha` is supplied by the caller.

On `298956f` the same probes measure K17 and K14b as MISSING. This is a negative control: the probes detect the defect.

## 4. B01–B12

All 12 cases are PASS on all 12 dimensions:

1. signal time and `available_at`
2. order time
3. fill time
4. fill price
5. side
6. fee and tax (Decimal)
7. realized and unrealized PnL
8. per-bar rejections
9. exclusion invalidation
10. open position
11. contract transition
12. provenance

Negative controls on B01:

| Control | Result |
|---|---|
| HALF_UP rounding | 2 differences |
| Core default open assumption | 7 differences |

## 5. G16 / G17 / G14b

| Check | Result |
|---|---|
| Fills in fill-time order (B01–B12 + X16) | PASS |
| FIFO: no `exit_ts < entry_ts` | PASS |
| Equity = initial cash before the first fill; equity − cash₀ = realized when flat | PASS |
| Prefix invariance (no future-dependent fills/PnL) | PASS |
| Unfilled orders do not change state before their fill. Every `TARGET_ORDER_ACTIVATED` event's `filled_position` equals the signed sum of earlier fills, and X16 shows no PnL before the delayed BUY fills. | PASS |
| Same-timestamp determinism (B09, input shuffled 3× + rerun) | PASS |
| **G17** X16 vs Oracle (Oracle-generated expectation) | PASS — 0 differences |
| **G14b** X14b vs Oracle (Oracle-generated expectation) | PASS — 0 differences |

Note on check 5: in M1.2 it was written as "the later target-0 order is a no-op". That wording encoded Core's pre-fix G17 behaviour. It is now stated in Oracle v1 terms: pending intent is reconciled, but filled state never moves before a fill. On `298956f` this check still passes, while the G17 Oracle probe FAILS there.

## 6. Contract gaps (`contract_gaps.json`)

| Gap | Status | Note |
|---|---|---|
| G12, G12b, G14, G14b, G15, G16, G17 | **CLOSED** | Evidence in the record |
| G03b TW price-banded tick ladder | OPEN | Blocks formal TW-equity research only; not a benchmark failure. Core `costs.py`: `tick_size` is one float. |
| G11 engine self-identification | OPEN, non-blocking | The adapter rejects a provenance SHA ≠ pin; the runner rejects a report SHA ≠ benchmarked SHA (C05). |
| G09 / C1 | BY_DESIGN / MAPPED | — |

## 7. Runner → Core → Registry (synthetic)

The test is `tests/test_m1_core_compat.py::test_synthetic_runner_core_registry_completed`.

Setup:
- Isolated tmp governance (`tests/m2_helpers.env`) with the test holdout lock `HOLDOUT-TW-5M-v1` and a tmp `experiments.jsonl`.
- Inputs: synthetic START bars, Agent `sma_crossover(2,3)`, a test-only approved TW CostProfile (FLOOR_INTEGER) and a TEST-ONLY constant tick of 0.5.

The COMPLETED record contains:
- `code.engine_git_sha = 2c6ba9a…`, `BENCHMARK_PASS` and the Agent SHA;
- `data[0]` snapshot / producer / verdict;
- `metrics.execution`:
  - Core status `RESEARCH_ONLY`, report status `RESEARCH_INTEGRATION_DEMO`;
  - Core CostProfile sha (equal to the hash of the submitted profile);
  - Agent CostProfile sha (equal to the spec pin);
  - dataset snapshot and producer, which match the spec;
  - spec sha;
- `holdout_access = false`.

## 8. CI / pytest

- With Core `2c6ba9a`: **227 passed**.
- Without Core: **195 passed, 1 skipped**.
- The CI `core-compat` job installs Core @ `2c6ba9a` and asserts:
  - 12/12 PASS;
  - G16, G17 and G14b PASS;
  - `engine_benchmark_status` = `BENCHMARK_PASS`, derived from the freshly generated record.

## 9. After M1

- **Merge order** (human): PR #1 → PR #2 → PR #3.
- **Before any real-data research:**
  - Core G03b, or a futures/IBKR market with a valid constant tick;
  - approved CostProfiles with broker evidence;
  - approved holdout locks;
  - confirmed timestamp and session semantics.
