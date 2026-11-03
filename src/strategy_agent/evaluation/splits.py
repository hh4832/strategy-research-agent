"""Chronological splits, walk-forward folds, purging and embargo.

Samples are indexed 0..n-1 in time order. Each sample i has a label interval
[t0[i], t1[i]] (decision time -> time the outcome is known, e.g. exit fill).
Purging removes training samples whose label interval overlaps the test
window; embargo additionally drops training samples that start within
`embargo` of the end of a test window (serial-correlation guard, only relevant
when training data can follow test data, e.g. purged k-fold / CPCV).
See QUANT_EVALUATION_PROTOCOL.md §3–§4.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np


@dataclass(frozen=True)
class Fold:
    train: np.ndarray
    test: np.ndarray
    purged: np.ndarray
    embargoed: np.ndarray


def chronological_split(n: int, fractions: Sequence[float] = (0.6, 0.2, 0.2), gap: int = 0) -> list[np.ndarray]:
    """Contiguous train/validation/test(holdout) blocks with `gap` samples dropped between them."""
    if abs(sum(fractions) - 1.0) > 1e-9:
        raise ValueError("fractions must sum to 1")
    edges = np.floor(np.cumsum([0, *fractions]) * n).astype(int)
    out = []
    for k in range(len(fractions)):
        lo = edges[k] + (gap if k > 0 else 0)
        out.append(np.arange(lo, edges[k + 1]))
    return out


def _overlaps(t0: np.ndarray, t1: np.ndarray, lo: float, hi: float) -> np.ndarray:
    return (t0 <= hi) & (t1 >= lo)


def purge_and_embargo(train: np.ndarray, test: np.ndarray, t0: Sequence[float], t1: Sequence[float],
                      embargo: float = 0.0) -> Fold:
    t0, t1 = np.asarray(t0, float), np.asarray(t1, float)
    if np.any(t1 < t0):
        raise ValueError("label end before label start")
    lo, hi = t0[test].min(), t1[test].max()
    overlap = _overlaps(t0[train], t1[train], lo, hi)
    emb = (t0[train] > hi) & (t0[train] <= hi + embargo)
    keep = ~overlap & ~emb
    return Fold(train[keep], test, train[overlap], train[emb & ~overlap])


def walk_forward(n: int, t0: Sequence[float], t1: Sequence[float], train_size: int, test_size: int,
                 step: int | None = None, anchored: bool = False, embargo: float = 0.0,
                 start: int = 0) -> list[Fold]:
    """Rolling (or anchored/expanding) walk-forward; training always precedes test.

    Training samples whose labels resolve inside the test window are purged.
    """
    step = step or test_size
    folds = []
    test_start = start + train_size
    while test_start + test_size <= n:
        tr_lo = start if anchored else test_start - train_size
        train = np.arange(tr_lo, test_start)
        test = np.arange(test_start, test_start + test_size)
        folds.append(purge_and_embargo(train, test, t0, t1, embargo))
        test_start += step
    return folds


def purged_kfold(n: int, t0: Sequence[float], t1: Sequence[float], k: int = 5, embargo: float = 0.0) -> list[Fold]:
    """Purged k-fold with embargo (López de Prado 2018, ch.7) -- for model selection
    INSIDE a walk-forward training window only, never across the holdout."""
    idx = np.arange(n)
    return [purge_and_embargo(np.setdiff1d(idx, test), test, t0, t1, embargo)
            for test in np.array_split(idx, k)]


def assert_no_leakage(fold: Fold, t0: Sequence[float], t1: Sequence[float]) -> None:
    t0, t1 = np.asarray(t0, float), np.asarray(t1, float)
    if np.intersect1d(fold.train, fold.test).size:
        raise AssertionError("train/test index overlap")
    lo, hi = t0[fold.test].min(), t1[fold.test].max()
    if np.any(_overlaps(t0[fold.train], t1[fold.train], lo, hi)):
        raise AssertionError("training label overlaps test window")
