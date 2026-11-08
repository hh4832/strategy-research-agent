"""Phase 5-B1 security remediation (Codex review of PR #12 @ cf15654): regression + adversarial tests.

S1 revocation permanence     S2 complete revocation pagination     S3 production trust isolation
M1 expiry UTC / boundary     M2 budget stale snapshot / TOCTOU     M3 duplicate dispatch / retry races
M4 CI / subprocess failure masking                                  M5 incomplete archive hash index
All evidence is TEST_FIXTURE / injected; nothing touches the repository's books.
"""
import copy
import json
import os
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from strategy_agent.approval import ApprovalEvidenceVerifier, ApprovalRejected, Expectation, GitHubCommentSource
from strategy_agent.approval import sources as SRC
from strategy_agent.approval import tokens as T
from strategy_agent.approval.revocations import RevocationLedger
from strategy_agent.approval.testkit import FixtureApprovals
from strategy_agent.binding import acceptance as BA
from strategy_agent.binding import testkit as K
from strategy_agent.binding.service import BindingRejected
from strategy_agent.orchestration import adapters as AD
from strategy_agent.orchestration import request as Q
from strategy_agent.orchestration.governance import ISOLATION_MARKER, Governance, is_isolated_test_root
from strategy_agent.orchestration.orchestrator import Orchestrator
from strategy_agent.phase5a import acceptance as A5
from strategy_agent.registry.chain import ChainedLog

ROOT = Path(__file__).resolve().parents[1]
REPO = "hh4832/strategy-research-agent"


def _exp(src, aid):
    return Expectation("BIND_AUTHORIZATION", Q.request_hash(src), aid, T.scope_sha256(src))


@pytest.fixture
def world(tmp_path):
    fa = FixtureApprovals()
    w = K.build_world(tmp_path / "w")
    return w, fa, K.service(w, fa.verifier)


# ============================================================================== S1 revocation permanence
def test_S1_revocation_deleted_after_observation_stays_revoked(world):
    w, fa, _ = world
    url = fa.bind_url(w["request"], w["authorization_id"])
    cid = int(url.rsplit("-", 1)[1])
    rev = fa.comment(f"RESEARCH-APPROVAL-REVOKE v1 comment={cid}")
    with pytest.raises(ApprovalRejected, match="V08_REVOKED"):
        fa.verifier.verify(url, _exp(w["request"], w["authorization_id"]))
    del fa.source.comments[int(rev.rsplit("-", 1)[1])]                     # attacker deletes the revocation
    with pytest.raises(ApprovalRejected, match="V08_REVOKED_PERMANENT"):
        fa.verifier.verify(url, _exp(w["request"], w["authorization_id"]))
    r = RevocationLedger(fa.ledger_path).revoked(cid)
    assert r["revoking_comment_id"] == int(rev.rsplit("-", 1)[1]) and r["observed_at"].endswith("Z")


def test_S1_revocation_edited_after_observation_stays_revoked(world):
    w, fa, svc = world
    url = fa.bind_url(w["request"], w["authorization_id"])
    cid = int(url.rsplit("-", 1)[1])
    rev = fa.comment(f"RESEARCH-APPROVAL-REVOKE v1 comment={cid}")
    with pytest.raises(BindingRejected, match="V08_REVOKED"):
        svc.bind(dry_run=True, **K.bind_kwargs(w, url))
    fa.source.comments[int(rev.rsplit("-", 1)[1])]["body"] = "never mind"   # edit removes the revoke line
    with pytest.raises(BindingRejected, match="V08_REVOKED_PERMANENT"):
        svc.bind(dry_run=False, **K.bind_kwargs(w, url))
    assert not (w["gov"] / "registry/authorization_bindings.jsonl").exists()


def test_S1_tampered_revocation_ledger_fails_closed(world):
    w, fa, _ = world
    url = fa.bind_url(w["request"], w["authorization_id"])
    cid = int(url.rsplit("-", 1)[1])
    fa.comment(f"RESEARCH-APPROVAL-REVOKE v1 comment={cid}")
    with pytest.raises(ApprovalRejected):
        fa.verifier.verify(url, _exp(w["request"], w["authorization_id"]))
    lines = fa.ledger_path.read_text().splitlines()
    rec = json.loads(lines[0])
    rec["approval_comment_id"] = 1                                            # "un-revoke" by rewriting history
    fa.ledger_path.write_text(json.dumps(rec) + "\n")
    good = fa.bind_url(w["request"], w["authorization_id"])
    with pytest.raises(ApprovalRejected, match="V08_REVOCATION_LEDGER_INVALID"):
        fa.verifier.verify(good, _exp(w["request"], w["authorization_id"]))


