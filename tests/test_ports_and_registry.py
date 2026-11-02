import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from strategy_agent.ports import (BacktestReport, BacktestRequest, ContractViolation, REQUIRED_PROVENANCE,
                                  check_bar_columns, check_report)

ROOT = Path(__file__).parents[1]


def _req():
    return BacktestRequest(spec={}, spec_sha256="a" * 64, datasets=(), window_start="2026-01-05 09:00",
                           window_end="2026-01-06 00:00", warmup_start="2026-01-01 00:00")


def _prov(**over):
    p = {k: "x" for k in REQUIRED_PROVENANCE}
    p.update(engine_git_sha="16281b2a242c", execution_convention="oracle-execution-convention-v1",
             spec_sha256="a" * 64)
    p.update(over)
    return p


def test_report_contract_ok():
    check_report(BacktestReport([{"exec_ts": "2026-01-05 09:15"}], [], [], [], None, _prov()), _req())


@pytest.mark.parametrize("over,status,fill", [
    ({"engine_git_sha": "0.2.0+source"}, "RESEARCH_BACKTEST", "2026-01-05 09:15"),   # package version, not SHA
    ({"execution_convention": "other"}, "RESEARCH_BACKTEST", "2026-01-05 09:15"),
    ({"spec_sha256": "b" * 64}, "RESEARCH_BACKTEST", "2026-01-05 09:15"),
    ({"seed": None}, "RESEARCH_BACKTEST", "2026-01-05 09:15"),
    ({}, "LIVE", "2026-01-05 09:15"),
    ({}, "RESEARCH_BACKTEST", "2026-01-04 09:15"),                                   # warm-up period fill
])
def test_report_contract_violations(over, status, fill):
    with pytest.raises(ContractViolation):
        check_report(BacktestReport([{"exec_ts": fill}], [], [], [], None, _prov(**over), status), _req())


def test_bar_columns_side_specific_eligibility_required():
    cols = ["timestamp", "available_at", "open", "high", "low", "close", "volume", "contract", "session",
            "data_exists", "research_eligible", "backtest_eligible", "execution_eligible"]
    with pytest.raises(ContractViolation):
        check_bar_columns(cols)


def test_experiment_record_schema_accepts_failed_record():
    schema = json.loads((ROOT / "schemas" / "EXPERIMENT_RECORD_SCHEMA.json").read_text())
    rec = {
        "experiment_id": "EXP-20261103-0123456789ab", "created_at": "2026-11-03T02:00:00+08:00",
        "status": "REJECTED_SPEC",
        "spec": {"strategy_id": "sma_crossover_tw_2330_5m", "version": "0.1.0", "spec_sha256": "c" * 64, "status": "DRAFT"},
        "trial": {"family_id": "FAM-TREND-TW-5M", "index": 0, "parameters": {"fast": 10, "slow": 50}},
        "data": [{"dataset_id": "tw_equity_fubon_5m", "snapshot_id": "final_gate_20261031_154458_20eba73",
                  "producer_commit_sha": "20eba73", "verdict": "SCOPED_GO"}],
        "code": {"strategy_agent_sha": "abcdef1", "engine_name": "market-research-core",
                 "engine_git_sha": "16281b2", "execution_convention": "oracle-execution-convention-v1"},
        "split": {"split_ref": "SPLIT-TW-5M-v1", "fold_id": "WF-01", "role": "WF_TEST",
                  "window_start": "2025-01-01", "window_end": "2025-04-01"},
        "seed": 20261103, "cost_multiplier": 1, "holdout_access": False,
        "rejection_reasons": [{"code": "S04", "message": "warm-up too short"}],
    }
    assert not list(Draft202012Validator(schema).iter_errors(rec))
