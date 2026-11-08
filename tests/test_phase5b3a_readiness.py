"""Phase 5-B3-A readiness (non-consuming): manifest, checks, authorization draft, gates and archive.

No backtest, no market data, no Holdout, no authorization created / approved / consumed; the real Runner stays
DISABLED. Contradiction and missing-record cases are injected only into in-memory copies of the records.
"""
import copy
import json
import shutil

import pytest

from strategy_agent.phase5a import acceptance as A5
from strategy_agent.readiness import acceptance as RA
from strategy_agent.readiness import authorization_draft as D
from strategy_agent.readiness import checks as K
from strategy_agent.readiness import manifest as M
from strategy_agent.readiness.candidate import CANDIDATE, ROOT

CODE = {"git_sha": "a" * 40, "dirty": False, "python": "3.11.0"}
HUMAN = ("authorization_id", "bound_request_hash", "budget_batch_limit", "executor_identity")
RESOLVED = ("research_market", "strategy_id_version", "strategy_spec_sha256", "dataset_snapshot_id",
            "dataset_manifest_sha256", "core_commit_sha", "temporal_rules", "research_window", "holdout_exclusion",
            "trading_cost_profile", "slippage_assumption", "git_and_environment_identity", "archive_destination")


@pytest.fixture(scope="module")
def m():
    return M.build(CODE)


def test_manifest_covers_every_required_field_and_resolves_from_authoritative_records(m):
    assert set(m["fields"]) == set(M.FIELDS) and len(M.FIELDS) == 17
    for k in RESOLVED:
        assert m["fields"][k]["status"] == "RESOLVED", (k, m["fields"][k])
    F = m["fields"]
    assert F["dataset_snapshot_id"]["value"] == "TSM_DEV_1M_20240529_20240628_18350b3adba8e28a7e5e"
    assert F["dataset_manifest_sha256"]["value"] == "8b6fc838b72ae3015f6eb418c3a77115e53a86f185e05c2249e9dcd58192cfc5"
    assert F["core_commit_sha"]["value"] == "b16179a7cc077c36046574769a32c367fa476cae"
    assert set(F["core_commit_sha"]["sources"]) == {"run_config", "dataset_producer_in_authorization",
                                                    "prior_real_run_core", "ci_pin"}
    assert F["strategy_spec_sha256"]["value"] == "b358416e4764801a6fd80512d665be27cdf85b7b9f5f3c118bd8934e6fd44123"
    assert F["holdout_exclusion"]["value"]["window_end_before_holdout"] is True
    assert F["slippage_assumption"]["value"]["slippage_bps_per_side"] == {"BASE": 2, "LOW": 1, "STRESS": 4, "CAT_STRESS": 2}


def test_authorization_binding_budget_executor_are_human_decisions_never_defaults(m):
    for k in HUMAN:
        f = m["fields"][k]
        assert f["status"] == "BLOCKED" and f["category"] == "HUMAN_DECISION" and f["value"] is None
    assert "SPENT" in m["fields"]["authorization_id"]["reason"]
    assert m["status"] == "BLOCKED" and m["real_runner"] == "DISABLED" and m["authorization_created"] is False


def test_spec_dataset_contradiction_is_found_by_the_real_exporter(m):
    x = m["cross_checks"]["phase5_request_spec_dataset_binding"]
    assert x["status"] == "BLOCKED" and "SPEC_DATASET_REFERENCE_MISMATCH" in x["error"]
    assert x["spec_data_requirements"][0]["snapshot_id"] == "20261101_205431_06359faf7ef4_IBKR_FINAL_GATE_FULL"
    assert m["ready_for_authorization_decision"] is False and "CONTRADICTION" in m["blocking_categories"]


def test_a_spec_repinned_to_the_dev_snapshot_would_satisfy_the_exporter(tmp_path):
    """The human decision is sufficient: same rules / parameters, data_requirements re-pinned (new sha)."""
    import hashlib
    from strategy_agent.proposal import exporter as E
    cfg = json.loads((ROOT / CANDIDATE["run_config"]).read_text())
    spec = json.loads((ROOT / cfg["strategy"]["spec_path"]).read_text())
    for d in spec["data_requirements"]["datasets"]:
        d.update(snapshot_id=cfg["dataset"]["snapshot_id"], producer_commit_sha=cfg["dataset"]["producer_commit_sha"],
                 manifest_sha256=cfg["snapshot_binding"]["manifest_sha256"])
    art = tmp_path / "art"
    shutil.copytree(ROOT / "configs", art / "configs")
    sp = art / cfg["strategy"]["spec_path"]
    sp.parent.mkdir(parents=True)
    sp.write_text(json.dumps(spec, indent=2))
    req = {"strategy_spec_ref": cfg["strategy"]["spec_path"], "strategy_spec_sha256": hashlib.sha256(sp.read_bytes()).hexdigest(),
           "strategy_family": {k: cfg["strategy"][k] for k in ("strategy_id", "version", "parameters")},
           "market": {"symbol": "TSM"}, "dataset_ref": {k: cfg["dataset"][k] for k in ("dataset_id", "producer_commit_sha")},
           "dataset_snapshot_id": cfg["dataset"]["snapshot_id"],
           "dataset_manifest_sha256": cfg["snapshot_binding"]["manifest_sha256"],
           "cost_profile_ref": {k: cfg["costs"][k] for k in ("scenario_set_path", "scenario_set_sha256")},
           "research_window": {k: cfg["windows"][k] for k in ("warmup_start", "start", "end")}}
    E._check_references(req, {"draft": {"strategy_spec_fragment": {}, "required_data": {"coverage": {"start": "2024-05-29", "end": "2024-06-28"}}}}, art)
    assert req["strategy_spec_sha256"] != cfg["strategy"]["spec_file_sha256"]        # a NEW pin for the human to approve


