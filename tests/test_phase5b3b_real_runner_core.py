"""Phase 5-B3-B Layer B: REAL_GATED with the EXISTING ResearchOnlyRunner + Core b16179a + Core receipt renewal.

ISOLATED books, SYNTHETIC development snapshot produced by Core's own producer, a NEW test authorization
(RA-IBKR_TSM-SMA520_B3BTEST-0001) naming one executor. The backend is NON-synthetic (``synthetic_snapshot=False``) so
the real-mode checks run: session config differs from the signed canonical config only in the Core-renewed receipt.
Skipped unless the pinned Core development reader + pandas/pyarrow are installed (CI phase5b3b-real-execution, Colab).
"""
import json
from pathlib import Path

import pytest

from strategy_agent.execution import core_world as CW

ok, why = CW.core_available()
pytestmark = pytest.mark.skipif(not ok, reason=f"Layer B needs the pinned Core development reader: {why}")

from strategy_agent.evaluation import phase4c as C  # noqa: E402
from strategy_agent.execution import acceptance as XA  # noqa: E402
from strategy_agent.execution.activation import RealActivationGate  # noqa: E402
from strategy_agent.execution.controlled import ResearchOnlyRunnerBackend  # noqa: E402
from strategy_agent.phase5a import acceptance as A5  # noqa: E402
from strategy_agent.research_only.runner import canonical_sha  # noqa: E402

G40 = A5.code_identity()["git_sha"]
EX = "colab-hh4832-exec-01"
AID = "RA-IBKR_TSM-SMA520_B3BTEST-0001"


@pytest.fixture
def L(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "EXECUTOR_MODULES", ())
    w = CW.build(tmp_path / "w", authorization_id=AID, executor_id=EX)
    return w


def _backend(w, config_path=None, canonical=True):
    return ResearchOnlyRunnerBackend(w["runner"], config_path=config_path or w["config_path"],
                                     snapshot_root=w["snapshot_root"], git_commit=G40, synthetic_snapshot=False,
                                     run_context={**w["run_context"], "agent_git_sha": G40},
                                     canonical_config_path=w["canonical_config_path"] if canonical else None)


def _orch(w, be, tmp_path, name="o"):
    g = RealActivationGate(governance_root=w["gov"], artifact_root=w["art"], executor_id=EX, production_books=False,
                           readiness_manifest=lambda: CW.readiness_manifest(w))
    return CW.orchestrator(w, tmp_path / name, be, worker=EX, activation=g)


def _uses(w):
    return (w["gov"] / "registry/research_authorization_uses.jsonl").read_text().splitlines()


def test_B3B_07_layer_b_existing_runner_real_gated_with_core_receipt_renewal(L, tmp_path):
    w = L
    be = _backend(w)
    o = _orch(w, be, tmp_path)
    ad = o.adapters["RESEARCH_ONLY_REAL"]
    assert ad.mode == "REAL_GATED" and ad.precheck(w["bound"]) == []
    s = o.submit(w["bound"], actor="TEST_FIXTURE_SUBMITTER", actor_kind="TEST_FIXTURE")
    w["fa"].approve_task(o, s["task_id"])
    st = o.dispatch(s["task_id"])
    assert st["state"] == "ARCHIVED" and st["budget_state"] == "CONSUMED" and be.calls == 1 and len(_uses(w)) == 1
    ref = o._execution_reference(o.log.get(s["task_id"]))
    rc = json.loads(Path(ref["receipt_path"]).read_text())
    k = rc["execution_contract"]
    assert rc["execution_mode"] == "REAL_GATED" and rc["synthetic"] is False
    assert k["runner_receipt"]["verification_ref"].startswith("DEVELOPMENT_RECEIPT_RENEWAL:")     # Core, not hand-made
    assert k["authorization_id"] == AID and k["executor_id"] == EX and k["agent_git_sha"] == G40
    assert k["core_commit"] == w["core_sha"] and k["dataset_snapshot_id"] == w["snapshot"]["snapshot_id"]
    acc = XA.fresh_process_accept(ref["archive_path"], ref["archive_manifest_sha256"], G40, w["gov"])
    assert acc["status"] == "PASS", {x: v["status"] for x, v in acc["checks"].items()}
    rec = json.loads(next((tmp_path / "o/out/execution_records").iterdir()).read_text())
    assert rec["budget_settlement"]["state"] == "CONSUMED" and rec["contract"] == k


def _tampered_session(w, tmp_path, mutate) -> Path:
    cfg = json.loads(Path(w["config_path"]).read_text())
    mutate(cfg)
    cfg["provenance"]["config_sha256"] = canonical_sha(cfg)                 # self-consistent, still not acceptable
    p = tmp_path / "tampered_session.json"
    p.write_text(json.dumps(cfg, indent=2))
    return p


@pytest.mark.parametrize("mutate,problem", [
    (lambda c: c["snapshot_binding"]["integrity_receipt"].__setitem__("verification_ref", "HAND_MADE:0"),
     "RUNNER_RECEIPT_NOT_RENEWED_BY_CORE"),
    (lambda c: c["windows"].__setitem__("end", "2024-06-30T00:00:00-04:00"), "RUNNER_SESSION_CONFIG_DIFFERS_BEYOND_RECEIPT"),
])
def test_B3B_03_layer_b_session_config_beyond_the_core_receipt_blocks_before_runner(L, tmp_path, mutate, problem):
    w = L
    be = _backend(w, config_path=_tampered_session(w, tmp_path, mutate))
    o = _orch(w, be, tmp_path)
    assert any(problem in p for p in o.adapters["RESEARCH_ONLY_REAL"].precheck(w["bound"]))
    s = o.submit(w["bound"], actor="TEST_FIXTURE_SUBMITTER", actor_kind="TEST_FIXTURE")
    w["fa"].approve_task(o, s["task_id"])
    assert o.dispatch(s["task_id"])["state"] == "BLOCKED" and be.calls == 0 and _uses(w) == []


def test_B3B_03_layer_b_real_backend_without_the_signed_canonical_config_is_blocked(L, tmp_path):
    w = L
    be = _backend(w, canonical=False)
    o = _orch(w, be, tmp_path)
    assert any("RUNNER_CANONICAL_CONFIG_REQUIRED" in p for p in o.adapters["RESEARCH_ONLY_REAL"].precheck(w["bound"]))
    assert be.calls == 0
