"""M3-B.2: research configuration, R02 on every entry, cost scenarios, six-gate market registry, Core contract,
Drive failure records. Tests marked ``needs_core`` run the real market-research-core (skipped without it)."""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from strategy_agent.costs.profile import load_profiles, profile_hash
from strategy_agent.research import contract as CC
from strategy_agent.research import cost_scenarios as CS
from strategy_agent.research.config import (SYNTHETIC_TEMPORAL_POLICY, ResearchConfigError, config_hash, file_sha256,
                                            stamp, validate_config)
from strategy_agent.research.data import GATES, GatedMarketSource, MarketGateClosed, readiness
from strategy_agent.research.environment import (ROOT, SYNTH_HOLDOUT_START, SYNTH_LOCK_ID, synthetic_cost_profile,
                                                 synthetic_environment)

CFG = ROOT / "configs" / "research" / "sma_5_20_tsm_synthetic_v1.json"
CFG_LOCAL = ROOT / "configs" / "research" / "sma_5_20_tsm_synthetic_local_v1.json"
IBKR_SET = ROOT / "configs" / "cost_scenarios" / "ibkr_tsm_draft_v1.json"
SYN_SET = ROOT / "configs" / "cost_scenarios" / "synthetic_equity_test_v1.json"
FORMAL = [ROOT / "registry" / "experiments.jsonl", ROOT / "registry" / "families.jsonl",
          ROOT / "governance" / "holdout_locks.jsonl", ROOT / "governance" / "controls.jsonl",
          ROOT / "governance" / "market_readiness.json", ROOT / "governance" / "source_confirmations.json"]


def _digest():
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None for p in FORMAL}


def _syn_ctx():
    from strategy_agent.research.data import SyntheticSource
    src = SyntheticSource()
    return dict(profiles={(p["profile_id"], p["version"]): p for p in [synthetic_cost_profile()]},
                fixture_dataset=src.describe(), synthetic_holdout=(SYNTH_LOCK_ID, SYNTH_HOLDOUT_START))


def _cfg(path=CFG):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _bad(cfg, rule, **ctx):
    with pytest.raises(ResearchConfigError) as e:
        validate_config(cfg, **(_syn_ctx() | ctx))
    assert rule in {r for r, _ in e.value.issues}, e.value.issues
    return e.value


# ------------------------------------------------------------------ market gate v2
def test_market_gate_registry_six_gates_synced_to_core_pr3():
    d = json.loads((ROOT / "governance" / "market_readiness.json").read_text())
    assert d["schema_version"] == "2.0.0" and tuple(d["required_gates"]) == GATES
    e = d["markets"]["IBKR_TSM"]
    ref = e["core_gate_ref"]
    assert ref["pr"] == 3 and ref["pr_head_sha"] == "c1a3661d127c7139ee86d824d1a380edf3d2e114" and ref["merged"] is False
    assert e["snapshot_id"] == "20261101_205431_06359faf7ef4_IBKR_FINAL_GATE_FULL"
    f = e["facts"]
    assert f["timestamp_label"] == f["observed_available_at"] == f["closing_auction"] == "UNKNOWN"
    assert f["conservative_temporal_policy"]["policy_id"] == "BAR_END_STRICT_LATER"
    assert f["conservative_temporal_policy"]["approval"] == "PENDING_RESEARCH_OWNER"
    r = readiness("IBKR_TSM")
    st = r.gate_status()
    assert st == {"DATA_QUALITY": "PASS", "TEMPORAL_SAFETY": "UNKNOWN", "COST_MODEL_COMPATIBILITY": "FAIL",
                  "COST_APPROVAL": "FAIL", "HOLDOUT_APPROVAL": "FAIL", "FORMAL_EXECUTION_ELIGIBILITY": "FAIL"}
    assert r.status == "NO_GO" and e["cost_profile"]["status"] == "DRAFT"
    assert hashlib.sha256((ROOT / "governance" / "cost_profiles" / "ibkr_us_equity_v0.json").read_bytes()
                          ).hexdigest() == e["cost_profile"]["file_sha256"]
    with pytest.raises(MarketGateClosed):
        GatedMarketSource("IBKR_TSM", loader=lambda **k: None).check()


