"""Phase 5-B2 Layer B: the EXISTING ResearchOnlyRunner under the atomic budget ledger (Core b16179a, SYNTHETIC snapshot).

Skipped unless the pinned Core development reader + pandas/pyarrow are installed (CI job phase5b2-controlled, Colab).

Security remediation: Layer B now runs through the production-mode Orchestrator on ISOLATED books with a real
A2 -> verified binding -> bound request (``core_world``), so the request identity is independent of the runner config
and is compared with the runner's OWN execution identity (config + snapshot manifest file + installed Core).
"""
import json
from pathlib import Path

import pytest

from strategy_agent.execution import core_world as CW

ok, why = CW.core_available()
pytestmark = pytest.mark.skipif(not ok, reason=f"Layer B needs the pinned Core development reader: {why}")

from strategy_agent.evaluation import phase4c as C  # noqa: E402
from strategy_agent.execution import acceptance as XA  # noqa: E402
from strategy_agent.execution.budget import BudgetRefused  # noqa: E402
from strategy_agent.execution.controlled import ResearchOnlyRunnerBackend  # noqa: E402
from strategy_agent.orchestration.adapters import ExecutionFailed  # noqa: E402
from strategy_agent.phase5a import acceptance as A5  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
G40 = A5.code_identity()["git_sha"]
MARKS = ["RESERVED", "COMMITTED", "EXECUTION_STARTED", "EXECUTION_COMPLETED", "EVIDENCE_VERIFIED", "CONSUMED"]


@pytest.fixture
def L(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "EXECUTOR_MODULES", ())
    w = CW.build(tmp_path / "w")
    be = ResearchOnlyRunnerBackend(w["runner"], config_path=w["config_path"], snapshot_root=w["snapshot_root"],
                                   git_commit=G40, synthetic_snapshot=True, run_context={**w["run_context"], "agent_git_sha": G40})
    o = CW.orchestrator(w, tmp_path / "o", be)
    return w, be, o


def _submit(w, o, doc=None):
    s = o.submit(doc or w["bound"], actor="TEST_FIXTURE_SUBMITTER", actor_kind="TEST_FIXTURE")
    if s["state"] == "PENDING_APPROVAL":
        w["fa"].approve_task(o, s["task_id"])
    return s


def _uses(w):
    return (w["gov"] / "registry/research_authorization_uses.jsonl").read_text().splitlines()


def test_B2_05_layer_b_existing_runner_under_ledger_then_existing_evaluator_passes(L):
    w, be, o = L
    ad = o.adapters["RESEARCH_ONLY_REAL"]
    assert ad.enabled and ad.precheck(w["bound"]) == []              # identity + runner probe, before any budget
    s = _submit(w, o)
    st = o.dispatch(s["task_id"])
    assert st["state"] == "ARCHIVED" and st["budget_state"] == "CONSUMED", st["last_data"]
    r = o.budget.for_task(s["task_id"])
    assert r["history"] == MARKS and len(_uses(w)) == 1 and be.calls == 1
    ref = o._execution_reference(o.log.get(s["task_id"]))
    acc = XA.fresh_process_accept(ref["archive_path"], ref["archive_manifest_sha256"], G40, w["gov"])
    assert acc["status"] == "PASS", {k: v["status"] for k, v in acc["checks"].items()}
    info = (Path(ref["archive_path"]) / "run_info.txt").read_text()
    assert "holdout_accessed=false" in info and f"agent_git_sha={G40}" in info
    rc = json.loads(Path(ref["receipt_path"]).read_text())
    assert rc["binding_id"] == w["binding"]["binding_id"] and rc["use_record_sha256"] == json.loads(_uses(w)[0])["record_sha256"]
    # spent: the ledger refuses a second reservation and the legacy path refuses RA11
    with pytest.raises(BudgetRefused, match="BUDGET_EXHAUSTED"):
        o.budget.reserve(authorization_id=w["authorization_id"], request_sha256="e" * 64, task_id="T-" + "d" * 20,
                         attempt_id="x", approval_evidence_sha256="d" * 64, binding_id=None, worker_id="worker-1",
                         planned_run_id="20261108_120000_second0")
    again = w["runner"].run(w["config_path"], snapshot_root=w["snapshot_root"], archive_root=w["gov"].parent / "x",
                            git_commit=G40[:7], actor="x")
    assert again["executed"] is False and "RA11" in {b["code"] for b in again["blockers"]} and len(_uses(w)) == 1


def test_B2_07_layer_b_pin_mismatch_blocks_before_reservation(L):
    w, be, o = L
    ad = o.adapters["RESEARCH_ONLY_REAL"]
    doc = CW.bound_request(w)
    doc["cost_profile_ref"]["scenario_set_sha256"] = "0" * 64
    pr = ad.precheck(doc)
    assert any("cost_profile" in p for p in pr) and any(p.startswith("BINDING_INVALID") for p in pr)
    doc = CW.bound_request(w)
    doc["dataset_snapshot_id"] = "TSM_DEV_LATEST"                          # never "use the latest data"
    assert any("dataset_snapshot_id" in p for p in ad.precheck(doc))
    assert o.budget.reservations() == {} and _uses(w) == [] and be.calls == 0


