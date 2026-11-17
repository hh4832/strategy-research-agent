"""Phase 5-C1 C3 Trade Integrity remediation: independent, fail-closed ledger reconciliation.

SYNTHETIC_FIXTURE: every ledger here is a small hand-built synthetic book (no market data). Rows follow the
market-research-core@50ea3bf archive columns; trades / open positions / metrics are derived from the synthetic
fills with Core's FIFO rule so that only the mutation under test differs from a consistent book.
"""
import copy
import hashlib
import json
import math
import shutil
from pathlib import Path

import pytest

from strategy_agent.evaluation import c3_reconciliation as R
from strategy_agent.evaluation import phase4c as C

D = "2024-06-03T"
E = "-04:00"
SCEN = C.SCENARIOS


def ts(hm, day=D):
    return f"{day}{hm}:00{E}"


def build(decisions, fills, rejections=(), symbol="TSM"):
    """decisions: [(hm, target, reason)]; fills: [(order_id, hm, side, qty, price, cost)];
    rejections: [(order_id, bar_hm|None, reason, expiry_hm|None)] -> one consistent scenario."""
    dec = [{"decision_ts": ts(h), "target": str(t), "reason": r} for h, t, r in decisions]
    orders = {o["order_id"]: o for o in R.order_universe(dec, symbol)}
    fl = []
    for oid, h, side, q, px, cost in fills:
        o = orders[oid]
        fl.append({"order_id": oid, "symbol": symbol, "contract": symbol, "side": side, "qty": str(q),
                   "exec_ts": ts(h), "fill_price": str(px), "explicit_cost": str(cost),
                   "order_time": o["order_time"].isoformat(), "signal_available_at": o["order_time"].isoformat()})
    rj = [{"order_id": oid, "bar_ts": ts(b) if b else "NaT", "reason": code,
           "detail": "", "expiry_ts": ts(x) if x else "NaT"} for oid, b, code, x in rejections]
    lots, trades = [], []
    for f in sorted(fl, key=lambda f: f["exec_ts"]):
        q, sgn = float(f["qty"]), 1 if f["side"] == "BUY" else -1
        rem = q
        while rem > 1e-12 and lots and lots[0]["s"] * sgn < 0:
            lot = lots.pop(0)
            assert lot["q"] == rem, "builder supports whole-lot closes only"
            gross = (float(f["fill_price"]) - lot["px"]) * rem * lot["s"]
            cost = lot["cost"] + float(f["explicit_cost"])
            trades.append({"symbol": symbol, "contract": symbol, "side": "LONG" if lot["s"] > 0 else "SHORT",
                           "qty": str(rem), "entry_ts": lot["ts"], "exit_ts": f["exec_ts"], "entry_price": str(lot["px"]),
                           "exit_price": f["fill_price"], "gross_pnl": str(gross), "net_pnl": str(gross - cost),
                           "invalidated": "False"})
            rem = 0
        if rem > 1e-12:
            lots.append({"s": sgn, "q": rem, "px": float(f["fill_price"]), "ts": f["exec_ts"],
                         "cost": float(f["explicit_cost"])})
    positions = [{"symbol": symbol, "contract": symbol, "side": "LONG" if l["s"] > 0 else "SHORT", "qty": l["q"],
                  "entry_ts": l["ts"], "entry_price": l["px"]} for l in lots]
    metrics = {"fills": len(fl), "rejections": len(rj),
               "trades": {"net_pnl": math.fsum(float(t["net_pnl"]) for t in trades)},
               "m3a4_checks": {"K00_ledger_is_fills": "PASS",
                               "K03_round_trip_costs_equal_ledger": "NOT_APPLICABLE_OPEN_POSITION" if lots else "PASS",
                               "T01_no_lookahead": "PASS"}}
    if lots:
        metrics["open_position_at_end"] = {"positions": positions}
    return {"trades": trades, "fills": fl, "rejections": rj, "decisions": dec, "metrics": metrics,
            "orders_submitted": len(orders), "symbol": symbol}


