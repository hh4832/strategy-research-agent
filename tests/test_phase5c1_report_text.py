"""Phase 5-C1 report text: run_info limitations are derived from the archive, never fixed to a fixture.

SYNTHETIC_FIXTURE: the small-sample case uses the Phase 4-C synthetic fixture archive end to end; the multi-year
and blocked cases use minimal acceptance dicts shaped like phase4c.accept output.
"""
import csv
import hashlib
from pathlib import Path

from strategy_agent.evaluation import phase4c as C

ROOT = Path(__file__).resolve().parents[1]
ARCH = ROOT / "tests/fixtures/phase4c/20261106_224000_0123456"
GOV = ROOT / "tests/fixtures/phase4c/governance_root"
SHA = "a" * 40
AT = "2026-11-07T09:00:00+08:00"


def _accept():
    pin = hashlib.sha256((ARCH / "MANIFEST.json").read_bytes()).hexdigest()
    return C.accept(ARCH, expected_manifest_sha256=pin, evaluator_commit_sha=SHA, evaluated_at=AT, governance_root=GOV)


def _info(acc):
    return dict(line.split("=", 1) for line in C._info(acc, SHA, AT, None).splitlines())


def _acc(n, days, status, suff, flags=("IN_SAMPLE_VS_OUT_OF_SAMPLE_NOT_TESTED", "BLINDNESS_UNVERIFIED"),
         market="NO_GO", basis="STRICT_REPLAY_OF_CORE_ACTIVATION_RULE"):
    return {"status": "PASS", "identity": {}, "evaluation": {"evidence_sufficiency": suff,
                                                             "risk_flags": [{"code": c} for c in flags]},
            "checks": {"C6_research_evaluation": {"scenarios": {"BASE": {"facts": {
                           "valid_trades": n, "elapsed_days": days, "sample_status": status}}}},
                       "C8_governance_isolation": {"formal_market_status": market},
                       "C3_trade_integrity": {"scenarios": {"BASE": {
                           "verified_noop_order_ids": ["0003-X"] * 129,
                           "noop_evidence": {"basis": basis, "unit_quantity": 100.0,
                                             "unit_quantity_basis": "FIRST_ACTIVATION_FROM_FLAT_BOOK",
                                             "core_lifecycle_ref": "market-research-core@50ea3bf execution.py"}}}}}}


def test_small_sample_fixture_text_uses_its_own_trade_count():
    acc = _accept()
    n = sum(1 for r in csv.DictReader((ARCH / "trades.csv").open()) if r["scenario"] == "BASE"
            and r["invalidated"] != "True")
    info = _info(acc)
    lim = info["limitations"]
    assert f"{n} valid trades < 30 minimum (sample INSUFFICIENT; evidence UNDETERMINED)" in lim
    assert "(< 1 year)" in lim and "no OOS" in lim and "BLINDNESS_UNVERIFIED" in lim
    assert "Formal Market NO_GO" in lim and lim.endswith("not evidence the strategy works")
    assert info["sample_valid_trades"] == str(n) and info["sample_min_valid_trades"] == "30"
    assert info["sample_status"] == "INSUFFICIENT"
    if n != 13:
        assert "13 trades" not in lim and "13 valid" not in lim


def test_multi_year_1196_trades_is_never_described_as_13():
    acc = _acc(1196, 2996.4, "SUFFICIENT", "DESCRIPTIVE_ONLY")
    info = _info(acc)
    lim = info["limitations"]
    assert "1196 valid trades >= 30 minimum (sample SUFFICIENT; evidence DESCRIPTIVE_ONLY)" in lim
    assert "2996 days observed" in lim and "< 1 year" not in lim
    assert "13" not in lim and "< 30" not in lim
    assert info["sample_valid_trades"] == "1196" and info["sample_status"] == "SUFFICIENT"


def test_insufficient_for_other_reasons_does_not_claim_below_minimum():
    lim = C.limitations_text(_acc(45, 400.0, "INSUFFICIENT", "UNDETERMINED"))
    assert "45 valid trades (sample INSUFFICIENT; evidence UNDETERMINED)" in lim and "< 30" not in lim


def test_flags_and_market_come_from_evidence_not_constants():
    lim = C.limitations_text(_acc(1196, 2996.4, "SUFFICIENT", "DESCRIPTIVE_ONLY", flags=(), market="GO"))
    assert "no OOS" not in lim and "BLINDNESS_UNVERIFIED" not in lim and "Formal Market GO" in lim
    lim = C.limitations_text(_acc(1196, 2996.4, "SUFFICIENT", "DESCRIPTIVE_ONLY", market=None))
    assert "Formal Market status UNKNOWN" in lim


def test_blocked_acceptance_does_not_invent_a_sample():
    acc = {"status": "BLOCKED", "checks": {"C2_archive_integrity": {"status": "FAIL"}}}
    info = _info(acc)
    assert "sample size not evaluated" in info["limitations"]
    assert info["sample_valid_trades"] == "null" and info["c3_noop_evidence_note"] == "C3 not evaluated"


def test_noop_is_labelled_as_core_rule_reconstruction_not_native_events(tmp_path):
    info = _info(_acc(1196, 2996.4, "SUFFICIENT", "DESCRIPTIVE_ONLY"))
    assert info["c3_noop_evidence_basis"] == "STRICT_REPLAY_OF_CORE_ACTIVATION_RULE"
    assert "reconstructed by replaying the Core activation rule" in info["c3_noop_evidence_note"]
    assert "NOT native Core order_events" in info["c3_noop_evidence_note"]
    assert info["c3_noop_orders_verified"] == "129"
    native = _info(_acc(1196, 2996.4, "SUFFICIENT", "DESCRIPTIVE_ONLY", basis="CORE_ORDER_EVENTS"))
    assert "native Core order_events" in native["c3_noop_evidence_note"] and "NOT" not in native["c3_noop_evidence_note"]
    acc = _accept()                                                # written files carry the same labels
    out = tmp_path / "ev"
    C.write_acceptance(acc, out, evaluator_commit_sha=SHA, evaluated_at=AT)
    assert "c3_noop_evidence_basis=STRICT_REPLAY_OF_CORE_ACTIVATION_RULE" in (out / "run_info.txt").read_text()
    md = (out / "archive_contract_mapping.md").read_text()
    assert "NOT native Core order_events" in md and "STRICT_REPLAY_OF_CORE_ACTIVATION_RULE" in md
