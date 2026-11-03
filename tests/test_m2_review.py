"""M2 pre-merge review decisions: Q2/Q1 confirmations (S17), Q6 CostProfile (S18, P02–P07), Q3 contract."""
import copy
import json
from pathlib import Path

import pytest

from strategy_agent.costs.profile import cost_mismatches, frozen_gate_issues, profile_hash, validate_profile
from strategy_agent.ports import ContractViolation, REQUIRED_BAR_COLUMNS, check_bar_columns
from strategy_agent.spec.validate import canonical_hash, default_context, validate

from m2_helpers import approved_profile, registered, governance_for_tests

ROOT = Path(__file__).parents[1]


def errors(spec, ctx=None):
    return {i.rule for i in validate(spec, ctx) if i.level == "ERROR"}


# ---------------------------------------------------------------- Q1 / Q2 (S17)

def test_repository_confirmations_are_unknown():
    items = json.loads((ROOT / "governance" / "source_confirmations.json").read_text())["items"]
    assert items["ibkr_tsm_session_coverage"]["status"] == "UNKNOWN"            # Q2
    assert items["txf_timestamp_label"]["status"] == "UNKNOWN"                 # Q1
    assert items["tw_equity_timestamp_label"]["status"] == "UNKNOWN"


def test_repository_governance_blocks_every_registered_example():
    for name in ("sma_crossover_tw_2330_5m.json", "orb_txf_1m.json", "atr_breakout_tsm_15m.json"):
        s = registered(name)
        found = errors(s)                      # default = repository governance files
        assert {"S17", "S18"} <= found, (name, found)


def test_tsm_session_unknown_blocks_until_confirmed():
    s = registered("atr_breakout_tsm_15m.json", mutate=lambda x: x["data_requirements"]["datasets"][0].update(
        producer_commit_sha="06359faf7ef4b8ff702a7ab1a6d07521d3b68f28"))
    ctx = governance_for_tests()
    assert "S17" in errors(s, ctx)
    ctx.confirmations["ibkr_tsm_session_coverage"].update(status="CONFIRMED", value="RTH_ONLY")
    assert "S17" not in errors(s, ctx)


# ------------------------------------------------------------------- Q6 (S18)

def test_draft_profile_cannot_back_a_registered_spec():
    ctx = governance_for_tests(profile=json.loads((ROOT / "governance/cost_profiles/tw_equity_cash_v0.json").read_text()))
    assert any(i.rule == "S18" and "DRAFT" in i.message for i in validate(registered(), ctx))


def test_inline_costs_must_equal_profile():
    s = registered(mutate=lambda x: x["costs"]["fee"].__setitem__("rate_side", 0.0005))
    assert any(i.rule == "S18" and "fee.rate_side" in i.message for i in validate(s, governance_for_tests()))


def test_profile_hash_pins_the_exact_version():
    tampered = approved_profile()
    tampered["components"][3]["rate"] = 0.0015            # quietly halve the sell tax
    assert any(p.rule == "P04" for p in validate_profile(tampered, {"hh4832"}))


def test_frozen_requires_non_assumed_commission_and_tax():
    assumed = approved_profile(broker_verified=False)
    assert frozen_gate_issues(assumed)
    ctx = governance_for_tests(profile=assumed)
    frozen = registered(status="FROZEN", mutate=lambda x: x["costs"]["profile_ref"].__setitem__(
        "sha256", assumed["provenance"]["profile_sha256"]))
    frozen["provenance"]["spec_sha256"] = None
    frozen["provenance"]["spec_sha256"] = canonical_hash(frozen)
    msgs = [i.message for i in validate(frozen, ctx) if i.rule == "S18"]
    assert any("P07" in m for m in msgs)
    assert not frozen_gate_issues(approved_profile(broker_verified=True))


@pytest.mark.parametrize("mutate,rule", [
    (lambda p: p["components"][3]["source"].__setitem__("kind", "MODEL"), "P02"),       # official tax "from a model"
    (lambda p: p["components"][3]["source"].__setitem__("ref", None), "P03"),           # official, unverified
    (lambda p: p["provenance"].__setitem__("approved_by", "strategy-agent"), "P04"),
    (lambda p: p["components"][4].update(basis="OFFICIAL_RULE",
                                         source={"kind": "LAW", "citation": "a statute", "ref": "https://x",
                                                 "verified_at": "2026-11-03", "verified_by": "hh4832"}), "P05"),
    (lambda p: p["components"].append(copy.deepcopy(p["components"][0])), "P06"),
])
def test_profile_rules(mutate, rule):
    p = approved_profile()
    mutate(p)
    p["provenance"]["profile_sha256"] = profile_hash(p)
    assert rule in {i.rule for i in validate_profile(p, {"hh4832"})}


def test_repository_profiles_are_valid_drafts_matching_examples():
    pairs = {"tw_equity_cash_v0": "sma_crossover_tw_2330_5m", "txf_futures_v0": "orb_txf_1m",
             "ibkr_us_equity_v0": "atr_breakout_tsm_15m"}
    ctx = default_context()
    for prof_file, spec_name in pairs.items():
        prof = json.loads((ROOT / "governance" / "cost_profiles" / f"{prof_file}.json").read_text())
        assert prof["status"] == "DRAFT"
        assert validate_profile(prof, set(ctx.approvers)) == []
        spec = json.loads((ROOT / "specs" / "examples" / f"{spec_name}.json").read_text())
        assert cost_mismatches(spec["costs"], prof) == []
        # every component declares its basis; slippage is never presented as official
        assert all(c["basis"] == "RESEARCH_ASSUMPTION" for c in prof["components"] if c["kind"] == "SLIPPAGE")


# -------------------------------------------------------------------- Q3

def test_side_specific_eligibility_is_part_of_the_engine_contract():
    assert {"buy_execution_eligible", "sell_execution_eligible"} <= set(REQUIRED_BAR_COLUMNS)
    with pytest.raises(ContractViolation):
        check_bar_columns([c for c in REQUIRED_BAR_COLUMNS if c != "sell_execution_eligible"])