def run(b, **kw):
    b = copy.deepcopy(b)
    return R.reconcile_scenario(trades=b["trades"], fills=b["fills"], rejections=b["rejections"],
                                decisions=b["decisions"], metrics=b["metrics"], orders_submitted=b["orders_submitted"],
                                symbol=b["symbol"], tol=C.TOL, **kw)


TEMP_OK = {"status": "PASS"}


def has(res, prefix):
    return any(p.startswith(prefix) for p in res["problems"])


# --------------------------------------------------------------------------------------------- books
CLOSED = build([("10:00", 1, "CROSS_ABOVE"), ("11:00", 0, "CROSS_BELOW"), ("12:00", 1, "CROSS_ABOVE"),
                ("13:00", 0, "CROSS_BELOW")],
               [("0000-CROSS_ABOVE", "10:14", "BUY", 100, 170.0, 1.02), ("0001-CROSS_BELOW", "11:14", "SELL", 100, 171.0, 1.53),
                ("0002-CROSS_ABOVE", "12:14", "BUY", 100, 170.5, 1.02), ("0003-CROSS_BELOW", "13:14", "SELL", 100, 170.0, 1.52)])

# entry -> exit, then a CROSS_ABOVE whose candidates are not eligible (expires) -> the next CROSS_BELOW is a NOOP,
# then a final CROSS_ABOVE that fills and stays open at the end of data (the Phase 5-C1 BASE shape)
def open_book():
    d2 = "2024-06-04T"
    dec = [{"decision_ts": ts("10:00"), "target": "1", "reason": "CROSS_ABOVE"},
           {"decision_ts": ts("11:00"), "target": "0", "reason": "CROSS_BELOW"},
           {"decision_ts": ts("15:30"), "target": "1", "reason": "CROSS_ABOVE"},      # expires (closing bars)
           {"decision_ts": ts("10:00", d2), "target": "0", "reason": "CROSS_BELOW"},  # NOOP: already flat
           {"decision_ts": ts("11:00", d2), "target": "1", "reason": "CROSS_ABOVE"}]  # fills, open at end
    b = build([("10:00", 1, "CROSS_ABOVE"), ("11:00", 0, "CROSS_BELOW")],
              [("0000-CROSS_ABOVE", "10:14", "BUY", 100, 170.0, 1.02),
               ("0001-CROSS_BELOW", "11:14", "SELL", 100, 171.0, 1.53)])
    b["decisions"] = dec
    b["orders_submitted"] = 5
    b["fills"].append({"order_id": "0004-CROSS_ABOVE", "symbol": "TSM", "contract": "TSM", "side": "BUY", "qty": "100.0",
                       "exec_ts": ts("11:14", d2), "fill_price": "172.0", "explicit_cost": "1.02",
                       "order_time": ts("11:00", d2), "signal_available_at": ts("11:00", d2)})
    b["rejections"] = [{"order_id": "0002-CROSS_ABOVE", "bar_ts": ts("15:44"), "reason": "BAR_NOT_ELIGIBLE", "detail": "",
                        "expiry_ts": "NaT"},
                       {"order_id": "0002-CROSS_ABOVE", "bar_ts": "NaT", "reason": "ORDER_EXPIRED", "detail": "",
                        "expiry_ts": ts("16:00")}]
    b["metrics"]["fills"], b["metrics"]["rejections"] = 3, 2
    b["metrics"]["open_position_at_end"] = {"positions": [{"symbol": "TSM", "contract": "TSM", "side": "LONG",
                                                           "qty": 100.0, "entry_ts": ts("11:14", d2),
                                                           "entry_price": 172.0}]}
    b["metrics"]["m3a4_checks"]["K03_round_trip_costs_equal_ledger"] = "NOT_APPLICABLE_OPEN_POSITION"
    return b


