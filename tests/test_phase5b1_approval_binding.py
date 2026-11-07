"""Phase 5-B1: trusted approval evidence + governed authorization binding.

All evidence here is TEST_FIXTURE (TestApprovalSource, ISOLATED_TEST trust) over GitHub-shaped payloads; binding runs
in a SYNTHETIC_FIXTURE world under tmp. The repository's governance/registry must be unchanged.
"""
import copy
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from strategy_agent.approval import ApprovalEvidenceVerifier, ApprovalRejected, Expectation, GitHubCommentSource
from strategy_agent.approval import tokens as T
from strategy_agent.approval.testkit import FixtureApprovals
from strategy_agent.binding import acceptance as BA
from strategy_agent.binding import testkit as K
from strategy_agent.binding.service import BindingRejected
from strategy_agent.binding.store import BindingStore
from strategy_agent.evaluation import phase4c as C
from strategy_agent.orchestration import governance as GV
from strategy_agent.orchestration import request as Q
from strategy_agent.orchestration.orchestrator import Orchestrator
from strategy_agent.phase5a import acceptance as A5

ROOT = Path(__file__).resolve().parents[1]


def schema(name):
    return json.loads((ROOT / "schemas" / name).read_text())


@pytest.fixture(scope="module")
def iso(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setattr(C, "EXECUTOR_MODULES", ())          # process-level C8 is asserted in the fresh-process test below
    before = A5.tree_fingerprint(ROOT)
    r = BA.isolated(tmp_path_factory.mktemp("b1"))
    yield r, before
    mp.undo()


@pytest.fixture
def world(tmp_path):
    fa = FixtureApprovals()
    w = K.build_world(tmp_path / "w")
    return w, fa, K.service(w, fa.verifier)


# ------------------------------------------------------------------------------------------- contracts
def test_schemas_are_valid_and_request_schema_v1_untouched():
    for n in ("RESEARCH_APPROVAL_EVIDENCE_SCHEMA.json", "AUTHORIZATION_BINDING_SCHEMA.json"):
        Draft202012Validator.check_schema(schema(n))
    import hashlib
    assert hashlib.sha256((ROOT / "schemas/RESEARCH_REQUEST_SCHEMA.json").read_bytes()).hexdigest() == \
        "1b0530f321c6c71e9eac1bbe5f9c33e3f92e4af4c4ce11b80777c9a94c5324eb"


def test_identities_pin_repository_and_numeric_account():
    ids = json.loads((ROOT / "governance/approval_identities.json").read_text())
    leads = json.loads((ROOT / "governance/approvers.json").read_text())["research_leads"]
    assert ids["repository"] == {"full_name": "hh4832/strategy-research-agent", "id": 1443253343}
    assert ids["github_accounts"] == {"hh4832": 269247281} and set(ids["github_accounts"]) <= set(leads)


def test_token_grammar_roundtrip_and_strictness():
    line = T.approval_line(intent="APPROVE_TASK", request_sha256="a" * 64, authorization_id="RA-X_Y-Z-0001",
                           scope="b" * 64, task_id="T-" + "c" * 20, expires_utc="2026-11-09T00:00:00Z")
    tok = T.parse_approval("hello\n" + line + "\nthanks")
    assert tok.intent == "APPROVE_TASK" and tok.task == "T-" + "c" * 20
    for bad in ("", "LGTM", line + "\n" + line, line.replace("decision=APPROVE", "decision=REJECT"),
                line.replace(" task=T-" + "c" * 20, ""), "RESEARCH-APPROVAL v1 decision=APPROVE"):
        with pytest.raises(T.TokenError):
            T.parse_approval(bad)
    with pytest.raises(T.TokenError):
        T.approval_line(intent="BIND_AUTHORIZATION", request_sha256="a" * 64, authorization_id="RA-X_Y-Z-0001",
                        scope="b" * 64, task_id="T-" + "c" * 20, expires_utc="2026-11-09T00:00:00Z")


def test_scope_excludes_authorization_and_covers_execution_relevant_fields():
    d = Q.load(ROOT / "docs/examples/phase5b1/PROBE_PENDING_tsm_sma520_research_only_real.json")
    b = dict(d, authorization_ref={"authorization_id": "RA-A-B-0001"})
    assert T.scope_sha256(d) == T.scope_sha256(b)
    for path in (("strategy_spec_sha256",), ("dataset_manifest_sha256",), ("core_commit",), ("research_window", "end"),
                 ("cost_profile_ref", "scenario_set_sha256"), ("holdout_policy", "lock_id"), ("research_budget", "batches_requested")):
        x = copy.deepcopy(d)
        cur = x
        for k in path[:-1]:
            cur = cur[k]
        cur[path[-1]] = "changed" if isinstance(cur[path[-1]], str) else 7
        assert T.scope_sha256(x) != T.scope_sha256(d), path


def test_verified_evidence_is_schema_valid_and_identity_comes_from_github(world):
    w, fa, svc = world
    url = fa.bind_url(w["request"], w["authorization_id"])
    ev = fa.verifier.verify(url, Expectation("BIND_AUTHORIZATION", Q.request_hash(w["request"]), w["authorization_id"],
                                             T.scope_sha256(w["request"])))
    Draft202012Validator(schema("RESEARCH_APPROVAL_EVIDENCE_SCHEMA.json")).validate(ev)
    assert ev["author"] == {"login": "hh4832", "id": 269247281, "type": "User", "author_association": "OWNER",
                            "performed_via_github_app": None}
    assert ev["approval_source"] == {"kind": "TEST_FIXTURE", "transport": "TEST_FIXTURE", "trust": "ISOLATED_TEST"}
    bad = copy.deepcopy(ev)
    bad["approval_source"]["trust"] = "PRODUCTION"                      # TEST_FIXTURE can never claim PRODUCTION
    assert list(Draft202012Validator(schema("RESEARCH_APPROVAL_EVIDENCE_SCHEMA.json")).iter_errors(bad))


def test_production_verifier_refuses_fixture_and_injected_sources():
    fa = FixtureApprovals()
    with pytest.raises(ApprovalRejected, match="V09"):
        ApprovalEvidenceVerifier(fa.source, production=True)
    with pytest.raises(ApprovalRejected, match="V09"):
        ApprovalEvidenceVerifier(GitHubCommentSource("hh4832/strategy-research-agent", transport=lambda u: (200, {})),
                                 production=True)
    assert "token" not in repr(GitHubCommentSource("hh4832/strategy-research-agent", token="SECRET")).lower() or \
        "SECRET" not in repr(GitHubCommentSource("hh4832/strategy-research-agent", token="SECRET"))


def test_injected_github_transport_parses_real_shaped_payloads():
    fa = FixtureApprovals()
    url = fa.comment("x\n" + T.approval_line(intent="BIND_AUTHORIZATION", request_sha256="a" * 64,
                                             authorization_id="RA-A-B-0001", scope="b" * 64,
                                             expires_utc=(datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")))
    cid = int(url.rsplit("-", 1)[1])

    def transport(u):
        if u.endswith("/repos/hh4832/strategy-research-agent"):
            return 200, {"full_name": "hh4832/strategy-research-agent", "id": 1443253343}
        if f"/issues/comments/{cid}" in u:
            return 200, fa.source.comments[cid]
        if "/issues/900/comments" in u:
            return 200, list(fa.source.comments.values())
        return 404, None
    v = ApprovalEvidenceVerifier(GitHubCommentSource("hh4832/strategy-research-agent", transport=transport), production=False)
    ev = v.verify(url, Expectation("BIND_AUTHORIZATION", "a" * 64, "RA-A-B-0001", "b" * 64))
    assert ev["approval_source"] == {"kind": "GITHUB_REST_API", "transport": "INJECTED", "trust": "ISOLATED_TEST"}
    v403 = ApprovalEvidenceVerifier(GitHubCommentSource("hh4832/strategy-research-agent", transport=lambda u: (403, None)),
                                    production=False)
    with pytest.raises(ApprovalRejected, match="V00"):
        v403.verify(url, Expectation("BIND_AUTHORIZATION", "a" * 64, "RA-A-B-0001", "b" * 64))


# --------------------------------------------------------------------------------------------- binding
def test_binding_creates_new_immutable_derived_request_and_consumes_nothing(world):
    w, fa, svc = world
    url = fa.bind_url(w["request"], w["authorization_id"])
    src_bytes = w["request_path"].read_bytes()
    dry = svc.bind(dry_run=True, **K.bind_kwargs(w, url))
    assert dry["written"] is False and not (w["gov"] / "registry/authorization_bindings.jsonl").exists()
    r = svc.bind(dry_run=False, **K.bind_kwargs(w, url))
    rec = r["record"]
    Draft202012Validator(schema("AUTHORIZATION_BINDING_SCHEMA.json")).validate(
        {k: v for k, v in rec.items() if k not in ("seq", "prev_sha256", "record_sha256")})
    bound = json.loads(Path(r["bound_request_path"]).read_text())
    assert w["request_path"].read_bytes() == src_bytes                     # parent never overwritten
    assert rec["source_request_sha256"] == Q.request_hash(w["request"]) != rec["bound_request_sha256"] == Q.request_hash(bound)
    assert {k for k in bound if bound[k] != w["request"].get(k)} == {"authorization_ref"}
    assert rec["source_proposal_sha256"] == w["proposal"]["proposal_sha256"]
    assert rec["binding_actor"] == {"github_login": "hh4832", "github_id": 269247281,
                                    "source": "VERIFIED_APPROVAL_EVIDENCE_AUTHOR"}
    assert rec["consumes_budget"] is False and rec["approves_execution"] is False
    assert svc.gov.uses(w["authorization_id"]) == []
    with pytest.raises(FileExistsError):                                    # stored artefacts are immutable
        from strategy_agent.binding.store import write_immutable
        write_immutable(w["gov"] / rec["bound_request_path"], b"{}")
    assert svc.bind(dry_run=False, **K.bind_kwargs(w, url))["status"] == "ALREADY_BOUND"
    with pytest.raises(BindingRejected, match="B09_REQUEST_ALREADY_BOUND"):     # retry with other evidence
        svc.bind(dry_run=False, **K.bind_kwargs(w, fa.bind_url(w["request"], w["authorization_id"])))
    assert len(BindingStore(w["gov"]).records()) == 1


def test_binding_refuses_unknown_authorization_and_never_creates_one(world):
    w, fa, svc = world
    url = fa.bind_url(w["request"], "RA-NOT_THERE-X-0001")
    with pytest.raises(BindingRejected) as e:
        svc.bind(dry_run=False, **K.bind_kwargs(w, url, authorization_id="RA-NOT_THERE-X-0001"))
    assert "B06_AUTHORIZATION_MISSING" in {f["code"] for f in e.value.failures}
    assert len((w["gov"] / "governance/research_authorizations.jsonl").read_text().splitlines()) == 1


def test_binding_detects_tampered_proposal_and_provenance(world):
    w, fa, svc = world
    url = fa.bind_url(w["request"], w["authorization_id"])
    p = json.loads(w["proposal_path"].read_text())
    p["draft"]["title"] = "tampered"
    w["proposal_path"].write_text(json.dumps(p))
    with pytest.raises(BindingRejected, match="B03_PROPOSAL_INVALID"):
        svc.bind(dry_run=True, **K.bind_kwargs(w, url))


def test_bound_request_still_needs_governance_and_unbound_real_request_is_blocked(world, tmp_path):
    w, fa, svc = world
    r = svc.bind(dry_run=False, **K.bind_kwargs(w, fa.bind_url(w["request"], w["authorization_id"])))
    bound = json.loads(Path(r["bound_request_path"]).read_text())
    o = Orchestrator(tmp_path / "s", tmp_path / "o", root=w["art"], governance_root=w["gov"],
                     benchmark=K.synthetic_benchmark, approval_verifier=fa.verifier)
    s = o.submit(bound, actor="x", actor_kind="TEST_FIXTURE")
    st = {c["id"]: c["status"] for c in s["governance"]["checks"]}
    assert st["G11_AUTHORIZATION_BINDING"] == "PASS" and [k for k, v in st.items() if v == "FAIL"] == ["G10_EXECUTION_MODE"]
    forged = dict(w["request"], authorization_ref={"authorization_id": w["authorization_id"]}, request_id="RR-FORGED-BIND")
    s2 = o.submit(forged, actor="x", actor_kind="TEST_FIXTURE")       # "just replace authorization_ref" is not a binding
    assert s2["state"] == "BLOCKED" and {c["id"]: c["status"] for c in s2["governance"]["checks"]}["G11_AUTHORIZATION_BINDING"] == "FAIL"
    # revoking the authorization after binding blocks it
    from strategy_agent.registry.chain import ChainedLog
    ChainedLog(w["gov"] / "governance/research_authorizations.jsonl").append(
        {"action": "REVOKE", "contract": "research-only-authorization-v1", "authorization_id": w["authorization_id"], "approved_by": "hh4832",
         "approval_ref": "https://example.test/revoke", "approved_at": "2026-11-07T13:00:00+08:00", "reason": "test revoke"})
    o2 = Orchestrator(tmp_path / "s2", tmp_path / "o", root=w["art"], governance_root=w["gov"],
                      benchmark=K.synthetic_benchmark, approval_verifier=fa.verifier)
    s3 = o2.submit(bound, actor="x", actor_kind="TEST_FIXTURE")
    assert {c["id"]: c["status"] for c in s3["governance"]["checks"]}["G5_AUTHORIZATION"] == "FAIL"


def test_task_approval_is_one_time_and_rechecked_before_running(tmp_path):
    fa = FixtureApprovals()
    gov = tmp_path / "gov"
    import shutil
    shutil.copytree(A5.FIX / "governance_root", gov)
    o = Orchestrator(tmp_path / "s", tmp_path / "o", governance_root=gov, source_archive_root=A5.FIX,
                     approval_verifier=fa.verifier, clock=A5.StepClock())
    rr = A5.fixture_replay_request(A5.FIX)
    tid = o.submit(rr, actor="x")["task_id"]
    url = fa.comment(fa.line(rr, intent="APPROVE_TASK", task_id=tid))
    o.approve(tid, evidence_ref=url)
    # revoked between approval and dispatch -> BLOCKED, nothing executed
    fa.comment(f"RESEARCH-APPROVAL-REVOKE v1 comment={url.rsplit('-', 1)[1]}")
    st = o.dispatch(tid)
    assert st["state"] == "BLOCKED" and st["history"][-1]["reason"] == "APPROVAL_V08_REVOKED"
    assert not (tmp_path / "s/executions").exists()
    # the same comment cannot approve a different task
    rr2 = dict(rr, request_id="RR-SECOND-TASK-B1")
    t2 = o.submit(rr2, actor="x")["task_id"]
    with pytest.raises(Exception, match="V06_SUBJECT_MISMATCH"):
        o.approve(t2, evidence_ref=url)


def test_g5_semantics_for_synthetic_are_machine_readable():
    syn = A5.fixture_replay_request(A5.FIX)
    syn.update(execution_mode="SYNTHETIC_TEST", request_id="RR-SYN-G5",
               requested_change={"kind": "SYNTHETIC_TEST", "description": "g5"})
    t = GV.Governance(ROOT).check(syn, test_mode=True)
    p = GV.Governance(ROOT).check(syn, test_mode=False)
    g5 = lambda d: next(c for c in d.checks if c["id"] == "G5_AUTHORIZATION")  # noqa: E731
    assert (t.status, g5(t)["status"]) == ("TEST_MODE_ONLY", "NOT_EVALUATED")
    assert (p.status, g5(p)["status"]) == ("BLOCKED", "NOT_EVALUATED")
    assert all(c["status"] in ("PASS", "FAIL", "NOT_APPLICABLE", "NOT_EVALUATED") for c in t.checks + p.checks)


def test_repository_books_refuse_test_verifier(tmp_path):
    with pytest.raises(ValueError):
        Orchestrator(tmp_path / "s", tmp_path / "o", approval_verifier=FixtureApprovals().verifier)


# ---------------------------------------------------------------------------------- 18 threats (isolated)
@pytest.mark.parametrize("n", range(1, 19))
def test_threat_model_case_is_rejected_for_the_right_reason(iso, n):
    r, _ = iso
    t = next(x for x in r["threats"] if x["threat"] == n)
    assert t["status"] == "PASS", t


def test_isolated_run_leaves_repository_books_unchanged(iso):
    r, before = iso
    assert r["repo_unchanged"] and A5.tree_fingerprint(ROOT) == before


def test_fresh_process_acceptance_is_scoped_go_without_real_evidence_and_never_overwrites(tmp_path):
    tj = tmp_path / "t.json"
    tj.write_text(json.dumps({"returncode": 0, "summary": "pytest (caller)"}))
    out = tmp_path / "approvals/20261107_160000_test000"
    cmd = [sys.executable, "-m", "strategy_agent.binding.acceptance", "--out-dir", str(out), "--tests-json", str(tj)]
    env = {k: v for k, v in __import__("os").environ.items() if k not in ("GH_TOKEN", "GITHUB_TOKEN")}
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT, timeout=900, env=env)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    res = json.loads(r.stdout[r.stdout.index("{"):])
    assert set(res["gates"].values()) == {"PASS"} and res["verdict"]["verdict"] == "SCOPED_GO"
    assert res["real"] == "PENDING_USER_EVIDENCE"
    assert sorted(p.name for p in out.iterdir() if p.is_file()) == sorted(BA.OUTPUT_FILES)
    info = (out / "run_info.txt").read_text()
    assert "consumed_batches=0" in info and "github_token_written=false" in info
    again = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT, timeout=900, env=env)
    assert again.returncode != 0 and "FileExistsError" in again.stderr
