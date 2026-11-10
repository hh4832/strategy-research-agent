"""Phase 5-B3-C PREPARATION: draft / run-config derivation / proposal check / preflight. Nothing is created,
approved, bound or consumed; every B3C gate stays NOT_EXECUTED and real_research_pass is always False."""
from __future__ import annotations

import copy
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from strategy_agent.phase5a import acceptance as A5
from strategy_agent.readiness import b3c
from strategy_agent.readiness.candidate import CANDIDATE, ROOT

HDR = b3c.HDR
SPEC_SHA = "f4f60b447013062324b7dc10aa0a569aebbd542796bef6ccfd2e723d0f5cff34"


@pytest.fixture(scope="module")
def draft():
    return b3c.build_draft()


def _filled(draft, **over):
    p = copy.deepcopy(draft["proposal_fields"])
    now = datetime.now(timezone.utc).replace(microsecond=0)
    p.update(authorization_id=b3c.CANDIDATE_AUTHORIZATION_ID, max_batches=1, issued_at=now.isoformat(),
             expires_at=(now + timedelta(days=7)).isoformat())
    p["executor"]["executor_id"] = b3c.PROPOSED_EXECUTOR
    p.update(over)
    return p


def test_B3C_draft_is_unapproved_and_every_human_decision_is_open(draft):
    assert draft["kind"] == "PHASE5B3C_AUTHORIZATION_DRAFT" and draft["status"] == "DRAFT_NOT_APPROVED"
    assert draft["authorization_id"] == {"proposed": "RA-IBKR_TSM-SMA520_P5B3C-0001", "decision": HDR,
                                         "rule": draft["authorization_id"]["rule"]}
    assert draft["executor"]["executor_id"] == HDR and draft["executor"]["consistency"] == "SINGLE_EXECUTOR_ONLY"
    assert draft["validity_period"]["issued_at"] == HDR and draft["validity_period"]["expires_at"] == HDR
    assert draft["max_research_batches"] == {"proposed": 1, "decision": HDR}
    p = draft["proposal_fields"]
    for k in ("authorization_id", "max_batches", "issued_at", "expires_at"):
        assert p[k] == HDR
    assert p["executor"] == {"executor_id": HDR, "consistency": "SINGLE_EXECUTOR_ONLY"}
    assert "approved_by" not in p and "approval_ref" not in p and "action" not in p


def test_B3C_draft_pins(draft):
    p = draft["proposal_fields"]
    assert p["market_id"] == "IBKR_TSM" and p["symbol"] == "TSM"
    assert p["strategy"]["spec_file_sha256"] == SPEC_SHA and p["strategy"]["spec_path"] == CANDIDATE["strategy_spec"]
    assert p["dataset"]["snapshot_id"] == "TSM_DEV_1M_20240529_20240628_18350b3adba8e28a7e5e"
    assert draft["dataset_and_period"]["dataset_manifest_sha256"].startswith("8b6fc838b72a")
    assert draft["core_commit"] == "b16179a7cc077c36046574769a32c367fa476cae"
    assert p["research_window"]["start"].startswith("2024-05-29") and p["research_window"]["end"].startswith("2024-06-29")
    assert p["costs"]["model"] == "IBKR_PRO_FIXED" and len(p["costs"]["scenarios"]) == 4
    assert draft["holdout_policy"]["lock_id"] == "HOLDOUT-TSM-15M-v1" and draft["holdout_policy"]["access"] == "NO_ACCESS"
    assert [s["step"] for s in draft["binding_procedure"]] == list(range(1, 12))
    assert draft["archive_location"]["immutable"] is True


def test_B3C_committed_draft_matches_fresh_build(draft):
    on_disk = json.loads((ROOT / "docs/phase5b3c/authorization_draft.json").read_text(encoding="utf-8"))
    assert on_disk == json.loads(b3c._dumps(draft))          # noqa: SLF001


def test_B3C_schema_dry_run_passes_in_memory_and_writes_nothing(draft):
    before = A5.tree_fingerprint(ROOT)
    assert b3c.schema_dry_run(draft) == {"status": "PASS", "placeholders_used_for": [], "written": False}
    assert A5.tree_fingerprint(ROOT) == before
    assert b3c.CANDIDATE_AUTHORIZATION_ID not in (ROOT / CANDIDATE["authorization_book"]).read_text()


def test_B3C_raw_draft_is_not_admissible_as_an_authorization(draft):
    from strategy_agent.governance.research_authorization import validate_events
    ev = copy.deepcopy(draft["proposal_fields"])
    ev.update(action="APPROVE", approved_by="hh4832", approval_ref="https://example.invalid/x",
              approved_at="2026-11-10T00:00:00+00:00")
    with pytest.raises(Exception):
        validate_events([ev], {"hh4832"})
    assert "HUMAN_DECISION_REQUIRED_LEFT" in b3c.proposal_problems(draft["proposal_fields"])


