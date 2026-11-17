"""P5C-TA-01 signal: hand-built synthetic OHLCV and a simulated NYSE-like calendar only (no market data)."""
from __future__ import annotations

import ast
import copy
import json
from dataclasses import replace
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from strategy_agent.signals import ta01 as T
from strategy_agent.signals.core import SignalBar, SignalInputError, cut

ROOT = Path(__file__).resolve().parents[1]
NY = ZoneInfo("America/New_York")
SPEC = json.loads((ROOT / "tests/fixtures/p5c_ta01/ta01_spec_proposal.json").read_text(encoding="utf-8"))
DEFAULTS = {k: SPEC["parameters"]["values"][k]["default"] for k in T.PARAMETER_KEYS}
P = T.TA01Params(res_n=3, vol_m=3, vol_k=2.0, close_pos=0.75, sup_l=2, max_hold_bars=3,
                 last_entry_local=time(14, 30), flatten_local=time(15, 15), regular_close_local=time(16, 0),
                 timezone="America/New_York")
FLAT = (100.0, 100.2, 99.8, 100.0, 1000.0)          # open, high, low, close, volume
BREAK = (100.0, 101.0, 99.9, 100.95, 5000.0)         # breakout + bullish + strong close + 5x volume
HOLD = (100.9, 101.0, 100.8, 100.9, 1000.0)


def day(d: str, rows, *, start="09:45", close="16:00", eligible=None):
    """rows: list of OHLCV tuples on consecutive 15m buckets from ``start``. available_at = bucket end."""
    t0 = datetime.fromisoformat(f"{d}T{start}:00").replace(tzinfo=NY)
    bars = []
    for i, (o, h, lw, c, v) in enumerate(rows):
        ts = t0 + timedelta(minutes=15 * i)
        el = True if eligible is None else eligible[i]
        bars.append(SignalBar(ts, ts + timedelta(minutes=15), o, h, lw, c, v, "TSM", d, el))
    s_open = datetime.fromisoformat(f"{d}T09:30:00").replace(tzinfo=NY)
    s_close = datetime.fromisoformat(f"{d}T{close}:00").replace(tzinfo=NY)
    return bars, {d: (s_open, s_close)}


def series(*days):
    bars, sess = [], {}
    for b, s in days:
        bars += b
        sess.update(s)
    return bars, sess


def reasons(decs):
    return [(d.decision_ts.astimezone(NY).strftime("%m-%d %H:%M"), d.target, d.reason) for d in decs]


# ------------------------------------------------------------------------------------------ components
def test_each_component_true_and_false():
    b = day("2025-01-13", [BREAK])[0][0]
    assert T.pattern(b, 100.2, 1000.0, P) == {"breakout": True, "bullish": True, "strong_close": True, "relative_volume": True}
    assert not T.pattern(b, 101.0, 1000.0, P)["breakout"]                  # close not above resistance
    assert not T.pattern(replace(b, close=99.95), 99.0, 1000.0, P)["bullish"]
    weak = replace(b, close=100.2)                                          # (100.2-99.9) < 0.75*1.1
    assert T.pattern(weak, 100.0, 1000.0, P)["strong_close"] is False
    assert T.pattern(replace(b, high=100.95, low=100.95, open=100.95), 100.0, 1.0, P)["strong_close"] is False  # zero range
    assert not T.pattern(b, 100.2, 2600.0, P)["relative_volume"]           # 5000 < 2 x 2600
    assert T.pattern(b, 100.2, 2500.0, P)["relative_volume"]               # boundary: >= is inclusive


@pytest.mark.parametrize("bad,why", [((100.0, 100.15, 99.9, 100.1, 5000.0), "no breakout"),
                                     ((100.95, 101.0, 99.9, 100.9, 5000.0), "bearish"),
                                     ((99.9, 101.0, 99.8, 100.3, 5000.0), "weak close"),
                                     ((100.0, 101.0, 99.9, 100.95, 1999.0), "low volume")])
def test_entry_needs_all_components(bad, why):
    bars, sess = day("2025-01-13", [FLAT] * 4 + [bad] + [FLAT] * 2)
    assert T.ta01_decisions(bars, sess, P) == [], why
    bars, sess = day("2025-01-13", [FLAT] * 4 + [BREAK] + [FLAT] * 2)
    assert reasons(T.ta01_decisions(bars, sess, P))[0][1:] == (1, "TA01_ENTRY")


