"""Shared types and invariants for pure signal functions.

Contract
--------
* A signal function maps a sequence of ``SignalBar`` to a list of
  ``Decision``. It has no I/O, no clock, no randomness and no global state.
* Decision time is the bar's ``available_at``. It is an explicit INPUT; it is
  never derived from ``ts`` (decision Q1: start/end label semantics of
  TXF/TW timestamps are confirmed by Codex from source evidence, not assumed
  here). Functions use ``ts`` only for ordering.
* Prefix invariance: for any cut-off ``c``, the decisions with
  ``decision_ts <= c`` computed from the full series equal the decisions
  computed from only the bars with ``available_at <= c``.
* Indicator state resets at every segment break: a contract change, or an
  exclusion window between two consecutive eligible bars. Ineligible bars
  (zero volume, outside session, quality-masked) are skipped exactly like
  missing bars: never synthesised, never used, no reset (convention v1 §1.2-1.3).
* The output is an intended target position. Fills, costs and execution are
  the formal engine's job (decision Q4).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterator, Sequence


class SignalInputError(ValueError):
    pass


@dataclass(frozen=True)
class SignalBar:
    ts: datetime
    available_at: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    contract: str
    session_id: str
    eligible: bool          # data_exists & research/backtest eligible & in session & not excluded & volume > 0


@dataclass(frozen=True)
class Decision:
    decision_ts: datetime
    target: int             # -1, 0, +1
    contract: str
    reason: str


def validate_bars(bars: Sequence[SignalBar]) -> None:
    prev = None
    for i, b in enumerate(bars):
        if b.available_at is None:
            raise SignalInputError(f"bar {i}: available_at is required (never inferred from ts)")
        if b.ts.tzinfo is None or b.available_at.tzinfo is None:
            raise SignalInputError(f"bar {i}: timestamps must be timezone-aware")
        if b.available_at < b.ts:
            raise SignalInputError(f"bar {i}: available_at before ts")
        if not (b.low <= min(b.open, b.close) and b.high >= max(b.open, b.close)):
            raise SignalInputError(f"bar {i}: inconsistent OHLC")
        if prev is not None:
            if b.ts <= prev.ts:
                raise SignalInputError(f"bar {i}: ts not strictly increasing")
            if b.available_at < prev.available_at:
                raise SignalInputError(f"bar {i}: available_at decreasing")
        prev = b


def breaks_between(prev: SignalBar | None, cur: SignalBar,
                   exclusions: Sequence[tuple[datetime, datetime]]) -> str | None:
    """Reason the indicator history must reset before ``cur``, or None."""
    if prev is None:
        return None
    if cur.contract != prev.contract:
        return "CONTRACT_CHANGE"
    for s, e in exclusions:
        if s < cur.available_at and e > prev.ts:     # window touches the gap or either bar
            return "EXCLUSION"
    return None


def eligible_stream(bars: Sequence[SignalBar], exclusions: Sequence[tuple[datetime, datetime]] = ()
                    ) -> Iterator[tuple[SignalBar, str | None]]:
    """Yield eligible bars with the reset reason that precedes each one."""
    validate_bars(bars)
    prev_elig: SignalBar | None = None
    for b in bars:
        if not b.eligible:
            continue
        yield b, breaks_between(prev_elig, b, exclusions)
        prev_elig = b


def cut(bars: Sequence[SignalBar], cutoff: datetime) -> list[SignalBar]:
    return [b for b in bars if b.available_at <= cutoff]
