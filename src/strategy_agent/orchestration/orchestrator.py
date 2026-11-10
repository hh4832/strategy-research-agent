"""Research Orchestrator (Phase 5-A1).

Research Request -> Validation -> Governance -> Approval -> Execution Dispatch -> Evaluation -> Registry / Archive

Deterministic and fail closed. Never approves on its own, never consumes or resets a research budget, never unlocks a
holdout, never imports the research-only runner or Core, never calls a broker. Evaluation is the existing Phase 4-C
acceptance (``evaluation.phase4c``) over the Phase 4-B evaluator; no PnL is recomputed here.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from strategy_agent.approval import ApprovalRejected, Expectation, evidence_sha256
from strategy_agent.evaluation import phase4c as C
from strategy_agent.execution import identity as I
from strategy_agent.registry.chain import ChainedLog, ConditionFailed

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
        sha = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True,
                                      stderr=subprocess.DEVNULL).strip()
        dirty = bool(subprocess.check_output(["git", "-C", str(root), "status", "--porcelain"], text=True,
                                             stderr=subprocess.DEVNULL).strip())
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
                 lease_seconds: int = 900, approval_verifier=None, controlled_adapter=None):
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
        self.verifier = approval_verifier
        if self.gov.production_books and approval_verifier is not None and not approval_verifier.production:
            raise ValueError("the repository's governance books accept only the production (live GitHub) verifier")
        self.budget = None
        if controlled_adapter is not None:                    # 5-B2: the only budget-consuming execution path
            if not getattr(controlled_adapter, "controlled", False):
                raise ValueError("only a ControlledRunnerAdapter may replace the RESEARCH_ONLY_REAL adapter")
            if test_mode:
                raise ValueError("controlled execution runs in a production-mode orchestrator (verified approvals)")
            mode = getattr(controlled_adapter, "mode", "DISABLED")
            if not controlled_adapter.enabled or (self.gov.production_books and mode != "REAL_GATED"):
                # never even create a budget ledger next to the real books unless the real path is fully gated
                raise ValueError("controlled execution is DISABLED for production books / non-synthetic backends")
            if mode == "REAL_GATED":
                # 5-B3-B standing preconditions (no request yet): the gate's executor is THIS worker, production books
                # need the production verifier, and an approved NEW authorization naming this executor must exist
                # (its budget is checked per request by A07, so a consumed batch can still be recovered). Today's repository books have none => refused before any ledger file exists.
                from strategy_agent.execution.activation import eligible_authorizations  # noqa: PLC0415
                if controlled_adapter.activation.executor_id != worker:
                    raise ValueError("REAL_GATED: the orchestrator worker is not the activation gate's executor")
                if self.gov.production_books and (approval_verifier is None or not approval_verifier.production):
                    raise ValueError("REAL_GATED on production books requires the production (live GitHub) verifier")
                if not eligible_authorizations(self.governance_root, worker, self.clock()):
                    raise ValueError("REAL_GATED: no approved, active, unexpired authorization names this executor "
                                     "(real Runner stays DISABLED)")
            from strategy_agent.execution.budget import BudgetLedger  # noqa: PLC0415
            self.adapters["RESEARCH_ONLY_REAL"] = controlled_adapter
            self.budget = BudgetLedger.for_governance(self.gov, clock=self.clock, git_commit=self.code["git_sha"])
            # P0-1: the adapter executes only against THIS ledger; approval evidence comes from THIS task log
            self.gov.gated_real_execution = mode == "REAL_GATED"
            controlled_adapter.bind(ledger=self.budget, artifact_root=self.root, approvals=self._running_approval,
                                    worker_id=self.worker, clock=self.clock)
            # G10 may pass only for an ENABLED controlled adapter on ISOLATED books (Governance re-checks the books)
            self.gov.isolated_controlled_execution = bool(controlled_adapter.enabled and not self.gov.production_books)

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
        out = {"task_id": task_id, "state": st.state, "request_hash": st.request_hash, "attempt": st.attempt,
               "lease_owner": st.lease_owner, "lease_expires_at": st.lease_expires_at,
               "history": [{k: e.get(k) for k in ("seq", "from", "to", "at", "actor", "actor_kind", "reason")}
                           for e in st.events],
               "last_data": st.last.get("data")}
        if self.budget is not None:                              # the ledger, not the task state, says if it settled
            try:
                r = self.budget.for_task(task_id)
                out["budget_state"] = r["state"] if r else None
            except Exception as e:  # noqa: BLE001 - a corrupted ledger is reported, never hidden
                out["budget_state"] = f"LEDGER_UNREADABLE:{getattr(e, 'code', type(e).__name__)}"
        return out

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

    def approve(self, task_id: str, *, approver: str | None = None, approval_ref: str | None = None,
                evidence_ref: str | None = None) -> dict:
        """A human research lead approves. Never called by the orchestrator itself.

        Test mode: only the TEST_FIXTURE approver (synthetic tasks only). Production (5-B1): ``evidence_ref`` (or
        ``approval_ref``) must be a GitHub comment that the ApprovalEvidenceVerifier accepts for THIS task, request,
        authorization and scope; the approver identity is the verified GitHub author, never a caller string."""
        doc = self.request_of(task_id)
        data: dict = {}
        dec = self.gov.check(doc, test_mode=self.test_mode)              # re-checked at approval time (no network)
        if dec.status not in ("PASS", "TEST_MODE_ONLY"):
            raise IllegalTransition("GOVERNANCE_BLOCKED", json.dumps(dec.blockers, default=str))
        if self.test_mode:
            if approver != "TEST_FIXTURE":
                raise IllegalTransition("APPROVER", "a test orchestrator accepts only the TEST_FIXTURE approver")
            kind, actor = "TEST_FIXTURE", "TEST_FIXTURE"
            data["approval_ref"] = approval_ref
        else:
            ev = self._verify_task_approval(task_id, doc, evidence_ref or approval_ref)
            actor = ev["author"]["login"]
            if approver is not None and approver != actor:
                raise IllegalTransition("APPROVER_MISMATCH", f"declared approver {approver!r} != verified author {actor!r}")
            self._claim_evidence(ev, task_id)
            kind = "HUMAN"
            data.update(approval_ref=ev["comment"]["url"], approval_evidence=ev,
                        approval_evidence_sha256=evidence_sha256(ev), approval_trust=ev["approval_source"]["trust"])
        data["governance"] = dec.as_dict()
        return self.log.transition(task_id, "APPROVED", actor=actor, actor_kind=kind, expect_from="PENDING_APPROVAL",
                                   data=data, idempotency_key=f"{task_id}:APPROVED")

    # ------------------------------------------------------------------------------- 5-B1 approval evidence
    def _verify_task_approval(self, task_id, doc, url) -> dict:
        if self.verifier is None:
            raise IllegalTransition("APPROVAL_VERIFIER_REQUIRED", "production approval needs an ApprovalEvidenceVerifier "
                                    "(GitHub comment evidence); a caller-declared approver is not accepted")
        if not url:
            raise IllegalTransition("APPROVAL_EVIDENCE_REQUIRED", "evidence_ref (GitHub comment URL) is required")
        try:
            return self.verifier.verify(url, Expectation.for_request(doc, intent="APPROVE_TASK", task_id=task_id))
        except ApprovalRejected as e:
            raise IllegalTransition("APPROVAL_" + e.code, str(e)) from None

    def _claim_evidence(self, ev: dict, task_id: str) -> None:
        """One comment approves one task (defence in depth: the token already names the task and request hash)."""
        log = ChainedLog(self.state_dir / "approval_evidence_uses.jsonl")
        cid = ev["comment"]["id"]

        def refuse(recs):
            for r in recs:
                if r["comment_id"] == cid:
                    return None if r["task_id"] == task_id else "EVIDENCE_REUSED"
            return None
        try:
            if any(r["comment_id"] == cid and r["task_id"] == task_id for r in (log.verify() if log.path.exists() else [])):
                return
            log.append_if({"comment_id": cid, "evidence_id": ev["evidence_id"], "task_id": task_id,
                           "request_sha256": ev["subject"]["request_sha256"], "intent": ev["intent"]}, refuse)
        except ConditionFailed as e:
            raise IllegalTransition("APPROVAL_EVIDENCE_REUSED", str(e)) from None

    def _recheck_approval(self, task_id, doc, st) -> str | None:
        """Right before running: the task approval and any binding approval must still verify (not deleted, edited,
        revoked; the task approval also not expired). Returns a blocking reason or None."""
        if self.test_mode:
            return None
        appr = next((e for e in reversed(st.events) if e["to"] == "APPROVED"), None)
        ev = (appr or {}).get("data", {}).get("approval_evidence")
        if not ev:
            return "APPROVAL_EVIDENCE_MISSING"
        if self.verifier is None:
            return "APPROVAL_VERIFIER_REQUIRED"
        try:
            self.verifier.verify(ev["comment"]["url"], Expectation.for_request(doc, intent="APPROVE_TASK", task_id=task_id))
        except ApprovalRejected as e:
            return "APPROVAL_" + e.code
        adapter = self.adapters[doc["execution_mode"]]
        if adapter.consumes_budget and ev["approval_source"]["trust"] != "PRODUCTION" and \
                not (self.budget is not None and self.gov.isolated_controlled_execution and not self.gov.production_books):
            return "APPROVAL_TRUST_NOT_PRODUCTION"            # ISOLATED_TEST evidence only ever spends isolated budget
        from strategy_agent.binding.store import BindingStore  # noqa: PLC0415
        rec = BindingStore(self.governance_root).for_bound(Q.request_hash(doc))
        if rec is not None:
            bev = json.loads((self.governance_root / rec["approval_evidence_path"]).read_text(encoding="utf-8"))
            try:
                src = dict(doc, authorization_ref="PENDING")
                self.verifier.verify(bev["comment"]["url"], Expectation("BIND_AUTHORIZATION", rec["source_request_sha256"],
                                                                        rec["authorization_id"], rec["scope_sha256"]),
                                     check_expiry=False)
                if Q.request_hash(src) != rec["source_request_sha256"]:
                    return "BINDING_PARENT_MISMATCH"
            except ApprovalRejected as e:
                return "BINDING_APPROVAL_" + e.code
        return None

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
        """APPROVED -> QUEUED -> RUNNING -> EVALUATING -> EVALUATED -> ARCHIVED (or FAILED / BLOCKED).

        M3: one dispatcher per task at a time (non-blocking exclusive file lock across threads and processes); a
        second dispatcher returns the current status without touching the task. The RUNNING claim also carries a
        nonce, so even without the lock a worker that did not create the RUNNING event never executes."""
        import fcntl  # noqa: PLC0415
        lock_dir = self.state_dir / "locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        fh = open(lock_dir / f"{task_id}.dispatch.lock", "a+")  # noqa: SIM115
        try:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {**self.status(task_id), "dispatch": "IN_PROGRESS_ELSEWHERE"}
            return self._dispatch_locked(task_id)
        finally:
            fh.close()

    def _dispatch_locked(self, task_id: str) -> dict:
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
            why = self._recheck_approval(task_id, doc, st)
            if why:
                self._sys(task_id, "BLOCKED", reason=why, data={"governance": dec.as_dict()},
                          idempotency_key=f"{task_id}:BLOCKED:approval:{st.attempt}")
                return self.status(task_id)
            if adapter.consumes_budget:
                if self.budget is None or not getattr(adapter, "controlled", False):
                    self._sys(task_id, "BLOCKED", reason="BUDGET_RESERVATION_REQUIRED",
                              data={"adapter": adapter.kind}, idempotency_key=f"{task_id}:BLOCKED:reservation:{st.attempt}")
                    return self.status(task_id)
                return self._dispatch_controlled(task_id, doc, st, dec, adapter)
            attempt = st.attempt + 1
            claim = __import__("uuid").uuid4().hex
            ev = self.log.transition(task_id, "RUNNING", actor="orchestrator", actor_kind="SYSTEM", worker=self.worker,
                                     lease_seconds=self.lease_seconds, expect_from="QUEUED",
                                     data={"attempt": attempt, "adapter": adapter.kind, "governance": dec.as_dict(),
                                           "claim": claim},
                                     idempotency_key=f"{task_id}:RUNNING:{attempt}")
            if ev.get("data", {}).get("claim") != claim:          # replayed someone else's RUNNING: never execute
                return {**self.status(task_id), "dispatch": "CLAIMED_BY_ANOTHER_WORKER"}
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
        self._settle(task_id)
        return self.status(task_id)

    # ------------------------------------------------------------------------- 5-B2 controlled execution
    def _approval_evidence_sha(self, st) -> str:
        appr = next((e for e in reversed(st.events) if e["to"] == "APPROVED"), None)
        return (appr or {}).get("data", {}).get("approval_evidence_sha256") or ""

    def _running_approval(self, task_id, reservation_id) -> str | None:
        """Approval evidence sha of a task that is RUNNING for exactly this reservation (else None => no grant)."""
        st = self.log.get(task_id)
        if st is None or st.state != "RUNNING":
            return None
        run = next((e for e in reversed(st.events) if e["to"] == "RUNNING"), None)
        if (run or {}).get("data", {}).get("reservation_id") != reservation_id:
            return None
        return self._approval_evidence_sha(st) or None

    def _reconcile(self, task_id, reservation_id, reason):
        try:
            self.budget.require_reconciliation(reservation_id, reason=reason, worker_id=self.worker)
        except Exception:  # noqa: BLE001 - the task outcome is still recorded below; budget stays consumed
            pass

    def _dispatch_controlled(self, task_id, doc, st, dec, adapter) -> dict:
        """QUEUED -> [precheck] -> reserve -> commit -> RUNNING -> execute -> receipt -> EVALUATING -> ... -> settle.
        Every step fails closed; after the commit nothing is ever released or re-run automatically."""
        from strategy_agent.binding.store import BindingStore  # noqa: PLC0415
        from strategy_agent.execution.budget import BudgetRefused  # noqa: PLC0415

        def block(reason, data=None, key=None):
            self._sys(task_id, "BLOCKED", reason=reason, data=data or {}, idempotency_key=f"{task_id}:BLOCKED:{key or reason}")
            return self.status(task_id)
        problems = adapter.precheck(doc)                              # identity / PIT pins before any budget
        if problems:
            return block("CONTROLLED_PRECHECK_FAILED", {"problems": problems})
        rec = BindingStore(self.governance_root).for_bound(Q.request_hash(doc))
        if rec is None:
            return block("BINDING_REQUIRED")
        attempt = st.attempt + 1
        try:
            self.budget.register_executor(self.worker, actor="orchestrator")
            r = self.budget.reserve(authorization_id=doc["authorization_ref"]["authorization_id"],
                                    request_sha256=Q.request_hash(doc), task_id=task_id,
                                    attempt_id=f"{task_id}:{attempt}", approval_evidence_sha256=self._approval_evidence_sha(st),
                                    binding_id=rec["binding_id"], worker_id=self.worker,
                                    planned_run_id=adapter.planned_run_id(task_id, self.clock()),
                                    # P0-2 residual: commit the task archive's research_request.json bytes BEFORE execution
                                    request_file_sha256=hashlib.sha256(I.archive_request_bytes(doc)).hexdigest())
        except BudgetRefused as e:
            return block("BUDGET_" + e.code, {"detail": str(e)})
        if r.get("state") != "RESERVED":                             # replayed reservation already past RESERVED
            self._reconcile(task_id, r["reservation_id"], "dispatch found a reservation beyond RESERVED")
            return block("RESERVATION_NOT_FRESH", {"reservation": r["reservation_id"], "state": r.get("state")})
        rid = r["reservation_id"]
        try:
            com = self.budget.commit(rid, worker_id=self.worker, actor=self.worker,
                                     scenarios=list(doc["cost_profile_ref"]["scenarios"]))
        except BudgetRefused as e:
            try:
                self.budget.release(rid, reason=f"commit refused: {e.code}", worker_id=self.worker)
            except BudgetRefused:
                self._reconcile(task_id, rid, f"commit refused and release refused: {e.code}")
            return block("BUDGET_" + e.code, {"detail": str(e), "reservation_id": rid})
        claim = __import__("uuid").uuid4().hex
        ev = self.log.transition(task_id, "RUNNING", actor="orchestrator", actor_kind="SYSTEM", worker=self.worker,
                                 lease_seconds=self.lease_seconds, expect_from="QUEUED",
                                 data={"attempt": attempt, "adapter": adapter.kind, "governance": dec.as_dict(),
                                       "claim": claim, "reservation_id": rid, "use_record_sha256": com["use"]["record_sha256"],
                                       "budget_consistency": "SINGLE_EXECUTOR_ONLY"},
                                 idempotency_key=f"{task_id}:RUNNING:{attempt}")
        if ev.get("data", {}).get("claim") != claim:
            self._reconcile(task_id, rid, "RUNNING claim replayed after commit")
            return {**self.status(task_id), "dispatch": "CLAIMED_BY_ANOTHER_WORKER"}
        exec_dir = self.state_dir / "executions" / task_id / f"attempt_{attempt}"
        try:
            ref = adapter.execute(doc, exec_dir, reservation_id=rid, task_id=task_id, attempt_id=f"{task_id}:{attempt}",
                                  started_at=self.clock())
        except Exception as e:  # noqa: BLE001 - after commit: never release, never re-run
            self._reconcile(task_id, rid, f"execution failed after commit: {getattr(e, 'code', type(e).__name__)}")
            self._sys(task_id, "FAILED", reason=getattr(e, "code", type(e).__name__), worker=self.worker,
                      data={"diagnostics": str(e)[:500], "attempt": attempt, "consumed_batches": 1,
                            "reservation_id": rid, "budget_state": "RECONCILIATION_REQUIRED", "rerun": "NEVER"},
                      idempotency_key=f"{task_id}:FAILED:{attempt}")
            return self.status(task_id)
        self.log.transition(task_id, "EVALUATING", actor="orchestrator", actor_kind="SYSTEM", worker=self.worker,
                            lease_seconds=self.lease_seconds, expect_from="RUNNING",
                            data={"execution_reference": {**ref, "reservation_id": rid}, "attempt": attempt},
                            idempotency_key=f"{task_id}:EVALUATING:{attempt}")
        chk = adapter.verify_evidence(rid, doc=doc)                  # P0-2: from disk, before the evaluator reads it
        if not chk["ok"]:
            self._reconcile(task_id, rid, f"execution evidence invalid before evaluation: {chk['problems']}"[:400])
            self._sys(task_id, "FAILED", reason="EXECUTION_EVIDENCE_INVALID", worker=self.worker,
                      data={"problems": chk["problems"], "consumed_batches": 1, "reservation_id": rid,
                            "budget_state": "RECONCILIATION_REQUIRED", "rerun": "NEVER"},
                      idempotency_key=f"{task_id}:FAILED:evidence:{attempt}")
            return self.status(task_id)
        try:
            self._evaluate(task_id, doc, self.log.get(task_id))
            if self.log.get(task_id).state == "EVALUATED":
                self._archive(task_id, doc)
        except Exception as e:  # noqa: BLE001 - evaluator / archive failure: never re-run the strategy
            self._reconcile(task_id, rid, f"evaluation/archive failed: {type(e).__name__}")
            self._sys(task_id, "FAILED", reason="EVALUATION_OR_ARCHIVE_FAILED_NO_RERUN", worker=self.worker,
                      data={"diagnostics": str(e)[:500], "consumed_batches": 1, "reservation_id": rid,
                            "budget_state": "RECONCILIATION_REQUIRED", "rerun": "NEVER"},
                      idempotency_key=f"{task_id}:FAILED:eval:{attempt}")
            return self.status(task_id)
        self._settle(task_id)
        return self.status(task_id)

    def _settle(self, task_id) -> None:
        """ARCHIVED + COMMITTED reservation -> CONSUMED only after the receipt, archive, identity and evaluation
        reference are re-verified FROM DISK against the ledger (P0-2). Any failure: RECONCILIATION_REQUIRED (the
        batch stays consumed; nothing is released or re-run)."""
        if self.budget is None:
            return
        from strategy_agent.execution.budget import BudgetRefused  # noqa: PLC0415
        r = self.budget.for_task(task_id)
        st = self.log.get(task_id)
        if not r or r["state"] != "COMMITTED" or st is None or st.state != "ARCHIVED":
            return
        adapter = self.adapters["RESEARCH_ONLY_REAL"]
        arch = next((e for e in reversed(st.events) if e["to"] == "ARCHIVED"), {}).get("data", {})
        ev = next((e for e in reversed(st.events) if e["to"] == "EVALUATED"), {}).get("data", {})
        if not getattr(adapter, "controlled", False) or not arch.get("task_archive"):
            self._reconcile(task_id, r["reservation_id"], "archived without controlled adapter or task archive")
            return
        doc = self.request_of(task_id)
        kw = {"doc": doc, "evaluation_reference": ev.get("evaluation_reference"), "archive_ref": arch["task_archive"],
              "archived_files": arch.get("files_sha256")}
        chk = adapter.verify_evidence(r["reservation_id"], record=True, **kw)
        if not chk["ok"]:
            self._reconcile(task_id, r["reservation_id"], f"settlement refused: {chk['problems']}"[:400])
            return
        from strategy_agent.execution.controlled import verify_execution_evidence  # noqa: PLC0415

        def recheck(res, use):                    # INSIDE the ledger lock, right before CONSUMED (TOCTOU)
            return verify_execution_evidence(self.budget, r["reservation_id"], record=False, worker_id=self.worker,
                                             governance_root=self.governance_root, reservation=res, use=use,
                                             expected_mode=adapter.mode, **kw)["problems"]
        try:
            ev_c = self.budget.settle(r["reservation_id"], receipt_sha256=chk["receipt_sha256"],
                                      output_manifest_sha256=chk["output_manifest_sha256"],
                                      archive_ref=arch["task_archive"], worker_id=self.worker, recheck=recheck)
        except BudgetRefused as e:
            self._reconcile(task_id, r["reservation_id"], f"settlement refused: {e.code}")
            return
        self._execution_record(task_id, r["reservation_id"], ev_c, arch, ev)

    def _execution_record(self, task_id, reservation_id, consumed_event, arch, ev) -> None:
        """5-B3-B: one immutable record per settled execution (contract + ledger settlement + evidence pointers)."""
        r = self.budget.reservations()[reservation_id]
        st = self.log.get(task_id)
        ref = self._execution_reference(st)
        rc = json.loads(Path(ref["receipt_path"]).read_text(encoding="utf-8"))
        rec = {"kind": "PHASE5B3B_EXECUTION_RECORD", "task_id": task_id, "reservation_id": reservation_id,
               "execution_mode": rc.get("execution_mode"), "contract": rc.get("execution_contract"),
               "binding_id": r.get("binding_id"), "approval_evidence_sha256": r.get("approval_evidence_sha256"),
               "runner_receipt_file": ref["receipt_path"], "runner_receipt_sha256": ref["receipt_sha256"],
               "runner_archive": ref["archive_path"], "runner_archive_manifest_sha256": ref["archive_manifest_sha256"],
               "evaluation_reference": ev.get("evaluation_reference"), "task_archive": arch["task_archive"],
               "task_archive_files_sha256": (r.get("evidence_verified") or {}).get("task_archive_files_sha256"),
               "budget_settlement": {"state": r["state"], "history": r["history"],
                                     "consumed_event_sha256": consumed_event.get("record_sha256")},
               "agent_commit": self.code["git_sha"]}
        d = self.archive_root / "execution_records"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / f"{task_id}.json", "x", encoding="utf-8") as fh:                  # never overwritten
            fh.write(json.dumps(rec, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n")

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
                                                           "input_sha256": ev.get("input_sha256"),
                                                           "archive_path": ref["archive_path"],
                                                           "archive_manifest_sha256": ref["archive_manifest_sha256"]},
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
            f"{now.astimezone(TPE):%Y%m%d_%H%M%S}_{self.code['git_sha'][:7]}_{task_id}"
        events = [e for e in self.log.records() if e["task_id"] == task_id]
        files = {
            "research_request.json": _dumps(doc),
            "task_events.jsonl": "".join(json.dumps(e, sort_keys=True, ensure_ascii=False) + "\n" for e in events).encode(),
            "governance_decision.json": _dumps(gov),
            "execution_reference.json": _dumps(self._execution_reference(st)),
            "evaluation_reference.json": _dumps(ev["data"]["evaluation_reference"]),
            "research_decision.json": _dumps(ev["data"]["research_decision"]),
        }
        # 5-B3-C: the run_info states what actually happened (a controlled execution has COMMITTED its batch before
        # it can be archived; REAL_GATED + a non-synthetic backend is a real backtest). Other paths: 0 / false.
        adapter = self.adapters[doc["execution_mode"]]
        res = self.budget.for_task(task_id) if (self.budget is not None and getattr(adapter, "controlled", False)) else None
        consumed = 1 if res and res.get("state") in ("COMMITTED", "CONSUMED") else 0
        backend_kind = getattr(getattr(adapter, "backend", None), "kind", None)
        real_bt = bool(consumed and getattr(adapter, "mode", "") == "REAL_GATED"
                       and backend_kind == "RESEARCH_ONLY_RUNNER_REAL_SNAPSHOT")
        info = {"kind": "PHASE5A1_RESEARCH_TASK", "task_id": task_id, "request_id": doc["request_id"],
                "request_hash": st.request_hash, "execution_mode": doc["execution_mode"], "test_mode": self.test_mode,
                "orchestrator_git_sha": self.code["git_sha"], "orchestrator_dirty": self.code["dirty"],
                "strategy_family": doc["strategy_family"]["family_id"], "dataset_snapshot_id": doc["dataset_snapshot_id"],
                "authorization_ref": doc["authorization_ref"], "attempt": st.attempt, "archived_at": now.isoformat(),
                "consumed_batches": consumed, "real_backtest_executed": real_bt,
                "execution_backend": backend_kind or adapter.kind, "broker_api_called": False,
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
        if self.budget is not None:
            out += self.budget.recover(worker_id=self.worker)
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
                if self.budget is not None:                       # committed budget whose outcome is unknown
                    r = self.budget.for_task(tid)
                    if r and r["state"] in ("RESERVED", "COMMITTED"):
                        self._reconcile(tid, r["reservation_id"], "worker crashed while RUNNING (lease expired)")
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
