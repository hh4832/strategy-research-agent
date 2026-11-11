"""DRIFT-MATCHED-v1: deterministic, in-sample unconditional-drift baseline.

Compare each trade net PnL to qty * entry_price * (exp(mu * holding_bars)-1).
mu is estimated on the supplied Core bars (including supplied warm-up). This is
not random-entry simulation, an independent-sample test or proof of timing alpha.
The t-statistic is descriptive only; no p-value or new threshold is introduced.
"""
from __future__ import annotations

import math
import statistics
from bisect import bisect_right
from datetime import datetime
from typing import Any, Mapping, Sequence

VERSION = "DRIFT-MATCHED-v1"


def _ts(x) -> datetime:
    return x if isinstance(x, datetime) else datetime.fromisoformat(str(x))


def drift_matched(trips: Sequence[Mapping], bars: Sequence[Mapping]) -> dict[str, Any]:
    bs = sorted(bars, key=lambda b: _ts(b["bar_end"]))
    ends = [_ts(b["bar_end"]) for b in bs]
    px = [float(b["close"]) for b in bs]
    lr = [math.log(px[i] / px[i - 1]) for i in range(1, len(px)) if px[i - 1] > 0 and px[i] > 0]
    if not lr or not trips:
        return {"version": VERSION, "status": "NOT_EVALUABLE", "reason": "no bars or no trades"}
    mu = statistics.fmean(lr)
    rows = []
    for t in trips:
        a, b = _ts(t["entry_ts"]), _ts(t["exit_ts"])
        h = max(bisect_right(ends, b) - bisect_right(ends, a), 0)          # bars with entry < bar_end <= exit
        exp_pnl = float(t["qty"]) * float(t["entry_price"]) * (math.exp(mu * h) - 1.0)
        rows.append(float(t["net_pnl"]) - exp_pnl)
    n = len(rows)
    mean = statistics.fmean(rows)
    sd = statistics.stdev(rows) if n > 1 else float("nan")
    tstat = mean / (sd / math.sqrt(n)) if n > 1 and sd > 0 else None
    return {"version": VERSION, "status": "EVALUATED", "mu_log_per_bar": mu, "bars": len(px), "trades": n,
            "net_pnl": math.fsum(float(t["net_pnl"]) for t in trips), "drift_matched_pnl": math.fsum(
                float(t["net_pnl"]) for t in trips) - math.fsum(rows), "excess_pnl": math.fsum(rows),
            "mean_excess_per_trade": mean, "stdev_excess": sd if math.isfinite(sd) else None,
            "t_stat_mean_excess": tstat, "basis": "descriptive t-statistic under an unverified i.i.d. assumption; in-sample shared drift estimate; no significance claim",
            "meaning": "positive excess exceeds the deterministic in-sample unconditional-drift baseline; not evidence of statistically significant timing alpha",
            "method": "DETERMINISTIC_UNCONDITIONAL_DRIFT", "independent_sample_test": False, "random_entry_simulation": False}


def c1_decompositions(trips, bars):
    """Fixed descriptive splits only; no rerun, re-optimization or threshold changes."""
    from zoneinfo import ZoneInfo
    from strategy_agent.evaluation.research import trade_stats
    ny=ZoneInfo('America/New_York')
    exit_year=lambda t:_ts(t['exit_ts']).astimezone(ny).year
    seen=lambda t:'2024-05-29'<=_ts(t['entry_ts']).astimezone(ny).date().isoformat()<='2024-06-28'
    ends=sorted(_ts(b['bar_end']) for b in bars)
    buckets={'1-4':[],'5-16':[],'17-64':[],'>=65':[],'0':[]}
    for t in trips:
        h=bisect_right(ends,_ts(t['exit_ts']))-bisect_right(ends,_ts(t['entry_ts']))
        key='0' if h==0 else '1-4' if h<=4 else '5-16' if h<=16 else '17-64' if h<=64 else '>=65'
        buckets[key].append(float(t['net_pnl']))
    stats=lambda group:trade_stats([float(t['net_pnl']) for t in group])
    return {'by_year':{str(y):stats([t for t in trips if exit_year(t)==y]) for y in range(2017,2025)},
            'by_half':{f'{a}-{b}':stats([t for t in trips if a<=exit_year(t)<=b]) for a,b in ((2017,2020),(2021,2024))},
            'by_holding_period_bars':{k:trade_stats(v) for k,v in buckets.items()},
            'seen_month_sensitivity':{'including':stats(trips),'excluding':stats([t for t in trips if not seen(t)]),
                                     'exclusion_rule':'entry New York date in 2024-05-29..2024-06-28'},
            'year_assignment':'exit New York calendar year; same trade accounting, no independent backtests'}
