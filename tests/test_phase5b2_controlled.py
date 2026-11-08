"""Phase 5-B2 Layer A (no Core): controlled execution + atomic budget, SYNTHETIC_FIXTURE only.

Test names carry the acceptance gate they evidence (B2-01..B2-09). Nothing touches the repository's books.
"""
import json
import multiprocessing as mp
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from strategy_agent.evaluation import phase4c as C
from strategy_agent.execution import testkit as X
from strategy_agent.execution.budget import BudgetLedger, BudgetRefused
from strategy_agent.execution.controlled import ControlledRunnerAdapter, SyntheticFixtureBackend
from strategy_agent.orchestration import request as Q
from strategy_agent.orchestration.governance import REAL_EXECUTION_ENABLED
from strategy_agent.orchestration.orchestrator import Orchestrator
from strategy_agent.orchestration.tasks import IllegalTransition
from strategy_agent.phase5a import acceptance as A5
from strategy_agent.registry.chain import ChainedLog

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _no_c8(monkeypatch):
    monkeypatch.setattr(C, "EXECUTOR_MODULES", ())       # process-level C8 is asserted by the fresh-process acceptance


@pytest.fixture
def W(tmp_path):
    return X.world(tmp_path)


def uses(W):
    p = W["gov"] / "registry/research_authorization_uses.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


def run(W, o=None):
    o = o or X.orchestrator(W)
    tid = X.submit_and_approve(W, o)
    return o, tid, o.dispatch(tid)


# ------------------------------------------------------------------------- B2-05 controlled execution (1)
def test_B2_05_approved_reserved_execution_runs_once_and_settles(W):
    o, tid, st = run(W)
    assert st["state"] == "ARCHIVED" and W["backend"].calls == 1
    r = o.budget.for_task(tid)
    assert r["history"] == ["RESERVED", "COMMITTED", "CONSUMED"] and r["binding_id"] == W["binding"]["binding_id"]
    assert r["approval_evidence_sha256"] and r["receipt_sha256"]
    assert len(uses(W)) == 1 and uses(W)[0]["run_id"] == r["planned_run_id"]
    ev = [json.loads(x) for x in (W["gov"] / "registry/research_budget_events.jsonl").read_text().splitlines()]
    for e in ev:
        if e["event_type"] in ("RESERVED", "COMMITTED", "CONSUMED"):
            for k in ("authorization_id", "request_sha256", "task_id", "reservation_id", "batch_units", "attempt_id",
                      "approval_evidence_sha256", "binding_id", "transaction_timestamp", "worker_id", "git_commit",
                      "event_type", "previous_event_sha256", "prev_sha256"):
                assert k in e, (e["event_type"], k)
    ref = next(e for e in o.log.get(tid).events if e["to"] == "EVALUATING")["data"]["execution_reference"]
    rc = json.loads(Path(ref["receipt_path"]).read_text())
    assert rc["output_manifest_sha256"] == ref["archive_manifest_sha256"] and rc["use_record_sha256"] == uses(W)[0]["record_sha256"]


def test_B2_08_existing_evaluator_runs_and_never_keeps_on_identity_mismatch(W):
    o, tid, st = run(W)
    from strategy_agent.phase5a.integration import trace
    d = trace(o, tid)["research_decision"]
    # the SYNTHETIC_FIXTURE archive carries another authorization => the existing Phase 4-C evaluator refuses it
    assert d["acceptance_status"] == "FAIL" and d["strategy_verdict"] != "KEEP"
    assert d["acceptance_checks"]["C8_governance_isolation"] == "FAIL"


# --------------------------------------------------------------------------- B2-03 approval (2, 6)
def test_B2_03_unapproved_task_never_executes(W):
    o = X.orchestrator(W)
    s = o.submit(W["bound"], actor="x", actor_kind="TEST_FIXTURE")
    assert o.dispatch(s["task_id"])["state"] == "PENDING_APPROVAL"
    assert W["backend"].calls == 0 and o.budget.reservations() == {}
    with pytest.raises(IllegalTransition, match="APPROVER|APPROVAL"):
        o.approve(s["task_id"], approver="hh4832", approval_ref="https://example.test/not-evidence")


