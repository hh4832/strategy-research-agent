"""Research-lead tool to approve or revoke a RESEARCH_ONLY_EXPLORATORY authorization. NOT for agents.

Same discipline as ``holdout_admin``: the approver must be allow-listed in
``governance/approvers.json``; the whole book is dry-run validated with the new
event BEFORE anything is appended, so an invalid event is never written; the
resulting line takes effect only once merged through a PR reviewed by the
research lead. No runner imports this module.

    python -m strategy_agent.governance.research_authorization_admin approve \
        --proposal governance/proposals/RA-IBKR_TSM-SMA520_SMOKE-0001.json \
        --approved-by hh4832 --approval-ref https://... [--expires-at ...]
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from strategy_agent.registry.chain import ChainedLog

from .research_authorization import DEFAULT_APPROVERS, DEFAULT_BOOK, validate_events

PROPOSAL_ONLY_FIELDS = ("proposal_status", "proposed_by", "proposed_at", "approval_instructions")


def _append_validated(book: Path, allowed: set[str], ev: dict) -> dict:
    log = ChainedLog(book)
    validate_events(log.verify() + [ev], allowed)
    return log.append(ev)


def approve(book: Path, approvers: Path, proposal: dict, *, approved_by: str, approval_ref: str,
            expires_at: str | None = None, now: datetime | None = None) -> dict:
    allowed = set(json.loads(Path(approvers).read_text(encoding="utf-8"))["research_leads"])
    if approved_by not in allowed:
        raise PermissionError(f"{approved_by} is not an allow-listed research lead")
    ev = {k: v for k, v in proposal.items() if k not in PROPOSAL_ONLY_FIELDS}
    ev.update(action="APPROVE", approved_by=approved_by, approval_ref=approval_ref,
              approved_at=(now or datetime.now(timezone.utc)).isoformat(timespec="seconds"))
    if expires_at:
        ev["expires_at"] = expires_at
    return _append_validated(book, allowed, ev)


def revoke(book: Path, approvers: Path, *, authorization_id: str, approved_by: str, approval_ref: str,
           reason: str, now: datetime | None = None) -> dict:
    allowed = set(json.loads(Path(approvers).read_text(encoding="utf-8"))["research_leads"])
    if approved_by not in allowed:
        raise PermissionError(f"{approved_by} is not an allow-listed research lead")
    return _append_validated(book, allowed, {
        "action": "REVOKE", "contract": "research-only-authorization-v1", "authorization_id": authorization_id,
        "approved_by": approved_by, "approval_ref": approval_ref, "reason": reason,
        "approved_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")})


def main(argv=None) -> int:  # pragma: no cover
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("approve")
    a.add_argument("--proposal", required=True)
    a.add_argument("--approved-by", required=True)
    a.add_argument("--approval-ref", required=True)
    a.add_argument("--expires-at")
    r = sub.add_parser("revoke")
    for k in ("authorization-id", "approved-by", "approval-ref", "reason"):
        r.add_argument(f"--{k}", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "approve":
        rec = approve(DEFAULT_BOOK, DEFAULT_APPROVERS, json.loads(Path(args.proposal).read_text(encoding="utf-8")),
                      approved_by=args.approved_by, approval_ref=args.approval_ref, expires_at=args.expires_at)
    else:
        rec = revoke(DEFAULT_BOOK, DEFAULT_APPROVERS, authorization_id=args.authorization_id,
                     approved_by=args.approved_by, approval_ref=args.approval_ref, reason=args.reason)
    print(json.dumps(rec, indent=2, ensure_ascii=False))
    print("\nCommit governance/research_authorizations.jsonl in a PR for research-lead review.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
