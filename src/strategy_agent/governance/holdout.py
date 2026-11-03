"""Holdout lock book (read-only for agents) and guard.

Decision Q7 (2026-11-03): the Final Holdout is approved and locked in advance
by the research lead. Agents can neither create nor change a lock and cannot
test against it repeatedly.

Controls
--------
1. ``governance/holdout_locks.jsonl`` is a hash-chained log. This module has no
   write API. Locks are added only with ``holdout_admin`` by a person listed in
   ``governance/approvers.json``, committed through a PR reviewed by the research lead; CI rejects edits to
   existing lines (``append_only``). CODEOWNERS lists the intended reviewer, but GitHub does NOT enforce it: main has no branch protection or ruleset (verified 2026-11-03).
2. Every entry must name an allow-listed approver and an approval reference,
   otherwise loading fails closed.
3. ``HoldoutGuard`` rules H01–H08 are checked before any experiment is queued.
4. Single use (H06) is enforced by ``HoldoutReservations.reserve``: an atomic
   compare-and-append made before QUEUED and never released.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from strategy_agent.registry.chain import ChainedLog, ConditionFailed

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_LOCKS = ROOT / "governance" / "holdout_locks.jsonl"
DEFAULT_APPROVERS = ROOT / "governance" / "approvers.json"


class LockBookInvalid(RuntimeError):
    pass


@dataclass(frozen=True)
class HoldoutLock:
    lock_id: str
    dataset_id: str
    snapshot_id: str
    holdout_start: datetime
    holdout_end: datetime | None     # None = through end of snapshot
    approved_by: str
    approval_ref: str


@dataclass(frozen=True)
class GuardDecision:
    allowed: bool
    code: str | None = None
    message: str = ""


def _ts(s: str) -> datetime:
    t = datetime.fromisoformat(s)
    if t.tzinfo is None:
        raise LockBookInvalid(f"timestamp without UTC offset: {s}")
    return t


class HoldoutBook:
    def __init__(self, locks_path: str | Path = DEFAULT_LOCKS, approvers_path: str | Path = DEFAULT_APPROVERS):
        self._log = ChainedLog(locks_path)
        self._approvers = set(json.loads(Path(approvers_path).read_text(encoding="utf-8"))["research_leads"])
        self.locks = self.build(self._log.verify(), self._approvers)

    @staticmethod
    def build(records: list[dict], approvers: set[str]) -> dict[str, HoldoutLock]:
        """Validate a sequence of lock events; used for loading AND for pre-append dry runs."""
        active: dict[str, HoldoutLock] = {}
        for n, r in enumerate(records):
            r = dict(r, seq=r.get("seq", n))
            if r.get("approved_by") not in approvers:
                raise LockBookInvalid(f"seq {r['seq']}: approver {r.get('approved_by')!r} not allow-listed")
            if not str(r.get("approval_ref", "")).startswith("https://"):
                raise LockBookInvalid(f"seq {r['seq']}: approval_ref must be a URL")
            if r["action"] == "APPROVE":
                if r["lock_id"] in active:
                    raise LockBookInvalid(f"duplicate lock_id {r['lock_id']}")
                if any(l.dataset_id == r["dataset_id"] for l in active.values()):
                    raise LockBookInvalid(f"dataset {r['dataset_id']} already has an active lock")
                end = _ts(r["holdout_end"]) if r.get("holdout_end") else None
                start = _ts(r["holdout_start"])
                if end is not None and end <= start:
                    raise LockBookInvalid(f"{r['lock_id']}: empty holdout window")
                active[r["lock_id"]] = HoldoutLock(r["lock_id"], r["dataset_id"], r["snapshot_id"], start, end,
                                                   r["approved_by"], r["approval_ref"])
            elif r["action"] == "REVOKE":
                # a revoked lock stays revoked; its holdout is NOT released for development
                if r["lock_id"] not in active:
                    raise LockBookInvalid(f"revoke of unknown lock {r['lock_id']}")
                lock = active.pop(r["lock_id"])
                active[f"REVOKED:{lock.lock_id}"] = lock
            else:
                raise LockBookInvalid(f"unknown action {r['action']}")
        return active

    def for_dataset(self, dataset_id: str) -> HoldoutLock | None:
        for k, l in self.locks.items():
            if l.dataset_id == dataset_id and not k.startswith("REVOKED:"):
                return l
        return None

    def all_windows(self, dataset_id: str) -> list[HoldoutLock]:
        """Active AND revoked windows: development may never enter either."""
        return [l for l in self.locks.values() if l.dataset_id == dataset_id]


class HoldoutReservations:
    """Atomic, permanent single-use reservations: one per (lock_id, family_id).

    ``reserve`` is a compare-and-append under the log's exclusive lock, so of
    any number of concurrent attempts exactly one succeeds. A reservation is
    written BEFORE the experiment is queued and is never released: FAILED,
    TIMEOUT, CANCELLED, a contract rejection or a worker crash all consume it.
    """

    def __init__(self, path: str | Path):
        self.log = ChainedLog(path)

    def reserved(self, lock_id: str, family_id: str) -> dict | None:
        for r in self.log.verify():
            if r["lock_id"] == lock_id and r["family_id"] == family_id:
                return r
        return None

    def reserve(self, *, lock_id: str, family_id: str, experiment_id: str, spec_sha256: str,
                actor: str, at: str) -> dict:
        def refuse(records):
            for r in records:
                if r["lock_id"] == lock_id and r["family_id"] == family_id:
                    return f"family {family_id} already reserved holdout {lock_id} ({r['experiment_id']})"
            return None
        return self.log.append_if({"lock_id": lock_id, "family_id": family_id, "experiment_id": experiment_id,
                                   "spec_sha256": spec_sha256, "actor": actor, "reserved_at": at}, refuse)


class HoldoutGuard:
    def __init__(self, book: HoldoutBook, reservations: HoldoutReservations):
        self.book, self.reservations = book, reservations

    def check(self, *, spec: dict, family_id: str, role: str, dataset_ids: list[str],
              window_start: str, window_end: str, cost_multiplier: float, holdout_access: bool) -> GuardDecision:
        ws, we = _ts(window_start), _ts(window_end)
        is_holdout = role == "FINAL_HOLDOUT"
        if holdout_access != is_holdout:
            return GuardDecision(False, "H08", "holdout_access flag must be true iff role == FINAL_HOLDOUT")
        for ds in dataset_ids:
            lock = self.book.for_dataset(ds)
            if lock is None:
                return GuardDecision(False, "H01", f"dataset {ds} has no research-lead approved holdout lock")
            if spec["experiment"]["holdout_lock_id"] != lock.lock_id:
                return GuardDecision(False, "H02", f"spec holdout_lock_id {spec['experiment']['holdout_lock_id']!r} "
                                                   f"!= approved lock {lock.lock_id!r}")
            if not is_holdout:
                for w in self.book.all_windows(ds):
                    w_end = w.holdout_end or datetime.max.replace(tzinfo=ws.tzinfo)
                    if ws < w_end and we > w.holdout_start:
                        return GuardDecision(False, "H03", f"development window overlaps holdout {w.lock_id} "
                                                           f"[{w.holdout_start.isoformat()}, ...)")
                continue
            if spec["status"] != "FROZEN":
                return GuardDecision(False, "H04", "final holdout requires a FROZEN spec")
            if ws != lock.holdout_start or (lock.holdout_end is not None and we != lock.holdout_end):
                return GuardDecision(False, "H05", "final holdout window must equal the locked window exactly")
            prior = self.reservations.reserved(lock.lock_id, family_id)
            if prior:   # early, non-authoritative check; the atomic reservation decides (runner)
                return GuardDecision(False, "H06", f"family {family_id} already used holdout {lock.lock_id} "
                                                   f"({prior['experiment_id']})")
            if cost_multiplier != 1:
                return GuardDecision(False, "H07", "final holdout is evaluated once, at 1x cost")
        return GuardDecision(True)
