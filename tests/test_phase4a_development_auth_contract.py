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
