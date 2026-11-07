"""Phase 5-A3: A2 Proposal x A1 Orchestrator integration (A3-01..A3-10).

SYNTHETIC_FIXTURE only: proposals come from A2 over the labelled fixtures/proposal completions, archives from
tests/fixtures/phase4c, governance from temporary copies. The repository's governance/registry must be unchanged.
"""
import ast
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from strategy_agent.evaluation import phase4c as C
from strategy_agent.orchestration import request as Q
from strategy_agent.orchestration.orchestrator import Orchestrator
from strategy_agent.phase5a import acceptance as ACC
from strategy_agent.phase5a import integration as I
from strategy_agent.phase5a.__main__ import main as cli

ROOT = Path(__file__).resolve().parents[1]
AT = "2026-11-07T12:00:00+08:00"


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    """One in-process Route A (+ fixture Route B). C8 is a process-level check: other tests in this pytest process
    import executors, so it is neutralised here and asserted for real in the fresh-subprocess test below."""
    mp = pytest.MonkeyPatch()
    mp.setattr(C, "EXECUTOR_MODULES", ())
    before = ACC.tree_fingerprint(ROOT)
    work = tmp_path_factory.mktemp("a3")
    a = ACC.route_a(work / "route_a", agent_commit_sha="a" * 40, clock=ACC.StepClock())
    contract = I.contract_check()
    iso = ACC.isolation_evidence()
    g = ACC.gates(contract, a, None, iso, fresh_process=False)
    yield {"a": a, "g": g, "contract": contract, "work": work, "before": before}
    mp.undo()


def test_shared_schema_unchanged_and_identical_for_a1_and_a2():
    r = I.contract_check()
    assert r["status"] == "PASS", r["checks"]
    assert r["observed"]["git_blob"] == "4836b8f5738d26a95d1fe6c6a0d55c683a810a93"
    assert r["observed"]["sha256"] == "1b0530f321c6c71e9eac1bbe5f9c33e3f92e4af4c4ce11b80777c9a94c5324eb"
    out = subprocess.run(["git", "-C", str(ROOT), "diff", "--quiet", I.CONTRACT["commit"], "--",
                          "schemas/RESEARCH_REQUEST_SCHEMA.json"])
    assert out.returncode == 0, "shared schema differs from the 5d088c4 contract commit"


def test_a3_01_a2_output_is_a_valid_a1_request(run):
    p = run["a"]["positive"]
    assert run["g"]["A3-01"]["status"] == "PASS"
    assert p["a1_validate_errors"] == [] and set(p["a1_pending_fields"]) == {"authorization_ref", "core_commit"}


def test_a3_02_actual_a2_export_accepted_by_actual_a1_orchestrator(run):
    p = run["a"]["positive"]
    assert run["g"]["A3-02"]["evidence"] == {"contract_compatibility": "PASS", "actual_proposal_to_request_export": "PASS",
                                             "orchestrator_acceptance": "PASS", "full_e2e_execution": "PASS"}
    # the request the orchestrator recorded is byte-for-byte the file A2 wrote (not a hand-made fixture)
    on_disk = json.loads(Path(p["export"]["request_path"]).read_text())
    assert on_disk == p["request"] and Q.request_hash(on_disk) == p["trace"]["request_hash"]
    assert p["export"]["provenance"]["request_submitted"] is False      # A2 itself never submits
    assert p["request"]["hypothesis"].startswith("SYNTHETIC_FIXTURE")
    assert p["request"]["authorization_ref"] == "PENDING" and p["request"]["execution_mode"] == "SYNTHETIC_TEST"
    link = p["trace"]["links"]
    assert len(link) == 1 and link[0]["proposal_sha256"] == p["proposal"]["proposal_sha256"]
    assert link[0]["request_file_sha256"] == p["export"]["request_file_sha256"]


def test_a3_03_incomplete_proposal_is_pending_information_without_request(run):
    inc = run["a"]["incomplete"]
    assert run["g"]["A3-03"]["status"] == "PASS"
    assert inc["proposal_status"] == "PENDING_INFORMATION" and inc["result"]["executable_request"] is False
    assert {"RULE_AST_PENDING", "SIGNAL_TIME", "PIT_UNKNOWN"} <= set(inc["result"]["blockers"])


def test_a3_04_pending_authorization_never_approved_queued_or_running(run):
    pa = run["a"]["pending_authorization"]
    assert run["g"]["A3-04"]["status"] == "PASS"
    assert pa["proposal_input_kind"] == "USER_HYPOTHESIS" and pa["request_authorization_ref"] == "PENDING"
    assert pa["states"] == ["PROPOSED", "VALIDATED", "BLOCKED"]
    assert pa["approve_attempt"]["error"] == "GOVERNANCE_BLOCKED"


