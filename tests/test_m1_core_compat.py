"""M1 final (M1.3) Core compatibility tests against the REAL installed market-research-core ExecutionEngine.

Skipped when Core is not installed (default CI job). The `core-compat` CI job installs Core at the pinned commit.
Every Core result below comes from Core's own code; nothing here is a fake engine or the oracle."""
from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

core = pytest.importorskip("market_research_core")
if not hasattr(core, "ExecutionEngine"):
    pytest.skip("installed Core predates Phase 2.1 ExecutionEngine", allow_module_level=True)

from benchmark.harness import mrc_crosscheck as X  # noqa: E402
from benchmark.harness.crosscheck import cases  # noqa: E402
from strategy_agent.adapters import mrc_adapter as A  # noqa: E402
from strategy_agent.costs.profile import profile_hash  # noqa: E402
from strategy_agent.ports import BacktestRequest, ContractViolation, DatasetRef, check_report  # noqa: E402
from strategy_agent.signals.core import SignalBar  # noqa: E402
from strategy_agent.signals.sma import sma_crossover  # noqa: E402
from strategy_agent.spec.validate import canonical_hash  # noqa: E402

from m2_helpers import approved_profile, dev_request, env, registered  # noqa: E402

PINNED = "2c6ba9ab7c336e7a4908744abef617e0dce713fd"
ROOT = Path(__file__).parents[1]
TPE = timezone(timedelta(hours=8))
SHA = A.core_identity()["git_sha"]


def case(stem):
    return next(c for c in cases() if c["case_id"].startswith(stem))


def test_installed_core_has_a_resolvable_commit():
    ident = A.core_identity()
    assert ident["installed"] and ident["git_sha"] and len(ident["git_sha"]) == 40


def test_matrix_is_measured_on_the_real_core():
    m = {r["cap_id"]: r for r in A.compatibility_matrix(core)}
    assert list(m) == [c.cap_id for c in A.CAPABILITIES]
    assert m["K01"]["status"] == "SUPPORTED" and "bars" in m["K01"]["evidence"]
    assert set(A.CORE_BAR_COLUMNS) == set(core.execution._REQUIRED_COLUMNS)


# ------------------------------------------------------------------------------------------ benchmark cases
def test_b02_passes_on_the_real_execution_engine():
    c = case("B02")
    res, report, notes = X.run_case(c, core, SHA)
    act = X.actual_view(res, report, c)
    assert notes["signal_source"] == "strategy_agent.signals.sma_crossover"
    assert X.compare(c, act, SHA, X.synthetic_request(c).spec_sha256, notes["orders"]) == []


@pytest.mark.parametrize("stem", [f"B{i:02d}" for i in range(1, 13)])
def test_every_case_matches_the_oracle_exactly(stem):
    """Exact comparison: TOL is 0 and fees/taxes are compared as Decimal strings (no tolerance masks precision)."""
    assert X.TOL == Decimal("0")
    c = case(stem)
    res, report, notes = X.run_case(c, core, SHA)
    assert X.compare(c, X.actual_view(res, report, c), SHA, X.synthetic_request(c).spec_sha256, notes["orders"]) == []
    assert notes["adapter_guard"] == []                       # no G17/G14b situation inside B01-B12


def test_negative_controls_still_detect_differences():
    """The comparator is live: HALF_UP rounding or Core's default open assumption must produce differences."""
    c = case("B01")
    orig = X.cost_profile
    X.cost_profile = lambda case, core: __import__("dataclasses").replace(orig(case, core), fee_rounding="HALF_UP",
                                                                         tax_rounding="HALF_UP")
    try:
        res, report, notes = X.run_case(c, core, SHA)
    finally:
        X.cost_profile = orig
    assert X.compare(c, X.actual_view(res, report, c), SHA, X.synthetic_request(c).spec_sha256, notes["orders"])
    res, report, notes = X.run_case(c, core, SHA, same_ts=False)
    assert X.compare(c, X.actual_view(res, report, c), SHA, X.synthetic_request(c).spec_sha256, notes["orders"])