def test_S1_revocation_between_prepare_and_commit_blocks_binding(world):
    w, fa, svc = world
    url = fa.bind_url(w["request"], w["authorization_id"])
    plan = svc.prepare(**K.bind_kwargs(w, url))
    assert plan["ok"]
    fa.comment(f"RESEARCH-APPROVAL-REVOKE v1 comment={url.rsplit('-', 1)[1]}")
    with pytest.raises(BindingRejected, match="B07_APPROVAL_V08_REVOKED"):
        svc._commit(plan, dry_run=False)
    assert BA.BindingStore(w["gov"]).records() == []


def test_S1_isolated_verifier_cannot_use_production_ledger():
    with pytest.raises(ValueError):
        ApprovalEvidenceVerifier(FixtureApprovals().source, revocation_ledger=ROOT / "registry/approval_revocations.jsonl")
    with pytest.raises(ValueError):
        ApprovalEvidenceVerifier(FixtureApprovals().source)                  # no implicit ledger for isolated


# ============================================================================= S2 complete pagination
def _paged_transport(fa, issue, comments, *, fail_page=None, drop_link_at=None, issue_count=None):
    """GitHub-shaped: 100 per page, Link rel=next, issue 'comments' count."""
    pages = [comments[i:i + 100] for i in range(0, len(comments), 100)] or [[]]

    def t(url):
        if url.endswith(f"/repos/{REPO}"):
            return 200, {"full_name": REPO, "id": 1443253343}, {}
        if f"/issues/comments/" in url:
            cid = int(url.rsplit("/", 1)[1])
            c = next((x for x in comments if x["id"] == cid), None)
            return (200, c, {}) if c else (404, None, {})
        if url.endswith(f"/issues/{issue}"):
            return 200, {"number": issue, "comments": len(comments) if issue_count is None else issue_count}, {}
        if f"/issues/{issue}/comments" in url:
            n = int(url.rsplit("page=", 1)[1])
            if fail_page == n:
                return 500, None, {}
            hdr = {}
            if n < len(pages) and n != drop_link_at:
                hdr = {"link": f'<{SRC.API}/repos/{REPO}/issues/{issue}/comments?per_page=100&page={n + 1}>; rel="next"'}
            return 200, pages[n - 1], hdr
        return 404, None, {}
    return t


def _many(fa, approval_url, n, revoke_at):
    cid = int(approval_url.rsplit("-", 1)[1])
    approval = fa.source.comments[cid]
    out = [approval]
    for i in range(n):
        body = f"RESEARCH-APPROVAL-REVOKE v1 comment={cid}" if i == revoke_at else f"chatter {i}"
        out.append({**copy.deepcopy(approval), "id": 7_000_000_000 + i, "body": body,
                    "html_url": approval["html_url"].rsplit("-", 1)[0] + f"-{7_000_000_000 + i}"})
    return out


def _paged_verifier(fa, tmp_path, transport):
    return ApprovalEvidenceVerifier(GitHubCommentSource(REPO, transport=transport),
                                    revocation_ledger=tmp_path / "paged_ledger.jsonl")


def test_S2_revocation_on_page_51_is_found(world, tmp_path):
    w, fa, _ = world
    url = fa.bind_url(w["request"], w["authorization_id"])
    comments = _many(fa, url, 5_150, revoke_at=5_060)                         # page 51
    v = _paged_verifier(fa, tmp_path, _paged_transport(fa, 900, comments))
    with pytest.raises(ApprovalRejected, match="V08_REVOKED"):
        v.verify(url, _exp(w["request"], w["authorization_id"]))


def test_S2_api_failure_mid_pagination_fails_closed(world, tmp_path):
    w, fa, _ = world
    url = fa.bind_url(w["request"], w["authorization_id"])
    v = _paged_verifier(fa, tmp_path, _paged_transport(fa, 900, _many(fa, url, 450, revoke_at=None), fail_page=3))
    with pytest.raises(ApprovalRejected, match="V00_APPROVAL_SOURCE_UNAVAILABLE"):
        v.verify(url, _exp(w["request"], w["authorization_id"]))