def test_B3C_check_proposal_accepts_a_fully_decided_proposal_only(draft):
    assert b3c.proposal_problems(_filled(draft)) == []
    cases = {
        "AUTHORIZATION_ID_ALREADY_USED": _filled(draft, authorization_id=b3c.SPENT_DEV),
        "AUTHORIZATION_ID_PATTERN": _filled(draft, authorization_id="RA-bad"),
        "MAX_BATCHES_NOT_1": _filled(draft, max_batches=2),
        "VALIDITY_PERIOD": _filled(draft, expires_at="2020-01-01T00:00:00+00:00"),
    }
    for code, p in cases.items():
        assert code in b3c.proposal_problems(p), code
    p = _filled(draft)
    p["executor"]["executor_id"] = HDR
    assert "EXECUTOR_ID" in b3c.proposal_problems(p)
    p = _filled(draft)
    p["research_window"] = dict(p["research_window"], end="2025-02-01T00:00:00-05:00")
    assert "PIN_CHANGED:research_window" in b3c.proposal_problems(p)
    p = _filled(draft)
    p["strategy"] = dict(p["strategy"], spec_file_sha256="0" * 64)
    assert "SPEC_PIN" in b3c.proposal_problems(p)


def test_B3C_derived_run_config_is_self_consistent_and_minimal():
    from strategy_agent.research_only.runner import ResearchOnlyRunner
    cfg = b3c.derive_run_config(b3c.CANDIDATE_AUTHORIZATION_ID)
    dev = json.loads((ROOT / CANDIDATE["run_config"]).read_text(encoding="utf-8"))
    changed = {k for k in set(cfg) | set(dev) if cfg.get(k) != dev.get(k)}
    assert changed == {"authorization_id", "run_config_id", "strategy", "purpose", "provenance"}
    assert {k for k in cfg["strategy"] if cfg["strategy"][k] != dev["strategy"][k]} == \
        {"spec_path", "spec_file_sha256", "version"}
    assert cfg["strategy"]["spec_file_sha256"] == SPEC_SHA
    with tempfile.TemporaryDirectory() as t:
        p = Path(t) / "c.json"
        p.write_text(json.dumps(cfg))
        _, blockers = ResearchOnlyRunner().load_config(p)
    assert blockers == []
    for bad in (b3c.SPENT_DEV, "RA-bad", ""):
        with pytest.raises(ValueError):
            b3c.derive_run_config(bad)


def test_B3C_derive_cli_refuses_without_a_formal_authorization_and_writes_nothing(tmp_path, capsys):
    out = tmp_path / "cfg.json"
    assert b3c.main(["derive-run-config", "--authorization-id", b3c.CANDIDATE_AUTHORIZATION_ID, "--out", str(out)]) == 2
    assert not out.exists() and "AUTHORIZATION_NOT_IN_BOOK" in capsys.readouterr().out
    assert not (ROOT / b3c.B3C_RUN_CONFIG_PATH).exists()


def test_B3C_preflight_is_non_consuming_and_never_a_real_pass():
    before = A5.tree_fingerprint(ROOT)
    r = b3c.preflight()
    assert A5.tree_fingerprint(ROOT) == before
    assert set(r["gates"]) == {f"B3C-{i:02d}" for i in range(1, 11)}
    assert all(g["status"] == "NOT_EXECUTED" for g in r["gates"].values())
    assert r["real_research_pass"] is False and r["real_execution"] == "NOT_PERFORMED"
    assert r["research_authorization"] == "NOT_GRANTED"
    assert r["preparation"] == "READY_FOR_HUMAN_DECISION", r["preparation_problems"]
    for gid in ("B3C-07", "B3C-08", "B3C-09", "B3C-10"):
        assert r["gates"][gid]["prepared_status"] == "NOT_APPLICABLE_BEFORE_EXECUTION"
    assert r["gates"]["B3C-05"]["prepared_checks"]["spent_dev_authorization_uses"] == 1
    assert r["gates"]["B3C-06"]["prepared_checks"] == {"REAL_EXECUTION_ENABLED": False, "real_runner": "DISABLED"}
    assert not (ROOT / "registry/research_budget_events.jsonl").exists()


def test_B3C_preflight_out_is_exclusive(tmp_path):
    out = tmp_path / "p.json"
    out.write_text("x")
    with pytest.raises(FileExistsError):
        b3c.main(["preflight", "--out", str(out)])
    assert out.read_text() == "x"
