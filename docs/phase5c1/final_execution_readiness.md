# Phase 5-C1 final engineering remediation and new-authorization readiness

This is engineering preparation, not approval or execution. PR #27 is stacked on Draft PR #26;
no merge is performed. Market IBKR TSM, 15-minute SMA5/20, 2017–2024 Development, four dated-cost
scenarios, PIT and Holdout NO_ACCESS remain fixed. The uncommitted failed_execution_reconciliation.md
is preserved and excluded from commits.

## Engineering changes

- N2 no longer compares immutable historical code to every future working tree. The M3-B launcher remains
  byte-identical, pins the original `fd5a9cade361b323a9e28999ad48e0b8e3ec650c`, and is checked against
  a committed historical Git mode/blob/path inventory including all original executable/input/governance
  files. Git object integrity, ancestry, launcher digest, Core pin and checkout assertions remain checked.
  Negative tests alter the launcher pin and historical inventory; a future independent source edit does
  not change that inventory. The historical C1 launcher uses the same historical-object principle.
  No historical Notebook, reference metrics or result bytes are changed.
- K02 code is unchanged from PR #27's initial repair. It still calls Core profile_for(exec_ts), validates
  base and dated schedule identities, rejects malformed/uncovered timestamps, retains all cost arithmetic
  and temporal checks. No cost SHA or financial rule is changed.
- New `c1_context` carries explicit new authorization, governance and record pins per process. The original
  defaults preserve historical interfaces; the new recovery entry rejects every old identity/pin.
- New contract ID `RA-IBKR_TSM-SMA520_P5C1-0002` is recognized only with exactly the previously reviewed
  scientific scope: normalize only the ID for the existing canonical scope digest. Every other immutable
  field must still match; event schema, lead approval, hash chain, active state, seven-day lifetime, one
  batch, executor, independent record SHA and exact new governance snapshot are still required. This
  recognition is not an approval and writes no governance record.
- New identity is forwarded through configuration, request export, preparation, preflight, binding,
  server CLAIM capability and settlement verification. Each C1 ID requires a matching single-use server
  capability. New CLAIM ref and Drive namespaces differ from the failed batch. An old Approval cannot
  authorize a new request. Existing unused-state, live governance and atomic server claim gates remain.
- Default recovery PREPARE writes an unsigned draft packet only; no data read, Task, Request, approval,
  reservation, CLAIM or strategy. The reviewed new authorizing record is a prerequisite for actual
  task PREPARE/PREFLIGHT/EXECUTE through the existing gates.
- `c1_observe` launches exactly once, streams redacted stdout/stderr to a fresh durable Drive directory,
  emits elapsed-time/disk/RSS heartbeats, and retains exit code, log hash and process outcome. Nonzero
  exits identify the diagnostics path rather than only CalledProcessError. No retry occurs. Runner
  stages and Task/work paths are observable; structured interruption evidence is included in the
  existing append-only archive. Exception text/environment/settings/credentials are not serialized by
  the structured diagnostics. SIGKILL cannot run finally, so live logs and the pre-CLAIM durable workspace
  remain the recovery sources; no claim of complete archives after hard termination is made.

## Validation and CI

Original CI run 43697014386 failed the historical N2 comparison in generic/research-design jobs;
exact-Core C1 integration already passed. No unconditional skip/xfail or removed safety test is used.
The old shallow-history conditional skip remains, while full-history jobs execute inventory checks.

Local clean CPython 3.11.17 environment uses requirements/phase5c1-runtime.txt (CPU wheels, exact runtime
and transitive versions), editable fixed Core 50ea3bfa42df7708dd3a1b21f17798c206be054e, and this branch.
The entry is tested only with synthetic temporary books/fixtures. No real Development or Holdout files
are opened. The new authorization draft is not written into a ledger.

The local targeted validation and current-head GitHub Actions acceptance are recorded in PR #27 and
the delivery report. Follow the PR's Checks for the latest HEAD, not an older successful run. The full
CI matrix remains enabled, including generic, historical Core, exact-Core C1, append-only and security
checks. A local success is not a remote CI claim. Final acceptance requires every current-head CI job
successful; required branch-protection policy is not inferred from an inaccessible administration API.

Reproducible local commands, from repository root, with exact Core installed:

