"""Pure signal functions: hand values, prefix invariance, future-perturbation
invariance, label-semantics independence (Q1), and segment resets."""
import json
import random
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from strategy_agent.signals.atr import atr_breakout, true_range, wilder_atr
from strategy_agent.signals.core import SignalBar, SignalInputError, cut
from strategy_agent.signals.orb import orb
from strategy_agent.signals.sma import sma_crossover

TPE = timezone(timedelta(hours=8))
CASES = Path(__file__).parents[1] / "benchmark" / "cases"
TF = timedelta(minutes=5)


def case_bars(stem):
    """Build SignalBars from a benchmark fixture. available_at is supplied
    EXPLICITLY here as fixture data (ts + 5m); the signal functions never derive it."""
    c = json.loads(next(CASES.glob(f"{stem}*.json")).read_text())
    out = []
    for row in c["bars"]:
        r = dict(zip(c["bars_columns"], row))
        ts = datetime.strptime(r["ts"], "%Y-%m-%d %H:%M").replace(tzinfo=TPE)
        out.append(SignalBar(ts, ts + TF, *(float(r[k]) for k in "ohlc"), float(r["v"]),
                             r.get("contract", c["instrument"]["symbol"]), ts.strftime("%Y-%m-%d"), r["v"] > 0))
    sessions = {s["date"]: (datetime.strptime(f"{s['date']} {s['start']}", "%Y-%m-%d %H:%M").replace(tzinfo=TPE),
                            datetime.strptime(f"{s['date']} {s['end']}", "%Y-%m-%d %H:%M").replace(tzinfo=TPE))
                for s in c["sessions"]}
    return out, sessions, c["expected"]["signals"]


def fmt(ds):
    return [{"signal_ts": d.decision_ts.strftime("%Y-%m-%d %H:%M"), "target": d.target} for d in ds]

# ------------------------------------------------------------ hand values


def test_sma_matches_b02_hand_values():
    bars, _, expected = case_bars("B02")
    assert fmt(sma_crossover(bars, 2, 3)) == expected


def test_orb_matches_b12_hand_values():
    bars, sessions, expected = case_bars("B12")
    got = orb(bars, sessions, range_bars=3, range_duration=timedelta(minutes=15),
              last_entry_offset=timedelta(minutes=30), flatten_offset=timedelta(minutes=35), allow_short=False)
    assert fmt(got) == expected


def test_wilder_atr_hand_values():
    t0 = datetime(2026, 1, 5, 9, tzinfo=TPE)
    hlc = [(10, 8, 9), (11, 9, 10), (12, 9, 11), (11, 10, 10.5), (13, 10, 12)]
    bars = [SignalBar(t0 + i * TF, t0 + (i + 1) * TF, c, h, l, c, 1, "X", "d", True) for i, (h, l, c) in enumerate(hlc)]
    # TR: bar1 max(2,|11-9|,|9-9|)=2 ; bar2 max(3,2,1)=3 ; bar3 max(1,0,1)=1 ; bar4 max(3,2.5,0.5)=3
    assert true_range(9, 11, 9) == 2
    atr = [a for _, a in wilder_atr(bars, 2)]
    assert atr == [None, None, 2.5, (2.5 * 1 + 1) / 2, ((2.5 + 1) / 2 * 1 + 3) / 2]

# --------------------------------------------------------- random series


