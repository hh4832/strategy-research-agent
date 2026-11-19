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
import re
import sys
import traceback
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
AUTHORIZATION_PATH = "docs/phase5c/ta01_fill_diagnostic_authorization.json"          # 0001 (kept unchanged)
AUTHORIZATION_PATHS = {
    "RA-IBKR_TSM-TA01_FILLDIAG-0001": AUTHORIZATION_PATH,
    "RA-IBKR_TSM-TA01_FILLDIAG-0002": "docs/phase5c/ta01_fill_diagnostic_authorization_0002.json",
    "RA-IBKR_TSM-TA01Q8_FILLDIAG-0001": "docs/phase5c/ta01q8_fill_diagnostic_authorization_0001.json",
}
# Retired ids are refused in every mode, whatever exists or is deleted on Drive (one-time use is not Drive-dependent).
RETIRED_AUTHORIZATIONS = {
    "RA-IBKR_TSM-TA01_FILLDIAG-0001": "PRE_DATA_INPUT_PATH_FAILURE (see docs/phase5c/ta01_fill_diagnostic_0001_disposition.json)",
    "RA-IBKR_TSM-TA01_FILLDIAG-0002": "USED: SUCCEEDED 2026-11-19 (MANIFEST ta01_fill_diagnostic.json sha256 "
                                      "683737a4ed94f106fe4b5dd0e8729c60fe54159e494a267542bea64ccde9d30b); no re-run",
}
OPEN_BLOCKERS = [
    {"id": "CORE_FULL_MINUTE_CAP_LOOKAHEAD", "status": "BLOCKER",
     "detail": "Core execution.py:413 rejects when qty > the fill bar's FULL-minute volume x cap; that volume is not "
               "known at the open. Blocks any strategy execution until fixed in Core; a smaller quantity does not "
               "resolve it. Counted separately in core_full_minute_cap_would_reject."},
]
DRIVE_OUTPUT_ROOT = "/content/drive/MyDrive/Quant_Research/strategy-research-agent/phase5c_ta01_fill_diagnostic"
ATTEMPT_WITNESS_DIR = "_attempt_witness"
APPROVAL_URL_RE = r"https://github\.com/hh4832/strategy-research-agent/(?:pull|issues)/[1-9][0-9]*#issuecomment-[1-9][0-9]*"
DIAGNOSTIC_ID = "P5C-TA01-FILL-DIAGNOSTIC-v1"
OUTPUT_KEYS = {"diagnostic_id", "status", "definitions", "data", "delayed_open", "entry", "exit", "by_decision_time",
               "exit_failures", "not_output", "inputs", "variant", "open_blockers"}
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
            "min_minute_volume_for_quantity": min_minute_volume(qty, participation_cap),
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
        "variant": _variant(spec), "open_blockers": OPEN_BLOCKERS,
    }
    assert_no_performance_fields(result)
    return result


def min_minute_volume(quantity: float, participation_cap) -> float | None:
    """Smallest 1m volume that lets ``quantity`` pass ``quantity <= volume x cap`` (8 units at 0.01 -> 800)."""
    return None if participation_cap is None else quantity / float(participation_cap)


def _variant(spec: dict) -> dict:
    from strategy_agent.signals.ta01 import check_sizing  # noqa: PLC0415
    rev = check_sizing(spec)
    return {"strategy_id": spec["strategy_id"], "variant": rev["variant"], "units": rev["units"],
            "labels": list(rev.get("labels", ())), "parent_strategy_id": rev.get("parent")}


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

def load_authorization(authorization_id: str = "RA-IBKR_TSM-TA01_FILLDIAG-0001", root: Path = ROOT, *,
                       allow_retired: bool = False) -> dict:
    """Load an authorization document by id. Retired ids (0001) are refused unless ``allow_retired`` (tests and the
    disposition record only); a retired id can never be prepared or run."""
    if authorization_id not in AUTHORIZATION_PATHS:
        raise DiagnosticRefused(f"unknown authorization id {authorization_id!r}")
    if authorization_id in RETIRED_AUTHORIZATIONS and not allow_retired:
        raise DiagnosticRefused(f"{authorization_id} is retired: {RETIRED_AUTHORIZATIONS[authorization_id]}")
    doc = json.loads((Path(root) / AUTHORIZATION_PATHS[authorization_id]).read_text(encoding="utf-8"))
    if doc.get("authorization_id") != authorization_id:
        raise DiagnosticRefused("authorization document id mismatch")
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


