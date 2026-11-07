# Phase 4-B Research Evaluator v1.0

## Scope and isolation

Base: verified Agent `545e89ac19305869e07bb4a95e9fffce07deeb47`.
Branch `feature/phase4b-research-evaluator`, separate worktree. Existing Phase4-A
runner, adapters, authorization records, locks, market gates, costs, strategies,
notebooks, formal outputs and Core are unchanged. This evaluator neither imports
an executor nor writes a registry, signs an authorization, reserves a batch,
reads raw bars, evaluates Holdout, or runs a strategy. All fixtures/examples are
`SYNTHETIC_FIXTURE`, engineering evidence only.

Existing functionality reused:

- `evaluation.research.trade_stats`: ratios after10 valid closed trades.
- `evaluation.sufficiency.assess`: Q5 sample/invalidation rules,30 valid trades,
  invalidated fraction<=10%, invalidated absolute PnL share<=25%.
- `evaluation.metrics`: drawdown and IS/OOS descriptive comparison.
- `QUANT_EVALUATION_PROTOCOL.md` §8 G1–G11, unchanged numerical thresholds.

The existing report/selector remains untouched. The standalone interface removes
its fourth strategy outcome only within the NEW evaluator. No shared module or
Phase4-A path is changed.

## Input and states

Machine input contract: `schemas/RESEARCH_EVALUATOR_INPUT_SCHEMA.json`; output: `schemas/RESEARCH_EVALUATION_SCHEMA.json`.
`schema_version=1.0.0`. `kind=SYNTHETIC_FIXTURE|RESEARCH_RESULT`.
No market-specific symbol allowlist; currency is one explicit accounting
currency per envelope. TSM/SOXX/NQ/ES/Taiwan Equity/TXF are accepted when their
finished results meet the contract. This is not a claim that those markets have
been backtested or their costs validated.

Required source fields: run ID, original archive path, full40-character source
Git/Core SHAs, dataset Snapshot ID,64-character dataset manifest SHA, strategy
ID, parameter family, timeframe, inclusive sample start/exclusive end and IANA
timezone, currency. No short SHA is silently expanded. Missing provenance blocks
assessment. A caller-supplied identity is an attestation, not authentication or
independent proof that the upstream run used that code.

Required evidence sections are holdout, trades, fills, equity_curve,
cost_breakdown, rejections, execution_diagnostics, data_quality, metrics and
budget_consumption. Each uses exactly one of:

```json
{"status":"AVAILABLE","value":"typed value specified in schema"}
```

or `{"status":"MISSING|UNTRUSTED|NOT_APPLICABLE","reason":"specific reason"}`.
There is no zero/default substitute for missing evidence. All required sections
must be AVAILABLE to produce engineering PASS; an empty trades/fills/rejections
list means observed zero, not missing. Empty equity is insufficient. Optional
statistical_evidence/cost_scenarios may be absent; missing supported stratification
is NOT_AVAILABLE and missing robustness experiments are NOT_TESTED. Their
absence never authorizes KEEP. Field-state NOT_APPLICABLE is distinct from
MISSING but cannot waive a REQUIRED evidence section. Missing/untrusted required
evidence yields BLOCKED, RETEST, UNDETERMINED with no derived performance numbers.

All timestamps must be aware RFC3339, independent of optional jsonschema format
packages. Fills are strictly later than signal_available_at when supplied.
Trades entry<exit; duplicate IDs, nonmonotonic equity, out-of-window observations,
overlapping folds, misassigned trade/fold PnL, incomparable cost scenarios,
nonfinite numbers and cost component discrepancies fail closed. Current v1
accepts only unopened Development results (`holdout.status=NOT_ACCESSED`);
UNKNOWN/ACCESSED blocks. This does not inspect a Holdout dataset.

Optional context: initial_capital, equity_sampling=REGULAR|IRREGULAR|UNKNOWN,
sample_role=IN_SAMPLE|OUT_OF_SAMPLE|UNKNOWN. No equity point or performance number
is invented to fill a gap. Each trade is an upstream closed round trip with
VALID/INVALIDATED status; trades and net/gross PnL are NEVER reconstructed from
fills. Fills require explicit qty/price and optional upstream notional (needed for
turnover, especially futures/FX). Rejection rate requires unique order IDs plus
actual submitted-order count, not fill count.