OPEN = open_book()


def multi_open_book():
    """Core-consistent two-lot book: BUY pending, SELL activated against the pending BUY (delta -100) and then
    never executable (SELL_NOT_ELIGIBLE -> ORDER_EXPIRED), a second BUY sized from filled 0 + pending (+100-100);
    both BUYs fill on the same bar -> two open 100-share lots at the end of data."""
    b = build([("10:00", 1, "CROSS_ABOVE"), ("10:15", 0, "CROSS_BELOW"), ("10:30", 1, "CROSS_ABOVE")],
              [("0000-CROSS_ABOVE", "10:44", "BUY", 100, 170.0, 1.02), ("0002-CROSS_ABOVE", "10:44", "BUY", 100, 170.0, 1.02)],
              [("0000-CROSS_ABOVE", "10:14", "ZERO_VOLUME_BAR", None), ("0000-CROSS_ABOVE", "10:29", "ZERO_VOLUME_BAR", None),
               ("0001-CROSS_BELOW", "10:29", "SELL_NOT_ELIGIBLE", None), ("0001-CROSS_BELOW", "10:44", "SELL_NOT_ELIGIBLE", None),
               ("0001-CROSS_BELOW", None, "ORDER_EXPIRED", "16:00")])
    return b


MULTI = multi_open_book()


# --------------------------------------------------------------------------------------------- 1-3 PASS paths
def test_01_complete_round_trips_pass():
    r = run(CLOSED)
    assert r["status"] == "PASS", r["problems"]
    assert r["trades_matched_to_entry_and_exit_fills"] == 2 and r["open_position_fills"] == 0
    assert r["order_classification"] == {"FILLED": 4} and r["unexplained_order_ids"] == []


def test_02_one_legal_open_position_passes_with_cost_identity_and_verified_noop():
    r = run(OPEN)
    assert r["status"] == "PASS", r["problems"]
    assert r["fills"] == 3 and r["closed_trade_fills"] == 2 and r["open_position_fills"] == 1
    assert r["open_position_fill_ids"] == ["0004-CROSS_ABOVE@" + ts("11:14", "2024-06-04T")]
    assert math.isclose(r["open_position_entry_cost"], 1.02) and abs(r["cost_residual"]) < 1e-9
    assert math.isclose(r["fill_ledger_explicit_costs"] - r["allocated_trade_costs"], 1.02)
    assert r["verified_noop_order_ids"] == ["0003-CROSS_BELOW"]
    assert r["noop_evidence"]["basis"] == "STRICT_REPLAY_OF_CORE_ACTIVATION_RULE"
    assert r["terminal_rejections"] == ["0002-CROSS_ABOVE"] and r["orders_without_fill_or_rejection_record"] == 1


def test_03_multiple_open_positions_reconciled_one_by_one_pass():
    r = run(MULTI)
    assert r["status"] == "PASS", r["problems"]
    assert r["open_positions_reported"] == r["open_positions_verified"] == 2 and r["open_position_fills"] == 2
    assert r["temporary_rejection_then_fill_ids"] == ["0000-CROSS_ABOVE"]
    assert r["by_class_ids"]["EXPIRED"] == ["0001-CROSS_BELOW"]


# --------------------------------------------------------------------------------------------- 4-8 ledger FAIL
def test_04_orphan_fill_fails():
    b = copy.deepcopy(OPEN)
    del b["metrics"]["open_position_at_end"]                       # the open entry fill is now unexplained
    r = run(b)
    assert r["status"] == "FAIL" and has(r, "OPEN_POSITION_EVIDENCE_MISSING") and has(r, "ORPHAN_FILL:0004-CROSS_ABOVE")
    b = copy.deepcopy(CLOSED)                                      # extra fill that no trade or position uses
    extra = dict(b["fills"][0], order_id="0001-CROSS_BELOW", exec_ts=ts("11:20"))
    b["fills"].append(extra)
    assert run(b)["status"] == "FAIL"


