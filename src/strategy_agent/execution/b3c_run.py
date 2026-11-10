"""Phase 5-B3-C: the ONE governed real execution of RA-IBKR_TSM-SMA520_P5B3C-0001, in two runs separated by the
research lead's own GitHub comments. No new execution logic: every step is an existing Phase 5 component.

Run 1  ``prepare``  (non-consuming; writes only to a new directory on the executor's Drive)
       committed completion -> A2 ``draft_proposal`` -> A2 exporter -> PENDING source request.
       The bound request (source + authorization_ref) and its task id are deterministic, so BOTH approval lines are
       printed now: BIND_AUTHORIZATION (source request hash) and APPROVE_TASK (bound request hash + task id).
       The research lead checks the hashes and posts each line as their OWN comment.
Run 2  ``execute``  (consumes the single batch, once)
       1. the prepared files are re-verified byte-for-byte against the run-1 manifest;
       2. a durable CLAIM marker on the executor's Drive must NOT exist (cross-runtime guard: a second Colab runtime
          cannot see an un-pushed ledger, so any earlier attempt => RECONCILIATION_REQUIRED, never a re-run);
       3. read-only: binding dry run (B01-B09 incl. live verification of the BIND comment), live verification of the
          APPROVE_TASK comment, runner probe, request identity == runner identity;
       4. binding written (AuthorizationBindingService), task submitted, approved with the verified comment;
       5. CLAIM marker written (O_EXCL) right before ``Orchestrator.dispatch``: REAL_GATED activation gate A01-A10 ->
          atomic reservation -> commit -> ResearchOnlyRunner -> evidence verification -> evaluation -> archive ->
          settlement (CONSUMED) -> execution record.
       No retry: any failure after the commit leaves RECONCILIATION_REQUIRED (the batch stays consumed).

Nothing here creates or approves an authorization, posts a comment, reads the Holdout or calls a broker.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
TPE = ZoneInfo("Asia/Taipei")
AUTHORIZATION_ID = "RA-IBKR_TSM-SMA520_P5B3C-0001"
EXECUTOR = "colab-hh4832-exec-01"
COMPLETION_PATH = "specs/research/b3c/tsm_sma520_p5b3c_completion.json"
ADAPTER_ID = "CLAUDE_AUTHORED_B3C_COMPLETION_FILE"
HYPOTHESIS = ("Phase 5-B3-C: run the pre-registered SMA 5/20 IBKR TSM 15m baseline once, unchanged, on the validated "
              "DEV snapshot through the governed Phase 5 controlled path (PIPELINE_ACCEPTANCE_ONLY; no new statistical "
              "evidence about SMA 5/20).")
MANIFEST = "PREPARE_MANIFEST.json"
FILES = ("completion.json", "proposal.json", "request.json", "request_provenance.json")
CLAIM = "CLAIM.json"
APPROVAL_HOURS = 48


class B3CStopped(RuntimeError):
    """Stopped before or without consuming anything (unless ``consumed`` says otherwise)."""

    def __init__(self, code: str, detail=None, *, consumed: bool = False):
        super().__init__(f"{code}: {json.dumps(detail, default=str)[:800] if detail is not None else ''}")
        self.code, self.detail, self.consumed = code, detail, consumed


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _dumps(o) -> bytes:
    return (json.dumps(o, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n").encode()


def _write_once(p: Path, b: bytes) -> str:
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "xb") as fh:                                   # O_EXCL: never overwrite
        fh.write(b)
    return _sha(b)


def _z(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------------------------------- the proposal (A2)
def build_completion(spec: dict, *, challenger_view: dict) -> dict:
    """The structured draft the A2 proposal agent validates. Authored (not model-generated) and committed for review:
    every strategy field is copied from the pre-registered spec; the ONE declared change is the execution path."""
    u = {k: spec["universe"][k] for k in ("market", "symbols", "timeframe")}
    ps = spec["position_sizing"]
    return {
        "title": "Phase 5-B3-C pipeline acceptance: pre-registered SMA 5/20 baseline, IBKR TSM 15m, DEV window",
        "hypothesis": {
            "question": "Does the governed Phase 5 pipeline run the pre-registered SMA 5/20 TSM baseline once, end to end, "
                        "on the validated real DEV snapshot with consistent, archived and settled evidence?",
            "testable_claim": "One governed execution yields an evidence-verified, settled and reproducible archive; no "
                              "performance claim is made or tested.",
            "null_hypothesis": "The governed pipeline fails a governance, identity, archive or settlement check on real "
                               "historical data.",
            "expected_direction": "CONDITIONAL",
            "purpose": "PIPELINE_ACCEPTANCE_ONLY. Not new statistical evidence about SMA 5/20 (the DEV window was "
                       "already evaluated)."},
        "market_mechanism": {"category": "MOMENTUM", "causal_logic": spec["hypothesis"]["mechanism"],
                             "failure_modes": ["Range-bound sessions and whipsaw.", "Transaction costs and overnight gaps.",
                                               "Sample far below the pre-registered minimum trade count on a one-month "
                                               "window."]},
        "strategy_rules": {
            "entry_condition": "Enter long when the completed-bar SMA5 crosses above SMA20 while flat (spec v0.1.1).",
            "exit_condition": "Exit long when the completed-bar SMA5 crosses below SMA20 (spec v0.1.1).",
            "position_sizing": f"FIXED_UNITS {ps['units']} shares; initial capital {ps['initial_capital']} USD.",
            "exit_policy": "Exits exactly as pre-registered in spec v0.1.1; no parameter or rule change.",
            "holding_horizon": "Hours to several sessions.", "signal_time": "BAR_AVAILABLE_AT",
            "execution_time": "STRICTLY_AFTER_SIGNAL_AVAILABILITY", "universe": u},
        "required_data": {**u, "price_fields": ["open", "high", "low", "close", "volume"], "additional_features": [],
                          "coverage": {"start": "2024-05-29", "end": "2024-06-28"},
                          "pit_requirements": ["Use only fully completed bars and original timestamps.",
                                               "Never access Holdout HOLDOUT-TSM-15M-v1; Development snapshot only."],
                          "pit_timing": "Signal at completed-bar available_at; fill strictly later on an eligible bar "
                                        "(CONSERVATIVE_LABEL_AGNOSTIC).",
                          "source_ref": spec["data_requirements"]["datasets"][0]["snapshot_id"],
                          "availability": "DECLARED_AVAILABLE_NOT_VERIFIED"},
        "research_variables": {
            "changes": [{"core_variable": "execution path (strategy, parameters, data and costs unchanged)",
                         "baseline": "Phase 4-A direct ResearchOnlyRunner DEV smoke run 20261107_081300_9439f8c (spec 0.1.0)",
                         "proposed_change": "Same pre-registered SMA 5/20 on the same DEV snapshot through the governed "
                                            "Phase 5 controlled path (spec 0.1.1: data binding re-pin only)",
                         "expected_improvement": "Governed, evidence-verified, settled execution (pipeline acceptance); "
                                                 "no performance change is expected or claimed.",
                         "potential_side_effects": "None on strategy results; any difference from the Phase 4-A run is "
                                                   "a pipeline defect to investigate."}],
            "parameter_search": {"mode": "FIXED", "max_trials": 1}, "selection_basis": "HYPOTHESIS_DRIVEN",
            "prior_experiment_refs": ["docs/evidence/phase4a_dev_smoke/20261107_081300_9439f8c"]},
        "challenger_view": copy.deepcopy(challenger_view),
        "strategy_spec_fragment": {k: copy.deepcopy(spec[k]) for k in ("universe", "parameters", "signal",
                                                                        "position_sizing", "risk_exits",
                                                                        "risk_constraints", "execution")},
    }


def expected_completion(artifact_root: Path = ROOT, spec_path: str | None = None) -> dict:
    from strategy_agent.readiness.b3c import B3C_SPEC_PATH  # noqa: PLC0415
    spec = json.loads((Path(artifact_root) / (spec_path or B3C_SPEC_PATH)).read_text(encoding="utf-8"))
    fx = json.loads((ROOT / "fixtures/proposal/complete_momentum.json").read_text(encoding="utf-8"))
    return build_completion(spec, challenger_view=fx["llm_draft"]["challenger_view"])   # review prompts only


def _authorization(governance_root: Path, authorization_id: str) -> dict:
    p = Path(governance_root) / "governance/research_authorizations.jsonl"
    recs = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    hit = [r for r in recs if r.get("authorization_id") == authorization_id and r.get("action") == "APPROVE"]
    if len(hit) != 1:
        raise B3CStopped("AUTHORIZATION_NOT_UNIQUE", {"authorization_id": authorization_id, "found": len(hit)})
    return hit[0]


def bindings(auth: dict, cfg: dict) -> dict:
    """A2 export bindings, copied from the signed authorization record and the signed canonical run config."""
    st, ds, cs, tp, win = auth["strategy"], auth["dataset"], auth["costs"], auth["temporal_policy"], cfg["windows"]
    return {"strategy_family": {"family_id": "sma-crossover-tsm", "strategy_id": st["strategy_id"],
                                "version": st["version"], "parameters": st["parameters"]},
            "strategy_spec_ref": st["spec_path"], "strategy_spec_sha256": st["spec_file_sha256"],
            "market": {"market_id": auth["market_id"], "symbol": auth["symbol"]},
            "dataset_ref": {"dataset_id": ds["dataset_id"], "producer_commit_sha": ds["producer_commit_sha"]},
            "dataset_snapshot_id": ds["snapshot_id"],
            "dataset_manifest_sha256": cfg["snapshot_binding"]["manifest_sha256"],
            "core_commit": cfg["core"]["phase4a_sha"],
            "signal_time_rule": {"policy_id": tp["policy_id"], "policy_version": tp["version"],
                                 "policy_sha256": tp["sha256"], "signal_time": "CORE_MODELED_AVAILABLE_AT"},
            "execution_time_rule": {"rule": "NEXT_ELIGIBLE_BAR_OPEN"},
            "cost_profile_ref": {"scenario_set_path": cs["scenario_set_path"],
                                 "scenario_set_sha256": cs["scenario_set_sha256"], "scenarios": list(cs["scenarios"])},
            "research_window": {"warmup_start": win["warmup_start"], "start": win["start"], "end": win["end"]},
            "requested_change": {"kind": "REPLAY_EXISTING"}, "execution_mode": "RESEARCH_ONLY_REAL",
            "research_budget": {"batches_requested": 1},
            "holdout_policy": {"access": "NO_ACCESS", "lock_id": auth["holdout"]["lock_id"]},
            "requested_outputs": ["RESEARCH_EVALUATION", "RESEARCH_DECISION", "RISK_FLAGS", "ROBUSTNESS_MATRIX"]}


def approval_lines(source: dict, authorization_id: str, *, expires_utc: str) -> dict:
    from strategy_agent.approval.tokens import approval_line, scope_sha256  # noqa: PLC0415
    from strategy_agent.orchestration import request as Q  # noqa: PLC0415
    from strategy_agent.orchestration.tasks import task_id_for  # noqa: PLC0415
    bound = dict(copy.deepcopy(source), authorization_ref={"authorization_id": authorization_id})
    sh, bh = Q.request_hash(source), Q.request_hash(bound)
    tid = task_id_for(bh)
    return {"source_request_sha256": sh, "bound_request_sha256": bh, "task_id": tid, "expires_utc": expires_utc,
            "BIND_AUTHORIZATION": approval_line(intent="BIND_AUTHORIZATION", request_sha256=sh,
                                                authorization_id=authorization_id, scope=scope_sha256(source),
                                                expires_utc=expires_utc),
            "APPROVE_TASK": approval_line(intent="APPROVE_TASK", request_sha256=bh, authorization_id=authorization_id,
                                          scope=scope_sha256(bound), task_id=tid, expires_utc=expires_utc)}


def prepare(out_dir: Path, *, artifact_root: Path = ROOT, governance_root: Path = ROOT,
            authorization_id: str = AUTHORIZATION_ID, canonical_config: Path | None = None, agent_commit: str,
            now: datetime | None = None, completion: dict | None = None, hours: int = APPROVAL_HOURS) -> dict:
    """Run 1 (non-consuming). ``out_dir`` must not exist; every file is written once."""
    from strategy_agent.phase5a.integration import proposal_to_request  # noqa: PLC0415
    from strategy_agent.proposal import FileDraftAdapter, draft_proposal  # noqa: PLC0415
    from strategy_agent.readiness.b3c import B3C_RUN_CONFIG_PATH  # noqa: PLC0415
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=False)
    now = now or datetime.now(TPE)
    auth = _authorization(governance_root, authorization_id)
    cfg_path = Path(canonical_config) if canonical_config else Path(artifact_root) / B3C_RUN_CONFIG_PATH
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    if cfg.get("authorization_id") != authorization_id:
        raise B3CStopped("RUN_CONFIG_AUTHORIZATION", {"config": cfg.get("authorization_id")})
    comp = completion if completion is not None else json.loads((Path(artifact_root) / COMPLETION_PATH).read_text("utf-8"))
    _write_once(out / "completion.json", _dumps(comp))
    P = draft_proposal(HYPOTHESIS, FileDraftAdapter(out / "completion.json", adapter_id=ADAPTER_ID),
                       agent_commit_sha=agent_commit, created_at=now.isoformat(timespec="seconds"))
    if P["status"] != "PROPOSED":
        raise B3CStopped("PROPOSAL_NOT_PROPOSED", P["validation"]["blockers"])
    _write_once(out / "proposal.json", json.dumps(P, ensure_ascii=False, indent=2).encode())
    r = proposal_to_request(P, bindings=bindings(auth, cfg), output=out / "request.json", artifact_root=artifact_root)
    if r["status"] != "EXPORTED":
        raise B3CStopped("A2_EXPORT", r)
    src = json.loads((out / "request.json").read_text(encoding="utf-8"))
    lines = approval_lines(src, authorization_id, expires_utc=_z(now + timedelta(hours=hours)))
    m = {"kind": "PHASE5B3C_PREPARE_MANIFEST", "authorization_id": authorization_id, "executor_id": EXECUTOR,
         "agent_commit": agent_commit, "prepared_at": now.isoformat(timespec="seconds"),
         "files_sha256": {n: _sha((out / n).read_bytes()) for n in FILES}, **lines,
         "consumed_batches": 0, "binding_written": False, "comments_posted_by_agent": False}
    _write_once(out / MANIFEST, _dumps(m))
    return m


def load_prepared(d: Path) -> tuple[dict, dict]:
    d = Path(d)
    m = json.loads((d / MANIFEST).read_text(encoding="utf-8"))
    bad = [n for n in FILES if not (d / n).is_file() or _sha((d / n).read_bytes()) != m["files_sha256"].get(n)]
    if bad:
        raise B3CStopped("PREPARED_FILES_CHANGED", bad)
    from strategy_agent.orchestration import request as Q  # noqa: PLC0415
    src = json.loads((d / "request.json").read_text(encoding="utf-8"))
    again = approval_lines(src, m["authorization_id"], expires_utc=m["expires_utc"])
    if any(again[k] != m[k] for k in ("source_request_sha256", "bound_request_sha256", "task_id", "BIND_AUTHORIZATION",
                                      "APPROVE_TASK")) or Q.request_hash(src) != m["source_request_sha256"]:
        raise B3CStopped("PREPARED_MANIFEST_INCONSISTENT")
    return m, src


# ------------------------------------------------------------------------------------------- run 2: execute
def production_backend(*, root: Path, snapshot_root: Path, session_dir: Path, git_commit: str, context: dict):
    """The existing ResearchOnlyRunner on the REAL snapshot, with Core's own receipt renewal (never hand-made)."""
    from strategy_agent.execution.controlled import ResearchOnlyRunnerBackend  # noqa: PLC0415
    from strategy_agent.readiness.b3c import B3C_RUN_CONFIG_PATH  # noqa: PLC0415
    from strategy_agent.research_only import development_run as DR  # noqa: PLC0415
    from strategy_agent.research_only.runner import ResearchOnlyRunner  # noqa: PLC0415
    canon = Path(root) / B3C_RUN_CONFIG_PATH
    runner = ResearchOnlyRunner(root=Path(root))
    sc = DR.session_config(canon, Path(snapshot_root), Path(session_dir))
    auth = _authorization(root, AUTHORIZATION_ID)
    ctx = dict(context) | {"canonical_config_path": str(canon), "canonical_config_sha256": sc["canonical_config_sha256"],
                           "canonical_config": sc["canonical_config"], "session_config_difference": sc["difference"],
                           "authorization_record_sha256": auth["record_sha256"], "agent_git_sha": git_commit}
    return ResearchOnlyRunnerBackend(runner, config_path=Path(sc["path"]), snapshot_root=Path(snapshot_root),
                                     git_commit=git_commit, synthetic_snapshot=False, run_context=ctx,
                                     canonical_config_path=canon)


