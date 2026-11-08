"""Layer B SYNTHETIC world: the EXISTING ResearchOnlyRunner + Core on a SYNTHETIC development snapshot.

Requires Core ``b16179a`` (development reader + native loader, reached ONLY through adapters/mrc_phase4a), pandas and
pyarrow; callers
skip when they are absent. Everything lives under a temporary directory OUTSIDE the repository:
  snapshot  produced by Core's own development producer from synthetic TSM-like minute bars (no market data)
  gov/      ISOLATED governance books (marker file), the DEV authorization proposal re-pinned to the synthetic
            snapshot (TEST FIXTURE), holdout lock, empty uses log
The repository's canonical config, books and the real 1/1-spent authorization are never read for writing.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DEV_CFG = ROOT / "configs/research_only/tsm_sma_5_20_smoke_2024_06_dev_18350b3adba8e28a7e5e.json"
DEV_PROPOSAL = ROOT / "governance/proposals/RA-IBKR_TSM-SMA520_DEV-18350b3adba8e28a7e5e.json"
MARKER = ROOT / "tests/fixtures/phase4c/governance_root/ISOLATED_TEST_GOVERNANCE_ROOT.json"
URL = "https://example.test/SYNTHETIC_FIXTURE-research-lead-approval"
NOW = datetime(2026, 11, 6, 22, 40, tzinfo=timezone(timedelta(hours=8)))
NY = "America/New_York"
HOLIDAYS = {date(2024, 5, 27), date(2024, 6, 19)}


def core_available() -> tuple[bool, str]:
    try:
        import pandas  # noqa: F401, PLC0415
        import pyarrow  # noqa: F401, PLC0415
        from strategy_agent.adapters.mrc_phase4a import core_development  # noqa: PLC0415 - the only Core import site
        _dev, tsm_native = core_development()
    except ImportError as e:
        return False, f"{type(e).__name__}: {e}"
    want = json.loads(DEV_CFG.read_text())["core"]["phase4a_sha"]
    got = tsm_native.core_code_identity()["core_git_sha"]
    return (got == want), f"installed Core {got[:7]} (pinned {want[:7]})"


def _days(a, b):
    d = a
    while d <= b:
        if d.weekday() < 5 and d not in HOLIDAYS:
            yield d
        d += timedelta(days=1)


def make_dev_snapshot(tmp: Path, core_sha: str) -> dict:
    import pandas as pd  # noqa: PLC0415
    import pyarrow as pa  # noqa: PLC0415
    import pyarrow.parquet as pq  # noqa: PLC0415
    from strategy_agent.adapters.mrc_phase4a import core_development  # noqa: PLC0415 - the only Core import site
    D = core_development()[0]
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
    src = parent / "tsm_source_SYNTHETIC_ONLY.parquet"
    pq.write_table(pa.Table.from_pylist(rows), src, row_group_size=390 * 5)
    mp = parent / "final_gate_manifest.json"
    mp.write_text(json.dumps({"results": [{"symbol": "TSM", "published_sha256": D.file_sha(src)}]}))
    plan = D.plan_conversion([{"path": str(src), "expected_sha256": D.file_sha(src), "lineage_ref": "SYNTHETIC_ONLY"}],
                             parent_manifest_path=mp, expected_parent_manifest_sha256=D.file_sha(mp))
    return D.produce_development_snapshot(plan, tmp / "derived", producer_sha=core_sha)


def build(tmp: Path, *, max_batches: int = 1) -> dict:
    from strategy_agent.governance.holdout_admin import approve as approve_lock  # noqa: PLC0415
    from strategy_agent.governance.research_authorization_admin import approve as approve_auth  # noqa: PLC0415
    from strategy_agent.research_only import development_run as DR  # noqa: PLC0415
    from strategy_agent.research_only.runner import ResearchOnlyRunner, canonical_sha  # noqa: PLC0415
    tmp = Path(tmp).resolve()
    if ROOT in tmp.parents:
        raise ValueError("the Layer B world must live outside the repository")
    cfg = json.loads(DEV_CFG.read_text())
    core = cfg["core"]["phase4a_sha"]
    bundle = make_dev_snapshot(tmp, core)
    sid = bundle["snapshot_pin"]["snapshot_id"]
    gov = tmp / "gov"
    (gov / "governance").mkdir(parents=True)
    (gov / "registry").mkdir()
    shutil.copy(MARKER, gov / MARKER.name)
    G = gov / "governance"
    shutil.copy(ROOT / "governance/approvers.json", G / "approvers.json")
    shutil.copy(ROOT / "governance/market_readiness.json", G / "market_readiness.json")
    for f in ("holdout_locks.jsonl", "research_authorizations.jsonl"):
        (G / f).touch()
    (gov / "registry/research_authorization_uses.jsonl").touch()
    approve_lock(G / "holdout_locks.jsonl", G / "approvers.json", lock_id="HOLDOUT-TSM-15M-v1",
                 dataset_id="ibkr_tsm_1m_normalized", snapshot_id="20261101_205431_06359faf7ef4_IBKR_FINAL_GATE_FULL",
                 start="2025-01-01T00:00:00-05:00", end="2026-10-17T00:00:00-04:00", approved_by="hh4832", approval_ref=URL)
    p = json.loads(DEV_PROPOSAL.read_text())
    p["dataset"].update(snapshot_id=sid, producer_commit_sha=core)          # SYNTHETIC_FIXTURE dataset pin
    p["max_batches"] = max_batches
    approve_auth(G / "research_authorizations.jsonl", G / "approvers.json", p, approved_by="hh4832", approval_ref=URL,
                 now=NOW)
    cfg["dataset"].update(snapshot_id=sid, producer_commit_sha=core)
    sb = cfg["snapshot_binding"]
    for k in ("manifest_sha256", "integrity_receipt", "integrity_receipt_sha256", "snapshot_pin"):
        sb[k] = copy.deepcopy(bundle[k])
    cfg["provenance"]["config_sha256"] = canonical_sha(cfg)
    cpath = tmp / "canonical_SYNTHETIC.json"
    cpath.write_text(json.dumps(cfg, indent=2))
    runner = ResearchOnlyRunner(locks_path=G / "holdout_locks.jsonl", authorizations_path=G / "research_authorizations.jsonl",
                                uses_path=gov / "registry/research_authorization_uses.jsonl",
                                approvers_path=G / "approvers.json", readiness_path=G / "market_readiness.json",
                                clock=lambda: NOW)
    sc = DR.session_config(cpath, Path(bundle["snapshot_root"]), tmp / "session")
    recs = [json.loads(x) for x in (G / "research_authorizations.jsonl").read_text().splitlines() if x.strip()]
    appr = [r for r in recs if r.get("authorization_id") == cfg["authorization_id"] and r.get("action") == "APPROVE"]
    run_context = {"agent_checkout_clean": True, "notebook": "PHASE5B2_LAYER_B_SYNTHETIC",
                   "canonical_config_path": str(cpath), "canonical_config_sha256": sc["canonical_config_sha256"],
                   "canonical_config": sc["canonical_config"], "session_config_difference": sc["difference"],
                   "authorization_record_sha256": appr[-1]["record_sha256"] if appr else None}
    return {"runner": runner, "run_context": run_context, "gov": gov, "config_path": Path(sc["path"]), "snapshot_root": Path(bundle["snapshot_root"]),
            "authorization_id": cfg["authorization_id"], "config": sc["config"], "core_sha": core, "now": NOW}


def request_for(world: dict) -> dict:
    """The governance-relevant identity of the run as a request-shaped dict (for the controlled precheck)."""
    c = world["config"]
    return {"authorization_ref": {"authorization_id": c["authorization_id"]},
            "dataset_snapshot_id": c["dataset"]["snapshot_id"],
            "dataset_manifest_sha256": c["snapshot_binding"]["manifest_sha256"],
            "strategy_spec_sha256": hashlib.sha256((ROOT / c["strategy"]["spec_path"]).read_bytes()).hexdigest(),
            "core_commit": world["core_sha"],
            "cost_profile_ref": {"scenario_set_sha256": c["costs"]["scenario_set_sha256"],
                                 "scenarios": list(c["costs"]["scenarios"]) + list(c["costs"].get("sensitivity", []))},
            "signal_time_rule": {"signal_time": "CORE_MODELED_AVAILABLE_AT"},
            "execution_time_rule": {"rule": "NEXT_ELIGIBLE_BAR_OPEN"},
            "holdout_policy": {"access": "NO_ACCESS", "lock_id": "HOLDOUT-TSM-15M-v1"}}
