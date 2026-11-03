"""Opening Range Breakout — spec ``orb_txf_1m``.

The opening range is defined by COMPLETION TIME, not bar labels: the range
bars are the eligible bars with ``session_start < available_at <=
session_start + range_duration``. This makes the function independent of
whether a source labels bars by start or end (decision Q1). The range must
contain exactly ``range_bars`` eligible bars; otherwise the session is skipped
(no partial ranges, no synthesised bars).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Mapping, Sequence

from .core import Decision, SignalBar, SignalInputError, eligible_stream


def orb(bars: Sequence[SignalBar], sessions: Mapping[str, tuple[datetime, datetime]], *, range_bars: int,
        range_duration: timedelta, last_entry_offset: timedelta, flatten_offset: timedelta,
        allow_short: bool = True, exclusions: Sequence[tuple[datetime, datetime]] = ()) -> list[Decision]:
    if range_bars < 1 or not (range_duration < last_entry_offset <= flatten_offset):
        raise SignalInputError("require range_duration < last_entry_offset <= flatten_offset")
    out: list[Decision] = []
    cur_session, rng, hi, lo, entered, pos = None, [], None, None, False, 0
    for b, reset in eligible_stream(bars, exclusions):
        if b.session_id not in sessions:
            raise SignalInputError(f"unknown session {b.session_id}")
        s_start, s_end = sessions[b.session_id]
        if b.session_id != cur_session:
            if pos:                                   # position survived a missing session-end bar
                out.append(Decision(b.available_at, 0, b.contract, "SESSION_CHANGE"))
                pos = 0
            cur_session, rng, hi, lo, entered = b.session_id, [], None, None, False
        if reset in ("CONTRACT_CHANGE", "EXCLUSION") and pos:
            out.append(Decision(b.available_at, 0, b.contract, reset))
            pos = 0
        if reset in ("CONTRACT_CHANGE", "EXCLUSION") and hi is None:
            rng = []                                  # a break inside the range invalidates it
        range_end = s_start + range_duration
        if b.available_at <= range_end:
            rng.append(b)
            if b.available_at == range_end:
                if len(rng) == range_bars:
                    hi, lo = max(x.high for x in rng), min(x.low for x in rng)
                else:
                    entered = True                    # incomplete range: no trade this session
            continue
        if hi is None:
            entered = True                            # range bar at range_end missing
            continue
        t = b.available_at - s_start
        if pos == 0 and not entered and t < last_entry_offset:
            if b.close > hi:
                out.append(Decision(b.available_at, 1, b.contract, "BREAK_UP"))
                pos, entered = 1, True
            elif allow_short and b.close < lo:
                out.append(Decision(b.available_at, -1, b.contract, "BREAK_DOWN"))
                pos, entered = -1, True
            continue
        if pos:
            if t >= flatten_offset or b.available_at >= s_end:
                out.append(Decision(b.available_at, 0, b.contract, "TIME_FLATTEN"))
                pos = 0
            elif (pos == 1 and b.close < lo) or (pos == -1 and b.close > hi):
                out.append(Decision(b.available_at, 0, b.contract, "STOP_RANGE_OPPOSITE"))
                pos = 0
    return out
