# Cost review: minimum commission and CostProfile applicability (Phase 5-C2, C2-6)

| | |
|---|---|
| Status | **REVIEW for Haw's decision.** No Core cost file, schedule or profile is changed |
| Reviewed | Core `configs/research/tsm_official_dated_costs_2017_2024.json`, base profile `tsm_official_history_research_base` v1.0.0, and `market_research_core.costs`. The file is identical in Core `76902a1` and `50ea3bf` |
| Checked against | IBKR public commission page, 2026-11-25 (sources below) |

## 1. What the profile models

| Item | Profile | IBKR Pro Fixed (official page, checked 2026-11-25) | Match |
|---|---|---|---|
| Commission per share | USD 0.005 (`equity_commission_per_share`) | USD 0.005 per share | yes |
| Minimum per order | USD 1.00 (`minimum_commission`) | USD 1.00 | yes |
| Maximum per order | 1% of trade value (`commission_cap_rate` 0.01) | 1 percent of trade value | yes |
| SEC fee, FINRA TAF | Official dated rates (`sec_rates`, `taf_rates`, verified source records) | Pass-through regulatory fees | yes (dated, separately sourced) |
| CAT fee | Scenario `cat_per_share` 0.00005 (CAT_STRESS 0.00010) | Pass-through | Assumption; not re-verified here |
| Slippage | 2 bps BASE, 1 LOW, 4 STRESS | n/a (model assumption) | Assumption |
| Spread | `half_spread_bps` 0 | n/a | **Not modelled** (see §3) |
| Volume participation | `volume_participation_cap` 0.01 | n/a (model) | Known-volume basis since Core `76902a1` |

Core computes the commission per order as `min(max(minimum, per_share × qty), cap × notional)` (`costs.py`), so the
minimum is applied correctly. **The profile metadata still says `approval_status: DRAFT` and `basis: ASSUMED`.**

## 2. Minimum-commission impact

The table shows the **round-trip commission only, in bps of notional**. Price levels are illustrative; no data was
read. The per-share rate only exceeds the USD 1 minimum from 200 shares.

| Units | P=$50 | P=$100 | P=$200 | P=$300 |
|---|---|---|---|---|
| 1 | 200.0 | 200.0 | 100.0 | 66.7 |
| 8 | 50.0 | 25.0 | 12.5 | 8.3 |
| 50 | 8.0 | 4.0 | 2.0 | 1.3 |
| 100 | 4.0 | 2.0 | 1.0 | 0.7 |
| 200 | 2.0 | 1.0 | 0.5 | 0.3 |
| 500 | 2.0 | 1.0 | 0.5 | 0.3 |
| 1000 | 2.0 | 1.0 | 0.5 | 0.3 |

On top of this come BASE slippage (2 bps per side, so 4 bps per round trip) and the regulatory fees.

**At 8 units, the minimum commission alone is 8–50 bps per round trip.** That is typically larger than an intraday
edge on a liquid large cap. P5C-TA-01 used 8 units because of the 1% participation cap: at 100 units, too many orders
could not be filled. Its fill-feasibility result is valid, but a performance result at 8 units would be dominated by
the minimum commission.

## 3. Findings

| # | Finding | Severity | Proposed handling |
|---|---|---|---|
| F1 | Rates match the official IBKR Pro Fixed schedule | — | None |
| F2 | The minimum commission dominates small orders (§2) | MAJOR for interpretation | Proposal rule R1 below |
| F3 | Quantity is squeezed from both sides: the 1% participation cap limits fills, the minimum commission inflates costs | MAJOR for design | Proposal rule R2 |
| F4 | `approval_status: DRAFT`, `basis: ASSUMED` | Gate | Haw decision D-C1 |
| F5 | Spread is not modelled (`half_spread_bps` 0); the 2 bps BASE slippage is an assumption | MINOR (research only) | Always report BASE **and** STRESS; future enhancement |
| F6 | The CAT per-share fee is not re-verified | MINOR | Future enhancement; the CAT_STRESS scenario already brackets it |

## 4. Proposal rules (to be carried into the C2-7 template)

- **R1 Economic quantity.** A performance quantity must keep the round-trip commission at the proposal's reference
  price at or below **X bps**. Haw sets X; the suggestion is 5 bps. Otherwise the proposal must label the run
  `COST_DOMINATED` and may not interpret net performance as evidence about the hypothesis.
- **R2 Two quantities, both pre-registered.** The fill-feasible quantity (limited by the participation cap) and the
  economic quantity (R1) may differ. If they do, the proposal registers both before any data is read, one for
  feasibility and one for performance, and states the reason. Changing a quantity after seeing results is a post-data
  amendment (see `AMENDMENT_GOVERNANCE.md`).
- **R3 Scenarios.** Every performance interpretation reports both BASE and STRESS. A conclusion that flips under
  STRESS must say so (`INTERPRETATION.md` §2).

## 5. Decisions for Haw

| # | Decision | Recommendation |
|---|---|---|
| D-C1 | Approve `tsm_official_history_research_base` for **RESEARCH_ONLY** use in TA-02. This is not BROKER_ACTUAL and not a formal COST_APPROVAL gate PASS | Approve; the rates match the official schedule |
| D-C2 | Threshold X for R1 | 5 bps round-trip commission at the reference price |
| D-C3 | Re-verify spread and CAT | Future enhancement; rely on STRESS and CAT_STRESS for now |

## Sources

- IBKR Commissions & Fees, checked 2026-11-25. Pro Fixed: USD 0.005 per share, minimum USD 1.00, maximum 1% of trade
  value.
  - https://investors.interactivebrokers.com/en/pricing/commissions-home.php
  - https://www.interactivebrokers.com/commissions
- SEC fee and FINRA TAF: the official dated sources already pinned in the Core schedule (`sec_rates`, `taf_rates`,
  `source_document_sha256`).