def test_05_duplicate_fill_fails():
    b = copy.deepcopy(CLOSED)
    b["fills"].append(dict(b["fills"][0]))
    b["metrics"]["fills"] += 1
    r = run(b)
    assert r["status"] == "FAIL" and "DUPLICATE_FILL" in r["problems"] and "ORDER_FILLED_TWICE" in r["problems"]


def test_06_duplicate_trade_fails():
    b = copy.deepcopy(CLOSED)
    b["trades"].append(dict(b["trades"][0]))
    r = run(b)
    assert r["status"] == "FAIL" and "DUPLICATE_TRADE" in r["problems"]


def test_07_missing_open_position_cost_fails():
    b = copy.deepcopy(OPEN)
    b["fills"][-1]["explicit_cost"] = ""                           # open entry cost absent from the fill ledger
    r = run(b)
    assert r["status"] == "FAIL" and has(r, "MALFORMED_FILL")
    b = copy.deepcopy(OPEN)                                        # ...or the open position is not reported at all
    b["metrics"]["open_position_at_end"]["positions"] = []
    r = run(b)
    assert r["status"] == "FAIL" and has(r, "ORPHAN_FILL") and has(r, "OPEN_POSITION_NET_MISMATCH")


def test_08_open_position_cost_double_counted_fails():
    b = copy.deepcopy(OPEN)
    t = b["trades"][0]                                             # open entry cost also charged to a closed trade
    t["net_pnl"] = str(float(t["net_pnl"]) - 1.02)
    b["metrics"]["trades"]["net_pnl"] -= 1.02
    r = run(b)
    assert r["status"] == "FAIL" and has(r, "COST_ALLOCATION") and has(r, "TRADE_COST_ALLOCATION")
    assert r["cost_residual"] == pytest.approx(-1.02)


# --------------------------------------------------------------------------------------------- 9-11 rejections
def test_09_temporary_zero_volume_then_fill_passes():
    r = run(MULTI)
    assert r["status"] == "PASS"
    assert r["temporary_rejection_codes_seen"] == ["ZERO_VOLUME_BAR"]
    assert "0000-CROSS_ABOVE" in r["rejected_orders"] and "0000-CROSS_ABOVE" in {f.split("@")[0] for f in r["open_position_fill_ids"]}


def test_10_fill_after_order_expired_fails():
    b = copy.deepcopy(CLOSED)
    b["rejections"].append({"order_id": "0001-CROSS_BELOW", "bar_ts": "NaT", "reason": "ORDER_EXPIRED", "detail": "",
                            "expiry_ts": ts("16:00")})
    b["metrics"]["rejections"] = 1
    r = run(b)
    assert r["status"] == "FAIL" and has(r, "FILLED_AFTER_TERMINAL:0001-CROSS_BELOW:ORDER_EXPIRED")


@pytest.mark.parametrize("bar,code,prefix", [
    ("11:29", "ZERO_VOLUME_BAR", "REJECTION_AFTER_FILL"),             # bar rejection after the fill
    ("10:45", "ZERO_VOLUME_BAR", "REJECTION_CHRONOLOGY"),             # before the order existed
    ("11:20", "SOME_NEW_CORE_CODE", "UNKNOWN_REJECTION_STATE"),       # unknown reason is never temporary
    ("11:10", "RISK_LIMIT", "FILLED_AFTER_TERMINAL"),                 # terminal bar rejection then a fill
])
def test_11_illegal_rejection_chronology_or_state_fails(bar, code, prefix):
    b = copy.deepcopy(CLOSED)
    b["rejections"].append({"order_id": "0001-CROSS_BELOW", "bar_ts": ts(bar), "reason": code, "detail": "",
                            "expiry_ts": "NaT"})
    b["metrics"]["rejections"] = 1
    r = run(b)
    assert r["status"] == "FAIL" and has(r, prefix), r["problems"]


