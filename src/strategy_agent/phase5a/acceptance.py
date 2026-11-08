"""Phase 5-A3 integration acceptance: Route A (synthetic E2E) + Route B (existing archive replay) -> A3-01..A3-10.

Route A  SYNTHETIC_FIXTURE end to end. The proposal is drafted by A2 (``FakeLLMAdapter`` over the labelled
         ``fixtures/proposal`` completions), the request is written by A2's real exporter, and A1's real orchestrator
         (isolated test mode, TEST_FIXTURE approver) runs it over the labelled Phase 4-C fixture archive. Negative paths
         (incomplete proposal, PENDING authorization, unauthorized formal research, wrong SHA, duplicate, concurrency,
         restart, crash, missing archive file, evaluator failure) run against temporary copies only.
Route B  Read-only replay of an EXISTING research archive through A1 (``ARCHIVE_REPLAY``, 0 batches). Locally this is
         the labelled synthetic fixture archive; the real ``20261107_081300_9439f8c`` archive is replayed only where it
         exists (Colab + Drive) and only after the research lead supplies their own approver id and approval URL.

A replay request is human-authored: A2 never selects an authorization, so it cannot (and must not) emit one.
Nothing here re-runs a strategy, imports the research-only runner or Core, consumes a batch, writes a governed
book or reads the Holdout. All verdicts come from the existing Phase 4-C acceptance / Phase 4-B evaluator.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from strategy_agent.evaluation import phase4c as C
from strategy_agent.orchestration import adapters as AD
from strategy_agent.orchestration import governance as GV
from strategy_agent.orchestration import request as Q
from strategy_agent.orchestration.orchestrator import ARCHIVE_FILES, Orchestrator
from strategy_agent.orchestration.tasks import IllegalTransition, TaskLog

from strategy_agent.approval.testkit import FixtureApprovals

from .integration import CONTRACT, LinkLog, contract_check, proposal_to_request, submit_proposal, trace

ROOT = Q.ROOT
TPE = ZoneInfo("Asia/Taipei")
FIX = ROOT / "tests/fixtures/phase4c"
FIX_RUN = "20261106_224000_0123456"
REAL_RUN = "20261107_081300_9439f8c"
REAL_ARCHIVE_MANIFEST = "328f2cf99c33b4d9d427327d092aaf31da619b5f5ad948c815e75b04b92345a9"
REAL_REPLAY_REQUEST = ROOT / "docs/examples/research_requests/tsm_sma520_dev_archive_replay.json"
BASE_SPEC = ROOT / "specs/examples/sma_crossover_tw_2330_5m.json"
LIFECYCLE = ["PROPOSED", "VALIDATED", "PENDING_APPROVAL", "APPROVED", "QUEUED", "RUNNING", "EVALUATING", "EVALUATED",
             "ARCHIVED"]
OUTPUT_FILES = ("integration_acceptance.json", "proposal_to_request.json", "task_lifecycle.json", "governance_checks.json",
                "evaluation_reference.json", "archive_integrity.json", "run_info.txt")
SYN = "SYNTHETIC_FIXTURE"


class StepClock:
    """Monotonic clock: +1 s per read; ``jump`` simulates elapsed wall time (lease expiry)."""

    def __init__(self, start: datetime | None = None):
        self.t = (start or datetime.now(TPE)).replace(microsecond=0)

    def __call__(self):
        self.t += timedelta(seconds=1)
        return self.t

    def jump(self, seconds: int):
        self.t += timedelta(seconds=seconds)


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def tree_fingerprint(root: Path, dirs=("governance", "registry")) -> dict:
    return {str(p.relative_to(root)): _sha(p.read_bytes()) for d in dirs for p in sorted((Path(root) / d).rglob("*"))
            if p.is_file()}


def code_identity() -> dict:
    try:
        sha = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
        dirty = subprocess.check_output(["git", "-C", str(ROOT), "status", "--porcelain"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        sha, dirty = "0" * 40, "UNKNOWN"
    return {"git_sha": sha, "dirty": bool(dirty), "python": platform.python_version()}


# ---------------------------------------------------------------------------------------------- A2 inputs
def _fixture(name: str) -> dict:
    f = json.loads((ROOT / "fixtures/proposal" / f"{name}.json").read_text(encoding="utf-8"))
    if f.get("kind") != SYN:
        raise ValueError("proposal fixtures must be labelled SYNTHETIC_FIXTURE")
    return f


def draft(name: str, *, agent_commit_sha: str, created_at: str, completion_file: Path | None = None) -> dict:
    """A2 draft. ``completion_file`` routes the SAME labelled completion through A2's FileDraftAdapter (input_kind
    USER_HYPOTHESIS) so the non-synthetic negative paths are still produced by A2 itself."""
    from strategy_agent.proposal import FakeLLMAdapter, FileDraftAdapter, draft_proposal  # noqa: PLC0415
    f = _fixture(name)
    if completion_file is None:
        adapter = FakeLLMAdapter()
    else:
        completion_file.write_text(json.dumps(f["llm_draft"], ensure_ascii=False), encoding="utf-8")
        adapter = FileDraftAdapter(completion_file, adapter_id="SYNTHETIC_FIXTURE_COMPLETION_FILE_NOT_A_MODEL")
    return draft_proposal(f["user_hypothesis"], adapter, agent_commit_sha=agent_commit_sha, created_at=created_at)


def synthetic_artifacts(root: Path, proposal: dict, *, execution_mode: str = "SYNTHETIC_TEST") -> dict:
    """Labelled SYNTHETIC_FIXTURE spec + cost file in a temporary artifact root, and A2 bindings pointing at them.
    No real dataset status, authorization or cost approval is implied."""
    root = Path(root)
    (root / "specs").mkdir(parents=True, exist_ok=True)
    (root / "configs").mkdir(parents=True, exist_ok=True)
    spec = json.loads(BASE_SPEC.read_text(encoding="utf-8"))
    spec.update(copy.deepcopy(proposal["draft"]["strategy_spec_fragment"]))
    spec.update(strategy_id="synthetic_fixture_sma", name="SYNTHETIC_FIXTURE not a real registered strategy")
    spec["data_requirements"]["datasets"] = [{"dataset_id": "synthetic_fixture_dataset",
                                              "snapshot_id": "SYNTHETIC_FIXTURE_snapshot", "producer_commit_sha": "1" * 40,
                                              "manifest_sha256": "2" * 64, "verdict": "GO", "usage_scope": "BACKTEST"}]
    spec["provenance"]["notes"] = "SYNTHETIC_FIXTURE integration demonstration only; no real dataset status or authorization."
    sf = root / "specs/synthetic_fixture_spec.json"
    if not sf.exists():
        sf.write_text(json.dumps(spec), encoding="utf-8")
    cf = root / "configs/synthetic_fixture_scenarios.json"
    if not cf.exists():
        cf.write_text(json.dumps({"kind": SYN, "status": "NOT_VERIFIED"}), encoding="utf-8")
    real = execution_mode != "SYNTHETIC_TEST"
    return {
        "strategy_family": {"family_id": "synthetic-fixture-family", "strategy_id": spec["strategy_id"],
                            "version": spec["version"], "parameters": {"fast": 5, "slow": 20}},
        "strategy_spec_ref": "specs/synthetic_fixture_spec.json", "strategy_spec_sha256": _sha(sf.read_bytes()),
        "market": {"market_id": "TW_EQUITY", "symbol": "2330"},
        "dataset_ref": {"dataset_id": "synthetic_fixture_dataset", "producer_commit_sha": "1" * 40},
        "dataset_snapshot_id": "SYNTHETIC_FIXTURE_snapshot", "dataset_manifest_sha256": "2" * 64,
        "signal_time_rule": {"policy_id": "CONSERVATIVE_LABEL_AGNOSTIC", "policy_version": "1.0.0",
                             "policy_sha256": "7fa3cdcaf501651f749dbc72c31b362fcb9e10cc55920191cef87c1a050059c4",
                             "signal_time": "CORE_MODELED_AVAILABLE_AT"},
        "execution_time_rule": {"rule": "NEXT_ELIGIBLE_BAR_OPEN"},
        "cost_profile_ref": {"scenario_set_path": "configs/synthetic_fixture_scenarios.json",
                             "scenario_set_sha256": _sha(cf.read_bytes()), "scenarios": ["BASE", "STRESS"]},
        "research_window": {"warmup_start": "2024-05-29T00:00:00+08:00", "start": "2024-06-03T00:00:00+08:00",
                            "end": "2024-06-29T00:00:00+08:00"},
        "requested_change": {"kind": "PARAMETER_CHANGE" if real else "SYNTHETIC_TEST"},
        "execution_mode": execution_mode, "research_budget": {"batches_requested": 1 if real else 0},
        "holdout_policy": {"access": "NO_ACCESS", "lock_id": "HOLDOUT-SYNTHETIC-FIXTURE"},
        "requested_outputs": ["RESEARCH_EVALUATION", "RESEARCH_DECISION", "RISK_FLAGS", "ROBUSTNESS_MATRIX"]}


def fixture_replay_request(src: Path) -> dict:
    """Human-authored replay request (A1 example) re-pinned to the labelled synthetic fixture archive."""
    a = C.read_archive(src / FIX_RUN, expected_manifest_sha256=C.sha256_bytes((src / FIX_RUN / "MANIFEST.json").read_bytes()))
    ri = C.parse(a)["run_info"]
    doc = Q.load(REAL_REPLAY_REQUEST)
    doc.update(request_id="RR-SYNTHETIC-FIXTURE-PHASE5A3-REPLAY", dataset_snapshot_id=ri["snapshot_id"],
               dataset_manifest_sha256=ri["manifest_sha256"],
               replay_source={"run_id": FIX_RUN, "archive_manifest_sha256": a["manifest_sha256"]})
    return doc


def _states(orch, tid) -> list[str]:
    return [e["to"] for e in orch.log.get(tid).events]


def _checks(gov: dict | None) -> dict:
    return {c["id"]: c["status"] for c in (gov or {}).get("checks", [])}


def _try(fn):
    try:
        return {"ok": True, "result": fn()}
    except (IllegalTransition, ValueError, FileExistsError) as e:
        return {"ok": False, "error": getattr(e, "code", type(e).__name__), "message": str(e)[:300]}


# ------------------------------------------------------------------------------------------------- Route A
def route_a(work: Path, *, agent_commit_sha: str, clock: StepClock | None = None) -> dict:
    """Everything here is SYNTHETIC_FIXTURE and lives under ``work`` (outside the repository)."""
    work = Path(work).resolve()
    if ROOT in work.parents or work == ROOT:
        raise ValueError("Route A work directory must be outside the repository")
    clock = clock or StepClock()
    at = clock().isoformat(timespec="seconds")
    art, ex = work / "artifacts", work / "exports"
    gov = work / "gov"
    shutil.copytree(FIX / "governance_root", gov)
    lead = sorted(json.loads((gov / "governance/approvers.json").read_text())["research_leads"])[0]
    repo_before = tree_fingerprint(ROOT)
    R: dict = {"label": SYN, "work": str(work), "created_at": at}
    fa = FixtureApprovals()                    # 5-B1: isolated TEST_FIXTURE approval evidence for production paths

    def test_orch(state, **kw):
        kw.setdefault("synthetic_fixture", FIX / FIX_RUN)
        return Orchestrator(work / state, work / "out", clock=clock, test_mode=True, governance_root=gov, **kw)

    def prod_orch(state, **kw):
        kw.setdefault("source_archive_root", work / "src")
        kw.setdefault("approval_verifier", fa.verifier)
        return Orchestrator(work / state, work / "out", clock=clock, governance_root=gov, **kw)

    # --- A3-02 positive path: actual A2 draft -> actual A2 export -> actual A1 submit -> full lifecycle
    P = draft("complete_momentum", agent_commit_sha=agent_commit_sha, created_at=at)
    B = synthetic_artifacts(art, P)
    o = test_orch("state")
    sub = submit_proposal(o, P, bindings=B, export_path=ex / "complete.json", artifact_root=art, actor="TEST_FIXTURE",
                          actor_kind="TEST_FIXTURE")
    tid = sub["task_id"]
    request = Q.load(sub["export"]["request_path"]) if sub["export"]["status"] == "EXPORTED" else None
    pending_state = sub["state"]
    gov_submit = sub["orchestrator"].get("governance") if sub["submitted"] else None
    refused_real_approver = _try(lambda: o.approve(tid, approver=lead, approval_ref="https://example.test/approval"))
    no_approval_dispatch = o.dispatch(tid)["state"] if tid else None
    o.approve(tid, approver="TEST_FIXTURE", approval_ref="SYNTHETIC_FIXTURE-test-approval")
    final = o.dispatch(tid)
    tr = trace(o, tid)
    arch = Path(tr["task_archive"]) if tr["task_archive"] else None
    R["positive"] = {"proposal": {"proposal_id": P["proposal_id"], "status": P["status"],
                                  "input_kind": P["origin"]["input_kind"], "proposal_sha256": P["proposal_sha256"],
                                  "request_contract_pin": P.get("request_contract_pin")},
                     "export": sub["export"], "request": request, "a1_validate_errors": Q.validate(request) if request else None,
                     "a1_pending_fields": Q.pending_fields(request) if request else None,
                     "task_id": tid, "state_after_submit": pending_state, "governance_at_submit": gov_submit,
                     "dispatch_before_approval_state": no_approval_dispatch, "real_approver_in_test_mode": refused_real_approver,
                     "final_state": final["state"], "states": _states(o, tid), "trace": tr,
                     "approver_kinds": sorted({e["actor_kind"] for e in o.log.get(tid).events if e["to"] == "APPROVED"}),
                     "archive_dir": str(arch) if arch else None,
                     "archive_parent": arch.parent.name if arch else None,
                     "archive_files": sorted(p.name for p in arch.iterdir()) if arch else []}

    # --- duplicate submit + restart (same state dir)
    n_events = len(o.log.records())
    dup = submit_proposal(o, P, bindings=B, export_path=ex / "complete_again.json", artifact_root=art,
                          actor="TEST_FIXTURE", actor_kind="TEST_FIXTURE")
    dup_same_path = submit_proposal(o, P, bindings=B, export_path=ex / "complete.json", artifact_root=art,
                                    actor="TEST_FIXTURE", actor_kind="TEST_FIXTURE")
    n_tasks_dirs = len(list((work / "out/test_tasks").iterdir()))
    o2 = test_orch("state")                                            # a restarted process on the same state
    rec = o2.recover()
    redispatch = o2.dispatch(tid)["state"]
    retry_archived = _try(lambda: o2.retry(tid, actor="TEST_FIXTURE", reason="must be refused"))
    R["duplicate_restart"] = {"duplicate_task_id": dup["task_id"], "duplicate_flag": dup["duplicate"],
                              "same_path_reexport": dup_same_path["export"].get("already_exported"),
                              "same_path_task_id": dup_same_path["task_id"],
                              "events_before": n_events, "events_after": len(o2.log.records()),
                              "links": len(LinkLog(o.state_dir).records()),
                              "recover_actions": rec, "redispatch_state": redispatch,
                              "retry_archived": retry_archived,
                              "task_archive_dirs_before": n_tasks_dirs,
                              "task_archive_dirs_after": len(list((work / "out/test_tasks").iterdir()))}

    # --- concurrency: four submitters, same proposal, separate export files, one state dir
    oc = test_orch("state_concurrent")
    results, errors = [], []

    def worker(i):
        try:
            results.append(submit_proposal(oc, P, bindings=B, export_path=ex / f"concurrent_{i}.json", artifact_root=art,
                                           actor="TEST_FIXTURE", actor_kind="TEST_FIXTURE"))
        except Exception as e:  # noqa: BLE001 - recorded as evidence
            errors.append(f"{type(e).__name__}: {e}")
    th = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in th:
        t.start()
    for t in th:
        t.join()
    R["concurrency"] = {"submitters": 4, "task_ids": sorted({r["task_id"] for r in results}),
                        "proposed_events": sum(1 for e in oc.log.records() if e["to"] == "PROPOSED"),
                        "links": len(LinkLog(oc.state_dir).records()), "errors": errors}

    # --- crash during RUNNING: lease expires, recovery marks FAILED, never re-executes
    P2 = draft("complete_momentum", agent_commit_sha=agent_commit_sha,
               created_at=(clock.t + timedelta(hours=1)).isoformat(timespec="seconds"))
    ocr = test_orch("state_crash", lease_seconds=60)
    s2 = submit_proposal(ocr, P2, bindings=B, export_path=ex / "crash.json", artifact_root=art, actor="TEST_FIXTURE",
                         actor_kind="TEST_FIXTURE")
    t2 = s2["task_id"]
    ocr.approve(t2, approver="TEST_FIXTURE", approval_ref="SYNTHETIC_FIXTURE-test-approval")
    # simulated worker crash: the worker claimed the task (QUEUED -> RUNNING with a lease) and died before executing
    ocr._sys(t2, "QUEUED", expect_from="APPROVED", idempotency_key=f"{t2}:QUEUED")
    ocr.log.transition(t2, "RUNNING", actor="orchestrator", actor_kind="SYSTEM", worker="crashed-worker",
                       lease_seconds=60, expect_from="QUEUED", data={"attempt": 1, "simulated_crash": True},
                       idempotency_key=f"{t2}:RUNNING:1")
    clock.jump(120)
    rec2 = test_orch("state_crash").recover()
    R["crash"] = {"task_id": t2, "recover_actions": rec2, "state": test_orch("state_crash").status(t2)["state"],
                  "executions_dir_exists": (work / "state_crash/executions" / t2).exists(),
                  "second_recover": test_orch("state_crash").recover()}

    # --- A3-03 incomplete proposal: no request, no task
    PI = draft("momentum", agent_commit_sha=agent_commit_sha, created_at=at)
    before = len(o.log.records())
    inc = submit_proposal(o, PI, bindings=B, export_path=ex / "incomplete.json", artifact_root=art,
                          actor="TEST_FIXTURE", actor_kind="TEST_FIXTURE")
    inc_nobind = proposal_to_request(P, bindings=None, output=ex / "no_bindings.json", artifact_root=art)
    R["incomplete"] = {"proposal_status": PI["status"], "result": inc["export"], "submitted": inc["submitted"],
                       "request_file_exists": (ex / "incomplete.json").exists(),
                       "events_added": len(o.log.records()) - before,
                       "complete_proposal_without_bindings": inc_nobind,
                       "no_bindings_file_exists": (ex / "no_bindings.json").exists()}

    # --- A3-04 / A3-05: A2 export with authorization_ref PENDING, non-synthetic origin, formal research mode
    PU = draft("complete_momentum", agent_commit_sha=agent_commit_sha, created_at=at,
               completion_file=work / "completion_SYNTHETIC_FIXTURE.json")
    BU = synthetic_artifacts(art, PU, execution_mode="RESEARCH_ONLY_REAL")
    op = prod_orch("state_prod")
    su = submit_proposal(op, PU, bindings=BU, export_path=ex / "unauthorized_real.json", artifact_root=art,
                         actor="TEST_FIXTURE_SUBMITTER", actor_kind="TEST_FIXTURE")
    tu = su["task_id"]
    appr = _try(lambda: op.approve(tu, approver=lead, approval_ref="https://example.test/approval"))
    disp = op.dispatch(tu)["state"] if tu else None
    in_test = test_orch("state_pending_in_test").submit(Q.load(su["export"]["request_path"]), actor="TEST_FIXTURE",
                                                        actor_kind="TEST_FIXTURE") if su["submitted"] else None
    syn_in_prod = op.submit(request, actor="TEST_FIXTURE_SUBMITTER", actor_kind="TEST_FIXTURE") if request else None
    forged = proposal_to_request(PU, bindings={**BU, "authorization_ref": {"authorization_id": "RA-FORGED"}},
                                 output=ex / "forged.json", artifact_root=art)
    R["pending_authorization"] = {
        "proposal_input_kind": PU["origin"]["input_kind"], "export": su["export"],
        "request_authorization_ref": Q.load(su["export"]["request_path"])["authorization_ref"] if su["submitted"] else None,
        "execution_mode": BU["execution_mode"], "task_id": tu, "state": su["state"],
        "governance": _checks(su["orchestrator"].get("governance")) if su["submitted"] else None,
        "approve_attempt": appr, "dispatch_state": disp, "states": _states(op, tu) if tu else [],
        "in_test_orchestrator": {"state": in_test["state"], "checks": _checks(in_test.get("governance"))} if in_test else None,
        "synthetic_request_in_production": {"state": syn_in_prod["state"],
                                            "checks": _checks(syn_in_prod.get("governance"))} if syn_in_prod else None,
        "forged_authorization_binding": {**forged, "file_exists": (ex / "forged.json").exists()}}

    # --- wrong SHA (spec reference tampering)
    bad = copy.deepcopy(B)
    bad["strategy_spec_sha256"] = "0" * 64
    ws = proposal_to_request(P, bindings=bad, output=ex / "wrong_sha.json", artifact_root=art)
    R["wrong_sha"] = {"result": ws, "file_exists": (ex / "wrong_sha.json").exists()}

    # --- Route B (synthetic fixture archive) + archive integrity negatives
    R["replay_fixture"] = route_b(work / "replay_fixture", fixture=True, approver=lead,
                                  approval_ref="https://example.test/approval", clock=clock)  # fixture: evidence via testkit
    shutil.copytree(FIX / FIX_RUN, work / "src_missing" / FIX_RUN)
    dm = fixture_replay_request(work / "src_missing")
    victim = next(p for p in sorted((work / "src_missing" / FIX_RUN).iterdir()) if p.name != "MANIFEST.json")
    victim.unlink()
    om = prod_orch("state_missing", source_archive_root=work / "src_missing")
    tm = om.submit(dm, actor=lead, actor_kind="HUMAN")["task_id"]
    fa.approve_task(om, tm)
    sm = om.dispatch(tm)
    R["missing_file"] = {"removed": victim.name, "state": sm["state"], "reason": sm["history"][-1]["reason"],
                         "states": [h["to"] for h in sm["history"]]}
    # evaluator failure: a consistent archive whose BASE trades were removed -> acceptance not PASS -> never KEEP
    src_ev = work / "src_evalfail"
    shutil.copytree(FIX / FIX_RUN, src_ev / FIX_RUN)
    tp = src_ev / FIX_RUN / "trades.csv"
    rows = tp.read_text().splitlines()
    tp.write_text("\n".join([rows[0]] + [r for r in rows[1:] if not r.startswith("BASE,")]) + "\n")
    m = json.loads((src_ev / FIX_RUN / "MANIFEST.json").read_text())
    m["files_sha256"]["trades.csv"] = _sha(tp.read_bytes())
    (src_ev / FIX_RUN / "MANIFEST.json").write_text(json.dumps(m))
    oe = prod_orch("state_evalfail", source_archive_root=src_ev)
    te = oe.submit(fixture_replay_request(src_ev), actor=lead, actor_kind="HUMAN")["task_id"]
    fa.approve_task(oe, te)
    se = oe.dispatch(te)
    dec = trace(oe, te)["research_decision"] or {}
    R["evaluator_failure"] = {"state": se["state"], "acceptance_status": dec.get("acceptance_status"),
                              "strategy_verdict": dec.get("strategy_verdict"),
                              "evidence_sufficiency": dec.get("evidence_sufficiency")}

    # --- integrity of everything written
    integ = {}
    for name in ("state", "state_concurrent", "state_crash", "state_prod", "state_missing", "state_evalfail"):
        try:
            TaskLog(work / name / "task_events.jsonl", clock).records()
            LinkLog(work / name).records()
            integ[name] = "CHAIN_OK"
        except Exception as e:  # noqa: BLE001
            integ[name] = f"BROKEN: {e}"
    links_ok = all(o.log.get(l["task_id"]) is not None and o.log.get(l["task_id"]).request_hash == l["request_hash"]
                   for l in LinkLog(o.state_dir).records())
    hashes_ok, overwrite = False, None
    if arch:
        recorded = next(e for e in o.log.get(tid).events if e["to"] == "ARCHIVED")["data"]["files_sha256"]
        hashes_ok = all(_sha((arch / n).read_bytes()) == h for n, h in recorded.items()) and set(recorded) == set(ARCHIVE_FILES)
        try:
            with open(arch / "research_decision.json", "xb"):
                overwrite = "OVERWRITTEN"
        except FileExistsError:
            overwrite = "REFUSED"
    R["integrity"] = {"chains": integ, "links_resolve_to_tasks": links_ok, "archive_hashes_match": hashes_ok,
                      "archive_overwrite_attempt": overwrite, "repo_governance_registry_unchanged":
                      tree_fingerprint(ROOT) == repo_before}
    return R


# ------------------------------------------------------------------------------------------------- Route B
def route_b(out: Path, *, fixture: bool, approver: str | None, approval_ref: str | None, request_path: Path | None = None,
            source_archive_root: Path | None = None, governance_root: Path | None = None, clock=None) -> dict:
    """Read-only replay through A1. ``fixture=True`` copies the labelled synthetic archive + fixture governance into
    ``out``; ``fixture=False`` replays a real archive against the repository's governed books (read only)."""
    out = Path(out)
    if out.exists():
        raise FileExistsError(f"replay output exists: {out}")
    if fixture:
        shutil.copytree(FIX / FIX_RUN, out / "src" / FIX_RUN)
        shutil.copytree(FIX / "governance_root", out / "gov")
        src, gov = out / "src", out / "gov"
        doc = fixture_replay_request(src)
        origin = "A1_HUMAN_AUTHORED_REPLAY_REQUEST re-pinned to the SYNTHETIC_FIXTURE archive"
    else:
        src, gov = Path(source_archive_root), Path(governance_root or ROOT)
        doc = Q.load(request_path or REAL_REPLAY_REQUEST)
        origin = "A1_HUMAN_AUTHORED_REPLAY_REQUEST (A2 never selects an authorization, so it cannot emit a replay)"
    rep = {"label": SYN if fixture else "EXISTING_REAL_ARCHIVE_READ_ONLY", "request_origin": origin,
           "request_id": doc.get("request_id"), "replay_source": doc.get("replay_source"), "request": doc}
    if doc.get("execution_mode") != "ARCHIVE_REPLAY":
        return {**rep, "status": "REFUSED", "reason": "REPLAY_ONLY"}
    if not approver or not str(approval_ref or "").startswith("https://"):
        return {**rep, "status": "PENDING_USER_APPROVAL",
                "reason": "the research lead must supply their own approver id and https approval reference"}
    if not (src / doc["replay_source"]["run_id"]).is_dir():
        return {**rep, "status": "PENDING_ARCHIVE", "reason": f"archive not present under {src}"}
    gb = tree_fingerprint(gov)
    fa = FixtureApprovals() if fixture else None
    if fixture:
        verifier = fa.verifier
    else:
        from strategy_agent.approval import production_verifier  # noqa: PLC0415 - live GitHub only
        verifier = production_verifier()
    o = Orchestrator(out / "state", out, source_archive_root=src, governance_root=gov, clock=clock,
                     approval_verifier=verifier)
    aid = doc["authorization_ref"]["authorization_id"]
    uses_before = len(o.gov.uses(aid))
    s = o.submit(doc, actor=approver, actor_kind="HUMAN")
    tid = s["task_id"]
    rep.update(task_id=tid, state_after_submit=s["state"], governance=s.get("governance"))
    if s["state"] != "PENDING_APPROVAL":
        return {**rep, "status": "BLOCKED", "states": _states(o, tid), "uses_before": uses_before}
    try:
        if fixture:
            fa.approve_task(o, tid)
        else:                                       # 5-B1: verified GitHub evidence for THIS task, or nothing
            o.approve(tid, approver=approver, evidence_ref=approval_ref)
    except IllegalTransition as e:
        return {**rep, "status": "APPROVAL_REJECTED", "reason": e.code, "message": str(e)[:300],
                "states": _states(o, tid), "uses_before": uses_before,
                "approval_line_required": "RESEARCH-APPROVAL v1 ... intent=APPROVE_TASK task=" + tid}
    if o.request_of(tid)["execution_mode"] != "ARCHIVE_REPLAY":
        raise IllegalTransition("REPLAY_ONLY", "replay runs ARCHIVE_REPLAY tasks only")
    st = o.dispatch(tid)
    tr = trace(o, tid)
    ex = next((e["data"]["execution_reference"] for e in o.log.get(tid).events if e["to"] == "EVALUATING"), {})
    accept = {}
    if tr["evaluation_reference"]:
        ap = Path(tr["evaluation_reference"]["dir"]) / "acceptance_checks.json"
        if ap.exists():
            accept = json.loads(ap.read_text())
    return {**rep, "status": st["state"], "states": [h["to"] for h in st["history"]], "trace": tr,
            "execution_reference": ex, "acceptance_checks": accept, "uses_before": uses_before,
            "uses_after": len(o.gov.uses(aid)), "governance_unchanged": tree_fingerprint(gov) == gb,
            "approver_declared": approver, "approver_note": "5-B1: approver is the verified GitHub author of the "
            "approval evidence (fixture: TEST_FIXTURE source, ISOLATED_TEST trust)"}


