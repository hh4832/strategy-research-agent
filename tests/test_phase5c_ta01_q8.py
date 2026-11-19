"""P5C-TA01-Q8: execution-sizing variant of P5C-TA-01 (POST_DIAGNOSTIC_VARIANT, EXPLORATORY_ONLY). No Core, no data."""
import copy
import json
from pathlib import Path

import pytest

from strategy_agent.execution import c1_integration as C
from strategy_agent.signals import ta01 as T
from strategy_agent.signals.core import SignalInputError
from strategy_agent.spec.validate import canonical_hash, validate

ROOT = Path(__file__).resolve().parents[1]
BASE_PATH = "tests/fixtures/p5c_ta01/ta01_spec_proposal.json"
Q8_PATH = "tests/fixtures/p5c_ta01/ta01_q8_spec_variant.json"
BASE = json.loads((ROOT / BASE_PATH).read_text())
Q8 = json.loads((ROOT / Q8_PATH).read_text())
DEFAULTS = {k: v["default"] for k, v in BASE["parameters"]["values"].items()}


def flat(d, pre=""):
    out = {}
    for k, v in d.items():
        out.update(flat(v, f"{pre}{k}.") if isinstance(v, dict) else {f"{pre}{k}": v})
    return out


def test_q8_differs_from_ta01_only_in_identity_sizing_and_provenance():
    fb, fq = flat(BASE), flat(Q8)
    changed = sorted(k for k in set(fb) | set(fq) if fb.get(k) != fq.get(k))
    assert [k for k in changed if not k.startswith("provenance.")] == [
        "name", "position_sizing.max_units", "position_sizing.units", "strategy_id", "version"]
    assert (BASE["position_sizing"]["units"], Q8["position_sizing"]["units"]) == (100, 8)
    assert (BASE["position_sizing"]["max_units"], Q8["position_sizing"]["max_units"]) == (100, 8)
    for section in ("universe", "data_requirements", "parameters", "signal", "risk_exits", "execution", "costs",
                    "risk_constraints", "experiment", "spec_version", "status", "hypothesis"):
        assert Q8[section] == BASE[section], section
    assert T.structure_sha256(Q8) == T.structure_sha256(BASE) == T.STRUCTURE_SHA256


def test_q8_identity_parent_and_labels():
    assert Q8["strategy_id"] != BASE["strategy_id"] and Q8["strategy_id"].startswith(T.STRATEGY_PREFIX)
    prov = Q8["provenance"]
    blank = copy.deepcopy(Q8)
    blank["provenance"]["spec_sha256"] = None
    assert prov["spec_sha256"] == canonical_hash(blank) == T.REVIEWED_SIZING[Q8["strategy_id"]]["spec_sha256"]
    assert prov["parent_spec_id"] == BASE["strategy_id"]
    assert C.sha(ROOT / BASE_PATH) in prov["notes"]                     # parent file pinned by SHA-256
    for label in ("POST_DIAGNOSTIC_VARIANT", "EXPLORATORY_ONLY", "never confirmatory, blind", "OPEN BLOCKER"):
        assert label in prov["notes"]
    assert list(validate(Q8)) == []                                      # schema + semantics


def test_q8_binds_to_identical_params():
    assert T.params_from_spec(Q8, DEFAULTS) == T.params_from_spec(BASE, DEFAULTS)


@pytest.mark.parametrize("units", [7, 9, 10, 100, 8.5, "8", True, 0])
def test_q8_refuses_any_other_quantity(units):
    for key in ("units", "max_units"):
        s = copy.deepcopy(Q8)
        s["position_sizing"][key] = units
        with pytest.raises(SignalInputError, match="TA01_SIZING"):
            T.params_from_spec(s, DEFAULTS)


@pytest.mark.parametrize("units", [7, 8, 9, 10, 99])
def test_ta01_base_still_refuses_any_quantity_but_100(units):
    s = copy.deepcopy(BASE)
    s["position_sizing"]["units"] = s["position_sizing"]["max_units"] = units
    with pytest.raises(SignalInputError, match="TA01_SIZING"):
        T.params_from_spec(s, DEFAULTS)


def test_q8_refuses_any_other_change_and_unknown_ids():
    s = copy.deepcopy(Q8)
    s["experiment"]["seed"] += 1                                        # not a rule, but the spec is pinned whole
    with pytest.raises(SignalInputError, match="differs from the reviewed spec"):
        T.params_from_spec(s, DEFAULTS)
    s = copy.deepcopy(Q8)
    s["strategy_id"] = "p5c_ta01_q9_res20_strongclose_relvol_tsm_15m"
    with pytest.raises(SignalInputError, match="no reviewed sizing"):
        T.params_from_spec(s, DEFAULTS)
    s = copy.deepcopy(Q8)
    s["provenance"]["parent_spec_id"] = None
    s["provenance"]["spec_sha256"] = None
    blank = copy.deepcopy(s)
    s["provenance"]["spec_sha256"] = canonical_hash(blank)
    with pytest.raises(SignalInputError):
        T.params_from_spec(s, DEFAULTS)
