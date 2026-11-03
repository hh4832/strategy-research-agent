"""Command line for the M3-B research runner.

    python -m strategy_agent.research readiness [--market IBKR_TSM]
    python -m strategy_agent.research synthetic --run-root DIR [--archive-root DIR | --drive] [--repeat N]
                                                [--cost-sensitivity] [--batch-id ID] [--resume]
    python -m strategy_agent.research real --market IBKR_TSM          # fails closed until the M3-A gate is GO

Exit codes: 0 all runs COMPLETED + ARCHIVED (+ reproducible when --repeat > 1); 2 a run did not complete, an
archive failed (incl. Drive unavailable) or reruns differed; 3 the market gate is closed.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import synthetic_setup
from .archive import DRIVE_ROOT
from .batch import MAX_BATCH_RUNS, cost_sensitivity_jobs, run_batch
from .data import GatedMarketSource, MarketGateClosed, readiness


def _readiness(a) -> int:
    r = readiness(a.market)
    miss = r.missing()
    print(json.dumps({"market": a.market, "status": r.status, "go": not miss, "missing": miss}, indent=1,
                     ensure_ascii=False))
    return 0 if not miss else 3


def _real(a) -> int:
    try:
        GatedMarketSource(a.market).check()
    except MarketGateClosed as e:
        print(f"REAL-MARKET RUN NOT STARTED: market gate {e.market} is {e.status}")
        for m in e.missing:
            print(f"  - {m}")
        return 3
    print("market gate is GO, but the real-market loader and formal run are enabled only after review of the "
          "M3-A report (M3-B2); nothing was run")
    return 3


def _synthetic(a) -> int:
    archive_root = Path(a.archive_root) if a.archive_root else (DRIVE_ROOT if a.drive else None)
    runner, job = synthetic_setup(Path(a.run_root), archive_root=archive_root, require_drive=a.drive, progress=print)
    jobs = cost_sensitivity_jobs(job) if a.cost_sensitivity else [job] * a.repeat
    if len(jobs) > MAX_BATCH_RUNS:
        print(f"refusing: {len(jobs)} runs > MAX_BATCH_RUNS {MAX_BATCH_RUNS}")
        return 2
    batch_id = a.batch_id or datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y%m%d_%H%M%S")
    ledger = (archive_root if archive_root and Path(archive_root).parent.exists() else Path(a.run_root)) / "_batches"
    out = run_batch(runner, jobs, batch_id=batch_id, ledger_dir=Path(ledger), resume=a.resume)
    ok = all(o.status in ("COMPLETED", "SKIPPED_RESUME") and o.archive_status in ("ARCHIVED", None) for o in out)
    ok = ok and all(o.archive_status == "ARCHIVED" for o in out if o.status == "COMPLETED") if archive_root else ok
    summary = {"batch_id": batch_id, "ledger": str(Path(ledger) / f"batch_{batch_id}.jsonl"),
               "runs": [o.__dict__ for o in out]}
    if a.repeat > 1 and not a.cost_sensitivity:
        shas = {o.metrics_sha256 for o in out if o.status == "COMPLETED"}
        idents = {o.identity for o in out if o.status == "COMPLETED"}
        summary["reproducible"] = len(shas) == 1 and len(idents) == 1
        ok = ok and summary["reproducible"]
    print(json.dumps(summary, indent=1, ensure_ascii=False, default=str))
    return 0 if ok else 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m strategy_agent.research")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("readiness")
    r.add_argument("--market", default="IBKR_TSM")
    s = sub.add_parser("synthetic")
    s.add_argument("--run-root", required=True, help="isolated synthetic governance + registry directory")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--archive-root")
    g.add_argument("--drive", action="store_true", help=f"archive to {DRIVE_ROOT} (fails if Drive is not mounted)")
    s.add_argument("--repeat", type=int, default=1)
    s.add_argument("--cost-sensitivity", action="store_true")
    s.add_argument("--batch-id")
    s.add_argument("--resume", action="store_true")
    x = sub.add_parser("real")
    x.add_argument("--market", default="IBKR_TSM")
    a = ap.parse_args(argv)
    return {"readiness": _readiness, "synthetic": _synthetic, "real": _real}[a.cmd](a)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
