"""M1 adapter: contract, fail-closed and runner/registry integration tests.

Tests that need the real market-research-core are in test_m1_core_compat.py (skipped when Core is absent).
Here Core is either absent or represented by a measured capability matrix; nothing simulates fills."""
from __future__ import annotations

import ast
import json
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from strategy_agent.adapters import mrc_adapter as A
from strategy_agent.ports import (CONVENTION, REQUIRED_PROVENANCE, BacktestRequest, ContractViolation, DatasetRef,
                                  check_report)
from strategy_agent.signals.core import Decision
from strategy_agent.spec.validate import canonical_hash

from m2_helpers import approved_profile, dev_request, env, holdout_request, registered

TZ = timezone(timedelta(hours=8))
ROOT = Path(__file__).parents[1]
SHA = "a50f3902c3a014922601d8cca4e7bdf761d377b8"


def ds(**kw):
    base = dict(dataset_id="tw_equity_fubon_5m", snapshot_id="final_gate_20261031_154458_20eba73",
                producer_commit_sha="20eba73", verdict="SCOPED_GO", usage_scope="BACKTEST",
                mandatory_exclusions=("8101 quarantine",))
    return DatasetRef(**(base | kw))


def request(spec=None, datasets=None):
    spec = spec or registered()
    return BacktestRequest(spec=spec, spec_sha256=canonical_hash(spec), datasets=datasets or (ds(),),
                           window_start="2025-01-01T00:00:00+08:00", window_end="2025-04-01T00:00:00+08:00",
                           warmup_start="2024-12-01T00:00:00+08:00", parameters={"fast": 10, "slow": 50})


def profiles():
    p = approved_profile()
    return {(p["profile_id"], p["version"]): p}


def core_result(req, status="RESEARCH_ONLY", **over):
    """ExecutionResult-shaped value with the provenance keys Core a50f390 emits (unit test of the converter only;
    real Core results are exercised in test_m1_core_compat.py)."""
    d0 = req.datasets[0]
    prov = {"engine_name": "market-research-core.ExecutionEngine", "engine_git_sha": SHA, "engine_version": "2.1.0",
            "execution_convention": CONVENTION, "dataset_snapshot_id": d0.snapshot_id,
            "dataset_producer_sha": d0.producer_commit_sha, "dataset_verdict": d0.verdict,
            "exclusions_applied": [], "spec_sha256": req.spec_sha256, "parameters": dict(req.parameters),
            "cost_multiplier": 1.0, "seed": 0, "window_start": req.window_start, "window_end": req.window_end,
            "cost_profile_id": "tw_equity_cash", "cost_profile_sha256": "a" * 64, "assumptions": ["FIFO lot matching"]}
    prov.update(over.pop("prov", {}))
    r = dict(fills=[{"exec_ts": "2025-01-02T09:05:00+08:00", "side": "BUY", "qty": 1000, "fill_price": 600.5}],
             round_trips=[{"net_pnl": 12.5}], rejections=[{"order_id": "0001", "reason": "ZERO_VOLUME_BAR"}],
             equity_curve=[], open_positions=[], invalidated_round_trips=[], provenance=prov, status=status)
    r.update(over)
    return SimpleNamespace(**r)


# ------------------------------------------------------------------------------------------ isolation
def _imports(path: Path) -> set[str]:
    out = set()
    for n in ast.walk(ast.parse(path.read_text())):
        if isinstance(n, ast.Import):
            out |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            out.add(n.module)
    return out


def test_adapter_is_the_only_core_importer_and_never_imports_the_oracle():
    adapter = ROOT / "src" / "strategy_agent" / "adapters" / "mrc_adapter.py"
    mods = _imports(adapter)
    assert "market_research_core" in mods and not any(m.startswith("benchmark") for m in mods)
    for p in (ROOT / "src").rglob("*.py"):
        if p != adapter:
            assert not any(m.startswith("market_research_core") for m in _imports(p)), p


