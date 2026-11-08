"""Independent P0-2 archive-settlement regression gate for GitHub Actions.

Runs only with isolated synthetic governance and test backend. This does not
rely on the remediation module's helper functions or report statuses.
"""
import json
from pathlib import Path

import pytest

from strategy_agent.execution import testkit as X


def _task_archive(orchestrator, task_id):
    events = orchestrator.log.get(task_id).events
    archived = next(e for e in reversed(events) if e["to"] == "ARCHIVED")
    return Path(archived["data"]["task_archive"])


@pytest.mark.parametrize("mutation", ["delete", "change_identity", "change_bytes", "bad_json"])
def test_independent_p0_2_archive_request_modified_before_settlement(tmp_path, mutation):
    world = X.world(tmp_path / "isolated")
    orchestrator = X.orchestrator(world)
    task_id = X.submit_and_approve(world, orchestrator)
    archive_original = orchestrator._archive

    def archive_and_modify(task, request):
        archive_original(task, request)
        target = _task_archive(orchestrator, task) / "research_request.json"
        assert target.is_file(), "control archive must contain the research request"
        if mutation == "delete":
            target.unlink()
        elif mutation == "change_identity":
            payload = json.loads(target.read_text())
            payload["dataset_snapshot_id"] = "SYNTHETIC_TAMPERED"
            target.write_text(json.dumps(payload, sort_keys=True) + "\n")
        elif mutation == "change_bytes":
            target.write_bytes(target.read_bytes() + b" ")
        else:
            target.write_text("{invalid-json")

    orchestrator._archive = archive_and_modify
    orchestrator.dispatch(task_id)
    rec = orchestrator.budget.for_task(task_id)
    assert world["backend"].calls == 1
    assert rec["state"] == "RECONCILIATION_REQUIRED", rec
    assert "EVIDENCE_VERIFIED" not in rec["history"], rec
    assert "CONSUMED" not in rec["history"], rec


def test_independent_p0_2_request_changed_after_verification_before_settlement(tmp_path):
    world = X.world(tmp_path / "isolated")
    orchestrator = X.orchestrator(world)
    task_id = X.submit_and_approve(world, orchestrator)
    original = orchestrator.budget.record_evidence_verified

    def verify_then_mutate(*args, **kwargs):
        event = original(*args, **kwargs)
        target = _task_archive(orchestrator, task_id) / "research_request.json"
        target.unlink()
        return event

    orchestrator.budget.record_evidence_verified = verify_then_mutate
    orchestrator.dispatch(task_id)
    rec = orchestrator.budget.for_task(task_id)
    assert world["backend"].calls == 1
    assert "EVIDENCE_VERIFIED" in rec["history"], rec
    assert "CONSUMED" not in rec["history"], rec
    assert rec["state"] == "RECONCILIATION_REQUIRED", rec


def test_independent_p0_2_unchanged_archive_settles_once(tmp_path):
    world = X.world(tmp_path / "isolated")
    orchestrator = X.orchestrator(world)
    task_id = X.submit_and_approve(world, orchestrator)
    orchestrator.dispatch(task_id)
    rec = orchestrator.budget.for_task(task_id)
    assert rec["state"] == "CONSUMED", rec
    assert rec["history"].count("CONSUMED") == 1
    assert world["backend"].calls == 1
    orchestrator.dispatch(task_id)
    assert world["backend"].calls == 1
    assert orchestrator.budget.for_task(task_id)["history"].count("CONSUMED") == 1
