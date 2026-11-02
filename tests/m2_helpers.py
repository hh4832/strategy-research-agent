"""Shared fixtures for M2 tests. FakePort stands in for the M1 market-research-core adapter."""
from __future__ import annotations

import copy
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from strategy_agent.family.canonical import FamilyIndex
from strategy_agent.governance.budget import BudgetConfig, BudgetGuard, Controls
from strategy_agent.governance.holdout import HoldoutBook, HoldoutGuard
from strategy_agent.governance.holdout_admin import approve
from strategy_agent.ports import REQUIRED_PROVENANCE, BacktestReport
from strategy_agent.registry.experiments import ExperimentRegistry
from strategy_agent.runner import EngineInfo, ExperimentRunner, RunRequest
from strategy_agent.spec.validate import canonical_hash

EX = Path(__file__).parents[1] / "specs" / "examples"
LOCK_URL = "https://app.notion.com/p/example-approval"


def registered(name="sma_crossover_tw_2330_5m.json", status="REGISTERED", mutate=None, max_trials=50):
    s = json.loads((EX / name).read_text())
    s["parameters"]["search"]["max_trials"] = max_trials
    if mutate:
        mutate(s)
    s["status"] = status
    s["provenance"]["spec_sha256"] = None
    s["provenance"]["spec_sha256"] = canonical_hash(s)
    return s


class FakePort:
    def __init__(self, mode="ok", sleep=0.0, round_trips=None, invalidated=(), engine_sha="16281b2a242c"):
        self.mode, self.sleep, self.calls = mode, sleep, 0
        self.round_trips = round_trips if round_trips is not None else [{"net_pnl": "100"}] * 40
        self.invalidated, self.engine_sha = list(invalidated), engine_sha
        self.release = threading.Event()

    def run(self, req):
        self.calls += 1
        if self.sleep:
            self.release.wait(self.sleep)
        if self.mode == "raise":
            raise RuntimeError("engine exploded")
        prov = {k: "x" for k in REQUIRED_PROVENANCE}
        prov.update(engine_git_sha=self.engine_sha, execution_convention="oracle-execution-convention-v1",
                    spec_sha256=req.spec_sha256)
        fill_ts = req.window_start[:10] + "T09:05:00+08:00"
        return BacktestReport([{"exec_ts": fill_ts}], self.round_trips, [], [], None, prov,
                              invalidated_round_trips=self.invalidated)


class Clock:
    def __init__(self, t=datetime(2026, 11, 3, 2, 0, tzinfo=timezone.utc)):
        self.t = t

    def __call__(self):
        self.t += timedelta(seconds=1)
        return self.t


def env(tmp: Path, *, budget=None, lock=True, port=None, benchmark="BENCHMARK_PASS", clock=None):
    gov = tmp / "gov"
    gov.mkdir(parents=True, exist_ok=True)
    (gov / "approvers.json").write_text(json.dumps({"research_leads": ["hh4832"]}))
    locks, controls = gov / "holdout_locks.jsonl", gov / "controls.jsonl"
    locks.touch()
    controls.touch()
    if lock:
        approve(locks, gov / "approvers.json", lock_id="HOLDOUT-TW-5M-v1", dataset_id="tw_equity_fubon_5m",
                snapshot_id="final_gate_20261031_154458_20eba73", start="2026-01-01T00:00:00+08:00", end=None,
                approved_by="hh4832", approval_ref=LOCK_URL)
    reg = ExperimentRegistry(tmp / "experiments.jsonl")
    cfg = BudgetConfig(**({"max_trials_per_family": 20, "max_backtests_per_day": 50, "max_new_families_per_day": 3,
                           "max_wall_seconds_per_day": 14400, "timeout_seconds_per_experiment": 5,
                           "max_consecutive_failures": 3} | (budget or {})))
    clock = clock or Clock()
    budget_guard = BudgetGuard(reg, cfg, Controls(controls, gov / "approvers.json"), clock=clock)
    runner = ExperimentRunner(reg, FamilyIndex(tmp / "families.jsonl"),
                              HoldoutGuard(HoldoutBook(locks, gov / "approvers.json"), reg), budget_guard,
                              port or FakePort(), EngineInfo("market-research-core", "16281b2a242c", "0.2.0", benchmark),
                              agent_sha="abcdef1", clock=clock)
    return runner, reg


def dev_request(spec, params=None, **kw):
    base = dict(spec=spec, parameters=params or {"fast": 10, "slow": 50}, role="WF_TEST", split_ref="SPLIT-TW-5M-v1",
                fold_id="WF-01", window_start="2025-01-01T00:00:00+08:00", window_end="2025-04-01T00:00:00+08:00",
                warmup_start="2024-12-01T00:00:00+08:00")
    base.update(kw)
    return RunRequest(**base)


def holdout_request(spec, **kw):
    return dev_request(spec, role="FINAL_HOLDOUT", fold_id="HOLDOUT", window_start="2026-01-01T00:00:00+08:00",
                       window_end="2026-10-23T00:00:00+08:00", holdout_access=True, **kw)


def clone(x):
    return copy.deepcopy(x)