def execute(prepared_dir: Path, *, bind_url: str, task_url: str, verifier, backend, work_dir: Path, claim_dir: Path,
            artifact_root: Path = ROOT, governance_root: Path = ROOT, executor_id: str = EXECUTOR,
            production_books: bool = True, readiness_manifest=None, benchmark=None, clock=None) -> dict:
    """Run 2. Consumes the single batch at most once. Returns the orchestrator status + evidence pointers."""
    from strategy_agent.approval import ApprovalRejected, Expectation  # noqa: PLC0415
    from strategy_agent.binding.service import AuthorizationBindingService, BindingRejected  # noqa: PLC0415
    from strategy_agent.execution import identity as I  # noqa: PLC0415
    from strategy_agent.execution.activation import RealActivationGate  # noqa: PLC0415
    from strategy_agent.execution.controlled import ControlledRunnerAdapter  # noqa: PLC0415
    from strategy_agent.orchestration.orchestrator import Orchestrator  # noqa: PLC0415
    m, src = load_prepared(prepared_dir)
    aid, tid = m["authorization_id"], m["task_id"]
    claim = Path(claim_dir) / CLAIM
    if claim.exists():
        raise B3CStopped("CLAIM_EXISTS", {"claim": str(claim), "meaning": "an earlier attempt exists: "
                                          "RECONCILIATION_REQUIRED by the research lead; never re-run"})
    clock = clock or (lambda: datetime.now(TPE))
    d = Path(prepared_dir)
    kw = {"source_request_path": d / "request.json", "provenance_path": d / "request_provenance.json",
          "proposal_path": d / "proposal.json", "authorization_id": aid, "evidence_url": bind_url}
    svc = AuthorizationBindingService(verifier=verifier, governance_root=governance_root, root=artifact_root,
                                      clock=clock, benchmark=benchmark)
    # ---- read-only checks before anything is written
    try:
        dry = svc.bind(dry_run=True, **kw)
    except BindingRejected as e:
        raise B3CStopped("BINDING_REJECTED", e.failures) from None
    bound = dict(copy.deepcopy(src), authorization_ref={"authorization_id": aid})
    from strategy_agent.orchestration import request as Q  # noqa: PLC0415
    if not (Q.request_hash(bound) == m["bound_request_sha256"] == dry["would_write"]["bound_request_sha256"]):
        raise B3CStopped("BOUND_REQUEST_DIFFERS_FROM_PREPARED")
    try:
        verifier.verify(task_url, Expectation.for_request(bound, intent="APPROVE_TASK", task_id=tid))
    except ApprovalRejected as e:
        raise B3CStopped("TASK_APPROVAL_" + e.code, str(e)) from None
    probe = backend.precheck(bound)
    idn, bad = backend.execution_identity()
    bad = list(probe) + list(bad) + I.compare(I.request_identity(bound), idn, source="BACKEND")
    if bad:
        raise B3CStopped("RUNNER_PRECHECK", bad)
    if readiness_manifest is None:            # A10: the B3-A production readiness manifest (None would block A10)
        from strategy_agent.readiness import manifest as RM  # noqa: PLC0415

        def readiness_manifest():
            return RM.build(None)
    gate = RealActivationGate(governance_root=governance_root, artifact_root=artifact_root, executor_id=executor_id,
                              production_books=production_books, readiness_manifest=readiness_manifest)
    ad = ControlledRunnerAdapter(backend, governance_root=governance_root, activation=gate)
    if ad.mode != "REAL_GATED":
        raise B3CStopped("NOT_REAL_GATED", ad.mode)
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=False)
    o = Orchestrator(work / "state", work / "archive", root=artifact_root, governance_root=governance_root,
                     benchmark=benchmark, approval_verifier=verifier, controlled_adapter=ad, clock=clock,
                     worker=executor_id)
    # ---- writes: binding (no budget), task, approval
    try:
        b = svc.bind(dry_run=False, **kw)
    except BindingRejected as e:
        raise B3CStopped("BINDING_REJECTED", e.failures) from None
    login = b["record"]["binding_actor"]["github_login"]
    s = o.submit(bound, actor=login, actor_kind="HUMAN")
    if s["task_id"] != tid or s["state"] != "PENDING_APPROVAL":
        raise B3CStopped("SUBMIT", s)
    o.approve(tid, evidence_ref=task_url)
    gaps = ad.precheck(bound)                                     # the full A01-A10 gate, now with the binding
    if gaps:
        raise B3CStopped("ACTIVATION_GATE", gaps)
    # ---- the single consuming step
    _write_once(claim, _dumps({"kind": "PHASE5B3C_EXECUTION_CLAIM", "authorization_id": aid, "task_id": tid,
                               "bound_request_sha256": m["bound_request_sha256"], "executor_id": executor_id,
                               "claimed_at": clock().isoformat(timespec="seconds"), "work_dir": str(work),
                               "meaning": "written right before dispatch; its presence forbids any further attempt"}))
    st = o.dispatch(tid)
    rec_dir = work / "archive/execution_records"
    recs = sorted(rec_dir.glob("*.json")) if rec_dir.is_dir() else []
    return {"status": st, "task_id": tid, "binding": b["record"], "work_dir": str(work),
            "execution_record": str(recs[0]) if recs else None, "claim": str(claim),
            "settled": st.get("state") == "ARCHIVED" and st.get("budget_state") == "CONSUMED"}


