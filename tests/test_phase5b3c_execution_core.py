"""Phase 5-B3-C execution path, Layer B: the b3c_run prepare -> execute flow end to end with the EXISTING
ResearchOnlyRunner + Core b16179a on a SYNTHETIC development snapshot, ISOLATED books and FixtureApprovals
(ISOLATED_TEST trust). A test authorization RA-IBKR_TSM-SMA520_B3CTEST-0001 is used; the repository's books and the
real B3-C authorization are never touched. Skipped without the pinned Core (CI job phase5b3c-execution, Colab)."""
import json
from pathlib import Path

import pytest

from strategy_agent.execution import core_world as CW

ok, why = CW.core_available()
pytestmark = pytest.mark.skipif(not ok, reason=f"Layer B needs the pinned Core development reader: {why}")

from strategy_agent.binding import testkit as K  # noqa: E402
from strategy_agent.evaluation import phase4c as C  # noqa: E402
from strategy_agent.execution import b3c_run as R  # noqa: E402
from strategy_agent.execution.controlled import ResearchOnlyRunnerBackend  # noqa: E402
from strategy_agent.phase5a import acceptance as A5  # noqa: E402

G40 = A5.code_identity()["git_sha"]
AID = "RA-IBKR_TSM-SMA520_B3CTEST-0001"


@pytest.fixture
def L(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "EXECUTOR_MODULES", ())
    before = A5.tree_fingerprint(R.ROOT)
    w = CW.build(tmp_path / "w", authorization_id=AID, executor_id=R.EXECUTOR, max_batches=1)
    # the world's own demo binding would hold the single batch: keep the books as the real B3-C ones (no binding yet)
    (w["gov"] / "registry/authorization_bindings.jsonl").write_text("")
    auth = R._authorization(w["gov"], AID)                                 # noqa: SLF001
    comp = R.expected_completion(w["art"], auth["strategy"]["spec_path"])
    m = R.prepare(tmp_path / "prep", artifact_root=w["art"], governance_root=w["gov"], authorization_id=AID,
                  canonical_config=w["canonical_config_path"], agent_commit=G40, completion=comp)
    yield w, m, tmp_path
    assert A5.tree_fingerprint(R.ROOT) == before                            # repository books never touched


def _backend(w):
    return ResearchOnlyRunnerBackend(w["runner"], config_path=w["config_path"], snapshot_root=w["snapshot_root"],
                                     git_commit=G40, synthetic_snapshot=False,
                                     run_context={**w["run_context"], "agent_git_sha": G40},
                                     canonical_config_path=w["canonical_config_path"])


def _urls(w, m):
    fa = w["fa"]
    return fa.comment("binding approval\n" + m["BIND_AUTHORIZATION"]), fa.comment("task approval\n" + m["APPROVE_TASK"])


def _run(w, m, tmp, *, bind_url=None, task_url=None, name="run", be=None):
    b, t = _urls(w, m)
    be = be or _backend(w)
    return R.execute(tmp / "prep", bind_url=bind_url or b, task_url=task_url or t, verifier=w["fa"].verifier,
                     backend=be, work_dir=tmp / name, claim_dir=tmp / "drive_claim", artifact_root=w["art"],
                     governance_root=w["gov"], production_books=False,
                     readiness_manifest=lambda: CW.readiness_manifest(w), benchmark=K.synthetic_benchmark), be


def _uses(w):
    return [json.loads(x) for x in (w["gov"] / "registry/research_authorization_uses.jsonl").read_text().splitlines()
            if x.strip() and json.loads(x)["authorization_id"] == AID]