```sh
python -m pytest -q tests/test_m3b_notebook.py tests/test_c1_execution_runner.py \
  tests/test_phase5c1_recovery_readiness.py tests/test_phase5c1_k02_dated_cost_identity.py \
  tests/test_authorization_contract_state.py tests/test_phase5b1_approval_binding.py
```

The original K02 acceptance covered 18 new tests, 144 related Agent cases, 56 Core cases,
24 legacy-Core M3-C cases and 22 authorization/NO-GO cases. New current-head tests add recovery
identity isolation, unchanged scientific scope, single-use new capability, unsigned-packet safeguards,
new approved-record preparation on disposable books, and redacted nonzero-exit retention. Tests on
older Core versions keep their original expectations; no version/reference assertions are rewritten.

## Original failure and Batch disposition (proposal for independent human review)

Original execution `209b56fe2bb4cf13b952f1baef51e8cfd89c6392`, governance
`df56664a12a54762e222f141b50e337c6dfc5088`, Core
`50ea3bfa42df7708dd3a1b21f17798c206be054e`; authorization
`RA-IBKR_TSM-SMA520_P5C1-0001`, Task `T-c357245378eeb8d5852b`, batch-1.

Known failure is Agent K02's base/resolved identity mismatch, not a demonstrated Core charging error.
Use Ledger records consumption; Budget ends RECONCILIATION_REQUIRED; Task ends FAILED; CLAIM exists.
The diagnostic lists five partial Ledger row IDs. No accepted strategy performance artifact is known.
The formal attempt_1 folder was empty at prior read-only inspection. No new forensic/data collection
was performed for this engineering remediation.

1. The original Batch is permanently NON-REUSABLE. Never delete its CLAIM or reset its capacity.
2. RECONCILIATION_REQUIRED can be retained operationally as a quarantined, unresolved historical state.
   It is **not** a terminal state in the ledger state machine (TERMINAL = CONSUMED/RELEASED). This
   document cannot convert it into an official settled terminal state.
3. Without the required completion/evidence/CONSUMED settlement events, complete governance or legal
   settlement cannot be declared. Budget capacity consumed is distinct from formal CONSUMED settlement.
4. An independent lead/governance review must accept or reject this bounded disposition, the preserved
   unresolved state and the new-attempt disclosure. No artificial CONSUMED event is requested.
5. A separately approved new ID, record, request, approvals and namespace can isolate new execution
   from the original Batch. It must not copy old Request SHA, Task, Approval or CLAIM.
6. Prior partial execution means performance visibility is UNKNOWN, not proven absent. The recovery
   is a **new registered Development attempt for technical recovery**, not a first unseen-data trial.
   There is no new hypothesis/parameter search. Do not claim statistical independence or automatically
   discount the failed attempt in multiple-testing accounting; the research lead must record the
   treatment of the prior exposure before new authorization. The fixed spec's trial accounting is not
   silently changed by this engineering patch.

### Remaining archive gap

Prior direct Drive check: 83/95 indexed byte hashes verified; 12 archived SEC HTML sources could not
be materialized because of download size mismatch. The supplied ZIP's 95/95 hashes passed, but this
is not substituted for verification of formal Drive bytes. The common archived directory is
`preparation/artifacts/docs/evidence/phase5c1_official_rates/sources/`.

