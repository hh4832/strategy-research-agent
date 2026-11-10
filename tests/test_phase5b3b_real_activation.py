"""Phase 5-B3-B: REAL_GATED controlled execution (activation gate A01-A10) on ISOLATED books with a MOCK runner.

No market data, no Core, no Holdout, no real authorization. Every refusal asserts the runner was NOT called.
Test names carry the gate they evidence (test_B3B_0X_...). Layer B (existing runner + Core receipt renewal) lives in
test_phase5b3b_real_runner_core.py.
"""
import json
from pathlib import Path

import pytest

from strategy_agent.evaluation import phase4c as C
from strategy_agent.execution import activation as AV
from strategy_agent.execution import real_testkit as R
from strategy_agent.execution import testkit as X
from strategy_agent.execution.budget import BudgetRefused
from strategy_agent.execution.controlled import ControlledRunnerAdapter
from strategy_agent.orchestration.adapters import ExecutionFailed
from strategy_agent.orchestration.orchestrator import Orchestrator
from strategy_agent.phase5a import acceptance as A5

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _no_c8(monkeypatch):
    monkeypatch.setattr(C, "EXECUTOR_MODULES", ())


@pytest.fixture
def W(tmp_path):
    return R.real_world(tmp_path)


def uses(W):
    p = W["gov"] / "registry/research_authorization_uses.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


def run(W, o=None):
    o = o or R.real_orchestrator(W)
    tid = X.submit_and_approve(W, o)
    return o, tid, o.dispatch(tid)


def blocked_without_runner(W, o, st, *conds):
    assert st["state"] == "BLOCKED", st["state"]
    txt = json.dumps(st["last_data"])
    for c in conds:
        assert c in txt, (c, txt[:600])
    assert W["backend"].calls == 0 and o.budget.reservations() == {} and uses(W) == []


# ------------------------------------------------------------------ B3B-01 refused without authorization
def test_B3B_01_success_path_requires_and_records_everything(W):
    o, tid, st = run(W)
    assert o.adapters["RESEARCH_ONLY_REAL"].mode == "REAL_GATED"
    assert st["state"] == "ARCHIVED" and st["budget_state"] == "CONSUMED" and W["backend"].calls == 1


def test_B3B_01_no_authorization_naming_this_executor_refuses_the_real_path(tmp_path):
    W = R.real_world(tmp_path, executor_id=None)                         # authorization without an executor
    with pytest.raises(ValueError, match="no approved, active, unexpired authorization names this executor"):
        R.real_orchestrator(W, executor_id=R.EXECUTOR)
    assert W["backend"].calls == 0 and not (W["gov"] / "registry/research_budget_events.jsonl").exists()


def test_B3B_01_unapproved_draft_authorization_never_executes(W):
    o = R.real_orchestrator(W)
    doc = json.loads(json.dumps(W["bound"]))
    doc["authorization_ref"]["authorization_id"] = "RA-SYNTHETIC_FIXTURE-DRAFT-0001"     # draft: not in the book
    assert any("A01_authorization_approved_active" in p for p in o.adapters["RESEARCH_ONLY_REAL"].precheck(doc))
    assert W["backend"].calls == 0


def test_B3B_01_revoked_after_construction_blocks_before_runner(W):
    from strategy_agent.governance.research_authorization_admin import revoke
    o = R.real_orchestrator(W)
    G = W["gov"] / "governance"
    revoke(G / "research_authorizations.jsonl", G / "approvers.json", authorization_id=W["authorization_id"],
           approved_by="hh4832", reason="TEST", approval_ref="https://example.test/revoke")
    s = o.submit(W["bound"], actor="TEST_FIXTURE_SUBMITTER", actor_kind="TEST_FIXTURE")
    assert s["state"] == "BLOCKED" and W["backend"].calls == 0


def test_B3B_01_expired_authorization_refused(W):
    later = lambda: __import__("datetime").datetime(2100, 1, 1).astimezone()  # noqa: E731
    assert AV.eligible_authorizations(W["gov"], R.EXECUTOR, later()) == []
    g = R.gate(W)
    g.clock = later
    assert "A01_authorization_approved_active" in g.evaluate(W["bound"], W["backend"])["failed"]


