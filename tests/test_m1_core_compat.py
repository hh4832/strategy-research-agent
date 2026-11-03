"""M1.1 Core compatibility tests against the REAL installed market-research-core ExecutionEngine.

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

PINNED = "a50f3902c3a014922601d8cca4e7bdf761d377b8"
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


@pytest.mark.parametrize("stem", ["B01", "B03", "B06", "B08", "B09", "B10", "B11", "B12"])
def test_only_c2_rounding_differs(stem):
    """Fills, times, sides, contracts, roll, invalidation and open position match; only 0.01 rounding differs."""
    c = case(stem)
    res, report, notes = X.run_case(c, core, SHA)
    diffs = X.compare(c, X.actual_view(res, report, c), SHA, X.synthetic_request(c).spec_sha256, notes["orders"])
    assert diffs and all(X._is_rounding(d) for d in diffs)


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


def test_b05_expiry_and_b07_rejection_log_are_real_gaps():
    res5, _, _ = X.run_case(case("B05"), core, SHA)
    assert len(res5.fills) == 0 and list(res5.rejections["reason"]) == ["ORDER_EXPIRED"]          # K14
    res7, _, _ = X.run_case(case("B07"), core, SHA)
    assert len(res7.fills) == 2 and len(res7.rejections) == 0                                   # K15


def test_c1_core_default_fills_one_bar_late():
    c = case("B01")
    res, _, _ = X.run_case(c, core, SHA, same_ts=False)
    assert [str(t)[11:16] for t in res.fills["exec_ts"]] == ["09:20", "09:35"]                 # oracle: 09:15, 09:30
    res, _, _ = X.run_case(c, core, SHA, same_ts=True)
    assert [str(t)[11:16] for t in res.fills["exec_ts"]] == ["09:15", "09:30"]
    assert set(res.fills["execution_assumption"]) == {"SAME_TIMESTAMP_BAR_OPEN"}


def test_c3_chronology_probe_detects_exit_before_entry():
    p = X.chronology_probe(core, SHA)
    assert p["status"] == "FAIL" and p["exit_before_entry"]


def test_c3_unfilled_target_does_not_move_core_target_state():
    """B05: the BUY never fills; the later target-0 order is a NOOP, not a SELL that would open a short."""
    res, _, notes = X.run_case(case("B05"), core, SHA)
    assert [o["side"] for o in notes["orders"]] == ["BUY", "SELL"] and len(res.fills) == 0


@pytest.mark.skipif(SHA != PINNED, reason="committed record is for the pinned commit")
def test_crosscheck_reproduces_committed_record(tmp_path):
    rec = X.crosscheck(tmp_path)
    committed = json.loads((ROOT / "benchmark" / "results" / f"mrc_{PINNED[:12]}" / "crosscheck.json").read_text())
    assert rec["verdict"] == committed["verdict"] == "FAIL" and rec["counts"] == committed["counts"]
    strip = lambda r: {k: v for k, v in r.items() if k not in ("actual",)}  # noqa: E731
    assert json.loads(json.dumps([strip(r) for r in rec["cases"]], default=str)) == \
        [strip(r) for r in committed["cases"]]
    gaps = json.loads((ROOT / "benchmark" / "results" / f"mrc_{PINNED[:12]}" / "contract_gaps.json").read_text())
    assert {g["gap_id"] for g in gaps["gaps"] if g["blocking"]} == {"G03b", "G12", "G12b", "G14", "G15", "G16"}


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


def _half_up(spec, prof):
    """TEST-ONLY: Core cannot express FLOOR_INTEGER (gap G12b), so the end-to-end probe uses HALF_UP in both
    the spec and an approved test profile. Never written to governance/."""
    for c in prof["components"]:
        if c["kind"] == "ROUNDING":
            c["rule"] = "HALF_UP"
    prof["provenance"]["profile_sha256"] = profile_hash(prof)
    spec["costs"]["fee"]["rounding"] = spec["costs"]["tax"]["rounding"] = "HALF_UP"
    spec["costs"]["profile_ref"]["sha256"] = prof["provenance"]["profile_sha256"]
    spec["provenance"]["spec_sha256"] = None
    spec["provenance"]["spec_sha256"] = canonical_hash(spec)
    return spec, prof


def _port(prof, frame, decs, tick=0.5):
    # TEST-ONLY constant tick (0.5 is the TWSE tick in the 100-500 band used by these synthetic prices)
    sem = {"tw_equity_fubon_5m": A.MarketSemantics(market="TW_EQUITY", bar_label="START",
                                                   session_semantics="CONFIRMED", timezone="Asia/Taipei",
                                                   initial_cash=1e7, constant_tick_size=tick)}
    return A.MrcBacktestPort({(prof["profile_id"], prof["version"]): prof}, decisions_provider=lambda r: decs,
                             bars_provider=lambda r: frame, semantics=sem, core=core)


def test_spec_to_core_to_report_end_to_end():
    frame, decs = _bars_and_decisions()
    spec, prof = _half_up(registered(), approved_profile())
    req = BacktestRequest(spec=spec, spec_sha256=canonical_hash(spec), datasets=(DatasetRef(
        "tw_equity_fubon_5m", "final_gate_20261031_154458_20eba73", "20eba73" + "0" * 33, "SCOPED_GO", "BACKTEST"),),
        window_start="2025-01-02T00:00:00+08:00", window_end="2025-01-03T00:00:00+08:00",
        warmup_start="2025-01-01T00:00:00+08:00", parameters={"fast": 2, "slow": 3})
    rep = _port(prof, frame, decs).run(req)
    check_report(rep, req)
    assert rep.provenance["engine_git_sha"] == SHA and rep.provenance["spec_sha256"] == req.spec_sha256
    assert rep.fills and all(f["exec_ts"] >= d.decision_ts.isoformat() for f, d in zip(rep.fills, decs))
    assert rep.status == "RESEARCH_INTEGRATION_DEMO"          # DRAFT/assumed components -> never formal


def test_real_tw_profile_fails_closed_on_floor_integer():
    frame, decs = _bars_and_decisions()
    spec, prof = registered(), approved_profile()
    req = BacktestRequest(spec=spec, spec_sha256=canonical_hash(spec), datasets=(DatasetRef(
        "tw_equity_fubon_5m", "final_gate_20261031_154458_20eba73", "20eba73", "SCOPED_GO", "BACKTEST"),),
        window_start="2025-01-02T00:00:00+08:00", window_end="2025-01-03T00:00:00+08:00",
        warmup_start="2025-01-01T00:00:00+08:00")
    with pytest.raises(A.CoreContractGap, match="FLOOR_INTEGER"):
        _port(prof, frame, decs).run(req)
    spec2, prof2 = _half_up(registered(), approved_profile())
    req2 = BacktestRequest(**{**req.__dict__, "spec": spec2, "spec_sha256": canonical_hash(spec2)})
    with pytest.raises(A.CoreContractGap, match="tick ladder"):                       # gap G03b
        _port(prof2, frame, decs, tick=None).run(req2)


def test_unknown_market_semantics_fail_closed():
    frame, decs = _bars_and_decisions()
    spec, prof = _half_up(registered(), approved_profile())
    req = BacktestRequest(spec=spec, spec_sha256=canonical_hash(spec), datasets=(DatasetRef(
        "tw_equity_fubon_5m", "s", "p", "SCOPED_GO", "BACKTEST"),), window_start="2025-01-02T00:00:00+08:00",
        window_end="2025-01-03T00:00:00+08:00", warmup_start="2025-01-01T00:00:00+08:00")
    port = _port(prof, frame, decs)
    port.semantics["tw_equity_fubon_5m"] = A.MarketSemantics("TXF", "UNKNOWN", "UNKNOWN", "Asia/Taipei", 1e7, 1.0)
    with pytest.raises(ContractViolation, match="not confirmed"):
        port.run(req)


def test_production_runner_never_reaches_core_without_benchmark_pass(tmp_path):
    frame, decs = _bars_and_decisions()
    spec, prof = registered(), approved_profile()
    port = _port(prof, frame, decs)
    calls = []
    port.run = (lambda f: (lambda r: calls.append(r) or f(r)))(port.run)
    runner, reg = env(tmp_path, port=port, benchmark="NOT_VERIFIED")
    runner.engine = A.engine_info()                        # derived from the real record: NOT_VERIFIED
    rec = runner.submit(dev_request(spec))
    assert runner.engine.benchmark_status == "NOT_VERIFIED"
    assert rec["status"] == "REJECTED_CONTRACT" and rec["rejection_reasons"][0]["code"] == "C00" and not calls
    assert reg.records()[-1]["code"]["engine_git_sha"] == SHA
