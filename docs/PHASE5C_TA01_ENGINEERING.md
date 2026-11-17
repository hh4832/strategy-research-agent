# Phase 5-C — P5C-TA-01 signal, executor dispatch and random-entry baseline (engineering)

Status: **engineering capability only.** The proposal spec now binds and runs on synthetic data: signals on Core 15m
buckets, orders filled on Core 1m bars within a 2-minute window (Haw's decision, 2026-11-18). Research use stays
BLOCKED (see the end). Tests use hand-built synthetic data. Nothing here is a research
result, a data/cost/market approval, a research authorization or a trading permission. The TSM 2016-10-19 to
2025-01-01 Development data has already been used, so any future TA-01 run on it is exploratory only — never blind
or confirmatory.

## What was added

| File | Purpose |
|---|---|
| `src/strategy_agent/signals/ta01.py` | Pure TA-01 signal (`ta01_decisions`), forced-entry variant for the null (`forced_entry_decisions`), candidate entry points (`entry_candidates`), fail-closed spec binding (`params_from_spec`), Core-row session map (`sessions_from_rows`), post-execution `intraday_check` |
| `src/strategy_agent/evaluation/random_entry.py` | `RANDOM-ENTRY-NULL-v1` baseline: explicit `RandomEntryConfig`, deterministic draws, `compare`, and `run` with one shared executor |
| `src/strategy_agent/research_only/core_phase4a.py` | `signal_family()` dispatch. TA-01 specs → TA-01. **Every other spec keeps the unchanged SMA call, orders (DAY) and 15m evaluation.** For TA-01 only: the input frame is the Core **1m** conservative frame; the 15m signal frame is rebuilt from it with Core's own `prepare_conservative_bars`; orders are `NEXT_BAR` and pass `ta01_fill_window`; metrics use 1m bars; `ta01_intraday_check` per scenario; `signal_family` and `ta01_fill_window` in the output |
| `tests/test_phase5c_ta01_signal.py`, `tests/test_phase5c_random_entry.py` | Synthetic unit tests (no Core) |
| `tests/test_phase5c_ta01_core_batch.py` | Synthetic end-to-end run through `execute_batch` and Core with synthetic prices, rates and days (same pattern as the existing K02 test); skipped without Core |
| `tests/fixtures/p5c_ta01/ta01_spec_proposal.json` | The P5C-TA-01 proposal spec draft, as a **test fixture**. It is not a registered spec, and its dataset refs are example placeholders |
| `.github/workflows/ci.yml` | One line: the Core TA-01 test is added to the existing synthetic-only `phase5c1-integration` job |

Not changed: Core, existing strategy specs, data, the formal cost set, periods, `registry/`, `governance/`, ledgers,
authorizations, CLAIM and archives. No runner config exists for TA-01.

### Governed runner (follow-up PR, stacked on this one)

`research_only/runner.py` `_execute` now asks `core_phase4a.execution_frame` which frame to pass to `execute_batch`:

- **SMA / every non-TA-01 spec:** `loaded.conservative_15m`, the same object Core's loader returned (tested to be
  the identical object). SMA behaviour and archive output are unchanged.
- **TA-01:** the Core 1m conservative frame, built by Core `prepare_conservative_bars(loaded.source_1m,
  bar_minutes=1)` from the rows Core's loader already returned (no extra data read). Fail-closed cross-check: Core's
  own 15m rebuild from the same rows must equal the loader's 15m frame (timing, eligibility, volume), otherwise
  `Phase4ABindingError`.
- The archive (`execution_diagnostics.json`) records `signal_family`, `execution_frame` and `ta01_fill_window`
  (orders not submitted because no bar opened within 2 minutes) for TA-01 only. Trade holding periods are still
  counted in Core 15m bars.
- Preflight, authorization, CLAIM and slot consumption are untouched; they run before `_execute`. A TA-01 failure
  (`TA01NoOvernightViolation`) after consumption is recorded by the existing `FAILURE.json` path.
