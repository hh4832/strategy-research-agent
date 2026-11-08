"""Layer A SYNTHETIC world for 5-B2 (no Core): B1 binding world + verified (TEST_FIXTURE) approval + controlled adapter.

Proposal (A2) -> PENDING request -> verified binding -> bound request -> Orchestrator (isolated books) with a
ControlledRunnerAdapter over the SYNTHETIC_STUB backend, whose execution identity is CONFIGURED here from the
authorization record, the artefact files on disk and the synthetic dataset pins (never from the request being
executed). Everything lives under ``work``.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from strategy_agent.approval.testkit import FixtureApprovals
from strategy_agent.binding import testkit as K
from strategy_agent.orchestration.orchestrator import Orchestrator
from strategy_agent.phase5a import acceptance as A5

from .budget import BudgetLedger
from .controlled import ControlledRunnerAdapter, SyntheticStubBackend


def _sha_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def stub_identity(w: dict) -> dict:
    """The stub's configured execution identity: authorization record + artefact files + synthetic dataset pins."""
    auth = next(json.loads(x) for x in (w["gov"] / "governance/research_authorizations.jsonl").read_text().splitlines()
                if x.strip() and json.loads(x).get("authorization_id") == w["authorization_id"])
    s, B = auth["strategy"], w["bindings"]
    return {"authorization_id": auth["authorization_id"], "strategy_id": s["strategy_id"], "strategy_version": s["version"],
            "strategy_parameters": s["parameters"], "strategy_spec_sha256": _sha_file(w["art"] / s["spec_path"]),
            "core_commit": K.CORE, "dataset_snapshot_id": auth["dataset"]["snapshot_id"],
            "dataset_manifest_sha256": B["dataset_manifest_sha256"],
            "cost_profile_sha256": _sha_file(w["art"] / auth["costs"]["scenario_set_path"]),
            "temporal_policy_sha256": auth["temporal_policy"]["sha256"],
            "research_window": dict(B["research_window"])}


def world(work: Path, *, max_batches: int = 1, backend=None, stub: dict | None = None) -> dict:
    work = Path(work)
    w = K.build_world(work / "w", max_batches=max_batches)
    fa = FixtureApprovals()
    svc = K.service(w, fa.verifier)
    r = svc.bind(dry_run=False, **K.bind_kwargs(w, fa.bind_url(w["request"], w["authorization_id"])))
    bound = json.loads(Path(r["bound_request_path"]).read_text(encoding="utf-8"))
    backend = backend or SyntheticStubBackend(A5.FIX / A5.FIX_RUN, identity=stub_identity(w), **(stub or {}))
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


def committed_running(W: dict, o: Orchestrator) -> dict:
    """Drive a task exactly as ``Orchestrator._dispatch_controlled`` does up to (not including) ``adapter.execute``:
    QUEUED -> precheck -> reserve -> commit -> RUNNING (claim nonce). Used to attack the in-flight window."""
    import uuid  # noqa: PLC0415

    from strategy_agent.orchestration import request as Q  # noqa: PLC0415
    tid = submit_and_approve(W, o)
    doc = o.request_of(tid)
    o.log.transition(tid, "QUEUED", actor="orchestrator", actor_kind="SYSTEM", idempotency_key=f"{tid}:QUEUED",
                     expect_from="APPROVED")
    ad = o.adapters["RESEARCH_ONLY_REAL"]
    if ad.precheck(doc):
        raise RuntimeError(f"precheck: {ad.precheck(doc)}")
    st = o.log.get(tid)
    attempt = st.attempt + 1
    o.budget.register_executor(o.worker, actor="orchestrator")
    r = o.budget.reserve(authorization_id=doc["authorization_ref"]["authorization_id"], request_sha256=Q.request_hash(doc),
                         task_id=tid, attempt_id=f"{tid}:{attempt}", approval_evidence_sha256=o._approval_evidence_sha(st),
                         binding_id=W["binding"]["binding_id"], worker_id=o.worker,
                         planned_run_id=ad.planned_run_id(tid, o.clock()))
    o.budget.commit(r["reservation_id"], worker_id=o.worker, actor=o.worker, scenarios=list(doc["cost_profile_ref"]["scenarios"]))
    o.log.transition(tid, "RUNNING", actor="orchestrator", actor_kind="SYSTEM", worker=o.worker, lease_seconds=900,
                     expect_from="QUEUED", data={"attempt": attempt, "claim": uuid.uuid4().hex,
                                                 "reservation_id": r["reservation_id"]},
                     idempotency_key=f"{tid}:RUNNING:{attempt}")
    return {"task_id": tid, "reservation_id": r["reservation_id"], "attempt_id": f"{tid}:{attempt}", "doc": doc}


def mp_execute(args: tuple) -> str:
    """Multiprocess replay worker (module level for ``spawn``): a FRESH orchestrator on the same books + task log
    replays one committed reservation through the public adapter."""
    state, out, art, gov, fixture, identity, calls_file, doc, rid, tid, aid, dst = args
    from strategy_agent.binding import testkit as BK  # noqa: PLC0415
    from strategy_agent.evaluation import phase4c as C  # noqa: PLC0415
    from strategy_agent.orchestration.adapters import ExecutionFailed  # noqa: PLC0415
    C.EXECUTOR_MODULES = ()
    be = SyntheticStubBackend(Path(fixture), identity=identity, calls_file=Path(calls_file))
    o = Orchestrator(Path(state), Path(out), root=Path(art), governance_root=Path(gov), benchmark=BK.synthetic_benchmark,
                     controlled_adapter=ControlledRunnerAdapter(be, governance_root=Path(gov)), clock=A5.StepClock())
    try:
        o.adapters["RESEARCH_ONLY_REAL"].execute(doc, Path(dst), reservation_id=rid, task_id=tid, attempt_id=aid)
        return "EXECUTED"
    except ExecutionFailed as e:
        return "REFUSED:" + e.code