| Unverified file | Title observed in uploaded copy |
| --- | --- |
| `02c8324ca3a9a9db803e06fd970c1ec1f8be8041703520fd76bfd304bc2dc6af.html` | SEC.gov — Fee Rate Advisory #2 for Fiscal Year 2021 |
| `4535eeff87702d55b1b3105912b3ec158397430ddc62aff0f2f0adb2957652cb.html` | SEC.gov — Fee Rate Advisory #3 for Fiscal Year 2016 |
| `4b67948e5316329c52c547e4c68df4d2ff1d62ae3d0a6473a669f5baad2fa2f3.html` | SEC.gov — Fee Rate Advisory #3 for Fiscal Year 2018 |
| `596dad0b1a212aa835313fbbf390d9ce41feabe334f72865184fde6e4664c206.html` | SEC.gov — Fee Rate Advisory #2 For Fiscal Year 2023 |
| `92e3d2266099c0735d2b1edac16afe4b1b3146cd7207e3fe9c281d04e1b04f1b.html` | SEC.gov — Fee Rate Advisory #2 for Fiscal Year 2022 |
| `994781fe09bdfd34d6518f62c882ba0bd95faf1ad8cdf0ad557e8ae358fc7ead.html` | SEC.gov — Fee Rate Advisory #2 for Fiscal Year 2024 |
| `a8a1adb275c7c31b36eb704cf22b7b858426af440c648a45055a8418459b2735.html` | SEC.gov — Fee Rate Advisory #3 for Fiscal Year 2017 |
| `aabee0fe96450dce4a61ff47503c9ff7bba2b99780af14d6e9c59772050f95ba.html` | SEC.gov — Fee Rate Advisory #4 for Fiscal Year 2016 |
| `c35e8a4a4394bc2bd5f48145b18331bc45d2061ae69753f653b93849a89fff56.html` | SEC.gov — Fee Rate Advisory #2 for Fiscal Year 2019 |
| `ccad4b94fc08bd686b2d4f7e659ae813c7de18b2da0cc1466619792a66ea5239.html` | SEC.gov — Fee Rate Advisory #3 for Fiscal Year 2021 |
| `db440d99fa53704c12ada450d0966d2aca3998a71e7e612af1ec96602dc4b995.html` | SEC.gov — Fee Rate Advisory #3 for Fiscal Year 2023 |
| `ef3850d73cfe50766130b62e3e6ce5b2f2946d3ce692bff521a551562ea8c248.html` | SEC.gov — Fee Rate Advisory #3 For Fiscal Year 2020 |

These are cost-rate source evidence, not price data. Missing formal bytes limit historical archive
completeness and tamper detection; they do not demonstrate an incorrect rate or cost calculation.
This gap does not block engineering repair. It is not automatically a new-authorization blocker if
an independent lead accepts the preserved gap and new PREPARE independently verifies the exact pinned
Core schedule/source bytes. Otherwise authorization remains blocked. Formal reconciliation stays
INCOMPLETE either way; no Archive or Ledger is edited here.

## Runtime requirements and feasibility

- Fresh CPU Runtime, CPython 3.11.17 via uv 0.13.4; locked numerical/data dependencies in
  requirements/phase5c1-runtime.txt. Core and Agent use full immutable Git commits. No local workspace
  paths, GPU, production data downloads or conversions are required by unsigned PREPARE.
- Drive must mount successfully. GITHUB_TOKEN is read from Colab Secrets only for approved-authority
  operations; never placed in command URLs, settings or archives. Code clones use public HTTPS URLs.
- Recovery output root is `/content/drive/MyDrive/Quant_Research/strategy-research-agent/phase5c1_recovery`.
  Draft packets, runtime logs and archives each create unique timestamp/UUID paths. A formal attempt
  has one fixed new-authorization/batch durable directory, created before CLAIM. Existing paths refuse
  reentry; a new log directory does not authorize a new strategy attempt.
- Original RUNNING-to-FAILED interval was about 2h 6m 42s. It includes staging/execution overhead;
  no per-stage timing or peak RSS was available. The first scenario's K02 rejection prevents claiming
  that all four scenarios completed within that time. There is no measured full-window time/memory
  guarantee from the available evidence.
- Architecture uses CPU/Pandas/Decimal; SMA/orders are computed once, then four full-window scenarios
  execute sequentially on the same frame. Staging copies verified Development material locally;
  repeated four-scenario simulation is intentional, not four independent data conversions. Core's
  order/bar scans and native-to-conservative preparation are plausible expensive stages by inspection,
  not measured bottleneck claims. Drive is used for staging/durable output; no evidence quantifies
  whether it dominated the failed run. Do not optimize numerical rules to meet a time target.
- Colab uptime/RAM/disk are not guaranteed. Heartbeats expose time/free disk/RSS but cannot prevent
  host termination. Before authorizing a long run, the lead must accept the CPU/time/RAM risk. If that
  risk is unacceptable, use a separately reviewed stable CPU VM/container running the SAME pinned CLI,
  locked environment, approved executor, durable storage and all claim/approval gates. Validate it with
  synthetic fixtures first and approve the changed execution/storage environment; this task does not
  switch production architecture or permit checkpoint-resume/reexecution.

## New authorization and Colab procedure

Draft proposal: `recovery_authorization.DRAFT_NOT_APPROVED.json`. Suggested ID:
`RA-IBKR_TSM-SMA520_P5C1-0002`; one batch, unchanged strategy/data/costs/PIT, Holdout NO_ACCESS.

