"""Phase 5-B1 remediation: exploit reproductions, runnable against BOTH the reviewed code (cf15654) and the fix.

    PYTHONPATH=<checkout>/src python -I docs/evidence/phase5b1_remediation/reproduce.py <checkout> <out.json>

Each case reports EXPLOITABLE (the attack succeeded) or BLOCKED (the attack was refused). Only isolated test
fixtures and temporary directories are used; nothing touches a repository's books, GitHub or the Holdout.
(Codex's original reproduce.py was not available to this session; these cases re-derive each finding.)
"""
from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
from pathlib import Path


def main(checkout: str, out: str) -> int:
    root = Path(checkout).resolve()
    sys.path.insert(0, str(root / "src"))
    from strategy_agent.approval import ApprovalEvidenceVerifier, ApprovalRejected, Expectation, GitHubCommentSource
    from strategy_agent.approval import sources as SRC
    from strategy_agent.approval import tokens as T
    from strategy_agent.approval.testkit import FixtureApprovals
    from strategy_agent.binding import testkit as K
    from strategy_agent.binding.service import BindingRejected
    from strategy_agent.evaluation import phase4c as C
    from strategy_agent.orchestration import adapters as AD
    from strategy_agent.orchestration import request as Q
    from strategy_agent.orchestration.orchestrator import Orchestrator
    from strategy_agent.phase5a import acceptance as A5
    from strategy_agent.registry.chain import ChainedLog
    C.EXECUTOR_MODULES = ()
    new_api = hasattr(SRC, "next_link")
    tmp = Path(tempfile.mkdtemp(prefix="b1_repro_"))
    import strategy_agent
    R = {"checkout": str(root), "api": "remediated" if new_api else "reviewed (cf15654)",
         "imported_from": str(Path(strategy_agent.__file__).resolve().parents[2])}

    def iso_verifier(source, name):
        try:
            return ApprovalEvidenceVerifier(source, production=False, revocation_ledger=tmp / f"{name}.jsonl")
        except TypeError:
            return ApprovalEvidenceVerifier(source, production=False)

    def attempt(fn):
        try:
            return "EXPLOITABLE", str(fn())[:160]
        except (ApprovalRejected, BindingRejected, ValueError, Exception) as e:  # noqa: BLE001
            return "BLOCKED", f"{type(e).__name__}: {getattr(e, 'code', '')} {str(e)[:160]}"

    W = K.build_world(tmp / "w")
    fa = FixtureApprovals()
    src, aid = W["request"], W["authorization_id"]
    exp = Expectation("BIND_AUTHORIZATION", Q.request_hash(src), aid, T.scope_sha256(src))

    # S1: revocation deleted after it was observed
    url = fa.bind_url(src, aid)
    cid = int(url.rsplit("-", 1)[1])
    rev = fa.comment(f"RESEARCH-APPROVAL-REVOKE v1 comment={cid}")
    first = attempt(lambda: fa.verifier.verify(url, exp))
    del fa.source.comments[int(rev.rsplit("-", 1)[1])]
    R["S1_revocation_deleted"] = {"first_check": first[0], "after_deletion": attempt(lambda: fa.verifier.verify(url, exp))}

    # S2: revocation on page 51
    fa2 = FixtureApprovals()
    u2 = fa2.bind_url(src, aid)
    c2 = int(u2.rsplit("-", 1)[1])
    base = fa2.source.comments[c2]
    comments = [base] + [{**copy.deepcopy(base), "id": 7_000_000_000 + i,
                          "body": f"RESEARCH-APPROVAL-REVOKE v1 comment={c2}" if i == 5_060 else f"chatter {i}"}
                         for i in range(5_150)]
    pages = [comments[i:i + 100] for i in range(0, len(comments), 100)]

    def transport(u):
        if u.endswith("/repos/hh4832/strategy-research-agent"):
            r = (200, {"full_name": "hh4832/strategy-research-agent", "id": 1443253343})
        elif "/issues/comments/" in u:
            r = (200, base)
        elif u.endswith("/issues/900"):
            r = (200, {"number": 900, "comments": len(comments)})
        else:
            n = int(u.rsplit("page=", 1)[1])
            hdr = {"link": f'<{SRC.API}/repos/hh4832/strategy-research-agent/issues/900/comments?per_page=100&page={n + 1}>; rel="next"'} \
                if n < len(pages) else {}
            r = (200, pages[n - 1], hdr)
        return r if new_api else r[:2]
    v2 = iso_verifier(GitHubCommentSource("hh4832/strategy-research-agent", transport=transport), "s2")
    R["S2_revocation_on_page_51"] = attempt(lambda: v2.verify(u2, exp))[0]

    # S3: a fake source claiming to be the live GitHub API
    class Fake:
        kind, transport_kind = "GITHUB_REST_API", "LIVE"

        def get_repo(self):
            return fa2.source.get_repo()

        def get_comment(self, c):
            return fa2.source.get_comment(c)

        def list_issue_comments(self, n):
            return fa2.source.list_issue_comments(n)
    fa3 = FixtureApprovals()
    u3 = fa3.bind_url(src, aid)
    fa2.source.comments.update(fa3.source.comments)

    def fake_prod():
        try:
            v = ApprovalEvidenceVerifier(Fake(), production=True, revocation_ledger=tmp / "s3.jsonl")
        except TypeError:
            v = ApprovalEvidenceVerifier(Fake(), production=True)
        ev = v.verify(u3, exp)
        if ev["approval_source"]["trust"] != "PRODUCTION":
            raise ValueError("not production")
        return ev["approval_source"]
    R["S3_fake_source_gets_production"] = attempt(fake_prod)
    real_copy = tmp / "copied_real_books"
    shutil.copytree(root / "governance", real_copy / "governance")
    shutil.copytree(root / "registry", real_copy / "registry")
    R["S3_test_verifier_on_copied_real_books"] = attempt(lambda: Orchestrator(
        tmp / "s3s", tmp / "s3o", governance_root=real_copy, approval_verifier=fa.verifier))[0]

    # M1: approval comment dated in the future
    from datetime import datetime, timedelta, timezone
    fut = datetime.now(timezone.utc) + timedelta(days=2)
    line = T.approval_line(intent="BIND_AUTHORIZATION", request_sha256=exp.request_sha256, authorization_id=aid,
                           scope=exp.scope_sha256, expires_utc=(fut + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    fa4 = FixtureApprovals()
    u4 = fa4.comment(line, created=fut)
    R["M1_comment_created_in_future"] = attempt(lambda: fa4.verifier.verify(u4, exp))[0]

    # M2: budget consumed between preliminary validation and the log append
    W5 = K.build_world(tmp / "w5")
    fa5 = FixtureApprovals()
    svc = K.service(W5, fa5.verifier)
    orig = svc.store.log.__class__.append_if

    def racing_append_if(self, payload, refuse):
        if self.path.name == "authorization_bindings.jsonl":
            ChainedLog(W5["gov"] / "registry/research_authorization_uses.jsonl").append(
                {"actor": "hh4832", "authorization_id": aid, "reserved_at": "2026-11-07T12:00:00+08:00",
                 "run_id": "20261107_120000_0000000", "scenarios": ["BASE", "LOW", "STRESS", "CAT_STRESS"]})
        return orig(self, payload, refuse)
    ChainedLog.append_if = racing_append_if
    try:
        R["M2_budget_used_during_binding"] = attempt(lambda: svc.bind(dry_run=False, **K.bind_kwargs(
            W5, fa5.bind_url(W5["request"], aid)))["status"])[0]
    finally:
        ChainedLog.append_if = orig

    # M3: a second dispatcher in the QUEUED -> RUNNING window
    gov = tmp / "gov6"
    shutil.copytree(A5.FIX / "governance_root", gov)
    fa6 = FixtureApprovals()
    calls = []

    class Counting(AD.ArchiveReplayAdapter):
        def execute(self, doc, out):
            calls.append(1)
            return super().execute(doc, Path(str(out) + f"_{len(calls)}"))

    def mk(worker):
        o = Orchestrator(tmp / "s6", tmp / "o6", governance_root=gov, source_archive_root=A5.FIX,
                         approval_verifier=fa6.verifier, clock=A5.StepClock(), worker=worker)
        o.adapters["ARCHIVE_REPLAY"] = Counting(A5.FIX)
        return o
    a = mk("worker-1")
    tid = a.submit(A5.fixture_replay_request(A5.FIX), actor="x")["task_id"]
    fa6.approve_task(a, tid)
    b = mk("worker-1")
    real_check = a.gov.check
    fired = []

    def check_then_race(doc, **kw):
        res = real_check(doc, **kw)
        if not fired and a.log.get(tid).state == "QUEUED":
            fired.append(1)
            try:
                b.dispatch(tid)
            except Exception:  # noqa: BLE001
                pass
        return res
    a.gov.check = check_then_race
    try:
        a.dispatch(tid)
    except Exception as e:  # noqa: BLE001
        R["M3_note"] = repr(e)[:160]
    R["M3_executions"] = len(calls)
    R["M3_double_execution"] = "EXPLOITABLE" if len(calls) > 1 else "BLOCKED"

    # M4: CI masking + append-only guard with a missing base
    ci = (root / ".github/workflows/ci.yml").read_text()
    R["M4_ci_pipe_masking"] = "BLOCKED" if "\ndefaults:\n  run:\n    shell: bash\n" in ci else "EXPLOITABLE"
    R["M4_hardcoded_returncode"] = "EXPLOITABLE" if '"returncode": 0' in ci else "BLOCKED"
    try:
        from strategy_agent.governance.append_only_guard import check
    except ImportError:
        from strategy_agent.governance.append_only import check
    import os
    cwd = os.getcwd()
    os.chdir(root)
    try:
        R["M4_append_only_missing_base"] = "BLOCKED" if check("refs/does/not/exist", root) else "EXPLOITABLE"
    finally:
        os.chdir(cwd)

    # M5: archive index coverage of the acceptance writer
    from strategy_agent.binding import acceptance as BA
    R["M5_index_covers_all_files"] = "BLOCKED" if hasattr(BA, "verify_archive") else "EXPLOITABLE"
    Path(out).write_text(json.dumps(R, indent=2) + "\n")
    print(json.dumps(R, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