def test_B3B_01_spent_dev_authorization_is_never_eligible(W):
    g = R.gate(W)
    doc = json.loads(json.dumps(W["bound"]))
    doc["authorization_ref"]["authorization_id"] = "RA-IBKR_TSM-SMA520_DEV-18350b3adba8e28a7e5e"
    r = g.evaluate(doc, W["backend"])
    assert "A01_authorization_approved_active" in r["failed"]
    assert r["conditions"]["A01_authorization_approved_active"]["detail"]["spent_forever"] is True


def test_B3B_01_production_books_refuse_the_real_path_today(tmp_path):
    """The repository's own books: no eligible authorization, test verifier refused, no ledger file created."""
    W = R.real_world(tmp_path)
    g = AV.RealActivationGate(governance_root=ROOT, artifact_root=ROOT, executor_id=R.EXECUTOR, production_books=True,
                              readiness_manifest=lambda: R.world_manifest(W))
    ad = ControlledRunnerAdapter(W["backend"], governance_root=ROOT, activation=g)
    assert ad.mode == "REAL_GATED"
    from strategy_agent.readiness.b3c import expected_eligible
    before = A5.tree_fingerprint(ROOT)
    now = A5.StepClock()()
    # refused in both repository states: a test / fixture verifier (or none) is never accepted on production books,
    # and before the B3-C approval nothing is eligible at all
    for verifier in (None, W["fa"].verifier):
        with pytest.raises(ValueError):
            Orchestrator(tmp_path / "s", tmp_path / "o", root=ROOT, governance_root=ROOT, controlled_adapter=ad,
                         approval_verifier=verifier, worker=R.EXECUTOR)
    assert AV.eligible_authorizations(ROOT, R.EXECUTOR, now) == expected_eligible(now)
    assert AV.eligible_authorizations(ROOT, "another-runtime", now) == []
    assert A5.tree_fingerprint(ROOT) == before and not (ROOT / "registry/research_budget_events.jsonl").exists()
    assert W["backend"].calls == 0


def test_B3B_01_synthetic_backend_or_missing_gate_is_never_real():
    W = {"gov": ROOT}
    from strategy_agent.execution.controlled import SyntheticStubBackend
    be = SyntheticStubBackend(A5.FIX / A5.FIX_RUN, identity={})
    assert ControlledRunnerAdapter(be, governance_root=ROOT).mode == "DISABLED"
    real = R.MockRealRunnerBackend(A5.FIX / A5.FIX_RUN, identity={})
    assert ControlledRunnerAdapter(real, governance_root=ROOT).mode == "DISABLED"            # no gate
    g = AV.RealActivationGate(governance_root=W["gov"], artifact_root=ROOT, executor_id="x-exec", production_books=False)
    assert ControlledRunnerAdapter(real, governance_root=ROOT, activation=g).mode == "DISABLED"  # books mismatch


# ------------------------------------------------------------------ B3B-02 authorization / bound request
def test_B3B_02_wrong_request_hash_is_blocked(W):
    o = R.real_orchestrator(W)
    doc = json.loads(json.dumps(W["bound"]))
    doc["hypothesis"] = "edited after binding"
    pr = o.adapters["RESEARCH_ONLY_REAL"].precheck(doc)
    assert any("A02_verified_binding_evidence" in p for p in pr) and any("A03_authorization_bound_request" in p for p in pr)
    assert o.submit(doc, actor="TEST_FIXTURE_SUBMITTER", actor_kind="TEST_FIXTURE")["state"] == "BLOCKED"
    assert W["backend"].calls == 0


def test_B3B_02_runner_input_equals_the_authorized_request(W):
    o, tid, st = run(W)
    rc = json.loads(Path(o._execution_reference(o.log.get(tid))["receipt_path"]).read_text())
    from strategy_agent.orchestration import request as Q
    assert rc["execution_contract"]["request_sha256"] == Q.request_hash(W["bound"]) == rc["request_sha256"]
    assert rc["execution_contract"]["authorization_id"] == W["authorization_id"] == rc["authorization_id"]
    assert rc["binding_id"] == W["binding"]["binding_id"]


