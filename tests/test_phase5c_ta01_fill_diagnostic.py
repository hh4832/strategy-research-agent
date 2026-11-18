"""TA-01 fill diagnostic: unsigned authorization document, approval binding and output guard (no Core, no data)."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from strategy_agent.approval.testkit import FixtureApprovals
from strategy_agent.approval.verifier import ApprovalRejected
from strategy_agent.execution import c1_integration as C
from strategy_agent.readiness.c1_authorization import PINS
from strategy_agent.research_only import ta01_fill_diagnostic as D

ROOT = Path(__file__).resolve().parents[1]
DOC = D.load_authorization(ROOT)


def test_authorization_document_pins_the_agreed_scope():
    s = DOC["scope"]
    assert DOC["authorization_id"].startswith("RA-") and DOC["status"] == "UNSIGNED_UNTIL_LEAD_APPROVAL_COMMENT"
    assert s["kind"] == "READ_ONLY_FILL_FEASIBILITY_DIAGNOSTIC" and s["holdout"]["access"] == "NONE"
    assert s["dataset"]["reviewed_pins"] == PINS and s["window"] == dict(C.WINDOW) and s["core_sha"] == C.CORE
    assert s["costs"]["dated_schedule_sha256"] == C.SCHEDULE_SHA256
    assert s["strategy"]["spec_file_sha256"] == C.sha(ROOT / s["strategy"]["spec_path"])
    assert s["strategy"]["quantity"] == 100 and s["strategy"]["tuning"] == "NONE"
    assert s["max_runs"] == 1 and s["batch_consumption"] == 0
    assert any("Holdout" in x for x in DOC["does_not_authorize"])


def test_approval_binds_this_exact_document():
    fx = FixtureApprovals()
    expires = (datetime.now(timezone.utc) + timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = fx.comment("SYNTHETIC_FIXTURE\n" + D.approval_line(DOC, expires))
    ev = D.verify_approval(DOC, url, verifier=fx.verifier)
    assert ev["approval_source"]["trust"] == "ISOLATED_TEST"           # main() refuses anything but PRODUCTION
    changed = json.loads(json.dumps(DOC))
    changed["scope"]["strategy"]["quantity"] = 200
    with pytest.raises(ApprovalRejected):
        D.verify_approval(changed, url, verifier=fx.verifier)
    other = fx.comment(D.approval_line(DOC, expires), login="someone", uid=1)
    with pytest.raises(ApprovalRejected):
        D.verify_approval(DOC, other, verifier=fx.verifier)


def test_output_guard_refuses_performance_fields():
    with pytest.raises(D.DiagnosticRefused):
        D.assert_no_performance_fields({"entry": {"net_pnl": 1.0}})
    with pytest.raises(D.DiagnosticRefused):
        D.assert_no_performance_fields({"entry": {}, "sharpe": 1})
    with pytest.raises(D.DiagnosticRefused):
        D.assert_no_performance_fields({"entry": {"fill_price_median": 1}})
    D.assert_no_performance_fields({"entry": {"intents": 3}, "not_output": ["PnL", "returns"]})


def test_prepare_mode_prints_approval_line_without_data(tmp_path, capsys):
    settings = tmp_path / "s.json"
    settings.write_text(json.dumps({"mode": "PREPARE", "approval_expires_utc": "2026-11-25T00:00:00Z"}))
    assert D.main(["--settings", str(settings)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["data_read"] is False and out["approval_line"].startswith("RESEARCH-APPROVAL v1 decision=APPROVE")
    assert DOC["authorization_id"] in out["approval_line"] and out["document_sha256"] in out["approval_line"]


def test_run_requires_typed_confirmation_before_anything(tmp_path):
    settings = tmp_path / "s.json"
    settings.write_text(json.dumps({"mode": "RUN", "confirm_authorization": "", "drive_root": str(tmp_path)}))
    with pytest.raises(D.DiagnosticRefused, match="confirm"):
        D.main(["--settings", str(settings)])
    assert list(tmp_path.iterdir()) == [settings]


def test_notebook_prepare_never_mounts_drive_or_reads_a_credential():
    nb = json.loads((ROOT / "notebooks/Phase5C_TA01_Fill_Diagnostic.ipynb").read_text())
    code = "".join(nb["cells"][2]["source"])
    lines = code.splitlines()
    gate = lines.index('if MODE == "RUN":  # PREPARE never mounts Drive and never reads a credential')
    for needle in ("from google.colab import drive", "drive.mount(", "userdata.get("):
        hits = [i for i, l in enumerate(lines) if needle in l]
        assert hits, needle
        assert all(i > gate and lines[i].startswith("    ") for i in hits), needle   # only inside the RUN branch
    assert "".join(nb["cells"][1]["source"]).startswith('MODE = "PREPARE"')                  # PREPARE is the default
