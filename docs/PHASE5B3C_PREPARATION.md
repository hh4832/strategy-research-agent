# Phase 5-B3-C — First Real Research: Preparation

Status: **PREPARATION ONLY**. This PR does not:
- create or approve an authorization;
- bind a Research Request;
- consume a budget;
- enable the Runner;
- read market data or the Holdout;
- change `market-research-core`.

The real Runner stays **DISABLED**: `REAL_EXECUTION_ENABLED = False`, and no eligible authorization exists. Every B3C gate is `NOT_EXECUTED` (see `PHASE5B3C_ACCEPTANCE.md`).

## 1. Authorization draft (unapproved)

| | |
|---|---|
| File | `docs/phase5b3c/authorization_draft.json` (deterministic; CI re-emits it and diffs) |
| Generator | `python -m strategy_agent.readiness.b3c emit-docs` |
| Schema dry run | `docs/phase5b3c/authorization_draft_schema_dry_run.json`: PASS, in memory, fake approver, `written: false` |
| Location | `docs/` only. It is **not** in `governance/research_authorizations.jsonl` and **not** in `governance/proposals/`. |

The raw draft is **not admissible**. `validate_events` rejects it, and `check-proposal` reports `HUMAN_DECISION_REQUIRED_LEFT`.

| Item | Draft value |
|---|---|
| Authorization ID | proposed `RA-IBKR_TSM-SMA520_P5B3C-0001` — **HUMAN_DECISION_REQUIRED** |
| Market / symbol | `IBKR_TSM` / `TSM` |
| Strategy spec | `specs/research/sma_crossover_tsm_15m_5_20_v0_1_1.json`, sha `f4f60b44…5cff34`, version 0.1.1 (parent 0.1.0 unchanged) |
| Snapshot | `TSM_DEV_1M_20240529_20240628_18350b3adba8e28a7e5e` |
| Manifest | `8b6fc838…92cfc5` |
| Core | `b16179a7cc077c36046574769a32c367fa476cae` |
| Research window | 2024-05-29 .. 2024-06-28 ET. Warmup starts 05-29, trading runs 06-03 .. 06-29 exclusive, interval `[start,end)`. |
| Costs | `IBKR_PRO_FIXED`, scenario set `bf4f2365…`, four scenarios: BASE, LOW, STRESS, CAT_STRESS |
| max_batches | proposed **1** — **HUMAN_DECISION_REQUIRED** |
| issued_at / expires_at | **HUMAN_DECISION_REQUIRED** (proposed validity: 7 days) |
| executor.executor_id | proposed `colab-hh4832-exec-01` — **HUMAN_DECISION_REQUIRED**. Consistency is `SINGLE_EXECUTOR_ONLY`. |
| Holdout | `HOLDOUT-TSM-15M-v1` (2025-01-01 → 2026-10-17), `NO_ACCESS`. The window ends before the holdout. |
| Archive | Drive `MyDrive/Quant_Research/strategy-research-agent/<YYYYMMDD_HHMMSS_<git>>` (immutable). Repo copy goes to `docs/evidence/phase5b3c/<run_id>/`. |
| Approval requirements | `approval_requirements` in the draft (research lead only; separate PR; admin tool) |

`RA-IBKR_TSM-SMA520_DEV-18350b3adba8e28a7e5e` stays SPENT 1/1. It is never reset or reused, and `check-proposal` rejects it.

**What this research can show.** It produces pipeline evidence on the already-evaluated DEV window. It is not new statistical evidence that SMA 5/20 works.

## 2. Binding procedure (from formal authorization to an executable task)

All tools already exist. The only new code is `readiness/b3c.py`, which provides `check-proposal`, `derive-run-config` and `preflight`. No step is performed in this PR.

| # | Who | Step | Command |
|---|---|---|---|
| 1 | Research lead | Decide every HDR. Save `governance/proposals/<ID>.json` in a **separate** PR. Check it. | `python -m strategy_agent.readiness.b3c check-proposal --proposal …` → `ok: true` |
| 2 | Research lead | Create the formal authorization. It takes effect only after the PR is merged. | `python -m strategy_agent.governance.research_authorization_admin approve --proposal governance/proposals/<ID>.json --approved-by hh4832 --approval-ref <URL>` |
| 3 | Research lead | Commit the canonical B3-C run config. It is refused unless `<ID>` is an APPROVE in the book. | `python -m strategy_agent.readiness.b3c derive-run-config --authorization-id <ID> --out configs/research_only/tsm_sma_5_20_p5b3c_2024_06_dev_18350b3adba8e28a7e5e.json` |
| 4 | B3-C notebook | Generate the Research Request (A2) | `python -m strategy_agent.proposal export-request --proposal P.json --output R.json --bindings B.json` |
| 5 | Tool (prints only) | Compute the request hash and the approval line | `python -m strategy_agent.binding approval-line --request R.json --intent BIND_AUTHORIZATION --authorization <ID>` |
| 6 | Research lead | Post that line as **their own** GitHub comment. This is the formal evidence. | GitHub UI |
| 7 | Tool | Verify the approval against the live GitHub source | `… binding verify-approval --request R.json --intent BIND_AUTHORIZATION --authorization <ID> --evidence-ref <URL>` |
| 8 | Tool | Bind the authorization to the request: `--dry-run` first, then `--write` | `… binding bind-authorization --request R.json --provenance R_provenance.json --proposal P.json --authorization-id <ID> --evidence-ref <URL>` |
| 9 | Tool | Validate and inspect | `… binding validate-bound-request --request B.json`; `… binding inspect-binding --bound-sha <sha>` |
| 10 | Notebook | Verify the executor: the worker, the gate `executor_id` and the authorization `executor.executor_id` must be equal (A08); `eligible_authorizations()` must list exactly `<ID>` | |
| 11 | Notebook | Verify the remaining budget: 0 of 1 used and no reservation (A07) | |
| 12 | Research lead | Approve the task (`intent=APPROVE_TASK` comment). After that, `Orchestrator.approve(task_id, evidence_ref=<URL>)` runs, and only then can `claim_execution` grant the single batch. | |

## 3. B3-C execution plan

**Preflight** (non-consuming, cell 1 fails fast):
- Drive mount;
- fresh runtime;
- commit pin and Version Sync;
- Colab Secret present (never printed);
- Core `b16179a` installed;
- `python -m strategy_agent.readiness.b3c preflight`;
- steps 7–11 above;
- snapshot bytes match the manifest;
- Core receipt renewal (`research_only.development_run.session_config`);
- `RealActivationGate` A01–A10 all true.

Any failure stops the run before a reservation exists.

**Execution** (exactly one batch):
1. `reserve(request_file_sha256=…)`.
2. `claim_execution`.
3. `ControlledRunnerAdapter(REAL_GATED, ResearchOnlyRunnerBackend(canonical_config_path=…))`.
4. `execute(doc, out_dir, reservation_id=, task_id=, attempt_id=)`.

There is no retry. A crash leaves the ledger in RECONCILIATION_REQUIRED and never in a reset state.

**Post-execution:**
1. `verify_execution_evidence(expected_mode=REAL_GATED)` and `task_archive_problems`.
2. `record_evidence_verified`.
3. `settle(recheck=)` → CONSUMED, plus one line in the uses log.
4. Archive to Drive (O_EXCL, `ARCHIVE_INDEX.json`).
5. Run the B3-C acceptance → evidence ZIP.
6. The research lead reviews the evaluator and cost results and decides B3C-10.

The B3-C notebook is written in B3-C itself, after the authorization exists. It follows the cell-1 manual-approval rule.