# ------------------------------------------------------------------ B3B-03 dataset / core / spec identity
@pytest.mark.parametrize("field,cond", [("core_commit", "A06_core_commit"), ("strategy_spec_sha256", "A04_strategy_spec_identity"),
                                        ("dataset_manifest_sha256", "A05_dataset_snapshot_manifest"),
                                        ("dataset_snapshot_id", "A05_dataset_snapshot_manifest")])
def test_B3B_03_runner_identity_mismatch_blocks_before_runner(tmp_path, field, cond):
    bad = {"core_commit": "9" * 40, "strategy_spec_sha256": "0" * 64, "dataset_manifest_sha256": "0" * 64,
           "dataset_snapshot_id": "SYNTHETIC_LATEST"}[field]
    W = R.real_world(tmp_path, identity_override={field: bad})
    o, tid, st = run(W)
    blocked_without_runner(W, o, st, cond)


def test_B3B_03_readiness_manifest_disagreeing_with_the_authorization_blocks(W):
    m = R.world_manifest(W)
    m["fields"]["dataset_manifest_sha256"]["value"] = "0" * 64
    o, tid, st = run(W, R.real_orchestrator(W, manifest=lambda: m))
    blocked_without_runner(W, o, st, "A10_readiness_manifest")
    W2 = R.real_world(W["work"] / "b")
    o2, _, st2 = run(W2, R.real_orchestrator(W2, manifest=lambda: (_ for _ in ()).throw(OSError("missing"))))
    blocked_without_runner(W2, o2, st2, "A10_readiness_manifest")


# ------------------------------------------------------------------ B3B-04 holdout
def test_B3B_04_holdout_crossing_or_access_is_refused(W):
    g = R.gate(W)
    doc = json.loads(json.dumps(W["bound"]))
    doc["research_window"]["end"] = "2025-02-01T00:00:00+08:00"
    assert "A09_holdout_excluded" in g.evaluate(doc, W["backend"])["failed"]
    doc = json.loads(json.dumps(W["bound"]))
    doc["holdout_policy"]["access"] = "READ"
    assert "A09_holdout_excluded" in g.evaluate(doc, W["backend"])["failed"]
    doc = json.loads(json.dumps(W["bound"]))
    doc["holdout_policy"]["lock_id"] = "HOLDOUT-OTHER-v1"
    assert "A09_holdout_excluded" in g.evaluate(doc, W["backend"])["failed"]
    assert g.evaluate(W["bound"], W["backend"])["conditions"]["A09_holdout_excluded"]["status"] == "PASS"
    assert W["backend"].calls == 0


# ------------------------------------------------------------------ B3B-05 budget
def test_B3B_05_second_use_of_the_same_budget_is_refused(W):
    o, tid, st = run(W)
    assert st["budget_state"] == "CONSUMED" and W["backend"].calls == 1
    assert "A07_budget_not_consumed" in R.gate(W).evaluate(W["bound"], W["backend"])["failed"]
    o2 = R.real_orchestrator(W, "second")                 # constructible (to recover), but every new run is refused
    s2 = o2.submit(W["bound"], actor="TEST_FIXTURE_SUBMITTER", actor_kind="TEST_FIXTURE")
    assert s2["state"] == "BLOCKED" and any(c["id"] == "G7_BUDGET" and c["status"] == "FAIL"
                                            for c in s2["governance"]["checks"])
    assert any("A07_budget_not_consumed" in p for p in o2.adapters["RESEARCH_ONLY_REAL"].precheck(W["bound"]))
    assert W["backend"].calls == 1 and len(uses(W)) == 1


def test_B3B_05_replay_and_duplicate_dispatch_run_at_most_once(W, tmp_path):
    o, tid, st = run(W)
    r = o.budget.for_task(tid)
    ad = o.adapters["RESEARCH_ONLY_REAL"]
    for i in range(3):
        with pytest.raises(ExecutionFailed):
            ad.execute(W["bound"], tmp_path / f"r{i}", reservation_id=r["reservation_id"], task_id=tid,
                       attempt_id=r["attempt_id"])
    assert o.dispatch(tid)["state"] == "ARCHIVED" and W["backend"].calls == 1 and len(uses(W)) == 1