def test_11b_next_session_rejection_and_expiry_before_bar_rows_fail():
    b = copy.deepcopy(CLOSED)
    b["rejections"].append({"order_id": "0001-CROSS_BELOW", "bar_ts": ts("10:14", "2024-06-04T"),
                            "reason": "ZERO_VOLUME_BAR", "detail": "", "expiry_ts": "NaT"})
    b["metrics"]["rejections"] = 1
    assert has(run(b), "REJECTION_CHRONOLOGY")
    b = copy.deepcopy(OPEN)                                        # expiry stamped before its own bar rejection
    b["rejections"][1]["expiry_ts"] = ts("15:40")
    assert has(run(b), "EXPIRY_CHRONOLOGY")


# --------------------------------------------------------------------------------------------- 12-14 NOOP
def test_12_noop_with_full_lifecycle_evidence_passes():
    r = run(OPEN)
    assert r["status"] == "PASS"
    assert r["noop_state_at_activation"]["0003-CROSS_BELOW"] == {"filled_position": 0.0, "pending_signed_qty": 0.0,
                                                                "desired_target": 0.0}
    ev = [{"order_id": "0003-CROSS_BELOW", "event": "TARGET_ORDER_NOOP"}]
    r = run(OPEN, order_events=ev)                                 # Core order_events, when archived, are preferred
    assert r["status"] == "PASS" and r["noop_evidence"]["basis"] == "CORE_ORDER_EVENTS"


def test_13_missing_noop_evidence_never_passes():
    r = run(OPEN, order_events=[])                                 # events archived but the NOOP event is absent
    assert r["status"] == "FAIL" and has(r, "NOOP_WITHOUT_EVENT:0003-CROSS_BELOW")
    # first order expires -> no fill from a flat book proves the unit -> the NOOP stays unexplained, never PASS
    b = build([("15:30", 1, "CROSS_ABOVE"), ("10:00", 0, "CROSS_BELOW"), ("11:00", 1, "CROSS_ABOVE")], [],
              [("0000-CROSS_ABOVE", "15:44", "BAR_NOT_ELIGIBLE", None), ("0000-CROSS_ABOVE", None, "ORDER_EXPIRED", "16:00")])
    b["decisions"] = [dict(d, decision_ts=d["decision_ts"].replace(D, "2024-06-04T")) if i else d
                      for i, d in enumerate(b["decisions"])]
    b["fills"] = [{"order_id": "0002-CROSS_ABOVE", "symbol": "TSM", "contract": "TSM", "side": "BUY", "qty": "100.0",
                   "exec_ts": ts("11:14", "2024-06-04T"), "fill_price": "172.0", "explicit_cost": "1.02",
                   "order_time": ts("11:00", "2024-06-04T")}]
    b["metrics"]["fills"], b["orders_submitted"] = 1, 3
    b["metrics"]["open_position_at_end"] = {"positions": [{"symbol": "TSM", "contract": "TSM", "side": "LONG",
                                                           "qty": 100.0, "entry_ts": ts("11:14", "2024-06-04T"),
                                                           "entry_price": 172.0}]}
    r = run(b, order_quantity=None)
    assert r["status"] == "UNDETERMINED", r["problems"]
    assert r["unexplained_order_ids"] == ["0001-CROSS_BELOW"] and r["noop_evidence"]["basis"] == "NONE"
    assert R.execution_quality({R.EK03: R.K03_OPEN}, TEMP_OK, r)["status"] == "FAIL"
    b = copy.deepcopy(OPEN)                                        # an order with neither fill, rejection nor NOOP proof
    b["rejections"] = []
    b["metrics"]["rejections"] = 0
    r = run(b)
    assert r["status"] == "FAIL" and has(r, "PENDING_TARGET_INCONSISTENT:0003-CROSS_BELOW")


