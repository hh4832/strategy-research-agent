"""SYNTHETIC_FIXTURE binding world for ISOLATED tests and the 5-B1 acceptance (never the repository's books).

Builds, under a temporary directory outside the repository:
  art/   artefact root: a copy of configs/ + a labelled synthetic spec (A2 references and G2/G4 resolve here)
  gov/   governance root: approvers, the fixture market_readiness (IBKR_TSM, NO_GO), a holdout lock for the synthetic
         dataset and ONE test authorization (RA-SYNTHETIC_FIXTURE-B1-0001, max_batches 1) matching the A2 request
         (the authorization schema only admits IBKR_TSM / TSM / SMA 5/20)
  exp/   the A2 proposal, PENDING request and provenance actually produced by A2 (FileDraftAdapter over the labelled
         complete_momentum completion with the symbol set to TSM, so input_kind is USER_HYPOTHESIS)
Core benchmark status is injected (``SYNTHETIC_BENCHMARK``): nothing here proves a Core benchmark.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from strategy_agent.registry.chain import ChainedLog

ROOT = Path(__file__).resolve().parents[3]
FIX_GOV = ROOT / "tests/fixtures/phase4c/governance_root"
AUTH_ID = "RA-SYNTHETIC_FIXTURE-B1-0001"
LOCK_ID = "HOLDOUT-TSM-15M-v1"           # the authorization schema pins this lock id; here it locks the synthetic dataset
CORE = "b16179a7cc077c36046574769a32c367fa476cae"
COSTS = "configs/research_only/ibkr_pro_fixed_tsm_research_only_costs_v1.json"
AT = "2026-11-07T12:00:00+08:00"
CHAIN = ("seq", "prev_sha256", "record_sha256")


def synthetic_benchmark(sha):
    return {"status": "BENCHMARK_PASS", "label": "SYNTHETIC_BENCHMARK (injected; not a Core benchmark)", "sha": sha}


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def build_world(work: Path, *, agent_commit_sha: str = "a" * 40, created_at: str = AT, max_batches: int = 1) -> dict:
    from strategy_agent.phase5a import acceptance as ACC  # noqa: PLC0415
    from strategy_agent.phase5a.integration import proposal_to_request  # noqa: PLC0415
    work = Path(work).resolve()
    if ROOT in work.parents or work == ROOT:
        raise ValueError("binding test world must live outside the repository")
    art, gov, exp = work / "art", work / "gov", work / "exp"
    shutil.copytree(ROOT / "configs", art / "configs")
    exp.mkdir(parents=True)
    # A2: proposal (non-synthetic origin, labelled completion) -> bindings -> actual export
    from strategy_agent.proposal import FileDraftAdapter, draft_proposal  # noqa: PLC0415
    fx = json.loads((ROOT / "fixtures/proposal/complete_momentum.json").read_text(encoding="utf-8"))
    comp = exp / "completion_SYNTHETIC_FIXTURE_TSM.json"
    comp.write_text(json.dumps(fx["llm_draft"], ensure_ascii=False).replace("2330", "TSM"), encoding="utf-8")
    P = draft_proposal(fx["user_hypothesis"].replace("2330", "TSM"),
                       FileDraftAdapter(comp, adapter_id="SYNTHETIC_FIXTURE_COMPLETION_FILE_NOT_A_MODEL"),
                       agent_commit_sha=agent_commit_sha, created_at=created_at)
    B = ACC.synthetic_artifacts(art, P, execution_mode="RESEARCH_ONLY_REAL")
    B["market"] = {"market_id": "IBKR_TSM", "symbol": "TSM"}
    cost = art / COSTS
    B["cost_profile_ref"] = {"scenario_set_path": COSTS, "scenario_set_sha256": _sha(cost.read_bytes()),
                             "scenarios": ["BASE", "LOW", "STRESS", "CAT_STRESS"]}
    B["core_commit"] = CORE
    B["holdout_policy"] = {"access": "NO_ACCESS", "lock_id": LOCK_ID}
    (exp / "proposal.json").write_text(json.dumps(P, ensure_ascii=False, indent=2), encoding="utf-8")
    conv = proposal_to_request(P, bindings=B, output=exp / "request.json", artifact_root=art)
    if conv["status"] != "EXPORTED":
        raise RuntimeError(f"A2 export failed in the test world: {conv}")
    req = json.loads((exp / "request.json").read_text(encoding="utf-8"))
    # governance root
    (gov / "governance").mkdir(parents=True)
    shutil.copy(FIX_GOV / "ISOLATED_TEST_GOVERNANCE_ROOT.json", gov / "ISOLATED_TEST_GOVERNANCE_ROOT.json")
    (gov / "registry").mkdir()
    shutil.copy(ROOT / "governance/approvers.json", gov / "governance/approvers.json")
    shutil.copy(FIX_GOV / "governance/market_readiness.json", gov / "governance/market_readiness.json")
    lock = json.loads((FIX_GOV / "governance/holdout_locks.jsonl").read_text().splitlines()[0])
    lock = {k: v for k, v in lock.items() if k not in CHAIN}
    lock.update(lock_id=LOCK_ID, dataset_id="synthetic_fixture_dataset", snapshot_id="SYNTHETIC_FIXTURE_snapshot",
                holdout_start="2025-01-01T00:00:00+08:00", holdout_end="2026-10-17T00:00:00+08:00",
                approval_ref="https://example.test/SYNTHETIC_FIXTURE-holdout-lock", notes="SYNTHETIC_FIXTURE")
    ChainedLog(gov / "governance/holdout_locks.jsonl").append(lock)
    auth = json.loads((FIX_GOV / "governance/research_authorizations.jsonl").read_text().splitlines()[0])
    auth = {k: v for k, v in auth.items() if k not in CHAIN}
    spec_p = art / req["strategy_spec_ref"]
    auth.update(authorization_id=AUTH_ID, max_batches=max_batches,
                approval_ref="https://example.test/SYNTHETIC_FIXTURE-authorization",
                issued_at="2026-11-01T00:00:00+08:00", expires_at="2099-12-31T23:59:59+08:00",
                dataset={"dataset_id": "synthetic_fixture_dataset", "producer_commit_sha": "1" * 40,
                         "snapshot_id": "SYNTHETIC_FIXTURE_snapshot"},
                strategy={"parameters": {"fast": 5, "slow": 20}, "spec_file_sha256": _sha(spec_p.read_bytes()),
                          "spec_path": req["strategy_spec_ref"], "strategy_id": req["strategy_family"]["strategy_id"],
                          "version": req["strategy_family"]["version"]},
                costs=dict(auth["costs"], scenario_set_path=COSTS, scenario_set_sha256=B["cost_profile_ref"]["scenario_set_sha256"]),
                research_window={"start": "2024-05-29T00:00:00+08:00", "end": "2024-06-29T00:00:00+08:00", "interval": "[start,end)"},
                development_boundary={"start": "2016-01-01T00:00:00+08:00", "end": "2025-01-01T00:00:00+08:00",
                                      "interval": "[start,end)"},
                holdout=dict(auth["holdout"], lock_id=LOCK_ID, start="2025-01-01T00:00:00+08:00",
                             end="2026-10-17T00:00:00+08:00"),
                notes="SYNTHETIC_FIXTURE test authorization for Phase 5-B1 binding tests; not a real authorization.")
    ChainedLog(gov / "governance/research_authorizations.jsonl").append(auth)
    (gov / "registry/research_authorization_uses.jsonl").write_text("")
    return {"work": work, "art": art, "gov": gov, "exp": exp, "proposal": P, "bindings": B, "request": req,
            "request_path": exp / "request.json", "provenance_path": exp / "request_provenance.json",
            "proposal_path": exp / "proposal.json", "authorization_id": AUTH_ID}


def service(world: dict, verifier, **kw):
    from .service import AuthorizationBindingService  # noqa: PLC0415
    return AuthorizationBindingService(verifier=verifier, governance_root=world["gov"], root=world["art"],
                                       benchmark=synthetic_benchmark, **kw)


def bind_kwargs(world: dict, evidence_url: str, **over) -> dict:
    kw = {"source_request_path": world["request_path"], "provenance_path": world["provenance_path"],
          "proposal_path": world["proposal_path"], "authorization_id": world["authorization_id"],
          "evidence_url": evidence_url}
    kw.update(over)
    return kw


def another_request(world: dict, name: str, *, created_at: str = "2026-11-07T13:00:00+08:00") -> dict:
    """A second, distinct A2 proposal + PENDING request in the same world (same scope, different proposal id)."""
    from strategy_agent.phase5a.integration import proposal_to_request  # noqa: PLC0415
    from strategy_agent.proposal import FileDraftAdapter, draft_proposal  # noqa: PLC0415
    fx = json.loads((ROOT / "fixtures/proposal/complete_momentum.json").read_text(encoding="utf-8"))
    comp = world["exp"] / "completion_SYNTHETIC_FIXTURE_TSM.json"
    P = draft_proposal(fx["user_hypothesis"].replace("2330", "TSM"),
                       FileDraftAdapter(comp, adapter_id="SYNTHETIC_FIXTURE_COMPLETION_FILE_NOT_A_MODEL"),
                       agent_commit_sha=world["proposal"]["origin"]["agent_commit_sha"], created_at=created_at)
    d = world["exp"] / name
    d.mkdir()
    (d / "proposal.json").write_text(json.dumps(P, ensure_ascii=False, indent=2), encoding="utf-8")
    conv = proposal_to_request(P, bindings=world["bindings"], output=d / "request.json", artifact_root=world["art"])
    if conv["status"] != "EXPORTED":
        raise RuntimeError(conv)
    return {**world, "proposal": P, "request": json.loads((d / "request.json").read_text(encoding="utf-8")),
            "request_path": d / "request.json", "provenance_path": d / "request_provenance.json",
            "proposal_path": d / "proposal.json"}