def random_series(seed, sessions=6, per=40, contract_change_at=3, ineligible_p=0.03, missing_p=0.04,
                  label_shift=timedelta(0)):
    rng = random.Random(seed)
    bars, sess = [], {}
    px = 100.0
    for d in range(sessions):
        start = datetime(2026, 1, 5 + d, 9, tzinfo=TPE)
        sid = start.strftime("%Y-%m-%d")
        sess[sid] = (start, start + per * TF)
        con = "C1" if d < contract_change_at else "C2"
        if d == contract_change_at:
            px += 5
        for i in range(per):
            if rng.random() < missing_p and i > 3:
                continue
            o = px
            c = px * (1 + rng.gauss(0, 0.004))
            h, l = max(o, c) * (1 + abs(rng.gauss(0, 0.002))), min(o, c) * (1 - abs(rng.gauss(0, 0.002)))
            ts = start + i * TF
            bars.append(SignalBar(ts - label_shift, ts + TF, o, h, l, c, 100, con, sid, rng.random() > ineligible_p))
            px = c
    excl = [(datetime(2026, 1, 6, 10, tzinfo=TPE), datetime(2026, 1, 6, 10, 20, tzinfo=TPE))]
    bars = [replace(b, eligible=False) if any(s <= b.ts < e for s, e in excl) else b for b in bars]
    return bars, sess, excl


SIGNALS = {
    "sma": lambda b, s, x: sma_crossover(b, 5, 20, exclusions=x),
    "orb": lambda b, s, x: orb(b, s, range_bars=3, range_duration=timedelta(minutes=15),
                               last_entry_offset=timedelta(minutes=120), flatten_offset=timedelta(minutes=180),
                               exclusions=x),
    "atr": lambda b, s, x: atr_breakout(b, s, n=10, k=1.0, stop_atr=1.5, max_hold_bars=8,
                                        last_entry_offset=timedelta(minutes=150),
                                        flatten_offset=timedelta(minutes=190), exclusions=x),
}


@pytest.mark.parametrize("name", SIGNALS)
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_prefix_invariance(name, seed):
    fn = SIGNALS[name]
    bars, sess, excl = random_series(seed)
    full = fn(bars, sess, excl)
    assert full, "series should produce decisions (otherwise the test is vacuous)"
    for k in range(1, len(bars) + 1):
        c = bars[k - 1].available_at
        assert fn(cut(bars, c), sess, excl) == [d for d in full if d.decision_ts <= c], (name, k)


@pytest.mark.parametrize("name", SIGNALS)
def test_future_perturbation_invariance(name):
    fn = SIGNALS[name]
    bars, sess, excl = random_series(11)
    full = fn(bars, sess, excl)
    rng = random.Random(5)
    for k in rng.sample(range(10, len(bars) - 10), 15):
        c = bars[k - 1].available_at
        future = [replace(b, open=b.open * 1.3, high=b.high * 1.5, low=b.low * 0.6, close=b.close * 0.7)
                  for b in bars[k:]]
        got = fn(bars[:k] + future, sess, excl)
        assert [d for d in got if d.decision_ts <= c] == [d for d in full if d.decision_ts <= c]


@pytest.mark.parametrize("name", SIGNALS)
def test_independent_of_bar_label_semantics(name):
    """Q1: same available_at, bars labelled by start vs end -> identical decisions."""
    fn = SIGNALS[name]
    start_lab, sess, excl = random_series(7)
    end_lab = [replace(b, ts=b.available_at - timedelta(seconds=1)) for b in start_lab]
    assert fn(start_lab, sess, excl) == fn(end_lab, sess, excl)


def test_available_at_is_required_and_never_inferred():
    t = datetime(2026, 1, 5, 9, tzinfo=TPE)
    with pytest.raises(SignalInputError):
        sma_crossover([SignalBar(t, None, 1, 1, 1, 1, 1, "X", "d", True)], 2, 3)
    with pytest.raises(SignalInputError):
        sma_crossover([SignalBar(t, t - TF, 1, 1, 1, 1, 1, "X", "d", True)], 2, 3)
    with pytest.raises(SignalInputError):
        sma_crossover([SignalBar(t.replace(tzinfo=None), t.replace(tzinfo=None), 1, 1, 1, 1, 1, "X", "d", True)], 2, 3)


def test_contract_change_flattens_and_resets():
    bars, sess, excl = random_series(2)
    ds = sma_crossover(bars, 5, 20)
    change = next(b for b in bars if b.contract == "C2" and b.eligible)
    # no decision on C2 may be computed from fewer than 20 C2 bars, except the forced flatten
    c2 = [b for b in bars if b.contract == "C2" and b.eligible]
    first_computable = c2[19].available_at
    for d in ds:
        if d.contract == "C2" and d.reason != "CONTRACT_CHANGE":
            assert d.decision_ts > first_computable
    assert all(d.decision_ts >= change.available_at or d.contract == "C1" for d in ds)


