"""Phase 5-C2 (C2-4 W-7, C2-5): dataset exposure log and evidence index.

Two append-only, hash-chained logs (``registry.chain.ChainedLog``; guarded in CI by ``append_only_guard``):

* ``registry/dataset_exposure.jsonl``: one line per authorized data read (diagnostic or performance) of a dataset
  window. Exposure is *derived* from these lines: a window that overlaps any line is EXPOSED, and evidence produced
  on an EXPOSED window can be at most EXPLORATORY_ONLY. A HOLDOUT line is a governance failure unless the Holdout
  was unlocked (no unlock process exists in this framework version).
* ``registry/evidence_index.jsonl``: one line per research artifact (authorization use, result, disposition) with
  where it lives, its SHA-256 and the approval behind it. ``repo_refs`` pin repository files by SHA-256 so the index
  can be verified offline; Drive results are referenced by their SHA-256 as recorded at run time.

Read-only helpers only; lines are appended through ``ChainedLog.append`` by a reviewed PR.
See docs/governance/EVIDENCE_AND_EXPOSURE.md.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from strategy_agent.registry.chain import ChainedLog

ROOT = Path(__file__).resolve().parents[3]
EXPOSURE_LOG = "registry/dataset_exposure.jsonl"
INDEX_LOG = "registry/evidence_index.jsonl"
HOLDOUT_LOCKS = "governance/holdout_locks.jsonl"

PARTITIONS = ("DEVELOPMENT", "HOLDOUT")
USES = ("DIAGNOSTIC", "PERFORMANCE")
EVIDENCE_LEVELS = ("EXPLORATORY_ONLY", "CONFIRMATORY")
STAGES = ("HYPOTHESIS", "PROPOSAL", "REVIEW", "AUTHORIZATION", "FEASIBILITY", "PERFORMANCE", "INTERPRETATION",
          "DISPOSITION")
EXPOSURE_FIELDS = ("exposure_id", "dataset_id", "partition", "window", "case_id", "authorization_id", "run_ref",
                   "use", "used_at", "evidence_index_id", "recorded_by", "backfilled")
INDEX_FIELDS = ("entry_id", "case_id", "stage", "authorization_id", "result", "approval_url", "agent_sha",
                "core_sha", "evidence_level", "repo_refs", "notes", "recorded_by", "backfilled")


class LedgerError(ValueError):
    pass


def _ts(s: str) -> datetime:
    d = datetime.fromisoformat(s)
    if d.tzinfo is None:
        raise LedgerError(f"timestamp without offset: {s}")
    return d


def _overlap(a: dict, b: dict) -> bool:
    return _ts(a["start"]) < _ts(b["end"]) and _ts(b["start"]) < _ts(a["end"])


def read(rel: str, root: Path = ROOT) -> list[dict]:
    p = Path(root) / rel
    return ChainedLog(p).verify() if p.exists() else []


def validate_exposure(rec: dict) -> list[str]:
    errs = [f"missing {k}" for k in EXPOSURE_FIELDS if k not in rec]
    if errs:
        return errs
    if rec["partition"] not in PARTITIONS:
        errs.append(f"partition {rec['partition']!r}")
    if rec["use"] not in USES:
        errs.append(f"use {rec['use']!r}")
    try:
        if _ts(rec["window"]["start"]) >= _ts(rec["window"]["end"]):
            errs.append("window start >= end")
    except (KeyError, TypeError, ValueError) as e:
        errs.append(f"window: {e}")
    return errs


def validate_index(rec: dict) -> list[str]:
    errs = [f"missing {k}" for k in INDEX_FIELDS if k not in rec]
    if errs:
        return errs
    if rec["stage"] not in STAGES:
        errs.append(f"stage {rec['stage']!r}")
    if rec["evidence_level"] not in EVIDENCE_LEVELS:
        errs.append(f"evidence_level {rec['evidence_level']!r}")
    for ref in rec["repo_refs"]:
        if set(ref) != {"path", "sha256"}:
            errs.append(f"repo_ref fields {sorted(ref)}")
    return errs


def exposures_overlapping(dataset_id: str, window: dict, root: Path = ROOT) -> list[dict]:
    return [r for r in read(EXPOSURE_LOG, root)
            if r["dataset_id"] == dataset_id and _overlap(r["window"], window)]


def exposure_status(dataset_id: str, window: dict, root: Path = ROOT) -> str:
    """EXPOSED if any authorized read overlapped this window, else UNEXPOSED."""
    return "EXPOSED" if exposures_overlapping(dataset_id, window, root) else "UNEXPOSED"


def max_evidence_level(dataset_id: str, window: dict, root: Path = ROOT) -> str:
    return "EXPLORATORY_ONLY" if exposure_status(dataset_id, window, root) == "EXPOSED" else "CONFIRMATORY"


def check_declared_exposure(dataset_id: str, window: dict, declared_status: str, requested_level: str,
                            root: Path = ROOT) -> list[str]:
    """Problems with what a proposal or authorization declares about its data (used by C2-7 / authorizations)."""
    actual = exposure_status(dataset_id, window, root)
    problems = []
    if declared_status != actual:
        problems.append(f"declared exposure {declared_status} but the exposure log says {actual}")
    if requested_level not in EVIDENCE_LEVELS:
        problems.append(f"unknown evidence level {requested_level!r}")
    elif requested_level == "CONFIRMATORY" and actual == "EXPOSED":
        problems.append("CONFIRMATORY evidence requested on an EXPOSED window")
    return problems


def holdout_problems(root: Path = ROOT) -> list[str]:
    """Exposure lines inside a locked Holdout window (no unlock process exists: every such line is a failure)."""
    locks = [r for r in read(HOLDOUT_LOCKS, root) if r.get("action") == "APPROVE"]
    out = []
    for e in read(EXPOSURE_LOG, root):
        if e["partition"] == "HOLDOUT":
            out.append(f"{e['exposure_id']}: HOLDOUT partition recorded")
        for lock in locks:
            hw = {"start": lock["holdout_start"], "end": lock["holdout_end"]}
            if e["dataset_id"] == lock["dataset_id"] and _overlap(e["window"], hw):
                out.append(f"{e['exposure_id']}: overlaps locked Holdout {lock.get('lock_id', '?')}")
    return out


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify(root: Path = ROOT) -> list[str]:
    """Offline verification of both logs: schema, unique ids, repo file pins, cross references, Holdout."""
    root = Path(root)
    problems = []
    exposure, index = read(EXPOSURE_LOG, root), read(INDEX_LOG, root)
    for r in exposure:
        problems += [f"exposure {r.get('exposure_id')}: {e}" for e in validate_exposure(r)]
    for r in index:
        problems += [f"index {r.get('entry_id')}: {e}" for e in validate_index(r)]
        for ref in r.get("repo_refs", []):
            p = root / ref["path"]
            if not p.is_file():
                problems.append(f"index {r['entry_id']}: missing {ref['path']}")
            elif sha256_file(p) != ref["sha256"]:
                problems.append(f"index {r['entry_id']}: {ref['path']} sha256 changed")
    for name, recs, key in (("exposure", exposure, "exposure_id"), ("index", index, "entry_id")):
        ids = [r.get(key) for r in recs]
        if len(ids) != len(set(ids)):
            problems.append(f"{name}: duplicate ids")
    index_ids = {r["entry_id"] for r in index}
    for r in exposure:
        if r["evidence_index_id"] is not None and r["evidence_index_id"] not in index_ids:
            problems.append(f"exposure {r['exposure_id']}: unknown evidence_index_id {r['evidence_index_id']}")
    problems += holdout_problems(root)
    return problems


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin CLI
    problems = verify()
    for p in problems:
        print("LEDGER PROBLEM:", p)
    print("research ledger:", "FAIL" if problems else "PASS")
    return 1 if problems else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