@pytest.mark.parametrize("mutate,field,category", [
    (lambda R: R["cfg"]["snapshot_binding"].__setitem__("manifest_sha256", "0" * 64), "dataset_manifest_sha256", "CONTRADICTION"),
    (lambda R: R["cfg"]["core"].__setitem__("phase4a_sha", "1" * 40), "core_commit_sha", "CONTRADICTION"),
    (lambda R: R.__setitem__("ci_core", None), "core_commit_sha", "MISSING"),
    (lambda R: R.__setitem__("spec_file_sha", "f" * 64), "strategy_spec_sha256", "CONTRADICTION"),
    (lambda R: R["cfg"]["windows"].__setitem__("end", "2025-02-01T00:00:00-05:00"), "research_window", "CONTRADICTION"),
    (lambda R: R["cfg"]["dataset"].pop("snapshot_id"), "dataset_snapshot_id", "MISSING"),
    (lambda R: R.__setitem__("cost_file_sha", None), "trading_cost_profile", "MISSING"),
])
def test_contradictory_or_missing_values_are_blocked_never_inferred(monkeypatch, mutate, field, category):
    real = M._records

    def records():
        R = copy.deepcopy(real())
        mutate(R)
        return R
    monkeypatch.setattr(M, "_records", records)
    f = M.build(CODE)["fields"][field]
    assert f["status"] == "BLOCKED" and f["category"] == category and f["value"] is None


def test_holdout_exclusion_fails_when_the_window_reaches_the_holdout(monkeypatch):
    real = M._records

    def records():
        R = copy.deepcopy(real())
        R["cfg"]["windows"]["end"] = "2025-01-02T00:00:00-05:00"
        R["auth"]["research_window"]["end"] = "2025-01-02T00:00:00-05:00"
        return R
    monkeypatch.setattr(M, "_records", records)
    f = M.build(CODE)["fields"]["holdout_exclusion"]
    assert f["status"] == "BLOCKED"


def test_dataset_readiness_nine_checks_pass_from_archived_evidence(m):
    ds = K.dataset_readiness(m)
    assert len(ds) == 9 and all(v["status"] == "PASS" for v in ds.values()), {k: v["status"] for k, v in ds.items()}
    assert ds["D9_core_benchmark_and_readiness_evidence"]["detail"]["core_benchmark"]["status"] == "BENCHMARK_PASS"


def test_controlled_readiness_real_runner_disabled_and_no_env_enable_path():
    cr = K.controlled_readiness()
    assert all(v["status"] == "PASS" for v in cr.values()), cr
    assert cr["C1_real_runner_disabled"]["detail"]["REAL_EXECUTION_ENABLED"] is False


def test_governance_spent_authorization_not_reusable_and_nothing_created(m):
    g = K.governance_readiness(m)
    assert all(v["status"] == "PASS" for v in g.values()), g
    assert g["G2_template_authorization_spent_not_reusable"]["detail"]["used"] == 1


def test_authorization_draft_is_unapproved_and_never_written(m):
    before = A5.tree_fingerprint(ROOT)
    d = D.build(m)
    assert d["status"] == "DRAFT_NOT_APPROVED" and "authorization_id" in d and d["authorization_id"]["decision"] == D.HDR
    for k in ("max_research_batches", "executor_identity"):
        assert d[k]["decision"] == D.HDR
    assert d["validity_period"]["issued_at"] == D.HDR and d["strategy_specification"]["spec"] == D.HDR
    assert d["holdout_policy"]["access"] == "NO_ACCESS"
    r = D.schema_dry_run(d)
    assert r["status"] == "PASS" and r["written"] is False and r["placeholders_used_for"] == ["spec_file_sha256", "spec_path"]
    assert A5.tree_fingerprint(ROOT) == before


