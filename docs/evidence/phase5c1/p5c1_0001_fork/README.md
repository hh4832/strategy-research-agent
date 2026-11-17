# RA-IBKR_TSM-SMA520_P5C1-0001 — sealed fork reference (immutable history)

`FORK_ARCHIVE_REFERENCE.json` in this directory is the authoritative, SHA-pinned pointer to the failed Phase 5-C1
batch-1 of authorization `RA-IBKR_TSM-SMA520_P5C1-0001`. No research data, ledger or archive bytes are copied into
Git: the formal Drive archive already preserves all evidence and its `ARCHIVE_INDEX.json` (status `COMPLETE`,
95 files, sha256 `b391d848…`) pins every file, including the three fork ledgers below.

## Disposition (lead decision, 2026-11-17)

- Preserved as immutable history; never appended to main's registry chains; never renumbered, re-hashed or rewritten.
- The authorization-use fact is not revoked; no budget is restored; no new CLAIM is created.
- Settlement stays `RECONCILIATION_REQUIRED` (failure: `execution failed after commit: ContractViolation`, the K02
  dated-cost identity defect, see `docs/phase5c1/k02_dated_cost_identity_fix.md`). It is NOT a completed study and
  `RECONCILIATION_REQUIRED` is never read as `CONSUMED`.
- Re-execution is barred by the existing remote CLAIM ref (`research-claims/phase5c1/d5ab3c93…/batch-1` → `df56664`);
  `c1_claim.inspect` refuses any existing ref, and the authorization expires 2026-11-19T16:58:13+08:00.

## Why it is a fork

The 0001 executor appended to its own Drive copy of main's ledgers (main at `df56664`). Those records were never
merged; the 0002 recovery later started from the same main prefix. Both continue from the same parent record:

| Ledger | Fork starts at | Parent (main record) | 0001 fork records |
|---|---|---|---|
| research_authorization_uses | seq 2 | `c7fd92df…` | use, run `20261115_023931_209b56f` (`ab1b06a1…`) |
| research_budget_events | seq 7 | main seq 6 head | RESERVED, COMMITTED, EXECUTION_STARTED, RECONCILIATION_REQUIRED (`BR-1679101111bc9a6d4d34ef94`) |
| authorization_bindings | seq 1 | main seq 0 head | `AB-88e998968de14c24804a6590` |

Main carries the 0002 records at those positions (see `docs/phase5c1/registry_reconciliation_0002.md`). Only one
fork can extend a hash chain without rewriting; the 0001 fork is therefore sealed here by reference.

## Verify

Download the Drive files named in `FORK_ARCHIVE_REFERENCE.json` and compare their SHA256 with the manifest and with
the archive's `ARCHIVE_INDEX.json`. `tests/test_phase5c1_registry_reconciliation.py` checks that the manifest is
internally consistent, that each fork's parent is the corresponding record in main's chain, and that main contains
no 0001 ledger record.
