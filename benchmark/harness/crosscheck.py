"""Cross-validation harness for auditing an external backtest engine.

Phase 3 does NOT import ``market_research_core`` here. The engine under test is
driven by a thin, separately-reviewed adapter (written only after Phase 2 is GO)
that must emit results in the canonical ``BenchmarkResult`` JSON shape:

    {
      "case_id": "B01_normal_entry_exit",
      "engine": {"name": "...", "git_sha": "...", "version": "..."},
      "fills": [{"exec_ts": "YYYY-MM-DD HH:MM", "side": "BUY", "qty": 1,
                 "contract": "SYNF", "fill_price": "20011", "fee": "50", "tax": "80.044"}],
      "rejections": [{"bar_ts": "...", "reason": "..."}],      # reason codes: see REASON_MAP
      "realized_pnl": "7339.76",
      "open_position": null | {"qty": 1, "unrealized_pnl_mark": "9800"}
    }

Usage:
    python -m benchmark.harness.crosscheck export  OUT_DIR       # CSV fixtures
    python -m benchmark.harness.crosscheck compare RESULTS_DIR   # audit report
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

CASE_DIR = Path(__file__).parents[1] / "cases"
TOL = Decimal("0.000001")

# Engine-specific reason codes are mapped onto oracle codes by the adapter.
# Listed here so that the mapping is reviewed, not improvised.
REASON_CODES = {
    "OUTSIDE_SESSION", "ZERO_VOLUME_BAR", "LIMIT_UP_LOCKED_BUY", "LIMIT_DOWN_LOCKED_SELL",
    "BAR_IN_EXCLUSION_WINDOW", "ORDER_EXPIRED", "UNFILLED_AT_END_OF_DATA",
}


def cases() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(CASE_DIR.glob("B*.json"))]


def export(out_dir: Path) -> None:
    """Write one long-format CSV per case with explicit available_at and flags."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for c in cases():
        tf = timedelta(minutes=c["timeframe_minutes"])
        sessions = [(datetime.strptime(f"{s['date']} {s['start']}", "%Y-%m-%d %H:%M"),
                     datetime.strptime(f"{s['date']} {s['end']}", "%Y-%m-%d %H:%M")) for s in c["sessions"]]
        excl = [(datetime.strptime(w["start"], "%Y-%m-%d %H:%M"), datetime.strptime(w["end"], "%Y-%m-%d %H:%M"))
                for w in c.get("exclusions", [])]
        path = out_dir / f"{c['case_id']}.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["timestamp_local", "timezone", "available_at_local", "contract", "open", "high", "low",
                        "close", "volume", "in_session", "excluded", "limit_up_locked", "limit_down_locked"])
            for row in c["bars"]:
                r = dict(zip(c["bars_columns"], row))
                ts = datetime.strptime(r["ts"], "%Y-%m-%d %H:%M")
                w.writerow([r["ts"], c["timezone"], (ts + tf).strftime("%Y-%m-%d %H:%M"),
                            r.get("contract", c["instrument"]["symbol"]), r["o"], r["h"], r["l"], r["c"], r["v"],
                            any(s <= ts and ts + tf <= e for s, e in sessions),
                            any(s <= ts < e for s, e in excl),
                            bool(r.get("limit_up_locked", False)), bool(r.get("limit_down_locked", False))])


def _d(x) -> Decimal:
    return Decimal(str(x))


def compare_one(case: dict, got: dict) -> list[str]:
    exp = case["expected"]
    errs: list[str] = []
    ef, gf = exp["fills"], got.get("fills", [])
    if len(ef) != len(gf):
        errs.append(f"fill count expected {len(ef)} got {len(gf)}")
    for i, (e, g) in enumerate(zip(ef, gf)):
        for k in ("exec_ts", "side", "contract"):
            if str(e[k]) != str(g.get(k)):
                errs.append(f"fill[{i}].{k}: expected {e[k]} got {g.get(k)}")
        for k in ("qty", "fill_price", "fee", "tax"):
            if abs(_d(e[k]) - _d(g.get(k, "NaN") if g.get(k) is not None else 0)) > TOL:
                errs.append(f"fill[{i}].{k}: expected {e[k]} got {g.get(k)}")
    if abs(_d(exp["realized_pnl"]) - _d(got.get("realized_pnl", 0))) > TOL:
        errs.append(f"realized_pnl expected {exp['realized_pnl']} got {got.get('realized_pnl')}")
    exp_rej = sorted((r["bar_ts"], r["reason"]) for r in exp["rejections"])
    got_rej = sorted((r.get("bar_ts"), r.get("reason")) for r in got.get("rejections", []))
    if exp_rej != got_rej:
        errs.append(f"rejections expected {exp_rej} got {got_rej}")
    fill_ts = {g.get("exec_ts") for g in gf}
    for bad in exp.get("forbidden_fills", []):
        if bad["exec_ts"] in fill_ts:
            errs.append(f"FORBIDDEN fill at {bad['exec_ts']}: {bad['why']}")
    if (exp["open_position"] is None) != (got.get("open_position") is None):
        errs.append("open_position presence mismatch")
    elif exp["open_position"]:
        if abs(_d(exp["open_position"]["unrealized_pnl_mark"]) - _d(got["open_position"]["unrealized_pnl_mark"])) > TOL:
            errs.append("open_position.unrealized_pnl_mark mismatch")
    return errs


def compare(results_dir: Path) -> int:
    report, failed = [], 0
    for c in cases():
        p = results_dir / f"{c['case_id']}.result.json"
        if not p.is_file():
            report.append((c["case_id"], "MISSING", []))
            failed += 1
            continue
        errs = compare_one(c, json.loads(p.read_text(encoding="utf-8")))
        status = "PASS" if not errs else ("PROPOSED_RULE_DIFF" if c.get("status", "").startswith("PROPOSED") else "FAIL")
        failed += status == "FAIL"
        report.append((c["case_id"], status, errs))
    for cid, status, errs in report:
        print(f"{status:20s} {cid}")
        for e in errs:
            print(f"    - {e}")
    print(f"\nBENCHMARK: {'PASS' if failed == 0 else 'FAIL'} ({failed} failing)")
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover
    cmd, arg = sys.argv[1], Path(sys.argv[2])
    sys.exit(export(arg) or 0 if cmd == "export" else compare(arg))
