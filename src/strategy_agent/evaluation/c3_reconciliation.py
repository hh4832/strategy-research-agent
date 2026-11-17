"""Phase 5-C1 C3 Trade Integrity: independent, fail-closed ledger reconciliation of a finished Core archive.

Layering (no layer reads a later one, so there is no circular dependency):

  1. Raw Core checks           metrics.json m3a4_checks / execution_diagnostics temporal (copied, never edited)
  2. C3 ledger reconciliation  ``reconcile_scenario`` - trades <-> fills <-> open positions <-> costs <-> orders,
                               computed only from archived ledgers (never from K03 or any other Core verdict)
  3. Execution-quality         ``execution_quality`` - raw checks + C3; the ONLY place where
     eligibility               K03 = NOT_APPLICABLE_OPEN_POSITION may be accepted, and only when C3 proved the
                               open position and the cost identity independently
  4. Downstream evaluator      phase4c.envelope -> universal.evaluate

Order lifecycle semantics are those of market-research-core 50ea3bfa42df7708dd3a1b21f17798c206be054e
``src/market_research_core/execution.py`` (pinned Core of the archived run):

  * pre-loop rejections (PIT_VIOLATION, UNSUPPORTED_CONSERVATIVE_ORDER_TYPE): order never activated, no bar_ts
  * activation: target orders are sized from filled position + pending live targets; delta == 0 ->
    TARGET_ORDER_NOOP (order_events only, NOT in fills/rejections); wrong side -> TARGET_SIDE_MISMATCH (terminal)
  * live bar loop, DAY order: bar-level rejections OUTSIDE_SESSION / BAR_NOT_ELIGIBLE / ZERO_VOLUME_BAR /
    VOLUME_PARTICIPATION_EXCEEDED / <side>_NOT_ELIGIBLE / LIMIT_NOT_TOUCHED keep the order waiting (temporary);
    BAR_IN_EXCLUSION_WINDOW / INSUFFICIENT_MARGIN / RISK_LIMIT end it (terminal)
  * expiry: ORDER_EXPIRED row with expiry_ts (= first session_close after order_time), no bar_ts
  * end of data: one row with the last reason and neither bar_ts nor expiry_ts (terminal)

Nothing here repairs, re-times or invents a ledger row. Every unexplained item is reported with its id.
"""
from __future__ import annotations

import math
from datetime import datetime
from zoneinfo import ZoneInfo

CORE_LIFECYCLE_REF = ("market-research-core@50ea3bfa42df7708dd3a1b21f17798c206be054e "
                      "src/market_research_core/execution.py")
NY = ZoneInfo("America/New_York")

# DAY order keeps waiting after these bar-level codes (execution.py live loop; DAY branch). Explicit allow-list:
# anything else - including Core-retried cost-schedule codes - is NOT accepted as temporary.
TEMPORARY_DAY_BAR_CODES = frozenset({"OUTSIDE_SESSION", "BAR_NOT_ELIGIBLE", "ZERO_VOLUME_BAR",
                                     "VOLUME_PARTICIPATION_EXCEEDED", "BUY_NOT_ELIGIBLE", "SELL_NOT_ELIGIBLE",
                                     "LIMIT_NOT_TOUCHED"})
TERMINAL_BAR_CODES = frozenset({"BAR_IN_EXCLUSION_WINDOW", "INSUFFICIENT_MARGIN", "RISK_LIMIT"})
ACTIVATION_TERMINAL_CODES = frozenset({"TARGET_SIDE_MISMATCH"})
PRE_ACTIVATION_CODES = frozenset({"PIT_VIOLATION", "UNSUPPORTED_CONSERVATIVE_ORDER_TYPE"})
EXPIRY_CODE = "ORDER_EXPIRED"
END_OF_DATA_CODES = TEMPORARY_DAY_BAR_CODES | {"UNFILLED_AT_END_OF_DATA"}
EK03 = "K03_round_trip_costs_equal_ledger"
K03_OPEN = "NOT_APPLICABLE_OPEN_POSITION"
QTY_EPS = 1e-12