def test_b09_roll_is_explicit_and_per_contract():
    c = case("B09")
    res, _, notes = X.run_case(c, core, SHA)
    assert [o["order_id"].split("-", 1)[1] for o in notes["orders"]] == ["FIXTURE_SIGNAL", "ROLL_CLOSE", "ROLL_OPEN",
                                                                         "FIXTURE_SIGNAL"]
    assert list(res.fills["contract"]) == ["SYNF2601", "SYNF2601", "SYNF2602", "SYNF2602"]
    assert set(res.round_trips["contract"]) == {"SYNF2601", "SYNF2602"}       # no stitched round trip


def test_b11_cross_window_trade_is_invalidated_and_run_is_research_only():
    res, report, _ = X.run_case(case("B11"), core, SHA)
    assert len(res.round_trips) == 0 and len(res.invalidated_round_trips) == 1
    assert res.status == "RESEARCH_ONLY" and report.status == "RESEARCH_INTEGRATION_DEMO"


def test_b05_expiry_and_b07_rejection_log_now_match():
    c5, c7 = case("B05"), case("B07")
    res5, _, _ = X.run_case(c5, core, SHA)
    assert len(res5.fills) == len(c5["expected"]["fills"]) > 0                                  # G14 closed: overnight fill
    res7, _, _ = X.run_case(c7, core, SHA)
    assert len(res7.fills) == len(c7["expected"]["fills"])
    assert len(res7.rejections) == len(c7["expected"]["rejections"]) > 0                        # G15 closed
    assert all(r["bar_ts"] is not None for r in res7.rejections.to_dict("records"))


def test_c1_core_default_fills_one_bar_late():
    c = case("B01")
    res, _, _ = X.run_case(c, core, SHA, same_ts=False)
    assert [str(t)[11:16] for t in res.fills["exec_ts"]] == ["09:20", "09:35"]                 # oracle: 09:15, 09:30
    res, _, _ = X.run_case(c, core, SHA, same_ts=True)
    assert [str(t)[11:16] for t in res.fills["exec_ts"]] == ["09:15", "09:30"]
    assert set(res.fills["execution_assumption"]) == {"SAME_TIMESTAMP_BAR_OPEN"}


def test_g16_chronology_acceptance_passes():
    g = X.g16_acceptance(core, SHA)
    assert g["status"] == "PASS" and all(g["checks"].values()) and len(g["checks"]) == 6, g


def test_g17_pending_target_rebalance_matches_oracle():
    """Oracle v1: delayed BUY (pending +1000) + later target 0 -> SELL 09:20, BUY 09:25, flat, realized -550.7."""
    p = X.oracle_probes(core, SHA)["G17"]
    assert p["expected_source"].startswith("oracle-generated")
    assert [(f["side"], f["exec_ts"][11:]) for f in p["expected"]["fills"]] == [("SELL", "09:20"), ("BUY", "09:25")]
    assert p["status"] == "PASS" and p["differences"] == [], p["differences"]
    assert Decimal(p["actual"]["realized_pnl"]) == Decimal(p["expected"]["realized_pnl"]) == Decimal("-550.7")
    assert p["actual"]["open_position"] is None and p["adapter_guard_hits"] == []


def test_g14b_half_open_day_expiry_matches_oracle():
    p = X.oracle_probes(core, SHA)["G14b"]
    assert p["expected"]["fills"] == [] == p["actual"]["fills"]
    assert ("", "ORDER_EXPIRED") in [(r["bar_ts"] or "", r["reason"]) for r in p["actual"]["rejections"]]
    assert p["status"] == "PASS" and p["differences"] == [] and p["adapter_guard_hits"] == []


def test_k17_k14b_measured_supported_so_guard_is_inactive():
    m = {r["cap_id"]: r["status"] for r in A.compatibility_matrix(core)}
    assert m["K16"] == m["K17"] == m["K14b"] == "SUPPORTED"
    assert A.unsupported_divergences(core) == set()


