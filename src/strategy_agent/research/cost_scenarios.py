"""Cost scenario management (M3-B.2): BASE / LOW / STRESS / CUSTOM.

Division of labour: market-research-core computes every fee, tax and slippage. A scenario only pins
(a) which approved-or-not CostProfile is handed to Core and (b) Core's ``cost_multiplier``. The Agent never
recomputes costs, never edits a profile and never upgrades a status: everything recorded per scenario is
*derived* from the pinned profile file and from the adapter's Core-compatibility check.

Rules (fail closed):
  CS01 schema valid; scenario ids unique; exactly one BASE (multiplier 1.0)
  CS02 profile_ref resolves to a known profile and its sha256 matches the profile's recorded hash
  CS03 multiplier must be pre-registered in the strategy spec's costs.sensitivity_multipliers
  CS04 LOW < 1 <= STRESS ordering (LOW multiplier < 1, STRESS multiplier > 1); CUSTOM uses its own profile at 1.0
  CS05 no double counting: ALL_IN commission with a separate EXCHANGE_FEE / REGULATORY_FEE component, or more
       than one COMMISSION / SLIPPAGE component on the same side, is rejected
  CS06 a profile Core cannot express makes the scenario NOT executable (Core compatibility INCOMPATIBLE)
  CS07 currency must be the same for every scenario of a set
Scenarios of one set share the strategy's parameters, so the registry counts them as ONE family trial.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import jsonschema

from strategy_agent.adapters import mrc_adapter as A
from strategy_agent.costs.profile import profile_hash

ROOT = Path(__file__).resolve().parents[3]
SCHEMA = ROOT / "schemas" / "COST_SCENARIO_SET_SCHEMA.json"


class CostScenarioError(ValueError):
    def __init__(self, rule: str, message: str):
        self.rule = rule
        super().__init__(f"{rule}: {message}")


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    kind: str
    cost_multiplier: float
    commission_model: str
    profile: dict = field(repr=False)
    record: dict = field(default_factory=dict)       # derived metadata (archive / report)

    @property
    def executable(self) -> bool:
        return self.record["core_compatibility"]["verdict"] == "COMPATIBLE"


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def commission_model(profile: dict) -> str:
    out = []
    for c in profile["components"]:
        if c["kind"] == "COMMISSION":
            out.append(f"{c['side']} " + (f"per_unit {c['per_unit']}" if "per_unit" in c else f"rate {c.get('rate')}"))
        elif c["kind"] == "MIN_COMMISSION":
            out.append(f"minimum {c.get('amount')}")
        elif c["kind"] in {"EXCHANGE_FEE", "REGULATORY_FEE", "TAX"}:
            out.append(f"{c['kind']} {c['side']} " + (f"rate {c['rate']}" if "rate" in c else f"per_unit {c.get('per_unit')}"))
    return "; ".join(out) or "none"


def slippage_model(profile: dict) -> str:
    s = [c for c in profile["components"] if c["kind"] == "SLIPPAGE"]
    return "; ".join(f"{c['ticks']} ticks" if "ticks" in c else f"{c.get('bps')} bps" for c in s) or "none"


def double_counting(profile: dict, commission_model_decl: str) -> list[str]:
    comps = profile["components"]
    out = []
    if commission_model_decl == "ALL_IN":
        out += [f"{c['id']}: separate {c['kind']} while commission is declared ALL_IN" for c in comps
                if c["kind"] in {"EXCHANGE_FEE", "REGULATORY_FEE"}]
    for kind in ("COMMISSION", "SLIPPAGE", "MIN_COMMISSION"):
        sides = [c["side"] for c in comps if c["kind"] == kind]
        if len(sides) != len(set(sides)) or ("BOTH" in sides and len(sides) > 1):
            out.append(f"more than one {kind} component on the same side")
    return out


def describe(profile: dict, *, instrument_type: str, core_sha: str | None) -> dict:
    """Everything recorded about a profile used by a scenario - derived, never typed in by hand."""
    basis = sorted({c["basis"] for c in profile["components"]})
    unmappable = A.unmappable_cost_components(profile, instrument_type)
    return {
        "profile_id": profile["profile_id"], "version": profile["version"],
        "sha256": profile_hash(profile), "recorded_sha256": profile["provenance"].get("profile_sha256"),
        "currency": profile["currency"],
        "commission_model": commission_model(profile), "slippage_model": slippage_model(profile),
        "fee_basis": basis,
        "assumption_status": "ASSUMED" if "RESEARCH_ASSUMPTION" in basis else "EVIDENCED",
        "approval_status": profile["status"],                 # copied verbatim: DRAFT stays DRAFT
        "approved_by": profile["provenance"].get("approved_by"),
        "core_compatibility": {"pinned_core": core_sha, "instrument_type": instrument_type,
                               "verdict": "COMPATIBLE" if not unmappable else "INCOMPATIBLE",
                               "unmappable": unmappable},
    }


def load_set(path: Path | dict, *, profiles: dict, spec: dict, instrument_type: str = "EQUITY",
             core_sha: str | None = None) -> tuple[dict, list[Scenario]]:
    s = json.loads(Path(path).read_text(encoding="utf-8")) if not isinstance(path, dict) else path
    try:
        jsonschema.validate(s, json.loads(SCHEMA.read_text(encoding="utf-8")))
    except jsonschema.ValidationError as e:
        raise CostScenarioError("CS01", f"schema: {e.message}") from None
    ids = [x["scenario_id"] for x in s["scenarios"]]
    if len(ids) != len(set(ids)):
        raise CostScenarioError("CS01", "duplicate scenario_id")
    base = [x for x in s["scenarios"] if x["kind"] == "BASE"]
    if len(base) != 1 or base[0]["cost_multiplier"] != 1.0:
        raise CostScenarioError("CS01", "exactly one BASE scenario with cost_multiplier 1.0 is required")
    allowed = [float(m) for m in spec["costs"]["sensitivity_multipliers"]]
    out, currencies = [], set()
    for x in s["scenarios"]:
        ref = x["profile_ref"]
        prof = profiles.get((ref["profile_id"], ref["version"]))
        if prof is None:
            raise CostScenarioError("CS02", f"{x['scenario_id']}: unknown profile {ref['profile_id']}@{ref['version']}")
        recorded = prof["provenance"].get("profile_sha256")
        if profile_hash(prof) != ref["sha256"] or (recorded is not None and recorded != ref["sha256"]):
            raise CostScenarioError("CS02", f"{x['scenario_id']}: profile sha256 mismatch")
        m = float(x["cost_multiplier"])
        if m not in allowed:
            raise CostScenarioError("CS03", f"{x['scenario_id']}: multiplier {m} not pre-registered {allowed}")
        if (x["kind"] == "LOW" and not m < 1) or (x["kind"] == "STRESS" and not m > 1) or \
                (x["kind"] == "CUSTOM" and m != 1.0):
            raise CostScenarioError("CS04", f"{x['scenario_id']}: {x['kind']} with multiplier {m}")
        if x["kind"] != "CUSTOM" and (ref["profile_id"], ref["version"]) != \
                (base[0]["profile_ref"]["profile_id"], base[0]["profile_ref"]["version"]):
            raise CostScenarioError("CS04", f"{x['scenario_id']}: BASE/LOW/STRESS must share the BASE profile")
        dc = double_counting(prof, x["commission_model"])
        if dc:
            raise CostScenarioError("CS05", f"{x['scenario_id']}: " + "; ".join(dc))
        rec = describe(prof, instrument_type=instrument_type, core_sha=core_sha) | {
            "scenario_id": x["scenario_id"], "kind": x["kind"], "cost_multiplier": m,
            "declared_commission_model": x["commission_model"],
            "sensitivity_only": True, "counts_as_new_family_or_trial": False}
        currencies.add(prof["currency"])
        out.append(Scenario(x["scenario_id"], x["kind"], m, x["commission_model"], prof, rec))
    if len(currencies) != 1:
        raise CostScenarioError("CS07", f"mixed currencies {sorted(currencies)}")
    meta = {"scenario_set_id": s["scenario_set_id"], "version": s["version"], "sha256": _sha(s),
            "scenarios": [sc.record for sc in out]}
    return meta, out


def require_executable(sc: Scenario) -> None:
    """CS06: a scenario whose profile Core cannot express is never run (no silent dropping of components)."""
    if not sc.executable:
        raise A.CoreContractGap(["K18"], f"scenario {sc.scenario_id}: "
                                + "; ".join(sc.record["core_compatibility"]["unmappable"]))