def _missing(v) -> bool:
    return v is None or str(v).strip() in ("", "NaT", "nan", "None")


def _ts(v) -> datetime:
    t = datetime.fromisoformat(str(v))
    if t.tzinfo is None:
        raise ValueError(f"naive timestamp {v!r}")
    return t


def _num(v) -> float:
    x = float(v)
    if not math.isfinite(x):
        raise ValueError(f"non-finite {v!r}")
    return x


def _close(a, b, tol) -> bool:
    return math.isclose(float(a), float(b), rel_tol=tol, abs_tol=tol)


def _ny_date(t: datetime):
    return t.astimezone(NY).date()


# ======================================================================================== order universe

def order_universe(decisions: list[dict], symbol: str) -> list[dict]:
    """decisions.csv -> the Core target orders exactly as adapters.mrc_adapter builds them:
    decisions_to_targets collapses repeated (target, contract); targets_to_orders skips a target equal to the
    previous intended target (start 0) and names the order f"{i:04d}-{reason}" with i the target index."""
    targets, last = [], None
    for d in sorted(decisions, key=lambda r: _ts(r["decision_ts"])):
        tgt = int(d["target"])
        if tgt not in (-1, 0, 1):
            raise ValueError(f"decision target {tgt} outside -1/0/+1")
        key = (tgt, symbol)
        if key == last:
            continue
        targets.append({"ts": _ts(d["decision_ts"]), "target": tgt, "reason": d["reason"]})
        last = key
    prev, orders = 0, []
    for i, t in enumerate(targets):
        if t["target"] == prev:
            continue
        orders.append({"order_id": f"{i:04d}-{t['reason']}", "order_time": t["ts"], "target_units": t["target"],
                       "side": "BUY" if t["target"] > prev else "SELL"})
        prev = t["target"]
    return orders


# ======================================================================================== order lifecycle

