"""Phase 5-B3-A Production Readiness Manifest (machine-readable).

Each field: ``{"value", "status", "category", "sources", "reason"}``.

  status RESOLVED  every authoritative source agrees
  status BLOCKED   category MISSING         a required record / value is absent
                   category CONTRADICTION   authoritative sources disagree (never resolved by picking one)
                   category HUMAN_DECISION  intentionally absent until a human decision (authorization, binding,
                                            executor, validity); B3-A never creates these

Nothing is inferred and nothing has a default. The manifest is deterministic for a given tree (no wall-clock time),
so the committed copy can be regenerated and compared byte for byte.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from strategy_agent.execution.identity import raw_run_info

from .candidate import CANDIDATE, ROOT, path

KIND = "PHASE5B3A_PRODUCTION_READINESS_MANIFEST"
VERSION = "1.0.0"
FIELDS = ("research_market", "strategy_id_version", "strategy_spec_sha256", "dataset_snapshot_id",
          "dataset_manifest_sha256", "core_commit_sha", "temporal_rules", "research_window", "holdout_exclusion",
          "trading_cost_profile", "slippage_assumption", "authorization_id", "bound_request_hash",
          "budget_batch_limit", "executor_identity", "git_and_environment_identity", "archive_destination")


def _sha_file(p: Path) -> str | None:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def _json(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def _inst(s):
    try:
        return datetime.fromisoformat(str(s))
    except ValueError:
        return None


def field(value, sources: dict, *, reason: str | None = None, category: str | None = None) -> dict:
    """RESOLVED only when every source value is present and identical; otherwise BLOCKED (MISSING / CONTRADICTION)."""
    vals = list(sources.values())
    if category == "HUMAN_DECISION":
        return {"value": value, "status": "BLOCKED", "category": category, "sources": sources, "reason": reason}
    if not vals or any(v is None for v in vals):
        missing = sorted(k for k, v in sources.items() if v is None)
        return {"value": None, "status": "BLOCKED", "category": "MISSING", "sources": sources,
                "reason": reason or f"missing in: {missing}"}
    canon = [json.dumps(v, sort_keys=True) for v in vals]
    if len(set(canon)) != 1:
        return {"value": None, "status": "BLOCKED", "category": "CONTRADICTION", "sources": sources,
                "reason": reason or "authoritative sources disagree"}
    return {"value": value if value is not None else vals[0], "status": "RESOLVED", "category": None,
            "sources": sources, "reason": reason}


def _ci_core_pin(wf: Path, job: str) -> str | None:
    """MRC_SHA of one CI job, read textually (no YAML dependency); absent => None (MISSING), never a default."""
    if not wf.is_file():
        return None
    inside = False
    for line in wf.read_text(encoding="utf-8").splitlines():
        if line.startswith("  ") and not line.startswith("    ") and line.strip().endswith(":"):
            inside = line.strip()[:-1] == job
        elif inside and line.strip().startswith("MRC_SHA:"):
            return line.split(":", 1)[1].strip() or None
    return None


def _records() -> dict:
    cfg = _json(path("run_config")) or {}
    auth = None
    book = path("authorization_book")
    if book.is_file():
        auth = next((json.loads(x) for x in book.read_text().splitlines()
                     if x.strip() and json.loads(x).get("authorization_id") == CANDIDATE["template_authorization_id"]
                     and json.loads(x).get("action") == "APPROVE"), None)
    uses = [json.loads(x) for x in path("uses_log").read_text().splitlines() if x.strip()] if path("uses_log").is_file() else []
    locks = [json.loads(x) for x in path("holdout_book").read_text().splitlines() if x.strip()] \
        if path("holdout_book").is_file() else []
    ev = path("prior_real_run_evidence")
    info = raw_run_info((ev / "run_info.txt").read_bytes()) if (ev / "run_info.txt").is_file() else {}
    diag = _json(ev / "execution_diagnostics.json") or {}
    spec_path = path("strategy_spec")                                   # the candidate (re-pinned) spec
    spec = _json(spec_path) if spec_path.is_file() else None
    parent_path = ROOT / ((cfg.get("strategy") or {}).get("spec_path") or "__missing__")   # the run config's spec
    parent = _json(parent_path) if parent_path.is_file() else None
    cost_path = ROOT / ((cfg.get("costs") or {}).get("scenario_set_path") or "__missing__")
    cost = _json(cost_path) if cost_path.is_file() else None
    mr = (_json(path("market_readiness")) or {}).get("markets", {}).get("IBKR_TSM")
    ci = _ci_core_pin(path("ci_workflow"), CANDIDATE["ci_core_job"])
    return {"cfg": cfg, "auth": auth, "uses": uses, "locks": locks, "info": info, "diag": diag, "spec": spec,
            "spec_file_sha": _sha_file(spec_path), "parent_spec": parent, "parent_spec_file_sha": _sha_file(parent_path),
            "cost": cost, "cost_file_sha": _sha_file(cost_path), "mr": mr,
            "ci_core": ci}


def build(code_identity: dict | None = None) -> dict:
    R = _records()
    cfg, a, info, spec, cost, mr = R["cfg"], R["auth"] or {}, R["info"], R["spec"] or {}, R["cost"] or {}, R["mr"] or {}
    cs, ast, ds = cfg.get("strategy") or {}, a.get("strategy") or {}, cfg.get("dataset") or {}
    sb = cfg.get("snapshot_binding") or {}
    lock = next((x for x in R["locks"] if x.get("lock_id") == (a.get("holdout") or {}).get("lock_id")
                 and x.get("action") == "APPROVE"), None)
    used = sum(1 for u in R["uses"] if u.get("authorization_id") == CANDIDATE["template_authorization_id"])
    f = {}
    f["research_market"] = field({"market_id": "IBKR_TSM", "symbol": "TSM"}, {
        "run_config": (cfg.get("market") or {}).get("market_id") and {"market_id": cfg["market"]["market_id"],
                                                                      "symbol": cfg["market"].get("symbol")},
        "authorization": a.get("market_id") and {"market_id": a["market_id"], "symbol": a.get("symbol")},
        "strategy_spec_universe": spec.get("universe") and {"market_id": "IBKR_TSM" if spec["universe"].get("market") == "IBKR" else spec["universe"].get("market"),
                                                            "symbol": (spec["universe"].get("symbols") or [None])[0]}})
    ident = lambda d: d and {"strategy_id": d.get("strategy_id"), "parameters": d.get("parameters")}  # noqa: E731
    spec_params = {k: p["default"] for k, p in (spec.get("parameters", {}).get("values") or {}).items()} if spec else None
    f["strategy_id_version"] = field(
        spec.get("strategy_id") and {"strategy_id": spec["strategy_id"], "version": spec.get("version"), "parameters": spec_params}, {
            "candidate_spec_file": spec.get("strategy_id") and {"strategy_id": spec["strategy_id"], "parameters": spec_params},
            "run_config": ident(cs.get("strategy_id") and cs), "authorization": ident(ast.get("strategy_id") and ast),
            "prior_real_run": info.get("strategy") and {"strategy_id": info["strategy"].split()[0],
                                                        "parameters": {"fast": 5, "slow": 20} if "'fast': 5, 'slow': 20" in info["strategy"] else None}},
        reason="strategy identity (id + parameters) agrees everywhere; the candidate spec VERSION is "
               f"{spec.get('version')} (data-binding re-pin of {(R['parent_spec'] or {}).get('version')}); the new "
               "authorization and the B3-B run config must pin it")
    f["strategy_spec_sha256"] = field(None, {"candidate_spec_file_on_disk": R["spec_file_sha"],
                                             "candidate_pin": CANDIDATE["strategy_spec_sha256"]},
                                      reason=f"parent spec sha (run config / spent authorization) = {R['parent_spec_file_sha']}")
    f["dataset_snapshot_id"] = field(None, {"run_config": ds.get("snapshot_id"), "authorization": (a.get("dataset") or {}).get("snapshot_id"),
                                            "snapshot_pin": (sb.get("snapshot_pin") or {}).get("snapshot_id"),
                                            "prior_real_run": info.get("snapshot_id")})
    f["dataset_manifest_sha256"] = field(None, {"run_config": sb.get("manifest_sha256"),
                                                "integrity_receipt": (sb.get("integrity_receipt") or {}).get("manifest_sha256"),
                                                "prior_real_run": info.get("manifest_sha256")},
                                         reason="bytes on Drive are re-hashed only by the Colab acceptance (optional, non-consuming)")
    f["core_commit_sha"] = field(None, {"run_config": (cfg.get("core") or {}).get("phase4a_sha"),
                                        "dataset_producer_in_authorization": (a.get("dataset") or {}).get("producer_commit_sha"),
                                        "prior_real_run_core": info.get("core_git_sha"), "ci_pin": R["ci_core"]})
    ra = ((mr.get("facts") or {}).get("conservative_temporal_policy") or {}).get("research_approval") or {}
    ex = spec.get("execution") or {}
    f["temporal_rules"] = field(None, {
        "run_config": (cfg.get("temporal_policy") or {}).get("sha256") and {"policy_sha256": cfg["temporal_policy"]["sha256"], "scope": cfg["temporal_policy"].get("scope")},
        "authorization": (a.get("temporal_policy") or {}).get("sha256") and {"policy_sha256": a["temporal_policy"]["sha256"], "scope": a["temporal_policy"].get("scope")},
        "market_readiness_research_approval": ra.get("policy_sha256") and {"policy_sha256": ra["policy_sha256"], "scope": ra.get("approval_scope")},
        "prior_real_run": info.get("temporal_policy_sha256") and {"policy_sha256": info["temporal_policy_sha256"], "scope": "RESEARCH_ONLY"}},
        reason=f"signal_time={ex.get('signal_time')} fill_rule={ex.get('fill_rule')} order_type={ex.get('order_type')}; "
               "Core conservative 15m aggregation: RTH only, closing auction excluded, fills strictly after modeled availability")
    w = cfg.get("windows") or {}
    rw = a.get("research_window") or {}
    def _span(lo, hi):
        lo, hi = _inst(lo), _inst(hi)
        return {"start": lo.isoformat(), "end": hi.isoformat()} if lo and hi else None
    f["research_window"] = field({"warmup_start": w.get("warmup_start"), "start": w.get("start"), "end": w.get("end"),
                                  "interval": w.get("interval")}, {
        "run_config_warmup_to_end": _span(w.get("warmup_start"), w.get("end")),
        "authorization_research_window": _span(rw.get("start"), rw.get("end")),
        "prior_real_run_window": _span(*(info.get("window", "").replace("warm-up ", "").replace(";", "").replace("[", "")
                                         .replace(")", "").replace(",", "").split()[i] for i in (0, 2)))
        if info.get("window") else None})
    ho = a.get("holdout") or {}
    hstart = _inst(ho.get("start"))
    excl = bool(lock and hstart and _inst(w.get("end")) and _inst(w["end"]) <= hstart
                and lock.get("holdout_start") == ho.get("start") and lock.get("holdout_end") == ho.get("end")
                and str(info.get("holdout_accessed")).lower() == "false"
                and (R["diag"].get("data") or {}).get("max_native_timestamp_before_holdout") is True)
    f["holdout_exclusion"] = field({"lock_id": ho.get("lock_id"), "holdout": [ho.get("start"), ho.get("end")],
                                    "window_end_before_holdout": excl, "blindness": ho.get("blindness")}, {
        "authorization": ho.get("lock_id"), "holdout_book": lock and lock.get("lock_id"),
        "exclusion_proven": ho.get("lock_id") if excl else None},
        reason=None if excl else "window end / lock bounds / prior-run holdout evidence do not prove exclusion")
    f["trading_cost_profile"] = field(None, {
        "cost_file_on_disk": R["cost_file_sha"], "run_config": (cfg.get("costs") or {}).get("scenario_set_sha256"),
        "authorization": (a.get("costs") or {}).get("scenario_set_sha256")})
    if f["trading_cost_profile"]["status"] == "RESOLVED":
        f["trading_cost_profile"]["value"] = {"scenario_set_sha256": R["cost_file_sha"], "model": (a.get("costs") or {}).get("model"),
                                              "scenarios": (a.get("costs") or {}).get("scenarios"),
                                              "coverage": cost.get("coverage"), "scope": cost.get("scope")}
    sl = {k: v.get("slippage_bps") for k, v in (cost.get("scenarios") or {}).items()}
    f["slippage_assumption"] = field({"slippage_bps_per_side": sl, "half_spread_bps": cost.get("half_spread_bps")},
                                     {"cost_set": sl or None}, reason="RESEARCH_ONLY assumption, not account-verified")
    f["authorization_id"] = field(None, {}, category="HUMAN_DECISION",
                                  reason=f"a NEW authorization is required; {CANDIDATE['template_authorization_id']} is SPENT "
                                         f"({used}/{a.get('max_batches')}) and may not be reused or reset. B3-A prepares a "
                                         "draft only (docs/phase5b3a/authorization_draft.json)")
    f["bound_request_hash"] = field(None, {}, category="HUMAN_DECISION",
                                    reason="exists only after the authorization is approved and the A2 request is bound with "
                                           "production GitHub approval evidence (Phase 5-B1)")
    f["budget_batch_limit"] = field(None, {}, category="HUMAN_DECISION",
                                    reason="proposed max_batches = 1 (draft); set only by the research lead's approval")
    f["executor_identity"] = field(None, {}, category="HUMAN_DECISION",
                                   reason="single registered executor id (SINGLE_EXECUTOR_ONLY) to be named in the authorization "
                                          "decision; proposed in the draft, never self-registered by B3-A")
    ci = code_identity
    if not ci:
        f["git_and_environment_identity"] = field(None, {}, category="HUMAN_DECISION",
                                                  reason="captured at acceptance / run time")
    elif ci.get("dirty") or not ci.get("git_sha") or set(ci["git_sha"]) == {"0"}:
        f["git_and_environment_identity"] = {"value": ci, "status": "BLOCKED", "category": "MISSING", "sources": {"code": ci},
                                             "reason": "uncommitted changes or unknown git sha: identity not reproducible"}
    else:
        f["git_and_environment_identity"] = {"value": ci, "status": "RESOLVED", "category": None, "sources": {"code": ci},
                                             "reason": "B3-B must record its own run-time identity again"}
    arch = cfg.get("archive") or {}
    f["archive_destination"] = field({"drive_root": arch.get("drive_root"), "run_id_format": arch.get("run_id_format"),
                                      "immutable": arch.get("immutable")},
                                     {"run_config": arch.get("drive_root"), "brief": CANDIDATE["drive_archive_root"]})
    cross = cross_checks(R)
    cats = {x["category"] for x in f.values() if x["status"] == "BLOCKED"} | \
           {"CONTRADICTION" for c in cross.values() if c["status"] == "BLOCKED"}
    return {"kind": KIND, "version": VERSION, "candidate": CANDIDATE, "fields": {k: f[k] for k in FIELDS},
            "cross_checks": cross,
            "candidate_lineage_sha256": (sb.get("snapshot_pin") or {}).get("lineage_sha256"),
            "status": "BLOCKED" if cats else "RESOLVED",
            "blocking_categories": sorted(c for c in cats if c),
            "ready_for_authorization_decision": not (cats - {"HUMAN_DECISION"}),
            "real_runner": "DISABLED", "authorization_created": False, "market_data_read": False}


def cross_checks(R: dict) -> dict:
    """Executable checks against the code paths the Phase 5 run would actually use (no re-implementation)."""
    out = {}
    cfg, spec = R["cfg"], R["spec"] or {}
    ds = cfg.get("dataset") or {}
    # the Phase 5 A2 exporter is the authority for spec <-> dataset binding of a request
    try:
        from strategy_agent.proposal import exporter as E  # noqa: PLC0415
        req = {"strategy_spec_ref": CANDIDATE["strategy_spec"],
               "strategy_spec_sha256": CANDIDATE["strategy_spec_sha256"],
               "strategy_family": {"strategy_id": spec.get("strategy_id"), "version": spec.get("version"),
                                   "parameters": (cfg.get("strategy") or {}).get("parameters")},
               "market": {"symbol": (cfg.get("market") or {}).get("symbol")},
               "dataset_ref": {"dataset_id": ds.get("dataset_id"), "producer_commit_sha": ds.get("producer_commit_sha")},
               "dataset_snapshot_id": ds.get("snapshot_id"),
               "dataset_manifest_sha256": (cfg.get("snapshot_binding") or {}).get("manifest_sha256"),
               "cost_profile_ref": {"scenario_set_path": (cfg.get("costs") or {}).get("scenario_set_path"),
                                    "scenario_set_sha256": (cfg.get("costs") or {}).get("scenario_set_sha256")},
               "research_window": {k: (cfg.get("windows") or {}).get(k) for k in ("warmup_start", "start", "end")}}
        prop = {"draft": {"strategy_spec_fragment": {}, "required_data": {"coverage": {"start": "2024-05-29", "end": "2024-06-28"}}}}
        E._check_references(req, prop, ROOT)                                     # noqa: SLF001
        out["phase5_request_spec_dataset_binding"] = {"status": "PASS", "detail": "A2 exporter reference checks pass"}
    except Exception as e:  # noqa: BLE001 - the exporter's refusal IS the finding
        sd = [{k: d.get(k) for k in ("dataset_id", "snapshot_id", "producer_commit_sha")}
              for d in (spec.get("data_requirements") or {}).get("datasets") or []]
        out["phase5_request_spec_dataset_binding"] = {
            "status": "BLOCKED", "error": f"{type(e).__name__}: {e}",
            "spec_data_requirements": sd,
            "candidate_dataset": {k: ds.get(k) for k in ("dataset_id", "snapshot_id", "producer_commit_sha")},
            "detail": "the strategy spec pins the FINAL_GATE parent snapshot; the candidate DEV snapshot is a derived "
                      "subset. The Phase 5 A2 exporter refuses the request, so a first Phase 5 run needs a NEW spec "
                      "version re-pinned to the DEV snapshot (rules / parameters unchanged; new spec sha) approved by "
                      "the research lead, and the new authorization must pin that spec sha"}
    par = R.get("parent_spec") or {}
    changed = sorted(k for k in set(par) | set(spec) if par.get(k) != spec.get(k))
    allowed = set(CANDIDATE["spec_rebind_fields"])
    pds, cds = ((par.get("data_requirements") or {}), (spec.get("data_requirements") or {}))
    rebind = {"snapshot_id", "producer_commit_sha", "manifest_sha256", "access_contract"}
    strip = lambda ds: [{k: v for k, v in d.items() if k not in rebind} for d in ds or []]  # noqa: E731
    dr_ok = ({k: v for k, v in pds.items() if k != "datasets"} == {k: v for k, v in cds.items() if k != "datasets"}
             and strip(pds.get("datasets")) == strip(cds.get("datasets")))
    out["spec_rules_unchanged_vs_parent"] = {
        "status": "PASS" if par and spec and set(changed) <= allowed and dr_ok and spec.get("version") != par.get("version")
        and (spec.get("provenance") or {}).get("parent_spec_id") == f"{par.get('strategy_id')}@{par.get('version')}" else "BLOCKED",
        "changed_keys": changed, "allowed": sorted(allowed), "data_requirements_non_dataset_keys_identical": dr_ok,
        "parent": f"{par.get('strategy_id')}@{par.get('version')}", "candidate": f"{spec.get('strategy_id')}@{spec.get('version')}"}
    rcpt = (cfg.get("snapshot_binding") or {}).get("integrity_receipt") or {}
    out["snapshot_integrity_receipt"] = {
        "status": "PASS" if rcpt.get("parquet_sha256") and rcpt.get("manifest_sha256") and rcpt.get("verification_ref") else "BLOCKED",
        "verification_ref": rcpt.get("verification_ref"),
        "note": "the prior real run renewed this receipt per session (Core renew_development_receipt); the B3-B session "
                "must renew it again and the session config must differ ONLY in the receipt fields (as recorded in the "
                "prior run_info session_config_difference)"}
    return out