# ------------------------------------------------------------------------------------------------ isolation
def isolation_evidence() -> dict:
    """A3-10. ``fresh_process`` matters: inside a shared test process other tests import executors."""
    pkg = ROOT / "src/strategy_agent/proposal"
    banned = ("strategy_agent.orchestration", "strategy_agent.research_only", "strategy_agent.adapters",
              "market_research_core", "strategy_agent.backtest")
    hits = []
    for f in sorted(pkg.glob("*.py")):
        for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            mods = [a.name for a in n.names] if isinstance(n, ast.Import) else \
                [n.module or ""] if isinstance(n, ast.ImportFrom) else []
            hits += [f"{f.name}:{m}" for m in mods if any(m == b or m.startswith(b + ".") for b in banned)]
    broker_mods = ("ib_insync", "ibapi", "alpaca", "shioaji", "fubon_neo", "ccxt")
    broker = []
    for d in ("phase5a", "proposal", "orchestration"):
        for f in sorted((ROOT / "src/strategy_agent" / d).glob("*.py")):
            for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
                mods = [a.name for a in n.names] if isinstance(n, ast.Import) else \
                    [n.module or ""] if isinstance(n, ast.ImportFrom) else []
                broker += [f"{d}/{f.name}:{m}" for m in mods if m.split(".")[0] in broker_mods]
    return {"real_execution_enabled": GV.REAL_EXECUTION_ENABLED,
            "research_only_runner_adapter_enabled": AD.ResearchOnlyRunnerAdapter.enabled,
            "executor_modules_loaded": [m for m in C.EXECUTOR_MODULES if m in sys.modules],
            "proposal_agent_forbidden_imports": hits, "broker_api_references": broker}