def test_windows_exclude_current_bar_and_need_warmup():
    # if the current bar were in the window, close <= own high could never break out and its own volume
    # would inflate the baseline; the entry proves both windows are prior-only
    bars, sess = day("2025-01-13", [FLAT] * 3 + [BREAK])
    assert reasons(T.ta01_decisions(bars, sess, P)) == [("01-13 10:45", 1, "TA01_ENTRY")]
    bars, sess = day("2025-01-13", [FLAT] * 2 + [BREAK])                   # only 2 prior bars < res_n = 3
    assert T.ta01_decisions(bars, sess, P) == []


def test_ineligible_buckets_never_enter_windows_or_hold_count():
    spike = (100.0, 150.0, 99.0, 100.0, 99999.0)                           # e.g. auction / zero-volume bucket
    rows = [FLAT, FLAT, spike, FLAT, BREAK, HOLD, HOLD, HOLD, HOLD]
    elig = [True, True, False, True, True, True, False, True, True]
    bars, sess = day("2025-01-13", rows, eligible=elig)
    out = reasons(T.ta01_decisions(bars, sess, P))
    # entry at 11:00: the ineligible 10:30 spike did not raise R or V, and 3 eligible prior bars completed warm-up;
    # held bars count only eligible buckets: 11:15 (1), 11:30 skipped, 11:45 (2), 12:00 (3) -> MAX_HOLD at 12:00
    assert out == [("01-13 11:00", 1, "TA01_ENTRY"), ("01-13 12:00", 0, "MAX_HOLD")]


def test_entry_cutoff_is_strict_on_decision_time():
    late_ok = [FLAT] * 17 + [BREAK]            # 18th bucket from 09:45 = 14:00-14:15, decision 14:15 < 14:30
    bars, sess = day("2025-01-13", late_ok)
    assert reasons(T.ta01_decisions(bars, sess, P))[0][0] == "01-13 14:15"
    too_late = [FLAT] * 18 + [BREAK]           # decision 14:30 is NOT < 14:30
    bars, sess = day("2025-01-13", too_late)
    assert T.ta01_decisions(bars, sess, P) == []


def test_one_entry_per_session_and_single_position():
    rows = [FLAT] * 3 + [BREAK, (100.9, 101.0, 99.0, 99.1, 1000.0)] + [FLAT] * 3 + [BREAK] + [HOLD] * 2
    bars, sess = day("2025-01-13", rows)
    out = reasons(T.ta01_decisions(bars, sess, P))
    assert [r[1] for r in out] == [1, 0]                                   # second breakout same day ignored
    assert out[1][2] == "SUPPORT_BREAK"
    nxt, s2 = day("2025-01-14", [FLAT] * 3 + [BREAK, HOLD])
    bars3, sess3 = series((bars, sess), (nxt, s2))
    entries = [r for r in reasons(T.ta01_decisions(bars3, sess3, P)) if r[1] == 1]
    assert [e[0][:5] for e in entries] == ["01-13", "01-14"]               # next session allowed again


def test_support_exit_max_hold_and_time_flatten():
    bars, sess = day("2025-01-13", [FLAT] * 3 + [BREAK, HOLD, (100.9, 101.0, 99.0, 99.5, 1000.0)])
    assert reasons(T.ta01_decisions(bars, sess, P))[-1][2] == "SUPPORT_BREAK"     # 99.5 < min(prior 2 lows)
    bars, sess = day("2025-01-13", [FLAT] * 3 + [BREAK] + [HOLD] * 3)
    assert reasons(T.ta01_decisions(bars, sess, P))[-1] == ("01-13 11:30", 0, "MAX_HOLD")
    p = replace(P, max_hold_bars=50)
    rows = [FLAT] * 17 + [BREAK] + [HOLD] * 6                              # entry 14:15; 15:15 decision flattens
    bars, sess = day("2025-01-13", rows)
    assert reasons(T.ta01_decisions(bars, sess, p))[-1] == ("01-13 15:15", 0, "TIME_FLATTEN")