def test_S2_truncated_listing_is_never_not_revoked(world, tmp_path):
    w, fa, _ = world
    url = fa.bind_url(w["request"], w["authorization_id"])
    comments = _many(fa, url, 350, revoke_at=None)
    v = _paged_verifier(fa, tmp_path, _paged_transport(fa, 900, comments, drop_link_at=2))   # Link vanishes early
    with pytest.raises(ApprovalRejected, match="REVOCATION_SCAN_INCOMPLETE"):
        v.verify(url, _exp(w["request"], w["authorization_id"]))
    v2 = _paged_verifier(fa, tmp_path, _paged_transport(fa, 900, comments, issue_count=999))  # count disagrees
    with pytest.raises(ApprovalRejected, match="REVOCATION_SCAN_INCOMPLETE"):
        v2.verify(url, _exp(w["request"], w["authorization_id"]))


def test_S2_page_cap_is_incomplete_not_pass(world, tmp_path, monkeypatch):
    w, fa, _ = world
    url = fa.bind_url(w["request"], w["authorization_id"])
    monkeypatch.setattr(SRC, "MAX_PAGES", 2)
    v = _paged_verifier(fa, tmp_path, _paged_transport(fa, 900, _many(fa, url, 450, revoke_at=None)))
    with pytest.raises(ApprovalRejected, match="REVOCATION_SCAN_INCOMPLETE"):
        v.verify(url, _exp(w["request"], w["authorization_id"]))


def test_S2_complete_scan_passes_and_exact_100_boundary(world, tmp_path):
    w, fa, _ = world
    url = fa.bind_url(w["request"], w["authorization_id"])
    for n in (98, 99, 199):                                                 # 99 / 100 / 200 comments
        comments = _many(fa, url, n, revoke_at=None)
        ev = _paged_verifier(fa, tmp_path, _paged_transport(fa, 900, comments)).verify(
            url, _exp(w["request"], w["authorization_id"]))
        assert ev["revocation"]["status"] == "NOT_REVOKED"


# ======================================================================== S3 production trust isolation
class _FakeProd:
    kind, transport_kind = "GITHUB_REST_API", "LIVE"

    def __init__(self, fa):
        self.fa = fa

    def get_repo(self):
        return self.fa.source.get_repo()

    def get_comment(self, cid):
        return self.fa.source.get_comment(cid)

    def list_issue_comments(self, n):
        return self.fa.source.list_issue_comments(n)


def test_S3_fake_source_impersonating_production_never_gets_production(world, tmp_path):
    w, fa, _ = world
    url = fa.bind_url(w["request"], w["authorization_id"])
    with pytest.raises(ApprovalRejected, match="V09"):
        ApprovalEvidenceVerifier(_FakeProd(fa), production=True, revocation_ledger=tmp_path / "l.jsonl")
    v = ApprovalEvidenceVerifier(_FakeProd(fa), revocation_ledger=tmp_path / "l.jsonl")
    assert v.trust == "ISOLATED_TEST"
    with pytest.raises(ApprovalRejected, match="V10_SCHEMA"):              # unknown source kind never validates
        v.verify(url, _exp(w["request"], w["authorization_id"]))


def test_S3_production_verifier_with_swapped_or_mutated_source_is_refused(world, tmp_path):
    w, fa, _ = world
    url = fa.bind_url(w["request"], w["authorization_id"])
    v = ApprovalEvidenceVerifier.for_production(revocation_ledger=tmp_path / "p.jsonl")
    assert v.production
    v.source = _FakeProd(fa)                                                # source replaced
    assert not v.production and v.trust == "ISOLATED_TEST"
    with pytest.raises(ApprovalRejected, match="V09_PRODUCTION_SOURCE_TAMPERED"):
        v.verify(url, _exp(w["request"], w["authorization_id"]))
    v2 = ApprovalEvidenceVerifier.for_production(revocation_ledger=tmp_path / "p2.jsonl")
    v2.source._transport = lambda u: (200, {}, {})                          # transport injected afterwards
    with pytest.raises(ApprovalRejected, match="V09_PRODUCTION_SOURCE_TAMPERED"):
        v2.verify(url, _exp(w["request"], w["authorization_id"]))
    v3 = ApprovalEvidenceVerifier.for_production(revocation_ledger=tmp_path / "p3.jsonl")
    v3.source.transport_kind = "LIVE"
    v3.source._transport = None
    s = GitHubCommentSource(REPO, transport=lambda u: (200, {}, {}))
    s.transport_kind = "LIVE"                                               # renamed label on an injected source
    with pytest.raises(ApprovalRejected, match="V09"):
        ApprovalEvidenceVerifier(s, production=True, revocation_ledger=tmp_path / "p4.jsonl")


