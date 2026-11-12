"""Phase 5-C1: pre-registered, point-in-time regime decomposition (evaluation.regimes) and its spec wiring."""
from __future__ import annotations

import copy
import json
import math
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from strategy_agent.evaluation import regimes as RG

NY = timezone(timedelta(hours=-5))
DEF = {"version": "REGIME-DAILY-v1", "trend": {"lookback_sessions": 3}, "volatility": {"window_sessions": 2,
                                                                                         "reference_sessions": 3}}


def _bars(closes):
    out, d = [], datetime(2020, 1, 2, 9, 30, tzinfo=NY)
    for i, c in enumerate(closes):
        day = d + timedelta(days=i)
        for k, px in enumerate((c - 1.0, c)):                          # last bar of the session carries the close
            s = day + timedelta(minutes=15 * k)
            out.append({"session": day.date().isoformat(), "bar_start": s, "bar_end": s + timedelta(minutes=15),
                        "close": px})
    return out


def test_definition_is_validated_and_fixed():
    assert RG.validate(DEF)["trend"]["lookback_sessions"] == 3
    for bad in ({}, {**DEF, "version": "X"}, {**DEF, "trend": {"lookback_sessions": 1}},
                {**DEF, "volatility": {"window_sessions": 2}}, {**DEF, "trend": {"lookback_sessions": True}}):
        with pytest.raises(RG.RegimeDefinitionError):
            RG.validate(bad)


def test_session_close_is_the_last_bar_of_each_session():
    assert RG.session_closes(_bars([10, 11])) == [("2020-01-02", 10.0), ("2020-01-03", 11.0)]


def test_labels_use_only_sessions_strictly_before_entry_and_unlabelled_without_history():
    closes = [("d%02d" % i, c) for i, c in enumerate([10, 11, 12, 13, 9, 9.5, 20, 30])]
    lab = RG.labels_for_sessions(closes, DEF)
    assert lab["d00"]["trend"] == lab["d02"]["trend"] == RG.UNLABELLED          # < 3 prior sessions
    assert lab["d03"]["trend"] == "UP"                                           # 12 > mean(10,11,12)
    assert lab["d05"]["trend"] == "DOWN"                                         # 9 < mean(12,13,9)
    # changing the entry session's own close (or anything later) never changes its label (point in time)
    for i in range(len(closes)):
        alt = list(closes)
        alt[i:] = [(s, c * 7 + 1) for s, c in alt[i:]]
        assert RG.labels_for_sessions(alt, DEF)[closes[i][0]] == lab[closes[i][0]], i
    assert {v["volatility"] for v in lab.values()} <= {"HIGH", "LOW", RG.UNLABELLED}
    assert lab["d00"]["volatility"] == RG.UNLABELLED


def test_volatility_label_hand_value():
    px = [100, 101, 99, 100, 130, 100]
    closes = [(f"s{i}", float(c)) for i, c in enumerate(px)]
    lab = RG.labels_for_sessions(closes, DEF)
    r = [math.log(px[i] / px[i - 1]) for i in range(1, len(px))]
    import statistics as st
    vol = {j: st.stdev(r[j - 2:j]) for j in range(2, len(px))}
    # entry s5 uses the last completed session j=4: vol(4) vs median(vol(2), vol(3), vol(4))
    assert lab["s5"]["volatility"] == ("HIGH" if vol[4] > st.median([vol[2], vol[3], vol[4]]) else "LOW") == "HIGH"


def test_preregistered_decompositions_only_with_a_pre_registered_definition():
    from strategy_agent.research_only.core_phase4a import preregistered_decompositions
    bars = _bars([10, 11, 12, 13, 14, 15, 16, 17])
    trips = [{"symbol": "X", "entry_ts": datetime(2020, 1, 2 + d, 10, 0, tzinfo=NY).isoformat(),
              "exit_ts": datetime(2020, 1, 2 + d, 15, 0, tzinfo=NY).isoformat(), "net_pnl": str(p), "gross_pnl": str(p),
              "qty": 1, "entry_price": 10.0}
             for d, p in ((0, 5), (4, -2), (6, 3))]
    rep = SimpleNamespace(round_trips=trips)
    assert preregistered_decompositions(rep, bars, {}) == {}                    # older specs: nothing added
    m = preregistered_decompositions(rep, bars, {"regime_definition": DEF, "drift_control": "DRIFT-MATCHED-v1"})
    r = m["by_regime"]
    assert r["counts"]["trend"] == {"UNLABELLED": 1, "UP": 2} and r["definition"]["version"] == "REGIME-DAILY-v1"
    assert r["trend"]["UP"]["net_pnl"] == 1.0 and r["trend"]["UNLABELLED"]["trade_count"] == 1
    assert sum(r["counts"]["volatility"].values()) == 3
    assert m["drift_control"]["status"] == "EVALUATED" and m["drift_control"]["trades"] == 3
    with pytest.raises(Exception):
        preregistered_decompositions(rep, bars, {"drift_control": "OTHER"})


def test_preregistered_spec_draft_validates_against_the_spec_schema():
    from strategy_agent.spec.validate import load_schema
    from jsonschema import Draft202012Validator
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    spec = json.loads((root / "docs/phase5c1/strategy_spec_v0_2_0.draft.json").read_text())
    errs = sorted(e.message for e in Draft202012Validator(load_schema()).iter_errors(spec))
    assert errs == [], errs
    old = json.loads((root / "specs/research/sma_crossover_tsm_15m_5_20_v0_1_1.json").read_text())
    same = {k for k in old if old[k] == spec.get(k)}
    assert {"universe", "parameters", "signal", "position_sizing", "risk_exits", "execution", "costs",
            "risk_constraints"} <= same                                  # rules are the pre-registered baseline
    assert spec["experiment"]["regime_definition"]["version"] == "REGIME-DAILY-v1"
    assert spec["experiment"]["family_trials_including_this"] == 3
    unchanged_old = copy.deepcopy(old)
    assert "regime_definition" not in unchanged_old["experiment"]        # older specs unchanged


def test_drift_matched_control_separates_timing_from_beta():
    from strategy_agent.evaluation import controls as CT
    t0 = datetime(2020, 1, 2, 9, 30, tzinfo=NY)
    bars = [{"bar_end": t0 + timedelta(minutes=15 * i), "close": 100.0 * math.exp(0.001 * i)} for i in range(50)]
    mu = 0.001                                                              # constant drift per bar

    def trip(i, j, net):
        return {"entry_ts": bars[i]["bar_end"].isoformat(), "exit_ts": bars[j]["bar_end"].isoformat(), "qty": 10,
                "entry_price": bars[i]["close"], "net_pnl": net}
    beta = [trip(i, i + 4, 10 * bars[i]["close"] * (math.exp(mu * 4) - 1)) for i in (0, 10, 20)]
    r = CT.drift_matched(beta, bars)
    assert math.isclose(r["mu_log_per_bar"], mu) and abs(r["excess_pnl"]) < 1e-9          # pure drift = no timing
    timed = [dict(t, net_pnl=t["net_pnl"] + 5) for t in beta]
    assert math.isclose(CT.drift_matched(timed, bars)["mean_excess_per_trade"], 5)
    assert CT.drift_matched([], bars)["status"] == "NOT_EVALUABLE"
