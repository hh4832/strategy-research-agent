"""Phase 4-A real-smoke path on Core b16179a with a SYNTHETIC development snapshot produced by Core's own producer.

TEST_ONLY: the snapshot, the governance books and the approvals are temporary fixtures; the repository's governance,
registry and canonical config are never touched (asserted). Proves: receipt renewal -> probe -> single consumption
-> Core load/execute -> archive contract files, temporal checks, and that the slot cannot be reused.
"""
import copy
import hashlib
import json
import math
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")
pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")
D = pytest.importorskip("market_research_core.tsm_development")
tn = pytest.importorskip("market_research_core.tsm_native")

from strategy_agent.governance.holdout_admin import approve as approve_lock  # noqa: E402
from strategy_agent.governance.research_authorization_admin import approve as approve_auth  # noqa: E402
from strategy_agent.research_only import development_run as DR  # noqa: E402
from strategy_agent.research_only.runner import ResearchOnlyRunner, canonical_sha  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEV_CFG = ROOT / "configs/research_only/tsm_sma_5_20_smoke_2024_06_dev_18350b3adba8e28a7e5e.json"
DEV_PROPOSAL = ROOT / "governance/proposals/RA-IBKR_TSM-SMA520_DEV-18350b3adba8e28a7e5e.json"
CORE = json.loads(DEV_CFG.read_text())["core"]["phase4a_sha"]
URL = "https://example.test/research-lead-approval"
NOW = datetime(2026, 11, 6, 22, 40, tzinfo=timezone(timedelta(hours=8)))
NY = "America/New_York"
HOLIDAYS = {date(2024, 5, 27), date(2024, 6, 19)}
REQUIRED = {"run_info.txt", "config.json", "dataset_manifest.json", "execution_diagnostics.json", "trades.csv",
            "fills.csv", "rejected_orders.csv", "cost_breakdown.csv", "metrics.json", "equity_curve.csv",
            "research_report.md", "MANIFEST.json"}
pytestmark = pytest.mark.skipif(tn.core_code_identity()["core_git_sha"] != CORE,
                                reason=f"installed Core is not the pinned development reader {CORE[:7]}")


def protected():
    dirs = ["governance", "registry", "configs"]
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for d in dirs for p in (ROOT / d).rglob("*")
            if p.is_file()}


def _days(a, b):
    d = a
    while d <= b:
        if d.weekday() < 5 and d not in HOLIDAYS:
            yield d
        d += timedelta(days=1)


def make_dev_snapshot(tmp: Path) -> dict:
    parent = tmp / "src" / D.PINNED_SNAPSHOT.run_name
    parent.mkdir(parents=True)
    rows = []
    for k, d in enumerate(_days(date(2024, 5, 22), date(2024, 6, 28))):
        t0 = pd.Timestamp(datetime(d.year, d.month, d.day, 9, 30), tz=NY)
        for i in range(390):
            x = k * 0.37 + i / 390
            mid = 170 + 6 * math.sin(x * 2.3) + 0.4 * math.sin(x * 41)
            rows.append({"timestamp_utc": (t0 + pd.Timedelta(minutes=i)).tz_convert("UTC"), "open": round(mid, 2),
                         "high": round(mid + .05, 2), "low": round(mid - .05, 2), "close": round(mid + .01, 2),
                         "volume": float(1000 + i)})
    src = parent / "tsm_source_test_only.parquet"
    pq.write_table(pa.Table.from_pylist(rows), src, row_group_size=390 * 5)
    mp = parent / "final_gate_manifest.json"
    mp.write_text(json.dumps({"results": [{"symbol": "TSM", "published_sha256": D.file_sha(src)}]}))
    plan = D.plan_conversion([{"path": str(src), "expected_sha256": D.file_sha(src), "lineage_ref": "SYNTHETIC_ONLY"}],
                             parent_manifest_path=mp, expected_parent_manifest_sha256=D.file_sha(mp))
    assert plan["requires_research_lead_approval"] is False
    return D.produce_development_snapshot(plan, tmp / "derived", producer_sha=CORE)