def test_time_exit_is_first_eligible_bucket_at_or_after_1515():
    p = replace(P, max_hold_bars=50)
    rows = [FLAT] * 17 + [BREAK] + [HOLD] * 6                              # decisions 14:15 (entry) ... 15:45
    elig = [True] * 21 + [False, False, True]                              # 15:15 and 15:30 decisions ineligible
    bars, sess = day("2025-01-13", rows, eligible=elig)
    out = reasons(T.ta01_decisions(bars, sess, p))
    assert out == [("01-13 14:15", 1, "TA01_ENTRY"), ("01-13 15:45", 0, "TIME_FLATTEN")]
    # nothing before 15:15 exits on time, and there is no separate session-end exit: a session closing at 16:00
    # with eligible buckets only up to 15:00 leaves the position open in-session (fail-safe handles it later)
    elig2 = [True] * 21 + [False, False, False]
    bars2, sess2 = day("2025-01-13", rows, eligible=elig2)
    assert reasons(T.ta01_decisions(bars2, sess2, p)) == [("01-13 14:15", 1, "TA01_ENTRY")]


def test_exit_impossible_in_session_flattens_on_next_session_and_blocks_same_bar_entry():
    p = replace(P, max_hold_bars=50)
    rows = [FLAT] * 17 + [BREAK] + [HOLD] * 6
    elig = [True] * 19 + [False] * 5                                       # every bucket after 14:30 ineligible
    d1 = day("2025-01-13", rows, eligible=elig)
    d2 = day("2025-01-14", [BREAK] + [FLAT] * 3)                           # pattern on the first bar of day 2
    bars, sess = series(d1, d2)
    out = reasons(T.ta01_decisions(bars, sess, p))
    assert out[-1] == ("01-14 10:00", 0, "SESSION_CHANGE")                 # no entry on the flattening bar
    assert sum(r[1] == 1 for r in out) == 1


def test_early_close_session_has_no_entry():
    bars, sess = day("2025-11-28", [FLAT] * 3 + [BREAK, HOLD], close="13:00")
    assert T.ta01_decisions(bars, sess, P) == []


def test_open_at_end_of_series_emits_no_fake_exit():
    bars, sess = day("2025-01-13", [FLAT] * 3 + [BREAK, HOLD])
    out = reasons(T.ta01_decisions(bars, sess, P))
    assert out == [("01-13 10:45", 1, "TA01_ENTRY")]                       # left open; execution must report it


def test_exclusion_resets_windows_and_flattens():
    bars, sess = day("2025-01-13", [FLAT] * 3 + [BREAK, HOLD, HOLD])
    excl = [(bars[4].available_at, bars[5].ts + timedelta(minutes=1))]
    out = reasons(T.ta01_decisions(bars, sess, P, excl))
    assert out[-1][2] == "EXCLUSION"


def test_prefix_invariance_no_lookahead():
    d1 = day("2025-01-13", [FLAT] * 3 + [BREAK] + [HOLD] * 4)
    d2 = day("2025-01-14", [FLAT] * 2 + [BREAK] + [HOLD] * 4)
    bars, sess = series(d1, d2)
    full = T.ta01_decisions(bars, sess, P)
    for b in bars:
        c = b.available_at
        assert [d for d in full if d.decision_ts <= c] == T.ta01_decisions(cut(bars, c), sess, P)


# -------------------------------------------------------------------------------------- post-execution check
def test_intraday_check():
    same = {"entry_ts": "2025-01-13T10:30:00-05:00", "exit_ts": "2025-01-13T15:30:00-05:00"}
    over = {"entry_ts": "2025-01-13T15:30:00-05:00", "exit_ts": "2025-01-14T10:00:00-05:00"}
    assert T.intraday_check([same], None, "America/New_York")["status"] == "PASS"
    assert T.intraday_check([same, over], None, "America/New_York")["violations"][0]["code"] == "EXIT_NOT_FILLED_IN_SESSION"
    assert T.intraday_check([], {"TSM": 100}, "America/New_York")["violations"][0]["code"] == "OPEN_POSITION_AT_END"
    assert T.intraday_check([{"entry_ts": "x"}], [], "America/New_York")["status"] == "FAIL"


