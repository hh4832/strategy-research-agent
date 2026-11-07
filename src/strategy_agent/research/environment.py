"""Governance environments for the research runner.

``formal_environment()`` - the repository's governance files and formal registry. Used only for real-market runs,
which are additionally behind the M3-A market gate. The research runner has no other way to reach the formal
registry, and it never writes governance files.

``synthetic_environment(root)`` - an ISOLATED copy under ``root`` (never the repository): its own registry,
families index, holdout lock book (test lock ``HOLDOUT-SYNTH-TSM-15M-TEST`` on the synthetic dataset), budget,
controls and a test-only research lead. The only governance inputs that differ from the formal ones are listed in
``SYNTHETIC_OVERRIDES`` and are recorded in every synthetic archive:

* ``ibkr_tsm_session_coverage`` is CONFIRMED as a property of the synthetic fixture (RTH bars by construction).
  It says nothing about IBKR data; the formal file keeps it UNKNOWN until M3-A.
* a test-only APPROVED CostProfile ``synthetic_equity_test`` expressible by Core (notional commission + minimum,
  1-tick slippage). It is not the IBKR schedule; formal IBKR costs stay DRAFT (and hit gap G18).
"""
from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass
from pathlib import Path

from strategy_agent.costs.profile import derive_spec_costs, profile_hash
from strategy_agent.family.canonical import FamilyIndex
from strategy_agent.governance.budget import BudgetConfig, BudgetGuard, Controls
from strategy_agent.governance.holdout import HoldoutBook, HoldoutGuard, HoldoutReservations
from strategy_agent.governance.holdout_admin import approve
from strategy_agent.registry.admission import AdmissionLock
from strategy_agent.registry.experiments import ExperimentRegistry
from strategy_agent.spec.validate import GovernanceContext, canonical_hash, default_context

ROOT = Path(__file__).resolve().parents[3]
TEST_LEAD = "synthetic-test-lead"
SYNTH_LOCK_ID = "HOLDOUT-SYNTH-TSM-15M-TEST"
SYNTH_HOLDOUT_START = "2025-04-01T00:00:00-04:00"
SYNTH_SPLIT = "SPLIT-SYNTH-TSM-15M-TEST"


@dataclass
class GovernanceEnv:
    kind: str                         # SYNTHETIC | FORMAL
    root: Path
    registry: ExperimentRegistry
    families: FamilyIndex
    holdout: HoldoutGuard
    budget: BudgetGuard
    admission: AdmissionLock
    governance: GovernanceContext
    cost_profiles: dict
    overrides: tuple[str, ...] = ()


def synthetic_cost_profile() -> dict:
    p = {
        "profile_id": "synthetic_equity_test", "version": "1.0.0", "status": "APPROVED", "market": "IBKR",
        "instrument_scope": "SYNTHETIC fixture only (not an IBKR schedule)", "currency": "USD",
        "effective_from": "2025-01-01", "effective_to": None,
        "components": [
            {"id": "commission", "kind": "COMMISSION", "side": "BOTH", "rate": 0.0005, "basis": "RESEARCH_ASSUMPTION",
             "source": {"kind": "MODEL", "citation": "synthetic test assumption: 5 bps of notional", "ref": None,
                        "verified_at": None, "verified_by": None}},
            {"id": "min_commission", "kind": "MIN_COMMISSION", "side": "BOTH", "amount": 1.0,
             "basis": "RESEARCH_ASSUMPTION",
             "source": {"kind": "MODEL", "citation": "synthetic test assumption", "ref": None, "verified_at": None,
                        "verified_by": None}},
            {"id": "slippage", "kind": "SLIPPAGE", "side": "BOTH", "ticks": 1, "basis": "RESEARCH_ASSUMPTION",
             "source": {"kind": "MODEL", "citation": "1 tick (0.01) adverse", "ref": None, "verified_at": None,
                        "verified_by": None}},
        ],
        "provenance": {"created_by": "claude-m3b", "created_at": "2026-11-04T05:00:00+08:00",
                       "approved_by": TEST_LEAD, "approved_at": "2026-11-04T05:00:00+08:00",
                       "approval_ref": "https://example.test/synthetic-only", "supersedes": None,
                       "profile_sha256": None,
                       "notes": "TEST-ONLY approval inside an isolated synthetic environment; never in governance/"},
    }
    p["provenance"]["profile_sha256"] = profile_hash(p)
    return p


