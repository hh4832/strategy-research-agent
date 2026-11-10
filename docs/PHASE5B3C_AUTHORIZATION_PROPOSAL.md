# Phase 5-B3-C — Authorization Proposal (CANDIDATE, unsigned)

Status: **CANDIDATE for the research lead's review**. This is not an APPROVE.
- Nothing is in `governance/research_authorizations.jsonl` or `governance/proposals/`.
- There is no binding, no GitHub approval evidence, no batch consumed, and no market data or Holdout read.
- The real Runner stays **DISABLED**.

## 1. Proposal

The proposal lives in `docs/phase5b3c/authorization_proposal_RA-IBKR_TSM-SMA520_P5B3C-0001.json`.
- It is generated deterministically by `python -m strategy_agent.readiness.b3c emit-docs`, and CI diffs it.
- It uses the `governance/proposals` format, so it has no `action`, `approved_by`, `approval_ref` or `approved_at`.

| Field | Value |
|---|---|
| authorization_id | `RA-IBKR_TSM-SMA520_P5B3C-0001` |
| scope / contract | `RESEARCH_ONLY_EXPLORATORY` / `research-only-authorization-v1` |
| purpose (notes) | `PIPELINE_ACCEPTANCE_ONLY`: this produces pipeline evidence on the DEV window, not new statistical evidence about SMA 5/20. |
| market / symbol | `IBKR_TSM` / `TSM` |
| strategy | `sma_crossover_tsm_15m_5_20` v0.1.1, parameters fast 5 / slow 20 |
| max_batches | 1 |
| executor | `colab-hh4832-exec-01`, `SINGLE_EXECUTOR_ONLY` |
| issued_at / expires_at | **HUMAN_DECISION_REQUIRED**. issued_at is the time of formal approval; expires_at = issued_at + 7 days. |
| holdout | `HOLDOUT-TSM-15M-v1`, [2025-01-01, 2026-10-17) ET, NO_ACCESS. Blindness is `BLINDNESS_UNVERIFIED` (inherited). |
| development boundary | [2016-10-19, 2025-01-01) ET |
| formal_gate_pin | `market_status NO_GO`, gate_version `2026-11-05.4`, gates_sha256 `11837b36…`. This equals today's `governance/market_readiness.json` (RA10). |

## 2. SHA256 and versions

| Item | Value |
|---|---|
| Strategy spec (v0.1.1) | `specs/research/sma_crossover_tsm_15m_5_20_v0_1_1.json` = `f4f60b447013062324b7dc10aa0a569aebbd542796bef6ccfd2e723d0f5cff34` |
| Parent spec (v0.1.0, unchanged) | `b358416e4764801a6fd80512d665be27cdf85b7b9f5f3c118bd8934e6fd44123` |
| Dataset | `ibkr_tsm_1m_normalized`, snapshot `TSM_DEV_1M_20240529_20240628_18350b3adba8e28a7e5e` |
| Snapshot manifest | `8b6fc838b72ae3015f6eb418c3a77115e53a86f185e05c2249e9dcd58192cfc5` |
| Snapshot parquet | `239d5741d65cc0c7118f618a5ebb7b1dd946f385d843cae151a24edc1e0a8452` |
| Core (producer + runner) | `b16179a7cc077c36046574769a32c367fa476cae` |
| Cost scenario set (IBKR_PRO_FIXED) | `configs/research_only/ibkr_pro_fixed_tsm_research_only_costs_v1.json` = `bf4f2365b42f1ede92954f7a9915fe730f59bd4c73d6e4783007eb07e1749cd3`. Scenarios: BASE, LOW, STRESS, CAT_STRESS. |
| Temporal policy | `CONSERVATIVE_LABEL_AGNOSTIC` 1.0.0 = `7fa3cdcaf501651f749dbc72c31b362fcb9e10cc55920191cef87c1a050059c4` |
| Formal gates | `11837b368e65efddc31f26f7237e881b5e9a80120ed2f22af76dcb4089b9ea32` |
| Research window | [2024-05-29, 2024-06-29) ET (warm-up from 05-29; trading from 06-03) |
| Derived B3-C run config (canonical sha) | `8d03317ed7b6f5f67b33dfabe99d0699c06c2fd2ea402c6bf167b8752efcacfc` (`tsm_sma_5_20_p5b3c_2024_06_dev_18350b3adba8e28a7e5e`) |
| Authorization book head (before) | `9178519601fc837743d8b95dd73097de8073a2c322993b01d39c3e62aca62f31` (DEV authorization, SPENT 1/1) |

## 3. `check-proposal` result

The result is in `docs/phase5b3c/authorization_proposal_check.json`.