- Tests call `_execute` with the data load replaced by synthetic Core output and write a full synthetic archive;
  no preflight, authorization, real data or Batch is involved.

## Rule-by-rule consistency check (spec draft → implementation)

| Rule | Spec draft | Implementation | Status |
|---|---|---|---|
| Resistance, volume average and support exclude the current bar | `lag: 1` | Windows hold previous eligible bars only and are updated after the decision. Test: an entry fires, which is impossible if the current bar were in the window | DEFINED |
| Warm-up | windows must be full | No signal until all three windows are full | DEFINED |
| Entry components | breakout, bullish, `high > low`, strong close, relative volume | `pattern()`; all must hold; `>=` is inclusive where the spec says `ge` | DEFINED |
| Signal time | `BAR_AVAILABLE_AT` | Decision at the `SignalBar.available_at` = Core `modeled_available_at` (unchanged mapping) | DEFINED |
| Signal bars vs execution bars | `timeframe: 15m`, `source_timeframe: 1m` | Signals on Core 15m buckets; fills on Core 1m bars (the minutes the 15m buckets are built from). Both frames come from Core `prepare_conservative_bars` on the same native rows; the Agent re-times nothing. Every 15m decision time equals the `modeled_available_at` of a 1m row (the bucket's last minute); a missing row fails closed | DEFINED |
| Fill rule (Haw, 2026-11-18) | `NEXT_ELIGIBLE_BAR_OPEN`; `execution.max_fill_delay_bars: 2` | `max_fill_delay_bars` counts **1-minute execution bars**: the order may fill only on Core's **first** candidate 1m bar after the decision (same session, `bar_start > order_time`), and only if its latest possible open is within 2 minutes of the decision and it is tradable. If the next bar with data opens 3 minutes later, or the in-window bar has zero volume / is ineligible, **the order does not fill** (no waiting). Implemented with Core `time_in_force="NEXT_BAR"` (one candidate; a rejected candidate ends the order) plus `ta01_fill_window`, which does not submit an order whose first candidate is outside the window and records it as `FILL_WINDOW_NO_BAR`. The binding requires the field (integer ≥ 2; 1 can never fill because Core's first candidate opens at decision + 2 min) and `source_timeframe: 1m`; the canonical hash does not cover `execution`, so this is checked explicitly | DEFINED (Core code read) |
| Spec TIF `UNTIL_END_OF_NEXT_SESSION_END` | schema const | Superseded in practice by the stricter 2-minute window (an order never lives longer than its single candidate bar) | DEFINED (stricter) |
| Unfilled entry / unfilled exit | — | Unfilled entry: no position; the later exit target is a no-op. Unfilled exit: the position would stay open → `intraday_check` FAILs → whole execution fails (`TA01NoOvernightViolation`) | DEFINED |
| One entry per session; one position; long only | `max_entries_per_session: 1`, `max_position_units: 100` | State machine; quantity from `position_sizing` as before | DEFINED |
| Exits | support break, then `max_holding_bars` 8, then the 15:15 time exit | Same priority; held bars = eligible bars after the entry **decision** bar | DEFINED |
| 15:15 time exit | the **first eligible bucket whose decision time is at or after 15:15 ET** emits the exit signal; `flatten_before_session_end: false` (no separate session-end rule) | `local decision time >= flatten_local`, but only while the decision is still **same-day feasible** (≤ `last_feasible_exit_decision`, 15:45 for a 16:00 close with 1m execution; Core's signal window ends at 15:30, so in practice 15:15 or, if 15:15 is ineligible, 15:30). If neither is eligible the execution FAILS (see below). The binding refuses `flatten_before_session_end` true/missing and any timeframe other than 15m | DEFINED (signal) |
| Entry cut-off | `time_lt 14:30` | Local decision time strictly before 14:30 | DEFINED |
| Early-close session | no entries (proposal) | `session_close` (from Core rows) earlier than the regular close parsed from the spec's `calendar_ref` → no entry. Because no position is ever carried across sessions, TA-01 never holds a position on an early-close session; the 12:45 value derived for a 13:00 close is **a timing derivation only, not strategy behaviour** | DEFINED (relies on Core's session columns) |
| Missing, zero-volume or auction buckets | ineligible | Skipped: never in a window, never counted as held, never a signal (`eligible_stream`) | DEFINED |
| No same-day exit decision possible | `no_overnight` | **No next-session flatten exists.** The signal raises `SameDayExitError` when it is still long at a decision later than the last feasible exit, when a later session appears, or when the input (eligible or not) already passed the last feasible exit; `execute_batch` turns it into `TA01NoOvernightViolation` (whole execution fails, no result) | DEFINED |
| Exit order not filled in the session | `no_overnight` | The signal cannot see fills. After execution `intraday_check` FAILs on any overnight round trip or open position, and `execute_batch` raises `TA01NoOvernightViolation` for the whole batch | DEFINED |
| Entry only if a same-day time exit exists | — | An entry is permitted only when `flatten_local` ≤ that session's last feasible exit decision (also restricts random-entry candidates) | DEFINED |
| Position open at end of data | — | No fabricated exit; `intraday_check` returns `OPEN_POSITION_AT_END` FAIL | DEFINED |
| Exit order fills in session | proposal assumption | The 15:15 decision has exactly **one** fill bar: the 1m bar labelled 15:17 (`bar_start` 15:16). If it is missing, has zero volume or hits the participation cap, the exit does not fill and the execution FAILS. How often this happens on real TSM 1m data (entries too) is not verified; synthetic tests verify only the code path | **UNVERIFIED on real data** |
| Bucket volume | — | Core `50ea3bf` `temporal.prepare_conservative_bars`: `volume = group.volume.sum()`; any zero-volume minute makes the bucket ineligible for signal and execution | DEFINED (Core code read) |
| TSM volume unit; frequency of zero-volume minutes | — | Not visible in code or docs; would need data | **NEEDS_CONFIRMATION** |
| Auction volume leaking into an eligible bucket; intraday U-shaped volume | — | Not handled (cannot be expressed safely) | **BLOCKED for research** until decided |