def classify_order(order: dict, fills: list[dict], rows: list[dict]) -> tuple[str, dict, list[str]]:
    """One order's archived evidence -> (classification, detail, problems). Fail closed on anything unknown."""
    oid, ot = order["order_id"], order["order_time"]
    problems: list[str] = []
    temp, terminal = [], []
    for r in rows:
        code, has_bar, has_exp = r["reason"], not _missing(r.get("bar_ts")), not _missing(r.get("expiry_ts"))
        if code == EXPIRY_CODE:
            if has_bar or not has_exp:
                problems.append(f"MALFORMED_EXPIRY_ROW:{oid}")
            terminal.append(("EXPIRED", code, _ts(r["expiry_ts"]) if has_exp else None))
        elif has_bar and code in TEMPORARY_DAY_BAR_CODES:
            temp.append((code, _ts(r["bar_ts"])))
        elif has_bar and code in TERMINAL_BAR_CODES:
            terminal.append(("TERMINAL_BAR_REJECTION", code, _ts(r["bar_ts"])))
        elif not has_bar and not has_exp and code in ACTIVATION_TERMINAL_CODES:
            terminal.append(("TERMINAL_ACTIVATION_REJECTION", code, None))
        elif not has_bar and not has_exp and code in PRE_ACTIVATION_CODES:
            terminal.append(("NEVER_ACTIVATED", code, None))
        elif not has_bar and not has_exp and code in END_OF_DATA_CODES:
            terminal.append(("UNFILLED_AT_END_OF_DATA", code, None))
        else:
            problems.append(f"UNKNOWN_REJECTION_STATE:{oid}:{code}:bar_ts={r.get('bar_ts')}:expiry_ts={r.get('expiry_ts')}")
    detail = {"temporary_rejections": [(c, t.isoformat()) for c, t in temp],
              "terminal": [(k, c, t.isoformat() if t else None) for k, c, t in terminal]}
    bar_times = [t for _, t in temp]
    if len(set(bar_times)) != len(bar_times):
        problems.append(f"DUPLICATE_BAR_REJECTION:{oid}")
    for _, t in temp:
        # Core matches only bars strictly after max(signal_available_at, order_time) in the signal's session
        if not (t > ot and _ny_date(t) == _ny_date(ot)):
            problems.append(f"REJECTION_CHRONOLOGY:{oid}:{t.isoformat()}")
    if len(fills) > 1:
        problems.append(f"ORDER_FILLED_TWICE:{oid}")
    if fills:
        if terminal:
            problems.append(f"FILLED_AFTER_TERMINAL:{oid}:" + ",".join(c for _, c, _ in terminal))
        fx = _ts(fills[0]["exec_ts"])
        if not (fx > ot and _ny_date(fx) == _ny_date(ot)):
            problems.append(f"FILL_OUTSIDE_DAY_ORDER_SESSION:{oid}")
        if any(t >= fx for t in bar_times):
            problems.append(f"REJECTION_AFTER_FILL:{oid}")
        cls = "FILLED_AFTER_TEMPORARY_REJECTION" if temp else "FILLED"
        return cls, detail | {"exec_ts": fx.isoformat()}, problems
    if not terminal:
        if temp:
            problems.append(f"UNRESOLVED_TEMPORARY_REJECTION:{oid}")
            return "UNRESOLVED", detail, problems
        return "NOOP_CANDIDATE", detail, problems
    if len(terminal) > 1:
        problems.append(f"MULTIPLE_TERMINAL_STATES:{oid}")
    kind, code, t = terminal[0]
    if kind == "EXPIRED":
        if t is None or not (t > ot and _ny_date(t) == _ny_date(ot)) or any(b >= t for b in bar_times):
            problems.append(f"EXPIRY_CHRONOLOGY:{oid}")
    elif kind == "TERMINAL_BAR_REJECTION":
        if not (t > ot and _ny_date(t) == _ny_date(ot)) or any(b >= t for b in bar_times):
            problems.append(f"TERMINAL_REJECTION_CHRONOLOGY:{oid}")
    elif kind in ("TERMINAL_ACTIVATION_REJECTION", "NEVER_ACTIVATED") and temp:
        problems.append(f"BAR_REJECTION_FOR_INACTIVE_ORDER:{oid}")
    return kind, detail | {"terminal_code": code, "terminal_ts": t.isoformat() if t else None}, problems


