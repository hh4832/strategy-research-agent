"""Phase 5-B2 security remediation: RE-DERIVED reproductions of the three Codex P0 findings on PR #13.

Codex's original report (/workspace/security-review-reports/pr13/SECURITY_REVIEW.md) and its reproducer were NOT
available to Claude. These attacks are re-derived from the finding descriptions in the remediation brief only; they
are not claimed to be Codex's original reproducer, and no original line numbers are claimed.

Everything runs in temporary ISOLATED governance roots (SYNTHETIC only); the repository's books are never touched.
The script is version-aware: it runs unchanged against the pre-fix commit (71b9a6d, old adapter API with caller-supplied
``use`` / ``reservation``) and the remediated commit (ledger-authoritative ``reservation_id`` API), and reports what
actually happened (runner calls, ledger state), never what was expected.

  python docs/evidence/phase5b2_remediation/reproduce.py --out result.json   # P0-1, P0-2 (no Core needed)
  P0-3 Layer B (runner + Core) additionally needs Core b16179a + pandas/pyarrow; otherwise reported SKIPPED.
"""
from __future__ import annotations

import argparse
import inspect
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from strategy_agent.evaluation import phase4c as C  # noqa: E402
from strategy_agent.execution import controlled as CT  # noqa: E402
from strategy_agent.execution import testkit as X  # noqa: E402

C.EXECUTOR_MODULES = ()          # the process-level C8 import check is irrelevant to these attacks
NEW_API = "reservation_id" in inspect.signature(CT.ControlledRunnerAdapter.execute).parameters


def _git():
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    except OSError:
        return None


def _calls(backend):
    return getattr(backend, "calls", None)


def _uses(W):
    p = W["gov"] / "registry/research_authorization_uses.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


def _try(fn):
    try:
        out = fn()
        return {"outcome": "EXECUTED_OR_ACCEPTED", "detail": str(out)[:200]}
    except Exception as e:  # noqa: BLE001 - every refusal is evidence
        return {"outcome": "REFUSED", "error": f"{type(e).__name__}: {getattr(e, 'code', '')} {str(e)[:200]}"}


def _execute(adapter, doc, out_dir, *, use, reservation, task_id, attempt_id, id_only=False):
    """Call the PUBLIC adapter the way a direct caller can, on whichever API this commit exposes."""
    if NEW_API:
        if id_only:                    # the remediated API: only ids; the ledger must refuse a replay / forgery
            return adapter.execute(doc, out_dir, reservation_id=reservation.get("reservation_id"), task_id=task_id,
                                   attempt_id=attempt_id)
        # a caller can still try to hand over objects; the remediated adapter must refuse them
        return adapter.execute(doc, out_dir, reservation_id=reservation.get("reservation_id"), task_id=task_id,
                               attempt_id=attempt_id, use=use, reservation=reservation)
    return adapter.execute(doc, out_dir, use=use, reservation=reservation)


# ------------------------------------------------------------------------------------------------------- P0-1
def p0_1(work: Path) -> dict:
    W = X.world(work / "p01")
    o = X.orchestrator(W)
    tid = X.submit_and_approve(W, o)
    st = o.dispatch(tid)
    be, ad = W["backend"], o.adapters["RESEARCH_ONLY_REAL"]
    r = o.budget.for_task(tid)
    after_legit = _calls(be)
    use = next(u for u in _uses(W) if u["run_id"] == r["planned_run_id"])
    doc = o.request_of(tid)
    attempt = f"{tid}:1"
    out = {"legit_dispatch_state": st["state"], "runner_calls_after_legit_dispatch": after_legit,
           "ledger_state_after_legit": r["state"]}
    # (a) replay the same reservation + use 3x through the public adapter
    reps = []
    for i in range(3):
        reps.append(_try(lambda i=i: _execute(ad, doc, work / f"replay_{i}", use=use, reservation={**r},
                                              task_id=tid, attempt_id=attempt)))
    out["replay_same_reservation_x3"] = reps
    if NEW_API:
        out["replay_same_reservation_id_only_x3"] = [
            _try(lambda i=i: _execute(ad, doc, work / f"replay_id_{i}", use=use, reservation=r, task_id=tid,
                                      attempt_id=attempt, id_only=True)) for i in range(3)]
    out["runner_calls_after_replay"] = _calls(be)
    # (b) forged reservation + forged use (never in the ledger)
    forged_use = {"run_id": "20991231_235959_forged0", "record_sha256": "f" * 64, "authorization_id": "RA-FORGED"}
    forged_res = {"reservation_id": "BR-" + "0" * 24, "task_id": "T-FORGED", "planned_run_id": forged_use["run_id"]}
    out["forged_reservation"] = _try(lambda: _execute(ad, doc, work / "forged", use=forged_use, reservation=forged_res,
                                                      task_id="T-FORGED", attempt_id="T-FORGED:1"))
    if NEW_API:
        out["forged_reservation_id_only"] = _try(lambda: _execute(ad, doc, work / "forged_id", use=None,
                                                                  reservation=forged_res, task_id="T-FORGED",
                                                                  attempt_id="T-FORGED:1", id_only=True))
    out["runner_calls_after_forged"] = _calls(be)
    # (c) caller claims COMMITTED for the real reservation id
    fake = {**r, "state": "COMMITTED", "history": ["RESERVED", "COMMITTED"]}
    out["fake_committed_status"] = _try(lambda: _execute(ad, doc, work / "fake_committed", use=use, reservation=fake,
                                                         task_id=tid, attempt_id=attempt))
    out["runner_calls_total"] = _calls(be)
    out["uses_records"] = len(_uses(W))
    out["ledger_state_final"] = o.budget.for_task(tid)["state"]
    out["VULNERABLE"] = out["runner_calls_total"] > 1
    return out


