"""P5C-TA-01 fill-feasibility diagnostic (Haw, 2026-11-18). NOT a backtest and NOT a performance study.

Question: for the order intents the frozen TA-01 spec produces on the TSM Development 1m data, how often is there
no tradable 1m bar inside the 2-minute window, and how often does the participation cap block the fixed quantity?

What it does (pure counting on Core columns; nothing re-timed, no Core ExecutionEngine run):
  * decisions: ``core_phase4a.ta01_signals`` (the same code path execute_batch uses, delayed-open exclusion included);
  * fill facts per intent: ``core_phase4a.ta01_fill_examine`` (same lookup ta01_select_fills uses);
  * an exit intent exists only after a fillable entry (the strategy is flat otherwise);
  * Core's own full-minute participation check (execution.py:413) is evaluated on the chosen bar with Core's formula
    ``qty > volume x cap`` and reported separately as a LOOK-AHEAD residual (the full-minute volume is not known at the
    open). It is not a validated opening-fill constraint.

What it never outputs: prices, PnL, returns, win rate, profit factor, drawdown or any strategy-validity statement.
The output whitelist is ``OUTPUT_KEYS``; ``assert_no_performance_fields`` enforces it before anything is written.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
AUTHORIZATION_PATH = "docs/phase5c/ta01_fill_diagnostic_authorization.json"
DIAGNOSTIC_ID = "P5C-TA01-FILL-DIAGNOSTIC-v1"
OUTPUT_KEYS = {"diagnostic_id", "status", "definitions", "data", "delayed_open", "entry", "exit", "by_decision_time",
               "exit_failures", "not_output", "inputs"}
FORBIDDEN_FRAGMENTS = ("pnl", "return", "sharpe", "drawdown", "win_rate", "profit", "equity", "price", "payoff",
                       "expectancy")


class DiagnosticRefused(RuntimeError):
    pass


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha256_of(obj) -> str:
    return hashlib.sha256(canonical(obj)).hexdigest()


def _quantiles(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    v = sorted(values)

    def q(p):                                              # type 7, as in evaluation.random_entry
        h = (len(v) - 1) * p
        lo = math.floor(h)
        return v[lo] + (v[min(lo + 1, len(v) - 1)] - v[lo]) * (h - lo)
    return {"n": len(v), "min": v[0], "p01": q(.01), "p05": q(.05), "p25": q(.25), "median": q(.5)}


def _side_counter():
    return {"intents": 0, "known_volume_cap_blocked": 0, "no_tradable_bar_in_window": 0,
            "no_tradable_bar_causes": Counter(), "intents_with_missing_minute_in_window": 0,
            "intents_with_zero_volume_minute_in_window": 0, "chosen_first_candidate": 0,
            "chosen_later_candidate_waited": 0, "core_full_minute_cap_would_reject": 0, "fillable": 0,
            "_known_ratio": [], "_chosen_ratio": []}


def _cause(x, window_minutes: int) -> str:
    slots = window_minutes
    if x["missing_minutes"] == slots:
        return "ALL_MINUTES_MISSING"
    if x["zero_volume_minutes"] == slots:
        return "ALL_MINUTES_ZERO_VOLUME"
    if x["missing_minutes"] + x["zero_volume_minutes"] == slots:
        return "MISSING_AND_ZERO_VOLUME"
    return "OTHER_CORE_INELIGIBLE"


def diagnose(core, frame_1m, *, spec: dict, parameters: dict, participation_cap, window_start: str, window_end: str,
             quantity: float | None = None) -> dict:
    """Counts only. ``frame_1m``: Core 1m conservative frame (from ``core_phase4a.execution_frame``)."""
    import pandas as pd  # noqa: PLC0415

    from . import core_phase4a as P  # noqa: PLC0415
    family, params = P.signal_family(spec, parameters)
    if family != "TA01":
        raise DiagnosticRefused("the fill diagnostic is defined for the TA-01 spec only")
    qty = float(quantity if quantity is not None else spec["position_sizing"]["units"])
    if qty != float(spec["position_sizing"]["units"]):
        raise DiagnosticRefused("quantity must equal the frozen spec's fixed units")
    rows, _ = P.conservative_rows_and_bars(frame_1m)
    decs, delayed_open = P.ta01_signals(core, frame_1m, rows, params)
    ws, we = datetime.fromisoformat(window_start), datetime.fromisoformat(window_end)
    kept = [d for d in decs if ws <= d.decision_ts < we]
    by_contract, window = P.ta01_fill_candidates(frame_1m, params.fill_window_minutes)
    sides = {"entry": _side_counter(), "exit": _side_counter()}
    by_time: dict[str, Counter] = {}
    exit_failures, long = [], False
    for d in kept:
        side = "entry" if d.target == 1 else "exit"
        if side == "exit" and not long:
            continue                                        # flat: no exit order exists
        t = pd.Timestamp(d.decision_ts).tz_convert("UTC")
        x = P.ta01_fill_examine(by_contract, window, "TSM", t, "BUY" if side == "entry" else "SELL", qty,
                                participation_cap)
        c = sides[side]
        c["intents"] += 1
        c["intents_with_missing_minute_in_window"] += x["missing_minutes"] > 0
        c["intents_with_zero_volume_minute_in_window"] += x["zero_volume_minutes"] > 0
        c["_known_ratio"].append(x["known_volume"] / qty)
        fillable = False
        if not x["known_volume_ok"]:
            c["known_volume_cap_blocked"] += 1
            outcome = "KNOWN_VOLUME_CAP"
        elif x["chosen"] is None:
            c["no_tradable_bar_in_window"] += 1
            outcome = _cause(x, params.fill_window_minutes)
            c["no_tradable_bar_causes"][outcome] += 1
        else:
            c["chosen_first_candidate" if x["chosen_index"] == 0 else "chosen_later_candidate_waited"] += 1
            c["_chosen_ratio"].append(float(x["chosen"]["volume"]) / qty)
            if x["core_full_bar_cap_would_reject"]:
                c["core_full_minute_cap_would_reject"] += 1
                outcome = "CORE_FULL_MINUTE_CAP"
            else:
                c["fillable"] += 1
                fillable, outcome = True, "FILLABLE"
        hm = pd.Timestamp(d.decision_ts).tz_convert("America/New_York").strftime("%H:%M")
        by_time.setdefault(f"{side} {hm}", Counter())[outcome] += 1
        if side == "entry":
            long = fillable
        else:
            if not fillable:
                exit_failures.append({"decision_ts": t.isoformat(), "reason": outcome})
            long = False                                    # each session is independent (no overnight)

    def finish(c):
        n = c["intents"]
        out = {k: v for k, v in c.items() if not k.startswith("_")}
        out["no_tradable_bar_causes"] = dict(c["no_tradable_bar_causes"])
        for k in ("known_volume_cap_blocked", "no_tradable_bar_in_window", "core_full_minute_cap_would_reject",
                  "intents_with_missing_minute_in_window", "intents_with_zero_volume_minute_in_window"):
            out[k + "_ratio"] = (out[k] / n) if n else None
        out["not_fillable_total"] = n - c["fillable"]
        out["not_fillable_ratio"] = ((n - c["fillable"]) / n) if n else None
        out["known_volume_to_quantity"] = _quantiles(c["_known_ratio"])
        out["chosen_bar_volume_to_quantity"] = _quantiles(c["_chosen_ratio"])
        return out

    f = frame_1m
    days = pd.to_datetime(f["source_timestamp"], utc=True).dt.tz_convert("America/New_York").dt.date
    in_rth = f["session"].eq("RTH")
    daily = f[in_rth].groupby(days[in_rth])["volume"].sum()
    by_year = daily.groupby(pd.Index([d.year for d in daily.index])).median()
    zero = f[in_rth].groupby(days[in_rth].map(lambda d: d.year))["volume"].apply(lambda v: float((v <= 0).mean()))
    result = {
        "diagnostic_id": DIAGNOSTIC_ID, "status": "COMPLETED",
        "definitions": {
            "window": f"Core bar_start (earliest possible open) in (t, t+{params.fill_window_minutes} min], "
                      "boundary included; missing / zero-volume / Core-ineligible minutes skipped",
            "known_volume_rule": P.KNOWN_VOLUME_RULE,
            "participation_cap": participation_cap, "quantity": qty,
            "core_full_minute_cap": "Core execution.py:413 qty > chosen bar FULL-minute volume x cap; LOOK-AHEAD "
                                    "residual, reported separately, not a validated fill constraint",
            "exit_intent": "exists only after a fillable entry in the same session",
            "volume_unit": "as stored in the Core source 'volume' column; unit UNVERIFIED (see data.median_daily_rth_volume_by_year)",
            "delayed_open_policy": "TA01-DELAYED-OPEN-EXCLUSION-v1"},
        "data": {"rows_1m": int(len(f)), "rth_sessions": int(daily.size),
                 "median_daily_rth_volume_by_year": {int(k): float(v) for k, v in by_year.items()},
                 "rth_zero_volume_minute_share_by_year": {int(k): float(v) for k, v in zero.items()}},
        "delayed_open": {"sessions": len(delayed_open),
                         "whole_session_excluded": sum(v["exclude_bucket_first_labels_through"] == "WHOLE_SESSION"
                                                       for v in delayed_open.values()),
                         "dates": sorted(delayed_open)},
        "entry": finish(sides["entry"]), "exit": finish(sides["exit"]),
        "by_decision_time": {k: dict(v) for k, v in sorted(by_time.items())},
        "exit_failures": {"count": len(exit_failures), "items": exit_failures,
                          "meaning": "in a strategy execution each of these fails the WHOLE run (no overnight)"},
        "not_output": ["prices", "PnL", "returns", "win rate", "profit factor", "drawdown", "strategy validity"],
    }
    assert_no_performance_fields(result)
    return result


def assert_no_performance_fields(obj, path="") -> None:
    if isinstance(obj, dict):
        if not path and set(obj) - OUTPUT_KEYS:
            raise DiagnosticRefused(f"unexpected top-level output keys {sorted(set(obj) - OUTPUT_KEYS)}")
        for k, v in obj.items():
            key = str(k).lower()
            if path != "not_output" and k != "not_output" and any(x in key for x in FORBIDDEN_FRAGMENTS):
                raise DiagnosticRefused(f"performance-like field refused: {path}/{k}")
            if k != "not_output":
                assert_no_performance_fields(v, f"{path}/{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            assert_no_performance_fields(v, f"{path}[{i}]")


# ---------------------------------------------------------------- authorization (unsigned document + live approval)

def load_authorization(root: Path = ROOT) -> dict:
    doc = json.loads((Path(root) / AUTHORIZATION_PATH).read_text(encoding="utf-8"))
    if doc.get("status") != "UNSIGNED_UNTIL_LEAD_APPROVAL_COMMENT":
        raise DiagnosticRefused("authorization document status changed")
    return doc


def approval_expectation(doc: dict):
    from strategy_agent.approval.verifier import Expectation  # noqa: PLC0415
    return Expectation("BIND_AUTHORIZATION", sha256_of(doc), doc["authorization_id"], sha256_of(doc["scope"]), None)


def approval_line(doc: dict, expires_utc: str) -> str:
    from strategy_agent.approval import tokens as T  # noqa: PLC0415
    e = approval_expectation(doc)
    return T.approval_line(intent=e.intent, request_sha256=e.request_sha256, authorization_id=e.authorization_id,
                           scope=e.scope_sha256, expires_utc=expires_utc)


def verify_approval(doc: dict, url: str, verifier=None) -> dict:
    """Live GitHub verification (V01-V09 of approval.verifier): author hh4832 (login AND id), not via an App, not
    edited, not expired or revoked, exact approval line for THIS document."""
    from strategy_agent.approval.verifier import ApprovalEvidenceVerifier  # noqa: PLC0415
    v = verifier or ApprovalEvidenceVerifier.for_production()
    return v.verify(url, approval_expectation(doc))


def check_scope(doc: dict, *, spec: dict, spec_sha256: str, core_sha: str, schedule_sha256: str, pins: dict,
                window: dict) -> None:
    s = doc["scope"]
    bad = [k for k, ok in {"spec_sha256": s["strategy"]["spec_file_sha256"] == spec_sha256,
                           "parameters": s["strategy"]["parameters"] == {k: v["default"] for k, v in
                                                                         spec["parameters"]["values"].items()},
                           "core": s["core_sha"] == core_sha, "cost_schedule": s["costs"]["dated_schedule_sha256"] == schedule_sha256,
                           "pins": s["dataset"]["reviewed_pins"] == pins, "window": s["window"] == window}.items()
           if not ok]
    if bad:
        raise DiagnosticRefused(f"run differs from the authorized scope: {bad}")


# ---------------------------------------------------------------- Colab entry point (subprocess, see notebook)

def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--settings", required=True)
    a = ap.parse_args(argv)
    settings = json.loads(Path(a.settings).read_text(encoding="utf-8"))
    doc = load_authorization()
    if settings["mode"] == "PREPARE":                     # no data, no approval: print what the lead must post
        print(json.dumps({"authorization_id": doc["authorization_id"], "document_sha256": sha256_of(doc),
                          "scope_sha256": sha256_of(doc["scope"]),
                          "approval_line": approval_line(doc, settings["approval_expires_utc"]),
                          "data_read": False}, indent=2))
        return 0
    if settings["mode"] != "RUN":
        raise DiagnosticRefused("mode must be PREPARE or RUN")
    if settings.get("confirm_authorization") != doc["authorization_id"]:
        raise DiagnosticRefused("type the full authorization id to confirm RUN")
    evidence = verify_approval(doc, settings["approval_url"])  # before any data is touched
    if (evidence.get("approval_source") or {}).get("trust") != "PRODUCTION":
        raise DiagnosticRefused("approval evidence is not PRODUCTION (live GitHub, lead identity)")
    out_dir = Path(settings["drive_root"]) / doc["authorization_id"]
    out_dir.mkdir(parents=True, exist_ok=False)              # one diagnostic per authorization; never re-run
    from strategy_agent.execution import c1_integration as C  # noqa: PLC0415
    from strategy_agent.execution import c1_runner as R  # noqa: PLC0415
    from strategy_agent.readiness.c1_authorization import PINS  # noqa: PLC0415
    from strategy_agent.adapters.mrc_phase4a import core_phase4a  # noqa: PLC0415
    from strategy_agent.adapters.mrc_adapter import _core  # noqa: PLC0415
    from . import core_phase4a as P  # noqa: PLC0415
    from .runner import write_once  # noqa: PLC0415
    native_mod, _costs = core_phase4a()
    ident = native_mod.core_code_identity()
    spec_path = ROOT / doc["scope"]["strategy"]["spec_path"]
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    schedules = C.load_dated_schedules(ROOT, expected_sha256=doc["scope"]["costs"]["dated_schedule_sha256"])
    caps = {s.base_profile.volume_participation_cap for s in schedules.values()}
    if len(caps) != 1:
        raise DiagnosticRefused(f"scenarios disagree on the participation cap {caps}")
    check_scope(doc, spec=spec, spec_sha256=C.sha(spec_path), core_sha=ident["core_git_sha"],
                schedule_sha256=C.SCHEDULE_SHA256, pins=PINS, window=dict(C.WINDOW))
    if ident.get("core_tree_dirty"):
        raise DiagnosticRefused("Core tree is dirty")
    staged = R.stage(settings["recovery_archive"], settings["original_preparation_archive"],
                     Path(settings["local_staging"]))
    cfg = {"snapshot_binding": {k: staged["bundle"][k] for k in ("manifest_relative_path", "manifest_sha256", "layout",
                                                                  "integrity_receipt", "integrity_receipt_sha256",
                                                                  "snapshot_pin")}}
    cfg["snapshot_binding"]["diagnostic_parquet_relative_path"] = "tsm_development_1m.parquet"
    binding = P.SnapshotBinding.from_config(cfg)
    w, dev, hold = doc["scope"]["window"], doc["scope"]["dataset"]["development_boundary"], doc["scope"]["holdout"]

    def day(iso, minus=0):
        return (datetime.fromisoformat(iso).date() - timedelta(days=minus)).isoformat()
    dates = (day(w["warmup_start"]), day(w["end"], 1))                       # [start, end) -> inclusive dates
    loaded = P.load_continuous_native(native_mod, staged["local_root"], binding, dates=dates,
                                      research_window=(day(dev["start"]), day(dev["end"], 1)),
                                      holdout=((day(hold["start"]), day(hold["end"], 1)),))
    core = _core()
    frame, frame_info = P.execution_frame(core, loaded, spec, doc["scope"]["strategy"]["parameters"])
    result = diagnose(core, frame, spec=spec, parameters=doc["scope"]["strategy"]["parameters"],
                      participation_cap=caps.pop(), window_start=w["start"], window_end=w["end"])
    result["inputs"] = {"authorization_id": doc["authorization_id"], "authorization_document_sha256": sha256_of(doc),
                        "approval_evidence_comment": evidence.get("comment"), "core_identity": ident,
                        "agent_code_sha": settings["code_sha"], "data_pins": PINS, "execution_frame": frame_info,
                        "holdout_accessed": False, "batch_consumed": False, "core_execution_engine_run": False,
                        "finished_at_utc": datetime.now(timezone.utc).isoformat()}
    assert_no_performance_fields(result)
    files = {"ta01_fill_diagnostic.json": write_once(out_dir / "ta01_fill_diagnostic.json",
                                                     (json.dumps(result, indent=2, sort_keys=True, default=str) + "\n").encode())}
    write_once(out_dir / "MANIFEST.json", (json.dumps({"files_sha256": files}, indent=2) + "\n").encode())
    print(json.dumps({k: result[k] for k in ("status", "entry", "exit", "delayed_open")}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
