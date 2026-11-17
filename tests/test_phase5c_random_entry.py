"""RANDOM-ENTRY-NULL-v1 on hand-built synthetic bars only. A fake executor stands in for the execution engine; the
same callable (same quantity and cost function) is used for the strategy and every draw."""
from __future__ import annotations

import ast
import builtins
from pathlib import Path

import pytest

from strategy_agent.evaluation import random_entry as R
from strategy_agent.signals import ta01 as T

from test_phase5c_ta01_signal import BREAK, FLAT, HOLD, NY, P, day, series

ROOT = Path(__file__).resolve().parents[1]
CFG = R.RandomEntryConfig(seed=7, draws=25, quantile=0.95, population="ALL_TRADEABLE_SESSIONS")


def market():
    """Five sessions; one TA-01 breakout on two of them; one early-close session (never tradeable)."""
    days = [day("2025-01-13", [FLAT] * 4 + [BREAK] + [HOLD] * 4),
            day("2025-01-14", [FLAT] * 9),
            day("2025-01-15", [FLAT] * 3 + [BREAK] + [HOLD] * 5),
            day("2025-01-16", [FLAT] * 9),
            day("2025-11-28", [FLAT] * 3 + [BREAK] + [HOLD] * 3, close="13:00")]
    return series(*days)


class FakeExecutor:
    """Fills at the decision bar's close (synthetic convention), qty 100, fixed 1.0 USD cost per side."""
    def __init__(self, bars):
        self.px = {b.available_at: b.close for b in bars}
        self.calls = []

    def __call__(self, decisions):
        self.calls.append(list(decisions))
        trades, entry = [], None
        for d in sorted(decisions, key=lambda x: x.decision_ts):
            if d.target == 1 and entry is None:
                entry = d
            elif d.target == 0 and entry is not None:
                gross = 100 * (self.px[d.decision_ts] - self.px[entry.decision_ts])
                trades.append({"entry_ts": entry.decision_ts, "exit_ts": d.decision_ts, "net_pnl": gross - 2.0})
                entry = None
        return trades


def test_config_is_explicit_and_validated():
    with pytest.raises(R.RandomEntryError, match="no defaults"):
        R.RandomEntryConfig.from_mapping({"seed": 1, "draws": 10})
    for bad in ({"seed": True}, {"seed": "1"}, {"draws": 0}, {"quantile": 1.0}, {"quantile": 95},
                {"population": "SAME_DAY"}):
        with pytest.raises(R.RandomEntryError):
            R.RandomEntryConfig(**{**CFG.__dict__, **bad})


def test_candidates_follow_strategy_constraints():
    bars, sess = market()
    cands = T.entry_candidates(bars, sess, P)
    assert "2025-11-28" not in cands                                    # early close: never a candidate
    for s, ts in cands.items():
        assert all(t.astimezone(NY).time() < P.last_entry_local for t in ts)
    assert cands["2025-01-13"][0] > bars[2].available_at                # warm-up (3 prior eligible bars) respected
    # after the strategy has entered on a session, later bars of that session are not candidates
    entry = next(d for d in T.ta01_decisions(bars, sess, P) if d.target == 1)
    assert max(cands[entry.decision_ts.astimezone(NY).date().isoformat()]) <= entry.decision_ts


def test_fixed_seed_is_reproducible_and_constraints_hold():
    bars, sess = market()
    a = R.null_decision_sets(bars, sess, P, n_entries=2, config=CFG)
    b = R.null_decision_sets(bars, sess, P, n_entries=2, config=CFG)
    assert a == b
    c = R.null_decision_sets(bars, sess, P, n_entries=2, config=R.RandomEntryConfig(8, 25, 0.95, "ALL_TRADEABLE_SESSIONS"))
    assert a != c
    cands = T.entry_candidates(bars, sess, P)
    allowed = {t for ts in cands.values() for t in ts}
    for decs in a:
        entries = [d for d in decs if d.target == 1]
        assert len(entries) == 2 and all(d.decision_ts in allowed for d in entries)
        assert len({d.decision_ts.astimezone(NY).date() for d in entries}) == 2      # one entry per session
        assert all(d.decision_ts.date().isoformat() != "2025-11-28" for d in entries)


