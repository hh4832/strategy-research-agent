"""Layer B SYNTHETIC world: the EXISTING ResearchOnlyRunner + Core on a SYNTHETIC development snapshot.

Requires Core ``b16179a`` (development reader + native loader, reached ONLY through adapters/mrc_phase4a), pandas and
pyarrow; callers
skip when they are absent. Everything lives under a temporary directory OUTSIDE the repository:
  snapshot  produced by Core's own development producer from synthetic TSM-like minute bars (no market data)
  art/      artefact root of the runner: configs/ + the SMA 5/20 spec whose data_requirements are re-pinned to the
            synthetic snapshot (TEST FIXTURE; the spec sha differs from the repository's)
  gov/      ISOLATED governance books (marker file), the DEV authorization proposal re-pinned to the synthetic
            snapshot + spec (TEST FIXTURE), holdout lock, empty uses log
  exp/      an A2 proposal -> PENDING request -> verified binding (FixtureApprovals, ISOLATED_TEST) -> bound request
The repository's canonical config, books and the real 1/1-spent authorization are never read for writing.

Security remediation (P0-3): the request is NOT derived from the runner config any more (``request_for`` was
self-referential). ``bound_request`` is produced by A2 from the snapshot bundle and the authorization, bound through
the binding service, and the controlled adapter then compares it with the runner's OWN execution identity (config +
snapshot manifest file + installed Core).
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
    # artefact root: configs + the spec re-pinned to the SYNTHETIC snapshot (spec sha changes; TEST FIXTURE)
    art = tmp / "art"
    shutil.copytree(ROOT / "configs", art / "configs")
    spec = json.loads((ROOT / cfg["strategy"]["spec_path"]).read_text())
    for ds in spec["data_requirements"]["datasets"]:
        ds.update(snapshot_id=sid, producer_commit_sha=core, manifest_sha256=bundle["manifest_sha256"])
    spec["provenance"]["notes"] = "SYNTHETIC_FIXTURE: data_requirements re-pinned to a synthetic development snapshot"
    sp = art / cfg["strategy"]["spec_path"]
    sp.parent.mkdir(parents=True)
    sp.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    spec_sha = hashlib.sha256(sp.read_bytes()).hexdigest()
    p = json.loads(DEV_PROPOSAL.read_text())
    p["dataset"].update(snapshot_id=sid, producer_commit_sha=core)          # SYNTHETIC_FIXTURE dataset pin
    p["strategy"]["spec_file_sha256"] = spec_sha                            # SYNTHETIC_FIXTURE spec pin
    p["max_batches"] = max_batches
    approve_auth(G / "research_authorizations.jsonl", G / "approvers.json", p, approved_by="hh4832", approval_ref=URL,
                 now=NOW)
    cfg["dataset"].update(snapshot_id=sid, producer_commit_sha=core)
    cfg["strategy"]["spec_file_sha256"] = spec_sha
    sb = cfg["snapshot_binding"]
    for k in ("manifest_sha256", "integrity_receipt", "integrity_receipt_sha256", "snapshot_pin"):
        sb[k] = copy.deepcopy(bundle[k])
    cfg["provenance"]["config_sha256"] = canonical_sha(cfg)
    cpath = tmp / "canonical_SYNTHETIC.json"
    cpath.write_text(json.dumps(cfg, indent=2))
    runner = ResearchOnlyRunner(root=art, locks_path=G / "holdout_locks.jsonl", authorizations_path=G / "research_authorizations.jsonl",
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
    w = {"runner": runner, "run_context": run_context, "gov": gov, "art": art, "config_path": Path(sc["path"]),
         "snapshot_root": Path(bundle["snapshot_root"]), "authorization_id": cfg["authorization_id"],
         "config": sc["config"], "core_sha": core, "now": NOW,
         "snapshot": {"snapshot_id": sid, "manifest_sha256": bundle["manifest_sha256"]}}
    w.update(_bind(w, tmp))
    return w


def _bind(w: dict, tmp: Path) -> dict:
    """A2 proposal (labelled SYNTHETIC_FIXTURE completion) -> PENDING request -> verified binding -> bound request.
    Pins come from the snapshot BUNDLE, the authorization record and the artefact files - not from the runner config."""
    from strategy_agent.approval.testkit import FixtureApprovals  # noqa: PLC0415
    from strategy_agent.binding import testkit as K  # noqa: PLC0415
    from strategy_agent.binding.service import AuthorizationBindingService  # noqa: PLC0415
    from strategy_agent.phase5a import acceptance as ACC  # noqa: PLC0415
    from strategy_agent.phase5a.integration import proposal_to_request  # noqa: PLC0415
    from strategy_agent.proposal import FileDraftAdapter, draft_proposal  # noqa: PLC0415
    art, gov, exp = w["art"], w["gov"], tmp / "exp"
    exp.mkdir()
    auth = next(json.loads(x) for x in (gov / "governance/research_authorizations.jsonl").read_text().splitlines()
                if x.strip() and json.loads(x).get("authorization_id") == w["authorization_id"])
    st, ds, cs = auth["strategy"], auth["dataset"], auth["costs"]
    spec = json.loads((art / st["spec_path"]).read_text())
    fx = json.loads((ROOT / "fixtures/proposal/complete_momentum.json").read_text(encoding="utf-8"))
    d = json.loads(json.dumps(fx["llm_draft"], ensure_ascii=False).replace("2330", "TSM"))
    uni = {k: spec["universe"][k] for k in ("market", "symbols", "timeframe")}
    d["strategy_rules"]["universe"].update(uni)
    d["required_data"].update(uni, coverage={"start": "2024-05-01", "end": "2024-07-31"})
    for k in list(d["strategy_spec_fragment"]):
        d["strategy_spec_fragment"][k] = spec[k]
    comp = exp / "completion_SYNTHETIC_FIXTURE_TSM_LAYER_B.json"
    comp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    P = draft_proposal(fx["user_hypothesis"].replace("2330", "TSM"),
                       FileDraftAdapter(comp, adapter_id="SYNTHETIC_FIXTURE_COMPLETION_FILE_NOT_A_MODEL"),
                       agent_commit_sha="a" * 40, created_at="2026-11-07T12:00:00+08:00")
    B = ACC.synthetic_artifacts(art, P, execution_mode="RESEARCH_ONLY_REAL")
    win = auth["research_window"]
    B.update(strategy_family={"family_id": "sma-crossover-tsm", "strategy_id": st["strategy_id"], "version": st["version"],
                              "parameters": st["parameters"]},
             strategy_spec_ref=st["spec_path"], strategy_spec_sha256=hashlib.sha256((art / st["spec_path"]).read_bytes()).hexdigest(),
             market={"market_id": auth["market_id"], "symbol": auth["symbol"]},
             dataset_ref={"dataset_id": ds["dataset_id"], "producer_commit_sha": ds["producer_commit_sha"]},
             dataset_snapshot_id=w["snapshot"]["snapshot_id"], dataset_manifest_sha256=w["snapshot"]["manifest_sha256"],
             core_commit=w["core_sha"],
             cost_profile_ref={"scenario_set_path": cs["scenario_set_path"], "scenario_set_sha256": cs["scenario_set_sha256"],
                               "scenarios": ["BASE", "LOW", "STRESS", "CAT_STRESS"]},
             research_window={"warmup_start": win["start"], "start": "2024-06-03T00:00:00-04:00", "end": win["end"]},
             holdout_policy={"access": "NO_ACCESS", "lock_id": auth["holdout"]["lock_id"]})
    (exp / "proposal.json").write_text(json.dumps(P, ensure_ascii=False, indent=2), encoding="utf-8")
    conv = proposal_to_request(P, bindings=B, output=exp / "request.json", artifact_root=art)
    if conv["status"] != "EXPORTED":
        raise RuntimeError(f"Layer B A2 export failed: {conv}")
    req = json.loads((exp / "request.json").read_text(encoding="utf-8"))
    fa = FixtureApprovals()
    svc = AuthorizationBindingService(verifier=fa.verifier, governance_root=gov, root=art, benchmark=K.synthetic_benchmark)
    r = svc.bind(dry_run=False, source_request_path=exp / "request.json", provenance_path=exp / "request_provenance.json",
                 proposal_path=exp / "proposal.json", authorization_id=w["authorization_id"],
                 evidence_url=fa.bind_url(req, w["authorization_id"]))
    bound = json.loads(Path(r["bound_request_path"]).read_text(encoding="utf-8"))
    return {"fa": fa, "binding": r["record"], "bound": bound, "exp": exp, "request": req}


def bound_request(world: dict) -> dict:
    """The registered bound request of the Layer B world (a copy: callers may mutate it)."""
    return copy.deepcopy(world["bound"])


def orchestrator(world: dict, work: Path, backend, *, worker: str = "worker-1", clock=None):
    """A production-mode Orchestrator on the ISOLATED Layer B books with the controlled adapter over ``backend``."""
    from strategy_agent.binding import testkit as K  # noqa: PLC0415
    from strategy_agent.orchestration.orchestrator import Orchestrator  # noqa: PLC0415
    from strategy_agent.phase5a import acceptance as A5  # noqa: PLC0415

    from .controlled import ControlledRunnerAdapter  # noqa: PLC0415
    ad = ControlledRunnerAdapter(backend, governance_root=world["gov"])
    return Orchestrator(Path(work) / "state", Path(work) / "out", root=world["art"], governance_root=world["gov"],
                        benchmark=K.synthetic_benchmark, approval_verifier=world["fa"].verifier, controlled_adapter=ad,
                        clock=clock or A5.StepClock(), worker=worker)