# ---------------------------------------------------------------------------------------------------- gates
def _g(ok: bool, evidence) -> dict:
    return {"status": "PASS" if ok else "FAIL", "evidence": evidence}


def gates(contract: dict, A: dict, real_replay: dict | None, iso: dict, *, fresh_process: bool) -> dict:
    p, d, cc, cr = A["positive"], A["duplicate_restart"], A["concurrency"], A["crash"]
    inc, pa, ws, rb = A["incomplete"], A["pending_authorization"], A["wrong_sha"], A["replay_fixture"]
    gs = p["governance_at_submit"] or {}
    four = {"contract_compatibility": contract["status"],
            "actual_proposal_to_request_export": "PASS" if p["export"]["status"] == "EXPORTED"
            and p["export"]["input_kind"] == SYN and p["export"]["provenance"]["status"] == "SCHEMA_VALID_PENDING_HUMAN_APPROVAL"
            and p["a1_validate_errors"] == [] else "FAIL",
            "orchestrator_acceptance": "PASS" if p["state_after_submit"] == "PENDING_APPROVAL"
            and gs.get("status") == "TEST_MODE_ONLY" and _checks(gs).get("G1_REQUEST_SCHEMA") == "PASS" else "FAIL",
            "full_e2e_execution": "PASS" if p["states"] == LIFECYCLE else "FAIL"}
    blocked_pa = pa["state"] == "BLOCKED" and pa["governance"].get("G5_AUTHORIZATION") == "FAIL" \
        and not {"APPROVED", "QUEUED", "RUNNING"} & set(pa["states"]) and not pa["approve_attempt"]["ok"]
    rb_ok = rb.get("status") == "ARCHIVED" and rb.get("acceptance_checks", {}).get("status") == "PASS" \
        and rb.get("uses_before") == rb.get("uses_after") == 1 and rb.get("governance_unchanged") \
        and rb["execution_reference"].get("consumed_batches") == 0
    g = {
        "A3-01": _g(contract["status"] == "PASS" and p["a1_validate_errors"] == [],
                    {"contract": contract["status"], "a1_validate_errors_on_a2_output": p["a1_validate_errors"]}),
        "A3-02": _g(all(v == "PASS" for v in four.values()), four),
        "A3-03": _g(inc["result"]["status"] == "PENDING_INFORMATION" and not inc["submitted"]
                    and not inc["request_file_exists"] and inc["events_added"] == 0
                    and inc["complete_proposal_without_bindings"]["status"] == "PENDING_INFORMATION"
                    and not inc["no_bindings_file_exists"],
                    {"status": inc["result"]["status"], "blockers": inc["result"].get("blockers"), "events_added":
                     inc["events_added"]}),
        "A3-04": _g(blocked_pa and pa["request_authorization_ref"] == "PENDING"
                    and pa["in_test_orchestrator"]["state"] == "BLOCKED"
                    and pa["forged_authorization_binding"]["status"] == "REJECTED"
                    and "AUTHORIZATION_MUST_REMAIN_PENDING" in str(pa["forged_authorization_binding"].get("errors"))
                    and not pa["forged_authorization_binding"]["file_exists"],
                    {"state": pa["state"], "G5": pa["governance"].get("G5_AUTHORIZATION"), "states": pa["states"],
                     "approve": pa["approve_attempt"].get("error"),
                     "forged_binding": pa["forged_authorization_binding"].get("errors")}),
        "A3-05": _g(blocked_pa and pa["governance"].get("G10_EXECUTION_MODE") == "FAIL" and pa["dispatch_state"] == "BLOCKED"
                    and pa["synthetic_request_in_production"]["state"] == "BLOCKED"
                    and pa["synthetic_request_in_production"]["checks"].get("G0_TEST_MODE") == "FAIL",
                    {"mode": pa["execution_mode"], "failed_checks": sorted(k for k, v in pa["governance"].items() if v == "FAIL"),
                     "synthetic_in_production": pa["synthetic_request_in_production"]}),
        "A3-06": _g(p["states"] == LIFECYCLE and p["approver_kinds"] == ["TEST_FIXTURE"] and p["archive_parent"] == "test_tasks"
                    and p["dispatch_before_approval_state"] == "PENDING_APPROVAL" and not p["real_approver_in_test_mode"]["ok"]
                    and "SYNTHETIC_TEST" in ((p["trace"]["research_decision"] or {}).get("label") or ""),
                    {"states": p["states"], "decision": p["trace"]["research_decision"]}),
        "A3-07": _g(rb_ok, {"route": rb.get("label"), "status": rb.get("status"),
                            "acceptance": rb.get("acceptance_checks", {}).get("status"),
                            "uses": f"{rb.get('uses_before')}->{rb.get('uses_after')}",
                            "real_archive_replay": (real_replay or {}).get("status", "PENDING_COLAB")}),
        "A3-08": _g(d["duplicate_task_id"] == p["task_id"] and d["duplicate_flag"] and d["same_path_reexport"]
                    and d["same_path_task_id"] == p["task_id"] and d["events_before"] == d["events_after"]
                    and d["links"] == 1 and d["recover_actions"] == [] and d["redispatch_state"] == "ARCHIVED"
                    and not d["retry_archived"]["ok"] and d["task_archive_dirs_before"] == d["task_archive_dirs_after"]
                    and len(cc["task_ids"]) == 1 and cc["proposed_events"] == 1 and cc["links"] == 1 and not cc["errors"]
                    and cr["recover_actions"] == [{"task_id": cr["task_id"], "action": "FAILED_NOT_REEXECUTED"}]
                    and cr["state"] == "FAILED" and not cr["executions_dir_exists"] and cr["second_recover"] == [],
                    {"duplicate": d, "concurrency": cc, "crash": cr}),
        "A3-09": _g(all(v == "CHAIN_OK" for v in A["integrity"]["chains"].values()) and A["integrity"]["links_resolve_to_tasks"]
                    and A["integrity"]["archive_hashes_match"] and A["integrity"]["archive_overwrite_attempt"] == "REFUSED"
                    and A["integrity"]["repo_governance_registry_unchanged"]
                    and A["missing_file"]["state"] == "FAILED" and "ARCHIVED" not in A["missing_file"]["states"]
                    and A["evaluator_failure"]["acceptance_status"] != "PASS"
                    and A["evaluator_failure"]["strategy_verdict"] != "KEEP"
                    and ws["result"]["status"] == "REJECTED" and not ws["file_exists"],
                    {"integrity": A["integrity"], "missing_file": A["missing_file"],
                     "evaluator_failure": A["evaluator_failure"], "wrong_sha": ws["result"].get("errors")}),
        "A3-10": _g(not iso["real_execution_enabled"] and not iso["research_only_runner_adapter_enabled"]
                    and (not fresh_process or not iso["executor_modules_loaded"]) and not iso["proposal_agent_forbidden_imports"]
                    and not iso["broker_api_references"] and all(
                        (r or {}).get("holdout_policy", {}).get("access") == "NO_ACCESS"
                        for r in (p["request"], rb.get("request"))),
                    {**iso, "fresh_process": fresh_process}),
    }
    return g