# ------------------------------------------------------------------------------------------------------- P0-2
def _tamper(kind: str):
    def hook(o, task_id):
        st = o.log.get(task_id)
        ref = o._execution_reference(st)
        arch = Path(ref["archive_path"])
        if kind == "receipt_deleted":
            Path(ref["receipt_path"]).unlink()
        elif kind == "receipt_modified":
            p = Path(ref["receipt_path"])
            d = json.loads(p.read_text())
            d["consumed_batches"] = 0
            p.chmod(0o644)
            p.write_text(json.dumps(d))
        elif kind == "archive_file_removed":
            (arch / "trades.csv").unlink()
        elif kind == "archive_manifest_corrupted":
            mp = arch / "MANIFEST.json"
            m = json.loads(mp.read_text())
            m["files_sha256"]["trades.csv"] = "0" * 64
            mp.write_text(json.dumps(m))
    return hook


def p0_2(work: Path) -> dict:
    out = {}
    for kind in ("receipt_deleted", "receipt_modified", "archive_file_removed", "archive_manifest_corrupted"):
        W = X.world(work / f"p02_{kind}")
        o = X.orchestrator(W)
        tid = X.submit_and_approve(W, o)
        hook, orig = _tamper(kind), o._archive

        def archive_then_tamper(task_id, doc, _orig=orig, _hook=hook, _o=o):
            _orig(task_id, doc)                       # tampering happens after the archive, BEFORE settlement
            _hook(_o, task_id)
        o._archive = archive_then_tamper
        try:
            st = o.dispatch(tid)["state"]
        except Exception as e:  # noqa: BLE001
            st = f"RAISED {type(e).__name__}: {e}"[:200]
        r = o.budget.for_task(tid)
        out[kind] = {"task_state": st, "ledger_state": r["state"], "history": r["history"],
                     "runner_calls": _calls(W["backend"]), "settled": r["state"] == "CONSUMED"}
    out["VULNERABLE"] = any(v["settled"] for v in out.values() if isinstance(v, dict))
    return out


# ------------------------------------------------------------------------------------------------------- P0-3
def p0_3_layer_a(work: Path) -> dict:
    """Wrong Core / spec / manifest pins on the request: does the controlled precheck notice?"""
    W = X.world(work / "p03a")
    ad = (CT.ControlledRunnerAdapter(W["backend"], governance_root=W["gov"], artifact_root=W["art"]) if NEW_API
          else CT.ControlledRunnerAdapter(W["backend"], governance_root=W["gov"]))
    out = {}
    for field, bad in (("core_commit", "0" * 40), ("strategy_spec_sha256", "0" * 64), ("dataset_manifest_sha256", "0" * 64)):
        doc = {**W["bound"], field: bad}
        out[field] = {"precheck_problems": ad.precheck(doc)}
        out[field]["passed_precheck"] = out[field]["precheck_problems"] == []
    out["VULNERABLE"] = any(v["passed_precheck"] for v in out.values() if isinstance(v, dict))
    return out


