# M3 Core ↔ Agent Integration Contract

| | |
|---|---|
| Status | **Contract v1.0.0 (M3-B.2).** Agent side implemented; Core side partly **PENDING** on Codex M3-A.4 |
| Agent | `hh4832/strategy-research-agent` (PR #4) |
| Core pinned by the Agent | `market-research-core@2c6ba9ab7c336e7a4908744abef617e0dce713fd` (BENCHMARK_PASS: B01–B12, G16, G17, G14b) |
| Core under development | PR #3 `feature/m3a-ibkr-tsm-readiness`, head `c1a3661` at the time of writing. **Not merged and not pinned.** |
| Rule | Core owns time rules, cost calculation and fill simulation. The Agent owns governance, configuration, signals, evaluation, reports and archives. Neither side re-implements the other. |

This document fixes **what** passes between the two sides and **how the Agent fails closed**. It does not predict names of Core APIs that have not been released. Every such item is marked **PENDING**.

## 1. Agent → Core (inputs)

The only call site is `strategy_agent.adapters.mrc_adapter.MrcBacktestPort.run` → `market_research_core.ExecutionEngine().run(bars, orders, cost_profile=, config=)`.

| Item | Produced by (Agent) | Notes |
|---|---|---|
| Bars | Data source (`research.data`) | Core bar columns, checked by `check_core_bars`. Real-market bars must come from **Core's own data adapter**: the Agent never re-times bars, fills gaps or edits eligibility. Only the synthetic fixture is built by the Agent. |
| Strategy signals | `signals.*` pure functions | Decision time is the bar's `available_at`. The adapter turns decisions into target-position MARKET orders (TIF DAY). R02 has already been checked. |
| CostProfile | `mrc_adapter.core_cost_profile` | Field mapping only. A component Core cannot express raises `CoreContractGap K18` (G18) — it is never dropped. |
| Cost multiplier | Cost scenario (`research.cost_scenarios`) | Must be one of the spec's pre-registered `sensitivity_multipliers`. Core applies it. |
| ExecutionConfig | `mrc_adapter.core_config` | Initial cash, bar label / session semantics from `MarketSemantics`, the `same_timestamp_open_assumption` mapping, and dataset / spec / commit pins. |
| Temporal policy | **PENDING** | The Agent will pass Core's policy id (e.g. `BAR_END_STRICT_LATER` as documented in Core PR #3) through whatever field Core releases. Until then no real-market run is possible: the TEMPORAL_SAFETY gate is UNKNOWN. |

## 2. Core → Agent (outputs)

These are taken unchanged into `BacktestReport` (`core_result_to_report`). The Agent never recomputes them.

| Item | Use |
|---|---|
| fills (time, price, side, qty, fee, tax, contract) | Archive `fills.csv`, evaluation |
| round trips (valid / exclusion-invalidated) | Evaluation; invalidated trips are reported apart from performance |
| rejections (order, bar, reason) | Archive `rejections.csv`; reasons are mapped by `map_reason`, and unknown reasons fail closed |
| equity curve | Risk metrics, Sharpe, exposure |
| open positions at end | Report and evaluation |
| provenance | Contract checks (§4), registry record `metrics.execution`, archive `snapshot_refs.json` |
| Ledger / per-component costs (regulatory fee, component basis and approval) | **PENDING** — Core PR #3 adds these, but the release and pin are pending. The Agent will archive them as received and never re-derive them. |

## 3. Pins every run must carry

| Pin | Where the Agent records it | Source of truth |
|---|---|---|
| Engine git SHA | Registry `code.engine_git_sha`, `metrics.execution`, archive | Installed Core commit (pip `direct_url` or git). BENCHMARK_PASS is derived from the committed cross-check record. |
| Temporal policy id / version / evidence | Research config `temporal_policy`, run identity, archive `research_config.json` | Core gate document (pinned URL in `governance/market_readiness.json`). Core provenance field: **PENDING**. |
| CostProfile id / version / SHA | Scenario record, `metrics.execution.agent_cost_profile_sha256`, Core `cost_profile_sha256` | Agent profile file (hash) and Core's hash of the submitted profile. Both are kept. |
| Dataset snapshot / producer SHA | Spec `data_requirements`, config `dataset`, Core provenance | Data producer; mirrored in the market gate registry |
| Spec / config SHA | Registry `spec.spec_sha256`, archive | Agent |
| Agent SHA | Registry `code.strategy_agent_sha` | Agent checkout. Formal runs require a clean tree. |

## 4. Agent-side validation of Core results (`research/contract.py`)

These checks run inside the instrumented port, **before** the M2 runner records the run. A mismatch therefore becomes `FAILED` in the registry, with the reason recorded.

| Check | Rule | SYNTHETIC | REAL_MARKET |
|---|---|---|---|
| CC01 | Provenance `engine_git_sha` equals the benchmarked engine | blocking | blocking |
| CC02 | Agent CostProfile SHA equals the scenario or spec pin; Core returned its own profile hash | blocking | blocking |
| CC03 | Dataset snapshot and producer equal the spec pins | blocking | blocking |
| CC04 | Spec SHA present in provenance | blocking | blocking |
| CC05 | Core temporal policy equals the config's policy | PENDING (pinned Core has no field) | **blocking — fails until Core releases the field** |
| CC06 | Core status known; real-market research needs `FORMAL_ELIGIBLE` | known status | blocking |
| CC07 | Cost multiplier was pre-registered | blocking | blocking |

The adapter's earlier checks also stay in force:

- `validate_core_provenance`: SHA equals the pin; convention; spec, snapshot and producer.
- The CostProfile chain.
- `known_divergences` for capabilities the Core has not demonstrated.

## 5. Market eligibility (`governance/market_readiness.json`, schema 2.0.0)

A real-market research run starts only if **all six** gates are PASS. A gate counts as PASS only with:

- a pinned `market-research-core/blob/<40-hex>/…` evidence URL, for Core-owned gates;
- a named approval, for TEMPORAL_SAFETY, COST_APPROVAL and HOLDOUT_APPROVAL.

The Agent mirrors Core's documents and **never** writes PASS for a Core gate itself.

| Gate | IBKR TSM (gate_version 2026-11-05.1, Core PR #3 @ c1a3661) |
|---|---|
| DATA_QUALITY | PASS — FINAL GATE data quality only, not execution approval |
| TEMPORAL_SAFETY | UNKNOWN — timestamp label, observed availability and closing auction UNKNOWN; `BAR_END_STRICT_LATER` pending owner acceptance and M3-A.4 |
| COST_MODEL_COMPATIBILITY | FAIL — pinned Core cannot represent per-share commission or bps slippage (G18). PR #3 adds them but is unpinned. |
| COST_APPROVAL | FAIL — `ibkr_us_equity@0.1.0` is DRAFT and ASSUMED |
| HOLDOUT_APPROVAL | FAIL — no approved `HOLDOUT-TSM-15M-v1` |
| FORMAL_EXECUTION_ELIGIBILITY | FAIL — Core decision `formal_execution NO_GO`; both sides ineligible |

## 6. Fail-closed behaviour

| Situation | Agent behaviour | Where it is recorded |
|---|---|---|
| Market gate not GO | `GatedMarketSource` raises `MarketGateClosed`; CLI `real` and `readiness` exit 3; REAL configs are refused (RC12) | Console; refusal file `_refused_configs/` |
| Config invalid (RC01–RC12, except RC04) | Refused before any registry write | `_refused_configs/refused_*.json` |
| Parameters invalid (R02 / RC04) | Submitted, then rejected by the M2 runner | Registry `REJECTED_SPEC` R02 |
| Cost component not expressible | `CoreContractGap K18` | Registry `FAILED`; scenario `core_compatibility INCOMPATIBLE` |
| Engine not BENCHMARK_PASS | Core never called | Registry `REJECTED_CONTRACT` C00 |
| Core output fails CC01–CC07 | `CoreContractMismatch` | Registry `FAILED`; `stages.json` |
| Unknown rejection reason / provenance gap | Adapter raises | Registry `FAILED` |
| Drive not mounted / write fails | `DRIVE_UNAVAILABLE` or `FAILED`; `ARCHIVE_FAILED.json` and a local failure record; no manifest | Archive stage `FAILED`; CLI exit 2 |

## 7. Core capabilities not yet available to the Agent (PENDING)

1. **Temporal policy in the execution API and in provenance.** PR #3 shows `available_at_policy` in `ExecutionConfig`. It is unreleased, so the Agent does not code against it yet.
2. **Per-share commission, bps slippage, half spread, sell regulatory fee and volume participation cap** in CostProfile, with component basis, evidence and approval. These are in PR #3 and unreleased.
3. **Real IBKR TSM bars** with `bar_start` / `bar_end` / `available_at` / session and side eligibility populated. Today they are UNKNOWN and both sides are ineligible.
4. **Closing-auction attribution.** UNKNOWN; the Agent never simulates auction orders.
5. **Engine self-identification (G11).** The SHA is still caller-supplied; the Agent cross-checks it (CC01, C05).

## 8. Minimal integration test after Codex M3-A.4 is released

Run once, in this order. If any step fails, the pin is not updated.

1. **Pin.** Set `MRC_SHA` (CI) and `PINNED` (tests) to the released Core SHA.
2. **Benchmark.** Run `python -m benchmark.harness.mrc_crosscheck`: B01–B12, G16, G17 and G14b must PASS with tolerance 0, so that `engine_benchmark_status` derives BENCHMARK_PASS.
3. **Cost mapping.** Map the new Core cost fields in `core_cost_profile`, field mapping only, and shrink `unmappable_cost_components` accordingly. Then load `configs/cost_scenarios/ibkr_tsm_draft_v1.json`: scenarios must turn `core_compatibility COMPATIBLE` while staying `DRAFT` / `ASSUMED`. Nothing may be upgraded.
4. **Temporal policy.** Pass the config's policy id through Core's released field. Then CC05 must PASS on a synthetic run with that policy, and FAIL with a different id.
5. **Provenance.** Archive Core's per-component cost provenance as received. A synthetic run with a regulatory fee must show Core's component, and the Agent must not recompute it.
6. **Gate stays closed.** `readiness --market IBKR_TSM` still exits 3 until the gate registry records PASS with pinned evidence and approvals. **No TSM run in this test.**

## 9. Change control

- The contract version changes with any change to §1–§6.
- `governance/market_readiness.json` changes only through a reviewed PR. Its `gate_version` is bumped on every sync, and REAL configs must name the gate_version they were reviewed against (RC12).