def test_guard_still_refuses_when_capability_is_not_demonstrated():
    """The guard is gated by measurement, not removed: with K17 unmeasured/unsupported it still refuses."""
    c = X._with_oracle(X.G16_CASE)
    res, _, _ = X.run_case(c, core, SHA)
    targets = A.decisions_to_targets(X.decisions(c)[0])
    orders = A.targets_to_orders(targets, symbol="SYNS", market="TW_EQUITY", quantity=1000.0, strategy_id="X16",
                                 experiment_id="benchmark", time_in_force="DAY", core=core)
    A.guard_known_divergences(res, orders, X.core_bars(c), set())               # supported -> no-op
    with pytest.raises(A.CoreContractGap, match="K17"):
        A.guard_known_divergences(res, orders, X.core_bars(c), {"G17"})


@pytest.mark.skipif(SHA != PINNED, reason="committed record is for the pinned commit")
def test_crosscheck_reproduces_committed_record(tmp_path):
    rec = X.crosscheck(tmp_path)
    committed = json.loads((ROOT / "benchmark" / "results" / f"mrc_{PINNED[:12]}" / "crosscheck.json").read_text())
    assert rec["verdict"] == committed["verdict"] == "PASS" and rec["counts"] == committed["counts"]
    strip = lambda r: {k: v for k, v in r.items() if k not in ("actual",)}  # noqa: E731
    assert json.loads(json.dumps([strip(r) for r in rec["cases"]], default=str)) == \
        [strip(r) for r in committed["cases"]]
    gaps = json.loads((ROOT / "benchmark" / "results" / f"mrc_{PINNED[:12]}" / "contract_gaps.json").read_text())
    assert {g["gap_id"] for g in gaps["gaps"] if g["blocking"]} == {"G03b"}           # formal TW equity only
    assert {g["gap_id"] for g in gaps["gaps"] if g.get("status") == "CLOSED"} == {
        "G12", "G12b", "G14", "G14b", "G15", "G16", "G17"}
    assert {k: v["status"] for k, v in rec["probes"]["oracle_probes"].items()} == {"G17": "PASS", "G14b": "PASS"}
    assert rec["probes"]["G16_acceptance"]["checks"] == committed["probes"]["G16_acceptance"]["checks"]
    assert A.engine_benchmark_status(PINNED)["status"] == "BENCHMARK_PASS"


# ------------------------------------------------------------------------------------------ runner integration
def _bars_and_decisions():
    """Synthetic Core-shaped bars (explicit START label) + Agent SMA decisions on them."""
    import pandas as pd
    t0 = datetime(2025, 1, 2, 9, 0, tzinfo=TPE)
    closes = [600, 600, 600, 602, 606, 610, 608, 600, 594, 590, 592, 594]
    rows, sbars = [], []
    for i, c in enumerate(closes):
        ts = t0 + timedelta(minutes=5 * i)
        rows.append({"bar_start": ts, "bar_end": ts + timedelta(minutes=5), "available_at": ts + timedelta(minutes=5),
                     "timezone": "Asia/Taipei", "session": "2025-01-02", "symbol": "2330", "market": "TW_EQUITY",
                     "contract": "2330", "open": float(c), "high": c + 1.0, "low": c - 1.0, "close": float(c),
                     "volume": 1000.0, "data_exists": True, "backtest_eligible": True, "buy_execution_eligible": True,
                     "sell_execution_eligible": True, "execution_rejection_reason": "",
                     "session_open": t0, "session_close": t0.replace(hour=13, minute=30)})
        sbars.append(SignalBar(ts, ts + timedelta(minutes=5), c, c + 1, c - 1, c, 1000, "2330", "2025-01-02", True))
    return pd.DataFrame(rows), sma_crossover(sbars, 2, 3)