def _model(citation: str) -> dict:
    return {"kind": "MODEL", "citation": citation, "ref": None, "verified_at": None, "verified_by": None}


def synthetic_componentized_cost_profile() -> dict:
    """TEST_ONLY componentized equity profile for the Core M3-A.4 path: per-share commission + minimum, bps
    slippage, a SELL regulatory-fee rate and a per-share exchange fee (fee_mode COMPONENTIZED in Core). Every
    number is a synthetic test assumption - not an IBKR, SEC or FINRA rate, and never a formal approval."""
    p = {
        "profile_id": "synthetic_equity_componentized_test", "version": "1.0.0", "status": "APPROVED",
        "market": "IBKR", "instrument_scope": "SYNTHETIC native fixture only (TEST_ONLY; not an IBKR schedule)",
        "currency": "USD", "effective_from": "2025-01-01", "effective_to": None,
        "components": [
            {"id": "commission", "kind": "COMMISSION", "side": "BOTH", "per_unit": 0.005,
             "basis": "RESEARCH_ASSUMPTION", "source": _model("TEST_ONLY synthetic per-share commission")},
            {"id": "min_commission", "kind": "MIN_COMMISSION", "side": "BOTH", "amount": 1.0,
             "basis": "RESEARCH_ASSUMPTION", "source": _model("TEST_ONLY synthetic per-order minimum")},
            {"id": "exchange_fee", "kind": "EXCHANGE_FEE", "side": "BOTH", "per_unit": 0.0003,
             "basis": "RESEARCH_ASSUMPTION", "source": _model("TEST_ONLY synthetic per-share exchange fee")},
            {"id": "regulatory_fee", "kind": "REGULATORY_FEE", "side": "SELL", "rate": 0.0000278,
             "basis": "RESEARCH_ASSUMPTION", "source": _model("TEST_ONLY synthetic sell-side regulatory rate "
                                                             "(not the SEC/FINRA schedule)")},
            {"id": "slippage", "kind": "SLIPPAGE", "side": "BOTH", "bps": 2, "basis": "RESEARCH_ASSUMPTION",
             "source": _model("TEST_ONLY 2 bps adverse")},
        ],
        "provenance": {"created_by": "claude-m3c", "created_at": "2026-11-05T14:00:00+08:00",
                       "approved_by": TEST_LEAD, "approved_at": "2026-11-05T14:00:00+08:00",
                       "approval_ref": "https://example.test/synthetic-only", "supersedes": None,
                       "profile_sha256": None,
                       "notes": "TEST-ONLY approval inside an isolated synthetic environment; never in governance/"},
    }
    p["provenance"]["profile_sha256"] = profile_hash(p)
    return p


SYNTHETIC_OVERRIDES = (
    "source_confirmations.ibkr_tsm_session_coverage=CONFIRMED (synthetic fixture definition, not IBKR evidence)",
    "cost profile synthetic_equity_test@1.0.0 APPROVED by test lead (not an IBKR schedule)",
    "cost profile synthetic_equity_componentized_test@1.0.0 APPROVED by test lead (TEST_ONLY; not an IBKR schedule)",
    f"holdout lock {SYNTH_LOCK_ID} on the synthetic dataset from {SYNTH_HOLDOUT_START}",
    "registry, families, budget, controls and approvers are isolated copies under the run root",
)


def synthetic_spec(spec: dict, dataset: dict, profile: dict) -> dict:
    """Executable SYNTHETIC variant of a research spec: dataset -> fixture, costs -> test profile, REGISTERED.
    Strategy rules, parameters, sizing and execution are unchanged."""
    s = copy.deepcopy(spec)
    s["name"] = "[SYNTHETIC FIXTURE] " + s["name"]
    s["data_requirements"]["datasets"] = [{k: dataset[k] for k in (
        "dataset_id", "snapshot_id", "producer_commit_sha", "verdict", "usage_scope", "access_contract")}]
    s["universe"].pop("source_timeframe", None)                           # fixture is native 15m
    s["universe"].pop("aggregation", None)
    s["costs"] = derive_spec_costs(profile) | {
        "sensitivity_multipliers": spec["costs"]["sensitivity_multipliers"],
        "profile_ref": {"profile_id": profile["profile_id"], "version": profile["version"],
                        "sha256": profile["provenance"]["profile_sha256"]}}
    s["costs"]["fee"].setdefault("rounding", "NONE")
    s["costs"]["tax"].setdefault("rounding", "NONE")
    s["experiment"]["split_ref"] = SYNTH_SPLIT
    s["experiment"]["holdout_lock_id"] = SYNTH_LOCK_ID
    s["status"] = "REGISTERED"
    s["provenance"]["notes"] = "SYNTHETIC variant generated by strategy_agent.research.environment.synthetic_spec"
    s["provenance"]["spec_sha256"] = None
    s["provenance"]["spec_sha256"] = canonical_hash(s)
    return s


