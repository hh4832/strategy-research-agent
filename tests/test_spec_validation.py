import copy
import json
from pathlib import Path

import pytest

from strategy_agent.spec.validate import canonical_hash, is_valid, validate

EX = Path(__file__).parents[1] / "specs" / "examples"
SMA = json.loads((EX / "sma_crossover_tw_2330_5m.json").read_text())
ORB = json.loads((EX / "orb_txf_1m.json").read_text())
ATR = json.loads((EX / "atr_breakout_tsm_15m.json").read_text())


def rules(spec):
    return {i.rule for i in validate(spec) if i.level == "ERROR"}


@pytest.mark.parametrize("spec", [SMA, ORB, ATR], ids=["sma", "orb", "atr"])
def test_examples_valid(spec):
    assert is_valid(spec), validate(spec)


def mut(spec, fn):
    s = copy.deepcopy(spec)
    fn(s)
    return s


CASES = [
    ("SCHEMA", SMA, lambda s: s["execution"].__setitem__("fill_rule", "SAME_BAR_CLOSE")),          # look-ahead fill
    ("SCHEMA", SMA, lambda s: s["signal"]["entry_rules"][0]["when"]["args"][0]["left"].__setitem__("lag", -1)),  # future bar
    ("SCHEMA", ORB, lambda s: s["universe"]["contract_policy"].__setitem__("continuous_inferred", True)),
    ("SCHEMA", SMA, lambda s: s["signal"].__setitem__("evaluation_time", "INTRABAR")),
    ("SCHEMA", SMA, lambda s: s["hypothesis"].pop("null_hypothesis")),
    ("SCHEMA", SMA, lambda s: s["experiment"].pop("seed")),
    ("SCHEMA", SMA, lambda s: s["data_requirements"]["datasets"][0].pop("snapshot_id")),
    ("S01", SMA, lambda s: s["parameters"]["values"]["fast"].__setitem__("default", 99)),
    ("S02", SMA, lambda s: s["parameters"]["values"]["fast"].update(default=20, max=60) or
        s["parameters"]["values"]["slow"].update(default=20, min=20)),
    ("S03", SMA, lambda s: s["signal"]["indicators"][0]["params"].__setitem__("period", {"param": "nope"})),
    ("S03", SMA, lambda s: s["signal"]["exit_rules"][0]["when"]["left"].__setitem__("ref", "sma_ghost")),
    ("S04", SMA, lambda s: s["data_requirements"].__setitem__("warmup_bars", 50)),   # covers default, not max(120)
    ("S04", ORB, lambda s: s["signal"]["indicators"][0].__setitem__("series_scope", "CONTINUOUS_ELIGIBLE_BARS")),
    ("S05", SMA, lambda s: s["costs"].__setitem__("sensitivity_multipliers", [1, 1.5, 1.2])),
    ("S06", ORB, lambda s: s["universe"]["contract_policy"].update(type="NOT_APPLICABLE")),
    ("S06", ORB, lambda s: s["universe"]["contract_policy"].update(no_position_across_contract_change=False)),
    ("S07", ORB, lambda s: s["universe"]["sessions"].__setitem__("no_overnight", False)),
    ("S08", SMA, lambda s: s["universe"]["sessions"].__setitem__("exclude_closing_auction", False)),
    ("S09", SMA, lambda s: s["data_requirements"]["datasets"][0].pop("mandatory_exclusions")),
    ("S10", SMA, lambda s: s["signal"]["entry_rules"][0].__setitem__("action", "ENTER_SHORT")),
    ("S11", SMA, lambda s: s["data_requirements"]["fields"].append("eps_ttm")),
    ("S12", SMA, lambda s: s["signal"].__setitem__("exit_rules", [])),
    ("S13", ATR, lambda s: s["risk_exits"]["stop_loss"].__setitem__("atr_indicator", "prev_close")),
    ("S14", ATR, lambda s: s["universe"].pop("aggregation")),
    ("S16", SMA, lambda s: s.__setitem__("status", "REGISTERED")),                 # no hash
]


@pytest.mark.parametrize("rule,base,fn", CASES, ids=[f"{c[0]}-{i}" for i, c in enumerate(CASES)])
def test_rule_rejects(rule, base, fn):
    assert rule in rules(mut(base, fn))


def test_registered_with_correct_hash_passes():
    from m2_helpers import approved_profile, governance_for_tests
    ctx = governance_for_tests()                       # governed inputs confirmed/approved for the test
    s = copy.deepcopy(SMA)
    s["status"] = "REGISTERED"
    s["costs"]["profile_ref"]["sha256"] = approved_profile()["provenance"]["profile_sha256"]
    s["provenance"]["spec_sha256"] = canonical_hash(s)
    assert is_valid(s, ctx), validate(s, ctx)
    s["parameters"]["values"]["fast"]["default"] = 15          # tamper after hashing
    assert "S16" in {i.rule for i in validate(s, ctx) if i.level == "ERROR"}


def test_registered_rejects_unresolved():
    s = copy.deepcopy(ATR)
    s["status"] = "REGISTERED"
    s["data_requirements"]["datasets"][0]["producer_commit_sha"] = "UNRESOLVED"
    s["provenance"]["spec_sha256"] = canonical_hash(s)
    assert "S16" in rules(s)


def test_unused_parameter_warns():
    s = copy.deepcopy(SMA)
    s["parameters"]["values"]["unused"] = {"type": "integer", "default": 1, "min": 1, "max": 3, "step": 1}
    warns = [i for i in validate(s) if i.level == "WARNING" and i.rule == "S03"]
    assert warns


def test_hash_is_order_independent():
    a = copy.deepcopy(SMA)
    b = json.loads(json.dumps(SMA, sort_keys=True))
    assert canonical_hash(a) == canonical_hash(b)