# ------------------------------------------------------------------ B3B-06 single executor
def test_B3B_06_unauthorized_executor_is_refused(W):
    with pytest.raises(ValueError, match="not the activation gate's executor"):
        R.real_orchestrator(W, worker="worker-2")
    with pytest.raises(ValueError, match="no approved"):
        R.real_orchestrator(W, executor_id="worker-2", worker="worker-2")
    r = R.gate(W, executor_id="worker-2").evaluate(W["bound"], W["backend"])
    assert "A08_authorized_executor" in r["failed"] and W["backend"].calls == 0


def test_B3B_06_executor_bound_to_the_adapter_must_equal_the_gate(W, tmp_path):
    o = R.real_orchestrator(W)
    c = X.committed_running(W, o)
    ad = o.adapters["RESEARCH_ONLY_REAL"]
    ad._worker = "worker-2"                                    # an executor swap after construction
    with pytest.raises(ExecutionFailed, match="A08_authorized_executor"):
        ad.execute(c["doc"], tmp_path / "x", reservation_id=c["reservation_id"], task_id=c["task_id"],
                   attempt_id=c["attempt_id"])
    assert W["backend"].calls == 0


def test_B3B_06_second_process_cannot_run_the_same_reservation(tmp_path):
    import multiprocessing as mp
    calls = tmp_path / "calls.txt"
    W = R.real_world(tmp_path / "w", mock={"calls_file": calls})
    o = R.real_orchestrator(W, "state")
    c = X.committed_running(W, o)
    args = [(str(W["work"] / "state"), str(W["work"] / "out"), str(W["art"]), str(W["gov"]), str(W["backend"].fixture),
             W["backend"].identity, str(calls), c["doc"], c["reservation_id"], c["task_id"], c["attempt_id"],
             str(tmp_path / f"p{i}"), R.EXECUTOR) for i in range(3)]
    with mp.get_context("spawn").Pool(3) as pool:
        res = pool.map(R.mp_real_execute, args)
    assert res.count("EXECUTED") == 1 and all(x == "REFUSED:EXECUTION_ALREADY_STARTED" for x in res if x != "EXECUTED")
    assert W["backend"].calls == 1 and len(uses(W)) == 1


# ------------------------------------------------------------------ B3B-07 receipt / result consistency
def test_B3B_07_receipt_carries_the_complete_execution_contract(W):
    o, tid, st = run(W)
    rc = json.loads(Path(o._execution_reference(o.log.get(tid))["receipt_path"]).read_text())
    k = rc["execution_contract"]
    from strategy_agent.execution.controlled import CONTRACT_KEYS
    assert all(k.get(x) not in (None, "", {}, []) for x in CONTRACT_KEYS)
    assert rc["execution_mode"] == "REAL_GATED" and rc["synthetic"] is False and k["executor_id"] == R.EXECUTOR
    assert k["slippage_bps_per_side"] and k["signal_time_rule"] == "CORE_MODELED_AVAILABLE_AT"
    assert k["execution_time_rule"] == "NEXT_ELIGIBLE_BAR_OPEN"


def test_B3B_07_missing_runner_receipt_never_settles(tmp_path):
    W = R.real_world(tmp_path, mock={"receipt": {"error": "no Core receipt"}})
    o, tid, st = run(W)
    r = o.budget.for_task(tid)
    assert st["state"] == "FAILED" and "EXECUTION_CONTRACT_INCOMPLETE" == st["history"][-1]["reason"]
    assert r["state"] == "RECONCILIATION_REQUIRED" and "EXECUTION_COMPLETED" not in r["history"]


def test_B3B_07_receipt_that_is_not_a_core_renewal_never_settles(tmp_path):
    W = R.real_world(tmp_path, mock={"receipt": {"verification_ref": "HAND_MADE_RECEIPT"}})
    o, tid, st = run(W)
    assert st["state"] == "FAILED" and o.budget.for_task(tid)["state"] == "RECONCILIATION_REQUIRED"
    assert "CONTRACT_RUNNER_RECEIPT_NOT_A_CORE_RENEWAL" in json.dumps(st["last_data"])


def test_B3B_07_runner_result_inconsistent_with_the_receipt_never_settles(tmp_path):
    W = R.real_world(tmp_path, mock={"stamp": {"dataset_manifest_sha256": "0" * 64}})
    o, tid, st = run(W)
    assert st["state"] == "FAILED" and o.budget.for_task(tid)["state"] == "RECONCILIATION_REQUIRED"


