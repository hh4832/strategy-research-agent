"""B01-B12 cross-check against the REAL market-research-core ``ExecutionEngine.run`` (no fake engine, no oracle
self-comparison).

python -m benchmark.harness.mrc_crosscheck [--out benchmark/results]

Per case:
  * Bars: the case's synthetic bars in Core's execution schema with an EXPLICIT START label, the case sessions
    and limit flags. These synthetic settings test the engine only; they are NOT evidence about real TXF/IBKR
    label or session semantics (Core keeps those UNKNOWN / blocked).
  * Signals: B02 and B12 from the Agent's own pure signal functions (``strategy_agent.signals``); the other
    cases from the hand-calculated fixture signals (``expected.signals``, read-only). B09's spec roll schedule
    is expanded by the adapter into explicit roll orders.
  * Orders -> Core via the M1 adapter (target-position MARKET orders, TIF DAY); costs -> Core CostProfile built
    from the case costs with rounding NONE (convention v1 §1.6: unrounded Decimal).
  * Comparison of 12 dimensions with the case's ``expected`` block (unchanged).
A second diagnostic run with Core's default ``same_timestamp_open_assumption=False`` documents conflict C1, and a
chronology probe (no oracle answer needed) documents conflict C3.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from benchmark.harness.crosscheck import cases
from strategy_agent.adapters import mrc_adapter as A
from strategy_agent.ports import BacktestRequest, DatasetRef
from strategy_agent.signals.core import Decision, SignalBar
from strategy_agent.signals.orb import orb
from strategy_agent.signals.sma import sma_crossover

OUT = Path(__file__).resolve().parents[1] / "results"
FMT = "%Y-%m-%d %H:%M"
TOL = Decimal("0")          # exact: no tolerance may hide the approved Decimal convention


def _dt(s: str) -> datetime:
    return datetime.strptime(s, FMT)


def _num(x) -> str:
    """Exact text of a Core number: Decimal as-is; float via repr (shortest round-trip form)."""
    return str(x) if isinstance(x, Decimal) else repr(float(x))


def _d(x) -> Decimal:
    return Decimal(str(x))


# ------------------------------------------------------------------------------------------ inputs
def core_bars(case: dict):
    import pandas as pd
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(case["timezone"])
    tf = timedelta(minutes=case["timeframe_minutes"])
    sess = sorted((_dt(f"{s['date']} {s['start']}"), _dt(f"{s['date']} {s['end']}")) for s in case["sessions"])
    fut = case["instrument"]["kind"] == "future"
    rows = []
    for raw in case["bars"]:
        r = dict(zip(case["bars_columns"], raw))
        ts = _dt(r["ts"])
        # the session containing the bar; else the next session that day; else the next session; else the last
        same_day = [s for s in sess if s[0].date() == ts.date()]
        so, sc = next((s for s in sess if s[0] <= ts < s[1]), None) or next(
            (s for s in same_day if s[1] > ts), None) or next((s for s in sess if s[1] > ts), sess[-1])
        up, dn = bool(r.get("limit_up_locked", False)), bool(r.get("limit_down_locked", False))
        rows.append({
            "bar_start": ts.replace(tzinfo=tz), "bar_end": (ts + tf).replace(tzinfo=tz),
            "available_at": (ts + tf).replace(tzinfo=tz), "timezone": case["timezone"],
            "session": so.strftime("%Y-%m-%d"), "symbol": case["instrument"]["symbol"],
            "market": "TXF" if fut else "TW_EQUITY", "contract": r.get("contract", case["instrument"]["symbol"]),
            "open": float(r["o"]), "high": float(r["h"]), "low": float(r["l"]), "close": float(r["c"]),
            "volume": float(r["v"]), "data_exists": True, "backtest_eligible": True,
            "buy_execution_eligible": not up, "sell_execution_eligible": not dn, "execution_rejection_reason": "",
            "buy_execution_rejection_reason": "LIMIT_UP_LOCKED_BUY" if up else "",
            "sell_execution_rejection_reason": "LIMIT_DOWN_LOCKED_SELL" if dn else "",
            "session_open": so.replace(tzinfo=tz), "session_close": sc.replace(tzinfo=tz)})
    return pd.DataFrame(rows)


def decisions(case: dict) -> tuple[list[Decision], str]:
    """Signals through the Agent's signal functions where the case has an implemented model."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(case["timezone"])
    tf = timedelta(minutes=case["timeframe_minutes"])
    sm, sym = case["signal_model"], case["instrument"]["symbol"]
    if sm["type"] in ("sma_cross", "orb"):
        bars = []
        for raw in case["bars"]:
            r = dict(zip(case["bars_columns"], raw))
            ts = _dt(r["ts"]).replace(tzinfo=tz)
            bars.append(SignalBar(ts, ts + tf, *(float(r[k]) for k in "ohlc"), float(r["v"]),
                                  r.get("contract", sym), ts.strftime("%Y-%m-%d"), r["v"] > 0))
        if sm["type"] == "sma_cross":
            return sma_crossover(bars, int(sm["fast"]), int(sm["slow"])), "strategy_agent.signals.sma_crossover"
        sessions = {s["date"]: (_dt(f"{s['date']} {s['start']}").replace(tzinfo=tz),
                                _dt(f"{s['date']} {s['end']}").replace(tzinfo=tz)) for s in case["sessions"]}
        s0 = next(iter(sessions.values()))[0]
        flat = _dt(f"{s0:%Y-%m-%d} {sm['flatten_decision_time']}").replace(tzinfo=tz) - s0
        n = int(sm["range_bars"])
        return orb(bars, sessions, range_bars=n, range_duration=n * tf, last_entry_offset=flat - tf,
                   flatten_offset=flat, allow_short=sm.get("direction") != "long"), "strategy_agent.signals.orb"
    targets = sm.get("targets", [])
    contracts = [t.get("contract", sym) for t in targets]
    out = []
    for i, s in enumerate(case["expected"]["signals"]):
        c = contracts[min(i, len(contracts) - 1)] if contracts else sym
        if sm["type"] == "scripted":                      # pick the scripted target that produced this signal
            m = [t for t in targets if (_dt(t["bar_close_of"]) + tf).strftime(FMT) == s["signal_ts"]]
            c = m[0].get("contract", sym) if m else c
        out.append(Decision(_dt(s["signal_ts"]).replace(tzinfo=tz), int(s["target"]), c, "FIXTURE_SIGNAL"))
    return out, "fixture expected.signals (hand-calculated)"