def check_approval_url(url) -> str:
    """Only a bare GitHub comment URL is accepted: no Markdown link, brackets, spaces, query or trailing text."""
    if not isinstance(url, str) or not re.fullmatch(APPROVAL_URL_RE, url):
        raise DiagnosticRefused("approval_url must be a bare URL like "
                                "https://github.com/hh4832/strategy-research-agent/pull/<n>#issuecomment-<id> "
                                "(Markdown links and surrounding text are refused)")
    return url


def verify_approval(doc: dict, url: str, verifier=None) -> dict:
    """Live GitHub verification (V01-V09 of approval.verifier): author hh4832 (login AND id), not via an App, not
    edited, not expired or revoked, exact approval line for THIS document."""
    from strategy_agent.approval.verifier import ApprovalEvidenceVerifier  # noqa: PLC0415
    v = verifier or ApprovalEvidenceVerifier.for_production()
    return v.verify(check_approval_url(url), approval_expectation(doc))


def check_scope(doc: dict, *, spec: dict, spec_sha256: str, core_sha: str, schedule_sha256: str, pins: dict,
                window: dict) -> None:
    s = doc["scope"]
    bad = [k for k, ok in {"spec_sha256": s["strategy"]["spec_file_sha256"] == spec_sha256,
                           "parameters": s["strategy"]["parameters"] == {k: v["default"] for k, v in
                                                                         spec["parameters"]["values"].items()},
                           "core": s["core_sha"] == core_sha, "cost_schedule": s["costs"]["dated_schedule_sha256"] == schedule_sha256,
                           "pins": s["dataset"]["reviewed_pins"] == pins, "window": s["window"] == window,
                           "quantity": float(s["strategy"]["quantity"]) == float(spec["position_sizing"]["units"]),
                           "strategy_id": s["strategy"].get("strategy_id") == spec["strategy_id"]}.items()
           if not ok]
    if bad:
        raise DiagnosticRefused(f"run differs from the authorized scope: {bad}")


# ---------------------------------------------------------------- pre-data checks (no market data is opened)

def _sha_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def precheck_inputs(recovery_archive: str, original_preparation_archive: str, *, pins: dict, metadata_fn) -> dict:
    """Path and metadata checks that open NO market data (no Parquet, no snapshot manifest or lineage body):

    * original preparation archive: ``metadata_fn`` (default ``c1_authorization.archive_metadata``: whitelisted JSON
      metadata only), personal pin-review reference present, observed pins == reviewed pins;
    * recovery archive: is a directory; ``RECOVERY_VERIFIED.json`` (status PASS) and ``recovery_provenance.json``
      exist and match the reviewed marker / provenance SHA-256; the snapshot directory and its ``binding.json`` exist
      and ``binding.json`` matches the reviewed binding SHA-256; the binding's manifest file exists (existence only).
    Any failure raises ``PreDataInputFailure`` before an output directory or attempt marker is created."""
    from strategy_agent.execution.c1_integration import SNAPSHOT  # noqa: PLC0415
    found = {}
    try:
        meta = metadata_fn(original_preparation_archive)
        if not meta["context"].get("pin_review_evidence_ref"):
            raise PreDataInputFailure("ORIGINAL_PREPARATION_ARCHIVE: personal pin-review reference missing")
        if meta["pins"]["observed_pins"] != pins:
            raise PreDataInputFailure("ORIGINAL_PREPARATION_ARCHIVE: observed pins differ from the reviewed pins")
        found["original_preparation_archive"] = "METADATA_VERIFIED"
    except PreDataInputFailure:
        raise
    except Exception as e:  # noqa: BLE001 - every metadata failure is a pre-data failure
        raise PreDataInputFailure(f"ORIGINAL_PREPARATION_ARCHIVE metadata check failed: {type(e).__name__}: {e}") from e
    root = Path(recovery_archive)
    if not root.is_dir():
        raise PreDataInputFailure(f"RECOVERY_ARCHIVE does not exist or is not a directory: {recovery_archive}")
    checks = [("RECOVERY_VERIFIED.json", "marker_sha256"), ("recovery_provenance.json", "recovery_provenance_sha256")]
    for name, pin in checks:
        p = root / name
        if not p.is_file():
            raise PreDataInputFailure(f"RECOVERY_ARCHIVE: {name} missing")
        if _sha_file(p) != pins[pin]:
            raise PreDataInputFailure(f"RECOVERY_ARCHIVE: {name} differs from the reviewed pin")
    if json.loads((root / "RECOVERY_VERIFIED.json").read_text()).get("status") != "PASS":
        raise PreDataInputFailure("RECOVERY_ARCHIVE: RECOVERY_VERIFIED status is not PASS")
    binding = root / SNAPSHOT / "binding.json"
    if not binding.is_file() or _sha_file(binding) != pins["binding_sha256"]:
        raise PreDataInputFailure("RECOVERY_ARCHIVE: snapshot binding.json missing or differs from the reviewed pin")
    manifest_rel = json.loads(binding.read_text()).get("manifest_relative_path")
    if not isinstance(manifest_rel, str) or ".." in Path(manifest_rel).parts or Path(manifest_rel).is_absolute():
        raise PreDataInputFailure("RECOVERY_ARCHIVE: unsafe manifest path in binding.json")
    if not (root / SNAPSHOT / manifest_rel).is_file():
        raise PreDataInputFailure("RECOVERY_ARCHIVE: snapshot manifest file missing")
    found["recovery_archive"] = "PATHS_AND_METADATA_VERIFIED"
    return found


