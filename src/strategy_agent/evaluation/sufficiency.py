"""Sample sufficiency after exclusion-window invalidation (decision Q5).

Trades whose holding period spans an exclusion window are INVALIDATED: they
never count toward effective performance. Removing them can (a) shrink the
sample below what supports a conclusion and (b) bias it if the removed
trades are systematically different. This check reports both; invalidated
PnL is shown for transparency only.
"""
from __future__ import annotations

from typing import Sequence

DEFAULTS = {"min_valid_trades": 30, "max_invalidated_fraction": 0.10, "max_invalidated_abs_pnl_share": 0.25}


def assess(valid_net_pnls: Sequence[float], invalidated_net_pnls: Sequence[float], *,
           min_valid_trades: int = DEFAULTS["min_valid_trades"],
           max_invalidated_fraction: float = DEFAULTS["max_invalidated_fraction"],
           max_invalidated_abs_pnl_share: float = DEFAULTS["max_invalidated_abs_pnl_share"]) -> dict:
    v, x = [float(a) for a in valid_net_pnls], [float(a) for a in invalidated_net_pnls]
    n_v, n_x = len(v), len(x)
    total = n_v + n_x
    frac = n_x / total if total else 0.0
    abs_all = sum(abs(a) for a in v) + sum(abs(a) for a in x)
    share = sum(abs(a) for a in x) / abs_all if abs_all else 0.0
    reasons = []
    if n_v < min_valid_trades:
        reasons.append({"code": "Q5-1", "message": f"valid trades {n_v} < {min_valid_trades} after invalidation"})
    if frac > max_invalidated_fraction:
        reasons.append({"code": "Q5-2", "message": f"invalidated fraction {frac:.3f} > {max_invalidated_fraction}"})
    if share > max_invalidated_abs_pnl_share:
        reasons.append({"code": "Q5-3", "message": f"invalidated |PnL| share {share:.3f} > {max_invalidated_abs_pnl_share}"})
    return {
        "status": "SUFFICIENT" if not reasons else "INSUFFICIENT",
        "valid_trades": n_v,
        "invalidated_trades": n_x,
        "invalidated_fraction": frac,
        "invalidated_abs_pnl_share": share,
        "effective_net_pnl": sum(v),                    # the ONLY performance figure
        "invalidated_net_pnl_for_reference": sum(x),    # never counted
        "reasons": reasons,
    }
