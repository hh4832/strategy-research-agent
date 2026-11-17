# Phase 5-C — P5C-TA-01 signal, executor dispatch and random-entry baseline (engineering)

Status: **engineering capability only.** Tests use hand-built synthetic data. Nothing here is a research
result, a data/cost/market approval, a research authorization or a trading permission. The TSM 2016-10-19 to
2025-01-01 Development data has already been used, so any future TA-01 run on it is exploratory only — never blind
or confirmatory.

## What was added

| File | Purpose |
|---|---|
| `src/strategy_agent/signals/ta01.py` | Pure TA-01 signal (`ta01_decisions`), forced-entry variant for the null (`forced_entry_decisions`), candidate entry points (`entry_candidates`), fail-closed spec binding (`params_from_spec`), Core-row session map (`sessions_from_rows`), post-execution `intraday_check` |
| `src/strategy_agent/evaluation/random_entry.py` | `RANDOM-ENTRY-NULL-v1` baseline: explicit `RandomEntryConfig`, deterministic draws, `compare`, and `run` with one shared executor |
| `src/strategy_agent/research_only/core_phase4a.py` | `signal_family()` dispatch. TA-01 specs → TA-01. **Every other spec keeps the unchanged SMA call.** For TA-01 only, `ta01_intraday_check` is added per scenario and `signal_family` is added to the output |
| `tests/test_phase5c_ta01_signal.py`, `tests/test_phase5c_random_entry.py` | Synthetic unit tests (no Core) |
| `tests/test_phase5c_ta01_core_batch.py` | Synthetic end-to-end run through `execute_batch` and Core with synthetic prices, rates and days (same pattern as the existing K02 test); skipped without Core |
| `tests/fixtures/p5c_ta01/ta01_spec_proposal.json` | The P5C-TA-01 proposal spec draft, as a **test fixture**. It is not a registered spec, and its dataset refs are example placeholders |
| `.github/workflows/ci.yml` | One line: the Core TA-01 test is added to the existing synthetic-only `phase5c1-integration` job |

Not changed: Core, existing strategy specs, data, the formal cost set, periods, `registry/`, `governance/`, ledgers,
authorizations, CLAIM, archives, and `research_only/runner.py`. The governed runner stays pinned to its C1 SMA config.

## Rule-by-rule consistency check (spec draft → implementation)

| Rule | Spec draft | Implementation | Status |
|---|---|---|---|
| Resistance, volume average and support exclude the current bar | `lag: 1` | Windows hold previous eligible bars only and are updated after the decision. Test: an entry fires, which is impossible if the current bar were in the window | DEFINED |
| Warm-up | windows must be full | No signal until all three windows are full | DEFINED |
| Entry components | breakout, bullish, `high > low`, strong close, relative volume | `pattern()`; all must hold; `>=` is inclusive where the spec says `ge` | DEFINED |
| Signal time | `BAR_AVAILABLE_AT` | Decision at the `SignalBar.available_at` = Core `modeled_available_at` (unchanged mapping) | DEFINED |
| Fill strictly after the signal; order expiry | `NEXT_ELIGIBLE_BAR_OPEN`; TIF `UNTIL_END_OF_NEXT_SESSION_END` | Same path as SMA: `decisions_to_targets` → `targets_to_orders` (MARKET, DAY) → Core decides fills, rejections and expiry | DEFINED (Core semantics, unchanged) |
| `execution.max_fill_delay_bars = 2` | declared | Not consumed by Agent code (the existing SMA specs declare the same value). Whether Core enforces it is not shown in this repo | **NEEDS_CONFIRMATION** |
| One entry per session; one position; long only | `max_entries_per_session: 1`, `max_position_units: 100` | State machine; quantity from `position_sizing` as before | DEFINED |
| Exits | support break, then `max_holding_bars` 8, then 15:15 time flatten or session end | Same priority; held bars = eligible bars after the entry **decision** bar | DEFINED |
| Entry cut-off | `time_lt 14:30` | Local decision time strictly before 14:30 | DEFINED |
| Early-close session | no entries (proposal) | `session_close` (from Core rows) earlier than the regular close parsed from the spec's `calendar_ref` → no entry | DEFINED (relies on Core's session columns) |
| Missing, zero-volume or auction buckets | ineligible | Skipped: never in a window, never counted as held, never a signal (`eligible_stream`) | DEFINED |
| Exit not filled in session | must not be hidden | The signal cannot see fills. If still long at the next session, it emits `SESSION_CHANGE` on that session's first eligible bar (no entry on that bar). After execution, `intraday_check` returns **FAIL** for any overnight round trip or open position | DEFINED (detection); Core behaviour **NEEDS_CONFIRMATION** |
| Position open at end of data | — | No fabricated exit; `intraday_check` returns `OPEN_POSITION_AT_END` FAIL | DEFINED |
| 15:15 decision fills in session | proposal assumption | Derived from `docs/M3C_FINAL_ACCEPTANCE.md` §4 (closing bucket excluded). Not verified on real 15m bucket construction | **UNVERIFIED** |
| Bucket volume = sum of 1m volumes; TSM volume unit; frequency of zero-volume minutes | — | Not visible in this repo | **NEEDS_CONFIRMATION** |
| Auction volume leaking into an eligible bucket; intraday U-shaped volume | — | Not handled (cannot be expressed safely) | **BLOCKED for research** until decided |

The binding is fail-closed. A spec is accepted only if `strategy_id` starts with `p5c_ta01_`, its canonical rule
structure equals the reviewed hash `0c7bde1a…246c`, the parameters are exactly `res_n, vol_m, vol_k, close_pos,
sup_l` and equal the spec defaults (FIXED search), the strategy is long-only and RTH-only with `no_overnight`, and
there is one `time_lt` cut-off plus an explicit `HH:MM-HH:MM` calendar. Anything else raises before any order
exists. TA-01 parameters given with a non-TA-01 spec are refused, so they never reach SMA.

## RANDOM-ENTRY-NULL-v1

See the module docstring for the full statement.

- **Question it answers:** does entering only on the TA-01 pattern beat entering at a random permitted entry point,
  with identical exits, quantity, fill path and costs? Mean net PnL per closed trade is compared with a configured
  quantile of the null distribution.
- **Population:** sessions with at least one permitted entry point. A permitted point is one where all non-pattern
  entry constraints hold, computed by the strategy's own code. Each draw takes `n_entries` sessions without
  replacement and one uniform point in each. Draw `i` is seeded with `Random(f"{seed}:{i}")`.
- **Fail-closed cases:** no permitted points; fewer sessions than entries; zero entries; a drawn entry not
  realised; missing `net_pnl`; a config field missing (there are no defaults).
- **Cannot answer:** statistical significance beyond the empirical rank, a market or beta comparison, regime
  dependence, trading permission.
- **Biases:** day-selection effects are attributed to the pattern; intraday seasonality is not matched; a random
  entry can exit on the next bar; samples may be small.
- **Not wired into any governed run.** Real-data use needs a research authorization. The seed, draws and quantile
  for any real study are `NEEDS_HUMAN_INPUT`. Tests use synthetic values only.

## Remaining before any TA-01 research

1. Decide the market (TSM is the only governed market and is NO_GO).
2. Resolve the auction-volume handling and the volume baseline.
3. Confirm volume aggregation and units, and the max-fill-delay and DAY expiry semantics.
4. Approve a cost set.
5. Create a runner config.
6. Obtain a new research authorization, and pre-register the seed, draws and quantile.

None of these is done or implied here.