def setup(tmp: Path):
    bundle = make_dev_snapshot(tmp)
    sid = bundle["snapshot_pin"]["snapshot_id"]
    g = tmp / "gov"
    g.mkdir()
    shutil.copy(ROOT / "governance/approvers.json", g / "approvers.json")
    shutil.copy(ROOT / "governance/market_readiness.json", g / "market_readiness.json")
    for f in ("holdout_locks.jsonl", "research_authorizations.jsonl", "uses.jsonl"):
        (g / f).touch()
    approve_lock(g / "holdout_locks.jsonl", g / "approvers.json", lock_id="HOLDOUT-TSM-15M-v1",
                 dataset_id="ibkr_tsm_1m_normalized", snapshot_id="20261101_205431_06359faf7ef4_IBKR_FINAL_GATE_FULL",
                 start="2025-01-01T00:00:00-05:00", end="2026-10-17T00:00:00-04:00", approved_by="hh4832",
                 approval_ref=URL)
    p = json.loads(DEV_PROPOSAL.read_text())
    p["dataset"].update(snapshot_id=sid, producer_commit_sha=CORE)        # TEST FIXTURE dataset pin
    approve_auth(g / "research_authorizations.jsonl", g / "approvers.json", p, approved_by="hh4832",
                 approval_ref=URL, now=NOW)
    cfg = json.loads(DEV_CFG.read_text())
    cfg["dataset"].update(snapshot_id=sid, producer_commit_sha=CORE)
    sb = cfg["snapshot_binding"]
    for k in ("manifest_sha256", "integrity_receipt", "integrity_receipt_sha256", "snapshot_pin"):
        sb[k] = copy.deepcopy(bundle[k])
    cfg["provenance"]["config_sha256"] = canonical_sha(cfg)
    cpath = tmp / "canonical.json"
    cpath.write_text(json.dumps(cfg, indent=2))
    runner = ResearchOnlyRunner(locks_path=g / "holdout_locks.jsonl", authorizations_path=g / "research_authorizations.jsonl",
                                uses_path=g / "uses.jsonl", approvers_path=g / "approvers.json",
                                readiness_path=g / "market_readiness.json", clock=lambda: NOW)
    return runner, g, cpath, Path(bundle["snapshot_root"])


def smoke(runner, cpath, root, tmp, sess="s1"):
    return DR.execute_development_smoke(cpath, snapshot_root=root, archive_root=tmp / "archive",
                                        session_dir=tmp / sess, git_commit="abcdef1234567", actor="hh4832",
                                        context={"agent_git_sha": "abcdef1234567", "agent_checkout_clean": True,
                                                 "notebook": "TEST"}, runner=runner)


def test_repository_dev_config_matches_signed_authorization():
    cfg = json.loads(DEV_CFG.read_text())
    assert cfg["provenance"]["config_sha256"] == canonical_sha(cfg)
    auth = [json.loads(x) for x in (ROOT / "governance/research_authorizations.jsonl").read_text().splitlines()
            if cfg["authorization_id"] in x]
    assert len(auth) == 1 and auth[0]["approved_by"] == "hh4832" and auth[0]["max_batches"] == 1
    assert auth[0]["dataset"] == cfg["dataset"] and auth[0]["strategy"]["parameters"] == {"fast": 5, "slow": 20}
    assert set(auth[0]["costs"]["scenarios"]) == {"BASE", "LOW", "STRESS", "CAT_STRESS"}
    assert cfg["core"]["phase4a_sha"] == cfg["dataset"]["producer_commit_sha"] == CORE


