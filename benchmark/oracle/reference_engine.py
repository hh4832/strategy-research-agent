"""Independent reference engine for the Phase-3 benchmark oracle.

Implements *Oracle Execution Convention v1* (see BENCHMARK_TEST_CASES.md §1).

Design constraints
------------------
* Standard library only (``decimal``, ``datetime``, ``json``). No pandas / numpy,
  and no import from ``market_research_core`` -- the oracle must stay independent
  of the engine it is meant to audit.
* Exact ``Decimal`` arithmetic, no rounding. Hand calculations in the case files
  are therefore reproducible to the last digit.
* Deliberately simple and slow; clarity over performance.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

D = Decimal
CONVENTION_VERSION = "oracle-execution-convention-v1"


# --------------------------------------------------------------------------- data


@dataclass
class Bar:
    ts: datetime          # bar start label (local time of the case timezone)
    contract: str
    o: Decimal
    h: Decimal
    l: Decimal
    c: Decimal
    v: int
    limit_up_locked: bool = False
    limit_down_locked: bool = False

    in_session: bool = True
    excluded: bool = False

    def end(self, tf: timedelta) -> datetime:
        return self.ts + tf


@dataclass
class Order:
    order_ts: datetime
    side: str            # BUY / SELL
    qty: int
    contract: str
    expires_at: datetime
    reason: str          # SIGNAL / ROLL_CLOSE / ROLL_OPEN
    filled: bool = False


@dataclass
class Fill:
    order_ts: datetime
    exec_ts: datetime
    side: str
    qty: int
    contract: str
    ref_open: Decimal
    fill_price: Decimal
    fee: Decimal
    tax: Decimal
    reason: str


@dataclass
class Result:
    signals: list[dict] = field(default_factory=list)
    suppressed_signals: list[dict] = field(default_factory=list)
    orders: list[dict] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    rejections: list[dict] = field(default_factory=list)
    round_trips: list[dict] = field(default_factory=list)
    invalidated_round_trips: list[dict] = field(default_factory=list)
    realized_pnl: Decimal = D(0)
    open_position: dict | None = None


# --------------------------------------------------------------------- helpers


def _dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M")


def _fmt(t: datetime | None) -> str | None:
    return None if t is None else t.strftime("%Y-%m-%d %H:%M")


def _num(x: Any) -> Decimal:
    return D(str(x))


def load_case(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _validate_bar(b: Bar) -> None:
    if not (b.l <= min(b.o, b.c) and b.h >= max(b.o, b.c) and b.l <= b.h):
        raise ValueError(f"inconsistent OHLC at {b.ts} {b.contract}")
    if b.v < 0:
        raise ValueError(f"negative volume at {b.ts}")


# ---------------------------------------------------------------------- engine


class ReferenceEngine:
    def __init__(self, case: dict):
        self.case = case
        self.tf = timedelta(minutes=int(case["timeframe_minutes"]))
        inst = case["instrument"]
        self.mult = _num(inst["multiplier"])
        self.tick = _num(inst["tick_size"])
        costs = case["costs"]
        self.fee_per_unit = _num(costs.get("fee_per_unit_side", 0))
        self.fee_rate = _num(costs.get("fee_rate_side", 0))
        self.tax_rate_side = _num(costs.get("tax_rate_side", 0))
        self.sell_tax_rate = _num(costs.get("sell_tax_rate", 0))
        self.slip_ticks = _num(costs.get("slippage_ticks", 0))
        self.qty = int(case.get("quantity", 1))
        self.sessions = [(_dt(f"{s['date']} {s['start']}"), _dt(f"{s['date']} {s['end']}"))
                         for s in case["sessions"]]
        self.exclusions = [(_dt(w["start"]), _dt(w["end"])) for w in case.get("exclusions", [])]
        cols = case["bars_columns"]
        self.bars: list[Bar] = []
        for row in case["bars"]:
            r = dict(zip(cols, row))
            b = Bar(_dt(r["ts"]), r.get("contract", inst["symbol"]), _num(r["o"]), _num(r["h"]),
                    _num(r["l"]), _num(r["c"]), int(r["v"]), bool(r.get("limit_up_locked", False)),
                    bool(r.get("limit_down_locked", False)))
            _validate_bar(b)
            b.in_session = any(s <= b.ts and b.ts + self.tf <= e for s, e in self.sessions)
            b.excluded = any(s <= b.ts < e for s, e in self.exclusions)
            self.bars.append(b)
        self.bars.sort(key=lambda b: (b.ts, b.contract))
        keys = [(b.ts, b.contract) for b in self.bars]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate (ts, contract) bars")
        self.roll = [(_dt(r["at"]), r["from_contract"], r["to_contract"]) for r in case.get("roll_schedule", [])]
        self.contract = case.get("initial_contract", inst["symbol"])

    # -------------------------------------------------------------- semantics

    def signal_eligible(self, b: Bar) -> bool:
        """Bars that may feed indicators / decisions (convention §1.3)."""
        return b.in_session and b.v > 0 and not b.excluded

    def order_expiry(self, order_ts: datetime) -> datetime:
        """Order lives until the end of the first session ending after order_ts (§1.5)."""
        for s, e in sorted(self.sessions):
            if e > order_ts:
                return e
        return order_ts  # no further session -> expires immediately

    def fill_price(self, side: str, ref_open: Decimal) -> Decimal:
        slip = self.slip_ticks * self.tick
        return ref_open + slip if side == "BUY" else ref_open - slip

    def costs(self, side: str, price: Decimal, qty: int) -> tuple[Decimal, Decimal]:
        notional = price * qty * self.mult
        fee = self.fee_per_unit * qty + self.fee_rate * notional
        tax = self.tax_rate_side * notional + (self.sell_tax_rate * notional if side == "SELL" else 0)
        return fee, tax

    # ------------------------------------------------------------- signals

    def decisions(self) -> list[tuple[datetime, int, str]]:
        """Return (decision_ts, target, contract) in chronological order."""
        model = self.case["signal_model"]
        kind = model["type"]
        elig = [b for b in self.bars if self.signal_eligible(b)]
        out: list[tuple[datetime, int, str]] = []
        res = self._result

        if kind == "scripted":
            by_key = {(b.ts, b.contract): b for b in self.bars}
            for t in model["targets"]:
                bar_ts = _dt(t["bar_close_of"])
                contract = t.get("contract", self.case["instrument"]["symbol"])
                b = by_key.get((bar_ts, contract))
                if b is None or not self.signal_eligible(b):
                    why = ("BAR_MISSING" if b is None else
                           "SIGNAL_BAR_EXCLUDED" if b.excluded else
                           "SIGNAL_BAR_OUTSIDE_SESSION" if not b.in_session else "SIGNAL_BAR_ZERO_VOLUME")
                    res.suppressed_signals.append({"bar_ts": t["bar_close_of"], "target": t["target"], "reason": why})
                    continue
                out.append((b.end(self.tf), int(t["target"]), contract))

        elif kind == "sma_cross":
            fast, slow = int(model["fast"]), int(model["slow"])
            closes: list[Decimal] = []
            prev = 0
            for b in elig:
                closes.append(b.c)
                if len(closes) < slow:
                    continue
                f = sum(closes[-fast:]) / fast
                s = sum(closes[-slow:]) / slow
                tgt = 1 if f > s else 0
                if tgt != prev:
                    out.append((b.end(self.tf), tgt, b.contract))
                    prev = tgt

        elif kind == "orb":
            n = int(model["range_bars"])
            exit_decision = model["flatten_decision_time"]  # HH:MM local, a bar-end time
            for s_start, s_end in self.sessions:
                day = [b for b in elig if s_start <= b.ts < s_end]
                or_window_end = s_start + n * self.tf
                rng = [b for b in day if b.ts < or_window_end]
                if len(rng) != n:
                    res.suppressed_signals.append({"bar_ts": _fmt(s_start), "target": 1,
                                                   "reason": "OPENING_RANGE_INCOMPLETE"})
                    continue
                hi = max(b.h for b in rng)
                flat_at = _dt(f"{s_start:%Y-%m-%d} {exit_decision}")
                entered = False
                for b in day:
                    if b.ts < or_window_end or b.end(self.tf) >= flat_at:
                        continue
                    if not entered and b.c > hi:
                        out.append((b.end(self.tf), 1, b.contract))
                        entered = True
                if entered:
                    out.append((flat_at, 0, self.contract))

        elif kind == "pit_flag":
            events = sorted(({"available_at": _dt(e["available_at"]), "value": bool(e["value"])}
                             for e in model["events"]), key=lambda e: e["available_at"])
            prev = 0
            for b in elig:
                dts = b.end(self.tf)
                known = [e for e in events if e["available_at"] <= dts]
                tgt = 1 if (known and known[-1]["value"]) else 0
                if tgt != prev:
                    out.append((dts, tgt, b.contract))
                    prev = tgt
        else:
            raise ValueError(f"unknown signal model {kind}")
        return sorted(out, key=lambda x: x[0])

    # ------------------------------------------------------------------ run

    def run(self) -> Result:
        self._result = res = Result()
        decisions = self.decisions()
        for dts, tgt, _ in decisions:
            res.signals.append({"signal_ts": _fmt(dts), "target": tgt})

        position: dict[str, int] = {}
        target = 0
        pending: list[Order] = []
        di = 0
        roll_i = 0
        roll = sorted(self.roll)

        # chronological event loop over distinct bar-start timestamps
        times = sorted({b.ts for b in self.bars} | {d[0] for d in decisions} | {r[0] for r in roll})
        bars_at: dict[datetime, list[Bar]] = {}
        for b in self.bars:
            bars_at.setdefault(b.ts, []).append(b)

        def new_order(ts: datetime, side: str, qty: int, contract: str, reason: str) -> None:
            o = Order(ts, side, qty, contract, self.order_expiry(ts), reason)
            pending.append(o)
            res.orders.append({"order_ts": _fmt(ts), "side": side, "qty": qty, "contract": contract,
                               "reason": reason, "expires_at": _fmt(o.expires_at)})

        for t in times:
            # (1) scheduled roll at this timestamp (decision known in advance from spec)
            while roll_i < len(roll) and roll[roll_i][0] == t:
                _, frm, to = roll[roll_i]
                held = position.get(frm, 0) + sum(
                    (o.qty if o.side == "BUY" else -o.qty) for o in pending if o.contract == frm)
                if held:
                    new_order(t, "SELL" if held > 0 else "BUY", abs(held), frm, "ROLL_CLOSE")
                    new_order(t, "BUY" if held > 0 else "SELL", abs(held), to, "ROLL_OPEN")
                self.contract = to
                roll_i += 1

            # (2) decisions whose decision_ts == t create orders (before bars starting at t open)
            while di < len(decisions) and decisions[di][0] == t:
                _, tgt, _ = decisions[di]
                if tgt != target:
                    desired = tgt * self.qty
                    cur = sum(position.values()) + sum(
                        (o.qty if o.side == "BUY" else -o.qty) for o in pending)
                    delta = desired - cur
                    if delta:
                        new_order(t, "BUY" if delta > 0 else "SELL", abs(delta), self.contract, "SIGNAL")
                    target = tgt
                di += 1

            # (3) bars starting at t: try to fill pending orders at the open
            for b in bars_at.get(t, []):
                for o in pending:
                    if o.filled or o.contract != b.contract or b.ts < o.order_ts:
                        continue
                    if b.ts >= o.expires_at:
                        continue  # handled by expiry sweep below
                    reason = None
                    if not b.in_session:
                        reason = "OUTSIDE_SESSION"
                    elif b.excluded:
                        reason = "BAR_IN_EXCLUSION_WINDOW"
                    elif b.v == 0:
                        reason = "ZERO_VOLUME_BAR"
                    elif o.side == "BUY" and b.limit_up_locked:
                        reason = "LIMIT_UP_LOCKED_BUY"
                    elif o.side == "SELL" and b.limit_down_locked:
                        reason = "LIMIT_DOWN_LOCKED_SELL"
                    if reason:
                        res.rejections.append({"bar_ts": _fmt(b.ts), "order_ts": _fmt(o.order_ts),
                                               "side": o.side, "contract": b.contract, "reason": reason})
                        continue
                    px = self.fill_price(o.side, b.o)
                    fee, tax = self.costs(o.side, px, o.qty)
                    res.fills.append(Fill(o.order_ts, b.ts, o.side, o.qty, o.contract, b.o, px, fee, tax, o.reason))
                    position[o.contract] = position.get(o.contract, 0) + (o.qty if o.side == "BUY" else -o.qty)
                    o.filled = True

            # (4) expiry sweep
            for o in pending:
                if not o.filled and t >= o.expires_at and not getattr(o, "_expired", False):
                    o._expired = True  # type: ignore[attr-defined]
                    res.rejections.append({"bar_ts": None, "order_ts": _fmt(o.order_ts), "side": o.side,
                                           "contract": o.contract, "reason": "ORDER_EXPIRED"})
            pending = [o for o in pending if not o.filled and not getattr(o, "_expired", False)]

        for o in pending:  # still live at end of data
            res.rejections.append({"bar_ts": None, "order_ts": _fmt(o.order_ts), "side": o.side,
                                   "contract": o.contract, "reason": "UNFILLED_AT_END_OF_DATA"})

        self._pair(res)
        return res

    # ---------------------------------------------------------- accounting

    def _pair(self, res: Result) -> None:
        open_lots: dict[str, list[Fill]] = {}
        for f in res.fills:
            lots = open_lots.setdefault(f.contract, [])
            sgn = 1 if f.side == "BUY" else -1
            if lots and (1 if lots[0].side == "BUY" else -1) != sgn:
                entry = lots.pop(0)
                if entry.qty != f.qty:
                    raise NotImplementedError("oracle v1 pairs equal-size fills only")
                direction = 1 if entry.side == "BUY" else -1
                gross = (f.fill_price - entry.fill_price) * f.qty * self.mult * direction
                costs = entry.fee + entry.tax + f.fee + f.tax
                rt = {"contract": f.contract, "entry_ts": _fmt(entry.exec_ts), "exit_ts": _fmt(f.exec_ts),
                      "direction": "LONG" if direction == 1 else "SHORT", "qty": f.qty,
                      "entry_price": str(entry.fill_price), "exit_price": str(f.fill_price),
                      "gross_pnl": str(gross), "costs": str(costs), "net_pnl": str(gross - costs)}
                span = [(s, e) for s, e in self.exclusions if entry.exec_ts < e and s < f.exec_ts + self.tf]
                if span:
                    rt["reason"] = "HOLDING_PERIOD_SPANS_EXCLUSION"
                    res.invalidated_round_trips.append(rt)
                else:
                    res.round_trips.append(rt)
                    res.realized_pnl += gross - costs
            else:
                lots.append(f)
        remaining = [f for lots in open_lots.values() for f in lots]
        if remaining:
            if len(remaining) != 1:
                raise NotImplementedError("oracle v1 supports a single open lot")
            e = remaining[0]
            last = [b for b in self.bars if b.contract == e.contract and self.signal_eligible(b)][-1]
            direction = 1 if e.side == "BUY" else -1
            mark = (last.c - e.fill_price) * e.qty * self.mult * direction
            exit_side = "SELL" if direction == 1 else "BUY"
            liq_px = self.fill_price(exit_side, last.c)
            lfee, ltax = self.costs(exit_side, liq_px, e.qty)
            liq = (liq_px - e.fill_price) * e.qty * self.mult * direction - e.fee - e.tax - lfee - ltax
            res.open_position = {
                "contract": e.contract, "direction": "LONG" if direction == 1 else "SHORT", "qty": e.qty,
                "entry_ts": _fmt(e.exec_ts), "entry_price": str(e.fill_price),
                "entry_costs": str(e.fee + e.tax), "mark_ts": _fmt(last.ts), "mark_price": str(last.c),
                "unrealized_pnl_mark": str(mark),
                "equity_change_mark_after_entry_costs": str(mark - e.fee - e.tax),
                "terminal_liquidation_estimate_net": str(liq),
            }


# ----------------------------------------------------------------- serialise


def result_to_dict(r: Result) -> dict:
    return {
        "convention": CONVENTION_VERSION,
        "signals": r.signals,
        "suppressed_signals": r.suppressed_signals,
        "orders": [{k: o[k] for k in ("order_ts", "side", "qty", "contract", "reason")} for o in r.orders],
        "fills": [{"order_ts": _fmt(f.order_ts), "exec_ts": _fmt(f.exec_ts), "side": f.side, "qty": f.qty,
                   "contract": f.contract, "ref_open": str(f.ref_open), "fill_price": str(f.fill_price),
                   "fee": str(f.fee), "tax": str(f.tax)} for f in r.fills],
        "rejections": r.rejections,
        "round_trips": r.round_trips,
        "invalidated_round_trips": r.invalidated_round_trips,
        "realized_pnl": str(r.realized_pnl),
        "open_position": r.open_position,
    }


def run_case(case: dict) -> dict:
    return result_to_dict(ReferenceEngine(case).run())


if __name__ == "__main__":  # pragma: no cover
    import sys
    for p in sys.argv[1:]:
        print(json.dumps(run_case(load_case(p)), indent=2, ensure_ascii=False))
