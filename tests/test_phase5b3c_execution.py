"""Phase 5-B3-C execution path on the REPOSITORY's books, non-consuming only (no Core needed):
run 1 (prepare) against the real authorization, the deterministic approval lines, and the fail-closed refusals of
run 2 before anything is written. The real execution itself is only ever run by the research lead in Colab."""
import json
from datetime import datetime

import pytest

from strategy_agent.execution import b3c_run as R
from strategy_agent.phase5a import acceptance as A5
from strategy_agent.readiness import b3c

ROOT = R.ROOT
NOW = datetime(2026, 11, 10, 14, 0, tzinfo=R.TPE)
G40 = "a" * 40
granted = b3c.formal_authorization_state()["state"] == "GRANTED_UNUSED"
needs_auth = pytest.mark.skipif(not granted, reason="the governed B3-C authorization is not active (unused) on these books")


def test_committed_completion_is_the_deterministic_build_from_spec_0_1_1():
    assert json.loads((ROOT / R.COMPLETION_PATH).read_text()) == json.loads(R._dumps(R.expected_completion()))  # noqa: SLF001
    c = R.expected_completion()
    assert c["strategy_spec_fragment"]["parameters"] == json.loads((ROOT / b3c.B3C_SPEC_PATH).read_text())["parameters"]
    assert "PIPELINE_ACCEPTANCE_ONLY" in c["hypothesis"]["purpose"]
    assert len(c["research_variables"]["changes"]) == 1 and "execution path" in c["research_variables"]["changes"][0]["core_variable"]


@needs_auth
def test_prepare_on_the_repository_books_is_non_consuming_and_passes_g1_g9(tmp_path):
    from strategy_agent.orchestration.governance import Governance
    before = A5.tree_fingerprint(ROOT)
    m = R.prepare(tmp_path / "prep", agent_commit=G40, now=NOW)
    assert A5.tree_fingerprint(ROOT) == before and m["consumed_batches"] == 0 and m["binding_written"] is False
    src = json.loads((tmp_path / "prep/request.json").read_text())
    assert src["authorization_ref"] == "PENDING" and src["execution_mode"] == "RESEARCH_ONLY_REAL"
    assert src["strategy_spec_sha256"] == b3c.B3C_SPEC_SHA256 and src["research_budget"] == {"batches_requested": 1}
    assert src["holdout_policy"]["access"] == "NO_ACCESS"
    bound = dict(src, authorization_ref={"authorization_id": R.AUTHORIZATION_ID})
    dec = Governance(ROOT, governance_root=ROOT).check(bound, test_mode=False, binding_check=False)
    st = {c["id"]: c["status"] for c in dec.checks}
    assert all(st[f"G{i}" + x] == "PASS" for i, x in ((1, "_REQUEST_SCHEMA"), (2, "_STRATEGY_SPEC"), (3, "_DATASET"),
                                                     (4, "_CORE_COST"), (5, "_AUTHORIZATION"), (6, "_AUTHORIZATION_SCOPE"),
                                                     (7, "_BUDGET"), (8, "_HOLDOUT"), (9, "_FORMAL_GATE"))), st
    assert st["G10_EXECUTION_MODE"] == "FAIL"           # only a REAL_GATED orchestrator enables it
    for k in ("BIND_AUTHORIZATION", "APPROVE_TASK"):
        assert m[k].startswith("RESEARCH-APPROVAL v1 decision=APPROVE intent=" + k)
    assert f"task={m['task_id']}" in m["APPROVE_TASK"] and m["source_request_sha256"] in m["BIND_AUTHORIZATION"]
    assert R.load_prepared(tmp_path / "prep")[0] == m
    with pytest.raises(FileExistsError):
        R.prepare(tmp_path / "prep", agent_commit=G40, now=NOW)


@needs_auth
def test_execute_refuses_before_anything_when_a_claim_exists(tmp_path):
    R.prepare(tmp_path / "prep", agent_commit=G40, now=NOW)
    (tmp_path / "claim").mkdir()
    (tmp_path / "claim" / R.CLAIM).write_text("{}")
    before = A5.tree_fingerprint(ROOT)
    with pytest.raises(R.B3CStopped) as e:
        R.execute(tmp_path / "prep", bind_url="x", task_url="y", verifier=None, backend=None,
                  work_dir=tmp_path / "w", claim_dir=tmp_path / "claim")
    assert e.value.code == "CLAIM_EXISTS" and not (tmp_path / "w").exists() and A5.tree_fingerprint(ROOT) == before