# ------------------------------------------------------------------------------------------ reasons
def test_reason_mapping_is_reviewed_and_fail_closed():
    assert A.map_reason("ZERO_VOLUME_BAR") == "ZERO_VOLUME_BAR"
    assert A.map_reason("PIT_VIOLATION") == "PIT_VIOLATION"                 # Core-only code, passed through
    assert A.map_reason("CLOSING_AUCTION_NOT_EXECUTABLE") == "CLOSING_AUCTION_NOT_EXECUTABLE"
    for vague in ("EXECUTION_INELIGIBLE", "NO_LATER_ELIGIBLE_BAR"):
        with pytest.raises(ContractViolation, match="not specific enough"):
            A.map_reason(vague)
    with pytest.raises(ContractViolation, match="unmapped"):
        A.map_reason("SOMETHING_NEW")


# ------------------------------------------------------------------------------------------ signals -> targets
def test_decisions_to_targets_passes_time_through_and_collapses_repeats():
    t0 = datetime(2026, 1, 5, 9, 15, tzinfo=TZ)
    d = [Decision(t0, 1, "2330", "golden_cross"), Decision(t0 + timedelta(minutes=5), 1, "2330", "hold"),
         Decision(t0 + timedelta(minutes=30), 0, "2330", "death_cross")]
    out = A.decisions_to_targets(list(reversed(d)))
    assert [o["target"] for o in out] == [1, 0]
    assert out[0]["signal_ts"] == t0.isoformat()                      # not shifted
    with pytest.raises(ContractViolation):
        A.decisions_to_targets([Decision(datetime(2026, 1, 5, 9, 15), 1, "2330", "x")])
    with pytest.raises(ContractViolation):
        A.decisions_to_targets([Decision(t0, 2, "2330", "x")])


# ------------------------------------------------------------------------------------------ validation
@pytest.mark.parametrize("bad, msg", [
    (dict(snapshot_id="LATEST"), "must be pinned"), (dict(producer_commit_sha="UNKNOWN"), "must be pinned"),
    (dict(verdict="NO_GO"), "not executable"), (dict(usage_scope="RESEARCH_ONLY"), "not BACKTEST")])
def test_dataset_pins_fail_closed(bad, msg):
    with pytest.raises(ContractViolation, match=msg):
        A.validate_dataset_refs([ds(**bad)])
    with pytest.raises(ContractViolation, match="no dataset"):
        A.validate_dataset_refs([])


def test_cost_profile_validation():
    spec, prof = registered(), approved_profile()
    A.validate_cost_profile(spec, prof)
    draft = json.loads((ROOT / "governance" / "cost_profiles" / "tw_equity_cash_v0.json").read_text())
    with pytest.raises(ContractViolation, match="not APPROVED"):
        A.validate_cost_profile(spec, draft)
    with pytest.raises(ContractViolation, match="not provided"):
        A.validate_cost_profile(spec, None)
    other = registered(mutate=lambda s: s["costs"]["fee"].update(rate_side=0.0005))
    other["costs"]["profile_ref"]["sha256"] = prof["provenance"]["profile_sha256"]
    with pytest.raises(ContractViolation, match="differ from CostProfile"):
        A.validate_cost_profile(other, prof)
    stale = registered()
    stale["costs"]["profile_ref"]["sha256"] = "0" * 64
    with pytest.raises(ContractViolation, match="sha256"):
        A.validate_cost_profile(stale, prof)


def test_expand_roll_schedule_uses_intended_target_only():
    t = [{"signal_ts": "2026-01-06T09:05:00+08:00", "target": 1, "contract": "F01", "reason": "S"},
         {"signal_ts": "2026-01-07T09:10:00+08:00", "target": 0, "contract": "F02", "reason": "S"}]
    roll = [{"at": "2026-01-07T09:00:00+08:00", "from_contract": "F01", "to_contract": "F02"}]
    out = A.expand_roll_schedule(t, roll)
    assert [(x["contract"], x["target"], x["reason"]) for x in out] == [
        ("F01", 1, "S"), ("F01", 0, "ROLL_CLOSE"), ("F02", 1, "ROLL_OPEN"), ("F02", 0, "S")]
    flat = A.expand_roll_schedule([dict(t[0], target=0)], roll)          # nothing held -> no roll orders
    assert [x["reason"] for x in flat] == ["S"]


