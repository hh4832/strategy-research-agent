"""Research-lead tool to approve or revoke a holdout lock. NOT for agents.

The runner never imports this module (enforced by tests). The resulting line
only takes effect once merged through a CODEOWNERS-reviewed PR.

    python -m strategy_agent.governance.holdout_admin approve \
        --lock-id HOLDOUT-TSM-15M-v1 --dataset ibkr_tsm_1m_normalized \
        --snapshot 20261101_205431_06359faf7ef4_IBKR_FINAL_GATE_FULL \
        --start 2025-07-01T00:00:00+00:00 --approved-by hh4832 \
        --approval-ref https://app.notion.com/p/...
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from strategy_agent.registry.chain import ChainedLog

from .holdout import DEFAULT_APPROVERS, DEFAULT_LOCKS, HoldoutBook


def approve(locks: Path, approvers: Path, *, lock_id: str, dataset_id: str, snapshot_id: str, start: str,
            end: str | None, approved_by: str, approval_ref: str, notes: str = "") -> dict:
    allowed = set(json.loads(approvers.read_text(encoding="utf-8"))["research_leads"])
    if approved_by not in allowed:
        raise PermissionError(f"{approved_by} is not an allow-listed research lead")
    for t in filter(None, (start, end)):
        if datetime.fromisoformat(t).tzinfo is None:
            raise ValueError("timestamps need a UTC offset")
    rec = {"action": "APPROVE", "lock_id": lock_id, "dataset_id": dataset_id, "snapshot_id": snapshot_id,
           "holdout_start": start, "holdout_end": end, "approved_by": approved_by, "approval_ref": approval_ref,
           "approved_at": datetime.now(timezone.utc).isoformat(), "notes": notes}
    return _append_validated(locks, allowed, rec)


def revoke(locks: Path, approvers: Path, *, lock_id: str, approved_by: str, approval_ref: str, notes: str) -> dict:
    allowed = set(json.loads(approvers.read_text(encoding="utf-8"))["research_leads"])
    if approved_by not in allowed:
        raise PermissionError(f"{approved_by} is not an allow-listed research lead")
    return _append_validated(locks, allowed, {"action": "REVOKE", "lock_id": lock_id, "approved_by": approved_by,
                                              "approval_ref": approval_ref, "notes": notes,
                                              "approved_at": datetime.now(timezone.utc).isoformat()})


def _append_validated(locks: Path, allowed: set[str], rec: dict) -> dict:
    """Dry-run the whole book with the new event first: an invalid event is never written."""
    log = ChainedLog(locks)
    HoldoutBook.build(log.verify() + [rec], allowed)
    return log.append(rec)


def main(argv=None) -> int:  # pragma: no cover
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("approve")
    for f in ("lock-id", "dataset", "snapshot", "start", "approved-by", "approval-ref"):
        a.add_argument(f"--{f}", required=True)
    a.add_argument("--end")
    a.add_argument("--notes", default="")
    r = sub.add_parser("revoke")
    for f in ("lock-id", "approved-by", "approval-ref", "notes"):
        r.add_argument(f"--{f}", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "approve":
        rec = approve(DEFAULT_LOCKS, DEFAULT_APPROVERS, lock_id=args.lock_id, dataset_id=args.dataset,
                      snapshot_id=args.snapshot, start=args.start, end=args.end, approved_by=args.approved_by,
                      approval_ref=args.approval_ref, notes=args.notes)
    else:
        rec = revoke(DEFAULT_LOCKS, DEFAULT_APPROVERS, lock_id=args.lock_id, approved_by=args.approved_by,
                     approval_ref=args.approval_ref, notes=args.notes)
    print(json.dumps(rec, indent=2, ensure_ascii=False))
    print("\nCommit governance/holdout_locks.jsonl in a PR; it takes effect only after CODEOWNERS approval.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
