"""Bounded batch execution with progress, failure recording and resume.

* At most ``MAX_BATCH_RUNS`` jobs per batch (hard cap; this is not an open-ended search loop).
* Every job's outcome (including exceptions) is appended to ``<ledger_dir>/batch_<batch_id>.jsonl``.
* ``resume=True`` skips jobs whose run identity already has a COMPLETED + ARCHIVED entry in the same ledger;
  everything else is run again (a rerun gets a new experiment id and a new archive directory).
"""
from __future__ import annotations

import json
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from .pipeline import ResearchJob, ResearchRunner

MAX_BATCH_RUNS = 10


class BatchLimitExceeded(ValueError):
    pass


@dataclass(frozen=True)
class BatchOutcome:
    label: str
    identity: str | None
    status: str
    experiment_id: str | None
    archive_status: str | None
    archive_path: str | None
    metrics_sha256: str | None
    error: str | None = None


def cost_sensitivity_jobs(job: ResearchJob, multipliers: Sequence[float] | None = None) -> list[ResearchJob]:
    """The spec's pre-registered cost multipliers (default ``costs.sensitivity_multipliers``) for ONE fixed
    parameter set. Same parameters -> same family trial; nothing here searches parameters."""
    from dataclasses import replace  # noqa: PLC0415
    ms = list(multipliers if multipliers is not None else job.spec["costs"]["sensitivity_multipliers"])
    if any(m not in job.spec["costs"]["sensitivity_multipliers"] for m in ms):
        raise ValueError(f"multipliers {ms} not pre-registered in the spec {job.spec['costs']['sensitivity_multipliers']}")
    return [replace(job, cost_multiplier=float(m), label=f"{job.label or 'run'}_cost{m:g}x") for m in ms]


def _ledger(ledger_dir: Path, batch_id: str) -> Path:
    ledger_dir.mkdir(parents=True, exist_ok=True)
    return ledger_dir / f"batch_{batch_id}.jsonl"


def done_identities(path: Path) -> set[str]:
    if not path.exists():
        return set()
    out = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if r.get("status") == "COMPLETED" and r.get("archive_status") == "ARCHIVED":
            from .archive import verify_manifest, ArchiveError
            try:
                archive = Path(r["archive_path"])
                verify_manifest(archive)
                final = json.loads((archive / 'final_status.json').read_text())
                refs = json.loads((archive / 'snapshot_refs.json').read_text())
                if final.get('status') == 'PASS' and refs.get('run_identity') == r['identity']:
                    out.add(r['identity'])
            except (OSError, ValueError, KeyError, TypeError, ArchiveError):
                continue  # missing/corrupt evidence is rerun, never accepted as cached success
    return out


def run_batch(runner: ResearchRunner, jobs: Sequence[ResearchJob], *, batch_id: str, ledger_dir: Path,
              resume: bool = False, progress: Callable[[str], None] = print,
              identity_of: Callable[[ResearchJob], str] | None = None) -> list[BatchOutcome]:
    if len(jobs) > MAX_BATCH_RUNS:
        raise BatchLimitExceeded(f"{len(jobs)} jobs > MAX_BATCH_RUNS={MAX_BATCH_RUNS}")
    path = _ledger(Path(ledger_dir), batch_id)
    skip = done_identities(path) if resume else set()
    out: list[BatchOutcome] = []
    for i, job in enumerate(jobs, 1):
        label = job.label or f"job{i}"
        from . import pipeline
        if pipeline.R02_ENFORCED is not True:
            raise pipeline.ResearchContractError('R02 is mandatory, including resume')
        ident = identity_of(job) if identity_of else runner.identity_of(job)
        from strategy_agent.runner import parameter_issues
        # Resume is also a governed entry: a cached identity cannot mask invalid parameters.
        valid_parameters = not parameter_issues(job.spec, dict(job.parameters))
        if ident and ident in skip and valid_parameters:
            progress(f"[{i}/{len(jobs)}] {label}: SKIPPED (identity {ident[:12]} already COMPLETED+ARCHIVED)")
            o = BatchOutcome(label, ident, "SKIPPED_RESUME", None, None, None, None)
        else:
            progress(f"[{i}/{len(jobs)}] {label}: running")
            try:
                r = runner.run(job)
                o = BatchOutcome(label, r.identity, r.status, r.record["experiment_id"],
                                 (r.archive or {}).get("status"), (r.archive or {}).get("path"), r.metrics_sha256,
                                 "; ".join(f"{x['code']}: {x['message']}" for x in r.record.get("rejection_reasons", []))
                                 or None)
            except Exception as e:  # noqa: BLE001 - recorded, batch continues
                o = BatchOutcome(label, ident, "EXCEPTION", None, None, None, None,
                                 f"{type(e).__name__}: {e} | {traceback.format_exc(limit=2)}")
            progress(f"[{i}/{len(jobs)}] {label}: {o.status} archive={o.archive_status}"
                     + (f" error={o.error[:160]}" if o.error else ""))
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(), **o.__dict__}, sort_keys=True) + "\n")
        out.append(o)
    return out
