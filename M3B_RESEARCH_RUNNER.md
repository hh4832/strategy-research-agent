# Phase 3 M3-B — Automated Research Runner MVP

**Verdict (M3-B.1 closure): engineering `GO` for the synthetic research runner; real Drive archive `PENDING`; real-market research `NO-GO` (Codex M3-A).** See §10.

Original M3-B verdict: **`SCOPED_GO`**

- **What passes:** all nine M3-B1 synthetic acceptance items, on the real `market-research-core@2c6ba9a` (`BENCHMARK_PASS`).
- **Limit 1 — no real-market run.** The M3-B2 real-market gate is closed: no Codex M3-A Market Readiness Report was available. The TSM run was **not started**.
- **Limit 2 — Drive archive not written from here.** The runner was not executed in Colab, where Drive is mounted. In this session, the Drive target correctly reported `DRIVE_UNAVAILABLE` and wrote no backup.

## 1. Baseline

| Item | Value |
|---|---|
| Agent base | `main` @ `fdbe84f` (PR #1–#3 merged). Branch `feature/m3b-research-runner`. |
| Core (read-only) | `2c6ba9ab7c336e7a4908744abef617e0dce713fd`. `engine_benchmark_status` = `BENCHMARK_PASS`, derived from `benchmark/results/mrc_2c6ba9ab7c33`. Core `main` = `bf01fa9` (merge of the same commit). |
| Not changed | Oracle, B01–B12, Core, formal `registry/`, holdout locks, budget, approvers, cost profiles. |
| Added governance file | `governance/market_readiness.json`. This is the market-gate record (owner: Codex). `IBKR_TSM` = `NO_GO`, synced from Codex M3-A in M3-B.1 (§10). |

## 2. Pipeline (`src/strategy_agent/research/`)

```
Strategy Spec -> Governance Admission -> Data Access -> Signal Generation -> Core Execution
   -> Performance Evaluation -> Experiment Registry -> Research Report -> Archive
```

| Stage | Implementation (reused unless marked new) | Output | Fail-closed on |
|---|---|---|---|
| Spec | `spec.validate`; **R02** parameter check (`runner.parameter_issues`), mandatory in the research runner since M3-B.1 | errors list | schema/S01–S18; params ≠ declared, non-numeric, out of range, off grid, constraint broken; FIXED ≠ declared defaults |
| Governance admission | M2 `ExperimentRunner` (family, holdout H01–H08, budget B01–B06, engine `BENCHMARK_PASS`) | registry events | any rejection (recorded) |
| Data access | `research.data` (new): `SyntheticSource`, `GatedMarketSource` | frame sha256, rows, snapshot | snapshot ≠ spec pin; missing Core columns; gate not GO |
| Signal generation | M2 `signals.sma.sma_crossover` | decisions + sha256 | decision not at a bar `available_at` |
| Core execution | M1 `MrcBacktestPort` → `ExecutionEngine.run` | fills, trips, rejections, equity | `CoreContractGap` (incl. new **K18**), provenance mismatch |
| Performance evaluation | `evaluation.research` (new; aggregates Core output only) | metrics + sha256 | insufficient data → `NOT_EVALUABLE` |
| Experiment registry | M2 hash-chained JSONL (verified after every run) | final status | broken chain |
| Research report | `research.report` (new): 13 sections + rule-based verdicts | markdown | — |
| Archive | `research.archive` (new) | run dir + verified manifest | existing dir (never reused); Drive not mounted → `DRIVE_UNAVAILABLE` |

Each stage writes `{inputs, outputs, status OK|FAILED|SKIPPED, error}` to `stages.json`. A stage that did not run is marked `SKIPPED` and gives the reason.

- **Engineering verdict:** in `final_status.json`.
- **Strategy recommendation:** 保留 / 修改後再測 / 淘汰 / 無法判定.

These are two separate verdicts and are never merged.

## 3. Fixed strategy

The spec is `specs/research/sma_crossover_tsm_15m_5_20.json` (**DRAFT**). It defines:

- **Instrument:** IBKR TSM, 15m RTH bars.
- **Rule:** SMA fast 5 / slow 20 (min = max = default), `search.mode FIXED`, `max_trials 1`, long only.
- **Timing:** decision at bar `available_at`; execution at Core's next eligible bar open (convention v1).
- **Costs:** cost multiplier 1.0, with pre-registered sensitivity at 0 / 1 / 2 / 4.

It stays DRAFT, because under the formal governance a REGISTERED copy fails:

- S17: Q2 session coverage is UNKNOWN.
- S18: the IBKR profile is DRAFT.

## 4. Evaluation (`evaluation/research.py`)

Trade-level metrics:

- count, win rate, average win, average loss, payoff, expectancy, profit factor.

Account-level metrics:

- total return;
- cost-adjusted net return and gross return;
- fees and taxes;
- max drawdown and its duration;
- Sharpe (needs ≥ 20 sessions and ≥ 10 trades; i.i.d. scaling stated);
- exposure and annualised turnover.

Tail risk:

- bar VaR / CVaR 95, skew, kurtosis;
- worst and best trade;
- consecutive losses;
- trades below −3 sd;
- worst session.

Decompositions:

- by year, by symbol, by holding period in bars, by parameter set;
- **by regime is `NOT_EVALUABLE`**: spec v1 pre-registers no regime definition, and no post-hoc labels are allowed.

Ratios on fewer than 10 trades are `NOT_EVALUABLE`. The sample gate (Q5) needs ≥ 30 valid trades.

Multiple testing:

- the family trial count comes from the registry;
- PSR(SR > 0) is reported;
- DSR = PSR only when N = 1. For N > 1 it needs the cross-trial variance, so it is `NOT_EVALUABLE` rather than guessed.

Data quality:

- zero-volume bars;
- missing bars against the session template;
- order share of bar volume;
- slippage against bar open;
- rejections by reason;
- unfillable signals.

## 5. M3-B1 acceptance (synthetic, real Core `2c6ba9a`)

The fixture is `fixtures/synthetic/synthetic_tsm_15m_v1.csv`:

- seed 20261104; 80 days × 26 bars, with 3 zero-volume and 2 missing bars;
- csv sha256 `f209b19b…`;
- regenerated byte for byte by a test.

It runs in an isolated governance environment. Its overrides are listed in every report and archive.

| # | Requirement | Result | Evidence |
|---|---|---|---|
| 1 | SMA fixed 5/20 | PASS | Spec ranges are fixed. A run with 10/20 is `REJECTED_SPEC` R02 and recorded. |
| 2 | Runner → Core → Registry | PASS | `QUEUED → RUNNING → COMPLETED`; all 9 stages OK; record carries Core / Agent SHA, CostProfile chain and dataset pins. |
| 3 | Performance report | PASS | 39 valid trips; all metrics present; NOT_EVALUABLE where the sample is too small. |
| 4 | Research result files | PASS | `research_report.md` (13 sections), `metrics.json`, `trades.csv`, `fills.csv`, `rejections.csv`, `equity_curve.csv`, `decisions.csv`. |
| 5 | Independent run archive | PASS | `YYYYMMDD_HHMMSS_<sha7>` (Asia/Taipei); `manifest.json` re-verified. |
| 6 | Rerun → same numbers | PASS | Same run identity `7db723fe…` and same metrics sha256 `e31045807af9…`; trades and fills byte-identical. |
| 7 | No overwrite | PASS | New experiment id and new dir per run; collision gets `-2`; first archive byte-identical after the rerun. |
| 8 | Forced contract failure recorded | PASS | Three cases, each with its reason in the registry and in `stages.json`: (a) per-share commission → `CoreContractGap K18` → `FAILED`; (b) data source snapshot ≠ spec pin → `FAILED`; (c) engine without BENCHMARK_PASS → `REJECTED_CONTRACT C00`, Core not called. |
| 9 | Formal holdout / registry untouched | PASS | sha256 of `registry/*`, holdout locks, controls, confirmations and readiness identical before and after; CI `git diff --exit-code -- registry governance`. |

Synthetic results are engineering evidence only. They say nothing about TSM or SMA. Each run below is commit `637dde1`, 39 valid trips, exposure 0.50, and strategy verdict **無法判定**.

| Cost × | Net PnL | PF | Expectancy | Total return | MDD | Sharpe (ann.) |
|---|---|---|---|---|---|---|
| 0 | 2,164.00 | 1.983 | 55.49 | 2.34% | −1.41% | 4.53 |
| 1 | 1,573.56 | 1.618 | 40.35 | 1.74% | −1.76% | 3.37 |
| 2 | 983.11 | 1.339 | 25.21 | 1.14% | −2.12% | 2.20 |
| 4 | −197.78 | 0.945 | −5.07 | −0.05% | −2.82% | −0.09 |

- The four multipliers count as **1** family trial (same parameters).
- The recommendation is 無法判定 because: synthetic data, assumed costs, and Core status `RESEARCH_ONLY`.

## 6. M3-B2 real-market readiness

`python -m strategy_agent.research readiness` and `... real` both exit `3`. No TSM data was loaded. The gate does **not** relax cost or session limits.

> M3-B.1: Codex M3-A has since reported **NO-GO** (see §10.1). The list below is the original M3-B checklist; the current, synced list is `required_before_go` in `governance/market_readiness.json`.

Missing conditions for IBKR TSM (original M3-B list):

1. Codex M3-A report: timestamp label and `available_at` rule for IBKR TSM 1m.
2. Codex M3-A report: session coverage (RTH vs RTH+ETH). Then `source_confirmations.ibkr_tsm_session_coverage` can be set to CONFIRMED (Q2).
3. Codex M3-A report: 1m → 15m aggregation and session calendar (DST, half days, holidays), with evidence.
4. Codex M3-A report: pinned snapshot / producer SHA and the window allowed for research. These fill `market_readiness.json` (status GO, evidence_ref, approval).
5. A research-lead approved holdout lock `HOLDOUT-TSM-15M-v1`.
6. An APPROVED `ibkr_us_equity` CostProfile with broker evidence.
7. **G18:** Core cannot express IBKR per-share commission ($0.005/share, $1 min) or bps slippage. The M1 adapter used to drop them silently; it now fails closed (K18).
   - Fix A: Core adds per-unit equity commission and bps slippage.
   - Fix B: the research lead approves a Core-expressible restatement.
8. A Core-side IBKR data adapter producing execution columns (buy/sell eligibility, `session_open/close`) for TSM.

## 7. Gaps found in this round

| Id | Where | Finding | Status |
|---|---|---|---|
| G18 | adapter ↔ Core costs | Per-share equity commission, bps slippage, exchange/regulatory fees and one-sided taxes were dropped by `core_cost_profile`. | Fixed in the adapter: now fail-closed `K18`. The Core capability is still missing. |
| R02 | M2 runner | The runner never checked run parameters against the spec ranges, so a FIXED spec could run any parameters. | Research-lead decision (M3-B.1): **mandatory** for every research entry point (CLI, batch, Colab). The M2 low-level `ExperimentRunner` keeps it opt-in for backward compatibility. |

## 8. Archive and Drive

- **Default root:** `MyDrive/Quant_Research/strategy-research-agent/` (Colab mount `/content/drive`).
- **Contents:** `run_info.txt`, `experiment_record.json`, `stages.json`, `final_status.json`, the trade, fill, rejection, equity and decision CSVs, `metrics.json`, `research_report.md`, `snapshot_refs.json`, `dataset_metadata.json` and `manifest.json`. No Parquet files and no secrets.
- **When Drive is not mounted:**
  - the run is written to a local `_local_archive`, marked `LOCAL_ONLY_DRIVE_UNAVAILABLE`;
  - the CLI exits `2`;
  - the notebook's final cell asserts and fails.
- **CI:** the `research-synthetic` job uploads the synthetic archive as an artifact.

## 9. What works now and what does not

**Works now:**

- one fixed, reproducible, governed research run on synthetic data;
- a bounded batch (≤ 10 runs; pre-registered cost multipliers);
- failure recording;
- resume by run identity;
- verified archives;
- Colab Run All.

**Does not work yet:**

- **Real TSM research:** the M3-A gate is closed; G18; the cost profile is not approved; there is no holdout lock.
- **Parameter search:** refused by design.
- **Market regime analysis:** no pre-registered definition.
- **Any 保留 recommendation:** needs real eligible data, approved costs, a `FORMAL_ELIGIBLE` Core status, a sufficient sample and the 2× cost result.

**Next minimal blocker:** the Codex M3-A Market Readiness Report for IBKR TSM. After it, the next blocker is G18 or a restated cost profile.

## 10. M3-B.1 closure (2026-11-04)

Commits: `720b174` (code, tests, notebook) and `3926d22` (notebook pinned to `720b174`). Core pin, Oracle, B01–B12, the original synthetic metrics (sha256 `e31045807af9…`, unchanged) and the formal registry / holdout locks are unchanged.

### 10.1 Market gate synced with Codex M3-A

Source: [`market-research-core@a9e0747` `docs/M3A_IBKR_TSM_READINESS.md`](https://github.com/hh4832/market-research-core/blob/a9e0747a61e5ff5d6a636ff3c657d98600f0ab01/docs/M3A_IBKR_TSM_READINESS.md) and its machine-readable `docs/diagnostics/m3a_ibkr_tsm_readiness.json`.

| Item | M3-A | `governance/market_readiness.json` |
|---|---|---|
| Formal execution / backtest | NO-GO | `status: NO_GO`, `m3a.formal_execution: NO_GO` |
| Bounded read-only diagnostics | SCOPED_GO, research only | `m3a.bounded_data_diagnostics: SCOPED_GO_RESEARCH_ONLY` |
| Snapshot / producer | `20261101_205431_06359faf7ef4_IBKR_FINAL_GATE_FULL` / `06359faf…` | same (also equal to the research spec's pins) |
| 1m timestamp label | UNKNOWN | `bar_label: UNKNOWN` |
| `available_at` | UNKNOWN | `available_at_rule: UNKNOWN` |
| Session | RTH + available ETH requested; per-bar mask UNKNOWN | `session_coverage: RTH_AND_AVAILABLE_ETH`, `per_bar_session_mask: UNKNOWN` |
| Execution eligibility | buy false, sell false, `TIMESTAMP_LABEL_OR_SESSION_UNKNOWN` | same |
| CostProfile | `ibkr_us_equity@0.1.0` DRAFT, file sha `50149cd2…` | same; sha verified against the repository file by a test |
| Core cost support | not lossless (per-share commission, spread, participation) | `core_representable: false`, gap G18 |

The gate now also refuses when `available_at_rule` or `per_bar_session_mask` is unknown, when either execution side is ineligible, when the CostProfile is not APPROVED or not Core-representable, and when M3-A `formal_execution` is not GO. Nothing was changed to GO; `source_confirmations.json` (Q2) stays UNKNOWN because M3-A confirms requested coverage but not a per-bar mask.

### 10.2 R02 mandatory on research entry points

* `parameter_issues` (R02): exact declared names; finite numbers only (no strings / booleans / NaN); within [min, max]; on the step grid; spec constraints hold; unknown search modes rejected; **FIXED runs the declared defaults only, whatever `max_trials` says**.
* `ResearchRunner` always builds the M2 runner with `enforce_parameter_ranges=R02_ENFORCED` (True) and refuses to submit if the flag is off. The CLI has no flag that sets parameters or disables governance; the notebook only calls the CLI.
* M2 compatibility: `ExperimentRunner(enforce_parameter_ranges=False)` stays the low-level default; all M2 tests unchanged.
* Negative tests: batch with smuggled parameters → three `REJECTED_SPEC` R02 records, nothing RUNNING; a FIXED copy with redeclared defaults (10/30) after the 5/20 baseline → same family, `BUDGET_EXCEEDED` B06; R02 patched off → `ResearchContractError`, nothing submitted; junk values and unknown modes → R02.

### 10.3 Colab notebook acceptance

Static checks (`tests/test_m3b_notebook.py`, run in CI): exact 40-hex `AGENT_COMMIT`; Core pin equal to CI; HEAD and clean-tree verified after checkout; credentials only from Colab Secrets, masked in output and never persisted in `.git/config`; Drive mount required; two `--drive` runs with `--summary-out`; required files, manifest sha256, new directory per run, run 1 not overwritten, reproducible metrics, formal registry / holdout fingerprint and clean tree all asserted; real TSM gate must exit 3; no strategy, engine or registry logic in cells.

**Local simulation** (this session, not Colab): every code cell executed in order, cloning `720b174` from GitHub and installing Core `2c6ba9a` from GitHub, with `google.colab` stubbed and a **local folder standing in for `/content/drive/MyDrive`**. All 13 cells passed: 60 tests passed (1 skipped: the Drive-unavailable test, because the simulated mount existed); run 1 `20261104_052409_720b174` and run 2 `20261104_052414_720b174`; same identity and metrics sha256 `e31045807af9…`; all 15 checks true; acceptance record written. Simulation files were removed afterwards.

**Real Google Drive archive: `PENDING`.** This session cannot mount Google Drive. The simulation proves the notebook logic, not that the files reached Drive. The item turns PASS only after the research lead runs Run All in Colab and `MyDrive/Quant_Research/strategy-research-agent/_acceptance/m3b1_colab_*_720b174.json` shows `"result": "PASS"`.

### 10.4 Remaining blockers

| Blocker | Owner |
|---|---|
| Real Drive run of the notebook | research lead (Colab Run All) |
| M3-A evidence: 1m label, `available_at`, per-bar session mask, Core IBKR execution columns | Codex / data owner |
| APPROVED IBKR CostProfile with account evidence | research lead |
| G18: per-share commission, spread, volume participation in Core | Codex |
| Holdout lock `HOLDOUT-TSM-15M-v1` | research lead |

## 11. M3-B.2 production readiness (2026-11-05)

Scope: configuration, R02 on every entry, cost scenarios, six-gate market registry, the Core ↔ Agent contract, Colab and Drive. Not changed: Core, Core pin, Oracle, B01–B12, formal holdout locks, SMA parameters. No TSM run.

| Area | Delivered |
|---|---|
| Research configuration | `schemas/RESEARCH_CONFIG_SCHEMA.json` (v1.0.0) and `research/config.py` with rules RC01–RC12. Every config has a version and a sha256. An edited config must be re-hashed, and the same id+version can never run with a different sha256 (config ledger). The temporal policy is a reference to Core's gate: UNKNOWN is never upgraded. Configs: `configs/research/sma_5_20_tsm_synthetic_v1.json` (Drive), `..._local_v1.json` (CI and offline). |
| Single entry point | `research/entry.py::run_config`, used by CLI `run`, Colab, batch, resume and future automation. There is no parameter flag and no governance switch. Config refusals are written to `_refused_configs/`. |
| R02 | Mandatory in `ResearchRunner`. Invalid parameters in a new config version are submitted and recorded as `REJECTED_SPEC` R02. A same-version edit is refused (RC02). Resume re-enters the runner, so R02 applies there too. The M2 low-level API is unchanged. |
| Cost scenarios | `schemas/COST_SCENARIO_SET_SCHEMA.json` and `research/cost_scenarios.py` (rules CS01–CS07): BASE, LOW, STRESS and CUSTOM. Recorded per scenario: profile id, version, SHA, currency, commission and slippage model, fee basis, assumption and approval status (copied, never upgraded) and the Core compatibility verdict. ALL_IN commission plus a separate fee is rejected as double counting. Core does every calculation. Scenarios count as one family trial. The IBKR set is DRAFT, ASSUMED and INCOMPATIBLE (G18), so it is not executable. |
| Market gate | `governance/market_readiness.json` schema 2.0.0: six gates, pinned to Core PR #3 @ `c1a3661`. A PASS needs pinned Core evidence and a named approval where required. IBKR TSM: DATA_QUALITY PASS; TEMPORAL_SAFETY UNKNOWN; COST_MODEL_COMPATIBILITY, COST_APPROVAL, HOLDOUT_APPROVAL and FORMAL_EXECUTION_ELIGIBILITY FAIL. |
| Core contract | `M3_CORE_AGENT_INTEGRATION.md` and `research/contract.py` (checks CC01–CC07, inside the port; a mismatch makes the run FAILED in the registry). Core APIs not yet released are marked PENDING. |
| Report | Sections are now: hypothesis, mechanism, strategy rules, execution assumptions, backtest results, cost sensitivity, risk metrics, negative evidence, opponent's view, remaining uncertainty, and research decision with separate **Engineering Verdict** and **Strategy Verdict**. A program-generated `cost_sensitivity_<batch>.md` sits next to the batch ledger. |
| Archive | Adds `research_config.json` and `cost_scenario.json`. A write failure leaves `ARCHIVE_FAILED.json` plus a local failure record and no manifest. |
| Colab | The notebook runs the Drive config twice, checks Python ≥ 3.11, and verifies all 14 required files, manifest coverage of every file, no overwrite, reproducibility, one family trial and that the formal registry is untouched. |