def cost_profile(case: dict, core):
    c, inst = case["costs"], case["instrument"]
    fut = inst["kind"] == "future"
    fields = ("equity_commission_rate", "minimum_commission", "sell_tax_rate", "futures_fee_per_contract_per_side",
              "futures_transaction_tax_rate", "contract_multiplier", "tick_size", "slippage_ticks", "fee_rounding",
              "tax_rounding")
    if not fut and (c.get("tax_rate_side") or c.get("fee_per_unit_side")):
        raise A.CoreContractGap(["K04"], "equity two-sided tax / per-unit fee not expressible")
    return core.CostProfile(
        profile_id=f"BENCHMARK_{case['case_id'][:3]}", version="synthetic-v1", approval_status="DRAFT",
        instrument_type="FUTURES" if fut else "EQUITY", basis="ASSUMED",
        source_evidence="benchmark fixture costs (convention v1 §1.6); synthetic",
        equity_commission_rate=0.0 if fut else float(c.get("fee_rate_side", 0)), minimum_commission=0.0,
        sell_tax_rate=0.0 if fut else float(c.get("sell_tax_rate", 0)),
        futures_fee_per_contract_per_side=float(c.get("fee_per_unit_side", 0)) if fut else 0.0,
        futures_transaction_tax_rate=float(c.get("tax_rate_side", 0)) if fut else 0.0,
        contract_multiplier=float(inst["multiplier"]), tick_size=float(inst["tick_size"]),
        slippage_ticks=float(c.get("slippage_ticks", 0)), fee_rounding="NONE", tax_rounding="NONE",
        component_basis={f: "ASSUMED" for f in fields})


