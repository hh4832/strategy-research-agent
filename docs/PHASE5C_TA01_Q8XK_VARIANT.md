# Phase 5-C — P5C-TA01-Q8XK: per-candidate known-volume fill selection

| | |
|---|---|
| Variant | **P5C-TA01-Q8XK**, strategy_id `p5c_ta01_q8xk_res20_strongclose_relvol_tsm_15m` |
| Parent | P5C-TA01-Q8X (`p5c_ta01_q8x_res20_strongclose_relvol_tsm_15m`) |
| Labels | **POST_DIAGNOSTIC_VARIANT, EXPLORATORY_ONLY** |
| Decided | Haw, 2026-11-24 14:31 Asia/Taipei: "做做看 A, 寫完規格直接改程式即可" |
| Core | market-research-core `76902a1` (`participation_volume_basis=LAST_COMPLETED_AT_FILL_OPEN`) |
| Authorization draft | `RA-IBKR_TSM-TA01Q8XK_FILLDIAG-0001` (unsigned) |

## 1. Why

`RA-IBKR_TSM-TA01Q8X_FILLDIAG-0002` (result `b41f8f7f…6129`, Core `76902a1`) left **3/326 exits** not fillable. All
three were 12:00 ET exits: 2017-03-30, 2017-07-25 and 2018-01-05.

The cause is that the Agent and Core checked **different bars**:
- **Agent, window stage:** the volume known at the order time `t`, i.e. the 1m row with `modeled_available_at == t`.
  It passed.
- **Core, on the chosen bar:** the volume known at that bar's earliest open, i.e. the row with
  `modeled_available_at == chosen.bar_start`, one minute later. It was below 800 shares (8 × 1%), so Core rejected.
- **Result:** the Agent did not re-route on a Core rejection, so these exits never reached the persistence stage.

The persistence stage already uses Core's rule. This variant applies the same rule **inside the 2-minute window**.
No new fill rule is introduced.

## 2. Rule (only change against Q8X)

For every order intent at decision time `t` (entries and exits):

1. **Order-time check (unchanged, Haw 2026-11-18, 用下單前已知量).** Volume known at `t` × 1% ≥ 8, otherwise
   `KNOWN_VOLUME_CAP`.
2. **Window candidates (unchanged).** Core-eligible 1m bars of the same session with volume > 0 and `bar_start` in
   `(t, t+2 min]`. Missing, zero-volume and ineligible minutes are skipped.
3. **New: per-candidate known volume.** Take the **first** window candidate `c` with `ref(c) × 1% ≥ 8`, where `ref(c)`
   is the volume of the RTH row of the same session with `modeled_available_at == c.bar_start`.
   - This is exactly Core 76902a1's reference bar.
   - A missing reference row means the candidate fails. There is no fallback to older bars.
   - If tradable candidates exist but none passes, the outcome is `WINDOW_KNOWN_VOLUME_AT_OPEN_BLOCKED`.
4. **Exits that get no window fill** persist to the session end with the same rule (unchanged from Q8X).
   **Entries are never extended.**
5. **No overnight (unchanged).** An exit with no fill by the session end still fails the whole run.

**Consequence:** with Core 76902a1 the chosen bar always passes Core's participation check, by construction.
`core_known_volume_would_reject` must be 0 in this variant.

## 3. What does not change

All of the following stay the same:
- the spec structure (structure hash `0c7bde1a…246c`);
- signals, parameters, 8 units, the 1% cap and costs;
- the window length, the delayed-open exclusion and the no-overnight rule;
- data pins, Core and Holdout (no access).

The spec differs from Q8X only in identity and provenance. The rule is bound in `signals/ta01.py`
`REVIEWED_SIZING` to this `strategy_id` and its canonical SHA (`candidate_volume_check: KNOWN_AT_CANDIDATE_OPEN`).

## 4. Evidence status

This is the 4th post-diagnostic variant of TA-01:

| Variant | Change |
|---|---|
| Q8 | quantity |
| Q8X | exit persistence |
| Core fix | not a variant |
| Q8XK | per-candidate known volume |

It is data-informed and never confirmatory, blind or out-of-sample. The TSM Development window 2016-10-19..2025-01-01
is already used. Even a 326/326 fill result only shows **fill feasibility**. It does not authorize a performance
backtest. The workflow review lists no cap on post-diagnostic variants (gap W-6); that remains open.

## 5. Implementation

- **`signals/ta01.py`:** `REVIEWED_SIZING` gets the Q8XK entry with `candidate_volume_check`.
  `CANDIDATE_VOLUME_CHECKS = ("ORDER_TIME_ONLY", "KNOWN_AT_CANDIDATE_OPEN")`.
- **`core_phase4a.ta01_fill_examine(…, candidate_known_check)`:** step 3.
  - `ta01_select_fills` passes it from the reviewed binding.
  - A new not-submitted reason `FILL_WINDOW_KNOWN_VOLUME_AT_OPEN`.
  - `execute_batch` reads the binding.
- **Fill diagnostic:** in this variant only, a new counter `window_known_volume_at_open_blocked` and the definition
  `candidate_volume_check`. Earlier variants' outputs are unchanged.
- **Tests (synthetic only):**
  - the Q8X-0002 failure pattern now moves to the next candidate or persists;
  - entries are not extended;
  - Core never rejects the chosen bar;
  - the diagnostic prediction equals the Core engine;
  - Q8/Q8X behaviour is unchanged;
  - the spec SHA binding holds.
- **Authorization:**
  - `RA-IBKR_TSM-TA01Q8X_FILLDIAG-0002` is retired (USED, SUCCEEDED).
  - New unsigned draft `RA-IBKR_TSM-TA01Q8XK_FILLDIAG-0001`: the Q8X-0002 scope with the Q8XK strategy.
