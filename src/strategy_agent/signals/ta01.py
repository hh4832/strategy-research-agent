"""P5C-TA-01 — 20-bar resistance breakout + strong-close candle + relative volume (long only).

Engineering implementation of the Phase 5-C proposal ``P5C-TA-01``. It is a pure signal function under the shared
contract in ``signals.core``: decisions are intended target positions at a bar's ``available_at``; fills, order
expiry, rejections and costs are the execution engine's job (the same Core path as the SMA signal).

Rule (one completed ELIGIBLE bar ``t``; ``[k]`` = k eligible bars back; windows EXCLUDE bar ``t``):

  R(t)  = max(high[1..res_n])          resistance            (spec ROLLING_MAX(high, res_n), lag 1)
  V(t)  = mean(volume[1..vol_m])       volume baseline       (spec SMA(volume, vol_m), lag 1)
  S(t)  = min(low[1..sup_l])           exit support          (spec ROLLING_MIN(low, sup_l), lag 1)

  ENTER_LONG(t) = flat, no entry yet this session, all windows full (warm-up), session is not an early close,
                  local decision time < last_entry_local, available_at < session_close, and
                  close > R  AND  close > open  AND  high > low  AND  close - low >= close_pos * (high - low)
                  AND  volume >= vol_k * V

  EXIT (first that holds, while long):  close < S            -> SUPPORT_BREAK
                                        held >= max_hold_bars -> MAX_HOLD   (eligible bars after the entry DECISION bar)
                                        local decision time >= flatten_local (15:15 ET in the spec) -> TIME_FLATTEN
  The time exit is fired by the FIRST eligible bucket whose decision time is at or after flatten_local. There is no
  separate session-end exit rule (the spec must state flatten_before_session_end: false).

NO OVERNIGHT (fail-closed). Every exit decision must be able to fill in the SAME session under the pinned Core rules
(market-research-core 50ea3bf; see ``last_feasible_exit_decision``). Hence:
  * an entry is only permitted on a session whose flatten_local is <= that session's last feasible exit decision;
  * while long, a bucket whose decision time is later than the last feasible exit decision raises SameDayExitError
    (no exit is emitted that could only fill the next day);
  * a position still long when a later session's bucket appears raises SameDayExitError; there is NO next-session
    flatten signal;
  * after the loop, a position still open while the input already extends past the last feasible exit decision (or
    into a later session, even through ineligible buckets only) raises SameDayExitError. A position open at a
    truncated end of input is left to the post-execution ``intraday_check`` (FAIL). CONTRACT_CHANGE / EXCLUSION resets the
  windows and flattens (only if that decision is still same-day feasible). Ineligible bars (missing minute, zero volume, outside RTH, auction buckets) are skipped:
  they never enter a window, never count as held bars, never signal.

What this function cannot know (NEEDS_CONFIRMATION, see docs/PHASE5C_TA01_ENGINEERING.md): whether an order fills,
when it expires, and therefore whether a TIME_FLATTEN actually executed in-session. ``intraday_check`` makes an
unfilled exit visible after execution instead of hiding it.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime, time
from typing import Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from datetime import timedelta

from .core import Decision, SignalBar, SignalInputError, eligible_stream

STRATEGY_PREFIX = "p5c_ta01_"
PARAMETER_KEYS = frozenset({"res_n", "vol_m", "vol_k", "close_pos", "sup_l"})
# canonical family structure of the reviewed P5C-TA-01 proposal spec (family.canonical.structure_hash);
# a spec whose rule STRUCTURE differs is refused instead of being run with these semantics
STRUCTURE_SHA256 = "0c7bde1a867d21956a4f02b426ccdeb1884d1d2cc65040b494b2f8ec69f0246c"
ENTRY, SUPPORT, MAX_HOLD, TIME_FLAT = "TA01_ENTRY", "SUPPORT_BREAK", "MAX_HOLD", "TIME_FLATTEN"
CORE_BUCKET_MINUTES = (1, 15)            # market-research-core 50ea3bf temporal.prepare_conservative_bars


class SameDayExitError(SignalInputError):
    """TA-01 is intraday-only: a position could not be exited by a decision that can still fill the same session."""


def last_feasible_exit_decision(session_close: datetime, bar_minutes: int) -> datetime:
    """Latest decision time whose MARKET order can still fill in the same session under pinned Core 50ea3bf.

    Evidence (market-research-core 50ea3bf):
      temporal.prepare_conservative_bars: buckets group source labels by floor(bar_minutes) (UTC); a bucket with
        labels L..L+m-1 has bar_start = L - 1min and modeled_available_at = L + m; it is execution-eligible only if
        L + m < session_close (else CLOSING_AUCTION_EXCLUDED) and every minute has volume.
      execution.run: candidate fill buckets need bar_start > max(signal available_at, order_time)
        (same_timestamp_open_assumption=False in Agent core_config), the same session_close as the signal bucket,
        and bar_start < DAY expiry (first session close after order_time).
    So the last executable bucket starts at L* = session_close - 2m; a decision d can reach it only if
    d < L* - 1min. Decisions lie on the m-minute grid, so the answer is the largest grid time strictly earlier
    than session_close - 2m - 1min. For 15m buckets and a 16:00 close this is 15:15; for a 13:00 close, 12:15.
    It is a NECESSARY condition only: the single remaining fill bucket may still be ineligible (zero volume,
    participation cap), in which case the DAY order expires and intraday_check FAILs."""
    if bar_minutes not in CORE_BUCKET_MINUTES:
        raise SignalInputError(f"bar_minutes must be one of {CORE_BUCKET_MINUTES}")
    limit = session_close - timedelta(minutes=2 * bar_minutes + 1)
    epoch_min = int(limit.timestamp() // 60)
    grid = epoch_min - (epoch_min % bar_minutes)
    if grid * 60 >= limit.timestamp():
        grid -= bar_minutes
    return datetime.fromtimestamp(grid * 60, tz=session_close.tzinfo)


@dataclass(frozen=True)
class TA01Params:
    res_n: int
    vol_m: int
    vol_k: float
    close_pos: float
    sup_l: int
    max_hold_bars: int
    last_entry_local: time
    flatten_local: time
    regular_close_local: time
    timezone: str
    bar_minutes: int

    def __post_init__(self):
        if min(self.res_n, self.vol_m, self.sup_l, self.max_hold_bars) < 1:
            raise SignalInputError("res_n, vol_m, sup_l and max_hold_bars must be >= 1")
        if not (self.vol_k > 0 and 0 < self.close_pos <= 1):
            raise SignalInputError("vol_k > 0 and 0 < close_pos <= 1 required")
        if not (self.last_entry_local <= self.flatten_local <= self.regular_close_local):
            raise SignalInputError("require last_entry_local <= flatten_local <= regular_close_local")
        ZoneInfo(self.timezone)
        if self.bar_minutes not in CORE_BUCKET_MINUTES:
            raise SignalInputError(f"bar_minutes must be one of {CORE_BUCKET_MINUTES}")


def _hm(text: str) -> time:
    if not isinstance(text, str) or not re.fullmatch(r"\d{2}:\d{2}", text):
        raise SignalInputError(f"HH:MM time required, got {text!r}")
    return time(int(text[:2]), int(text[3:]))


def structure_sha256(spec: Mapping) -> str:
    from strategy_agent.family.canonical import structure_hash  # noqa: PLC0415 - pure canonicaliser
    return structure_hash(dict(spec))


def is_ta01_spec(spec: Mapping) -> bool:
    return str(spec.get("strategy_id", "")).startswith(STRATEGY_PREFIX)


def params_from_spec(spec: Mapping, parameters: Mapping) -> TA01Params:
    """Bind a P5C-TA-01 spec + run parameters to ``TA01Params``. Fail-closed on any structural or value mismatch:
    the function implements exactly one rule structure, so a different spec is never silently re-interpreted."""
    if not is_ta01_spec(spec):
        raise SignalInputError("not a P5C-TA-01 spec")
    got = structure_sha256(spec)
    if got != STRUCTURE_SHA256:
        raise SignalInputError(f"TA01_STRUCTURE_MISMATCH: {got} != reviewed {STRUCTURE_SHA256}")
    if set(parameters) != PARAMETER_KEYS:
        raise SignalInputError(f"TA01_PARAMETERS: expected {sorted(PARAMETER_KEYS)}, got {sorted(parameters)}")
    values = spec["parameters"]["values"]
    if spec["parameters"]["search"]["mode"] != "FIXED":
        raise SignalInputError("TA01_SEARCH_MODE: only FIXED (no parameter search) is supported")
    for k in PARAMETER_KEYS:                      # run parameters must equal the spec's fixed defaults
        if not math.isclose(float(parameters[k]), float(values[k]["default"]), rel_tol=0, abs_tol=1e-12):
            raise SignalInputError(f"TA01_PARAMETER_NOT_SPEC_DEFAULT: {k}")
    entry = spec["signal"]["entry_rules"]
    if len(entry) != 1 or spec["signal"].get("max_entries_per_session") != 1:
        raise SignalInputError("TA01: exactly one entry rule and max_entries_per_session == 1 required")
    cut = [a["time_local"] for a in entry[0]["when"]["args"] if a.get("op") == "time_lt"]
    texit = spec["risk_exits"]["time_exit"]
    if len(cut) != 1 or texit.get("flatten_before_session_end") is not False:
        # the only time exit is the first eligible bucket at/after flatten_decision_time_local; a separate
        # session-end exit rule is not part of TA-01 and must be explicitly disabled in the spec
        raise SignalInputError("TA01: one time_lt entry cut-off and flatten_before_session_end=false required")
    sessions = spec["universe"]["sessions"]
    m = re.search(r"(\d{2}:\d{2})-(\d{2}:\d{2})", str(sessions.get("calendar_ref", "")))
    if sessions.get("allowed") != ["RTH"] or not m or sessions.get("no_overnight") is not True:
        raise SignalInputError("TA01: RTH-only, no_overnight and an explicit HH:MM-HH:MM calendar_ref required")
    if spec["risk_constraints"]["allowed_directions"] != ["LONG"]:
        raise SignalInputError("TA01 is long-only")
    tf = spec["universe"].get("timeframe")
    if tf != "15m":
        raise SignalInputError(f"TA01: universe.timeframe must be 15m, got {tf!r}")
    ints = {k: int(parameters[k]) for k in ("res_n", "vol_m", "sup_l")}
    if any(float(parameters[k]) != ints[k] for k in ints):
        raise SignalInputError("TA01: window lengths must be integers")
    return TA01Params(**ints, vol_k=float(parameters["vol_k"]), close_pos=float(parameters["close_pos"]),
                      max_hold_bars=int(texit["max_holding_bars"]), last_entry_local=_hm(cut[0]),
                      flatten_local=_hm(texit["flatten_decision_time_local"]), regular_close_local=_hm(m.group(2)),
                      timezone=spec["universe"]["timezone"], bar_minutes=15)


def sessions_from_rows(rows: Iterable[Mapping]) -> dict[str, tuple[datetime, datetime]]:
    """session_id -> (session_open, session_close) from Core bar rows. Inconsistent rows fail closed."""
    out: dict[str, tuple[datetime, datetime]] = {}
    for r in rows:
        sid, so, sc = r["session"], r["session_open"], r["session_close"]
        if not isinstance(so, datetime) or not isinstance(sc, datetime) or so.tzinfo is None or sc.tzinfo is None:
            raise SignalInputError(f"session {sid}: aware session_open/session_close required")
        if sid in out and out[sid] != (so, sc):
            raise SignalInputError(f"session {sid}: inconsistent session_open/session_close")
        out[sid] = (so, sc)
    return out


def _session(sessions, b):
    try:
        return sessions[b.session_id]
    except KeyError:
        raise SignalInputError(f"unknown session {b.session_id}") from None


def _early_close(s_end: datetime, p: TA01Params) -> bool:
    return s_end.astimezone(ZoneInfo(p.timezone)).time() < p.regular_close_local


def pattern(b: SignalBar, resistance: float, vol_avg: float, p: TA01Params) -> dict[str, bool]:
    """The three TA-01 components, individually (used by tests and diagnostics)."""
    rng = b.high - b.low
    return {"breakout": b.close > resistance,
            "bullish": b.close > b.open,
            "strong_close": rng > 0 and (b.close - b.low) >= p.close_pos * rng,
            "relative_volume": b.volume >= p.vol_k * vol_avg}


def _run(bars: Sequence[SignalBar], sessions: Mapping[str, tuple[datetime, datetime]], p: TA01Params, *,
         exclusions=(), entry_at: frozenset | None = None, collect_candidates: bool = False):
    tz = ZoneInfo(p.timezone)
    out: list[Decision] = []
    candidates: dict[str, list[datetime]] = {}
    highs, vols, lows = deque(maxlen=p.res_n), deque(maxlen=p.vol_m), deque(maxlen=p.sup_l)
    pos, held, entered, session = 0, 0, False, None
    for b, reset in eligible_stream(bars, exclusions):
        s_start, s_end = _session(sessions, b)
        last_exit = last_feasible_exit_decision(s_end, p.bar_minutes)
        flattened = False
        if b.session_id != session:
            if pos:                                    # never flatten yesterday's position with today's bar
                raise SameDayExitError(f"NO_SAME_DAY_EXIT: position from session {session} still long at "
                                       f"{b.available_at.isoformat()} (session {b.session_id})")
            session, entered = b.session_id, False
        if pos and b.available_at > last_exit:        # an exit decided now could only fill the next session
            raise SameDayExitError(f"NO_SAME_DAY_EXIT: still long at {b.available_at.isoformat()} after the last "
                                   f"feasible exit decision {last_exit.isoformat()} (session {session})")
        if reset:
            highs.clear(), vols.clear(), lows.clear()
            if pos:
                out.append(Decision(b.available_at, 0, b.contract, reset))
                pos, flattened = 0, True
        local = b.available_at.astimezone(tz).time()
        if pos:
            held += 1
            if len(lows) == p.sup_l and b.close < min(lows):
                out.append(Decision(b.available_at, 0, b.contract, SUPPORT))
                pos = 0
            elif held >= p.max_hold_bars:
                out.append(Decision(b.available_at, 0, b.contract, MAX_HOLD))
                pos = 0
            elif local >= p.flatten_local:                 # first eligible bucket with decision time >= 15:15
                out.append(Decision(b.available_at, 0, b.contract, TIME_FLAT))
                pos = 0
        else:
            warm = len(highs) == p.res_n and len(vols) == p.vol_m and len(lows) == p.sup_l
            flatten_at = datetime.combine(b.available_at.astimezone(tz).date(), p.flatten_local, tz)
            allowed = (warm and not entered and not flattened and not _early_close(s_end, p)
                       and local < p.last_entry_local and s_start <= b.available_at < s_end
                       and flatten_at <= last_exit)           # a same-day time exit must exist before entering
            if allowed and collect_candidates:
                candidates.setdefault(b.session_id, []).append(b.available_at)
            if allowed:
                fire = (b.available_at in entry_at) if entry_at is not None else \
                    all(pattern(b, max(highs), math.fsum(vols) / len(vols), p).values())
                if fire:
                    out.append(Decision(b.available_at, 1, b.contract, ENTRY if entry_at is None else "RANDOM_ENTRY"))
                    pos, held, entered = 1, 0, True
        highs.append(b.high), vols.append(b.volume), lows.append(b.low)   # windows updated AFTER the decision
    if pos:                     # input (eligible or not) already passed the last feasible exit -> FAIL, never wait
        last_exit = last_feasible_exit_decision(sessions[session][1], p.bar_minutes)
        later = [x for x in bars if x.available_at > out[-1].decision_ts]
        if any(x.session_id != session or x.available_at > last_exit for x in later):
            raise SameDayExitError(f"NO_SAME_DAY_EXIT: session {session} has no exit decision by {last_exit.isoformat()}")
    return out, candidates


def ta01_decisions(bars: Sequence[SignalBar], sessions: Mapping[str, tuple[datetime, datetime]], params: TA01Params,
                   exclusions: Sequence[tuple[datetime, datetime]] = ()) -> list[Decision]:
    return _run(bars, sessions, params, exclusions=exclusions)[0]


def forced_entry_decisions(bars, sessions, params: TA01Params, entry_at: Iterable[datetime], exclusions=()):
    """Same state machine and exits as ``ta01_decisions`` but entries only at ``entry_at`` (random-entry null).
    An ``entry_at`` time that is not a permitted entry point under the strategy constraints is never entered."""
    return _run(bars, sessions, params, exclusions=exclusions, entry_at=frozenset(entry_at))[0]


def entry_candidates(bars, sessions, params: TA01Params, exclusions=()) -> dict[str, list[datetime]]:
    """Per session, every bar time at which the strategy's NON-pattern entry constraints hold (warm-up, time window,
    no early close, in session, flat, not yet entered). Computed along the strategy's own state path."""
    return _run(bars, sessions, params, exclusions=exclusions, collect_candidates=True)[1]


