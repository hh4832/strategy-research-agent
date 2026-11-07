# MVP v1 Release Acceptance — `09ac0d5`

**Verdict: MVP_V1_ENGINEERING_GO** (research-only engineering scope; not a strategy or market-eligibility claim)

| Item | Result |
|---|---|
| R1_repository_integrity | PASS |
| R2_regression_tests | PASS |
| R3_real_archive_evaluation_consistency | PASS |
| R4_governance_isolation | PASS |
| R5_archive_integrity | PASS |
| R6_colab_portability | PASS |

Strategy RETEST / evidence UNDETERMINED; formal market NO_GO; Phase 4-C checks {'C1_archive_identity': 'PASS', 'C2_archive_integrity': 'PASS', 'C3_trade_integrity': 'PASS', 'C4_performance_consistency': 'PASS', 'C5_temporal_safety': 'PASS', 'C6_research_evaluation': 'PASS', 'C7_reproducibility': 'PASS', 'C8_governance_isolation': 'PASS'}

## Real-archive comparison vs Phase 4-C evaluation 20261107_100344_883d14c

| Scenario | identical metrics | identical input | net return | win rate | payoff | expectancy | PF | MDD |
|---|---|---|---|---|---|---|---|---|
| BASE | True | True | 0.018443499999999998 | 0.46153846153846156 | 3.624255230045876 | 141.87307692307692 | 3.106504482896465 | -0.00782950773686697 |
| LOW | True | True | 0.018963499999999998 | 0.46153846153846156 | 3.777023971053822 | 145.87307692307692 | 3.237449118046133 | -0.0076319293552954415 |
| STRESS | True | True | 0.0176435 | 0.38461538461538464 | 4.6682013325073095 | 135.71923076923076 | 2.9176258328170683 | -0.008145635305713661 |
| CAT_STRESS | True | True | 0.018440900000000003 | 0.46153846153846156 | 3.6235159321982287 | 141.85307692307694 | 3.1058707990270533 | -0.007830495919687541 |

Total costs: {'BASE': '136.6500', 'LOW': '84.6500', 'STRESS': '216.6500', 'CAT_STRESS': '136.9100'}
Presentation-only changes: {'cost_sensitivity': {'reference': 'required evidence absent', 'release': 'cost scenarios supplied on the same signals and positions'}}

Governance: 1/1 uses; formal NO_GO; new batch consumed 0

Archive: source unchanged True; MANIFEST pin True; reference evaluation equals committed copy True

Integration: RC 09ac0d5ac4315e56dcfae44f1deea810d1dc8910 parents ['80e89dea84084490c4d53273b0cbc46351308df8']

Tests (this runtime): 362 passed, 54 skipped in 28.76s (python 3.13.18, core absent (Core-dependent tests skip by design; covered by CI jobs pinned to 699e3ad/e31c57b/b16179a))