The binding is fail-closed. A spec is accepted only if `strategy_id` starts with `p5c_ta01_`, its canonical rule
structure equals the reviewed hash `0c7bde1a…246c`, the parameters are exactly `res_n, vol_m, vol_k, close_pos,
sup_l` and equal the spec defaults (FIXED search), the strategy is long-only and RTH-only with `no_overnight`,
there is one `time_lt` cut-off plus an explicit `HH:MM-HH:MM` calendar, and `flatten_before_session_end` is explicitly
`false`. Anything else raises before any order
exists. TA-01 parameters given with a non-TA-01 spec are refused, so they never reach SMA.

## No-overnight and the last feasible exit decision (evidence)

Haw's decision: TA-01 is intraday. Evidence from pinned Core `50ea3bf` (read-only source; no data read):

1. `temporal.prepare_conservative_bars`: a bar with source labels `L..L+e-1` has `bar_start = L − 1min`,
   `possible_open_latest = L` and `modeled_available_at = L + e − 1 + 1min`. It is execution-eligible only if
   `L + e < session_close` (otherwise `CLOSING_AUCTION_EXCLUDED`) and every minute has volume. A 15m bucket is
   signal-eligible only if, in addition, `modeled_available_at` falls within `10:00–15:30` ET (`TemporalPolicy`).