## Metrics, definitions and authority

The schema `$defs.metric_definitions` and existing evaluation module define units.
All amounts are in declared account currency; ratios are fractions, holding and
drawdown durations are seconds. No cross-currency aggregation is performed.

| Metric | Implementation / limitation |
|---|---|
| Total return | Final Core equity / declared initial capital−1; net mark-to-market, includes open positions |
| Net return | Sum VALID closed trade net_pnl / initial capital; distinct from total return |
| Gross closed PnL | Upstream trade.gross_pnl when actually present; not an invented gross-equity curve |
| Annualized return | Geometric ACT/365.2425 CAGR, actual equity span>=365.2425 days, first equity=initial capital and upstream explicit external_cash_flows=0; otherwise unavailable/insufficient |
| Win rate, average win/loss, payoff, expectancy, PF | Reuse trade_stats;10 valid trades minimum for ratios, zero trades count in denominator, average loss negative; undefined no-loss PF/payoff marked NOT_APPLICABLE |
| Max drawdown | Reuse Core-observation equity definition; negative fraction; no extra initial equity observation inserted |
| Drawdown duration | Longest elapsed time from prior observed peak until recovery or last observation; not a fabricated daily/bar count |
| Volatility | Nonannualized ddof=1 equity-return std only for declared AND actually regular intervals; records interval seconds |
| Tail loss | Existing100-return minimum; empirical5% quantile and conditional mean; no tail inference for irregular intervals |
| Worst trade | Minimum VALID closed Core net_pnl |
| Exposure | Explicit upstream held-bar fraction, never inferred from overnight wall time |
| Turnover | Sum explicit fill.notional /2 / mean observed equity; nonannualized; no assumed contract multiplier |
| Cost / gross profit | Explicit whole-run debits / sum positive VALID closed gross_pnl; includes whole-run costs (possibly open positions), not closed-trade net profitability |
| Rejection rate | Unique rejected orders / submitted orders, only with unambiguous denominator |
| Holding period | Actual wall seconds per closed valid trade, not inferred bars |
| Fill diagnostics | Preserved upstream; execution chronology checked; prices/slippage not resimulated |

Metrics already computed upstream remain preserved under `upstream_metrics`.
Available standard overlapping Core metrics (including closed trade statistics)
are checked against aggregation within1e-9; a mismatch produces engineering FAIL,
RETEST, UNDETERMINED and no conflicting derived metric series. Matching metrics
are labeled UPSTREAM_METRIC/MATCH. New aggregations are explicitly labeled
AGGREGATE_OF_CORE_RECORDS; they do not replace Core's PnL or performance authority.
No fee, commission, tax, TAF, CAT or slippage is charged again. The optional
slippage cost diagnostic is informational; embedded slippage is not subtracted.
The evaluator does not recompute a formal Sharpe/PSR/DSR/p-value/FDR procedure.
Source-provided statistical evidence is reported/checked against the existing
protocol, never invented from a short bar sample.

## Stratification and robustness

Calendar year uses exit time in the declared timezone. Symbol, elapsed holding
period, pre-existing regime/parameter-region labels, and observed cost scenarios
are grouped only from supplied records. Incomplete labels make the entire
corresponding grouping NOT_AVAILABLE; no partial subset is presented as complete.
Groups below30 valid trades are INSUFFICIENT_SAMPLE even if a descriptive net sum
is shown. Regime labels must be pre-registered upstream; this module does not
create/verify regime definitions or infer parameter regions after seeing returns.

Robustness matrix distinguishes actual observations from NOT_TESTED: IS/OOS
comparison needs supplied fold scores, temporal stability needs actual
nonoverlapping source-bound folds, parameter stability needs supplied regions,
cost sensitivity needs completed comparable scenarios (never linear repricing),
concentration uses observed net contributions, multiple testing needs actual
family trial records/diagnostics. Selection/survivorship checks are upstream
attestations with evidence refs; their underlying universe construction is not
independently reproduced. Observed fill timing alone cannot prove signal
calculation has no look-ahead; full upstream diagnostics remain necessary.

