"""Research-only real-market runner (Phase 4-A): preflight + archive discipline.

Execution against real TSM data is deliberately NOT implemented yet. The Core
Phase 4-A snapshot adapter is being built by Codex; its API is not released,
so nothing here guesses it. ``preflight`` decides, fail-closed, whether a run
would be allowed; ``run`` only proceeds to Core once a Phase 4-A SHA and
contract are pinned in the run config, and until that integration is written
it stops with WAITING_FOR_CORE_ADAPTER.

There is no ignore_gate / force_run / skip_* parameter anywhere in this module
(tested). The formal market gates are read, never written.

Preflight rules (in addition to RA01–RA12, CO01–CO06, R02):
RO01 run config hash, kind REAL_RESEARCH_ONLY, no unknown keys
RO02 config pins equal the authorization (built into the ResearchRequest)
RO03 formal gates are exactly the expected NO-GO state: market NO_GO,
     TEMPORAL_SAFETY UNKNOWN, COST_APPROVAL FAIL, FORMAL_EXECUTION_ELIGIBILITY FAIL
RO04 Core Phase 4-A SHA + delivery contract pinned (else WAITING_FOR_CORE_ADAPTER)
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from strategy_agent.governance.holdout import HoldoutBook, LockBookInvalid
from strategy_agent.governance.research_authorization import (AuthorizationBookInvalid, ResearchAuthorizationBook,
                                                              ResearchAuthorizationGuard, ResearchRequest)
from strategy_agent.runner import parameter_issues

from .costs import CostSet, CostSetError

ROOT = Path(__file__).resolve().parents[3]
EXPECTED_FORMAL = {"TEMPORAL_SAFETY": "UNKNOWN", "COST_APPROVAL": "FAIL", "FORMAL_EXECUTION_ELIGIBILITY": "FAIL"}
CONFIG_KEYS = {"schema_version", "run_config_id", "version", "kind", "purpose", "authorization_id", "market", "dataset",
               "strategy", "temporal_policy", "costs", "windows", "core", "sample", "archive", "provenance"}


def canonical_sha(obj: dict) -> str:
    o = json.loads(json.dumps(obj))
    o.get("provenance", {})["config_sha256"] = None
    return hashlib.sha256(json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


@dataclass
class Preflight:
    status: str                                   # BLOCKED | WAITING_FOR_CORE_ADAPTER | READY_FOR_CORE_INTEGRATION
    blockers: list = field(default_factory=list)  # [(code, message)]
    governance: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"status": self.status, "blockers": [{"code": c, "message": m} for c, m in self.blockers],
                "governance": self.governance}


class ResearchOnlyRunner:
    def __init__(self, *, root: Path = ROOT, locks_path: Path | None = None, authorizations_path: Path | None = None,
                 uses_path: Path | None = None, approvers_path: Path | None = None, readiness_path: Path | None = None,
                 clock: Callable[[], datetime] | None = None):
        self.root = root
        self.locks_path = locks_path or root / "governance" / "holdout_locks.jsonl"
        self.auth_path = authorizations_path or root / "governance" / "research_authorizations.jsonl"
        self.uses_path = uses_path or root / "registry" / "research_authorization_uses.jsonl"
        self.approvers_path = approvers_path or root / "governance" / "approvers.json"
        self.readiness_path = readiness_path or root / "governance" / "market_readiness.json"
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    # ---------------------------------------------------------------- inputs

    def load_config(self, path: Path) -> tuple[dict, list]:
        cfg = json.loads(Path(path).read_text(encoding="utf-8"))
        b = []
        if set(cfg) - CONFIG_KEYS:
            b.append(("RO01", f"unknown config keys {sorted(set(cfg) - CONFIG_KEYS)} (no bypass switches exist)"))
        if cfg.get("kind") != "REAL_RESEARCH_ONLY":
            b.append(("RO01", f"kind {cfg.get('kind')!r} is not REAL_RESEARCH_ONLY"))
        if cfg.get("provenance", {}).get("config_sha256") != canonical_sha(cfg):
            b.append(("RO01", "config_sha256 does not match content (edited config => new version + new hash)"))
        return cfg, b

    def request(self, cfg: dict, cost_set: CostSet | None) -> ResearchRequest:
        return ResearchRequest(
            authorization_id=cfg["authorization_id"], market_id=cfg["market"]["market_id"], symbol=cfg["market"]["symbol"],
            dataset=dict(cfg["dataset"]), temporal_policy_sha256=cfg["temporal_policy"]["sha256"],
            scenario_set_sha256=cost_set.sha256 if cost_set else cfg["costs"]["scenario_set_sha256"],
            scenarios=tuple(cfg["costs"]["scenarios"] + cfg["costs"]["sensitivity"]),
            spec_file_sha256=hashlib.sha256((self.root / cfg["strategy"]["spec_path"]).read_bytes()).hexdigest(),
            strategy_id=cfg["strategy"]["strategy_id"], strategy_version=cfg["strategy"]["version"],
            parameters=dict(cfg["strategy"]["parameters"]), warmup_start=cfg["windows"]["warmup_start"],
            window_start=cfg["windows"]["start"], window_end=cfg["windows"]["end"])

    # ------------------------------------------------------------- preflight

    def preflight(self, config_path: Path) -> Preflight:
        cfg, b = self.load_config(config_path)
        gov: dict = {}
        market = json.loads(self.readiness_path.read_text(encoding="utf-8"))["markets"][cfg["market"]["market_id"]]
        gates = {k: v["status"] for k, v in market["gates"].items()}
        gov["formal"] = {"market_status": market["status"], "gates": gates, "gate_version": market["gate_version"]}
        if market["status"] != "NO_GO" or any(gates.get(k) != v for k, v in EXPECTED_FORMAL.items()):
            b.append(("RO03", f"formal gates are not the expected NO-GO state: {market['status']} {gates}"))

        cost_set = None
        try:
            cost_set = CostSet(self.root / cfg["costs"]["scenario_set_path"], root=self.root)
            if cost_set.sha256 != cfg["costs"]["scenario_set_sha256"]:
                b.append(("CO03", "cost scenario set file sha differs from the run config"))
            first = datetime.fromisoformat(cfg["windows"]["warmup_start"]).date()
            last = datetime.fromisoformat(cfg["windows"]["end"]).date() - timedelta(days=1)
            gov["cost_window"] = cost_set.check_window(first, last)
        except CostSetError as e:
            b.append(("CO", str(e)))

        spec = json.loads((self.root / cfg["strategy"]["spec_path"]).read_text(encoding="utf-8"))
        b += [("R02", f"{i.path} {i.message}") for i in parameter_issues(spec, dict(cfg["strategy"]["parameters"]))]

        try:
            hb = HoldoutBook(self.locks_path, self.approvers_path)
            lock = hb.for_dataset(cfg["dataset"]["dataset_id"])
            gov["holdout_lock"] = ({"lock_id": lock.lock_id, "start": lock.holdout_start.isoformat(),
                                    "end": lock.holdout_end.isoformat() if lock.holdout_end else None,
                                    "approved_by": lock.approved_by} if lock else "MISSING")
        except LockBookInvalid as e:
            hb = None
            b.append(("H00", f"holdout lock book invalid: {e}"))
        if gov.get("holdout_lock") == "MISSING":
            b.append(("H01", f"no approved holdout lock for {cfg['dataset']['dataset_id']}"))

        try:
            ab = ResearchAuthorizationBook(self.auth_path, self.approvers_path)
            guard = ResearchAuthorizationGuard(ab, hb, market, self.uses_path, clock=self.clock)
            d = guard.check(self.request(cfg, cost_set))
            gov["authorization"] = ("APPROVED" if d.authorization else "MISSING")
            gov["authorization_uses"] = guard.used(cfg["authorization_id"])
            b += d.failures
        except AuthorizationBookInvalid as e:
            b.append(("RA00", f"authorization book invalid: {e}"))

        core = cfg["core"]
        governance_blocked = bool(b)
        if not (core.get("phase4a_sha") and core.get("phase4a_contract_ref")):
            b.append(("RO04", "Core Phase 4-A SHA and delivery contract not pinned"))
            status = "BLOCKED" if governance_blocked else "WAITING_FOR_CORE_ADAPTER"
        else:
            status = "BLOCKED" if governance_blocked else "READY_FOR_CORE_INTEGRATION"
        gov["core"] = {"baseline_sha": core["baseline_sha"], "phase4a_sha": core.get("phase4a_sha")}
        return Preflight(status, b, gov)

    def run(self, config_path: Path) -> dict:
        """Preflight, then (only when READY) hand off to the Core Phase 4-A integration, which is not written yet."""
        pf = self.preflight(config_path)
        if pf.status != "READY_FOR_CORE_INTEGRATION":
            return {"executed": False, **pf.as_dict()}
        raise NotImplementedError("Core Phase 4-A integration is written only after the delivery contract is read")


# --------------------------------------------------------------- archiving


def run_id(now: datetime, git_commit: str) -> str:
    if len(git_commit) < 7 or any(c not in "0123456789abcdef" for c in git_commit.lower()):
        raise ValueError("git_commit must be a hex commit sha")
    return f"{now.strftime('%Y%m%d_%H%M%S')}_{git_commit[:7]}"


def write_once(path: Path, data: bytes) -> str:
    """Create a file that must not exist (immutable archive; N3-safe). Returns its sha256."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    return hashlib.sha256(data).hexdigest()
