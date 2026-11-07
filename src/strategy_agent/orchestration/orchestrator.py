"""Research Orchestrator (Phase 5-A1).

Research Request -> Validation -> Governance -> Approval -> Execution Dispatch -> Evaluation -> Registry / Archive

Deterministic and fail closed. Never approves on its own, never consumes or resets a research budget, never unlocks a
holdout, never imports the research-only runner or Core, never calls a broker. Evaluation is the existing Phase 4-C
acceptance (``evaluation.phase4c``) over the Phase 4-B evaluator; no PnL is recomputed here.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from strategy_agent.evaluation import phase4c as C

from . import request as Q
from .adapters import (AdapterDisabled, ArchiveReplayAdapter, ExecutionFailed, ResearchOnlyRunnerAdapter,
                       SyntheticExecutionAdapter)
from .governance import Governance
from .tasks import IllegalTransition, TaskLog, task_id_for

ROOT = Q.ROOT
TPE = ZoneInfo("Asia/Taipei")
ARCHIVE_FILES = ("research_request.json", "task_events.jsonl", "governance_decision.json", "execution_reference.json",
                 "evaluation_reference.json", "research_decision.json", "run_info.txt")


def code_identity(root: Path = ROOT) -> dict:
    try:
        sha = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        dirty = bool(subprocess.check_output(["git", "-C", str(root), "status", "--porcelain"], text=True).strip())
    except (OSError, subprocess.CalledProcessError):
        sha, dirty = "0" * 40, True
    return {"git_sha": sha, "dirty": dirty}


def _write_once(path: Path, data: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "xb") as fh:                     # O_EXCL: never overwrites
        fh.write(data)
    return C.sha256_bytes(data)


def _dumps(obj) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True, default=str, ensure_ascii=False) + "\n").encode()


class Orchestrator:
    def __init__(self, state_dir, archive_root, *, source_archive_root=None, root: Path = ROOT, governance_root=None,
                 clock=None, test_mode: bool = False, synthetic_fixture=None, benchmark=None, worker: str = "worker-1",
                 lease_seconds: int = 900):
        self.state_dir, self.archive_root = Path(state_dir).resolve(), Path(archive_root).resolve()
        self.root = Path(root).resolve()
        if test_mode and (self.root in self.state_dir.parents or self.root in self.archive_root.parents):
            raise ValueError("test orchestrator state/archive must live outside the repository")
        if not test_mode and synthetic_fixture is not None:
            raise ValueError("synthetic fixtures are only accepted by a test orchestrator")
        self.test_mode = test_mode
        self.clock = clock or (lambda: datetime.now(TPE))
        self.gov = Governance(self.root, governance_root=governance_root, clock=self.clock, benchmark=benchmark)
        self.governance_root = Path(governance_root or root)
        self.log = TaskLog(self.state_dir / "task_events.jsonl", self.clock)
        self.worker, self.lease_seconds = worker, lease_seconds
        self.adapters = {"ARCHIVE_REPLAY": ArchiveReplayAdapter(source_archive_root or self.archive_root),
                         "SYNTHETIC_TEST": SyntheticExecutionAdapter(synthetic_fixture if test_mode else None),
                         "RESEARCH_ONLY_REAL": ResearchOnlyRunnerAdapter()}
        self.code = code_identity(self.root)

    # ------------------------------------------------------------------------------------------------ helpers
    def _sys(self, task_id, to, **kw):
        return self.log.transition(task_id, to, actor="orchestrator", actor_kind="SYSTEM", **kw)

    def request_of(self, task_id) -> dict:
        st = self.log.get(task_id)
        if st is None:
            raise IllegalTransition("UNKNOWN_TASK", task_id)
        return st.events[0]["data"]["request"]

    def status(self, task_id) -> dict:
        st = self.log.get(task_id)
        if st is None:
            raise IllegalTransition("UNKNOWN_TASK", task_id)
        return {"task_id": task_id, "state": st.state, "request_hash": st.request_hash, "attempt": st.attempt,
                "lease_owner": st.lease_owner, "lease_expires_at": st.lease_expires_at,
                "history": [{k: e.get(k) for k in ("seq", "from", "to", "at", "actor", "actor_kind", "reason")}
                            for e in st.events],
                "last_data": st.last.get("data")}

    # ---------------------------------------------------------------------------------------------- lifecycle
    def submit(self, doc: dict, *, actor: str, actor_kind: str = "HUMAN") -> dict:
        """PROPOSED -> VALIDATED -> PENDING_APPROVAL, or BLOCKED with reasons. Same request => same task."""
        if not isinstance(doc, dict):
            raise IllegalTransition("REQUEST_NOT_OBJECT", "request must be a JSON object")
        h = Q.request_hash(doc)
        existing = self.log.by_request_hash(h)
        if existing:
            return {"task_id": existing.task_id, "state": existing.state, "duplicate": True}
        tid = task_id_for(h)
        meta = {"request": doc, "request_id": doc.get("request_id"), "strategy_family": (doc.get("strategy_family") or {}).get("family_id"),
                "dataset_snapshot_id": doc.get("dataset_snapshot_id"), "authorization_ref": doc.get("authorization_ref"),
                "orchestrator_git_sha": self.code["git_sha"], "orchestrator_dirty": self.code["dirty"], "test_mode": self.test_mode}
        try:
            self.log.transition(tid, "PROPOSED", actor=actor, actor_kind=actor_kind, data=meta, request_hash=h,
                                idempotency_key=f"{tid}:PROPOSED")
        except IllegalTransition as e:
            if e.code in ("DUPLICATE_REQUEST", "TASK_EXISTS"):         # lost a race with a concurrent submit
                st = self.log.by_request_hash(h)
                return {"task_id": st.task_id, "state": st.state, "duplicate": True}
            raise
        errs = Q.validate(doc)
        if errs:
            self._sys(tid, "BLOCKED", reason="REQUEST_SCHEMA_INVALID", data={"errors": errs}, idempotency_key=f"{tid}:BLOCKED:schema")
            return {"task_id": tid, "state": "BLOCKED", "errors": errs}
        self._sys(tid, "VALIDATED", data={"pending_fields": Q.pending_fields(doc)}, idempotency_key=f"{tid}:VALIDATED")
        dec = self.gov.check(doc, test_mode=self.test_mode)
        if dec.status not in ("PASS", "TEST_MODE_ONLY"):
            self._sys(tid, "BLOCKED", reason="GOVERNANCE_BLOCKED", data={"governance": dec.as_dict()},
                      idempotency_key=f"{tid}:BLOCKED:governance")
            return {"task_id": tid, "state": "BLOCKED", "governance": dec.as_dict()}
        self._sys(tid, "PENDING_APPROVAL", data={"governance": dec.as_dict()}, idempotency_key=f"{tid}:PENDING_APPROVAL")
        return {"task_id": tid, "state": "PENDING_APPROVAL", "governance": dec.as_dict()}

    def approve(self, task_id: str, *, approver: str, approval_ref: str) -> dict:
        """A human research lead approves. Never called by the orchestrator itself."""
        doc = self.request_of(task_id)
        if self.test_mode:
            if approver != "TEST_FIXTURE":
                raise IllegalTransition("APPROVER", "a test orchestrator accepts only the TEST_FIXTURE approver")
            kind = "TEST_FIXTURE"
        else:
            if approver not in self.gov.approvers():
                raise IllegalTransition("APPROVER", f"{approver} is not an allow-listed research lead")
            if not str(approval_ref).startswith("https://"):
                raise IllegalTransition("APPROVAL_REF", "approval_ref must be an https URL")
            kind = "HUMAN"
        dec = self.gov.check(doc, test_mode=self.test_mode)              # re-checked at approval time
        if dec.status not in ("PASS", "TEST_MODE_ONLY"):
            raise IllegalTransition("GOVERNANCE_BLOCKED", json.dumps(dec.blockers, default=str))
        return self.log.transition(task_id, "APPROVED", actor=approver, actor_kind=kind, expect_from="PENDING_APPROVAL",
                                   data={"approval_ref": approval_ref, "governance": dec.as_dict()},
                                   idempotency_key=f"{task_id}:APPROVED")

    def cancel(self, task_id: str, *, actor: str, reason: str) -> dict:
        return self.log.transition(task_id, "CANCELLED", actor=actor, actor_kind="HUMAN", reason=reason,
                                   idempotency_key=f"{task_id}:CANCELLED")

    def retry(self, task_id: str, *, actor: str, reason: str) -> dict:
        """Explicit retry of a FAILED task; refused for any budget-consuming adapter (no silent re-consumption)."""
        st = self.log.get(task_id)
        doc = self.request_of(task_id)
        adapter = self.adapters[doc["execution_mode"]]
        if st.state != "FAILED":
            raise IllegalTransition("ILLEGAL_TRANSITION", f"retry only from FAILED (is {st.state})")
        if adapter.consumes_budget:
            raise IllegalTransition("RETRY_WOULD_CONSUME", "a budget-consuming task is never retried")
        return self.log.transition(task_id, "QUEUED", actor=actor, actor_kind="HUMAN", reason=reason,
                                   data={"retry_of_attempt": st.attempt}, idempotency_key=f"{task_id}:RETRY:{st.attempt}")

    def dispatch(self, task_id: str) -> dict:
        """APPROVED -> QUEUED -> RUNNING -> EVALUATING -> EVALUATED -> ARCHIVED (or FAILED / BLOCKED)."""
        st = self.log.get(task_id)
        if st is None:
            raise IllegalTransition("UNKNOWN_TASK", task_id)
        doc = self.request_of(task_id)
        adapter = self.adapters[doc["execution_mode"]]
        if st.state == "APPROVED":
            if not adapter.enabled:
                self._sys(task_id, "BLOCKED", reason="ADAPTER_DISABLED", data={"adapter": adapter.kind},
                          idempotency_key=f"{task_id}:BLOCKED:adapter")
                return self.status(task_id)
            self._sys(task_id, "QUEUED", idempotency_key=f"{task_id}:QUEUED", expect_from="APPROVED")
            st = self.log.get(task_id)
        if st.state == "QUEUED":
            dec = self.gov.check(doc, test_mode=self.test_mode)           # fresh check right before running
            if dec.status not in ("PASS", "TEST_MODE_ONLY") or not adapter.enabled:
                self._sys(task_id, "BLOCKED", reason="GOVERNANCE_RECHECK_BLOCKED" if adapter.enabled else "ADAPTER_DISABLED",
                          data={"governance": dec.as_dict()}, idempotency_key=f"{task_id}:BLOCKED:recheck:{st.attempt}")
                return self.status(task_id)
            attempt = st.attempt + 1
            self.log.transition(task_id, "RUNNING", actor="orchestrator", actor_kind="SYSTEM", worker=self.worker,
                                lease_seconds=self.lease_seconds, expect_from="QUEUED",
                                data={"attempt": attempt, "adapter": adapter.kind, "governance": dec.as_dict()},
                                idempotency_key=f"{task_id}:RUNNING:{attempt}")
            exec_dir = self.state_dir / "executions" / task_id / f"attempt_{attempt}"
            try:
                ref = adapter.execute(doc, exec_dir)
            except (ExecutionFailed, AdapterDisabled, OSError) as e:
                self._sys(task_id, "FAILED", reason=getattr(e, "code", type(e).__name__), worker=self.worker,
                          data={"diagnostics": str(e), "attempt": attempt, "consumed_batches": 0},
                          idempotency_key=f"{task_id}:FAILED:{attempt}")
                return self.status(task_id)
            self.log.transition(task_id, "EVALUATING", actor="orchestrator", actor_kind="SYSTEM", worker=self.worker,
                                lease_seconds=self.lease_seconds, expect_from="RUNNING",
                                data={"execution_reference": ref, "attempt": attempt},
                                idempotency_key=f"{task_id}:EVALUATING:{attempt}")
            st = self.log.get(task_id)
        if st.state == "EVALUATING":
            self._evaluate(task_id, doc, st)
            st = self.log.get(task_id)
        if st.state == "EVALUATED":
            self._archive(task_id, doc)
        return self.status(task_id)

    def _execution_reference(self, st) -> dict:
        return next(e["data"]["execution_reference"] for e in reversed(st.events)
                    if e["to"] == "EVALUATING" and "execution_reference" in e.get("data", {}))

    def _evaluate(self, task_id, doc, st):
        ref = self._execution_reference(st)
        at = self.clock().isoformat(timespec="seconds")
        acc = C.accept(ref["archive_path"], expected_manifest_sha256=ref["archive_manifest_sha256"],
                       evaluator_commit_sha=self.code["git_sha"], evaluated_at=at, governance_root=self.governance_root)
        name = f"{C.evaluation_dir_name(at, self.code['git_sha'])}_{task_id[-8:]}_a{st.attempt}"
        out = self.archive_root / ("test_evaluations" if self.test_mode else "evaluations") / name
        files = C.write_acceptance(acc, out, evaluator_commit_sha=self.code["git_sha"], evaluated_at=at)
        ev = acc.get("evaluation") or {}
        decision = {"strategy_verdict": ev.get("strategy_verdict", "BLOCKED"),
                    "evidence_sufficiency": ev.get("evidence_sufficiency", "UNDETERMINED"),
                    "engineering_status": ev.get("engineering_status", "BLOCKED"),
                    "acceptance_status": acc["status"],
                    "acceptance_checks": {k: v["status"] for k, v in acc.get("checks", {}).items()},
                    "risk_flags": sorted({f["code"] for f in ev.get("risk_flags", [])}),
                    "blocked_reason": (acc.get("error") or {}).get("code") if acc["status"] == "BLOCKED" else None,
                    "evidence": ref.get("evidence"), "test_mode": self.test_mode}
        if acc["status"] != "PASS":                      # required evidence missing / inconsistent => never KEEP
            decision["strategy_verdict"] = "RETEST" if decision["strategy_verdict"] in ("KEEP", None) else decision["strategy_verdict"]
            decision["engineering_status"] = "BLOCKED" if acc["status"] == "BLOCKED" else decision["engineering_status"]
            decision["evidence_sufficiency"] = "UNDETERMINED"
        if self.test_mode:
            decision["label"] = "SYNTHETIC_TEST — engineering evidence only, not a research decision"
        self.log.transition(task_id, "EVALUATED", actor="orchestrator", actor_kind="SYSTEM", worker=self.worker,
                            expect_from="EVALUATING",
                            data={"evaluation_reference": {"dir": str(out), "files_sha256": files, "evaluated_at": at,
                                                           "evaluator_commit": self.code["git_sha"],
                                                           "input_sha256": ev.get("input_sha256")},
                                  "research_decision": decision},
                            idempotency_key=f"{task_id}:EVALUATED:{st.attempt}")

    def _archive(self, task_id, doc):
        st = self.log.get(task_id)
        if st.state != "EVALUATED":                       # checked before any directory is created
            raise IllegalTransition("ILLEGAL_TRANSITION", f"archive only from EVALUATED (is {st.state})")
        ev = next(e for e in reversed(st.events) if e["to"] == "EVALUATED")
        gov = next(e["data"]["governance"] for e in reversed(st.events) if e["to"] == "RUNNING")
        now = self.clock()
        out = self.archive_root / ("test_tasks" if self.test_mode else "tasks") / \
            f"{now.astimezone(TPE):%Y%m%d_%H%M%S}_{self.code['git_sha'][:7]}"
        events = [e for e in self.log.records() if e["task_id"] == task_id]
        files = {
            "research_request.json": _dumps(doc),
            "task_events.jsonl": "".join(json.dumps(e, sort_keys=True, ensure_ascii=False) + "\n" for e in events).encode(),
            "governance_decision.json": _dumps(gov),
            "execution_reference.json": _dumps(self._execution_reference(st)),
            "evaluation_reference.json": _dumps(ev["data"]["evaluation_reference"]),
            "research_decision.json": _dumps(ev["data"]["research_decision"]),
        }
        info = {"kind": "PHASE5A1_RESEARCH_TASK", "task_id": task_id, "request_id": doc["request_id"],
                "request_hash": st.request_hash, "execution_mode": doc["execution_mode"], "test_mode": self.test_mode,
                "orchestrator_git_sha": self.code["git_sha"], "orchestrator_dirty": self.code["dirty"],
                "strategy_family": doc["strategy_family"]["family_id"], "dataset_snapshot_id": doc["dataset_snapshot_id"],
                "authorization_ref": doc["authorization_ref"], "attempt": st.attempt, "archived_at": now.isoformat(),
                "consumed_batches": 0, "real_backtest_executed": False, "broker_api_called": False,
                "formal_market_status": gov.get("formal_market_status"), "trading_permission": False}
        files["run_info.txt"] = "".join(f"{k}={v if isinstance(v, str) else json.dumps(v)}\n" for k, v in info.items()).encode()
        out.mkdir(parents=True, exist_ok=False)          # an existing archive is never reused or overwritten
        hashes = {n: _write_once(out / n, b) for n, b in files.items()}
        self._sys(task_id, "ARCHIVED", expect_from="EVALUATED", data={"task_archive": str(out), "files_sha256": hashes},
                  idempotency_key=f"{task_id}:ARCHIVED")

    def recover(self) -> list[dict]:
        """After a crash: RUNNING with an expired lease -> FAILED (never silently re-executed);
        EVALUATING with an expired lease -> evaluation resumed (read-only, consumes nothing)."""
        out = []
        now = self.clock()
        for tid in self.log.task_ids():
            st = self.log.get(tid)
            if st.state not in ("RUNNING", "EVALUATING") or not st.lease_expires_at:
                if st.state == "EVALUATED":
                    self._archive(tid, self.request_of(tid))
                    out.append({"task_id": tid, "action": "ARCHIVED_AFTER_RESTART"})
                continue
            if datetime.fromisoformat(st.lease_expires_at) > now:
                continue
            if st.state == "RUNNING":
                self._sys(tid, "FAILED", reason="CRASH_RECOVERY_LEASE_EXPIRED", worker=self.worker,
                          data={"diagnostics": f"worker {st.lease_owner} lease expired at {st.lease_expires_at} before an "
                                               "execution reference was recorded; not re-executed", "consumed_batches": 0},
                          idempotency_key=f"{tid}:FAILED:recovery:{st.attempt}")
                out.append({"task_id": tid, "action": "FAILED_NOT_REEXECUTED"})
            else:
                self.log.transition(tid, "EVALUATING", actor="orchestrator", actor_kind="SYSTEM", worker=self.worker,
                                    lease_seconds=self.lease_seconds, expect_from="EVALUATING",
                                    data={"execution_reference": self._execution_reference(st), "resumed": True},
                                    idempotency_key=f"{tid}:EVALUATING:resume:{st.events[-1]['seq']}")
                self.dispatch(tid)
                out.append({"task_id": tid, "action": "EVALUATION_RESUMED"})
        return out