def test_B3C_execute_once_end_to_end_settles_and_archives(L):
    w, m, tmp = L
    r, be = _run(w, m, tmp)
    assert r["settled"] is True and r["status"]["state"] == "ARCHIVED" and r["status"]["budget_state"] == "CONSUMED"
    assert be.calls == 1 and len(_uses(w)) == 1 and r["task_id"] == m["task_id"]
    rec = json.loads(Path(r["execution_record"]).read_text())
    assert rec["contract"]["execution_mode"] == "REAL_GATED" and rec["contract"]["executor_id"] == R.EXECUTOR
    assert rec["contract"]["request_sha256"] == m["bound_request_sha256"]
    info = (Path(rec["task_archive"]) / "run_info.txt").read_text()
    assert "consumed_batches=1" in info and "real_backtest_executed=true" in info
    acc = R.acceptance(r, governance_root=w["gov"], cloud=True, production=False, authorization_id=AID)
    assert acc["verdict"] == "EXECUTED_PENDING_RESULT_ACCEPTANCE", acc["failed"]
    assert acc["gates"]["B3C-10"]["status"] == "PENDING_RESEARCH_LEAD_DECISION"
    assert all(acc["gates"][f"B3C-{i:02d}"]["status"] == "PASS" for i in range(1, 10))
    ev = acc["gates"]["B3C-08"]["evidence"]                   # fresh-process evaluator: C1-C8 all PASS
    assert ev["evaluator_acceptance"] == "PASS" and set(ev["evaluator_checks"].values()) == {"PASS"}, ev
    import shutil
    shutil.copytree(tmp / "run", tmp / "drive_copy")          # the notebook re-evaluates the Drive copy of the run
    re_ = R.reevaluate(tmp / "drive_copy/archive/execution_records" / Path(r["execution_record"]).name, tmp / "reeval",
                       governance_root=w["gov"], evaluator_commit=G40,
                       archive_prefix_map=(str(tmp / "run"), str(tmp / "drive_copy")))
    assert re_["runner_archive"].startswith(str(tmp / "drive_copy"))
    assert re_["status"] == "PASS" and re_["evaluator_process"]["executor_modules_loaded"] == [] and re_["consumed_batches"] == 0
    assert R.acceptance(r, governance_root=w["gov"], cloud=True, production=False, authorization_id=AID,
                        evaluation=re_)["verdict"] == "EXECUTED_PENDING_RESULT_ACCEPTANCE"
    bad = R.acceptance(r, governance_root=w["gov"], cloud=True, production=False, authorization_id=AID,
                       evaluation={"status": "FAIL", "checks": {**re_["checks"], "C8_governance_isolation": "FAIL"}})
    assert bad["gates"]["B3C-08"]["status"] == "FAIL" and bad["verdict"] == "NO_GO"
    idx = R.evidence_bundle(r, tmp / "prep", tmp / "evidence", acceptance_doc=acc)
    for n in ("prepare/PREPARE_MANIFEST.json", "execution_record.json", "task_archive/run_info.txt",
              "runner_archive/metrics.json", "runner_archive/MANIFEST.json", "runner_receipt.json", "b3c_acceptance.json"):
        assert n in idx, n
    assert not any(n.endswith(".parquet") for n in idx)                    # never the market data
    with pytest.raises(FileExistsError):
        R.evidence_bundle(r, tmp / "prep", tmp / "evidence", acceptance_doc=acc)


def test_B3C_second_attempt_is_refused_by_the_durable_claim(L):
    w, m, tmp = L
    _run(w, m, tmp)
    with pytest.raises(R.B3CStopped) as e:
        _run(w, m, tmp, name="run2")
    assert e.value.code == "CLAIM_EXISTS" and len(_uses(w)) == 1


def test_B3C_wrong_task_approval_stops_before_any_write(L):
    w, m, tmp = L
    bad = w["fa"].comment("task approval\n" + m["APPROVE_TASK"].replace(m["task_id"], "T-" + "0" * 20))
    with pytest.raises(R.B3CStopped) as e:
        _run(w, m, tmp, task_url=bad)
    assert e.value.code.startswith("TASK_APPROVAL_")
    assert not (tmp / "drive_claim").exists() and not (tmp / "run").exists() and _uses(w) == []
    from strategy_agent.binding.store import BindingStore
    assert BindingStore(w["gov"]).for_source(m["source_request_sha256"]) == []


def test_B3C_bind_comment_for_another_request_is_rejected(L):
    w, m, tmp = L
    other = w["fa"].comment("binding approval\n" + m["BIND_AUTHORIZATION"].replace(m["source_request_sha256"], "f" * 64))
    with pytest.raises(R.B3CStopped) as e:
        _run(w, m, tmp, bind_url=other)
    assert e.value.code == "BINDING_REJECTED" and _uses(w) == [] and not (tmp / "drive_claim").exists()


def test_B3C_tampered_prepared_request_is_refused(L):
    w, m, tmp = L
    p = tmp / "prep/request.json"
    p.write_text(p.read_text().replace("TSM", "TSM ", 1))
    with pytest.raises(R.B3CStopped) as e:
        _run(w, m, tmp)
    assert e.value.code == "PREPARED_FILES_CHANGED" and _uses(w) == []
