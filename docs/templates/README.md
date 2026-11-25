# Research lifecycle templates (Phase 5-C2)

| Gate | Artifact | Template / schema | Evidence-index stage |
|---|---|---|---|
| Hypothesis + Proposal | proposal + A2 `research_proposal.json` | `AI_RESEARCH_PROPOSAL.md` (PR-C) + `schemas/RESEARCH_PROPOSAL_SCHEMA.json` | PROPOSAL |
| Review | review record | `REVIEW_RECORD.md` | REVIEW |
| Authorization | authorization + approval comment | `schemas/RESEARCH_AUTHORIZATION_SCHEMA.json` (with `amendment`, PR-D) | AUTHORIZATION |
| Feasibility | diagnostic result (write-once on Drive) | case-parameterized diagnostic (PR-G) | FEASIBILITY |
| Performance | run + evaluator output | formal path (PR-G) + `universal.evaluate` | PERFORMANCE |
| Interpretation | interpretation | `INTERPRETATION.md` | INTERPRETATION |
| Final disposition | case disposition JSON + review record | format of `docs/phase5c/ta01_case_disposition.json` | DISPOSITION |

Every artifact is recorded in `registry/evidence_index.jsonl`; every data read in `registry/dataset_exposure.jsonl`
(`docs/governance/EVIDENCE_AND_EXPOSURE.md`). Case files live under `docs/cases/<case_id>/`.
