"""Phase 5-A3 CLI. Wires A2 (proposal) into A1 (orchestrator); adds no way around governance.

  python -m strategy_agent.phase5a validate        --proposal P.json
  python -m strategy_agent.phase5a submit-proposal --proposal P.json --bindings B.json --export R.json \
        --artifact-root DIR --state-dir D --archive-root A --actor ID
  python -m strategy_agent.phase5a status          --task-id T --state-dir D --archive-root A
  python -m strategy_agent.phase5a replay          --task-id T --state-dir D --archive-root A --source-archive-root S
  python -m strategy_agent.phase5a acceptance      --out-dir OUT [--cloud] \
        [--real-archive-root S --approver ID --approval-ref https://...]

There is deliberately no approve / dispatch-any / force command here: approval stays with
``python -m strategy_agent.orchestration approve`` (allow-listed research lead + https reference + governance re-check),
``replay`` runs ARCHIVE_REPLAY tasks only (read-only, 0 batches), and ``submit-proposal`` uses a production
orchestrator, where an A2 request (authorization_ref always PENDING) is recorded and BLOCKED by G5.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from strategy_agent.orchestration.tasks import IllegalTransition


def _orch(a):
    from strategy_agent.orchestration.orchestrator import Orchestrator  # noqa: PLC0415
    return Orchestrator(a.state_dir, a.archive_root, source_archive_root=getattr(a, "source_archive_root", None))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m strategy_agent.phase5a")
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate"); v.add_argument("--proposal", required=True)
    s = sub.add_parser("submit-proposal")
    for f in ("--proposal", "--bindings", "--export", "--artifact-root", "--state-dir", "--archive-root", "--actor"):
        s.add_argument(f, required=True)
    for name in ("status", "replay"):
        x = sub.add_parser(name)
        x.add_argument("--task-id", required=True); x.add_argument("--state-dir", required=True)
        x.add_argument("--archive-root", required=True)
        if name == "replay":
            x.add_argument("--source-archive-root", required=True)
    ac = sub.add_parser("acceptance")
    ac.add_argument("--out-dir", required=True); ac.add_argument("--cloud", action="store_true")
    ac.add_argument("--real-archive-root"); ac.add_argument("--approver"); ac.add_argument("--approval-ref")
    a = p.parse_args(argv)
    try:
        if a.cmd == "validate":
            from strategy_agent.proposal.agent import validate_proposal  # noqa: PLC0415
            out = validate_proposal(json.loads(Path(a.proposal).read_text(encoding="utf-8")))
            print(json.dumps(out, indent=2, ensure_ascii=False))
            return 0 if out["valid"] else 2
        if a.cmd == "submit-proposal":
            from .integration import submit_proposal  # noqa: PLC0415
            out = submit_proposal(_orch(a), json.loads(Path(a.proposal).read_text(encoding="utf-8")),
                                  bindings=json.loads(Path(a.bindings).read_text(encoding="utf-8")),
                                  export_path=Path(a.export), artifact_root=Path(a.artifact_root), actor=a.actor,
                                  actor_kind="HUMAN")
        elif a.cmd == "status":
            from .integration import trace  # noqa: PLC0415
            out = trace(_orch(a), a.task_id)
        elif a.cmd == "replay":
            o = _orch(a)
            mode = o.request_of(a.task_id)["execution_mode"]
            if mode != "ARCHIVE_REPLAY":
                raise IllegalTransition("REPLAY_ONLY", f"replay runs ARCHIVE_REPLAY tasks only (task is {mode})")
            out = o.dispatch(a.task_id)
        else:
            from . import acceptance as ACC  # noqa: PLC0415
            real = None
            if a.real_archive_root:
                real = {"source_archive_root": Path(a.real_archive_root), "approver": a.approver,
                        "approval_ref": a.approval_ref}
            r = ACC.run(Path(a.out_dir), real_replay=real, cloud=a.cloud, fresh_process=True)
            out = {"verdict": r["verdict"], "gates": {k: g["status"] for k, g in r["gates"].items()},
                   "route_b_real": (r["route_b_real"] or {}).get("status", "NOT_RUN"), "out_dir": a.out_dir,
                   "files_sha256": r["files_sha256"]}
    except IllegalTransition as e:
        print(json.dumps({"error": e.code, "message": str(e)}, indent=2))
        return 2
    print(json.dumps(out, indent=2, default=str, ensure_ascii=False))
    if a.cmd == "acceptance":
        return 0 if out["verdict"]["verdict"] in ("GO", "SCOPED_GO") else 1
    return 1 if isinstance(out, dict) and (out.get("state") == "BLOCKED" or out.get("submitted") is False) else 0


if __name__ == "__main__":
    sys.exit(main())
