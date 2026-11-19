# P5C-TA01-Q8 — execution-sizing variant of P5C-TA-01

Labels: **POST_DIAGNOSTIC_VARIANT, EXPLORATORY_ONLY.** Haw proposed this on 2026-11-19, **after** seeing the
`RA-IBKR_TSM-TA01_FILLDIAG-0002` fill diagnostic: at 100 units, 163/278 exits were not fillable under the 1%
participation cap. The variant is therefore data-informed. TSM Development 2016-10-19..2025-01-01 has already been
used, so no result of this variant may be called confirmatory, blind or out-of-sample.

## Spec

| | P5C-TA-01 (parent) | P5C-TA01-Q8 |
|---|---|---|
| File | `tests/fixtures/p5c_ta01/ta01_spec_proposal.json` (unchanged) | `tests/fixtures/p5c_ta01/ta01_q8_spec_variant.json` |
| `strategy_id` | `p5c_ta01_res20_strongclose_relvol_tsm_15m` | `p5c_ta01_q8_res20_strongclose_relvol_tsm_15m` |
| `version` | 0.0.2 | 0.0.1 |
| `position_sizing.units` / `max_units` | 100 / 100 | **8 / 8** |
| Rule structure hash | `0c7bde1a…246c` | identical |

- **Changed:** only `strategy_id`, `name`, `version`, the two sizing fields and `provenance`. Provenance holds the
  parent id, the parent file SHA-256, the labels, the post-diagnostic basis and the open blocker.
- **Unchanged — every other section:**
  - universe (TSM, IBKR, RTH; 15m signals, 1m execution);
  - parameters (20-bar breakout, strong close, 2× relative volume, 10-bar support, 8-bar maximum hold);
  - the 15:15 ET time exit and one entry per session;
  - the two-minute window and no overnight;
  - costs (the 1% cap comes from the pinned schedule) and the risk constraints.

  `risk_constraints.max_position_units` stays 100 as the parent's ceiling.
- **Canonical spec SHA** (`provenance.spec_sha256`): `d35076f7f88375aaaedc9e2ab36ffce4a93036769d3245555f04553270a08110`.

## Binding

`signals.ta01.REVIEWED_SIZING` binds each reviewed `strategy_id` to its sizing:
- TA-01 is accepted only at 100 units.
- Q8 is accepted only at 8 units, and the **whole** spec must match the canonical SHA above. Its parent reference
  must also be present.

Any other id or quantity is refused (`TA01_SIZING`), so the binding never accepts an arbitrary size. With the 1% cap,
8 units need at least **800** shares in the known 1m bar; 100 units need 10,000.

## Open blocker (unchanged by the smaller size)

`CORE_FULL_MINUTE_CAP_LOOKAHEAD`: Core `execution.py:413` checks the cap against the fill bar's **full-minute**
volume, which is not known at the open. It stays a **BLOCKER** for any strategy execution. The fill diagnostic
counts it separately (`core_full_minute_cap_would_reject`) and reports it in `open_blockers`.

## Fill diagnostic

The `RA-IBKR_TSM-TA01Q8_FILLDIAG-0001` draft is **NOT approved**. Its scope:
- TSM Development 2017–2024, Holdout NONE;
- the frozen Q8 spec, 8 units, no tuning;
- one run, no Batch, no Core ExecutionEngine;
- no prices, PnL, returns, win rate or validity output.

It reports entries and exits separately:
- known-volume cap blocks;
- intents with no tradable bar in the 2-minute window;
- Core full-minute rejections (the look-ahead residual).

It uses the attempt protocol v2: pre-data checks first; a durable attempt marker; write-once success and failure
manifests. 0001 and 0002 are retired, and their Drive contents are never touched.