def p0_3_layer_b(work: Path) -> dict:
    from strategy_agent.execution import core_world as CW
    ok, why = CW.core_available()
    if not ok:
        return {"status": "SKIPPED", "reason": why}
    from strategy_agent.orchestration import request as Q
    from strategy_agent.phase5a import acceptance as A5
    g40 = A5.code_identity()["git_sha"]
    out = {"core": why}
    for field, bad in (("core_commit", "0" * 40), ("strategy_spec_sha256", "0" * 64), ("dataset_manifest_sha256", "0" * 64)):
        w = CW.build(work / f"p03b_{field}")
        be = CT.ResearchOnlyRunnerBackend(w["runner"], config_path=w["config_path"], snapshot_root=w["snapshot_root"],
                                          git_commit=g40, synthetic_snapshot=True,
                                          run_context={**w["run_context"], "agent_git_sha": g40})
        task = "T-" + "c" * 20
        if NEW_API:
            o = CW.orchestrator(w, work / f"p03b_orch_{field}", be)
            ad, led = o.adapters["RESEARCH_ONLY_REAL"], o.budget
            base = CW.bound_request(w)
            binding = w["binding"]["binding_id"]
        else:
            led = X.ledger_for(w["gov"], clock=lambda w=w: w["now"], git_commit=g40)
            ad = CT.ControlledRunnerAdapter(be, governance_root=w["gov"])
            base, binding = CW.request_for(w), None
        doc = {**base, field: bad}
        rec = {"precheck_problems": ad.precheck(doc)}
        try:
            led.register_executor("worker-1", actor="repro")
            r = led.reserve(authorization_id=w["authorization_id"], request_sha256=Q.request_hash(doc), task_id=task,
                            attempt_id=f"{task}:1", approval_evidence_sha256="d" * 64, binding_id=binding,
                            worker_id="worker-1", planned_run_id=ad.planned_run_id(task, w["now"]))
            com = led.commit(r["reservation_id"], worker_id="worker-1", actor="repro",
                             scenarios=doc["cost_profile_ref"]["scenarios"])
            rec["direct_execute"] = _try(lambda: _execute(ad, doc, work / f"p03b_exec_{field}", use=com["use"],
                                                          reservation=r, task_id=task, attempt_id=f"{task}:1",
                                                          id_only=True))
        except Exception as e:  # noqa: BLE001
            rec["direct_execute"] = {"outcome": "REFUSED", "error": f"{type(e).__name__}: {str(e)[:200]}"}
        if NEW_API:          # and through the orchestrator: the tampered doc is not the bound request
            s_ = o.submit(doc, actor="TEST_FIXTURE_SUBMITTER", actor_kind="TEST_FIXTURE")
            rec["orchestrator_submit_state"] = s_["state"]
        rec["runner_calls"] = be.calls
        rec["passed_precheck"] = rec["precheck_problems"] == []
        rec["runner_executed_with_wrong_pin"] = be.calls > 0
        out[field] = rec
    out["VULNERABLE"] = any(v.get("passed_precheck") or v.get("runner_executed_with_wrong_pin")
                            for v in out.values() if isinstance(v, dict))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    work = Path(tempfile.mkdtemp(prefix="p5b2_repro_"))
    res = {"kind": "PHASE5B2_SECURITY_REPRODUCTION", "provenance": "RE-DERIVED from the remediation brief; Codex's "
           "original report/reproducer unavailable to Claude", "git_head": _git(), "adapter_api":
           "LEDGER_AUTHORITATIVE_RESERVATION_ID" if NEW_API else "CALLER_SUPPLIED_USE_AND_RESERVATION",
           "python": sys.version.split()[0]}
    for name, fn in (("P0_1_reservation_replay_forgery", p0_1), ("P0_2_receipt_archive_integrity", p0_2),
                     ("P0_3_identity_layer_a_precheck", p0_3_layer_a), ("P0_3_identity_layer_b_runner", p0_3_layer_b)):
        try:
            res[name] = fn(work)
        except Exception as e:  # noqa: BLE001
            res[name] = {"status": "ERROR", "error": f"{type(e).__name__}: {e}"[:400]}
    shutil.rmtree(work, ignore_errors=True)
    Path(a.out).write_text(json.dumps(res, indent=2, sort_keys=True, default=str) + "\n")
    print(json.dumps({k: (v.get("VULNERABLE") if isinstance(v, dict) else v) for k, v in res.items()}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