def test_14_pending_target_inconsistency_fails():
    b = copy.deepcopy(MULTI)                                       # second BUY sized 200 though Core delta is 100
    b["fills"][1]["qty"] = "200.0"
    b["metrics"]["open_position_at_end"]["positions"][1]["qty"] = 200.0
    r = run(b)
    assert r["status"] == "FAIL" and has(r, "PENDING_TARGET_INCONSISTENT:0002-CROSS_ABOVE")
    b = copy.deepcopy(OPEN)                                        # a fill on an order Core would have NOOPed
    b["fills"].append({**b["fills"][0], "order_id": "0003-CROSS_BELOW", "side": "SELL",
                       "exec_ts": ts("10:14", "2024-06-04T"), "order_time": ts("10:00", "2024-06-04T")})
    r = run(b)
    assert r["status"] == "FAIL" and has(r, "PENDING_TARGET_INCONSISTENT:0003-CROSS_BELOW")


# --------------------------------------------------------------------------------------------- 15-18 K03
def test_15_k03_pass_keeps_existing_behaviour():
    c = {"K00": "PASS", R.EK03: "PASS"}
    assert R.execution_quality(c, TEMP_OK, None)["status"] == "PASS"
    assert R.execution_quality(c, TEMP_OK, run(CLOSED))["status"] == "PASS"
    assert R.execution_quality({**c, "K00": "FAIL"}, TEMP_OK, run(CLOSED))["status"] == "FAIL"
    assert R.execution_quality(c, {"status": "FAIL"}, run(CLOSED))["status"] == "FAIL"


def test_16_k03_open_position_accepted_only_with_complete_c3():
    c = {"K00": "PASS", R.EK03: R.K03_OPEN}
    q = R.execution_quality(c, TEMP_OK, run(OPEN))
    assert q["status"] == "PASS" and q["explained_exceptions"][0]["check"] == R.EK03
    assert R.execution_quality(c, TEMP_OK, None)["status"] == "FAIL"     # no C3 -> never accepted


def test_17_k03_open_position_with_inconsistent_position_fails():
    c = {"K00": "PASS", R.EK03: R.K03_OPEN}
    b = copy.deepcopy(OPEN)
    b["metrics"]["open_position_at_end"]["positions"][0]["entry_price"] = 999.0
    assert R.execution_quality(c, TEMP_OK, run(b))["status"] == "FAIL"
    assert R.execution_quality(c, TEMP_OK, run(CLOSED))["status"] == "FAIL"  # K03 says open, C3 found none
    assert R.execution_quality({**c, "K01": "FAIL"}, TEMP_OK, run(OPEN))["status"] == "FAIL"


@pytest.mark.parametrize("v", ["NOT_APPLICABLE_NO_ROUND_TRIPS", "NOT_APPLICABLE", "FAIL", "UNKNOWN", ""])
def test_18_other_k03_states_fail(v):
    assert R.execution_quality({R.EK03: v}, TEMP_OK, run(OPEN))["status"] == "FAIL"


# --------------------------------------------------------------------------------------------- wiring (phase4c)
def parsed_for(b):
    rows = lambda key: [dict(r, scenario=s) for s in SCEN for r in b[key]]  # noqa: E731
    return {"trades": rows("trades"), "fills": rows("fills"), "rejections": rows("rejections"),
            "decisions": b["decisions"], "metrics": {s: copy.deepcopy(b["metrics"]) for s in SCEN},
            "diagnostics": {"decisions": {"orders": b["orders_submitted"]}}, "config": {"market": {"symbol": "TSM"}}}


