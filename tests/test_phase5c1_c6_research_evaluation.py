"""Phase 5-C1 C6 Research Evaluation remediation: evidence-conditioned validation.

SYNTHETIC_FIXTURE: unit cases use hand-built facts / evaluator outputs; the end-to-end cases run the REAL
research evaluator (universal.evaluate) on the Phase 4-C synthetic fixture archive with only the fact under test
changed in memory (no archive file is written).
"""
import copy
import hashlib
from pathlib import Path

import pytest

from strategy_agent.evaluation import c6_research as R
from strategy_agent.evaluation import phase4c as C
from strategy_agent.evaluation.universal import evaluate

ROOT = Path(__file__).resolve().parents[1]
ARCH = ROOT / "tests/fixtures/phase4c/20261106_224000_0123456"
GOV = ROOT / "tests/fixtures/phase4c/governance_root"
SHA = "a" * 40
AT = "2026-11-07T09:00:00+08:00"


# ------------------------------------------------------------------------------------------ unit builders
def facts(n=1196, days=2900.0, net=1683.8, non_winner=True, oos=False, blind="BLINDNESS_UNVERIFIED",
          costs=(("BASE", 1683.8), ("LOW", 3411.78), ("STRESS", -2367.19), ("CAT_STRESS", 1680.89))):
    return {"valid_trades": n, "sample_status": "SUFFICIENT" if n >= 30 else "INSUFFICIENT", "elapsed_days": days,
            "net_pnl": net, "has_non_winner": non_winner, "oos_evidence": oos, "holdout_status": "NOT_ACCESSED",
            "blindness": blind, "statistical_evidence": {},
            "cost_scenarios": None if costs is None else [{"scenario_id": s, "net_pnl": v} for s, v in costs]}


def ev_for(f, *, drop=(), add=(), verdict=None, suff=None, eng="PASS", cost_row="AVAILABLE"):
    """What the existing evaluator produces for these facts (mirrors universal._flags / evaluate)."""
    flags = ["IN_SAMPLE_VS_OUT_OF_SAMPLE_NOT_TESTED", "SELECTION_BIAS_NOT_TESTED", "MULTIPLE_TESTING_NOT_TESTED",
             "MARKET_REGIME_COVERAGE_NOT_ESTABLISHED"]
    if f["blindness"] == "BLINDNESS_UNVERIFIED":
        flags.append("BLINDNESS_UNVERIFIED")
    if f["sample_status"] != "SUFFICIENT":
        flags.append("INSUFFICIENT_SAMPLE")
    if f["elapsed_days"] < 365.2425:
        flags.append("LESS_THAN_ONE_YEAR_OBSERVED")
    if f["net_pnl"] <= 0:
        flags.append("NON_POSITIVE_CLOSED_NET_PNL")
    if f["net_pnl"] > 0 and f["has_non_winner"]:
        flags.append("NET_PROFIT_COVERED_BY_TOP_TRADES")
    rf = [{"code": c, "evidence": {}} for c in flags if c not in drop] + [{"code": c, "evidence": {}} for c in add]
    for s in f["cost_scenarios"] or []:
        if f["net_pnl"] > 0 and s["net_pnl"] <= 0:
            rf.append({"code": "COST_SCENARIO_TURNS_NON_POSITIVE", "evidence": dict(s)})
    if verdict is None:
        verdict = "RETEST" if f["sample_status"] != "SUFFICIENT" or f["net_pnl"] > 0 else "REJECT"
    if suff is None:
        suff = "UNDETERMINED" if f["sample_status"] != "SUFFICIENT" else "DESCRIPTIVE_ONLY"
    return {"engineering_status": eng, "strategy_verdict": verdict, "evidence_sufficiency": suff, "risk_flags": rf,
            "robustness_matrix": [{"group": "cost_sensitivity", "status": cost_row}]}


def check(f, ev):
    return R.check_scenario("X", f, ev)


# ------------------------------------------------------------------------------------------ PASS by evidence
def test_small_sample_requires_insufficient_flag_undetermined_and_retest():
    f = facts(n=13, days=200.0, net=120.0)
    assert check(f, ev_for(f))["status"] == "PASS"
    r = check(f, ev_for(f, suff="DESCRIPTIVE_ONLY"))
    assert r["status"] == "FAIL" and "evidence_sufficiency_matches_sample" in r["failed_conditions"]
    r = check(f, ev_for(f, drop=("INSUFFICIENT_SAMPLE",)))
    assert "sample_flag_matches_sample" in r["failed_conditions"]


