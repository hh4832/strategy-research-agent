"""Research-only real-market runner (Phase 4-A): preflight + archive discipline.

``preflight`` decides, fail-closed, whether a run would be allowed (governance
only, no data). ``run`` then performs the NON-consuming data-contract checks
(Core identity pin, Core cost profiles, cost-set consistency, metadata-only
snapshot probe - see ``core_phase4a``) and only when all of them pass does it
atomically consume the single authorization batch slot, load data through Core
``load_native_tsm`` and execute every scenario with Core ``ExecutionEngine``.
A failure after the slot is consumed is archived and reported; the slot is
never released or reset.

There is no ignore_gate / force_run / skip_* parameter anywhere in this module
(tested). The formal market gates are read, never written.

Preflight rules (in addition to RA01–RA12, CO01–CO06, R02):
RO01 run config hash, kind REAL_RESEARCH_ONLY, no unknown keys
RO02 config pins equal the authorization (built into the ResearchRequest)
RO03 formal gates are exactly the expected NO-GO state: market NO_GO,
     TEMPORAL_SAFETY UNKNOWN, COST_APPROVAL FAIL, FORMAL_EXECUTION_ELIGIBILITY FAIL
RO04 Core Phase 4-A SHA + delivery contract pinned (else WAITING_FOR_CORE_ADAPTER)
RO05 installed Core is exactly the pinned Phase 4-A SHA with a clean tree (checked by ``run`` before consuming)
CO07 Core research constants (slippage, CAT, TAF record) equal the accepted Agent cost set
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from strategy_agent.governance.holdout import HoldoutBook, LockBookInvalid
from strategy_agent.governance.research_authorization import (AuthorizationBookInvalid, ResearchAuthorizationBook,
                                                              ResearchAuthorizationGuard, ResearchRequest)
from strategy_agent.runner import parameter_issues

from .costs import CostSet, CostSetError

TAIPEI = ZoneInfo("Asia/Taipei")

ROOT = Path(__file__).resolve().parents[3]
EXPECTED_FORMAL = {"TEMPORAL_SAFETY": "UNKNOWN", "COST_APPROVAL": "FAIL", "FORMAL_EXECUTION_ELIGIBILITY": "FAIL"}
CONFIG_KEYS = {"schema_version", "run_config_id", "version", "kind", "purpose", "authorization_id", "market", "dataset",
               "strategy", "temporal_policy", "costs", "windows", "core", "sample", "archive", "provenance",
               "snapshot_binding", "core_costs"}


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
            if cfg.get("core_costs", {}).get("dated_schedule_sha256"):
                from strategy_agent.execution.c1_integration import load_dated_schedules, SCHEDULE_SHA256, SCHEDULE
                if cfg["core_costs"].get("dated_schedule_path") != SCHEDULE:
                    raise CostSetError("dated schedule path does not match the reviewed C1 pin")
                if cfg["core_costs"]["dated_schedule_sha256"] != SCHEDULE_SHA256:
                    raise CostSetError("dated schedule SHA does not match the reviewed C1 pin")
                schedules = load_dated_schedules(self.root, expected_sha256=cfg["core_costs"]["dated_schedule_sha256"])
                first = datetime.fromisoformat(cfg["windows"]["start"]).date()
                for schedule in schedules.values():
                    schedule.require_coverage(first.isoformat(), last.isoformat())
                gov["cost_window"] = {"status": "PASS", "kind": "OFFICIAL_DATED_SCHEDULE",
                                      "start": first.isoformat(), "end": last.isoformat(),
                                      "schedule_sha256": SCHEDULE_SHA256}
            else:
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
            from strategy_agent.governance.research_authorization import dated_cost_failures
            b += d.failures + dated_cost_failures(d.authorization, cfg.get("core_costs", {}))
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

    def _guard(self, cfg: dict) -> ResearchAuthorizationGuard:
        market = json.loads(self.readiness_path.read_text(encoding="utf-8"))["markets"][cfg["market"]["market_id"]]
        return ResearchAuthorizationGuard(ResearchAuthorizationBook(self.auth_path, self.approvers_path),
                                          HoldoutBook(self.locks_path, self.approvers_path), market, self.uses_path,
                                          clock=self.clock)

    def data_checks(self, cfg: dict, *, snapshot_root: Path, core, core_costs, core_identity: dict) -> dict:
        """Everything that can fail BEFORE the batch slot is consumed. Reads no OHLCV and no holdout bytes."""
        from . import core_phase4a as P  # noqa: PLC0415
        b: list = []
        pin = cfg["core"]["phase4a_sha"]
        if core_identity.get("core_git_sha") != pin or core_identity.get("core_tree_dirty"):
            b.append(("RO05", f"installed Core {core_identity} != pinned {pin} (clean)"))
        cc = cfg["core_costs"]
        profiles = {}
        dated_schedules = None
        gov_compat = None
        try:
            if cc.get("dated_schedule_sha256"):
                from strategy_agent.execution.c1_integration import (load_dated_schedules,
                    validate_cost_scenario_compatibility)
                dated_schedules = load_dated_schedules(self.root, expected_sha256=cc["dated_schedule_sha256"])
                compat = validate_cost_scenario_compatibility(self.root, dated_schedules)
                gov_compat = compat
                for schedule in dated_schedules.values():
                    schedule.require_coverage(*(
                        datetime.fromisoformat(cfg["windows"]["start"]).date().isoformat(),
                        (datetime.fromisoformat(cfg["windows"]["end"]).date() - timedelta(days=1)).isoformat()))
            else:
                profiles = P.research_profiles(core_costs, sec_config=self.root / cc["sec_config_path"],
                                               sec_sha=cc["sec_configuration_sha256"],
                                               taf_record=self.root / cc["taf_record_path"], taf_sha=cc["taf_record_sha256"],
                                               scenarios=list(cfg["costs"]["scenarios"] + cfg["costs"]["sensitivity"]))
                gov_compat = None
        except (ValueError, KeyError, OSError) as e:
            b.append(("CO", f"Core dated/profile cost validation refused: {e}"))
        cost_set = CostSet(self.root / cfg["costs"]["scenario_set_path"], root=self.root)
        taf = json.loads((self.root / cc["taf_record_path"]).read_text(encoding="utf-8"))
        if not cc.get("dated_schedule_sha256"):
            b += P.cost_set_consistency(cost_set, core_costs, taf, datetime.fromisoformat(cfg["windows"]["start"]).date())
        auth = self._guard(cfg).book.get(cfg["authorization_id"])
        if auth is None:
            b.append(('RA01','new derived snapshot requires its own research-lead-approved authorization'))
            return {'blockers':b,'probe':P.ProbeResult('BLOCKED',[('RA01','unsigned authorization')],{}),
                    'snapshot_root':Path(snapshot_root),'profiles':profiles,'dated_schedules':dated_schedules}
        derived=cfg.get('snapshot_binding',{}).get('snapshot_pin')
        if derived and (derived.get('snapshot_id')!=cfg['dataset']['snapshot_id'] or derived.get('producer_sha')!=cfg['dataset']['producer_commit_sha']):
            b.append(('RO05','derived binding differs from authorized dataset identity'))
        rw, ho = auth["research_window"], auth["holdout"]
        last = lambda iso: (datetime.fromisoformat(iso).date() - timedelta(days=1)).isoformat()  # noqa: E731
        win = (datetime.fromisoformat(rw["start"]).date().isoformat(), last(rw["end"]))
        hold = ((datetime.fromisoformat(ho["start"]).date().isoformat(), last(ho["end"])),)
        dates = (datetime.fromisoformat(cfg["windows"]["warmup_start"]).date().isoformat(), last(cfg["windows"]["end"]))
        probe = P.probe_snapshot(snapshot_root, P.SnapshotBinding.from_config(cfg), dates=dates, research_window=win,
                                 holdout=hold)
        b += probe.blockers
        return {"blockers": b, "probe": probe, "snapshot_root": Path(snapshot_root), "profiles": profiles,
                "dated_schedules": dated_schedules, "cost_compatibility": gov_compat, "dates": dates, "research_window": win,
                "holdout": hold}

    def probe(self, config_path: Path, *, snapshot_root: Path, core_costs=None, core_native=None) -> dict:
        """Preflight + every pre-consumption data-contract check. NEVER consumes the slot, never loads OHLCV."""
        pf = self.preflight(config_path)
        if core_costs is None or core_native is None:
            from strategy_agent.adapters.mrc_phase4a import core_phase4a  # noqa: PLC0415
            native_mod, costs_mod = core_phase4a()
            core_costs, core_native = core_costs or costs_mod, core_native or native_mod
        cfg, _ = self.load_config(config_path)
        ident = core_native.core_code_identity()
        dc = self.data_checks(cfg, snapshot_root=Path(snapshot_root), core=None, core_costs=core_costs,
                              core_identity=ident)
        blockers = pf.blockers + dc["blockers"]
        return {"status": "COMPATIBLE_READY_TO_CONSUME" if not blockers else "BLOCKED_BEFORE_CONSUMPTION",
                "slot_consumed": False, "preflight": pf.as_dict(), "core_identity": ident,
                "blockers": [{"code": c, "message": m} for c, m in blockers], "probe": dc["probe"].as_dict(),
                "profiles": {k: v.sha256 for k, v in dc["profiles"].items()},
                "dated_schedules": {k: v.sha256 for k, v in (dc.get("dated_schedules") or {}).items()},
                "cost_compatibility": dc.get("cost_compatibility")}

    def run(self, config_path: Path, *, snapshot_root: Path | None = None, archive_root: Path | None = None,
            git_commit: str | None = None, actor: str | None = None, core=None, core_costs=None,
            core_native=None, run_context: dict | None = None) -> dict:
        """Preflight -> non-consuming data checks -> consume ONE slot -> Core load + execute -> immutable archive."""
        pf = self.preflight(config_path)
        if pf.status != "READY_FOR_CORE_INTEGRATION":
            return {"executed": False, "slot_consumed": False, **pf.as_dict()}
        if snapshot_root is None or archive_root is None or not git_commit or not actor:
            raise ValueError("snapshot_root, archive_root, git_commit and actor are required to run")
        if core is None:
            from strategy_agent.adapters.mrc_adapter import _core  # noqa: PLC0415
            core = _core()
        if core_costs is None or core_native is None:
            from strategy_agent.adapters.mrc_phase4a import core_phase4a  # noqa: PLC0415
            native_mod, costs_mod = core_phase4a()
            core_costs, core_native = core_costs or costs_mod, core_native or native_mod
        cfg, _ = self.load_config(config_path)
        ident = core_native.core_code_identity()
        dc = self.data_checks(cfg, snapshot_root=Path(snapshot_root), core=core, core_costs=core_costs,
                              core_identity=ident)
        base = {"preflight": pf.as_dict(), "core_identity": ident, "probe": dc["probe"].as_dict()}
        if dc["blockers"]:
            return {"executed": False, "slot_consumed": False, "status": "BLOCKED_BEFORE_CONSUMPTION",
                    "blockers": [{"code": c, "message": m} for c, m in dc["blockers"]], **base}

        started_at = self.clock()
        rid = run_id(started_at.astimezone(TAIPEI), git_commit)      # archive id in Asia/Taipei
        guard = self._guard(cfg)
        cost_set = CostSet(self.root / cfg["costs"]["scenario_set_path"], root=self.root)
        req = self.request(cfg, cost_set)
        d = guard.check(req)                                     # re-check immediately before the atomic reserve
        from strategy_agent.governance.research_authorization import dated_cost_failures
        d.failures += dated_cost_failures(d.authorization, cfg.get("core_costs", {}))
        if d.failures:
            return {"executed": False, "slot_consumed": False, "status": "BLOCKED_BEFORE_CONSUMPTION",
                    "blockers": [{"code": c, "message": m} for c, m in d.failures], **base}
        use = guard.consume(req, run_id=rid, actor=actor)        # raises ConditionFailed (RA11) when used
        out_dir = Path(archive_root) / rid
        try:
            return self._execute(cfg, rid, use, out_dir, dc, ident, base, core, core_costs, core_native,
                                 run_context=run_context, started_at=started_at)
        except Exception as e:  # noqa: BLE001 - the slot stays consumed; record and stop
            import traceback  # noqa: PLC0415
            failure = {"status": "FAILED_AFTER_CONSUMPTION", "run_id": rid, "slot": use,
                       "error": f"{type(e).__name__}: {e}", "code": getattr(e, "code", None),
                       "traceback": traceback.format_exc(limit=5),
                       "policy": "slot is NOT released; no retry; report to research lead"}
            write_once(out_dir / "FAILURE.json", _dumps(failure))
            return {"executed": False, "slot_consumed": True, **failure, **base}

    def _execute(self, cfg, rid, use, out_dir, dc, ident, base, core, core_costs, core_native, *,
                 run_context=None, started_at=None) -> dict:
        from . import archive_contract as AC  # noqa: PLC0415
        from . import core_phase4a as P  # noqa: PLC0415
        b = P.SnapshotBinding.from_config(cfg)
        DW = core_native.DateWindow
        loaded = P.load_continuous_native(core_native,dc["snapshot_root"],b,
            dates=dc["dates"],research_window=dc["research_window"],holdout=dc["holdout"])
        spec = json.loads((self.root / cfg["strategy"]["spec_path"]).read_text(encoding="utf-8"))
        cost_kw = ({"dated_schedules": dc["dated_schedules"]} if dc.get("dated_schedules") is not None
                   else {"profiles": dc["profiles"]})
        batch = P.execute_batch(core, core_costs, frame=loaded.conservative_15m, core_sha=ident["core_git_sha"],
                                **cost_kw, spec=spec, parameters=dict(cfg["strategy"]["parameters"]),
                                window_start=cfg["windows"]["start"], window_end=cfg["windows"]["end"],
                                warmup_start=cfg["windows"]["warmup_start"], dataset=dict(cfg["dataset"]))
        ctx = dict(run_context or {})
        mpath = Path(dc["snapshot_root"]) / b.manifest_relative_path
        ctx.setdefault("snapshot_manifest", json.loads(mpath.read_text(encoding="utf-8")))
        summary = AC.write_contract_archive(
            out_dir, write_once=write_once, cfg=cfg, run_id=rid, use=use, loaded=loaded, batch=batch,
            core_identity=ident, holdout=dc["holdout"], context=ctx, cat_rounding=P.rounding_masked(batch),
            started_at=started_at or self.clock())
        return {"executed": True, "slot_consumed": True, "archive_dir": str(out_dir), **summary, **base}


def _dumps(obj) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True, default=_jsonable, ensure_ascii=False) + "\n").encode()


def _jsonable(v):
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if hasattr(v, "item"):
        return v.item()
    if hasattr(v, "to_dict"):
        return v.to_dict()
    return str(v)


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