def _go_registry(tmp_path, **gate_over):
    pin = "https://github.com/hh4832/market-research-core/blob/" + "a" * 40 + "/docs/x.md"
    gates = {g: {"status": "PASS", "evidence_ref": pin if g != "COST_APPROVAL" and g != "HOLDOUT_APPROVAL"
                 else "https://example.test/approval", "approved_by": "lead"} for g in GATES}
    for g, v in gate_over.items():
        gates[g] = {**gates[g], **v}
    entry = {"status": "GO", "snapshot_id": "s", "producer_commit_sha": "p", "gates": gates,
             "allowed_window": {"start": "2020-01-01", "end": "2021-01-01"}}
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"required_gates": list(GATES), "markets": {"M": entry}}))
    return p


def test_gate_pass_needs_pinned_core_evidence_and_approval(tmp_path):
    ok = _go_registry(tmp_path)
    assert GatedMarketSource("M", loader=lambda **k: None, path=ok).check("2020-02-01", "2020-03-01").status == "GO"
    cases = {"TEMPORAL_SAFETY": ({"evidence_ref": "https://example.test/claude-says-ok"}, "FAIL_UNPINNED_EVIDENCE"),
             "FORMAL_EXECUTION_ELIGIBILITY": ({"evidence_ref": "https://github.com/hh4832/market-research-core/blob/main/x"},
                                              "FAIL_UNPINNED_EVIDENCE"),
             "COST_APPROVAL": ({"approved_by": None}, "FAIL_NO_APPROVAL"),
             "HOLDOUT_APPROVAL": ({"evidence_ref": None}, "FAIL_NO_EVIDENCE"),
             "DATA_QUALITY": ({"status": "UNKNOWN"}, "UNKNOWN")}
    for g, (over, want) in cases.items():
        p = _go_registry(tmp_path, **{g: over})
        assert readiness("M", p).gate_status()[g] == want
        with pytest.raises(MarketGateClosed, match=g):
            GatedMarketSource("M", loader=lambda **k: None, path=p).check()
    # a registry cannot drop one of the six required gates
    d = json.loads(ok.read_text())
    d["required_gates"] = ["DATA_QUALITY"]
    del d["markets"]["M"]["gates"]["TEMPORAL_SAFETY"]
    ok.write_text(json.dumps(d))
    assert "TEMPORAL_SAFETY" in readiness("M", ok).required
    with pytest.raises(MarketGateClosed, match="TEMPORAL_SAFETY"):
        GatedMarketSource("M", loader=lambda **k: None, path=ok).check()


# ------------------------------------------------------------------ research configuration
def test_committed_configs_are_valid_versioned_and_hashed():
    for path in (CFG, CFG_LOCAL):
        c = validate_config(_cfg(path), path=path, **_syn_ctx())
        assert c.sha256 == _cfg(path)["provenance"]["config_sha256"] == config_hash(_cfg(path))
        assert not c.issues and c.data["parameters"] == {"fast": 5, "slow": 20}
        assert [s.scenario_id for s in c.scenarios] == ["BASE", "LOW_FRICTIONLESS", "STRESS_2X", "STRESS_4X"]
        assert c.ref["temporal_policy"]["policy_id"] == SYNTHETIC_TEMPORAL_POLICY


def test_config_edit_without_rehash_is_refused_rc02():
    c = _cfg()
    c["parameters"]["fast"] = 6                                       # quiet edit, old hash kept
    e = _bad(c, "RC02")
    assert any(r == "RC04" for r, _ in e.issues)                       # and R02 is reported too


