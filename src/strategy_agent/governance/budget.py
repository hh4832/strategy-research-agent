"""Research budget and stop rules.

All counters are DERIVED FROM THE REGISTRY, never kept in memory, so a crash
or restart cannot reset them. ``check`` must be called while holding the
runner's ``AdmissionLock`` and the RUNNING event must be written before the
lock is released; in-flight runs (QUEUED/RUNNING) count toward every cap and
are charged their full timeout for B04. Together this makes admission atomic
for any number of workers on one host. Limits live in
``governance/research_budget.json``.

Codes
-----
B01  family trial cap reached (registry trial count, protocol §6.1)
B02  daily backtest count reached (experiments that reached RUNNING today)
B03  daily new-family cap reached
B04  daily wall-time budget exhausted (completed wall time + in-flight runs charged at timeout + this run's timeout)
B05  HALTED: N consecutive FAILED/TIMEOUT outcomes; only a research-lead
     RESUME control (governance/controls.jsonl) clears it
B06  spec-level max_trials reached for this exact structure
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from strategy_agent.registry.chain import ChainedLog

from .holdout import DEFAULT_APPROVERS, GuardDecision

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_BUDGET = ROOT / "governance" / "research_budget.json"
DEFAULT_CONTROLS = ROOT / "governance" / "controls.jsonl"


@dataclass(frozen=True)
class BudgetConfig:
    max_trials_per_family: int
    max_backtests_per_day: int
    max_new_families_per_day: int
    max_wall_seconds_per_day: float
    timeout_seconds_per_experiment: float
    max_consecutive_failures: int
    day_timezone: str = "Asia/Taipei"

    @classmethod
    def load(cls, path: str | Path = DEFAULT_BUDGET) -> "BudgetConfig":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


class Controls:
    """Read-only view of research-lead control events (RESUME)."""

    def __init__(self, path: str | Path = DEFAULT_CONTROLS, approvers_path: str | Path = DEFAULT_APPROVERS):
        self.log = ChainedLog(path)
        self.approvers = set(json.loads(Path(approvers_path).read_text(encoding="utf-8"))["research_leads"])

    def last_resume(self) -> datetime | None:
        last = None
        for r in self.log.verify():
            if r.get("action") == "RESUME" and r.get("approved_by") in self.approvers:
                last = datetime.fromisoformat(r["at"])
        return last


class BudgetGuard:
    def __init__(self, registry, config: BudgetConfig, controls: Controls,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.registry, self.cfg, self.controls, self.clock = registry, config, controls, clock

    def _day_start(self) -> datetime:
        tz = ZoneInfo(self.cfg.day_timezone)
        local = self.clock().astimezone(tz)
        return datetime.combine(local.date(), time(0), tz)

    def halted(self) -> tuple[bool, int]:
        resume = self.controls.last_resume()
        streak = 0
        for r in self.registry.records():
            if resume is not None and datetime.fromisoformat(r["event_at"]) <= resume:
                continue
            if r["status"] == "COMPLETED":
                streak = 0
            elif r["status"] in {"FAILED", "TIMEOUT"}:
                streak += 1
        return streak >= self.cfg.max_consecutive_failures, streak

    def check(self, *, family_id: str, family_is_new: bool, structure_sha: str, params_sha: str,
              spec_max_trials: int | None) -> GuardDecision:
        halted, streak = self.halted()
        if halted:
            return GuardDecision(False, "B05", f"HALTED after {streak} consecutive FAILED/TIMEOUT; "
                                               "research-lead RESUME required")
        finals = self.registry.family_records(family_id)
        seen = {(r["trial"].get("structure_sha256"), r["trial"].get("params_sha256")) for r in finals
                if r["status"] not in {"REJECTED_GOVERNANCE", "BUDGET_EXCEEDED"}}
        new_trial = (structure_sha, params_sha) not in seen
        if new_trial and self.registry.trial_count(family_id) >= self.cfg.max_trials_per_family:
            return GuardDecision(False, "B01", f"family {family_id} reached {self.cfg.max_trials_per_family} trials")
        if spec_max_trials is not None and new_trial:
            same_structure = {p for s, p in seen if s == structure_sha}
            if len(same_structure) >= spec_max_trials:
                return GuardDecision(False, "B06", f"spec max_trials={spec_max_trials} reached")
        day0 = self._day_start()
        if self.registry.count_status({"RUNNING"}, since=day0) >= self.cfg.max_backtests_per_day:
            return GuardDecision(False, "B02", f"daily backtest cap {self.cfg.max_backtests_per_day} reached")
        if family_is_new:
            new_today = {r["trial"]["family_id"] for r in self.registry.records()
                         if r["trial"].get("family_assignment") == "NEW"
                         and datetime.fromisoformat(r["event_at"]) >= day0}
            if len(new_today) >= self.cfg.max_new_families_per_day:
                return GuardDecision(False, "B03", f"daily new-family cap {self.cfg.max_new_families_per_day} reached")
        wall = sum(float(r.get("resource_usage", {}).get("wall_seconds", 0)) for r in self.registry.records()
                   if datetime.fromisoformat(r["event_at"]) >= day0)
        # in-flight runs are charged their full timeout up front, so concurrent admissions cannot overshoot
        in_flight = [r for r in self.registry.final_records() if r["status"] in {"QUEUED", "RUNNING"}]
        wall += len(in_flight) * self.cfg.timeout_seconds_per_experiment
        if wall + self.cfg.timeout_seconds_per_experiment > self.cfg.max_wall_seconds_per_day:
            return GuardDecision(False, "B04", f"daily wall-time budget {self.cfg.max_wall_seconds_per_day}s used")
        return GuardDecision(True)
