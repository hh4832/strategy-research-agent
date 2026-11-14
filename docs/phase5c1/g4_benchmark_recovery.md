# C1 G4 authentic benchmark recovery

This is engineering evidence and execution infrastructure. It does not authorize a strategy run, read Holdout, change Market Gate NO_GO or consume the C1 Development Batch.

## Root cause and authentic recovery

G4 reads `<artifact_root>/benchmark/results/mrc_50ea3bfa42df/crosscheck.json`. The previous C1 PREPARE neither contained nor packaged an exact-50ea record. Records for other Core commits are ineligible. Successful earlier CI and official dated-cost source reviews are not substitutes for this record.

After repository/history/local/CI searches and the research lead's Drive search found no available original, the lead explicitly authorized ONE real engineering benchmark. The unchanged Agent harness `benchmark.harness.mrc_crosscheck` was run once on clean Agent commit `3db1d75d9d6f9de1320f3e4542e24dd84d551713` and the actually installed clean Core commit `50ea3bfa42df7708dd3a1b21f17798c206be054e`, Python 3.11.17, using existing deterministic engineering cases and independent oracle. No formal Development data, Data Lake validation, Holdout or authorization operation was involved.

Original run: `2026-11-14T07:52:52+08:00` (Asia/Taipei). Local immutable evidence directory:

`/tmp/c1-g4-benchmark-recovery/20261114_075252_460022_5fa61ad6f3394dd49e567a5353bc4d51`

Result: B01–B12 **12 PASS / 0 FAIL / 0 NOT_EXECUTABLE**. G16 and all six checks PASS; G17 PASS; G14b PASS. The unchanged `engine_benchmark_status()` derives BENCHMARK_PASS. The harness exit code alone was not used as proof, because its verdict covers only B01–B12.

Raw original `crosscheck.json` SHA256:

`2c74d6d2fccd4233892f64b1447ceb2133a261b733f52aa1e8069d25df2f7ffc`

Pinned evidence manifest SHA256:

`049f043b0aaa64eaf524d8540071fa38de6603ddca8eb083bdd8164d88787821`

The repository preserves the original crosscheck, report, gap report, provenance, summary, stdout/stderr and original SHA256 manifest bytes in `benchmark/results/mrc_50ea3bfa42df/`. Provenance records original Agent/Core identities, Python version, original command, timezone and SHA256 for unchanged harness/oracle/case/adapter inputs. A new envelope manifest binds every preserved file; its digest and the raw record digest are fixed in `c1_benchmark.py`. These are outputs from the real engine, not hand-set PASS results. The source Agent SHA remains the original clean benchmark SHA and is not rewritten to the later packaging code SHA.

## Deterministic fail-closed integration

`c1_benchmark.verify()` checks the pinned source manifest, exact file inventory and byte hashes, original Agent/full Core identity, original input fingerprints when copying from code, then invokes the unchanged benchmark validator. Missing files, symlinks, tampering, wrong Core or non-PASS evidence stop preparation.

`c1_integration.prepare()` copies verified original evidence bytes into the exact artifact-root lookup path before sealing. Existing destination directories are refused. `c1_runner.prepare()` additionally evaluates the same G1–G9 required by B05 before printing human approval lines. This is a read-only check, with actual batch consumption separately recorded as zero; Governance's `consumes_budget` field describes the future request, not a side effect of checking it. G10 and all execution/approval/activation guards remain unchanged.

The preparation's inventory seals the evidence and pre-binding report. PREFLIGHT verifies both the sealed inventory and the pinned packaged evidence before the existing live personal evidence checks and Binding dry run. No G4 threshold or authorization scope was changed.

## Static and isolated audit