def test_S3_books_are_production_unless_marked_regardless_of_path(tmp_path):
    assert not is_isolated_test_root(ROOT)
    assert not is_isolated_test_root(Path("."))  if Path(".").resolve() == ROOT else True
    copy_root = tmp_path / "copied_real_books"                              # copy of the real books elsewhere
    shutil.copytree(ROOT / "governance", copy_root / "governance")
    shutil.copytree(ROOT / "registry", copy_root / "registry")
    assert not is_isolated_test_root(copy_root)
    alias = tmp_path / "alias"
    alias.symlink_to(ROOT, target_is_directory=True)                        # path alias of the repository
    assert not is_isolated_test_root(alias)
    rel = os.path.relpath(ROOT, Path.cwd())
    assert not is_isolated_test_root(Path(rel))
    fake_marker = tmp_path / "fake_marked"
    shutil.copytree(copy_root, fake_marker)
    (fake_marker / ISOLATION_MARKER).symlink_to(ROOT / "tests/fixtures/phase4c/governance_root" / ISOLATION_MARKER)
    assert not is_isolated_test_root(fake_marker)                           # symlinked marker is not accepted
    for g in (copy_root, alias, Path(rel)):
        with pytest.raises(ValueError):
            Orchestrator(tmp_path / f"s_{g.name}", tmp_path / "o", governance_root=g,
                         approval_verifier=FixtureApprovals().verifier)


def test_S3_isolated_binding_never_usable_with_production_books(world, tmp_path):
    w, fa, svc = world
    r = svc.bind(dry_run=False, **K.bind_kwargs(w, fa.bind_url(w["request"], w["authorization_id"])))
    bound = json.loads(Path(r["bound_request_path"]).read_text())
    prod_like = tmp_path / "prod_like"                                      # same books, marker removed
    shutil.copytree(w["gov"], prod_like)
    (prod_like / ISOLATION_MARKER).unlink()
    ok, detail = BA.BindingStore(prod_like).verify_bound(bound, require_production=not is_isolated_test_root(prod_like))
    assert not ok and any("ISOLATED_TEST" in p for p in detail["problems"])


def test_S3_budget_consuming_adapter_never_runs(tmp_path):
    fa = FixtureApprovals()
    gov = tmp_path / "gov"
    shutil.copytree(A5.FIX / "governance_root", gov)
    o = Orchestrator(tmp_path / "s", tmp_path / "o", governance_root=gov, source_archive_root=A5.FIX,
                     approval_verifier=fa.verifier, clock=A5.StepClock())

    class Consuming(AD.ExecutionAdapter):
        kind, enabled, consumes_budget = "CONSUMING", True, True
        calls = 0

        def execute(self, doc, out):
            type(self).calls += 1
            raise AD.ExecutionFailed("X", "must not run")
    o.adapters["ARCHIVE_REPLAY"] = Consuming()
    rr = A5.fixture_replay_request(A5.FIX)
    tid = o.submit(rr, actor="x")["task_id"]
    fa.approve_task(o, tid)
    st = o.dispatch(tid)
    assert st["state"] == "BLOCKED" and Consuming.calls == 0
    assert st["history"][-1]["reason"] in ("APPROVAL_TRUST_NOT_PRODUCTION", "BUDGET_RESERVATION_NOT_IMPLEMENTED")


# ============================================================================== M1 expiry / timezone
def _boundary(fa, w, tmp_path, now):
    v = ApprovalEvidenceVerifier(fa.source, clock=lambda: now, revocation_ledger=tmp_path / f"b{now.timestamp()}.jsonl")
    return v


