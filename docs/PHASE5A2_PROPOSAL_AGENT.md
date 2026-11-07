# Phase 5-A2 — Research Proposal Agent MVP

## Scope and engineering verdict

Base/main at start: `95b6ec52e1b5dc7d618cd8586c6a384e26b4f621` (MVP v1).
Branch: `feature/phase5a2-proposal-agent`, separate worktree. No AGENTS.md was
present. This is design only: natural-language hypothesis → model draft →
deterministic checks → proposal → explicit reference bindings → schema-valid
request awaiting human approval. It never invokes a backtest/orchestrator,
registers a strategy, signs/consumes authorization, changes budget/gates,
accesses Holdout, downloads margin data or reports invented performance.

- Proposal Generation: PASS on offline fixtures; arbitrary ideas preserve UNKNOWN.
- Schema Validation: PASS, new Draft/Proposal schemas plus existing Strategy Spec fragments.
- Research Request Compatibility: PASS for the exact shared Schema and its date-order rule;
  actual Orchestrator/governance end-to-end execution is NOT_RUN.
- Governance Isolation: PASS, no writes outside new proposal/schema/test/doc/example scope.
- Reproducibility: PASS for fixed model completion, source commit, timestamp and prior inputs.
- Overall: SCOPED_GO — offline design/export engineering only. No production LLM,
  verified data availability, executable Strategy Spec materialization or approval claimed.

## LLM seam, input and generation

`proposal.adapters.DraftAdapter` has adapter_id, input_kind and
`draft(hypothesis: str) -> JSON text`. `FakeLLMAdapter` loads marked
SYNTHETIC_FIXTURE completions; an unmatched hypothesis is retained verbatim with
UNKNOWN rules/data, not silently converted to SMA. `FileDraftAdapter` accepts a
completion produced by an externally configured model. No network/LLM SDK,
API key or large dependency is required. A real provider can implement the same
small interface; no production model integration is claimed.

Model output is untrusted. Draft JSON Schema is strict; unknown fields,
self-approval, fake authorization/readiness, nonfinite values and explicit
reported numeric performance claims are rejected, not silently stripped.
Free-text numerical-claim checks catch known patterns, not all possible
misinformation: human review is mandatory and arbitrary text is never executed.
A future model must leave unknowns explicit. Example completions propose causal
hypotheses/questions; they are not evidence that those mechanisms exist.

```python
from strategy_agent.proposal import draft_proposal, FakeLLMAdapter, write_proposal
proposal = draft_proposal(
    "當大盤趨勢較強時，個股動能策略是否有較高的報酬？", FakeLLMAdapter(),
    agent_commit_sha=actual_full_code_commit,
    created_at="2026-11-07T12:00:00+08:00",  # fixed replay clock, not measured run time
)
write_proposal(proposal, "/tmp/new_proposal_bundle")
```

The CLI records the actual clean checkout SHA. Default clock is Asia/Taipei.
The pure API accepts an explicit caller-attested SHA. For a clean checkout:

```bash
pip install -e '.[test]'
printf '%s\n' '當大盤趨勢較強時，個股動能策略是否有較高的報酬？' > /tmp/hypothesis.txt
python -m strategy_agent.proposal draft --input /tmp/hypothesis.txt --output /tmp/new_proposal_bundle
python -m strategy_agent.proposal validate --proposal /tmp/new_proposal_bundle/proposal.json
# Optional real/pre-generated model completion, still untrusted and validated:
python -m strategy_agent.proposal draft --input /tmp/hypothesis.txt \
  --llm-draft /path/to/model_completion.json --output /tmp/new_model_proposal
```

`draft --prior-proposals history.json` accepts an explicit array of prior
checksummed proposal artifacts, read only; this is not a registry/budget update.
No novelty claim is made when prior coverage is unknown. All new proposals are
PROPOSED or PENDING_INFORMATION and always NEEDS_REVIEW. PROPOSED means design
information is sufficiently explicit, not that data or execution is approved.
Source availability can only be UNKNOWN, NOT_VERIFIED, UNAVAILABLE or
DECLARED_AVAILABLE_NOT_VERIFIED; a declared source is never proof of existence/GO.
The agent does not dereference planned source paths or verify market data.

