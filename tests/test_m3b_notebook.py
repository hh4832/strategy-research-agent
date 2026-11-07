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


def test_n2_pin_is_an_already_committed_integration_commit_with_identical_code(cells):
    """N2: the notebook pins a commit made BEFORE the notebook edit (no self-referential SHA), and everything Colab
    executes from that commit is byte-identical to this tree."""
    import subprocess
    commit = _cfg(cells)["AGENT_COMMIT"]
    git = lambda *a: subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True)  # noqa: E731
    if git("rev-parse", "--git-dir").returncode != 0 or git("cat-file", "-e", f"{commit}^{{commit}}").returncode != 0:
        pytest.skip("git history with the pinned commit not available (shallow clone)")
    assert git("merge-base", "--is-ancestor", commit, "HEAD").returncode == 0
    # pipeline code and pinned inputs: byte-identical
    paths = ["src", "configs/research", "configs/cost_scenarios", "fixtures", "schemas", "specs", "benchmark",
             "pyproject.toml",
             "docs/evidence/m3c_reference_metrics.json"]
    # Every file present at the pin must be unchanged (no M/D/R/T). Files ADDED after the pin (e.g. the Phase 4-A
    # research-only modules) are allowed: the pinned Colab clone does not contain them, so they cannot change what
    # it executes - provided no pinned file references them.
    status = [ln.split("\t") for ln in git("diff", "--name-status", commit, "--", *paths).stdout.splitlines() if ln]
    assert not [s for s in status if s[0] != "A"], status
    for _, added in status:
        if added.endswith(".py") and added.startswith("src/"):
            mod = added[len("src/"):-len(".py")].replace("/", ".").removesuffix(".__init__")
            assert git("grep", "-q", mod, commit, "--", "src", "notebooks").returncode != 0, f"pinned code references {mod}"
    # governance records may gain reviewed facts (e.g. the 2026-11-05 RESEARCH_ONLY temporal acceptance), but only
    # in market_readiness.json and without changing any market or gate status the pinned smoke ran against
    # Allowed governance drift since the pin: market_readiness.json (statuses checked below), newly ADDED files
    # (e.g. Phase 4-A research-only authorization book and proposals), and append-only growth of existing *.jsonl
    # logs (e.g. a research-lead holdout lock). The pinned smoke only fingerprints governance before/after its own
    # run and must still find the market gate closed, which the status check below guarantees.
    gstatus = [ln.split("\t") for ln in git("diff", "--name-status", commit, "--", "governance").stdout.splitlines() if ln]
    for st, path in ((s[0], s[-1]) for s in gstatus):
        if st == "A" or path == "governance/market_readiness.json":
            continue
        assert st == "M" and path.endswith(".jsonl"), (st, path)
        old_bytes = subprocess.run(["git", "show", f"{commit}:{path}"], cwd=ROOT, capture_output=True).stdout
        assert (ROOT / path).read_bytes().startswith(old_bytes), f"{path} was not append-only since the pin"
    old = json.loads(git("show", f"{commit}:governance/market_readiness.json").stdout)
    new = json.loads((ROOT / "governance" / "market_readiness.json").read_text(encoding="utf-8"))
    for m, e in old["markets"].items():
        assert new["markets"][m]["status"] == e["status"]
        assert {g: (v["status"], v.get("approved_by")) for g, v in new["markets"][m]["gates"].items()} == \
            {g: (v["status"], v.get("approved_by")) for g, v in e["gates"].items()}
