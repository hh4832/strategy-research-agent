"""M3-B unit tests that need no market-research-core (fixture, evaluation, readiness gate, archive, batch, spec)."""
from __future__ import annotations

import copy
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from strategy_agent.adapters import mrc_adapter as A
from strategy_agent.evaluation import research as E
from strategy_agent.research import RESEARCH_SPEC, fixtures as FX, load_research_spec
from strategy_agent.research.archive import DRIVE_MOUNT, Archiver
from strategy_agent.research.batch import MAX_BATCH_RUNS, BatchLimitExceeded, cost_sensitivity_jobs, run_batch
from strategy_agent.research.data import GatedMarketSource, MarketGateClosed, readiness
from strategy_agent.research.environment import (ROOT, synthetic_cost_profile, synthetic_environment,
                                                 synthetic_spec)
from strategy_agent.research.pipeline import FIXED_BASELINE, ResearchJob
from strategy_agent.spec.validate import default_context, validate

ET = timezone(timedelta(hours=-5))


# ------------------------------------------------------------------------------------------ fixture
def test_fixture_is_reproducible_and_verified(tmp_path):
    text = (FX.FIXTURE_DIR / f"{FX.FIXTURE_ID}.csv").read_text(encoding="utf-8")
    assert FX.generate() == text                                   # byte-for-byte from the seed
    f = FX.load()
    assert f.meta["kind"] == "SYNTHETIC" and f.meta["not_market_data"] is True and len(f.rows) == f.meta["rows"]
    assert sum(r["volume"] == "0" for r in f.rows) == len(FX.ZERO_VOLUME)
    assert len(f.rows) == FX.N_DAYS * FX.BARS_PER_DAY - len(FX.MISSING)
    (tmp_path / f"{FX.FIXTURE_ID}.csv").write_text(text.replace("100.00", "100.01", 1), encoding="utf-8")
    (tmp_path / f"{FX.FIXTURE_ID}.meta.json").write_text(json.dumps(f.meta), encoding="utf-8")
    with pytest.raises(ValueError, match="sha256"):
        FX.load(tmp_path)


# ------------------------------------------------------------------------------------------ evaluation
def test_trade_stats_hand_values_and_not_evaluable():
    p = [100, -50, 30, -20, 10, -10, 40, -30, 60, -20]           # 10 trades
    s = E.trade_stats(p)
    assert s["trade_count"] == 10 and s["net_pnl"] == 110
    assert s["win_rate"] == 0.5
    assert s["average_win"] == 48 and s["average_loss"] == -26
    assert math.isclose(s["payoff_ratio"], 48 / 26) and s["expectancy"] == 11
    assert math.isclose(s["profit_factor"], 240 / 130)
    few = E.trade_stats(p[:9])
    assert all(few[k]["status"] == "NOT_EVALUABLE" for k in ("win_rate", "payoff_ratio", "expectancy", "profit_factor"))
    allwin = E.trade_stats([1.0] * 12)
    assert allwin["profit_factor"]["status"] == "NOT_EVALUABLE" and allwin["average_loss"]["status"] == "NOT_EVALUABLE"


def test_multiple_testing_never_guesses_dsr():
    import numpy as np
    r = np.random.default_rng(1).normal(0.0002, 0.001, 500)
    one = E.multiple_testing(r, family_id="F", family_trials=1, n_sessions=30, n_trades=20)
    assert one["deflated_sharpe"] == one["psr_vs_zero"] and 0 <= one["psr_vs_zero"] <= 1
    many = E.multiple_testing(r, family_id="F", family_trials=5, n_sessions=30, n_trades=20)
    assert many["deflated_sharpe"]["status"] == "NOT_EVALUABLE"
    small = E.multiple_testing(r[:50], family_id="F", family_trials=1, n_sessions=3, n_trades=2)
    assert small["psr_vs_zero"]["status"] == "NOT_EVALUABLE"


def _bar(t0, i, vol=1000.0, session="2025-01-06"):
    s = t0 + timedelta(minutes=15 * i)
    return {"bar_start": s, "bar_end": s + timedelta(minutes=15), "session": session, "open": 100.0 + i,
            "volume": vol, "session_open": t0, "session_close": t0 + timedelta(minutes=15 * 4)}