def test_B2_03_revoked_approval_blocks_before_any_reservation(W):
    o = X.orchestrator(W)
    s = o.submit(W["bound"], actor="x", actor_kind="TEST_FIXTURE")
    url = W["fa"].comment(W["fa"].line(o.request_of(s["task_id"]), intent="APPROVE_TASK", task_id=s["task_id"]))
    o.approve(s["task_id"], evidence_ref=url)
    W["fa"].comment(f"RESEARCH-APPROVAL-REVOKE v1 comment={url.rsplit('-', 1)[1]}")
    st = o.dispatch(s["task_id"])
    assert st["state"] == "BLOCKED" and st["history"][-1]["reason"].startswith("APPROVAL_V08")
    assert W["backend"].calls == 0 and o.budget.reservations() == {} and uses(W) == []


# ------------------------------------------------------------------------ B2-02 authorization scope (3, 4)
def test_B2_02_pending_authorization_and_unbound_requests_never_execute(W):
    o = X.orchestrator(W)
    s = o.submit(W["request"], actor="x", actor_kind="TEST_FIXTURE")                      # PENDING source
    assert s["state"] == "BLOCKED"
    forged = dict(W["request"], authorization_ref={"authorization_id": W["authorization_id"]}, request_id="RR-FORGED-B2")
    s2 = o.submit(forged, actor="x", actor_kind="TEST_FIXTURE")                           # not a registered binding
    assert s2["state"] == "BLOCKED" and {c["id"]: c["status"] for c in s2["governance"]["checks"]}["G11_AUTHORIZATION_BINDING"] == "FAIL"
    assert W["backend"].calls == 0


def test_B2_02_exhausted_budget_after_approval_never_executes(W):
    o = X.orchestrator(W)
    tid = X.submit_and_approve(W, o)
    ChainedLog(W["gov"] / "registry/research_authorization_uses.jsonl").append(        # spent elsewhere (legacy path)
        {"actor": "hh4832", "authorization_id": W["authorization_id"], "reserved_at": "2026-11-08T10:00:00+08:00",
         "run_id": "20261108_100000_0000000", "scenarios": ["BASE", "LOW", "STRESS", "CAT_STRESS"]})
    st = o.dispatch(tid)
    assert st["state"] == "BLOCKED" and W["backend"].calls == 0 and len(uses(W)) == 1


def test_B2_02_authorization_revoked_after_approval_never_executes(W):
    o = X.orchestrator(W)
    tid = X.submit_and_approve(W, o)
    ChainedLog(W["gov"] / "governance/research_authorizations.jsonl").append(
        {"action": "REVOKE", "contract": "research-only-authorization-v1", "authorization_id": W["authorization_id"],
         "approved_by": "hh4832", "approval_ref": "https://example.test/revoke", "approved_at": "2026-11-08T10:00:00+08:00",
         "reason": "test"})
    st = o.dispatch(tid)
    assert st["state"] == "BLOCKED" and W["backend"].calls == 0 and uses(W) == []


# ------------------------------------------------------------------- B2-07 / B2-04 identity pins (5)
def test_B2_07_spec_or_cost_changed_after_approval_blocks_before_reservation(W):
    o = X.orchestrator(W)
    tid = X.submit_and_approve(W, o)
    spec = W["art"] / W["bound"]["strategy_spec_ref"]
    spec.write_bytes(spec.read_bytes() + b"\n")
    st = o.dispatch(tid)
    assert st["state"] == "BLOCKED" and st["history"][-1]["reason"] == "GOVERNANCE_RECHECK_BLOCKED"
    assert o.budget.reservations() == {} and W["backend"].calls == 0


def test_B2_04_modified_bound_request_is_not_bound(W):
    o = X.orchestrator(W)
    bad = json.loads(json.dumps(W["bound"]))
    bad["dataset_manifest_sha256"] = "3" * 64
    s = o.submit(bad, actor="x", actor_kind="TEST_FIXTURE")
    assert s["state"] == "BLOCKED"
    assert {c["id"]: c["status"] for c in s["governance"]["checks"]}["G11_AUTHORIZATION_BINDING"] == "FAIL"