class PreDataInputFailure(DiagnosticRefused):
    """Input path / metadata failure detected before any market data could be opened."""


# ---------------------------------------------------------------- one-time attempt protocol (append-only)

def sanitize(text: str, secrets: tuple[str, ...] = ()) -> str:
    """Remove credentials from text that will be printed or archived."""
    out = str(text)
    for sec in secrets:
        if sec:
            out = out.replace(sec, "[REDACTED]")
    out = re.sub(r"(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})", "[REDACTED]", out)
    out = re.sub(r"(?i)\b(bearer|token)\s+(?!\[REDACTED\])[A-Za-z0-9_\-\.]{8,}", r"\1 [REDACTED]", out)
    out = re.sub(r"(?i)\b(authorization|token|password|secret)(\s*[:=]\s*)(?!\[REDACTED\]|RA-[A-Z0-9_]+(?:-[A-Z0-9_]+)*-[0-9]{4}\b)\S+",
                 r"\1\2[REDACTED]", out)          # authorization ids (RA-...-NNNN) are not credentials
    return out


def _json_bytes(obj) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True, default=str) + "\n").encode()


def attempt_paths(drive_root: Path, authorization_id: str) -> tuple[Path, Path]:
    return (Path(drive_root) / ATTEMPT_WITNESS_DIR / f"{authorization_id}.ATTEMPT_STARTED.json",
            Path(drive_root) / authorization_id)


