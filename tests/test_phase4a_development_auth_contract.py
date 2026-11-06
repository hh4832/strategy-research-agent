"""Phase4A development authorization ID and one-click Colab signoff contract tests.

No governance records are modified; this does not approve any authorization.
"""
import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schemas/RESEARCH_AUTHORIZATION_SCHEMA.json"
NOTEBOOK = ROOT / "notebooks/Phase4A_Development_Research_Lead_Signoff.ipynb"


def test_dev_authorization_id_is_narrow_and_legacy_accepted():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    pattern = schema["$defs"]["approve"]["properties"]["authorization_id"]["pattern"]
    for valid in (
        "RA-IBKR_TSM-SMA520_SMOKE-0001",
        "RA-IBKR_TSM-SMA520_DEV-18350b3adba8e28a7e5e",
    ):
        assert re.fullmatch(pattern, valid)
    for invalid in (
        "RA-IBKR_TSM-SMA520_DEV-not-a-hex-digest",
        "RA-IBKR_TSM-SMA520_DEV-18350b3adba8e28a7e5e00",
        "RA-NQ-SMA520_DEV-18350b3adba8e28a7e5e",
    ):
        assert not re.fullmatch(pattern, invalid)
    Draft202012Validator.check_schema(schema)


def test_signoff_notebook_requires_personal_github_identity_and_never_runs_strategy():
    nb = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    assert nb["nbformat"] == 4
    cells = [c["source"] for c in nb["cells"] if c["cell_type"] == "code"]
    program = "\n".join("".join(cell) for cell in cells)
    for cell in cells:
        compile("".join(cell), str(NOTEBOOK), "exec")
    assert "userdata.get(\"GITHUB_TOKEN\")" in program
    assert "github_login.lower() == APPROVER.lower()" in program
    assert "research_authorization_admin import approve" in program
    assert "approve(" in program
    assert "ResearchOnlyRunner(root=WORK).preflight(" in program
    assert "ResearchOnlyRunner.run(" not in program
    assert ".consume(" not in program
    assert "git\", \"push" in program


def test_nonconsuming_probe_renews_only_development_receipt_without_strategy():
    notebook = ROOT / "notebooks/Phase4A_TSM_Development_Nonconsuming_Probe.ipynb"
    nb = json.loads(notebook.read_text(encoding="utf-8"))
    code = "\\n".join("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code")
    for i, cell in enumerate(nb["cells"]):
        if cell["cell_type"] == "code":
            compile("".join(cell["source"]), f"{notebook}:cell{i}", "exec")
    assert "renew_development_receipt(source_binding)" in code
    assert 'source_binding["integrity_receipt_sha256"] == cfg["snapshot_binding"]["integrity_receipt_sha256"]' in code
    assert "ephemeral = copy.deepcopy(cfg)" in code
    assert "canonical_sha(ephemeral)" in code
    assert "runner.probe(temp_config,snapshot_root=SNAPSHOT)" in code
    assert 'result["slot_consumed"] is False' in code
    assert "runner.run(" not in code
    assert ".consume(" not in code


def test_v3_colab_uses_signed_authorization_for_formal_gate_pin():
    notebook = ROOT / "notebooks/Phase4A_TSM_Development_Nonconsuming_Probe_v3.ipynb"
    nb = json.loads(notebook.read_text(encoding="utf-8"))
    code_cells = ["".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"]
    for i, code in enumerate(code_cells):
        compile(code, f"{notebook}:cell{i}", "exec")
    program = "\\n".join(code_cells)
    assert 'assert "formal_gate_pin" not in cfg' in program
    assert 'auth["formal_gate_pin"]["market_status"] == "NO_GO"' in program
    assert 'ephemeral["formal_gate_pin"]' not in program
    assert 'runner.probe(temp_config,snapshot_root=SNAPSHOT)' in program
    assert "runner.run(" not in program
    assert ".consume(" not in program
