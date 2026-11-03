"""Append-only Experiment Registry (ARCHITECTURE §3.4, schema v1.1).

Each experiment is a sequence of events with the same ``experiment_id``.
Allowed lifecycle (anything else raises ``TransitionError``)::

    (new) ─► QUEUED ─► RUNNING ─► COMPLETED | FAILED | TIMEOUT | CANCELLED | REJECTED_CONTRACT
      │        │
      │        └────► CANCELLED | REJECTED_* | BUDGET_EXCEEDED
      └──► REJECTED_SPEC | REJECTED_DATA | REJECTED_CONTRACT | REJECTED_GOVERNANCE | BUDGET_EXCEEDED

Terminal states are final: no further event for that experiment is accepted.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Iterable

from jsonschema import Draft202012Validator

from .chain import ChainedLog

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "schemas" / "EXPERIMENT_RECORD_SCHEMA.json"
SCHEMA_VERSION = "1.1.0"

REJECTIONS = {"REJECTED_SPEC", "REJECTED_DATA", "REJECTED_CONTRACT", "REJECTED_GOVERNANCE", "BUDGET_EXCEEDED"}
TERMINAL = {"COMPLETED", "FAILED", "TIMEOUT", "CANCELLED"} | REJECTIONS
DATA_TOUCHED = {"RUNNING", "COMPLETED", "FAILED", "TIMEOUT", "CANCELLED"}  # reached the engine
TRANSITIONS: dict[str | None, set[str]] = {
    None: {"QUEUED"} | REJECTIONS,
    "QUEUED": {"RUNNING", "CANCELLED"} | REJECTIONS,
    "RUNNING": {"COMPLETED", "FAILED", "TIMEOUT", "CANCELLED", "REJECTED_CONTRACT"},
}


class TransitionError(RuntimeError):
    pass


class RecordInvalid(ValueError):
    pass


def new_experiment_id(now: datetime) -> str:
    return f"EXP-{now:%Y%m%d}-{uuid.uuid4().hex[:12]}"


def params_hash(params: dict) -> str:
    return hashlib.sha256(json.dumps(params, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class ExperimentRegistry:
    def __init__(self, path: str | Path):
        self.log = ChainedLog(path)
        self._validator = Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))

    # ------------------------------------------------------------- write

    def append(self, record: dict) -> dict:
        rec = dict(record, schema_version=SCHEMA_VERSION)
        errors = sorted(self._validator.iter_errors(rec), key=lambda e: list(e.path))
        if errors:
            raise RecordInvalid("; ".join(f"/{'/'.join(map(str, e.path))}: {e.message}" for e in errors[:5]))
        current = self.state(rec["experiment_id"])
        if rec["status"] not in TRANSITIONS.get(current, set()):
            raise TransitionError(f"{rec['experiment_id']}: {current} -> {rec['status']} not allowed")
        return self.log.append(rec)

    # -------------------------------------------------------------- read

    def records(self) -> list[dict]:
        return self.log.verify()

    def state(self, experiment_id: str) -> str | None:
        st = None
        for r in self.log.read():
            if r["experiment_id"] == experiment_id:
                st = r["status"]
        return st

    def events(self, experiment_id: str) -> list[dict]:
        return [r for r in self.records() if r["experiment_id"] == experiment_id]

    def final_records(self) -> list[dict]:
        """Last event per experiment."""
        last: dict[str, dict] = {}
        for r in self.records():
            last[r["experiment_id"]] = r
        return list(last.values())

    def family_records(self, family_id: str) -> list[dict]:
        return [r for r in self.final_records() if r["trial"]["family_id"] == family_id]

    def trial_count(self, family_id: str) -> int:
        """Distinct (structure, parameters) attempts in a family, for DSR's N.

        Conservative: every outcome counts (completed, failed, timed out,
        cancelled, rejected) except attempts stopped by governance/budget before
        any spec or data work. Pre-registered grid size is a floor.
        """
        keys, planned = set(), 0
        for r in self.family_records(family_id):
            planned = max(planned, int(r["trial"].get("family_size_at_registration") or 0))
            if r["status"] in {"REJECTED_GOVERNANCE", "BUDGET_EXCEEDED"}:
                continue
            keys.add((r["trial"].get("structure_sha256"), r["trial"].get("params_sha256")))
        return max(len(keys), planned)

    def holdout_accesses(self, lock_id: str) -> list[dict]:
        """Every experiment that reached QUEUED or later against a holdout lock."""
        return [r for r in self.final_records()
                if r.get("holdout_access") and r["split"].get("holdout_lock_id") == lock_id
                and r["status"] not in {"REJECTED_GOVERNANCE", "BUDGET_EXCEEDED"}]

    def count_status(self, statuses: Iterable[str], since: datetime | None = None) -> int:
        statuses = set(statuses)
        n = 0
        for r in self.records():
            if r["status"] in statuses and (since is None or datetime.fromisoformat(r["event_at"]) >= since):
                n += 1
        return n