def intraday_check(round_trips: Iterable[Mapping], open_position, timezone: str) -> dict:
    """After execution: TA-01 is intraday by design. Any round trip that exits on a later local date than it entered,
    or a position open at the end, means an exit did not fill in-session. FAIL makes it visible; nothing is fixed."""
    tz, bad = ZoneInfo(timezone), []
    for t in round_trips:
        try:
            a = t["entry_ts"] if isinstance(t["entry_ts"], datetime) else datetime.fromisoformat(str(t["entry_ts"]))
            z = t["exit_ts"] if isinstance(t["exit_ts"], datetime) else datetime.fromisoformat(str(t["exit_ts"]))
        except (KeyError, TypeError, ValueError):
            bad.append({"code": "ROUND_TRIP_UNREADABLE", "trip": str(t)[:200]})
            continue
        if a.astimezone(tz).date() != z.astimezone(tz).date():
            bad.append({"code": "EXIT_NOT_FILLED_IN_SESSION", "entry_ts": a.isoformat(), "exit_ts": z.isoformat()})
    has_open = bool(open_position) if not hasattr(open_position, "__len__") else len(open_position) > 0
    if has_open:
        bad.append({"code": "OPEN_POSITION_AT_END"})
    return {"status": "FAIL" if bad else "PASS", "violations": bad}


def params_digest(p: TA01Params) -> str:
    d = {k: (v.isoformat() if isinstance(v, time) else v) for k, v in p.__dict__.items()}
    return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()