def _port(prof, frame, decs, tick=0.5):
    # TEST-ONLY constant tick (0.5 is the TWSE tick in the 100-500 band used by these synthetic prices)
    sem = {"tw_equity_fubon_5m": A.MarketSemantics(market="TW_EQUITY", bar_label="START",
                                                   session_semantics="CONFIRMED", timezone="Asia/Taipei",
                                                   initial_cash=1e7, constant_tick_size=tick)}
    return A.MrcBacktestPort({(prof["profile_id"], prof["version"]): prof}, decisions_provider=lambda r: decs,
                             bars_provider=lambda r: frame, semantics=sem, core=core)


def test_spec_to_core_to_report_end_to_end():
    frame, decs = _bars_and_decisions()
    spec, prof = registered(), approved_profile()                 # FLOOR_INTEGER now expressible (G12b closed)
    req = BacktestRequest(spec=spec, spec_sha256=canonical_hash(spec), datasets=(DatasetRef(
        "tw_equity_fubon_5m", "final_gate_20261031_154458_20eba73", "20eba73" + "0" * 33, "SCOPED_GO", "BACKTEST"),),
        window_start="2025-01-02T00:00:00+08:00", window_end="2025-01-03T00:00:00+08:00",
        warmup_start="2025-01-01T00:00:00+08:00", parameters={"fast": 2, "slow": 3})
    rep = _port(prof, frame, decs).run(req)
    check_report(rep, req)
    assert rep.provenance["engine_git_sha"] == SHA and rep.provenance["spec_sha256"] == req.spec_sha256
    assert rep.fills and all(f["exec_ts"] >= d.decision_ts.isoformat() for f, d in zip(rep.fills, decs))
    assert rep.status == "RESEARCH_INTEGRATION_DEMO"          # DRAFT/assumed components -> never formal


def test_real_tw_profile_floor_integer_maps_but_tick_ladder_still_fails_closed():
    frame, decs = _bars_and_decisions()
    spec, prof = registered(), approved_profile()
    cp = A.core_cost_profile(prof, instrument_type="EQUITY", tick_size=0.5, contract_multiplier=1.0, core=core)
    assert cp.fee_rounding == cp.tax_rounding == "FLOOR_INTEGER"
    req = BacktestRequest(spec=spec, spec_sha256=canonical_hash(spec), datasets=(DatasetRef(
        "tw_equity_fubon_5m", "final_gate_20261031_154458_20eba73", "20eba73", "SCOPED_GO", "BACKTEST"),),
        window_start="2025-01-02T00:00:00+08:00", window_end="2025-01-03T00:00:00+08:00",
        warmup_start="2025-01-01T00:00:00+08:00")
    with pytest.raises(A.CoreContractGap, match="tick ladder"):                       # gap G03b still open
        _port(prof, frame, decs, tick=None).run(req)


def test_unknown_market_semantics_fail_closed():
    frame, decs = _bars_and_decisions()
    spec, prof = registered(), approved_profile()
    req = BacktestRequest(spec=spec, spec_sha256=canonical_hash(spec), datasets=(DatasetRef(
        "tw_equity_fubon_5m", "s", "p", "SCOPED_GO", "BACKTEST"),), window_start="2025-01-02T00:00:00+08:00",
        window_end="2025-01-03T00:00:00+08:00", warmup_start="2025-01-01T00:00:00+08:00")
    port = _port(prof, frame, decs)
    port.semantics["tw_equity_fubon_5m"] = A.MarketSemantics("TXF", "UNKNOWN", "UNKNOWN", "Asia/Taipei", 1e7, 1.0)
    with pytest.raises(ContractViolation, match="not confirmed"):
        port.run(req)


def test_runner_never_reaches_core_without_benchmark_pass(tmp_path):
    """Engine status comes from the evidence record only: an unknown/unbenchmarked commit stays NOT_VERIFIED."""
    frame, decs = _bars_and_decisions()
    spec, prof = registered(), approved_profile()
    port = _port(prof, frame, decs)
    calls = []
    port.run = (lambda f: (lambda r: calls.append(r) or f(r)))(port.run)
    runner, reg = env(tmp_path, port=port, benchmark="NOT_VERIFIED")
    assert A.engine_benchmark_status("0" * 40)["status"] == "NOT_VERIFIED"
    assert A.engine_benchmark_status("a50f3902c3a014922601d8cca4e7bdf761d377b8")["status"] == "NOT_VERIFIED"
    rec = runner.submit(dev_request(spec))
    assert rec["status"] == "REJECTED_CONTRACT" and rec["rejection_reasons"][0]["code"] == "C00" and not calls


