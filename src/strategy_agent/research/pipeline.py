"""M3-B automated research runner: one fixed strategy, one reproducible run at a time.

    Strategy Spec -> Governance Admission -> Data Access -> Signal Generation -> Core Execution
      -> Performance Evaluation -> Experiment Registry -> Research Report -> Archive

Every stage appends a ``Stage`` entry (inputs, outputs, status, error). Nothing is skipped silently: a stage that
does not run is recorded as SKIPPED with the reason, and any contract mismatch stops the run (fail closed).

Assembly only - no second engine:
* admission, holdout, budget, family, engine-benchmark gate and the registry events are the M2
  ``ExperimentRunner`` (unchanged);
* execution is the M1 ``MrcBacktestPort`` around market-research-core ``ExecutionEngine.run``;
* signals are the M2 pure functions (``signals.sma.sma_crossover``);
* evaluation only aggregates what Core returned (``evaluation.research``).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from strategy_agent.adapters import mrc_adapter as A
from strategy_agent.evaluation.research import evaluate
from strategy_agent.ports import BacktestRequest
from strategy_agent.runner import EngineInfo, ExperimentRunner, RunRequest
from strategy_agent.signals.sma import sma_crossover
from strategy_agent.spec.validate import canonical_hash, validate

from .data import MarketData
from .environment import GovernanceEnv

ROOT = Path(__file__).resolve().parents[3]
RUNNER_VERSION = "m3b-0.1.1"
R02_ENFORCED = True        # research-lead decision (M3-B.1): mandatory for every research entry point
FIXED_BASELINE = {"fast": 5, "slow": 20}


class ResearchContractError(RuntimeError):
    """A stage input/output did not match its contract; the run stops (fail closed)."""


@dataclass
class Stage:
    name: str
    status: str = "PENDING"           # OK | FAILED | SKIPPED
    inputs: dict = field(default_factory=dict)
    outputs: dict = field(default_factory=dict)
    error: str | None = None


@dataclass(frozen=True)
class ResearchJob:
    spec: dict
    parameters: dict
    window_start: str
    window_end: str
    warmup_start: str
    role: str = "WF_TEST"
    split_ref: str = ""
    fold_id: str = "DEV-01"
    cost_multiplier: float = 1.0
    seed: int = 0
    label: str = ""
    research_config: dict | None = None


@dataclass
class ResearchResult:
    job: ResearchJob
    identity: str
    record: dict
    events: list
    stages: list
    report: Any = None
    market_data: MarketData | None = None
    decisions: list = field(default_factory=list)
    metrics: dict | None = None
    metrics_sha256: str | None = None
    research_report_md: str | None = None
    archive: dict | None = None
    context: dict = field(default_factory=dict)

    @property
    def status(self) -> str:
        return self.record.get("status", "UNKNOWN")


def agent_identity(repo: Path = ROOT) -> dict:
    """Agent commit + whether the working tree differs from it (a dirty tree is recorded, never hidden)."""
    def git(*a):
        r = subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else ""
    sha = git("rev-parse", "HEAD")
    dirty = bool(git("status", "--porcelain", "--untracked-files=no"))
    return {"agent_sha": sha or "0000000", "agent_tree_dirty": dirty, "agent_branch": git("rev-parse", "--abbrev-ref",
                                                                                             "HEAD")}


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, default=str, separators=(",", ":"), ensure_ascii=False)


def sha256_of(obj: Any) -> str:
    return hashlib.sha256(_canon(obj).encode()).hexdigest()


def run_identity(job: ResearchJob, *, spec_sha: str, engine: EngineInfo, agent_sha: str, profile_sha: str | None,
                 dataset: dict) -> str:
    """Same identity <=> same research question on the same inputs and code. Reruns keep the identity; every run
    still gets a new experiment id and a new archive directory."""
    return sha256_of({"spec_sha256": spec_sha, "parameters": job.parameters, "window": [job.warmup_start,
                      job.window_start, job.window_end], "role": job.role, "cost_multiplier": job.cost_multiplier,
                      "seed": job.seed, "engine_git_sha": engine.git_sha, "agent_sha": agent_sha,
                      "cost_profile_sha256": profile_sha, "dataset": {k: dataset.get(k) for k in (
                          "dataset_id", "snapshot_id", "producer_commit_sha")}, "runner": RUNNER_VERSION,
                      "adapter": A.ADAPTER_VERSION, "research_config": job.research_config})


class _InstrumentedPort:
    """BacktestPort that runs the M1 port and records the data / signal / core stages it drives."""

    def __init__(self, port: A.MrcBacktestPort, stages: dict[str, Stage]):
        self.port, self.stages = port, stages
        self.report = None

    def run(self, request: BacktestRequest):
        st = self.stages["core_execution"]
        st.inputs = {"spec_sha256": request.spec_sha256, "window": [request.window_start, request.window_end],
                     "cost_multiplier": request.cost_multiplier, "engine": "market-research-core ExecutionEngine.run"}
        try:
            rep = self.port.run(request)
        except BaseException as e:
            st.status, st.error = "FAILED", f"{type(e).__name__}: {e}"
            raise
        st.status = "OK"
        st.outputs = {"fills": len(rep.fills), "round_trips": len(rep.round_trips),
                      "invalidated_round_trips": len(rep.invalidated_round_trips), "rejections": len(rep.rejections),
                      "report_status": rep.status, "core_status": rep.provenance.get("core_status"),
                      "engine_git_sha": rep.provenance.get("engine_git_sha"),
                      "cost_profile_sha256": rep.provenance.get("cost_profile_sha256")}
        self.report = rep
        return rep


class ResearchRunner:
    def __init__(self, env: GovernanceEnv, source, semantics: A.MarketSemantics, *, core=None,
                 engine: EngineInfo | None = None, agent: dict | None = None, archiver=None,
                 clock: Callable[[], datetime] | None = None, progress: Callable[[str], None] | None = None):
        self.env, self.source, self.semantics = env, source, semantics
        self.core = core
        self.engine = engine or A.engine_info()
        self.agent = agent or agent_identity()
        self.archiver = archiver
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.progress = progress or (lambda m: None)

    def identity_of(self, job: ResearchJob) -> str:
        ref = job.spec.get('costs', {}).get('profile_ref') or {}
        profile = self.env.cost_profiles.get((ref.get('profile_id'), ref.get('version'))) or {}
        return run_identity(job, spec_sha=canonical_hash(job.spec), engine=self.engine,
                            agent_sha=self.agent['agent_sha'],
                            profile_sha=profile.get('provenance', {}).get('profile_sha256'),
                            dataset=job.spec['data_requirements']['datasets'][0])

    # ------------------------------------------------------------------ stages
    def _signals(self, job: ResearchJob, md: MarketData, st: Stage) -> list:
        p = job.parameters
        st.inputs = {"function": "strategy_agent.signals.sma.sma_crossover", "fast": p["fast"], "slow": p["slow"],
                     "bars": len(md.signal_bars), "eligible_bars": sum(b.eligible for b in md.signal_bars)}
        decs = sma_crossover(list(md.signal_bars), int(p["fast"]), int(p["slow"]))
        ws, we = datetime.fromisoformat(job.window_start), datetime.fromisoformat(job.window_end)
        kept = [d for d in decs if ws <= d.decision_ts < we]
        bad = [d for d in kept if not any(b.available_at == d.decision_ts for b in md.signal_bars)]
        if bad:
            raise ResearchContractError(f"{len(bad)} decisions not at a bar's available_at")
        st.status = "OK"
        st.outputs = {"decisions_total": len(decs), "decisions_in_window": len(kept),
                      "dropped_warmup_or_outside_window": len(decs) - len(kept),
                      "decision_time_rule": "bar available_at (bar_end) after the bar is complete",
                      "decisions_sha256": sha256_of([(d.decision_ts.isoformat(), d.target, d.reason) for d in kept])}
        return kept

    def run(self, job: ResearchJob) -> ResearchResult:
        names = ("spec", "governance_admission", "data_access", "signal_generation", "core_execution",
                 "performance_evaluation", "experiment_registry", "research_report", "archive")
        stages = {n: Stage(n) for n in names}
        spec = job.spec
        spec_sha = canonical_hash(spec)
        ref = spec.get("costs", {}).get("profile_ref") or {}
        profile = self.env.cost_profiles.get((ref.get("profile_id"), ref.get("version")))
        dataset = (spec.get("data_requirements", {}).get("datasets") or [{}])[0]
        ident = run_identity(job, spec_sha=spec_sha, engine=self.engine, agent_sha=self.agent["agent_sha"],
                             profile_sha=(profile or {}).get("provenance", {}).get("profile_sha256"), dataset=dataset)
        self.progress(f"[{job.label or 'run'}] identity {ident[:12]} spec {spec_sha[:12]}")

        # 1 spec (validation is repeated and recorded by the M2 runner; here it is logged as a stage)
        st = stages["spec"]
        issues = [i for i in validate(spec, self.env.governance) if i.level == "ERROR"]
        st.inputs = {"strategy_id": spec.get("strategy_id"), "version": spec.get("version"),
                     "status": spec.get("status"), "spec_sha256": spec_sha}
        st.outputs = {"errors": [f"{i.rule} {i.path} {i.message}" for i in issues]}
        st.status = "FAILED" if issues else "OK"
        from strategy_agent.runner import parameter_issues  # noqa: PLC0415
        perr = parameter_issues(spec, dict(job.parameters))
        if perr:                         # the M2 runner records the same R02 rejection in the registry
            st.status = "FAILED"
            st.outputs["errors"] += [f"{i.rule} {i.path} {i.message}" for i in perr]

        md_box: dict = {}

        def bars_provider(request):
            ds = stages["data_access"]
            ds.inputs = {"source": getattr(self.source, "kind", type(self.source).__name__),
                         "dataset_id": request.datasets[0].dataset_id, "snapshot_id": request.datasets[0].snapshot_id,
                         "warmup_start": request.warmup_start, "window_end": request.window_end}
            try:
                md = self.source.load(symbol=spec["universe"]["symbols"][0], warmup_start=request.warmup_start,
                                      window_end=request.window_end)
                if md.metadata.get("dataset_id") != request.datasets[0].dataset_id or \
                        md.metadata.get("snapshot_id") != request.datasets[0].snapshot_id:
                    raise ResearchContractError(f"source returned {md.metadata.get('dataset_id')}@"
                                                f"{md.metadata.get('snapshot_id')}, spec pins "
                                                f"{request.datasets[0].dataset_id}@{request.datasets[0].snapshot_id}")
                A.check_core_bars(md.frame)
            except BaseException as e:
                ds.status, ds.error = "FAILED", f"{type(e).__name__}: {e}"
                raise
            ds.status = "OK"
            ds.outputs = {k: md.metadata.get(k) for k in ("rows", "frame_sha256", "kind", "first_day", "last_day")}
            md_box["md"] = md
            return md.frame

        def decisions_provider(request):
            sg = stages["signal_generation"]
            try:
                decs = self._signals(job, md_box["md"], sg)
            except BaseException as e:
                sg.status, sg.error = "FAILED", f"{type(e).__name__}: {e}"
                raise
            md_box["decisions"] = decs
            return decs

        sem_map = {dataset.get("dataset_id"): self.semantics}
        port = A.MrcBacktestPort({k: v for k, v in self.env.cost_profiles.items()},
                                 decisions_provider=decisions_provider, bars_provider=bars_provider,
                                 semantics=sem_map, core=self.core)
        ip = _InstrumentedPort(port, stages)
        runner = ExperimentRunner(self.env.registry, self.env.families, self.env.holdout, self.env.budget, ip,
                                  self.engine, agent_sha=self.agent["agent_sha"], admission=self.env.admission,
                                  actor="strategy-agent-m3b", clock=self.clock, governance=self.env.governance,
                                  enforce_parameter_ranges=R02_ENFORCED)
        if getattr(runner, "enforce_parameter_ranges", False) is not True:
            raise ResearchContractError("R02 parameter governance is mandatory for research runs; refusing to submit")
        rr = RunRequest(spec=spec, parameters=dict(job.parameters), role=job.role,
                        split_ref=job.split_ref or spec["experiment"]["split_ref"], fold_id=job.fold_id,
                        window_start=job.window_start, window_end=job.window_end, warmup_start=job.warmup_start,
                        cost_multiplier=job.cost_multiplier, seed=job.seed,
                        holdout_access=job.role == "FINAL_HOLDOUT")

        # 2-7 admission + execution + registry (M2 runner)
        ga = stages["governance_admission"]
        ga.inputs = {"environment": self.env.kind, "role": job.role, "window": [job.window_start, job.window_end],
                     "engine_benchmark_status": self.engine.benchmark_status, "engine_git_sha": self.engine.git_sha}
        if self.agent.get("agent_tree_dirty") and self.env.kind == "FORMAL":
            raise ResearchContractError("formal runs require a clean agent working tree")
        record = runner.submit(rr)
        events = self.env.registry.events(record["experiment_id"])
        statuses = [e["status"] for e in events]
        ga.outputs = {"family_id": record["trial"]["family_id"], "trial_index": record["trial"]["index"],
                      "events": statuses}
        admitted = "RUNNING" in statuses
        ga.status = "OK" if admitted else "FAILED"
        if not admitted:
            ga.error = "; ".join(f"{r['code']}: {r['message']}" for r in record.get("rejection_reasons", []))
        for n in ("data_access", "signal_generation", "core_execution"):
            if stages[n].status == "PENDING":
                stages[n].status = "SKIPPED"
                stages[n].error = f"not reached (registry status {record['status']})"
        reg = stages["experiment_registry"]
        reg.inputs = {"registry": str(self.env.registry.log.path)}
        reg.outputs = {"experiment_id": record["experiment_id"], "final_status": record["status"],
                       "events": statuses, "chain_verified": True,
                       "rejection_reasons": record.get("rejection_reasons", [])}
        self.env.registry.records()                     # verifies the hash chain (raises if broken)
        reg.status = "OK"

        res = ResearchResult(job, ident, record, events, [], ip.report, md_box.get("md"), md_box.get("decisions", []),
                             context={"spec_sha256": spec_sha, "engine": asdict(self.engine), "agent": self.agent,
                                      "environment": self.env.kind, "overrides": list(self.env.overrides),
                                      "cost_profile": profile, "dataset": dataset, "research_config": job.research_config, "runner_version": RUNNER_VERSION,
                                      "adapter_version": A.ADAPTER_VERSION,
                                      "semantics": asdict(self.semantics)})

        # 8 evaluation
        ev = stages["performance_evaluation"]
        if record["status"] == "COMPLETED" and ip.report is not None and res.market_data is not None:
            try:
                ps = spec["position_sizing"]
                tf = spec["universe"]["timeframe"]
                if not tf.endswith("m"):
                    raise ResearchContractError(f"timeframe {tf} not supported by the M3-B evaluator")
                bpd = (res.market_data.metadata.get("fixture_meta") or {}).get("bars_per_day") or \
                    res.market_data.metadata.get("bars_per_day")
                if not bpd:
                    raise ResearchContractError("bars_per_day missing from dataset metadata (Sharpe scaling)")
                res.metrics = evaluate(ip.report, res.market_data.rows,
                                       initial_capital=float(self.semantics.initial_cash), bar_minutes=int(tf[:-1]),
                                       bars_per_year=252 * int(bpd), parameters=job.parameters,
                                       symbol=spec["universe"]["symbols"][0], order_qty=float(ps["units"]),
                                       family_id=record["trial"]["family_id"],
                                       family_trials=self.env.registry.trial_count(record["trial"]["family_id"]))
                res.metrics_sha256 = sha256_of(res.metrics)
                ev.status, ev.outputs = "OK", {"metrics_sha256": res.metrics_sha256,
                                               "trade_count": res.metrics["trades"]["trade_count"]}
            except BaseException as e:  # noqa: BLE001 - recorded
                ev.status, ev.error = "FAILED", f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"
        else:
            ev.status, ev.error = "SKIPPED", f"no completed backtest (registry status {record['status']})"

        # 9 report
        from .report import build_report  # noqa: PLC0415
        rp = stages["research_report"]
        res.stages = [asdict(s) for s in stages.values()]
        try:
            res.research_report_md = build_report(res)
            rp.status, rp.outputs = "OK", {"sha256": hashlib.sha256(res.research_report_md.encode()).hexdigest()}
        except BaseException as e:  # noqa: BLE001
            rp.status, rp.error = "FAILED", f"{type(e).__name__}: {e}"

        # 10 archive
        ar = stages["archive"]
        if self.archiver is None:
            ar.status, ar.error = "SKIPPED", "no archiver configured"
        else:
            res.stages = [asdict(s) for s in stages.values()]
            try:
                res.archive = self.archiver.write(res)
                ar.status = "OK" if res.archive["status"] == "ARCHIVED" else "FAILED"
                ar.outputs = res.archive
                if ar.status == "FAILED":
                    ar.error = res.archive.get("reason")
            except BaseException as e:  # noqa: BLE001
                ar.status, ar.error = "FAILED", f"{type(e).__name__}: {e}"
        res.stages = [asdict(s) for s in stages.values()]
        if self.archiver is not None and res.archive and res.archive.get("path"):
            self.archiver.finalize(res)          # stage log incl. the archive stage itself
        self.progress(f"[{job.label or 'run'}] {record['status']} exp {record['experiment_id']} "
                      f"archive {(res.archive or {}).get('status')}")
        return res