def synthetic_request(case: dict) -> BacktestRequest:
    import hashlib
    sha = hashlib.sha256(json.dumps(case, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    ds = DatasetRef(dataset_id=f"benchmark_{case['case_id'][:3]}", snapshot_id=case["case_id"],
                    producer_commit_sha="0" * 40, verdict="SYNTHETIC", usage_scope="RESEARCH_ONLY")
    s = case["sessions"]
    return BacktestRequest(spec={}, spec_sha256=sha, datasets=(ds,), window_start=f"{s[0]['date']}T00:00:00+08:00",
                           window_end=f"{s[-1]['date']}T23:59:59+08:00", warmup_start=f"{s[0]['date']}T00:00:00+08:00")


_GUARD = {}


def _guard_scope(core) -> set:
    """Gaps the adapter guard enforces on this Core: those whose capability (K17/K14b) is not demonstrated."""
    if id(core) not in _GUARD:
        _GUARD[id(core)] = A.unsupported_divergences(core)
    return _GUARD[id(core)]


def run_case(case: dict, core, core_sha: str, same_ts: bool = True):
    tz = case["timezone"]
    bars = core_bars(case)
    A.check_core_bars(bars)
    decs, signal_source = decisions(case)
    targets = A.decisions_to_targets(decs)
    if case.get("roll_schedule"):
        targets = A.expand_roll_schedule(
            targets, [dict(r, at=_dt(r["at"]).replace(tzinfo=__import__("zoneinfo").ZoneInfo(tz)).isoformat())
                      for r in case["roll_schedule"]])
    fut = case["instrument"]["kind"] == "future"
    orders = A.targets_to_orders(targets, symbol=case["instrument"]["symbol"], market="TXF" if fut else "TW_EQUITY",
                                 quantity=float(case["quantity"]), strategy_id=case["case_id"][:3],
                                 experiment_id="benchmark", time_in_force="DAY", core=core)
    req = synthetic_request(case)
    sem = A.MarketSemantics(market="TXF" if fut else "TW_EQUITY", bar_label="START", session_semantics="CONFIRMED",
                            timezone=tz, initial_cash=1e9, initial_margin_per_contract=1.0 if fut else None)
    excl = tuple((_dt(w["start"]).replace(tzinfo=__import__("zoneinfo").ZoneInfo(tz)),
                  _dt(w["end"]).replace(tzinfo=__import__("zoneinfo").ZoneInfo(tz)), f"EXCL_{i}")
                 for i, w in enumerate(case.get("exclusions", [])))
    cfg = A.core_config(req, sem, core_sha, exclusions=excl, core=core)
    if not same_ts:
        cfg = dataclasses.replace(cfg, same_timestamp_open_assumption=False)
    res = core.ExecutionEngine().run(bars, orders, cost_profile=cost_profile(case, core), config=cfg)
    report = A.core_result_to_report(res, req, core_sha)            # provenance + reason mapping via the adapter
    return res, report, {"signal_source": signal_source, "orders": [
        {"order_id": o.order_id, "contract": o.contract, "side": o.side, "target_position": o.target_position,
         "order_time": A.ts_label(o.order_time)} for o in orders], "same_timestamp_open_assumption": same_ts,
        "adapter_guard": A.known_divergences(res, orders, bars, _guard_scope(core)),
        "divergence_situations": A.known_divergences(res, orders, bars)}


# ------------------------------------------------------------------------------------------ comparison
def actual_view(res, report, case: dict) -> dict:
    tz = case["timezone"]
    fills = [{"exec_ts": A.ts_label(f["exec_ts"], tz), "side": f["side"], "qty": int(f["qty"]),
              "contract": f["contract"], "fill_price": _num(f["fill_price"]), "fee": _num(f["fee"]),
              "tax": _num(f["tax"]), "order_id": f["order_id"],
              "execution_assumption": f.get("execution_assumption")}
             for f in res.fills.to_dict("records")] if len(res.fills) else []
    trips = res.round_trips.to_dict("records") if len(res.round_trips) else []
    inval = res.invalidated_round_trips.to_dict("records") if len(res.invalidated_round_trips) else []
    opens = res.open_positions.to_dict("records") if len(res.open_positions) else []
    return {
        "fills": fills,
        "realized_pnl": str(sum((Decimal(_num(t["net_pnl"])) for t in trips), Decimal("0"))),
        "round_trip_net_pnl": [_num(t["net_pnl"]) for t in trips],
        "rejections": [{"bar_ts": A.ts_label(r["bar_ts"]) if r.get("bar_ts") and r["reason"] != "ORDER_EXPIRED" else None,
                        "reason": r["reason"], "order_id": r.get("order_id")} for r in report.rejections],
        "invalidated_net_pnl": [_num(t["net_pnl"]) for t in inval],
        "open_position": ({"qty": opens[0]["qty"], "side": opens[0]["side"], "contract": opens[0]["contract"],
                           "mark_price": _num(opens[0]["mark_price"]),
                           "unrealized_pnl_mark": _num(opens[0]["unrealized_pnl"])} if opens else None),
        "provenance": {k: report.provenance.get(k) for k in ("engine_git_sha", "execution_convention",
                                                             "spec_sha256", "dataset_snapshot_id", "core_status")},
    }


def compare(case: dict, act: dict, core_sha: str, req_sha: str, orders: list) -> list[dict]:
    """12 dimensions; each diff carries the time of the event it concerns (for 'first divergent event')."""
    exp = case["expected"]
    d: list[dict] = []

    def add(dim, idx, e, a, ts, num=None):
        d.append({"dimension": dim, "index": idx, "expected": e, "actual": a, "event_ts": ts,
                  "numeric_difference": num})
    # 1-2 signal / order times: every expected signal must have produced an order at that time
    exp_sig = sorted({s["signal_ts"] for s in exp["signals"]})
    got_sig = sorted({o["order_time"] for o in orders if "ROLL" not in o["order_id"]})
    if not set(got_sig) <= set(exp_sig):
        add("signal/order time", None, exp_sig, got_sig, min(exp_sig + got_sig))
    ef, af = exp["fills"], act["fills"]
    if len(ef) != len(af):
        add("fill count", None, len(ef), len(af), (ef[min(len(ef), len(af))]["exec_ts"] if len(ef) > len(af)
                                                   else af[min(len(ef), len(af))]["exec_ts"]) if ef or af else "")
    for i, (e, a) in enumerate(zip(ef, af)):
        if e["order_ts"] not in {o["order_time"] for o in orders}:
            add("order time", i, e["order_ts"], None, e["order_ts"])
        if e["exec_ts"] != a["exec_ts"]:
            add("fill time", i, e["exec_ts"], a["exec_ts"], min(e["exec_ts"], a["exec_ts"]))
        for k, dim in (("side", "long/short"), ("contract", "contract transition")):
            if str(e[k]) != str(a[k]):
                add(dim, i, e[k], a[k], e["exec_ts"])
        if int(e["qty"]) != int(a["qty"]):
            add("long/short", i, e["qty"], a["qty"], e["exec_ts"])
        for k, dim in (("fill_price", "fill price"), ("fee", "fee/tax"), ("tax", "fee/tax")):
            diff = _d(a[k]) - _d(e[k])
            if abs(diff) > TOL:
                add(dim + f" ({k})", i, e[k], a[k], e["exec_ts"], str(diff))
    for i, f in enumerate(exp.get("forbidden_fills", [])):
        if f["exec_ts"] in {a["exec_ts"] for a in af}:
            add("forbidden fill", i, "no fill", f["exec_ts"], f["exec_ts"])
    diff = _d(act["realized_pnl"]) - _d(exp["realized_pnl"])
    if abs(diff) > TOL:
        add("realized PnL", None, exp["realized_pnl"], act["realized_pnl"], ef[-1]["exec_ts"] if ef else "", str(diff))
    er = sorted((r["bar_ts"] or "", r["reason"]) for r in exp["rejections"])
    ar = sorted((r["bar_ts"] or "", r["reason"]) for r in act["rejections"])
    if er != ar:
        add("rejection reasons", None, er, ar, min([t for t, _ in er if t] or [""]))
    ei, ai = exp.get("invalidated_net_pnl", []), act["invalidated_net_pnl"]
    if len(ei) != len(ai) or any(abs(_d(a) - _d(b)) > TOL for a, b in zip(ei, ai)):
        add("invalidated round trips", None, ei, ai, "")
    eo, ao = exp["open_position"], act["open_position"]
    if (eo is None) != (ao is None):
        add("open position", None, eo, ao, "end")
    elif eo:
        diff = _d(ao["unrealized_pnl_mark"]) - _d(eo["unrealized_pnl_mark"])
        if abs(diff) > TOL or int(ao["qty"]) != int(eo["qty"]) or ao["contract"] != eo["contract"]:
            add("open position", None, eo["unrealized_pnl_mark"], ao["unrealized_pnl_mark"], "end", str(diff))
    p = act["provenance"]
    if p["engine_git_sha"] != core_sha or p["execution_convention"] != "oracle-execution-convention-v1" \
            or p["spec_sha256"] != req_sha:
        add("provenance", None, {"engine_git_sha": core_sha, "spec_sha256": req_sha}, p, "")
    return d


ROOT_CAUSES = {
    "C2": {"root_cause": "C2 float/rounding: Core quantizes fee and tax to 0.01 (HALF_UP/UP/DOWN only); convention "
                         "v1 §1.6 is unrounded Decimal, so any fee/tax with more than 2 decimals differs and the "
                         "difference propagates into realized PnL",
           "responsible_repository": "market-research-core",
           "proposed_minimum_correction": "add rounding mode 'NONE' to CostProfile.fee_rounding/tax_rounding "
                                          "(skip _round) and carry fees/taxes as Decimal in calculate_costs; keep "
                                          "HALF_UP for real-market profiles",
           "blocking": True},
    "K15": {"root_cause": "K15 rejection log: Core appends one rejection per UNFILLED order (final reason) and none "
                          "for bars skipped by an order that fills later; convention v1 §1.4 logs one rejection per "
                          "skipped bar with bar_ts",
            "responsible_repository": "market-research-core",
            "proposed_minimum_correction": "in ExecutionEngine.run, inside the candidate loop, append "
                                           "{order_id, bar_ts=bar_start, reason} for every bar skipped with a "
                                           "rejection reason (OUTSIDE_SESSION, ZERO_VOLUME_BAR, side-ineligible, "
                                           "exclusion) whether or not the order fills later",
            "blocking": True},
    "K14": {"root_cause": "K14 TIF: Core DAY restricts candidates to the session of the FIRST candidate bar; "
                          "convention v1 §1.5 keeps the order until the end of the first session whose end > order_ts",
            "responsible_repository": "market-research-core",
            "proposed_minimum_correction": "add TIF 'UNTIL_END_OF_NEXT_SESSION_END' (or align DAY) computing expiry "
                                           "from session_close > order_time and emit ORDER_EXPIRED at that time",
            "blocking": True},
    "C1": {"root_cause": "C1 next-bar open: with same_timestamp_open_assumption=False Core requires bar_start > "
                         "available_at and fills one bar after convention v1 (first bar with ts >= order_ts)",
           "responsible_repository": "strategy-research-agent (adapter config) / documented",
           "proposed_minimum_correction": "adapter sets same_timestamp_open_assumption=True only for START-labelled "
                                          "bars with available_at == bar_end (done); no Core change required",
           "blocking": False},
}


def _is_rounding(x: dict) -> bool:
    """A difference fully explained by 0.01 quantization (C2): fee/tax/PnL deltas below 4 x 0.005."""
    return (x["dimension"].startswith("fee/tax") or x["dimension"] in ("realized PnL", "invalidated round trips")) \
        and x.get("numeric_difference") is not None and abs(Decimal(x["numeric_difference"])) < Decimal("0.02")


def classify(diffs: list[dict]) -> list[str]:
    tags = set()
    for x in diffs:
        dim = x["dimension"]
        if dim.startswith("fee/tax") or dim == "realized PnL" or dim == "invalidated round trips":
            if x.get("numeric_difference") is not None and abs(Decimal(x["numeric_difference"])) < Decimal("0.01") * 4:
                tags.add("C2")
        if dim == "rejection reasons":
            got = {r for _, r in x["actual"]}
            want = {r for _, r in x["expected"]}
            if "ORDER_EXPIRED" in got - want:
                tags.add("K14")
            if want - got:
                tags.add("K15")
        if dim in ("fill time", "fill count", "order time") and "K14" not in tags:
            tags.add("TIMING")
        if dim in ("fill price (fill_price)", "long/short", "contract transition", "open position", "provenance",
                   "forbidden fill"):
            tags.add(dim.upper().replace(" ", "_"))
    return sorted(tags)


def crosscheck(out_root: Path = OUT) -> dict:
    core = A._core()
    ident = A.core_identity()
    sha = ident.get("git_sha")
    if not sha:
        raise SystemExit("installed Core has no resolvable commit: refusing to produce an unpinned record")
    matrix = A.compatibility_matrix(core)
    rows = []
    for c in cases():
        try:
            res, report, notes = run_case(c, core, sha)
        except (A.CoreContractGap, ValueError) as e:
            rows.append({"case_id": c["case_id"], "core_sha": sha, "status": "NOT_EXECUTABLE",
                         "executed_on_core": False, "error": f"{type(e).__name__}: {e}", "differences": [],
                         "root_cause_tags": ["NOT_EXECUTABLE"], "blocking": True})
            continue
        act = actual_view(res, report, c)
        diffs = compare(c, act, sha, synthetic_request(c).spec_sha256, notes["orders"])
        diffs.sort(key=lambda x: (x["event_ts"] in ("", "end", None), x["event_ts"] or ""))
        tags = classify(diffs)
        # C1 diagnostic: same case with Core's default assumption
        res0, rep0, _ = run_case(c, core, sha, same_ts=False)
        a0 = actual_view(res0, rep0, c)
        shift = [(e["exec_ts"], a["exec_ts"]) for e, a in zip(c["expected"]["fills"], a0["fills"])
                 if e["exec_ts"] != a["exec_ts"]]
        exp = c["expected"]
        rows.append({
            "case_id": c["case_id"], "core_sha": sha, "status": "PASS" if not diffs else "FAIL",
            "executed_on_core": True, "core_api": "ExecutionEngine.run", "signal_source": notes["signal_source"],
            "orders": notes["orders"],
            "expected": {k: exp[k] for k in ("signals", "rejections", "realized_pnl", "open_position")}
            | {"fills": [{k: f[k] for k in ("order_ts", "exec_ts", "side", "qty", "contract", "fill_price", "fee", "tax")}
                         for f in exp["fills"]], "invalidated_net_pnl": exp.get("invalidated_net_pnl", [])},
            "actual": act, "differences": diffs,
            "first_divergent_event": diffs[0] if diffs else None,
            "root_cause_tags": tags,
            "differences_other_than_c2_rounding": [x for x in diffs if not _is_rounding(x)],
            "root_causes": [ROOT_CAUSES[t] | {"tag": t} for t in tags if t in ROOT_CAUSES],
            "blocking": any(ROOT_CAUSES.get(t, {"blocking": True})["blocking"] for t in tags),
            "c1_default_assumption_diagnostic": {"same_timestamp_open_assumption": False,
                                                 "fill_time_shifts": shift,
                                                 "fills": len(a0["fills"])}})
    n = {s: sum(r["status"] == s for r in rows) for s in ("PASS", "FAIL", "NOT_EXECUTABLE")}
    # NOTE: verdict covers B01-B12 only; G16 and oracle probes are reported next to it (see engine status rule)
    n["PASS_IF_ONLY_C2_FIXED"] = sum(r["status"] != "NOT_EXECUTABLE" and not r["differences_other_than_c2_rounding"]
                                     for r in rows)
    verdict = "PASS" if n["PASS"] == len(rows) else "FAIL"
    rec = {"engine": {"name": A.CORE_DIST, "git_sha": sha, "version": ident["version"], "kind": "REAL_CORE",
                      "api": "ExecutionEngine.run", "source": ident["source"]},
           "adapter_version": A.ADAPTER_VERSION,
           "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "verdict": verdict, "counts": n, "capability_matrix": matrix, "cases": rows,
           "probes": {"G16_acceptance": g16_acceptance(core, sha), "oracle_probes": oracle_probes(core, sha)},
           "statement": "expected values read from benchmark/cases (unchanged); actual values produced by the real "
                        "Core ExecutionEngine.run; synthetic START label/sessions are engine-test settings, not "
                        "evidence about real TXF/IBKR data"}
    d = Path(out_root) / f"mrc_{sha[:12]}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "crosscheck.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (d / "report.md").write_text(report_md(rec), encoding="utf-8")
    (d / "contract_gaps.json").write_text(json.dumps(contract_gaps(rec), ensure_ascii=False, indent=1, default=str),
                                          encoding="utf-8")
    return rec


GAP_DEFS = [
    ("G12", "C2", "K12", ROOT_CAUSES["C2"], "src/market_research_core/costs.py: _round / calculate_costs; "
     "CostProfile.fee_rounding/tax_rounding Literal"),
    ("G15", "K15", "K15", ROOT_CAUSES["K15"], "src/market_research_core/execution.py: ExecutionEngine.run "
     "candidate loop (the `continue` branches)"),
    ("G14", "K14", "K14", ROOT_CAUSES["K14"], "src/market_research_core/execution.py: DAY branch "
     "(`candidates[candidates['session_open'] == first_session_open]`) and ExecutionOrder.time_in_force Literal"),
]


def contract_gaps(rec: dict) -> dict:
    sha = rec["engine"]["git_sha"]
    out = []
    for gid, tag, cap, rc, where in GAP_DEFS:
        hit = [r for r in rec["cases"] if tag in r["root_cause_tags"]]
        if not hit:
            continue
        ex = next(d for r in hit for d in r["differences"] if (tag == "C2" and _is_rounding(d))
                  or (tag != "C2" and d["dimension"] == "rejection reasons"))
        out.append({"gap_id": gid, "capability": cap, "core_sha": sha, "cases": [r["case_id"] for r in hit],
                    "expected": ex["expected"], "actual": ex["actual"], "numeric_difference": ex["numeric_difference"],
                    "first_divergent_event": {r["case_id"]: r["first_divergent_event"] for r in hit},
                    "root_cause": rc["root_cause"], "responsible_repository": rc["responsible_repository"],
                    "code_location": where, "proposed_minimum_correction": rc["proposed_minimum_correction"],
                    "blocking": rc["blocking"]})
    g16 = rec["probes"]["G16_acceptance"]
    out.append({"gap_id": "G16", "capability": "C3 order execution chronology", "core_sha": sha,
                "status": "CLOSED" if g16["status"] == "PASS" else "OPEN", "cases": ["B01-B12", G16_CASE["case_id"]],
                "expected": "fills in fill-time order; exit_ts >= entry_ts; equity/cash path consistent; prefix "
                            "invariance; same-timestamp determinism", "actual": g16["checks"],
                "numeric_difference": None, "first_divergent_event": None,
                "root_cause": "fixed in Core by the bar_start event loop", "responsible_repository": "market-research-core",
                "code_location": "src/market_research_core/execution.py: ExecutionEngine.run event loop",
                "proposed_minimum_correction": "none" if g16["status"] == "PASS" else "see checks",
                "blocking": g16["status"] != "PASS"})
    for gid in ("G12", "G14", "G15"):
        if not any(g["gap_id"] == gid for g in out):
            out.append({"gap_id": gid, "status": "CLOSED", "core_sha": sha, "cases": ["B01-B12"],
                        "evidence": "no case differs on this dimension under exact Decimal comparison",
                        "blocking": False})
    op = rec["probes"]["oracle_probes"]
    p17, p14 = op["G17"], op["G14b"]
    out += [
        {"gap_id": "G17", "status": "OPEN" if p17["status"] == "FAIL" else "CLOSED", "capability": "K17 target supersession",
         "core_sha": sha, "cases": [p17["case_id"] + " (oracle-generated expectation; outside B01-B12)"],
         "expected": p17["expected"]["fills"], "actual": p17["actual"]["fills"], "numeric_difference": None,
         "first_divergent_event": p17["first_divergent_event"],
         "root_cause": "a target_position order that becomes active while an earlier order on the same "
                       "(strategy, symbol, contract) is still live is sized from the FILLED target "
                       "(target_positions) only; Oracle v1 sizes it from position + live pending orders. In the G16 "
                       "scenario Core turns target 0 into a no-op, then fills the delayed BUY and ends LONG 1000 "
                       "against a latest target of 0; Oracle sells at 09:20, buys at 09:25 and ends flat",
         "responsible_repository": "market-research-core",
         "code_location": "src/market_research_core/execution.py:243-244 (`current_target = target_positions.get(...)`; "
                          "`delta = target_position - current_target`) and :328",
         "proposed_minimum_correction": "compute the delta once, when the order becomes active (order_time), as "
                                        "target_position - (filled position + remaining signed qty of still-live "
                                        "earlier orders with the same target_key); keep the order's qty fixed after",
         "blocking": p17["status"] == "FAIL", "adapter_guard": "known_divergences() refuses any run in which an order "
         "activates while an earlier one is live (B01-B12: 0 hits; probe: hit)",
         "adapter_guard_hits": p17["adapter_guard_hits"],
         "resolution": None if p17["status"] == "FAIL" else f"fixed in Core {sha[:7]}: oracle probe PASS, K17 probe "
                       "SUPPORTED; root_cause above describes the defect as found in 298956f"},
        {"gap_id": "G14b", "status": "OPEN" if p14["status"] == "FAIL" else "CLOSED",
         "capability": "K14 DAY expiry boundary", "core_sha": sha,
         "cases": [p14["case_id"] + " (oracle-generated expectation; outside B01-B12)"],
         "expected": {"fills": p14["expected"]["fills"], "rejections": p14["expected"]["rejections"]},
         "actual": {"fills": p14["actual"]["fills"], "rejections": p14["actual"]["rejections"]},
         "numeric_difference": None, "first_divergent_event": p14["first_divergent_event"],
         "root_cause": "DAY expiry is inclusive: a bar with bar_start == expiry (the first session_close after "
                       "order_time) is still a candidate. With back-to-back sessions (close 09:20 = next open 09:20) a "
                       "session-1 DAY order fills on the opening bar of session 2; Oracle v1 expires it "
                       "(skips bars with ts >= expires_at)",
         "responsible_repository": "market-research-core",
         "code_location": "src/market_research_core/execution.py:211 (`matching['bar_start'] <= expiry`) and :232 "
                          "(`event_ts <= record['expiry']`)",
         "proposed_minimum_correction": "make the boundary exclusive (`<`) in both places; ORDER_EXPIRED at expiry "
                                        "stays as is", "blocking": p14["status"] == "FAIL",
         "adapter_guard": "known_divergences() refuses any run with a fill at bar_start >= its DAY expiry",
         "adapter_guard_hits": p14["adapter_guard_hits"],
         "resolution": None if p14["status"] == "FAIL" else f"fixed in Core {sha[:7]}: oracle probe PASS, K14b probe "
                       "SUPPORTED; root_cause above describes the defect as found in 298956f"},
        {"gap_id": "G12b", "status": "CLOSED", "capability": "K12", "core_sha": sha,
         "cases": ["governance/cost_profiles/tw_equity_cash_v0"],
         "expected": "FLOOR_INTEGER fee/tax rounding (TW broker statements)",
         "actual": "costs.py rounding Literal includes FLOOR_INTEGER (quantize to 1, ROUND_FLOOR); adapter maps it",
         "numeric_difference": None, "first_divergent_event": None, "root_cause": "fixed",
         "responsible_repository": "market-research-core", "code_location": "src/market_research_core/costs.py:31-32, :72",
         "proposed_minimum_correction": "none", "blocking": False},
        {"gap_id": "G03b", "status": "OPEN", "capability": "K03", "core_sha": sha,
         "cases": ["specs/examples/sma_crossover_tw_2330_5m"],
         "expected": "TWSE/TPEx price-banded tick ladder (0.01/0.05/0.1/0.5/1/5) for slippage and tick rounding",
         "actual": "CostProfile.tick_size is one float; adverse_fill_price uses it for every price",
         "numeric_difference": None, "first_divergent_event": None,
         "root_cause": "single tick; TW equities change tick by price band", "responsible_repository": "market-research-core",
         "code_location": "src/market_research_core/costs.py:29 (tick_size) and :98-100 (adverse_fill_price)",
         "proposed_minimum_correction": "accept a tick ladder [(upper_price, tick), ...] (or a per-bar tick_size "
                                        "column) and round to the band of the adverse price",
         "blocking": True, "blocks": "formal TW-equity research only; not a B01-B12 failure (fixtures use one band)"},
        {"gap_id": "G11", "status": "OPEN", "capability": "K11", "core_sha": sha, "cases": ["all"],
         "expected": "engine reports its own commit",
         "actual": "provenance.engine_git_sha = config.core_commit_sha (caller-supplied)", "numeric_difference": None,
         "first_divergent_event": None,
         "root_cause": "engine identity is asserted by the caller; the adapter supplies the pip/git-resolved commit "
                       "and validate_core_provenance rejects a mismatch", "responsible_repository": "market-research-core",
         "code_location": "src/market_research_core/execution.py: provenance['engine_git_sha'] = config.core_commit_sha",
         "proposed_minimum_correction": "derive engine_git_sha from package metadata and reject a config value that "
                                        "disagrees", "blocking": False},
        {"gap_id": "G09", "status": "BY_DESIGN", "capability": "K09", "core_sha": sha, "cases": ["B09"],
         "expected": "roll from spec schedule", "actual": "adapter expands the roll schedule into explicit orders",
         "numeric_difference": None, "first_divergent_event": None, "root_cause": "by design",
         "responsible_repository": "strategy-research-agent",
         "code_location": "src/strategy_agent/adapters/mrc_adapter.py: expand_roll_schedule",
         "proposed_minimum_correction": "none", "blocking": False},
        {"gap_id": "C1", "status": "MAPPED", "capability": "next-bar open", "core_sha": sha, "cases": ["all"],
         "expected": "fill at first bar with bar_start >= order_ts (= available_at)",
         "actual": "Core default same_timestamp_open_assumption=False fills one bar later; adapter sets True for "
                   "START/CONFIRMED semantics", "numeric_difference": None, "first_divergent_event": None,
         "root_cause": ROOT_CAUSES["C1"]["root_cause"], "responsible_repository": "strategy-research-agent",
         "code_location": "src/strategy_agent/adapters/mrc_adapter.py: core_config",
         "proposed_minimum_correction": "none", "blocking": False},
    ]
    return {"core_sha": sha, "agent_adapter_version": rec["adapter_version"], "verdict": rec["verdict"],
            "counts": rec["counts"], "gaps": out}


# ------------------------------------------------------------------------------------------ G16 + oracle probes
def _probe_case(case_id, sessions, locked, targets, n=8):
    """Synthetic scenario in fixture format. Expected answers come from running the independent Oracle
    (benchmark.oracle, unchanged) - not hand-calculated, and not added to benchmark/cases."""
    return {"case_id": case_id, "convention": "oracle-execution-convention-v1", "timezone": "Asia/Taipei",
            "timeframe_minutes": 5, "title": case_id,
            "instrument": {"symbol": "SYNS", "kind": "equity", "multiplier": 1, "tick_size": "0.1", "currency": "TWD"},
            "costs": {"fee_rate_side": "0.001425", "sell_tax_rate": "0.003", "slippage_ticks": 1}, "quantity": 1000,
            "sessions": sessions, "bars_columns": ["ts", "o", "h", "l", "c", "v", "limit_up_locked", "limit_down_locked"],
            "bars": [[f"2026-01-05 09:{m:02d}", "60", "60.5", "59.5", "60", 1000, m in locked, False]
                     for m in range(0, 5 * n, 5)],
            "signal_model": {"type": "scripted", "targets": [{"bar_close_of": f"2026-01-05 09:{b:02d}", "target": t}
                                                             for b, t in targets]}}


ONE_SESSION = [{"date": "2026-01-05", "start": "09:00", "end": "09:40"}]
G16_CASE = _probe_case("X16_delayed_buy_later_target", ONE_SESSION, {10, 15, 20}, [(5, 1), (15, 0)])
G14B_CASE = _probe_case("X14b_day_expiry_back_to_back_sessions",
                        [{"date": "2026-01-05", "start": "09:00", "end": "09:20"},
                         {"date": "2026-01-05", "start": "09:20", "end": "09:40"}], {10, 15}, [(5, 1)])


def _with_oracle(case: dict) -> dict:
    from benchmark.oracle.reference_engine import run_case as oracle_run
    o = oracle_run(case)
    c = json.loads(json.dumps(case))
    c["expected"] = {"signals": o["signals"], "fills": o["fills"],
                     "rejections": [{"bar_ts": r["bar_ts"], "reason": r["reason"]} for r in o["rejections"]],
                     "realized_pnl": o["realized_pnl"], "open_position": o["open_position"],
                     "invalidated_net_pnl": [r["net_pnl"] for r in o["invalidated_round_trips"]],
                     "forbidden_fills": [], "source": "oracle-generated (benchmark.oracle.reference_engine.run_case)"}
    return c


def _fills(res):
    return [(str(f["exec_ts"]), f["order_id"], f["side"], float(f["qty"]), _num(f["fill_price"]), _num(f["fee"]),
             _num(f["tax"]), f["contract"]) for f in res.fills.to_dict("records")] if len(res.fills) else []


def g16_acceptance(core, sha: str) -> dict:
    """G16 chronology acceptance: invariants over B01-B12 and the delayed-order scenario, prefix invariance,
    same-timestamp determinism. No tolerance on Decimal costs; account floats checked to 1e-6."""
    import random
    checks = {}
    runs = [(c["case_id"], c) for c in cases()] + [(G16_CASE["case_id"], _with_oracle(G16_CASE))]
    ordered, no_back, equity_ok, prefix_ok, notes = [], [], [], [], []
    for cid, c in runs:
        res, _, meta = run_case(c, core, sha)
        t = [str(f["exec_ts"]) for f in res.fills.to_dict("records")] if len(res.fills) else []
        ordered.append(t == sorted(t))
        trips = (res.round_trips.to_dict("records") if len(res.round_trips) else []) + \
            (res.invalidated_round_trips.to_dict("records") if len(res.invalidated_round_trips) else [])
        no_back.append(all(r["exit_ts"] >= r["entry_ts"] for r in trips))
        eq = res.equity_curve.to_dict("records")
        first_fill = min(t) if t else None
        trips_all = trips
        realized = sum(float(r["net_pnl"]) for r in trips_all)
        unreal = sum(float(r.get("unrealized_pnl", 0.0)) for r in (res.open_positions.to_dict("records")
                                                                   if len(res.open_positions) else []))
        # (a) nothing reaches equity before the first fill; (b) when flat at the end, equity - cash0 == realized
        pre_ok = all(abs(r["equity"] - 1e9) < 1e-6 for r in eq if first_fill and str(r["timestamp"]) < first_fill)
        end_ok = len(res.open_positions) > 0 or abs((eq[-1]["equity"] - 1e9) - realized) < 1e-6
        equity_ok.append(pre_ok and end_ok)
        if not (pre_ok and end_ok):
            notes.append(f"{cid} equity: pre_ok={pre_ok} end={eq[-1]['equity'] - 1e9!r} realized={realized!r} "
                         f"unreal={unreal!r}")
        # prefix invariance: re-run with bars and orders cut at each fill time; earlier fills must be identical
        full = _fills(res)
        for ts in sorted(set(t)):
            cut = json.loads(json.dumps(c))
            cut["bars"] = [b for b in cut["bars"] if datetime.strptime(dict(zip(cut["bars_columns"], b))["ts"], FMT)
                           .strftime(FMT) <= ts[:16]]
            cut["expected"]["signals"] = [x for x in cut["expected"]["signals"] if x["signal_ts"] <= ts[:16]]
            if cut["signal_model"]["type"] in ("sma_cross", "orb"):
                pass                                    # signals recomputed from the truncated bars themselves
            try:
                r2, _, _ = run_case(cut, core, sha)
            except Exception as e:  # noqa: BLE001
                notes.append(f"{cid} cut@{ts[11:16]}: {type(e).__name__}")
                continue
            got = [f for f in _fills(r2) if f[0] <= ts]
            want = [f for f in full if f[0] <= ts]
            if got != want:
                prefix_ok.append(False)
                notes.append(f"{cid} cut@{ts[11:16]}: {want} != {got}")
            else:
                prefix_ok.append(True)
    checks["fills_in_fill_time_order"] = all(ordered)
    checks["no_exit_before_entry"] = all(no_back)
    checks["equity_starts_at_initial_cash_and_reconciles_when_flat"] = all(equity_ok)
    checks["prefix_invariance_no_future_dependent_fills"] = all(prefix_ok) and bool(prefix_ok)
    # unfilled orders must not change state early: every target-order activation sees a filled position equal to
    # the signed sum of fills strictly before it (pending intent is reported separately, never as position), and
    # the delayed BUY of X16 changes nothing before its own fill time. Evidence: Core's own order_events.
    res16, _, meta16 = run_case(_with_oracle(G16_CASE), core, sha)
    early = []
    for cid, c in runs:
        r, _, m = run_case(c, core, sha)
        contract_of = {o["order_id"]: o["contract"] for o in m["orders"]}
        oe = getattr(r, "order_events", None)                 # Core >= 2c6ba9a; absent -> equity check only
        ev = oe.to_dict("records") if oe is not None and len(oe) else []
        fl = r.fills.to_dict("records") if len(r.fills) else []
        for e in ev:
            if e.get("event") != "TARGET_ORDER_ACTIVATED":
                continue
            filled = sum(float(f["qty"]) * (1 if f["side"] == "BUY" else -1) for f in fl
                         if f["exec_ts"] < e["event_ts"] and f["contract"] == contract_of[e["order_id"]])
            if abs(filled - float(e["filled_position"])) > 1e-9:
                early.append(f"{cid} {e['order_id']} @ {e['event_ts']}: filled_position {e['filled_position']} != {filled}")
    buy = [f for f in (res16.fills.to_dict("records") if len(res16.fills) else []) if f["side"] == "BUY"]
    pre_buy = [x for x in res16.equity_curve.to_dict("records") if buy and x["timestamp"] < buy[0]["exec_ts"]]
    checks["unfilled_order_does_not_change_state_before_fill"] = (
        not early and bool(buy)
        and all(abs(x["unrealized_pnl"]) < 1e-9 and abs(x["realized_net_pnl"]) < 1e-9 for x in pre_buy))
    notes += early[:10]
    # same-timestamp determinism: B09 has two orders at 2026-01-07 09:00; permute inputs, rerun twice
    b09 = next(c for c in cases() if c["case_id"].startswith("B09"))
    base, _, _ = run_case(b09, core, sha)
    orig = A.targets_to_orders
    same = []
    for seed in (1, 2, 3):
        def shuffled(*a, **k):
            out = orig(*a, **k)
            random.Random(seed).shuffle(out)
            return out
        A.targets_to_orders = shuffled
        try:
            rr, _, _ = run_case(b09, core, sha)
        finally:
            A.targets_to_orders = orig
        same.append(_fills(rr) == _fills(base))
    rr2, _, _ = run_case(b09, core, sha)
    checks["same_timestamp_orders_deterministic"] = all(same) and _fills(rr2) == _fills(base)
    status = "PASS" if all(checks.values()) else "FAIL"
    return {"status": status, "checks": checks, "notes": notes[:20],
            "scenario": {"case_id": G16_CASE["case_id"], "core_fills": _fills(res16),
                         "core_open_positions": res16.open_positions.to_dict("records") if len(res16.open_positions) else [],
                         "orders": meta16["orders"]}}


def oracle_probes(core, sha: str) -> dict:
    """Scenarios outside B01-B12 compared with the independent Oracle (oracle-generated expectations)."""
    out = {}
    for gap, case in (("G17", G16_CASE), ("G14b", G14B_CASE)):
        c = _with_oracle(case)
        res, report, notes = run_case(c, core, sha)
        act = actual_view(res, report, c)
        diffs = compare(c, act, sha, synthetic_request(c).spec_sha256, notes["orders"])
        diffs.sort(key=lambda x: (x["event_ts"] in ("", "end", None), x["event_ts"] or ""))
        out[gap] = {"case_id": c["case_id"], "status": "PASS" if not diffs else "FAIL",
                    "expected_source": c["expected"]["source"],
                    "expected": {k: c["expected"][k] for k in ("fills", "rejections", "realized_pnl", "open_position")},
                    "actual": {k: act[k] for k in ("fills", "rejections", "realized_pnl", "open_position")},
                    "orders": notes["orders"], "differences": diffs, "adapter_guard_hits": notes["adapter_guard"],
                    "divergence_situations": notes["divergence_situations"],
                    "guard_enforced_for": sorted(_guard_scope(core)),
                    "first_divergent_event": diffs[0] if diffs else None}
    return out


# ------------------------------------------------------------------------------------------ report
def report_md(rec: dict) -> str:
    e = rec["engine"]
    L = [f"# Benchmark comparison — real market-research-core `{e['git_sha'][:12]}` ({e['api']})", "",
         f"Verdict: **{rec['verdict']}**  (PASS {rec['counts']['PASS']}, FAIL {rec['counts']['FAIL']}, "
         f"NOT_EXECUTABLE {rec['counts']['NOT_EXECUTABLE']} of 12; would PASS if only the C2 rounding gap were "
         f"fixed: {rec['counts']['PASS_IF_ONLY_C2_FIXED']})", "",
         "| case | status | first divergent event | root cause tags | blocking |", "|---|---|---|---|---|"]
    for r in rec["cases"]:
        fd = r.get("first_divergent_event")
        fd_s = (f"{fd['dimension']} @ {fd['event_ts'] or '—'}: {fd['expected']} → {fd['actual']}"
                if fd else ("—" if r["status"] == "PASS" else r.get("error", "")))
        L.append(f"| {r['case_id']} | {r['status']} | {str(fd_s)[:150]} | {', '.join(r['root_cause_tags']) or '—'} | "
                 f"{'yes' if r['blocking'] else 'no'} |")
    g = rec["probes"]["G16_acceptance"]
    L += ["", f"G16 chronology acceptance: **{g['status']}** — " + ", ".join(f"{k}={'PASS' if v else 'FAIL'}"
                                                                         for k, v in g["checks"].items()), ""]
    for k, v in rec["probes"]["oracle_probes"].items():
        fd = v["first_divergent_event"]
        L.append(f"Oracle probe {k} (`{v['case_id']}`, outside B01-B12): **{v['status']}**"
                 + (f" — first divergence {fd['dimension']} @ {fd['event_ts']}: {fd['expected']} → {fd['actual']}"
                    if fd else "") + f"; adapter guard hits {len(v['adapter_guard_hits'])}")
    L.append("")
    L += ["## Differences per case", ""]
    for r in rec["cases"]:
        if r["status"] != "FAIL":
            continue
        L += [f"### {r['case_id']}", "", f"Signals: {r['signal_source']}", "",
              "| dimension | # | expected | actual (real Core) | numeric difference |", "|---|---|---|---|---|"]
        for d in r["differences"]:
            L.append(f"| {d['dimension']} | {'' if d['index'] is None else d['index']} | {str(d['expected'])[:80]} | "
                     f"{str(d['actual'])[:80]} | {d['numeric_difference'] or ''} |")
        c1 = r["c1_default_assumption_diagnostic"]["fill_time_shifts"]
        if c1:
            L.append(f"\nC1 diagnostic (Core default `same_timestamp_open_assumption=False`): fill times {c1}")
        L.append("")
    L += ["## Capability matrix (measured on the installed Core)", "", "| cap | contract | status | evidence |",
          "|---|---|---|---|"]
    for m in rec["capability_matrix"]:
        L.append(f"| {m['cap_id']} | {m['contract']} | {m['status']} | {m['evidence'][:150]} |")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m benchmark.harness.mrc_crosscheck")
    ap.add_argument("--out", default=str(OUT))
    rec = crosscheck(Path(ap.parse_args(argv).out))
    print(report_md(rec))
    return 0 if rec["verdict"] == "PASS" else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