def test_evaluate_small_report_marks_ratios_not_evaluable_and_counts_quality():
    t0 = datetime(2025, 1, 6, 9, 30, tzinfo=ET)
    bars = [_bar(t0, 0), _bar(t0, 1, vol=0.0), _bar(t0, 3)]       # bar 2 missing, bar 1 zero volume
    trips = [{"symbol": "X", "entry_ts": (t0 + timedelta(minutes=15)).isoformat(),
              "exit_ts": (t0 + timedelta(minutes=45)).isoformat(), "net_pnl": "5.5", "gross_pnl": "7"}]
    fills = [{"exec_ts": (t0 + timedelta(minutes=15)).isoformat(), "fill_price": "101.01", "qty": 10, "fee": "1",
              "tax": "0"}, {"exec_ts": (t0 + timedelta(minutes=45)).isoformat(), "fill_price": "103", "qty": 10,
                            "fee": "0.5", "tax": "0"}]
    eq = [{"timestamp": b["bar_end"].isoformat(), "equity": 1000 + i} for i, b in enumerate(bars)]
    rep = SimpleNamespace(round_trips=trips, invalidated_round_trips=[], fills=fills,
                          rejections=[{"reason": "ZERO_VOLUME_BAR"}, {"reason": "ORDER_EXPIRED"}], equity_curve=eq,
                          open_position=None)
    m = E.evaluate(rep, bars, initial_capital=1000, bar_minutes=15, bars_per_year=26 * 252,
                   parameters={"fast": 5, "slow": 20}, symbol="X", order_qty=10)
    assert m["trades"]["trade_count"] == 1 and m["trades"]["win_rate"]["status"] == "NOT_EVALUABLE"
    assert m["sharpe_ratio"]["status"] == "NOT_EVALUABLE" and m["sample"]["status"] == "INSUFFICIENT"
    assert m["by_regime"]["status"] == "NOT_EVALUABLE"
    dq = m["data_quality"]
    assert dq["zero_volume_bars"] == 1 and dq["missing_bars"] == 1 and dq["expected_bars_from_session_template"] == 4
    assert dq["unfillable_signals"] == 1 and dq["rejections_by_reason"]["ZERO_VOLUME_BAR"] == 1
    assert math.isclose(dq["slippage_cost_vs_bar_open"], 0.01 * 10 + 0.0, abs_tol=1e-9)
    assert m["costs"]["total"] == 1.5 and math.isclose(m["cost_adjusted_net_return"], 0.0055)
    assert list(m["by_holding_period_bars"]) == ["1-4"]


# ------------------------------------------------------------------------------------------ readiness gate
def test_ibkr_tsm_market_gate_is_closed_and_lists_missing_conditions():
    r = readiness("IBKR_TSM")
    assert r.status != "GO"
    miss = r.missing()
    assert any("M3-A" in m for m in miss) and any("G18" in m for m in miss)
    with pytest.raises(MarketGateClosed) as e:
        GatedMarketSource("IBKR_TSM").load(symbol="TSM", warmup_start="2025-01-01", window_end="2025-02-01")
    assert e.value.missing == miss


def test_gate_needs_complete_go_record_and_allowed_window(tmp_path):
    go = {"status": "GO", "evidence_ref": "https://x", "approved_by": "lead", "approved_at": "2026-11-05",
          "snapshot_id": "s", "producer_commit_sha": "p", "bar_label": "START", "session_coverage": "RTH_ONLY",
          "allowed_window": {"start": "2024-01-01", "end": "2025-12-31"}}
    path = tmp_path / "r.json"
    path.write_text(json.dumps({"markets": {"M": go}}))
    with pytest.raises(MarketGateClosed, match="no Core data loader"):
        GatedMarketSource("M", path=path).check("2024-02-01", "2024-03-01")
    with pytest.raises(MarketGateClosed, match="outside allowed"):
        GatedMarketSource("M", loader=lambda **k: None, path=path).check("2023-02-01", "2024-03-01")
    assert GatedMarketSource("M", loader=lambda **k: None, path=path).check("2024-02-01", "2024-03-01").status == "GO"
    for k in ("evidence_ref", "approved_by", "snapshot_id"):
        path.write_text(json.dumps({"markets": {"M": dict(go, **{k: None})}}))
        with pytest.raises(MarketGateClosed, match=k):
            GatedMarketSource("M", loader=lambda **kw: None, path=path).check()


