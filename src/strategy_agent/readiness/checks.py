"""Phase 5-B3-A execution-critical readiness checks (non-consuming; records + archived evidence + code paths).

Real Dataset Readiness re-uses the archived evidence of the PRIOR real development run (read-only) and the existing
Core benchmark records; it never opens market data, the snapshot bytes or the Holdout. Controlled Execution
Readiness inspects the code paths the real run would use and asserts that the real Runner stays DISABLED.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .candidate import ROOT, path

ALLOWED_ENV = {"SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "GH_TOKEN", "GITHUB_TOKEN"}   # TLS + approval-evidence read only


def _c(ok: bool | None, detail, *, attested: bool = False) -> dict:
    st = "PASS" if ok else ("BLOCKED" if ok is None else "FAIL")
    return {"status": st, "basis": "ARCHIVED_EVIDENCE_ATTESTED" if attested else "RECORD_OR_CODE_CHECK", "detail": detail}


def _evidence() -> tuple[dict, dict, dict]:
    ev = path("prior_real_run_evidence")
    diag = json.loads((ev / "execution_diagnostics.json").read_text()) if (ev / "execution_diagnostics.json").is_file() else {}
    met = json.loads((ev / "metrics.json").read_text()) if (ev / "metrics.json").is_file() else {}
    from strategy_agent.execution.identity import raw_run_info  # noqa: PLC0415
    info = raw_run_info((ev / "run_info.txt").read_bytes()) if (ev / "run_info.txt").is_file() else {}
    return diag, met, info


def dataset_readiness(m: dict) -> dict:
    """The nine execution-critical dataset requirements of the brief (section 3)."""
    F, X = m["fields"], m["cross_checks"]
    diag, met, info = _evidence()
    data, prov, temporal = diag.get("data") or {}, diag.get("loader_provenance") or {}, diag.get("temporal") or {}
    m3a4 = diag.get("m3a4_checks") or {}
    sid, msha = F["dataset_snapshot_id"]["value"], F["dataset_manifest_sha256"]["value"]
    out = {}
    out["D1_snapshot_exists_and_immutable"] = _c(
        bool(sid and X["snapshot_integrity_receipt"]["status"] == "PASS"
             and (prov.get("derived_snapshot_pin") or {}).get("lineage_sha256") == m["candidate_lineage_sha256"]
             and prov.get("integrity_method") == "REUSED_TRUSTED_SHA256_RECEIPT_WITH_STAT_GUARD"
             and prov.get("snapshot_id") == sid),
        {"snapshot_id": sid, "lineage_sha256": (prov.get("derived_snapshot_pin") or {}).get("lineage_sha256"), "integrity_method": prov.get("integrity_method"),
         "note": "immutability attested by the Core development-snapshot contract receipt of the prior real run; "
                 "bytes are re-hashed only by the optional Colab Drive check"}, attested=True)
    out["D2_manifest_matches_dataset"] = _c(
        F["dataset_manifest_sha256"]["status"] == "RESOLVED" and prov.get("manifest_sha256") == msha
        and prov.get("snapshot_id") == sid,
        {"manifest_sha256": msha, "loader_provenance_manifest_sha256": prov.get("manifest_sha256")}, attested=True)
    pit_keys = ("P03_available_at_policy", "P04_temporal_policy_sha256", "P07_modeled_rule", "T01_no_lookahead")
    pit = {s: {k: c.get(k) for k in pit_keys} for s, c in m3a4.items()}
    out["D3_timestamps_and_available_at_PIT"] = _c(
        bool(pit) and all(v == "PASS" for c in pit.values() for v in c.values())
        and prov.get("policy_sha256") == (F["temporal_rules"]["sources"].get("authorization") or {}).get("policy_sha256"),
        {"m3a4": pit, "policy_sha256": prov.get("policy_sha256"), "arrow_schema_timestamp": (prov.get("arrow_schema") or {}).get("timestamp_utc")},
        attested=True)
    out["D4_signal_to_execution_conservative"] = _c(
        bool(temporal) and all(t.get("status") == "PASS" and t.get("fills_not_strictly_after_signal_available_at") == 0
                               and t.get("fills_on_or_before_signal_bar") == 0 for t in temporal.values())
        and "fill_rule=NEXT_ELIGIBLE_BAR_OPEN" in (F["temporal_rules"]["reason"] or ""),
        {s: {k: t.get(k) for k in ("min_minutes_signal_available_to_fill", "fills_not_strictly_after_signal_available_at")}
         for s, t in temporal.items()}, attested=True)
    rej = data.get("execution_rejection_reasons") or {}
    out["D5_calendar_and_RTH_boundaries"] = _c(
        "OUTSIDE_CONSERVATIVE_RTH" in rej and "CLOSING_AUCTION_EXCLUDED" in rej and bool(data.get("sessions")),
        {"sessions": data.get("sessions"), "rejections": rej}, attested=True)
    out["D6_missing_data_explicit_policy"] = _c(
        "INCOMPLETE_SOURCE_BUCKET" in rej and (diag.get("rejections_by_scenario") is not None),
        {"policy": "Core conservative aggregation: an incomplete source bucket is never executable "
                   "(INCOMPLETE_SOURCE_BUCKET); orders not filled within max_fill_delay_bars expire (ORDER_EXPIRED); "
                   "no forward fill", "observed": {"INCOMPLETE_SOURCE_BUCKET": rej.get("INCOMPLETE_SOURCE_BUCKET")}},
        attested=True)
    cps = info.get("cost_profile_sha256")
    try:
        cps = json.loads(cps) if isinstance(cps, str) else cps
    except ValueError:
        cps = None
    out["D7_cost_and_slippage_pinned"] = _c(
        F["trading_cost_profile"]["status"] == "RESOLVED" and F["slippage_assumption"]["status"] == "RESOLVED"
        and isinstance(cps, dict) and set(cps) == {"BASE", "LOW", "STRESS", "CAT_STRESS"},
        {"scenario_set_sha256": (F["trading_cost_profile"]["value"] or {}).get("scenario_set_sha256"),
         "core_profile_sha256_per_scenario_prior_run": cps,
         "note": "Core profile shas depend on the trade date (dated SEC interval); re-derived (CO07) by the B3-B run"})
    out["D8_research_period_excludes_holdout"] = _c(
        F["holdout_exclusion"]["status"] == "RESOLVED" and (F["holdout_exclusion"]["value"] or {}).get("window_end_before_holdout") is True
        and data.get("max_native_timestamp_before_holdout") is True,
        {"holdout": (F["holdout_exclusion"]["value"] or {}).get("holdout"), "native_last_utc": data.get("native_last_utc")})
    from strategy_agent.adapters import mrc_adapter as A  # noqa: PLC0415 - reads benchmark records only
    bench = A.engine_benchmark_status(F["core_commit_sha"]["value"])
    mr = json.loads(path("market_readiness").read_text())["markets"]["IBKR_TSM"]
    dq = (mr.get("gates") or {}).get("DATA_QUALITY", {}).get("status")
    out["D9_core_benchmark_and_readiness_evidence"] = _c(
        bench["status"] == "BENCHMARK_PASS" and dq == "PASS",
        {"core_benchmark": bench, "DATA_QUALITY_gate": dq, "market_status": mr.get("status"),
         "note": "market status NO_GO is the FORMAL gate (unchanged); research-only eligibility rests on the authorization"})
    return out


def controlled_readiness() -> dict:
    """Code-path checks: real Runner DISABLED, no env-var enable path, controlled infra refuses production books."""
    from strategy_agent.execution.budget import CONSISTENCY  # noqa: PLC0415
    from strategy_agent.execution.controlled import ControlledRunnerAdapter  # noqa: PLC0415
    from strategy_agent.orchestration import adapters as AD  # noqa: PLC0415
    from strategy_agent.orchestration import governance as GV  # noqa: PLC0415
    out = {}
    try:
        AD.ResearchOnlyRunnerAdapter().execute({}, ROOT / "__never__")
        disabled = False
    except AD.AdapterDisabled:
        disabled = True
    except Exception:  # noqa: BLE001
        disabled = False
    out["C1_real_runner_disabled"] = _c(disabled and GV.REAL_EXECUTION_ENABLED is False
                                        and AD.ResearchOnlyRunnerAdapter.enabled is False,
                                        {"REAL_EXECUTION_ENABLED": GV.REAL_EXECUTION_ENABLED,
                                         "ResearchOnlyRunnerAdapter.execute": "AdapterDisabled" if disabled else "NOT_REFUSED"})

    class _Real:                     # a non-synthetic backend on the PRODUCTION books must never be enabled
        synthetic = False
    ad = ControlledRunnerAdapter(_Real(), governance_root=ROOT)
    out["C2_controlled_adapter_refuses_production_books"] = _c(ad.enabled is False and ad.isolated is False,
                                                              {"enabled": ad.enabled, "isolated": ad.isolated})
    try:
        from strategy_agent.orchestration.orchestrator import Orchestrator  # noqa: PLC0415
        import tempfile  # noqa: PLC0415
        with tempfile.TemporaryDirectory() as t:
            Orchestrator(Path(t) / "s", Path(t) / "o", root=ROOT, governance_root=ROOT, controlled_adapter=ad)
        refused = False
    except ValueError:
        refused = True
    out["C3_orchestrator_refuses_controlled_execution_on_production_books"] = _c(refused, {"refused": refused})
    hits = []
    for p in sorted((ROOT / "src").rglob("*.py")):
        for m in re.finditer(r"(?:os\.environ(?:\.get)?\s*[\[(]\s*|getenv\(\s*)[\"']([A-Z0-9_]+)[\"']", p.read_text(encoding="utf-8")):
            if m.group(1) not in ALLOWED_ENV:
                hits.append(f"{p.relative_to(ROOT)}:{m.group(1)}")
    out["C4_no_environment_variable_enable_path"] = _c(not hits, {"unexpected_env_reads": hits,
                                                                 "allowed": sorted(ALLOWED_ENV)})
    out["C5_single_executor_only"] = _c(CONSISTENCY == "SINGLE_EXECUTOR_ONLY", {"consistency": CONSISTENCY,
                                                                               "exactly_once": "NOT_CLAIMED"})
    return out


def governance_readiness(m: dict) -> dict:
    """Authorization / budget books: the spent authorization stays spent and the repository books are untouched."""
    from strategy_agent.governance.research_authorization import AuthorizationBookInvalid, ResearchAuthorizationBook  # noqa: PLC0415
    from .candidate import CANDIDATE  # noqa: PLC0415
    out = {}
    try:
        a = ResearchAuthorizationBook(path("authorization_book"), path("approvers")).get(CANDIDATE["template_authorization_id"])
        ok_book = True
    except AuthorizationBookInvalid as e:
        a, ok_book = None, f"{e}"
    uses = [json.loads(x) for x in path("uses_log").read_text().splitlines() if x.strip()]
    used = sum(1 for u in uses if u.get("authorization_id") == CANDIDATE["template_authorization_id"])
    out["G1_books_valid"] = _c(ok_book is True, {"authorization_book": ok_book})
    out["G2_template_authorization_spent_not_reusable"] = _c(
        a is not None and used >= int(a.get("max_batches", 0)) >= 1,
        {"authorization_id": CANDIDATE["template_authorization_id"], "used": used, "max_batches": (a or {}).get("max_batches")})
    # Only the known spent authorizations, plus (after the research lead's own approval PR) the ONE governed B3-C
    # authorization that b3c.formal_authorization_state() verifies against its committed proposal and run config.
    from .b3c import formal_authorization_state  # noqa: PLC0415
    fa = formal_authorization_state()
    out["G3_no_new_authorization_created"] = _c(
        fa["state"] in ("NOT_GRANTED", "GRANTED_UNUSED", "GRANTED_USED"),
        {"book": [json.loads(x).get("authorization_id") for x in path("authorization_book").read_text().splitlines() if x.strip()],
         "b3c_authorization_state": fa})
    out["G4_new_authorization_is_a_human_decision"] = _c(
        all(m["fields"][k]["category"] == "HUMAN_DECISION" for k in
            ("authorization_id", "bound_request_hash", "budget_batch_limit", "executor_identity")),
        {k: m["fields"][k]["status"] for k in ("authorization_id", "bound_request_hash", "budget_batch_limit", "executor_identity")})
    return out
