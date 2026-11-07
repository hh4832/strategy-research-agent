"""MVP v1 release acceptance (read-only). SYNTHETIC_FIXTURE for the comparison logic; the real comparison runs only
in notebooks/MVP_V1_Release_Acceptance.ipynb against the real archive and the recorded Phase 4-C evaluation."""
import ast
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from strategy_agent.evaluation import phase4c as C
from strategy_agent.evaluation import release as R

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests/fixtures/phase4c"
ARCH = FIX / "20261106_224000_0123456"
GOV = FIX / "governance_root"
AT = "2026-11-07T09:00:00+08:00"


@pytest.fixture(autouse=True)
def _process_level_executor_check_is_tested_elsewhere(monkeypatch):
    # C8's "no executor module loaded in this PROCESS" is order-dependent inside a shared pytest process (other tests
    # import the runner). It is asserted in a fresh subprocess by test_phase4c_acceptance; here only compare() is tested.
    monkeypatch.setattr(C, "EXECUTOR_MODULES", ())


def _acc():
    pin = hashlib.sha256((ARCH / "MANIFEST.json").read_bytes()).hexdigest()
    return C.accept(ARCH, expected_manifest_sha256=pin, evaluator_commit_sha="a" * 40, evaluated_at=AT, governance_root=GOV)


def _reference(tmp, mutate=None):
    """Simulate the Phase 4-C (883d14c) output: same results, old cost-sensitivity reason text."""
    ref = tmp / "ref"
    C.write_acceptance(_acc(), ref, evaluator_commit_sha="b" * 40, evaluated_at=AT)
    ev = json.loads((ref / "evaluation.json").read_text())
    for r in ev["robustness_matrix"]:
        if r["group"] == "cost_sensitivity":
            r["reason"] = "required evidence absent"
    (ref / "evaluation.json").write_text(json.dumps(ev))
    if mutate:
        mutate(ref)
    return ref


def test_comparison_passes_when_only_presentation_reason_differs(tmp_path):
    comp = R.compare(_acc(), _reference(tmp_path))
    assert comp["status"] == "PASS", comp
    assert comp["presentation_reason_changes"] == {"cost_sensitivity": {
        "reference": "required evidence absent", "release": "cost scenarios supplied on the same signals and positions"}}
    assert all(s["metrics_identical"] and s["input_sha256_identical"] for s in comp["scenarios"].values())


def test_comparison_fails_on_any_metric_or_verdict_difference(tmp_path):
    def bump(ref):
        a = json.loads((ref / "acceptance_checks.json").read_text())
        a["per_scenario"]["STRESS"]["metrics"]["win_rate"]["value"] += 1e-6
        (ref / "acceptance_checks.json").write_text(json.dumps(a))
    comp = R.compare(_acc(), _reference(tmp_path, bump))
    assert comp["status"] == "FAIL" and comp["scenarios"]["STRESS"]["metric_differences"] == ["win_rate"]

    def verdict(ref):
        a = json.loads((ref / "acceptance_checks.json").read_text())
        a["per_scenario"]["BASE"]["strategy_verdict"] = "KEEP"
        (ref / "acceptance_checks.json").write_text(json.dumps(a))
    assert R.compare(_acc(), _reference(tmp_path / "v", verdict))["status"] == "FAIL"


def test_comparison_fails_if_reason_change_is_not_exactly_the_presentation_fix(tmp_path):
    def other(ref):
        ev = json.loads((ref / "evaluation.json").read_text())
        for r in ev["robustness_matrix"]:
            if r["group"] == "selection_bias":
                r["reason"] = "something else"
        (ref / "evaluation.json").write_text(json.dumps(ev))
    assert R.compare(_acc(), _reference(tmp_path, other))["status"] == "FAIL"


def test_reference_integrity_requires_byte_identical_copy(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    C.write_acceptance(_acc(), a, evaluator_commit_sha="b" * 40, evaluated_at=AT)
    shutil.copytree(a, b)
    assert R.reference_integrity(a, b)["status"] == "PASS"
    (b / "run_info.txt").write_text("x")
    assert R.reference_integrity(a, b)["status"] == "FAIL"


def test_committed_phase4c_reference_is_present_and_untouched():
    ref = R.REFERENCE_EVAL_DIR
    assert sorted(p.name for p in ref.iterdir()) == sorted(C.OUTPUTS)
    acc = json.loads((ref / "acceptance_checks.json").read_text())
    assert acc["status"] == "PASS" and set(acc["per_scenario"]) == set(C.SCENARIOS)
    assert acc["identity"]["archive_manifest_sha256"] == R.ARCHIVE_MANIFEST_SHA
    m = ROOT / "docs/evidence/phase4a_dev_smoke" / R.SOURCE_RUN_ID / "MANIFEST.json"
    assert hashlib.sha256(m.read_bytes()).hexdigest() == R.ARCHIVE_MANIFEST_SHA
    assert R.ARCHIVE_MANIFEST_SHA != R.DATASET_MANIFEST_SHA


@pytest.mark.skipif(subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--is-shallow-repository"], capture_output=True,
                                   text=True).stdout.strip() != "false", reason="needs full git history")
def test_integration_manifest_has_every_source_commit():
    m = R.integration_manifest(ROOT)
    missing = [k for k, v in m["sources"].items() if not v["ancestor_of_rc"]]
    assert not missing, missing
    assert all(m["components_present"].values())


def test_run_refuses_wrong_or_dirty_checkout(tmp_path):
    with pytest.raises(RuntimeError):
        R.run(ARCH, tmp_path, tmp_path / "out", expected_rc_sha="0" * 40, root=ROOT)
    assert not (tmp_path / "out").exists()


def test_release_module_never_imports_an_executor():
    tree = ast.parse((ROOT / "src/strategy_agent/evaluation/release.py").read_text())
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module} | \
           {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not [m for m in mods if m and any(m.startswith(x) for x in ("strategy_agent.research_only", "strategy_agent.adapters",
                                                                    "strategy_agent.research", "research_only", "adapters"))]
    src = (ROOT / "src/strategy_agent/evaluation/release.py").read_text()
    assert "ResearchOnlyRunner" not in src and ".run(cfg" not in src
