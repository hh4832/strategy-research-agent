"""Phase 5-C2 PR-A: dataset exposure log + evidence index (read-only checks against the repository)."""
import json
import shutil
from pathlib import Path

import pytest

from strategy_agent.governance import research_ledger as L
from strategy_agent.registry.chain import ChainedLog

ROOT = Path(__file__).resolve().parents[1]
DS = "ibkr_tsm_1m_normalized"
DEV = {"start": "2016-10-19T00:00:00-04:00", "end": "2025-01-01T00:00:00-05:00"}
HOLDOUT = {"start": "2025-01-01T00:00:00-05:00", "end": "2026-10-17T00:00:00-04:00"}


def jsonl(rel):
    return [json.loads(x) for x in (ROOT / rel).read_text().splitlines() if x.strip()]


def test_repository_ledgers_verify():
    assert L.verify(ROOT) == []


def test_every_authorization_use_is_indexed_and_exposed():
    index = {r["authorization_id"]: r for r in L.read(L.INDEX_LOG, ROOT)}
    exposure = {r["authorization_id"] for r in L.read(L.EXPOSURE_LOG, ROOT)}
    for use in jsonl("registry/research_authorization_uses.jsonl"):
        rec = index[use["authorization_id"]]
        assert rec["result"]["use_record_sha256"] == use["record_sha256"]
        assert use["authorization_id"] in exposure


def test_sma_c1_output_manifest_matches_budget_events():
    events = [e for e in jsonl("registry/research_budget_events.jsonl")
              if e.get("output_manifest_sha256") and e.get("authorization_id") == "RA-IBKR_TSM-SMA520_P5C1-0002"]
    rec = next(r for r in L.read(L.INDEX_LOG, ROOT) if r["authorization_id"] == "RA-IBKR_TSM-SMA520_P5C1-0002")
    assert {e["output_manifest_sha256"] for e in events} == {rec["result"]["sha256"]}


def test_ta01_index_matches_case_disposition():
    disp = json.loads((ROOT / "docs/phase5c/ta01_case_disposition.json").read_text())
    index = {r["authorization_id"]: r for r in L.read(L.INDEX_LOG, ROOT) if r["case_id"] == "P5C-TA-01"}
    exposed = {r["authorization_id"] for r in L.read(L.EXPOSURE_LOG, ROOT) if r["case_id"] == "P5C-TA-01"}
    for d in disp["diagnostics"]:
        assert index[d["authorization_id"]]["result"]["sha256"] == d["result_sha256"]
        assert (d["authorization_id"] in exposed) == (d["status"] == "SUCCEEDED")   # 0001 read no data
    stages = [r["stage"] for r in L.read(L.INDEX_LOG, ROOT) if r["case_id"] == "P5C-TA-01"]
    assert stages[0] == "PROPOSAL" and stages[-1] == "DISPOSITION"


def test_exposure_status_and_evidence_ceiling():
    assert L.exposure_status(DS, DEV, ROOT) == "EXPOSED"
    assert L.max_evidence_level(DS, DEV, ROOT) == "EXPLORATORY_ONLY"
    assert L.exposure_status(DS, HOLDOUT, ROOT) == "UNEXPOSED"
    assert L.exposure_status("other_dataset", DEV, ROOT) == "UNEXPOSED"
    later = {"start": "2024-12-01T00:00:00-05:00", "end": "2025-01-01T00:00:00-05:00"}
    assert L.exposure_status(DS, later, ROOT) == "EXPOSED"                      # overlap, not containment


def test_declared_exposure_checks():
    assert L.check_declared_exposure(DS, DEV, "EXPOSED", "EXPLORATORY_ONLY", ROOT) == []
    assert any("declared exposure UNEXPOSED" in p for p in L.check_declared_exposure(DS, DEV, "UNEXPOSED", "EXPLORATORY_ONLY", ROOT))
    assert any("CONFIRMATORY" in p for p in L.check_declared_exposure(DS, DEV, "EXPOSED", "CONFIRMATORY", ROOT))


def test_no_holdout_exposure_in_repository():
    assert L.holdout_problems(ROOT) == []


def _copy(tmp_path):
    for rel in (L.EXPOSURE_LOG, L.INDEX_LOG, L.HOLDOUT_LOCKS):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, tmp_path / rel)
    for r in L.read(L.INDEX_LOG, ROOT):
        for ref in r["repo_refs"]:
            (tmp_path / ref["path"]).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / ref["path"], tmp_path / ref["path"])
    return tmp_path


def test_holdout_read_is_reported(tmp_path):
    root = _copy(tmp_path)
    ChainedLog(root / L.EXPOSURE_LOG).append({
        "exposure_id": "EXP-TEST-HOLDOUT", "dataset_id": DS, "partition": "DEVELOPMENT", "window": HOLDOUT,
        "case_id": "TEST", "authorization_id": None, "run_ref": "x", "use": "DIAGNOSTIC", "used_at": None,
        "evidence_index_id": None, "recorded_by": "test", "backfilled": False})
    assert any("overlaps locked Holdout" in p for p in L.verify(root))


def test_changed_repo_file_is_reported(tmp_path):
    root = _copy(tmp_path)
    p = root / "docs/phase5c/ta01_case_disposition.json"
    p.write_text(p.read_text() + " ")
    assert any("ta01_case_disposition.json sha256 changed" in x for x in L.verify(root))


def test_invalid_lines_are_reported(tmp_path):
    root = _copy(tmp_path)
    ChainedLog(root / L.INDEX_LOG).append({"entry_id": "EV-BAD", "stage": "GUESS"})
    probs = L.verify(root)
    assert any("EV-BAD: missing case_id" in p for p in probs)
