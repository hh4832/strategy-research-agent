# Phase 3 M1 — Core Integration Adapter & Independent Benchmark Acceptance

**M1 verdict: `INTEGRATION_PENDING`** — not GO.

The adapter, validation, provenance transfer, reason mapping, runner/registry integration and the real-Core cross-check are delivered and tested. The pinned Core (`market-research-core@f4276cb`, branch `feature/phase2-unified-data-backtest`) exposes only `BacktestEngine.run_sma`, a long-only SMA integration probe. Codex's Phase 2.1 generic Execution Engine has not been published. On this Core, 11 of 12 benchmark cases cannot be expressed at all, and B02 runs but fails on cost precision. Formal M1 GO therefore cannot be claimed.

## 1. Baseline and stacking

| Item | Value |
|---|---|
| Agent base | `feature/m2-registry-governance` @ `f39413c` |
| Agent PRs | PR #1 → PR #2. Neither is merged, so this branch is stacked on PR #2 (`feature/m1-core-adapter`). |
| Core | `hh4832/market-research-core` @ `f4276cb081ae4407d356007dc76168a8cbdf2f82`, read-only. Not modified, and no Codex branch was touched. |
| Core `main` | `aadedf0` has no backtest module. The Phase 2 engine exists only on `feature/phase2-unified-data-backtest`. |

## 2. Adapter scope (`src/strategy_agent/adapters/mrc_adapter.py`)

| Responsibility | Function | Behaviour |
|---|---|---|
| Spec → Core request | `build_core_request` | Builds the proposed `CORE_EXECUTION_REQUEST_V0`. Validates dataset pins, the CostProfile and the execution convention. |
| Signals → targets | `decisions_to_targets` | Passes `Decision.decision_ts` (= `available_at`) through unchanged and collapses repeated targets. Rejects naive timestamps and targets outside −1/0/+1. |
| Core result → `BacktestReport` | `core_result_to_report` | Copies values and never recomputes them. Validates engine SHA, datasets and convention. |
| Provenance transfer | (in `core_result_to_report`) | Fills every `ports.REQUIRED_PROVENANCE` field. `check_report` (C4) still applies. |
| Rejection mapping | `map_reason`, `REASON_MAP` | Mapping is reviewed explicitly. Ambiguous Core codes (`EXECUTION_INELIGIBLE`) and unknown codes fail closed. |
| Version validation | `validate_dataset_refs`, `validate_cost_profile`, `validate_core_provenance` | Rejects `LATEST`, `UNKNOWN`, non-`BACKTEST` scopes and non-GO verdicts. CostProfile must be APPROVED with a matching sha256 and matching cost fields. The result's engine SHA must equal the pinned SHA. |
| Engine identity | `core_identity`, `engine_info` | Takes the commit from pip `direct_url` or from a clean git checkout, never from the package version. Benchmark status comes from the cross-check record, never from hand-set values. |
| Port | `MrcBacktestPort.run` | Validates first. Raises `CoreContractGap` while any required capability is missing, so it never simulates. |

The adapter does not:
- simulate fills or compute formal PnL;
- edit eligibility or fill missing bars;
- stitch futures contracts;
- import or call the oracle.

`test_m1_adapter.py` asserts this through an AST import check. The M2 isolation test was narrowed to exempt exactly this one file, for the Core import only, as ARCHITECTURE §6 prescribes. The oracle ban still applies everywhere, including the adapter.

## 3. Core compatibility matrix (measured on the installed Core, not assumed)

Source: `compatibility_matrix()`, which combines introspection with one real `run_sma` probe run. The full table with evidence is in `benchmark/results/mrc_f4276cb081ae/report.md`.

| Cap | Contract | Status @ f4276cb | Needed by |
|---|---|---|---|
| K01 generic execution entry point (external targets/orders) | C3 | MISSING | B01, B03–B12 |
| K02 short positions | C3 | MISSING | B06 |
| K03 tick slippage | C8 | PARTIAL (bps only) | all |
| K04 per-unit fixed fee, per-side tax | C8 | MISSING | futures cases |
| K05 buy/sell execution eligibility | C1 | MISSING (single `execution_eligible`) | B07 |
| K06 sessions / OUTSIDE_SESSION | C1 | MISSING | B05 |
| K07 ZERO_VOLUME_BAR | C1 | MISSING | B04 |
| K08 exclusion windows / invalidated round trips | C5 | MISSING | B11 |
| K09 roll schedule | C6 | MISSING | B09 |
| K10 open position at end | C7 | PARTIAL (position kept, not reported) | B10 |
| K11 real engine git SHA in provenance | C4 | MISSING (`engine_version` 0.2.0) | all |
| K12 Decimal arithmetic or a documented rounding rule | C3 | MISSING (float) | all |
| K13 PIT membership flag | C1 | MISSING | B08 |
| K14 TIF / expiry | C3 | MISSING | — |
| K15 specific reason codes | C3 | PARTIAL (`EXECUTION_INELIGIBLE` has no cause) | B04, B05, B07 |