@needs_auth
def test_execute_on_the_repository_books_refuses_fixture_evidence_and_writes_nothing(tmp_path):
    from strategy_agent.approval.testkit import FixtureApprovals
    m = R.prepare(tmp_path / "prep", agent_commit=G40, now=NOW)
    fa = FixtureApprovals()
    before = A5.tree_fingerprint(ROOT)
    with pytest.raises(R.B3CStopped) as e:
        R.execute(tmp_path / "prep", bind_url=fa.comment(m["BIND_AUTHORIZATION"]), task_url=fa.comment(m["APPROVE_TASK"]),
                  verifier=fa.verifier, backend=None, work_dir=tmp_path / "w", claim_dir=tmp_path / "claim")
    assert e.value.code == "BINDING_REJECTED" and any(f["code"] == "B08_TRUST" for f in e.value.detail)
    assert A5.tree_fingerprint(ROOT) == before and not (tmp_path / "claim").exists() and not (tmp_path / "w").exists()


@needs_auth
def test_prepared_files_and_manifest_are_tamper_evident(tmp_path):
    R.prepare(tmp_path / "prep", agent_commit=G40, now=NOW)
    p = tmp_path / "prep/proposal.json"
    p.write_text(p.read_text() + " ")
    with pytest.raises(R.B3CStopped) as e:
        R.load_prepared(tmp_path / "prep")
    assert e.value.code == "PREPARED_FILES_CHANGED"


@needs_auth
def test_production_gate_on_the_repository_books_passes_everything_but_the_binding(tmp_path, monkeypatch):
    """Regression (first Colab EXECUTE stopped at A10): execute() must give the REAL_GATED gate the B3-A production
    readiness manifest. On the repository books every condition except A02/A03 (no binding yet) must PASS."""
    from strategy_agent.execution import activation as AV
    from strategy_agent.execution import identity as I
    from strategy_agent.execution.real_testkit import MockRealRunnerBackend
    R.prepare(tmp_path / "prep", agent_commit=G40, now=NOW)
    src = json.loads((tmp_path / "prep/request.json").read_text())
    bound = dict(src, authorization_ref={"authorization_id": R.AUTHORIZATION_ID})
    seen = {}

    class Spy(AV.RealActivationGate):
        def __init__(self, **kw):
            seen.update(kw)
            raise RuntimeError("STOP_AFTER_GATE_CONSTRUCTION")
    monkeypatch.setattr(AV, "RealActivationGate", Spy)
    be = MockRealRunnerBackend(A5.FIX / A5.FIX_RUN, identity=I.request_identity(bound))
    from strategy_agent.approval.testkit import FixtureApprovals
    fa = FixtureApprovals()
    m = R.load_prepared(tmp_path / "prep")[0]
    import strategy_agent.binding.service as S

    class DryOk:                      # only to reach the gate construction; nothing is written
        def __init__(self, **kw): pass
        def bind(self, dry_run=True, **kw):
            return {"would_write": {"bound_request_sha256": m["bound_request_sha256"]}}
    monkeypatch.setattr(S, "AuthorizationBindingService", DryOk)
    monkeypatch.setattr(fa.verifier, "verify", lambda *a, **k: {})
    with pytest.raises(RuntimeError, match="STOP_AFTER_GATE_CONSTRUCTION"):
        R.execute(tmp_path / "prep", bind_url="b", task_url="t", verifier=fa.verifier, backend=be,
                  work_dir=tmp_path / "w", claim_dir=tmp_path / "c")
    assert callable(seen["readiness_manifest"]) and seen["production_books"] is True
    monkeypatch.undo()
    g = AV.RealActivationGate(governance_root=ROOT, artifact_root=ROOT, executor_id=R.EXECUTOR, production_books=True,
                              readiness_manifest=seen["readiness_manifest"])
    c = g.evaluate(bound, be)["conditions"]
    assert {k for k, v in c.items() if v["status"] != "PASS"} == {"A02_verified_binding_evidence",
                                                                   "A03_authorization_bound_request"}, c