def test_B3B_07_receipt_contract_modified_before_settlement_needs_reconciliation(W):
    o = R.real_orchestrator(W)
    tid = X.submit_and_approve(W, o)
    orig = o._archive

    def archive_then_tamper(task_id, doc):
        orig(task_id, doc)
        p = Path(o._execution_reference(o.log.get(task_id))["receipt_path"])
        d = json.loads(p.read_text())
        d["execution_contract"]["executor_id"] = "worker-2"
        p.write_text(json.dumps(d))
    o._archive = archive_then_tamper
    o.dispatch(tid)
    r = o.budget.for_task(tid)
    assert r["state"] == "RECONCILIATION_REQUIRED" and "CONSUMED" not in r["history"] and W["backend"].calls == 1


# ------------------------------------------------------------------ B3B-08 archive integrity and settlement
def test_B3B_08_settles_once_and_writes_one_immutable_execution_record(W):
    o, tid, st = run(W)
    recs = list((W["work"] / "out/execution_records").iterdir())
    assert len(recs) == 1
    rec = json.loads(recs[0].read_text())
    assert rec["budget_settlement"]["state"] == "CONSUMED" and rec["execution_mode"] == "REAL_GATED"
    assert rec["budget_settlement"]["history"][-1] == "CONSUMED" and rec["contract"]["run_id"]
    assert set(rec["task_archive_files_sha256"]) >= {"research_request.json", "execution_reference.json"}
    with pytest.raises(FileExistsError):
        o._execution_record(tid, rec["reservation_id"], {}, {"task_archive": rec["task_archive"]}, {})


@pytest.mark.parametrize("kind", ["archive_file_removed", "task_request_modified"])
def test_B3B_08_archive_lost_or_modified_never_settles(W, kind):
    o = R.real_orchestrator(W)
    tid = X.submit_and_approve(W, o)
    orig = o._archive

    def archive_then_tamper(task_id, doc):
        orig(task_id, doc)
        st = o.log.get(task_id)
        if kind == "archive_file_removed":
            (Path(o._execution_reference(st)["archive_path"]) / "trades.csv").unlink()
        else:
            ta = Path(next(e for e in reversed(st.events) if e["to"] == "ARCHIVED")["data"]["task_archive"])
            (ta / "research_request.json").write_text("{}")
    o._archive = archive_then_tamper
    o.dispatch(tid)
    r = o.budget.for_task(tid)
    assert r["state"] == "RECONCILIATION_REQUIRED" and "CONSUMED" not in r["history"]
    assert not (W["work"] / "out/execution_records").exists() and W["backend"].calls == 1


# ------------------------------------------------------------------ B3B-09 crash recovery / fail closed
def test_B3B_09_crash_after_start_is_never_rerun(tmp_path):
    W = R.real_world(tmp_path, mock={"fail_with": "runner crashed"})
    o, tid, st = run(W)
    r = o.budget.for_task(tid)
    assert st["state"] == "FAILED" and r["state"] == "RECONCILIATION_REQUIRED" and "EXECUTION_STARTED" in r["history"]
    o.recover()
    assert o.dispatch(tid)["state"] == "FAILED" and W["backend"].calls == 1
    with pytest.raises(BudgetRefused):
        o.budget.release(r["reservation_id"], reason="x", worker_id=R.EXECUTOR)
    o3 = R.real_orchestrator(W, "after_crash")                # the batch stays consumed: a new run is refused
    s3 = o3.submit(W["bound"], actor="TEST_FIXTURE_SUBMITTER", actor_kind="TEST_FIXTURE")
    assert s3["state"] == "BLOCKED" and W["backend"].calls == 1


def test_B3B_09_interrupted_dispatch_is_not_dispatched_again(W, tmp_path):
    clock = A5.StepClock()
    o = R.real_orchestrator(W, "s", clock=clock)
    c = X.committed_running(W, o)                         # crash between commit and execute (lease abandoned)
    clock.jump(7200)
    o2 = R.real_orchestrator(W, "s", clock=clock)
    o2.recover()
    assert o2.budget.for_task(c["task_id"])["state"] == "RECONCILIATION_REQUIRED"
    assert o2.dispatch(c["task_id"])["state"] == "FAILED" and W["backend"].calls == 0
