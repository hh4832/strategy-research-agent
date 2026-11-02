"""ATR (Wilder) and volatility breakout — spec ``atr_breakout_tsm_15m``.

Entry threshold uses ATR through t-1 so the breakout bar cannot inflate its
own threshold. The stop is ``signal-bar close - stop_atr * ATR_at_entry``,
evaluated on bar close: the pure layer has no fills, so the reference is the
SIGNAL BAR CLOSE (the spec's ``reference`` field states which one is used;
an ENTRY_FILL_PRICE reference needs engine feedback and belongs to the
formal engine, decision Q4).
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Mapping, Sequence

from .core import Decision, SignalBar, SignalInputError, eligible_stream


def true_range(prev_close: float, high: float, low: float) -> float:
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def wilder_atr(bars: Sequence[SignalBar], n: int,
               exclusions: Sequence[tuple[datetime, datetime]] = ()) -> list[tuple[datetime, float | None]]:
    """(available_at, ATR) per eligible bar; None until n true ranges exist in the segment."""
    if n < 1:
        raise SignalInputError("n >= 1")
    out = []
    prev_c, trs, atr = None, [], None
    for b, reset in eligible_stream(bars, exclusions):
        if reset:
            prev_c, trs, atr = None, [], None
        if prev_c is not None:
            tr = true_range(prev_c, b.high, b.low)
            if atr is None:
                trs.append(tr)
                if len(trs) == n:
                    atr = math.fsum(trs) / n
            else:
                atr = (atr * (n - 1) + tr) / n
        out.append((b.available_at, atr))
        prev_c = b.close
    return out


def atr_breakout(bars: Sequence[SignalBar], sessions: Mapping[str, tuple[datetime, datetime]], *, n: int, k: float,
                 stop_atr: float, max_hold_bars: int, last_entry_offset: timedelta, flatten_offset: timedelta,
                 exclusions: Sequence[tuple[datetime, datetime]] = ()) -> list[Decision]:
    if k <= 0 or stop_atr <= 0 or max_hold_bars < 1:
        raise SignalInputError("k, stop_atr > 0 and max_hold_bars >= 1")
    out: list[Decision] = []
    prev_c, trs, atr_prev = None, [], None      # atr_prev = ATR through the previous eligible bar
    pos, held, stop, session = 0, 0, None, None
    for b, reset in eligible_stream(bars, exclusions):
        s_start, s_end = sessions[b.session_id]
        if b.session_id != session:
            if pos:
                out.append(Decision(b.available_at, 0, b.contract, "SESSION_CHANGE"))
                pos = 0
            session = b.session_id
        if reset:
            prev_c, trs, atr_prev = None, [], None
            if pos and reset in ("CONTRACT_CHANGE", "EXCLUSION"):
                out.append(Decision(b.available_at, 0, b.contract, reset))
                pos = 0
        t = b.available_at - s_start
        if pos:
            held += 1
            if b.close < stop:
                out.append(Decision(b.available_at, 0, b.contract, "STOP_ATR"))
                pos = 0
            elif held >= max_hold_bars:
                out.append(Decision(b.available_at, 0, b.contract, "MAX_HOLD"))
                pos = 0
            elif t >= flatten_offset or b.available_at >= s_end:
                out.append(Decision(b.available_at, 0, b.contract, "TIME_FLATTEN"))
                pos = 0
        elif atr_prev is not None and prev_c is not None and t < last_entry_offset \
                and b.close > prev_c + k * atr_prev:
            out.append(Decision(b.available_at, 1, b.contract, "VOL_BREAKOUT"))
            pos, held, stop = 1, 0, b.close - stop_atr * atr_prev
        # update ATR with this bar AFTER the decision (so the decision used t-1)
        if prev_c is not None:
            tr = true_range(prev_c, b.high, b.low)
            if atr_prev is None:
                trs.append(tr)
                if len(trs) == n:
                    atr_prev = math.fsum(trs) / n
            else:
                atr_prev = (atr_prev * (n - 1) + tr) / n
        prev_c = b.close
    return out
