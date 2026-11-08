"""Phase 5-B2 security remediation of PR #13 (Codex P0-1 / P0-2 / P0-3), Layer A (no Core; SYNTHETIC only).

Test names carry the finding they evidence (test_P0_1_..., test_P0_2_..., test_P0_3_...), reported separately from
B2-01..B2-10. Layer B (existing runner + Core) P0-3 tests live in test_phase5b2_runner_core.py.
Expectations are the secure behaviour required by the brief; none was relaxed to make a test pass.
"""
import json
import multiprocessing as mp
from pathlib import Path

import pytest

from strategy_agent.evaluation import phase4c as C
from strategy_agent.execution import testkit as X
from strategy_agent.execution.budget import BudgetRefused
from strategy_agent.execution.controlled import ControlledRunnerAdapter, SyntheticFixtureBackend, SyntheticStubBackend
from strategy_agent.orchestration.adapters import ExecutionFailed
from strategy_agent.phase5a import acceptance as A5
from strategy_agent.registry.chain import ChainedLog


@pytest.fixture(autouse=True)
def _no_c8(monkeypatch):
    monkeypatch.setattr(C, "EXECUTOR_MODULES", ())


@pytest.fixture
def W(tmp_path):
    return X.world(tmp_path)


def uses(W):
    return [json.loads(x) for x in (W["gov"] / "registry/research_authorization_uses.jsonl").read_text().splitlines() if x]


def run(W, o=None):
    o = o or X.orchestrator(W)
    tid = X.submit_and_approve(W, o)
    return o, tid, o.dispatch(tid)


# ===================================================================== P0-1 reservation replay / forgery
def test_P0_1_forged_reservation_is_blocked_without_running(W, tmp_path):
    o = X.orchestrator(W)
    ad = o.adapters["RESEARCH_ONLY_REAL"]
    forged_use = {"run_id": "SYNSTUB_forged", "record_sha256": "f" * 64}
    forged_res = {"reservation_id": "BR-" + "0" * 24, "task_id": "T-FORGED", "planned_run_id": "SYNSTUB_forged",
                  "state": "COMMITTED"}
    with pytest.raises(ExecutionFailed, match="CALLER_SUPPLIED_RESERVATION_REFUSED"):
        ad.execute(W["bound"], tmp_path / "a", reservation_id=forged_res["reservation_id"], task_id="T-FORGED",
                   attempt_id="T-FORGED:1", use=forged_use, reservation=forged_res)
    with pytest.raises(ExecutionFailed, match="UNKNOWN_RESERVATION"):
        ad.execute(W["bound"], tmp_path / "b", reservation_id=forged_res["reservation_id"], task_id="T-FORGED",
                   attempt_id="T-FORGED:1")
    assert W["backend"].calls == 0 and uses(W) == [] and o.budget.reservations() == {}


def test_P0_1_reused_reservation_runs_at_most_once(W, tmp_path):
    o, tid, st = run(W)
    assert st["state"] == "ARCHIVED" and W["backend"].calls == 1
    r = o.budget.for_task(tid)
    ad = o.adapters["RESEARCH_ONLY_REAL"]
    for i in range(3):                                       # replay after settlement: never runs again
        with pytest.raises(ExecutionFailed, match="EXECUTION_NOT_ELIGIBLE"):
            ad.execute(o.request_of(tid), tmp_path / f"r{i}", reservation_id=r["reservation_id"], task_id=tid,
                       attempt_id=r["attempt_id"])
    assert W["backend"].calls == 1 and len(uses(W)) == 1 and o.budget.for_task(tid)["state"] == "CONSUMED"


def test_P0_1_replay_while_in_flight_is_refused(W, tmp_path):
    """Re-entrant replay of the SAME committed reservation while the first execution is running."""
    o = X.orchestrator(W)
    ad, be = o.adapters["RESEARCH_ONLY_REAL"], W["backend"]
    c = X.committed_running(W, o)
    seen, orig = [], be.run

    def run_and_replay(doc, out_dir, *, use):
        try:
            ad.execute(doc, tmp_path / "replay", reservation_id=c["reservation_id"], task_id=c["task_id"],
                       attempt_id=c["attempt_id"])
            seen.append("EXECUTED")
        except ExecutionFailed as e:
            seen.append(e.code)
        return orig(doc, out_dir, use=use)
    be.run = run_and_replay
    ad.execute(c["doc"], tmp_path / "first", reservation_id=c["reservation_id"], task_id=c["task_id"],
               attempt_id=c["attempt_id"])
    assert seen == ["EXECUTION_ALREADY_STARTED"] and be.calls == 1
    assert o.budget.for_task(c["task_id"])["history"][-2:] == ["EXECUTION_STARTED", "EXECUTION_COMPLETED"]


