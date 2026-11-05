"""TSM research cost assumption IBKR_PRO_FIXED (RESEARCH_ONLY, research lead 2026-11-05).

The Core-format configurations in configs/core_cost_profiles are loaded with Core's OWN loader
(market_research_core.load_cost_profile @ 699e3ad) and priced with Core's OWN calculate_fill_costs: nothing here
re-implements a cost. Checks: pins, dated SEC Section 31 selection, Fixed per-share / minimum / 1% cap,
BASE / LOW / STRESS slippage, half-spread 0, fail-closed outside the evidenced dates, the documented Core gap for
FINRA TAF, and that research acceptance never upgrades a formal gate.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from strategy_agent.adapters import mrc_adapter as A

ROOT = Path(__file__).resolve().parents[1]
CFG = {s: ROOT / "configs" / "core_cost_profiles" / f"ibkr_pro_fixed_tsm_research_{s.lower()}_v1.json"
       for s in ("BASE", "LOW", "STRESS")}
EVIDENCE = ROOT / "docs" / "evidence" / "tsm_cost_assumptions.json"
needs_core = pytest.mark.skipif(not A.core_supports_conservative(), reason="Core M3-A.4 not installed")


def _ev():
    return json.loads(EVIDENCE.read_text(encoding="utf-8"))


def test_evidence_pins_match_committed_configurations():
    ev = _ev()
    for s, p in CFG.items():
        c = json.loads(p.read_text(encoding="utf-8"))
        sc = ev["core_configurations"][s]
        assert sc["configuration_sha256"] == c["configuration_sha256"]
        assert sc["path"] == str(p.relative_to(ROOT))
        assert c["profile"]["approval_status"] == "DRAFT"                       # never BROKER_ACTUAL / APPROVED
        assert c["applicability"]["verification_status"] == "UNVERIFIED"
        assert c["profile"]["basis"] == "ASSUMED" and "BROKER_ACTUAL" not in json.dumps(c)
    a = ev["assumption"]
    assert (a["cost_model"], a["approval_scope"], a["status"]) == ("IBKR_PRO_FIXED", "RESEARCH_ONLY",
                                                                   "ACCEPTED_RESEARCH_ONLY")
    assert a["slippage_bps_per_side"] == {"BASE": 2, "LOW": 1, "STRESS": 4} and a["half_spread_bps"] == 0


@needs_core
@pytest.mark.parametrize("scenario,bps", [("BASE", 2), ("LOW", 1), ("STRESS", 4)])
def test_core_loader_accepts_and_pins_each_scenario(scenario, bps):
    from market_research_core import configuration_sha256, load_cost_profile
    c = json.loads(CFG[scenario].read_text(encoding="utf-8"))
    assert configuration_sha256(c) == c["configuration_sha256"]
    p = load_cost_profile(CFG[scenario], symbol="TSM", as_of="2024-06-03")
    ev = _ev()["core_configurations"][scenario]
    assert p.sha256 == ev["resolved_profile_sha256_by_as_of"]["2024-06-03"]
    assert (p.commission_scheme, p.fee_mode, p.equity_commission_per_share, p.minimum_commission,
            p.commission_cap_rate, p.equity_slippage_bps, p.half_spread_bps) == \
        ("FIXED", "COMPONENTIZED", 0.005, 1, 0.01, bps, 0)
    assert not p.exchange_fee_per_unit and not p.clearing_fee_per_unit        # exchange fees inside Fixed commission
    assert p.approval_status == "DRAFT" and set(p.component_approval_status.values()) == {"PENDING"}


@needs_core
@pytest.mark.parametrize("as_of,per_million", [
    ("2016-10-19", 21.80), ("2017-07-03", 21.80), ("2017-07-04", 23.10), ("2018-05-22", 13.00),
    ("2019-04-16", 20.70), ("2020-02-18", 22.10), ("2021-02-25", 5.10), ("2022-05-14", 22.90),
    ("2023-02-27", 8.00), ("2024-05-22", 27.80), ("2025-05-13", 27.80), ("2025-05-14", 0.00),
    ("2026-04-03", 0.00), ("2026-04-04", 20.60), ("2026-10-16", 20.60)])
def test_dated_sec_section_31_selection(as_of, per_million):
    from market_research_core import load_cost_profile
    p = load_cost_profile(CFG["BASE"], symbol="TSM", as_of=as_of)
    assert Decimal(str(p.sell_regulatory_fee_rate)) == Decimal(str(per_million)) / Decimal(1_000_000)
    meta = p.configuration_metadata
    assert meta["regulatory_version"].startswith("SEC31-") and meta["effective_from"] <= as_of


@needs_core
@pytest.mark.parametrize("as_of", ["2016-10-18", "2026-10-17", "2027-01-04"])
def test_outside_evidenced_dates_fails_closed_never_zero(as_of):
    from market_research_core import load_cost_profile
    with pytest.raises(ValueError):
        load_cost_profile(CFG["BASE"], symbol="TSM", as_of=as_of)


@needs_core
def test_fixed_commission_minimum_per_share_and_one_percent_cap_known_answers():
    from market_research_core import load_cost_profile
    from market_research_core.costs import calculate_fill_costs
    p = load_cost_profile(CFG["BASE"], symbol="TSM", as_of="2024-06-03")
    # per-share: 1000 sh @ 150 -> 5.00 (min 1, cap 1500)
    assert calculate_fill_costs(150.0, 1000, "BUY", p)["commission"] == Decimal("5.00")
    # minimum: 100 sh @ 150 -> 0.50 -> 1.00
    assert calculate_fill_costs(150.0, 100, "BUY", p)["commission"] == Decimal("1.00")
    # cap: 100 sh @ 0.50 -> notional 50, min 1.00 capped at 1% = 0.50
    assert calculate_fill_costs(0.50, 100, "BUY", p)["commission"] == Decimal("0.50")
    # SEC Section 31 on sells only: 100 sh @ 150 x 27.80/1e6 = 0.417 (rounded by Core HALF_UP to 0.42)
    sell = calculate_fill_costs(150.0, 100, "SELL", p)
    buy = calculate_fill_costs(150.0, 100, "BUY", p)
    assert buy["regulatory_fee"] == 0 and sell["regulatory_fee"] in (Decimal("0.417"), Decimal("0.42"))
    assert sell["exchange_fee"] == sell["clearing_fee"] == sell["transaction_tax"] == 0


@needs_core
def test_slippage_scenarios_are_separate_profiles_not_a_multiplier():
    """BASE/LOW/STRESS differ ONLY in equity_slippage_bps; commissions and fees are identical (a Core
    cost_multiplier would also scale the commission, so it is not used for these scenarios)."""
    cs = {s: json.loads(p.read_text(encoding="utf-8")) for s, p in CFG.items()}
    strip = lambda c: {k: v for k, v in c["profile"].items()  # noqa: E731
                       if k not in ("profile_id", "equity_slippage_bps", "component_evidence")}
    assert strip(cs["BASE"]) == strip(cs["LOW"]) == strip(cs["STRESS"])
    assert cs["BASE"]["regulatory_schedule"] == cs["LOW"]["regulatory_schedule"] == cs["STRESS"]["regulatory_schedule"]


@needs_core
def test_finra_taf_is_a_recorded_core_gap_not_silently_dropped():
    """TAF is sell-only, per share, capped per trade and dated; Core 699e3ad has no field for that shape
    (regulatory_fee_per_unit charges both sides, uncapped, undated). It is recorded as a blocker, never zeroed."""
    import market_research_core as core
    fields = set(core.CostProfile.__dataclass_fields__)
    assert "regulatory_fee_per_unit" in fields
    assert not {f for f in fields if "sell_regulatory_fee_per_unit" in f or "regulatory_fee_cap" in f}
    ev = _ev()
    taf = ev["regulatory_fees"]["FINRA_TAF"]
    assert taf["core_expressible"] is False and taf["status_in_run"] == "BLOCKER_NOT_MODELLED"
    assert all(r["side"] == "SELL" for r in taf["schedule"])
    assert {"CORE_GAP_TAF", "CAT_FEE_PASS_THROUGH_UNKNOWN"} <= {b["id"] for b in ev["blockers"]}


def test_research_acceptance_never_upgrades_formal_gates():
    e = json.loads((ROOT / "governance" / "market_readiness.json").read_text())["markets"]["IBKR_TSM"]
    ca = e["facts"]["cost_research_assumption"]
    assert (ca["cost_model"], ca["approval_scope"], ca["status"]) == \
        ("IBKR_PRO_FIXED", "RESEARCH_ONLY", "ACCEPTED_RESEARCH_ONLY")
    assert ca["rate_evidence"] != "BROKER_ACTUAL"
    g = e["gates"]
    assert g["COST_APPROVAL"]["status"] == "FAIL" and g["COST_APPROVAL"]["approved_by"] is None
    assert g["TEMPORAL_SAFETY"]["status"] == "UNKNOWN" and g["HOLDOUT_APPROVAL"]["status"] == "FAIL"
    assert g["FORMAL_EXECUTION_ELIGIBILITY"]["status"] == "FAIL" and e["status"] == "NO_GO"
    assert (ROOT / "governance" / "holdout_locks.jsonl").read_text() == ""   # no lock created by the Agent