def replay_activation(orders: list[dict], outcome: dict[str, dict], signed_fills: list[tuple[datetime, float]],
                      unit_qty: float) -> tuple[dict[str, dict], list[str]]:
    """Replay Core's target activation (filled position + pending live targets) over the archived outcomes and
    return, per order, the predicted activation result and every disagreement with the archive."""
    pred: dict[str, dict] = {}
    problems: list[str] = []
    active: list[dict] = []          # activated orders with a non-zero delta: {"oid", "delta", "outcome"}

    def done_by(o: dict, t: datetime) -> bool:
        k = o["outcome"]
        c = k["class"]
        if c in ("FILLED", "FILLED_AFTER_TEMPORARY_REJECTION"):
            return _ts(k["exec_ts"]) < t          # a fill at the same bar_start happens after activation
        if c == "EXPIRED":
            return _ts(k["terminal_ts"]) <= t     # expiry is processed before activation at the same event
        if c == "TERMINAL_BAR_REJECTION":
            return _ts(k["terminal_ts"]) < t
        return False                              # end of data: still live

    for o in sorted(orders, key=lambda x: (x["order_time"], x["order_id"])):
        oid, t, k = o["order_id"], o["order_time"], outcome[o["order_id"]]
        if k["class"] == "NEVER_ACTIVATED":
            pred[oid] = {"predicted": "NEVER_ACTIVATED"}
            continue
        filled = math.fsum(q for x, q in signed_fills if x < t)
        pending = [p for p in active if not done_by(p, t)]
        pending_signed = math.fsum(p["delta"] for p in pending)
        delta = o["target_units"] * unit_qty - (filled + pending_signed)
        row = {"filled_position": filled, "pending_signed_qty": pending_signed,
               "pending_order_ids": [p["oid"] for p in pending], "projected_position": filled + pending_signed,
               "desired_target": o["target_units"] * unit_qty, "signed_delta": delta}
        if abs(delta) < QTY_EPS:
            row["predicted"] = "TARGET_ORDER_NOOP"
            ok = k["class"] == "NOOP_CANDIDATE"
        elif ("BUY" if delta > 0 else "SELL") != o["side"]:
            row["predicted"] = "TARGET_SIDE_MISMATCH"
            ok = k["class"] == "TERMINAL_ACTIVATION_REJECTION" and k.get("terminal_code") == "TARGET_SIDE_MISMATCH"
        else:
            row["predicted"] = "TARGET_ORDER_ACTIVATED"
            if k["class"] in ("FILLED", "FILLED_AFTER_TEMPORARY_REJECTION"):
                ok = _close(k["signed_qty"], delta, 1e-12)
            else:
                ok = k["class"] in ("EXPIRED", "TERMINAL_BAR_REJECTION", "UNFILLED_AT_END_OF_DATA")
            active.append({"oid": oid, "delta": delta, "outcome": k})
        if not ok:
            problems.append(f"PENDING_TARGET_INCONSISTENT:{oid}:predicted={row['predicted']}:archived={k['class']}")
        pred[oid] = row
    return pred, problems


# ======================================================================================== scenario reconciliation

