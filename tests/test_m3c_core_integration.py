"""M3-C Phase C: Agent <-> Core M3-A.4 (699e3ad) minimal integration acceptance.

The conservative end-to-end batch (SMA 5/20 on the native-1m TEST_ONLY fixture, BASE / LOW / STRESS_2X, TRAIN +
VALIDATION) runs ONCE per session (module fixture) and the 14 acceptance items are asserted on it. Tests that need
Core M3-A.4 skip when the installed Core lacks it; pure-Agent tests (mapping rules, N1 comparator) always run.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from strategy_agent.adapters import mrc_adapter as A
from strategy_agent.research import reproducibility as R
from strategy_agent.research.environment import ROOT, synthetic_componentized_cost_profile

CFG = ROOT / "configs" / "research" / "sma_5_20_tsm_conservative_synthetic_local_v1.json"
CFG_DRIVE = ROOT / "configs" / "research" / "sma_5_20_tsm_conservative_synthetic_v1.json"
AGENT = {"agent_sha": "abcdef1234567890abcdef1234567890abcdef12", "agent_tree_dirty": False, "agent_branch": "t"}
FORMAL = [ROOT / "registry" / "experiments.jsonl", ROOT / "registry" / "families.jsonl",
          ROOT / "governance" / "holdout_locks.jsonl", ROOT / "governance" / "controls.jsonl",
          ROOT / "governance" / "market_readiness.json", ROOT / "governance" / "source_confirmations.json"]
SCENARIOS = ("BASE", "LOW_FRICTIONLESS", "STRESS_2X")

needs_m3a4 = pytest.mark.skipif(not A.core_supports_conservative(), reason="Core M3-A.4 conservative API not installed")


def _digest():
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None for p in FORMAL}


def _csv(path: Path) -> list[dict]:
    import csv
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


# =========================================================== the one bounded E2E batch
@pytest.fixture(scope="module")
def e2e(tmp_path_factory):
    if not A.core_supports_conservative():
        pytest.skip("Core M3-A.4 conservative API not installed")
    from strategy_agent.research.entry import run_config
    tmp = tmp_path_factory.mktemp("m3c")
    before = _digest()
    s = run_config(CFG, run_root=tmp / "env", archive_root=tmp / "arch", agent=AGENT, progress=lambda m: None,
                   batch_id="m3c")
    reg_lines = (tmp / "env" / "experiments.jsonl").read_text().splitlines()
    s2 = run_config(CFG, run_root=tmp / "env", archive_root=tmp / "arch", agent=AGENT, progress=lambda m: None,
                    batch_id="m3c", resume=True)
    rows = {(r["role"], r["scenario_id"]): r for r in s["cost_sensitivity"]["rows"]}
    return {"tmp": tmp, "s": s, "s2": s2, "rows": rows, "reg_lines": reg_lines, "before": before, "after": _digest()}


def _arch(e2e, role="TRAIN", sc="BASE") -> Path:
    return Path(e2e["rows"][(role, sc)]["archive_path"])


# 1. synthetic SMA 5/20 through the whole pipeline
@needs_m3a4
def test_01_e2e_sma_5_20_completes_and_archives(e2e):
    runs = e2e["s"]["runs"]
    assert len(runs) == 6 and all(o["status"] == "COMPLETED" and o["archive_status"] == "ARCHIVED" for o in runs)
    for (role, sc), r in e2e["rows"].items():
        d = Path(r["archive_path"])
        assert _json(d / "experiment_record.json")[-1]["trial"]["parameters"] == {"fast": 5, "slow": 20}
        assert len(_csv(d / "decisions.csv")) > 0 and len(_csv(d / "fills.csv")) > 0
        stages = {x["name"]: x["status"] for x in _json(d / "stages.json")}
        assert set(stages.values()) == {"OK"}, stages


# 2. START / END conservative temporal convention, RTH-only, missing / zero-volume fail closed
@needs_m3a4
def test_02_start_end_envelope_rth_only_and_defects_fail_closed(e2e):
    import market_research_core as core
    from strategy_agent.research.data import NativeSyntheticSource
    md = NativeSyntheticSource().load(symbol="TSM", warmup_start="2025-01-06T00:00:00-05:00",
                                      window_end="2025-02-20T00:00:00-05:00")
    counts = md.metadata["eligibility_counts"]["rejection_reasons"]
    assert counts == {"OUTSIDE_CONSERVATIVE_RTH": 30, "CLOSING_AUCTION_EXCLUDED": 30,
                      "INCOMPLETE_SOURCE_BUCKET": 1, "ZERO_VOLUME_BAR": 1}
    f = md.frame
    one = __import__("pandas").Timedelta(minutes=1)
    for b in f.to_dict("records"):
        labels = sorted(__import__("pandas").Timestamp(r["source_timestamp"]) for r in b["source_rows"])
        t0, tn = labels[0], labels[-1]
        # START hypothesis: [t0, tn+1m); END hypothesis: [t0-1m, tn). Envelope = both.
        assert b["bar_start"] == t0 - one and b["possible_open_latest"] == t0
        assert b["available_at"] == b["modeled_available_at"] == tn + one
        if b["execution_rejection_reason"] in ("INCOMPLETE_SOURCE_BUCKET", "ZERO_VOLUME_BAR",
                                               "OUTSIDE_CONSERVATIVE_RTH", "CLOSING_AUCTION_EXCLUDED"):
            assert not b["signal_eligible"] and not b["buy_execution_eligible"] and not b["sell_execution_eligible"]
    # ETH labels never become eligible (Core RTH-only); policy pin
    import pandas as pd
    eth = [{"source_timestamp": pd.Timestamp("2025-01-07 09:00", tz="America/New_York") + i * one, "symbol": "TSM",
            "contract": "TSM", "open": 100.0, "high": 100.1, "low": 99.9, "close": 100.0, "volume": 1000.0}
           for i in range(45)]
    eb = core.prepare_conservative_bars(pd.DataFrame(eth), bar_minutes=15)
    assert not eb.signal_eligible.any() and not eb.buy_execution_eligible.any()
    assert md.metadata["temporal_policy"]["sha256"] == core.CONSERVATIVE_POLICY.sha256
    # no fill ever lands in an ineligible bucket
    bad = {b["bar_start"].isoformat() for b in md.rows if b["execution_rejection_reason"]}
    for r in e2e["rows"].values():
        for fl in _csv(Path(r["archive_path"]) / "fills.csv"):
            assert fl["exec_ts"] not in bad


# 3. decision time vs fill time
@needs_m3a4
def test_03_decision_at_modeled_availability_fill_strictly_later(e2e):
    for r in e2e["rows"].values():
        d = Path(r["archive_path"])
        decisions = {x["decision_ts"] for x in _csv(d / "decisions.csv")}
        for fl in _csv(d / "fills.csv"):
            sig, ex = fl["signal_available_at"], fl["exec_ts"]
            assert fl["signal_time"] == fl["modeled_available_at"] == sig
            assert sig in decisions                                   # the fill answers a recorded decision
            assert __import__("pandas").Timestamp(ex) > __import__("pandas").Timestamp(sig)
            assert fl["source_timestamp_label"] == "UNKNOWN"
            assert fl["temporal_assumption"].startswith("UNKNOWN source label; END completes at t, START at t+1m")
            assert fl["temporal_policy_id"] == "CONSERVATIVE_LABEL_AGNOSTIC"


# 4. CostProfile BASE / LOW / STRESS
@needs_m3a4
def test_04_base_low_stress_scale_every_ledger_cost(e2e):
    for role in ("TRAIN", "VALIDATION"):
        pnl = {sc: e2e["rows"][(role, sc)]["net_pnl"] for sc in SCENARIOS}
        assert pnl["LOW_FRICTIONLESS"] > pnl["BASE"] > pnl["STRESS_2X"]
        led = {sc: {f["order_id"]: f for f in _csv(_arch(e2e, role, sc) / "fills.csv")} for sc in SCENARIOS}
        assert set(led["BASE"]) == set(led["LOW_FRICTIONLESS"]) == set(led["STRESS_2X"])   # same orders
        for oid, b in led["BASE"].items():
            lo, st = led["LOW_FRICTIONLESS"][oid], led["STRESS_2X"][oid]
            assert Decimal(lo["explicit_cost"]) == 0 and Decimal(lo["price_adjustment_cost"]) == 0
            assert Decimal(lo["fill_price"]) == Decimal(lo["raw_price"])
            for k in ("commission", "exchange_fee"):           # price-independent components scale exactly
                assert Decimal(st[k]) == 2 * Decimal(b[k])
            assert Decimal(st["explicit_cost"]) > Decimal(b["explicit_cost"])     # regulatory: on a worse fill


# 5. Core ledger vs Agent metrics; known answers for the TEST_ONLY profile (independent arithmetic)
@needs_m3a4
def test_05_ledger_equals_metrics_and_known_answer_costs(e2e):
    p = synthetic_componentized_cost_profile()
    rate = Decimal(str(next(c["rate"] for c in p["components"] if c["kind"] == "REGULATORY_FEE")))
    for r in e2e["rows"].values():
        d = Path(r["archive_path"])
        fills, m = _csv(d / "fills.csv"), _json(d / "metrics.json")["metrics"]
        mult = Decimal(str(_json(d / "experiment_record.json")[-1]["cost_multiplier"]))
        assert m["costs"]["fees"] == pytest.approx(sum(float(f["fee"]) for f in fills), abs=1e-9)
        assert m["costs"]["regulatory_fees"] == pytest.approx(sum(float(f["regulatory_fee"]) for f in fills), abs=1e-9)
        assert m["costs"]["total"] == pytest.approx(sum(float(f["explicit_cost"]) for f in fills), abs=1e-9)
        for f in fills:
            q = Decimal(f["qty"])
            assert Decimal(f["commission"]) == max(Decimal("0.005") * q, Decimal("1.0")) * mult
            assert Decimal(f["exchange_fee"]) == Decimal("0.0003") * q * mult
            want_reg = Decimal(f["fill_price"]) * q * rate * mult if f["side"] == "SELL" else Decimal(0)
            assert abs(Decimal(f["regulatory_fee"]) - want_reg) <= Decimal("1e-9")
            assert Decimal(f["transaction_tax"]) == 0 and Decimal(f["clearing_fee"]) == 0
            raw, fp = Decimal(f["raw_price"]), Decimal(f["fill_price"])
            adverse = (fp - raw) if f["side"] == "BUY" else (raw - fp)
            assert raw * Decimal("0.0002") * mult <= adverse + Decimal("1e-12") < raw * Decimal("0.0002") * mult + Decimal("0.01")
            assert f["commission_scope"] == "COMPONENTIZED"


# 6-9. provenance and pins: engine SHA, temporal policy SHA, CostProfile SHA (agent + Core), dataset snapshot
@needs_m3a4
def test_06_09_provenance_and_pins(e2e):
    import market_research_core as core
    cfg = _json(CFG)
    eng = A.core_identity()["git_sha"]
    for r in e2e["rows"].values():
        d = Path(r["archive_path"])
        rec = _json(d / "experiment_record.json")[-1]
        refs = _json(d / "snapshot_refs.json")
        st = {x["name"]: x for x in _json(d / "stages.json")}["core_execution"]["outputs"]
        assert set(st["contract_checks"].values()) == {"PASS"} and "CC08_conservative_research_only" in st["contract_checks"]
        assert set(v for v in st["core_m3a4_contract_checks"].values()) <= {"PASS", "NOT_APPLICABLE_OPEN_POSITION",
                                                                            "NOT_APPLICABLE_NO_ROUND_TRIPS"}
        blob = json.dumps(refs) + json.dumps(rec)
        assert eng in blob                                                          # 7 engine SHA
        assert core.CONSERVATIVE_POLICY.sha256 in blob or st["contract_checks"]["CC05_temporal_policy"] == "PASS"
        assert cfg["costs"]["profile_ref"]["sha256"] in blob                        # 8 agent CostProfile SHA
        assert cfg["dataset"]["snapshot_id"] in blob                                # 9 dataset snapshot
        for f in _csv(d / "fills.csv"):
            assert f["temporal_policy_sha256"] == core.CONSERVATIVE_POLICY.sha256
            assert f["execution_eligibility_scope"] == "RESEARCH_ONLY"


# 10-11. append-only registry and resume integrity
@needs_m3a4
def test_10_11_append_only_and_resume(e2e):
    assert [o["status"] for o in e2e["s2"]["runs"]] == ["SKIPPED_RESUME"] * 6
    now = (e2e["tmp"] / "env" / "experiments.jsonl").read_text().splitlines()
    assert now[: len(e2e["reg_lines"])] == e2e["reg_lines"] and len(now) == len(e2e["reg_lines"])
    fams = {json.loads(x)["trial"]["family_id"] for x in now}
    assert len(fams) == 1                                    # all scenarios / windows: one family, one trial


# 12. manifest integrity (and it detects tampering)
@needs_m3a4
def test_12_manifest_and_self_hash(e2e, tmp_path):
    import shutil
    from strategy_agent.research.archive import ArchiveError, verify_manifest
    for r in e2e["rows"].values():
        assert R.archive_integrity(Path(r["archive_path"]))["status"] == "PASS"
    c = tmp_path / "copy"
    shutil.copytree(_arch(e2e), c)
    (c / "fills.csv").write_text((c / "fills.csv").read_text() + "\n")
    with pytest.raises(ArchiveError):
        verify_manifest(c)


# 13. formal registry / holdout untouched
@needs_m3a4
def test_13_formal_holdout_and_registry_untouched(e2e):
    assert e2e["before"] == e2e["after"]


# N1 in CI: this environment reproduces the committed reference numerically (bitwise only reported)
@needs_m3a4
def test_15_numeric_reproducibility_vs_committed_reference(e2e):
    ref = _json(ROOT / "docs" / "evidence" / "m3c_reference_metrics.json")
    assert ref["core_sha"] == A.core_identity()["git_sha"]
    assert set(ref["runs"]) == {f"{r}/{s}" for r, s in e2e["rows"]}
    for (role, sc), r in e2e["rows"].items():
        got = _json(Path(r["archive_path"]) / "metrics.json")
        want = ref["runs"][f"{role}/{sc}"]
        cmp = R.compare_metrics(want["metrics"], got["metrics"], sha_a=want["metrics_sha256"],
                                sha_b=got["metrics_sha256"])
        assert cmp["numeric_reproducibility"] == "PASS", cmp["differences"][:5]
        assert R.archive_integrity(Path(r["archive_path"]))["status"] == "PASS"


# 14. market gate stays fail-closed for real TSM on the new Core
def test_14_real_market_gate_fail_closed():
    from strategy_agent.research.data import GatedMarketSource, MarketGateClosed, readiness
    rd = readiness("IBKR_TSM")
    g = rd.gate_status()
    assert rd.status == "NO_GO" and rd.missing()
    assert g["FORMAL_EXECUTION_ELIGIBILITY"] != "PASS" and g["COST_APPROVAL"] != "PASS"
    assert g["HOLDOUT_APPROVAL"] != "PASS" and g["TEMPORAL_SAFETY"] != "PASS"
    with pytest.raises(MarketGateClosed):
        GatedMarketSource("IBKR_TSM").load(symbol="TSM", warmup_start="2025-01-01T00:00:00-05:00",
                                           window_end="2025-02-01T00:00:00-05:00")


# =========================================================== contract tamper tests (direct Core run)
def _direct(core, qty=100.0, extra_sell=True):
    import pandas as pd
    from strategy_agent.ports import BacktestRequest, DatasetRef  # noqa: F401
    from strategy_agent.research.native_fixture import load
    rows = [r for r in load().rows if r["source_timestamp"].startswith(("2025-01-07", "2025-01-08"))]
    src = pd.DataFrame([{"source_timestamp": pd.Timestamp(r["source_timestamp"]), "symbol": "TSM", "contract": "TSM",
                         "open": float(r["open"]), "high": float(r["high"]), "low": float(r["low"]),
                         "close": float(r["close"]), "volume": float(r["volume"])} for r in rows])
    bars = core.prepare_conservative_bars(src, bar_minutes=15)
    elig = bars[bars.signal_eligible].reset_index(drop=True)
    t1, t2 = elig.modeled_available_at.iloc[2], elig.modeled_available_at.iloc[10]
    mk = lambda oid, side, t: core.ExecutionOrder(  # noqa: E731
        symbol="TSM", market="IBKR_EQUITY", contract="TSM", signal_time=t, signal_available_at=t, order_time=t,
        side=side, strategy_id="s", experiment_id="e", quantity=qty, time_in_force="DAY", order_id=oid)
    o = [mk("o1", "BUY", t1)] + ([mk("o2", "SELL", t2)] if extra_sell else [])
    cp = A.core_cost_profile(synthetic_componentized_cost_profile(), instrument_type="EQUITY", tick_size=0.01, core=core)
    cfg = core.ExecutionConfig(initial_cash=100000.0, bar_label="UNKNOWN", session_semantics="CONFIRMED",
                               timestamp_timezone="America/New_York", available_at_policy=A.CONSERVATIVE,
                               temporal_policy=core.CONSERVATIVE_POLICY, core_commit_sha="x" * 40,
                               dataset_snapshot_id="d", dataset_producer_sha="p", dataset_verdict="GO",
                               permitted_usage_scope="RESEARCH_ONLY")
    return core.ExecutionEngine().run(bars, o, cost_profile=cp, config=cfg), o, cp


@needs_m3a4
def test_contract_passes_on_clean_result_including_round_trip_reconciliation():
    import market_research_core as core
    from strategy_agent.adapters.m3a4_contract import validate_conservative_result
    res, orders, cp = _direct(core)
    sha = res.provenance["engine_git_sha"]
    checks = validate_conservative_result(res, orders=orders, cost_profile=cp, policy=core.CONSERVATIVE_POLICY,
                                          core_sha=sha)
    assert checks["K03_round_trip_costs_equal_ledger"] == "PASS" and set(checks.values()) == {"PASS"}


@needs_m3a4
@pytest.mark.parametrize("mutate,check", [
    (lambda r: r.fills.__setitem__("regulatory_fee", r.fills["regulatory_fee"].map(lambda x: Decimal(str(x)) + Decimal("0.01"))),
     "K02_ledger_cost_identities"),
    (lambda r: r.provenance.__setitem__("source_timestamp_label", "START"), "P06_label_unknown"),
    (lambda r: r.provenance.__setitem__("temporal_policy_sha256", "0" * 64), "P04_temporal_policy_sha256"),
    (lambda r: r.provenance.__setitem__("cost_profile_sha256", "0" * 64), "P09_cost_profile_sha256"),
    (lambda r: r.fills.__setitem__("exec_ts", r.fills["signal_available_at"]), "T01_no_lookahead"),
    (lambda r: r.provenance.__setitem__("engine_version", "2.1.0"), "P02_engine_version"),
])
def test_contract_fails_closed_on_tampering(mutate, check):
    import market_research_core as core
    from strategy_agent.adapters.m3a4_contract import validate_conservative_result
    from strategy_agent.ports import ContractViolation
    res, orders, cp = _direct(core)
    res = replace(res, provenance=dict(res.provenance), fills=res.fills.copy())   # ledger is Core's copy of fills
    mutate(res)
    with pytest.raises(ContractViolation, match=check):
        validate_conservative_result(res, orders=orders, cost_profile=cp, policy=core.CONSERVATIVE_POLICY,
                                     core_sha=res.provenance["engine_git_sha"])


# =========================================================== cost mapping (Agent rules; no Core needed)
LEGACY = frozenset({"equity_commission_rate", "minimum_commission"})
NEW = LEGACY | A.M3A4_COST_FIELDS


def test_mapping_is_core_capability_aware():
    p = synthetic_componentized_cost_profile()
    legacy = A.unmappable_cost_components(p, "EQUITY", LEGACY)
    assert any("per-unit equity commission" in x for x in legacy) and any("EXCHANGE_FEE" in x for x in legacy)
    assert any("REGULATORY_FEE" in x for x in legacy) and any("bps" in x for x in legacy)
    assert A.unmappable_cost_components(p, "EQUITY", NEW) == []
    q = copy.deepcopy(p)
    for c in q["components"]:
        if c["kind"] == "REGULATORY_FEE":
            c["rate"] = None                                       # a missing rate is never zero
    assert any("REGULATORY_FEE" in x for x in A.unmappable_cost_components(q, "EQUITY", NEW))
    q = copy.deepcopy(p)
    q["components"].append({"id": "t", "kind": "TAX", "side": "BUY", "rate": 0.001, "basis": "RESEARCH_ASSUMPTION",
                            "source": {}})
    assert any("TAX side BUY" in x for x in A.unmappable_cost_components(q, "EQUITY", NEW))
    fut = copy.deepcopy(p)
    assert any("Core: ticks only for futures" in x for x in A.unmappable_cost_components(fut, "FUTURES", NEW))


@needs_m3a4
def test_mapped_profile_carries_component_basis_evidence_approval_and_legacy_hash_is_unchanged():
    import market_research_core as core
    from strategy_agent.research.environment import synthetic_cost_profile
    cp = A.core_cost_profile(synthetic_componentized_cost_profile(), instrument_type="EQUITY", tick_size=0.01, core=core)
    assert cp.fee_mode == "COMPONENTIZED" and cp.equity_commission_per_share == 0.005
    assert cp.sell_regulatory_fee_rate == 0.0000278 and cp.exchange_fee_per_unit == 0.0003
    assert cp.equity_slippage_bps == 2.0 and cp.equity_commission_rate == 0.0
    for k in ("equity_commission_per_share", "sell_regulatory_fee_rate", "exchange_fee_per_unit",
              "equity_slippage_bps"):
        assert cp.component_approval_status[k] == "APPROVED" and "TEST_ONLY" in cp.component_evidence[k]
    legacy = A.core_cost_profile(synthetic_cost_profile(), instrument_type="EQUITY", tick_size=0.01, core=core)
    assert legacy.fee_mode is None and not legacy.equity_commission_per_share
    # IBKR draft: now representable on Core M3-A.4, but DRAFT/PENDING - representable is not approved
    from strategy_agent.costs.profile import load_profiles
    ib = load_profiles(ROOT / "governance" / "cost_profiles")[("ibkr_us_equity", "0.1.0")] \
        if ("ibkr_us_equity", "0.1.0") in load_profiles(ROOT / "governance" / "cost_profiles") else None
    if ib is not None:
        icp = A.core_cost_profile(ib, instrument_type="EQUITY", tick_size=0.01, core=core)
        assert icp.approval_status == "DRAFT" and set(icp.component_approval_status.values()) == {"PENDING"}


@needs_m3a4
def test_futures_per_contract_commission_known_answer():
    import market_research_core as core
    fut = {"profile_id": "synthetic_futures_test", "version": "1.0.0", "status": "APPROVED", "components": [
        {"id": "c", "kind": "COMMISSION", "side": "BOTH", "per_unit": 50.0, "basis": "RESEARCH_ASSUMPTION"},
        {"id": "t", "kind": "TAX", "side": "BOTH", "rate": 0.00002, "basis": "RESEARCH_ASSUMPTION"},
        {"id": "s", "kind": "SLIPPAGE", "side": "BOTH", "ticks": 1, "basis": "RESEARCH_ASSUMPTION"}],
        "provenance": {"approval_ref": "test"}}
    cp = A.core_cost_profile(fut, instrument_type="FUTURES", tick_size=1.0, contract_multiplier=200.0, core=core)
    from market_research_core.costs import calculate_fill_costs
    c = calculate_fill_costs(20000.0, 2, "BUY", cp)
    assert c["commission"] == Decimal("100")                        # 50 per contract per side x 2 contracts
    assert c["transaction_tax"] == Decimal("20000") * 2 * 200 * Decimal("0.00002")
    assert cp.slippage_ticks == 1 and cp.tick_size == 1.0
    assert cp.fee_mode is None                                     # legacy fields only -> legacy hash


# =========================================================== config rules for the CORE_POLICY reference
@needs_m3a4
def test_config_pins_core_policy_sha_and_pairs_fixture_with_policy(tmp_path):
    from strategy_agent.research.config import ResearchConfigError, stamp
    from strategy_agent.research.entry import build_synthetic
    c = _json(CFG)
    bad = copy.deepcopy(c)
    bad["temporal_policy"]["sha256"] = "0" * 64
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(stamp(bad)))
    with pytest.raises(ResearchConfigError, match="RC06"):
        build_synthetic(p, run_root=tmp_path / "e1", archive_root=None, agent=AGENT)
    legacy = _json(ROOT / "configs" / "research" / "sma_5_20_tsm_synthetic_local_v1.json")
    legacy["temporal_policy"] = c["temporal_policy"]
    p2 = tmp_path / "mix.json"
    p2.write_text(json.dumps(stamp(legacy)))
    with pytest.raises(ResearchConfigError, match="RC06"):
        build_synthetic(p2, run_root=tmp_path / "e2", archive_root=None, agent=AGENT)
    assert _json(CFG_DRIVE)["temporal_policy"] == c["temporal_policy"]


# =========================================================== N1: numeric vs archive vs bitwise
def test_n1_tolerance_is_fixed_and_separates_ulp_noise_from_real_differences():
    assert R.NUMERIC_TOLERANCE == {"rel_tol": 1e-9, "abs_tol": 1e-9}
    a = {"net": 767.3029999999999, "n": 25, "s": "x", "lst": [1.0, 2.0]}
    ulp = dict(a, net=767.3029999999998)
    r = R.compare_metrics(a, ulp, sha_a="a", sha_b="b")
    assert r["numeric_reproducibility"] == "PASS" and r["bitwise_cross_environment"] == "NOT_GUARANTEED"
    assert [d["path"] for d in r["within_tolerance"]] == ["metrics.net"]
    for bad in (dict(a, net=767.313), dict(a, n=26), dict(a, s="y"), dict(a, lst=[1.0]), {k: a[k] for k in ("n", "s")}):
        assert R.compare_metrics(a, bad)["numeric_reproducibility"] == "FAIL"
    same = R.compare_metrics(a, copy.deepcopy(a), sha_a="h", sha_b="h")
    assert same["numeric_reproducibility"] == "PASS" and same["bitwise_cross_environment"] == "IDENTICAL"
    add = R.compare_metrics(a, dict(a, reg=0.0), schema_additions={"metrics.reg": 0.0})
    assert add["numeric_reproducibility"] == "PASS" and add["declared_schema_additions"]
    assert R.compare_metrics(a, dict(a, reg=0.5), schema_additions={"metrics.reg": 0.0})["numeric_reproducibility"] == "FAIL"


def test_n1_recorded_colab_vs_local_evidence_is_consistent():
    ev = _json(ROOT / "docs" / "evidence" / "m3c_n1_reproducibility.json")
    cl = ev["colab_vs_local_train_base"]
    assert cl["numeric_reproducibility"] == "PASS" and cl["bitwise_cross_environment"] == "NOT_GUARANTEED"
    assert [d["path"] for d in cl["within_tolerance"]] == ["metrics.sample.effective_net_pnl"]
    assert cl["differences"] == [] and cl["archive_integrity"] == {"colab": "PASS", "local": "PASS"}