# ------------------------------------------------------------------------------------------- B3C-01..10 (post-run)
def acceptance(result: dict, *, governance_root: Path = ROOT, cloud: bool, production: bool,
               authorization_id: str = AUTHORIZATION_ID, executor_id: str = EXECUTOR, max_batches: int = 1,
               evaluation: dict | None = None) -> dict:
    """Evidence-based statuses after the real run. B3C-10 needs the research lead's written result decision, so the
    overall verdict is at most EXECUTED_PENDING_RESULT_ACCEPTANCE."""
    G: dict = {}

    def put(gid, ok, why, ev):
        G[gid] = {"status": "PASS" if ok else "FAIL", "why": [] if ok else why, "evidence": ev}
    rec = json.loads(Path(result["execution_record"]).read_text(encoding="utf-8")) if result.get("execution_record") else {}
    k, b, st = rec.get("contract") or {}, result.get("binding") or {}, result.get("status") or {}
    auth = _authorization(governance_root, authorization_id)
    uses = [json.loads(x) for x in (Path(governance_root) / "registry/research_authorization_uses.jsonl")
            .read_text(encoding="utf-8").splitlines() if x.strip()]
    mine = [u for u in uses if u.get("authorization_id") == authorization_id]
    put("B3C-01", auth["max_batches"] == max_batches and (auth.get("executor") or {}).get("executor_id") == executor_id,
        ["authorization record"], {"authorization_record_sha256": auth.get("record_sha256")})
    put("B3C-02", b.get("approval_trust") == ("PRODUCTION" if production else "ISOLATED_TEST")
        and b.get("bound_request_sha256") == k.get("request_sha256"), ["binding / approval trust"],
        {"binding_id": b.get("binding_id"), "trust": b.get("approval_trust")})
    pins = {"strategy_spec_sha256": auth["strategy"]["spec_file_sha256"], "dataset_snapshot_id": auth["dataset"]["snapshot_id"],
            "core_commit": auth["dataset"]["producer_commit_sha"]}
    put("B3C-03", all(k.get(x) == v for x, v in pins.items()), ["pins differ"],
        {x: [k.get(x), v] for x, v in pins.items()})
    put("B3C-04", bool(k) and st.get("state") == "ARCHIVED", ["no archived execution"],
        {"holdout_lock": auth["holdout"]["lock_id"], "gate": "A09 at precheck and dispatch"})
    put("B3C-05", k.get("executor_id") == executor_id and len(mine) == 1, ["executor / uses"],
        {"executor_id": k.get("executor_id"), "uses": len(mine)})
    put("B3C-06", k.get("execution_mode") == "REAL_GATED" and rec.get("execution_mode") == "REAL_GATED",
        ["not REAL_GATED"], {"execution_mode": k.get("execution_mode")})
    rr = (k.get("runner_receipt") or {}).get("verification_ref", "")
    put("B3C-07", str(rr).startswith("DEVELOPMENT_RECEIPT_RENEWAL:") and bool(rec.get("runner_receipt_sha256")),
        ["runner receipt"], {"verification_ref_prefix": str(rr)[:40]})
    sc = (k.get("execution_result") or {}).get("scenarios") or []
    if evaluation is None:          # the evaluation recorded by the orchestrator (fresh process for REAL_GATED)
        rd = json.loads((Path(rec["task_archive"]) / "research_decision.json").read_text("utf-8")) if rec else {}
        ev_status, ev_checks, ev_src = rd.get("acceptance_status"), rd.get("acceptance_checks") or {}, "task_archive"
    else:                           # an explicit fresh-process re-evaluation of the same runner archive
        ev_status, ev_checks, ev_src = evaluation.get("status"), evaluation.get("checks") or {}, "fresh_reevaluation"
    ev_ok = ev_status == "PASS" and len(ev_checks) == 8 and set(ev_checks.values()) == {"PASS"}
    put("B3C-08", sorted(sc) == sorted(auth["costs"]["scenarios"]) and ev_ok,
        (["scenarios"] if sorted(sc) != sorted(auth["costs"]["scenarios"]) else []) +
        ([] if ev_ok else [f"evaluator acceptance {ev_status}: {[c for c, v in ev_checks.items() if v != 'PASS']}"]),
        {"scenarios": sc, "evaluator_acceptance": ev_status, "evaluator_checks": ev_checks, "source": ev_src})
    put("B3C-09", bool(rec.get("task_archive")) and cloud, ["archive / cloud"],
        {"task_archive": rec.get("task_archive"), "cloud": cloud})
    settled = (rec.get("budget_settlement") or {}).get("state") == "CONSUMED"
    G["B3C-10"] = {"status": "PENDING_RESEARCH_LEAD_DECISION" if settled else "FAIL",
                   "why": [] if settled else ["ledger not CONSUMED"],
                   "evidence": {"budget_state": (rec.get("budget_settlement") or {}).get("state"),
                                "research_result_acceptance": "PENDING_RESEARCH_LEAD_DECISION"}}
    fail = [g for g, v in G.items() if v["status"] == "FAIL"]
    return {"kind": "PHASE5B3C_ACCEPTANCE", "gates": G,
            "verdict": "NO_GO" if fail else "EXECUTED_PENDING_RESULT_ACCEPTANCE", "failed": fail,
            "meaning": "a PASS shows the governed pipeline ran once on real data; it says nothing about SMA 5/20 "
                       "profitability"}


