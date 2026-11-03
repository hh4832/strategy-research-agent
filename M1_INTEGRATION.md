# Phase 3 M1 — Core Integration Adapter & Independent Benchmark Acceptance

**M1.1 verdict: `INTEGRATION_PENDING`** (not GO, not NO-GO).

The adapter now drives the real Core Phase 2.1 `ExecutionEngine.run`, pinned at `market-research-core@a50f3902c3a014922601d8cca4e7bdf761d377b8`.

- All 12 benchmark cases execute on the real engine: **1 PASS, 11 FAIL, 0 NOT_EXECUTABLE**.
- The failures come from four concrete, fixable Core contract gaps: G12, G15, G14 and G16.
- None of the failures is a defect in the Agent's adapter.
- No case shows look-ahead, a wrong fill time, a wrong side or contract, a stitched roll, or a forced close.
- NO-GO is not warranted. M1 GO is blocked until Core closes the gaps and all 12 cases PASS on a pinned commit.

Record files:
- `benchmark/results/mrc_a50f3902c3a0/crosscheck.json` — machine-readable record.
- `contract_gaps.json` — handoff file for Codex.
- `report.md` — human-readable report.

## 1. Baseline

| Item | Value |
|---|---|
| Agent | `feature/m1-core-adapter`. Stacked on PR #2 (`f39413c`), which is stacked on PR #1. **No PR is merged.** This round updates PR #3. |
| Core (read-only) | `a50f3902…` on `feature/phase2.1-execution-contract` (Core PR #2). Not modified. |
| Previous M1 run | Core `f4276cb` (`run_sma` only) gave 0 PASS, 1 FAIL, 11 NOT_EXECUTABLE. Record kept in `benchmark/results/mrc_f4276cb081ae/`. |
| Oracle | `benchmark/oracle` and `benchmark/cases`. **0 lines changed.** |

## 2. Adapter (`src/strategy_agent/adapters/mrc_adapter.py`)

| Responsibility | Function | Notes |
|---|---|---|
| Decisions → targets | `decisions_to_targets` | Decision time (= `available_at`) passes through unchanged. |
| Spec roll schedule → explicit orders | `expand_roll_schedule` | Uses the *intended* target only; never fills, never stitches prices. |
| Targets → Core orders | `targets_to_orders` | `ExecutionOrder` target-position MARKET orders, TIF `DAY`. Each order uses `signal_time = signal_available_at = order_time` = the decision time (convention v1 §1.2). |
| Pins → Core config | `core_config` | Builds `ExecutionConfig` with Core commit, snapshot, producer SHA, verdict, scope, spec sha, parameters, cost multiplier, seed and window. `same_timestamp_open_assumption=True` is set **only** for START-labelled, CONFIRMED-session data (see C1). |
| Agent CostProfile → Core | `core_cost_profile` | Field mapping only. Rules Core cannot express (FLOOR_INTEGER rounding, price-banded tick ladder) raise `CoreContractGap`. |
| Bars | `check_core_bars` | Checks that Core's execution columns are present. Eligibility, sessions and availability come from Core's own data adapters (`taiwan.py`). |
| `ExecutionResult` → `BacktestReport` | `core_result_to_report` | Values are copied, not recomputed. Provenance is checked: full 40-hex engine SHA equal to the pin, convention, spec sha, snapshot and producer. Core `FORMAL_ELIGIBLE` maps to `RESEARCH_BACKTEST`; anything else maps to `RESEARCH_INTEGRATION_DEMO`. |
| Reasons | `map_reason` | Oracle codes map 1:1. Core-only codes are reviewed and passed through under their own names. Vague codes (`EXECUTION_INELIGIBLE`, `NO_LATER_ELIGIBLE_BAR`) and unknown codes fail closed. |
| Port | `MrcBacktestPort.run` | Validates dataset pins, CostProfile, convention and market semantics, then calls `ExecutionEngine.run`. Production runners reach it only with `BENCHMARK_PASS`. |

The adapter never simulates fills, computes PnL or fees, edits eligibility, moves signal times, fills missing bars, stitches futures prices or calls the Oracle. This is enforced by AST import tests and the narrowed M2 isolation test.

## 3. Capability matrix on Core a50f390 (measured by introspection, not assumed)

| Cap | Status | Evidence |
|---|---|---|
| K01 generic execution entry point | SUPPORTED | `ExecutionEngine.run(bars, orders, *, cost_profile, config)` |
| K02 short positions | SUPPORTED | FIFO long/short lots |
| K03 tick slippage | SUPPORTED (constant tick) | `CostProfile.tick_size` + `slippage_ticks`; see G03b for the TW tick ladder |
| K04 futures per-contract fee, tax | SUPPORTED | |
| K05 buy/sell eligibility | SUPPORTED | `buy_`/`sell_execution_eligible` plus per-side reasons |
| K06 sessions | SUPPORTED | `session_open` / `session_close` |
| K07 zero volume | SUPPORTED | |
| K08 exclusion windows, invalidated round trips | SUPPORTED | B11 invalidated net PnL matches (10339.7) |
| K09 roll | PARTIAL (by design) | Contract-keyed FIFO; roll orders are supplied by the adapter; B09 matches |
| K10 open position | SUPPORTED | B10 mark 9800 matches |
| K11 engine SHA | PARTIAL | Caller-supplied `core_commit_sha` (G11) |
| K12 arithmetic / rounding | PARTIAL | Rounds to 0.01 only (G12, G12b) |
| K13 PIT | SUPPORTED | `PIT_VIOLATION`; `bar_start >= available_at` |
| K14 TIF | PARTIAL | `DAY` = session of the first candidate bar (G14) |
| K15 rejection log | PARTIAL | One rejection per *unfilled order*, none per skipped bar (G15) |

## 4. B01–B12 on the real `ExecutionEngine.run` (Core a50f390)

Inputs:
- Synthetic fixture bars with an explicit START label and the case sessions. These test the engine only; they are **not** evidence about real TXF or IBKR label/session semantics, which Core keeps UNKNOWN and blocked.
- Signals for B02 and B12 come from the Agent's own `sma_crossover` and `orb`. The other cases use the hand-calculated fixture signals.

| Case | Result | First divergent event | Realized PnL expected / actual | Root cause |
|---|---|---|---|---|
| B01 | FAIL | tax @ 09:15: 80.044 → 80.04 (−0.004) | 7339.76 / 7339.76 | G12 |
| B02 | **PASS** | — | −1149.80 / −1149.80 | — |
| B03 | FAIL | tax @ 09:20: 80.124 → 80.12 | −2660.2 / −2660.2 | G12 |
| B04 | FAIL | missing per-bar rejection `ZERO_VOLUME_BAR` @ 09:15 | 3339.76 / 3339.76 | G15, G12 |
| B05 | FAIL | `ORDER_EXPIRED` instead of an overnight fill; per-bar `OUTSIDE_SESSION` ×2 missing | −155.395 / 0 | G14, G15 |
| B06 | FAIL | tax @ 09:10: 160.792 → 160.79 | 38679.2 / 38679.2 | G12 |
| B07 | FAIL | 4 per-bar limit-lock rejections missing (fills correct) | −7253.2275 / −7253.23 | G15, G12 |
| B08 | FAIL | fee @ 01-06 09:05: 87.0675 → 87.07 | −58.7625 / −58.77 | G12 |
| B09 | FAIL | tax @ 01-06 09:05: 80.052 → 80.05 (roll, contracts and fills correct) | 26278.152 / 26278.15 | G12 |
| B10 | FAIL | tax 80.044 → 80.04 (open position and mark correct) | 0 / 0 | G12 |
| B11 | FAIL | tax 80.044 → 80.04 (invalidation correct) | 0 / 0 | G12 |
| B12 | FAIL | tax @ 09:25: 80.168 → 80.17 | 1339.632 / 1339.63 | G12 |

Twelve dimensions were compared per case:
1. Signal / availability time
2. Order time
3. Fill time
4. Fill price
5. Long/short
6. Fee/tax
7. Realized/unrealized PnL
8. Rejections
9. Invalidated round trips
10. Open position
11. Contract transitions
12. Provenance

Every difference is listed with its numeric delta in `crosscheck.json`. **Counterfactual:** if only G12 were fixed, 9 of 12 cases would PASS. Fixing G12, G15 and G14 leaves no remaining differences.

### Priority conflicts

- **C1 — next-bar open.** Convention v1 fills at the first bar with `ts ≥ order_ts`, where `order_ts = available_at = bar_end` of the signal bar. With Core's default `same_timestamp_open_assumption=False`, every fill is **one bar late** (B01: 09:20/09:35 instead of 09:15/09:30). With `True`, timing matches the Oracle in all 12 cases. This is not a PIT relaxation: Core still requires `bar_start ≥ available_at` and `bar_end > signal_time`, and the fill is stamped `SAME_TIMESTAMP_BAR_OPEN`. The adapter enables the flag only when bars are START-labelled and the session is CONFIRMED; UNKNOWN fails closed. No Core change is needed.
- **C2 — float vs Decimal.** Core quantizes fee and tax to 0.01 (HALF_UP/UP/DOWN) and accounts in float. Oracle v1 §1.6 is unrounded Decimal. The difference is at most 0.005 per fee/tax line and up to 0.0075 on realized PnL. Fill prices and tick rounding match in all executed cases. → **G12**
- **C3 — chronology.** This is shown by a probe that needs no Oracle answer:
  1. A BUY is ordered at 09:10 but is limit-up locked until 09:25.
  2. A target-0 SELL is ordered at 09:20.
  3. Core fills the SELL at 09:20 and the BUY at 09:25, then books a LONG round trip with **exit 09:20 before entry 09:25**.

  Core processes orders in order-time sequence and updates FIFO, cash and target state per order, not per fill time. → **G16**

  Separately, an unfilled target order does *not* corrupt later targets: in B05 the later target-0 becomes a NOOP. That part is correct.
- **C4 — market rules (synthetic only).**
  - Side-specific eligibility: correct (B07 fill times).
  - Exclusion: correct (B11).
  - Session: correct.
  - Roll: correct (B09).
  - PIT: correct (B08).
  - TIF: differs (G14).

## 5. Contract gaps (G01–G15 updated in place; G03b, G12b, G16 added)

| Gap | Status @ a50f390 | Blocking | Minimum correction (Core, unless noted) |
|---|---|---|---|
| G01 generic execution API | **CLOSED** | — | — |
| G02 short | **CLOSED** | — | — |
| G03 tick slippage | **CLOSED** (constant tick) | — | — |
| **G03b** TW price-banded tick ladder | OPEN | yes, for real TW equity | `costs.py`: accept a tick ladder (or a per-bar tick from the pinned trading_rules) in `adverse_fill_price` |
| G04 per-unit fee / tax | **CLOSED** | — | — |
| G05 side eligibility / G13 PIT | **CLOSED** | — | — |
| G06 / G07 session, zero volume | **CLOSED** (execution) | — | — |
| G08 exclusion | **CLOSED** | — | — |
| G09 roll | CLOSED by design | no | The adapter expands the spec roll schedule (`expand_roll_schedule`) |
| G10 open position | **CLOSED** | — | — |
| G11 engine SHA | PARTIAL | no | `execution.py`: derive `engine_git_sha` from package metadata and reject a disagreeing config value |
| **G12** unrounded mode | OPEN | **yes** (10 cases) | `costs.py`: add rounding `NONE` (skip `_round`; Decimal fees/taxes) for convention-v1 profiles |
| **G12b** FLOOR_INTEGER | OPEN | yes, for the TW CostProfile | `costs.py`: add `FLOOR_INTEGER` (`quantize(Decimal('1'), ROUND_FLOOR)`) |
| **G14** TIF | OPEN | **yes** (B05) | `execution.py`: TIF expiring at the first `session_close > order_time`, with `ORDER_EXPIRED` at that time (convention v1 §1.5) |
| **G15** per-bar rejection log | OPEN | **yes** (B04, B05, B07) | `execution.py` candidate loop: append `{order_id, bar_ts, reason}` for every skipped bar, even if the order fills later |
| **G16** chronology | OPEN | **yes** (probe) | `execution.py`: event loop over `bar_start`; at each bar, evaluate live orders in order-time sequence and apply fills to FIFO, cash and targets immediately |

Machine-readable version for Codex: `benchmark/results/mrc_a50f3902c3a0/contract_gaps.json`. Each entry has gap id, Core SHA, cases, expected, actual, numeric difference, first divergent event, root cause, responsible repository, code location, minimum correction and a blocking flag.

## 6. Runner integration

Tests use the real Core and synthetic bars:
- **End to end:** Strategy Spec → Agent `sma_crossover` → adapter → `ExecutionEngine.run` → `BacktestReport` → `check_report` (C4). The report carries the real engine SHA and the spec sha, and its status is `RESEARCH_INTEGRATION_DEMO` because the cost components are DRAFT/assumed.
- **Production runner:** with `engine_info()` derived from the committed record (`NOT_VERIFIED`), the runner records `REJECTED_CONTRACT` (C00) and **never calls Core**.
- No `BENCHMARK_PASS` is forged anywhere. The end-to-end test calls the port directly and does not go through the production runner. It is a test-only path, isolated from the runner.
- The real TW CostProfile (FLOOR_INTEGER, tick ladder) fails closed with `CoreContractGap` (G12b, G03b). UNKNOWN market semantics fail closed.
- Holdout, budget and spec governance are unchanged. `registry/` and `governance/` are untouched.

## 7. CI / pytest

| Environment | Result |
|---|---|
| Local, real Core a50f390 | **215 passed** |
| Local, without Core | 193 passed, 1 module skipped |
| CI `core-compat` | Installs Core at `a50f3902…` and runs the compat and adapter tests plus the cross-check (asserts `REAL_CORE` at the pinned SHA) |

## 8. Conditions for M1 GO / entry into M3

1. Core closes G12, G14, G15 and G16 on a new pinned commit.
2. The cross-check on that commit is 12/12 PASS against unchanged Oracle answers, and `engine_benchmark_status` returns `BENCHMARK_PASS` from the real-Core record.
3. `core-compat` moves to that SHA and passes.
4. A production runner run on that commit records COMPLETED with full provenance.
5. For real TW equity research, G03b and G12b must also be closed, and the CostProfile must be APPROVED with broker evidence.