def test_B2_07_precheck_refuses_missing_pins_and_pit_rules(W):
    ad = ControlledRunnerAdapter(W["backend"], governance_root=W["gov"])
    for k, v in (("dataset_snapshot_id", ""), ("core_commit", "PENDING")):
        assert any(k in p for p in ad.precheck(dict(W["bound"], **{k: v})))
    assert "PIT_RULE_MISMATCH" in ad.precheck(dict(W["bound"], execution_time_rule={"rule": "SAME_BAR_CLOSE"}))
    assert "HOLDOUT_ACCESS_REQUESTED" in ad.precheck(dict(W["bound"], holdout_policy={"access": "READ", "lock_id": "x"}))


# ------------------------------------------------------------------- B2-06 duplicate / concurrency / crash (7-10)
def test_B2_06_duplicate_request_and_repeated_dispatch_execute_once(W):
    o, tid, st = run(W)
    again = o.submit(W["bound"], actor="x", actor_kind="TEST_FIXTURE")
    assert again["task_id"] == tid
    for _ in range(3):
        assert o.dispatch(tid)["state"] == "ARCHIVED"
    assert X.orchestrator(W, worker="worker-1").dispatch(tid)["state"] == "ARCHIVED"
    assert W["backend"].calls == 1 and len(uses(W)) == 1 and len(o.budget.reservations()) == 1
    with pytest.raises(BudgetRefused, match="DUPLICATE_REQUEST"):
        o.budget.reserve(authorization_id=W["authorization_id"], request_sha256=Q.request_hash(W["bound"]),
                         task_id="T-" + "9" * 20, attempt_id="x", approval_evidence_sha256="e" * 64, binding_id=None,
                         worker_id="worker-1", planned_run_id="other")


def test_B2_06_threads_dispatching_concurrently_execute_once(W):
    import threading
    o = X.orchestrator(W)
    tid = X.submit_and_approve(W, o)
    errs = []

    def go():
        try:
            X.orchestrator(W, worker="worker-1").dispatch(tid)
        except Exception as e:  # noqa: BLE001
            errs.append(repr(e))
    th = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in th]
    [t.join() for t in th]
    assert W["backend"].calls == 1 and len(uses(W)) == 1


@pytest.mark.parametrize("procs", [8])
def test_B2_01_multiprocess_reservations_never_exceed_the_limit(tmp_path, procs):
    W = X.world(tmp_path, max_batches=2)
    L = X.ledger_for(W["gov"], clock=lambda: datetime.now(timezone.utc))
    L.register_executor("worker-1", actor="t")
    ctx = mp.get_context("spawn")
    with ctx.Pool(procs) as pool:
        res = pool.map(X.mp_reserve, [(str(W["gov"]), W["authorization_id"], i, "worker-1") for i in range(procs)])
    ok = [r for r in res if r.startswith("RESERVED")]
    assert len(ok) == 2 and all(r == "REFUSED:BUDGET_EXHAUSTED" for r in res if not r.startswith("RESERVED")), res
    assert len([r for r in L.reservations().values() if r["state"] == "RESERVED"]) == 2


def test_B2_01_reservation_counts_uses_and_open_reservations_and_retry_is_idempotent(tmp_path):
    W = X.world(tmp_path, max_batches=2)
    L = X.ledger_for(W["gov"], clock=lambda: datetime.now(timezone.utc))
    L.register_executor("w", actor="t")
    kw = dict(authorization_id=W["authorization_id"], attempt_id="1", approval_evidence_sha256="e" * 64, binding_id=None,
              worker_id="w")
    a = L.reserve(request_sha256="a" * 64, task_id="T-" + "a" * 20, planned_run_id="r1", **kw)
    again = L.reserve(request_sha256="a" * 64, task_id="T-" + "a" * 20, planned_run_id="r1-retry", **kw)
    assert again["reservation_id"] == a["reservation_id"] and again["idempotent"]
    ChainedLog(W["gov"] / "registry/research_authorization_uses.jsonl").append(
        {"actor": "x", "authorization_id": W["authorization_id"], "reserved_at": "2026-11-08T10:00:00+08:00",
         "run_id": "legacy", "scenarios": ["BASE"]})
    with pytest.raises(BudgetRefused, match="BUDGET_EXHAUSTED"):          # 1 legacy use + 1 open reservation = 2
        L.reserve(request_sha256="b" * 64, task_id="T-" + "b" * 20, planned_run_id="r2", **kw)
    assert L.capacity(W["authorization_id"]) == {"max_batches": 2, "used": 2, "uses_records": 1, "reserved_uncommitted": 1}


