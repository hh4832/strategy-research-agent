"""B2-2 Controlled Runner Adapter + execution receipts.

``ControlledRunnerAdapter`` is the only adapter that may consume a research batch. The Orchestrator drives it
(``Orchestrator._dispatch_controlled``) strictly in this order, re-reading the authoritative state at each step:
verified approval -> binding -> governance (G1-G11) -> precheck (identity / PIT pins, BEFORE any budget) ->
atomic reservation -> commit (uses record) -> backend execution -> receipt -> existing evaluator -> archive -> settle.

Enablement is NOT a flag or an environment variable: ``enabled`` is computed from the governance root (must be an
isolated test root carrying ISOLATED_TEST_GOVERNANCE_ROOT.json) AND the backend (must declare a synthetic snapshot).
On the repository's books it is always False, so RESEARCH_ONLY_REAL stays DISABLED.

Backends never re-implement PnL, costs or signals:
  * ``SyntheticFixtureBackend``   copies a labelled SYNTHETIC_FIXTURE Phase 4-A contract archive (no market data)
  * ``ResearchOnlyRunnerBackend`` the EXISTING ResearchOnlyRunner on a SYNTHETIC development snapshot; it runs the
    runner's own preflight/probe before the reservation and its own data checks + Core execution after the commit,
    and never calls ``runner.run()`` (which would consume a second time).
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

from strategy_agent.evaluation import phase4c as C
from strategy_agent.orchestration.adapters import AdapterDisabled, ExecutionAdapter, ExecutionFailed
from strategy_agent.orchestration.governance import is_isolated_test_root

RECEIPT = "EXECUTION_RECEIPT.json"
PIN_FIELDS = ("strategy_spec_sha256", "dataset_snapshot_id", "dataset_manifest_sha256", "core_commit")


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def write_receipt(out_dir: Path, receipt: dict) -> tuple[Path, str]:
    p = Path(out_dir) / RECEIPT
    b = (json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
    with open(p, "xb") as fh:                       # immutable
        fh.write(b)
    return p, _sha(b)


class ControlledRunnerAdapter(ExecutionAdapter):
    kind = "CONTROLLED_RESEARCH_ONLY"
    consumes_budget = True
    controlled = True
    evidence = "CONTROLLED_EXECUTION"

    def __init__(self, backend, *, governance_root: Path):
        self.backend = backend
        self.isolated = is_isolated_test_root(governance_root)
        self._enabled = bool(self.isolated and getattr(backend, "synthetic", False) is True)

    @property
    def enabled(self) -> bool:                       # read-only: no setter, no env var
        return self._enabled

    def planned_run_id(self, task_id: str, now: datetime) -> str:
        return self.backend.planned_run_id(task_id, now)

    def precheck(self, doc: dict) -> list[str]:
        """Identity / PIT pins BEFORE any budget is reserved. Any mismatch => BLOCKED (never 'use latest data')."""
        if not self.enabled:
            return ["CONTROLLED_EXECUTION_DISABLED: production books or non-synthetic backend"]
        bad = [f"REQUEST_FIELD_MISSING:{k}" for k in PIN_FIELDS if not doc.get(k) or doc.get(k) == "PENDING"]
        if doc.get("holdout_policy", {}).get("access") != "NO_ACCESS":
            bad.append("HOLDOUT_ACCESS_REQUESTED")
        if doc.get("signal_time_rule", {}).get("signal_time") != "CORE_MODELED_AVAILABLE_AT" or \
                doc.get("execution_time_rule", {}).get("rule") != "NEXT_ELIGIBLE_BAR_OPEN":
            bad.append("PIT_RULE_MISMATCH")
        return bad + self.backend.precheck(doc)

    def execute(self, doc: dict, out_dir: Path, *, use: dict | None = None, reservation: dict | None = None,
                started_at: datetime | None = None) -> dict:
        if not self.enabled:
            raise AdapterDisabled("CONTROLLED_EXECUTION_DISABLED")
        if not use or not reservation or use.get("run_id") != reservation.get("planned_run_id"):
            raise ExecutionFailed("NO_COMMITTED_RESERVATION", "execution requires a committed budget reservation")
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=False)
        started = started_at or datetime.now().astimezone()
        res = self.backend.run(doc, out_dir, use=use)
        arch = Path(res["archive_path"])
        mp = arch / "MANIFEST.json"
        if not mp.is_file():
            raise ExecutionFailed("RECEIPT_INCOMPLETE", "runner finished without an archive MANIFEST")
        manifest_sha = _sha(mp.read_bytes())
        try:
            C.read_archive(arch, expected_manifest_sha256=manifest_sha)      # every listed file present + hashed
        except C.ArchiveRejected as e:
            raise ExecutionFailed("RECEIPT_ARCHIVE_INCOMPLETE", f"{e.code}: {e}") from None
        receipt = {"kind": "EXECUTION_RECEIPT", "reservation_id": reservation["reservation_id"],
                   "task_id": reservation["task_id"], "run_id": use["run_id"], "use_record_sha256": use["record_sha256"],
                   "backend": self.backend.kind, "backend_identity": res.get("identity", {}),
                   "started_at": started.isoformat(timespec="seconds"),
                   "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                   "archive_path": str(arch), "output_manifest_sha256": manifest_sha, "synthetic": True,
                   "consumed_batches": 1}
        rp, rsha = write_receipt(out_dir, receipt)
        return {"adapter": self.kind, "evidence": self.evidence, "archive_path": str(arch),
                "archive_manifest_sha256": manifest_sha, "receipt_path": str(rp), "receipt_sha256": rsha,
                "run_id": use["run_id"], "consumed_batches": 1, "strategy_executed": True,
                "backend": self.backend.kind, "synthetic": True}


class SyntheticFixtureBackend:
    """Labelled SYNTHETIC_FIXTURE archive (produced earlier by the real Phase 4-A producer). No data, no PnL code."""
    kind = "SYNTHETIC_FIXTURE_ARCHIVE"
    synthetic = True

    def __init__(self, fixture_archive: Path, *, fail_with: str | None = None, drop_file: str | None = None):
        self.fixture = Path(fixture_archive)
        self.fail_with, self.drop_file = fail_with, drop_file      # crash / incomplete-archive injection for tests
        self.calls = 0

    def planned_run_id(self, task_id: str, now: datetime) -> str:
        """The fixture's own run id (its MANIFEST binds it): one synthetic execution per ledger (RUN_ID_ALREADY_USED)."""
        return json.loads((self.fixture / "MANIFEST.json").read_text(encoding="utf-8"))["run_id"]

    def precheck(self, doc: dict) -> list[str]:
        return [] if (self.fixture / "MANIFEST.json").is_file() else ["SYNTHETIC_FIXTURE_MISSING"]

    def run(self, doc: dict, out_dir: Path, *, use: dict) -> dict:
        self.calls += 1
        if self.fail_with:
            raise RuntimeError(self.fail_with)
        dst = Path(out_dir) / use["run_id"]
        shutil.copytree(self.fixture, dst)
        if self.drop_file:
            (dst / self.drop_file).unlink()
        return {"archive_path": str(dst), "identity": {"fixture": self.fixture.name, "label": "SYNTHETIC_FIXTURE"}}


