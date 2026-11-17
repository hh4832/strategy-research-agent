# Phase 5-C1 registry reconciliation — RA-IBKR_TSM-SMA520_P5C1-0002 (settled execution records)

Status: prepared on branch `fix/phase5c1-registry-reconciliation` from main `8a60be9`; awaiting lead review.
Separate from the C3/C6 evaluator remediation (`fix/phase5c1-c3-trade-integrity`). No research logic, archive,
Core, holdout, authorization or approval record is changed. No batch is consumed by this change.

## Execution being recorded

| Item | Value |
|---|---|
| Authorization | `RA-IBKR_TSM-SMA520_P5C1-0002` (APPROVE seq 4, max_batches 1) |
| Task / request | `T-f2bf75d20083ea17aeff` / `f2bf75d20083ea17aeff2e55982340d434887c5e25789747c7eeddb64d11730b` |
| Run | `20261117_000458_a4cc87e` |
| Output MANIFEST.json | `579283777cdff1864ced571de79fa322449353efcbb40e55f65f49d0e2860a69` |
| Remote CLAIM | `refs/heads/research-claims/phase5c1/8b9e3762…/batch-1` → `8a60be9` (single ref) |
| Source of records | EXECUTE governance state `phase5c1_recovery/attempts/RA-IBKR_TSM-SMA520_P5C1-0002/batch-1/work/state/` (read-only) |

## Records carried over (byte-exact suffix of the EXECUTE state; nothing generated or re-chained)

| Ledger | main seq | new seq | head before → after |
|---|---|---|---|
| `registry/research_authorization_uses.jsonl` | 0–1 | 2 (P5C1-0002, run above) | `c7fd92df…` → `cbdd8fe2…` |
| `registry/research_budget_events.jsonl` | 0–6 | 7–12 RESERVED, COMMITTED, EXECUTION_STARTED, EXECUTION_COMPLETED, EVIDENCE_VERIFIED, CONSUMED | `3a3d56a8…` → `fc955313…` |
| `registry/authorization_bindings.jsonl` | 0 | 1 (`AB-194175cae2e389734e8d03c8`) | `ab69f924…` → `3ea970f6…` |

New content-addressed files referenced by binding seq 1 (SHA verified): `registry/bound_requests/f2bf75d2….json`
(file sha `bf366a51…` = binding `bound_request_file_sha256` = budget `request_file_sha256`) and
`registry/approval_evidence/24877b73….json` (file sha = name = binding `approval_evidence_sha256`).
All other governance/registry files are byte-identical between main and the EXECUTE state (21 files, incl.
`research_authorizations.jsonl`, `holdout_locks.jsonl`, `market_readiness.json`, proposals, cost profiles).
Precedent: `444cfcd` (P5B3C-0001 execution records: uses +1, bindings +1, budget events +7).

Reproduce / re-verify (idempotent; a second run adds nothing):

    python -m strategy_agent.governance.registry_reconciliation --state <EXECUTE state> \
      --authorization-id RA-IBKR_TSM-SMA520_P5C1-0002 --run-id 20261117_000458_a4cc87e \
      --task-id T-f2bf75d20083ea17aeff --request-sha256 f2bf75d2… --output-manifest-sha256 57928377… \
      --claim-refs <git ls-remote origin 'refs/heads/research-claims/*'> --remote-claim REMOTE_CLAIM.json [--apply]
    python -m strategy_agent.governance.append_only --base 8a60be9b25e446bb7b492115a90a9a84c02e396d

Note: budget `approval_evidence_sha256` (`5b560755…`) is the execution-stage approval evidence hash, a different
object from the binding's BIND evidence file (`24877b73…`); main's P5B3C-0001 records follow the same convention
(`6217af3c…` vs `fe0a53b0…`). It is constant across all six 0002 budget events.

## RA-IBKR_TSM-SMA520_P5C1-0001 — sealed fork (lead decision 2026-11-17)

0001's failed batch-1 (`ContractViolation`, settlement `RECONCILIATION_REQUIRED`) wrote one use, four budget events
and one binding into its own Drive copy of main's ledgers. They fork from the same main records that 0002 extends
(uses seq 2, budget seq 7, bindings seq 1), so they are NOT appended, re-numbered or re-hashed. They are sealed by
SHA-pinned reference in `docs/evidence/phase5c1/p5c1_0001_fork/` (formal archive `ARCHIVE_INDEX.json`
`b391d848…`, status COMPLETE, pins every fork ledger byte). The use fact is not revoked, no budget is restored,
no CLAIM is created, and 0001 is not a completed study.

## Governance code touched (review separately)

- `src/strategy_agent/readiness/b3c.py` `execution_records()`: the legacy B3-C classifier counted every non-B3-C
  reservation as an anomaly. It now RECOGNIZES (not authorizes) a C1 / C1_RECOVERY reservation only when it is the
  single reservation of that authorization and is CONSUMED with exactly one use, or RECONCILIATION_REQUIRED with at
  most one use (`c1_recognized_reservations` records the state). Anything else still counts against B3-C. It never
  reads RECONCILIATION_REQUIRED as CONSUMED and grants no eligibility. Precedent: `9a0d6bb`.
- `tests/test_phase5b3c_preparation.py` fixture: "pre-B3C prefix" now cuts at the first B3-C record (a true chain
  prefix) instead of deleting B3-C lines, which broke the chain once a later record follows B3-C.
