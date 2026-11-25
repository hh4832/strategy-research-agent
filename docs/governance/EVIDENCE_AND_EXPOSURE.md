# Evidence index and dataset exposure (Phase 5-C2, C2-4 W-7 and C2-5)

There are two append-only, hash-chained logs. Both use `registry.chain.ChainedLog`, and the CI `append-only` job guards
them, because they live under `registry/`. The read-only helpers are in `strategy_agent.governance.research_ledger`;
`python -m strategy_agent.governance.research_ledger` verifies both logs.

## `registry/evidence_index.jsonl` — where every research artifact is

There is one line per artifact: proposal, authorization use or result, feasibility result, performance run and case
disposition. Later C2 PRs add review, interpretation and authorization lines. Each line has the following fields:

| Field | Meaning |
|---|---|
| `entry_id`, `case_id`, `stage` | `stage` is one of HYPOTHESIS, PROPOSAL, REVIEW, AUTHORIZATION, FEASIBILITY, PERFORMANCE, INTERPRETATION or DISPOSITION |
| `authorization_id`, `approval_url` | The authorization and the approval comment behind it |
| `result` | `location` (`repo:` or `drive:`), `kind`, `sha256` and run identifiers. The SHA-256 is the one recorded at run time; `null` when it was never recorded |
| `agent_sha`, `core_sha` | The code that produced the artifact. `null` when the repo does not hold it; the reason is in `notes` |
| `evidence_level` | `EXPLORATORY_ONLY` or `CONFIRMATORY` |
| `repo_refs` | Repository files pinned by SHA-256. `verify()` fails if one of them changes, so only reference immutable records |
| `notes`, `recorded_by`, `backfilled` | Free text, the writer, and whether the line was recorded after the fact |

## `registry/dataset_exposure.jsonl` — which data has been seen

There is one line per authorized **data read**, whether a diagnostic or a performance run:
- `dataset_id`, `partition` (DEVELOPMENT or HOLDOUT) and `window`;
- the case, the authorization and the run;
- `use` (DIAGNOSTIC or PERFORMANCE) and `evidence_index_id`.

A failed attempt that read no data, such as TA-01 FILLDIAG-0001, has an index line but no exposure line.

**Exposure is derived from these lines; it is not declared:**
- A window that overlaps any line is `EXPOSED`.
- Evidence on an EXPOSED window is at most `EXPLORATORY_ONLY` (`max_evidence_level`).
- `check_declared_exposure` reports a proposal or authorization whose declared exposure or requested evidence level
  contradicts the log. C2-7 wires it into the proposal checks.
- Any line in a locked Holdout window, or with partition HOLDOUT, is reported by `holdout_problems()`. This framework
  version has no Holdout unlock process.

## Backfill (2026-11-25)

| Case | Index lines | Exposure lines |
|---|---|---|
| SMA520 | 3 performance runs: the 2 one-month windows and the full Development window | 3 |
| P5C-TA-01 | Proposal, 6 fill diagnostics and the disposition | 5 diagnostics (FILLDIAG-0001 read no data) |

Result:
- **TSM Development 2016-10-19..2025-01-01 is EXPOSED.**
- **The Holdout 2025-01-01..2026-10-17 has no line.**

Every backfilled SHA is cross-checked by tests against the repository records it came from:
- `research_authorization_uses.jsonl`;
- `research_budget_events.jsonl`;
- `ta01_case_disposition.json`.

**Evidence missing:** some fields are `null` because the repository never recorded them.
- The agent SHA of the C1 run `20261117_000458_a4cc87e` (only the suffix `a4cc87e` is known).
- The agent SHAs of four TA-01 diagnostics. They are in the `ATTEMPT_STARTED.json` files on Drive.
