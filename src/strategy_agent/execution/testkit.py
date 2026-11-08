"""Layer A SYNTHETIC world for 5-B2 (no Core): B1 binding world + verified (TEST_FIXTURE) approval + controlled adapter.

Proposal (A2) -> PENDING request -> verified binding -> bound request -> Orchestrator (isolated books) with a
ControlledRunnerAdapter over the labelled SYNTHETIC_FIXTURE archive backend. Everything lives under ``work``.
"""
from __future__ import annotations

import json
from pathlib import Path

from strategy_agent.approval.testkit import FixtureApprovals
from strategy_agent.binding import testkit as K
from strategy_agent.orchestration.orchestrator import Orchestrator
from strategy_agent.phase5a import acceptance as A5

from .budget import BudgetLedger
from .controlled import ControlledRunnerAdapter, SyntheticFixtureBackend


def world(work: Path, *, max_batches: int = 1, backend=None) -> dict:
    work = Path(work)
    w = K.build_world(work / "w", max_batches=max_batches)
    fa = FixtureApprovals()
    svc = K.service(w, fa.verifier)
    r = svc.bind(dry_run=False, **K.bind_kwargs(w, fa.bind_url(w["request"], w["authorization_id"])))
    bound = json.loads(Path(r["bound_request_path"]).read_text(encoding="utf-8"))
    backend = backend or SyntheticFixtureBackend(A5.FIX / A5.FIX_RUN)
    return {**w, "fa": fa, "binding": r["record"], "bound": bound, "backend": backend, "work": work}


def orchestrator(W: dict, name: str = "s", *, worker: str = "worker-1", clock=None, backend=None) -> Orchestrator:
    ad = ControlledRunnerAdapter(backend or W["backend"], governance_root=W["gov"])
    return Orchestrator(W["work"] / name, W["work"] / "out", root=W["art"], governance_root=W["gov"],
                        benchmark=K.synthetic_benchmark, approval_verifier=W["fa"].verifier, controlled_adapter=ad,
                        clock=clock or A5.StepClock(), worker=worker)


def submit_and_approve(W: dict, o: Orchestrator) -> str:
    s = o.submit(W["bound"], actor="TEST_FIXTURE_SUBMITTER", actor_kind="TEST_FIXTURE")
    if s["state"] != "PENDING_APPROVAL":
        raise RuntimeError(f"bound synthetic request not accepted: {s['state']} {s.get('governance')}")
    W["fa"].approve_task(o, s["task_id"])
    return s["task_id"]


def ledger_for(gov: Path, *, clock, git_commit: str = "a" * 40) -> BudgetLedger:
    gov = Path(gov)
    return BudgetLedger(authorizations_path=gov / "governance/research_authorizations.jsonl",
                        approvers_path=gov / "governance/approvers.json",
                        uses_path=gov / "registry/research_authorization_uses.jsonl",
                        events_path=gov / "registry/research_budget_events.jsonl", clock=clock, git_commit=git_commit)


def mp_reserve(args: tuple) -> str:
    """Multiprocess worker (module level so ``spawn`` can import it): one reservation attempt per process."""
    gov, aid, i, worker = args
    from datetime import datetime, timezone  # noqa: PLC0415
    from .budget import BudgetRefused  # noqa: PLC0415
    L = ledger_for(Path(gov), clock=lambda: datetime.now(timezone.utc))
    try:
        r = L.reserve(authorization_id=aid, request_sha256=f"{i:064x}", task_id=f"T-{i:020x}", attempt_id="1",
                      approval_evidence_sha256="e" * 64, binding_id=None, worker_id=worker, planned_run_id=f"run_{i}")
        return "RESERVED:" + r["reservation_id"]
    except BudgetRefused as e:
        return "REFUSED:" + e.code