@pytest.mark.parametrize("procs", [2, 6])
def test_P0_1_same_reservation_from_several_processes_runs_at_most_once(tmp_path, procs):
    calls = tmp_path / "calls.txt"
    W = X.world(tmp_path / "w", stub={"calls_file": calls})
    o = X.orchestrator(W, "state")
    c = X.committed_running(W, o)
    ident = X.stub_identity(W)
    args = [(str(W["work"] / "state"), str(W["work"] / "out"), str(W["art"]), str(W["gov"]), str(W["backend"].fixture),
             ident, str(calls), c["doc"], c["reservation_id"], c["task_id"], c["attempt_id"], str(tmp_path / f"p{i}"))
            for i in range(procs)]
    with mp.get_context("spawn").Pool(procs) as pool:
        res = pool.map(X.mp_execute, args)
    assert res.count("EXECUTED") == 1 and all(x == "REFUSED:EXECUTION_ALREADY_STARTED" for x in res if x != "EXECUTED")
    assert W["backend"].calls == 1 and len(uses(W)) == 1                       # runner calls <= 1 across processes
    evs = [e["event_type"] for e in ChainedLog(W["gov"] / "registry/research_budget_events.jsonl").verify()]
    assert evs.count("EXECUTION_STARTED") == 1


def test_P0_1_fake_committed_status_is_never_eligibility(tmp_path):
    W = X.world(tmp_path, max_batches=2)
    o = X.orchestrator(W)
    ad = o.adapters["RESEARCH_ONLY_REAL"]
    o.budget.register_executor("worker-1", actor="t")
    r = o.budget.reserve(authorization_id=W["authorization_id"], request_sha256="a" * 64, task_id="T-" + "a" * 20,
                         attempt_id="T-a:1", approval_evidence_sha256="e" * 64, binding_id=W["binding"]["binding_id"],
                         worker_id="worker-1", planned_run_id="SYNSTUB_x")
    with pytest.raises(ExecutionFailed, match="CALLER_SUPPLIED_RESERVATION_REFUSED"):
        ad.execute(W["bound"], tmp_path / "a", reservation_id=r["reservation_id"], task_id=r["task_id"],
                   attempt_id=r["attempt_id"], reservation={**r, "state": "COMMITTED"})
    with pytest.raises(ExecutionFailed, match="EXECUTION_NOT_ELIGIBLE"):     # the ledger says RESERVED
        ad.execute(W["bound"], tmp_path / "b", reservation_id=r["reservation_id"], task_id=r["task_id"],
                   attempt_id=r["attempt_id"])
    assert W["backend"].calls == 0 and uses(W) == []


def test_P0_1_committed_reservation_needs_matching_identity_and_running_task(W, tmp_path):
    o = X.orchestrator(W)
    ad = o.adapters["RESEARCH_ONLY_REAL"]
    c = X.committed_running(W, o)
    with pytest.raises(ExecutionFailed, match="RESERVATION_IDENTITY_MISMATCH"):  # another task / attempt
        ad.execute(c["doc"], tmp_path / "a", reservation_id=c["reservation_id"], task_id=c["task_id"], attempt_id="x:9")
    o.log.transition(c["task_id"], "FAILED", actor="orchestrator", actor_kind="SYSTEM", reason="TEST", worker=o.worker,
                     idempotency_key="x")
    with pytest.raises(ExecutionFailed, match="RESERVATION_IDENTITY_MISMATCH"):  # task not RUNNING: no approval grant
        ad.execute(c["doc"], tmp_path / "b", reservation_id=c["reservation_id"], task_id=c["task_id"],
                   attempt_id=c["attempt_id"])
    assert W["backend"].calls == 0


def test_P0_1_adapter_refuses_a_foreign_ledger_and_unbound_execution(W, tmp_path):
    other = X.world(tmp_path / "other")
    ad = ControlledRunnerAdapter(W["backend"], governance_root=W["gov"], artifact_root=W["art"])
    with pytest.raises(ValueError, match="own governance root"):
        ad.bind(ledger=X.ledger_for(other["gov"], clock=A5.StepClock()), artifact_root=W["art"],
                approvals=lambda *a: "e" * 64, worker_id="worker-1")
    with pytest.raises(ExecutionFailed, match="LEDGER_UNBOUND"):
        ad.execute(W["bound"], tmp_path / "x", reservation_id="BR-x", task_id="T-x", attempt_id="T-x:1")
    assert W["backend"].calls == 0


