"""RANDOM-ENTRY-NULL-v1: reproducible random-entry comparison baseline for P5C-TA-01 (engineering component).

Question it answers
    Does conditioning the entry on the TA-01 pattern (breakout + strong-close candle + relative volume) give a higher
    mean net PnL per closed trade than entering at a randomly chosen PERMITTED entry point, when exits, position size,
    order/fill path and cost calculation are identical?

How (all parameters in an explicit ``RandomEntryConfig``; nothing is a hidden constant)
    * Population (``ALL_TRADEABLE_SESSIONS``): sessions that have at least one permitted entry point. A permitted
      entry point is an eligible bar where every NON-pattern TA-01 entry constraint holds (warm-up complete, decision
      time before the entry cut-off, not an early-close session, inside the session, flat, not yet entered) — taken
      from ``signals.ta01.entry_candidates``, i.e. the strategy's own constraint code.
    * Sampling unit: one session; per draw, ``n_entries`` distinct sessions are drawn WITHOUT replacement and one
      permitted entry point is drawn uniformly inside each. ``n_entries`` must equal the strategy's entry count.
    * Seed: draw ``i`` uses ``random.Random(f"{seed}:{i}")`` (string seeding is deterministic across runs/platforms).
    * Exits: ``signals.ta01.forced_entry_decisions`` — the SAME state machine, exits and session handling.
    * Execution and costs: the caller passes ONE ``execute(decisions) -> trades`` callable that is used for the
      strategy and for every draw, so fills, expiry, quantity and costs are identical by construction.
    * Statistic: mean ``net_pnl`` per closed trade; reported against the null distribution's ``quantile``.

What it cannot answer
    Statistical significance beyond the empirical rank; whether TA-01 beats buy-and-hold or a market factor; why an
    effect exists; regime dependence; anything on data other than what the caller supplies. The result is never a
    trading permission, and this component must not be run on real market data without a research authorization.

Known sampling biases
    * Day selection: the strategy enters on specific days; the null spreads entries uniformly over tradeable days, so
      a day-level effect (news days) is attributed to the pattern.
    * Within-day timing: uniform over permitted bars; intraday volume/volatility seasonality is not matched.
    * Exit interaction: a random entry may sit below the exit support and exit on the next bar.
    * Small samples: with few strategy trades the null distribution is coarse; draws are not independent of each
      other's session overlap (sampling without replacement is only within a draw).
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from strategy_agent.signals import ta01 as T

VERSION = "RANDOM-ENTRY-NULL-v1"
POPULATIONS = ("ALL_TRADEABLE_SESSIONS",)


class RandomEntryError(ValueError):
    pass


@dataclass(frozen=True)
class RandomEntryConfig:
    seed: int
    draws: int
    quantile: float
    population: str

    def __post_init__(self):
        if not isinstance(self.seed, int) or isinstance(self.seed, bool):
            raise RandomEntryError("seed must be an explicit integer")
        if not isinstance(self.draws, int) or self.draws < 1:
            raise RandomEntryError("draws must be an integer >= 1")
        if not (isinstance(self.quantile, float) and 0 < self.quantile < 1):
            raise RandomEntryError("quantile must be a float in (0, 1)")
        if self.population not in POPULATIONS:
            raise RandomEntryError(f"population must be one of {POPULATIONS}")

    @classmethod
    def from_mapping(cls, m: Mapping) -> "RandomEntryConfig":
        missing = {"seed", "draws", "quantile", "population"} - set(m)
        if missing:
            raise RandomEntryError(f"config missing {sorted(missing)} (no defaults are supplied)")
        return cls(seed=m["seed"], draws=m["draws"], quantile=m["quantile"], population=m["population"])


def draw_entry_sets(candidates: Mapping[str, Sequence], n_entries: int, config: RandomEntryConfig) -> list[list]:
    pool = {s: sorted(ts) for s, ts in candidates.items() if ts}
    if not pool:
        raise RandomEntryError("NO_PERMITTED_ENTRY_POINTS: no session has a permitted entry point")
    if not isinstance(n_entries, int) or n_entries < 1:
        raise RandomEntryError("n_entries must be >= 1 (a strategy with no trades has nothing to compare)")
    if n_entries > len(pool):
        raise RandomEntryError(f"INSUFFICIENT_SESSIONS: {n_entries} entries > {len(pool)} tradeable sessions")
    sessions = sorted(pool)
    out = []
    for i in range(config.draws):
        rng = random.Random(f"{config.seed}:{i}")
        chosen = sorted(rng.sample(sessions, n_entries))
        out.append([rng.choice(pool[s]) for s in chosen])
    return out


def null_decision_sets(bars, sessions, params, *, n_entries: int, config: RandomEntryConfig, exclusions=()):
    """Decision lists, one per draw. Every draw must realise exactly ``n_entries`` entries, else fail closed."""
    cands = T.entry_candidates(bars, sessions, params, exclusions)
    sets = []
    for i, entry_at in enumerate(draw_entry_sets(cands, n_entries, config)):
        decs = T.forced_entry_decisions(bars, sessions, params, entry_at, exclusions)
        realised = sum(d.target == 1 for d in decs)
        if realised != n_entries:
            raise RandomEntryError(f"ENTRY_NOT_REALISED: draw {i} realised {realised}/{n_entries} entries")
        sets.append(decs)
    return sets


def _mean_net(trades: Sequence[Mapping], who: str) -> float:
    if not trades:
        raise RandomEntryError(f"NO_CLOSED_TRADES: {who}")
    try:
        return math.fsum(float(t["net_pnl"]) for t in trades) / len(trades)
    except (KeyError, TypeError, ValueError) as e:
        raise RandomEntryError(f"TRADE_WITHOUT_NET_PNL: {who}") from e


def _quantile(xs: Sequence[float], q: float) -> float:
    """Linear interpolation between closest ranks (Hyndman-Fan type 7)."""
    s = sorted(xs)
    h = (len(s) - 1) * q
    lo = math.floor(h)
    return s[lo] if lo + 1 >= len(s) else s[lo] + (h - lo) * (s[lo + 1] - s[lo])


def compare(strategy_trades: Sequence[Mapping], null_trades: Sequence[Sequence[Mapping]],
            config: RandomEntryConfig) -> dict:
    if len(null_trades) != config.draws:
        raise RandomEntryError("one trade list per configured draw is required")
    s = _mean_net(strategy_trades, "strategy")
    nulls = [_mean_net(t, f"draw {i}") for i, t in enumerate(null_trades)]
    thr = _quantile(nulls, config.quantile)
    return {"version": VERSION, "config": config.__dict__, "statistic": "mean net_pnl per closed trade",
            "strategy_mean": s, "strategy_trades": len(strategy_trades), "null_means": nulls,
            "null_quantile_value": thr, "share_of_null_below_strategy": sum(x < s for x in nulls) / len(nulls),
            "strategy_above_quantile": s > thr,
            "not_answered": ["statistical significance beyond empirical rank", "market/beta comparison",
                             "regime dependence", "trading permission"]}


def run(bars, sessions, params, *, strategy_decisions, execute: Callable[[list], list], config: RandomEntryConfig,
        exclusions=()) -> dict:
    """Strategy and every draw go through the SAME ``execute`` callable (same fills, quantity, costs)."""
    strategy_trades = execute(list(strategy_decisions))
    n = sum(d.target == 1 for d in strategy_decisions)
    sets = null_decision_sets(bars, sessions, params, n_entries=n, config=config, exclusions=exclusions)
    return compare(strategy_trades, [execute(d) for d in sets], config)
