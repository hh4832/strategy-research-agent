# Phase 4-C — First Real Research End-to-End Acceptance

`Existing Real Archive → Archive Normalizer → Research Evaluator → Research Decision → Immutable Evaluation Archive`

This is engineering integration acceptance, **not** new strategy research. Nothing in this phase:
- re-runs the backtest,
- installs or imports Core,
- reads a snapshot, raw bars or the Holdout,
- consumes authorization budget,
- writes to the source archive, `governance/` or `registry/`.

## 1. Branch and integration

| Item | Value |
|---|---|
| Branch | `feature/phase4c-real-e2e-acceptance` |
| Base | Phase 4-A `a6509151fee5a5c0d103e3860b41fcbc616777a4` (PR #5 head) |
| Integration | `ab82919`: `--no-ff` merge of Phase 4-B `00f561aa51bce2d4b3974a0c27b46125c15d2209` (PR #6 head; source commits `9258a48`, `00f561a`; its base `545e89a` is an ancestor of `a650915`). There were no conflicts. PR #5 and PR #6 branches are untouched. |
| Phase 4-C code | `883d14c0059add270d596e4bdc86be46052219ea` (evaluator pin used by the notebook) |
| Source run | `20261107_081300_9439f8c` (Agent `9439f8c6…`, Core `b16179a7…`; authorization `RA-IBKR_TSM-SMA520_DEV-18350b3adba8e28a7e5e` 1/1 spent) |
| Archive MANIFEST.json pin | `328f2cf99c33b4d9d427327d092aaf31da619b5f5ad948c815e75b04b92345a9`. Taken from the copy committed independently in `d456fed`. This is **not** the dataset manifest `8b6fc838…cfc5`; the code checks that the two differ. |

## 2. Archive contract gap analysis

Phase 4-B's `archive_input.phase4a_archive` expects the older per-scenario layout:
`<scenario>/metrics.json`, `fills.csv`, `rejections.csv`, `equity_curve.json`, `cost_diagnostics.json`, `m3a4_checks.json`.

The real archive uses the current 14-file top-level contract from `research_only/archive_contract.py`, which writes one file per kind with a `scenario` column or key. As a result, the Phase 4-B normalizer would have found none of its allowlisted files. Phase 4-C adds a read-only normalizer for the actual contract, `evaluation/phase4c.py`. The Phase 4-B normalizer is left unchanged.

Bytes inspected:
- 8 of the 14 real files are committed in PR #5 under `docs/evidence/phase4a_dev_smoke/20261107_081300_9439f8c/`.
- The other 6 exist on Drive (folder `1zRh-u4l1K6GVBAi0P7kecayTPk4t0fKb`; `fills.csv` 98,755 B; `equity_curve.csv` 525,748 B) and are read only by the Colab runner.
- Their headers come from the same producer code, which was exercised on the SYNTHETIC fixture.

| Evaluator field | Actual archive source | Mapping | Evidence status |
|---|---|---|---|
| Source identity | `MANIFEST.json` run_id; `run_info.txt` agent/core/snapshot/dataset-manifest/config/authorization; `dataset_manifest.json` loader provenance | Direct, cross-checked across all three (C1) | AVAILABLE |
| Trades | `trades.csv` (Core round trips per scenario) | Direct. There is no trade_id column, so `trade_id = scenario:entry_ts:exit_ts`, which is content-derived so duplicates collide. Trades are never rebuilt from fills. | AVAILABLE |
| Fills | `fills.csv` (Core fill ledger) | Direct. `fill_id = order_id@exec_ts`. Core exports no `notional`, so turnover is NOT_AVAILABLE rather than computed. | AVAILABLE |
| Equity curve | `equity_curve.csv` | Direct. Observed from the warm-up start (2024-05-29), so `sample_period` = warm-up start → end; trading window [06-03, 06-29) | AVAILABLE |
| Costs | `cost_breakdown.csv` (Core `tsm_cost_diagnostics` totals) | explicit = commission + SEC + TAF + CAT; slippage is EMBEDDED_IN_PRICES (`price_adjustment_cost`, informational) | AVAILABLE |
| Rejections | `rejected_orders.csv` | Direct, one row per Core rejection event (2× BAR_NOT_ELIGIBLE + ORDER_EXPIRED for one order). Rejection rate is NOT_AVAILABLE (order id repeats). | AVAILABLE |
| Temporal diagnostics | `execution_diagnostics.json` temporal + `metrics.json` m3a4_checks | PASS only if temporal PASS and all M3-A.4 checks PASS; `lookahead_checked` NOT asserted | AVAILABLE (fill-level) |
| Authorization usage | `registry/research_authorization_uses.jsonl` (signed, `d456fed`) vs `run_info` authorization_use | Direct, record SHA must match | AVAILABLE |
| Holdout status | `holdout_locks.jsonl` + authorization holdout pin + run_info `holdout_accessed=false` + last 1m timestamp < holdout start | NOT_ACCESSED only if all agree; blindness copied from the authorization (BLINDNESS_UNVERIFIED) | AVAILABLE |
| Data quality | `market_readiness.json` DATA_QUALITY (FINAL GATE parent, PASS) + loader provenance matching dataset pins | Attestation; not re-validated | AVAILABLE (attested) |
| Cost scenarios | `cost_breakdown.csv` + `trades.csv`, same run | `cost_multiplier` = observed total cost / BASE total cost; no scenario is declared "2×" | AVAILABLE |

Remaining evidence gaps. None of these is fabricated, and each is reported explicitly:
1. **Order ledger not archived.** Only the count of 28 is recorded. 26 orders filled and 1 was rejected; the outcome of the remaining **1** order is not recorded. This follows from the M1 mapping: after a rejected entry, the next exit order targets a position Core never held. C3 reports it as `orders_without_fill_or_rejection_record = 1`.
2. **No `trade_id` or `notional`** in the Core exports, so IDs are content-derived and turnover is unavailable.
3. **Signal-computation look-ahead is not independently re-verified.** It is guaranteed structurally (a pure SMA over Core `modeled_available_at`) and checked at the fill level.

Minimal fix if wanted later (a producer change for future runs only, not this run): archive `orders.csv` with per-order outcomes, plus `trade_id` and `notional` columns.

## 3. Minimal evaluator changes (additive)

- **Schema:** optional `holdout.value.blindness`.
- **New flags, deterministic and with no cutoff:**
  - `BLINDNESS_UNVERIFIED`
  - `NET_PROFIT_COVERED_BY_TOP_TRADES`
  - `MARKET_REGIME_COVERAGE_NOT_ESTABLISHED`
- No threshold, metric definition or decision rule was changed. All 60 Phase 4-B tests are unchanged and passing.

## 4. Checks

| Check | What is verified |
|---|---|
| C1 | Identity across MANIFEST / run_info / loader provenance / config SHAs; full SHAs; dataset manifest ≠ archive manifest |
| C2 | Pinned MANIFEST.json, exact 13-file contract, every file SHA, no symlinks or unlisted entries, source unchanged before/after |
| C3 | Per scenario: no duplicate trades/fills; every trade matched to its BUY/SELL fill (time, price, qty); gross = Δprice×qty; fills = 2×trades; allocated trade costs = fill-ledger explicit cost; counts vs metrics; rejected orders never filled |
| C4 | Evaluator vs Core metrics (net/total return, win rate, payoff, expectancy, PF, MDD, net PnL, trade count) within 1e-9 (protocol); cost ledger identities |
| C5 | Fills strictly after signal availability, on a later bar, NEXT_ELIGIBLE_BAR_OPEN, T01 PASS; holdout not accessed |
| C6 | INSUFFICIENT_SAMPLE, concentration, cost sensitivity observed, missing OOS, BLINDNESS_UNVERIFIED, limited regime coverage, < 1 year; verdict RETEST / UNDETERMINED, never KEEP |
| C7 | Same input and time give a byte-identical payload; a different time changes only metadata |
| C8 | Uses 1/1 and matching the run; governance/registry unchanged; formal gates equal the authorization pin (NO_GO); holdout lock present; no executor module loaded in the process |

## 5. Status

- SYNTHETIC_FIXTURE (produced by the real Phase 4-A producer): C1–C8 PASS; 16 Phase 4-C tests.
- **Real archive: pending execution of `notebooks/Phase4C_Real_Evaluation_Acceptance.ipynb` in Colab.** A synthetic PASS is not the real acceptance.
