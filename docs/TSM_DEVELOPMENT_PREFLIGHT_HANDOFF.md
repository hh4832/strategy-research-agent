# Data preparation handoff — no strategy / no batch

Core PR #4 and Agent PR #5 continue on their existing branches. See Acceptance
§11 and the Core v1.1 development contract. The signed old SMOKE-0001 record,
its0/1 usage and original Holdout lock are preserved. Costs/formal gates and
Phase3 evidence are unchanged.

Agent entry point:

```python
from strategy_agent.research_only.development_snapshot import prepare_development_proposal
result = prepare_development_proposal(
    actual_core_producer_bundle, core_sha=actual_installed_reader_sha,
    output_directory=separate_new_report_directory,
)
```

It performs Core native data/15m/PIT preflight, then exclusively creates
`research_config.proposed.json`, `research_authorization.proposed.json` and
`data_preflight.json`. Proposal remains unsigned; do not call `run`. The function
cannot write formal governance/registry directories. Existing metadata probe
now supports the explicit derived pin and checks full receipt binding.

Required approvals, only if needed:

1. If isolated source is unavailable: research lead reviews/signs the actual
   one-time producer plan (original whole-file bytes plus selected mixed group1
   can include Holdout). The unsigned Core preview is not permission.
2. After a real immutable development bundle passes: research lead personally
   approves its NEW authorization. Do not edit/reuse SMOKE-0001.

No additional strategy stage or parameter research is opened. Actual snapshot
and actual preflight remain pending legal source access; synthetic CI is not
real data evidence.