| Case | Result |
|---|---|
| As proposed | `ok: false`, problems `HUMAN_DECISION_REQUIRED_LEFT` and `VALIDITY_PERIOD`; schema `NOT_RUN`. This is expected: the validity can only be fixed at approval. |
| Example validity (issued 2026-11-11T09:00+08:00 → expires 2026-11-18T09:00+08:00; an example only, not a decision) | `ok: true`, `problems: []`, schema `PASS` (in memory, fake approver, `written: false`) |

Tests also cover the following:
- RA10 changes are rejected.
- A spent or malformed ID is rejected.
- An executor still set to HUMAN_DECISION_REQUIRED is rejected.
- `max_batches ≠ 1` is rejected.
- Pin changes are rejected.
- An admin `approve` into an **isolated copy** of `governance/` makes the authorization eligible for `colab-hh4832-exec-01` only. It is not eligible for any other runtime, nor after 7 days. The repository books are unchanged.

## 4. The formal authorization PR (made by the research lead, not by an agent)

Branch it from `main`. It should contain exactly:
1. `governance/proposals/RA-IBKR_TSM-SMA520_P5B3C-0001.json`
2. One appended APPROVE line in `governance/research_authorizations.jsonl`
3. `configs/research_only/tsm_sma_5_20_p5b3c_2024_06_dev_18350b3adba8e28a7e5e.json`

```bash
# 1. fix the validity at the moment of decision (issued_at = now; expires_at = +7 days)
python -m strategy_agent.readiness.b3c propose --issued-at "$(date -u +%Y-%m-%dT%H:%M:%S+00:00)" \
    --out governance/proposals/RA-IBKR_TSM-SMA520_P5B3C-0001.json
python -m strategy_agent.readiness.b3c check-proposal --proposal governance/proposals/RA-IBKR_TSM-SMA520_P5B3C-0001.json
#    -> must print "ok": true
# 2. the research lead's own approval (allow-listed approver; validated before append)
python -m strategy_agent.governance.research_authorization_admin approve \
    --proposal governance/proposals/RA-IBKR_TSM-SMA520_P5B3C-0001.json \
    --approved-by hh4832 --approval-ref <URL of this authorization PR>
# 3. canonical B3-C run config (refused unless step 2's APPROVE line exists)
python -m strategy_agent.readiness.b3c derive-run-config --authorization-id RA-IBKR_TSM-SMA520_P5B3C-0001 \
    --out configs/research_only/tsm_sma_5_20_p5b3c_2024_06_dev_18350b3adba8e28a7e5e.json
```

**The authorization takes effect only when that PR is merged**, and it is valid in [issued_at, expires_at). Two consequences:
- Set `issued_at` close to the merge.
- Run B3-C within the 7 days. Once the 7 days pass, the ID is never extended; a new ID is required.

## 5. GitHub steps only the research lead may do

1. Open, review and merge the authorization PR in §4. That merge is the formal APPROVE.
2. In the B3-C run, `approval-line --intent BIND_AUTHORIZATION` prints a line. Post that line as **your own** comment. It ties the Research Request sha256 to this authorization. Then the tools run `verify-approval`, `bind-authorization --dry-run`, `--write` and `validate-bound-request`.
3. Post the second line, `approval-line --intent APPROVE_TASK --task-id <T>`, as **your own** comment. It approves the single task, and only after it can `claim_execution` grant the one batch.
4. Decide whether to accept the research result (B3C-10).

No agent posts, signs or approves any of these. The PAT stays only in Colab Secrets.

## 6. Expected B3C-01..10 acceptance for the single real run

Before execution:
- **B3C-01** passes when the book has this APPROVE, it is active, `check-proposal` reports ok, and `eligible_authorizations()` = [this ID].
- **B3C-02** passes when both approval comments are verified live and the binding record equals the request sha256 and this ID.
- **B3C-03** passes when activation-gate checks A04–A06 and A10 pass, so the pins in §2 are identical everywhere.
- **B3C-04** passes when A09 passes and the run reports `holdout_accessed=false`.
- **B3C-05** passes when A07 and A08 pass: 0 of 1 batches used, and the worker, the gate and the authorization name the same executor.

Execution:
- **B3C-06**: the REAL_GATED `ResearchOnlyRunnerBackend` runs once.
- **B3C-07**: the Core receipt was renewed by the Core, and the runner receipt and execution contract are consistent.
- **B3C-08**: all 4 scenarios are reported, and the sample rule is applied.

After execution:
- **B3C-09**: the 7 task-archive files are verified, the Drive archive is O_EXCL and has `ARCHIVE_INDEX.json`, and the evidence ZIP comes from a fresh Colab runtime.
- **B3C-10**: the ledger is CONSUMED, the uses log has 1 line, the authorization is spent forever, and the research lead decides on the result.

Any FAIL means NO_GO, and the batch stays consumed. See `PHASE5B3C_ACCEPTANCE.md`.