def test_orb_incomplete_range_skips_session():
    bars, sessions, _ = case_bars("B12")
    bars = [b for b in bars if b.ts.minute != 5]              # drop one opening-range bar
    assert orb(bars, sessions, range_bars=3, range_duration=timedelta(minutes=15),
               last_entry_offset=timedelta(minutes=30), flatten_offset=timedelta(minutes=35)) == []


def test_orb_intrabar_touch_does_not_trigger():
    bars, sessions, _ = case_bars("B12")
    got = orb(bars, sessions, range_bars=3, range_duration=timedelta(minutes=15),
              last_entry_offset=timedelta(minutes=30), flatten_offset=timedelta(minutes=35), allow_short=False)
    assert got[0].decision_ts.strftime("%H:%M") == "09:25"     # 09:15 bar high touched but close did not confirm


def test_atr_threshold_uses_previous_bar():
    t0 = datetime(2026, 1, 5, 9, tzinfo=TPE)
    closes = [100, 101, 100, 101, 100, 101, 112]               # last bar: huge bar
    bars = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        bars.append(SignalBar(t0 + i * TF, t0 + (i + 1) * TF, o, max(o, c) + 0.5, min(o, c) - 0.5, c, 1, "X", "d", True))
    sess = {"d": (t0, t0 + 60 * TF)}
    ds = atr_breakout(bars, sess, n=3, k=3.0, stop_atr=1, max_hold_bars=5, last_entry_offset=timedelta(hours=2),
                      flatten_offset=timedelta(hours=3))
    # ATR_prev ~ 2; threshold 101 + 3*2 = 107 < 112 -> entry. Using ATR incl. the big bar (~5.3)
    # would give 101 + 16 = 117 > 112 -> no entry. Entry proves t-1 ATR.
    assert [d.reason for d in ds] == ["VOL_BREAKOUT"]


def _leaky_sma(bars, sess, excl):
    """Mutant: decides at bar t using bar t+1's close (one-bar look-ahead)."""
    shifted = [replace(b, close=bars[i + 1].close if i + 1 < len(bars) else b.close,
                       high=max(b.high, bars[i + 1].close if i + 1 < len(bars) else b.close),
                       low=min(b.low, bars[i + 1].close if i + 1 < len(bars) else b.close))
               for i, b in enumerate(bars)]
    return sma_crossover(shifted, 5, 20, exclusions=excl)


def test_invariance_harness_catches_lookahead_mutant():
    bars, sess, excl = random_series(1)
    full = _leaky_sma(bars, sess, excl)
    violations = sum(
        _leaky_sma(cut(bars, bars[k - 1].available_at), sess, excl)
        != [d for d in full if d.decision_ts <= bars[k - 1].available_at]
        for k in range(1, len(bars) + 1))
    assert violations > 0


def test_random_series_produce_enough_decisions():
    for seed in (1, 2, 3):
        bars, sess, excl = random_series(seed)
        counts = {n: len(fn(bars, sess, excl)) for n, fn in SIGNALS.items()}
        assert all(v >= 4 for v in counts.values()), counts


@pytest.mark.parametrize("name", SIGNALS)
def test_ineligible_bars_have_no_influence(name):
    fn = SIGNALS[name]
    bars, sess, excl = random_series(4, ineligible_p=0.0)
    poisoned = [replace(b, eligible=False, open=b.open * 9, high=b.high * 9, low=b.low * 0.1, close=b.close * 0.1)
                if i % 17 == 5 else b for i, b in enumerate(bars)]
    removed = [b for i, b in enumerate(bars) if i % 17 != 5]
    assert fn(poisoned, sess, excl) == fn(removed, sess, excl)
