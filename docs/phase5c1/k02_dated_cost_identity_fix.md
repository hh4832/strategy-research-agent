# Phase 5-C1 K02 dated cost identity repair

## Scope and root cause

Engineering-only repair, based on PR #26 head `d5d690e433a2c4482804fd2fa164e5ee2697018b`.
Original execution: Agent `209b56fe2bb4cf13b952f1baef51e8cfd89c6392`, governance
`df56664a12a54762e222f141b50e337c6dfc5088`, Core `50ea3bfa42df7708dd3a1b21f17798c206be054e`.

K02 compared every fill's profile SHA/version to the submitted global base profile. Core's dated
contract deliberately emits a resolved profile per New York execution date, so a valid dated fill
was rejected. A deterministic synthetic result is rejected by the unchanged pre-repair validator
with K02 and passes the repaired validator without modifying any result bytes or financial values.
No evidence here requires changing Core's charging formulas.

## Changed files and semantics

- `src/strategy_agent/adapters/m3a4_contract.py`: optional caller-supplied `cost_schedule`; retain P09/P10
  global base checks and all cost arithmetic checks. P12 checks the submitted base against the pinned
  schedule base plus returned schedule SHA/id/version/scenario/timezone/coverage/document. A dated
  result without its submitted schedule fails closed. K02 resolves via the existing Core
  `DatedCostSchedule.profile_for(row['exec_ts'])`, then verifies profile id/SHA/version and fee mode.
  Resolution errors (including naive/missing timestamps and uncovered dates) are K02 contract failures;
  no fallback to base or signal date. Static results continue to use the submitted base; id is now
  explicitly checked alongside SHA/version. This strengthens rejection of a tampered id.
- `src/strategy_agent/research_only/core_phase4a.py`: pass the exact scenario schedule already submitted
  to Core into the validator. Static callers pass None; existing dated cost diagnostics remain intact.
- `tests/test_phase5c1_k02_dated_cost_identity.py`: 18 synthetic regression cases, including a minimal four-scenario
  execution-entry integration with injected deterministic decisions and real Core execution/diagnostics.
- Existing CI already selects `tests/test_phase5c1*.py` in its exact-Core job; no workflow changes needed.
- This document.

No second cost resolver, recalculation, hardcoded cost SHA or alternative effective-date rules were added.
Authorization binding, schedule/source integrity validation, SMA definitions, order timing, PIT, slippage,
commission, official cost schedule contents and Holdout gates are unchanged. No Core source changes.

## Regression coverage

1. Static base accepts correct fills and rejects an incorrect SHA.
2. Valid dated fills pass for BASE, LOW, STRESS, CAT_STRESS.
3. Incorrect resolved SHA/version/id or commission scope fails.
4. Incorrect submitted base fails even if returned provenance is changed to agree with that wrong base.
5. Two legal same-session fills on consecutive dates resolve different profiles across the rate switch.
   UTC timestamps at 04:59:59 and 05:00:00 exercise the New York midnight boundary.
6. Uncovered, naive and missing execution timestamps fail without fallback.
7. Valid signal and execution times differ; a mutated previous-date signal cannot change K02's expected
   identity. That mutation still fails T01. Core restricts conservative fills to the signal's session;
   the cross-date mutation is explicitly negative evidence, not a claimed valid overnight fill.
8. Missing submitted schedule or incorrect schedule provenance fails P12.
9. The actual `execute_batch` caller forwards all four pinned scenario objects and accepts nonempty
   synthetic results under both K02 and P12, retaining Core dated-cost diagnostics.

## Validation

Python 3.12.14, pytest 9.2.0; separate editable environments for exact Core `50ea3bf` and legacy `699e3ad`.
Synthetic prices/rates/temporary books only. No formal Development or Holdout data was accessed.

| Validation | Result |
| --- | --- |
| New K02 regressions (included in Agent total below) | 18 PASS |
| Exact Core 50ea3bf: Agent K02, M3-C, Phase4-A, C1 runner/G4, authorization binding/state | 144 PASS, 2 existing pinned-Phase4-A skips, 1 legacy-only reference deselected |
| Exact Core 50ea3bf: Core test_dated_costs + test_m3a4_final | 56 PASS |
| Legacy Core 699e3ad: full Agent test_m3c_core_integration, including committed numeric reference | 24 PASS |
| C1 unauthorized execution, multiyear cost NO-GO, approval URL scope | 22 PASS, 18 unrelated cases deselected |
| Baseline/repaired comparison on identical synthetic dated fills | Baseline K02 FAIL; repaired PASS |
| git diff --check / protected paths diff / Core checkout diff | PASS |

Commands (from the Agent repository; each environment has the specified editable Core checkout):

```sh
# Exact Core 50ea3bf
pytest -q tests/test_phase5c1_k02_dated_cost_identity.py tests/test_m3c_core_integration.py \
  tests/test_phase4a_core_integration.py tests/test_c1_execution_runner.py \
  tests/test_phase5b1_approval_binding.py tests/test_authorization_contract_state.py \
  -k 'not test_15_numeric_reproducibility_vs_committed_reference'
pytest -q tests/test_phase5c1_integration.py \
  -k 'unauthorized_execution or real_multiyear_cost_window or original_approval_url'
# From Core 50ea3bf checkout
pytest -q tests/test_dated_costs.py tests/test_m3a4_final.py
# From Agent checkout with Core 699e3ad installed
pytest -q tests/test_m3c_core_integration.py
```

Only existing dependency deprecation warnings were emitted. Test file renamed into the existing C1 CI
selection after the runs above; its contents are unchanged by the rename.

The old M3-C numeric reference is explicitly pinned to Core `699e3ad`; running it against `50ea3bf`
correctly fails its SHA assertion. Its expected value was not changed. It is verified separately using
its actual pinned dependency; the exact-Core C1 regressions exclude only that version-specific case.

## Research impact, restrictions and remaining risks

This repairs acceptance of correctly resolved dated identities; it does not validate the failed formal
research output, change any financial result, or certify strategy performance. Tests use synthetic prices
and assumed synthetic rates, not real research data. New tests skip when the optional dated Core API is
unavailable; the explicit pinned-Core CI job runs them.

Original Authorization `RA-IBKR_TSM-SMA520_P5C1-0001` / batch-1 remains CLAIMED / NON-REUSABLE,
EXECUTE FAILED, SETTLEMENT RECONCILIATION_REQUIRED, REEXECUTION NO-GO. No CLAIM creation/deletion,
Ledger append/rewrite/settlement, original Authorization reuse or Archive modification occurred.
The 12 remaining archived HTML SHA256 checks remain an independent incomplete reconciliation issue;
this repair does not claim to complete it. The earlier uncommitted reconciliation document is preserved
and excluded from the repair commit.

Draft engineering review only; no merge, PREPARE, PREFLIGHT or formal EXECUTE. Changed runtime code is not
permission to reuse the original execution or authorization. READY_FOR_REEXECUTION = NO.
