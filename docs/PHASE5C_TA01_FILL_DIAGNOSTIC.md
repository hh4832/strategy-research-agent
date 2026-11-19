# Phase 5-C — TA-01 fill-feasibility diagnostic

Status: **tooling only; no diagnostic result exists.** This is not a backtest and produces no performance numbers.

| Authorization | State |
|---|---|
| `RA-IBKR_TSM-TA01_FILLDIAG-0001` | Approved by hh4832, then **PRE_DATA_INPUT_PATH_FAILURE**. Now **retired** in code: refused in PREPARE and RUN whatever exists on Drive. The empty Drive directory is left as is. Record: `docs/phase5c/ta01_fill_diagnostic_0001_disposition.json`. Document file kept byte-identical (its SHA is bound by the comment) |
| `RA-IBKR_TSM-TA01_FILLDIAG-0002` | Approved (pull/35#issuecomment-6994446298), **USED, SUCCEEDED 2026-11-19**. Result `ta01_fill_diagnostic.json` sha256 `683737a4…d30b`. At 100 units, 163/278 exits are not fillable under the participation cap, so P5C-TA-01 is BLOCKED. Retired in code; never re-run or overwritten |
| `RA-IBKR_TSM-TA01Q8_FILLDIAG-0001` | **Draft, NOT approved.** For the **P5C-TA01-Q8** sizing variant (see `docs/PHASE5C_TA01_Q8_VARIANT.md`). Same dataset, window, Holdout NONE, cost schedule, Core, executor, inputs and attempt protocol as 0002; only the strategy (Q8 spec, 8 units) and the labels differ |

## Haw's decisions (2026-11-18)

| Item | Decision | Implemented in |
|---|---|---|
| New authorization | Issue one, scope agreed | `docs/phase5c/ta01_fill_diagnostic_authorization.json` (unsigned) |
| Two-minute window | Do not change Core; the Agent selects the 1m bar | `core_phase4a.ta01_select_fills` (PR #31) |
| Participation cap | Use the volume known before the order | known-volume rule in `ta01_select_fills` (PR #31) |
| Delayed opening auction | Exclude it | `signals.ta01.delayed_open_buckets` (PR #31) |

## What the diagnostic counts

Decisions come from `core_phase4a.ta01_signals`, the same function `execute_batch` uses, so the delayed-open
exclusion is included. Fill facts come from `core_phase4a.ta01_fill_examine`, the same lookup the fill selection uses.
Core's ExecutionEngine is **not** run.

Entries and exits are counted separately. An exit intent exists only after a fillable entry.

| Field | Meaning |
|---|---|
| `intents` | Order intents (the denominator) |
| `known_volume_cap_blocked` | The volume known at t (the completed 1m bar available at t) × cap is below 100 shares |
| `no_tradable_bar_in_window` and `no_tradable_bar_causes` | No Core-eligible bar with volume > 0 and `bar_start` in (t, t+2]. Causes: all missing, all zero volume, both, or other Core ineligibility |
| `intents_with_missing_minute_in_window`, `intents_with_zero_volume_minute_in_window` | Missing minutes and zero-volume minutes, counted separately |
| `chosen_first_candidate`, `chosen_later_candidate_waited` | Which in-window bar would fill |
| `core_full_minute_cap_would_reject` | Core's own check (`execution.py:413`) on the chosen bar's **full-minute** volume. This is a **look-ahead residual**, reported separately and **not** a validated fill constraint |
| `known_volume_to_quantity`, `chosen_bar_volume_to_quantity` | Quantiles of volume ÷ 100 |
| `exit_failures` | Exits with no fill. Each one would fail a whole strategy run |
| `by_decision_time` | Outcomes per decision time (ET) |
| `delayed_open` | Sessions flagged, and their dates |
| `data` | Median daily RTH volume per year (to check the volume unit) and the zero-volume minute share per year |

`assert_no_performance_fields` refuses any output key that looks like a price or a performance field before anything
is written.

## Authorization and run

1. Review and merge this code. The notebook pins an exact reviewed commit.
2. Run `notebooks/Phase5C_TA01_Fill_Diagnostic.ipynb` in `PREPARE` mode. It does not mount Google Drive, reads
   no data and no credential, and prints the exact
   `RESEARCH-APPROVAL v1 … intent=BIND_AUTHORIZATION …` line for this document's SHA.
3. hh4832 personally posts that line on the PR. The line is checked live by `approval.verifier` (V01–V09):
   - author is hh4832 (login and numeric id);
   - not posted via an App;
   - not edited, not expired, not revoked;
   - the line matches this exact document.

   An agent cannot create this evidence.
4. In a fresh Runtime, run `RUN` with the two existing reviewed Phase 5-C1 Drive archives and the typed id. The run:
   - verifies the approval;
   - stages data with the reviewed pins (`c1_runner.stage`, Core preflight);
   - loads Development data only (`load_continuous_native` refuses Holdout overlap);
   - writes `ta01_fill_diagnostic.json` and `MANIFEST.json` once, refusing a second run.

Scope:
- Spec: the frozen TA-01 fixture. Quantity: 100. No tuning.
- Window: 2017-01-01 to 2025-01-01, with warm-up from 2016-10-19.
- Cost schedule: used only for its cap value (0.01).
- Executor: `colab-hh4832-exec-01`.
- No Batch, no PREPARE / PREFLIGHT / CLAIM / EXECUTE.

## Open after the diagnostic

- Core's full-minute cap remains in the strategy execution path because the cost profile and Core are pinned.
  Removing it is a separate decision.
- Whether the volume unit is shares: compare `data.median_daily_rth_volume_by_year` with a public daily volume.
- Normal-open auction placement still depends on the UNKNOWN label convention.

## Attempt protocol v2 (after the 0001 failure)

Root cause of 0001:
- `RECOVERY_ARCHIVE` pointed to a non-existent path.
- The v1 RUN created its one-time output directory **before** checking input paths.
- The notebook did not capture the child process's output, so the error text was not visible in Colab.

`ta01_fill_diagnostic.run_attempt` (order is the safety property):

1. **Checks that do not touch Drive.** Retired ids refused; typed id; **bare** approval URL only (Markdown links,
   brackets, spaces and query strings refused); recovery and preparation paths must equal those pinned in the
   authorization; `drive_root` is fixed.
2. **Prior attempt check.** Refuse if `_attempt_witness/<id>.ATTEMPT_STARTED.json` **or** `<id>/` exists. An empty
   directory counts. Deleting the directory does not help, because the witness remains, and a retired id is refused
   in code.
3. **Pre-data checks.** Live approval verification, scope check, then `precheck_inputs`:
   - original preparation archive: whitelisted JSON metadata and reviewed pins;
   - recovery archive: directory, marker (status PASS) and provenance SHA, `binding.json` SHA, manifest file present
     (existence only).

   No Parquet and no market data are opened here. **Any failure here creates nothing on Drive.**
4. **Durable attempt marker.** The witness file, then `<id>/ATTEMPT_STARTED.json`, both write-once. From here on
   Development data may be read and the authorization is used up.
5. **Data step.** Stage, load and diagnose.
6. **Result.** Success: `ta01_fill_diagnostic.json` + `MANIFEST.json` (status SUCCEEDED). Failure:
   `FAILURE_MANIFEST.json` (status FAILED, sanitized traceback). Everything is write-once, nothing is overwritten,
   and a failed attempt needs a **new** authorization.

The notebook runs every step with captured output. On failure it prints the **complete, sanitized** STDERR: the
token value, `ghp_` / `github_pat_` tokens and `authorization/token/password/secret = …` are redacted.
