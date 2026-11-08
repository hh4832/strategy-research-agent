"""CLI. No command can approve on the orchestrator's behalf, consume a batch, unlock a holdout or start a real
backtest; ``replay`` only re-evaluates an existing archive read-only.

  python -m strategy_agent.orchestration validate --request req.json
  python -m strategy_agent.orchestration submit   --request req.json --state-dir D --archive-root A --actor hh4832
  python -m strategy_agent.orchestration approve  --task-id T --evidence-ref https://github.com/<repo>/pull/N#issuecomment-ID --state-dir D --archive-root A
      (5-B1: verified GitHub comment evidence via the live API; GH_TOKEN/GITHUB_TOKEN from the environment)
  python -m strategy_agent.orchestration status   --task-id T --state-dir D --archive-root A
  python -m strategy_agent.orchestration replay   --task-id T --state-dir D --archive-root A [--source-archive-root S]
  python -m strategy_agent.orchestration recover  --state-dir D --archive-root A
  python -m strategy_agent.orchestration cancel   --task-id T --actor hh4832 --reason R --state-dir D --archive-root A
"""
from __future__ import annotations

import argparse
import json
import sys

from . import request as Q
from .tasks import IllegalTransition


def _orch(a):
    from .orchestrator import Orchestrator  # noqa: PLC0415
    verifier = None
    if a.cmd in ("approve", "replay"):              # only the production (live GitHub) verifier, never a fixture
        from strategy_agent.approval import production_verifier  # noqa: PLC0415
        verifier = production_verifier()
    return Orchestrator(a.state_dir, a.archive_root, source_archive_root=getattr(a, "source_archive_root", None),
                        approval_verifier=verifier)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m strategy_agent.orchestration")
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate"); v.add_argument("--request", required=True)
    for name in ("submit", "approve", "status", "replay", "recover", "cancel"):
        s = sub.add_parser(name)
        s.add_argument("--state-dir", required=True)
        s.add_argument("--archive-root", required=True)
        if name == "submit":
            s.add_argument("--request", required=True); s.add_argument("--actor", required=True)
        if name in ("approve", "status", "replay", "cancel"):
            s.add_argument("--task-id", required=True)
        if name == "approve":
            s.add_argument("--evidence-ref", "--approval-ref", dest="evidence_ref", required=True)
            s.add_argument("--approver", help="optional; must equal the verified GitHub author")
        if name == "cancel":
            s.add_argument("--actor", required=True); s.add_argument("--reason", required=True)
        if name in ("replay", "recover"):
            s.add_argument("--source-archive-root")
    a = p.parse_args(argv)
    try:
        if a.cmd == "validate":
            doc = Q.load(a.request)
            errs = Q.validate(doc)
            out = {"valid": not errs, "errors": errs, "pending_fields": Q.pending_fields(doc) if not errs else [],
                   "request_hash": Q.request_hash(doc)}
        else:
            o = _orch(a)
            if a.cmd == "submit":
                out = o.submit(Q.load(a.request), actor=a.actor, actor_kind="HUMAN")
            elif a.cmd == "approve":
                out = o.approve(a.task_id, approver=a.approver, evidence_ref=a.evidence_ref)
            elif a.cmd == "status":
                out = o.status(a.task_id)
            elif a.cmd == "replay":
                mode = o.request_of(a.task_id)["execution_mode"]
                if mode != "ARCHIVE_REPLAY":
                    raise IllegalTransition("REPLAY_ONLY", f"replay runs ARCHIVE_REPLAY tasks only (task is {mode})")
                out = o.dispatch(a.task_id)
            elif a.cmd == "recover":
                out = o.recover()
            else:
                out = o.cancel(a.task_id, actor=a.actor, reason=a.reason)
    except IllegalTransition as e:
        print(json.dumps({"error": e.code, "message": str(e)}, indent=2))
        return 2
    print(json.dumps(out, indent=2, default=str, ensure_ascii=False))
    return 0 if not (isinstance(out, dict) and (out.get("valid") is False or out.get("state") == "BLOCKED")) else 1


if __name__ == "__main__":
    sys.exit(main())