class ResearchOnlyRunnerBackend:
    """The existing ResearchOnlyRunner, split around the ledger commit (never ``runner.run()``)."""
    kind = "RESEARCH_ONLY_RUNNER_SYNTHETIC_SNAPSHOT"

    def __init__(self, runner, *, config_path: Path, snapshot_root: Path, git_commit: str, synthetic_snapshot: bool,
                 run_context: dict | None = None, core=None, core_costs=None, core_native=None):
        self.runner, self.config_path, self.snapshot_root = runner, Path(config_path), Path(snapshot_root)
        self.git_commit, self.run_context = git_commit, dict(run_context or {})
        self.synthetic = bool(synthetic_snapshot)
        self.core, self.core_costs, self.core_native = core, core_costs, core_native
        self.calls = 0

    def _core(self):
        if self.core is None:
            from strategy_agent.adapters.mrc_adapter import _core  # noqa: PLC0415
            self.core = _core()
        if self.core_costs is None or self.core_native is None:
            from strategy_agent.adapters.mrc_phase4a import core_phase4a  # noqa: PLC0415
            n, c = core_phase4a()
            self.core_native, self.core_costs = self.core_native or n, self.core_costs or c

    def planned_run_id(self, task_id: str, now: datetime) -> str:
        from strategy_agent.research_only.runner import TAIPEI, run_id  # noqa: PLC0415
        return run_id(now.astimezone(TAIPEI), self.git_commit)

    def precheck(self, doc: dict) -> list[str]:
        cfg, _ = self.runner.load_config(self.config_path)
        bad = []
        pins = {"authorization_id": (doc.get("authorization_ref") or {}).get("authorization_id"),
                "snapshot_id": doc.get("dataset_snapshot_id")}
        if cfg.get("authorization_id") != pins["authorization_id"]:
            bad.append("RUNNER_CONFIG_AUTHORIZATION_MISMATCH")
        if cfg["dataset"].get("snapshot_id") != pins["snapshot_id"]:
            bad.append("RUNNER_CONFIG_SNAPSHOT_MISMATCH")
        if cfg["costs"].get("scenario_set_sha256") != doc.get("cost_profile_ref", {}).get("scenario_set_sha256"):
            bad.append("RUNNER_CONFIG_COST_MISMATCH")
        self._core()
        probe = self.runner.probe(self.config_path, snapshot_root=self.snapshot_root, core_costs=self.core_costs,
                                  core_native=self.core_native)
        if probe["status"] != "COMPATIBLE_READY_TO_CONSUME" or probe["blockers"] or probe["slot_consumed"]:
            bad.append(f"RUNNER_PROBE:{probe['status']}:{[b.get('code') if isinstance(b, dict) else b for b in probe['blockers']]}")
        return bad

    def run(self, doc: dict, out_dir: Path, *, use: dict) -> dict:
        self.calls += 1
        self._core()
        r = self.runner
        cfg, _ = r.load_config(self.config_path)
        ident = self.core_native.core_code_identity()
        dc = r.data_checks(cfg, snapshot_root=self.snapshot_root, core=self.core, core_costs=self.core_costs,
                           core_identity=ident)
        if dc["blockers"]:
            raise ExecutionFailed("RUNNER_DATA_CHECKS", str(dc["blockers"]))
        base = {"core_identity": ident, "probe": dc["probe"].as_dict(), "controlled": True}
        dst = Path(out_dir) / use["run_id"]
        out = r._execute(cfg, use["run_id"], use, dst, dc, ident, base, self.core, self.core_costs, self.core_native,
                         run_context=self.run_context, started_at=r.clock())
        if not out.get("executed"):
            raise ExecutionFailed("RUNNER_NOT_EXECUTED", str(out)[:300])
        return {"archive_path": out["archive_dir"], "identity": {"core_git_sha": ident.get("core_git_sha"),
                                                                 "agent_git_sha": self.git_commit}}
