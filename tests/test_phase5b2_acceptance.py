"""Phase 5-B2 acceptance runner: real junit results -> gates; failure propagation; immutable archive + ZIP check."""
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = {k: v for k, v in os.environ.items() if k not in ("GH_TOKEN", "GITHUB_TOKEN")}


def _junit(tmp_path, failed=False):
    p = tmp_path / "j.xml"
    case = '<testcase classname="t" name="test_B2_01_x"><failure>boom</failure></testcase>' if failed else \
        '<testcase classname="t" name="test_B2_01_x"/>'
    names = "".join(f'<testcase classname="t" name="test_B2_0{i}_x"/>' for i in range(2, 10))
    p.write_text(f'<testsuite>{case}{names}</testsuite>')
    return p


def _run(tmp_path, *args):
    return subprocess.run([sys.executable, "-m", "strategy_agent.execution.acceptance", *args], capture_output=True,
                          text=True, cwd=ROOT, timeout=900, env=ENV)


def test_B2_10_acceptance_archive_is_complete_immutable_and_zip_consistent(tmp_path):
    out = tmp_path / "executions/20261108_120000_test000"
    r = _run(tmp_path, "--out-dir", str(out), "--junit", str(_junit(tmp_path)), "--tests-rc", "0")
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    res = json.loads(r.stdout[r.stdout.index("{"):])
    assert res["verdict"]["verdict"] == "SCOPED_GO" and res["gates"]["B2-10"] == "BLOCKED"     # no Drive here
    names = {p.name for p in out.iterdir() if p.is_file()}
    for n in ("execution_acceptance.json", "budget_reservations.json", "execution_receipts.json", "task_lifecycle.json",
              "governance_checks.json", "archive_integrity.json", "run_info.txt", "ARCHIVE_INDEX.json"):
        assert n in names
    z = tmp_path / "a.zip"
    with zipfile.ZipFile(z, "w") as f:
        for p in sorted(out.iterdir()):
            if p.is_file():
                f.write(p, p.name)
    assert _run(tmp_path, "--out-dir", str(out), "--verify-archive", "--zip", str(z)).returncode == 0
    with zipfile.ZipFile(tmp_path / "bad.zip", "w") as f:
        f.writestr("run_info.txt", "tampered")
    assert _run(tmp_path, "--out-dir", str(out), "--verify-archive", "--zip", str(tmp_path / "bad.zip")).returncode == 1
    again = _run(tmp_path, "--out-dir", str(out), "--junit", str(_junit(tmp_path)), "--tests-rc", "0")
    assert again.returncode != 0 and "FileExistsError" in again.stderr
    ident = json.loads((out / "execution_receipts.json").read_text())["execution_identity"]
    for k in ("proposal_sha256", "bound_request_sha256", "approval_evidence_sha256", "authorization_id", "git_commit",
              "core_commit", "strategy_spec_sha256", "dataset_snapshot_id", "dataset_manifest_sha256", "signal_time_rule",
              "execution_time_rule", "cost_scenarios", "holdout_policy", "research_window", "batch_reservation_id",
              "output_manifest_sha256"):
        assert ident.get(k), k


def test_B2_10_failed_tests_propagate_to_a_failing_acceptance(tmp_path):
    r = _run(tmp_path, "--out-dir", str(tmp_path / "o"), "--junit", str(_junit(tmp_path, failed=True)), "--tests-rc", "1")
    assert r.returncode == 1
    res = json.loads(r.stdout[r.stdout.index("{"):])
    assert res["gates"]["B2-01"] == "FAIL" and res["gates"]["B2-10"] == "FAIL" and res["verdict"]["verdict"] == "NO_GO"
