# M3 Core ↔ Agent Integration Contract

| | |
|---|---|
| Status | **Contract v1.1.0 (M3-C Phase C).** Core M3-A.4 released (`699e3ad`, engineering GO) and integrated by the Agent. Formal market use stays NO_GO. |
| Agent | `hh4832/strategy-research-agent` (PR #4) |
| Core pinned by the Agent | `market-research-core@699e3ad59951bbebb769bb69170be8d9a1a96c01` (BENCHMARK_PASS on this SHA: B01–B12, G16, G17, G14b). Previous pin: `2c6ba9a`. |
| Core M3-A.4 | PR #3 head `699e3ad` (unmerged in Core; pinned by exact SHA). API: `docs/M3A4_TEMPORAL_COST_CONTRACT.md`; diagnostics `docs/diagnostics/m3a4_final.json`; CI run 41967704177. |
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
| Temporal policy | `mrc_adapter.conservative_config` | `MarketSemantics.available_at_policy = CONSERVATIVE_LABEL_AGNOSTIC` → `ExecutionConfig(bar_label=UNKNOWN, available_at_policy=…, temporal_policy=core.CONSERVATIVE_POLICY)`. Bars come from Core `prepare_conservative_bars` (the Agent never re-times a bar). Research model only; it can never be FORMAL_ELIGIBLE. The legacy `OBSERVED` path is unchanged. |

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
| Ledger / per-component costs | `result.ledger` (copy of fills): commission, exchange / clearing / regulatory fees, transaction tax, explicit_cost, slippage / spread / tick-rounding, temporal pins. Archived as received in `fills.csv`. The Agent checks identities only (`adapters/m3a4_contract.py`) and sums `regulatory_fee` into `metrics.costs` (never re-derived). |

## 3. Pins every run must carry

| Pin | Where the Agent records it | Source of truth |
|---|---|---|
| Engine git SHA | Registry `code.engine_git_sha`, `metrics.execution`, archive | Installed Core commit (pip `direct_url` or git). BENCHMARK_PASS is derived from the committed cross-check record. |
| Temporal policy id / version / evidence | Research config `temporal_policy`, run identity, archive `research_config.json` | Core `TemporalPolicy.sha256`, pinned in the config (`temporal_policy.sha256`, RC06) and compared with Core provenance (CC05, P04). |
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
| CC05 | Core temporal policy id **and sha256** equal the config's policy | blocking | blocking |
| CC08 | Conservative policy: label UNKNOWN, scope and status RESEARCH_ONLY | blocking | blocking (never formal) |
| CC06 | Core status known; real-market research needs `FORMAL_ELIGIBLE` | known status | blocking |
| CC07 | Cost multiplier was pre-registered | blocking | blocking |

The adapter's earlier checks also stay in force:

- `validate_core_provenance`: SHA equals the pin; convention; spec, snapshot and producer.
- The CostProfile chain.
- `known_divergences` for capabilities the Core has not demonstrated.
- **Core M3-A.4 field contract** (`adapters/m3a4_contract.validate_conservative_result`, conservative runs):
  - P01–P11 provenance pins: engine SHA and version ≥ 2.2.0, policy id / version / sha recomputed, label UNKNOWN, modeled rule, RESEARCH_ONLY, CostProfile sha / version / fee_mode, component basis / evidence / approval.
  - K00–K03 ledger: the ledger is a copy of fills; fee = commission + exchange + clearing; explicit = fee + tax + regulatory; price adjustment = slippage + spread + rounding; costs are adverse and non-negative; ledger pins; round-trip allocated costs = ledger explicit costs.
  - T01 no look-ahead: signal at modeled availability; the earliest possible fill open is strictly later; the candidate envelope is consistent.
  - R01: every rejection code has been reviewed.
  - Any failure raises `ContractViolation`, and the registry records FAILED.

## 5. Market eligibility (`governance/market_readiness.json`, schema 2.0.0)

A real-market research run starts only if **all six** gates are PASS. A gate counts as PASS only with:

- a pinned `market-research-core/blob/<40-hex>/…` evidence URL, for Core-owned gates;
- a named approval, for TEMPORAL_SAFETY, COST_APPROVAL and HOLDOUT_APPROVAL.

The Agent mirrors Core's documents and **never** writes PASS for a Core gate itself.

| Gate | IBKR TSM (gate_version 2026-11-05.2, Core PR #3 @ 699e3ad) |
|---|---|
| DATA_QUALITY | PASS — FINAL GATE data quality only, not execution approval |
| TEMPORAL_SAFETY | UNKNOWN — Core `CONSERVATIVE_LABEL_AGNOSTIC` is engineering GO as a research model only. The label, availability and closing auction stay UNKNOWN; the model awaits research-owner acceptance. |
| COST_MODEL_COMPATIBILITY | PASS (representability only) — Core 699e3ad expresses per-share commission, bps slippage, SELL regulatory rate and per-unit fees. It is pinned with B01–B12 PASS and the Agent mapping is tested. TIERED commissions and a missing regulatory rate still fail closed. |
| COST_APPROVAL | FAIL — `ibkr_us_equity@0.1.0` is DRAFT and ASSUMED; the IBKR regulatory schedule is PENDING |
| HOLDOUT_APPROVAL | FAIL — no approved `HOLDOUT-TSM-15M-v1` |
| FORMAL_EXECUTION_ELIGIBILITY | FAIL — Core `formal_market NO_GO`; the conservative mode cannot be formal-eligible |

## 6. Fail-closed behaviour

| Situation | Agent behaviour | Where it is recorded |
|---|---|---|
| Market gate not GO | `GatedMarketSource` raises `MarketGateClosed`; CLI `real` and `readiness` exit 3; REAL configs are refused (RC12) | Console; refusal file `_refused_configs/` |
| Config invalid (RC01–RC12, except RC04) | Refused before any registry write | `_refused_configs/refused_*.json` |
| Parameters invalid (R02 / RC04) | Submitted, then rejected by the M2 runner | Registry `REJECTED_SPEC` R02 |
| Cost component not expressible by the **installed** Core | `CoreContractGap K18` (checked before any Core import) | Registry `FAILED`; scenario `core_compatibility INCOMPATIBLE` |
| Core M3-A.4 field contract broken | `ContractViolation` (P/K/T/R checks) | Registry `FAILED`; `stages.json` |
| Engine not BENCHMARK_PASS | Core never called | Registry `REJECTED_CONTRACT` C00 |
| Core output fails CC01–CC07 | `CoreContractMismatch` | Registry `FAILED`; `stages.json` |
| Unknown rejection reason / provenance gap | Adapter raises | Registry `FAILED` |
| Drive not mounted / write fails | `DRIVE_UNAVAILABLE` or `FAILED`; `ARCHIVE_FAILED.json` and a local failure record; no manifest | Archive stage `FAILED`; CLI exit 2 |

## 7. Core capabilities still not available for real-market use

1. **Real IBKR TSM bars under the conservative model.** Core can prepare them, but no real run is authorized: the gate is NO_GO.
2. **Timestamp label and closing-auction attribution.** UNKNOWN; outside Phase 3. The Agent never simulates auction orders.
3. **Approved rates.** Only TEST_ONLY profiles have been executed. There is no IBKR account evidence, no dated regulatory schedule and no Fixed cap verification.
4. **TIERED commissions.** Core does not support them and fails closed. The Agent lists them as unmappable.
5. **Engine self-identification (G11).** The SHA is still caller-supplied; the Agent cross-checks it (CC01, P01).

## 8. Minimal integration after Codex M3-A.4 — executed in M3-C Phase C

Every step was done on Core `699e3ad`. Evidence is in `docs/M3C_FINAL_ACCEPTANCE.md`.

1. **Pin.** `MRC_SHA` (CI), `PINNED` (tests), README and notebook are set to `699e3ad`.
2. **Benchmark.** B01–B12, G16, G17 and G14b PASS with tolerance 0, and BENCHMARK_PASS is derived (`benchmark/results/mrc_699e3ad59951/`).
3. **Cost mapping.** The mapping follows the installed Core's fields. IBKR draft scenarios become COMPATIBLE and stay DRAFT / ASSUMED. Legacy profiles keep the same Core profile hash.
4. **Temporal policy.** The config pins Core's policy sha (RC06). CC05 / P04 compare it with Core provenance, and a wrong sha is refused.
5. **Provenance.** Core's per-component costs and temporal pins are archived as received, and `regulatory_fee` is included in `metrics.costs`.
6. **Gate stays closed.** `readiness --market IBKR_TSM` exits 3. **No TSM run.**

## 9. Change control

- The contract version changes with any change to §1–§6.
- `governance/market_readiness.json` changes only through a reviewed PR. Its `gate_version` is bumped on every sync, and REAL configs must name the gate_version they were reviewed against (RC12).