def _write_once(path: Path, obj: dict) -> None:
    if not path.exists():
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(obj), encoding="utf-8")
        os.replace(tmp, path)


def synthetic_environment(root: Path, *, dataset_id: str, clock=None, budget: dict | None = None,
                          extra_profiles: tuple = ()) -> GovernanceEnv:
    """``extra_profiles``: further TEST-ONLY profiles (e.g. a CUSTOM cost scenario); each must already carry its
    own approval/sha - nothing is upgraded here."""
    root = Path(root).resolve()
    if ROOT in (root, *root.parents) and (root / ".git").exists():
        raise ValueError("synthetic environment must not be the repository itself")
    for formal in (ROOT / "registry", ROOT / "governance"):
        if root == formal or formal in root.parents:
            raise ValueError(f"synthetic environment cannot live under {formal}")
    gov = root / "governance"
    gov.mkdir(parents=True, exist_ok=True)
    _write_once(gov / "approvers.json", {"research_leads": [TEST_LEAD]})
    locks, controls = gov / "holdout_locks.jsonl", gov / "controls.jsonl"
    locks.touch()
    controls.touch()
    if not locks.read_text(encoding="utf-8").strip():
        approve(locks, gov / "approvers.json", lock_id=SYNTH_LOCK_ID, dataset_id=dataset_id,
                snapshot_id="synthetic", start=SYNTH_HOLDOUT_START, end=None, approved_by=TEST_LEAD,
                approval_ref="https://example.test/synthetic-holdout")
    reg = ExperimentRegistry(root / "experiments.jsonl")
    cfg = BudgetConfig(**({"max_trials_per_family": 20, "max_backtests_per_day": 50, "max_new_families_per_day": 3,
                           "max_wall_seconds_per_day": 14400, "timeout_seconds_per_experiment": 120,
                           "max_consecutive_failures": 3} | (budget or {})))
    kw = {"clock": clock} if clock else {}
    bg = BudgetGuard(reg, cfg, Controls(controls, gov / "approvers.json"), **kw)
    base = default_context()
    conf = copy.deepcopy(base.confirmations)
    conf["ibkr_tsm_session_coverage"].update(status="CONFIRMED", value="RTH_ONLY",
                                             evidence_ref="synthetic fixture definition (fixtures/synthetic)")
    profiles = {(p["profile_id"], p["version"]): p
                for p in (synthetic_cost_profile(), synthetic_componentized_cost_profile())}
    for p in extra_profiles:
        profiles[(p["profile_id"], p["version"])] = p
    ctx = GovernanceContext(conf, profiles, frozenset({TEST_LEAD}))
    return GovernanceEnv("SYNTHETIC", root, reg, FamilyIndex(root / "families.jsonl"),
                         HoldoutGuard(HoldoutBook(locks, gov / "approvers.json"),
                                      HoldoutReservations(root / "holdout_reservations.jsonl")),
                         bg, AdmissionLock(root), ctx, profiles, SYNTHETIC_OVERRIDES)


def formal_environment() -> GovernanceEnv:
    """Repository governance + formal registry (real-market runs only; gated by M3-A upstream)."""
    from strategy_agent.costs.profile import load_profiles  # noqa: PLC0415
    gov = ROOT / "governance"
    reg = ExperimentRegistry(ROOT / "registry" / "experiments.jsonl")
    ctx = default_context()
    return GovernanceEnv("FORMAL", ROOT, reg, FamilyIndex(ROOT / "registry" / "families.jsonl"),
                         HoldoutGuard(HoldoutBook(gov / "holdout_locks.jsonl", gov / "approvers.json"),
                                      HoldoutReservations(ROOT / "registry" / "holdout_reservations.jsonl")),
                         BudgetGuard(reg, BudgetConfig.load(), Controls()), AdmissionLock(ROOT / "registry"), ctx,
                         load_profiles())