def test_committed_docs_are_the_deterministic_output():
    r = RA.evaluate(code=None)
    for name, obj in (("readiness_manifest.json", r["manifest"]), ("authorization_draft.json", r["draft"])):
        assert (ROOT / "docs/phase5b3a" / name).read_bytes() == RA._dumps(obj), f"{name}: run --emit-docs"


def _junit(tmp_path, *, fail=None):
    names = ["test_phase5b1_x.t", "test_phase5b2_controlled.t", "test_phase5b3a_readiness.t",
             "test_phase5b2_security_remediation.test_P0_1_same_reservation_from_several_processes_runs_at_most_once[2]",
             "test_phase5b2_independent_p0_2_gate.t"]
    cases = "".join(f'<testcase classname="{n.rsplit(".", 1)[0]}" name="{n.rsplit(".", 1)[1]}">'
                    + ("<failure/>" if fail and fail in n else "") + "</testcase>" for n in names)
    p = tmp_path / "j.xml"
    p.write_text(f"<testsuite>{cases}</testsuite>")
    return p


def test_gates_verdict_no_go_while_spec_binding_is_blocked(tmp_path):
    r = RA.evaluate(code=CODE)
    tests = RA.junit([_junit(tmp_path)])
    g = RA.gates(r["manifest"], r["dataset"], r["controlled"], r["governance"], r["draft_check"], tests,
                 tests_rc=0, cloud=True, code=CODE, drive={"status": "NOT_RUN"})
    assert {k: v["status"] for k, v in g.items()} == {**{k: "PASS" for k in RA.GATES}, "B3A-02": "BLOCKED"}
    v = RA.verdict(g)
    assert v["verdict"] == "NO_GO" and list(v["blockers"]) == ["B3A-02"]


def test_gates_scoped_go_only_if_the_spec_binding_passes_and_cloud(tmp_path):
    r = RA.evaluate(code=CODE)
    r["manifest"]["cross_checks"]["phase5_request_spec_dataset_binding"] = {"status": "PASS"}
    tests = RA.junit([_junit(tmp_path)])
    args = (r["manifest"], r["dataset"], r["controlled"], r["governance"], r["draft_check"], tests)
    assert RA.verdict(RA.gates(*args, tests_rc=0, cloud=True, code=CODE, drive={"status": "NOT_RUN"}))["verdict"] == "SCOPED_GO"
    assert RA.verdict(RA.gates(*args, tests_rc=0, cloud=False, code=CODE, drive={"status": "NOT_RUN"}))["verdict"] == "NOT_EVIDENCED"
    g = RA.gates(*args, tests_rc=0, cloud=True, code=CODE, drive={"status": "FAIL"})
    assert g["B3A-01"]["status"] == "FAIL"
    g = RA.gates(*args[:-1], RA.junit([_junit(tmp_path, fail="several_processes")]), tests_rc=1, cloud=True, code=CODE,
                 drive={"status": "NOT_RUN"})
    assert g["B3A-07"]["status"] == "FAIL" and g["B3A-08"]["status"] == "FAIL"


def test_snapshot_bytes_check_is_strict(tmp_path, m):
    assert RA.snapshot_bytes_check(None, m)["status"] == "NOT_RUN"
    assert RA.snapshot_bytes_check(tmp_path / "wrong_name", m)["status"] == "FAIL"
    d = tmp_path / m["fields"]["dataset_snapshot_id"]["value"]
    d.mkdir()
    (d / "manifest.json").write_text("{}")
    (d / "tsm_development_1m.parquet").write_bytes(b"x")
    r = RA.snapshot_bytes_check(d, m)
    assert r["status"] == "FAIL" and r["observed"]["manifest.json"] != r["expected"]["manifest.json"]


def test_archive_is_immutable_indexed_and_tamper_evident(tmp_path):
    out = tmp_path / "executions" / "20261108_000000_aaaaaaa"
    before = A5.tree_fingerprint(ROOT)
    res = RA.run(out, junit_paths=[_junit(tmp_path)], tests_rc=0, cloud=False, context={"note": "test"}, snapshot_dir=None)
    assert res["verdict"]["verdict"] in ("NO_GO", "NOT_EVIDENCED") and A5.tree_fingerprint(ROOT) == before
    assert sorted(p.name for p in out.iterdir()) == sorted(RA.OUTPUT_FILES)
    assert RA.verify_archive(out)["status"] == "PASS"
    info = (out / "run_info.txt").read_text()
    for k in ("real_runner=DISABLED", "authorization_created=false", "market_data_read=false", "holdout_read=false",
              "new_backtest_executed=false"):
        assert k in info
    with pytest.raises(FileExistsError):
        RA.run(out, junit_paths=[], tests_rc=0, cloud=False, context=None, snapshot_dir=None)
    (out / "readiness_checks.json").write_text("{}")
    assert RA.verify_archive(out)["status"] == "FAIL"
