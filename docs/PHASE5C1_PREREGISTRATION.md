# Phase 5-C1 — Pre-registered research proposal: TSM SMA 5/20 on Development 2017–2024

| | |
|---|---|
| Status | PRE-REGISTERED DRAFT, fixed before any result on this window exists. Not authorized, not executed. |
| Proposal (A2, PROPOSED) | `docs/phase5c1/research_proposal.json` (current deterministic content hash), deterministic from `proposal_completion.json` (`python -m strategy_agent.execution.c1_proposal emit`). One declared change: the evaluation window. |
| Spec | `docs/phase5c1/strategy_spec_v0_2_0.draft.json`. Rules are identical to spec 0.1.1; only the hypothesis, data binding (PENDING) and experiment fields differ. |
| Baseline | SMA 5/20 on 15m RTH bars, long-only, 100 shares. Fixed since M3-B. **Not re-selected** after the June 2024 result. |
| Family | `FAM-SMA-TSM`. This is trial **3** (Phase 4-A smoke and Phase 5-B3-C both ran on 2024-05-29..06-28). |

## 1. Market mechanism and refutable hypothesis

**Mechanism.** Information about TSM reaches the US ADR gradually:
- Taiwan-session news and semiconductor-sector flows are absorbed by US participants over hours;
- large holders split their orders across the session.

This gradual incorporation should produce short-horizon positive autocorrelation of 15m returns. A completed-bar SMA5 crossing above SMA20 would detect it. The counterparties are liquidity providers and slower traders who trade against the move.

**Refutable hypothesis.** Crossover long trades earn a positive net return per round trip **after costs** and **beyond TSM's unconditional upward drift over the same holding time**, consistently across 2017–2024.

## 2. Null hypothesis

H0: SMA5/20 net performance does not exceed the deterministic in-sample unconditional-drift baseline for the same holding durations, or net expectancy is ≤ 0 after BASE or STRESS costs.

H0 is operationalized descriptively with the pre-registered **DRIFT-MATCHED-v1** control (`evaluation/controls.py`). Each trade's net PnL is compared with `qty·entry_price·(e^{μh}−1)`, where:
- μ is the mean 15m log return over the evaluated bars;
- h is the number of bars held.

## 3. Fixed rules (unchanged from spec 0.1.1)

- **Entry:** while flat, the completed-bar SMA5 crosses above SMA20.
- **Exit:** the completed-bar SMA5 crosses below SMA20.
- **Exits:** no stop, no take-profit, no time exit.
- **Size:** FIXED_UNITS 100 shares; initial capital 100,000 USD.
- **Positions:** long-only, one position at a time. Positions may be held overnight.
- **Session:** RTH only; no closing auction.

## 4. Timing

| | |
|---|---|
| Signal time | `BAR_AVAILABLE_AT`: Core `modeled_available_at` of the completed 15m bar (CONSERVATIVE_LABEL_AGNOSTIC END envelope) |
| Execution time | `NEXT_ELIGIBLE_BAR_OPEN`, strictly later than signal availability; max fill delay 2 bars; market order |
| Outcome period | Entry fill to exit fill of each round trip (net of costs). Equity is marked to market bar by bar. |

## 5. Development window (chosen by rule, not by results)

- **Rule:** every complete calendar year inside the Development boundary [2016-10-19, 2025-01-01).
- **Research window:** [2017-01-01, 2025-01-01) ET.
- **Warm-up:** from 2016-10-19, which is the data start.
- **Holdout:** not read; the window ends exactly at the Holdout start.

The window was fixed before any result on it existed. It is never shortened, extended or split to reach 30 trades or to improve results.

**Pre-registered sensitivity: the previously seen month.** The month 2024-05-29..2024-06-28 has already been executed twice. Results are therefore also reported excluding trades that entered in that month. This check is disclosed now, not added later.

## 6. Cost and slippage scenarios

The authorized set `configs/research_only/ibkr_pro_fixed_tsm_research_only_costs_v1.json` (`bf4f2365…`), IBKR_PRO_FIXED:

| Scenario | Description |
|---|---|
| BASE | slippage 2 bps per side |
| LOW | 1 bp per side |
| STRESS | 4 bps per side |
| CAT_STRESS | 2 bps + stressed CAT fee |

These are RESEARCH_ONLY assumptions, not account-verified costs.

## 7. Metrics (computed by the existing evaluator from Core output)

Per scenario:
- trade count (valid round trips), with the sample rule `min_trades 30` → otherwise INSUFFICIENT_SAMPLE;
- expectancy, payoff ratio, profit factor, win rate;
- maximum drawdown and its duration;
- tail risk: VaR / CVaR 95 of bar returns, worst trade, maximum consecutive losses, skew, kurtosis;
- Sharpe and PSR versus 0;
- exposure, turnover, cost to gross profit;
- `drift_control`.

## 8. Decompositions (all pre-registered; no post-hoc cuts)

- **By calendar year** (2017 … 2024) and **by halves** (2017–2020, 2021–2024).
- **By holding period** in bars: 1–4, 5–16, 17–64, ≥ 65.
- **By market regime**, `REGIME-DAILY-v1` (`evaluation/regimes.py`, point in time, using completed sessions **strictly before** the entry session):
  - *trend*: prior session close above or below the mean of the prior 200 session closes → UP / DOWN;
  - *volatility*: 20-session realized volatility above or below its median over the prior 252 sessions → HIGH / LOW.

  Trades without enough history are reported as UNLABELLED and never re-assigned. Trend labels start around August 2017 and volatility labels around late 2017.

## 9. Bias controls

| Bias | Control | Residual risk (stated) |
|---|---|---|
| Look-ahead | Conservative END envelope; fills strictly later; regime labels use prior sessions only | Real 1m timestamp label and publication latency are UNKNOWN (RESEARCH_ONLY assumption) |
| Survivorship | Single symbol, pre-chosen as the only GO equity in the final gate, not selected by performance | TSM is a known 2017–2024 winner, so a result does not generalize to other names |
| Selection bias | Rules and parameters fixed since M3-B; no search; one batch; family N = 3 recorded | The June 2024 month was seen twice (sensitivity in §5) |
| Data snooping | Window fixed by rule; success criteria below fixed now; deflated Sharpe is NOT_EVALUABLE with N > 1 rather than guessed | Further variants must each count toward FAM-SMA-TSM |
| Market beta | DRIFT-MATCHED-v1 excess (timing versus drift) | A drift estimate from the same window is in-sample |

## 10. Opposing view and failure conditions

**Opposing view.** In a large, liquid ADR, 15m SMA crossings are noise. Any intraday autocorrelation is arbitraged away well inside 15 minutes. Long-only trades over 2017–2024 simply ride TSM's rise (beta), and costs consume the rest.

This view predicts:
- DRIFT-MATCHED excess ≈ 0 or negative;
- STRESS profit factor ≤ 1;
- profits concentrated in 2020–2024 up-trends (trend = UP), with losses in range-bound or low-volatility regimes.

### Pre-registered decision rule

**SUPPORTED_ON_DEVELOPMENT only if ALL of the following hold:**
1. ≥ 30 valid trades;
2. BASE and STRESS expectancy > 0, and STRESS profit factor > 1;
3. BASE DRIFT-MATCHED mean excess per trade > 0;
4. net PnL > 0 in ≥ 5 of 8 years and in both halves;
5. no single year contributes more than 50% of net PnL.

Otherwise the result is **NOT_SUPPORTED**.

Neither outcome authorizes Holdout access or trading. SUPPORTED_ON_DEVELOPMENT only qualifies the rule for a separately authorized, one-time Holdout test.

The evaluator's own verdict (KEEP / RETEST / DISCARD) and risk flags are reported alongside, unchanged.

### One-variable rule for later rounds

Any improvement must change exactly one core variable, declared in advance with its expected benefit and side effects. Each such round is a new trial in FAM-SMA-TSM.

Example: a time exit at the session end, to remove overnight-gap risk. Expected benefit: lower tail risk. Expected side effect: shorter holds and more cost per unit of profit.