def test_B2_06_second_executor_is_refused_single_executor_only(W):
    L = X.ledger_for(W["gov"], clock=lambda: datetime.now(timezone.utc))
    L.register_executor("colab-runtime-A", actor="t")
    with pytest.raises(BudgetRefused, match="SECOND_EXECUTOR_REFUSED"):
        L.register_executor("colab-runtime-B", actor="t")
    with pytest.raises(BudgetRefused, match="EXECUTOR_NOT_REGISTERED"):
        L.reserve(authorization_id=W["authorization_id"], request_sha256="a" * 64, task_id="T-" + "a" * 20,
                  attempt_id="1", approval_evidence_sha256="e" * 64, binding_id=None, worker_id="colab-runtime-B",
                  planned_run_id="r")


def test_B2_06_crash_after_commit_is_never_released_or_rerun(tmp_path):
    W = X.world(tmp_path, backend=SyntheticFixtureBackend(A5.FIX / A5.FIX_RUN, fail_with="worker crashed mid-run"))
    o, tid, st = run(W)
    r = o.budget.for_task(tid)
    assert st["state"] == "FAILED" and r["state"] == "RECONCILIATION_REQUIRED" and len(uses(W)) == 1
    with pytest.raises(BudgetRefused):
        o.budget.release(r["reservation_id"], reason="try", worker_id="worker-1")
    with pytest.raises(IllegalTransition):
        o.retry(tid, actor="x", reason="must be refused")
    assert o.dispatch(tid)["state"] == "FAILED" and W["backend"].calls == 1
    assert o.budget.capacity(W["authorization_id"])["used"] == 1


def test_B2_06_crash_between_uses_append_and_commit_event_needs_reconciliation(W):
    L = X.ledger_for(W["gov"], clock=lambda: datetime.now(timezone.utc))
    L.register_executor("w", actor="t")
    r = L.reserve(authorization_id=W["authorization_id"], request_sha256="a" * 64, task_id="T-" + "a" * 20,
                  attempt_id="1", approval_evidence_sha256="e" * 64, binding_id=None, worker_id="w", planned_run_id="r1")
    with L._cs() as v:                                                    # simulate: crash right after the uses append
        v.append_use({"authorization_id": W["authorization_id"], "run_id": "r1", "actor": "w",
                      "reserved_at": "2026-11-08T10:00:00+08:00", "scenarios": ["BASE"]})
    with pytest.raises(BudgetRefused, match="RELEASE_REFUSED"):
        L.release(r["reservation_id"], reason="x", worker_id="w")
    assert L.recover(worker_id="w") == [{"reservation_id": r["reservation_id"], "action": "RECONCILIATION_REQUIRED"}]
    assert L.capacity(W["authorization_id"])["used"] == 1


def test_B2_06_crash_before_commit_releases_only_with_proof(W):
    L = X.ledger_for(W["gov"], clock=lambda: datetime.now(timezone.utc))
    L.register_executor("w", actor="t")
    r = L.reserve(authorization_id=W["authorization_id"], request_sha256="a" * 64, task_id="T-" + "a" * 20,
                  attempt_id="1", approval_evidence_sha256="e" * 64, binding_id=None, worker_id="w", planned_run_id="r1")
    rel = L.release(r["reservation_id"], reason="pre-execution failure", worker_id="w")
    assert rel["event_type"] == "RELEASED" and "absent from the uses log" in rel["proof"]
    assert L.capacity(W["authorization_id"])["used"] == 0


