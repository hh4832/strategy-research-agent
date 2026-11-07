# MVP v1 — Final Research-only Integration and Release Acceptance

## 1. Actual Git DAG

Checked on 2026-11-07 after `git fetch`. `origin/main` = `d738f7fa38596cb4996d093c81ad3da4bdf8236a`.

```
main  fdbe84f (merge PR #3) ── d738f7f "使用 Colab 建立"  (only on main: adds phase4a-tsm-research-only/notebooks/Phase4A_Real_Snapshot_Probe.ipynb)
        │
        └─ PR #4 M3-B          8f3f6e1  (base main)
             └─ PR #5 Phase 4-A   a650915  (base M3-B)
                  ├─ PR #6 Phase 4-B   00f561a  (base 545e89a, an ancestor of a650915)
                  └─ PR #7 Phase 4-C   d455ed1  (= a650915 + merge ab82919 of 00f561a + Phase 4-C)

release/mvp-v1-candidate
  c9f2a75  merge --no-ff d455ed1 into d738f7f   (parents: d738f7f, d455ed1)
  80e89de  presentation fix + release acceptance module
  09ac0d5  release probe must not import Core; isolate compare() tests      ← RC pin
  (+ docs/notebook commits)
```

| Source | SHA | Ancestor of RC |
|---|---|---|
| main (kept, incl. `d738f7f`) | `d738f7fa38596cb4996d093c81ad3da4bdf8236a` | yes (first parent of `c9f2a75`) |
| PR #4 M3-B | `8f3f6e1` | yes |
| PR #5 Phase 4-A | `a6509151fee5a5c0d103e3860b41fcbc616777a4` | yes |
| PR #6 Phase 4-B | `00f561aa51bce2d4b3974a0c27b46125c15d2209` | yes |
| PR #7 Phase 4-C | `d455ed104817c44d5147dab0bcd460fb4c758635` | yes |
| Phase 4-A real-run Agent | `9439f8c6f53b9c6c7f2f49b5a543ffdbc84f2f2a` | yes |
| Phase 4-C evaluator | `883d14c0059add270d596e4bdc86be46052219ea` | yes |
| Core (real run, other repo) | `b16179a7cc077c36046574769a32c367fa476cae` | recorded only |

How the integration was done:
- One `--no-ff` merge of PR #7's head, which already contains #4, #5 and #6. No commit was merged twice.
- No conflicts, because `d738f7f` only adds a file under a new top-level folder.
- No force push. The #4–#7 branches were not modified.
- `main`'s extra commit `d738f7f` (a Colab-saved copy of the Phase 4-A probe notebook) is kept as is.

Components present: Phase 3 / M3-B (`research/pipeline.py`), Phase 4-A runner (`research_only/runner.py`), Phase 4-B evaluator (`evaluation/universal.py`), Phase 4-C real-archive adapter (`evaluation/phase4c.py`). The real-run evidence (`docs/evidence/phase4a_dev_smoke/20261107_081300_9439f8c/`) and the Phase 4-C acceptance evidence (`docs/evidence/phase4c/20261107_100344_883d14c/`) are both kept.

## 2. Changes on the release candidate (minimal)

| File | Change |
|---|---|
| `src/strategy_agent/evaluation/universal.py` | Presentation only. An AVAILABLE robustness row no longer keeps the NOT_TESTED default reason "required evidence absent". The cost-sensitivity row now reads "cost scenarios supplied on the same signals and positions"; any other AVAILABLE row reads "evidence supplied". Status, evidence, metrics, flags, thresholds and verdicts are unchanged. |
| `tests/test_universal_evaluator.py` | Regression test for the fix. |
| `src/strategy_agent/evaluation/release.py` | Read-only release acceptance: integration manifest, Phase 4-C acceptance at the pinned RC, comparison with the recorded evaluation, governance and archive checks, R1–R6, and a 7-file write-once (O_EXCL) release archive. It never imports an executor or Core; Core presence is probed with `find_spec` only. |
| `tests/test_mvp_v1_release.py` | 8 tests (SYNTHETIC_FIXTURE for the comparison logic). |
| `notebooks/MVP_V1_Release_Acceptance.ipynb` | Thin Colab runner (`MVP-V1-RELEASE-v1`, pin `09ac0d5`). |

Found and fixed during the release run: a fresh-clone run of the full suite in its own venv exposed two problems.
- `release.run_pytest()` imported `market_research_core` to detect Core. This violated the adapter-only Core-import rule, and the two architecture tests that caught it were correct and were not changed.
- The comparison tests depended on no executor module being loaded in the shared pytest process. The process-level C8 check is tested in a subprocess elsewhere, so these tests now isolate it.

## 3. Release regression on the RC pin `09ac0d5`

| Environment | Result |
|---|---|
| CI `test` Python 3.11 (no Core) | 362 passed, 54 skipped |
| CI `test` Python 3.12 (no Core) | 362 passed, 54 skipped |
| CI `core-compat` (Core 699e3ad) | 62 passed |
| CI `research-synthetic` (Core 699e3ad) | 31 + 24 passed |
| CI `phase4a-core` (Core e31c57b) | 106 passed, 2 skipped |
| CI `phase4a-dev-smoke` (Core b16179a) | 113 passed, 4 skipped |
| CI `development-snapshot-preflight` | 1 passed |
| Local fresh clone + own venv, Python 3.13.18, no Core | 362 passed, 54 skipped |
| Local full suite, Core 699e3ad | 456 passed, 3 skipped |
| Local full suite, Core b16179a | 466 passed, 4 skipped, **1 failed**: `test_15_numeric_reproducibility_vs_committed_reference` |

About the `test_15` failure: it asserts that the Phase 3 committed reference was produced on Core `699e3ad`. On any other Core it fails on that assert alone; the numbers are identical, and B01–B12 are identical on e31c57b and b16179a. It passes on 699e3ad, which is where CI runs it (`core-compat`). The test is not modified, because Phase 3 is closed.

CI run on the pin: <https://github.com/hh4832/strategy-research-agent/actions/runs/42334448265>.

## 4. Release acceptance (R1–R6)

`notebooks/MVP_V1_Release_Acceptance.ipynb` performs `Existing Archive → Read-only Evaluator → Comparison → Release Report`. It does not call `ResearchOnlyRunner.run()`, does not re-run the backtest, does not install Core, does not read the Holdout, does not consume any batch, and does not write to the source archive.

| Item | Checked by | Status |
|---|---|---|
| R1 Repository integrity | `integration_manifest()`: every source SHA is an ancestor of the RC; components present; clean checkout at pin; no code change after the pin | PASS (fresh clone at `09ac0d5`); re-verified in Colab |
| R2 Regression tests | CI table above + `run_pytest()` in the Colab runtime | CI PASS; Colab pending |
| R3 Real-archive evaluation = Phase 4-C | `compare()`. For all 4 scenarios: identical metrics, identical envelope `input_sha256`, identical verdicts and flags; C1–C8 identical; payload identical except metadata and the one presentation reason | **PENDING** (needs Drive) |
| R4 Governance isolation | C8 + uses 1/1 + formal NO_GO + holdout lock + 0 batches | **PENDING** |
| R5 Archive integrity | source archive 14 files, MANIFEST pin `328f2cf9…`, unchanged before/after; Drive Phase 4-C evaluation byte-identical to the committed copy and unchanged | **PENDING** |
| R6 Colab portability | the whole notebook completes in a clean runtime opened from GitHub | **PENDING** |

Verdict rule: `MVP_V1_ENGINEERING_GO` only if R1–R6 are all PASS; otherwise `PENDING`, `BLOCKED` or `NO_GO`.
