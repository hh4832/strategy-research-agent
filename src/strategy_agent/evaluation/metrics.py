"""Performance and statistical-reliability metrics.

Every function documents its assumptions; QUANT_EVALUATION_PROTOCOL.md §5–§7
states where each is (and is not) valid. Inputs are plain sequences / numpy
arrays so the module has no dependency on any backtest engine.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np
from scipy import stats

EULER_GAMMA = 0.5772156649015329


# ------------------------------------------------------------- basic metrics


def sharpe_ratio(returns: Sequence[float], periods_per_year: float | None = None, rf: float = 0.0) -> float:
    """Sample Sharpe (ddof=1). Annualised by sqrt(periods_per_year) if given.

    Assumes i.i.d. returns for the sqrt-time scaling; with autocorrelated returns
    the annualised value is biased (Lo, 2002) -- report the per-period value too.
    """
    r = np.asarray(returns, dtype=float) - rf
    if r.size < 2:
        return float("nan")
    sd = r.std(ddof=1)
    if sd == 0:
        return float("nan")
    sr = r.mean() / sd
    return float(sr * math.sqrt(periods_per_year)) if periods_per_year else float(sr)


def sortino_ratio(returns: Sequence[float], periods_per_year: float | None = None, target: float = 0.0) -> float:
    """Mean excess over target divided by downside deviation.

    Downside deviation = sqrt(mean(min(r - target, 0)^2)) over ALL observations
    (not only negative ones), the standard Sortino & Price definition.
    """
    r = np.asarray(returns, dtype=float) - target
    if r.size < 2:
        return float("nan")
    dd = math.sqrt(np.mean(np.minimum(r, 0.0) ** 2))
    if dd == 0:
        return float("inf") if r.mean() > 0 else float("nan")
    s = r.mean() / dd
    return float(s * math.sqrt(periods_per_year)) if periods_per_year else float(s)


def max_drawdown(equity: Sequence[float]) -> float:
    """Most negative peak-to-trough fraction (e.g. -0.25). Equity must be > 0."""
    e = np.asarray(equity, dtype=float)
    if e.size == 0:
        return float("nan")
    if np.any(e <= 0):
        raise ValueError("equity must be strictly positive for a fractional drawdown")
    peak = np.maximum.accumulate(e)
    return float(np.min(e / peak - 1.0))


def max_drawdown_duration(equity: Sequence[float]) -> int:
    """Longest number of observations spent below a prior peak."""
    e = np.asarray(equity, dtype=float)
    peak, longest, cur = -np.inf, 0, 0
    for x in e:
        if x >= peak:
            peak, cur = x, 0
        else:
            cur += 1
            longest = max(longest, cur)
    return longest


def profit_factor(trade_pnls: Sequence[float]) -> float:
    p = np.asarray(trade_pnls, dtype=float)
    gains, losses = p[p > 0].sum(), -p[p < 0].sum()
    if losses == 0:
        return float("inf") if gains > 0 else float("nan")
    return float(gains / losses)


def turnover(traded_notional: Sequence[float], average_equity: float, years: float) -> float:
    """Annualised one-way turnover = sum(|traded notional|) / 2 / avg equity / years."""
    if average_equity <= 0 or years <= 0:
        raise ValueError("average_equity and years must be positive")
    return float(np.abs(np.asarray(traded_notional, dtype=float)).sum() / 2.0 / average_equity / years)


def exposure(position: Sequence[float]) -> float:
    """Fraction of evaluated bars with a non-zero position."""
    p = np.asarray(position, dtype=float)
    return float(np.mean(p != 0)) if p.size else float("nan")


# -------------------------------------------------- PSR / DSR (Bailey & LdP)


def probabilistic_sharpe_ratio(returns: Sequence[float], sr_benchmark: float = 0.0) -> float:
    """P(true SR > sr_benchmark) given sample skew/kurtosis (per-period units).

    Bailey & López de Prado (2012). Assumes stationary returns; uses Pearson
    (non-excess) kurtosis. Not valid for very small T (< ~30) or for strongly
    autocorrelated returns without adjustment.
    """
    r = np.asarray(returns, dtype=float)
    t = r.size
    sr = sharpe_ratio(r)
    if t < 3 or not np.isfinite(sr):
        return float("nan")
    g3 = stats.skew(r, bias=False)
    g4 = stats.kurtosis(r, fisher=False, bias=False)
    denom = 1.0 - g3 * sr + (g4 - 1.0) / 4.0 * sr ** 2
    if denom <= 0:
        return float("nan")
    z = (sr - sr_benchmark) * math.sqrt(t - 1) / math.sqrt(denom)
    return float(stats.norm.cdf(z))


def expected_max_sharpe(n_trials: int, sr_variance: float) -> float:
    """E[max SR] across n independent trials whose true SR is 0 (per-period units).

    False-strategy theorem approximation (Bailey, Borwein, LdP, Zhu 2014).
    n_trials must be the EFFECTIVE number of independent trials; correlated
    variants inflate n if counted naively (see protocol §6.3).
    """
    if n_trials < 1:
        raise ValueError("n_trials >= 1")
    if n_trials == 1:
        return 0.0
    z1 = stats.norm.ppf(1.0 - 1.0 / n_trials)
    z2 = stats.norm.ppf(1.0 - 1.0 / (n_trials * math.e))
    return float(math.sqrt(sr_variance) * ((1.0 - EULER_GAMMA) * z1 + EULER_GAMMA * z2))


def deflated_sharpe_ratio(returns: Sequence[float], n_trials: int, trial_sr_variance: float) -> float:
    """DSR = PSR evaluated against the expected maximum SR of n null trials."""
    return probabilistic_sharpe_ratio(returns, expected_max_sharpe(n_trials, trial_sr_variance))


def minimum_track_record_length(returns: Sequence[float], sr_benchmark: float = 0.0, alpha: float = 0.05) -> float:
    """Observations needed for PSR(sr_benchmark) >= 1 - alpha at current moments."""
    r = np.asarray(returns, dtype=float)
    sr = sharpe_ratio(r)
    if not np.isfinite(sr) or sr <= sr_benchmark:
        return float("inf")
    g3 = stats.skew(r, bias=False)
    g4 = stats.kurtosis(r, fisher=False, bias=False)
    z = stats.norm.ppf(1 - alpha)
    return float(1 + (1 - g3 * sr + (g4 - 1) / 4 * sr ** 2) * (z / (sr - sr_benchmark)) ** 2)


# ------------------------------------------------------ multiple testing


@dataclass(frozen=True)
class MultipleTestResult:
    adjusted_p: tuple[float, ...]
    reject: tuple[bool, ...]
    method: str


def holm(pvalues: Sequence[float], alpha: float = 0.05) -> MultipleTestResult:
    """Holm step-down; controls FWER under arbitrary dependence."""
    p = np.asarray(pvalues, dtype=float)
    m = p.size
    order = np.argsort(p)
    adj = np.empty(m)
    running = 0.0
    for k, i in enumerate(order):
        running = max(running, min(1.0, (m - k) * p[i]))
        adj[i] = running
    return MultipleTestResult(tuple(adj), tuple(adj <= alpha), "holm")


def benjamini_hochberg(pvalues: Sequence[float], alpha: float = 0.05, dependent: bool = False) -> MultipleTestResult:
    """BH (FDR, independence / PRDS) or BY (dependent=True, arbitrary dependence)."""
    p = np.asarray(pvalues, dtype=float)
    m = p.size
    c = sum(1.0 / i for i in range(1, m + 1)) if dependent else 1.0
    order = np.argsort(p)
    ranked = p[order] * m * c / np.arange(1, m + 1)
    adj_sorted = np.minimum.accumulate(ranked[::-1])[::-1]
    adj = np.empty(m)
    adj[order] = np.minimum(adj_sorted, 1.0)
    return MultipleTestResult(tuple(adj), tuple(adj <= alpha), "by" if dependent else "bh")


# ------------------------------------------------- overfitting diagnostics


def probability_of_backtest_overfitting(perf: np.ndarray, n_blocks: int = 8) -> float:
    """CSCV PBO (Bailey, Borwein, LdP, Zhu 2017).

    perf: T x N matrix of per-period returns for N configurations evaluated on
    the SAME periods. Splits T into n_blocks contiguous blocks (even), forms all
    C(S, S/2) IS/OOS partitions, and returns the fraction where the IS-best
    configuration ranks at or below the OOS median. Assumes blocks are long
    enough for serial dependence to be negligible across blocks.
    """
    perf = np.asarray(perf, dtype=float)
    t, n = perf.shape
    if n_blocks % 2 or n_blocks < 2 or t < n_blocks or n < 2:
        raise ValueError("need even n_blocks <= T and N >= 2")
    blocks = np.array_split(np.arange(t), n_blocks)
    logits = []
    for is_idx in itertools.combinations(range(n_blocks), n_blocks // 2):
        is_rows = np.concatenate([blocks[i] for i in is_idx])
        oos_rows = np.concatenate([blocks[i] for i in range(n_blocks) if i not in is_idx])
        is_sr = perf[is_rows].mean(0) / (perf[is_rows].std(0, ddof=1) + 1e-12)
        oos_sr = perf[oos_rows].mean(0) / (perf[oos_rows].std(0, ddof=1) + 1e-12)
        best = int(np.argmax(is_sr))
        rank = stats.rankdata(oos_sr)[best] / (n + 1)
        logits.append(math.log(rank / (1 - rank)))
    return float(np.mean(np.asarray(logits) <= 0))


def cost_sensitivity(gross_trade_pnls: Sequence[float], trade_costs: Sequence[float],
                     multipliers: Sequence[float]) -> dict:
    """Net PnL at each cost multiplier plus the break-even multiplier."""
    g = np.asarray(gross_trade_pnls, dtype=float)
    c = np.asarray(trade_costs, dtype=float)
    net = {float(m): float(g.sum() - m * c.sum()) for m in multipliers}
    be = float(g.sum() / c.sum()) if c.sum() > 0 else float("inf")
    return {"net_by_multiplier": net, "breakeven_multiplier": be}


def oos_stability(is_scores: Sequence[float], oos_scores: Sequence[float]) -> dict:
    """Fold-level IS vs OOS diagnostics for one configuration across WF folds."""
    i, o = np.asarray(is_scores, float), np.asarray(oos_scores, float)
    return {
        "oos_positive_fraction": float(np.mean(o > 0)),
        "median_oos": float(np.median(o)),
        "is_oos_degradation": float(1 - np.median(o) / np.median(i)) if np.median(i) > 0 else float("nan"),
        "worst_fold_oos": float(o.min()),
    }