def test_M1_expiry_exact_boundary_utc_and_other_timezones(world, tmp_path):
    w, fa, _ = world
    created = datetime(2026, 11, 7, 10, 0, 0, tzinfo=timezone.utc)
    expires = created + timedelta(hours=10)
    line = T.approval_line(intent="BIND_AUTHORIZATION", request_sha256=Q.request_hash(w["request"]),
                           authorization_id=w["authorization_id"], scope=T.scope_sha256(w["request"]),
                           expires_utc=expires.strftime("%Y-%m-%dT%H:%M:%SZ"))
    url = fa.comment(line, created=created)
    e = _exp(w["request"], w["authorization_id"])
    tpe = ZoneInfo("Asia/Taipei")
    assert _boundary(fa, w, tmp_path, expires - timedelta(seconds=1)).verify(url, e)["verification"]["result"] == "PASS"
    assert _boundary(fa, w, tmp_path, (expires - timedelta(seconds=1)).astimezone(tpe)).verify(url, e)
    for now in (expires, expires.astimezone(tpe), expires + timedelta(seconds=1)):
        with pytest.raises(ApprovalRejected, match="V07_EXPIRED"):
            _boundary(fa, w, tmp_path, now).verify(url, e)
    with pytest.raises(ApprovalRejected, match="V07_CLOCK_NOT_TIMEZONE_AWARE"):
        _boundary(fa, w, tmp_path, datetime(2026, 11, 7, 12, 0, 0)).verify(url, e)
    with pytest.raises(ApprovalRejected, match="V07_CREATED_IN_FUTURE"):
        _boundary(fa, w, tmp_path, created - timedelta(hours=1)).verify(url, e)


def test_M1_validity_window_boundary(world, tmp_path):
    w, fa, _ = world
    created = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=1)
    e = _exp(w["request"], w["authorization_id"])
    for hours, ok in ((336, True), (336 + 1 / 3600, False)):
        exp = created + timedelta(hours=hours)
        url = fa.comment(T.approval_line(intent="BIND_AUTHORIZATION", request_sha256=e.request_sha256,
                                         authorization_id=e.authorization_id, scope=e.scope_sha256,
                                         expires_utc=exp.strftime("%Y-%m-%dT%H:%M:%SZ")), created=created)
        if ok:
            assert fa.verifier.verify(url, e)
        else:
            with pytest.raises(ApprovalRejected, match="V07_VALIDITY_WINDOW"):
                fa.verifier.verify(url, e)


# ============================================================================= M2 budget TOCTOU
def test_M2_budget_used_after_preliminary_validation_blocks_commit(world):
    w, fa, svc = world
    plan = svc.prepare(**K.bind_kwargs(w, fa.bind_url(w["request"], w["authorization_id"])))
    assert plan["ok"]
    ChainedLog(w["gov"] / "registry/research_authorization_uses.jsonl").append(
        {"actor": "hh4832", "authorization_id": w["authorization_id"], "reserved_at": "2026-11-07T12:00:00+08:00",
         "run_id": "20261107_120000_0000000", "scenarios": ["BASE", "LOW", "STRESS", "CAT_STRESS"]})
    with pytest.raises(BindingRejected, match="B06_BUDGET_EXHAUSTED"):
        svc._commit(plan, dry_run=False)
    assert BA.BindingStore(w["gov"]).records() == []


def test_M2_authorization_revoked_after_preliminary_validation_blocks_commit(world):
    w, fa, svc = world
    plan = svc.prepare(**K.bind_kwargs(w, fa.bind_url(w["request"], w["authorization_id"])))
    ChainedLog(w["gov"] / "governance/research_authorizations.jsonl").append(
        {"action": "REVOKE", "contract": "research-only-authorization-v1", "authorization_id": w["authorization_id"],
         "approved_by": "hh4832", "approval_ref": "https://example.test/revoke",
         "approved_at": "2026-11-07T13:00:00+08:00", "reason": "test revoke"})
    with pytest.raises(BindingRejected, match="B06_AUTHORIZATION_MISSING"):
        svc._commit(plan, dry_run=False)


def test_M2_concurrent_binding_of_two_requests_respects_budget(world):
    w, fa, svc = world
    w2 = K.another_request(w, "second")
    plans = [svc.prepare(**K.bind_kwargs(x, fa.bind_url(x["request"], x["authorization_id"]))) for x in (w, w2)]
    assert all(p["ok"] for p in plans)                                    # both looked fine in isolation
    out = []

    def go(p):
        try:
            out.append(svc._commit(p, dry_run=False)["status"])
        except BindingRejected as e:
            out.append(e.code)
    th = [threading.Thread(target=go, args=(p,)) for p in plans]
    [t.start() for t in th]
    [t.join() for t in th]
    assert sorted(out) == ["B06_AUTHORIZATION_FULLY_BOUND", "BOUND"]
    assert len(BA.BindingStore(w["gov"]).records()) == 1


