# P5C-TA-01 — case closure (2026-11-24)

**Status: CLOSED — `FILL_FEASIBLE_PERFORMANCE_NOT_TESTED`.** Decided by Haw on 2026-11-24 at 18:23 Asia/Taipei. The
machine-readable record is `ta01_case_disposition.json`.

## What was asked

Can the first AI-proposed hypothesis, P5C-TA-01, pass a controlled, auditable, human-authorized research lifecycle?
Profitability was never the question.

- **Hypothesis:** on TSM 15m RTH bars, a break above the 20-bucket resistance with a strong close and ≥ 2× relative
  volume continues intraday. Long only, no overnight.
- **Origin:** Claude, 2026-11-17 (`ta01_origin/`).

## What was found (fill feasibility only; no PnL was ever computed)

| Run | Variant / Core | Exits not fillable | Cause |
|---|---|---|---|
| FILLDIAG-0001 | 100 units / 50ea3bf | — | Pre-data input-path failure; no data read |
| FILLDIAG-0002 | 100 units / 50ea3bf | 163/278 | Known-volume cap |
| Q8-0001 | 8 units / 50ea3bf | 7/326 | 4 known-volume cap, 3 Core look-ahead |
| Q8X-0001 | + exits persist / 50ea3bf | 3/326 | Core full-minute look-ahead |
| Q8X-0002 | same / **76902a1** | 3/326 (other dates) | Agent checked volume known at `t`; Core checked the volume known at the bar's open |
| **Q8XK-0001** | + per-candidate known volume / 76902a1 | **0/326** | — (entries 326/326) |

The 2-minute window itself was never the binding constraint.

## Blocker

`CORE_FULL_MINUTE_CAP_LOOKAHEAD` is **CLOSED**. The evidence chain:
1. Spec approved (PR #41).
2. Core fixed (market-research-core PR #5, `76902a1`).
3. TA-01 moved onto the new Core and fails closed on older Cores (PR #42).
4. Under the new Core, the 3 earlier look-ahead rejections fill.
5. Q8XK shows 0 Core rejections.

## Evidence status

**EXPLORATORY_ONLY.**
- Four changes were made after seeing results: quantity, exit persistence, the Core rule and the candidate rule.
- The TSM Development window was already used by the SMA studies.
- **No performance backtest was run. Holdout was never accessed.**

## Not part of this closure (open)

- **Workflow gaps:**
  - W-2: review records.
  - W-3: interpretation artifact.
  - W-6: no cap on post-diagnostic variants.
  - W-7: Drive results not indexed.
- The evaluator should treat the legacy Core basis plus a cap as LOOK_AHEAD for formal runs.
- Minimum-commission review for 8-unit orders.
- The Core feature branch (PR #4) is unmerged; the Agent pins SHAs on it.
- Old Agent PRs #5, #6 and #7 are still open.

**Reopening** needs a new proposal and a new authorization. Any TA-01 performance study needs its own authorization and
remains EXPLORATORY_ONLY on this data.