def test_P0_1_ledger_settle_requires_completed_and_verified_evidence(W, tmp_path):
    o = X.orchestrator(W)
    c = X.committed_running(W, o)
    with pytest.raises(BudgetRefused, match="SETTLE_REFUSED"):
        o.budget.settle(c["reservation_id"], receipt_sha256="a" * 64, output_manifest_sha256="b" * 64,
                        archive_ref=str(tmp_path), worker_id="worker-1")
    ref = o.adapters["RESEARCH_ONLY_REAL"].execute(c["doc"], tmp_path / "e", reservation_id=c["reservation_id"],
                                                   task_id=c["task_id"], attempt_id=c["attempt_id"])
    with pytest.raises(BudgetRefused, match="SETTLE_REFUSED"):              # completed but not verified
        o.budget.settle(c["reservation_id"], receipt_sha256=ref["receipt_sha256"],
                        output_manifest_sha256=ref["archive_manifest_sha256"], archive_ref=str(tmp_path), worker_id="worker-1")
    assert o.budget.for_task(c["task_id"])["state"] == "COMMITTED"


def test_P0_1_marker_events_cannot_be_forged_into_the_ledger(W):
    o, tid, st = run(W)
    p = W["gov"] / "registry/research_budget_events.jsonl"
    recs = [json.loads(x) for x in p.read_text().splitlines()]
    started = next(r for r in recs if r["event_type"] == "EXECUTION_STARTED")
    started["worker_id"] = "attacker"
    p.write_text("".join(json.dumps(r if r["seq"] != started["seq"] else started) + "\n" for r in recs))
    with pytest.raises(BudgetRefused, match="LEDGER_CORRUPTED"):
        o.budget.capacity(W["authorization_id"])


# ============================================================ P0-2 receipt / archive integrity before settlement
def _tamper_before_settle(W, kind):
    o = X.orchestrator(W)
    tid = X.submit_and_approve(W, o)
    orig = o._archive

    def archive_then_tamper(task_id, doc):
        orig(task_id, doc)
        st = o.log.get(task_id)
        ref = o._execution_reference(st)
        arch = Path(ref["archive_path"])
        if kind == "receipt_deleted":
            Path(ref["receipt_path"]).unlink()
        elif kind == "receipt_modified":
            p = Path(ref["receipt_path"])
            d = json.loads(p.read_text())
            d["consumed_batches"] = 0
            p.write_text(json.dumps(d))
        elif kind == "archive_file_removed":
            (arch / "trades.csv").unlink()
        elif kind == "archive_manifest_corrupted":
            m = json.loads((arch / "MANIFEST.json").read_text())
            m["files_sha256"]["trades.csv"] = "0" * 64
            (arch / "MANIFEST.json").write_text(json.dumps(m))
        elif kind == "evaluation_output_altered":
            ev = next(e for e in reversed(st.events) if e["to"] == "EVALUATED")["data"]["evaluation_reference"]
            (Path(ev["dir"]) / "research_decision.json").write_text('{"strategy_verdict": "KEEP"}')
    o._archive = archive_then_tamper
    st = o.dispatch(tid)
    return o, tid, st


@pytest.mark.parametrize("kind", ["receipt_deleted", "receipt_modified"])
def test_P0_2_receipt_deleted_or_modified_before_settlement_needs_reconciliation(W, kind):
    o, tid, st = _tamper_before_settle(W, kind)
    r = o.budget.for_task(tid)
    assert r["state"] == "RECONCILIATION_REQUIRED" and "CONSUMED" not in r["history"]
    assert "EVIDENCE_VERIFIED" not in r["history"] and st["budget_state"] == "RECONCILIATION_REQUIRED"
    assert W["backend"].calls == 1 and len(uses(W)) == 1 and o.budget.capacity(W["authorization_id"])["used"] == 1
    with pytest.raises(BudgetRefused):
        o.budget.release(r["reservation_id"], reason="x", worker_id="worker-1")       # never released