def test_B2_06_running_lease_expiry_marks_reconciliation(tmp_path):
    clock = A5.StepClock()
    W = X.world(tmp_path)
    o = X.orchestrator(W, clock=clock)
    tid = X.submit_and_approve(W, o)
    real = W["backend"].run

    def die(*a, **k):                                                     # process dies: no exception handling runs
        raise SystemExit("killed")
    W["backend"].run = die
    with pytest.raises(SystemExit):
        o.dispatch(tid)
    W["backend"].run = real
    clock.jump(3600)
    o2 = X.orchestrator(W, clock=clock)
    out = o2.recover()
    assert {"task_id": tid, "action": "FAILED_NOT_REEXECUTED"} in out
    assert o2.budget.for_task(tid)["state"] == "RECONCILIATION_REQUIRED" and W["backend"].calls == 0


def test_B2_08_evaluator_failure_never_reruns(W, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("evaluator crashed")
    monkeypatch.setattr(C, "accept", boom)
    o, tid, st = run(W)
    assert st["state"] == "FAILED" and st["history"][-1]["reason"] == "EVALUATION_OR_ARCHIVE_FAILED_NO_RERUN"
    assert o.budget.for_task(tid)["state"] == "RECONCILIATION_REQUIRED"
    monkeypatch.undo()
    assert o.dispatch(tid)["state"] == "FAILED" and W["backend"].calls == 1 and len(uses(W)) == 1


def test_B2_08_incomplete_archive_is_never_success(tmp_path):
    W = X.world(tmp_path, backend=SyntheticFixtureBackend(A5.FIX / A5.FIX_RUN, drop_file="trades.csv"))
    o, tid, st = run(W)
    assert st["state"] == "FAILED" and st["history"][-1]["reason"] == "RECEIPT_ARCHIVE_INCOMPLETE"
    assert "ARCHIVED" not in [h["to"] for h in st["history"]]
    assert o.budget.for_task(tid)["state"] == "RECONCILIATION_REQUIRED"


def test_B2_01_corrupted_ledger_fails_closed(W):
    o = X.orchestrator(W)
    tid = X.submit_and_approve(W, o)
    p = W["gov"] / "registry/research_budget_events.jsonl"
    o.budget.register_executor("worker-1", actor="t")
    lines = p.read_text().splitlines()
    rec = json.loads(lines[0])
    rec["executor_id"] = "attacker"
    p.write_text(json.dumps(rec) + "\n")
    st = o.dispatch(tid)
    assert st["state"] == "BLOCKED" and "LEDGER_CORRUPTED" in st["history"][-1]["reason"] and W["backend"].calls == 0


# ------------------------------------------------------------------ B2-09 isolation (12) + no real execution
def test_B2_09_production_books_never_enable_controlled_execution(tmp_path, monkeypatch):
    monkeypatch.setenv("ENABLE_REAL_RUNNER", "true")
    assert REAL_EXECUTION_ENABLED is False
    ad = ControlledRunnerAdapter(SyntheticFixtureBackend(A5.FIX / A5.FIX_RUN), governance_root=ROOT)
    assert ad.enabled is False and ad.precheck({})[0].startswith("CONTROLLED_EXECUTION_DISABLED")
    with pytest.raises(AttributeError):
        ad.enabled = True
    with pytest.raises(ValueError):
        Orchestrator(tmp_path / "s", tmp_path / "o", controlled_adapter=ad)
    assert not (ROOT / "registry/research_budget_events.jsonl").exists()


def test_B2_09_non_synthetic_backend_is_never_enabled(W):
    class RealLooking(SyntheticFixtureBackend):
        synthetic = False
    ad = ControlledRunnerAdapter(RealLooking(A5.FIX / A5.FIX_RUN), governance_root=W["gov"])
    assert ad.enabled is False
    with pytest.raises(ValueError):
        X.orchestrator(W, backend=RealLooking(A5.FIX / A5.FIX_RUN))


def test_B2_09_repository_books_untouched_by_layer_a(tmp_path):
    before = A5.tree_fingerprint(ROOT)
    W = X.world(tmp_path)
    run(W)
    assert A5.tree_fingerprint(ROOT) == before
