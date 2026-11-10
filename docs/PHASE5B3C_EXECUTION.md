# Phase 5-B3-C — First Real Research Execution (code + notebook; executed only by the research lead)

| | |
|---|---|
| Authorization | `RA-IBKR_TSM-SMA520_P5B3C-0001`: GRANTED_UNUSED, 1 batch, executor `colab-hh4832-exec-01`. Expires 2026-11-17 11:54 Asia/Taipei. |
| Purpose | `PIPELINE_ACCEPTANCE_ONLY`. This is not new statistical evidence about SMA 5/20. |
| Code | `src/strategy_agent/execution/b3c_run.py`. It composes existing Phase 5 components only; there is no new execution logic. |
| Notebook | `notebooks/Phase5B3C_First_Real_Research_Execution.ipynb`. It is run twice, in fresh runtimes. Cell 1 holds every manual approval and fails fast. |
| Agent | Prepares the code only. The agent never runs the notebook, posts a comment, or merges a PR. |

## Flow

Run 1, `STAGE="PREPARE"` (non-consuming):
1. The committed completion `specs/research/b3c/tsm_sma520_p5b3c_completion.json` (built deterministically from spec 0.1.1) goes through A2 `draft_proposal` (status PROPOSED) and the A2 exporter. The result is a PENDING source request.
2. The files are written once to Drive at `00Quant_Research/phase5b3c/<ID>/prepare/`, with a `PREPARE_MANIFEST.json`.
3. The bound request (the source plus `authorization_ref`) and its task id `T-<hash[:20]>` are deterministic, so the notebook prints two lines now:
   - `BIND_AUTHORIZATION` with the source hash;
   - `APPROVE_TASK` with the bound hash and the task id.

   Each line is valid for 48 hours.

**The research lead** checks the hashes and the task id, then posts each line as a separate comment of their own. Then they copy both comment URLs.

Run 2, `STAGE="EXECUTE"`, with both URLs and `I_CONFIRM_SINGLE_BATCH=True`:
1. The prepared files are re-verified byte for byte against the manifest.
2. If a `CLAIM.json` exists on Drive, the run stops. This guards across runtimes: any earlier attempt means RECONCILIATION_REQUIRED, never a re-run.
3. Read-only checks:
   - binding dry run B01–B09, which includes live verification of the BIND comment;
   - live verification of the APPROVE_TASK comment;
   - the runner probe;
   - request identity == runner identity.

   Core renews the session receipt here.
4. The binding is written. The task is submitted and approved with the verified comment. The full activation gate A01–A10 must then pass.
5. `CLAIM.json` is written with O_EXCL. Then `Orchestrator.dispatch` runs: reservation → commit → the existing ResearchOnlyRunner on the real snapshot → evidence verification → evaluation → archive → settle (CONSUMED) → execution record.
6. The full local state is copied to Drive. The evidence bundle (no market data) goes to `docs/evidence/phase5b3c/<run>/`. The B3C acceptance is computed from that evidence.
7. The governed records are pushed to a new branch `research/phase5b3c-result-p5b3c-0001`:
   - `registry/` bindings, bound request and approval evidence;
   - the budget ledger;
   - the uses log;
   - the evidence bundle.

   **The research lead opens and merges that PR. That merge is the formal settlement record (B3C-10).**

There is no retry: a failure after the commit leaves RECONCILIATION_REQUIRED, and the batch stays consumed.

PAT, Colab Secret `GITHUB_TOKEN`, scoped to this repository only:
- Contents: Read and write
- Issues: Read-only
- Pull requests: Read-only

It is held in memory only.

## Repository states (so the result PR stays green and fail-closed)

`b3c.formal_authorization_state()` now also reads the pushed execution records:

| State | Conditions |
|---|---|
| `GRANTED_UNUSED` | No ledger, no binding, no use |
| `GRANTED_USED` | Exactly 1 use, exactly 1 binding, and exactly 1 reservation for this ID. That reservation is in CONSUMED or RECONCILIATION_REQUIRED. No other reservations exist. The ledger hash chain verifies. |
| `UNEXPECTED` | Anything else |