def test_core_bar_columns_are_checked_not_derived():
    A.check_core_bars(SimpleNamespace(columns=list(A.CORE_BAR_COLUMNS)))
    with pytest.raises(ContractViolation, match="buy_execution_eligible"):
        A.check_core_bars(SimpleNamespace(columns=[c for c in A.CORE_BAR_COLUMNS if c != "buy_execution_eligible"]))


# ------------------------------------------------------------------------------------------ result -> report
def test_core_result_to_report_transfers_provenance_and_maps_reasons():
    req = request()
    rep = A.core_result_to_report(core_result(req), req, SHA)
    check_report(rep, req)                                            # existing C4 contract
    assert all(rep.provenance[k] not in (None, "") for k in REQUIRED_PROVENANCE)
    assert rep.provenance["engine_git_sha"] == SHA and rep.provenance["dataset_snapshot_id"] == req.datasets[0].snapshot_id
    assert rep.rejections[0]["reason"] == "ZERO_VOLUME_BAR" and rep.round_trips == [{"net_pnl": 12.5}]
    assert rep.open_position is None


@pytest.mark.parametrize("over, msg", [
    (dict(prov={"engine_git_sha": "0.2.0+source"}), "not a full commit"),
    (dict(prov={"engine_git_sha": "deadbeef" * 5}), "pinned"),
    (dict(prov={"dataset_snapshot_id": "other_snapshot"}), "request pinned"),
    (dict(prov={"spec_sha256": "b" * 64}), "different spec"),
    (dict(prov={"execution_convention": "v0"}), "convention"),
    (dict(rejections=[{"order_id": "1", "reason": "EXECUTION_INELIGIBLE"}]), "not specific enough")])
def test_core_result_fail_closed(over, msg):
    req = request()
    with pytest.raises(ContractViolation, match=msg):
        A.core_result_to_report(core_result(req, **over), req, SHA)


def test_core_status_is_never_promoted():
    req = request()
    assert A.core_result_to_report(core_result(req), req, SHA).status == "RESEARCH_INTEGRATION_DEMO"
    rep = A.core_result_to_report(core_result(req, status="FORMAL_ELIGIBLE"), req, SHA)
    assert rep.status == "RESEARCH_BACKTEST" and rep.provenance["core_status"] == "FORMAL_ELIGIBLE"
    check_report(rep, req)
    bad = A.core_result_to_report(core_result(req), req, SHA)
    with pytest.raises(ContractViolation, match="live/tradable"):
        check_report(type(bad)(**{**bad.__dict__, "status": "TRADABLE"}), req)


# ------------------------------------------------------------------------------------------ port + engine status
GAP_MATRIX = [dict(cap_id=c.cap_id, status="MISSING") for c in A.CAPABILITIES]
FULL_MATRIX = [dict(cap_id=c.cap_id, status="SUPPORTED") for c in A.CAPABILITIES]


def test_port_raises_contract_gap_instead_of_simulating(monkeypatch):
    monkeypatch.setattr(A, "core_identity", lambda: {"installed": True, "git_sha": SHA, "version": "0.2.0", "source": "x"})
    port = A.MrcBacktestPort(profiles(), decisions_provider=lambda r: [], bars_provider=lambda r: None, matrix=GAP_MATRIX)
    with pytest.raises(A.CoreContractGap, match="INTEGRATION_PENDING") as e:
        port.run(request())
    assert set(e.value.gaps) == set(A.MrcBacktestPort.REQUIRED_FOR_FORMAL_RUN)


def test_port_without_providers_fails_closed(monkeypatch):
    monkeypatch.setattr(A, "core_identity", lambda: {"installed": True, "git_sha": SHA, "version": "0.2.0", "source": "x"})
    with pytest.raises(ContractViolation, match="providers? .*required|bars_provider"):
        A.MrcBacktestPort(profiles(), matrix=FULL_MATRIX).run(request())