def test_forced_entry_uses_identical_exit_logic():
    bars, sess = market()
    strat = T.ta01_decisions(bars, sess, P)
    forced = T.forced_entry_decisions(bars, sess, P, [d.decision_ts for d in strat if d.target == 1])
    assert [(d.decision_ts, d.target) for d in forced] == [(d.decision_ts, d.target) for d in strat]
    assert [d.reason for d in forced if d.target == 0] == [d.reason for d in strat if d.target == 0]


def test_no_permitted_points_or_too_many_entries_fail_closed():
    with pytest.raises(R.RandomEntryError, match="NO_PERMITTED_ENTRY_POINTS"):
        R.draw_entry_sets({}, 1, CFG)
    bars, sess = day("2025-11-28", [FLAT] * 6, close="13:00")
    with pytest.raises(R.RandomEntryError, match="NO_PERMITTED_ENTRY_POINTS"):
        R.null_decision_sets(bars, sess, P, n_entries=1, config=CFG)
    bars, sess = market()
    with pytest.raises(R.RandomEntryError, match="INSUFFICIENT_SESSIONS"):
        R.null_decision_sets(bars, sess, P, n_entries=5, config=CFG)
    with pytest.raises(R.RandomEntryError):
        R.null_decision_sets(bars, sess, P, n_entries=0, config=CFG)


def test_same_executor_quantity_and_costs_for_strategy_and_every_draw():
    bars, sess = market()
    ex = FakeExecutor(bars)
    out = R.run(bars, sess, P, strategy_decisions=T.ta01_decisions(bars, sess, P), execute=ex, config=CFG)
    assert len(ex.calls) == CFG.draws + 1
    assert out["strategy_trades"] == 2 and len(out["null_means"]) == CFG.draws
    assert out["version"] == "RANDOM-ENTRY-NULL-v1" and out["config"]["seed"] == 7
    assert out["null_quantile_value"] == R._quantile(out["null_means"], 0.95)
    assert "trading permission" in out["not_answered"]


def test_compare_and_quantile():
    cfg = R.RandomEntryConfig(1, 3, 0.5, "ALL_TRADEABLE_SESSIONS")
    out = R.compare([{"net_pnl": 3}], [[{"net_pnl": 1}], [{"net_pnl": 2}], [{"net_pnl": 4}]], cfg)
    assert out["null_quantile_value"] == 2 and out["share_of_null_below_strategy"] == 2 / 3
    assert out["strategy_above_quantile"] is True
    assert R._quantile([1, 2, 3, 4], 0.25) == 1.75
    with pytest.raises(R.RandomEntryError, match="NO_CLOSED_TRADES"):
        R.compare([], [[{"net_pnl": 1}]] * 3, cfg)
    with pytest.raises(R.RandomEntryError, match="TRADE_WITHOUT_NET_PNL"):
        R.compare([{"pnl": 1}], [[{"net_pnl": 1}]] * 3, cfg)
    with pytest.raises(R.RandomEntryError):
        R.compare([{"net_pnl": 1}], [[{"net_pnl": 1}]] * 2, cfg)


def test_reads_no_files_and_imports_no_data_path(monkeypatch):
    src = (ROOT / "src/strategy_agent/evaluation/random_entry.py").read_text(encoding="utf-8")
    mods = {n.module for n in ast.walk(ast.parse(src)) if isinstance(n, ast.ImportFrom) and n.module}
    mods |= {a.name for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Import) for a in n.names}
    assert not any(m.startswith(("strategy_agent.research", "strategy_agent.adapters", "strategy_agent.execution",
                                 "pandas", "pyarrow", "market_research_core")) for m in mods)
    bars, sess = market()

    def no_open(*a, **k):
        raise AssertionError("random-entry null must not open files")
    monkeypatch.setattr(builtins, "open", no_open)
    R.run(bars, sess, P, strategy_decisions=T.ta01_decisions(bars, sess, P), execute=FakeExecutor(bars), config=CFG)