def reevaluate(execution_record: Path, out_dir: Path, *, governance_root: Path, evaluator_commit: str,
               archive_prefix_map: tuple[str, str] | None = None, evaluated_at: str | None = None) -> dict:
    """Read-only re-evaluation of an ALREADY executed run: the existing Phase 4-C evaluator, in a fresh process, on the
    runner archive named by the execution record (its MANIFEST sha must match the record). 0 batches, no re-run.
    ``archive_prefix_map`` maps the runtime path recorded at execution to where the same bytes live now (Drive copy)."""
    from strategy_agent.evaluation import fresh_process as FP  # noqa: PLC0415
    from strategy_agent.evaluation import phase4c as C  # noqa: PLC0415
    rec = json.loads(Path(execution_record).read_text("utf-8"))
    arch = rec["runner_archive"]
    if archive_prefix_map:
        old, new = archive_prefix_map
        if not arch.startswith(old):
            raise B3CStopped("ARCHIVE_PATH_NOT_MAPPED", {"recorded": arch, "prefix": old})
        arch = new + arch[len(old):]
    at = evaluated_at or datetime.now(TPE).isoformat(timespec="seconds")
    acc = FP.accept(arch, expected_manifest_sha256=rec["runner_archive_manifest_sha256"],
                    evaluator_commit_sha=evaluator_commit, evaluated_at=at, governance_root=governance_root)
    files = C.write_acceptance(acc, out_dir, evaluator_commit_sha=evaluator_commit, evaluated_at=at)
    ev = acc.get("evaluation") or {}
    return {"status": acc["status"], "checks": {c: v["status"] for c, v in (acc.get("checks") or {}).items()},
            "evaluator_process": acc.get("evaluator_process"), "error": acc.get("error"), "runner_archive": arch,
            "runner_archive_manifest_sha256": rec["runner_archive_manifest_sha256"], "evaluated_at": at,
            "evaluator_commit": evaluator_commit, "output_dir": str(out_dir), "files_sha256": files,
            "strategy_verdict": ev.get("strategy_verdict"), "evidence_sufficiency": ev.get("evidence_sufficiency"),
            "engineering_status": ev.get("engineering_status"), "consumed_batches": 0, "strategy_re_run": False}


