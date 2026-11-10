"""Phase 5-C1: the committed pre-registered proposal is the deterministic A2 output of the draft spec 0.2.0."""
import json

from strategy_agent.execution import c1_proposal as P
from strategy_agent.proposal.agent import validate_proposal


def test_committed_proposal_and_completion_are_the_deterministic_build():
    comp, prop = P.build()
    assert json.loads((P.ROOT / P.OUT / "proposal_completion.json").read_text()) == json.loads(P._dumps(comp))  # noqa: SLF001
    assert json.loads((P.ROOT / P.OUT / "research_proposal.json").read_text()) == json.loads(P._dumps(prop))  # noqa: SLF001
    assert prop["status"] == "PROPOSED" and validate_proposal(prop)["valid"] is True
    assert validate_proposal(prop)["authorization"] == "NOT_GRANTED" and validate_proposal(prop)["execution_eligible"] is False


def test_proposal_changes_exactly_one_core_variable_and_keeps_the_baseline_rules():
    comp, _ = P.build()
    ch = comp["research_variables"]["changes"]
    assert len(ch) == 1 and ch[0]["core_variable"].startswith("evaluation window")
    assert comp["research_variables"]["parameter_search"] == {"mode": "FIXED", "max_trials": 1}
    old = json.loads((P.ROOT / "specs/research/sma_crossover_tsm_15m_5_20_v0_1_1.json").read_text())
    for k, v in comp["strategy_spec_fragment"].items():
        assert v == old[k], k
    assert comp["required_data"]["coverage"] == {"start": "2016-10-19", "end": "2024-12-31"}
