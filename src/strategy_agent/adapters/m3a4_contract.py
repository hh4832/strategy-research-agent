"""Agent-side validation of a Core M3-A.4 ``CONSERVATIVE_LABEL_AGNOSTIC`` result.

Field names are taken from the released contract only (market-research-core
``docs/M3A4_TEMPORAL_COST_CONTRACT.md`` @ 699e3ad59951bbebb769bb69170be8d9a1a96c01, "Exact Core->Agent fields").
Nothing is recomputed as a second simulation: the checks below are identities and pins that must hold on
what Core returned. Any failure raises ``ContractViolation`` so the M2 runner records FAILED, never COMPLETED.

T* temporal, K* cost/ledger, P* provenance/pins.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from decimal import Decimal
from typing import Mapping, Sequence

import pandas as pd

from strategy_agent.ports import ContractViolation

CONSERVATIVE = "CONSERVATIVE_LABEL_AGNOSTIC"
MIN_ENGINE_VERSION = (2, 2, 0)
LEDGER_FIELDS = (
    "order_id", "symbol", "market", "contract", "strategy_id", "experiment_id", "session", "side", "qty",
    "signal_time", "signal_available_at", "order_time", "exec_ts", "execution_assumption",
    "source_timestamp", "signal_source_timestamp", "source_timestamp_label", "modeled_available_at",
    "candidate_modeled_available_at", "candidate_possible_open_latest",
    "temporal_policy_id", "temporal_policy_version", "temporal_policy_sha256", "temporal_assumption",
    "execution_eligibility_scope",
    "raw_price", "fill_price", "fee", "tax", "regulatory_fee", "commission", "exchange_fee", "clearing_fee",
    "transaction_tax", "explicit_cost", "commission_scope", "price_adjustment_cost", "slippage_cost",
    "spread_cost", "tick_rounding_cost", "cost_profile_id", "cost_profile_version", "cost_profile_sha256",
)


def _d(x) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


def _ver(v: str) -> tuple:
    try:
        return tuple(int(p) for p in str(v).split("."))
    except ValueError:
        return (0,)


def policy_sha256(policy_dict: Mapping) -> str:
    """Same canonical form as Core TemporalPolicy.sha256 (sorted compact JSON of all fields)."""
    return hashlib.sha256(json.dumps(dict(policy_dict), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_conservative_result(result, *, orders: Sequence, cost_profile, policy, core_sha: str) -> dict:
    """Return {check_id: "PASS"} or raise ContractViolation listing every failed check."""
    fails: list[str] = []
    checks: dict[str, str] = {}

    def put(cid: str, ok: bool, msg: str) -> None:
        checks[cid] = "PASS" if ok else "FAIL"
        if not ok:
            fails.append(f"{cid}: {msg}")

    prov = dict(result.provenance)
    # ---------------------------------------------------------------- P: provenance / pins
    put("P01_engine_git_sha", prov.get("engine_git_sha") == core_sha,
        f"engine_git_sha {prov.get('engine_git_sha')} != installed Core {core_sha}")
    put("P02_engine_version", _ver(prov.get("engine_version")) >= MIN_ENGINE_VERSION,
        f"engine_version {prov.get('engine_version')} < 2.2.0 (no M3-A.4 contract)")
    put("P03_available_at_policy", prov.get("available_at_policy") == CONSERVATIVE,
        f"available_at_policy {prov.get('available_at_policy')}")
    tp = prov.get("temporal_policy") or {}
    put("P04_temporal_policy_sha256",
        prov.get("temporal_policy_sha256") == policy.sha256 == policy_sha256(tp) == policy_sha256(asdict(policy)),
        f"temporal policy sha {prov.get('temporal_policy_sha256')} / recomputed {policy_sha256(tp)} != {policy.sha256}")
    put("P05_temporal_policy_id", (prov.get("temporal_policy_id"), prov.get("temporal_policy_version")) ==
        (policy.policy_id, policy.version), f"policy {prov.get('temporal_policy_id')}@{prov.get('temporal_policy_version')}")
    put("P06_label_unknown", prov.get("source_timestamp_label") == "UNKNOWN",
        f"source_timestamp_label {prov.get('source_timestamp_label')} (must stay UNKNOWN; never inferred)")
    put("P07_modeled_rule", prov.get("modeled_available_at_rule") == "LATEST_START_END_COMPLETION",
        f"modeled_available_at_rule {prov.get('modeled_available_at_rule')}")
    put("P08_research_only", result.status == "RESEARCH_ONLY" and prov.get("execution_eligibility_scope") ==
        "RESEARCH_ONLY", f"status {result.status} / scope {prov.get('execution_eligibility_scope')} "
                         "(conservative mode can never be FORMAL_ELIGIBLE)")
    put("P09_cost_profile_sha256", prov.get("cost_profile_sha256") == cost_profile.sha256,
        f"provenance cost_profile_sha256 {prov.get('cost_profile_sha256')} != submitted {cost_profile.sha256}")
    put("P10_cost_profile_version", prov.get("cost_profile_version") == cost_profile.version and
        prov.get("fee_mode") == cost_profile.fee_mode,
        f"cost version/fee_mode {prov.get('cost_profile_version')}/{prov.get('fee_mode')}")
    put("P11_cost_components_declared", isinstance(prov.get("cost_profile_components"), dict) and
        all({"basis", "source_evidence", "approval_status"} <= set(v) for v in prov["cost_profile_components"].values()),
        "cost_profile_components lack basis/evidence/approval")

    # ---------------------------------------------------------------- ledger
    ledger = result.ledger
    put("K00_ledger_is_fills", ledger.equals(result.fills) and ledger is not result.fills,
        "ledger must be a copy of fills (one row per executed order, not a second simulation)")
    missing = [f for f in LEDGER_FIELDS if f not in ledger.columns] if len(ledger) else []
    put("K01_ledger_fields", not missing, f"ledger lacks {missing}")
    by_order = {o.order_id: o for o in orders}
    k_bad, t_bad = [], []
    if len(ledger) and not missing:
        for r in ledger.to_dict("records"):
            oid = r["order_id"]
            fee, tax, reg = _d(r["fee"]), _d(r["tax"]), _d(r["regulatory_fee"])
            if fee != _d(r["commission"]) + _d(r["exchange_fee"]) + _d(r["clearing_fee"]):
                k_bad.append(f"{oid}: fee != commission + exchange_fee + clearing_fee")
            if tax != _d(r["transaction_tax"]):
                k_bad.append(f"{oid}: tax != transaction_tax (alias)")
            if _d(r["explicit_cost"]) != fee + tax + reg:
                k_bad.append(f"{oid}: explicit_cost != fee + tax + regulatory_fee")
            if _d(r["slippage_cost"]) + _d(r["spread_cost"]) + _d(r["tick_rounding_cost"]) != _d(r["price_adjustment_cost"]):
                k_bad.append(f"{oid}: slippage + spread + tick_rounding != price_adjustment_cost")
            adverse = (_d(r["fill_price"]) - _d(r["raw_price"])) * (1 if r["side"] == "BUY" else -1)
            if adverse < 0:
                k_bad.append(f"{oid}: fill price is better than the raw open (costs must be adverse)")
            if any(_d(r[k]) < 0 for k in ("commission", "exchange_fee", "clearing_fee", "transaction_tax",
                                          "regulatory_fee", "price_adjustment_cost")):
                k_bad.append(f"{oid}: negative cost component")
            if (r["cost_profile_sha256"], r["cost_profile_version"]) != (cost_profile.sha256, cost_profile.version):
                k_bad.append(f"{oid}: ledger cost pin {r['cost_profile_sha256'][:12]} != submitted profile")
            if r["commission_scope"] != (cost_profile.fee_mode or "LEGACY"):
                k_bad.append(f"{oid}: commission_scope {r['commission_scope']} != fee_mode {cost_profile.fee_mode}")
            # ----- temporal (no look-ahead under either label hypothesis)
            ex, sa = pd.Timestamp(r["exec_ts"]), pd.Timestamp(r["signal_available_at"])
            st, ot = pd.Timestamp(r["signal_time"]), pd.Timestamp(r["order_time"])
            mod, cmod = pd.Timestamp(r["modeled_available_at"]), pd.Timestamp(r["candidate_modeled_available_at"])
            latest_open = pd.Timestamp(r["candidate_possible_open_latest"])
            if not (mod == st <= sa <= ot):
                t_bad.append(f"{oid}: signal must sit at a completed bucket (modeled {mod}, signal {st}, avail {sa}, order {ot})")
            if not ex > max(sa, ot):
                t_bad.append(f"{oid}: earliest possible fill open {ex} not strictly after availability {max(sa, ot)}")
            if not ex <= latest_open < cmod:
                t_bad.append(f"{oid}: candidate envelope inconsistent (open {ex}..{latest_open}, completes {cmod})")
            if r["source_timestamp_label"] != "UNKNOWN" or r["execution_eligibility_scope"] != "RESEARCH_ONLY":
                t_bad.append(f"{oid}: label/scope {r['source_timestamp_label']}/{r['execution_eligibility_scope']}")
            if (r["temporal_policy_id"], r["temporal_policy_sha256"]) != (policy.policy_id, policy.sha256):
                t_bad.append(f"{oid}: ledger temporal pin {r['temporal_policy_sha256'][:12]}")
            if r["execution_assumption"] != "NEXT_ELIGIBLE_BAR_OPEN":
                t_bad.append(f"{oid}: execution_assumption {r['execution_assumption']}")
            o = by_order.get(oid)
            if o is None or getattr(o, "order_type", "MARKET") != "MARKET":
                t_bad.append(f"{oid}: fill without a submitted MARKET order")
    put("K02_ledger_cost_identities", not k_bad, "; ".join(k_bad[:5]))
    put("T01_no_lookahead", not t_bad, "; ".join(t_bad[:5]))

    # ---------------------------------------------------------------- ledger vs round trips (FIFO allocation)
    rt = result.round_trips
    if len(rt) and {"gross_pnl", "net_pnl"} <= set(rt.columns) and len(ledger) and not missing:
        open_qty = sum(float(p.get("qty", 0) or 0) for p in result.open_positions.to_dict("records")) \
            if len(result.open_positions) else 0.0
        if not open_qty:
            costs_rt = sum((_d(g) - _d(n) for g, n in zip(rt["gross_pnl"], rt["net_pnl"])), Decimal(0))
            costs_ledger = sum((_d(x) for x in ledger["explicit_cost"]), Decimal(0))
            put("K03_round_trip_costs_equal_ledger", abs(costs_rt - costs_ledger) <= Decimal("1e-9"),
                f"round-trip allocated costs {costs_rt} != ledger explicit costs {costs_ledger}")
        else:
            checks["K03_round_trip_costs_equal_ledger"] = "NOT_APPLICABLE_OPEN_POSITION"
    else:
        checks["K03_round_trip_costs_equal_ledger"] = "NOT_APPLICABLE_NO_ROUND_TRIPS"

    # ---------------------------------------------------------------- rejections are reviewed codes only
    from .mrc_adapter import map_reason  # noqa: PLC0415
    unknown = []
    for r in (result.rejections.to_dict("records") if len(result.rejections) else []):
        try:
            map_reason(str(r.get("reason")))
        except ContractViolation:
            unknown.append(str(r.get("reason")))
    put("R01_rejection_codes_reviewed", not unknown, f"unreviewed rejection codes {sorted(set(unknown))}")
    if fails:
        raise ContractViolation("Core M3-A.4 contract: " + " | ".join(fails))
    return checks
