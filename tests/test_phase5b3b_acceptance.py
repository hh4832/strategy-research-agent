"""Phase 5-B3-B acceptance mechanics: gates come from tests that ran (junit), never from a pre-filled PASS file."""
import json

import pytest

from strategy_agent.execution import real_acceptance as RA
from strategy_agent.phase5a import acceptance as A5

CODE = {"git_sha": "b" * 40, "dirty": False, "python": "3.11.0"}


def _junit(tmp_path, names, fail=()):
    cases = "".join(f'<testcase classname="t" name="{n}">' + ("<failure/>" if n in fail else "") + "</testcase>"
                    for n in names)
    p = tmp_path / "j.xml"
    p.write_text(f"<testsuite>{cases}</testsuite>")
    return p


ALL = [f"test_B3B_0{i}_x" for i in range(1, 10)] + ["test_B3B_07_layer_b_x", "test_B3B_03_layer_b_x"]


def test_production_books_are_fail_closed_today():
    """Valid in both repository states: before the research lead's B3-C approval (nothing eligible, construction
    refused) and while the ONE governed B3-C authorization is active (eligible == exactly it). Never ran anything."""
    from strategy_agent.readiness.b3c import expected_eligible
    p = RA.production_state()
    assert p["status"] == "PASS" and p["probe_runner_calls"] == 0 and p["b3c_authorization_state"] != "UNEXPECTED"
    assert p["eligible_authorizations"] == p["expected_eligible"] == expected_eligible()
    if not p["eligible_authorizations"]:
        assert p["research_authorization"] == "NOT_GRANTED" and p["real_runner"] == "DISABLED"
    else:
        assert p["eligible_authorizations"] == ["RA-IBKR_TSM-SMA520_P5B3C-0001"]
    assert "production (live GitHub) verifier" in p["real_gated_orchestrator_on_production_books"] or \
        not p["eligible_authorizations"]                    # refused either way; never CONSTRUCTED here


def test_gates_need_tests_that_actually_ran(tmp_path):
    prod = RA.production_state()
    t = RA.junit([_junit(tmp_path, ALL)])
    g = RA.gates(t, prod, tests_rc=0, cloud=True, code=CODE)
    assert all(v["status"] == "PASS" for v in g.values()) and RA.verdict(g)["verdict"] == "SCOPED_GO"
    assert RA.verdict(RA.gates(t, prod, tests_rc=0, cloud=False, code=CODE))["verdict"] == "NOT_EVIDENCED"
    no_core = RA.junit([_junit(tmp_path, [n for n in ALL if "layer_b" not in n])])
    g = RA.gates(no_core, prod, tests_rc=0, cloud=True, code=CODE)
    assert g["B3B-07"]["status"] == "BLOCKED" and g["B3B-03"]["status"] == "BLOCKED"
    g = RA.gates(RA.junit([_junit(tmp_path, ALL, fail=("test_B3B_05_x",))]), prod, tests_rc=1, cloud=True, code=CODE)
    assert g["B3B-05"]["status"] == "FAIL" and g["B3B-10"]["status"] == "FAIL" and RA.verdict(g)["verdict"] == "NO_GO"
    g = RA.gates({}, prod, tests_rc=0, cloud=True, code=CODE)
    assert all(g[f"B3B-0{i}"]["status"] == "BLOCKED" for i in range(1, 10)) and g["B3B-10"]["status"] == "FAIL"
    g = RA.gates(t, {**prod, "status": "FAIL"}, tests_rc=0, cloud=True, code=CODE)
    assert g["B3B-01"]["status"] == "FAIL"


def test_archive_layers_are_separate_and_tamper_evident(tmp_path):
    before = A5.tree_fingerprint(RA.ROOT)
    out = tmp_path / "executions/20261109_000000_bbbbbbb"
    r = RA.run(out, junit_path=_junit(tmp_path, ALL), tests_rc=0, cloud=False, context={"note": "t"})
    assert A5.tree_fingerprint(RA.ROOT) == before
    assert r["layers"] == {"engineering_readiness": r["verdict"]["verdict"],
                           "research_authorization": RA.production_state()["research_authorization"],
                           "real_execution": "NOT_PERFORMED", "research_result_acceptance": "NOT_APPLICABLE"}
    assert sorted(p.name for p in out.iterdir()) == sorted(RA.OUTPUT_FILES)
    assert RA.verify_archive(out)["status"] == "PASS"
    info = (out / "run_info.txt").read_text()
    for k in ("real_execution=NOT_PERFORMED", "authorization_created=false", "new_backtest_executed=false",
              f"real_runner={RA.production_state()['real_runner']}"):
        assert k in info
    with pytest.raises(FileExistsError):
        RA.run(out, junit_path=None, tests_rc=0, cloud=False, context=None)
    (out / "b3b_junit.xml").write_text("<testsuite/>")
    assert RA.verify_archive(out)["status"] == "FAIL"
    from strategy_agent.readiness.b3c import expected_eligible
    assert json.loads((out / "governance_state.json").read_text())["eligible_authorizations"] == expected_eligible()