def test_config_rules(tmp_path):
    c = _cfg()
    _bad(stamp(dict(c, timeframe="5m")), "RC05")
    _bad(stamp({**c, "dataset": dict(c["dataset"], snapshot_id="other")}), "RC05")
    _bad(stamp({**c, "temporal_policy": dict(c["temporal_policy"], status="ACCEPTED")}), "RC06")
    _bad(stamp({**c, "strategy": dict(c["strategy"], spec_file_sha256="0" * 64)}), "RC03")
    _bad(stamp({**c, "costs": dict(c["costs"], scenario_set=dict(c["costs"]["scenario_set"], file_sha256="0" * 64))}), "RC07")
    w = copy.deepcopy(c["windows"])
    w["validation"]["end"] = "2025-04-20T00:00:00-04:00"               # into the holdout
    _bad(stamp({**c, "windows": w}), "RC08")
    w = copy.deepcopy(c["windows"])
    w["in_sample"]["end"] = "2025-03-10T00:00:00-04:00"                # overlaps validation
    _bad(stamp({**c, "windows": w}), "RC08")
    _bad(stamp({**c, "holdout": {"holdout_id": "HOLDOUT-I-PICKED"}}), "RC09")
    _bad(stamp({**c, "capital": dict(c["capital"], initial_capital=1e9)}), "RC10")
    _bad(stamp({**c, "position_sizing": dict(c["position_sizing"], units=1000)}), "RC10")
    _bad(stamp({**c, "risk_limits": {"allowed_directions": ["LONG", "SHORT"]}}), "RC10")
    _bad(stamp({**c, "output": {"target": "DRIVE", "path": "/tmp/elsewhere"}}), "RC11")
    with pytest.raises(ResearchConfigError) as e:
        validate_config(dict(c, unknown_override=True), **_syn_ctx())
    assert e.value.issues[0][0] == "RC01"


def test_real_market_config_cannot_upgrade_unknown_and_is_refused():
    spec_path = "specs/research/sma_crossover_tsm_15m_5_20.json"
    spec = json.loads((ROOT / spec_path).read_text())
    gate = readiness("IBKR_TSM").entry
    ib = load_profiles()[("ibkr_us_equity", "0.1.0")]
    c = _cfg()
    c.update(kind="REAL_MARKET", config_id="sma_5_20_tsm_real_probe",
             market={"market_id": "IBKR_TSM", "gate_version": gate["gate_version"]},
             dataset={k: spec["data_requirements"]["datasets"][0][k] for k in ("dataset_id", "snapshot_id", "producer_commit_sha")},
             temporal_policy={"policy_id": "BAR_END_STRICT_LATER", "version": "M3-A.1", "status": "ACCEPTED",
                              "source": "CORE_GATE", "evidence_ref": gate["gates"]["TEMPORAL_SAFETY"]["evidence_ref"],
                              "approved_by": "claude"},
             costs={"profile_ref": {"profile_id": "ibkr_us_equity", "version": "0.1.0", "sha256": profile_hash(ib)},
                    "scenario_set": {"path": "configs/cost_scenarios/ibkr_tsm_draft_v1.json",
                                     "file_sha256": file_sha256(IBKR_SET)}},
             holdout={"holdout_id": "HOLDOUT-TSM-15M-v1"})
    c["windows"] = {"warmup_start": "2018-01-01T00:00:00-05:00",
                    "in_sample": {"start": "2018-01-02T00:00:00-05:00", "end": "2019-01-02T00:00:00-05:00"},
                    "validation": {"start": "2019-01-02T00:00:00-05:00", "end": "2020-01-02T00:00:00-05:00"}}
    with pytest.raises(ResearchConfigError) as e:
        validate_config(stamp(c), profiles=load_profiles())
    rules = {r for r, _ in e.value.issues}
    msgs = " ".join(m for _, m in e.value.issues)
    assert {"RC06", "RC09", "RC12"} <= rules
    assert "UNKNOWN is never upgraded" in msgs and "not GO" in msgs


# ------------------------------------------------------------------ cost scenarios
def _spec():
    return json.loads((ROOT / "specs" / "research" / "sma_crossover_tsm_15m_5_20.json").read_text())


def test_ibkr_scenarios_keep_draft_and_are_not_executable():
    meta, sc = CS.load_set(IBKR_SET, profiles=load_profiles(), spec=_spec(), core_sha="2c6ba9a")
    for s in sc:
        r = s.record
        assert r["approval_status"] == "DRAFT" and r["assumption_status"] == "ASSUMED" and r["currency"] == "USD"
        assert r["core_compatibility"]["verdict"] == "INCOMPATIBLE" and r["counts_as_new_family_or_trial"] is False
        assert "per_unit 0.005" in r["commission_model"] and r["slippage_model"] == "2 bps"
        with pytest.raises(Exception, match="K18"):
            CS.require_executable(s)
    assert {s.kind for s in sc} == {"BASE", "LOW", "STRESS"} and meta["sha256"]


