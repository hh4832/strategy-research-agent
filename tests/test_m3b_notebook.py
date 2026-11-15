"""M3-B.1 static acceptance of the Colab Run All notebook (no Colab / Drive needed).

What a Drive run must do is fixed here; the real Drive run itself is a separate, recorded step."""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
NB = ROOT / "notebooks" / "M3B_Research_Runner.ipynb"
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text()


@pytest.fixture(scope="module")
def cells():
    nb = json.loads(NB.read_text(encoding="utf-8"))
    return ["".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"]


def _cfg(cells):
    ns = {}
    exec(cells[0].split("import hashlib")[0], ns)          # the constant assignments only
    return ns


def test_cells_parse_and_have_no_outputs(cells):
    nb = json.loads(NB.read_text(encoding="utf-8"))
    for c in nb["cells"]:
        if c["cell_type"] == "code":
            ast.parse("".join(c["source"]))
            assert c["outputs"] == [] and c["execution_count"] is None      # no stale outputs / leaked data


def test_starts_from_an_exact_commit_and_pinned_core(cells):
    cfg = _cfg(cells)
    cfgfile = ROOT / cfg["CONFIG"]
    assert cfgfile.is_file() and json.loads(cfgfile.read_text())["output"]["target"] == "DRIVE"
    assert "pip install -q -e '.[test,research]'" in "\n".join(cells)        # declared dependencies only
    assert re.fullmatch(r"[0-9a-f]{40}", cfg["AGENT_COMMIT"])
    pin = re.search(r"MRC_SHA: ([0-9a-f]{40})", CI).group(1)
    assert cfg["CORE_SHA"] == pin
    code = "\n".join(cells)
    assert "git checkout -q {AGENT_COMMIT}" in code and "git rev-parse HEAD" in code
    assert "head == AGENT_COMMIT" in code and "git status --porcelain" in code
    assert "market-research-core@{CORE_SHA}" in code


def test_credentials_only_from_colab_secrets(cells):
    code = "\n".join(cells)
    assert "userdata.get('GITHUB_TOKEN')" in code
    for pat in (r"ghp_[A-Za-z0-9]{10,}", r"github_pat_", r"(?i)password\s*=", r"(?i)api[_-]?key\s*=",
                r"https://[^/\s]*:[^@\s]*@github\.com"):
        assert not re.search(pat, NB.read_text(encoding="utf-8")), pat
    assert 'out.replace(t, "***")' in code and "MASK = [t for t in (GITHUB_TOKEN,) if t]" in code   # never echoed
    assert "Bearer" not in code and 'GIT_TERMINAL_PROMPT="0"' in code             # no prompt, valid auth scheme
    # public clone first; the secret is only a fallback
    assert code.index('r = sh(f"git clone -q {AGENT_REPO} {WORKDIR}", check=False)') < code.index("x-access-token")
    assert "extraheader" in code.lower() and "not in open(f\"{WORKDIR}/.git/config\")" in code   # never persisted


def test_drive_is_required_and_archive_is_verified(cells):
    code = "\n".join(cells)
    assert "drive.mount('/content/drive')" in code and "assert os.path.isdir('/content/drive/MyDrive')" in code
    assert code.count("python -m strategy_agent.research run --config {CONFIG}") == 2 and "--summary-out" in code
    for name in ("run_info.txt", "experiment_record.json", "stages.json", "metrics.json", "research_report.md",
                 "trades.csv", "fills.csv", "rejections.csv", "equity_curve.csv", "snapshot_refs.json",
                 "final_status.json", "manifest.json"):
        assert f'"{name}"' in code, name
    # M3-C: one execution + a resume pass (no second full run); N1 numeric check vs committed reference
    assert "--resume" in code and "strategy_agent.research repro --summary" in code
    for check in ("manifest_every_file_sha256", "resume_skips_every_run", "run1_not_overwritten",
                  "numeric_reproducibility_vs_reference", "archive_integrity_self_sha", "core_m3a4_checks_pass",
                  "formal_registry_holdout_untouched", "git_tree_clean", "cost_scenarios_one_family_trial"):
        assert check in code
    assert "sys.version_info >= (3, 11)" in code
    assert "assert all(checks.values())" in code
    assert "Quant_Research/strategy-research-agent" in code


def test_gate_and_no_strategy_logic(cells):
    code = "\n".join(cells)
    assert "readiness --market IBKR_TSM" in code and "returncode == 3" in code
    for forbidden in ("sma_crossover", "ExecutionEngine", "ExperimentRunner", "evaluate(", "def run(",
                      "enforce_parameter_ranges", "registry/experiments.jsonl\").write", "--cost-sensitivity",
                      "calculate_cost", "cost_multiplier", "--archive-root"):
        assert forbidden not in code, forbidden
    # every shell command is install / git / pytest / the research CLI / fingerprint helpers
    cmds = re.findall(r'sh\(f?"([^"]+)"', code)
    allowed = ("rm -rf", "git ", "cd {WORKDIR} && git", "cd {WORKDIR} && pip install", "cd {WORKDIR} && python -m pytest",
               "cd {WORKDIR} && python -m strategy_agent.research", "cd {WORKDIR} && python -c")
    assert cmds and all(c.startswith(allowed) for c in cmds), cmds
    assert "/content/m3c_env" in code             # isolated run root, not the repository


def _verify_historical_pin(config, evidence, git):
    # Evidence is anchored to immutable history, not the evolving working tree.
    assert config["AGENT_COMMIT"] == evidence["agent_commit"]
    assert config["CORE_SHA"] == evidence["core_sha"]
    commit = evidence["agent_commit"]
    assert git("merge-base", "--is-ancestor", commit, "HEAD").returncode == 0
    actual = git("ls-tree", "-r", commit, "--", *evidence["paths"])
    assert actual.returncode == 0
    assert actual.stdout.splitlines() == evidence["entries"], "historical executable/input tree changed"
    # Check object availability/integrity as well as the inventory of Git blob identities.
    assert git("fsck", "--no-reflogs", "--no-dangling", commit).returncode == 0


def test_n2_pin_is_an_already_committed_integration_commit_with_identical_code(cells):
    """The historical launcher and pinned executable/input blobs stay fixed; HEAD may evolve."""
    import hashlib
    import subprocess
    evidence = json.loads((ROOT / "tests/fixtures/notebook_pins/m3b_historical.json").read_text())
    git = lambda *a: subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True)
    commit = evidence["agent_commit"]
    if git("rev-parse", "--git-dir").returncode != 0 or git("cat-file", "-e", f"{commit}^{{commit}}").returncode != 0:
        pytest.skip("git history with the pinned commit not available (shallow clone)")
    assert hashlib.sha256(NB.read_bytes()).hexdigest() == evidence["notebook_sha256"]
    _verify_historical_pin(_cfg(cells), evidence, git)