def run_attempt(doc: dict, *, settings: dict, drive_root: Path, verifier=None, precheck=None, scope_check=None,
                execute=None, now=None, secrets: tuple[str, ...] = ()) -> dict:
    """RUN protocol. Ordering is the safety property:

    1. typed confirmation, bare approval URL, inputs equal the authorized paths, retired ids refused;
    2. refuse if the attempt witness OR the output directory already exists (an empty directory counts);
    3. live approval verification, scope check (spec / Core / costs / pins / window) and ``precheck_inputs`` - none
       of them opens market data; a failure here creates NOTHING on Drive;
    4. durable attempt marker: witness file, then the output directory with ATTEMPT_STARTED.json (write-once);
    5. ``execute`` (the only step that may read Development data);
    6. success: result + MANIFEST.json (status SUCCEEDED), failure: FAILURE_MANIFEST.json (status FAILED, sanitized
       traceback) - both write-once, never overwritten; the exception is re-raised.
    """
    from .runner import write_once  # noqa: PLC0415
    now = now or (lambda: datetime.now(timezone.utc).isoformat())
    aid = doc["authorization_id"]
    if aid in RETIRED_AUTHORIZATIONS:
        raise DiagnosticRefused(f"{aid} is retired: {RETIRED_AUTHORIZATIONS[aid]}")
    if settings.get("confirm_authorization") != aid:
        raise DiagnosticRefused("type the full authorization id to confirm RUN")
    check_approval_url(settings.get("approval_url"))
    inputs = doc["scope"].get("inputs") or {}
    for key in ("recovery_archive", "original_preparation_archive"):
        if settings.get(key) != inputs.get(key):
            raise PreDataInputFailure(f"{key} differs from the path pinned in {aid}: {settings.get(key)!r}")
    witness, out_dir = attempt_paths(drive_root, aid)
    if witness.exists() or out_dir.exists():
        raise DiagnosticRefused(f"{aid} was already attempted (witness or output directory exists); "
                                "one attempt per authorization, a new authorization is required")
    evidence = verify_approval(doc, settings["approval_url"], verifier=verifier)
    if verifier is None and (evidence.get("approval_source") or {}).get("trust") != "PRODUCTION":
        raise DiagnosticRefused("approval evidence is not PRODUCTION (live GitHub, lead identity)")
    if scope_check is not None:
        scope_check()
    pre = precheck(settings["recovery_archive"], settings["original_preparation_archive"])
    started = {"authorization_id": aid, "status": "ATTEMPT_STARTED", "started_at_utc": now(),
               "agent_code_sha": settings.get("code_sha"), "authorization_document_sha256": sha256_of(doc),
               "approval_comment": evidence.get("comment"), "precheck": pre,
               "meaning": "from here on Development data may be read; this authorization is used up"}
    write_once(witness, _json_bytes(started))
    out_dir.mkdir(parents=False, exist_ok=False)
    started_sha = write_once(out_dir / "ATTEMPT_STARTED.json", _json_bytes(started))
    try:
        result = execute()
        assert_no_performance_fields(result)
        result_bytes = _json_bytes(result)
        files = {"ATTEMPT_STARTED.json": started_sha,
                 "ta01_fill_diagnostic.json": write_once(out_dir / "ta01_fill_diagnostic.json", result_bytes)}
        write_once(out_dir / "MANIFEST.json", _json_bytes({"authorization_id": aid, "status": "SUCCEEDED",
                                                           "finished_at_utc": now(), "files_sha256": files}))
        return result
    except BaseException as e:
        failure = {"authorization_id": aid, "status": "FAILED", "failed_at_utc": now(),
                   "error_type": type(e).__name__, "error": sanitize(str(e), secrets),
                   "traceback": sanitize("".join(traceback.format_exception(e)), secrets),
                   "files_sha256": {"ATTEMPT_STARTED.json": started_sha},
                   "meaning": "attempt used; Development data may have been opened; a new authorization is required"}
        write_once(out_dir / "FAILURE_MANIFEST.json", _json_bytes(failure))
        raise


# ---------------------------------------------------------------- Colab entry point (subprocess, see notebook)

def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--settings", required=True)
    a = ap.parse_args(argv)
    settings = json.loads(Path(a.settings).read_text(encoding="utf-8"))
    import os  # noqa: PLC0415
    secrets = tuple(v for v in (os.environ.get("GITHUB_TOKEN"),) if v)
    try:
        return _main(settings, secrets)
    except BaseException as e:  # print a sanitized, complete error for the notebook, then fail
        print("TA01_FILL_DIAGNOSTIC_ERROR " + sanitize("".join(traceback.format_exception(e)), secrets),
              file=sys.stderr)
        return 2


def _main(settings: dict, secrets) -> int:
    doc = load_authorization(settings.get("authorization_id", ""))
    if settings["mode"] == "PREPARE":                     # no data, no Drive, no credential
        print(json.dumps({"authorization_id": doc["authorization_id"], "document_sha256": sha256_of(doc),
                          "scope_sha256": sha256_of(doc["scope"]),
                          "approval_line": approval_line(doc, settings["approval_expires_utc"]),
                          "status": doc["status"], "data_read": False}, indent=2))
        return 0
    if settings["mode"] != "RUN":
        raise DiagnosticRefused("mode must be PREPARE or RUN")
    if settings.get("drive_root") != DRIVE_OUTPUT_ROOT or doc["scope"]["inputs"]["drive_output_root"] != DRIVE_OUTPUT_ROOT:
        raise DiagnosticRefused("drive_root is fixed; changing it cannot bypass the one-attempt rule")
    result = run_production(doc, settings, secrets, drive_root=Path(DRIVE_OUTPUT_ROOT))
    print(json.dumps({k: result[k] for k in ("status", "entry", "exit", "delayed_open")}, indent=2, default=str))
    return 0


