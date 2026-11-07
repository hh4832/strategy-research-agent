# Phase 4-B engineering acceptance

- Branch: `feature/phase4b-research-evaluator`.
- Verified parent: `545e89ac19305869e07bb4a95e9fffce07deeb47`.
- Evaluator implementation + contracts/tests: `9258a48f58a18a502e52a13b2bdf1137485bb0e2`.
- Scope: new evaluator modules/schemas/tests, README and development/example docs only.
- No shared module, Phase4-A Runner/adapter, governance, registry, configs, specs,
  strategy, Core or notebook modification. No real TSM run, no raw/Holdout access,
  no authorization consumption, no registry/Notion publication.

## Validation

Python3.11:333 passed,53 skipped. Python3.12:333 passed,53 skipped. These are full
no-Core suites; skips are existing Core-dependent integration cases.60 new
Evaluator cases pass without Core. No existing Oracle expected value changed.
Existing GitHub workflows automatically discover the new tests; they also retain
fixed-Core compatibility, synthetic execution and Phase4-A integration jobs.
Remote CI results and PR creation outcome are reported separately in delivery.

The actual standalone CLI generated all five checked-in files under
`docs/examples/phase4b/SYNTHETIC_FIXTURE_example` from the fixture, using clean
implementation commit9258a48 and the recorded Asia/Taipei evaluation timestamp.
Result: engineering PASS, Strategy Verdict RETEST, Evidence Sufficiency
DESCRIPTIVE_ONLY, evaluator batch consumption0. The synthetic source's positive
closed net PnL80 is NOT a TSM/SMA effect; no real result was read. The deterministic
replay test fixes its timestamp and compares all five output files byte-for-byte.

Input/output schemas, all12 requested fixture classes, Q5 invalidations,
G1–G11, aware chronology, cost/metric reconciliation, archive checksums/path
allowlist, governance fingerprint preservation and output no-overwrite are tested.

## Supported / not supported

See `RESEARCH_EVALUATOR_CONTRACT.md` for formulas/units. Supported conditionally:
Core equity total return, closed net return, actual-span CAGR, trade ratios,
drawdown/elapsed duration, regular-interval nonannualized volatility/tail loss,
explicit upstream exposure/notional turnover, cost burden, rejection rate,
holding period and observed fill diagnostics. Missing assumptions remain explicit;
no universal annualization factor or duplicate PnL engine is introduced.

Unsupported: arbitrary-frequency annualized Sharpe/volatility, fresh statistical
experiment execution or evidence authentication, trade reconstruction, inferred
regimes, raw-data validation, Holdout assessment or deployment approval.

Phase4-A Archive is read-only/byte-verifiable for known result files. It currently
omits trades.csv, complete source metadata and full explicit execution evidence;
the normalizer deliberately yields BLOCKED/RETEST, not a fabricated evaluation.
Claude's actual finished archive is still required for real headers, precision,
provenance, cost component semantics, trade exports and observed scenario/fold
coverage. No integration PASS on a real archive is claimed.

## PR boundary

Use an independent PR with head `feature/phase4b-research-evaluator`, base
`feature/phase4a-tsm-research-only` to isolate only the Evaluator changes from the
verified Phase4-A parent. This is a stacked code-review dependency, not a change
to the Phase4-A execution path. No merge is performed. GitHub API currently returns
Forbidden in this environment; a pushed branch/compare link does not itself mean
a PR was created. Final delivery states the actual outcome without fabricating a
PR number.
