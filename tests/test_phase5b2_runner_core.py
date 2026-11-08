"""Phase 5-B2 Layer B: the EXISTING ResearchOnlyRunner under the atomic budget ledger (Core b16179a, SYNTHETIC snapshot).

Skipped unless the pinned Core development reader + pandas/pyarrow are installed (CI job phase4a-dev-smoke, Colab).
"""
import json
from pathlib import Path

import pytest

from strategy_agent.execution import core_world as CW

ok, why = CW.core_available()
pytestmark = pytest.mark.skipif(not ok, reason=f"Layer B needs the pinned Core development reader: {why}")

from strategy_agent.evaluation import phase4c as C  # noqa: E402
from strategy_agent.execution.budget import BudgetLedger, BudgetRefused  # noqa: E402
from strategy_agent.execution.controlled import ControlledRunnerAdapter, ResearchOnlyRunnerBackend  # noqa: E402
from strategy_agent.orchestration.adapters import ExecutionFailed  # noqa: E402
from strategy_agent.phase5a import acceptance as A5  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
G40 = A5.code_identity()["git_sha"]


@pytest.fixture
def L(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "EXECUTOR_MODULES", ())
    w = CW.build(tmp_path / "w")
    g = w["gov"]
    led = BudgetLedger(authorizations_path=g / "governance/research_authorizations.jsonl",
                       approvers_path=g / "governance/approvers.json",
                       uses_path=g / "registry/research_authorization_uses.jsonl",
                       events_path=g / "registry/research_budget_events.jsonl", clock=lambda: w["now"], git_commit=G40)
    be = ResearchOnlyRunnerBackend(w["runner"], config_path=w["config_path"], snapshot_root=w["snapshot_root"],
                                   git_commit=G40, synthetic_snapshot=True, run_context={**w["run_context"], "agent_git_sha": G40})
    led.register_executor("worker-1", actor="t")
    return w, led, be, ControlledRunnerAdapter(be, governance_root=g)


def _reserve(w, led, ad, task="T-" + "c" * 20, req="b" * 64, run_id=None):
    return led.reserve(authorization_id=w["authorization_id"], request_sha256=req, task_id=task, attempt_id="1",
                       approval_evidence_sha256="d" * 64, binding_id=None, worker_id="worker-1",
                       planned_run_id=run_id or ad.planned_run_id(task, w["now"]))


def _uses(w):
    return (w["gov"] / "registry/research_authorization_uses.jsonl").read_text().splitlines()


def test_B2_05_layer_b_existing_runner_under_ledger_then_existing_evaluator_passes(L, tmp_path):
    w, led, be, ad = L
    doc = CW.request_for(w)
    assert ad.enabled and ad.precheck(doc) == []                         # runner's own probe, before any budget
    r = _reserve(w, led, ad)
    com = led.commit(r["reservation_id"], worker_id="worker-1", actor="hh4832", scenarios=doc["cost_profile_ref"]["scenarios"])
    ref = ad.execute(doc, tmp_path / "exec", use=com["use"], reservation=r, started_at=w["now"])
    acc = C.accept(ref["archive_path"], expected_manifest_sha256=ref["archive_manifest_sha256"], evaluator_commit_sha=G40,
                   evaluated_at="2026-11-08T12:00:00+08:00", governance_root=w["gov"])
    assert acc["status"] == "PASS", {k: v["status"] for k, v in acc["checks"].items()}
    led.settle(r["reservation_id"], receipt_sha256=ref["receipt_sha256"], output_manifest_sha256=ref["archive_manifest_sha256"],
               archive_ref=ref["archive_path"], worker_id="worker-1")
    assert led.for_task("T-" + "c" * 20)["history"] == ["RESERVED", "COMMITTED", "CONSUMED"]
    assert len(_uses(w)) == 1 and be.calls == 1                           # exactly one use: the runner never consumed again
    info = (Path(ref["archive_path"]) / "run_info.txt").read_text()
    assert "holdout_accessed=false" in info and f"agent_git_sha={G40}" in info
    # spent: the ledger refuses a second reservation and the legacy path refuses RA11
    with pytest.raises(BudgetRefused, match="BUDGET_EXHAUSTED"):
        _reserve(w, led, ad, task="T-" + "d" * 20, req="e" * 64, run_id="20261108_120000_second0")
    again = w["runner"].run(w["config_path"], snapshot_root=w["snapshot_root"], archive_root=tmp_path / "x",
                            git_commit=G40[:7], actor="x")
    assert again["executed"] is False and "RA11" in {b["code"] for b in again["blockers"]} and len(_uses(w)) == 1


def test_B2_07_layer_b_pin_mismatch_blocks_before_reservation(L):
    w, led, be, ad = L
    doc = CW.request_for(w)
    doc["cost_profile_ref"]["scenario_set_sha256"] = "0" * 64
    assert "RUNNER_CONFIG_COST_MISMATCH" in ad.precheck(doc)
    doc = CW.request_for(w)
    doc["dataset_snapshot_id"] = "TSM_DEV_LATEST"                          # never "use the latest data"
    assert "RUNNER_CONFIG_SNAPSHOT_MISMATCH" in ad.precheck(doc)
    assert led.reservations() == {} and _uses(w) == []


def test_B2_06_layer_b_runner_crash_after_commit_needs_reconciliation(L, tmp_path, monkeypatch):
    w, led, be, ad = L
    doc = CW.request_for(w)
    r = _reserve(w, led, ad)
    com = led.commit(r["reservation_id"], worker_id="worker-1", actor="hh4832", scenarios=doc["cost_profile_ref"]["scenarios"])

    def crash(*a, **k):
        raise RuntimeError("Core crashed mid-batch")
    monkeypatch.setattr(w["runner"], "_execute", crash)
    with pytest.raises(RuntimeError):
        ad.execute(doc, tmp_path / "exec", use=com["use"], reservation=r, started_at=w["now"])
    led.require_reconciliation(r["reservation_id"], reason="runner crashed", worker_id="worker-1")
    with pytest.raises(BudgetRefused):
        led.release(r["reservation_id"], reason="x", worker_id="worker-1")
    assert len(_uses(w)) == 1 and led.capacity(w["authorization_id"])["used"] == 1


def test_B2_02_layer_b_execute_without_committed_reservation_is_refused(L, tmp_path):
    w, led, be, ad = L
    with pytest.raises(ExecutionFailed, match="NO_COMMITTED_RESERVATION"):
        ad.execute(CW.request_for(w), tmp_path / "exec", use=None, reservation=None)
    assert be.calls == 0 and _uses(w) == []