def verdict(g: dict, real_replay: dict | None, *, cloud: bool) -> dict:
    if any(v["status"] != "PASS" for v in g.values()):
        return {"verdict": "NO_GO" if any(g[k]["status"] != "PASS" for k in ("A3-04", "A3-05", "A3-10")) else "NOT_ACCEPTED",
                "why": sorted(k for k, v in g.items() if v["status"] != "PASS")}
    rr_ok = bool(real_replay) and real_replay.get("status") == "ARCHIVED" and \
        real_replay.get("acceptance_checks", {}).get("status") == "PASS" and real_replay.get("uses_before") == real_replay.get("uses_after")
    if rr_ok and cloud:
        return {"verdict": "GO", "why": "A3-01..A3-10 PASS; actual A2->A1 synthetic E2E; real archive replay PASS; cloud archive"}
    missing = ([] if rr_ok else ["real archive replay (Colab)"]) + ([] if cloud else ["Drive cloud archive"])
    return {"verdict": "SCOPED_GO", "why": "A3-01..A3-10 PASS locally; missing: " + ", ".join(missing)}


# ---------------------------------------------------------------------------------------------------- write
def write_outputs(out_dir: Path, report: dict) -> dict:
    """Seven files, each O_EXCL. An existing acceptance in ``out_dir`` is never overwritten."""
    out_dir = Path(out_dir)
    if any((out_dir / n).exists() for n in OUTPUT_FILES):
        raise FileExistsError(f"integration acceptance already exists in {out_dir}")
    A, B = report["route_a"], report.get("route_b_real")
    dumps = lambda o: (json.dumps(o, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n").encode()  # noqa: E731
    files = {
        "integration_acceptance.json": {k: report[k] for k in ("verdict", "gates", "contract", "code", "fresh_process",
                                                               "cloud", "generated_at", "scope")},
        "proposal_to_request.json": {"label": SYN, "positive": {k: A["positive"][k] for k in
                                     ("proposal", "export", "request", "a1_validate_errors", "a1_pending_fields")},
                                     "incomplete": A["incomplete"], "pending_authorization_export": A["pending_authorization"]["export"],
                                     "wrong_sha": A["wrong_sha"]},
        "task_lifecycle.json": {"synthetic_positive": {"task_id": A["positive"]["task_id"], "states": A["positive"]["states"],
                                                       "trace": A["positive"]["trace"]},
                                "duplicate_restart": A["duplicate_restart"], "concurrency": A["concurrency"],
                                "crash": A["crash"], "replay_fixture_states": A["replay_fixture"].get("states"),
                                "replay_real_states": (B or {}).get("states")},
        "governance_checks.json": {"synthetic_submit": A["positive"]["governance_at_submit"],
                                   "pending_authorization": A["pending_authorization"],
                                   "replay_fixture": A["replay_fixture"].get("governance"),
                                   "replay_real": (B or {}).get("governance"),
                                   "isolation": report["isolation"]},
        "evaluation_reference.json": {"synthetic": A["positive"]["trace"]["evaluation_reference"],
                                      "synthetic_decision": A["positive"]["trace"]["research_decision"],
                                      "replay_fixture": {k: A["replay_fixture"].get(k) for k in ("trace", "acceptance_checks")},
                                      "replay_real": {k: (B or {}).get(k) for k in ("status", "trace", "acceptance_checks",
                                                                                    "execution_reference")},
                                      "evaluator": "existing Phase 4-C acceptance over the Phase 4-B evaluator; no PnL "
                                                   "recomputed; no LLM verdict"},
        "archive_integrity.json": {"integrity": A["integrity"], "missing_file": A["missing_file"],
                                   "evaluator_failure": A["evaluator_failure"],
                                   "replay_real_uses": {k: (B or {}).get(k) for k in ("uses_before", "uses_after",
                                                                                     "governance_unchanged")}},
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for n, obj in files.items():
        with open(out_dir / n, "xb") as fh:
            b = dumps(obj)
            fh.write(b)
        hashes[n] = _sha(b)
    info = {"kind": "PHASE5A3_INTEGRATION_ACCEPTANCE", "verdict": report["verdict"]["verdict"],
            "git_sha": report["code"]["git_sha"], "dirty": report["code"]["dirty"], "python": report["code"]["python"],
            "contract_commit": CONTRACT["commit"], "schema_sha256": CONTRACT["schema_sha256"],
            "generated_at": report["generated_at"], "cloud": report["cloud"],
            "real_archive_replay": (B or {}).get("status", "NOT_RUN"), "consumed_batches": 0,
            "real_backtest_executed": False, "holdout_read": False, "broker_api_called": False,
            "files_sha256": hashes}
    b = "".join(f"{k}={v if isinstance(v, str) else json.dumps(v, sort_keys=True)}\n" for k, v in info.items()).encode()
    with open(out_dir / "run_info.txt", "xb") as fh:
        fh.write(b)
    hashes["run_info.txt"] = _sha(b)
    return hashes


def run(out_dir: Path, *, real_replay: dict | None = None, cloud: bool = False, fresh_process: bool = True,
        work_dir: Path | None = None) -> dict:
    """Full acceptance. ``real_replay``: kwargs for ``route_b(fixture=False, ...)`` or None (not run here)."""
    out_dir = Path(out_dir)
    code = code_identity()
    clock = StepClock()
    contract = contract_check()
    A = route_a(Path(work_dir or out_dir / "work") / "route_a", agent_commit_sha=code["git_sha"], clock=clock)
    B = route_b(Path(work_dir or out_dir / "work") / "route_b_real", fixture=False, clock=clock, **real_replay) \
        if real_replay else None
    iso = isolation_evidence()
    g = gates(contract, A, B, iso, fresh_process=fresh_process)
    report = {"generated_at": datetime.now(TPE).isoformat(timespec="seconds"), "code": code, "contract": contract,
              "route_a": A, "route_b_real": B, "isolation": iso, "gates": g, "fresh_process": fresh_process,
              "cloud": cloud, "verdict": verdict(g, B, cloud=cloud),
              "scope": "Phase 5-A3 integration acceptance. Route A is SYNTHETIC_FIXTURE engineering evidence; Route B "
                       "replays an existing archive read-only (0 batches). Neither is new strategy research."}
    report["files_sha256"] = write_outputs(out_dir, report)
    return report