# ======================================================================= M3 dispatch / retry races
def _counting_replay(tmp_path):
    fa = FixtureApprovals()
    gov = tmp_path / "gov"
    shutil.copytree(A5.FIX / "governance_root", gov)
    calls = []

    class Counting(AD.ArchiveReplayAdapter):
        def execute(self, doc, out):
            calls.append(threading.get_ident())
            return super().execute(doc, out)

    def make(worker="worker-1"):
        o = Orchestrator(tmp_path / "s", tmp_path / "o", governance_root=gov, source_archive_root=A5.FIX,
                         approval_verifier=fa.verifier, clock=A5.StepClock(), worker=worker)
        o.adapters["ARCHIVE_REPLAY"] = Counting(A5.FIX)
        return o
    o = make()
    rr = A5.fixture_replay_request(A5.FIX)
    tid = o.submit(rr, actor="x")["task_id"]
    fa.approve_task(o, tid)
    return make, tid, calls


def test_M3_concurrent_dispatch_executes_once(tmp_path, monkeypatch):
    monkeypatch.setattr(__import__("strategy_agent.evaluation.phase4c", fromlist=["x"]), "EXECUTOR_MODULES", ())
    make, tid, calls = _counting_replay(tmp_path)
    res, errs = [], []

    def go(i):
        try:
            res.append(make(worker=f"w{i % 2}").dispatch(tid))
        except Exception as e:  # noqa: BLE001
            errs.append(repr(e))
    th = [threading.Thread(target=go, args=(i,)) for i in range(6)]
    [t.start() for t in th]
    [t.join() for t in th]
    assert len(calls) == 1, calls
    final = make().dispatch(tid)
    assert final["state"] == "ARCHIVED" and len(calls) == 1
    assert sum(1 for h in final["history"] if h["to"] == "RUNNING") == 1
    assert not [e for e in errs if "IllegalTransition" not in e], errs


def test_M3_duplicate_execution_attempt_after_completion_and_replayed_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(__import__("strategy_agent.evaluation.phase4c", fromlist=["x"]), "EXECUTOR_MODULES", ())
    make, tid, calls = _counting_replay(tmp_path)
    o = make()
    assert o.dispatch(tid)["state"] == "ARCHIVED"
    for _ in range(3):
        assert make().dispatch(tid)["state"] == "ARCHIVED"
    with pytest.raises(Exception):
        o.retry(tid, actor="x", reason="must be refused")
    assert len(calls) == 1


def test_M3_replayed_running_claim_never_executes(tmp_path, monkeypatch):
    """Without the lock: a worker whose RUNNING transition replays another worker's event must not execute."""
    monkeypatch.setattr(__import__("strategy_agent.evaluation.phase4c", fromlist=["x"]), "EXECUTOR_MODULES", ())
    make, tid, calls = _counting_replay(tmp_path)
    o = make()
    o._sys(tid, "QUEUED", expect_from="APPROVED", idempotency_key=f"{tid}:QUEUED")
    stale = o.log.get(tid)                                    # what a racing worker read: QUEUED, attempt 0
    assert stale.state == "QUEUED"
    o.log.transition(tid, "RUNNING", actor="orchestrator", actor_kind="SYSTEM", worker="worker-1", lease_seconds=900,
                     expect_from="QUEUED", data={"attempt": 1, "claim": "someone-else"}, idempotency_key=f"{tid}:RUNNING:1")
    real_get, first = o.log.get, [True]

    def get(t):
        if first[0]:
            first[0] = False
            return stale
        return real_get(t)
    o.log.get = get
    out = o._dispatch_locked(tid)
    assert out.get("dispatch") == "CLAIMED_BY_ANOTHER_WORKER" and calls == []


# ======================================================================= M4 failure masking
def test_M4_ci_never_masks_failures():
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "\ndefaults:\n  run:\n    shell: bash\n" in ci                  # => bash -eo pipefail for every step
    assert '"returncode": 0' not in ci and "|| true" not in ci and "continue-on-error" not in ci