## 4. Benchmark B01–B12 on the real Core

Record: `benchmark/results/mrc_f4276cb081ae/crosscheck.json` and `report.md`, with `engine.kind = REAL_CORE`. Expected values are read unchanged from `benchmark/cases`.

| Case | Status | Root cause | Responsible repo |
|---|---|---|---|
| B02 next-bar SMA | **FAIL** (executed on `run_sma`) | K03/K12: slippage in bps cannot express 1 tick; float arithmetic | market-research-core |
| B01, B03–B12 | NOT_EXECUTABLE | K01 (no generic entry point), plus the case-specific capabilities in §3 | market-research-core |

B02 detail (real Core vs oracle):

| Field | Expected | Actual |
|---|---|---|
| Signal → fill timing | BUY at 09:30, SELL at 09:45 | identical |
| Forbidden fill | none at 09:25 (the cross bar) | none |
| BUY price / fee | 60.4 / 86.07 | 60.4005 / 86.0707125 |
| SELL price / fee / tax | 59.6 / 84.93 / 178.8 | 59.6005 / 84.9307 / 178.8015 |
| Realized PnL | −1149.80 | −1149.8029 |

The Core probe follows the execution convention's timing (next eligible bar, no same-bar fill). It cannot reproduce the cost model.

## 5. CI / pytest

| Environment | Result |
|---|---|
| Local, with real Core @ f4276cb | 196 passed |
| Local, without Core | 190 passed, 1 module skipped |
| Spec validation | 3 / 3 example specs OK |
| CI job `test` (3.11 / 3.12) | Core not installed; the compat module skips |
| CI job `core-compat` (new) | Installs Core at the pinned commit; runs compat and adapter tests and the cross-check, and asserts `REAL_CORE` at the pinned SHA |

New tests:
- `test_m1_adapter.py` (25): contract, fail-closed, engine-status forgery cases, runner/registry integration.
- `test_m1_core_compat.py` (6): real Core.

## 6. Registry integration

- With the real record, `engine_info()` returns `NOT_VERIFIED`. The runner then records `REJECTED_CONTRACT` (C00) with engine SHA `f4276cb…`.
- If a PASS were wrongly claimed, `MrcBacktestPort` still raises `CoreContractGap`. The runner records `QUEUED → RUNNING → FAILED`, with the gap list and full provenance (spec sha256, dataset pins, agent SHA).
- Holdout and spec governance are unchanged under the real adapter: a holdout request without a lock → `REJECTED_GOVERNANCE`; a DRAFT spec → `REJECTED_SPEC`.
- No governance control was disabled. `registry/` and `governance/` were not modified.

## 7. Contract gaps for market-research-core (G01–G15 = K01–K15)

1. **G01** A generic `execute(request)` (or equivalent) that accepts `CORE_EXECUTION_REQUEST_V0`: targets with `signal_ts`, pinned datasets, the CostProfile reference, and the execution convention. It returns `CORE_EXECUTION_RESULT_V0`: fills, round trips, invalidated round trips, rejections, equity curve, open position, provenance.
2. **G02** Short positions.
3. **G03 / G04** Tick slippage with instrument tick size; per-unit fee, per-side tax, minimum fee and rounding from the CostProfile.
4. **G05 / G13** `buy_execution_eligible`, `sell_execution_eligible` and PIT flags supplied by Core, not derived by the adapter.
5. **G06 / G07 / G15** Session, zero-volume and limit-lock rejections with specific codes that map 1:1 to `REASON_CODES`.
6. **G08** Exclusion windows: no signal, fill or valuation inside; invalidated round trips reported.
7. **G09** Spec-driven roll schedule.
8. **G10** `open_position` with mark price at end of data.
9. **G11** A real commit SHA in result provenance.
10. **G12** Decimal arithmetic, or a documented rounding rule that matches the oracle.
11. **G14** Time-in-force / expiry.

## 8. Conditions for M1 GO and entry into M3

1. Core publishes the generic execution API (G01) and pins it to a commit. M1 then implements `MrcBacktestPort.run` against it; the call site is already in place.
2. The cross-check on that commit is all 12 PASS against unchanged oracle answers (`engine_benchmark_status` → `BENCHMARK_PASS`).
3. The `core-compat` CI job moves to the new SHA and passes.
4. A runner end-to-end run on the new Core produces a COMPLETED experiment record with full provenance.
5. M3 starts only after the above. Until then, the runner fails closed (`REJECTED_CONTRACT` / `FAILED`) and no formal research result can be produced.
