"""Phase 5 shared Research Request contract (schemas/RESEARCH_REQUEST_SCHEMA.json v1.0.0)."""
import copy
import json
from pathlib import Path

import pytest

from strategy_agent.orchestration import request as Q

ROOT = Path(__file__).resolve().parents[1]
EX = ROOT / "docs/examples/research_requests"
REPLAY = Q.load(EX / "tsm_sma520_dev_archive_replay.json")
REAL = Q.load(EX / "tsm_sma520_dev_research_only_real_DISABLED.json")


def bad(**edits):
    d = copy.deepcopy(REPLAY)
    for k, v in edits.items():
        if v is KeyError:
            d.pop(k)
        else:
            d[k] = v
    return Q.validate(d)


def test_schema_is_valid_draft_2020_12_and_examples_validate():
    from jsonschema import Draft202012Validator
    Draft202012Validator.check_schema(Q.schema())
    assert Q.validate(REPLAY) == [] and Q.validate(REAL) == []


def test_every_contract_field_is_present_and_required():
    req = set(Q.schema()["required"])
    for f in ("schema_version", "request_id", "hypothesis", "market_mechanism", "strategy_family", "strategy_spec_ref",
              "strategy_spec_sha256", "dataset_snapshot_id", "dataset_manifest_sha256", "core_commit", "signal_time_rule",
              "execution_time_rule", "cost_profile_ref", "requested_change", "research_budget", "authorization_ref",
              "holdout_policy", "requested_outputs"):
        assert f in req, f
        assert bad(**{f: KeyError}), f


@pytest.mark.parametrize("field,value", [
    ("strategy_spec_sha256", "b358416e"), ("dataset_manifest_sha256", "PENDING"), ("dataset_manifest_sha256", "8b6fc838"),
    ("core_commit", "b16179a"), ("core_commit", "B16179A7CC077C36046574769A32C367FA476CAE"),
    ("strategy_spec_sha256", "PENDING")])
def test_abbreviated_or_pending_hashes_are_rejected_where_not_pendable(field, value):
    assert bad(**{field: value})


def test_only_core_commit_and_authorization_ref_may_be_pending():
    d = copy.deepcopy(REPLAY)
    d["core_commit"] = "PENDING"
    d["authorization_ref"] = "PENDING"
    assert Q.validate(d) == [] and Q.pending_fields(d) == ["core_commit", "authorization_ref"]
    assert Q.pending_fields(REPLAY) == []


@pytest.mark.parametrize("extra", [{"approved": True}, {"gate_status": "PASS"}, {"authorization_valid": True},
                                   {"holdout_unlocked": True}, {"budget_override": 5}, {"force_run": True}])
def test_a_request_cannot_assert_governance_facts(extra):
    d = copy.deepcopy(REPLAY)
    d.update(extra)
    assert Q.validate(d)


def test_holdout_access_can_only_be_none():
    assert bad(holdout_policy={"access": "READ", "lock_id": "HOLDOUT-TSM-15M-v1"})


def test_mode_coupling():
    r = copy.deepcopy(REPLAY)
    r.pop("replay_source")
    assert Q.validate(r)                                            # replay needs a source
    r = copy.deepcopy(REPLAY)
    r["research_budget"] = {"batches_requested": 1}
    assert Q.validate(r)                                            # replay consumes nothing
    r = copy.deepcopy(REAL)
    r["research_budget"] = {"batches_requested": 0}
    assert Q.validate(r)
    s = copy.deepcopy(REPLAY)
    s.update(execution_mode="SYNTHETIC_TEST", requested_change={"kind": "SYNTHETIC_TEST", "description": "t"})
    assert Q.validate(s) == []
    s["research_budget"] = {"batches_requested": 1}
    assert Q.validate(s)


def test_window_semantics_and_paths():
    assert bad(research_window={"warmup_start": "2024-06-03T00:00:00-04:00", "start": "2024-06-01T00:00:00-04:00",
                                "end": "2024-06-29T00:00:00-04:00"})
    assert bad(research_window={"warmup_start": "2024-05-29T00:00:00", "start": "2024-06-03T00:00:00-04:00",
                                "end": "2024-06-29T00:00:00-04:00"})            # offset required
    assert bad(strategy_spec_ref="../outside.json") and bad(strategy_spec_ref="/etc/passwd")


def test_request_hash_is_deterministic_and_content_sensitive():
    assert Q.request_hash(REPLAY) == Q.request_hash(json.loads(json.dumps(REPLAY)))
    d = copy.deepcopy(REPLAY)
    d["hypothesis"] += " "
    assert Q.request_hash(d) != Q.request_hash(REPLAY)


def test_requested_outputs_must_include_research_decision():
    assert bad(requested_outputs=["RISK_FLAGS"])


def test_non_object_input_never_raises():
    assert Q.validate(None) and Q.validate([]) and Q.validate("x")
