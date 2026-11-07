"""RESEARCH_ONLY_EXPLORATORY authorization (Phase 4-A, contract research-only-authorization-v1).

A separate contract from formal market eligibility. It lets ONE bounded,
pre-specified, non-holdout exploratory run happen while every formal gate
stays exactly as it is (market NO_GO, TEMPORAL_SAFETY UNKNOWN, COST_APPROVAL
FAIL, FORMAL_EXECUTION_ELIGIBILITY FAIL). There is no ignore_gate / force_run
switch anywhere: the only way to run is to satisfy every rule below.

Controls
--------
* ``governance/research_authorizations.jsonl`` is a hash-chained log with no
  agent write API here. Events are appended only by
  ``research_authorization_admin`` (run by an allow-listed research lead),
  which validates before appending, exactly like ``holdout_admin``.
* ``ResearchAuthorizationGuard.check`` (RA01–RA12) is pure and fail-closed.
* ``ResearchAuthorizationGuard.consume`` reserves one batch slot atomically
  (``ChainedLog.append_if``) before any data is read; slots are never released.

Rules
-----
RA01 authorization exists, is APPROVED by an allow-listed lead with an https approval_ref, and is not revoked
RA02 now is within [issued_at, expires_at)
RA03 market / symbol match
RA04 dataset snapshot and producer SHA match
RA05 temporal policy pin matches the authorization AND the gate's recorded RESEARCH_ONLY acceptance
RA06 cost scenario-set file SHA matches; requested scenarios are a subset of the authorized ones
RA07 strategy spec file SHA, id, version and parameters (5/20) match; R02 is still enforced by the runner
RA08 warm-up and research window lie inside the authorized research window / development boundary
RA09 the approved holdout lock exists with exactly the authorized half-open boundaries and the
     development boundary ends at (or before) the holdout start; no warm-up, signal or fill in the holdout
RA10 formal gates are byte-identical to the pinned snapshot (status NO_GO); any change voids the authorization
RA11 batch budget: slots used < max_batches (atomic reservation)
RA12 request is RESEARCH_ONLY_EXPLORATORY with holdout_access == False
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from jsonschema import Draft202012Validator

from strategy_agent.registry.chain import ChainedLog, ConditionFailed

ROOT = Path(__file__).resolve().parents[3]
SCHEMA = ROOT / "schemas" / "RESEARCH_AUTHORIZATION_SCHEMA.json"
DEFAULT_BOOK = ROOT / "governance" / "research_authorizations.jsonl"
DEFAULT_USES = ROOT / "registry" / "research_authorization_uses.jsonl"
DEFAULT_APPROVERS = ROOT / "governance" / "approvers.json"
CHAIN_FIELDS = ("seq", "prev_sha256", "record_sha256")


class AuthorizationBookInvalid(RuntimeError):
    pass


def _ts(s: str) -> datetime:
    t = datetime.fromisoformat(s)
    if t.tzinfo is None:
        raise AuthorizationBookInvalid(f"timestamp without UTC offset: {s}")
    return t


def gates_sha256(gates: dict) -> str:
    """Hash of the formal gate STATUSES only (name -> status)."""
    flat = {k: (v["status"] if isinstance(v, dict) else v) for k, v in gates.items()}
    return hashlib.sha256(json.dumps(flat, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")))


def validate_events(events: list[dict], approvers: set[str]) -> dict[str, dict]:
    """Validate a sequence of events; return {authorization_id: approved record} for active ones.

    Used both for loading and for the admin tool's pre-append dry run.
    """
    v = _validator()
    active: dict[str, dict] = {}
    revoked: set[str] = set()
    for n, raw in enumerate(events):
        ev = {k: x for k, x in raw.items() if k not in CHAIN_FIELDS}
        errs = sorted(v.iter_errors(ev), key=lambda e: list(e.path))
        if errs:
            raise AuthorizationBookInvalid(f"event {n}: schema: {errs[0].message}")
        if ev["approved_by"] not in approvers:
            raise AuthorizationBookInvalid(f"event {n}: approver {ev['approved_by']!r} not allow-listed")
        aid = ev["authorization_id"]
        if ev["action"] == "APPROVE":
            if aid in active or aid in revoked:
                raise AuthorizationBookInvalid(f"event {n}: authorization id {aid} reused")
            if _ts(ev["expires_at"]) <= _ts(ev["issued_at"]):
                raise AuthorizationBookInvalid(f"event {n}: expires_at must be after issued_at")
            for w in ("research_window", "development_boundary"):
                if _ts(ev[w]["end"]) <= _ts(ev[w]["start"]):
                    raise AuthorizationBookInvalid(f"event {n}: empty {w}")
            if _ts(ev["development_boundary"]["end"]) > _ts(ev["holdout"]["start"]):
                raise AuthorizationBookInvalid(f"event {n}: development boundary overlaps the holdout")
            if _ts(ev["research_window"]["end"]) > _ts(ev["development_boundary"]["end"]) or \
                    _ts(ev["research_window"]["start"]) < _ts(ev["development_boundary"]["start"]):
                raise AuthorizationBookInvalid(f"event {n}: research window outside the development boundary")
            if gates_sha256(ev["formal_gate_pin"]["gates"]) != ev["formal_gate_pin"]["gates_sha256"]:
                raise AuthorizationBookInvalid(f"event {n}: formal_gate_pin hash mismatch")
            if any(s == "PASS" for g, s in ev["formal_gate_pin"]["gates"].items()
                   if g in ("TEMPORAL_SAFETY", "COST_APPROVAL", "FORMAL_EXECUTION_ELIGIBILITY")):
                raise AuthorizationBookInvalid(f"event {n}: research-only authorization must not pin a passed formal gate")
            active[aid] = ev
        else:
            if aid not in active:
                raise AuthorizationBookInvalid(f"event {n}: revoke of unknown authorization {aid}")
            active.pop(aid)
            revoked.add(aid)
    return active


class ResearchAuthorizationBook:
    """Read-only view. Deliberately has no write method."""

    def __init__(self, path: str | Path = DEFAULT_BOOK, approvers_path: str | Path = DEFAULT_APPROVERS):
        self._log = ChainedLog(path)
        self._approvers = set(json.loads(Path(approvers_path).read_text(encoding="utf-8"))["research_leads"])
        self.active = validate_events(self._log.verify(), self._approvers)

    def get(self, authorization_id: str) -> dict | None:
        return self.active.get(authorization_id)


@dataclass(frozen=True)
class ResearchRequest:
    authorization_id: str
    market_id: str
    symbol: str
    dataset: dict
    temporal_policy_sha256: str
    scenario_set_sha256: str
    scenarios: tuple
    spec_file_sha256: str
    strategy_id: str
    strategy_version: str
    parameters: dict
    warmup_start: str
    window_start: str
    window_end: str
    role: str = "RESEARCH_ONLY_EXPLORATORY"
    holdout_access: bool = False


@dataclass
class AuthorizationDecision:
    allowed: bool
    failures: list = field(default_factory=list)       # [(code, message)]
    authorization: dict | None = None


class ResearchAuthorizationGuard:
    def __init__(self, book: ResearchAuthorizationBook, holdout_book, market_entry: dict, uses_path: str | Path = DEFAULT_USES,
                 clock: Callable[[], datetime] | None = None):
        self.book, self.holdout_book, self.market = book, holdout_book, market_entry
        self.uses = ChainedLog(uses_path)
        self.clock = clock or (lambda: datetime.now().astimezone())

    def used(self, authorization_id: str) -> int:
        return sum(1 for r in self.uses.verify() if r["authorization_id"] == authorization_id)

    def check(self, req: ResearchRequest) -> AuthorizationDecision:
        f: list = []
        a = self.book.get(req.authorization_id)
        if a is None:
            return AuthorizationDecision(False, [("RA01", f"no active approved authorization {req.authorization_id}")])
        now = self.clock()
        if not (_ts(a["issued_at"]) <= now < _ts(a["expires_at"])):
            f.append(("RA02", f"authorization valid only in [{a['issued_at']}, {a['expires_at']})"))
        if (req.market_id, req.symbol) != (a["market_id"], a["symbol"]):
            f.append(("RA03", f"{req.market_id}/{req.symbol} not authorized"))
        if req.dataset != a["dataset"]:
            f.append(("RA04", "dataset snapshot / producer differ from the authorization"))
        approval = (self.market.get("facts", {}).get("conservative_temporal_policy", {}).get("research_approval") or {})
        if req.temporal_policy_sha256 != a["temporal_policy"]["sha256"] or \
                approval.get("policy_sha256") != a["temporal_policy"]["sha256"] or approval.get("approval_scope") != "RESEARCH_ONLY":
            f.append(("RA05", "temporal policy sha differs from the authorization or the recorded RESEARCH_ONLY acceptance"))
        if req.scenario_set_sha256 != a["costs"]["scenario_set_sha256"]:
            f.append(("RA06", "cost scenario set sha differs from the authorization"))
        extra = set(req.scenarios) - set(a["costs"]["scenarios"])
        if extra or not req.scenarios:
            f.append(("RA06", f"scenarios {sorted(extra) or '[]'} not authorized"))
        s = a["strategy"]
        if (req.spec_file_sha256, req.strategy_id, req.strategy_version) != (s["spec_file_sha256"], s["strategy_id"], s["version"]) \
                or req.parameters != s["parameters"]:
            f.append(("RA07", "strategy spec / parameters differ from the authorization (fixed SMA 5/20)"))
        ws, we, wu = _ts(req.window_start), _ts(req.window_end), _ts(req.warmup_start)
        rw, dev = a["research_window"], a["development_boundary"]
        if not (_ts(rw["start"]) <= ws < we <= _ts(rw["end"])):
            f.append(("RA08", f"window [{req.window_start}, {req.window_end}) outside authorized research window"))
        if not (_ts(dev["start"]) <= wu <= ws):
            f.append(("RA08", "warm-up must start inside the development boundary and not after the window"))
        if we > _ts(dev["end"]):
            f.append(("RA08", "window ends after the development boundary"))
        lock = self.holdout_book.for_dataset(a["dataset"]["dataset_id"]) if self.holdout_book else None
        h = a["holdout"]
        if lock is None or lock.lock_id != h["lock_id"]:
            f.append(("RA09", f"approved holdout lock {h['lock_id']} missing (H01)"))
        else:
            if lock.holdout_start != _ts(h["start"]) or lock.holdout_end is None or lock.holdout_end != _ts(h["end"]):
                f.append(("RA09", "holdout lock boundaries differ from the authorized boundaries"))
            if max(we, wu) > lock.holdout_start or _ts(dev["end"]) > lock.holdout_start:
                f.append(("RA09", "warm-up / window / development boundary reaches the holdout"))
        pin = a["formal_gate_pin"]
        if self.market.get("status") != pin["market_status"] or gates_sha256(self.market.get("gates", {})) != pin["gates_sha256"] \
                or self.market.get("gate_version") != pin["gate_version"]:
            f.append(("RA10", "formal market gates changed since the authorization was issued (re-authorize)"))
        if self.used(req.authorization_id) >= a["max_batches"]:
            f.append(("RA11", f"batch budget {a['max_batches']} used"))
        if req.role != "RESEARCH_ONLY_EXPLORATORY" or req.holdout_access:
            f.append(("RA12", "research-only authorization cannot be used for formal or holdout runs"))
        return AuthorizationDecision(not f, f, a)

    def consume(self, req: ResearchRequest, *, run_id: str, actor: str) -> dict:
        """Atomically reserve one batch slot. Raises ConditionFailed when the budget is used."""
        a = self.book.get(req.authorization_id)
        if a is None:
            raise ConditionFailed("RA01: no active authorization")

        def refuse(records):
            n = sum(1 for r in records if r["authorization_id"] == req.authorization_id)
            return f"RA11: batch budget {a['max_batches']} used" if n >= a["max_batches"] else None
        return self.uses.append_if({"authorization_id": req.authorization_id, "run_id": run_id, "actor": actor,
                                    "reserved_at": self.clock().isoformat(), "scenarios": list(req.scenarios)}, refuse)