Opponent flags are deterministic data-backed facts: nonpositive PnL, positive
gross/negative net, single trade/symbol/year covering ALL net profit, observed
cost sign reversal, any observed rejection (no unapproved 'large' cutoff),
missing tails/tests, less than one year observed, sample insufficiency and
SYNTHETIC_FIXTURE. Flags include evidence and severity, no invented risk score.
Their absence is never proof of safety. Blocked inputs also emit blocker flags.

## Decisions: exactly KEEP / RETEST / REJECT

Engineering PASS/FAIL/BLOCKED is separate from strategy verdict and six layers.

1. Missing/untrusted/inconsistent required evidence: RETEST, UNDETERMINED;
   RETEST does not imply a positive strategy effect.
2. Q5 insufficient sample: RETEST, UNDETERMINED, regardless of positive/negative PnL.
3. Sufficient descriptive sample with nonpositive closed net PnL or an observed
   existing protocol gate failure: REJECT. The scope is the supplied sample,
   not proof that a strategy can never work.
4. KEEP requires all G1–G11 PASS plus supplied MinTRL/selection/survivorship and
   full upstream temporal-check evidence, actual RESEARCH_RESULT (not synthetic).
   It means research retention only, never formal market admission or trading.
5. Otherwise RETEST, DESCRIPTIVE_ONLY. A positive return alone can never be KEEP.

G1 needs REGISTERED/FROZEN spec, validator zero errors, supplied spec SHA and hash
verification. G2 needs matching Core SHA and archived benchmark evidence, G3
verified data pins. G4 requires Q5 sufficient and>=6 actual OOS folds. G5–G7 use
ONLY supplied OOS_DAILY_NET Sharpe/PSR>=.95, DSR>=.95 with complete family trial count,
Holm p<=.05. G8 requires observed2x result>0 and supplied break-even>=2. G9 reuses
>=60% positive folds and<=40% single-fold net contribution. G10 is declared FIXED
or the existing grid neighbor/PBO criteria. G11 uses declared drawdown halt and
available turnover/exposure. No new Sharpe/p-value/FDR threshold is introduced.
All statistical evidence is bound to source run/Core/dataset-manifest identity
and an evidence reference. JSON assertions do not authenticate a benchmark or
registry; callers must mark unverifiable assertions UNTRUSTED. The evaluator
never fabricates BENCHMARK_PASS or obtains those checks by executing a backtest.

## Phase4-A Archive compatibility

`evaluation.archive_input.phase4a_archive` verifies the caller-pinned SHA of
`MANIFEST.json`, requires COMPLETED and a named scenario, then verifies ONLY these
allowlisted result artifacts when present: metrics.json, fills.csv, trades.csv,
rejections.csv, equity_curve.json, cost_diagnostics.json, m3a4_checks.json.
It rejects unsafe paths/hash mismatches and never follows arbitrary manifest
entries to raw Parquet/source/Holdout files. Actual source provenance in the
manifest must agree with context. Context can supply missing identity and
explicit data/holdout attestation, NOT absent performance. It does not independently
authenticate that context. Archived SHA pins establish byte integrity only.

Current4-A output has metrics/equity/fills/rejections/cost diagnostics/checks but
NO trades.csv and incomplete source/temporal/holdout context. It is **structurally
readable, not fully evaluable**. The normalizer requires an explicit unopened-Development attestation before reading
result artifacts and returns MISSING trades and UNKNOWN
execution diagnostics rather than extrapolating completed trades from fills or
claiming all temporal checks passed. Cost scenarios/statistical evaluation use
the explicit unified envelope; no cross-run comparison is guessed. Caller/source
identity absent from the archive must be externally pinned. Budget is a copied
usage receipt for the SOURCE run, not a new evaluation consumption.

