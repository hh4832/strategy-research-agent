# Phase 5-C1 execution runner remediation — Draft PR review

Status: **EXECUTION_READY = NO** pending actual Drive/Colab preflight. Publication as an independent Draft PR is authorized. This change does not merge, approve or execute formal research. READY_FOR_COLAB_PREFLIGHT is a separate release/CI status, not permission to execute.

## Baseline and protected state

GitHub main and local detached HEAD were checked at `df56664a12a54762e222f141b50e337c6dfc5088` on 2026-11-12. Remote: `https://github.com/hh4832/strategy-research-agent.git`.

- Authorization: `RA-IBKR_TSM-SMA520_P5C1-0001`.
- Original Proposal bytes SHA256: `7c7b4dd7bd6e55e647453a8026f03b5107dbcd69c16d2ace13c53b78102808b0`.
- Original full Authorization Ledger bytes SHA256: `6272e0785d8e5bab4ad1195b05f128bd962214d3c48c2d7d0636ea3c19965264`.
- APPROVE record SHA256: `567dafcc44ba0300ac7504b56bdf3aafc46f420e26423e14be633429dbc30d26`.
- Validity: `[2026-11-12T16:58:13+08:00, 2026-11-19T16:58:13+08:00)`.
- Executor: `colab-hh4832-exec-01`; authorized capacity 1; C1 uses, reservations and bindings all 0 at inspection.
- Formal Market Gate NO_GO; Holdout NO_ACCESS. No Holdout data was opened. Existing metadata says BLINDNESS_UNVERIFIED; this work does not certify historical blindness.

## Blocker matrix

| Blocker | Local remediation | Remaining acceptance |
| --- | --- | --- |
| Obsolete execution notebook pins | New `Phase5C1_Development_Execution.ipynb`; Governance df56664, Core 50ea3bf; full execution SHA mandatory, clean checkout verified | Publication uses two commits: a runtime commit, followed by a notebook-only pin update referencing that complete runtime SHA. The notebook URL pins the later notebook commit. Tests compare the pinned runtime source bytes to the PR source; no branch HEAD or self-referential SHA is used. |
| B3-A manifest mismatches | C1-only artifacts, request, task and independently measured readiness manifest; actual existing A10 checked unchanged | Actual Development archive preflight NOT_EXECUTED |
| Separate runtime CLAIMs | GitHub atomic create-ref keyed by Authorization + batch 1, independent of Request/runtime; single-use process capability required by C1 dispatch | Four-process simulated server test PASS; real server write test NOT_EXECUTED (no remote mutations authorized) |
| Incomplete output archive | Drive-resident mutable execution workspace before CLAIM; new archive directory per attempt with checksums/index, work, governance/registry, claim, session receipt, evaluation/results and run_info | Actual Drive copy/receipt/archive validation NOT_EXECUTED |
| Fresh Runtime actual data validation | Exact reviewed pins, receipt renewal, Core PIT/data checks, four dated schedules, backend identity and live approval dry run wired into separate PREFLIGHT | Fresh Colab Runtime and actual Drive archive access NOT_EXECUTED |

## Minimal affected components

- `execution/c1_runner.py`: C1 orchestration composition and CLI, separating PREPARE / PREFLIGHT / explicit EXECUTE. PREPARE emits exact comment lines but posts nothing. PREFLIGHT performs BIND dry run, live TASK evidence verification, backend identity and existing A10 without binding/reservation/dispatch.
- `execution/c1_claim.py`: fixed repository and human identities, current remote protected-state byte comparison, read-only claim inspection, one atomic server ref creation, no deletion or retry. Any prior/unverifiable claim or uncertain write acknowledgement means reconciliation.
- `execution/c1_archive.py`: write-once archive creation, full byte inventory, source/destination checksum checks and tamper detection; incomplete archives remain incomplete evidence.
- `execution/b3c_run.py`: pass freshly renewed runtime bundle into the existing session builder; require server-issued capability for C1 only. B3-C keeps its original path. A10 and reservation safety rules unchanged.
- `execution/c1_integration.py`: correct the existing nested `draft.hypothesis.question` lookup.
- New notebook, isolated regression tests and this report, plus one existing CI command extended to run the runner suite with pinned Core. No approved Proposal/Ledger, strategy, costs, data pins, Market Gate or Holdout policy file changed.

## Pins preserved

- Core: `50ea3bfa42df7708dd3a1b21f17798c206be054e`.
- Data producer: `7bcc61813a6cf2956c6df69ea796bf1c7f2b495f`.
- Snapshot: `TSM_DEV_1M_20161019_20241231_ca9f87df83f31bd24211`.
- Manifest SHA256: `edabc0abf38bab6cad013b2248b68344dc0073da105a6085dd5e8f05f85272e4`.
- Parquet SHA256: `e1e7955401fcbe4a84e7486fa7b1cc8ae97a92513426086cf7902afcf958ee03`.
- Strategy spec SHA256: `0aa5c293842e1b3ff29212bcfbd07a01a49b1311dfc96047ea9b8347f77d394b`.
- Cost scenario file SHA256: `bf4f2365b42f1ede92954f7a9915fe730f59bd4c73d6e4783007eb07e1749cd3`.
- Dated schedule canonical SHA256: `43b6045758760a40ad88d741b1a97a73cfb0b6cf6b2a4a7f1f3b31d5085a4216`.
- TSM 15m SMA5/20; warmup from 2016-10-19, trading 2017-01-01 through exclusive 2025-01-01 New York boundary; BASE / LOW / STRESS / CAT_STRESS.