2. `execution.run`: a MARKET order's candidates are same-session bars with `bar_start > max(available_at,
   order_time)`. `NEXT_BAR` uses only the first candidate; if Core rejects it, the order ends unfilled.
3. With 1m execution bars, a decision `d` has its first candidate at label `d + 2min` (`bar_start d + 1min`). The
   2-minute window admits exactly that bar. It is executable only if `d + 3min < session_close`, so for a 16:00
   close the last feasible decision on the 15m grid is **15:45 ET** (13:00 close: 12:45, derivation only). Core's
   signal window already ends at 15:30, so this bound never cuts a real decision; it guards the code path.
   Implemented in `signals.ta01.last_feasible_exit_decision` and unit-tested, including EST and EDT.
4. This is a **necessary** condition, not a guarantee. The single in-window bar may be missing or untradable; the
   exit then does not fill and the execution fails rather than carrying the position.

### Failure output

- `SameDayExitError` is raised by the signal before any order exists. `execute_batch` re-raises it as
  `TA01NoOvernightViolation`.
- After execution, a failed `intraday_check` in any scenario makes `execute_batch` raise `TA01NoOvernightViolation`
  after all scenarios are evaluated.
- In both cases `execute_batch` returns nothing, so no metrics, trades or report reach the caller. The tests assert
  this.
- The governed runner (`research_only/runner.py`, read only, not run) catches the exception and writes only
  `FAILURE.json` (status, error text, traceback). `write_contract_archive` is never called, so no performance, trade
  list or archive is produced.
- There is no next-session `SESSION_CHANGE` flatten.

The proposal's 15:15 time exit is kept; with 1m execution it is no longer the last feasible decision, so an
ineligible 15:15 bucket moves the exit to 15:30. Entries remain limited to decisions before 14:30.

## RANDOM-ENTRY-NULL-v1

See the module docstring for the full statement.

- **Question it answers (only this):** do TA-01's entries, taken as a whole, have a higher mean net PnL per closed
  trade than the same number of random permitted entries, with identical exits, quantity, fill path and costs? The
  strategy is compared with a configured quantile of the null distribution.
  - The comparison is **joint**: the null randomises both which sessions are traded (day selection) and when inside
    a session the entry happens (intraday timing).
  - A difference cannot be attributed to either part alone.
  - It does **not** identify an isolated or causal effect of the breakout, the candle shape or the volume condition,
    because no component is ablated.
- **Population:** sessions with at least one permitted entry point. A permitted point is one where all non-pattern
  entry constraints hold, computed by the strategy's own code. Each draw takes `n_entries` sessions without
  replacement and one uniform point in each. Draw `i` is seeded with `Random(f"{seed}:{i}")`.
- **Fail-closed cases:** no permitted points; fewer sessions than entries; zero entries; a drawn entry not
  realised; missing `net_pnl`; a config field missing (there are no defaults).
- **Cannot answer:** the separate effect of day selection vs intraday timing; the isolated or causal effect of any
  single component; statistical significance beyond the empirical rank; a market or beta comparison; regime
  dependence; trading permission.
- **Biases:** day-selection effects are attributed to the pattern; intraday seasonality is not matched; a random
  entry can exit on the next bar; samples may be small.
- **Not wired into any governed run.** Real-data use needs a research authorization. The seed, draws and quantile
  for any real study are `NEEDS_HUMAN_INPUT`. Tests use synthetic values only.

## Remaining before any TA-01 research

0. **Runner config:** the runner can now execute a TA-01 spec, but no TA-01 run config or authorization exists.

1. Decide the market (TSM is the only governed market and is NO_GO).
2. Resolve the auction-volume handling and the volume baseline.
3. Confirm the TSM volume unit and zero-volume frequency, and how often the single 2-minute fill bar is missing or
   untradable on real 1m data. (Separately: older SMA docs — `readiness/checks.py:72`, `PHASE5B3A_READINESS.md` D6,
   `PHASE5C1_PREREGISTRATION.md:43` — describe `max_fill_delay_bars` as enforced for SMA; it is not. Not changed
   here.)
4. Approve a cost set.
5. Create a runner config.
6. Obtain a new research authorization, and pre-register the seed, draws and quantile.

None of these is done or implied here.
