"""B2-1 Atomic Research Budget Ledger.

Built on the EXISTING books, not a parallel one:
  * authorization book  governance research_authorizations.jsonl (ResearchAuthorizationBook; read only)
  * uses log            the existing research_authorization_uses.jsonl: still THE authoritative consumption record
                        (same record format ``ResearchAuthorizationGuard.consume`` writes)
  * budget events       NEW append-only ChainedLog research_budget_events.jsonl: the reservation state machine

Critical section: every budget decision runs while holding the exclusive flock of the USES LOG - the same lock the
legacy ``guard.consume`` takes - so reservations, commits and any legacy consumption are serialised in one place.
Inside it the authority is re-read (authorization active / not expired, uses, all reservations) before anything is
written. Lock order is always uses-log -> events-log.

States (per reservation; AVAILABLE is the remaining capacity):
  RESERVED  capacity held, NO side effect yet          -> COMMITTED | RELEASED
  COMMITTED uses record written (budget consumed);    -> CONSUMED | RECONCILIATION_REQUIRED
            execution may start from here on
  CONSUMED  execution receipt + archive settled        (terminal)
  RELEASED  released before commit, proven side-effect free (planned run id absent from the uses log) (terminal)
  RECONCILIATION_REQUIRED  committed but outcome unknown / incomplete: capacity stays consumed, never auto re-run
Capacity used = |uses records of the authorization| + |RESERVED reservations without a uses record|.

Consistency scope: fcntl locks serialise threads and processes on ONE host / filesystem. They are NOT a
distributed lock across Colab runtimes or Google Drive. The ledger therefore admits exactly ONE registered executor
(SINGLE_EXECUTOR_ONLY): a second executor id is refused until the first is retired with no open reservation.
At-most-once dispatch is guaranteed per ledger; exactly-once execution is NOT claimed (it would need an idempotent
external runner with verifiable receipts end to end).

Security remediation (Codex P0-1 / P0-2 on PR #13). Execution progress is recorded as MARKER events that are only valid
while the reservation is COMMITTED (they never change the state):
  EXECUTION_STARTED    ``claim_execution``: the ONLY grant to run a backend. Written in the critical section after
                       re-verifying the reservation identity (request / task / authorization / binding / attempt /
                       approval evidence), the registered executor and the authoritative uses record. At most one per
                       reservation: a replay, a second process or a caller-claimed state is refused, never run again.
  EXECUTION_COMPLETED  receipt sha + output MANIFEST sha recorded right after the receipt is written
  EVIDENCE_VERIFIED    receipt + archive + identity re-verified from disk against the ledger (orchestrator)
``settle`` (COMMITTED -> CONSUMED) requires COMPLETED and VERIFIED with identical shas. A started execution without
verified evidence is never released and never re-run: it can only go to RECONCILIATION_REQUIRED.
Caller-supplied reservation / use objects are never trusted anywhere: only this ledger's folded state is.
"""
from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from strategy_agent.registry.chain import GENESIS, ChainedLog, ChainError, ConditionFailed, canonical, record_hash

OPEN = ("RESERVED", "COMMITTED", "RECONCILIATION_REQUIRED")
TERMINAL = ("CONSUMED", "RELEASED")
CONSISTENCY = "SINGLE_EXECUTOR_ONLY"
TRANSITIONS = {"COMMITTED": ("RESERVED",), "RELEASED": ("RESERVED",), "CONSUMED": ("COMMITTED",),
               "RECONCILIATION_REQUIRED": ("COMMITTED", "RESERVED")}
MARKERS = ("EXECUTION_STARTED", "EXECUTION_COMPLETED", "EVIDENCE_VERIFIED")    # valid only while COMMITTED
CLAIM_IDENTITY = ("request_sha256", "task_id", "authorization_id", "binding_id", "attempt_id", "approval_evidence_sha256")


class BudgetRefused(RuntimeError):
    def __init__(self, code: str, message: str = ""):
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


