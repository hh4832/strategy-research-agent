"""Phase 5-B3-B test support: the REAL_GATED path on ISOLATED books with a MOCK runner (no market data, no Core).

``MockRealRunnerBackend`` is a non-synthetic stand-in for the existing ResearchOnlyRunner: it reports a configured
execution identity and writes the labelled SYNTHETIC_STUB archive (see ``SyntheticStubBackend``). Its runner receipt
is explicitly ``MOCK_TEST_ONLY_NOT_CORE``; on production books it can never be used because the activation gate
requires PRODUCTION binding trust there and the real backend's receipt comes from Core's renewal (Layer B tests).
"""
from __future__ import annotations

from pathlib import Path

from strategy_agent.binding import testkit as K
from strategy_agent.orchestration.orchestrator import Orchestrator
from strategy_agent.phase5a import acceptance as A5

from .activation import RealActivationGate
from .controlled import ControlledRunnerAdapter, SyntheticStubBackend
from .testkit import stub_identity, world

EXECUTOR = "colab-hh4832-exec-01"


class MockRealRunnerBackend(SyntheticStubBackend):
    kind = "MOCK_REAL_RUNNER_TEST_ONLY"
    synthetic = False

    def __init__(self, *a, receipt: dict | None = None, **k):
        super().__init__(*a, **k)
        self.receipt = receipt

    def runner_receipt(self) -> dict:
        if self.receipt is not None:
            return dict(self.receipt)
        return {"kind": "MOCK_TEST_ONLY_NOT_CORE", "verification_ref": "DEVELOPMENT_RECEIPT_RENEWAL:MOCK_TEST_ONLY_NOT_CORE"}


def world_manifest(W: dict) -> dict:
    """A readiness manifest for the ISOLATED test world (A10); built from the world's authorization + artefacts."""
    return _manifest_from_identity(stub_identity(W))


def real_world(work: Path, *, executor_id: str | None = EXECUTOR, max_batches: int = 1, mock: dict | None = None,
               identity_override: dict | None = None) -> dict:
    W = world(work, max_batches=max_batches, executor_id=executor_id)
    ident = {**stub_identity(W), **(identity_override or {})}
    W["backend"] = MockRealRunnerBackend(W["backend"].fixture, identity=ident, **(mock or {}))
    W["executor_id"] = executor_id
    return W


def gate(W: dict, *, executor_id: str | None = None, manifest=None) -> RealActivationGate:
    return RealActivationGate(governance_root=W["gov"], artifact_root=W["art"],
                              executor_id=executor_id or W["executor_id"] or EXECUTOR, production_books=False,
                              readiness_manifest=manifest if manifest is not None else (lambda: world_manifest(W)))


def real_orchestrator(W: dict, name: str = "s", *, worker: str | None = None, executor_id: str | None = None,
                      manifest=None, clock=None, backend=None) -> Orchestrator:
    g = gate(W, executor_id=executor_id, manifest=manifest)
    ad = ControlledRunnerAdapter(backend or W["backend"], governance_root=W["gov"], activation=g)
    return Orchestrator(Path(W["work"]) / name, Path(W["work"]) / "out", root=W["art"], governance_root=W["gov"],
                        benchmark=K.synthetic_benchmark, approval_verifier=W["fa"].verifier, controlled_adapter=ad,
                        clock=clock or A5.StepClock(), worker=worker or g.executor_id)


def mp_real_execute(args: tuple) -> str:
    """Multiprocess replay worker (``spawn``): a FRESH REAL_GATED orchestrator on the same isolated books + task log."""
    state, out, art, gov, fixture, identity, calls_file, doc, rid, tid, aid, dst, executor = args
    from strategy_agent.evaluation import phase4c as C  # noqa: PLC0415
    from strategy_agent.orchestration.adapters import ExecutionFailed  # noqa: PLC0415
    C.EXECUTOR_MODULES = ()
    be = MockRealRunnerBackend(Path(fixture), identity=identity, calls_file=Path(calls_file))
    g = RealActivationGate(governance_root=Path(gov), artifact_root=Path(art), executor_id=executor,
                           production_books=False, readiness_manifest=lambda: _manifest_from_identity(identity))
    o = Orchestrator(Path(state), Path(out), root=Path(art), governance_root=Path(gov), benchmark=K.synthetic_benchmark,
                     controlled_adapter=ControlledRunnerAdapter(be, governance_root=Path(gov), activation=g),
                     clock=A5.StepClock(), worker=executor)
    try:
        o.adapters["RESEARCH_ONLY_REAL"].execute(doc, Path(dst), reservation_id=rid, task_id=tid, attempt_id=aid)
        return "EXECUTED"
    except ExecutionFailed as e:
        return "REFUSED:" + e.code


def _manifest_from_identity(idn: dict) -> dict:
    F = {"strategy_spec_sha256": idn["strategy_spec_sha256"], "dataset_snapshot_id": idn["dataset_snapshot_id"],
         "dataset_manifest_sha256": idn["dataset_manifest_sha256"], "core_commit_sha": idn["core_commit"],
         "trading_cost_profile": {"scenario_set_sha256": idn["cost_profile_sha256"]}}
    return {"kind": "TEST_WORLD_READINESS_MANIFEST", "fields": {k: {"status": "RESOLVED", "category": None, "value": v}
                                                               for k, v in F.items()},
            "cross_checks": {"phase5_request_spec_dataset_binding": {"status": "PASS"}}}