def test_less_than_one_year_must_be_flagged_and_multi_year_must_not():
    f = facts(n=40, days=200.0)
    assert check(f, ev_for(f))["status"] == "PASS"
    assert "period_flag_matches_period" in check(f, ev_for(f, drop=("LESS_THAN_ONE_YEAR_OBSERVED",)))["failed_conditions"]
    f = facts(days=2900.0)
    assert "period_flag_matches_period" in check(f, ev_for(f, add=("LESS_THAN_ONE_YEAR_OBSERVED",)))["failed_conditions"]


def test_multi_year_sufficient_sample_is_descriptive_only_retest():
    f = facts()                                                    # the Phase 5-C1 BASE shape
    r = check(f, ev_for(f))
    assert r["status"] == "PASS", r["failed_conditions"]
    assert r["verdict"] == "RETEST" and r["evidence_sufficiency"] == "DESCRIPTIVE_ONLY"
    r = check(f, ev_for(f, suff="UNDETERMINED"))                    # the old fixture-bound expectation now FAILS
    assert "evidence_sufficiency_matches_sample" in r["failed_conditions"]
    assert "sample_flag_matches_sample" in check(f, ev_for(f, add=("INSUFFICIENT_SAMPLE",)))["failed_conditions"]


def test_missing_oos_must_stay_flagged():
    f = facts()
    r = check(f, ev_for(f, drop=("IN_SAMPLE_VS_OUT_OF_SAMPLE_NOT_TESTED",)))
    assert r["status"] == "FAIL" and "missing_oos_flagged" in r["failed_conditions"]


def test_cost_sensitivity_turning_scenarios_must_match_and_be_observed():
    f = facts()
    ev = ev_for(f)
    ev["risk_flags"] = [x for x in ev["risk_flags"] if x["code"] != "COST_SCENARIO_TURNS_NON_POSITIVE"]
    r = check(f, ev)
    assert "cost_turning_scenarios_flagged" in r["failed_conditions"]
    assert r["conditions"]["cost_turning_scenarios_flagged"]["expected"] == ["STRESS"]
    assert "cost_sensitivity_observed" in check(f, ev_for(f, cost_row="NOT_TESTED"))["failed_conditions"]
    stress = facts(net=-2367.19)                                   # a losing scenario: REJECT, no turning flag
    assert check(stress, ev_for(stress))["status"] == "PASS"
    assert check(stress, ev_for(stress))["verdict"] == "REJECT"


def test_profit_concentration_blindness_multiple_testing_and_selection_bias_kept():
    f = facts()
    for code, cond in (("NET_PROFIT_COVERED_BY_TOP_TRADES", "profit_concentration_flagged"),
                       ("BLINDNESS_UNVERIFIED", "blindness_flagged"),
                       ("MULTIPLE_TESTING_NOT_TESTED", "multiple_testing_flagged"),
                       ("SELECTION_BIAS_NOT_TESTED", "selection_bias_flagged"),
                       ("MARKET_REGIME_COVERAGE_NOT_ESTABLISHED", "regime_coverage_flagged")):
        r = check(f, ev_for(f, drop=(code,)))
        assert r["status"] == "FAIL" and cond in r["failed_conditions"], code


# ------------------------------------------------------------------------------------------ contradictions
def test_contradictory_flags_fail():
    f = facts()                                                    # net > 0 but "non-positive" flag present
    assert "non_positive_flag_matches_net" in check(f, ev_for(f, add=("NON_POSITIVE_CLOSED_NET_PNL",)))["failed_conditions"]
    f = facts(net=-50.0)                                           # net <= 0, sufficient, but RETEST
    assert "non_positive_sufficient_is_reject" in check(f, ev_for(f, verdict="RETEST"))["failed_conditions"]
    f = facts(n=13, net=-50.0)                                     # insufficient sample: only RETEST is legal
    assert "insufficient_sample_verdict_retest" in check(f, ev_for(f, verdict="REJECT"))["failed_conditions"]


# ------------------------------------------------------------------------------------------ missing evidence
def test_missing_required_evidence_never_passes():
    f = facts()
    assert "evaluator_engineering_pass" in check(f, ev_for(f, eng="BLOCKED"))["failed_conditions"]
    assert "cost_sensitivity_observed" in check(facts(costs=None), ev_for(facts(costs=None)))["failed_conditions"]
    nb = facts(blind=None)
    assert "blindness_evidence_present" in check(nb, ev_for(nb))["failed_conditions"]
    f2 = dict(facts(), holdout_status=None)
    assert "holdout_not_accessed" in check(f2, ev_for(f2))["failed_conditions"]
    out = R.research_evaluation({"BASE": []}, {"BASE": {}}, {"BASE": ev_for(f)}, "BASE")
    assert out["status"] == "FAIL" and out["scenarios"]["BASE"]["failed_conditions"] == ["FACTS_UNREADABLE"]
    assert R.research_evaluation({}, {}, {}, "BASE")["status"] == "FAIL"