def test_M4_failed_test_subprocess_makes_acceptance_fail(tmp_path, remediation_json):
    tj = tmp_path / "t.json"
    tj.write_text(json.dumps({"returncode": 1, "summary": "1 failed"}))
    env = {k: v for k, v in os.environ.items() if k not in ("GH_TOKEN", "GITHUB_TOKEN")}
    r = subprocess.run([sys.executable, "-m", "strategy_agent.binding.acceptance", "--out-dir", str(tmp_path / "o"),
                        "--tests-json", str(tj), "--remediation-json", str(remediation_json)],
                       capture_output=True, text=True, cwd=ROOT, timeout=900, env=env)
    assert r.returncode == 1
    res = json.loads(r.stdout[r.stdout.index("{"):])
    assert res["gates"]["B1-10"] == "FAIL" and res["verdict"]["verdict"] == "NOT_ACCEPTED"


def test_M4_append_only_guard_fails_on_missing_base_ref():
    from strategy_agent.governance.append_only_guard import check
    probs = check("refs/does/not/exist", ROOT)
    assert probs and "not available" in probs[0]


# ======================================================================= M5 archive hash index
def test_M5_archive_index_covers_every_file_and_detects_tampering(tmp_path, remediation_json):
    tj = tmp_path / "t.json"
    tj.write_text(json.dumps({"returncode": 0, "summary": "ok"}))
    ctx = tmp_path / "ctx.json"
    ctx.write_text(json.dumps({"notebook": "test"}))
    out = tmp_path / "approvals/x"
    env = {k: v for k, v in os.environ.items() if k not in ("GH_TOKEN", "GITHUB_TOKEN")}
    r = subprocess.run([sys.executable, "-m", "strategy_agent.binding.acceptance", "--out-dir", str(out),
                        "--tests-json", str(tj), "--context-json", str(ctx), "--remediation-json", str(remediation_json)],
                       capture_output=True, text=True, cwd=ROOT,
                       timeout=900, env=env)
    assert r.returncode == 0, r.stderr[-2000:]
    idx = json.loads((out / BA.INDEX).read_text())
    assert set(idx["files"]) == {p.name for p in out.iterdir() if p.is_file()} - {BA.INDEX}
    assert {"notebook_context.json", "run_info.txt"} <= set(idx["files"])
    assert BA.verify_archive(out)["status"] == "PASS"
    (out / "extra.json").write_text("{}")
    assert "unlisted file: extra.json" in BA.verify_archive(out)["problems"]
    (out / "extra.json").unlink()
    p = out / "governance_checks.json"
    p.write_text(p.read_text() + " ")
    assert "altered file: governance_checks.json" in BA.verify_archive(out)["problems"]
    (out / "run_info.txt").unlink()
    assert "missing file: run_info.txt" in BA.verify_archive(out)["problems"]
    rc = subprocess.run([sys.executable, "-m", "strategy_agent.binding.acceptance", "--out-dir", str(out),
                         "--verify-archive"], capture_output=True, text=True, cwd=ROOT)
    assert rc.returncode == 1


def test_M5_missing_or_altered_binding_evidence_file_blocks(world, tmp_path):
    w, fa, svc = world
    r = svc.bind(dry_run=False, **K.bind_kwargs(w, fa.bind_url(w["request"], w["authorization_id"])))
    bound = json.loads(Path(r["bound_request_path"]).read_text())
    ev = w["gov"] / r["record"]["approval_evidence_path"]
    ev.write_text(ev.read_text().replace("hh4832", "hh4833"))
    ok, d = BA.BindingStore(w["gov"]).verify_bound(bound, require_production=False)
    assert not ok and "approval evidence file missing or altered" in d["problems"]
    ev.unlink()
    ok, d = BA.BindingStore(w["gov"]).verify_bound(bound, require_production=False)
    assert not ok
    o = Orchestrator(tmp_path / "s", tmp_path / "o", root=w["art"], governance_root=w["gov"],
                     benchmark=K.synthetic_benchmark, approval_verifier=fa.verifier)
    s = o.submit(bound, actor="x", actor_kind="TEST_FIXTURE")
    assert {c["id"]: c["status"] for c in s["governance"]["checks"]}["G11_AUTHORIZATION_BINDING"] == "FAIL"


def test_SEC_gate_requires_every_reproduction_blocked(remediation_json):
    rem = json.loads(Path(remediation_json).read_text())
    assert rem["api"] == "remediated"
    st = BA.remediation_status(rem)
    assert st["status"] == "PASS" and not st["not_blocked"] and len(st["cases"]) == 11
    bad = dict(rem, S2_revocation_on_page_51="EXPLOITABLE")
    assert BA.remediation_status(bad)["status"] == "FAIL" and BA.remediation_status(None)["status"] == "FAIL"