def test_full_smoke_once_with_archive_contract_then_spent(tmp_path):
    before = protected()
    runner, g, cpath, root = setup(tmp_path)
    out = smoke(runner, cpath, root, tmp_path)
    assert out["status"] == "COMPLETED", out.get("error") or out.get("blockers")
    assert out["probe_before_consumption"]["status"] == "COMPATIBLE_READY_TO_CONSUME"
    assert len((g / "uses.jsonl").read_text().splitlines()) == 1
    arch = Path(out["archive_dir"])
    assert arch.name.startswith("20261106_224000_abcdef1")              # Asia/Taipei run id
    assert REQUIRED <= {p.name for p in arch.iterdir()}
    m = json.loads((arch / "MANIFEST.json").read_text())
    for rel, sha in m["files_sha256"].items():
        assert hashlib.sha256((arch / rel).read_bytes()).hexdigest() == sha
    info = (arch / "run_info.txt").read_text()
    for key in ("agent_git_sha=abcdef1234567", f"core_git_sha={CORE}", "authorization_id=RA-IBKR_TSM-SMA520_DEV-",
                "manifest_sha256=", "session_config_sha256=", "canonical_config_sha256=", "cost_profile_sha256=",
                "execution_started_at_asia_taipei=2026-11-06T22:40:00+08:00", "holdout_accessed=false"):
        assert key in info, key
    diag = json.loads((arch / "execution_diagnostics.json").read_text())
    assert diag["data"]["max_native_timestamp_before_holdout"] is True
    assert diag["data"]["native_first_utc"] >= "2024-05-29"                 # warm-up never earlier than 05-29
    for name, t in diag["temporal"].items():
        assert t["status"] == "PASS" and t["fills_checked"] > 0, name
        assert t["min_minutes_signal_available_to_fill"] > 0
    metrics = json.loads((arch / "metrics.json").read_text())
    assert set(metrics) == {"BASE", "LOW", "STRESS", "CAT_STRESS"}
    assert {m["sample_status"] for m in metrics.values()} <= {"INSUFFICIENT_SAMPLE", "SAMPLE_OK"}
    costs = {r["scenario"]: r for r in __import__("csv").DictReader((arch / "cost_breakdown.csv").open())}
    assert float(costs["LOW"]["price_adjustment_cost"]) < float(costs["BASE"]["price_adjustment_cost"]) \
        < float(costs["STRESS"]["price_adjustment_cost"])
    assert float(costs["CAT_STRESS"]["cat"]) >= float(costs["BASE"]["cat"])
    assert "NOT evidence that SMA 5/20 works" in (arch / "research_report.md").read_text()
    # spent: the archive guard stops a second Colab session before any probe or consumption ...
    with pytest.raises(DR.SmokeStopped, match="PRIOR_ATTEMPT"):
        smoke(runner, cpath, root, tmp_path, sess="s2")
    # ... and the ledger itself refuses (RA11) even without the archive guard
    again = runner.run(cpath, snapshot_root=root, archive_root=tmp_path / "other", git_commit="abcdef1", actor="x")
    assert again["executed"] is False and "RA11" in {b["code"] for b in again["blockers"]}
    assert len((g / "uses.jsonl").read_text().splitlines()) == 1
    assert protected() == before


def test_session_config_changes_only_the_receipt_and_refuses_foreign_binding(tmp_path):
    runner, g, cpath, root = setup(tmp_path)
    sc = DR.session_config(cpath, root, tmp_path / "s")
    assert DR._strip(sc["config"]) == DR._strip(sc["canonical_config"])
    assert sc["config"]["snapshot_binding"]["integrity_receipt"]["verification_ref"].startswith("DEVELOPMENT_RECEIPT_RENEWAL:")
    bad = json.loads(cpath.read_text())
    bad["snapshot_binding"]["manifest_sha256"] = "0" * 64
    bad["provenance"]["config_sha256"] = canonical_sha(bad)
    (tmp_path / "bad.json").write_text(json.dumps(bad))
    with pytest.raises(DR.SmokeStopped, match="BINDING_MISMATCH"):
        DR.session_config(tmp_path / "bad.json", root, tmp_path / "s2")
    assert (g / "uses.jsonl").read_text() == ""


def test_probe_failure_stops_before_consumption(tmp_path):
    runner, g, cpath, root = setup(tmp_path)
    cfg = json.loads(cpath.read_text())
    cfg["costs"]["scenario_set_sha256"] = "0" * 64                      # authorization no longer matches (RA06)
    cfg["provenance"]["config_sha256"] = canonical_sha(cfg)
    cpath.write_text(json.dumps(cfg))
    out = smoke(runner, cpath, root, tmp_path)
    assert out["status"] == "STOPPED_BY_PROBE" and out["slot_consumed"] is False
    assert (g / "uses.jsonl").read_text() == "" and not (tmp_path / "archive").exists()
