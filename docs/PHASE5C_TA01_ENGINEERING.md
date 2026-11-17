# Phase 5-C — P5C-TA-01 signal, executor dispatch and random-entry baseline (engineering)

Status: **engineering capability only. P5C-TA-01 as proposed is BLOCKED (not executable)**: its spec declares
`execution.max_fill_delay_bars = 2`, which nothing enforces (see below), so the binding refuses it. Tests use hand-built synthetic data. Nothing here is a research
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
| Fill strictly after the signal; order expiry | `NEXT_ELIGIBLE_BAR_OPEN`; TIF `UNTIL_END_OF_NEXT_SESSION_END` | Same path as SMA: `decisions_to_targets` → `targets_to_orders` (MARKET, DAY) → Core. Pinned Core `50ea3bf` `execution.run`: fill at the open of the first candidate bucket of the **same session** with `bar_start > max(available_at, order_time)` (`same_timestamp_open_assumption=False`); a DAY order expires at the first session close after `order_time` | DEFINED (Core code read) |
| `execution.max_fill_delay_bars = 2` | declared in the proposal (inherited from the ATR example's execution block when the draft was built) | **Not enforced** anywhere: absent from Agent execution code and from Core `50ea3bf` (code search); orders are DAY, expiring at the first session close. The schema (`STRATEGY_SPEC_SCHEMA.json`) makes it optional and does not define how the bars are counted. Because the canonical structure hash does not cover `execution`, `params_from_spec` checks it explicitly and **refuses any spec that declares it** (`TA01_MAX_FILL_DELAY_UNENFORCEABLE`). The proposal fixture keeps the value, so **TA-01 as proposed is BLOCKED**. Execution-path tests use an explicitly labelled engineering variant that omits only this field | **BLOCKED — NEEDS Haw decision** |
| One entry per session; one position; long only | `max_entries_per_session: 1`, `max_position_units: 100` | State machine; quantity from `position_sizing` as before | DEFINED |
| Exits | support break, then `max_holding_bars` 8, then the 15:15 time exit | Same priority; held bars = eligible bars after the entry **decision** bar | DEFINED |
| 15:15 time exit | the **first eligible bucket whose decision time is at or after 15:15 ET** emits the exit signal; `flatten_before_session_end: false` (no separate session-end rule) | `local decision time >= flatten_local`, but only while the decision is still **same-day feasible** (≤ `last_feasible_exit_decision`, 15:15 for a 16:00 close). On a regular session the 15:15 decision bucket is therefore the only time-exit bucket; if it is ineligible the execution FAILS (see below). The binding refuses `flatten_before_session_end` true/missing and any timeframe other than 15m | DEFINED (signal) |
| Entry cut-off | `time_lt 14:30` | Local decision time strictly before 14:30 | DEFINED |
| Early-close session | no entries (proposal) | `session_close` (from Core rows) earlier than the regular close parsed from the spec's `calendar_ref` → no entry. Because no position is ever carried across sessions, TA-01 never holds a position on an early-close session; the 12:15 value derived for a 13:00 close is **a timing derivation only, not strategy behaviour** | DEFINED (relies on Core's session columns) |
| Missing, zero-volume or auction buckets | ineligible | Skipped: never in a window, never counted as held, never a signal (`eligible_stream`) | DEFINED |
| No same-day exit decision possible | `no_overnight` | **No next-session flatten exists.** The signal raises `SameDayExitError` when it is still long at a decision later than the last feasible exit, when a later session appears, or when the input (eligible or not) already passed the last feasible exit; `execute_batch` turns it into `TA01NoOvernightViolation` (whole execution fails, no result) | DEFINED |
| Exit order not filled in the session | `no_overnight` | The signal cannot see fills. After execution `intraday_check` FAILs on any overnight round trip or open position, and `execute_batch` raises `TA01NoOvernightViolation` for the whole batch | DEFINED |
| Entry only if a same-day time exit exists | — | An entry is permitted only when `flatten_local` ≤ that session's last feasible exit decision (also restricts random-entry candidates) | DEFINED |
| Position open at end of data | — | No fabricated exit; `intraday_check` returns `OPEN_POSITION_AT_END` FAIL | DEFINED |
| 15:15 exit order fills in session | proposal assumption | By Core code the 15:15 decision has exactly **one** same-day fill bucket (source labels 15:30–15:44, `bar_start` 15:29 ET). If that bucket is ineligible (zero-volume minute, participation cap) the DAY order expires and the execution FAILS. Whether real TSM data makes it fillable is not verified; synthetic tests verify only the code path | **UNVERIFIED on real data** |
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

1. `temporal.prepare_conservative_bars`: a 15m bucket with source labels `L..L+14` has `bar_start = L − 1min` and
   `modeled_available_at = L + 15min`. It is execution-eligible only if `L + 15min < session_close` (otherwise
   `CLOSING_AUCTION_EXCLUDED`) and every minute has volume. It is signal-eligible only if, in addition,
   `modeled_available_at` falls within `10:00–15:30` ET (`TemporalPolicy`).
2. `execution.run`: a MARKET order fills at the open of the first same-session candidate with
   `bar_start > max(available_at, order_time)`. A DAY order expires at the first session close after `order_time`.
3. Hence the last executable bucket of a 16:00 session is `L = 15:30` (`bar_start 15:29`), and the latest decision
   that can still reach it is **15:15 ET**. In general, for `m`-minute buckets this is the largest `m`-grid time
   strictly before `session_close − 2m − 1min`. (For a 13:00 early close the derivation gives 12:15. This is a
   timing derivation only: TA-01 does not enter on early-close sessions and never carries a position into one.)
   This is implemented in `signals.ta01.last_feasible_exit_decision` and unit-tested, including EST and EDT.
4. This is a **necessary** condition, not a guarantee. The 15:15 decision has a single same-day fill bucket. If that
   bucket is ineligible, the execution fails rather than carrying the position.

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

So the proposal's 15:15 time exit coincides with the latest same-day-feasible decision. No earlier time was
chosen. Entries remain limited to decisions before 14:30.

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

0. **Engineering blocker:** decide `max_fill_delay_bars`. Either remove it from the TA-01 spec, or define how its bars
   are counted so it can be enforced Agent-side without changing Core.

1. Decide the market (TSM is the only governed market and is NO_GO).
2. Resolve the auction-volume handling and the volume baseline.
3. Confirm the TSM volume unit and zero-volume frequency, and resolve the `max_fill_delay_bars` divergence (it is
   not enforced).
4. Approve a cost set.
5. Create a runner config.
6. Obtain a new research authorization, and pre-register the seed, draws and quantile.

None of these is done or implied here.