# ------------------------------------------------------------------------------------------ costs (gap G18)
def test_ibkr_per_share_commission_and_bps_slippage_fail_closed():
    prof = json.loads((ROOT / "governance" / "cost_profiles" / "ibkr_us_equity_v0.json").read_text())
    miss = A.unmappable_cost_components(prof, "EQUITY")
    assert any("per-unit equity commission" in m for m in miss) and any("bps" in m for m in miss)
    with pytest.raises(A.CoreContractGap, match="K18"):
        A.core_cost_profile(prof, instrument_type="EQUITY", tick_size=0.01)       # before any Core import
    assert A.unmappable_cost_components(synthetic_cost_profile(), "EQUITY") == []
    for name in ("tw_equity_cash_v0", "txf_futures_v0"):
        p = json.loads((ROOT / "governance" / "cost_profiles" / f"{name}.json").read_text())
        assert A.unmappable_cost_components(p, "FUTURES" if "txf" in name else "EQUITY") == []


# ------------------------------------------------------------------------------------------ spec / environment
def test_research_spec_is_fixed_draft_and_formally_not_executable():
    spec = load_research_spec()
    assert spec["status"] == "DRAFT" and RESEARCH_SPEC.exists()
    assert not [i for i in validate(spec) if i.level == "ERROR"]
    v = spec["parameters"]["values"]
    assert {k: (v[k]["min"], v[k]["default"], v[k]["max"]) for k in v} == {"fast": (5, 5, 5), "slow": (20, 20, 20)}
    assert spec["parameters"]["search"] == {"mode": "FIXED", "max_trials": 1}
    assert {k: v[k]["default"] for k in v} == FIXED_BASELINE
    assert spec["risk_constraints"]["allowed_directions"] == ["LONG"]
    reg = copy.deepcopy(spec)
    reg["status"] = "REGISTERED"
    rules = {i.rule for i in validate(reg, default_context()) if i.level == "ERROR"}
    assert {"S17", "S18"} <= rules                     # formal governance: Q2 UNKNOWN + DRAFT IBKR profile


def test_synthetic_environment_is_isolated(tmp_path):
    with pytest.raises(ValueError):
        synthetic_environment(ROOT / "registry" / "x", dataset_id="d")
    with pytest.raises(ValueError):
        synthetic_environment(ROOT / "governance", dataset_id="d")
    env = synthetic_environment(tmp_path / "env", dataset_id="d")
    assert str(env.registry.log.path).startswith(str(tmp_path))
    assert env.governance.confirmations["ibkr_tsm_session_coverage"]["status"] == "CONFIRMED"
    assert default_context().confirmations["ibkr_tsm_session_coverage"]["status"] == "UNKNOWN"   # formal untouched
    from strategy_agent.research.data import SyntheticSource
    src = SyntheticSource()
    spec = synthetic_spec(load_research_spec(), src.describe(), synthetic_cost_profile())
    assert src.dataset_id == spec["data_requirements"]["datasets"][0]["dataset_id"]
    assert not [i for i in validate(spec, env.governance) if i.level == "ERROR"]
    assert spec["signal"] == load_research_spec()["signal"] and spec["parameters"] == load_research_spec()["parameters"]


# ------------------------------------------------------------------------------------------ batch / archive
def _job():
    return ResearchJob(spec=load_research_spec(), parameters=dict(FIXED_BASELINE), window_start="a", window_end="b",
                       warmup_start="a")


def test_batch_is_bounded_and_cost_jobs_are_preregistered():
    with pytest.raises(BatchLimitExceeded):
        run_batch(None, [_job()] * (MAX_BATCH_RUNS + 1), batch_id="x", ledger_dir=Path("/nonexistent"))
    jobs = cost_sensitivity_jobs(_job())
    assert [j.cost_multiplier for j in jobs] == [0.0, 1.0, 2.0, 4.0]
    assert all(j.parameters == FIXED_BASELINE for j in jobs)
    with pytest.raises(ValueError, match="pre-registered"):
        cost_sensitivity_jobs(_job(), [3.0])


