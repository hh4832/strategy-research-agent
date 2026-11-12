# Phase 5-C1 integration and research readiness

## Current acceptance: BLOCKED, no research execution

The user's real Colab evidence reports recovery local full preflight PASS and
Drive archive byte read-back PASS, preserving FAILED.json, with no conversion,
strategy execution, authorization or batch consumption. This is data-preparation
evidence, not a successful strategy test. Private Drive is unavailable here.

Known, user-supplied identity (not invented file hashes):

- Snapshot: `TSM_DEV_1M_20161019_20241231_ca9f87df83f31bd24211`
- Original producer: `7bcc61813a6cf2956c6df69ea796bf1c7f2b495f`
- Original plan: `4dd605385229b1b062fccbe5900b097cff3525fe13f66964273f8e63166f286a`
- Reader: Core `bc0592984a09d806ebb9ef78ce377db78b404f86`
- Original conversion approval: https://github.com/hh4832/market-research-core/pull/4#issuecomment-6768321310
- Archive: `MyDrive/00Quant_Research/data/development_recovery_archives/TSM_DEV_1M_20161019_20241231_ca9f87df83f31bd24211_20261110T142034_335006Z/`

Manifest / lineage / Parquet / original binding / marker / recovery-provenance
SHA256 are **PENDING**. Their first observation is not a pretrusted pin. The
actual recovery implementation commit is read from the verified recovery record,
separately from producer and reader; accepted recovery implementations are
`21d0936b467fc1ea94ab434e086b212bfb6868f4` and the delivered `bc059...` commit.

## Blocking Core cost capability (cannot be resolved by authorization)

Source inspection of the exact pinned Core establishes:

- `tsm_research_costs.build_tsm_research_profile` uses a single interval covering
  2024-06-03..2024-06-28, not a dated multi-rate profile.
- `tsm_cost_diagnostics` declares
  `cross_rate_switching=UNSUPPORTED_NO_FORCED_LIQUIDATION_OR_STITCHING`.
- `ExecutionEngine` checks the profile's effective dates before fills and accepts
  one CostProfile per run, not a dated SEC/TAF schedule callback.
- Agent `CostSet.check_window` refuses a window crossing SEC or TAF intervals.

The fixed 2017–2024 window crosses several SEC/TAF intervals. Applying 2024 rates
throughout, broadening effective metadata, resetting positions at rate boundaries
or stitching independent backtests would change the cost contract or scientific
experiment. None is done. The original four scenario rates and their dated
schedule stay unchanged. `EXECUTE_AUTHORIZED` therefore refuses **before any
binding write or batch consumption**, even with otherwise valid authorization.
The notebook must not be advertised as capable of a lawful long execution yet.
A separately reviewed Core delivery supporting continuous dated costs is required;
this task does not modify Core or promise that a merge alone adds that feature.

Core PR #3 was observed OPEN on GitHub during this work. PR #4 depends on its
unmerged code. Exact commit installation includes the code (the pin is an
engineering dependency, not approval or merge evidence). Dependency review/merge
remains the lead's responsibility. Neither PR is merged here. The cost limitation
is independently present in the tested commit and blocks formal research even if
both PRs are merged without further implementation.

## Data integration

`execution.c1_integration.inspect_archive` verifies the recovery marker, its
provenance hash, exact snapshot/producer/plan, original approval evidence, daily
footers and the whitelisted archive file hashes. It never opens paths named by
conversion lineage. Unlisted archive entries fail closed. `OBSERVED_NOT_TRUSTED`
is distinct from `REVIEWED_PINS_VERIFIED`; only an explicitly supplied complete
six-hash map can produce the latter after matching actual bytes. No automatic
trust-on-first-observation promotion exists.

`stage_archive` copies to a new local directory, verifies copied bytes, calls
Core `renew_development_receipt`, then full Core preflight. It writes a separate
runtime binding; the archived old receipt is never used to load rows. The original
snapshot, FAILED.json and recovery evidence remain intact. The local receipt is
session-specific and cannot be reused from Drive or a later machine.

`prepare` emits an external artifact root, spec 0.2.0 with the actual binding,
canonical self-hashed config, A2 proposal, original strategy/cost inputs, unsigned
authorization draft and readiness report. No governance or registry file is
written. No Runner, signal, engine, signer or budget API is called.

Once the blockers are resolved and the lead has separately created formal
Research Authorization, `prepare_governed_request` reuses B3-C's actual A2
exporter and exact approval-line generation. The only permissible execution chain
remains B3-C `execute` -> GitHub evidence/binding -> REAL_GATED A01–A10 -> atomic
ledger -> existing ResearchOnlyRunner -> fresh evaluator -> archive -> settlement.
The new wrapper does not dispatch that chain while the fixed Core is incapable.
B3-C defaults and previous governed single-batch behavior remain unchanged.

