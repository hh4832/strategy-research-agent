# Phase 5-C1 Recovery Authorization 0002 — approved record, pending authoritative inclusion

This governance change materializes the exact record reviewed and confirmed by the user. It does not
execute research or merge any PR. The branch is stacked on engineering PR #27, whose parent is #26.
Neither a branch commit nor CI success establishes authoritative governance inclusion.

## Human approval and exact event

- Authorization: `RA-IBKR_TSM-SMA520_P5C1-0002`.
- Lead: `hh4832` (GitHub user ID 269247281, OWNER, User, no GitHub App).
- Approval reference: https://github.com/hh4832/strategy-research-agent/pull/27#issuecomment-6893178421
- Comment created/updated: `2026-11-15T10:46:22Z`; equal timestamps, unedited at verification.
- approved_at: `2026-11-15T10:46:22+00:00` (the actual human comment timestamp; 18:46:22 Asia/Taipei).
- issued_at: `2026-11-16T00:00:00+08:00`.
- expires_at: `2026-11-23T00:00:00+08:00`.
- Interval: `[issued_at, expires_at)`, exactly seven days; signature before issued_at is permitted.
- seq: `4`.
- prev_sha256: `567dafcc44ba0300ac7504b56bdf3aafc46f420e26423e14be633429dbc30d26`.
- record_sha256: `719212edb4f0483def43188f416e1b62069e32c37c3567c9dde808df9053fd69`.
- Approved review-envelope fingerprint: `9700842d43abd79dc78c18b6162c6405a5efbcb564b46115e97b6df76b5f3ffc`.
- Approval body SHA256 (original GitHub CRLF bytes): `624ad6c4de410d922cbf4afcbffe1533ec7630d99a809771d853a8144cb42e06`.

The event is appended to governance/research_authorizations.jsonl using the existing canonical chain
algorithm. Every previous byte is retained. The proposal is separately included at
`governance/proposals/RA-IBKR_TSM-SMA520_P5C1-0002.json`; it remains proposal input, not the approval event.
No caller-supplied chain fields are passed to ChainedLog.append. The returned event must equal the
user-confirmed preview including its exact hash. Record SHA is a content/chain hash, not a digital signature.

## Confirmed disposition and scope

1. Original `RA-IBKR_TSM-SMA520_P5C1-0001 / batch-1` is permanently non-reusable.
2. Preserve original CLAIM, Use/Budget/Task Ledger, Archive and unresolved RECONCILIATION_REQUIRED;
   no invented CONSUMED event, reset or rewrite.
3. Prior execution produced partial Ledger data and may have exposed results. Preserve that exposure
   in the research record; this is not a first unseen-data or demonstrated independent scientific trial.
4. Preserve the twelve archived HTML byte-verification gaps and their risk disclosure. New PREPARE
   must verify the pinned cost-source bytes or remain NO-GO. Historical reconciliation is not declared complete.
5. One new registered Development attempt for technical recovery only, with no parameter search.
6. Fixed SMA5/20, 15-minute IBKR TSM, 2017–2024 Development window plus existing warm-up/boundaries,
   exact dataset/spec/dated costs/PIT/executor. No strategy/Core changes.
7. Holdout NO_ACCESS and Market Gate NO_GO remain unchanged.
8. max_batches=1, SINGLE_EXECUTOR_ONLY, no automatic retry. New Authorization, Request/Task, approvals,
   CLAIM and Drive namespaces must remain separate from the original Batch.

The approval comment is for the Authorization Record only. It is not BIND_AUTHORIZATION, APPROVE_TASK
or EXECUTE permission. No Request/Task or execution approval is created by this governance change.

## Verification

- Re-read actual approval and current main book before mutation; main was
  `df56664a12a54762e222f141b50e337c6dfc5088`, with four records and the expected chain tail.
- Full event Schema, allow-listed Lead, all five seq/prev/hash links, exact confirmed record SHA,
  seven-day canonical contract, unique new ID and non-reused approval reference: PASS.
- Proposal immutable fields and dates equal the approved event after removing proposal/event metadata: PASS.
- Pure authorization-to-request scope mapping and negative tampered-parameter check: PASS. This constructs
  metadata in memory only; no PREPARE, request file, Task or binding service is run.
- Time eligibility: excluded before issued_at, included exactly at issued_at, excluded exactly at expiry: PASS.
  These are metadata checks on this branch, not a claim of current authoritative activation.
- Related authorization, approval-binding, C1 runner and recovery regressions: 106 PASS (CPython 3.11.17).
- Two recovery tests now build a disposable pre-recovery historical prefix before injecting their synthetic
  approval. This avoids duplicating the newly approved real ID in a copied book, preserves all positive
  and negative expectations, and never edits the source book. No production code or cost validation is changed.

## Dependency and authoritative governance requirements

At submission the engineering PRs remain Draft/unmerged:

- #26 targets main.
- #27 targets the #26 branch, head `210d10819ed70b5e92cd22536cc2e46f1ee79ecb`.
- This governance PR targets #27's branch so CI uses the reviewed recovery contract implementation.

Current main does not yet recognize C1_RECOVERY. Humans must review and integrate these dependencies
in the proper order (retarget/rebase as needed), then review/merge this append-only governance change.
The agent performs no merge. If the authoritative book tail changes, the confirmed chain hash cannot
be silently reused or rewritten; reconcile the new chain and obtain explicit confirmation of any changed event.

Only after this exact event and new proposal legitimately enter authoritative main can its full commit
be reported as GOVERNANCE_SHA. Live CLAIM inspection compares protected governance bytes against main;
an unmerged branch commit does not satisfy that condition. Preserve the original execution/CLAIM history.

Even after merge, the authorization is usable only in its time interval and subject to the existing
unused-state/source/approval gates. New formal PREPARE is a separate later operation and has not run here.
No Data Lake verification, Holdout access, PREPARE, PREFLIGHT or EXECUTE is performed in this task.

Current authority status: PENDING_MAIN_INCLUSION. READY_FOR_FORMAL_PREPARE=NO until authoritative
inclusion and validity conditions are verified. Do not substitute this PR branch SHA for GOVERNANCE_SHA.
