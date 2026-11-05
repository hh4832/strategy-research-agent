# M3 Core–Agent integration contract v1.0.0

Status: **PENDING new Core API binding and minimal acceptance**. This document defines Agent obligations and evidence, not new Core API names. Core source and formulas are unchanged.

## Reviewed versions and dependency

Agent work continues PR #4, `feature/m3b-research-runner`, based on `d9d3c444fc7748d5d27e6a24227611a5d5f87d50` (contains M3-B `49172fa880c25a1de4711ddbbcc98cd0cc1071ce`). Execution stays pinned to Core `2c6ba9ab7c336e7a4908744abef617e0dce713fd`, matching existing benchmark evidence and CI. No Oracle redesign or historical FINAL GATE rerun.

Latest Core PR #3 reviewed: `c1a3661d127c7139ee86d824d1a380edf3d2e114`. Exact report and diagnostic copies are in `docs/evidence/`; `governance/market_readiness.json` records their SHA-256 and source URLs. The report advertises `BAR_END_STRICT_LATER` and expanded cost representation. That is source evidence, **not Agent compatibility acceptance**. We have not upgraded the execution pin, rerun Oracle, or approved a market gate.

## Ownership and exchange

| Direction | Required content / responsibility |
|---|---|
| Agent → Core | Approved immutable dataset reference and producer SHA; Core-provided execution frame; pure strategy decisions at available information times; strategy Spec and config pins; explicit position sizing, initial capital, registered temporal policy, cost profile and scenario multiplier. |
| Core → Agent | Fills, rejection reasons, ledger/equity, execution eligibility and provenance binding the submitted inputs to engine SHA, temporal policy and cost profile pins. Agent aggregates results; no alternative execution engine or fee calculation. |
| Temporal evidence | Policy ID, version, SHA and evidence reference; observed timing and conservative simulation assumptions remain separate. UNKNOWN source label/publication/auction evidence stays UNKNOWN. |
| Cost evidence | Agent and mapped Core profile IDs, versions and SHAs; currency; commission and slippage models; component versus all-in fee basis; each assumption, approval and compatibility state. DRAFT/ASSUMED is never upgraded by metadata. |
| Dataset evidence | Snapshot `20261101_205431_06359faf7ef4_IBKR_FINAL_GATE_FULL`, producer `06359faf7ef4b8ff702a7ab1a6d07521d3b68f28`; frame/content SHA from actual reads; no invented data or timestamp conversion. |

`research.contract.validate_handoff` checks an **Agent-normalized evidence envelope**, exact pin agreement, payload presence/types, provenance binding and formal eligibility. Its keys are Agent evidence names, not a prediction of future Core signatures. Binding raw new Core results into this envelope is PENDING. The current synthetic adapter retains its existing result validation. This envelope is not used to assert formal compatibility today.

## Fail-closed boundaries

All six independent gates require GO with evidence: DATA_QUALITY, TEMPORAL_SAFETY, COST_MODEL_COMPATIBILITY, COST_APPROVAL, HOLDOUT_APPROVAL and FORMAL_EXECUTION_ELIGIBILITY. Existing readiness approval, exact snapshot, permitted window, per-side eligibility and actual loader are also required. A data-quality GO cannot authorize execution. No formal TSM run is implemented or started here.

Core's new policy cannot establish that an UNKNOWN source timestamp labels a completed bar. A conservative policy is an explicit research assumption requiring owner acceptance. Closing-auction attribution is UNKNOWN; no auction fill is authorized.

The pinned adapter rejects nonrepresentable fees (K18). Latest PR documentation suggests representation exists upstream, but Agent mapping, lossless conversion, component provenance and approval acceptance remain PENDING. Do not silently drop unsupported fees or combine all-in commission with exchange/regulatory components.

## Research configuration and scenarios

`schemas/RESEARCH_CONFIG_SCHEMA.json` v1.0.0 defines JSON configuration. `configs/research/synthetic_sma_v1.json` is an executable fixture example. Canonical SHA-256 uses sorted compact UTF-8 JSON with only top-level `sha256` set to null; NaN/infinity are invalid. Spec, profile and temporal-policy pins are checked separately. Change config version and regenerate SHA for a new setting; every archive retains the complete setting and its pin. The existing experiment registry preserves each submitted run.

Run from repository root:

```bash
python -m strategy_agent.research synthetic --run-root /tmp/m3b2-env \
  --config configs/research/synthetic_sma_v1.json --batch-id review \
  --summary-out /tmp/m3b2-summary.json
```

The example's in-sample interval is fixture warmup/context only; no fitting or parameter search is performed. Validation runs the fixed registered strategy. The runner rejects changes to registered sizing, capital, risk limits, dataset, windows or timing rather than pretending they were applied. R02 parameter deviations reach the runner and persist REJECTED_SPEC. CLI, batch and resume share this runner; no governance override flag exists. M2's low-level default remains unchanged.

BASE/LOW/STRESS are explicit registered multipliers, applied solely by Core to the same strategy/profile. They count as one family parameter trial. CUSTOM is a schema-supported separately pinned profile: it must be available, approved, representable and match a separately registered Spec. Unsupported or unapproved custom profiles are rejected, not executed or restated. Real-market profiles remain DRAFT. The synthetic APPROVED profile is test-only and explicitly ASSUMED; it never enters formal governance.

## Minimum acceptance after Codex M3-A.4

1. Review an exact new Core commit and its public signatures/provenance. Bind policy ID/version/SHA and profile mapping using that API; do not guess fields.
2. On tiny synthetic bars, verify pin agreement and reject missing/mismatched dataset, engine, temporal or cost evidence.
3. Verify a decision at availability cannot fill on a forbidden equal-boundary bar; UNKNOWN timing and missing session masks fail closed. Core owns all boundary calculations.
4. Pass separately approved synthetic profile components through Core; compare returned ledger/fill costs and provenance using existing fixtures, including all-in/component ambiguity and unsupported components. No Agent-side fee formula.
5. Verify per-side eligibility, volume-cap rejection, failure records and unchanged formal registry/holdout; run the existing minimum adapter contract checks. Do not rerun historical FINAL GATE or redesign Oracle.
6. Only after review, update the execution SHA, compatibility evidence, CI and notebook pin together. Market/holdout/cost approval remain independent external gates.

## External evidence still needed

Real Colab Run All and Drive archive: PENDING. No Drive mount exists in this cloud session. Local archive verification is not Drive evidence. New Core integration and formal TSM execution: PENDING / NO-GO respectively. GitHub API returned Forbidden, so live CI and PR state cannot be asserted from this session. Notion connector is unavailable; a handoff draft is provided locally.
