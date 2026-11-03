"""M3-B1 synthetic MVP acceptance on the REAL market-research-core (skipped without Core).

Covers the nine acceptance items of the task:
 1 SMA fixed 5/20            2 Runner -> Core -> Registry     3 performance metrics      4 research files
 5 independent run archive   6 rerun reproducibility          7 no overwrite             8 contract failure recorded
 9 formal holdout / registry untouched
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

core = pytest.importorskip("market_research_core")
if not hasattr(core, "ExecutionEngine"):
    pytest.skip("installed Core predates ExecutionEngine", allow_module_level=True)

from strategy_agent.adapters import mrc_adapter as A  # noqa: E402
from strategy_agent.costs.profile import profile_hash  # noqa: E402
from strategy_agent.research import synthetic_setup  # noqa: E402
from strategy_agent.research.batch import cost_sensitivity_jobs, run_batch  # noqa: E402
from strategy_agent.research.environment import ROOT, synthetic_spec  # noqa: E402
from strategy_agent.research.pipeline import FIXED_BASELINE, ResearchContractError  # noqa: E402
from strategy_agent.research.report import DROP, KEEP, RETEST, UNDECIDABLE  # noqa: E402
from strategy_agent.spec.validate import GovernanceContext  # noqa: E402

if A.engine_info().benchmark_status != "BENCHMARK_PASS":
    pytest.skip("installed Core has no BENCHMARK_PASS record", allow_module_level=True)

FORMAL = [ROOT / "registry" / "experiments.jsonl", ROOT / "registry" / "families.jsonl",
          ROOT / "governance" / "holdout_locks.jsonl", ROOT / "governance" / "controls.jsonl",
          ROOT / "governance" / "source_confirmations.json", ROOT / "governance" / "market_readiness.json"]
AGENT = {"agent_sha": "abcdef1234567890abcdef1234567890abcdef12", "agent_tree_dirty": False, "agent_branch": "test"}


def _digest(paths):
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None for p in paths}


@pytest.fixture(scope="module")
def formal_before():
    return _digest(FORMAL)


@pytest.fixture(scope="module")
def two_runs(tmp_path_factory, formal_before):
    root = tmp_path_factory.mktemp("m3b")
    runner, job = synthetic_setup(root / "env", archive_root=root / "archive", agent=AGENT)
    r1 = runner.run(job)
    archive1 = {p.name: p.read_bytes() for p in Path(r1.archive["path"]).iterdir()}
    r2 = runner.run(job)
    return root, runner, job, r1, r2, archive1


# 1 + 2
def test_fixed_sma_5_20_runs_through_runner_core_registry(two_runs):
    _, runner, job, r1, _, _ = two_runs
    assert job.parameters == FIXED_BASELINE == {"fast": 5, "slow": 20}
    assert r1.status == "COMPLETED", r1.record.get("rejection_reasons")
    assert [e["status"] for e in r1.events] == ["QUEUED", "RUNNING", "COMPLETED"]
    assert {s["name"]: s["status"] for s in r1.stages} == {n: "OK" for n in (
        "spec", "governance_admission", "data_access", "signal_generation", "core_execution",
        "performance_evaluation", "experiment_registry", "research_report", "archive")}
    rec = r1.record
    assert rec["code"]["engine_git_sha"] == A.core_identity()["git_sha"] and rec["code"]["engine_benchmark_status"] == \
        "BENCHMARK_PASS" and rec["code"]["strategy_agent_sha"] == AGENT["agent_sha"]
    ex = rec["metrics"]["execution"]
    assert ex["core_status"] == "RESEARCH_ONLY" and ex["report_status"] == "RESEARCH_INTEGRATION_DEMO"
    assert ex["agent_cost_profile_sha256"] == job.spec["costs"]["profile_ref"]["sha256"]
    assert ex["dataset_snapshot_id"] == job.spec["data_requirements"]["datasets"][0]["snapshot_id"]
    assert rec["holdout_access"] is False and rec["split"]["role"] == "WF_TEST"


def test_no_lookahead_fills_after_decisions_at_bar_close(two_runs):
    _, _, _, r1, _, _ = two_runs
    avail = {b.available_at for b in r1.market_data.signal_bars}
    assert r1.decisions and all(d.decision_ts in avail for d in r1.decisions)
    fills = sorted(r1.report.fills, key=lambda f: f["exec_ts"])
    decs = sorted(r1.decisions, key=lambda d: d.decision_ts)
    assert len(fills) == len(decs)
    for f, d in zip(fills, decs):
        assert datetime.fromisoformat(f["exec_ts"]) >= d.decision_ts
        assert f["side"] == ("BUY" if d.target == 1 else "SELL")


# 3
def test_performance_metrics_complete(two_runs):
    m = two_runs[3].metrics
    for k in ("trade_count", "win_rate", "average_win", "average_loss", "payoff_ratio", "expectancy", "profit_factor"):
        assert k in m["trades"]
    for k in ("total_return", "max_drawdown", "sharpe_ratio", "cost_adjusted_net_return", "exposure",
              "turnover_annualised", "tail_risk", "by_year", "by_symbol", "by_holding_period_bars",
              "by_parameter_set", "by_regime", "data_quality", "sample", "multiple_testing"):
        assert k in m
    assert m["by_regime"]["status"] == "NOT_EVALUABLE"
    assert m["trades"]["trade_count"] == two_runs[3].report.round_trips.__len__()
    assert m["data_quality"]["zero_volume_bars"] == 3 and m["data_quality"]["missing_bars"] == 2
    assert m["multiple_testing"]["family_trials"] == 1


# 4 + 5
def test_report_and_archive(two_runs):
    _, _, _, r1, _, _ = two_runs
    md = r1.research_report_md
    for i, title in enumerate(["Experiment summary", "Market mechanism", "Hypothesis", "Exact strategy rules",
                               "Data source / snapshot / PIT definition", "CostProfile / assumptions",
                               "Backtest metrics", "Yearly / regime / trade decomposition",
                               "Bias and data-quality checks", "Negative evidence", "Opponent's view",
                               "Remaining uncertainties", "Final recommendation"], 1):
        assert f"## {i}. {title}" in md
    assert "SYNTHETIC FIXTURE RUN" in md
    v = r1.context["verdicts"]
    assert v["engineering"]["status"] == "PASS" and v["strategy"]["decision"] == UNDECIDABLE
    assert any("synthetic" in b for b in v["strategy"]["blockers"])
    d = Path(r1.archive["path"])
    assert r1.archive["status"] == "ARCHIVED" and r1.archive["verified"]
    import re
    assert re.fullmatch(r"\d{8}_\d{6}_abcdef1(-\d+)?", d.name)
    need = {"run_info.txt", "experiment_record.json", "fills.csv", "trades.csv", "rejections.csv", "equity_curve.csv",
            "metrics.json", "research_report.md", "snapshot_refs.json", "dataset_metadata.json", "manifest.json",
            "stages.json", "final_status.json", "decisions.csv"}
    assert need <= {p.name for p in d.iterdir()}
    man = json.loads((d / "manifest.json").read_text())["files"]
    for name, v2 in man.items():
        assert hashlib.sha256((d / name).read_bytes()).hexdigest() == v2["sha256"]
    refs = json.loads((d / "snapshot_refs.json").read_text())
    assert refs["core"]["git_sha"] == A.core_identity()["git_sha"] and refs["agent"]["agent_sha"] == AGENT["agent_sha"]
    assert refs["cost_profile"]["agent_sha256"] and refs["cost_profile"]["core_sha256"]
    assert json.loads((d / "final_status.json").read_text())["status"] == "PASS"
    assert not any(p.suffix == ".parquet" for p in d.iterdir())


# 6 + 7
def test_rerun_is_reproducible_and_never_overwrites(two_runs):
    root, _, _, r1, r2, archive1 = two_runs
    assert r1.identity == r2.identity and r1.metrics_sha256 == r2.metrics_sha256
    assert r1.record["experiment_id"] != r2.record["experiment_id"]
    assert r1.archive["path"] != r2.archive["path"]
    a, b = Path(r1.archive["path"]), Path(r2.archive["path"])
    assert (a / "trades.csv").read_bytes() == (b / "trades.csv").read_bytes()
    assert (a / "fills.csv").read_bytes() == (b / "fills.csv").read_bytes()
    assert {p.name: p.read_bytes() for p in a.iterdir()} == archive1          # first archive untouched
    reg = [json.loads(x) for x in (root / "env" / "experiments.jsonl").read_text().splitlines()]
    assert sum(r["status"] == "COMPLETED" for r in reg) == 2                   # both kept, append-only


# 8
def test_contract_failures_are_recorded_in_the_registry(tmp_path):
    runner, job = synthetic_setup(tmp_path / "env", archive_root=tmp_path / "archive", agent=AGENT)
    # (a) cost profile Core cannot express (gap G18): per-share commission -> CoreContractGap K18 -> FAILED
    prof = copy.deepcopy(next(iter(runner.env.cost_profiles.values())))
    prof["version"] = "1.0.1"
    prof["components"][0] = {**prof["components"][0], "per_unit": 0.005}
    prof["components"][0].pop("rate")
    prof["provenance"]["profile_sha256"] = profile_hash(prof)
    runner.env.cost_profiles[(prof["profile_id"], prof["version"])] = prof
    g = runner.env.governance
    runner.env.governance = GovernanceContext(g.confirmations, dict(runner.env.cost_profiles), g.approvers)
    spec = synthetic_spec(json.loads((ROOT / "specs" / "research" / "sma_crossover_tsm_15m_5_20.json").read_text()),
                          runner.source.describe(), prof)
    r = runner.run(replace(job, spec=spec, label="g18"))
    assert r.status == "FAILED" and "K18" in r.record["rejection_reasons"][0]["message"]
    assert [e["status"] for e in r.events] == ["QUEUED", "RUNNING", "FAILED"]
    st = {s["name"]: s for s in r.stages}
    assert st["core_execution"]["status"] == "FAILED" and "K18" in st["core_execution"]["error"]
    assert st["performance_evaluation"]["status"] == "SKIPPED"
    assert r.archive["status"] == "ARCHIVED" and r.context["verdicts"]["strategy"]["decision"] == UNDECIDABLE
    assert json.loads((Path(r.archive["path"]) / "final_status.json").read_text())["status"] == "FAIL"
    # (b) data source returns a different snapshot than the spec pins -> ResearchContractError -> FAILED
    src = runner.source
    orig = src.describe

    def wrong():
        return dict(orig(), snapshot_id="tampered_snapshot_id")
    src.describe = wrong
    try:
        r = runner.run(replace(job, label="snapshot-mismatch"))
    finally:
        src.describe = orig
    assert r.status == "FAILED" and "ResearchContractError" in r.record["rejection_reasons"][0]["message"]
    # (c) engine without BENCHMARK_PASS -> REJECTED_CONTRACT C00, Core never called
    from strategy_agent.runner import EngineInfo
    runner.engine = EngineInfo("market-research-core", "0" * 40, "x", "NOT_VERIFIED")
    r = runner.run(replace(job, label="unverified-engine"))
    assert r.status == "REJECTED_CONTRACT" and r.record["rejection_reasons"][0]["code"] == "C00"
    assert {s["name"]: s["status"] for s in r.stages}["core_execution"] == "SKIPPED"


def test_parameter_search_is_refused(tmp_path):
    runner, job = synthetic_setup(tmp_path / "env", agent=AGENT)
    r = runner.run(replace(job, parameters={"fast": 10, "slow": 20}))
    assert r.status == "REJECTED_SPEC" and r.record["rejection_reasons"][0]["code"] == "R02"   # fixed 5/20 only
    assert {s["name"]: s["status"] for s in r.stages}["spec"] == "FAILED"


def test_cost_sensitivity_batch_resume_and_no_trial_inflation(tmp_path):
    runner, job = synthetic_setup(tmp_path / "env", archive_root=tmp_path / "archive", agent=AGENT)
    jobs = cost_sensitivity_jobs(job, [1.0, 2.0])
    logs = []
    out = run_batch(runner, jobs, batch_id="t", ledger_dir=tmp_path / "ledger", progress=logs.append)
    assert [o.status for o in out] == ["COMPLETED", "COMPLETED"] and any("[2/2]" in x for x in logs)
    pnl = [json.loads((Path(o.archive_path) / "metrics.json").read_text())["metrics"]["trades"]["net_pnl"] for o in out]
    assert pnl[0] > pnl[1]                                  # costs are applied by Core and scale with the multiplier
    fam = runner.env.registry.final_records()[-1]["trial"]["family_id"]
    assert runner.env.registry.trial_count(fam) == 1        # same parameters -> one trial, not two
    from strategy_agent.research.pipeline import run_identity
    ident = {o.label: o.identity for o in out}

    def identity_of(j):
        return ident.get(j.label)
    again = run_batch(runner, jobs, batch_id="t", ledger_dir=tmp_path / "ledger", resume=True, progress=logs.append,
                      identity_of=identity_of)
    assert [o.status for o in again] == ["SKIPPED_RESUME", "SKIPPED_RESUME"]
    assert run_identity is not None


# 9
def test_formal_registry_and_holdout_untouched(two_runs, formal_before):
    assert _digest(FORMAL) == formal_before


def test_recommendation_rules_are_separate_from_engineering(two_runs):
    from strategy_agent.research.report import recommend
    r1 = two_runs[3]
    fake = copy.copy(r1)
    fake.context = dict(r1.context, environment="FORMAL", cost_profile={"status": "APPROVED", "components": []})
    fake.market_data = replace(r1.market_data, metadata=dict(r1.market_data.metadata, kind="REAL_MARKET"))
    # Core status is still RESEARCH_ONLY for this run -> still undecidable
    assert recommend(fake)["decision"] == UNDECIDABLE
    assert {KEEP, RETEST, DROP, UNDECIDABLE} == {"保留", "修改後再測", "淘汰", "無法判定"}


# ------------------------------------------------------------------ M3-B.1: R02 mandatory on research entry points
def test_r02_batch_cannot_smuggle_parameters(tmp_path):
    runner, job = synthetic_setup(tmp_path / "env", archive_root=tmp_path / "archive", agent=AGENT)
    bad = [replace(job, parameters={"fast": 6, "slow": 20}, label="fast6"),
           replace(job, parameters={"fast": 5, "slow": 20, "extra": 1}, label="extra"),
           replace(job, parameters={"fast": "5", "slow": 20}, label="string")]
    out = run_batch(runner, bad, batch_id="r02", ledger_dir=tmp_path / "ledger", progress=lambda m: None)
    assert [o.status for o in out] == ["REJECTED_SPEC"] * 3
    recs = runner.env.registry.final_records()
    assert [r["rejection_reasons"][0]["code"] for r in recs] == ["R02"] * 3       # recorded, not silently dropped
    assert not any(r["status"] in ("RUNNING", "COMPLETED") for r in runner.env.registry.records())


def test_r02_fixed_spec_cannot_be_quietly_redeclared(tmp_path):
    """Editing the FIXED defaults (and ranges) of the same strategy is still the same family: after the 5/20
    baseline, a 10/30 'FIXED' copy is stopped by the spec trial budget (B06), so no extra trial is spent."""
    runner, job = synthetic_setup(tmp_path / "env", agent=AGENT)
    assert runner.run(job).status == "COMPLETED"
    s = copy.deepcopy(job.spec)
    s["parameters"]["values"]["fast"].update(default=10, min=10, max=10)
    s["parameters"]["values"]["slow"].update(default=30, min=30, max=30)
    from strategy_agent.spec.validate import canonical_hash
    s["provenance"]["spec_sha256"] = None
    s["provenance"]["spec_sha256"] = canonical_hash(s)
    r = runner.run(replace(job, spec=s, parameters={"fast": 10, "slow": 30}, label="redeclared"))
    assert r.status == "BUDGET_EXCEEDED" and r.record["rejection_reasons"][0]["code"] == "B06"
    assert r.record["trial"]["family_id"] == runner.env.registry.final_records()[0]["trial"]["family_id"]


def test_r02_cannot_be_switched_off_by_patching(tmp_path, monkeypatch):
    from strategy_agent.research import pipeline
    runner, job = synthetic_setup(tmp_path / "env", agent=AGENT)
    monkeypatch.setattr(pipeline, "R02_ENFORCED", False)
    with pytest.raises(ResearchContractError, match="R02"):
        runner.run(replace(job, parameters={"fast": 6, "slow": 20}))
    assert runner.env.registry.records() == []                                 # nothing submitted