# ------------------------------------------------------------------------------------------ illegal positive verdict
@pytest.mark.parametrize("verdict,suff", [("KEEP", "PROTOCOL_GATES_SATISFIED"), ("KEEP", "DESCRIPTIVE_ONLY"),
                                          ("RETEST", "PROTOCOL_GATES_SATISFIED")])
def test_illegal_positive_research_verdict_fails(verdict, suff):
    f = facts()
    r = check(f, ev_for(f, verdict=verdict, suff=suff))
    assert r["status"] == "FAIL" and "no_positive_verdict" in r["failed_conditions"]


def test_c6_pass_never_changes_verdict_or_metrics():
    f = facts()
    ev = ev_for(f)
    before = copy.deepcopy(ev)
    R.check_scenario("BASE", f, ev)
    assert ev == before


# ------------------------------------------------------------------------------------------ end to end (real evaluator)
def _parsed():
    arch = C.read_archive(ARCH, expected_manifest_sha256=hashlib.sha256((ARCH / "MANIFEST.json").read_bytes()).hexdigest())
    return arch, C.parse(arch), C.load_governance(GOV)


def _c6(arch, parsed, gov):
    c3 = C.trade_integrity(parsed)
    docs, evs = {}, {}
    for s in C.SCENARIOS:
        docs[s], _ = C.envelope(parsed, arch, gov, s, c3[s])
        evs[s] = evaluate(docs[s], evaluator_commit_sha=SHA, evaluated_at=AT)
    return R.research_evaluation({s: [r for r in parsed["trades"] if r["scenario"] == s] for s in C.SCENARIOS},
                                 docs, evs, C.PRIMARY), evs


def test_e2e_small_fixture_still_passes_with_its_own_evidence():
    out, evs = _c6(*_parsed())
    assert out["status"] == "PASS", {s: v["failed_conditions"] for s, v in out["scenarios"].items()}
    b = out["scenarios"]["BASE"]
    assert b["facts"]["sample_status"] == "INSUFFICIENT" and b["evidence_sufficiency"] == "UNDETERMINED"
    assert "LESS_THAN_ONE_YEAR_OBSERVED" in b["risk_flags"]


def test_e2e_longer_window_changes_expectations_not_the_rule():
    arch, parsed, gov = _parsed()
    parsed["config"]["windows"]["warmup_start"] = "2022-01-03T09:30:00-05:00"   # > 1 year observed
    out, evs = _c6(arch, parsed, gov)
    b = out["scenarios"]["BASE"]
    assert "LESS_THAN_ONE_YEAR_OBSERVED" not in b["risk_flags"] and b["facts"]["elapsed_days"] > 365.2425
    assert b["conditions"]["period_flag_matches_period"]["pass"]
    assert out["status"] == "PASS", {s: v["failed_conditions"] for s, v in out["scenarios"].items()}


def test_e2e_missing_cost_scenarios_or_holdout_fails():
    arch, parsed, gov = _parsed()
    c3 = C.trade_integrity(parsed)
    docs = {s: C.envelope(parsed, arch, gov, s, c3[s])[0] for s in C.SCENARIOS}
    evs = {s: evaluate(docs[s], evaluator_commit_sha=SHA, evaluated_at=AT) for s in C.SCENARIOS}
    trades = {s: [r for r in parsed["trades"] if r["scenario"] == s] for s in C.SCENARIOS}
    d2 = copy.deepcopy(docs)
    d2["BASE"]["cost_scenarios"] = {"status": "NOT_TESTED", "reason": "removed"}
    assert R.research_evaluation(trades, d2, evs, "BASE")["status"] == "FAIL"
    d3 = copy.deepcopy(docs)
    d3["BASE"]["holdout"] = {"status": "MISSING", "reason": "removed"}
    ev3 = dict(evs, BASE=evaluate(d3["BASE"], evaluator_commit_sha=SHA, evaluated_at=AT))
    r = R.research_evaluation(trades, d3, ev3, "BASE")
    assert r["status"] == "FAIL" and "evaluator_engineering_pass" in r["scenarios"]["BASE"]["failed_conditions"]