def run_production(doc: dict, settings: dict, secrets=(), *, drive_root: Path, verifier=None) -> dict:
    """The real RUN wiring (Core, reviewed pins, staging, loader). ``verifier`` and ``drive_root`` are injectable only
    for the synthetic end-to-end test; ``main`` always uses the live production verifier and the fixed Drive root."""
    from strategy_agent.execution import c1_integration as C  # noqa: PLC0415
    from strategy_agent.execution import c1_runner as R  # noqa: PLC0415
    from strategy_agent.readiness import c1_authorization as CA  # noqa: PLC0415
    from strategy_agent.adapters.mrc_phase4a import core_phase4a, core_repository_root  # noqa: PLC0415
    from strategy_agent.adapters.mrc_adapter import _core  # noqa: PLC0415
    from . import core_phase4a as P  # noqa: PLC0415
    pins = CA.PINS
    native_mod, _costs = core_phase4a()
    ident = native_mod.core_code_identity()
    spec_path = ROOT / doc["scope"]["strategy"]["spec_path"]
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    caps: set = set()

    def scope_check():
        if ident.get("core_tree_dirty"):
            raise DiagnosticRefused("Core tree is dirty")
        # The pinned schedule and its official SEC/FINRA source documents live in the Core checkout (as in C1).
        schedules = C.load_dated_schedules(core_repository_root(),
                                           expected_sha256=doc["scope"]["costs"]["dated_schedule_sha256"])
        caps.update(s.base_profile.volume_participation_cap for s in schedules.values())
        if len(caps) != 1:
            raise DiagnosticRefused(f"scenarios disagree on the participation cap {caps}")
        check_scope(doc, spec=spec, spec_sha256=C.sha(spec_path), core_sha=ident["core_git_sha"],
                    schedule_sha256=C.SCHEDULE_SHA256, pins=pins, window=dict(C.WINDOW))

    def execute():
        staged = R.stage(settings["recovery_archive"], settings["original_preparation_archive"],
                         Path(settings["local_staging"]))
        cfg = {"snapshot_binding": {k: staged["bundle"][k] for k in (
            "manifest_relative_path", "manifest_sha256", "layout", "integrity_receipt", "integrity_receipt_sha256",
            "snapshot_pin")}}
        cfg["snapshot_binding"]["diagnostic_parquet_relative_path"] = None      # footer diagnostics only (as C1)
        binding = P.SnapshotBinding.from_config(cfg)
        w, dev, hold = doc["scope"]["window"], doc["scope"]["dataset"]["development_boundary"], doc["scope"]["holdout"]

        def day(iso, minus=0):
            return (datetime.fromisoformat(iso).date() - timedelta(days=minus)).isoformat()
        dates = (day(w["warmup_start"]), day(w["end"], 1))                   # [start, end) -> inclusive dates
        loaded = P.load_continuous_native(native_mod, staged["local_root"], binding, dates=dates,
                                          research_window=(day(dev["start"]), day(dev["end"], 1)),
                                          holdout=((day(hold["start"]), day(hold["end"], 1)),))
        core = _core()
        frame, frame_info = P.execution_frame(core, loaded, spec, doc["scope"]["strategy"]["parameters"])
        result = diagnose(core, frame, spec=spec, parameters=doc["scope"]["strategy"]["parameters"],
                          participation_cap=next(iter(caps)), window_start=w["start"], window_end=w["end"])
        result["inputs"] = {"authorization_id": doc["authorization_id"],
                            "authorization_document_sha256": sha256_of(doc), "core_identity": ident,
                            "agent_code_sha": settings.get("code_sha"), "data_pins": pins,
                            "execution_frame": frame_info, "holdout_accessed": False, "batch_consumed": False,
                            "core_execution_engine_run": False}
        return result

    return run_attempt(doc, settings=settings, drive_root=Path(drive_root), scope_check=scope_check,
                       precheck=lambda r, o: precheck_inputs(r, o, pins=pins, metadata_fn=CA.archive_metadata),
                       execute=execute, secrets=tuple(secrets), verifier=verifier)


if __name__ == "__main__":
    sys.exit(main())
