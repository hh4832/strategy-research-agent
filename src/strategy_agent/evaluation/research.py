"""Research performance evaluation for one finished backtest (M3-B).

Inputs are the ``BacktestReport`` produced by the formal engine (via the M1 adapter) and the bar table that was
given to it. Nothing here simulates fills or recomputes PnL: trade PnL, fees, taxes and equity come from Core;
this module only aggregates them. Every figure that cannot be supported by the sample is returned as
``{"status": "NOT_EVALUABLE", "reason": ...}`` instead of a number.

Conventions
-----------
* Trades = VALID round trips only (exclusion-invalidated trips are reported separately, never as performance).
* Returns for Sharpe / tail risk = bar-to-bar changes of Core's equity curve, i.e. mark-to-market net of costs.
* Holding period is measured in bars of the evaluated timeframe: (exit_ts - entry_ts) / bar length.
* Market regimes are evaluated only when the spec pre-registers a regime definition; none exists in spec v1, so
  "by_regime" is NOT_EVALUABLE by construction (no post-hoc regime labels).
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
from scipy import stats

from . import metrics as M
from .sufficiency import assess

MIN_TRADES_FOR_RATIOS = 10            # win rate / payoff / PF / expectancy need at least this many valid trips
MIN_SESSIONS_FOR_SHARPE = 20
MIN_TRADES_FOR_SHARPE = 10
MIN_RETURNS_FOR_TAIL = 100
HOLDING_BUCKETS = ((1, 4), (5, 16), (17, 64), (65, None))     # bars, inclusive


def not_evaluable(reason: str) -> dict:
    return {"status": "NOT_EVALUABLE", "reason": reason}


def _f(x: Any) -> float:
    return float(x)


def _ts(x: Any) -> datetime:
    return x if isinstance(x, datetime) else datetime.fromisoformat(str(x))


def _finite(x: float) -> float | None:
    return float(x) if x is not None and math.isfinite(x) else None


def trade_stats(pnls: Sequence[float], *, label: str = "") -> dict:
    """Trade-level statistics on net PnL per round trip (account currency)."""
    p = [float(x) for x in pnls]
    n = len(p)
    out: dict[str, Any] = {"trade_count": n, "net_pnl": float(math.fsum(p))}
    if n < MIN_TRADES_FOR_RATIOS:
        why = f"{n} valid round trips{(' in ' + label) if label else ''} < {MIN_TRADES_FOR_RATIOS}"
        for k in ("win_rate", "average_win", "average_loss", "payoff_ratio", "expectancy", "profit_factor"):
            out[k] = not_evaluable(why)
        return out
    wins, losses = [x for x in p if x > 0], [x for x in p if x < 0]
    out["win_rate"] = len(wins) / n
    out["average_win"] = float(np.mean(wins)) if wins else not_evaluable("no winning trades")
    out["average_loss"] = float(np.mean(losses)) if losses else not_evaluable("no losing trades")
    out["payoff_ratio"] = (out["average_win"] / abs(out["average_loss"])
                           if wins and losses else not_evaluable("needs both wins and losses"))
    out["expectancy"] = float(math.fsum(p) / n)
    pf = M.profit_factor(p)
    out["profit_factor"] = _finite(pf) if math.isfinite(pf) else not_evaluable("no losing trades (PF undefined)")
    return out


def _bars_in_market(trips: Iterable[Mapping], open_pos: Mapping | None, bar_ends: Sequence[datetime],
                    end_of_data: datetime) -> list[int]:
    """1 where a position was held over the bar ending at t (entry < t <= exit), from Core's trips."""
    spans = [(_ts(t["entry_ts"]), _ts(t["exit_ts"])) for t in trips]
    for p in (open_pos or {}).get("positions", []):          # adapter shape: {"positions": [Core open rows]}
        spans.append((_ts(p["entry_ts"]), end_of_data))
    return [int(any(a < t <= b for a, b in spans)) for t in bar_ends]