The old governance SHA is a historical reference only. A new fixed governance SHA and new record SHA
cannot truthfully be supplied before the lead approves and commits the new authorization. They are
left empty, never substituted from the original Batch. New contract scope recognition alone is not
approval; original logs remain unchanged.

1. Open `notebooks/Phase5C1_Recovery_Execution.ipynb` at the final published launcher commit. Its
   EXECUTION_CODE_SHA names a prior immutable runtime commit (no self-SHA cycle). Run default PREPARE
   in a fresh CPU Runtime: prints unsigned packet directory, NOT_EXECUTED and batch_consumption=0.
   No real data or GitHub write is performed.
2. Lead reviews Batch disposition, prior result exposure/multiple-testing treatment, source-byte gap,
   runtime feasibility and the draft. Through the existing governance process, approve a new one-batch
   record with new approval reference, issued_at/expires_at (seven days), exact scientific scope and
   executor. Do not copy old approval metadata. Commit that governance record separately; this task
   neither approves nor writes it. Record its full governance SHA and record_sha256.
3. Set those pins, existing reviewed RECOVERY_ARCHIVE and ORIGINAL_PREPARATION_ARCHIVE paths. In a
   new Runtime run PREPARE again. The existing authoritative pipeline now checks the new record/unused
   capacity/live governance/remote-claim absence BEFORE staging the existing Development snapshot.
   No Data Lake requalification or new conversion is involved. It seals new artifacts and creates the
   new source request through B3-C export. Changing the authorization changes the bound request;
   request_hash produces the full Request SHA; Task ID is T- plus its first 20 hex characters. Never
   hand-edit/copy the previous SHA or Task.
4. Lead personally posts the newly printed BIND_AUTHORIZATION line (new source Request SHA) and distinct
   APPROVE_TASK line (new bound Request SHA, scope, authorization and Task), with valid expiry. The
   software never posts comments. Store the new preparation path and the two URLs.
5. Fresh Runtime, same immutable execution/governance/Core pins, MODE=PREFLIGHT. Require fresh receipt,
   PIT, four costs, authentic G4, A10, production backend identity and live approval PASS. No batch
   consumption/CLAIM occurs. Do not treat CI or a preflight pass as EXECUTE approval.
6. Only a separate explicit user decision permits MODE=EXECUTE plus typing the exact NEW authorization
   and CONFIRM_REMOTE_CLAIM=True. Missing/old record/pins/approval/context or prior state fails closed.
   New server namespace is `refs/heads/research-claims/phase5c1/` + SHA256(new Authorization ID) + `/batch-1`.
   New Drive attempt namespace is `phase5c1_recovery/attempts/<NEW_AUTH>/batch-1`; new archives are under
   `phase5c1_recovery/archives/<timestamp_UUID>`. Original namespace remains untouched.

Read-only acceptance: inspect runtime_logs/NEW_AUTH/*/process_outcome.json and runner.log; verify draft
PACKET_INDEX or formal ARCHIVE_INDEX with the existing verifier. Confirm exact provenance, Use/Budget/
Task chain, new CLAIM subject, execution receipt/output manifest, settlement and required result files.
Process exit zero is not acceptance of strategy results. Any discrepancy remains UNKNOWN/reconciliation.

Browser disconnect: never rerun Run All or EXECUTE. Inspect the existing durable attempt, live logs,
Archive and server ref read-only. Unknown process/settlement state requires manual reconciliation;
this simulation has no authorized retry/resume. No notebook auto-runs a second attempt.

## Acceptance boundary and finite remaining human actions

Engineering can be ready for new authorization only after current-head full CI and non-consuming
entry tests pass. Actual Google-hosted Colab/Drive execution is not represented by local synthetic
bootstrap tests; the initial unsigned packet run is the safe human platform acceptance step. No real
PREPARE/PREFLIGHT/EXECUTE has been run in this task.

AUTHORIZATION_GO=NO until independent disposition/risk review, the new approved record and governance
pin, new task/request binding, fresh personal approvals and preflight are complete. EXECUTE_GO=NO until
that AND a separate explicit formal execution decision. No old authorization reuse, official settlement,
Drive overwrite, Holdout access or scientific first-trial claim is implied by ENGINEERING_GO.
