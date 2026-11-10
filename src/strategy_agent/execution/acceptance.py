"""Phase 5-B2 acceptance: B2-01..B2-10 from real test results + real E2E artefacts (never from prose).

    python -m strategy_agent.execution.acceptance --out-dir OUT --junit A.xml [--junit B.xml] [--context-json C] [--cloud]
    python -m strategy_agent.execution.acceptance --out-dir OUT --verify-archive [--zip Z]

Layer A (always): A2 proposal -> verified binding -> verified approval -> governance -> atomic reservation -> commit ->
controlled execution (SYNTHETIC_FIXTURE backend) -> receipt -> existing evaluator -> archive -> settlement.
Layer B (when the pinned Core development reader is installed): the existing ResearchOnlyRunner on a SYNTHETIC
snapshot under the same ledger, evaluated by the existing Phase 4-C evaluator.
Consistency scope is SINGLE_EXECUTOR_ONLY, so the best possible verdict is SCOPED_GO (no distributed atomicity).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from strategy_agent.evaluation import phase4c as C
from strategy_agent.orchestration import adapters as AD
from strategy_agent.orchestration import governance as GV
from strategy_agent.phase5a import acceptance as A5

from . import testkit as X
from .budget import CONSISTENCY

TPE = ZoneInfo("Asia/Taipei")
ROOT = A5.ROOT
INDEX = "ARCHIVE_INDEX.json"
OUTPUT_FILES = ("execution_acceptance.json", "budget_reservations.json", "execution_receipts.json", "task_lifecycle.json",
                "governance_checks.json", "archive_integrity.json", "notebook_context.json", "security_remediation.json",
                "security_reproduction.json", "run_info.txt", INDEX)
SETTLED_HISTORY = ["RESERVED", "COMMITTED", "EXECUTION_STARTED", "EXECUTION_COMPLETED", "EVIDENCE_VERIFIED", "CONSUMED"]
REPRODUCER = ROOT / "docs/evidence/phase5b2_remediation/reproduce.py"
SECURITY = {   # Codex security review of PR #13 (HEAD 71b9a6d); reported SEPARATELY from B2-01..B2-10
    "P0-1": ("Reservation Replay / Forgery: only the ledger grants execution, exactly once per reservation",
             ("P0_1_reservation_replay_forgery",)),
    "P0-2": ("Receipt / Archive Integrity Before Settlement: re-verified from disk; tampering never settles",
             ("P0_2_receipt_archive_integrity", "P0_2_task_archive_research_request")),
    "P0-3": ("Request / Execution Identity: Core / spec / dataset manifest verified against authoritative sources",
             ("P0_3_identity_layer_a_precheck", "P0_3_identity_layer_b_runner")),
}
GATES = {
    "B2-01": "Atomic Reservation: concurrent workers / processes never exceed the limit; authority re-read in one critical section",
    "B2-02": "Authorization Scope: PENDING / unbound / exhausted / revoked authorizations never execute",
    "B2-03": "Human Approval Verification: unapproved or revoked approval never executes",
    "B2-04": "Bound Request Integrity: a modified or forged bound request is not bound",
    "B2-05": "Synthetic Controlled Execution: approved + reserved execution runs exactly once and settles",
    "B2-06": "Crash / Retry / Concurrency: no silent release, no re-run, single executor, idempotent replays",
    "B2-07": "PIT / Dataset / Cost Integrity: pin mismatches block before any reservation",
    "B2-08": "Evaluator / Archive Integration: existing evaluator, never KEEP on failure, incomplete archive never success",
    "B2-09": "No Real Execution / No Holdout / No Budget Consumption on real books",
    "B2-10": "CI / Colab / Drive Reproducibility",
}


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def junit(paths: list[Path]) -> dict:
    out: dict = {}
    for p in paths:
        for tc in ET.parse(p).getroot().iter("testcase"):
            name = tc.get("name", "")
            st = "SKIPPED" if tc.find("skipped") is not None else \
                "FAIL" if (tc.find("failure") is not None or tc.find("error") is not None) else "PASS"
            out[f"{tc.get('classname')}::{name}"] = st
    return out


def layer_a(work: Path) -> dict:
    W = X.world(work / "layer_a")
    o = X.orchestrator(W)
    tid = X.submit_and_approve(W, o)
    st = o.dispatch(tid)
    from strategy_agent.phase5a.integration import trace  # noqa: PLC0415
    tr = trace(o, tid)
    ev = next(e for e in o.log.get(tid).events if e["to"] == "EVALUATING")["data"]["execution_reference"]
    receipt = json.loads(Path(ev["receipt_path"]).read_text(encoding="utf-8"))
    return {"label": "SYNTHETIC_STUB (Layer A; ledger / evidence mechanics only, not producer output)", "task_id": tid, "state": st["state"],
            "states": [h["to"] for h in st["history"]], "reservation": o.budget.for_task(tid),
            "budget_events": o.budget.events(), "receipt": receipt, "receipt_sha256": ev["receipt_sha256"],
            "decision": tr["research_decision"], "task_archive": tr["task_archive"], "backend_calls": W["backend"].calls,
            "uses": [json.loads(x) for x in (W["gov"] / "registry/research_authorization_uses.jsonl").read_text().splitlines()],
            "governance_at_submit": {c["id"]: c["status"] for c in o.log.get(tid).events[1]["data"]["governance"]["checks"]}
            if o.log.get(tid).events[1].get("data", {}).get("governance") else None,
            "identity": {"proposal_sha256": W["binding"]["source_proposal_sha256"],
                         "bound_request_sha256": W["binding"]["bound_request_sha256"],
                         "approval_evidence_sha256": o.budget.for_task(tid)["approval_evidence_sha256"],
                         "authorization_id": W["authorization_id"], "binding_id": W["binding"]["binding_id"],
                         **{k: W["bound"].get(k) for k in ("core_commit", "strategy_spec_sha256", "dataset_snapshot_id",
                                                             "dataset_manifest_sha256", "signal_time_rule",
                                                             "execution_time_rule", "holdout_policy", "research_window")},
                         "cost_scenarios": W["bound"]["cost_profile_ref"]["scenarios"],
                         "batch_reservation_id": o.budget.for_task(tid)["reservation_id"],
                         "output_manifest_sha256": receipt["output_manifest_sha256"], "git_commit": o.code["git_sha"]}}


def layer_b(work: Path) -> dict:
    """The EXISTING ResearchOnlyRunner + Core on a SYNTHETIC snapshot, driven by the production-mode Orchestrator on
    ISOLATED books through a real A2 -> verified binding -> bound request (security remediation: no self-referential
    request), then the existing Phase 4-C evaluator in a fresh process."""
    from . import core_world as CW  # noqa: PLC0415
    ok, why = CW.core_available()
    if not ok:
        return {"status": "NOT_AVAILABLE", "why": why}
    from .controlled import ResearchOnlyRunnerBackend  # noqa: PLC0415
    g40 = A5.code_identity()["git_sha"]
    w = CW.build(work / "layer_b")
    g = w["gov"]
    be = ResearchOnlyRunnerBackend(w["runner"], config_path=w["config_path"], snapshot_root=w["snapshot_root"],
                                   git_commit=g40, synthetic_snapshot=True, run_context={**w["run_context"], "agent_git_sha": g40})
    o = CW.orchestrator(w, work / "layer_b_orch", be)
    pre = o.adapters["RESEARCH_ONLY_REAL"].precheck(w["bound"])
    if pre:
        return {"status": "PRECHECK_BLOCKED", "problems": pre}
    s = o.submit(w["bound"], actor="TEST_FIXTURE_SUBMITTER", actor_kind="TEST_FIXTURE")
    if s["state"] != "PENDING_APPROVAL":
        return {"status": "SUBMIT_BLOCKED", "governance": s.get("governance")}
    tid = s["task_id"]
    w["fa"].approve_task(o, tid)
    st = o.dispatch(tid)
    r = o.budget.for_task(tid)
    if st["state"] != "ARCHIVED":
        return {"status": "FAIL", "state": st["state"], "last_data": st["last_data"], "reservation": r,
                "runner_calls": be.calls}
    ref = o._execution_reference(o.log.get(tid))                      # noqa: SLF001
    acc = fresh_process_accept(ref["archive_path"], ref["archive_manifest_sha256"], g40, g)
    uses = (g / "registry/research_authorization_uses.jsonl").read_text().splitlines()
    ok = acc["status"] == "PASS" and len(uses) == 1 and be.calls == 1 and r["state"] == "CONSUMED"
    return {"status": "PASS" if ok else "FAIL",
            "label": "SYNTHETIC development snapshot via the existing ResearchOnlyRunner (Layer B, orchestrated)",
            "core": why, "task_state": st["state"], "acceptance": acc["status"],
            "acceptance_checks": {k: v["status"] for k, v in acc["checks"].items()},
            "reservation": r, "budget_events": o.budget.events(), "binding_id": w["binding"]["binding_id"],
            "bound_request_sha256": w["binding"]["bound_request_sha256"],
            "receipt": json.loads(Path(ref["receipt_path"]).read_text()), "receipt_sha256": ref["receipt_sha256"],
            "uses_records": len(uses), "runner_calls": be.calls}


def fresh_process_accept(archive: str, manifest_sha: str, commit: str, gov: Path) -> dict:
    """The existing Phase 4-C evaluator in a FRESH interpreter: its C8 process-level check must not see the runner
    this process imported for Layer B. A non-zero exit is a failed evaluation, never a pass."""
    import subprocess  # noqa: PLC0415
    code = ("import json,sys;from strategy_agent.evaluation import phase4c as C;"
            "a=C.accept(sys.argv[1],expected_manifest_sha256=sys.argv[2],evaluator_commit_sha=sys.argv[3],"
            "evaluated_at=sys.argv[4],governance_root=sys.argv[5]);print(json.dumps(a,default=str))")
    r = subprocess.run([sys.executable, "-c", code, str(archive), manifest_sha, commit,
                        datetime.now(TPE).isoformat(timespec="seconds"), str(gov)], capture_output=True, text=True,
                       timeout=900, cwd=ROOT)
    if r.returncode != 0:
        return {"status": "FAIL", "checks": {}, "error": {"code": "EVALUATOR_SUBPROCESS_FAILED", "rc": r.returncode,
                                                         "stderr": r.stderr[-500:]}}
    return json.loads(r.stdout.strip().splitlines()[-1])


def gates(A: dict, B: dict, tests: dict, *, cloud: bool, tests_rc: int | None) -> dict:
    def by(prefix):
        return {k: v for k, v in tests.items() if f"test_{prefix.replace('-', '_')}_" in k}
    out = {}
    for gid, req in GATES.items():
        t = by(gid)
        extra_ok, evidence = True, {}
        if gid == "B2-05":
            extra_ok = A["state"] == "ARCHIVED" and A["backend_calls"] == 1 and \
                A["reservation"]["history"] == SETTLED_HISTORY and len(A["uses"]) == 1 and \
                B.get("status") in ("PASS", "NOT_AVAILABLE")
            evidence = {"layer_a": {k: A[k] for k in ("state", "backend_calls")}, "layer_a_budget": A["reservation"]["history"],
                        "layer_b": {k: B.get(k) for k in ("status", "acceptance", "uses_records", "runner_calls", "why")}}
        if gid == "B2-08":
            d = A["decision"] or {}
            extra_ok = d.get("strategy_verdict") != "KEEP" and (B.get("status") in ("PASS", "NOT_AVAILABLE"))
            evidence = {"layer_a_decision": {k: d.get(k) for k in ("acceptance_status", "strategy_verdict")},
                        "layer_b_acceptance": B.get("acceptance"), "layer_b_checks": B.get("acceptance_checks")}
        if gid == "B2-09":
            from strategy_agent.readiness.b3c import governed_ledger_ok  # noqa: PLC0415
            extra_ok = not GV.REAL_EXECUTION_ENABLED and not AD.ResearchOnlyRunnerAdapter.enabled and governed_ledger_ok()
            evidence = {"REAL_EXECUTION_ENABLED": GV.REAL_EXECUTION_ENABLED,
                        "repo_budget_ledger_exists": (ROOT / "registry/research_budget_events.jsonl").exists()}
        if gid == "B2-10":
            extra_ok = tests_rc == 0 and cloud
            evidence = {"tests_returncode": tests_rc, "cloud": cloud, "python": platform.python_version(),
                        "consistency": CONSISTENCY}
            status = "PASS" if extra_ok else ("BLOCKED" if not cloud and tests_rc == 0 else "FAIL")
            out[gid] = {"requirement": req, "test": "regression + B2 suites + Drive archive", "expected": "rc=0, cloud",
                        "actual": evidence, "evidence": evidence, "status": status}
            continue
        failed = [k for k, v in t.items() if v == "FAIL"]
        passed = [k for k, v in t.items() if v == "PASS"]
        status = "FAIL" if failed or not extra_ok else ("PASS" if passed else "BLOCKED")
        out[gid] = {"requirement": req, "test": sorted(t), "expected": "all listed tests PASS (+ artefact checks)",
                    "actual": {"passed": len(passed), "failed": failed, "skipped": [k for k, v in t.items() if v == "SKIPPED"]},
                    "evidence": evidence, "status": status}
    return out


def reproduce(out: Path) -> dict:
    """The re-derived P0 reproducer in a FRESH interpreter (its results are what actually happened)."""
    import subprocess  # noqa: PLC0415
    r = subprocess.run([sys.executable, str(REPRODUCER), "--out", str(out)], capture_output=True, text=True,
                       cwd=str(ROOT), timeout=1800)
    if r.returncode != 0 or not out.is_file():
        return {"status": "ERROR", "returncode": r.returncode, "stderr": r.stderr[-2000:]}
    return json.loads(out.read_text(encoding="utf-8"))


def security_gates(tests: dict, repro: dict) -> dict:
    out = {}
    for gid, (req, keys) in SECURITY.items():
        t = {k: v for k, v in tests.items() if f"test_{gid.replace('-', '_')}_" in k}
        failed = [k for k, v in t.items() if v == "FAIL"]
        res = {k: repro.get(k, {}) for k in keys}
        vulnerable = [k for k, v in res.items() if not isinstance(v, dict) or v.get("VULNERABLE") is not False
                      or v.get("CONTROL_SETTLES") is False]          # a fix that blocks honest settlement is a FAIL
        skipped = [k for k, v in res.items() if isinstance(v, dict) and v.get("status") == "SKIPPED"]
        status = "FAIL" if failed or (set(vulnerable) - set(skipped)) else (
            "BLOCKED" if skipped or not t else "FIXED_PENDING_INDEPENDENT_REVIEW")
        out[gid] = {"finding": req, "regression_tests": {"passed": sum(v == "PASS" for v in t.values()),
                                                         "failed": failed, "names": sorted(t)},
                    "reproduction_after_fix": {k: {"VULNERABLE": v.get("VULNERABLE") if isinstance(v, dict) else None,
                                                   "status": v.get("status") if isinstance(v, dict) else None}
                                               for k, v in res.items()},
                    "status": status}
    return out


def verdict(g: dict, B: dict) -> dict:
    bad = sorted(k for k, v in g.items() if v["status"] == "FAIL")
    if bad:
        return {"verdict": "NO_GO" if set(bad) & {"B2-01", "B2-02", "B2-03", "B2-09"} else "NOT_ACCEPTED", "why": bad}
    pending = sorted(k for k, v in g.items() if v["status"] == "BLOCKED")
    why = [f"consistency {CONSISTENCY}: fcntl locks are not a distributed lock across Colab runtimes / Google Drive"]
    if B.get("status") != "PASS":
        why.append(f"Layer B (existing runner) {B.get('status')}")
    if pending:
        why.append(f"gates not evidenced: {pending}")
    return {"verdict": "SCOPED_GO", "why": why}


def _tree(d: Path) -> dict:
    files = sorted(p for p in Path(d).rglob("*") if p.is_file()) if Path(d).exists() else []
    lines = [f"{p.relative_to(d).as_posix()}:{_sha(p.read_bytes())}" for p in files]
    return {"files": len(lines), "sha256": _sha("\n".join(lines).encode())}


def verify_archive(out_dir: Path, zip_path: Path | None = None) -> dict:
    out_dir = Path(out_dir)
    try:
        idx = json.loads((out_dir / INDEX).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {"status": "FAIL", "problems": [f"index unreadable: {e}"]}
    present = {p.name for p in out_dir.iterdir() if p.is_file() and p.name != INDEX}
    probs = [f"unlisted file: {n}" for n in sorted(present - set(idx["files"]))]
    probs += [f"missing file: {n}" for n in sorted(set(idx["files"]) - present)]
    probs += [f"altered file: {n}" for n in sorted(present & set(idx["files"]))
              if _sha((out_dir / n).read_bytes()) != idx["files"][n]["sha256"]]
    probs += [f"required output missing: {n}" for n in OUTPUT_FILES if n != INDEX and n not in present]
    if _tree(out_dir / "work") != idx.get("work_tree"):
        probs.append("work/ tree differs from the index")
    if zip_path is not None:
        with zipfile.ZipFile(zip_path) as z:
            names = set(z.namelist())
            want = set(idx["files"]) | {INDEX}
            probs += [f"zip missing: {n}" for n in sorted(want - names)] + [f"zip extra: {n}" for n in sorted(names - want)]
            for n in sorted(want & names):
                if _sha(z.read(n)) != (idx["files"][n]["sha256"] if n != INDEX else _sha((out_dir / INDEX).read_bytes())):
                    probs.append(f"zip differs: {n}")
    return {"status": "PASS" if not probs else "FAIL", "problems": probs, "files": len(present)}


def run(out_dir: Path, *, junit_paths: list[Path], tests_rc: int | None, cloud: bool, context: dict | None) -> dict:
    out_dir = Path(out_dir)
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FileExistsError(f"execution acceptance already exists in {out_dir}")
    work = out_dir / "work"
    repo_before = A5.tree_fingerprint(ROOT)
    A = layer_a(work)
    B = layer_b(work)
    tests = junit(junit_paths)
    g = gates(A, B, tests, cloud=cloud, tests_rc=tests_rc)
    out_dir.mkdir(parents=True, exist_ok=True)
    repro = reproduce(out_dir / "security_reproduction.json")
    sec = security_gates(tests, repro)
    code = A5.code_identity()
    v = verdict(g, B)
    unchanged = A5.tree_fingerprint(ROOT) == repo_before
    if not unchanged:
        v = {"verdict": "NO_GO", "why": ["repository governance/registry changed during acceptance"]}
    dumps = lambda o: (json.dumps(o, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n").encode()  # noqa: E731
    files = {
        "execution_acceptance.json": {"verdict": v, "gates": g, "code": code, "cloud": cloud, "consistency": CONSISTENCY,
                                      "exactly_once": "NOT_CLAIMED (at-most-once dispatch per ledger)",
                                      "generated_at": datetime.now(TPE).isoformat(timespec="seconds"),
                                      "repository_books_unchanged": unchanged},
        "budget_reservations.json": {"layer_a": {"reservation": A["reservation"], "events": A["budget_events"], "uses": A["uses"]},
                                     "layer_b": {k: B.get(k) for k in ("reservation", "budget_events", "uses_records")}},
        "execution_receipts.json": {"layer_a": {"receipt": A["receipt"], "sha256": A["receipt_sha256"]},
                                    "layer_b": {"receipt": B.get("receipt"), "sha256": B.get("receipt_sha256")},
                                    "execution_identity": A["identity"]},
        "task_lifecycle.json": {"layer_a": {k: A[k] for k in ("task_id", "states", "state", "backend_calls")},
                                "budget_state_machine": A["reservation"]["history"]},
        "governance_checks.json": {"layer_a_submit": A["governance_at_submit"], "layer_b_checks": B.get("acceptance_checks"),
                                   "real_execution_enabled": GV.REAL_EXECUTION_ENABLED},
        "archive_integrity.json": {"layer_a_task_archive": A["task_archive"], "layer_a_decision": A["decision"],
                                   "layer_b": {k: B.get(k) for k in ("status", "acceptance")}, "tests": tests},
        "notebook_context.json": context or {"note": "no notebook context (CLI / CI run)"},
        "security_remediation.json": {"gates": sec, "reviewed_head": "71b9a6da829c652d7e1f89e107b770d730728598",
                                      "security_verdict": "NOT_DECLARED (independent Codex re-review required)",
                                      "reproducer": "RE-DERIVED from the remediation brief; Codex's original unavailable"},
    }
    for n, obj in files.items():
        with open(out_dir / n, "xb") as fh:
            fh.write(dumps(obj))
    info = {"kind": "PHASE5B2_CONTROLLED_EXECUTION_ACCEPTANCE", "verdict": v["verdict"], **code, "cloud": cloud,
            "consistency": CONSISTENCY, "layer_b": B.get("status"), "real_market_execution": False, "holdout_read": False,
            "real_authorization_consumed": False, "broker_api_called": False, "tests_returncode": tests_rc,
            "security_remediation": {k: x["status"] for k, x in sec.items()},
            "security_verdict": "NOT_DECLARED (independent Codex re-review required)"}
    with open(out_dir / "run_info.txt", "x", encoding="utf-8") as fh:
        fh.write("".join(f"{k}={v_ if isinstance(v_, str) else json.dumps(v_)}\n" for k, v_ in info.items()))
    idx = {"kind": "PHASE5B2_ARCHIVE_INDEX", "work_tree": _tree(work),
           "files": {p.name: {"sha256": _sha(p.read_bytes()), "bytes": p.stat().st_size}
                     for p in sorted(out_dir.iterdir()) if p.is_file()}}
    with open(out_dir / INDEX, "x", encoding="utf-8") as fh:
        json.dump(idx, fh, indent=2, sort_keys=True)
    if verify_archive(out_dir)["status"] != "PASS":
        raise RuntimeError("archive index self-check failed")
    return {"verdict": v, "gates": {k: x["status"] for k, x in g.items()}, "layer_b": B.get("status"),
            "security_remediation": {k: x["status"] for k, x in sec.items()}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m strategy_agent.execution.acceptance")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--junit", action="append", default=[])
    ap.add_argument("--tests-rc", type=int)
    ap.add_argument("--context-json")
    ap.add_argument("--cloud", action="store_true")
    ap.add_argument("--verify-archive", action="store_true")
    ap.add_argument("--zip")
    a = ap.parse_args(argv)
    if a.verify_archive:
        r = verify_archive(Path(a.out_dir), Path(a.zip) if a.zip else None)
        print(json.dumps(r, indent=2))
        return 0 if r["status"] == "PASS" else 1
    ctx = json.loads(Path(a.context_json).read_text()) if a.context_json else None
    r = run(Path(a.out_dir), junit_paths=[Path(p) for p in a.junit], tests_rc=a.tests_rc, cloud=a.cloud, context=ctx)
    print(json.dumps(r, indent=2))
    sec_ok = "FAIL" not in r["security_remediation"].values()       # BLOCKED (not evidenced here) is reported, not hidden
    return 0 if r["verdict"]["verdict"] in ("GO", "SCOPED_GO") and sec_ok else 1


if __name__ == "__main__":
    sys.exit(main())