def test_a3_05_unauthorized_formal_research_blocked_and_synthetic_never_formal(run):
    pa = run["a"]["pending_authorization"]
    assert run["g"]["A3-05"]["status"] == "PASS"
    assert {"G5_AUTHORIZATION", "G10_EXECUTION_MODE"} <= {k for k, v in pa["governance"].items() if v == "FAIL"}
    assert pa["in_test_orchestrator"]["checks"]["G0_TEST_MODE"] == "FAIL"
    assert pa["synthetic_request_in_production"]["checks"]["G0_TEST_MODE"] == "FAIL"


def test_a3_06_synthetic_lifecycle(run):
    p = run["a"]["positive"]
    assert run["g"]["A3-06"]["status"] == "PASS" and p["states"] == ACC.LIFECYCLE
    assert p["real_approver_in_test_mode"]["error"] == "APPROVER"
    assert set(p["archive_files"]) == set(__import__("strategy_agent.orchestration.orchestrator",
                                                     fromlist=["ARCHIVE_FILES"]).ARCHIVE_FILES)
    assert "test_evaluations" in p["trace"]["evaluation_reference"]["dir"]


def test_a3_07_existing_archive_replay_through_existing_evaluator(run):
    rb = run["a"]["replay_fixture"]
    assert run["g"]["A3-07"]["status"] == "PASS"
    assert rb["execution_reference"]["evidence"] == "EXISTING_ARCHIVE"
    assert rb["execution_reference"]["strategy_executed"] is False
    assert rb["acceptance_checks"]["status"] == "PASS"
    assert rb["uses_before"] == rb["uses_after"] == 1


def test_a3_08_duplicate_restart_concurrency_crash(run):
    assert run["g"]["A3-08"]["status"] == "PASS", run["g"]["A3-08"]["evidence"]


def test_a3_09_registry_archive_governance_integrity(run):
    assert run["g"]["A3-09"]["status"] == "PASS", run["g"]["A3-09"]["evidence"]
    assert run["a"]["missing_file"]["state"] == "FAILED"
    assert run["a"]["wrong_sha"]["result"]["status"] == "REJECTED"
    assert ACC.tree_fingerprint(ROOT) == run["before"]


def test_a3_10_runner_disabled_holdout_isolated(run):
    assert run["g"]["A3-10"]["status"] == "PASS"
    assert run["g"]["A3-10"]["evidence"]["proposal_agent_forbidden_imports"] == []


def test_phase5a_package_never_imports_runner_core_or_broker():
    banned = ("strategy_agent.research_only", "strategy_agent.adapters", "market_research_core", "ib_insync", "ibapi")
    for f in (ROOT / "src/strategy_agent/phase5a").glob("*.py"):
        for n in ast.walk(ast.parse(f.read_text())):
            mods = [a.name for a in n.names] if isinstance(n, ast.Import) else \
                [n.module or ""] if isinstance(n, ast.ImportFrom) else []
            assert not [m for m in mods if any(m == b or m.startswith(b + ".") for b in banned)], (f, mods)


def test_full_acceptance_in_a_fresh_process_and_never_overwritten(tmp_path):
    out = tmp_path / "integrations" / "20261107_120000_test000"
    cmd = [sys.executable, "-m", "strategy_agent.phase5a", "acceptance", "--out-dir", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT, timeout=600)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    res = json.loads(r.stdout)
    assert res["verdict"]["verdict"] == "SCOPED_GO"                        # no real replay / no cloud here
    assert set(res["gates"].values()) == {"PASS"} and res["route_b_real"] == "NOT_RUN"
    assert sorted(p.name for p in out.iterdir() if p.is_file()) == sorted(ACC.OUTPUT_FILES)
    acc = json.loads((out / "integration_acceptance.json").read_text())
    assert acc["fresh_process"] is True and acc["gates"]["A3-10"]["evidence"]["executor_modules_loaded"] == []
    assert "consumed_batches=0" in (out / "run_info.txt").read_text()
    again = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT, timeout=600)
    assert again.returncode != 0 and "FileExistsError" in again.stderr      # existing work dir / files untouched
    assert json.loads((out / "integration_acceptance.json").read_text()) == acc