def _verify(recs: list[dict], path) -> None:
    prev = GENESIS
    for i, r in enumerate(recs):
        if r.get("seq") != i or r.get("prev_sha256") != prev or r.get("record_sha256") != record_hash(r):
            raise BudgetRefused("LEDGER_CORRUPTED", f"{path}: chain broken at seq {i}")
        prev = r["record_sha256"]


class BudgetLedger:
    def __init__(self, *, authorizations_path: Path, approvers_path: Path, uses_path: Path, events_path: Path,
                 clock, git_commit: str):
        self.auth_path, self.approvers_path = Path(authorizations_path), Path(approvers_path)
        self.uses_path, self.events_path = Path(uses_path), Path(events_path)
        self.clock, self.git_commit = clock, git_commit
        for p in (self.uses_path, self.events_path):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.touch(exist_ok=True)

    @classmethod
    def for_governance(cls, gov, *, clock, git_commit: str) -> "BudgetLedger":
        g = Path(gov.governance_root)
        return cls(authorizations_path=gov.paths["auth"], approvers_path=gov.paths["approvers"],
                   uses_path=gov.paths["uses"], events_path=g / "registry/research_budget_events.jsonl",
                   clock=clock, git_commit=git_commit)

    # ------------------------------------------------------------------------------------- critical section
    @contextmanager
    def _cs(self):
        """Exclusive lock on the uses log (shared with legacy guard.consume), then a verified authoritative view."""
        import fcntl  # noqa: PLC0415
        with open(self.uses_path, "a+", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                fh.seek(0)
                uses = [json.loads(ln) for ln in fh.read().splitlines() if ln.strip()]
                _verify(uses, self.uses_path)
                try:
                    events = ChainedLog(self.events_path).verify()
                except ChainError as e:
                    raise BudgetRefused("LEDGER_CORRUPTED", str(e)) from None
                yield _View(self, fh, uses, events)
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def _auth(self, authorization_id: str) -> dict:
        from strategy_agent.governance.research_authorization import (AuthorizationBookInvalid,  # noqa: PLC0415
                                                                      ResearchAuthorizationBook)
        try:
            a = ResearchAuthorizationBook(self.auth_path, self.approvers_path).get(authorization_id)
        except AuthorizationBookInvalid as e:
            raise BudgetRefused("AUTHORIZATION_BOOK_INVALID", str(e)) from None
        if a is None:
            raise BudgetRefused("AUTHORIZATION_NOT_ACTIVE", f"{authorization_id} is missing or revoked")
        exp = a.get("expires_at")
        if exp and datetime.fromisoformat(exp) <= self.clock():
            raise BudgetRefused("AUTHORIZATION_EXPIRED", exp)
        return a

    # ------------------------------------------------------------------------------------------- reads
    def events(self) -> list[dict]:
        return ChainedLog(self.events_path).verify()

    def reservations(self) -> dict[str, dict]:
        return _fold(self.events())

    def for_task(self, task_id: str) -> dict | None:
        return next((r for r in self.reservations().values() if r["task_id"] == task_id), None)

    def use_record(self, reservation_id: str) -> dict | None:
        """The authoritative uses record a COMMITTED reservation was granted (matched by its record sha)."""
        with self._cs() as v:
            r = v.get(reservation_id)
            return next((dict(u) for u in v.uses if u.get("record_sha256") == r.get("use_record_sha256")), None)

    def capacity(self, authorization_id: str) -> dict:
        with self._cs() as v:
            a = self._auth(authorization_id)
            return {"max_batches": a["max_batches"], **v.used(authorization_id)}

    # ------------------------------------------------------------------------------------------ writes
    def register_executor(self, executor_id: str, *, actor: str) -> dict:
        with self._cs() as v:
            cur = v.executor()
            if cur == executor_id:
                return {"executor_id": executor_id, "status": "ALREADY_REGISTERED"}
            if cur is not None:
                raise BudgetRefused("SECOND_EXECUTOR_REFUSED", f"{cur} is the registered executor ({CONSISTENCY})")
            return v.append({"event_type": "EXECUTOR_REGISTERED", "executor_id": executor_id, "actor": actor})

    def retire_executor(self, executor_id: str, *, actor: str) -> dict:
        with self._cs() as v:
            if v.executor() != executor_id:
                raise BudgetRefused("NOT_REGISTERED_EXECUTOR", executor_id)
            if any(r["state"] in OPEN for r in v.folded().values()):
                raise BudgetRefused("OPEN_RESERVATIONS", "settle, release or reconcile every reservation first")
            return v.append({"event_type": "EXECUTOR_RETIRED", "executor_id": executor_id, "actor": actor})

    def reserve(self, *, authorization_id: str, request_sha256: str, task_id: str, attempt_id: str,
                approval_evidence_sha256: str, binding_id: str | None, worker_id: str, planned_run_id: str,
                batch_units: int = 1) -> dict:
        if batch_units != 1:
            raise BudgetRefused("BATCH_UNITS", "exactly one batch per reservation")
        with self._cs() as v:
            if v.executor() != worker_id:
                raise BudgetRefused("EXECUTOR_NOT_REGISTERED", f"{worker_id} is not the registered executor "
                                    f"({v.executor()}); {CONSISTENCY}")
            folded = v.folded()
            mine = [r for r in folded.values() if r["task_id"] == task_id]
            if mine:                                              # retries / replays never get a second slot
                return {**mine[0], "idempotent": True}
            if any(r["request_sha256"] == request_sha256 and r["state"] != "RELEASED" for r in folded.values()):
                raise BudgetRefused("DUPLICATE_REQUEST", "this request already holds or used a reservation")
            if any(u.get("run_id") == planned_run_id for u in v.uses):
                raise BudgetRefused("RUN_ID_ALREADY_USED", planned_run_id)
            a = self._auth(authorization_id)
            used = v.used(authorization_id)
            if used["used"] + batch_units > a["max_batches"]:
                raise BudgetRefused("BUDGET_EXHAUSTED", f"{used['used']}/{a['max_batches']} used or reserved")
            rid = "BR-" + hashlib.sha256(f"{authorization_id}|{task_id}|{request_sha256}".encode()).hexdigest()[:24]
            v.append({"event_type": "RESERVED", "reservation_id": rid, "authorization_id": authorization_id,
                             "request_sha256": request_sha256, "task_id": task_id, "attempt_id": attempt_id,
                             "batch_units": batch_units, "approval_evidence_sha256": approval_evidence_sha256,
                             "binding_id": binding_id, "worker_id": worker_id, "planned_run_id": planned_run_id,
                             "authorization_record_sha256": a.get("record_sha256")})
            return {**v.get(rid), "idempotent": False}

    def commit(self, reservation_id: str, *, worker_id: str, actor: str, scenarios: list[str]) -> dict:
        """RESERVED -> COMMITTED: write the authoritative uses record (legacy format) in the same critical section."""
        with self._cs() as v:
            r = v.get(reservation_id)
            if r["state"] != "RESERVED":
                raise BudgetRefused("NOT_RESERVED", f"{reservation_id} is {r['state']}")
            if v.executor() != worker_id:
                raise BudgetRefused("EXECUTOR_NOT_REGISTERED", worker_id)
            a = self._auth(r["authorization_id"])                  # authority re-read right before consumption
            n = sum(1 for u in v.uses if u.get("authorization_id") == r["authorization_id"])
            if n >= a["max_batches"]:
                raise BudgetRefused("BUDGET_EXHAUSTED", f"uses {n}/{a['max_batches']}")
            use = v.append_use({"authorization_id": r["authorization_id"], "run_id": r["planned_run_id"],
                                "actor": actor, "reserved_at": self.clock().isoformat(), "scenarios": list(scenarios)})
            ev = v.append({"event_type": "COMMITTED", "reservation_id": reservation_id, "worker_id": worker_id,
                           "use_record_sha256": use["record_sha256"], "use_seq": use["seq"]}, r)
            return {"use": use, "event": ev}

    def release(self, reservation_id: str, *, reason: str, worker_id: str) -> dict:
        with self._cs() as v:
            r = v.get(reservation_id)
            if r["state"] != "RESERVED":
                raise BudgetRefused("RELEASE_REFUSED", f"{reservation_id} is {r['state']}: only an uncommitted "
                                    "reservation can be released")
            if any(u.get("run_id") == r["planned_run_id"] for u in v.uses):
                raise BudgetRefused("RELEASE_REFUSED", "a uses record exists for the planned run: side effect possible")
            return v.append({"event_type": "RELEASED", "reservation_id": reservation_id, "reason": reason,
                             "worker_id": worker_id, "proof": "planned_run_id absent from the uses log; never committed"}, r)

    def settle(self, reservation_id: str, *, receipt_sha256: str, output_manifest_sha256: str, archive_ref: str,
               worker_id: str) -> dict:
        with self._cs() as v:
            r = v.get(reservation_id)
            if r["state"] != "COMMITTED":
                raise BudgetRefused("SETTLE_REFUSED", f"{reservation_id} is {r['state']}")
            if v.executor() != worker_id:
                raise BudgetRefused("EXECUTOR_NOT_REGISTERED", worker_id)
            done, ver = r.get("execution_completed") or {}, r.get("evidence_verified") or {}
            if not r.get("execution_started") or not done or not ver:
                raise BudgetRefused("SETTLE_REFUSED", f"{reservation_id}: execution evidence not completed and verified")
            for k, val in (("receipt_sha256", receipt_sha256), ("output_manifest_sha256", output_manifest_sha256)):
                if not val or done.get(k) != val or ver.get(k) != val:
                    raise BudgetRefused("SETTLE_EVIDENCE_MISMATCH", f"{k} differs from the recorded execution evidence")
            return v.append({"event_type": "CONSUMED", "reservation_id": reservation_id, "worker_id": worker_id,
                             "receipt_sha256": receipt_sha256, "output_manifest_sha256": output_manifest_sha256,
                             "archive_ref": archive_ref}, r)

    def claim_execution(self, reservation_id: str, *, worker_id: str, expect: dict) -> dict:
        """The ONE grant to execute: COMMITTED + identity match + registered executor + authoritative uses record +
        no earlier EXECUTION_STARTED. Returns the ledger's own reservation and uses record (never the caller's)."""
        with self._cs() as v:
            r = v.get(reservation_id)
            if r["state"] != "COMMITTED":
                raise BudgetRefused("EXECUTION_NOT_ELIGIBLE", f"{reservation_id} is {r['state']} in the ledger")
            if r.get("execution_started"):
                raise BudgetRefused("EXECUTION_ALREADY_STARTED", f"{reservation_id} was already granted one execution "
                                    f"(attempt {r['execution_started'].get('attempt_id')}); never run again")
            if v.executor() != worker_id or r.get("worker_id") != worker_id:
                raise BudgetRefused("EXECUTOR_NOT_REGISTERED", f"{worker_id} ({CONSISTENCY})")
            diff = [k for k in CLAIM_IDENTITY if not r.get(k) or expect.get(k) != r.get(k)]
            if diff:
                raise BudgetRefused("RESERVATION_IDENTITY_MISMATCH", ",".join(diff))
            use = next((u for u in v.uses if u.get("record_sha256") == r.get("use_record_sha256")), None)
            if use is None or use.get("run_id") != r["planned_run_id"] or use.get("authorization_id") != r["authorization_id"] \
                    or use.get("seq") != r.get("use_seq"):
                raise BudgetRefused("USE_RECORD_MISSING", "no authoritative uses record for this reservation")
            ev = v.append({"event_type": "EXECUTION_STARTED", "reservation_id": reservation_id, "worker_id": worker_id,
                           "use_record_sha256": use["record_sha256"], "planned_run_id": r["planned_run_id"]}, r)
            return {"reservation": v.get(reservation_id), "use": dict(use), "event": ev}

    def record_completion(self, reservation_id: str, *, worker_id: str, receipt_sha256: str,
                          output_manifest_sha256: str, receipt_path: str) -> dict:
        with self._cs() as v:
            r = v.get(reservation_id)
            if r["state"] != "COMMITTED" or not r.get("execution_started") or r.get("execution_completed"):
                raise BudgetRefused("COMPLETION_REFUSED", f"{reservation_id} is {r['state']} "
                                    f"started={bool(r.get('execution_started'))} completed={bool(r.get('execution_completed'))}")
            return v.append({"event_type": "EXECUTION_COMPLETED", "reservation_id": reservation_id, "worker_id": worker_id,
                             "receipt_sha256": receipt_sha256, "output_manifest_sha256": output_manifest_sha256,
                             "receipt_path": str(receipt_path)}, r)

    def record_evidence_verified(self, reservation_id: str, *, worker_id: str, receipt_sha256: str,
                                 output_manifest_sha256: str, archive_ref: str, evaluation_input_sha256: str | None) -> dict:
        with self._cs() as v:
            r = v.get(reservation_id)
            done = r.get("execution_completed") or {}
            if r["state"] != "COMMITTED" or not done or r.get("evidence_verified"):
                raise BudgetRefused("VERIFY_REFUSED", f"{reservation_id} is {r['state']} completed={bool(done)}")
            if done.get("receipt_sha256") != receipt_sha256 or done.get("output_manifest_sha256") != output_manifest_sha256:
                raise BudgetRefused("SETTLE_EVIDENCE_MISMATCH", "verified evidence differs from the recorded completion")
            return v.append({"event_type": "EVIDENCE_VERIFIED", "reservation_id": reservation_id, "worker_id": worker_id,
                             "receipt_sha256": receipt_sha256, "output_manifest_sha256": output_manifest_sha256,
                             "archive_ref": archive_ref, "evaluation_input_sha256": evaluation_input_sha256}, r)

    def require_reconciliation(self, reservation_id: str, *, reason: str, worker_id: str) -> dict:
        with self._cs() as v:
            r = v.get(reservation_id)
            if r["state"] == "RECONCILIATION_REQUIRED":
                return r
            if r["state"] not in TRANSITIONS["RECONCILIATION_REQUIRED"]:
                raise BudgetRefused("RECONCILE_REFUSED", f"{reservation_id} is {r['state']}")
            return v.append({"event_type": "RECONCILIATION_REQUIRED", "reservation_id": reservation_id,
                             "reason": reason, "worker_id": worker_id}, r)

    def recover(self, *, worker_id: str) -> list[dict]:
        """Crash recovery, conservative: RESERVED whose planned run already has a uses record (crash between the uses
        append and the COMMITTED event) -> RECONCILIATION_REQUIRED. Nothing is released or re-run automatically."""
        out = []
        for r in list(self.reservations().values()):
            if r["state"] == "RESERVED":
                with self._cs() as v:
                    if any(u.get("run_id") == r["planned_run_id"] for u in v.uses):
                        cur = v.get(r["reservation_id"])
                        if cur["state"] == "RESERVED":
                            v.append({"event_type": "RECONCILIATION_REQUIRED", "reservation_id": r["reservation_id"],
                                      "reason": "uses record exists without COMMITTED event (crash during commit)",
                                      "worker_id": worker_id}, cur)
                            out.append({"reservation_id": r["reservation_id"], "action": "RECONCILIATION_REQUIRED"})
        return out


class _View:
    def __init__(self, ledger: BudgetLedger, fh, uses, events):
        self.l, self.fh, self.uses, self.events = ledger, fh, uses, events

    def executor(self):
        cur = None
        for e in self.events:
            if e["event_type"] == "EXECUTOR_REGISTERED":
                cur = e["executor_id"]
            elif e["event_type"] == "EXECUTOR_RETIRED" and e["executor_id"] == cur:
                cur = None
        return cur

    def folded(self):
        return _fold(self.events)

    def get(self, rid):
        r = self.folded().get(rid)
        if r is None:
            raise BudgetRefused("UNKNOWN_RESERVATION", rid)
        return r

    def used(self, aid):
        uses = [u for u in self.uses if u.get("authorization_id") == aid]
        run_ids = {u.get("run_id") for u in uses}
        held = [r for r in self.folded().values() if r["authorization_id"] == aid and r["state"] == "RESERVED"
                and r["planned_run_id"] not in run_ids]
        return {"used": len(uses) + len(held), "uses_records": len(uses), "reserved_uncommitted": len(held)}

    def append(self, payload: dict, prior: dict | None = None) -> dict:
        base = {"transaction_timestamp": self.l.clock().isoformat(), "git_commit": self.l.git_commit,
                "previous_event_sha256": prior["last_event_sha256"] if prior else None}
        if prior:
            for k in ("authorization_id", "request_sha256", "task_id", "attempt_id", "batch_units",
                      "approval_evidence_sha256", "binding_id"):
                base[k] = prior[k]
        rec = ChainedLog(self.l.events_path).append_if({**base, **payload}, lambda recs: None
                                                       if len(recs) == len(self.events) else "CONCURRENT_EVENT_WRITE")
        self.events.append(rec)
        return rec

    def append_use(self, payload: dict) -> dict:
        """Append to the uses log through the lock we already hold (same canonical chained format as ChainedLog)."""
        prev = self.uses[-1]["record_sha256"] if self.uses else GENESIS
        rec = dict(payload, seq=len(self.uses), prev_sha256=prev)
        rec["record_sha256"] = record_hash(rec)
        self.fh.seek(0, os.SEEK_END)
        self.fh.write(canonical(rec).decode("utf-8") + "\n")
        self.fh.flush()
        os.fsync(self.fh.fileno())
        self.uses.append(rec)
        return rec


def _fold(events: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for e in events:
        t = e["event_type"]
        if t == "RESERVED":
            out[e["reservation_id"]] = {**{k: e.get(k) for k in (
                "reservation_id", "authorization_id", "request_sha256", "task_id", "attempt_id", "batch_units",
                "approval_evidence_sha256", "binding_id", "worker_id", "planned_run_id")},
                "state": "RESERVED", "last_event_sha256": e["record_sha256"], "history": ["RESERVED"]}
        elif t in MARKERS:
            r = out.get(e["reservation_id"])
            key = {"EXECUTION_STARTED": "execution_started", "EXECUTION_COMPLETED": "execution_completed",
                   "EVIDENCE_VERIFIED": "evidence_verified"}[t]
            prereq = {"EXECUTION_STARTED": None, "EXECUTION_COMPLETED": "execution_started",
                      "EVIDENCE_VERIFIED": "execution_completed"}[t]
            if r is None or r["state"] != "COMMITTED" or r.get(key) or (prereq and not r.get(prereq)):
                raise BudgetRefused("LEDGER_INCONSISTENT", f"{t} for {e.get('reservation_id')} from "
                                    f"{None if r is None else r['state']}")
            r[key] = {k: e.get(k) for k in ("attempt_id", "worker_id", "use_record_sha256", "receipt_sha256",
                                            "output_manifest_sha256", "receipt_path", "archive_ref",
                                            "evaluation_input_sha256", "record_sha256") if e.get(k) is not None}
            r["last_event_sha256"] = e["record_sha256"]
            r["history"].append(t)
        elif t in TRANSITIONS:
            r = out.get(e["reservation_id"])
            if r is None or r["state"] not in TRANSITIONS[t]:
                raise BudgetRefused("LEDGER_INCONSISTENT", f"{t} for {e.get('reservation_id')} from "
                                    f"{None if r is None else r['state']}")
            r.update(state=t, last_event_sha256=e["record_sha256"])
            r["history"].append(t)
            for k in ("use_record_sha256", "use_seq", "receipt_sha256", "output_manifest_sha256", "archive_ref", "reason"):
                if k in e:
                    r[k] = e[k]
    return out


__all__ = ["BudgetLedger", "BudgetRefused", "CONSISTENCY", "CLAIM_IDENTITY", "MARKERS", "ConditionFailed"]
