"""Pre-registered market-drift control for long-only strategies (Phase 5-C1, ``DRIFT-MATCHED-v1``).

The spec's null hypothesis is "no better than random-entry long trades with the same holding-time distribution".
In expectation such a random long trade earns the unconditional drift over its holding span, so each trade's net
PnL is compared with ``qty * entry_price * (exp(mu * h) - 1)``: ``mu`` = mean bar-to-bar log close return over the
bars given to Core, ``h`` = bars held (entry < bar_end <= exit). A trend-timing edge must show a positive mean
EXCESS (net minus drift-matched); a positive net PnL with zero excess is market beta, not timing. Descriptive
t-statistic only (i.i.d. assumption stated); no threshold is applied here.
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
            "t_stat_mean_excess": tstat, "basis": "i.i.d. trades; descriptive, no threshold applied here",
            "meaning": "excess > 0 = timing beyond unconditional drift; excess ~ 0 with net > 0 = market beta"}