def test_engine_status_requires_g16_evidence(tmp_path):
    src = ROOT / "benchmark" / "results" / f"mrc_{SHA[:12]}" / "crosscheck.json"
    rec = json.loads(src.read_text())
    d = tmp_path / f"mrc_{SHA[:12]}"
    d.mkdir()
    rec["probes"].pop("G16_acceptance")
    (d / "crosscheck.json").write_text(json.dumps(rec))
    assert A.engine_benchmark_status(SHA, record_dir=tmp_path)["status"] == "NOT_VERIFIED"


@pytest.mark.skipif(SHA != PINNED, reason="BENCHMARK_PASS record exists for the pinned commit only")
def test_synthetic_runner_core_registry_completed(tmp_path):
    """SYNTHETIC: Runner -> Adapter -> real Core -> BacktestReport -> Registry, in an isolated tmp governance with
    the tests' own holdout lock (tests/m2_helpers.env). Bars are synthetic, the constant tick is TEST-ONLY and the
    approved CostProfile is a test-only approval; governance/ and the real registry are never touched."""
    frame, decs = _bars_and_decisions()
    spec, prof = registered(), approved_profile()
    calls = []
    port = _port(prof, frame, decs)
    port.run = (lambda f: (lambda r: calls.append(r) or f(r)))(port.run)
    runner, reg = env(tmp_path, port=port)
    runner.engine = A.engine_info()                      # derived from the committed evidence record
    assert runner.engine.benchmark_status == "BENCHMARK_PASS" and runner.engine.git_sha == PINNED
    rec = runner.submit(dev_request(spec, {"fast": 2, "slow": 3}))
    assert rec["status"] == "COMPLETED", rec.get("rejection_reasons")
    assert len(calls) == 1 and (tmp_path / "experiments.jsonl").read_text().strip()      # isolated test registry
    last = reg.records()[-1]
    assert last["status"] == "COMPLETED"
    assert last["code"]["engine_git_sha"] == PINNED and last["code"]["engine_benchmark_status"] == "BENCHMARK_PASS"
    assert last["code"]["strategy_agent_sha"] == "abcdef1"
    assert last["data"] and all(d["snapshot_id"] and d["producer_commit_sha"] for d in last["data"])
    ex = last["metrics"]["execution"]
    assert ex["engine_git_sha"] == PINNED and ex["cost_profile_sha256"] and ex["core_status"] in (
        "RESEARCH_ONLY", "FORMAL_ELIGIBLE")
    assert ex["report_status"] in ("RESEARCH_BACKTEST", "RESEARCH_INTEGRATION_DEMO")
    # CostProfile chain: agent profile pinned by the spec -> mapped Core profile -> Core provenance hash
    assert ex["agent_cost_profile_sha256"] == prof["provenance"]["profile_sha256"] == \
        spec["costs"]["profile_ref"]["sha256"]
    cp = A.core_cost_profile(prof, instrument_type="EQUITY", tick_size=0.5, contract_multiplier=1.0, core=core)
    assert ex["cost_profile_sha256"] == cp.sha256
    # dataset snapshot / producer / spec pins carried through Core into the record
    d0 = spec["data_requirements"]["datasets"][0]
    assert ex["dataset_snapshot_id"] == d0["snapshot_id"] == last["data"][0]["snapshot_id"]
    assert ex["dataset_producer_sha"] == d0["producer_commit_sha"] and ex["spec_sha256"] == last["spec"]["spec_sha256"]
    assert last["metrics"]["fills"] >= 1 and last["holdout_access"] is False