## Research cycle (Task 3) — existing modules suffice

The cycle Hypothesis → Proposal → Approval → Controlled Execution → Evaluation → Decision → Archive is built from existing modules:
- A2 `draft_proposal` + exporter (proposal from an authored completion file; no external LLM);
- 5-B1 GitHub approvals + binding;
- Orchestrator REAL_GATED (A01–A10, atomic ledger);
- the existing ResearchOnlyRunner + Core;
- the fresh-process Phase 4-C evaluator;
- task archive + execution record + governed records pushed by the research lead.

Minimal changes made in this phase:
1. **Pre-registered regime decomposition** (`evaluation/regimes.py`). It was previously "not implemented", yet it is required by item 8.
2. **Pre-registered drift-matched control** (`evaluation/controls.py`), required to test H0 against market beta.
3. **Optional spec fields** `experiment.regime_definition`, `experiment.drift_control` and `experiment.family_trials_including_this` in `STRATEGY_SPEC_SCHEMA.json`, wired in `core_phase4a.execute_batch` **only when present**, so the output for older specs is unchanged.

Not done, to stay minimal:
- The B3-C-specific constants in `readiness/` and `execution/b3c_run.py` are generalized only once the new snapshot pins exist (see the data document).
- The Phase 4-B stratum `market_regime` still reads per-trade labels from the archive's `trades.csv`, which has no regime column. The regime result therefore appears in the runner metrics (`by_regime`, passed through as `upstream_metrics`), and the evaluator flag MARKET_REGIME_COVERAGE_NOT_ESTABLISHED remains.

## Next Research Authorization (exact scope, after the snapshot exists)

| Field | Value |
|---|---|
| ID (candidate) | `RA-IBKR_TSM-SMA520_P5C1-0001` |
| Purpose | `PRE_REGISTERED_HYPOTHESIS_TEST` (this document) |
| Market / symbol | IBKR_TSM / TSM |
| Strategy | `sma_crossover_tsm_15m_5_20` **0.2.0**, fast 5 / slow 20. The spec sha is fixed when the data pins are filled. |
| Dataset | `TSM_DEV_1M_20161019_20241231_<digest>` from Core dev-contract v1.2, with its manifest and lineage. The plan sha must be the one the research lead personally signed. |
| Core | the v1.2 commit sha (Codex) |
| Research window | [2017-01-01, 2025-01-01) ET; warm-up from 2016-10-19 |
| Development boundary | [2016-10-19, 2025-01-01) |
| Costs | `bf4f2365…`, BASE / LOW / STRESS / CAT_STRESS |
| Temporal policy | `CONSERVATIVE_LABEL_AGNOSTIC` 1.0.0 `7fa3cdca…` |
| Holdout | `HOLDOUT-TSM-15M-v1`, NO_ACCESS |
| max_batches | 1 |
| Executor | `colab-hh4832-exec-01`, SINGLE_EXECUTOR_ONLY |
| Validity | from formal approval + 7 days |
| Formal gates | NO_GO pin as today (RA10) |

Before this authorization, two prerequisites must be met:
1. the Core v1.2 PR is merged;
2. the research lead has signed the conversion plan in Colab and produced the snapshot.

## Methodology amendment — 2026-11-11 (before long-period strategy execution)

Corrected the earlier random-entry interpretation to match DRIFT-MATCHED-v1's
actual deterministic formula. There is no random-entry simulation, independent
sample test, p-value or statistically significant timing-alpha claim. μ is
in-sample on the supplied Core bars, including supplied warm-up. A positive excess
is descriptive relative to that baseline only. Formula, strategy rules, Family
N=3, all five success conditions and every numeric threshold remain unchanged.
The proposal and spec H0 now match the implemented method. No result was used
for this correction. Existing research outputs remain unchanged.

User-reported Recovery local preflight / archive read-back PASS is accepted as
provided data evidence. Actual Agent byte pins remain PENDING; see
PHASE5C1_INTEGRATION.md for the archive inspection and Core cross-rate blocker.
