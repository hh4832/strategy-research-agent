# M3-C FINAL — Agent Integration & Phase 3 Closure

| Item | Value |
|---|---|
| Date | 2026-11-05 (Asia/Taipei) |
| Agent | `hh4832/strategy-research-agent` PR #4, branch `feature/m3b-research-runner`. **Not merged.** |
| Agent commits | Phase A head `54d48f7`; integration `fd5a9cade361b323a9e28999ad48e0b8e3ec650c`; notebook pin `7447085207d4b710050e2a26a45eee79d9cc5e8e`; then this docs-only commit |
| Core (fixed dependency) | `hh4832/market-research-core@699e3ad59951bbebb769bb69170be8d9a1a96c01` (PR #3 head, confirmed; unmerged; Core CI run 41967704177; Core verdict Engineering GO for the research model) |
| Machine-readable record | `docs/evidence/m3c_final_acceptance.json` |

## Verdicts

| Area | Verdict |
|---|---|
| Adapter compatibility | **PASS** |
| Temporal safety (engineering, research model) | **PASS**. The label stays UNKNOWN; this is not a confirmed IBKR label. |
| Cost correctness | **PASS**. Uses a TEST_ONLY profile; these are not approved rates. |
| Colab / Drive | **PASS** (16/16) |
| **Engineering Verdict** | **GO** |
| **Formal Market Eligibility (IBKR TSM)** | **NO-GO** |

Synthetic PASS does not mean the strategy works on real TSM. A CostProfile that is ASSUMED, or that Core can represent, is not APPROVED. Passing the conservative timing model does not prove what the IBKR timestamp label means. No real TSM backtest was run.

## 1. CI

The `ci` run [42006606267](https://github.com/hh4832/strategy-research-agent/actions/runs/42006606267) on `7447085` passed all five jobs:

- `test (3.11)`
- `test (3.12)`
- `core-compat`
- `research-synthetic` (includes the M3-C tests and the bounded E2E plus resume)
- `append-only`

Local runs:

| Environment | Result |
|---|---|
| Core `699e3ad` | 304 passed |
| Core `2c6ba9a` | 282 passed, 22 skipped (the M3-A.4 tests skip) |
| No Core | 234 passed, 27 skipped |

The CI run reproduced the six reference `metrics_sha256` values bit for bit.

## 2. Core confirmation and benchmark

- PR #3 head equals `699e3ad`. Core was not modified.
- On this SHA, B01–B12, G16, G17 and G14b all **PASS** with tolerance 0, and `BENCHMARK_PASS` is derived (`benchmark/results/mrc_699e3ad59951/`).
- The legacy 8-run config gives identical values on `2c6ba9a` and `699e3ad`. The only difference is the declared new field `costs.regulatory_fees = 0.0` (`docs/evidence/m3c_n1_reproducibility.json`).

## 3. Agent changes

All changes are limited to the adapter, configuration, contract validation and tests.

**Adapter: conservative mode.**
- `MarketSemantics.available_at_policy = CONSERVATIVE_LABEL_AGNOSTIC` builds an `ExecutionConfig` with label UNKNOWN, NY timezone, `core.CONSERVATIVE_POLICY` and RESEARCH_ONLY.
- The legacy `OBSERVED` path is unchanged.

**Adapter: cost mapping.** The mapping follows the *installed* Core's CostProfile fields.
- Newly mapped: per-share commission, bps slippage, SELL regulatory rate, per-unit exchange fee, `fee_mode`, and per-component basis, evidence and approval.
- Approval is APPROVED only when the Agent profile is APPROVED; otherwise it is PENDING.
- Legacy profiles keep their Core hash.
- Components that cannot be mapped fail with K18 before any Core import.

**Contract validator** (`adapters/m3a4_contract.py`):

| Group | Checks |
|---|---|
| P01–P11 | Pins and provenance |
| K00–K03 | Ledger identities; round-trip costs equal ledger costs |
| T01 | No look-ahead |
| R01 | Rejection codes have been reviewed |

Any failure raises `ContractViolation`, and the registry records FAILED.

**Data.** `NativeSyntheticSource` regenerates the native-1m TEST_ONLY fixture and checks it against its pinned sha256. Core's `prepare_conservative_bars` then builds 15-minute buckets. The Agent never re-times a bar.

**Config.** A `CORE_POLICY` temporal reference pins Core's policy sha (RC06). CC05 compares both id and sha; the new CC08 enforces research-only for conservative runs.

**Evaluator gap fixed.** Core's `regulatory_fee` is now included in `metrics.costs`. Previously the evaluator summed only `fee + tax`.

**Market gate registry.**
- `gate_version` is now 2026-11-05.2, synced to `699e3ad`.
- COST_MODEL_COMPATIBILITY is PASS for representability only.
- The market stays NO_GO.

## 4. Temporal policy

- **Policy:** `CONSERVATIVE_LABEL_AGNOSTIC` 1.0.0, sha `7fa3cdca…59c4`. The rule `LATEST_START_END_COMPLETION` is research-only and can never be FORMAL_ELIGIBLE.
- **START/END envelope.** For every bucket: `bar_start = t0 − 1m`, `possible_open_latest = t0`, `modeled_available_at = tn + 1m`.
- **RTH only.**
  - The opening bucket is OUTSIDE_CONSERVATIVE_RTH.
  - The last bucket is CLOSING_AUCTION_EXCLUDED.
  - ETH labels are never eligible.
- **Fail closed.** A missing minute gives INCOMPLETE_SOURCE_BUCKET and a zero-volume minute gives ZERO_VOLUME_BAR. Both buckets are ineligible and never filled.
- **Decision and fill timing.**
  - A decision is made at `modeled_available_at`.
  - Every fill's earliest possible open is strictly later.
  - The policy sha appears in the config, in Core provenance and on every ledger row.

## 5. CostProfile and cost correctness

**Profile:** `synthetic_equity_componentized_test@1.0.0`, TEST_ONLY, `fee_mode` COMPONENTIZED.

| Component | Value |
|---|---|
| Commission | 0.005 per share, minimum 1.00 |
| Exchange fee | 0.0003 per share |
| Regulatory fee | SELL only, 0.0000278 × notional |
| Slippage | 2 bps |

**Hashes:** Agent sha `22a66908…`; Core sha `1c27d9d2…`.

Every check below passes:

- Known answers per fill: commission, exchange fee, regulatory fee and the slippage band.
- Ledger identities.
- Round-trip costs equal ledger costs when flat.
- `metrics.costs` equals the ledger sums.
- LOW = 0 cost.
- STRESS commission and exchange fee are exactly 2×.
- The futures per-contract commission and BOTH-side tax produce known answers.

## 6. Synthetic execution and cost sensitivity

SMA 5/20 is fixed (R02 enforced, one family trial). The table shows the reference metrics, which are identical in CI, locally and in Colab.

| Role | Scenario | Multiplier | Round trips | Net PnL | Explicit costs |
|---|---|---|---|---|---|
| TRAIN | BASE | 1.0 | 7 | −178.4476 | 17.4776 |
| TRAIN | LOW_FRICTIONLESS | 0.0 | 7 | −120.0000 | 0.0000 |
| TRAIN | STRESS_2X | 2.0 | 7 | −222.8945 | 34.9545 |
| VALIDATION | BASE | 1.0 | 4 | 765.5836 | 9.4164 |
| VALIDATION | LOW_FRICTIONLESS | 0.0 | 4 | 799.0000 | 0.0000 |
| VALIDATION | STRESS_2X | 2.0 | 4 | 740.1677 | 18.8323 |

- **Batch-level 2x check:** TRAIN survives 2x: NO. VALIDATION survives 2x: YES.
- **Interpretation:** This is synthetic engineering evidence only. The samples are far below Q5, and the strategy verdict is 無法判定. It says nothing about TSM or SMA.

## 7. Provenance and archive

**Provenance.**
- Engine `699e3ad`, version 2.2.0.
- CC01–CC08 PASS.
- M3-A.4 checks P01–P11, K00–K03, T01 and R01 PASS. K03 is NOT_APPLICABLE when a position is still open at the end.
- Six tamper tests fail closed: regulatory fee, label, policy sha, CostProfile sha, exec_ts and engine version.

**Colab smoke test.**
- Record: `_acceptance/m3c_colab_20261105_204152_fd5a9ca.json` (Drive fileId `16G-iElnoykgZeQyJwGGlJsBudCiDXBHz`).
- Python 3.13.18; 16/16 checks.
- Six archives `20261105_2040xx–2041xx_fd5a9ca`.
- Resume: 6/6 SKIPPED_RESUME.
- Manifest and self-hash: PASS.
- Formal registry and holdout untouched.

An earlier run from a cached old notebook also wrote 16 `*_8f6f798` folders and an `m3b2_colab_*` record. These are append-only and are not M3-C evidence.

## 8. Reproducibility (N1) and notebook pin (N2)

**N1 reproducibility.**
- **Numeric:** the tolerance is fixed in code at rel/abs 1e-9 (`research/reproducibility.py`).
- **Archive integrity:** exact, per archive.
- **Bitwise across environments:** NOT_GUARANTEED by policy and never forced.
  - M3-C: observed IDENTICAL in all 6 runs (Colab 3.13 = CI 3.11 = local).
  - M3-B.2 TRAIN BASE: differed by 1 ULP in `sample.effective_net_pnl`. Numeric reproducibility PASS; no metrics were rewritten.

**N2 notebook pin.** The notebook pins the already-committed integration commit `fd5a9ca` and Core `699e3ad`. `test_n2_…` asserts that `fd5a9ca` is an ancestor of HEAD with identical code; CI checks out full history for this.

## 9. Market gates (IBKR_TSM, gate_version 2026-11-05.2)

| Gate | Status |
|---|---|
| DATA_QUALITY | PASS |
| TEMPORAL_SAFETY | UNKNOWN (research-owner acceptance pending) |
| COST_MODEL_COMPATIBILITY | PASS (representability only) |
| COST_APPROVAL | FAIL |
| HOLDOUT_APPROVAL | FAIL |
| FORMAL_EXECUTION_ELIGIBILITY | FAIL |
| **Market** | **NO_GO** (`readiness` exits 3) |

## 10. Remaining blockers (formal research)

1. **TEMPORAL_SAFETY.** The research owner must accept CONSERVATIVE_LABEL_AGNOSTIC. Formal use would also need an attributed timestamp label and closing-auction handling, which are outside Phase 3.
2. **COST_APPROVAL.** The IBKR profile is DRAFT/ASSUMED. There is no account pricing evidence, the regulatory schedule is PENDING, and the Fixed cap is unverified.
3. **HOLDOUT_APPROVAL.** HOLDOUT-TSM-15M-v1 is not approved.
4. **FORMAL_EXECUTION_ELIGIBILITY.** Core `formal_market` is NO_GO, and the conservative mode can never be FORMAL_ELIGIBLE.
5. **Merges.** Core PR #3 and Agent PR #4 await owner review and merge.

## 11. Non-blocking presentation issues (recorded, not fixed)

- **N3.** A resume pass with the same batch id overwrites `_batches/summary_<batch>.json` and `cost_sensitivity_<batch>.md`. Those files then show SKIPPED_RESUME rows and "2x NOT_EVALUABLE". Run archives and the batch ledger are intact; the numbers come from the run archives.
- **N4.** Report sections 5 and 10 keep the legacy START-label wording in conservative mode. The PIT line and provenance are correct.
- **N5.** The data-quality counts in report section 10 are per bucket. Core's source-level defects appear only in `dataset_metadata.eligibility_counts`.

## 12. Scope kept

- No new strategy, no tuning, and no market-data FINAL GATE rerun.
- No Core change.
- No real TSM run.
- No M3-D.
- No merge to main.

**Phase 3 engineering development stops here.**