def test_real_replay_requires_user_approval_and_archive(tmp_path):
    r = ACC.route_b(tmp_path / "a", fixture=False, approver=None, approval_ref=None, source_archive_root=tmp_path)
    assert r["status"] == "PENDING_USER_APPROVAL"
    r = ACC.route_b(tmp_path / "b", fixture=False, approver="someone", approval_ref="https://example.test/x",
                    source_archive_root=tmp_path / "nowhere")
    assert r["status"] == "PENDING_ARCHIVE"
    req = Q.load(ACC.REAL_REPLAY_REQUEST)
    assert req["replay_source"] == {"run_id": ACC.REAL_RUN, "archive_manifest_sha256": ACC.REAL_ARCHIVE_MANIFEST}
    bad = tmp_path / "real.json"
    bad.write_text(json.dumps({**req, "execution_mode": "RESEARCH_ONLY_REAL"}))
    r = ACC.route_b(tmp_path / "c", fixture=False, approver="x", approval_ref="https://e.test", request_path=bad,
                    source_archive_root=tmp_path)
    assert r["status"] == "REFUSED" and r["reason"] == "REPLAY_ONLY"


def test_proposal_to_request_rejects_tampered_proposal_and_writes_nothing(tmp_path):
    p = ACC.draft("complete_momentum", agent_commit_sha="a" * 40, created_at=AT)
    b = ACC.synthetic_artifacts(tmp_path / "art", p)
    p["draft"]["title"] = "tampered after hashing"
    r = I.proposal_to_request(p, bindings=b, output=tmp_path / "r.json", artifact_root=tmp_path / "art")
    assert r["status"] == "REJECTED" and "PROPOSAL_SHA_MISMATCH" in r["errors"] and not (tmp_path / "r.json").exists()


def test_cli_submit_proposal_is_production_only_and_blocked(tmp_path, monkeypatch, capsys):
    p = ACC.draft("complete_momentum", agent_commit_sha="a" * 40, created_at=AT,
                  completion_file=tmp_path / "completion.json")
    b = ACC.synthetic_artifacts(tmp_path / "art", p, execution_mode="RESEARCH_ONLY_REAL")
    (tmp_path / "p.json").write_text(json.dumps(p, ensure_ascii=False))
    (tmp_path / "b.json").write_text(json.dumps(b))
    rc = cli(["submit-proposal", "--proposal", str(tmp_path / "p.json"), "--bindings", str(tmp_path / "b.json"),
              "--export", str(tmp_path / "x/req.json"), "--artifact-root", str(tmp_path / "art"),
              "--state-dir", str(tmp_path / "s"), "--archive-root", str(tmp_path / "o"), "--actor", "cli-test"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and out["state"] == "BLOCKED" and out["export"]["status"] == "EXPORTED"
    rc = cli(["replay", "--task-id", out["task_id"], "--state-dir", str(tmp_path / "s"), "--archive-root",
              str(tmp_path / "o"), "--source-archive-root", str(tmp_path)])
    assert rc == 2 and json.loads(capsys.readouterr().out)["error"] == "REPLAY_ONLY"
    rc = cli(["status", "--task-id", out["task_id"], "--state-dir", str(tmp_path / "s"), "--archive-root", str(tmp_path / "o")])
    st = json.loads(capsys.readouterr().out)
    assert st["states"] == ["PROPOSED", "VALIDATED", "BLOCKED"] and st["links"][0]["submitted_by"] == "cli-test"
    with pytest.raises(SystemExit):
        cli(["approve", "--task-id", out["task_id"]])                     # no approve command in this CLI


def test_broken_synthetic_fixture_fails_instead_of_staying_running(tmp_path):
    fx = tmp_path / "fx" / ACC.FIX_RUN
    shutil.copytree(ACC.FIX / ACC.FIX_RUN, fx)
    (fx / "trades.csv").unlink()
    o = Orchestrator(tmp_path / "s", tmp_path / "o", test_mode=True, synthetic_fixture=fx,
                     governance_root=ACC.FIX / "governance_root", clock=ACC.StepClock())
    p = ACC.draft("complete_momentum", agent_commit_sha="a" * 40, created_at=AT)
    b = ACC.synthetic_artifacts(tmp_path / "art", p)
    s = I.submit_proposal(o, p, bindings=b, export_path=tmp_path / "e/r.json", artifact_root=tmp_path / "art",
                          actor="TEST_FIXTURE", actor_kind="TEST_FIXTURE")
    o.approve(s["task_id"], approver="TEST_FIXTURE", approval_ref="SYNTHETIC_FIXTURE")
    st = o.dispatch(s["task_id"])
    assert st["state"] == "FAILED" and st["history"][-1]["reason"] == "UNSAFE_ARCHIVE_PATH"
    assert "ARCHIVED" not in [h["to"] for h in st["history"]]