`governed_ledger_ok()` (ledger absent, or present only as that record) replaces the earlier "no ledger" assertions in:
- B2-09
- B3-B `production_state`
- the B3-C preflight
- CI

The preflight reports `EXECUTED_AWAITING_RESULT_ACCEPTANCE` after the run.

The task archive's `run_info.txt` now states the truth:
- `consumed_batches=1` for a committed controlled execution;
- `real_backtest_executed=true` only for REAL_GATED with the real-snapshot runner backend;
- `execution_backend=<kind>`.

## Verification (agent side)

- **Layer B** (`tests/test_phase5b3c_execution_core.py`, Core b16179a): the exact `prepare → execute` code runs end to end with the existing ResearchOnlyRunner and Core receipt renewal on a SYNTHETIC snapshot, using isolated books and fixture approvals. Results:
  - ARCHIVED, CONSUMED, 1 runner call, 1 use;
  - B3C-01..09 PASS, B3C-10 PENDING;
  - the evidence bundle contains no parquet.

  Refusal cases:
  - a second attempt is refused with CLAIM_EXISTS;
  - a wrong task comment stops before any write;
  - a BIND comment for another request is rejected;
  - a tampered request is refused.
- **Repository books** (`tests/test_phase5b3c_execution.py`): run 1 against the real authorization passes G1–G9 and writes nothing. G10 needs the REAL_GATED orchestrator. Fixture evidence on the real books is refused with B08 before any write.
- **Simulated post-run repository** (isolated worktree, never pushed): the records of a governed run are produced through the same code (mock backend, fixture approvals). On that state:
  - `GRANTED_USED`;
  - preflight `EXECUTED_AWAITING_RESULT_ACCEPTANCE`;
  - B3-B `production_state` PASS;
  - full suites green.

## B3C-01..10 after the real run (`b3c_run.acceptance`)

| Gate | Evidence |
|---|---|
| 01 | The authorization record has max_batches 1 and the named executor |
| 02 | The binding trust is PRODUCTION, and the bound hash equals the contract request hash |
| 03 | The contract spec, snapshot and Core equal the authorization pins |
| 04 | The execution is archived; gate A09 passed at precheck and at dispatch |
| 05 | The contract executor is the named executor; uses = 1 |
| 06 | The contract execution mode is REAL_GATED |
| 07 | The runner receipt was renewed by Core (`DEVELOPMENT_RECEIPT_RENEWAL:`) and the receipt hash is recorded |
| 08 | All 4 cost scenarios are present |
| 09 | A task archive exists and the run was on Colab + Drive |
| 10 | The ledger is CONSUMED; the result itself is `PENDING_RESEARCH_LEAD_DECISION` |

Any FAIL means NO_GO, and the batch stays consumed.

## Incident 2026-11-10: in-process evaluation failed C8 (fixed; no re-run)

The real run (`docs/evidence/phase5b3c/20261110_141740_e3ce267`, result branch commit `444cfcd`) has these results:
- It executed once, ARCHIVED and CONSUMED.
- Its metrics, cost breakdown and rejected orders are byte-identical to the Phase 4-A DEV run. Its 52 trades are identical except `experiment_id`.

However, the Phase 4-C evaluator ran **in the dispatching process**, where the runner and Core were loaded. Its C8 governance-isolation check therefore failed, and the recorded `acceptance_status` was FAIL (C1–C7 PASS).

`b3c_run.acceptance` B3C-08 did not look at the evaluator status, so it reported PASS. That was an agent defect.

Fixes:
- The `Orchestrator._evaluate` of a REAL_GATED controlled execution runs the existing evaluator in a fresh interpreter (`evaluation/fresh_process.py`).
- B3C-08 now requires all of the evaluator's C1–C8 checks to PASS, in addition to the 4 scenarios.
- `b3c_run.reevaluate` re-evaluates an already executed run read-only, in a fresh process, from the Drive copy of the runner archive. The archive's MANIFEST sha must equal the execution record. It uses 0 batches and does not re-run anything.
- `notebooks/Phase5B3C_Fresh_Process_Reevaluation.ipynb` runs that re-evaluation for the real run. It installs no Core and appends `…_fresh_reevaluation/` (8 evaluator files + `b3c_acceptance_v2.json`) to the result branch.

The original evidence is never modified.
