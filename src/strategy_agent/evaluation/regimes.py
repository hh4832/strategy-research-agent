"""Pre-registered market-regime labels for trade decomposition (Phase 5-C1).

Only the definition pre-registered in the strategy spec (``experiment.regime_definition``) is ever applied; regimes
are never chosen or re-cut after results are seen. Labels are point-in-time: a trade is labelled from completed
sessions STRICTLY BEFORE its entry session (the entry session itself and anything later are never read). A trade
without enough prior history is ``UNLABELLED`` and reported as such, never re-assigned.

Supported definition (``REGIME-DAILY-v1``), all numbers fixed in the spec:
  trend       prior session close vs the mean of the prior ``lookback_sessions`` session closes  -> UP | DOWN
  volatility  stdev of the prior ``window_sessions`` close-to-close log returns vs the median of that same statistic
              over the prior ``reference_sessions`` sessions                                     -> HIGH | LOW
Session close = close of the session's last bar given to Core (conservative RTH bars).
"""
from __future__ import annotations

import math
import statistics
from bisect import bisect_left
from datetime import datetime
from typing import Any, Mapping, Sequence

VERSION = "REGIME-DAILY-v1"
UNLABELLED = "UNLABELLED"


class RegimeDefinitionError(ValueError):
    pass


def validate(defn: Mapping) -> dict:
    if not isinstance(defn, Mapping) or defn.get("version") != VERSION:
        raise RegimeDefinitionError(f"regime definition must be version {VERSION}")
    t, v = defn.get("trend") or {}, defn.get("volatility") or {}
    for name, x, lo in (("trend.lookback_sessions", t.get("lookback_sessions"), 2),
                        ("volatility.window_sessions", v.get("window_sessions"), 2),
                        ("volatility.reference_sessions", v.get("reference_sessions"), 2)):
        if not isinstance(x, int) or isinstance(x, bool) or x < lo:
            raise RegimeDefinitionError(f"{name} must be an integer >= {lo}")
    return {"version": VERSION, "trend": {"lookback_sessions": t["lookback_sessions"]},
            "volatility": {"window_sessions": v["window_sessions"], "reference_sessions": v["reference_sessions"]}}


def session_closes(bars: Sequence[Mapping]) -> list[tuple[str, float]]:
    """(session, close of its last bar) in session order, from the bars given to Core."""
    last: dict[str, tuple[datetime, float]] = {}
    for b in bars:
        s, end = str(b["session"]), b["bar_end"]
        end = end if isinstance(end, datetime) else datetime.fromisoformat(str(end))
        if s not in last or end > last[s][0]:
            last[s] = (end, float(b["close"]))
    return [(s, last[s][1]) for s in sorted(last)]


def labels_for_sessions(closes: Sequence[tuple[str, float]], defn: Mapping) -> dict[str, dict[str, str]]:
    """Label for a trade ENTERING in session ``closes[i][0]``, computed from closes[:i] only."""
    d = validate(defn)
    L, W, R = d["trend"]["lookback_sessions"], d["volatility"]["window_sessions"], d["volatility"]["reference_sessions"]
    px = [c for _, c in closes]
    rets = [math.log(px[i] / px[i - 1]) for i in range(1, len(px))]          # rets[k] ends at session k+1
    vol_at: list[float | None] = []                                            # vol over sessions ending at j
    for j in range(len(px)):
        vol_at.append(statistics.stdev(rets[j - W:j]) if j >= W else None)
    out: dict[str, dict[str, str]] = {}
    for i, (s, _) in enumerate(closes):
        prior = px[:i]                                                          # strictly before the entry session
        trend = UNLABELLED
        if len(prior) >= L:
            trend = "UP" if prior[-1] > statistics.fmean(prior[-L:]) else "DOWN"
        vol = UNLABELLED
        j = i - 1                                                               # last completed session
        if j >= 0 and vol_at[j] is not None:
            ref = [x for x in vol_at[max(0, j - R + 1):j + 1] if x is not None]
            if len(ref) >= R:
                vol = "HIGH" if vol_at[j] > statistics.median(ref) else "LOW"
        out[s] = {"trend": trend, "volatility": vol}
    return out


def label_trade(trip: Mapping, sessions: Sequence[str], labels: Mapping[str, Mapping[str, str]], *,
                session_of) -> dict[str, str]:
    s = session_of(trip)
    if s not in labels:                                                         # entry outside the labelled sessions
        k = bisect_left(list(sessions), s)
        return {"trend": UNLABELLED, "volatility": UNLABELLED, "note": f"entry session {s} not in bars (pos {k})"}
    return dict(labels[s])


def decompose(trips: Sequence[Mapping], bars: Sequence[Mapping], defn: Mapping, *, session_of, stats_fn) -> dict[str, Any]:
    """by_regime = {"definition", "trend": {label: stats}, "volatility": {label: stats}, "counts"}."""
    d = validate(defn)
    closes = session_closes(bars)
    labels = labels_for_sessions(closes, d)
    sessions = [s for s, _ in closes]
    groups: dict[str, dict[str, list[float]]] = {"trend": {}, "volatility": {}}
    for t in trips:
        lab = label_trade(t, sessions, labels, session_of=session_of)
        for dim in ("trend", "volatility"):
            groups[dim].setdefault(lab[dim], []).append(float(t["net_pnl"]))
    return {"definition": d, "point_in_time": "labels use completed sessions strictly before the entry session",
            **{dim: {k: stats_fn(v, label=f"{dim}={k}") for k, v in sorted(groups[dim].items())} for dim in groups},
            "counts": {dim: {k: len(v) for k, v in sorted(groups[dim].items())} for dim in groups}}