def evaluate(report, bars: Sequence[Mapping], *, initial_capital: float, bar_minutes: int, bars_per_year: float,
             parameters: Mapping, symbol: str, order_qty: float, regime_definition: Mapping | None = None,
             family_id: str = "UNASSIGNED", family_trials: int = 1) -> dict:
    """All M3-B metrics. ``bars`` are the rows given to Core (bar_start, bar_end, open, volume, ...)."""
    trips = [dict(t) for t in report.round_trips]
    invalid = [dict(t) for t in report.invalidated_round_trips]
    fills = [dict(f) for f in report.fills]
    rej = [dict(r) for r in report.rejections]
    eq = [dict(e) for e in report.equity_curve]
    pnls = [_f(t["net_pnl"]) for t in trips]
    gross = [_f(t["gross_pnl"]) for t in trips]
    fees = float(math.fsum(_f(f["fee"]) for f in fills))
    taxes = float(math.fsum(_f(f["tax"]) for f in fills))
    sessions = sorted({str(b["session"]) for b in bars})

    # ---------------------------------------------------------------- overall
    out: dict[str, Any] = {"symbol": symbol, "parameters": dict(parameters), "bars": len(bars),
                           "sessions": len(sessions), "fills": len(fills), "rejections": len(rej)}
    out["trades"] = trade_stats(pnls)
    out["sample"] = assess(pnls, [_f(t["net_pnl"]) for t in invalid])
    out["open_position_at_end"] = report.open_position
    equity = [_f(e["equity"]) for e in eq]
    final_equity = equity[-1] if equity else initial_capital
    out["total_return"] = (final_equity - initial_capital) / initial_capital
    out["cost_adjusted_net_return"] = float(math.fsum(pnls)) / initial_capital       # closed trades, net of costs
    out["gross_return_closed_trades"] = float(math.fsum(gross)) / initial_capital
    out["costs"] = {"fees": fees, "taxes": taxes, "total": fees + taxes,
                    "cost_share_of_gross": (fees + taxes) / abs(math.fsum(gross)) if math.fsum(gross) else None}
    out["max_drawdown"] = M.max_drawdown(equity) if equity else not_evaluable("no equity curve")
    out["max_drawdown_duration_bars"] = M.max_drawdown_duration(equity) if equity else None

    rets = np.diff(np.asarray(equity)) / np.asarray(equity[:-1]) if len(equity) > 1 else np.asarray([])
    if len(sessions) < MIN_SESSIONS_FOR_SHARPE or len(trips) < MIN_TRADES_FOR_SHARPE:
        out["sharpe_ratio"] = not_evaluable(f"needs >= {MIN_SESSIONS_FOR_SHARPE} sessions and >= "
                                            f"{MIN_TRADES_FOR_SHARPE} valid trades (have {len(sessions)}, {len(trips)})")
    else:
        out["sharpe_ratio"] = {"annualised": _finite(M.sharpe_ratio(rets, bars_per_year)),
                               "per_bar": _finite(M.sharpe_ratio(rets)),
                               "basis": f"bar returns of Core equity, sqrt({bars_per_year:g}) scaling, i.i.d. assumption"}

    bar_ends = [_ts(b["bar_end"]) for b in bars]
    in_mkt = _bars_in_market(trips + invalid, report.open_position, bar_ends, bar_ends[-1] if bar_ends else None)
    out["exposure"] = M.exposure(in_mkt) if in_mkt else not_evaluable("no bars")
    notional = [_f(f["fill_price"]) * _f(f["qty"]) for f in fills]
    years = len(sessions) / 252.0
    out["turnover_annualised"] = (M.turnover(notional, float(np.mean(equity)), years)
                                  if fills and equity and years > 0 else not_evaluable("no fills"))
    out["tail_risk"] = tail_risk(rets, pnls, eq)
    out["multiple_testing"] = multiple_testing(rets, family_id=family_id, family_trials=family_trials,
                                               n_sessions=len(sessions), n_trades=len(trips))

    # ---------------------------------------------------------------- decompositions
    out["by_year"] = {y: trade_stats(v, label=y) for y, v in _group(trips, lambda t: str(_ts(t["exit_ts"]).year)).items()}
    out["by_symbol"] = {s: trade_stats(v, label=s) for s, v in _group(trips, lambda t: str(t["symbol"])).items()}
    bm = bar_minutes * 60.0

    def bucket(t):
        held = round((_ts(t["exit_ts"]) - _ts(t["entry_ts"])).total_seconds() / bm)
        for lo, hi in HOLDING_BUCKETS:
            if held >= lo and (hi is None or held <= hi):
                return f"{lo}-{hi}" if hi else f">={lo}"
        return "0"
    order = [f"{lo}-{hi}" if hi else f">={lo}" for lo, hi in HOLDING_BUCKETS]
    held = _group(trips, bucket)
    out["by_holding_period_bars"] = {k: trade_stats(held[k], label=k) for k in sorted(
        held, key=lambda k: order.index(k) if k in order else -1)}
    key = ",".join(f"{k}={parameters[k]}" for k in sorted(parameters))
    out["by_parameter_set"] = {key: trade_stats(pnls, label=key)}
    out["by_regime"] = (not_evaluable("no pre-registered regime definition in the spec; regimes are never labelled "
                                      "after seeing results") if not regime_definition else
                        not_evaluable("regime evaluation not implemented in M3-B"))

    # ---------------------------------------------------------------- execution / data quality
    out["data_quality"] = data_quality(bars, fills, rej, order_qty, bar_minutes)
    return out


