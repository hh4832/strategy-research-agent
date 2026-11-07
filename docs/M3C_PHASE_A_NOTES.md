# M3-C Phase A — Agent Closure Notes

Base: PR #4 HEAD `6309a732470c79600ff37d551adeeb9f194bdc30` (5/5 CI green). Core pin unchanged: `2c6ba9ab7c336e7a4908744abef617e0dce713fd`.

## Fix in this round (presentation only)

The per-run report used to say `cost-sensitivity (2x) not yet run` even when the batch had run BASE/LOW/2x/4x.

- **Run level** (`report.two_x_note`) says exactly one of:
  - this run *is* the 2x scenario;
  - the batch scenario set includes a 2x scenario, see `_batches/cost_sensitivity_<batch>.md`;
  - this invocation ran a single multiplier, so 2x is not evaluated.

  It never claims a comparison it did not compute.
- **Batch level** (`entry.batch_two_x`, summary key `batch_two_x_check`, plus a `## Batch-level 2x-cost check` section in the batch markdown):
  - For each role, it compares the COMPLETED 1x vs 2x net PnL.
  - If both are not present, the result is `NOT_EVALUABLE`.
- Formal performance numbers are unchanged. The local metrics SHAs for all 8 config-run jobs and the legacy synthetic run match those from before the fix.
- Regression tests:
  - `test_batch_two_x_check_and_markdown_separate_levels`
  - `test_run_level_two_x_note_never_claims_or_ignores`
  - extra assertions in `test_config_run_end_to_end_resume_and_one_trial`

## Existing evidence cited (not rerun)

- Colab Run All on real Drive at agent `8f6f798`:
  - acceptance record `_acceptance/m3b2_colab_20261105_113140_8f6f798.json`, 16/16 true;
  - 16 archive folders.
- That evidence covers the code it ran. The reports archived on Drive still carry the old 2x wording, which is expected. This round changes report text only, so no Colab rerun is needed.

## Recorded, not fixed (non-blocking)

- **N1 — 1-ULP float difference in a reference field.**
  - Colab TRAIN BASE `metrics_sha256` `736fe065…` vs local `f0bf3f69…`.
  - The only differing field found is `sample.effective_net_pnl` (767.3029999999999 vs 767.3029999999998), from a plain `sum()` in `evaluation/sufficiency.py`.
  - `trades.net_pnl`, Sharpe, PSR, returns, costs and drawdown are identical.
  - Possible later hardening, outside this round's scope: `math.fsum`, or rounding before the canonical hash.
  - Until then, compare reproducibility across environments by value, not by `metrics_sha256`.
- **N2 — Notebook pin.**
  - The notebook pins `AGENT_COMMIT 8f6f798`.
  - A later Colab smoke test should repin to the final PR #4 HEAD.
  - This is only needed if the merged HEAD is to have its own Drive evidence.

## Phase B

Core M3-A.4 is not delivered: market-research-core PR #3 head is still `c1a3661`, with no newer commit or branch. Per instructions, Phase C is not started. Status: **WAITING_FOR_CORE**.
