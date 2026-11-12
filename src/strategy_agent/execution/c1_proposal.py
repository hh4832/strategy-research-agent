"""Phase 5-C1: the first pre-registered research proposal (A2 format), built from the draft spec 0.2.0.

Authored (no model): every strategy field is copied from the spec; the ONE declared change versus the executed
baseline is the evaluation window (one DEV month -> the 2017-2024 Development years). A2 ``draft_proposal``
validates it (status must be PROPOSED). Nothing is exported, bound, approved or executed here.

  python -m strategy_agent.execution.c1_proposal emit
"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SPEC = "docs/phase5c1/strategy_spec_v0_2_0.draft.json"
OUT = "docs/phase5c1"
ADAPTER_ID = "CLAUDE_AUTHORED_P5C1_COMPLETION_FILE"
AGENT_COMMIT = "0440c7a428ffa7756d76651b5c50f88640bdad98"      # Phase 5-C1 baseline main (deterministic)
CREATED_AT = "2026-11-10T16:00:00+08:00"


def build_completion(spec: dict, *, challenger_view: dict) -> dict:
    u = {k: spec["universe"][k] for k in ("market", "symbols", "timeframe")}
    ps, h = spec["position_sizing"], spec["hypothesis"]
    return {
        "title": "Phase 5-C1: does SMA 5/20 on TSM 15m time trends beyond drift and costs on Development 2017-2024?",
        "hypothesis": {"question": h["statement"], "testable_claim": h["falsifiable_prediction"],
                       "null_hypothesis": h["null_hypothesis"], "expected_direction": "CONDITIONAL",
                       "purpose": "PRE_REGISTERED_HYPOTHESIS_TEST on Development data only; Holdout never read."},
        "market_mechanism": {"category": "MOMENTUM", "causal_logic": h["mechanism"],
                             "failure_modes": list(h["known_failure_modes"])},
        "strategy_rules": {
            "entry_condition": "Enter long when the completed-bar SMA5 crosses above SMA20 while flat (spec 0.2.0 = 0.1.1 rules).",
            "exit_condition": "Exit long when the completed-bar SMA5 crosses below SMA20.",
            "position_sizing": f"FIXED_UNITS {ps['units']} shares; initial capital {ps['initial_capital']} USD.",
            "exit_policy": "Opposite crossover only; no stop, take-profit or time exit; overnight holding allowed.",
            "holding_horizon": h["expected_holding_period"], "signal_time": "BAR_AVAILABLE_AT",
            "execution_time": "STRICTLY_AFTER_SIGNAL_AVAILABILITY", "universe": u},
        "required_data": {**u, "price_fields": ["open", "high", "low", "close", "volume"], "additional_features": [],
                          "coverage": {"start": "2016-10-19", "end": "2024-12-31"},
                          "pit_requirements": ["Use only fully completed bars and original timestamps.",
                                               "Regime labels use completed sessions strictly before the entry session.",
                                               "Never access Holdout HOLDOUT-TSM-15M-v1; Development data only."],
                          "pit_timing": "Signal at completed-bar available_at; fill strictly later on an eligible bar "
                                        "(CONSERVATIVE_LABEL_AGNOSTIC).",
                          "source_ref": spec["data_requirements"]["datasets"][0]["snapshot_id"],
                          "availability": "DECLARED_AVAILABLE_NOT_VERIFIED"},
        "research_variables": {
            "changes": [{"core_variable": "evaluation window (rules, parameters, costs unchanged)",
                         "baseline": "one Development month 2024-05-29..2024-06-28 (13 trades, INSUFFICIENT_SAMPLE)",
                         "proposed_change": "all complete Development calendar years [2017-01-01, 2025-01-01), chosen by "
                                            "rule before any result on them",
                         "expected_improvement": "an interpretable sample (hundreds of trades, 8 years, several regimes) "
                                                 "to test the pre-registered hypothesis; no performance improvement claimed",
                         "potential_side_effects": "includes the already-seen month (disclosed; reported with and "
                                                   "without it); one symbol only (survivorship); RESEARCH_ONLY costs"}],
            "parameter_search": {"mode": "FIXED", "max_trials": 1}, "selection_basis": "HYPOTHESIS_DRIVEN",
            "prior_experiment_refs": ["docs/evidence/phase4a_dev_smoke/20261107_081300_9439f8c",
                                      "docs/evidence/phase5b3c/20261110_141740_e3ce267"]},
        "challenger_view": copy.deepcopy(challenger_view),
        "strategy_spec_fragment": {k: copy.deepcopy(spec[k]) for k in ("universe", "parameters", "signal",
                                                                        "position_sizing", "risk_exits",
                                                                        "risk_constraints", "execution")},
    }


def build() -> tuple[dict, dict]:
    from strategy_agent.proposal import FileDraftAdapter, draft_proposal  # noqa: PLC0415
    spec = json.loads((ROOT / SPEC).read_text(encoding="utf-8"))
    fx = json.loads((ROOT / "fixtures/proposal/complete_momentum.json").read_text(encoding="utf-8"))
    comp = build_completion(spec, challenger_view=fx["llm_draft"]["challenger_view"])
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "completion.json"
        p.write_text(json.dumps(comp, ensure_ascii=False), encoding="utf-8")
        prop = draft_proposal(spec["hypothesis"]["statement"], FileDraftAdapter(p, adapter_id=ADAPTER_ID),
                              agent_commit_sha=AGENT_COMMIT, created_at=CREATED_AT)
    return comp, prop


def _dumps(o) -> bytes:
    return (json.dumps(o, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def main(argv=None) -> int:
    if (argv or sys.argv[1:]) != ["emit"]:
        print(__doc__)
        return 2
    comp, prop = build()
    if prop["status"] != "PROPOSED":
        print(json.dumps(prop["validation"], ensure_ascii=False, indent=2))
        return 1
    (ROOT / OUT / "proposal_completion.json").write_bytes(_dumps(comp))
    (ROOT / OUT / "research_proposal.json").write_bytes(_dumps(prop))
    print(prop["proposal_id"], prop["status"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
