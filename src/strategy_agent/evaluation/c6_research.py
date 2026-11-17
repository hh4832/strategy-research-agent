"""Phase 5-C1 C6 Research Evaluation: evidence-conditioned validation of the research evaluator's judgement.

The previous C6 required the exact outcome of the 13-trade, < 1 year Phase 4-C fixture (INSUFFICIENT_SAMPLE,
LESS_THAN_ONE_YEAR_OBSERVED, RETEST, UNDETERMINED). Here every expectation is first derived independently from the
archived facts (trades.csv, the sample window, the cost scenarios, the holdout pin in the envelope) using the
protocol's existing rules - nothing new, no new threshold - and then the evaluator output must agree with it:

  * sample sufficiency      sufficiency.assess on archived trades (protocol Q5: >= 30 valid trades, ...)
                            INSUFFICIENT <=> INSUFFICIENT_SAMPLE flag <=> evidence UNDETERMINED and verdict RETEST
                            SUFFICIENT   <=> no INSUFFICIENT_SAMPLE flag and evidence DESCRIPTIVE_ONLY
  * observation period      elapsed < 365.2425 days <=> LESS_THAN_ONE_YEAR_OBSERVED
  * closed net PnL          net <= 0 <=> NON_POSITIVE_CLOSED_NET_PNL; sufficient and net <= 0 => REJECT
  * concentration           net > 0 with at least one non-winning trade => NET_PROFIT_COVERED_BY_TOP_TRADES
  * cost sensitivity        cost-sensitivity row AVAILABLE; every cost scenario turning net <= 0 while this
                            scenario is > 0 => COST_SCENARIO_TURNS_NON_POSITIVE
  * OOS / holdout           no OOS evidence => IN_SAMPLE_VS_OUT_OF_SAMPLE_NOT_TESTED; holdout blindness
                            BLINDNESS_UNVERIFIED (from the authorization pin) => flag
  * not-tested robustness   MULTIPLE_TESTING / SELECTION_BIAS / MARKET_REGIME coverage flags unless evidence exists
  * positive verdict        KEEP / PROTOCOL_GATES_SATISFIED is illegal while OOS is untested, blindness is
                            unverified or any of the above risk flags stands (it can never be produced by C6)

Missing evidence never passes: a BLOCKED/FAIL evaluation, an absent fact or an unreadable row is a FAIL.
C6 only checks; it never edits a verdict, a metric or a flag.
"""
from __future__ import annotations

import math
from datetime import datetime

from .sufficiency import assess

ONE_YEAR_DAYS = 365.2425                   # universal._flags: LESS_THAN_ONE_YEAR_OBSERVED
POSITIVE = {"KEEP"}
POSITIVE_SUFFICIENCY = {"PROTOCOL_GATES_SATISFIED"}


def _ts(v) -> datetime:
    t = datetime.fromisoformat(str(v))
    if t.tzinfo is None:
        raise ValueError("naive timestamp")
    return t


def expected_facts(trades: list[dict], doc: dict) -> dict:
    """Independent facts from archived rows + envelope pins (never from the evaluator result)."""
    valid = [float(t["net_pnl"]) for t in trades if str(t.get("invalidated")) != "True"]
    invalid = [float(t["net_pnl"]) for t in trades if str(t.get("invalidated")) == "True"]
    sample = assess(valid, invalid)
    sp = doc["source"]["sample_period"]
    days = (_ts(sp["end"]) - _ts(sp["start"])).total_seconds() / 86400
    net = math.fsum(valid)
    hold = doc.get("holdout", {})
    hv = hold.get("value", {}) if hold.get("status") == "AVAILABLE" else {}
    cs = doc.get("cost_scenarios", {})
    scen = cs.get("value", []) if cs.get("status") == "AVAILABLE" else None
    return {"valid_trades": len(valid), "sample_status": sample["status"], "elapsed_days": days, "net_pnl": net,
            "has_non_winner": any(x <= 0 for x in valid),
            "oos_evidence": doc.get("sample_role") == "OUT_OF_SAMPLE" or bool(doc.get("folds")),
            "holdout_status": hv.get("status"), "blindness": hv.get("blindness"),
            "statistical_evidence": doc.get("statistical_evidence") or {},
            "cost_scenarios": None if scen is None else
            [{"scenario_id": r["scenario_id"], "net_pnl": float(r["net_pnl"])} for r in scen]}