def reconcile_scenario(*, trades: list[dict], fills: list[dict], rejections: list[dict], decisions: list[dict],
                       metrics: dict, orders_submitted: int, symbol: str, tol: float,
                       order_quantity: float | None = None, order_events: list[dict] | None = None) -> dict:
    problems: list[str] = []
    undetermined: list[str] = []

    # ---------------------------------------------------------------- ledger shape (kept from the original C3)
    keys = [(r["entry_ts"], r["exit_ts"], r.get("symbol")) for r in trades]
    if len(set(keys)) != len(keys):
        problems.append("DUPLICATE_TRADE")
    fid = [(r["order_id"], r["exec_ts"]) for r in fills]
    if len(set(fid)) != len(fid):
        problems.append("DUPLICATE_FILL")
    if len({r["order_id"] for r in fills}) != len(fills):
        problems.append("ORDER_FILLED_TWICE")
    costs_ok = True
    for i, f in enumerate(fills):
        try:
            f["_qty"], f["_px"], f["_cost"], f["_ts"] = (_num(f["qty"]), _num(f["fill_price"]),
                                                         _num(f["explicit_cost"]), _ts(f["exec_ts"]))
        except (KeyError, TypeError, ValueError):
            problems.append(f"MALFORMED_FILL:{f.get('order_id')}@{f.get('exec_ts')}")
            costs_ok = False
        f["_i"] = i
    if not costs_ok:
        return {"status": "FAIL", "problems": problems, "undetermined": undetermined}

    # ---------------------------------------------------------------- trades <-> fills, strictly one-to-one
    unassigned = {f["_i"]: f for f in fills}
    by_key: dict[tuple, list[int]] = {}
    for f in fills:
        by_key.setdefault((f["symbol"], f["side"], f["_ts"]), []).append(f["_i"])

    def take(sym, side, ts, qty, px, what):
        cand = [i for i in by_key.get((sym, side, _ts(ts)), []) if i in unassigned]
        if not cand:
            problems.append(f"TRADE_WITHOUT_FILL:{what}:{ts}")
            return None
        exact = [i for i in cand if _close(unassigned[i]["_qty"], qty, tol) and _close(unassigned[i]["_px"], _num(px), tol)]
        if not exact:
            problems.append(f"TRADE_FILL_MISMATCH:{what}:{ts}")
            return unassigned.pop(cand[0])
        # candidates equal in symbol/side/time/qty/price are interchangeable; each is still consumed exactly once
        return unassigned.pop(min(exact, key=lambda i: (unassigned[i]["order_id"], i)))

    matched, per_trade_cost_bad, trade_fill_cost = 0, [], []
    for r in sorted(trades, key=lambda x: (_ts(x["entry_ts"]), _ts(x["exit_ts"]))):
        side_in, side_out = ("BUY", "SELL") if r["side"] == "LONG" else ("SELL", "BUY") if r["side"] == "SHORT" \
            else (None, None)
        if side_in is None:
            problems.append(f"TRADE_SIDE_UNKNOWN:{r['entry_ts']}")
            continue
        qty = _num(r["qty"])
        e = take(r["symbol"], side_in, r["entry_ts"], qty, r["entry_price"], "entry")
        x = take(r["symbol"], side_out, r["exit_ts"], qty, r["exit_price"], "exit")
        sign = 1.0 if r["side"] == "LONG" else -1.0
        if not _close(sign * (_num(r["exit_price"]) - _num(r["entry_price"])) * qty, r["gross_pnl"], tol):
            problems.append(f"TRADE_GROSS_PNL:{r['entry_ts']}")
        if not _ts(r["entry_ts"]) < _ts(r["exit_ts"]):
            problems.append(f"TRADE_CHRONOLOGY:{r['entry_ts']}")
        if e is None or x is None:
            continue
        alloc = _num(r["gross_pnl"]) - _num(r["net_pnl"])
        trade_fill_cost.append(e["_cost"] + x["_cost"])
        if not _close(alloc, e["_cost"] + x["_cost"], tol):
            per_trade_cost_bad.append(r["entry_ts"])
        matched += 1
    if per_trade_cost_bad:
        problems.append(f"TRADE_COST_ALLOCATION:{len(per_trade_cost_bad)} trades, first {per_trade_cost_bad[:3]}")
    closed_fill_ids = {i for i in range(len(fills))} - set(unassigned)

    # ---------------------------------------------------------------- remaining fills <-> Core open positions
    oprec = metrics.get("open_position_at_end")
    positions = (oprec or {}).get("positions") if isinstance(oprec, dict) else None
    open_fills: list[dict] = []
    if positions is None:
        if unassigned:
            problems.append("OPEN_POSITION_EVIDENCE_MISSING:" + ",".join(sorted(f["order_id"] for f in unassigned.values())))
        positions = []
    for p in positions:
        side = {"LONG": "BUY", "SHORT": "SELL"}.get(p.get("side"))
        try:
            pts, pq, ppx = _ts(p["entry_ts"]), _num(p["qty"]), _num(p["entry_price"])
        except (KeyError, TypeError, ValueError):
            problems.append(f"OPEN_POSITION_MALFORMED:{p}")
            continue
        cand = [f for f in unassigned.values() if f["symbol"] == p.get("symbol") and f["side"] == side
                and f["_ts"] == pts and f.get("contract", p.get("contract")) == p.get("contract")
                and _close(f["_qty"], pq, tol) and _close(f["_px"], ppx, tol)]
        if not cand:
            problems.append(f"OPEN_POSITION_WITHOUT_FILL:{p.get('symbol')}:{p.get('entry_ts')}")
            continue
        # identical candidates are interchangeable; one position consumes exactly one fill
        open_fills.append(unassigned.pop(min(cand, key=lambda f: (f["order_id"], f["_i"]))["_i"]))
    for f in unassigned.values():
        problems.append(f"ORPHAN_FILL:{f['order_id']}@{f['exec_ts']}")
    signed = lambda f: f["_qty"] if f["side"] == "BUY" else -f["_qty"]  # noqa: E731
    ledger_net = math.fsum(signed(f) for f in fills)
    pos_net = math.fsum((1 if p.get("side") == "LONG" else -1) * float(p.get("qty", 0)) for p in positions)
    if not _close(ledger_net, pos_net, tol):
        problems.append(f"OPEN_POSITION_NET_MISMATCH:fill ledger net {ledger_net} != open positions {pos_net}")

    # ---------------------------------------------------------------- cost identity
    closed_cost = math.fsum(_num(r["gross_pnl"]) - _num(r["net_pnl"]) for r in trades)
    open_cost = math.fsum(f["_cost"] for f in open_fills)
    ledger_cost = math.fsum(f["_cost"] for f in fills)
    residual = ledger_cost - (closed_cost + open_cost)
    if not _close(closed_cost + open_cost, ledger_cost, tol):
        problems.append(f"COST_ALLOCATION:closed {closed_cost} + open entry {open_cost} != fill ledger {ledger_cost}")

    # ---------------------------------------------------------------- metrics agreement (kept)
    if not _close(math.fsum(_num(r["net_pnl"]) for r in trades), metrics.get("trades", {}).get("net_pnl", float("nan")), tol):
        problems.append("NET_PNL_VS_METRICS")
    if metrics.get("fills") != len(fills) or metrics.get("rejections") != len(rejections):
        problems.append("COUNTS_VS_METRICS")

    # ---------------------------------------------------------------- order lifecycle
    lifecycle = _lifecycle(fills=fills, rejections=rejections, decisions=decisions, orders_submitted=orders_submitted,
                           symbol=symbol, order_quantity=order_quantity, trades=trades, positions=positions,
                           order_events=order_events, tol=tol)
    problems += lifecycle.pop("problems")
    undetermined += lifecycle.pop("undetermined")

    status = "FAIL" if problems else "UNDETERMINED" if undetermined else "PASS"
    rej_orders = sorted({r["order_id"] for r in rejections})
    filled = {r["order_id"] for r in fills}
    return {"status": status, "problems": problems, "undetermined": undetermined,
            "trades": len(trades), "trades_matched_to_entry_and_exit_fills": matched, "fills": len(fills),
            "closed_trade_fills": len(closed_fill_ids), "open_position_fills": len(open_fills),
            "open_positions_reported": len(positions), "open_positions_verified": len(open_fills),
            "open_position_fill_ids": [f"{f['order_id']}@{f['exec_ts']}" for f in open_fills],
            "fill_allocation_identity": f"{len(fills)} = 2 x {matched} closed trades + {len(open_fills)} open entries"
                                        f" + {len(unassigned)} orphans",
            "fill_ledger_net_position": ledger_net, "open_positions_net": pos_net,
            "rejection_rows": len(rejections), "rejected_orders": rej_orders,
            "terminal_rejections": sorted(lifecycle["by_class_ids"].get("EXPIRED", [])),
            "orders_submitted": orders_submitted, "orders_filled": len(filled),
            "orders_without_fill_or_rejection_record": len(lifecycle["by_class_ids"].get("NOOP_CANDIDATE", [])),
            "allocated_trade_costs": closed_cost, "open_position_entry_cost": open_cost,
            "fill_ledger_explicit_costs": ledger_cost, "cost_residual": residual,
            "cost_identity": "closed_trade_allocated_cost + verified_open_position_entry_cost == fill_ledger_explicit_cost",
            "cost_tolerance": f"rel/abs {tol}", "slippage_accounting": "EMBEDDED_IN_PRICES (not part of explicit cost)",
            **lifecycle}


