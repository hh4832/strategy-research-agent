"""P0-3 remediation: authoritative execution identity, verified BEFORE reservation commit and BEFORE the runner.

A research request names what may be executed (strategy spec, Core, dataset snapshot + manifest, cost profile, temporal
policy, research window, holdout policy, execution mode). Before 5-B2 remediation the controlled precheck compared only
a few of these against a runner config built from the same request. Now three independent sources must all agree:

  1. the REQUEST, which must be exactly a registered bound request (binding log + immutable stored copy), whose
     authorization is active, unexpired and whose scope (spec sha / strategy / dataset snapshot / producer / costs /
     temporal policy / holdout lock / research window) matches the authorization record
  2. the ARTEFACTS on disk: strategy spec file and cost scenario file hash to the request's pins
  3. the BACKEND's own execution identity, derived from what it will actually run (runner config + snapshot manifest
     file on disk + installed Core identity), never from the request

Any mismatch is a refusal. Nothing is ever substituted ("latest snapshot", another manifest, another Core).
The same functions check the produced archive against the request and the authoritative uses record (P0-2).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

IDENTITY_KEYS = ("authorization_id", "strategy_id", "strategy_version", "strategy_parameters", "strategy_spec_sha256",
                 "core_commit", "dataset_snapshot_id", "dataset_manifest_sha256", "cost_profile_sha256",
                 "temporal_policy_sha256", "research_window")
WINDOW_KEYS = ("warmup_start", "start", "end")


def _sha_file(p: Path) -> str | None:
    p = Path(p)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() and not p.is_symlink() else None


def _inst(v):
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def _window(w) -> dict | None:
    if not isinstance(w, dict):
        return None
    return {k: w.get(k) for k in WINDOW_KEYS}


def same_window(a, b) -> bool:
    a, b = _window(a), _window(b)
    if a is None or b is None:
        return False
    return all(_inst(a[k]) is not None and _inst(a[k]) == _inst(b[k]) for k in WINDOW_KEYS)


def request_identity(doc: dict) -> dict:
    sf = doc.get("strategy_family") or {}
    return {"authorization_id": (doc.get("authorization_ref") or {}).get("authorization_id"),
            "strategy_id": sf.get("strategy_id"), "strategy_version": sf.get("version"),
            "strategy_parameters": sf.get("parameters"), "strategy_spec_sha256": doc.get("strategy_spec_sha256"),
            "core_commit": doc.get("core_commit"), "dataset_snapshot_id": doc.get("dataset_snapshot_id"),
            "dataset_manifest_sha256": doc.get("dataset_manifest_sha256"),
            "cost_profile_sha256": (doc.get("cost_profile_ref") or {}).get("scenario_set_sha256"),
            "temporal_policy_sha256": (doc.get("signal_time_rule") or {}).get("policy_sha256"),
            "research_window": _window(doc.get("research_window"))}


def compare(expected: dict, actual: dict, *, source: str, keys=IDENTITY_KEYS) -> list[str]:
    """Every key in ``keys`` must be present on both sides and equal (windows compared as instants)."""
    bad = []
    for k in keys:
        e, a = expected.get(k), actual.get(k)
        if e in (None, "", "PENDING") or a in (None, "", "PENDING"):
            bad.append(f"IDENTITY_UNVERIFIABLE:{source}:{k}")
        elif k == "research_window":
            if not same_window(e, a):
                bad.append(f"IDENTITY_MISMATCH:{source}:{k}")
        elif e != a:
            bad.append(f"IDENTITY_MISMATCH:{source}:{k}")
    return bad


def policy_problems(doc: dict) -> list[str]:
    bad = []
    if doc.get("execution_mode") != "RESEARCH_ONLY_REAL":
        bad.append("EXECUTION_MODE_NOT_RESEARCH_ONLY_REAL")
    if (doc.get("holdout_policy") or {}).get("access") != "NO_ACCESS":
        bad.append("HOLDOUT_ACCESS_REQUESTED")
    if (doc.get("signal_time_rule") or {}).get("signal_time") != "CORE_MODELED_AVAILABLE_AT" or \
            (doc.get("execution_time_rule") or {}).get("rule") != "NEXT_ELIGIBLE_BAR_OPEN":
        bad.append("PIT_RULE_MISMATCH")
    return bad


def authorization_problems(doc: dict, auth: dict) -> list[str]:
    """Request scope vs the authoritative authorization record (never the other way round)."""
    bad = []
    s, d, c = auth.get("strategy") or {}, auth.get("dataset") or {}, auth.get("costs") or {}
    sf = doc.get("strategy_family") or {}
    checks = {
        "strategy_spec_sha256": (s.get("spec_file_sha256"), doc.get("strategy_spec_sha256")),
        "strategy_spec_ref": (s.get("spec_path"), doc.get("strategy_spec_ref")),
        "strategy_id": (s.get("strategy_id"), sf.get("strategy_id")),
        "strategy_version": (s.get("version"), sf.get("version")),
        "strategy_parameters": (s.get("parameters"), sf.get("parameters")),
        "dataset_snapshot_id": (d.get("snapshot_id"), doc.get("dataset_snapshot_id")),
        "dataset_id": (d.get("dataset_id"), (doc.get("dataset_ref") or {}).get("dataset_id")),
        "dataset_producer_commit": (d.get("producer_commit_sha"), (doc.get("dataset_ref") or {}).get("producer_commit_sha")),
        "cost_profile_sha256": (c.get("scenario_set_sha256"), (doc.get("cost_profile_ref") or {}).get("scenario_set_sha256")),
        "temporal_policy_sha256": ((auth.get("temporal_policy") or {}).get("sha256"),
                                   (doc.get("signal_time_rule") or {}).get("policy_sha256")),
        "holdout_lock_id": ((auth.get("holdout") or {}).get("lock_id"), (doc.get("holdout_policy") or {}).get("lock_id")),
    }
    for k, (want, got) in checks.items():
        if want in (None, "") or want != got:
            bad.append(f"AUTHORIZATION_SCOPE_MISMATCH:{k}")
    rw, req = auth.get("research_window") or {}, _window(doc.get("research_window")) or {}
    lo, hi = _inst(rw.get("start")), _inst(rw.get("end"))
    pts = [_inst(req.get(k)) for k in WINDOW_KEYS]
    if lo is None or hi is None or any(p is None for p in pts) or not (lo <= pts[0] <= pts[1] < pts[2] <= hi):
        bad.append("AUTHORIZATION_SCOPE_MISMATCH:research_window")
    return bad


def artefact_problems(doc: dict, artifact_root: Path) -> list[str]:
    root, bad = Path(artifact_root), []
    spec = doc.get("strategy_spec_ref") or ""
    if not spec or _sha_file(root / spec) != doc.get("strategy_spec_sha256"):
        bad.append("ARTEFACT_SHA_MISMATCH:strategy_spec")
    cost = (doc.get("cost_profile_ref") or {}).get("scenario_set_path") or ""
    if not cost or _sha_file(root / cost) != (doc.get("cost_profile_ref") or {}).get("scenario_set_sha256"):
        bad.append("ARTEFACT_SHA_MISMATCH:cost_profile")
    return bad


def binding_problems(doc: dict, governance_root: Path) -> tuple[list[str], dict | None]:
    """The request must be EXACTLY a registered bound request (record + intact stored copy + evidence file)."""
    from strategy_agent.binding.store import BindingStore  # noqa: PLC0415
    store = BindingStore(governance_root)
    ok, info = store.verify_bound(doc, require_production=False)
    if not ok:
        return [f"BINDING_INVALID:{p}" for p in (info.get("problems") or [info.get("error", "unbound")])], None
    from strategy_agent.orchestration.request import request_hash  # noqa: PLC0415
    return [], store.for_bound(request_hash(doc))


def load_authorization(governance_root: Path, authorization_id: str, now: datetime) -> tuple[dict | None, list[str]]:
    from strategy_agent.governance.research_authorization import (AuthorizationBookInvalid,  # noqa: PLC0415
                                                                  ResearchAuthorizationBook)
    g = Path(governance_root) / "governance"
    try:
        a = ResearchAuthorizationBook(g / "research_authorizations.jsonl", g / "approvers.json").get(authorization_id)
    except AuthorizationBookInvalid as e:
        return None, [f"AUTHORIZATION_BOOK_INVALID:{e}"]
    if a is None:
        return None, ["AUTHORIZATION_NOT_ACTIVE"]
    exp = a.get("expires_at")
    if exp and datetime.fromisoformat(exp) <= now:
        return None, ["AUTHORIZATION_EXPIRED"]
    return a, []


def verify_request(doc: dict, *, governance_root: Path, artifact_root: Path, backend, now: datetime) -> dict:
    """Full identity verification against authoritative sources. Returns {problems, binding, authorization, identity}."""
    bad = list(policy_problems(doc))
    bp, binding = binding_problems(doc, governance_root)
    bad += bp
    rid = request_identity(doc)
    auth, ap = load_authorization(governance_root, rid["authorization_id"] or "", now)
    bad += ap
    if auth is not None:
        bad += authorization_problems(doc, auth)
        if binding is not None and binding.get("authorization_id") != auth.get("authorization_id"):
            bad.append("BINDING_AUTHORIZATION_MISMATCH")
    bad += artefact_problems(doc, artifact_root)
    try:
        bid, bprob = backend.execution_identity()
    except Exception as e:  # noqa: BLE001 - an unverifiable backend is a refusal
        bid, bprob = {}, [f"BACKEND_IDENTITY_UNAVAILABLE:{type(e).__name__}:{e}"]
    bad += [f"BACKEND:{p}" for p in bprob]
    bad += compare(rid, bid, source="BACKEND")
    return {"problems": sorted(set(bad)), "binding": binding, "authorization": auth, "identity": rid,
            "backend_identity": bid}


# ----------------------------------------------------------------------------- produced archive (P0-2 / P0-3)
def raw_run_info(data: bytes) -> dict:
    """run_info.txt as RAW strings (a digits-only sha must never become an int); authorization_use parsed as JSON."""
    out = {}
    for line in data.decode("utf-8").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            try:                                      # a JSON-quoted string value is unquoted; nothing else is coerced
                j = json.loads(v)
                v = j if isinstance(j, str) else v
            except json.JSONDecodeError:
                pass
            out.setdefault(k, v)
    try:
        out["authorization_use"] = json.loads(out.get("authorization_use", "null"))
    except json.JSONDecodeError:
        out["authorization_use"] = None
    return out


def archive_problems(archive: dict, *, doc: dict, use: dict, run_id: str) -> list[str]:
    """The produced archive (already hash-verified by phase4c.read_archive) must carry THIS request's identity and the
    authoritative uses record it was granted; a runner result for anything else is never settled."""
    bad = []
    files = archive["files"]
    info = raw_run_info(files["run_info.txt"])
    cfg = json.loads(files["config_canonical.json"])
    rid = request_identity(doc)
    st = cfg.get("strategy") or {}
    got = {"authorization_id": info.get("authorization_id"), "strategy_id": st.get("strategy_id"),
           "strategy_version": st.get("version"), "strategy_parameters": st.get("parameters"),
           "strategy_spec_sha256": st.get("spec_file_sha256"), "core_commit": info.get("core_git_sha"),
           "dataset_snapshot_id": info.get("snapshot_id"), "dataset_manifest_sha256": info.get("manifest_sha256"),
           "cost_profile_sha256": (cfg.get("costs") or {}).get("scenario_set_sha256"),
           "temporal_policy_sha256": info.get("temporal_policy_sha256"), "research_window": cfg.get("windows")}
    bad += compare(rid, got, source="ARCHIVE")
    au = info.get("authorization_use") if isinstance(info.get("authorization_use"), dict) else {}
    if au.get("record_sha256") != use.get("record_sha256") or archive["manifest"].get("run_id") != run_id \
            or info.get("run_id") != run_id:
        bad.append("ARCHIVE_USE_RECORD_MISMATCH")
    if str(info.get("holdout_accessed")).lower() != "false":
        bad.append("ARCHIVE_HOLDOUT_NOT_PROVEN_UNTOUCHED")
    return bad


__all__ = ["IDENTITY_KEYS", "request_identity", "compare", "verify_request", "archive_problems", "same_window",
           "authorization_problems", "artefact_problems", "policy_problems", "raw_run_info"]