def test_port_validates_before_reporting_gaps(monkeypatch):
    monkeypatch.setattr(A, "core_identity", lambda: {"installed": True, "git_sha": SHA, "version": "0.2.0", "source": "x"})
    port = A.MrcBacktestPort({}, decisions_provider=lambda r: [], bars_provider=lambda r: None, matrix=GAP_MATRIX)
    with pytest.raises(ContractViolation, match="not provided"):
        port.run(request())


def test_port_fails_closed_without_core_commit(monkeypatch):
    monkeypatch.setattr(A, "core_identity", lambda: {"installed": False, "git_sha": None, "version": None, "source": None})
    port = A.MrcBacktestPort(profiles(), decisions_provider=lambda r: [], bars_provider=lambda r: None, matrix=FULL_MATRIX)
    with pytest.raises(A.CoreContractGap, match="K11"):
        port.run(request())


def test_matrix_without_core_is_all_missing(monkeypatch):
    def boom():
        raise ImportError("not installed")
    monkeypatch.setattr(A, "_core", boom)
    m = A.compatibility_matrix()
    assert len(m) == len(A.CAPABILITIES) and all(r["status"] == "MISSING" for r in m)


def _record(tmp, sha, kind="REAL_CORE", statuses=None, g16="PASS", probes=("PASS", "PASS")):
    d = tmp / f"mrc_{sha[:12]}"
    d.mkdir(parents=True)
    st = statuses or {f"B{i:02d}": "PASS" for i in range(1, 13)}
    pr = {} if g16 is None else {"G16_acceptance": {"status": g16, "checks": {"x": g16 == "PASS"}}}
    if probes is not None:
        pr["oracle_probes"] = {"G17": {"status": probes[0]}, "G14b": {"status": probes[1]}}
    (d / "crosscheck.json").write_text(json.dumps({"engine": {"git_sha": sha, "kind": kind}, "verdict": "x",
                                                   "cases": [{"case_id": f"{k}_x", "status": v} for k, v in st.items()],
                                                   "probes": pr}))


def test_benchmark_status_requires_real_core_all_pass_same_sha(tmp_path):
    assert A.engine_benchmark_status(None, tmp_path)["status"] == "NOT_VERIFIED"
    assert A.engine_benchmark_status(SHA, tmp_path)["status"] == "NOT_VERIFIED"
    _record(tmp_path / "a", SHA)
    assert A.engine_benchmark_status(SHA, tmp_path / "a")["status"] == "BENCHMARK_PASS"
    _record(tmp_path / "b", SHA, kind="FAKE_ENGINE")
    assert A.engine_benchmark_status(SHA, tmp_path / "b")["status"] == "NOT_VERIFIED"
    _record(tmp_path / "c", SHA, statuses={f"B{i:02d}": "PASS" for i in range(1, 12)})       # B12 missing
    assert A.engine_benchmark_status(SHA, tmp_path / "c")["status"] == "NOT_VERIFIED"
    _record(tmp_path / "d", SHA, statuses={f"B{i:02d}": ("FAIL" if i == 2 else "PASS") for i in range(1, 13)})
    assert A.engine_benchmark_status(SHA, tmp_path / "d")["status"] == "NOT_VERIFIED"
    _record(tmp_path / "f", SHA, g16="FAIL")                                                 # 12 PASS, G16 FAIL
    assert A.engine_benchmark_status(SHA, tmp_path / "f")["status"] == "NOT_VERIFIED"
    _record(tmp_path / "g", SHA, g16=None)                                                   # no G16 evidence
    assert A.engine_benchmark_status(SHA, tmp_path / "g")["status"] == "NOT_VERIFIED"
    _record(tmp_path / "h", SHA, probes=("FAIL", "PASS"))                                     # G17 FAIL
    assert A.engine_benchmark_status(SHA, tmp_path / "h")["status"] == "NOT_VERIFIED"
    _record(tmp_path / "i", SHA, probes=None)                                                 # no probe evidence
    assert A.engine_benchmark_status(SHA, tmp_path / "i")["status"] == "NOT_VERIFIED"
    other = "a" * 40
    _record(tmp_path / "e", other)
    (tmp_path / "e" / f"mrc_{SHA[:12]}").mkdir()
    (tmp_path / "e" / f"mrc_{SHA[:12]}" / "crosscheck.json").write_text(
        (tmp_path / "e" / f"mrc_{other[:12]}" / "crosscheck.json").read_text())                # copied record
    assert A.engine_benchmark_status(SHA, tmp_path / "e")["status"] == "NOT_VERIFIED"