## Deterministic validation

Draft/Proposal schemas: `schemas/PROPOSAL_DRAFT_SCHEMA.json` and
`schemas/RESEARCH_PROPOSAL_SCHEMA.json`. They contain hypothesis, null/direction,
causal mechanism, entry/exit/sizing/horizon, universe/time, source/coverage/PIT,
one-variable change/baseline/side effects, proposed search budget/prior refs,
evaluation plan, ten bias checks, challenger view, provenance and hashes.

Checks include missing mechanisms/rules, strict planned signal availability and
later fills, universe/data/fragment consistency, explicit source/coverage,
individual publication/available_at plans for every extra feature, exactly one
core-variable change, fixed/grid budget consistency and cardinality, range/type/
step/default constraints, parameter/reference resolution, condition arity/time,
entry/exit direction, and explicit parameter-change binding when supplied.
No grid/backtest is run. Review flags identify recent-best-return selection,
missing prior coverage, shared-family variants and overlapping planned data.
Unknown information produces PENDING_INFORMATION, never an invented rule/source.

The typed Strategy Spec fragment reuses existing universe/parameters/signal/
position_sizing/risk_exits/risk_constraints/execution schemas. It intentionally
omits dataset GO, costs, experiment/authorization pins and full metadata.
`strategy_spec_draft.json` is PARTIAL_DRAFT_NOT_EXECUTABLE, full_spec_validated=false.
It is NOT a complete Strategy Spec and cannot be sent to a runner. Existing
registered specs are never modified. Full materialization is a separate reviewed
step; export can reference only an already supplied, compatible full spec file.
The validator checks structural agreement, not a proof that arbitrary natural
language and AST are semantically equivalent. Human rule review is required.

Evaluation plan contains all13 requested dimensions under existing
`QUANT_EVALUATION_PROTOCOL v1.0`: trade count, win rate, payoff, expectancy, PF,
drawdown, tails, costs, regime, annual performance, symbol concentration, holding
duration, parameter robustness. No new decision thresholds or performance values.
All ten bias checks remain NOT_VERIFIED. Challenger questions cover causal
failure, falsification, beta, regime and profit concentration without claiming
unobserved risks were measured.

## Hypothesis/family identity and anti-snooping

Reuses ONLY pure `family.canonical.structure_hash`; never calls
`FamilyIndex.assign()` or writes families/experiments. Names/titles/parameter and
indicator IDs cannot reset the tested typed identity. Family fingerprints ignore
small parameter changes as the existing protocol requires; hypothesis/parameter
fingerprints retain actual defaults/roles. Hypothesis hashes are structural
heuristics, not a proof of arbitrary semantic equivalence. Incomplete designs use
an explicitly labeled fallback; collisions are POSSIBLE_DUPLICATE_INCOMPLETE_DESIGN,
not proof of duplicate completed experiments.

Prior artifacts are checksum/structure validated before comparison. Flags include
DUPLICATE_HYPOTHESIS, SAME_FAMILY_PARAMETER_VARIANT, SAME_FAMILY_DESIGN_VARIANT,
REPEATED_DATA_TEST_RISK and DATA_SNOOPING_SELECTION. Overlap is based on explicit
planned source/date references and is not proof that a backtest was performed.
Research change records and proposed search budget are preserved, not enacted.
No history is deleted, trial count reset, best parameter selected or budget granted.

## Shared Research Request contract — adopted without changes

During development Claude published:

- Commit: `5d088c4de362add925344d8e48b36c2e9d728d96`
- Source: `schemas/RESEARCH_REQUEST_SCHEMA.json`
- Schema version: `1.0.0`
- File SHA256: `1b0530f321c6c71e9eac1bbe5f9c33e3f92e4af4c4ce11b80777c9a94c5324eb`

