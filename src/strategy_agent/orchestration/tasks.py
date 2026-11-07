"""Task state machine + append-only, hash-chained task event log (``registry.chain.ChainedLog``).

The log is the only source of truth: a task's state is the ``to`` of its last event. Every transition is a
compare-and-append under the log's exclusive file lock, so two workers can never both move a task out of the same
state, an idempotency key is honoured at most once, and a lease held by another live worker blocks the transition.
Nothing is ever updated or deleted; a crash leaves either the old event or the new one, never a partial state.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from strategy_agent.registry.chain import ChainedLog, ConditionFailed

STATES = ("PROPOSED", "VALIDATED", "PENDING_APPROVAL", "APPROVED", "QUEUED", "RUNNING", "EVALUATING", "EVALUATED",
          "ARCHIVED", "BLOCKED", "FAILED", "CANCELLED")
TERMINAL = {"ARCHIVED", "BLOCKED", "CANCELLED"}          # FAILED is terminal unless an explicit, non-consuming retry
# to-state -> (allowed from-states, precondition names checked by the orchestrator before appending)
TRANSITIONS: dict[str, tuple[frozenset, tuple[str, ...]]] = {
    "PROPOSED": (frozenset({None}), ("request_hash_unique",)),
    "VALIDATED": (frozenset({"PROPOSED"}), ("schema_valid", "semantics_valid")),
    "PENDING_APPROVAL": (frozenset({"VALIDATED"}), ("governance_pass",)),
    "APPROVED": (frozenset({"PENDING_APPROVAL"}), ("human_research_lead", "https_approval_ref", "governance_pass")),
    "QUEUED": (frozenset({"APPROVED", "FAILED"}), ("adapter_enabled", "retry_allowed_if_from_failed")),
    "RUNNING": (frozenset({"QUEUED"}), ("governance_recheck_pass", "adapter_enabled", "single_claim_lease")),
    "EVALUATING": (frozenset({"RUNNING", "EVALUATING"}), ("execution_reference_recorded", "lease_owner_or_expired")),
    "EVALUATED": (frozenset({"EVALUATING"}), ("evaluation_recorded", "lease_owner")),
    "ARCHIVED": (frozenset({"EVALUATED"}), ("task_archive_written",)),
    "BLOCKED": (frozenset({"PROPOSED", "VALIDATED", "PENDING_APPROVAL", "APPROVED", "QUEUED"}), ("reason_given",)),
    "FAILED": (frozenset({"RUNNING", "EVALUATING", "EVALUATED", "QUEUED"}), ("reason_given",)),
    "CANCELLED": (frozenset({"PROPOSED", "VALIDATED", "PENDING_APPROVAL", "APPROVED", "QUEUED"}), ("reason_given",)),
}
LEASED = {"RUNNING", "EVALUATING"}


class IllegalTransition(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class TaskState:
    task_id: str
    state: str
    events: tuple
    request_hash: str
    attempt: int
    lease_owner: str | None
    lease_expires_at: str | None

    @property
    def last(self) -> dict:
        return self.events[-1]


def _events_for(records: list[dict], task_id: str) -> list[dict]:
    return [r for r in records if r.get("task_id") == task_id]


def _state(records: list[dict], task_id: str) -> TaskState | None:
    ev = _events_for(records, task_id)
    if not ev:
        return None
    last = ev[-1]
    attempt = sum(1 for e in ev if e["to"] == "RUNNING")
    return TaskState(task_id, last["to"], tuple(ev), ev[0]["request_hash"], attempt,
                     last.get("lease_owner") if last["to"] in LEASED else None,
                     last.get("lease_expires_at") if last["to"] in LEASED else None)


class TaskLog:
    def __init__(self, path, clock):
        self.log = ChainedLog(path)
        self.clock = clock

    def records(self) -> list[dict]:
        return self.log.verify()

    def get(self, task_id: str) -> TaskState | None:
        return _state(self.records(), task_id)

    def by_request_hash(self, request_hash: str) -> TaskState | None:
        recs = self.records()
        ids = [r["task_id"] for r in recs if r.get("to") == "PROPOSED" and r.get("request_hash") == request_hash]
        return _state(recs, ids[0]) if ids else None

    def task_ids(self) -> list[str]:
        return list(dict.fromkeys(r["task_id"] for r in self.records()))

    def transition(self, task_id: str, to: str, *, actor: str, actor_kind: str, reason: str | None = None,
                   data: dict | None = None, request_hash: str | None = None, idempotency_key: str | None = None,
                   worker: str | None = None, lease_seconds: int | None = None, expect_from: str | None = None) -> dict:
        """Append one transition atomically. Raises IllegalTransition (fail closed) on any violation."""
        if to not in TRANSITIONS:
            raise IllegalTransition("UNKNOWN_STATE", to)
        if actor_kind not in ("HUMAN", "SYSTEM", "TEST_FIXTURE"):
            raise IllegalTransition("ACTOR_KIND", actor_kind)
        if to in ("BLOCKED", "FAILED", "CANCELLED") and not reason:
            raise IllegalTransition("REASON_REQUIRED", f"{to} needs a reason")
        allowed, _pre = TRANSITIONS[to]
        now = self.clock()
        key = idempotency_key or f"{task_id}:{to}:{now.isoformat()}"
        outcome: dict = {}

        def refuse(records):
            if any(r.get("idempotency_key") == key for r in records):
                prior = next(r for r in records if r.get("idempotency_key") == key)
                outcome["duplicate"] = prior
                return f"IDEMPOTENT_REPLAY:{key}"
            cur = _state(records, task_id)
            frm = cur.state if cur else None
            if to == "PROPOSED":
                if cur is not None:
                    return f"TASK_EXISTS:{task_id}"
                if any(r.get("to") == "PROPOSED" and r.get("request_hash") == request_hash for r in records):
                    return "DUPLICATE_REQUEST"
            if frm not in allowed:
                return f"ILLEGAL_TRANSITION:{frm}->{to}"
            if expect_from is not None and frm != expect_from:
                return f"STALE_STATE:expected {expect_from}, found {frm}"
            if cur and cur.lease_owner and cur.lease_owner != worker:
                if datetime.fromisoformat(cur.lease_expires_at) > now:
                    return f"LEASE_HELD:{cur.lease_owner} until {cur.lease_expires_at}"
            payload["from"] = frm                       # decided under the lock, not before it
            if payload["request_hash"] is None and cur:
                payload["request_hash"] = cur.request_hash
            return None

        payload = {"task_id": task_id, "from": None, "to": to, "at": now.isoformat(), "actor": actor,
                   "actor_kind": actor_kind, "reason": reason, "data": data or {}, "idempotency_key": key,
                   "request_hash": request_hash}
        if to in LEASED:
            payload["lease_owner"] = worker
            payload["lease_expires_at"] = (now + timedelta(seconds=lease_seconds or 900)).isoformat()
        try:
            return self.log.append_if(payload, refuse)
        except ConditionFailed as e:
            msg = str(e)
            if msg.startswith("IDEMPOTENT_REPLAY"):
                return outcome["duplicate"]          # retried call: same effect, nothing appended twice
            code = msg.split(":", 1)[0]
            raise IllegalTransition(code, msg) from None


def task_id_for(request_hash: str) -> str:
    return "T-" + request_hash[:20]


def default_paths(state_dir) -> dict:
    d = Path(state_dir)
    return {"events": d / "task_events.jsonl"}
