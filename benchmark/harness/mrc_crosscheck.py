"""B01-B12 cross-check against the REAL market-research-core (no fake engine, no oracle self-comparison).

python -m benchmark.harness.mrc_crosscheck [--out benchmark/results]

For every case the harness asks the M1 adapter which Core capabilities the case needs. If the installed Core
cannot express the case, the case is recorded NOT_EXECUTABLE with the missing capabilities (responsible repo:
market-research-core). If it can, the case's synthetic bars are passed to the real Core engine and the Core
result is compared with the case's hand-calculated expectations through ``crosscheck.compare_one`` (the oracle
answers in the case files are read, never modified).

Core f4276cb exposes only ``BacktestEngine.run_sma``: a case is executable on it only if it is an equity
long-only SMA cross with flags expressible as a single ``execution_eligible`` column. Tick slippage is NOT
expressible (bps only); the run uses the bps closest to one tick at the first bar and records that
approximation as part of the root cause instead of hiding it.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from benchmark.harness.crosscheck import cases, compare_one
from strategy_agent.adapters import mrc_adapter as A

OUT = Path(__file__).resolve().parents[1] / "results"


def _summary(exp: dict) -> dict:
    return {"fills": [{k: f[k] for k in ("exec_ts", "side", "qty", "fill_price", "fee", "tax")} for f in exp["fills"]],
            "rejections": exp["rejections"], "realized_pnl": exp["realized_pnl"], "open_position": exp["open_position"]}


def run_sma_case(case: dict, core) -> tuple[dict, dict]:
    """Execute one SMA case on Core.run_sma. Returns (benchmark-shaped result, run notes)."""
    import pandas as pd
    tz = case["timezone"]
    tf = timedelta(minutes=case["timeframe_minutes"])
    sessions = [(datetime.strptime(f"{s['date']} {s['start']}", "%Y-%m-%d %H:%M"),
                 datetime.strptime(f"{s['date']} {s['end']}", "%Y-%m-%d %H:%M")) for s in case["sessions"]]
    rows = []
    for raw in case["bars"]:
        r = dict(zip(case["bars_columns"], raw))
        ts = datetime.strptime(r["ts"], "%Y-%m-%d %H:%M")
        ok = any(s <= ts and ts + tf <= e for s, e in sessions)      # case-defined flags, no inference
        rows.append({"timestamp": ts, "open": float(r["o"]), "high": float(r["h"]), "low": float(r["l"]),
                     "close": float(r["c"]), "volume": float(r["v"]), "available_at": ts + tf,
                     "data_exists": True, "research_eligible": ok, "backtest_eligible": ok,
                     "execution_eligible": ok and not r.get("limit_up_locked") and not r.get("limit_down_locked")})
    frame = pd.DataFrame(rows)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"]).dt.tz_localize(tz)
    frame["available_at"] = pd.to_datetime(frame["available_at"]).dt.tz_localize(tz)
    sm, costs, inst = case["signal_model"], case["costs"], case["instrument"]
    first_open = Decimal(str(case["bars"][0][1]))
    bps = float(Decimal(inst["tick_size"]) * costs["slippage_ticks"] / first_open * 10000)
    prov = core.models.UnifiedProvenance(market="TW", symbol=inst["symbol"], snapshot_id=f"BENCHMARK_{case['case_id']}",
                                         producer_commit_sha="benchmark-fixture", data_verdict="SYNTHETIC",
                                         source_files=(f"benchmark/cases/{case['case_id']}.json",),
                                         permitted_usage_scope="BACKTEST")
    assumptions = core.BacktestAssumptions(initial_cash=1e12, fast_window=int(sm["fast"]), slow_window=int(sm["slow"]),
                                           fee_rate=float(costs["fee_rate_side"]),
                                           sell_tax_rate=float(costs["sell_tax_rate"]), slippage_bps=bps,
                                           unit_size=float(case["quantity"]))
    res = core.BacktestEngine().run_sma(core.UnifiedBars(frame, prov), assumptions)
    fills, realized = [], Decimal("0")
    for t in res.trades.to_dict("records"):
        fills.append({"exec_ts": A.ts_label(t["timestamp"].tz_convert(tz)), "side": t["side"], "qty": int(t["units"]),
                      "contract": inst["symbol"], "fill_price": repr(float(t["price"])), "fee": repr(float(t["fee"])),
                      "tax": repr(float(t["tax"])), "signal_ts": A.ts_label(t["signal_timestamp"].tz_convert(tz))})
        if t["side"] == "SELL":
            realized += Decimal(repr(float(t["realized_pnl"])))
    rejections, unmapped = [], []
    for r in res.rejections.to_dict("records") if len(res.rejections) else []:
        try:
            rejections.append({"bar_ts": A.ts_label(r["bar_timestamp"]), "reason": A.map_reason(r["reason"])})
        except Exception as e:  # noqa: BLE001
            unmapped.append(str(e))
    last_pos = float(res.equity_curve["position"].iloc[-1])
    got = {"case_id": case["case_id"], "fills": fills, "rejections": rejections, "realized_pnl": str(realized),
           "open_position": None if last_pos == 0 else {"qty": last_pos, "unrealized_pnl_mark": "NOT_REPORTED"}}
    return got, {"api": "BacktestEngine.run_sma", "slippage_bps_used": bps,
                 "approximation": f"1 tick ({inst['tick_size']}) expressed as {bps:.6f} bps of the first open",
                 "unmapped_rejections": unmapped, "provenance_keys": sorted(res.provenance)}


def executable_on_phase2_probe(case: dict) -> bool:
    sm = case["signal_model"]
    return (sm["type"] == "sma_cross" and case["instrument"]["kind"] == "equity" and not case.get("exclusions")
            and not case.get("roll_schedule"))


def classify(errs: list[str], notes: dict) -> tuple[str, str]:
    if any("fill_price" in e or ".fee" in e or ".tax" in e or "realized_pnl" in e for e in errs) \
            and notes.get("approximation"):
        return "CORE_CAPABILITY_GAP:K03/K12 (bps slippage cannot express ticks; float arithmetic)", "market-research-core"
    if any("exec_ts" in e or "FORBIDDEN" in e for e in errs):
        return "CORE_SEMANTIC_DIFF:execution timing", "market-research-core"
    if notes.get("unmapped_rejections"):
        return "CORE_CAPABILITY_GAP:K15 (rejection reason not mappable)", "market-research-core"
    return "UNCLASSIFIED", "strategy-research-agent (adapter) - investigate"


def crosscheck(out_root: Path = OUT) -> dict:
    core = A._core()
    ident = A.core_identity()
    if not ident.get("git_sha"):
        raise SystemExit("installed Core has no resolvable commit: refusing to produce an unpinned record")
    matrix = A.compatibility_matrix(core)
    rows = []
    for c in cases():
        need = A.caps_required_by_case(c["case_id"])
        missing = A.missing_for(need, matrix)
        exp = _summary(c["expected"])
        if executable_on_phase2_probe(c):
            got, notes = run_sma_case(c, core)
            errs = compare_one(c, got)
            if notes["unmapped_rejections"]:
                errs.append("unmapped core rejections: " + "; ".join(notes["unmapped_rejections"]))
            status = "PASS" if not errs else "FAIL"
            cause, repo = ("", "") if status == "PASS" else classify(errs, notes)
            rows.append({"case_id": c["case_id"], "status": status, "executed_on_core": True, "core_api": notes["api"],
                         "expected": exp, "actual": {k: got[k] for k in ("fills", "rejections", "realized_pnl",
                                                                          "open_position")},
                         "difference": errs, "root_cause_category": cause, "responsible_repo": repo,
                         "missing_capabilities": missing, "notes": notes})
        else:
            rows.append({"case_id": c["case_id"], "status": "NOT_EXECUTABLE", "executed_on_core": False,
                         "core_api": None, "expected": exp, "actual": "NOT_EXECUTED",
                         "difference": [f"core {ident['git_sha'][:7]} cannot express the case: missing "
                                        + ", ".join(missing or ["K01"])],
                         "root_cause_category": "CORE_CAPABILITY_GAP:" + "/".join(missing or ["K01"]),
                         "responsible_repo": "market-research-core", "missing_capabilities": missing})
    n = {s: sum(r["status"] == s for r in rows) for s in ("PASS", "FAIL", "NOT_EXECUTABLE")}
    verdict = "PASS" if n["PASS"] == len(rows) else ("INTEGRATION_PENDING" if n["NOT_EXECUTABLE"] else "FAIL")
    rec = {"engine": {"name": A.CORE_DIST, "git_sha": ident["git_sha"], "version": ident["version"],
                      "kind": "REAL_CORE", "source": ident["source"]},
           "adapter_version": A.ADAPTER_VERSION, "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "verdict": verdict, "counts": n, "capability_matrix": matrix, "cases": rows,
           "statement": "expected values read from benchmark/cases (unchanged); actual values produced by the real "
                        "Core engine; the oracle was not executed as the engine under test"}
    d = Path(out_root) / f"mrc_{ident['git_sha'][:12]}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "crosscheck.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (d / "report.md").write_text(report_md(rec), encoding="utf-8")
    return rec


def report_md(rec: dict) -> str:
    e = rec["engine"]
    L = [f"# Benchmark comparison — real market-research-core `{e['git_sha'][:12]}`", "",
         f"Verdict: **{rec['verdict']}**  (PASS {rec['counts']['PASS']}, FAIL {rec['counts']['FAIL']}, "
         f"NOT_EXECUTABLE {rec['counts']['NOT_EXECUTABLE']} of 12)", "",
         "| case | status | core API | root cause | responsible repo |", "|---|---|---|---|---|"]
    for r in rec["cases"]:
        L.append(f"| {r['case_id']} | {r['status']} | {r['core_api'] or '—'} | {r['root_cause_category'] or '—'} | "
                 f"{r['responsible_repo'] or '—'} |")
    L += ["", "## Executed cases: expected vs actual", ""]
    for r in rec["cases"]:
        if not r["executed_on_core"]:
            continue
        L += [f"### {r['case_id']} — {r['status']}", "", "| | expected | actual (real Core) |", "|---|---|---|"]
        ef, af = r["expected"]["fills"], r["actual"]["fills"]
        for i in range(max(len(ef), len(af))):
            a = ef[i] if i < len(ef) else {}
            b = af[i] if i < len(af) else {}
            for k in ("exec_ts", "side", "qty", "fill_price", "fee", "tax"):
                L.append(f"| fill[{i}].{k} | {a.get(k, '—')} | {b.get(k, '—')} |")
        L.append(f"| realized_pnl | {r['expected']['realized_pnl']} | {r['actual']['realized_pnl']} |")
        L.append(f"| open_position | {r['expected']['open_position']} | {r['actual']['open_position']} |")
        L += ["", "Differences:", ""] + [f"- {d}" for d in r["difference"]] + ["", f"Notes: {r['notes']['approximation']}", ""]
    L += ["## Capability matrix (measured on the installed Core)", "", "| cap | contract | status | needed by | evidence |",
          "|---|---|---|---|---|"]
    for m in rec["capability_matrix"]:
        L.append(f"| {m['cap_id']} | {m['contract']} | {m['status']} | {', '.join(m['required_by']) or '—'} | "
                 f"{m['evidence'][:140]} |")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m benchmark.harness.mrc_crosscheck")
    ap.add_argument("--out", default=str(OUT))
    rec = crosscheck(Path(ap.parse_args(argv).out))
    print(report_md(rec))
    return 0 if rec["verdict"] == "PASS" else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