def test_scenario_rules_and_custom_profile():
    base = json.loads(SYN_SET.read_text())
    prof = synthetic_cost_profile()
    profiles = {(prof["profile_id"], prof["version"]): prof}
    meta, sc = CS.load_set(SYN_SET, profiles=profiles, spec=_spec())
    assert all(s.executable for s in sc) and meta["scenario_set_id"] == "synthetic_equity_test_v1"
    custom = copy.deepcopy(prof)
    custom["version"] = "1.0.1"
    custom["components"][0]["rate"] = 0.001
    custom["provenance"]["profile_sha256"] = None
    custom["provenance"]["profile_sha256"] = profile_hash(custom)
    profiles[(custom["profile_id"], custom["version"])] = custom
    s = copy.deepcopy(base)
    s["scenarios"].append({"scenario_id": "CUSTOM_10BPS", "kind": "CUSTOM", "cost_multiplier": 1.0,
                           "commission_model": "ALL_IN",
                           "profile_ref": {"profile_id": custom["profile_id"], "version": "1.0.1",
                                           "sha256": profile_hash(custom)}})
    _, sc = CS.load_set(s, profiles=profiles, spec=_spec())
    assert sc[-1].record["kind"] == "CUSTOM" and sc[-1].record["sha256"] == profile_hash(custom)

    def bad(mut, rule):
        x = copy.deepcopy(base)
        mut(x)
        with pytest.raises(CS.CostScenarioError) as e:
            CS.load_set(x, profiles=profiles, spec=_spec())
        assert e.value.rule == rule
    bad(lambda x: x["scenarios"][0].update(cost_multiplier=1.5), "CS01")
    bad(lambda x: x["scenarios"][1]["profile_ref"].update(sha256="0" * 64), "CS02")
    bad(lambda x: x["scenarios"][2].update(cost_multiplier=3.0), "CS03")         # not pre-registered
    bad(lambda x: x["scenarios"][1].update(cost_multiplier=2.0), "CS04")         # LOW above 1
    bad(lambda x: x["scenarios"].append(dict(x["scenarios"][0], scenario_id="B2")), "CS01")
    fee = copy.deepcopy(prof)
    fee["version"] = "1.0.2"
    fee["components"].append({"id": "exch", "kind": "EXCHANGE_FEE", "side": "BOTH", "rate": 0.0001,
                              "basis": "RESEARCH_ASSUMPTION", "source": {"kind": "MODEL", "citation": "test fee"}})
    fee["provenance"]["profile_sha256"] = None
    fee["provenance"]["profile_sha256"] = profile_hash(fee)
    profiles[(fee["profile_id"], "1.0.2")] = fee
    bad(lambda x: x["scenarios"].append({"scenario_id": "C2", "kind": "CUSTOM", "cost_multiplier": 1.0,
                                         "commission_model": "ALL_IN", "profile_ref": {
                                             "profile_id": fee["profile_id"], "version": "1.0.2",
                                             "sha256": profile_hash(fee)}}), "CS05")      # all-in + fee = double count


# ------------------------------------------------------------------ Core contract (agent side)
def _rep(**prov):
    base = {"engine_git_sha": "e" * 40, "agent_cost_profile_sha256": "a" * 64, "cost_profile_sha256": "c" * 64,
            "dataset_snapshot_id": "snap", "dataset_producer_sha": "prod", "spec_sha256": "s" * 64,
            "core_status": "RESEARCH_ONLY"}
    return SimpleNamespace(provenance={**base, **prov})


def _job(**kw):
    spec = {"data_requirements": {"datasets": [{"snapshot_id": "snap", "producer_commit_sha": "prod"}]},
            "costs": {"profile_ref": {"sha256": "a" * 64}, "sensitivity_multipliers": [0, 1, 2, 4]}}
    return SimpleNamespace(spec=spec, scenario=None, config=kw.get("config"), cost_multiplier=kw.get("m", 1.0))


