# Phase 5-B3-C — Acceptance Specification (B3C-01..10)

This file gives the status of the B3-C gates on the date of this preparation PR. Each gate is in one of three states:

- **NOT_EXECUTED.** This is the status of every gate today. A gate can only become PASS from a real B3-C run that was authorized and archived.
- **prepared_checks.** Non-consuming checks that ran now. They are produced by `python -m strategy_agent.readiness.b3c preflight` and asserted in CI by `phase5b3c-preparation`.
- **Synthetic and mock evidence** from B3-B (`docs/evidence/phase5b3b/20261110_091207_d9afac9`). This evidence is engineering readiness only and never counts as a B3C PASS.

`real_research_pass` is always `false` in this phase.

| Gate | Requirement | Pass criterion (real run) | Today | Prepared check (non-consuming) |
|---|---|---|---|---|
| B3C-01 | Formal Authorization | There is one APPROVE line for the new ID in the merged book. It is active and unexpired, `executor.executor_id` is named, `max_batches = 1`, and `check-proposal` reports ok. | NOT_EXECUTED | The draft passes the schema dry run; the candidate ID is unused in the book; nothing was written to `governance/` |
| B3C-02 | GitHub Approval Binding | Two approvals verified by `verify-approval` against live GitHub: one with `BIND_AUTHORIZATION` and one with `APPROVE_TASK`. The binding record matches the request sha256 and the authorization. | NOT_EXECUTED | The binding CLI is present; there are 0 binding records for the candidate; the 11-step procedure is documented |
| B3C-03 | Dataset / Core / Spec pin | Authorization, bound request, canonical config, session config and disk all agree on: spec `f4f60b44…`, snapshot `…18350b3adba8e28a7e5e`, manifest `8b6fc838…`, Core `b16179a`, costs `bf4f2365…`, temporal `7fa3cdca…` (A04–A06). | NOT_EXECUTED | Manifest fields RESOLVED; the 3 cross-checks PASS; the spec sha on disk matches; the derived run config has a consistent self-hash and pins |
| B3C-04 | PIT / Holdout | The window ends before 2025-01-01. The run reports `holdout_accessed = false`. The highest native timestamp is before the holdout (A09). | NOT_EXECUTED | The lock is present; the window ends before the holdout; holdout read = false |
| B3C-05 | Single executor and budget | The worker, the gate and the authorization name the same executor (A08). 0 of 1 batches used at precheck (A07). There is exactly one reservation. | NOT_EXECUTED | The spent DEV authorization still shows 1 use; there are no eligible authorizations; no budget ledger exists |
| B3C-06 | Real Runner execution | REAL_GATED mode: the `ResearchOnlyRunnerBackend` real snapshot runs once through `claim_execution`. | NOT_EXECUTED | `REAL_EXECUTION_ENABLED = False`; the Runner is DISABLED |
| B3C-07 | Execution receipt | The Core receipt is renewed by the Core (`DEVELOPMENT_RECEIPT_RENEWAL:`). The runner receipt, the execution contract and `raw_run_info` are consistent with the result. | NOT_EXECUTED | none (requires execution) |
| B3C-08 | Evaluator and cost results | All 4 IBKR_PRO_FIXED scenarios are reported. The sample rule (`min_trades 30` → INSUFFICIENT_SAMPLE) is applied, not hidden. | NOT_EXECUTED | none |
| B3C-09 | Archive and reproducibility | The 7 task-archive files are verified. The Drive archive is O_EXCL with `ARCHIVE_INDEX.json`. The evidence ZIP comes from a Colab run (cloud = true, fresh runtime, clean pin). | NOT_EXECUTED | none |
| B3C-10 | Ledger settlement and result acceptance | `settle(recheck)` → CONSUMED, with one line in the uses log. The authorization becomes spent forever. The research lead accepts or rejects the result in writing. | NOT_EXECUTED | none |

**Verdict rule for the future B3-C run:**
- Any FAIL → NO_GO, and the batch stays consumed (no reset).
- If every gate passes but the research lead has not decided → `EXECUTED_PENDING_RESULT_ACCEPTANCE`.
- Only a decision by the research lead closes B3C-10.

What a PASS means is limited: the governed pipeline worked once on real data. It says nothing about whether SMA 5/20 is profitable.