def _fake_result(tmp_path):
    return SimpleNamespace(
        context={"agent": {"agent_sha": "abcdef1234567"}, "environment": "SYNTHETIC", "spec_sha256": "s" * 64,
                 "engine": {"git_sha": "c" * 40, "benchmark_status": "BENCHMARK_PASS"}, "runner_version": "r",
                 "adapter_version": "a", "cost_profile": synthetic_cost_profile(), "overrides": ["o"],
                 "verdicts": {}},
        job=_job(), report=None, market_data=None, record={"experiment_id": "EXP-1", "status": "REJECTED_SPEC"},
        status="REJECTED_SPEC", identity="i" * 64, metrics=None, metrics_sha256=None, events=[{"x": 1}],
        stages=[{"name": "spec", "status": "FAILED"}], research_report_md="# r\n", decisions=[], archive=None)


def test_archive_never_overwrites_and_verifies(tmp_path):
    fixed = datetime(2026, 11, 4, 5, 0, 0, tzinfo=timezone.utc)
    a = Archiver(tmp_path / "arch", clock=lambda: fixed)
    r1, r2 = _fake_result(tmp_path), _fake_result(tmp_path)
    r1.archive = a.write(r1)
    a.finalize(r1)
    before = (Path(r1.archive["path"]) / "manifest.json").read_bytes()
    r2.archive = a.write(r2)
    a.finalize(r2)
    p1, p2 = Path(r1.archive["path"]), Path(r2.archive["path"])
    assert p1.name == "20261104_130000_abcdef1" and p2.name == "20261104_130000_abcdef1-2"
    assert (p1 / "manifest.json").read_bytes() == before and r1.archive["verified"] and r2.archive["verified"]
    man = json.loads((p1 / "manifest.json").read_text())["files"]
    assert {"run_info.txt", "experiment_record.json", "stages.json", "final_status.json", "research_report.md",
            "snapshot_refs.json", "dataset_metadata.json"} <= set(man)
    assert not any(n.endswith(".parquet") for n in man)


def test_drive_unavailable_is_reported_never_claimed(tmp_path):
    if DRIVE_MOUNT.is_dir():
        pytest.skip("Drive is mounted here")
    a = Archiver(require_drive=True, local_fallback=tmp_path / "local")
    r = _fake_result(tmp_path)
    r.archive = a.write(r)
    assert r.archive["status"] == "DRIVE_UNAVAILABLE" and "not mounted" in r.archive["reason"]
    assert Path(r.archive["path"]).parent == tmp_path / "local"
    info = (Path(r.archive["path"]) / "run_info.txt").read_text()
    assert "DRIVE_UNAVAILABLE" in info and "NOT a Drive backup" in info
    a2 = Archiver(tmp_path / "plain", require_drive=True)
    assert a2.write(_fake_result(tmp_path))["status"] == "DRIVE_UNAVAILABLE"


def test_research_package_has_no_engine_or_oracle_imports():
    import ast
    pkg = ROOT / "src" / "strategy_agent" / "research"
    for f in pkg.glob("*.py"):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        mods = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | \
            {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not any(m.startswith("benchmark") for m in mods), f
        assert not any(m.startswith("market_research_core") for m in mods), f   # Core only via the M1 adapter


def test_parameter_issues_r02():
    from strategy_agent.runner import parameter_issues
    spec = load_research_spec()
    assert parameter_issues(spec, {"fast": 5, "slow": 20}) == []
    for bad in ({"fast": 6, "slow": 20}, {"fast": 5}, {"fast": 5, "slow": 20, "x": 1}, {"fast": 5.5, "slow": 20}):
        assert parameter_issues(spec, bad) and all(i.rule == "R02" for i in parameter_issues(spec, bad))
    tw = json.loads((ROOT / "specs" / "examples" / "sma_crossover_tw_2330_5m.json").read_text())
    assert parameter_issues(tw, {"fast": 10, "slow": 50}) == []
    assert any("step grid" in i.message for i in parameter_issues(tw, {"fast": 7, "slow": 50}))
    tw["parameters"]["values"]["fast"]["max"] = 60
    assert any("violates" in i.message for i in parameter_issues(tw, {"fast": 55, "slow": 50}))