def test_B2_06_layer_b_runner_crash_after_commit_needs_reconciliation(L, monkeypatch):
    w, be, o = L

    def crash(*a, **k):
        raise RuntimeError("Core crashed mid-batch")
    monkeypatch.setattr(w["runner"], "_execute", crash)
    s = _submit(w, o)
    st = o.dispatch(s["task_id"])
    r = o.budget.for_task(s["task_id"])
    assert st["state"] == "FAILED" and r["state"] == "RECONCILIATION_REQUIRED" and "EXECUTION_STARTED" in r["history"]
    with pytest.raises(BudgetRefused):
        o.budget.release(r["reservation_id"], reason="x", worker_id="worker-1")
    assert o.dispatch(s["task_id"])["state"] == "FAILED" and be.calls == 1          # never re-run
    assert len(_uses(w)) == 1 and o.budget.capacity(w["authorization_id"])["used"] == 1


def test_B2_02_layer_b_execute_without_committed_reservation_is_refused(L, tmp_path):
    w, be, o = L
    ad = o.adapters["RESEARCH_ONLY_REAL"]
    with pytest.raises(ExecutionFailed, match="NO_COMMITTED_RESERVATION"):
        ad.execute(CW.bound_request(w), tmp_path / "exec")
    with pytest.raises(ExecutionFailed, match="UNKNOWN_RESERVATION"):
        ad.execute(CW.bound_request(w), tmp_path / "exec2", reservation_id="BR-" + "0" * 24, task_id="T-x", attempt_id="T-x:1")
    assert be.calls == 0 and _uses(w) == []


# ------------------------------------------------------------------------- P0-3 identity (Layer B, real runner)
@pytest.mark.parametrize("field,bad", [("core_commit", "0" * 40), ("strategy_spec_sha256", "0" * 64),
                                       ("dataset_manifest_sha256", "0" * 64)])
def test_P0_3_layer_b_wrong_core_spec_manifest_blocked_before_runner(L, field, bad):
    w, be, o = L
    ad = o.adapters["RESEARCH_ONLY_REAL"]
    doc = {**CW.bound_request(w), field: bad}
    pr = ad.precheck(doc)
    assert pr and any(field in p or p.startswith("BINDING_INVALID") for p in pr)
    s = o.submit(doc, actor="TEST_FIXTURE_SUBMITTER", actor_kind="TEST_FIXTURE")      # not the bound request
    assert s["state"] == "BLOCKED"
    assert be.calls == 0 and o.budget.reservations() == {} and _uses(w) == []


def test_P0_3_layer_b_runner_snapshot_manifest_swapped_on_disk_blocks_before_runner(L):
    w, be, o = L
    mp = w["snapshot_root"] / w["config"]["snapshot_binding"].get("manifest_relative_path", "manifest.json")
    mp.chmod(0o644)
    mp.write_bytes(mp.read_bytes() + b" ")                                 # another manifest than the pinned one
    s = _submit(w, o)
    st = o.dispatch(s["task_id"])
    assert st["state"] == "BLOCKED" and "RUNNER_SNAPSHOT_MANIFEST_SHA" in json.dumps(st["last_data"])
    assert be.calls == 0 and o.budget.reservations() == {} and _uses(w) == []


def test_P0_3_layer_b_installed_core_differs_from_request_blocks_before_runner(L, monkeypatch):
    w, be, o = L
    be._core()
    real = be.core_native.core_code_identity
    monkeypatch.setattr(be.core_native, "core_code_identity", lambda: {**real(), "core_git_sha": "9" * 40})
    s = _submit(w, o)
    st = o.dispatch(s["task_id"])
    assert st["state"] == "BLOCKED" and "core_commit" in json.dumps(st["last_data"])
    assert be.calls == 0 and o.budget.reservations() == {} and _uses(w) == []


def test_P0_3_layer_b_runner_config_changed_after_claim_never_runs_core(L, monkeypatch):
    """TOCTOU: the runner re-derives its identity right before Core runs; a changed config is refused (and the
    committed batch goes to reconciliation, never re-run)."""
    w, be, o = L
    orig, claim = be.execution_identity, o.budget.claim_execution
    granted = []

    def claim_then_flag(*a, **k):
        g = claim(*a, **k)
        granted.append(1)
        return g

    def flip():
        idn, bad = orig()
        return ({**idn, "strategy_parameters": {"fast": 10, "slow": 20}}, bad) if granted else (idn, bad)
    monkeypatch.setattr(o.budget, "claim_execution", claim_then_flag)
    monkeypatch.setattr(be, "execution_identity", flip)
    ran = []
    monkeypatch.setattr(w["runner"], "_execute", lambda *a, **k: ran.append(1) or {"executed": False})
    s = _submit(w, o)
    st = o.dispatch(s["task_id"])
    r = o.budget.for_task(s["task_id"])
    assert st["state"] == "FAILED" and "RUNNER_IDENTITY_CHANGED" in json.dumps(st["last_data"]) and ran == []
    assert r["state"] == "RECONCILIATION_REQUIRED"