## Safety and reconciliation

The authoritative cross-runtime lock is the server's unique ref name, not Drive mkdir/O_EXCL. On a later explicitly confirmed EXECUTE, hh4832 must have repository Contents write permission to create that permanent claim ref. A successful ref creation is not batch consumption; it grants only one process one attempt. The ref is never deleted or reused. Rejected POST, lost acknowledgement, existing Drive control/attempt, any C1 binding/use/reservation (including released reservations), corruption or incomplete settlement requires human reconciliation. This task created no remote ref.

A server ref cannot guarantee exactly-once completion. Runtime loss may leave unused capacity but still forbid retry. The durable Drive workspace preserves partial state when finally cannot run; archive verification does not itself authorize retry. A human must inspect remote claim, mutable workspace, binding, uses, budget transitions, receipts, task state and index before deciding any reconciliation action. No automatic release, resume, re-sign or second dispatch exists here.

Archives use unique write-once paths and hash verification. Google Drive is not WORM storage: later external mutation cannot be physically prevented by this code, but checksum/inventory checks detect it. Actual Drive persistence/consistency and the required immutability policy remain deployment acceptance items.

## Non-consuming test evidence

`tests/test_c1_execution_runner.py` exercises the real C1 PREPARE/request/A10 pipeline with explicitly synthetic metadata and the original approved governance read-only. It checks PREPARE CLI output, no governance bytes changed, no binding/claim/batch writes; Core/spec/data/cost/schedule/window mismatches; invalid data reports; expiration; four independent processes with different Request hashes competing for one simulated atomic server ref; lost acknowledgement; capability reuse/serialization; direct C1 dispatch bypass rejection; interruption archive/re-entry refusal; claim/session archive coverage; tampering and symlink rejection; fresh runtime-bundle forwarding; and unpublished notebook pin rejection.

Synthetic metadata cannot prove actual data receipt/PIT/runtime acceptance. No formal strategy was run. The existing regression suites use isolated fixtures; their synthetic engine checks do not consume the formal authorization.

Final matrix (same selected related suites on each interpreter):

| Python | Passed | Failed | Skipped |
| --- | ---: | ---: | ---: |
| 3.11.17 | 167 | 0 | 5 |
| 3.12.14 | 167 | 0 | 5 |

The 5 skips are the existing B3-C tests conditioned on an active unused B3-C authorization; the formal B3-C authorization has already been used. The Core-dependent PREPARE fixture explicitly skips when Core is absent from the generic job. The existing pinned-Core C1 CI matrix now includes the runner suite, so that fixture executes under both Python versions. New runner-specific tests previously passed 25 tests on each interpreter; publication adds a runtime-pin source-byte regression. Logs: `/tmp/c1-runner-final-311.log`, `/tmp/c1-runner-final-312.log`.

Command: `PYTHONPATH=src <python> -m pytest -q -rs -p no:cacheprovider --tb=short tests/test_c1_execution_runner.py tests/test_authorization_contract_state.py tests/test_c1_authorization_colab.py tests/test_phase5c1*.py tests/test_phase5b3c_execution.py tests/test_phase4a_research_only.py`.

`git diff --check` passed. No tracked diff under governance, registry, configs or specs. Original signed-file SHA256 checks passed after tests.

## Release and human operation

The research lead authorized branch creation, commit, push and an independent Draft PR, but not merge or formal execution. The notebook commit contains a literal full SHA for the earlier runtime commit; the Draft PR and final delivery report provide both actual immutable SHAs. The resulting URL follows:

`https://colab.research.google.com/github/hh4832/strategy-research-agent/blob/<FULL_REVIEWED_EXECUTION_SHA>/notebooks/Phase5C1_Development_Execution.ipynb`

This line is a URL template. Use the full immutable notebook commit URL reported with the Draft PR; do not substitute a mutable branch URL.

In a fresh Runtime, use PREPARE with the two exact reviewed Drive archive paths. Preserve the printed immutable preparation path and request/task hashes. The lead personally reviews/posts the two approval lines, then runs a fresh PREFLIGHT with those real URLs and the same code pin/preparation archive. Require actual-data PASS, all four dated costs, live governance/claim verification and complete archive verification. Only then may the lead separately decide whether to set EXECUTE and both explicit confirmations for one attempt. No execution is scheduled by this remediation.

Published runtime pin: `ba939f64cce4ecafc0cad16d34cbb8616daa2f1c`. The later notebook commit changes launcher metadata/pin only; all five execution source files are byte-identical to this runtime commit.

Publication bootstrap correction: the uv 0.9.5 catalog offers CPython 3.11.14 but not 3.11.17. The launcher now fixes uv 0.13.4, whose catalog explicitly offers CPython 3.11.17; Python remains 3.11.17. This changes the launcher/tests/report only; the execution runtime pin remains unchanged.