@pytest.mark.parametrize("kind", ["archive_file_removed", "archive_manifest_corrupted", "evaluation_output_altered"])
def test_P0_2_archive_or_evaluation_tampered_before_settlement_never_settles(W, kind):
    o, tid, st = _tamper_before_settle(W, kind)
    r = o.budget.for_task(tid)
    assert r["state"] == "RECONCILIATION_REQUIRED" and "CONSUMED" not in r["history"]
    assert W["backend"].calls == 1 and len(uses(W)) == 1
    assert o.dispatch(tid)["budget_state"] == "RECONCILIATION_REQUIRED" and W["backend"].calls == 1


def test_P0_2_evidence_tampered_before_evaluation_fails_closed(W):
    o = X.orchestrator(W)
    tid = X.submit_and_approve(W, o)
    ad = o.adapters["RESEARCH_ONLY_REAL"]
    orig = ad.execute

    def execute_then_tamper(*a, **k):
        ref = orig(*a, **k)
        Path(ref["receipt_path"]).unlink()
        return ref
    ad.execute = execute_then_tamper
    st = o.dispatch(tid)
    assert st["state"] == "FAILED" and st["history"][-1]["reason"] == "EXECUTION_EVIDENCE_INVALID"
    assert "EVALUATED" not in [h["to"] for h in st["history"]]
    assert o.budget.for_task(tid)["state"] == "RECONCILIATION_REQUIRED" and W["backend"].calls == 1


def test_P0_2_runner_result_inconsistent_with_request_is_never_settled(tmp_path):
    W = X.world(tmp_path, stub={"stamp": {"core_commit": "9" * 40}})        # the runner produced something else
    o, tid, st = run(W)
    r = o.budget.for_task(tid)
    assert st["state"] == "FAILED" and "RUNNER_RESULT_INCONSISTENT" == st["history"][-1]["reason"]
    assert r["state"] == "RECONCILIATION_REQUIRED" and "EXECUTION_COMPLETED" not in r["history"]
    assert W["backend"].calls == 1 and o.dispatch(tid)["state"] == "FAILED" and W["backend"].calls == 1


def test_P0_2_archive_carrying_another_uses_record_is_inconsistent(tmp_path):
    """A runner result whose archive is bound to a different authorization use (here: the fixture's own) is rejected."""
    class ForeignUse(SyntheticStubBackend):
        def run(self, doc, out_dir, *, use):
            return super().run(doc, out_dir, use={**use, "record_sha256": "c" * 64})
    W0 = X.world(tmp_path / "a")
    W = X.world(tmp_path / "b", backend=ForeignUse(W0["backend"].fixture, identity=X.stub_identity(W0)))
    W["backend"].identity = X.stub_identity(W)
    o, tid, st = run(W)
    assert st["state"] == "FAILED" and "ARCHIVE_USE_RECORD_MISMATCH" in json.dumps(st["last_data"])
    assert o.budget.for_task(tid)["state"] == "RECONCILIATION_REQUIRED"


def test_P0_2_crash_after_execution_started_is_never_rerun(tmp_path):
    W = X.world(tmp_path, stub={"fail_with": "worker crashed after start"})
    o, tid, st = run(W)
    r = o.budget.for_task(tid)
    assert st["state"] == "FAILED" and r["state"] == "RECONCILIATION_REQUIRED"
    assert r["history"] == ["RESERVED", "COMMITTED", "EXECUTION_STARTED", "RECONCILIATION_REQUIRED"]
    o.recover()
    assert o.dispatch(tid)["state"] == "FAILED" and W["backend"].calls == 1 and len(uses(W)) == 1
    with pytest.raises(BudgetRefused):
        o.budget.release(r["reservation_id"], reason="x", worker_id="worker-1")