def test_committed_record_for_previous_core_is_not_a_pass():
    st = A.engine_benchmark_status(SHA)                       # a50f390 (M1.1): 11 FAIL, no G16 evidence
    assert st["status"] == "NOT_VERIFIED" and "B01=FAIL" in st["why"] and "B02" not in st["why"]


def test_committed_record_for_m1_2_core_is_not_a_pass():
    st = A.engine_benchmark_status("298956f78522a9f2d13efbd38645178b1b477691")      # G17/G14b FAIL in its record
    assert st["status"] == "NOT_VERIFIED" and "G17=FAIL" in st["why"] and "G14b=FAIL" in st["why"]


def test_committed_record_for_final_core_is_a_pass_derived_from_evidence():
    st = A.engine_benchmark_status("2c6ba9ab7c336e7a4908744abef617e0dce713fd")
    assert st["status"] == "BENCHMARK_PASS" and "G17/G14b" in st["why"]


# ------------------------------------------------------------------------------------------ runner / registry
def _runner(tmp, port, status):
    runner, reg = env(tmp, port=port, benchmark=status)
    if status != "BENCHMARK_PASS":
        runner.engine = A.engine_info()           # real identity + status derived from the committed record
        assert runner.engine.git_sha == SHA and runner.engine.benchmark_status == status
    return runner, reg


def test_runner_records_rejected_contract_while_core_unverified(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "core_identity", lambda: {"installed": True, "git_sha": SHA, "version": "0.2.0", "source": "x"})
    port = A.MrcBacktestPort(profiles(), decisions_provider=lambda r: [], bars_provider=lambda r: None, matrix=GAP_MATRIX)
    status = A.engine_benchmark_status(SHA)["status"]                  # derived from the committed real-Core record
    runner, reg = _runner(tmp_path, port, status)
    rec = runner.submit(dev_request(registered()))
    assert rec["status"] == "REJECTED_CONTRACT" and rec["rejection_reasons"][0]["code"] == "C00"
    assert rec["code"]["engine_benchmark_status"] == "NOT_VERIFIED"
    assert reg.records()[-1]["status"] == "REJECTED_CONTRACT"
    assert reg.records()[-1]["code"]["engine_git_sha"] == SHA


def test_runner_records_failed_with_gap_list_and_full_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "core_identity", lambda: {"installed": True, "git_sha": SHA, "version": "0.2.0", "source": "x"})
    port = A.MrcBacktestPort(profiles(), decisions_provider=lambda r: [], bars_provider=lambda r: None, matrix=GAP_MATRIX)
    runner, reg = _runner(tmp_path, port, "BENCHMARK_PASS")           # even if a PASS were (wrongly) claimed
    rec = runner.submit(dev_request(registered()))
    assert rec["status"] == "FAILED" and "CoreContractGap" in rec["rejection_reasons"][0]["message"]
    assert "K01" in rec["rejection_reasons"][0]["message"]
    statuses = [r["status"] for r in reg.events(rec["experiment_id"])]
    assert statuses[-3:] == ["QUEUED", "RUNNING", "FAILED"]
    assert rec["data"][0]["snapshot_id"] and rec["spec"]["spec_sha256"] and rec["code"]["strategy_agent_sha"]


def test_governance_still_applies_with_real_adapter(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "core_identity", lambda: {"installed": True, "git_sha": SHA, "version": "0.2.0", "source": "x"})
    port = A.MrcBacktestPort(profiles(), decisions_provider=lambda r: [], bars_provider=lambda r: None, matrix=FULL_MATRIX)
    runner, reg = env(tmp_path, port=port, benchmark="BENCHMARK_PASS", lock=False)
    rec = runner.submit(holdout_request(registered()))
    assert rec["status"] == "REJECTED_GOVERNANCE"
    draft = registered(status="DRAFT")
    assert runner.submit(dev_request(draft))["status"] == "REJECTED_SPEC"