def test_core_contract_checks():
    r = CC.check(_rep(), job=_job(), engine_git_sha="e" * 40, real_market=False)
    assert not r.failed and r.checks["CC05_temporal_policy"] == "PENDING"
    for prov, cid in (({"engine_git_sha": "f" * 40}, "CC01_engine_git_sha"),
                      ({"agent_cost_profile_sha256": "b" * 64}, "CC02_agent_cost_profile_sha256"),
                      ({"cost_profile_sha256": None}, "CC02_core_cost_profile_sha256"),
                      ({"dataset_snapshot_id": "other"}, "CC03_dataset_snapshot"),
                      ({"dataset_producer_sha": "x"}, "CC03_dataset_producer"),
                      ({"core_status": "WEIRD"}, "CC06_core_status")):
        with pytest.raises(CC.CoreContractMismatch, match=cid):
            CC.enforce(_rep(**prov), job=_job(), engine_git_sha="e" * 40, real_market=False)
    with pytest.raises(CC.CoreContractMismatch, match="CC07"):
        CC.enforce(_rep(), job=_job(m=3.0), engine_git_sha="e" * 40, real_market=False)
    real = CC.check(_rep(), job=_job(config={"temporal_policy": {"policy_id": "BAR_END_STRICT_LATER"}}),
                    engine_git_sha="e" * 40, real_market=True)
    assert {"CC05_temporal_policy", "CC06_core_status"} <= set(real.failed)        # pending Core field blocks REAL
    ok = CC.check(_rep(temporal_policy_id="BAR_END_STRICT_LATER", core_status="FORMAL_ELIGIBLE"),
                  job=_job(config={"temporal_policy": {"policy_id": "BAR_END_STRICT_LATER"}}),
                  engine_git_sha="e" * 40, real_market=True)
    assert not ok.failed