def test_sessions_from_rows_fail_closed():
    so = datetime(2025, 1, 13, 9, 30, tzinfo=NY)
    sc = datetime(2025, 1, 13, 16, 0, tzinfo=NY)
    assert T.sessions_from_rows([{"session": "d", "session_open": so, "session_close": sc}] * 2) == {"d": (so, sc)}
    with pytest.raises(SignalInputError):
        T.sessions_from_rows([{"session": "d", "session_open": so, "session_close": sc},
                              {"session": "d", "session_open": so, "session_close": sc - timedelta(hours=3)}])
    with pytest.raises(SignalInputError):
        T.sessions_from_rows([{"session": "d", "session_open": so.replace(tzinfo=None), "session_close": sc}])


# ------------------------------------------------------------------------------------------- spec binding
def test_spec_binds_to_exact_reviewed_structure():
    p = T.params_from_spec(SPEC, DEFAULTS)
    assert (p.res_n, p.vol_m, p.vol_k, p.close_pos, p.sup_l, p.max_hold_bars) == (20, 20, 2.0, 0.75, 10, 8)
    assert (p.last_entry_local, p.flatten_local, p.regular_close_local) == (time(14, 30), time(15, 15), time(16, 0))


@pytest.mark.parametrize("mutate", [
    lambda s: s["signal"]["entry_rules"][0]["when"]["args"].pop(),                         # drop volume rule
    lambda s: s["signal"]["indicators"][0].update(lag=0),                                   # window incl. current
    lambda s: s["signal"]["exit_rules"][0]["when"].update(op="gt"),                         # different exit
    lambda s: s["risk_constraints"].update(allowed_directions=["LONG", "SHORT"]),
    lambda s: s["parameters"]["search"].update(mode="PREREGISTERED_GRID", max_trials=4),
    lambda s: s["universe"]["sessions"].update(calendar_ref="NYSE RTH"),
    lambda s: s.update(strategy_id="sma_crossover_lookalike"),
    lambda s: s["risk_exits"]["time_exit"].update(flatten_before_session_end=True),   # no session-end exit rule
    lambda s: s["risk_exits"]["time_exit"].pop("flatten_before_session_end"),
])
def test_spec_binding_fails_closed(mutate):
    s = copy.deepcopy(SPEC)
    mutate(s)
    with pytest.raises(SignalInputError):
        T.params_from_spec(s, DEFAULTS)


@pytest.mark.parametrize("params", [{**DEFAULTS, "res_n": 30}, {k: v for k, v in DEFAULTS.items() if k != "sup_l"},
                                    {**DEFAULTS, "fast": 5}])
def test_run_parameters_must_equal_spec_defaults(params):
    with pytest.raises(SignalInputError):
        T.params_from_spec(SPEC, params)


# ------------------------------------------------------------------------------------------ dispatch
def test_dispatch_routes_only_ta01_specs_and_keeps_sma_path():
    from strategy_agent.research_only import core_phase4a as P4
    sma_spec = json.loads((ROOT / "specs/research/sma_crossover_tsm_15m_5_20_v0_1_1.json").read_text(encoding="utf-8"))
    assert P4.signal_family(sma_spec, {"fast": 5, "slow": 20}) == ("SMA", None)
    fam, p = P4.signal_family(SPEC, DEFAULTS)
    assert fam == "TA01" and p.res_n == 20
    with pytest.raises(P4.Phase4ABindingError):
        P4.signal_family(sma_spec, {**DEFAULTS})                           # TA-01 params never reach SMA
    with pytest.raises(P4.Phase4ABindingError):
        P4.signal_family(SPEC, {"fast": 5, "slow": 20})                    # SMA params never reach TA-01
    bad = copy.deepcopy(SPEC)
    bad["signal"]["indicators"][1]["input"] = "close"
    with pytest.raises(P4.Phase4ABindingError):
        P4.signal_family(bad, DEFAULTS)


def test_ta01_never_uses_sma_orb_or_atr_code():
    src = (ROOT / "src/strategy_agent/signals/ta01.py").read_text(encoding="utf-8")
    mods = {n.module for n in ast.walk(ast.parse(src)) if isinstance(n, ast.ImportFrom) and n.module}
    assert not {"sma", "orb", "atr"} & {m.split(".")[-1] for m in mods}
    p4 = (ROOT / "src/strategy_agent/research_only/core_phase4a.py").read_text(encoding="utf-8")
    assert "signals.orb" not in p4 and "signals.atr" not in p4
