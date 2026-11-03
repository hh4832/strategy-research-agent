"""Versioned CostProfile (decision Q6).

A spec's inline ``costs`` block is no longer free text for REGISTERED/FROZEN
specs: it must reference an APPROVED profile (``costs.profile_ref``) whose
SHA-256 matches, and its numbers must equal what the profile derives.

Rules (P01–P07)
---------------
P01 schema valid
P02 OFFICIAL_RULE components cite LAW or EXCHANGE_RULE; BROKER_ACTUAL cite a
    BROKER_SCHEDULE or BROKER_STATEMENT; RESEARCH_ASSUMPTION cite MODEL/NONE
P03 APPROVED profiles: every OFFICIAL_RULE / BROKER_ACTUAL component has a
    https ref plus verified_at and verified_by
P04 APPROVED profiles: approved_by is an allow-listed research lead and an
    approval_ref URL is present; profile_sha256 matches the canonical hash
P05 SLIPPAGE is always RESEARCH_ASSUMPTION (no official or broker figure exists)
P06 at most one component per (kind, side) after expansion of BOTH
P07 (gate for FROZEN specs) COMMISSION and TAX must not be RESEARCH_ASSUMPTION
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[3]
SCHEMA = ROOT / "schemas" / "COST_PROFILE_SCHEMA.json"
PROFILE_DIR = ROOT / "governance" / "cost_profiles"

SOURCE_FOR_BASIS = {
    "OFFICIAL_RULE": {"LAW", "EXCHANGE_RULE"},
    "BROKER_ACTUAL": {"BROKER_SCHEDULE", "BROKER_STATEMENT"},
    "RESEARCH_ASSUMPTION": {"MODEL", "NONE"},
}


@dataclass(frozen=True)
class ProfileIssue:
    rule: str
    path: str
    message: str


def profile_hash(profile: dict) -> str:
    p = copy.deepcopy(profile)
    p["provenance"]["profile_sha256"] = None
    return hashlib.sha256(json.dumps(p, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def validate_profile(profile: dict, approvers: set[str]) -> list[ProfileIssue]:
    errs = sorted(Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8"))).iter_errors(profile),
                  key=lambda e: list(e.path))
    if errs:
        return [ProfileIssue("P01", "/" + "/".join(map(str, e.path)), e.message) for e in errs]
    out: list[ProfileIssue] = []
    approved = profile["status"] == "APPROVED"
    seen: set[tuple[str, str]] = set()
    for i, c in enumerate(profile["components"]):
        path = f"/components/{i}"
        src = c["source"]
        if src["kind"] not in SOURCE_FOR_BASIS[c["basis"]]:
            out.append(ProfileIssue("P02", path, f"{c['basis']} cannot be sourced from {src['kind']}"))
        if approved and c["basis"] != "RESEARCH_ASSUMPTION":
            if not (str(src.get("ref") or "").startswith("https://") and src.get("verified_at") and src.get("verified_by")):
                out.append(ProfileIssue("P03", path, f"{c['id']}: {c['basis']} needs https ref, verified_at, verified_by"))
        if c["kind"] == "SLIPPAGE" and c["basis"] != "RESEARCH_ASSUMPTION":
            out.append(ProfileIssue("P05", path, "slippage is always a research assumption"))
        for side in (("BUY", "SELL") if c["side"] == "BOTH" else (c["side"],)):
            key = (c["kind"], side)
            if key in seen:
                out.append(ProfileIssue("P06", path, f"duplicate {c['kind']} for side {side}"))
            seen.add(key)
    prov = profile["provenance"]
    if approved:
        if prov.get("approved_by") not in approvers or not str(prov.get("approval_ref") or "").startswith("https://"):
            out.append(ProfileIssue("P04", "/provenance", "APPROVED profile needs allow-listed approved_by and approval_ref URL"))
        if prov.get("profile_sha256") != profile_hash(profile):
            out.append(ProfileIssue("P04", "/provenance/profile_sha256", "missing or wrong profile hash"))
    return out


def frozen_gate_issues(profile: dict) -> list[ProfileIssue]:
    """P07: results meant for FROZEN / holdout cannot rest on assumed commissions or taxes."""
    return [ProfileIssue("P07", f"/components/{i}", f"{c['id']}: {c['kind']} is a RESEARCH_ASSUMPTION")
            for i, c in enumerate(profile["components"])
            if c["kind"] in {"COMMISSION", "TAX", "MIN_COMMISSION"} and c["basis"] == "RESEARCH_ASSUMPTION"]


def derive_spec_costs(profile: dict) -> dict:
    """The spec ``costs`` numbers implied by a profile (compared field by field)."""
    fee = {"per_unit_side": 0.0, "rate_side": 0.0}
    tax = {"rate_side": 0.0, "sell_rate": 0.0}
    slip: dict = {}
    for c in profile["components"]:
        k, side = c["kind"], c["side"]
        if k == "COMMISSION":
            if side != "BOTH":
                raise ValueError("spec v1 models commission as symmetric (side BOTH)")
            fee["per_unit_side"] += c.get("per_unit", 0.0)
            fee["rate_side"] += c.get("rate", 0.0)
        elif k in {"EXCHANGE_FEE", "REGULATORY_FEE"}:
            if side == "BOTH":
                fee["per_unit_side"] += c.get("per_unit", 0.0)
                fee["rate_side"] += c.get("rate", 0.0)
            elif side == "SELL":
                tax["sell_rate"] += c.get("rate", 0.0)
            else:
                raise ValueError("buy-only fees are not representable in spec v1")
        elif k == "MIN_COMMISSION":
            fee["min_per_order"] = c["amount"]
        elif k == "TAX":
            if side == "BOTH":
                tax["rate_side"] += c.get("rate", 0.0)
            elif side == "SELL":
                tax["sell_rate"] += c.get("rate", 0.0)
            else:
                raise ValueError("buy-only tax is not representable in spec v1")
        elif k == "ROUNDING":
            target = c.get("applies_to", "ALL")
            if target in ("COMMISSION", "ALL"):
                fee["rounding"] = c["rule"]
            if target in ("TAX", "ALL"):
                tax["rounding"] = c["rule"]
        elif k == "SLIPPAGE":
            slip = {"model": "TICKS", "value": c["ticks"]} if "ticks" in c else {"model": "BPS", "value": c["bps"]}
    return {"currency": profile["currency"], "fee": fee, "tax": tax, "slippage": slip}


def cost_mismatches(spec_costs: dict, profile: dict) -> list[str]:
    want = derive_spec_costs(profile)
    diffs = []
    if spec_costs.get("currency") != want["currency"]:
        diffs.append(f"currency {spec_costs.get('currency')} != {want['currency']}")
    for block in ("fee", "tax"):
        for k, v in want[block].items():
            got = spec_costs.get(block, {}).get(k, 0.0 if not isinstance(v, str) else "NONE")
            if (abs(float(got) - float(v)) > 1e-12) if not isinstance(v, str) else got != v:
                diffs.append(f"{block}.{k} {got} != profile {v}")
    for k, v in want["slippage"].items():
        if spec_costs.get("slippage", {}).get(k) != v:
            diffs.append(f"slippage.{k} {spec_costs.get('slippage', {}).get(k)} != profile {v}")
    return diffs


def load_profiles(directory: str | Path = PROFILE_DIR) -> dict[tuple[str, str], dict]:
    out = {}
    for p in sorted(Path(directory).glob("*.json")):
        prof = json.loads(p.read_text(encoding="utf-8"))
        out[(prof["profile_id"], prof["version"])] = prof
    return out