- API signatures: BIND uses the explicit public Expectation constructor; TASK keeps bound request/authorization/scope/task identity checks. New tests run the REAL Binding Service dry run and REAL artifact-root G4 lookup, not an injected BENCHMARK_PASS. Only the backend data probe is stubbed in the isolated no-dispatch test.
- Paths/provenance: exact Core-specific path and raw record/manifest pins are checked at source and destination. Date-cost configuration/source bytes, strategy, snapshot/receipt/PIT contract and data pins are unchanged.
- Runtime identity: code/Governance/Core full pins and clean trees remain required. Preparation/runtime SHA equality remains required. New-code reuse of an old preparation is explicitly rejected in regression coverage.
- Cross-runtime boundary: the existing Authorization/batch-specific server ref, one-process capability, prior claim/reservation/binding stop conditions and reconciliation rules are unchanged. No server claim is acquired by PREPARE/PREFLIGHT.
- Archive/settlement: complete work/control/session/governance/registry archives and the single consuming step remain inside the explicitly confirmed EXECUTE path. It was not invoked. No new settlement behavior was added.
- Isolation: real immutable benchmark bytes are used for the positive case. Negative tests tamper only disposable copies. Full Binding dry run is exercised against marked isolated copies of books; byte equality and no claims/batch side effects are asserted.
- Actual Drive snapshot/receipt/PIT, Colab Runtime, personal comments and Drive persistence remain NOT_EXECUTED by Codex. They must be checked by the lead in Colab; engineering tests do not replace them.

## One final Colab recovery sequence

1. Open the fixed-commit Notebook URL delivered with PR #26. Keep MODE=PREPARE, the final full Execution SHA, Governance df56664a12a54762e222f141b50e337c6dfc5088 and Core 50ea3bfa42df7708dd3a1b21f17798c206be054e. Keep both EXECUTE confirmations empty/False. Set the Colab Secret GITHUB_TOKEN; never put it in settings or an archive.
2. The Notebook presets the reviewed paths:
   - Recovery: `/content/drive/MyDrive/00Quant_Research/data/development_recovery_archives/TSM_DEV_1M_20161019_20241231_ca9f87df83f31bd24211_20261110T142034_335006Z`.
   - Original pin-review archive: `/content/drive/MyDrive/Quant_Research/strategy-research-agent/phase5c1/20261111_230118_690789`.
   These are explicit reviewed sources, not a latest-file search or permission to validate the whole Data Lake.
3. In ONE fresh Runtime run PREPARE. It checks current unused/active authorization and remote claim absence, stages only the selected Development snapshot with existing receipt/PIT checks, and creates a NEW append-only benchmark archive under `MyDrive/Quant_Research/strategy-research-agent/phase5c1_execution/benchmark_evidence/`. That archive uses Asia/Taipei time, contains the original evidence/provenance, execution SHA, run_info and SHA256 inventory, and is verified before completion. This operation copies evidence and never reruns the engineering benchmark. The new preparation archive also includes the full evidence, manifest, G1–G9 report and immutable request/proposal files.
4. Copy the printed NEW preparation path, task ID and request hash. Review all evidence. The lead personally posts the newly printed BIND_AUTHORIZATION and APPROVE_TASK lines; do not transplant old approvals. Copy their two real, distinct URLs.
5. Start another fresh Runtime. Keep the same fixed code pin and reviewed source paths, set MODE=PREFLIGHT, PREPARATION to the exact printed NEW path, BIND_URL/TASK_URL to the NEW personal comments, and keep both execution confirmations disabled. Require PASS and a complete newly verified archive. Stop here; do not choose EXECUTE or create CLAIM.

Old archive `20261113T193709_744783Z_e64cf0d7edbf4fe0a186e31985c33cf3` and task `T-b94874797c10aa2742c8` remain unchanged and cannot be reused with this new code/artifact inventory. Their expiry `2026-11-15T19:37:09Z` (2026-11-16 03:37:09 Asia/Taipei) does not grant a changed request. A NEW PREPARE and NEW personal binding/task approvals are required. This does not re-sign the underlying Research Authorization; its original validity/scope/budget must still pass unchanged. If expired, already used or otherwise inconsistent, stop.

Google Drive is not WORM storage: unique write-once paths and verified hashes prevent accidental overwrite in this workflow and detect later changes, but do not promise physical immutability against external edits. No old Drive archive is opened for writing.

## Delivery validation

See the final PR #26 delivery report and latest-head GitHub Actions for exact matrix counts, final runtime SHA and launcher SHA. The runtime commit is created before the later Notebook pin commit to avoid a self-SHA cycle. No floating branch HEAD or fabricated commit is used. No formal research is executed during delivery.
