"""M1 Core compatibility tests: executed against the REAL installed market-research-core.

Skipped when Core is not installed (the default CI job). The `core-compat` CI job installs Core at the pinned
commit and runs them. Nothing here is a fake engine: every Core result below comes from Core's own code."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

core = pytest.importorskip("market_research_core")

from benchmark.harness import mrc_crosscheck as X  # noqa: E402
from benchmark.harness.crosscheck import cases  # noqa: E402
from strategy_agent.adapters import mrc_adapter as A  # noqa: E402

from m2_helpers import approved_profile, dev_request, env, registered  # noqa: E402

PINNED = "f4276cb081ae4407d356007dc76168a8cbdf2f82"
ROOT = Path(__file__).parents[1]


def test_installed_core_has_a_resolvable_commit():
    ident = A.core_identity()
    assert ident["installed"] and ident["git_sha"] and len(ident["git_sha"]) == 40


def test_matrix_is_measured_on_the_real_core():
    m = A.compatibility_matrix(core)
    assert [r["cap_id"] for r in m] == [c.cap_id for c in A.CAPABILITIES]
    assert {r["status"] for r in m} <= {"SUPPORTED", "PARTIAL", "MISSING"}
    k01 = next(r for r in m if r["cap_id"] == "K01")
    has_generic = any(n.lower() in {"executionengine", "execute", "executionrequest"} for n in dir(core))
    assert (k01["status"] == "SUPPORTED") == has_generic


def test_real_core_probe_runs():
    p = A._probe_run_sma(core)
    assert p["trades"] >= 1 and "engine_version" in p["provenance"]


def test_b02_on_real_core_matches_oracle_timing():
    case = next(c for c in cases() if c["case_id"].startswith("B02"))
    got, notes = X.run_sma_case(case, core)
    exp = case["expected"]
    assert [(f["exec_ts"], f["side"], f["qty"]) for f in got["fills"]] == \
        [(f["exec_ts"], f["side"], f["qty"]) for f in exp["fills"]]
    assert not {f["exec_ts"] for f in got["fills"]} & {f["exec_ts"] for f in exp["forbidden_fills"]}
    for g, e in zip(got["fills"], exp["fills"]):                 # only the bps-vs-tick approximation differs
        assert abs(Decimal(g["fill_price"]) - Decimal(e["fill_price"])) / Decimal(e["fill_price"]) < Decimal("1e-4")
    assert notes["api"] == "BacktestEngine.run_sma"


@pytest.mark.skipif(A.core_identity().get("git_sha") != PINNED, reason="committed record is for the pinned commit")
def test_crosscheck_reproduces_committed_record(tmp_path):
    rec = X.crosscheck(tmp_path)
    committed = json.loads((ROOT / "benchmark" / "results" / f"mrc_{PINNED[:12]}" / "crosscheck.json").read_text())
    assert rec["verdict"] == committed["verdict"] == "INTEGRATION_PENDING"
    assert rec["counts"] == committed["counts"]
    strip = lambda r: {k: v for k, v in r.items() if k != "notes"}  # noqa: E731
    assert [strip(r) for r in rec["cases"]] == [strip(r) for r in committed["cases"]]
    assert rec["engine"]["kind"] == "REAL_CORE" and rec["engine"]["git_sha"] == PINNED


def test_runner_with_real_core_records_integration_pending(tmp_path):
    p = approved_profile()
    port = A.MrcBacktestPort({(p["profile_id"], p["version"]): p}, decisions_provider=lambda r: [])
    runner, reg = env(tmp_path, port=port, benchmark="BENCHMARK_PASS")
    rec = runner.submit(dev_request(registered()))
    if port.gaps():
        assert rec["status"] == "FAILED" and "INTEGRATION_PENDING" in rec["rejection_reasons"][0]["message"]
    runner.engine = A.engine_info()
    rec2 = runner.submit(dev_request(registered(), params={"fast": 5, "slow": 50}))
    if A.engine_benchmark_status(A.core_identity()["git_sha"])["status"] != "BENCHMARK_PASS":
        assert rec2["status"] == "REJECTED_CONTRACT"