RUNNER_EVIDENCE = ("run_info.txt", "MANIFEST.json", "metrics.json", "cost_breakdown.csv", "research_report.md",
                   "execution_diagnostics.json", "trades.csv", "rejected_orders.csv")


def evidence_bundle(result: dict, prepared_dir: Path, dest: Path, *, acceptance_doc: dict) -> dict:
    """Copy the reviewable evidence (never the market data) into a NEW directory with a sha256 index. Works for a
    settled run and for a failed one (whatever exists is copied; nothing is fabricated)."""
    import shutil  # noqa: PLC0415
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=False)
    src: dict[str, Path] = {f"prepare/{n}": Path(prepared_dir) / n for n in (*FILES, MANIFEST)}
    rec = json.loads(Path(result["execution_record"]).read_text("utf-8")) if result.get("execution_record") else {}
    if rec:
        src["execution_record.json"] = Path(result["execution_record"])
        for n in ("research_request.json", "task_events.jsonl", "governance_decision.json", "execution_reference.json",
                  "evaluation_reference.json", "research_decision.json", "run_info.txt"):
            src[f"task_archive/{n}"] = Path(rec["task_archive"]) / n
        for n in RUNNER_EVIDENCE:
            src[f"runner_archive/{n}"] = Path(rec["runner_archive"]) / n
        src["runner_receipt.json"] = Path(rec["runner_receipt_file"])
    for rel, p in src.items():
        if p.is_file():
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, dest / rel)
    _write_once(dest / "b3c_acceptance.json", _dumps(acceptance_doc))
    _write_once(dest / "execution_status.json", _dumps({k: result.get(k) for k in ("task_id", "status", "settled",
                                                                                    "claim")}))
    idx = {str(p.relative_to(dest)): _sha(p.read_bytes()) for p in sorted(dest.rglob("*")) if p.is_file()}
    _write_once(dest / "ARCHIVE_INDEX.json", _dumps(idx))
    return idx


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m strategy_agent.execution.b3c_run")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("emit-completion")
    a = ap.parse_args(argv)
    if a.cmd == "emit-completion":
        p = ROOT / COMPLETION_PATH
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(_dumps(expected_completion()))
        print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