def multiple_testing(rets: np.ndarray, *, family_id: str, family_trials: int, n_sessions: int,
                     n_trades: int) -> dict:
    """Selection-bias bookkeeping from the registry: the family's trial count is the N of the deflated Sharpe.
    With N = 1 the DSR equals the PSR against 0; with N > 1 it needs the cross-trial Sharpe variance, which a
    single fixed run does not have, so it is NOT_EVALUABLE rather than guessed."""
    out = {"family_id": family_id, "family_trials": family_trials,
           "note": "every attempt in the family counts (completed, failed, rejected) except governance/budget stops"}
    if n_sessions < MIN_SESSIONS_FOR_SHARPE or n_trades < MIN_TRADES_FOR_SHARPE or len(rets) < MIN_RETURNS_FOR_TAIL:
        out["psr_vs_zero"] = out["deflated_sharpe"] = not_evaluable("sample below the Sharpe minimums")
        return out
    out["psr_vs_zero"] = _finite(M.probabilistic_sharpe_ratio(rets, 0.0))
    out["deflated_sharpe"] = (out["psr_vs_zero"] if family_trials <= 1 else
                              not_evaluable(f"{family_trials} trials: needs the cross-trial Sharpe variance"))
    return out


def _group(trips, keyf) -> dict[str, list[float]]:
    g: dict[str, list[float]] = defaultdict(list)
    for t in trips:
        g[keyf(t)].append(_f(t["net_pnl"]))
    return dict(sorted(g.items()))


def tail_risk(rets: np.ndarray, pnls: Sequence[float], eq: Sequence[Mapping]) -> dict:
    out: dict[str, Any] = {}
    if len(rets) < MIN_RETURNS_FOR_TAIL:
        out["bar_return"] = not_evaluable(f"{len(rets)} bar returns < {MIN_RETURNS_FOR_TAIL}")
    else:
        q = float(np.quantile(rets, 0.05))
        out["bar_return"] = {"var_95": q, "cvar_95": float(rets[rets <= q].mean()), "worst": float(rets.min()),
                             "skew": _finite(float(stats.skew(rets, bias=False))),
                             "excess_kurtosis": _finite(float(stats.kurtosis(rets, bias=False)))}
    if pnls:
        sd = float(np.std(pnls, ddof=1)) if len(pnls) > 1 else 0.0
        mu = float(np.mean(pnls))
        streak = worst = 0
        for x in pnls:
            streak = streak + 1 if x < 0 else 0
            worst = max(worst, streak)
        out["trades"] = {"worst_trade": float(min(pnls)), "best_trade": float(max(pnls)),
                         "max_consecutive_losses": worst,
                         "extreme_losses_below_mean_minus_3sd": int(sum(x < mu - 3 * sd for x in pnls)) if sd else 0}
    else:
        out["trades"] = not_evaluable("no valid trades")
    if eq:
        by_day: dict[str, float] = {}
        for e in eq:
            by_day[str(_ts(e["timestamp"]).date())] = _f(e["equity"])
        vals = list(by_day.values())
        d = np.diff(vals) / np.asarray(vals[:-1]) if len(vals) > 1 else np.asarray([])
        out["worst_session_return"] = float(d.min()) if d.size else not_evaluable("fewer than 2 sessions")
    return out


def data_quality(bars: Sequence[Mapping], fills: Sequence[Mapping], rej: Sequence[Mapping], order_qty: float,
                 bar_minutes: int) -> dict:
    vols = [float(b["volume"]) for b in bars]
    by_session: dict[str, list[datetime]] = defaultdict(list)
    for b in bars:
        by_session[str(b["session"])].append(_ts(b["bar_start"]))
    expected = 0
    missing = 0
    for s, starts in by_session.items():
        b0 = next(b for b in bars if str(b["session"]) == s)
        span = (_ts(b0["session_close"]) - _ts(b0["session_open"])).total_seconds() / 60 / bar_minutes
        expected += int(round(span))
        missing += int(round(span)) - len(starts)
    opens = {str(_ts(b["bar_start"])): float(b["open"]) for b in bars}
    vol_at = {str(_ts(b["bar_start"])): float(b["volume"]) for b in bars}
    slip = []
    part = []
    for f in fills:
        k = str(_ts(f["exec_ts"]))
        if k in opens:
            slip.append(abs(_f(f["fill_price"]) - opens[k]) * _f(f["qty"]))
        if vol_at.get(k):
            part.append(_f(f["qty"]) / vol_at[k])
    reasons: dict[str, int] = defaultdict(int)
    for r in rej:
        reasons[str(r.get("reason"))] += 1
    return {
        "zero_volume_bars": int(sum(v <= 0 for v in vols)),
        "expected_bars_from_session_template": expected,
        "missing_bars": missing,
        "median_bar_volume": float(np.median(vols)) if vols else None,
        "order_qty": order_qty,
        "max_participation_of_bar_volume": float(max(part)) if part else None,
        "slippage_cost_vs_bar_open": float(math.fsum(slip)),
        "rejections_by_reason": dict(sorted(reasons.items())),
        "unfillable_signals": int(reasons.get("ORDER_EXPIRED", 0) + reasons.get("UNFILLED_AT_END_OF_DATA", 0)),
    }