That exact file is adopted byte-for-byte at the common path, not redefined or
changed. The Proposal records the pin; exporter checks actual schema bytes before
use. Missing schema yields CONTRACT_PENDING (exit3), drift is rejected. Shared
contract tests assert the exact pin/hash and validate generated requests directly
against it. Orchestration source is neither imported nor modified by this agent.

`export-request` needs a PROPOSED design plus an explicit operator bindings JSON.
Bindings use the shared Request's actual field names; no second Request schema
or guessed artefact IDs exist. Provide strategy_family, strategy_spec_ref and
file SHA, market, dataset_ref/snapshot/dataset-manifest SHA, temporal policy,
cost scenario file/hash/scenarios, research_window, requested_change.kind,
execution_mode, research_budget, holdout_policy and requested_outputs.
Optional core_commit defaults to the shared contract's PENDING.
Producer-owned schema_version/request_id/hypothesis/market_mechanism and change
 description are derived from the proposal and cannot be overridden.

```bash
python -m strategy_agent.proposal export-request \
  --proposal /tmp/new_proposal_bundle/proposal.json \
  --bindings /path/to/operator_supplied_existing_references.json \
  --output /tmp/new_request/research_request.json
```

Without complete proposal/bindings, exit2/PENDING_INFORMATION and no request file.
The request ID is deterministic. Shared schema/date ordering are checked; only
JSON files under specs/ and configs/ are read, with path/hash checks. Referenced
full Strategy Spec is validated by the existing validator, must match the proposed
fragment, identity/parameters/dataset refs, and the requested single symbol.
Warm-up/research dates must fit proposal coverage. Multi-symbol request export
is not silently collapsed to the first symbol. Cost reference checks establish
JSON/file integrity only, not broker rates/cost approval. No dataset/Holdout or
raw snapshot file is opened. Files are referenced, not copied into the request.

Authorization always remains PENDING, even if a caller tries to provide an ID.
A syntactically valid request is SCHEMA_VALID_PENDING_HUMAN_APPROVAL, never
EXECUTION_ELIGIBLE. Synthetic proposals can only export SYNTHETIC_TEST (0 requested
batches), not real research. A real-mode request's requested batch is metadata,
not consumption; no request is submitted. Orchestrator governance must separately
resolve every reference/authorization/gate and may block the request.

## Artifacts and tests

Six proposal files: proposal.json, proposal_report.md, strategy_spec_draft.json,
data_requirements.json, bias_risk_checklist.json, challenger_review.md.
Only a successful bound export adds research_request.json plus its provenance
sidecar (contract/proposal/request hashes, pending fields, no submission/consumption).
Proposal.json is the authoritative checksummed design; sidecars are derived views.
Existing directories/files are never overwritten. Formal research evidence,
governance/registry, source code, schemas, tests, specs and configuration paths
cannot be output targets. Fixed input/model/commit/clock/history reproduce bytes.

Fixtures under `fixtures/proposal/`: required Momentum, Mean Reversion and Margin
Financing natural-language ideas plus one fully specified structural momentum
example. All are SYNTHETIC_FIXTURE. The broad ideas remain PENDING_INFORMATION;
only the complete structural design is PROPOSED, still unverified/unapproved.
No financing data was downloaded and no fixture is performance evidence.

Validation:54 new Proposal/Contract tests. Python3.11 and3.12 full no-Core
regression each416 PASS /54 existing optional-Core skips. The existing CI discovers
the tests without workflow changes. Local reused-environment subprocess initially
resolved an older editable checkout; rerunning with the absolute current-worktree
PYTHONPATH corrected that environment issue without touching Evaluator tests/code.
Standard `pip install -e '.[test]'` on this checkout has the correct import root.
Final CI/commit/PR outcome is included in the delivery.

Remaining integrations: production LLM provider; human validation of textual rule
meaning/causal/data claims; verified data/cost/strategy references and full spec
materialization; Orchestrator governance/E2E and personal research authorization.
None is bypassed. This phase stops at design/pending review, not a backtest stage.