def test_n2_rejects_changed_launcher_sha_and_historical_evidence(cells):
    import copy
    import subprocess
    evidence = json.loads((ROOT / "tests/fixtures/notebook_pins/m3b_historical.json").read_text())
    git = lambda *a: subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True)
    if git("cat-file", "-e", evidence["agent_commit"]).returncode:
        pytest.skip("historical objects unavailable in shallow clone")
    cfg = _cfg(cells)
    with pytest.raises(AssertionError):
        _verify_historical_pin({**cfg, "AGENT_COMMIT": "0" * 40}, evidence, git)
    bad = copy.deepcopy(evidence)
    bad["entries"][0] = bad["entries"][0].replace("100644", "100755")
    with pytest.raises(AssertionError, match="historical executable/input tree changed"):
        _verify_historical_pin(cfg, bad, git)


def test_n2_current_repair_cannot_change_historical_objects(cells, tmp_path):
    import subprocess
    evidence = json.loads((ROOT / "tests/fixtures/notebook_pins/m3b_historical.json").read_text())
    git = lambda *a: subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True)
    if git("cat-file", "-e", evidence["agent_commit"]).returncode:
        pytest.skip("historical objects unavailable in shallow clone")
    # Stand-in future source is deliberately different. Verification reads only the pinned Git tree.
    (tmp_path / "m3a4_contract.py").write_text("a future independent repair")
    _verify_historical_pin(_cfg(cells), evidence, git)
