# Phase 3 handoff — M3-B.2

Decision: **SCOPED_GO**. Synthetic engineering readiness is separate from strategy validity; no real TSM strategy was executed.

## Integration of concurrent work

The PR #4 branch advanced during this task from `d9d3c444fc7748d5d27e6a24227611a5d5f87d50` to `f9c2ecf` / implementation `a082c85`. A non-fast-forward push was rejected. The final merge preserves the published configuration schema, `research.entry.run_config`, scenario management, report improvements, six-gate registry and existing tests. The earlier local alternative config API is superseded; it is not a second supported implementation. Both histories are retained without force-pushing.

Additional hardening retained from this task:

- Resume validates R02 before cached success, refuses disabled R02, and uses the runner's identity by default.
- Cached success requires a verified archive, passing final status and matching run identity. Tampered/missing evidence is rerun.
- Manifest checks exact file membership, safe names, byte counts and every SHA.
- Archive allocation and final manifest failures produce explicit local failure records. Failed finalization cannot remain verified/ARCHIVED.
- Exact reviewed Core report/diagnostic copies are retained under `docs/evidence/`; their source commit is `c1a3661d127c7139ee86d824d1a380edf3d2e114`. Execution stays pinned to `2c6ba9ab7c336e7a4908744abef617e0dce713fd`.

## Usage and artifacts

Schema: `schemas/RESEARCH_CONFIG_SCHEMA.json`. Cost scenario schema: `schemas/COST_SCENARIO_SET_SCHEMA.json`. Config examples and BASE/LOW/STRESS/CUSTOM restrictions are in `configs/research/` and `configs/cost_scenarios/`. Core contract: `M3_CORE_AGENT_INTEGRATION.md`. Report: existing 13-section module, separate engineering and strategy verdicts, program-calculated results only.

```bash
python -m strategy_agent.research run \
  --config configs/research/sma_5_20_tsm_synthetic_local_v1.json \
  --run-root /tmp/m3b2-env --archive-root /tmp/m3b2-archive --batch-id review
```

Add `--resume` to resume the verified batch. Configuration mutation requires a new content SHA and version; invalid R02 parameters persist REJECTED_SPEC. All costs remain computed by Core. Config windows x cost scenarios share one family parameter trial. Formal Registry/Holdout and cost approvals are not changed.

## Validation and remaining blockers

Final merged-tree results are recorded in `docs/evidence/m3b2_validation.json`. Earlier local-only 148-test evidence is superseded by those results; it does not describe the concurrent implementation.

Actual Colab Run All: **PENDING**, not executed in Google's runtime. Actual Google Drive archive: **PENDING**, no mount available here. Static notebook tests and local write simulations are not Drive evidence. TSM formal execution: **NO-GO**; timestamp/auction remain UNKNOWN. New Core temporal/cost adapter acceptance: **PENDING** until the reviewed M3-A.4 release and minimum integration tests in the contract. No Oracle redesign, historical FINAL GATE rerun, real TSM strategy, or formal Holdout mutation.

GitHub API returned **Forbidden**; live CI/PR state remains unverified. Push/remote commit verification uses the existing Git proxy and is reported separately. No merge of PR #4 into main is performed.

Notion Phase 3 update: **PENDING**, no Notion connector is available. This file is prepared handoff content, not evidence of a Notion update.

Next owners: research lead runs the pinned Colab notebook and retains its Drive acceptance JSON; data owner supplies bounded timing/session/auction evidence; Core owner completes M3-A.4 before the minimum integration acceptance; research lead supplies governed cost and holdout approvals. Stop feature work after this bounded delivery.

Notebook Agent pin: `f4ab50712010159d7daf5d2bd10b2724958a9bf8` (merged implementation and 156-test acceptance). The following closure commit only updates the notebook pin and delivery evidence.
