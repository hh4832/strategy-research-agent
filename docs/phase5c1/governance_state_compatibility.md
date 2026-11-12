# C1 governance state compatibility

Baseline: main 2d724930128e225723b56b9ec86282107414afd8. Authorization PR #23 remains at 998189f36973e59e74016df46c99b69544ff780e; its two approved files are not modified by this fix.

## Root cause matrix

Run 43188152066: 15 failed jobs, 10 successful jobs, 1 cancelled (generic 3.11 fail-fast). Local generic reproduction on the four-record checkout: 25 failed, 723 passed, 78 skipped. The job list/failed steps were obtained through the GitHub API; this classification combines those steps with local reproduction and code inspection, not a claim of access to full job logs.

A = stale fixed count; B = test reads mutable formal state rather than an isolated fixed fixture; C = different authorization contracts conflated; D = actual validation gap.

| Failed job | Job ID | Class | Root cause |
|---|---|---|---|
| phase4a-core | 131080437001 | A | Historical test caps/allow-list only covers the old three-record book. |
| phase5b3c-execution (3.11) | 131080437015 | C | Acceptance/readiness depends on governed_ledger_ok, B3-C state or unscoped eligible_authorizations; legitimate C1 changes those global assumptions. |
| phase5b3c-preparation (3.11) | 131080437019 | A, B, C | Historical whitelist; copy/filter of a mutable book removes a middle line and breaks seq/prev links; B3-C rejects legitimate C1 as unknown. |
| phase4a-dev-smoke | 131080437034 | A | Historical test caps/allow-list only covers the old three-record book. |
| test (3.12) | 131080437072 | A, B, C | 25 local generic failures: fixed count, mutable production validation fixtures, malformed filtered fixture chain, unscoped eligibility and B3-C classification. |
| phase5b2-controlled (3.12) | 131080437146 | C | Acceptance/readiness depends on governed_ledger_ok, B3-C state or unscoped eligible_authorizations; legitimate C1 changes those global assumptions. |
| phase5b3b-real-execution (3.12) | 131080437206 | C | Acceptance/readiness depends on governed_ledger_ok, B3-C state or unscoped eligible_authorizations; legitimate C1 changes those global assumptions. |
| phase5b3c-execution (3.12) | 131080437257 | C | Acceptance/readiness depends on governed_ledger_ok, B3-C state or unscoped eligible_authorizations; legitimate C1 changes those global assumptions. |
| phase5b2-controlled (3.11) | 131080437285 | C | Acceptance/readiness depends on governed_ledger_ok, B3-C state or unscoped eligible_authorizations; legitimate C1 changes those global assumptions. |
| phase5b3a-readiness (3.11) | 131080437301 | C | Acceptance/readiness depends on governed_ledger_ok, B3-C state or unscoped eligible_authorizations; legitimate C1 changes those global assumptions. |
| phase5b3a-readiness (3.12) | 131080437354 | C | Acceptance/readiness depends on governed_ledger_ok, B3-C state or unscoped eligible_authorizations; legitimate C1 changes those global assumptions. |
| phase5c1-integration (3.12) | 131080437455 | A, B, C | Shared historical/B3-C failures; missing-authorization test uses production root where C1 is now present (AUTHORIZATION_NOT_UNIQUE assumption). |
| phase5b3b-real-execution (3.11) | 131080437559 | C | Acceptance/readiness depends on governed_ledger_ok, B3-C state or unscoped eligible_authorizations; legitimate C1 changes those global assumptions. |
| phase5b3c-preparation (3.12) | 131080437566 | A, B, C | Historical whitelist; copy/filter of a mutable book removes a middle line and breaks seq/prev links; B3-C rejects legitimate C1 as unknown. |
| phase5c1-integration (3.11) | 131080437978 | A, B, C | Shared historical/B3-C failures; missing-authorization test uses production root where C1 is now present (AUTHORIZATION_NOT_UNIQUE assumption). |

The cancelled Python 3.11 generic job has no independent failure; it follows matrix fail-fast. Successful append-only and full original-file/hash-chain validation show no damage to the supplied approval.

D inspection: `_authorization()` formerly selected raw JSON rows without verifying the full hash chain/schema/uniqueness. B3-C's raw state reader similarly did not first validate the full book. These are hardened here. The observed seq/prev errors came from tests deleting the middle B3-C record while retaining C1, not from the original human-approved ledger. UNKNOWN_AUTHORIZATIONS is a safe rejection caused by an outdated classifier, not a reason to accept arbitrary unknown records. AUTHORIZATION_NOT_UNIQUE in the C1 test is an obsolete no-authorization fixture assumption, not duplicate real approval.

