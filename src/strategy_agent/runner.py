"""Experiment runner (Backtest Executor, ARCHITECTURE §3.3).

Decision Q4: ``market-research-core`` is the ONLY formal backtest executor.
This runner orchestrates and records; it calls whatever implements
``ports.BacktestPort`` (the M1 adapter around market-research-core). It never
simulates fills itself and never uses ``benchmark.oracle``.

Order of checks (each failure is recorded, nothing is silently skipped):
  1 spec valid & REGISTERED/FROZEN        -> REJECTED_SPEC
  -- steps 2–6 run under the AdmissionLock (atomic for concurrent workers) --
  2 family assignment (canonical)          -> recorded on every event
  3 holdout guard H01–H08                  -> REJECTED_GOVERNANCE
  4 budget B01–B06 (in-flight included)    -> BUDGET_EXCEEDED
  5 engine SHA has BENCHMARK_PASS          -> REJECTED_CONTRACT
  6 holdout reservation (atomic, permanent) -> REJECTED_GOVERNANCE H06
    QUEUED -> RUNNING                       (lock released after RUNNING)
  -- outside the lock --
    port.run with timeout / cancel
  7 check_report (contract C4)             -> REJECTED_CONTRACT
  8 COMPLETED with metrics + Q5 sufficiency | FAILED | TIMEOUT | CANCELLED
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from strategy_agent.evaluation.sufficiency import assess
from strategy_agent.family.canonical import FamilyIndex
from strategy_agent.governance.budget import BudgetGuard
from strategy_agent.governance.holdout import HoldoutGuard
from strategy_agent.registry.admission import AdmissionLock
from strategy_agent.registry.chain import ConditionFailed
from strategy_agent.ports import BacktestPort, BacktestRequest, ContractViolation, DatasetRef, check_report
from strategy_agent.registry.experiments import ExperimentRegistry, new_experiment_id, params_hash
from strategy_agent.spec.validate import Issue, canonical_hash, validate


@dataclass(frozen=True)
class EngineInfo:
    name: str
    git_sha: str
    version: str
    benchmark_status: str          # BENCHMARK_PASS | NOT_VERIFIED


@dataclass(frozen=True)
class RunRequest:
    spec: dict
    parameters: dict
    role: str                      # TRAIN | VALIDATION | WF_TEST | FINAL_HOLDOUT
    split_ref: str
    fold_id: str
    window_start: str
    window_end: str
    warmup_start: str
    cost_multiplier: float = 1.0
    seed: int = 0
    holdout_access: bool = False
    datasets: tuple = field(default=())


class OracleNotAllowed(TypeError):
    """Decision Q4: the Decimal oracle is an acceptance tool, never the executor."""


class ExperimentRunner:
    def __init__(self, registry: ExperimentRegistry, families: FamilyIndex, holdout: HoldoutGuard,
                 budget: BudgetGuard, port: BacktestPort, engine: EngineInfo, agent_sha: str,
                 admission: AdmissionLock, actor: str = "strategy-agent",
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc), governance=None):
        if type(port).__module__.split(".")[0] == "benchmark":
            raise OracleNotAllowed("benchmark oracle cannot be used as the BacktestPort; "
                                   "market-research-core is the only formal executor")
        self.registry, self.families, self.holdout, self.budget = registry, families, holdout, budget
        self.port, self.engine, self.agent_sha, self.actor, self.clock = port, engine, agent_sha, actor, clock
        self.admission = admission
        self.governance = governance          # GovernanceContext; None = repository governance files

    # ------------------------------------------------------------ helpers

    def _base(self, exp_id: str, created: str, req: RunRequest, trial: dict, spec_sha: str) -> dict:
        spec = req.spec
        dsets = spec.get("data_requirements", {}).get("datasets") or [
            {"dataset_id": "UNKNOWN", "snapshot_id": "UNKNOWN", "producer_commit_sha": "UNKNOWN", "verdict": "UNKNOWN"}]
        return {
            "experiment_id": exp_id, "created_at": created, "actor": self.actor,
            "spec": {"strategy_id": str(spec.get("strategy_id", "UNKNOWN")), "version": str(spec.get("version", "0.0.0")),
                     "spec_sha256": spec_sha, "status": spec.get("status") if spec.get("status") in
                     {"DRAFT", "REGISTERED", "FROZEN"} else "DRAFT"},
            "trial": trial,
            "data": [{k: str(d.get(k, "UNKNOWN")) for k in ("dataset_id", "snapshot_id", "producer_commit_sha", "verdict")}
                     | {"exclusions_applied": list(d.get("mandatory_exclusions", []))} for d in dsets],
            "code": {"strategy_agent_sha": self.agent_sha, "engine_name": self.engine.name,
                     "engine_git_sha": self.engine.git_sha if self.engine.git_sha else "NOT_RUN",
                     "engine_version": self.engine.version, "execution_convention": "oracle-execution-convention-v1",
                     "engine_benchmark_status": self.engine.benchmark_status},
            "split": {"split_ref": req.split_ref, "fold_id": req.fold_id, "role": req.role,
                      "window_start": req.window_start, "window_end": req.window_end,
                      "holdout_lock_id": spec.get("experiment", {}).get("holdout_lock_id")},
            "seed": req.seed, "cost_multiplier": req.cost_multiplier, "holdout_access": req.holdout_access,
        }

    def _emit(self, base: dict, status: str, **extra) -> dict:
        return self.registry.append(dict(base, status=status, event_at=self.clock().isoformat(), **extra))

    def _admit(self, req, cancel, exp_id, created, spec, spec_sha, p_sha):
        # 2 family
        a = self.families.assign(spec)
        trial = {"family_id": a.family_id, "declared_family_id": a.declared_family_id, "family_assignment": a.mode,
                 "structure_sha256": a.structure_sha256, "params_sha256": p_sha, "parameters": req.parameters,
                 "index": self.registry.trial_count(a.family_id)}
        if spec["parameters"]["search"]["mode"] != "FIXED":
            trial["family_size_at_registration"] = spec["parameters"]["search"]["max_trials"]
        base = self._base(exp_id, created, req, trial, spec_sha)

        # 3 holdout (static rules + early H06)
        g = self.holdout.check(spec=spec, family_id=a.family_id, role=req.role,
                               dataset_ids=[d["dataset_id"] for d in spec["data_requirements"]["datasets"]],
                               window_start=req.window_start, window_end=req.window_end,
                               cost_multiplier=req.cost_multiplier, holdout_access=req.holdout_access)
        if not g.allowed:
            return "DONE", self._emit(base, "REJECTED_GOVERNANCE",
                                      rejection_reasons=[{"code": g.code, "message": g.message}])

        # 4 budget (in-flight runs included)
        b = self.budget.check(family_id=a.family_id, family_is_new=a.mode == "NEW", structure_sha=a.structure_sha256,
                              params_sha=p_sha, spec_max_trials=spec["parameters"]["search"].get("max_trials"))
        if not b.allowed:
            return "DONE", self._emit(base, "BUDGET_EXCEEDED",
                                      rejection_reasons=[{"code": b.code, "message": b.message}])

        # 5 engine
        if self.engine.benchmark_status != "BENCHMARK_PASS":
            return "DONE", self._emit(base, "REJECTED_CONTRACT", rejection_reasons=[
                {"code": "C00", "message": f"engine {self.engine.git_sha} has no BENCHMARK_PASS record"}])

        # 6 atomic single-use holdout reservation; consumed by ANY later outcome
        if req.holdout_access:
            try:
                self.holdout.reservations.reserve(lock_id=spec["experiment"]["holdout_lock_id"], family_id=a.family_id,
                                                  experiment_id=exp_id, spec_sha256=spec_sha, actor=self.actor,
                                                  at=self.clock().isoformat())
            except ConditionFailed as e:
                return "DONE", self._emit(base, "REJECTED_GOVERNANCE",
                                          rejection_reasons=[{"code": "H06", "message": str(e)}])

        self._emit(base, "QUEUED")
        if cancel.is_set():
            return "DONE", self._emit(base, "CANCELLED",
                                      rejection_reasons=[{"code": "X01", "message": "cancelled before start"}])
        self._emit(base, "RUNNING")
        return "RUN", base

    # --------------------------------------------------------------- main

    def submit(self, req: RunRequest, cancel: threading.Event | None = None,
               timeout_seconds: float | None = None) -> dict:
        cancel = cancel or threading.Event()
        now = self.clock()
        exp_id, created = new_experiment_id(now), now.isoformat()
        spec = req.spec
        spec_sha = canonical_hash(spec)
        p_sha = params_hash(req.parameters)

        # 1 spec
        issues = [i for i in validate(spec, self.governance) if i.level == "ERROR"]
        if not issues and spec["status"] not in {"REGISTERED", "FROZEN"}:
            issues = [Issue("ERROR", "R01", "/status", "only REGISTERED/FROZEN specs may execute")]
        if issues:
            trial = {"family_id": "UNASSIGNED", "index": 0, "parameters": req.parameters, "params_sha256": p_sha}
            return self._emit(self._base(exp_id, created, req, trial, spec_sha), "REJECTED_SPEC",
                              rejection_reasons=[{"code": i.rule, "message": i.message, "path": i.path} for i in issues])

        # 2-6 admission: every check-then-write runs under ONE exclusive lock, and the RUNNING
        # event is written before it is released, so concurrent workers see each other's runs.
        with self.admission.hold():
            outcome = self._admit(req, cancel, exp_id, created, spec, spec_sha, p_sha)
        if outcome[0] == "DONE":
            return outcome[1]
        base = outcome[1]

        request = BacktestRequest(spec=spec, spec_sha256=spec_sha,
                                  datasets=tuple(DatasetRef(d["dataset_id"], d["snapshot_id"], d["producer_commit_sha"],
                                                            d["verdict"], d["usage_scope"],
                                                            tuple(d.get("mandatory_exclusions", ())))
                                                 for d in spec["data_requirements"]["datasets"]),
                                  window_start=req.window_start, window_end=req.window_end,
                                  warmup_start=req.warmup_start, cost_multiplier=req.cost_multiplier,
                                  seed=req.seed, parameters=req.parameters)
        box: dict = {}

        def work():
            try:
                box["report"] = self.port.run(request)
            except BaseException as e:  # noqa: BLE001 - recorded, not swallowed
                box["error"] = e

        limit = timeout_seconds if timeout_seconds is not None else self.budget.cfg.timeout_seconds_per_experiment
        t0 = time.monotonic()
        th = threading.Thread(target=work, daemon=True)
        th.start()
        while th.is_alive() and time.monotonic() - t0 < limit and not cancel.is_set():
            th.join(0.01)
        wall = round(time.monotonic() - t0, 6)
        usage = {"wall_seconds": wall}
        if cancel.is_set():
            return self._emit(base, "CANCELLED", resource_usage=usage,
                              rejection_reasons=[{"code": "X02", "message": "cancelled while running; result discarded"}])
        if th.is_alive():
            # soft timeout: the worker thread is abandoned and any late result is ignored.
            # M6 runs each experiment in its own process for a hard kill.
            return self._emit(base, "TIMEOUT", resource_usage=usage,
                              rejection_reasons=[{"code": "T01", "message": f"exceeded {limit}s"}])
        if "error" in box:
            e = box["error"]
            return self._emit(base, "FAILED", resource_usage=usage,
                              rejection_reasons=[{"code": "E01", "message": f"{type(e).__name__}: {e}"[:500]}])

        # 7 contract
        report = box["report"]
        try:
            check_report(report, request)
        except ContractViolation as e:
            return self._emit(base, "REJECTED_CONTRACT", resource_usage=usage,
                              rejection_reasons=[{"code": "C04", "message": str(e)[:500]}])

        prov = dict(report.provenance or {})
        if prov.get("engine_git_sha") and self.engine.git_sha and prov["engine_git_sha"] != self.engine.git_sha:
            return self._emit(base, "REJECTED_CONTRACT", resource_usage=usage, rejection_reasons=[
                {"code": "C05", "message": f"report engine {prov['engine_git_sha']} != benchmarked engine "
                                           f"{self.engine.git_sha}"}])

        # 8 completed
        valid = [float(r["net_pnl"]) for r in report.round_trips]
        invalid = [float(r["net_pnl"]) for r in report.invalidated_round_trips]
        suff = assess(valid, invalid)
        metrics = {"fills": len(report.fills), "round_trips": len(valid), "effective_net_pnl": suff["effective_net_pnl"],
                   "open_position": report.open_position is not None, "rejections": len(report.rejections),
                   "execution": {"report_status": report.status,
                                 **{k: prov.get(k) for k in ("engine_name", "engine_git_sha", "core_status",
                                                             "adapter_version", "cost_profile_id",
                                                             "cost_profile_sha256", "execution_convention")
                                    if prov.get(k) is not None}}}
        return self._emit(base, "COMPLETED", resource_usage=usage, metrics=metrics, sample_sufficiency=suff)