def test_trade_integrity_wiring_and_status_rollup():
    t = C.trade_integrity(parsed_for(OPEN))
    assert set(t) == set(SCEN) and C.c3_status(t) == "PASS"
    b = copy.deepcopy(OPEN)
    b["fills"][-1]["qty"] = "50.0"
    b["metrics"]["open_position_at_end"]["positions"][0]["qty"] = 50.0
    t = C.trade_integrity(parsed_for(b))
    assert C.c3_status(t) in ("UNDETERMINED", "FAIL") and C.c3_status(t) != "PASS"
    assert C.c3_status({"BASE": {"status": "PASS"}, "LOW": {"status": "UNDETERMINED"}}) == "UNDETERMINED"
    assert C.c3_status({"BASE": {"status": "UNDETERMINED"}, "LOW": {"status": "FAIL"}}) == "FAIL"
    assert C.c3_status({}) == "FAIL"


# --------------------------------------------------------------------------------------------- 19-20 archive level
ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests/fixtures/phase4c"
ARCH, GOV = FIX / "20261106_224000_0123456", FIX / "governance_root"


def _accept(a, pin=None):
    return C.accept(a, expected_manifest_sha256=pin or hashlib.sha256((a / "MANIFEST.json").read_bytes()).hexdigest(),
                    evaluator_commit_sha="a" * 40, evaluated_at="2026-11-07T09:00:00+08:00", governance_root=GOV)


def test_19_missing_or_modified_official_csv_fails(tmp_path):
    a = tmp_path / ARCH.name
    shutil.copytree(ARCH, a)
    pin = hashlib.sha256((a / "MANIFEST.json").read_bytes()).hexdigest()
    (a / "fills.csv").write_bytes((a / "fills.csv").read_bytes().replace(b"1.020", b"1.021", 1))
    acc = _accept(a, pin)                                          # modified bytes, pinned manifest -> blocked
    assert acc["status"] == "BLOCKED" and acc["error"]["code"] == "ARCHIVE_ARTIFACT_SHA"
    (a / "fills.csv").unlink()
    assert _accept(a, pin)["status"] == "BLOCKED"
    b = tmp_path / "b" / ARCH.name                                 # consistent re-hash (bad producer) -> C3 FAIL
    shutil.copytree(ARCH, b)
    lines = (b / "fills.csv").read_text().splitlines(keepends=True)
    (b / "fills.csv").write_text("".join(lines[:-1]))              # drop the last fill row
    m = json.loads((b / "MANIFEST.json").read_text())
    m["files_sha256"]["fills.csv"] = hashlib.sha256((b / "fills.csv").read_bytes()).hexdigest()
    (b / "MANIFEST.json").write_text(json.dumps(m, indent=2, sort_keys=True))
    acc = _accept(b)
    assert acc["checks"]["C3_trade_integrity"]["status"] == "FAIL" and acc["status"] == "FAIL"


def test_20_c1_c2_c5_c7_c8_semantics_unchanged_and_k03_open_needs_c3():
    acc = _accept(ARCH)
    st = {k: v["status"] for k, v in acc["checks"].items()}
    for k in ("C1_archive_identity", "C2_archive_integrity", "C3_trade_integrity", "C5_temporal_safety",
              "C7_reproducibility"):
        assert st[k] == "PASS", st
    c8 = acc["checks"]["C8_governance_isolation"]
    assert c8["status"] == "PASS" or c8["executor_modules_loaded"]
    # envelope: K03 = NOT_APPLICABLE_OPEN_POSITION is FAIL without C3, PASS only with a complete C3 for the scenario
    arch = C.read_archive(ARCH, expected_manifest_sha256=hashlib.sha256((ARCH / "MANIFEST.json").read_bytes()).hexdigest())
    parsed = C.parse(arch)
    parsed["metrics"]["BASE"]["m3a4_checks"][R.EK03] = R.K03_OPEN
    gov = C.load_governance(GOV)
    doc, _ = C.envelope(parsed, arch, gov, "BASE")
    assert doc["execution_diagnostics"]["value"]["status"] == "FAIL"
    c3 = C.trade_integrity(parsed)["BASE"]                         # fixture has no open position -> still FAIL
    doc, _ = C.envelope(parsed, arch, gov, "BASE", c3)
    assert doc["execution_diagnostics"]["value"]["status"] == "FAIL"
