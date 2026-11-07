"""Phase 5-B1 CLI (production books, live GitHub verifier only; no fixture or test source can be selected here).

  python -m strategy_agent.binding approval-line   --request R.json --intent BIND_AUTHORIZATION --authorization RA-...
  python -m strategy_agent.binding verify-approval --request R.json --intent ... --evidence-ref URL [--authorization RA] [--task-id T]
  python -m strategy_agent.binding bind-authorization --request R.json --provenance R_provenance.json --proposal P.json \
        --authorization-id RA-... --evidence-ref URL [--dry-run | --write]      (default: dry run, nothing written)
  python -m strategy_agent.binding validate-bound-request --request B.json
  python -m strategy_agent.binding inspect-binding [--bound-sha H | --source-sha H]

``approval-line`` only PRINTS the exact line a research lead may post in their own GitHub comment; nothing here
posts, approves on anyone's behalf, creates an authorization or consumes a batch.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _req(p):
    from strategy_agent.orchestration import request as Q  # noqa: PLC0415
    return Q.load(p)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m strategy_agent.binding")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("approval-line", "verify-approval"):
        x = sub.add_parser(name)
        x.add_argument("--request", required=True)
        x.add_argument("--intent", required=True, choices=["BIND_AUTHORIZATION", "APPROVE_TASK"])
        x.add_argument("--authorization"); x.add_argument("--task-id")
        if name == "approval-line":
            x.add_argument("--hours", type=int, default=48)
        else:
            x.add_argument("--evidence-ref", required=True)
    b = sub.add_parser("bind-authorization")
    for f in ("--request", "--provenance", "--proposal", "--authorization-id", "--evidence-ref"):
        b.add_argument(f, required=True)
    g = b.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", default=True)
    g.add_argument("--write", action="store_true")
    v = sub.add_parser("validate-bound-request"); v.add_argument("--request", required=True)
    i = sub.add_parser("inspect-binding"); i.add_argument("--bound-sha"); i.add_argument("--source-sha")
    a = ap.parse_args(argv)

    from strategy_agent.approval import ApprovalRejected, Expectation, production_verifier  # noqa: PLC0415
    from strategy_agent.approval.tokens import approval_line, scope_sha256  # noqa: PLC0415
    from strategy_agent.orchestration import request as Q  # noqa: PLC0415
    rc = 0
    if a.cmd == "approval-line":
        doc = _req(a.request)
        aid = a.authorization or (doc["authorization_ref"]["authorization_id"] if isinstance(doc.get("authorization_ref"), dict) else None)
        if not aid:
            print(json.dumps({"error": "AUTHORIZATION_REQUIRED"})); return 2
        exp = (datetime.now(timezone.utc) + timedelta(hours=a.hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        out = {"post_this_line_yourself": approval_line(intent=a.intent, request_sha256=Q.request_hash(doc),
                                                        authorization_id=aid, scope=scope_sha256(doc), task_id=a.task_id,
                                                        expires_utc=exp),
               "note": "Post it in your OWN GitHub comment on this repository (web UI; not via an AI agent / GitHub App)."}
    elif a.cmd == "verify-approval":
        doc = _req(a.request)
        e = Expectation.for_request(doc, intent=a.intent, task_id=a.task_id)
        if a.authorization:
            e = Expectation(e.intent, e.request_sha256, a.authorization, e.scope_sha256, e.task_id)
        try:
            out = {"result": "PASS", "evidence": production_verifier().verify(a.evidence_ref, e)}
        except ApprovalRejected as err:
            out, rc = {"result": "REJECTED", "code": err.code, "message": str(err), "record": err.record}, 1
    elif a.cmd == "bind-authorization":
        from .service import AuthorizationBindingService, BindingRejected  # noqa: PLC0415
        svc = AuthorizationBindingService(verifier=production_verifier())
        try:
            out = svc.bind(dry_run=not a.write, source_request_path=Path(a.request), provenance_path=Path(a.provenance),
                           proposal_path=Path(a.proposal), authorization_id=a.authorization_id, evidence_url=a.evidence_ref)
        except BindingRejected as err:
            out, rc = {"status": "REJECTED", "failures": err.failures}, 1
    elif a.cmd == "validate-bound-request":
        from strategy_agent.orchestration.governance import Governance  # noqa: PLC0415
        from .store import BindingStore  # noqa: PLC0415
        doc = _req(a.request)
        ok, detail = BindingStore(Q.ROOT).verify_bound(doc, require_production=True)
        dec = Governance(Q.ROOT).check(doc)
        out = {"bound": ok, "binding": detail, "schema_errors": Q.validate(doc),
               "governance": {c["id"]: c["status"] for c in dec.checks}, "governance_status": dec.status}
        rc = 0 if ok else 1
    else:
        from .service import inspect  # noqa: PLC0415
        out = inspect(Q.ROOT, bound_sha=a.bound_sha, source_sha=a.source_sha)
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    return rc


if __name__ == "__main__":
    sys.exit(main())