def check_scenario(name: str, facts: dict, ev: dict) -> dict:
    conds: dict[str, dict] = {}

    def need(cid, ok, expected, observed):
        conds[cid] = {"pass": bool(ok), "expected": expected, "observed": observed}

    eng = ev.get("engineering_status")
    need("evaluator_engineering_pass", eng == "PASS", "PASS", eng)
    codes = sorted({f.get("code") for f in ev.get("risk_flags", [])})
    has = set(codes)
    verdict, suff = ev.get("strategy_verdict"), ev.get("evidence_sufficiency")
    rows = {r.get("group"): r for r in ev.get("robustness_matrix", []) if isinstance(r, dict)}
    sufficient = facts["sample_status"] == "SUFFICIENT"
    net = facts["net_pnl"]

    # sample sufficiency <-> flag <-> evidence sufficiency <-> verdict
    need("sample_flag_matches_sample", ("INSUFFICIENT_SAMPLE" in has) == (not sufficient),
         "INSUFFICIENT_SAMPLE present" if not sufficient else "INSUFFICIENT_SAMPLE absent",
         {"valid_trades": facts["valid_trades"], "sample_status": facts["sample_status"],
          "flag_present": "INSUFFICIENT_SAMPLE" in has})
    if sufficient:
        need("evidence_sufficiency_matches_sample", suff == "DESCRIPTIVE_ONLY", "DESCRIPTIVE_ONLY", suff)
    else:
        need("evidence_sufficiency_matches_sample", suff == "UNDETERMINED", "UNDETERMINED", suff)
        need("insufficient_sample_verdict_retest", verdict == "RETEST", "RETEST", verdict)
    # observation period
    short = facts["elapsed_days"] < ONE_YEAR_DAYS
    need("period_flag_matches_period", ("LESS_THAN_ONE_YEAR_OBSERVED" in has) == short,
         "LESS_THAN_ONE_YEAR_OBSERVED " + ("present" if short else "absent"),
         {"elapsed_days": facts["elapsed_days"], "flag_present": "LESS_THAN_ONE_YEAR_OBSERVED" in has})
    # closed net PnL
    if facts["valid_trades"]:
        need("non_positive_flag_matches_net", ("NON_POSITIVE_CLOSED_NET_PNL" in has) == (net <= 0),
             "NON_POSITIVE_CLOSED_NET_PNL " + ("present" if net <= 0 else "absent"), {"net_pnl": net})
    if sufficient and net <= 0:
        need("non_positive_sufficient_is_reject", verdict == "REJECT", "REJECT", verdict)
    if sufficient and net > 0:
        need("positive_net_verdict_not_overstated", verdict in ("RETEST", "REJECT"), "RETEST or REJECT", verdict)
    # concentration
    if net > 0 and facts["has_non_winner"]:
        need("profit_concentration_flagged", "NET_PROFIT_COVERED_BY_TOP_TRADES" in has,
             "NET_PROFIT_COVERED_BY_TOP_TRADES present", "NET_PROFIT_COVERED_BY_TOP_TRADES" in has)
    # cost sensitivity
    cs = facts["cost_scenarios"]
    row = rows.get("cost_sensitivity", {})
    need("cost_sensitivity_observed", cs is not None and row.get("status") == "AVAILABLE",
         "cost scenarios supplied and cost_sensitivity AVAILABLE",
         {"cost_scenarios_supplied": cs is not None, "row_status": row.get("status")})
    if cs is not None:
        turning = sorted(c["scenario_id"] for c in cs if net > 0 and c["net_pnl"] <= 0)
        flagged = sorted(f["evidence"].get("scenario_id") for f in ev.get("risk_flags", [])
                         if f.get("code") == "COST_SCENARIO_TURNS_NON_POSITIVE" and isinstance(f.get("evidence"), dict))
        need("cost_turning_scenarios_flagged", turning == flagged, turning, flagged)
    # OOS, holdout blindness
    if not facts["oos_evidence"]:
        need("missing_oos_flagged", "IN_SAMPLE_VS_OUT_OF_SAMPLE_NOT_TESTED" in has,
             "IN_SAMPLE_VS_OUT_OF_SAMPLE_NOT_TESTED present", "IN_SAMPLE_VS_OUT_OF_SAMPLE_NOT_TESTED" in has)
    need("holdout_not_accessed", facts["holdout_status"] == "NOT_ACCESSED", "NOT_ACCESSED", facts["holdout_status"])
    if facts["blindness"] == "BLINDNESS_UNVERIFIED":
        need("blindness_flagged", "BLINDNESS_UNVERIFIED" in has, "BLINDNESS_UNVERIFIED present", "BLINDNESS_UNVERIFIED" in has)
    elif facts["blindness"] is None:
        need("blindness_evidence_present", False, "holdout blindness recorded in the authorization pin", None)
    # multiple testing / selection bias / regime: flagged unless the evidence exists
    se = facts["statistical_evidence"]
    if se.get("selection_bias_checked") is not True:
        need("selection_bias_flagged", "SELECTION_BIAS_NOT_TESTED" in has, "SELECTION_BIAS_NOT_TESTED present",
             "SELECTION_BIAS_NOT_TESTED" in has)
    if se.get("multiple_testing_checked") is not True and not se.get("dsr"):
        need("multiple_testing_flagged", "MULTIPLE_TESTING_NOT_TESTED" in has, "MULTIPLE_TESTING_NOT_TESTED present",
             "MULTIPLE_TESTING_NOT_TESTED" in has)
    need("regime_coverage_flagged", "MARKET_REGIME_COVERAGE_NOT_ESTABLISHED" in has,
         "MARKET_REGIME_COVERAGE_NOT_ESTABLISHED present (no pre-registered regime labels)",
         "MARKET_REGIME_COVERAGE_NOT_ESTABLISHED" in has)
    # an illegal positive research verdict can never pass
    blockers = [c for c in ("IN_SAMPLE_VS_OUT_OF_SAMPLE_NOT_TESTED", "BLINDNESS_UNVERIFIED", "INSUFFICIENT_SAMPLE",
                            "SELECTION_BIAS_NOT_TESTED", "MULTIPLE_TESTING_NOT_TESTED") if c in has]
    need("no_positive_verdict", verdict not in POSITIVE and suff not in POSITIVE_SUFFICIENCY
         and not (verdict in POSITIVE and blockers), "no KEEP / PROTOCOL_GATES_SATISFIED",
         {"verdict": verdict, "evidence_sufficiency": suff, "standing_blockers": blockers})
    failed = sorted(k for k, v in conds.items() if not v["pass"])
    return {"status": "PASS" if not failed else "FAIL", "failed_conditions": failed, "conditions": conds,
            "verdict": verdict, "evidence_sufficiency": suff, "risk_flags": codes,
            "facts": {k: v for k, v in facts.items() if k != "statistical_evidence"}}


def research_evaluation(scenario_trades: dict[str, list[dict]], docs: dict[str, dict], evaluations: dict[str, dict],
                        primary: str) -> dict:
    out = {}
    for s, ev in evaluations.items():
        try:
            facts = expected_facts(scenario_trades.get(s, []), docs[s])
        except (KeyError, TypeError, ValueError) as e:
            out[s] = {"status": "FAIL", "failed_conditions": ["FACTS_UNREADABLE"], "error": str(e)}
            continue
        out[s] = check_scenario(s, facts, ev)
    p = evaluations.get(primary, {})
    ok = bool(out) and primary in out and all(v["status"] == "PASS" for v in out.values())
    return {"status": "PASS" if ok else "FAIL", "primary": primary, "scenarios": out,
            "verdict": p.get("strategy_verdict"), "evidence_sufficiency": p.get("evidence_sufficiency"),
            "verdict_not_keep": all(e.get("strategy_verdict") != "KEEP" for e in evaluations.values()),
            "validation": "EVIDENCE_CONDITIONED (expectations derived from archived facts, not from a fixture)"}