def test_P0_2_evaluator_failure_is_never_rerun(W, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("evaluator crashed")
    monkeypatch.setattr(C, "accept", boom)
    o, tid, st = run(W)
    r = o.budget.for_task(tid)
    assert st["state"] == "FAILED" and r["state"] == "RECONCILIATION_REQUIRED"
    assert "EXECUTION_COMPLETED" in r["history"] and "CONSUMED" not in r["history"]
    o.recover()
    assert o.dispatch(tid)["state"] == "FAILED" and W["backend"].calls == 1


# ====================================================================== P0-3 request / execution identity
@pytest.mark.parametrize("field,bad", [("core_commit", "0" * 40), ("strategy_spec_sha256", "0" * 64),
                                       ("dataset_manifest_sha256", "0" * 64), ("dataset_snapshot_id", "SYNTHETIC_LATEST")])
def test_P0_3_wrong_core_spec_or_manifest_is_blocked_before_runner(W, tmp_path, field, bad):
    o = X.orchestrator(W)
    ad = o.adapters["RESEARCH_ONLY_REAL"]
    doc = {**W["bound"], field: bad}
    pr = ad.precheck(doc)
    assert any(p.startswith("BINDING_INVALID") for p in pr) and any(field in p for p in pr)
    s = o.submit(doc, actor="TEST_FIXTURE_SUBMITTER", actor_kind="TEST_FIXTURE")
    assert s["state"] == "BLOCKED"                                          # not the bound request
    assert W["backend"].calls == 0 and o.budget.reservations() == {} and uses(W) == []


def test_P0_3_backend_identity_differs_from_bound_request_blocks_before_reservation(tmp_path):
    """The bound request is intact, but the backend would run another Core / manifest: BLOCKED before any budget."""
    W0 = X.world(tmp_path / "a")
    W = X.world(tmp_path / "b", backend=SyntheticStubBackend(W0["backend"].fixture,
                                                             identity={**X.stub_identity(W0), "core_commit": "9" * 40}))
    o, tid, st = run(W)
    assert st["state"] == "BLOCKED" and st["history"][-1]["reason"] == "CONTROLLED_PRECHECK_FAILED"
    assert "IDENTITY_MISMATCH:BACKEND:core_commit" in json.dumps(st["last_data"])
    assert W["backend"].calls == 0 and o.budget.reservations() == {} and uses(W) == []


def test_P0_3_foreign_fixture_identity_is_blocked_before_runner(W):
    """The old Layer A fixture archive carries the DEV authorization / snapshot / manifest: never run for this request."""
    be = SyntheticFixtureBackend(A5.FIX / A5.FIX_RUN)
    o, tid, st = run(W, X.orchestrator(W, backend=be))
    assert st["state"] == "BLOCKED" and be.calls == 0 and o.budget.reservations() == {}
    pr = json.dumps(st["last_data"])
    for k in ("authorization_id", "dataset_manifest_sha256", "dataset_snapshot_id", "strategy_spec_sha256"):
        assert f"IDENTITY_MISMATCH:BACKEND:{k}" in pr


def test_P0_3_artefact_changed_on_disk_after_binding_blocks(W):
    o = X.orchestrator(W)
    tid = X.submit_and_approve(W, o)
    spec = W["art"] / W["bound"]["strategy_spec_ref"]
    spec.write_bytes(spec.read_bytes() + b" ")
    st = o.dispatch(tid)
    assert st["state"] == "BLOCKED" and W["backend"].calls == 0 and o.budget.reservations() == {}


def test_P0_3_identity_rechecked_at_execution_time_after_commit(W, tmp_path):
    """Identity drifting between reservation and execution is refused before the claim (no runner call)."""
    o = X.orchestrator(W)
    c = X.committed_running(W, o)
    cost = W["art"] / W["bound"]["cost_profile_ref"]["scenario_set_path"]
    cost.write_bytes(cost.read_bytes() + b" ")
    with pytest.raises(ExecutionFailed, match="IDENTITY_VERIFICATION_FAILED"):
        o.adapters["RESEARCH_ONLY_REAL"].execute(c["doc"], tmp_path / "x", reservation_id=c["reservation_id"],
                                                 task_id=c["task_id"], attempt_id=c["attempt_id"])
    r = o.budget.for_task(c["task_id"])
    assert W["backend"].calls == 0 and "EXECUTION_STARTED" not in r["history"]


def test_P0_3_expired_or_revoked_authorization_blocks_at_execution(W, tmp_path):
    from strategy_agent.governance.research_authorization_admin import revoke
    o = X.orchestrator(W)
    c = X.committed_running(W, o)
    G = W["gov"] / "governance"
    revoke(G / "research_authorizations.jsonl", G / "approvers.json", authorization_id=W["authorization_id"],
           approved_by="hh4832", reason="TEST revoke after commit", approval_ref="https://example.test/revoke")
    with pytest.raises(ExecutionFailed, match="AUTHORIZATION_NOT_ACTIVE"):
        o.adapters["RESEARCH_ONLY_REAL"].execute(c["doc"], tmp_path / "x", reservation_id=c["reservation_id"],
                                                 task_id=c["task_id"], attempt_id=c["attempt_id"])
    assert W["backend"].calls == 0