# ------------------------------------------------------------------ Drive archive failure record
def test_archive_write_failure_leaves_explicit_record(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    from strategy_agent.research.archive import Archiver
    from test_m3b_unit import _fake_result       # tests/ is on sys.path (rootdir-relative, like m2_helpers)
    a = Archiver(tmp_path / "arch", local_fallback=tmp_path / "local",
                 clock=lambda: datetime(2026, 11, 5, 3, 0, tzinfo=timezone.utc))
    real = Path.write_text
    calls = {"n": 0}

    def flaky(self, *args, **kw):
        if self.parent.name.startswith("2026") and self.name not in ("ARCHIVE_FAILED.json",):
            calls["n"] += 1
            if calls["n"] == 3:
                raise OSError("Transport endpoint is not connected")          # Drive FUSE drop
        return real(self, *args, **kw)
    monkeypatch.setattr(Path, "write_text", flaky)
    r = _fake_result(tmp_path)
    out = a.write(r)
    monkeypatch.setattr(Path, "write_text", real)
    assert out["status"] == "FAILED" and "Transport endpoint" in out["reason"]
    d = Path(out["path"])
    assert (d / "ARCHIVE_FAILED.json").is_file() and not (d / "manifest.json").exists()
    rec = json.loads((tmp_path / "local" / "_archive_failures" / f"{d.name}.json").read_text())
    assert rec["status"] == "FAILED" and len(rec["files_written_before_failure"]) == 2


# ------------------------------------------------------------------ end to end on the real Core
needs_core = pytest.mark.skipif(
    __import__("importlib").util.find_spec("market_research_core") is None, reason="market-research-core not installed")
AGENT = {"agent_sha": "abcdef1234567890abcdef1234567890abcdef12", "agent_tree_dirty": False, "agent_branch": "t"}


def _core_ok():
    from strategy_agent.adapters import mrc_adapter as A
    return A.engine_info().benchmark_status == "BENCHMARK_PASS"


@needs_core
def test_config_run_end_to_end_resume_and_one_trial(tmp_path):
    if not _core_ok():
        pytest.skip("no BENCHMARK_PASS Core")
    from strategy_agent.research.entry import run_config
    before = _digest()
    logs = []
    s = run_config(CFG_LOCAL, run_root=tmp_path / "env", archive_root=tmp_path / "arch", agent=AGENT,
                   progress=logs.append, batch_id="b1")
    runs = s["runs"]
    assert len(runs) == 8 and all(o["status"] == "COMPLETED" and o["archive_status"] == "ARCHIVED" for o in runs)
    rows = s["cost_sensitivity"]["rows"]
    for role in ("TRAIN", "VALIDATION"):
        pnl = {r["scenario_id"]: r["net_pnl"] for r in rows if r["role"] == role}
        assert pnl["LOW_FRICTIONLESS"] > pnl["BASE"] > pnl["STRESS_2X"] > pnl["STRESS_4X"]   # Core applied costs
    reg = [json.loads(x) for x in (tmp_path / "env" / "experiments.jsonl").read_text().splitlines()]
    fam = {r["trial"]["family_id"] for r in reg}
    assert len(fam) == 1
    from strategy_agent.registry.experiments import ExperimentRegistry
    assert ExperimentRegistry(tmp_path / "env" / "experiments.jsonl").trial_count(fam.pop()) == 1
    d = Path(runs[0]["archive_path"])
    rc = json.loads((d / "research_config.json").read_text())
    assert rc["sha256"] == _cfg(CFG_LOCAL)["provenance"]["config_sha256"]
    assert json.loads((d / "cost_scenario.json").read_text())["scenario_id"] in {"BASE", "LOW_FRICTIONLESS", "STRESS_2X", "STRESS_4X"}
    st = {x["name"]: x for x in json.loads((d / "stages.json").read_text())}
    assert st["core_execution"]["outputs"]["contract_checks"]["CC01_engine_git_sha"] == "PASS"
    assert (tmp_path / "arch" / "_batches" / "cost_sensitivity_b1.md").is_file()
    # M3-C: single-run reports point to the batch-level 2x check instead of claiming "not yet run"
    md = (tmp_path / "arch" / "_batches" / "cost_sensitivity_b1.md").read_text()
    assert "## Batch-level 2x-cost check" in md and "TRAIN: 1x net" in md and "VALIDATION: 1x net" in md
    for o in runs:
        rep = (Path(o["archive_path"]) / "research_report.md").read_text()
        assert "not yet run" not in rep and "### Strategy Verdict" in rep
    assert s["batch_two_x_check"]["TRAIN"]["evaluable"] is True
    # resume: completed identities are skipped, nothing re-submitted
    s2 = run_config(CFG_LOCAL, run_root=tmp_path / "env", archive_root=tmp_path / "arch", agent=AGENT,
                    progress=logs.append, batch_id="b1", resume=True)
    assert [o["status"] for o in s2["runs"]] == ["SKIPPED_RESUME"] * 8
    assert len((tmp_path / "env" / "experiments.jsonl").read_text().splitlines()) == len(reg)
    assert _digest() == before


@needs_core
def test_r02_through_config_entry_new_version_records_rejected_spec(tmp_path):
    if not _core_ok():
        pytest.skip("no BENCHMARK_PASS Core")
    from strategy_agent.research.entry import ConfigLedgerConflict, run_config
    c = _cfg(CFG_LOCAL)
    c["parameters"] = {"fast": 6, "slow": 20}
    # (a) same id+version, re-hashed after a parameter change -> refused by the config ledger once v1.0.0 ran
    (tmp_path / "env").mkdir()                       # v1.0.0 already recorded in this environment's config ledger
    (tmp_path / "env" / "research_configs.jsonl").write_text(json.dumps(
        {"config_id": c["config_id"], "version": c["version"], "sha256": c["provenance"]["config_sha256"]}) + "\n")
    p2 = tmp_path / "edited.json"
    p2.write_text(json.dumps(stamp(c)))
    with pytest.raises(ConfigLedgerConflict, match="needs a new version"):
        run_config(p2, run_root=tmp_path / "env", archive_root=tmp_path / "a", agent=AGENT, progress=lambda m: None)
    assert list((tmp_path / "env" / "_refused_configs").glob("refused_*.json"))
    # (b) new version with invalid parameters: submitted, recorded REJECTED_SPEC R02, nothing executed
    c["version"] = "1.1.0"
    p3 = tmp_path / "v110.json"
    p3.write_text(json.dumps(stamp(c)))
    s = run_config(p3, run_root=tmp_path / "env", archive_root=tmp_path / "a", agent=AGENT, progress=lambda m: None)
    assert [o["status"] for o in s["runs"]] == ["REJECTED_SPEC"] * 8
    reg = [json.loads(x) for x in (tmp_path / "env" / "experiments.jsonl").read_text().splitlines()]
    assert {r["rejection_reasons"][0]["code"] for r in reg if r["status"] == "REJECTED_SPEC"} == {"R02"}
    assert not any(r["status"] in ("RUNNING", "COMPLETED") for r in reg)
    assert any(r == "RC04" for r, _ in s["config_issues"])


@needs_core
def test_resume_cannot_smuggle_parameters(tmp_path):
    """Resume re-enters ResearchRunner.run: a job altered between attempts is still R02-checked."""
    if not _core_ok():
        pytest.skip("no BENCHMARK_PASS Core")
    from strategy_agent.research.batch import run_batch
    from strategy_agent.research.entry import build_synthetic
    runner, cfg, jobs = build_synthetic(CFG_LOCAL, run_root=tmp_path / "env", archive_root=tmp_path / "a",
                                        agent=AGENT, progress=lambda m: None)
    tampered = [replace(jobs[0], parameters={"fast": 7, "slow": 20})]
    out = run_batch(runner, tampered, batch_id="r", ledger_dir=tmp_path / "l", resume=True,
                    identity_of=runner.identity, progress=lambda m: None)
    assert out[0].status == "REJECTED_SPEC" and "R02" in out[0].error


@needs_core
def test_drive_config_without_drive_is_pending_not_pass(tmp_path):
    if not _core_ok():
        pytest.skip("no BENCHMARK_PASS Core")
    from strategy_agent.research.archive import DRIVE_MOUNT
    if DRIVE_MOUNT.is_dir():
        pytest.skip("Drive mounted here")
    from strategy_agent.research.entry import build_synthetic
    runner, cfg, jobs = build_synthetic(CFG, run_root=tmp_path / "env", archive_root=None, agent=AGENT,
                                        progress=lambda m: None)
    r = runner.run(jobs[0])
    assert r.status == "COMPLETED" and r.archive["status"] == "DRIVE_UNAVAILABLE"
    assert {s["name"]: s["status"] for s in r.stages}["archive"] == "FAILED"
    assert "NOT a Drive backup" in (Path(r.archive["path"]) / "run_info.txt").read_text()


# ------------------------------------------------------------------ M3-C: run-level vs batch-level cost sensitivity
def test_batch_two_x_check_and_markdown_separate_levels():
    from strategy_agent.research.entry import batch_two_x, sensitivity_markdown

    def row(role, sid, m, net, st="COMPLETED"):
        return {"role": role, "scenario_id": sid, "kind": "X", "cost_multiplier": m, "status": st, "trade_count": 20,
                "net_pnl": net, "profit_factor": 1.1, "expectancy": 1.0, "total_return": 0.01}
    rows = [row("TRAIN", "BASE", 1.0, 767.303), row("TRAIN", "STRESS_2X", 2.0, 374.606),
            row("VALIDATION", "BASE", 1.0, 10.0), row("VALIDATION", "STRESS_2X", 2.0, -5.0),
            row("OTHER", "BASE", 1.0, 3.0)]
    chk = batch_two_x(rows)
    assert chk["TRAIN"]["survives_2x"] is True and chk["VALIDATION"]["survives_2x"] is False
    assert chk["OTHER"]["evaluable"] is False                                 # no 2x run -> never claimed
    md = sensitivity_markdown({"config": {"config_id": "c", "version": "1", "sha256": "s", "kind": "SYNTHETIC",
                                          "scenario_set": {"scenario_set_id": "x", "version": "1", "sha256": "y"}},
                               "cost_sensitivity": {"rows": rows}})
    assert "## Batch-level 2x-cost check" in md and "TRAIN: 1x net 767.3030, 2x net 374.6060 -> survives 2x: **YES**" in md
    assert "VALIDATION" in md and "**NO**" in md and "OTHER: NOT_EVALUABLE" in md
    assert "767.3030" in md                                                   # numbers copied, not recomputed


def test_run_level_two_x_note_never_claims_or_ignores():
    from strategy_agent.research.report import two_x_note
    sm = [{"scenario_id": "BASE", "kind": "BASE", "cost_multiplier": 1.0},
          {"scenario_id": "STRESS_2X", "kind": "STRESS", "cost_multiplier": 2.0}]

    def res(m, mults):
        return SimpleNamespace(job=SimpleNamespace(cost_multiplier=m),
                               context={"research_config": {"scenario_multipliers": mults} if mults is not None else None})
    n = two_x_note(res(1.0, sm))
    assert "not evaluated in this single-run report" in n and "STRESS_2X" in n and "batch-level" in n
    assert "IS the 2x-cost scenario" in two_x_note(res(2.0, sm))
    assert "single cost multiplier (no 2x scenario)" in two_x_note(res(1.0, None))
    assert "single cost multiplier" in two_x_note(res(1.0, sm[:1]))
    for x in (n, two_x_note(res(1.0, None))):
        assert "not yet run" not in x