## Continuous bounded reads

`load_continuous_native` partitions New York dates into disjoint inclusive ranges
of at most 31 days. Empty footer-proven ranges are recorded, not treated as data
loss. Every populated query uses the unchanged Core loader and all its receipt,
stat, manifest, daily-group, Holdout and PIT guards. Footer row counts match each
returned chunk; duplicate or unordered native records/buckets are rejected.
Chunks are concatenated **before** one SMA computation and one engine run per
scenario. Cash, positions, signals and costs are never restarted per month, and
loading creates no budget event. Warm-up remains 2016-10-19 onward; no orders are
allowed before 2017-01-01. Synthetic same-rate continuity tests compare with Core's
whole-frame preparation, including decisions, fills, equity and cost attribution.
These tests do not prove unsupported cross-rate costs work.

The pre-registered annual/half splits, actual held-bar buckets, PIT regimes and
seen-month entry-date exclusion are descriptive partitions of that same run.
Family remains N=3, four cost scenarios are one batch. No random strategy or
success threshold was introduced. DRIFT-MATCHED-v1 is deterministic in-sample
unconditional drift, not random-entry simulation or significant timing alpha.
The dated methodology amendment is in PHASE5C1_PREREGISTRATION.md.

## Colab and the lead's next actions

Use `notebooks/Phase5C1_Long_Development_Readiness.ipynb`. All manual choices,
Drive mount, commit pins, paths, original approval path, new authorization and
GitHub evidence references, and confirmation are in the first code cell. No
later `input()` exists. Default PREPARE_ONLY never promotes itself to execution.
Outputs use new Asia/Taipei timestamp + Agent SHA directories and verified Drive
copies under the existing Quant_Research structure.

1. Run PREPARE_ONLY on the named archive with the original personal approval JSON.
2. Review/export `pin_summary.json` and supply the six exact independently reviewed
   hashes. Do not copy one-month pins or use newly observed hashes as pretrusted.
3. Obtain a reviewed Core continuous dated-cost delivery and confirm its dependency
   approvals. Update the Agent pin in a separate review; never loosen this gate.
4. Only then review the one-batch authorization draft: candidate
   `RA-IBKR_TSM-SMA520_P5C1-0001`, executor `colab-hh4832-exec-01`, proposed validity
   seven days, NO_ACCESS Holdout, unchanged formal NO_GO, fixed 2017–2024 scope.
   Issuance/expiry and approval remain human decisions; no APPROVE line is emitted.
5. The existing formal authorization, BIND_AUTHORIZATION and APPROVE_TASK GitHub
   evidence and ledger gates must all pass before any separately requested run.

Status vocabulary: synthetic tests = engineering evidence; real Agent binding =
PENDING until actual archive checks and pin review; formal research = NOT_EXECUTED;
ready to submit formal authorization = **NO** while the cost blocker remains.

### Concrete GitHub approval handoff (only after the blockers above are resolved)

The generated `authorization.DRAFT_NOT_APPROVED.json` wraps `proposal_fields`;
it is not itself an APPROVE record. The lead reviews the exact generated spec,
canonical config, six reviewed file pins, original conversion scope and proposed
one-batch/seven-day validity. Preserve the original producer separately from
reader/recovery SHA. Publish the exact reviewed spec/config with their hashes in
the lead's separate authorization change, then personally fill the issuance and
expiry decisions and save the proposal fields to
`governance/proposals/RA-IBKR_TSM-SMA520_P5C1-0001.json`.

The existing human-only command remains:

```bash
python -m strategy_agent.governance.research_authorization_admin approve \
  --proposal governance/proposals/RA-IBKR_TSM-SMA520_P5C1-0001.json \
  --approved-by hh4832 --approval-ref <human-approval-evidence-URL>
```

This task never runs it or merges that separate change. With authoritative
APPROVE books available, `c1_integration.prepare_governed_request(...)` reuses the
A2 exporter and B3-C exact-line generation. Review the immutable
`PREPARE_MANIFEST.json`: the lead posts its `BIND_AUTHORIZATION` line and its
`APPROVE_TASK` line as two personal GitHub comments, each bound to the actual
source/bound request hashes, task id and expiry. The live production verifier,
binding service, REAL_GATED A01–A10, executor identity, atomic budget ledger,
CLAIM marker, fresh evaluator and evidence settlement remain mandatory. Printing
a line grants no authorization. The current C1 capability gate still stops before
dispatch; a GitHub comment cannot waive unsupported dated costs.