## Minimal behavior changes

- Read-only contract identities use the exact reviewed immutable proposal fields (canonical fingerprints), including ID, scope, contract, dataset/spec/cost pins, executor and one-batch limit. Only already-human-variable time/evidence fields and B3-C proposal metadata are excluded; validity must remain seven timezone-aware days. These fingerprints identify existing contracts; no strategy/data/cost pins are changed.
- Verify the entire chain, schema, approver and duplicate-ID rules before classification. Unknown production contracts and malformed known contracts fail closed, even if another valid record exists. Synthetic test contracts retain their existing isolation-marker boundary.
- Classify B3-C independently of a valid C1 record. Eligibility has an explicit optional authorization ID; B3-C readiness/acceptance uses its own ID. Shared execution gates bind to the prepared ID, and manifest kind must agree with B3-C/C1 ID. C1 is never a replacement for B3-C and vice versa.
- Standing eligibility remains independent of spent budget so reconciliation of already consumed B3-C remains possible. Actual new work remains blocked by A07/RA11; expiry/issued time, A08 executor, A09 Holdout and RA10 NO_GO pins remain enforced.
- Fixed historical fixtures replace tests that delete lines from whichever production ledger happens to be checked out. No approved record, signature, timestamp or digest is rewritten. No count-only change from 3 to 4.

## Acceptance boundary

The new isolated regression suite covers three records, valid fourth C1, both cross-contract directions, unknown ID, duplicate ID, seq/prev/hash corruption, expiry/not-yet-issued, executor, budget, Holdout NO_ACCESS and formal NO_GO. Test events are explicitly synthetic temporary fixtures, never generated through admin approve and never added to production books.

This fix does not authorize, merge, execute research, read Holdout or consume a real Batch. The human lead must merge the fix first, then PR #23 can receive main without changing the two original approved files, followed by fresh PR CI. Until then READY_FOR_HUMAN_MERGE for #23 is NO and AUTHORIZATION_EFFECTIVE is NO.

## Local validation (four-record integration checkout)

The approved Proposal/Ledger were copied byte-for-byte only into a disposable integration worktree; PR #23 and its branch were left untouched.

| Suite | Python | Passed | Failed | Skipped |
|---|---|---:|---:|---:|
| Generic without Core, before final two added negative cases | 3.12.14 | 762 | 0 | 78 |
| C1 / authorization / Phase4A / B3-C non-consuming + 17 contract regressions, pinned Core 50ea3bfa42df7708dd3a1b21f17798c206be054e | 3.11.17 | 142 | 0 | 5 |
| Same pinned-Core suite | 3.12.14 | 142 | 0 | 5 |

The five skips are the pre-existing tests requiring the historical B3-C authorization to be UNUSED; the checked-in B3-C history is already GRANTED_USED. All C1 integration tests and the new contract regressions executed. The generic Core-dependent skips are covered separately by the existing pinned-Core CI jobs; no CI test is disabled by this change.

Approved Proposal SHA256 remains `7c7b4dd7bd6e55e647453a8026f03b5107dbcd69c16d2ace13c53b78102808b0`; approved Ledger SHA256 remains `6272e0785d8e5bab4ad1195b05f128bd962214d3c48c2d7d0636ea3c19965264`.

## Isolated Core fixture compatibility

The first fix-CI iteration exposed the existing synthetic B3-C Core E2E ID `RA-IBKR_TSM-SMA520_B3CTEST-0001`. It must not be treated as a production B3-C/C1 authorization. The fixture now uses `ISOLATED_TEST_PHASE5B3C_PREPARE_MANIFEST` and its fixed synthetic clock. Only that exact fixture ID, with the existing isolation marker and synthetic label, is admitted by the test path. Production manifest kinds still reject it, test manifests reject C1/unknown IDs and unmarked roots, and full chain/schema/approver/revocation checks remain required. No test expectation or production refusal is removed.

Additional historical Core B3-C E2E + contract isolation suite on Core b16179a7cc077c36046574769a32c367fa476cae: Python 3.11.17 and 3.12.14 each 22 passed, 0 failed, 0 skipped. The fixture clock is fixed for preview, evidence verification and activation; production uses its normal current clock. Existing historical-record reads remain possible after expiry, while eligibility/A01/RA02 prevent new expired execution.
