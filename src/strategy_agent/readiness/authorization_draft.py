"""Phase 5-B3-A authorization SPECIFICATION draft (never written to governance/, never approved, never consumed).

The draft copies pins only from the readiness manifest (authoritative records). Every value the research lead must
decide is an explicit ``HUMAN_DECISION_REQUIRED`` entry, never a default. ``schema_dry_run`` validates the draft
against the real authorization schema IN MEMORY with clearly fake approval fields, to prove the shape is
admissible; nothing is appended to any book.
"""
from __future__ import annotations

import copy
import json

from .candidate import CANDIDATE, path

HDR = "HUMAN_DECISION_REQUIRED"


def _template() -> dict:
    for x in path("authorization_book").read_text().splitlines():
        e = json.loads(x) if x.strip() else {}
        if e.get("authorization_id") == CANDIDATE["template_authorization_id"] and e.get("action") == "APPROVE":
            return e
    raise LookupError("template authorization record missing")


def build(m: dict) -> dict:
    t = _template()
    F, X = m["fields"], m["cross_checks"]
    spec_blocked = X["phase5_request_spec_dataset_binding"]["status"] != "PASS"
    proposal = {
        "contract": t["contract"], "scope": t["scope"], "market_id": t["market_id"], "symbol": t["symbol"],
        "dataset": copy.deepcopy(t["dataset"]), "temporal_policy": copy.deepcopy(t["temporal_policy"]),
        "costs": copy.deepcopy(t["costs"]), "strategy": copy.deepcopy(t["strategy"]),
        "research_window": copy.deepcopy(t["research_window"]),
        "development_boundary": copy.deepcopy(t["development_boundary"]), "holdout": copy.deepcopy(t["holdout"]),
        "formal_gate_pin": copy.deepcopy(t["formal_gate_pin"]),
        "does_not_authorize": list(t["does_not_authorize"]) + [
            "enabling the real Runner outside the governed Phase 5 controlled path",
            "more than the approved max_batches", "any executor other than the named single executor",
            "reuse or reset of RA-IBKR_TSM-SMA520_DEV-18350b3adba8e28a7e5e"],
    }
    if spec_blocked:
        proposal["strategy"]["spec_file_sha256"] = HDR
        proposal["strategy"]["spec_path"] = HDR
    else:   # the research lead's re-pinned spec (data binding only); the spent authorization keeps its parent spec
        proposal["strategy"].update(spec_path=CANDIDATE["strategy_spec"], spec_file_sha256=F["strategy_spec_sha256"]["value"],
                                    version=F["strategy_id_version"]["value"]["version"])
    return {
        "kind": "PHASE5B3A_AUTHORIZATION_SPECIFICATION_DRAFT",
        "status": "DRAFT_NOT_APPROVED",
        "warning": "NOT an authorization. Not in governance/. Creating / approving it is the research lead's action "
                   "(research_authorization_admin approve), after reviewing every HUMAN_DECISION_REQUIRED entry.",
        "research_purpose": {
            "purpose": "First real research execution through the governed Phase 5 pipeline (A2 request -> verified "
                       "binding -> atomic budget ledger -> existing ResearchOnlyRunner -> evidence-verified settlement)",
            "hypothesis": "Pre-registered baseline only: SMA 5/20 on TSM 15m (spec hypothesis unchanged). Re-running the "
                          "already-evaluated DEV window adds pipeline evidence, NOT new statistical evidence about SMA",
            "decision": HDR,
        },
        "market_instrument": {"market_id": t["market_id"], "symbol": t["symbol"]},
        "dataset_and_period": {"dataset": t["dataset"], "dataset_manifest_sha256": F["dataset_manifest_sha256"]["value"],
                               "research_window": t["research_window"], "trading_window": {
                                   k: F["research_window"]["value"].get(k) for k in ("start", "end")}},
        "strategy_specification": {
            "strategy_id": proposal["strategy"]["strategy_id"], "version": proposal["strategy"]["version"],
            "parameters": proposal["strategy"]["parameters"],
            "spec": HDR if spec_blocked else {"path": proposal["strategy"]["spec_path"],
                                              "sha256": proposal["strategy"]["spec_file_sha256"],
                                              "parent": {"path": t["strategy"]["spec_path"], "sha256": t["strategy"]["spec_file_sha256"]},
                                              "change": "data_requirements re-pinned to the DEV snapshot only "
                                                        "(research lead decision 2026-11-08); rules / parameters unchanged",
                                              "rules_unchanged_check": X["spec_rules_unchanged_vs_parent"]["status"]},
            "spec_decision": X["phase5_request_spec_dataset_binding"] if spec_blocked else "DECIDED (re-pinned spec)"},
        "cost_assumptions": {"scenario_set": t["costs"], "slippage_bps_per_side": F["slippage_assumption"]["value"]},
        "max_research_batches": {"proposed": 1, "decision": HDR},
        "validity_period": {"issued_at": HDR, "expires_at": HDR,
                            "constraint": "must expire before any change of the pinned formal gates (RA10)"},
        "executor_identity": {"proposed": "single named executor (e.g. one Colab runtime of hh4832)", "decision": HDR,
                              "consistency": "SINGLE_EXECUTOR_ONLY"},
        "holdout_policy": {"lock_id": t["holdout"]["lock_id"], "holdout": [t["holdout"]["start"], t["holdout"]["end"]],
                           "access": "NO_ACCESS", "blindness": t["holdout"].get("blindness")},
        "expected_archive_destination": F["archive_destination"]["value"],
        "authorization_id": {"proposed_pattern": "RA-IBKR_TSM-SMA520_<NEW>", "decision": HDR},
        "proposal_fields": proposal,
    }


def schema_dry_run(draft: dict) -> dict:
    """Validate the proposal shape against the REAL schema, in memory, with fake approval fields. Never writes."""
    from strategy_agent.governance.research_authorization import validate_events  # noqa: PLC0415
    ev = copy.deepcopy(draft["proposal_fields"])
    if HDR in json.dumps(ev):
        pending = sorted({k for k, v in ev.get("strategy", {}).items() if v == HDR})
        ev["strategy"].update({"spec_path": "specs/research/DRY_RUN_PLACEHOLDER.json", "spec_file_sha256": "0" * 64})
    else:
        pending = []
    ev.update(action="APPROVE", authorization_id="RA-DRYRUN-NOTREAL-0000", max_batches=1,
              issued_at="2026-11-09T00:00:00+08:00", expires_at="2026-11-10T00:00:00+08:00",
              approved_by="dry-run-not-an-approver", approval_ref="https://example.invalid/DRY_RUN_NOT_AN_APPROVAL",
              approved_at="2026-11-09T00:00:00+00:00", notes="IN-MEMORY SCHEMA DRY RUN ONLY")
    try:
        validate_events([ev], {"dry-run-not-an-approver"})
        return {"status": "PASS", "placeholders_used_for": pending, "written": False}
    except Exception as e:  # noqa: BLE001
        return {"status": "FAIL", "error": f"{type(e).__name__}: {e}"[:600], "written": False}
