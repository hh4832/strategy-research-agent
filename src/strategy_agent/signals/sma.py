"""SMA crossover (long only) — spec ``sma_crossover_tw_2330_5m``."""
from __future__ import annotations

import math
from collections import deque
from datetime import datetime
from typing import Sequence

from .core import Decision, SignalBar, SignalInputError, eligible_stream


def sma_crossover(bars: Sequence[SignalBar], fast: int, slow: int,
                  exclusions: Sequence[tuple[datetime, datetime]] = ()) -> list[Decision]:
    """Enter long on fast-SMA crossing above slow-SMA; exit on crossing below.

    A cross needs two consecutive computed states in the same segment, so a
    reset never fabricates a cross. On a contract change an open position is
    flattened (no position across contract change).
    """
    if not (0 < fast < slow):
        raise SignalInputError("require 0 < fast < slow")
    out: list[Decision] = []
    win: deque[float] = deque(maxlen=slow)
    prev_state: int | None = None        # sign(fast - slow) at previous eligible bar
    pos = 0
    for b, reset in eligible_stream(bars, exclusions):
        if reset:
            win.clear()
            prev_state = None
            if reset == "CONTRACT_CHANGE" and pos:
                out.append(Decision(b.available_at, 0, b.contract, "CONTRACT_CHANGE"))
                pos = 0
        win.append(b.close)
        if len(win) < slow:
            continue
        w = list(win)
        f, s = math.fsum(w[-fast:]) / fast, math.fsum(w) / slow
        state = (f > s) - (f < s)
        if prev_state is not None:
            if pos == 0 and prev_state <= 0 and state > 0:
                out.append(Decision(b.available_at, 1, b.contract, "CROSS_ABOVE"))
                pos = 1
            elif pos == 1 and prev_state >= 0 and state < 0:
                out.append(Decision(b.available_at, 0, b.contract, "CROSS_BELOW"))
                pos = 0
        prev_state = state
    return out