def _lifecycle(*, fills, rejections, decisions, orders_submitted, symbol, order_quantity, trades, positions,
               order_events, tol) -> dict:
    problems: list[str] = []
    undetermined: list[str] = []
    try:
        orders = order_universe(decisions, symbol)
    except (KeyError, TypeError, ValueError) as e:
        return {"problems": [f"ORDER_UNIVERSE_UNREADABLE:{e}"], "undetermined": [], "by_class": {}, "by_class_ids": {},
                "unexplained_order_ids": []}
    if len(orders) != orders_submitted:
        problems.append(f"ORDER_UNIVERSE_COUNT:decisions.csv implies {len(orders)} orders != diagnostics {orders_submitted}")
    known = {o["order_id"]: o for o in orders}
    f_by: dict[str, list] = {}
    for f in fills:
        f_by.setdefault(f["order_id"], []).append(f)
    r_by: dict[str, list] = {}
    for r in rejections:
        r_by.setdefault(r["order_id"], []).append(r)
    for oid in sorted((set(f_by) | set(r_by)) - set(known)):
        problems.append(f"UNKNOWN_ORDER_ID:{oid}")
    outcome: dict[str, dict] = {}
    by_class_ids: dict[str, list] = {}
    for o in orders:
        oid = o["order_id"]
        fl = f_by.get(oid, [])
        for f in fl:
            if f["side"] != o["side"]:
                problems.append(f"FILL_SIDE_VS_ORDER:{oid}")
            if "order_time" in f and not _missing(f["order_time"]) and _ts(f["order_time"]) != o["order_time"]:
                problems.append(f"FILL_ORDER_TIME_VS_DECISION:{oid}")
        cls, detail, pr = classify_order(o, fl, r_by.get(oid, []))
        problems += pr
        if fl:
            detail["signed_qty"] = fl[0]["_qty"] if fl[0]["side"] == "BUY" else -fl[0]["_qty"]
        outcome[oid] = {"class": cls, **detail}
        by_class_ids.setdefault(cls, []).append(oid)

    # NOOP evidence: Core order_events when archived, else a strict replay of Core's activation rule
    noop_ids = by_class_ids.get("NOOP_CANDIDATE", [])
    evidence = {"basis": None, "unit_quantity": None, "unit_quantity_basis": None}
    predictions: dict[str, dict] = {}
    if order_events is not None:
        evidence["basis"] = "CORE_ORDER_EVENTS"
        noop_events = {e["order_id"] for e in order_events if e.get("event") == "TARGET_ORDER_NOOP"}
        for oid in noop_ids:
            if oid not in noop_events:
                problems.append(f"NOOP_WITHOUT_EVENT:{oid}")
        for oid in sorted(noop_events - set(noop_ids)):
            problems.append(f"NOOP_EVENT_CONFLICTS_WITH_LEDGER:{oid}")
    else:
        qty = order_quantity
        basis = "DECLARED"
        if qty is None:
            # The first activated order in Core's activation order sees a flat book with nothing pending, so its
            # fill (if any) is exactly |target| x unit. Anything else leaves the unit undetermined.
            first = next((o for o in sorted(orders, key=lambda x: (x["order_time"], x["order_id"]))
                          if outcome[o["order_id"]]["class"] != "NEVER_ACTIVATED"), None)
            k = outcome[first["order_id"]] if first else {}
            if first and k["class"] in ("FILLED", "FILLED_AFTER_TEMPORARY_REJECTION") and first["target_units"]:
                qty, basis = abs(k["signed_qty"]) / abs(first["target_units"]), "FIRST_ACTIVATION_FROM_FLAT_BOOK"
                evidence["unit_quantity_anchor_order_ids"] = [first["order_id"]]
            else:
                qty, basis = None, None
        evidence |= {"unit_quantity": qty, "unit_quantity_basis": basis}
        if qty is None:
            evidence["basis"] = "NONE"
            if orders:
                undetermined.append("ORDER_SIZING_AND_NOOP_UNVERIFIABLE: no Core order_events archived and the first "
                                    "activated order did not fill, so the order unit quantity is not proven; needs a "
                                    f"Core order_events export (NOOP candidates: {noop_ids})")
        else:
            evidence["basis"] = "STRICT_REPLAY_OF_CORE_ACTIVATION_RULE"
            sf = sorted(((f["_ts"], f["_qty"] if f["side"] == "BUY" else -f["_qty"]) for f in fills), key=lambda x: x[0])
            predictions, pr = replay_activation(orders, outcome, sf, qty)
            problems += pr
    evidence["core_lifecycle_ref"] = CORE_LIFECYCLE_REF
    explained = {"FILLED", "FILLED_AFTER_TEMPORARY_REJECTION", "EXPIRED", "TERMINAL_BAR_REJECTION",
                 "TERMINAL_ACTIVATION_REJECTION", "NEVER_ACTIVATED", "UNFILLED_AT_END_OF_DATA"}
    if evidence["basis"] == "CORE_ORDER_EVENTS":
        verified_noop = [oid for oid in noop_ids if f"NOOP_WITHOUT_EVENT:{oid}" not in problems]
    else:
        verified_noop = [oid for oid in noop_ids if predictions.get(oid, {}).get("predicted") == "TARGET_ORDER_NOOP"]
    unexplained = sorted({oid for cls, ids in by_class_ids.items() if cls not in explained for oid in ids}
                         - set(verified_noop))
    noop_states = {}
    for oid in noop_ids:
        p = predictions.get(oid)
        if p:
            noop_states[oid] = {k: p[k] for k in ("filled_position", "pending_signed_qty", "desired_target")}
    return {"problems": problems, "undetermined": undetermined,
            "order_classification": {k: len(v) for k, v in sorted(by_class_ids.items())},
            "by_class_ids": by_class_ids,
            "temporary_rejection_then_fill_ids": sorted(by_class_ids.get("FILLED_AFTER_TEMPORARY_REJECTION", [])),
            "temporary_rejection_codes_seen": sorted({c for oid in by_class_ids.get("FILLED_AFTER_TEMPORARY_REJECTION", [])
                                                      for c, _ in outcome[oid]["temporary_rejections"]}),
            "verified_noop_order_ids": sorted(verified_noop), "noop_evidence": evidence,
            "noop_state_at_activation": noop_states,
            "unexplained_order_ids": unexplained}


