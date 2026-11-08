"""The first real-research CANDIDATE: pointers to authoritative records only (no value is declared here).

Every value in the readiness manifest is READ from these records and cross-checked; a missing record or a
disagreement between records is BLOCKED, never filled with a default.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

CANDIDATE = {
    "candidate_id": "IBKR_TSM_SMA520_DEV_SNAPSHOT_PHASE5",
    "description": "IBKR TSM, existing validated DEVELOPMENT snapshot, pre-registered SMA 5/20 baseline, executed through "
                   "the Phase 5 controlled pipeline (A2 request -> verified binding -> budget ledger -> ResearchOnlyRunner)",
    # authoritative records (repository-relative)
    "run_config": "configs/research_only/tsm_sma_5_20_smoke_2024_06_dev_18350b3adba8e28a7e5e.json",
    "template_authorization_id": "RA-IBKR_TSM-SMA520_DEV-18350b3adba8e28a7e5e",   # SPENT 1/1: a pin source, never reused
    "authorization_book": "governance/research_authorizations.jsonl",
    "approvers": "governance/approvers.json",
    "uses_log": "registry/research_authorization_uses.jsonl",
    "holdout_book": "governance/holdout_locks.jsonl",
    "market_readiness": "governance/market_readiness.json",
    "prior_real_run_evidence": "docs/evidence/phase4a_dev_smoke/20261107_081300_9439f8c",   # archived, read-only
    "ci_workflow": ".github/workflows/ci.yml",
    "ci_core_job": "phase5b2-controlled",
    "drive_archive_root": "MyDrive/Quant_Research/strategy-research-agent/",
}


def path(key: str) -> Path:
    return ROOT / CANDIDATE[key]
