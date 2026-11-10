"""Phase 5-B3-C PREPARATION: authorization draft, run-config derivation, proposal check, non-consuming preflight.

Nothing here creates, approves, binds or consumes anything:
  * the draft is written only to ``docs/phase5b3c/`` (never ``governance/``), status DRAFT_NOT_APPROVED;
  * every value the research lead must decide is ``HUMAN_DECISION_REQUIRED`` (HDR), never a default;
  * ``derive_run_config`` returns a dict in memory (the canonical B3-C config is committed only AFTER the formal
    authorization exists, in the research lead's own PR);
  * ``preflight`` reports B3C-01..10. Gates that need an approved authorization, a GitHub approval, market data,
    budget or the real Runner are ``NOT_EXECUTED``; the non-consuming checks that DID run are listed separately as
    ``prepared_checks``. ``real_research_pass`` is always False here: a synthetic / prepared PASS is never a
    real-research PASS.

  python -m strategy_agent.readiness.b3c emit-docs
  python -m strategy_agent.readiness.b3c preflight [--out FILE]
  python -m strategy_agent.readiness.b3c propose --issued-at <ISO+offset> --out governance/proposals/<ID>.json
  python -m strategy_agent.readiness.b3c check-proposal --proposal P.json     (research lead, before admin approve)
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import authorization_draft as D
from . import manifest as M
from .candidate import CANDIDATE, ROOT, path

HDR = D.HDR
TPE = ZoneInfo("Asia/Taipei")
DOCS = ROOT / "docs/phase5b3c"
CANDIDATE_AUTHORIZATION_ID = "RA-IBKR_TSM-SMA520_P5B3C-0001"
PROPOSED_EXECUTOR = "colab-hh4832-exec-01"
PROPOSED_MAX_BATCHES = 1
PROPOSED_VALIDITY_DAYS = 7
SPENT_DEV = CANDIDATE["template_authorization_id"]
RUN_CONFIG_ID = "tsm_sma_5_20_p5b3c_2024_06_dev_18350b3adba8e28a7e5e"
B3C_RUN_CONFIG_PATH = f"configs/research_only/{RUN_CONFIG_ID}.json"
AUTH_ID_RE = re.compile(r"^(?:RA-[A-Z0-9_]+-[A-Z0-9_]+-\d{4}|RA-IBKR_TSM-SMA520_DEV-[0-9a-f]{20})$")
EXECUTOR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,63}$")

GATES = {
    "B3C-01": "Formal Research Authorization (APPROVE, active, unexpired, named executor, max_batches)",
    "B3C-02": "GitHub approval evidence bound to the Research Request hash",
    "B3C-03": "Dataset / Core / Spec pins identical across authorization, request, run config and disk",
    "B3C-04": "PIT / Holdout exclusion (window ends before HOLDOUT-TSM-15M-v1; no holdout read)",
    "B3C-05": "Single executor and remaining budget (exactly one batch, atomic reservation)",
    "B3C-06": "Real ResearchOnlyRunner execution through the REAL_GATED controlled path",
    "B3C-07": "Execution receipt (Core integrity receipt, runner receipt, contract) consistent with the result",
    "B3C-08": "Evaluator and cost-scenario results (4 IBKR_PRO_FIXED scenarios, sample rule)",
    "B3C-09": "Archive integrity and reproducibility (immutable archive, hash index, Colab + Drive)",
    "B3C-10": "Formal ledger settlement (CONSUMED, uses log) and research-result acceptance by the research lead",
}
REAL_ONLY = {"B3C-02", "B3C-06", "B3C-07", "B3C-08", "B3C-09", "B3C-10"}

APPROVAL_REQUIREMENTS = [
    "Research lead (allow-listed in governance/approvers.json) fills every HUMAN_DECISION_REQUIRED value and runs "
    "`python -m strategy_agent.readiness.b3c check-proposal` until it reports no problem",
    "Proposal saved as governance/proposals/<authorization_id>.json in a SEPARATE authorization PR (not this one)",
    "`python -m strategy_agent.governance.research_authorization_admin approve --proposal ... --approved-by hh4832 "
    "--approval-ref <PR/comment URL>` run BY THE RESEARCH LEAD; the line takes effect only once that PR is merged",
    "executor.executor_id must name exactly ONE runtime (SINGLE_EXECUTOR_ONLY); the B3-C notebook refuses any other",
    "max_batches is the whole budget of the authorization; it is never reset and the ID is never reused",
    "expires_at must be after issued_at, short (proposed 7 days), and before any change of the pinned formal gates",
    "No agent creates, approves, signs, posts or merges any of the above",
]


def _sha_file(p: Path) -> str | None:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def _dumps(o) -> bytes:
    return (json.dumps(o, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n").encode()


def _book_lines(key: str) -> list[dict]:
    p = path(key)
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.is_file() else []


# ---------------------------------------------------------------- authorization draft
def binding_procedure(authorization_id: str = CANDIDATE_AUTHORIZATION_ID) -> list[dict]:
    """The ordered human + tool steps from formal authorization to a bound, executable request (documentation only)."""
    a = authorization_id
    return [
        {"step": 1, "who": "research lead", "what": "Create the formal authorization (separate PR, admin tool)",
         "command": f"python -m strategy_agent.governance.research_authorization_admin approve --proposal "
                    f"governance/proposals/{a}.json --approved-by hh4832 --approval-ref <URL>",
         "produces": "one APPROVE line in governance/research_authorizations.jsonl (after merge)"},
        {"step": 2, "who": "research lead", "what": "Commit the canonical B3-C run config derived for this ID",
         "command": f"python -m strategy_agent.readiness.b3c derive-run-config --authorization-id {a} "
                    f"--out {B3C_RUN_CONFIG_PATH}", "produces": "canonical run config (self-hash verified)"},
        {"step": 3, "who": "notebook (B3-C cell)", "what": "Generate the Research Request from the A2 proposal",
         "command": "python -m strategy_agent.proposal export-request --proposal P.json --output R.json --bindings B.json",
         "produces": "R.json + R_provenance.json"},
        {"step": 4, "who": "tool (prints only)", "what": "Compute the request hash and the exact approval line",
         "command": f"python -m strategy_agent.binding approval-line --request R.json --intent BIND_AUTHORIZATION "
                    f"--authorization {a}",
         "produces": "RESEARCH-APPROVAL v1 decision=APPROVE intent=BIND_AUTHORIZATION request=<sha256> ..."},
        {"step": 5, "who": "research lead", "what": "Post that line as their OWN GitHub comment (formal evidence)",
         "command": "(GitHub UI; never posted by an agent)", "produces": "comment URL = evidence_ref"},
        {"step": 6, "who": "tool", "what": "Verify the approval evidence against the live GitHub source",
         "command": f"python -m strategy_agent.binding verify-approval --request R.json --intent BIND_AUTHORIZATION "
                    f"--authorization {a} --evidence-ref <URL>", "produces": "VERIFIED or refusal"},
        {"step": 7, "who": "tool", "what": "Bind authorization and request (dry run first, then write)",
         "command": f"python -m strategy_agent.binding bind-authorization --request R.json --provenance "
                    f"R_provenance.json --proposal P.json --authorization-id {a} --evidence-ref <URL> --dry-run  "
                    f"# then --write", "produces": "bound request B.json + binding record"},
        {"step": 8, "who": "tool", "what": "Validate the bound request and inspect the binding",
         "command": "python -m strategy_agent.binding validate-bound-request --request B.json && "
                    "python -m strategy_agent.binding inspect-binding --bound-sha <sha>", "produces": "PASS"},
        {"step": 9, "who": "notebook", "what": "Verify the executor: worker == authorization executor_id == gate",
         "command": "RealActivationGate(executor_id=<executor_id>) A08 + eligible_authorizations()",
         "produces": "authorization listed as eligible for this executor only"},
        {"step": 10, "who": "notebook", "what": "Verify the remaining budget (0 of max_batches used, no reservation)",
         "command": "RealActivationGate A07 + budget ledger state", "produces": "remaining == max_batches"},
        {"step": 11, "who": "research lead", "what": "Approve the TASK (second GitHub comment, intent=APPROVE_TASK)",
         "command": "python -m strategy_agent.binding approval-line --request B.json --intent APPROVE_TASK "
                    f"--authorization {a} --task-id <T>; then Orchestrator.approve(task_id, evidence_ref=<URL>)",
         "produces": "task APPROVED; only now may claim_execution grant the single batch"},
    ]


def build_draft(m: dict | None = None) -> dict:
    m = m or M.build(None)
    base = D.build(m)
    prop = copy.deepcopy(base["proposal_fields"])
    prop["does_not_authorize"] = sorted(set(prop["does_not_authorize"]) | {
        "reading HOLDOUT-TSM-15M-v1 or any data at or after its start",
        "any market, symbol, window, spec, Core or cost profile other than the pinned ones",
        "a second batch, a re-run, or reuse of this authorization ID after it is spent"})
    prop["executor"] = {"executor_id": HDR, "consistency": "SINGLE_EXECUTOR_ONLY"}
    prop.update(authorization_id=HDR, max_batches=HDR, issued_at=HDR, expires_at=HDR)
    t = prop["strategy"]
    d = {k: v for k, v in base.items() if k not in ("kind", "proposal_fields", "authorization_id", "executor_identity",
                                                    "max_research_batches", "validity_period", "warning")}
    d.update({
        "kind": "PHASE5B3C_AUTHORIZATION_DRAFT",
        "status": "DRAFT_NOT_APPROVED",
        "warning": "NOT an authorization, not a proposal file, not in governance/. It grants nothing. Creating it is "
                   "the research lead's own action in a separate PR after every HUMAN_DECISION_REQUIRED is decided.",
        "authorization_id": {"proposed": CANDIDATE_AUTHORIZATION_ID, "decision": HDR,
                             "rule": "new ID; schema pattern RA-<A>-<B>-NNNN; never "
                                     f"{SPENT_DEV} (SPENT 1/1, never reset or reused)"},
        "strategy_specification": {**base["strategy_specification"],
                                   "pinned": {"path": t["spec_path"], "sha256": t["spec_file_sha256"],
                                              "version": t["version"]}},
        "core_commit": m["fields"]["core_commit_sha"]["value"],
        "cost_assumptions": {**base["cost_assumptions"], "profile": "IBKR_PRO_FIXED",
                             "scenarios": "4 scenarios of the pinned scenario set (no other)"},
        "max_research_batches": {"proposed": PROPOSED_MAX_BATCHES, "decision": HDR},
        "validity_period": {"issued_at": HDR, "expires_at": HDR, "proposed_length_days": PROPOSED_VALIDITY_DAYS,
                            "constraint": "issued_at < expires_at; expire before any change of the pinned formal gates"},
        "executor": {"proposed": PROPOSED_EXECUTOR, "executor_id": HDR, "consistency": "SINGLE_EXECUTOR_ONLY",
                     "rule": "one Colab runtime identity; the gate, the orchestrator worker and the authorization "
                             "must all name it"},
        "approval_requirements": APPROVAL_REQUIREMENTS,
        "binding_procedure": binding_procedure(),
        "archive_location": {"drive_root": m["fields"]["archive_destination"]["value"],
                             "run_id_format": "YYYYMMDD_HHMMSS_<git_commit>",
                             "evidence_in_repo": "docs/evidence/phase5b3c/<run_id>/", "immutable": True},
        "run_config": {"path_after_authorization": B3C_RUN_CONFIG_PATH, "run_config_id": RUN_CONFIG_ID,
                       "derived_from": CANDIDATE["run_config"],
                       "changes": ["authorization_id", "run_config_id", "strategy.spec_path/spec_file_sha256/version "
                                   "-> spec 0.1.1", "purpose", "provenance (config_sha256 recomputed)"]},
        "proposal_fields": prop,
    })
    return d


def schema_dry_run(draft: dict) -> dict:
    """In memory only, fake approval values (never an approver, never written): proves the shape is admissible."""
    ev = copy.deepcopy(draft["proposal_fields"])
    ev["executor"]["executor_id"] = "dry-run-executor-not-real"
    return D.schema_dry_run({"proposal_fields": ev})


def proposal_problems(p: dict) -> list[str]:
    """For the research lead before ``research_authorization_admin approve``: any leftover HDR, wrong pin or reuse."""
    bad = []
    blob = json.dumps(p)
    if HDR in blob:
        bad.append("HUMAN_DECISION_REQUIRED_LEFT")
    aid = p.get("authorization_id")
    if not isinstance(aid, str) or not AUTH_ID_RE.match(aid):
        bad.append("AUTHORIZATION_ID_PATTERN")
    if aid == SPENT_DEV or aid in {x.get("authorization_id") for x in _book_lines("authorization_book")}:
        bad.append("AUTHORIZATION_ID_ALREADY_USED")
    ex = (p.get("executor") or {}).get("executor_id")
    if not isinstance(ex, str) or not EXECUTOR_RE.match(ex) or ex == HDR:
        bad.append("EXECUTOR_ID")
    if (p.get("executor") or {}).get("consistency") != "SINGLE_EXECUTOR_ONLY":
        bad.append("EXECUTOR_CONSISTENCY")
    if p.get("max_batches") != PROPOSED_MAX_BATCHES:
        bad.append("MAX_BATCHES_NOT_1")
    st = p.get("strategy") or {}
    if st.get("spec_path") != CANDIDATE["strategy_spec"] or st.get("spec_file_sha256") != CANDIDATE["strategy_spec_sha256"] \
            or _sha_file(ROOT / CANDIDATE["strategy_spec"]) != CANDIDATE["strategy_spec_sha256"]:
        bad.append("SPEC_PIN")
    try:
        i, e = datetime.fromisoformat(p["issued_at"]), datetime.fromisoformat(p["expires_at"])
        if i.tzinfo is None or e.tzinfo is None or e <= i:
            bad.append("VALIDITY_PERIOD")
    except Exception:  # noqa: BLE001
        bad.append("VALIDITY_PERIOD")
    try:   # RA10: the pinned formal gates must equal today's market readiness, or the authorization is void at once
        from strategy_agent.governance.research_authorization import gates_sha256  # noqa: PLC0415
        mk = json.loads(path("market_readiness").read_text(encoding="utf-8"))["markets"][p.get("market_id")]
        pin = p.get("formal_gate_pin") or {}
        if (mk.get("status"), mk.get("gate_version"), gates_sha256(mk.get("gates", {}))) != \
                (pin.get("market_status"), pin.get("gate_version"), pin.get("gates_sha256")):
            bad.append("RA10_FORMAL_GATES_CHANGED")
    except Exception:  # noqa: BLE001
        bad.append("RA10_FORMAL_GATES_UNREADABLE")
    ref = build_draft()["proposal_fields"]
    for k in ("market_id", "symbol", "dataset", "temporal_policy", "costs", "research_window", "holdout",
              "formal_gate_pin", "scope", "contract"):
        if p.get(k) != ref.get(k):
            bad.append(f"PIN_CHANGED:{k}")
    return bad


# ---------------------------------------------------------------- candidate proposal (for the research lead)
ILLUSTRATIVE_ISSUED_AT = "2026-11-11T09:00:00+08:00"   # docs example only; the real value is set at approval
PROPOSAL_ONLY_FIELDS = ("proposal_status", "proposed_by", "proposed_at", "approval_instructions")
PURPOSE = "PIPELINE_ACCEPTANCE_ONLY"
VALIDITY_RULE = f"issued_at = the research lead's formal approval time; expires_at = issued_at + {PROPOSED_VALIDITY_DAYS} days"


def build_proposal(*, authorization_id: str = CANDIDATE_AUTHORIZATION_ID, executor_id: str = PROPOSED_EXECUTOR,
                   issued_at: str | None = None, m: dict | None = None) -> dict:
    """The research lead's CANDIDATE proposal (governance/proposals format). Never an APPROVE: no action, approver,
    approval_ref or approved_at. Without ``issued_at`` the validity stays HUMAN_DECISION_REQUIRED (it can only be
    fixed at the moment of formal approval)."""
    from datetime import timedelta  # noqa: PLC0415
    p = copy.deepcopy(build_draft(m)["proposal_fields"])
    if issued_at is None:
        iss = exp = HDR
    else:
        i = datetime.fromisoformat(issued_at)
        if i.tzinfo is None:
            raise ValueError("issued_at needs an explicit UTC offset")
        iss, exp = i.isoformat(timespec="seconds"), (i + timedelta(days=PROPOSED_VALIDITY_DAYS)).isoformat(timespec="seconds")
    p.update(authorization_id=authorization_id, max_batches=PROPOSED_MAX_BATCHES, issued_at=iss, expires_at=exp)
    p["executor"] = {"executor_id": executor_id, "consistency": "SINGLE_EXECUTOR_ONLY"}
    p["notes"] = (f"Purpose {PURPOSE}: one governed Phase 5 controlled execution of the pre-registered SMA 5/20 "
                  f"baseline on the validated DEV snapshot (window 2024-05-29..2024-06-28 ET), to accept the pipeline. "
                  f"NOT new statistical evidence about SMA 5/20. Single executor {executor_id}; max_batches 1; "
                  f"{VALIDITY_RULE}. Holdout HOLDOUT-TSM-15M-v1 NO_ACCESS. {SPENT_DEV} stays spent and is not reused.")
    return {"proposal_status": "CANDIDATE_UNSIGNED; research lead must personally decide and approve",
            "proposed_by": "strategy-research-agent-b3c-proposal-only",
            "proposed_at": "2026-11-10T00:00:00+08:00",
            "approval_instructions": "see docs/PHASE5B3C_AUTHORIZATION_PROPOSAL.md",
            **p}


def schema_check(proposal: dict) -> dict:
    """Real schema + book validation IN MEMORY with clearly fake approval fields; nothing is written."""
    from strategy_agent.governance.research_authorization import validate_events  # noqa: PLC0415
    if HDR in json.dumps(proposal):
        return {"status": "NOT_RUN", "why": "HUMAN_DECISION_REQUIRED values left", "written": False}
    ev = {k: v for k, v in proposal.items() if k not in PROPOSAL_ONLY_FIELDS}
    ev.update(action="APPROVE", approved_by="dry-run-not-an-approver",
              approval_ref="https://example.invalid/DRY_RUN_NOT_AN_APPROVAL", approved_at=ev["issued_at"])
    try:
        validate_events([ev], {"dry-run-not-an-approver"})
        return {"status": "PASS", "written": False}
    except Exception as e:  # noqa: BLE001
        return {"status": "FAIL", "error": f"{type(e).__name__}: {e}"[:600], "written": False}


def check(proposal: dict) -> dict:
    bad = proposal_problems({k: v for k, v in proposal.items() if k not in PROPOSAL_ONLY_FIELDS})
    return {"problems": bad, "schema": schema_check(proposal), "ok": not bad and schema_check(proposal)["status"] == "PASS"}


# ---------------------------------------------------------------- run config (in memory)
def derive_run_config(authorization_id: str) -> dict:
    """The B3-C canonical run config for a formal authorization ID, derived from the DEV config. In memory only."""
    from strategy_agent.research_only.development_run import canonical_sha  # noqa: PLC0415
    if not AUTH_ID_RE.match(authorization_id or "") or authorization_id == SPENT_DEV:
        raise ValueError(f"refused authorization id {authorization_id!r}")
    cfg = json.loads(path("run_config").read_text(encoding="utf-8"))
    spec = json.loads((ROOT / CANDIDATE["strategy_spec"]).read_text(encoding="utf-8"))
    cfg["authorization_id"] = authorization_id
    cfg["run_config_id"] = RUN_CONFIG_ID
    cfg["strategy"].update(spec_path=CANDIDATE["strategy_spec"], spec_file_sha256=CANDIDATE["strategy_spec_sha256"],
                           version=spec["version"])
    cfg["purpose"] = ("Phase 5-B3-C first real research execution through the governed Phase 5 controlled path; "
                      "pipeline evidence on the already-evaluated DEV window, NOT new evidence that SMA works")
    cfg["provenance"] = {"created_by": "strategy-research-agent-b3c-derivation",
                         "derived_from": CANDIDATE["run_config"],
                         "supersedes": "none; the DEV config and its spent authorization stay unchanged",
                         "config_sha256": None}
    cfg["provenance"]["config_sha256"] = canonical_sha(cfg)
    return cfg


# ---------------------------------------------------------------- preflight (non-consuming)
def _prepared(m: dict, draft: dict) -> dict:
    from strategy_agent.execution.activation import eligible_authorizations  # noqa: PLC0415
    from strategy_agent.orchestration import governance as GV  # noqa: PLC0415
    F = m["fields"]
    book = _book_lines("authorization_book")
    uses = [u for u in _book_lines("uses_log") if u.get("authorization_id") == SPENT_DEV]
    locks = [x for x in _book_lines("holdout_book") if x.get("lock_id") == "HOLDOUT-TSM-15M-v1"]
    approved = [x for x in book if x.get("authorization_id") == CANDIDATE_AUTHORIZATION_ID]
    try:
        cfg = derive_run_config(CANDIDATE_AUTHORIZATION_ID)
        from strategy_agent.research_only.development_run import canonical_sha  # noqa: PLC0415
        cfg_ok = cfg["provenance"]["config_sha256"] == canonical_sha(cfg) and cfg["strategy"]["spec_file_sha256"] == \
            CANDIDATE["strategy_spec_sha256"] and cfg["dataset"]["snapshot_id"] == F["dataset_snapshot_id"]["value"]
    except Exception as e:  # noqa: BLE001
        cfg_ok = f"{type(e).__name__}: {e}"
    elig = eligible_authorizations(ROOT, PROPOSED_EXECUTOR, datetime.now(TPE))
    return {
        "B3C-01": {"draft_schema_dry_run": schema_dry_run(draft)["status"],
                   "candidate_id_unused_in_book": not approved, "draft_written_to_governance": False},
        "B3C-02": {"binding_cli_present": (ROOT / "src/strategy_agent/binding/__main__.py").is_file(),
                   "binding_records_for_candidate": 0, "binding_procedure_steps": len(draft["binding_procedure"])},
        "B3C-03": {"manifest_fields_resolved": all(F[k]["status"] == "RESOLVED" for k in (
                       "dataset_snapshot_id", "dataset_manifest_sha256", "core_commit_sha", "strategy_spec_sha256",
                       "trading_cost_profile", "research_window")),
                   "cross_checks": {k: v["status"] for k, v in m["cross_checks"].items()},
                   "spec_sha_on_disk": _sha_file(ROOT / CANDIDATE["strategy_spec"]),
                   "derived_run_config_self_hash_and_pins": cfg_ok},
        "B3C-04": {"holdout_lock_present": bool(locks),
                   "window_end_before_holdout": (F["holdout_exclusion"]["value"] or {}).get("window_end_before_holdout"),
                   "holdout_read": False},
        "B3C-05": {"spent_dev_authorization_uses": len(uses), "eligible_authorizations_today": elig,
                   "budget_ledger_exists": (ROOT / "registry/research_budget_events.jsonl").exists()},
        "B3C-06": {"REAL_EXECUTION_ENABLED": GV.REAL_EXECUTION_ENABLED, "real_runner": "DISABLED" if not elig else "GATED"},
    }


def preflight(m: dict | None = None) -> dict:
    m = m or M.build(None)
    draft = build_draft(m)
    P = _prepared(m, draft)
    sane = {
        "B3C-01": P["B3C-01"]["draft_schema_dry_run"] == "PASS" and P["B3C-01"]["candidate_id_unused_in_book"],
        "B3C-02": P["B3C-02"]["binding_cli_present"],
        "B3C-03": P["B3C-03"]["manifest_fields_resolved"] and set(P["B3C-03"]["cross_checks"].values()) == {"PASS"}
        and P["B3C-03"]["spec_sha_on_disk"] == CANDIDATE["strategy_spec_sha256"]
        and P["B3C-03"]["derived_run_config_self_hash_and_pins"] is True,
        "B3C-04": P["B3C-04"]["holdout_lock_present"] and P["B3C-04"]["window_end_before_holdout"] is True,
        "B3C-05": P["B3C-05"]["spent_dev_authorization_uses"] == 1 and not P["B3C-05"]["eligible_authorizations_today"]
        and not P["B3C-05"]["budget_ledger_exists"],
        "B3C-06": P["B3C-06"]["REAL_EXECUTION_ENABLED"] is False and P["B3C-06"]["real_runner"] == "DISABLED",
    }
    gates = {}
    for gid, req in GATES.items():
        prep = P.get(gid)
        gates[gid] = {"requirement": req, "status": "NOT_EXECUTED",
                      "prepared_checks": prep or "none possible without a real execution",
                      "prepared_status": ("PREPARED" if sane[gid] else "PREPARATION_PROBLEM") if gid in sane
                      else "NOT_APPLICABLE_BEFORE_EXECUTION",
                      "needs": "formal authorization + GitHub approval + real execution" if gid in REAL_ONLY
                      else "formal authorization"}
    problems = [k for k, v in gates.items() if v["prepared_status"] == "PREPARATION_PROBLEM"]
    return {"kind": "PHASE5B3C_PREFLIGHT", "gates": gates, "preparation_problems": problems,
            "preparation": "READY_FOR_HUMAN_DECISION" if not problems else "PREPARATION_PROBLEM",
            "real_execution": "NOT_PERFORMED", "research_authorization": "NOT_GRANTED" if not P["B3C-05"][
                "eligible_authorizations_today"] else "GRANTED", "real_research_pass": False,
            "meaning": "preparation only; every B3C gate is NOT_EXECUTED until the research lead authorizes and runs B3-C"}


def emit_docs() -> list[str]:
    m = M.build(None)
    draft = build_draft(m)
    DOCS.mkdir(parents=True, exist_ok=True)
    prop = build_proposal(m=m)
    illus = build_proposal(m=m, issued_at=ILLUSTRATIVE_ISSUED_AT)
    out = {"authorization_draft.json": draft, "authorization_draft_schema_dry_run.json": schema_dry_run(draft),
           f"authorization_proposal_{CANDIDATE_AUTHORIZATION_ID}.json": prop,
           "authorization_proposal_check.json": {
               "as_proposed": check(prop),
               "illustrative_validity": {"issued_at": ILLUSTRATIVE_ISSUED_AT, "note": "example time only, NOT a decision",
                                         **check(illus)}}}
    for n, o in out.items():
        (DOCS / n).write_bytes(_dumps(o))
    return sorted(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m strategy_agent.readiness.b3c")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("emit-docs")
    p = sub.add_parser("preflight"); p.add_argument("--out", type=Path)
    c = sub.add_parser("check-proposal"); c.add_argument("--proposal", type=Path, required=True)
    q = sub.add_parser("propose"); q.add_argument("--authorization-id", default=CANDIDATE_AUTHORIZATION_ID)
    q.add_argument("--executor-id", default=PROPOSED_EXECUTOR); q.add_argument("--issued-at", required=True)
    q.add_argument("--out", type=Path, required=True)
    r = sub.add_parser("derive-run-config"); r.add_argument("--authorization-id", required=True)
    r.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    if a.cmd == "emit-docs":
        print(json.dumps(emit_docs()))
        return 0
    if a.cmd == "preflight":
        res = preflight()
        b = _dumps(res)
        if a.out:
            with open(a.out, "xb") as f:   # O_EXCL: never overwrite evidence
                f.write(b)
        sys.stdout.write(b.decode())
        return 0 if res["preparation"] == "READY_FOR_HUMAN_DECISION" else 1
    if a.cmd == "check-proposal":
        res = check(json.loads(a.proposal.read_text(encoding="utf-8")))
        print(json.dumps(res, indent=2))
        return 0 if res["ok"] else 1
    if a.cmd == "propose":   # writes a CANDIDATE proposal file only (O_EXCL); never the book, never an APPROVE
        prop = build_proposal(authorization_id=a.authorization_id, executor_id=a.executor_id, issued_at=a.issued_at)
        with open(a.out, "xb") as f:
            f.write(_dumps(prop))
        print(json.dumps(check(prop), indent=2))
        return 0
    # derive-run-config: refuses unless the ID is a formal, active APPROVE in the merged book
    ids = {x.get("authorization_id") for x in _book_lines("authorization_book") if x.get("action") == "APPROVE"}
    if a.authorization_id not in ids:
        print(json.dumps({"refused": "AUTHORIZATION_NOT_IN_BOOK", "authorization_id": a.authorization_id}))
        return 2
    with open(a.out, "xb") as f:
        f.write(_dumps(derive_run_config(a.authorization_id)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