# ======================================================================================== execution quality

def execution_quality(checks: dict, temporal: dict, c3: dict | None) -> dict:
    """Raw Core checks + independent C3 -> execution-quality eligibility. PASS requires temporal PASS and every
    M3-A.4 check PASS, with exactly one explained exception: K03 == NOT_APPLICABLE_OPEN_POSITION, accepted only
    when C3 for this scenario is PASS, verified >=1 open position against the fill ledger with no orphan, and
    closed the cost identity. Every other non-PASS value (incl. other NOT_APPLICABLE_*) fails closed."""
    reasons, exceptions = [], []
    if (temporal or {}).get("status") != "PASS":
        reasons.append("TEMPORAL_NOT_PASS")
    if not checks:
        reasons.append("M3A4_CHECKS_MISSING")
    for k, v in sorted((checks or {}).items()):
        if v == "PASS":
            continue
        if k == EK03 and v == K03_OPEN:
            ok = (c3 is not None and c3.get("status") == "PASS" and c3.get("open_positions_verified", 0) >= 1
                  and c3.get("open_positions_verified") == c3.get("open_positions_reported")
                  and not any(p.startswith("COST_ALLOCATION") for p in c3.get("problems", [])))
            if ok:
                exceptions.append({"check": k, "value": v, "explained_by": "C3 open-position + cost reconciliation",
                                   "open_position_fill_ids": c3.get("open_position_fill_ids"),
                                   "open_position_entry_cost": c3.get("open_position_entry_cost"),
                                   "cost_residual": c3.get("cost_residual")})
                continue
            reasons.append(f"{k}={v} without independent C3 open-position/cost reconciliation")
            continue
        reasons.append(f"{k}={v}")
    return {"status": "FAIL" if reasons else "PASS", "reasons": reasons, "explained_exceptions": exceptions}