Claude's real archive is needed to validate actual headers, full provenance,
precision, cost ledger and diagnostic semantics and to supply genuine trade
exports through a separately reviewed handoff. This phase does not ask Claude
to change execution logic, reads no real result/Holdout and consumes no authority.
No direct support is claimed for the older M3-B `manifest.json` layout; it may
supply the common envelope, or needs a separately scoped mapper in future.

## API / CLI / outputs

```python
from strategy_agent.evaluation.universal import evaluate, write_evaluation
result = evaluate(result_envelope, evaluator_commit_sha=actual_full_code_sha,
                  evaluated_at="2026-11-06T21:00:00+08:00")
write_evaluation(result, "/tmp/new_evaluation_directory")
```

The pure API's SHA argument is the caller's explicit code attestation. CLI checks
the actual full Git HEAD/clean tree and rejects a mismatching expected SHA:

```bash
python -m strategy_agent.evaluation \
  --input tests/fixtures/evaluator/positive_short_run.json \
  --output /tmp/new_synthetic_evaluation
# Archive-only mode; no strategy or snapshot path is accepted:
python -m strategy_agent.evaluation \
  --phase4a-archive /path/to/finished/archive \
  --archive-manifest-sha256 <independently-pinned-64hex> \
  --scenario BASE --context /path/to/explicit_identity_context.json \
  --output /tmp/new_evaluation
```

Exit0=engineering PASS (strategy may still be RETEST/REJECT); exit2=blocked/failed
assessment. Input parsing/integrity failures stop before writing. Five new files:
evaluation.json, evaluation_report.md, risk_flags.json, robustness_matrix.csv,
research_decision.json. All carry Source Run ID/path/commit, Evaluator commit,
aware timestamp (default Asia/Taipei), schema/evaluator versions. Unknown source
metadata is null/MISSING, not a synthetic real identity. Source archive and formal
governance/registry paths cannot be output targets. Existing directories are
refused; source files never rewritten. Output JSON uses finite numbers only.

Same envelope + evaluator revision + explicit evaluation timestamp produces
byte-identical five files (tested). Default current timestamp intentionally
changes between invocations; input SHA remains deterministic. The fixed clock
option is for replay/testing and must not be presented as actual elapsed time.
No Notion/Research Registry write occurs.

## Verification and remaining boundary

`tests/test_universal_evaluator.py` and checked-in SYNTHETIC_FIXTURE cover all12
requested scenarios, hand values, existing gates, artifact tampering, source
read-only behavior, cross-market notional/frequency semantics and reproducibility.
Run `pytest -q tests/test_universal_evaluator.py` without Core; full no-Core
regressions run on Python3.11/3.12. Existing CI already discovers these tests;
workflow modifications are unnecessary. The tests fingerprint governance,
registry, configs and notebooks around evaluation.

Unsupported: rebuilding trades/PnL from fills, fresh strategy execution,
annualized volatility/Sharpe from arbitrary bar frequency, raw market validation,
unknown derivative/FX multipliers, inferred regimes/parameter searches, market
impact simulation, authenticated statistical evidence acquisition, final Holdout
assessment, deployment decisions and automatic registry/Notion publication.

## Phase 4-C additions (real-archive acceptance)

Minimal, additive changes; no threshold, metric definition or decision rule changed:

- Input schema: optional `holdout.value.blindness` (`VERIFIED_BLIND|BLINDNESS_UNVERIFIED`), copied from the signed
  research authorization. `BLINDNESS_UNVERIFIED` emits risk flag `BLINDNESS_UNVERIFIED`; absence asserts nothing.
- Risk flags (deterministic facts, severity REVIEW, no cutoff): `NET_PROFIT_COVERED_BY_TOP_TRADES` (smallest k whose
  combined net PnL is at least the total, with the remaining trades' sum) and `MARKET_REGIME_COVERAGE_NOT_ESTABLISHED`
  (no pre-registered regime labels).
- `evaluation/phase4c.py`: read-only normalizer for the CURRENT Phase 4-A archive contract (top-level 14-file layout of
  `research_only/archive_contract.py`), acceptance checks C1–C8 and the eight-file evaluation archive. The older
  per-scenario normalizer `archive_input.phase4a_archive` is unchanged.
