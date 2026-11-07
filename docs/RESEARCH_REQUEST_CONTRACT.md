# Research Request contract v1.0.0 (Phase 5 shared interface)

Schema: `schemas/RESEARCH_REQUEST_SCHEMA.json`. Validator: `strategy_agent.orchestration.request.validate`.

A request **references** governed artefacts by id and full hash. It never carries their content and never asserts their status. Strategy Spec, Research Authorization and the Evaluator input remain the authoritative contracts; the request only points at them.

| Field | Required | May be PENDING | Notes |
|---|---|---|---|
| schema_version | yes | no | `1.0.0` |
| request_id | yes | no | `RR-…` |
| hypothesis, market_mechanism | yes | no | free text, recorded verbatim, never interpreted |
| strategy_family | yes | no | `family_id`, `strategy_id`, `version`, `parameters` |
| strategy_spec_ref + strategy_spec_sha256 | yes | no | repo-relative path + sha256 of the file bytes |
| market | yes | no | `market_id`, `symbol` |
| dataset_ref | yes | no | `dataset_id`, `producer_commit_sha` (40-hex) |
| dataset_snapshot_id + dataset_manifest_sha256 | yes | no | the DATASET manifest, never a research-archive MANIFEST |
| core_commit | yes | **yes** | 40-hex only; abbreviations rejected; PENDING ⇒ governance BLOCKED |
| signal_time_rule | yes | no | `CONSERVATIVE_LABEL_AGNOSTIC` + version + policy sha; signal time = Core `modeled_available_at` |
| execution_time_rule | yes | no | `NEXT_ELIGIBLE_BAR_OPEN` |
| cost_profile_ref | yes | no | scenario-set path + sha256 + scenarios |
| research_window | yes | no | `warmup_start ≤ start < end`, explicit offsets, half-open |
| requested_change | yes | no | `kind` + description (+ optional `baseline_request_id`) |
| execution_mode | yes | no | `SYNTHETIC_TEST` · `ARCHIVE_REPLAY` · `RESEARCH_ONLY_REAL` (disabled in 5-A1) |
| replay_source | iff ARCHIVE_REPLAY | no | `run_id` + archive `MANIFEST.json` sha256 from an independent record |
| research_budget | yes | no | `batches_requested`: 0 for replay/synthetic, 1 for real |
| authorization_ref | yes | **yes** | `{authorization_id}`; validity/scope/budget read from the governed books; PENDING ⇒ BLOCKED |
| holdout_policy | yes | no | `access` must be `NO_ACCESS`; `lock_id` |
| requested_outputs | yes | no | must include `RESEARCH_DECISION` |

Security properties, each enforced by `additionalProperties: false` and covered by tests:
- No field can grant authorization, mark a gate as passed, unlock a holdout, override a budget or force a run.
- Hashes are always full-length and lower-case.
- Only `core_commit` and `authorization_ref` may be `PENDING`. A PENDING request can be validated and recorded, but it can never become `APPROVED`.

Change control: changes to this schema go through a dedicated contract PR coordinated with Phase 5-A2 (Codex). The orchestrator must not change it unilaterally.

Examples: `docs/examples/research_requests/`.
