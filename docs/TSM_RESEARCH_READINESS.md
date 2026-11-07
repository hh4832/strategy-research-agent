# TSM Research Readiness — Temporal Acceptance & IBKR Fixed Cost

| Item | Value |
|---|---|
| Date | 2026-11-05 (Asia/Taipei) |
| Agent | `strategy-research-agent` PR #4, branch `feature/m3b-research-runner` (**not merged**). Base: `358402c`. |
| Core | `market-research-core@699e3ad59951bbebb769bb69170be8d9a1a96c01` (PR #3, **not merged**). Not modified. |
| Data | `20261101_205431_06359faf7ef4_IBKR_FINAL_GATE_FULL`, TSM RTH 2016-10-19 … 2026-10-16 (2,513 days). Read-only; not re-downloaded or re-validated. |
| Machine-readable | `docs/evidence/tsm_cost_assumptions.json`, `governance/market_readiness.json` (gate_version 2026-11-05.4) |

## Decision: **NOT_READY**

The research lead's two assumptions are recorded. Exploratory TSM research is still not runnable: six exact blockers remain (§7). No real TSM backtest was run, and the Holdout was not touched.

| Question | Answer |
|---|---|
| Temporal Research Approval | **ACCEPTED** (RESEARCH_ONLY) |
| Fixed Cost Assumption Approval | **ACCEPTED_RESEARCH_ONLY** (`IBKR_PRO_FIXED`). These are listed terms, not account-verified rates, and never `BROKER_ACTUAL`. |
| Regulatory Fee Coverage | **PARTIAL.** SEC Section 31 is dated and complete for the whole snapshot and expressible in Core. FINRA TAF is dated and sourced but **not expressible in Core**. CAT pass-through is **UNKNOWN**. |
| Research-only Eligibility | **NO-GO** |
| Formal Market Eligibility | **NO-GO** |
| Core modification needed? | **Only for exact TAF modelling** (or the research lead accepts a documented conservative bound instead, §7). Nothing else needs Core. |
| Can the first real TSM non-holdout exploratory backtest start? | **No** — see §7 |

## 1. Temporal research assumption

The record is in `governance/market_readiness.json`, under `markets.IBKR_TSM.facts.conservative_temporal_policy.research_approval`.

| Field | Value |
|---|---|
| approval_type / scope | TEMPORAL_ASSUMPTION / RESEARCH_ONLY |
| policy | `CONSERVATIVE_LABEL_AGNOSTIC` 1.0.0, sha `7fa3cdcaf501651f749dbc72c31b362fcb9e10cc55920191cef87c1a050059c4` (equals Core `CONSERVATIVE_POLICY.sha256`, tested) |
| approval_basis | USER_EXPLICIT_ACCEPTANCE — `hh4832`, research lead in `governance/approvers.json`, 2026-11-05 21:16 +08:00 |
| source_timestamp_label / actual_publication_latency / closing_auction_attribution | UNKNOWN / UNKNOWN / UNKNOWN |
| Model properties (Core) | RTH only; signals 10:00–15:30 ET; fills strictly after modeled availability; no incomplete bars |

**Not approved:**
- the label (bar_start or bar_end) and the publication latency;
- closing-auction fills;
- live trading;
- formal holdout use;
- unconfirmed costs.

**Formal gates unchanged:**
- TEMPORAL_SAFETY stays **UNKNOWN** (`approved_by` null).
- FORMAL_EXECUTION_ELIGIBILITY stays **FAIL**.

## 2. Fixed cost assumption (`IBKR_PRO_FIXED`, RESEARCH_ONLY)

| Item | Assumption | Evidence status |
|---|---|---|
| Commission | USD 0.005/share/side | IBKR listed terms ([IBKR UK commissions](https://www.interactivebrokers.co.uk/en/index.php?f=39753)), not account-verified |
| Minimum | USD 1/order | same |
| Maximum | 1% of notional | same |
| Exchange / clearing fees | included in the Fixed commission (0 separately) | same ("all-inclusive") |
| Slippage | BASE 2 bps, LOW 1 bp, STRESS 4 bps per side | research assumption |
| Half-spread | 0 (not added on top of slippage) | research decision |

**Core-format configurations.** One per scenario, in `configs/core_cost_profiles/ibkr_pro_fixed_tsm_research_{base,low,stress}_v1.json`.
- They load with Core's own `load_cost_profile` and are priced by Core's own `calculate_fill_costs`, using `commission_scheme FIXED`, `commission_cap_rate 0.01` and `fee_mode COMPONENTIZED`.
- Each profile is `DRAFT` with component approvals `PENDING`, so formal approval is never implied.
- The scenarios differ **only** in `equity_slippage_bps`. A Core cost multiplier would also scale the commission, so it is not used for them.

| Scenario | configuration_sha256 | Core profile sha256 (as_of 2024-06-03) |
|---|---|---|
| BASE | `bb10abbb…3f94` | `e8364d60…2e8e` |
| LOW | `50f90682…6fb8` | `97c481aa…939b` |
| STRESS | `d2a49ca8…df7f` | `7fb93d8c…3316` |

The Core profile SHA depends on the trade date, because the selected SEC interval is part of the profile. Pin it per run window.

A conflicting IBKR page (`stknorthambundcoms.php`) shows a 0.5% maximum and a legacy SEC rate. It is recorded and not used.

## 3. Dated regulatory fees

**SEC Section 31** — sells only, fraction of sale notional, no minimum or maximum.
- IBKR passes it through; the source is secondary, because IBKR's primary page is unreachable from here.
- It is modelled through Core's dated `regulatory_schedule`. A date outside 2016-10-19 … 2026-10-16 fails closed and is never treated as zero.

| Effective (trade date) | $ / million | Source |
|---|---|---|
| 2016-10-19 – 2017-07-03 | 21.80 | SEC 2016-206, 2017-111 |
| 2017-07-04 – 2018-05-21 | 23.10 | SEC 2017-111, 2018-67 |
| 2018-05-22 – 2019-04-15 | 13.00 | SEC 2018-67 |
| 2019-04-16 – 2020-02-17 | 20.70 | SEC 2019-30 |
| 2020-02-18 – 2021-02-24 | 22.10 | SEC 2020-7, 2020-49 |
| 2021-02-25 – 2022-05-13 | 5.10 | SEC 2021-8, 2021-36 |
| 2022-05-14 – 2023-02-26 | 22.90 | SEC 2022-60 |
| 2023-02-27 – 2024-05-21 | 8.00 | SEC 2023-15, 2023-41 |
| 2024-05-22 – 2025-05-13 | 27.80 | SEC FY2024 #2 |
| 2025-05-14 – 2026-04-03 | 0.00 (official) | SEC FY2025 |
| 2026-04-04 – 2026-10-16 | 20.60 | SEC FY2026 #2; continues by statute, no FY2027 advisory listed |

The SEC sets rates by charge date. Trade date is used as the charge date, so the settlement lag around each change is not modelled.

**FINRA TAF** — sells only, USD per share, with a per-trade maximum.
- IBKR passes it through (secondary source).
- **Core 699e3ad cannot express it.** `regulatory_fee_per_unit` charges both sides, has no cap and is not dated.
- It is **not modelled and not zeroed**: blocker `CORE_GAP_TAF`.

| Effective | $/share sold | Max/trade | Source |
|---|---|---|---|
| 2016-10-19 – 2021-12-31 | 0.000119 | 5.95 | FINRA RN 12-31; SR-FINRA-2020-032 |
| 2022 | 0.000130 | 6.49 | SR-FINRA-2020-032 |
| 2023 | 0.000145 | 7.27 | SR-FINRA-2020-032 |
| 2024 – 2025 | 0.000166 | 8.30 | SR-FINRA-2020-032; SR-FINRA-2024-019 |
| 2026-01-01 – 2026-09-30 | 0.000195 | 9.79 | SR-FINRA-2024-019 |
| 2026-10-01 – 2026-12-31 | 0.00 (fee holiday) | 0.00 | SR-FINRA-2026-021 |

**FINRA CAT fees** — per executed equivalent share. Rates are listed on [CAT fee alerts](https://catnmsplan.com/cat-fee-alerts): 2024-1 0.000035; historical assessment 0.000013; 2025-1 0.000022; 2025-2 0.000009; 2026 fees proposed; invoices paused after Dec 2025.
- Whether IBKR Fixed passes them through, and for which trade periods, is **UNKNOWN**.
- Blocker `CAT_FEE_PASS_THROUGH_UNKNOWN`. Not zeroed.

**Scenarios.**
- `HISTORICAL_COST_SCENARIO` is the dated one above. It is incomplete until TAF and CAT are resolved.
- No `CURRENT_COST_SCENARIO` was created. It would apply 2026 rates to the whole history, so it is allowed only as a separate, labelled scenario if the research lead asks, and the two are never mixed.

## 4. Holdout

`governance/holdout_locks.jsonl` is **empty**. HOLDOUT-TSM-15M-v1 has no approved boundaries, and the Agent cannot create a lock (A4). Until the research lead approves one, H01 rejects every IBKR TSM experiment. Nothing touched any holdout.

## 5. Why research-only is still NO-GO under the current rules

Each of these points fails closed independently, and none was changed:

1. `GatedMarketSource` / RC12 need market status GO, all six gates PASS and an `allowed_window`.
2. CC06 requires Core `FORMAL_ELIGIBLE` for REAL_MARKET runs, which conservative mode never returns.
3. H01: there is no holdout lock.
4. The Agent has no loader from the snapshot's native 1m rows to Core `prepare_conservative_bars`, and no way to use dated Core cost configurations in the port.

## 6. Minimal RESEARCH_ONLY authorization — design only, not implemented

The goal is a separate, explicitly authorized path that **does not** change how the formal gates decide:

1. **Record.** Add `markets.IBKR_TSM.research_authorization` (append-reviewed via PR) with:
   - `scope: RESEARCH_ONLY_EXPLORATORY`, `approved_by` from `approvers.json`, `approved_at`, `expires_at`;
   - pins: temporal policy sha, cost configuration SHAs, dataset snapshot and producer;
   - `allowed_window`, which must end before the approved holdout start;
   - `max_runs`.
2. **Gate.** A new `ResearchAuthorization` check (separate from `Readiness.missing()`) requires all of:
   - DATA_QUALITY and COST_MODEL_COMPATIBILITY PASS;
   - the temporal `research_approval` sha equal to the Core policy sha;
   - the cost `research_assumption` ACCEPTED with no open regulatory blocker;
   - an approved holdout lock (H01 unchanged);
   - the window inside `allowed_window`.

   The formal `GatedMarketSource` path and RC12 stay as they are.
3. **Config / contract.**
   - A new config kind `REAL_RESEARCH_ONLY` (RC13): requires `temporal_policy.source = CORE_POLICY` and the dated cost configurations.
   - CC06 for this kind accepts Core `RESEARCH_ONLY` only; FORMAL_ELIGIBLE would itself be a contract mismatch.
   - Registry records are tagged `RESEARCH_ONLY`. They can never feed holdout, family promotion or a deployment verdict.
4. **Data / cost adapter.**
   - Read native 1m rows from the pinned snapshot, then Core `prepare_conservative_bars`.
   - Call Core `load_cost_profile(path, symbol, as_of)` per window, and split windows at SEC rate changes because Core rejects fills outside the selected interval.
5. **Never:**
   - formal gate PASS, live trading or holdout access;
   - treating UNKNOWN fees as zero;
   - parameter search (R02 stays mandatory).

## 7. Remaining blocking issues (exact; no open-ended verification phase)

| ID | Blocker | Resolves with |
|---|---|---|
| HOLDOUT_LOCK_MISSING | No approved HOLDOUT-TSM-15M-v1 (H01) | The research lead approves the lock with explicit dates (`holdout_admin approve`) |
| NO_RESEARCH_ONLY_REAL_PATH | No authorized research-only execution path (§5) | Implement §6 after the research lead authorizes it |
| NO_REAL_NATIVE_LOADER | No snapshot → Core conservative bars / dated cost config path | Part of §6 |
| CORE_GAP_TAF | TAF is not expressible in Core 699e3ad | A Codex Core release (sell-side per-unit fee + per-trade cap + dated schedule), **or** a research-lead decision to accept a documented conservative bound (both sides, uncapped, per-year configs) for RESEARCH_ONLY |
| CAT_FEE_PASS_THROUGH_UNKNOWN | IBKR Fixed CAT pass-through is unknown | An IBKR statement or primary page, or a research-lead documented assumption |
| (formal only) | TEMPORAL_SAFETY UNKNOWN, COST_APPROVAL FAIL, HOLDOUT_APPROVAL FAIL, FORMAL_EXECUTION_ELIGIBILITY FAIL | Out of scope for exploratory research |

## 8. Checks run

`tests/test_tsm_cost_assumptions.py` and `tests/test_m3b2_contract.py` cover:

- temporal policy sha = Core;
- CostProfile SHAs pinned;
- dated SEC selection at every boundary, with dates outside the snapshot failing closed;
- Fixed known answers: per-share, minimum, 1% cap, SEC on sells only;
- BASE/LOW/STRESS differing only in slippage;
- the TAF Core gap recorded;
- research acceptance never upgrading a gate;
- holdout ledger empty and market gate fail-closed.

The existing CI regression suite runs unchanged. No Phase 3 synthetic acceptance rerun was needed, because no pipeline code changed.

## 9. Scope kept

- No Core change.
- No raw-data change or re-validation.
- No formal registry or holdout change.
- No SMA parameter change.
- No holdout use.
- No real TSM backtest.
- No merge.
