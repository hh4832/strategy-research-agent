"""SYNTHETIC_FIXTURE approvals for ISOLATED tests (TestApprovalSource, kind TEST_FIXTURE, trust ISOLATED_TEST).

A verifier built here is ``production=False``: the repository's governance books refuse it (Orchestrator and
AuthorizationBindingService raise), its evidence is labelled ISOLATED_TEST, and a budget-consuming adapter refuses
ISOLATED_TEST approvals. It exists so tests exercise the real verifier logic on GitHub-shaped payloads.
"""
from __future__ import annotations

import itertools
from pathlib import Path
from datetime import datetime, timedelta, timezone

from .sources import TestApprovalSource
from .tokens import approval_line, scope_sha256
from .verifier import ApprovalEvidenceVerifier

REPO_FULL = "hh4832/strategy-research-agent"
REPO = {"full_name": REPO_FULL, "id": 1443253343}
LEAD = ("hh4832", 269247281)


def _z(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class FixtureApprovals:
    __test__ = False

    def __init__(self, *, issue: int = 900, clock=None):
        self.source = TestApprovalSource(dict(REPO))
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        import tempfile  # noqa: PLC0415
        self.ledger_path = Path(tempfile.mkdtemp(prefix="isolated_revocations_")) / "approval_revocations.jsonl"
        self.verifier = ApprovalEvidenceVerifier(self.source, clock=self.clock, revocation_ledger=self.ledger_path)
        self.issue, self._ids = issue, itertools.count(9_000_000_001)

    def comment(self, body: str, *, issue: int | None = None, login: str = LEAD[0], uid: int = LEAD[1],
                association: str = "OWNER", user_type: str = "User", app=None, created: datetime | None = None,
                edited: bool = False, comment_id: int | None = None, kind: str = "pull") -> str:
        issue = issue or self.issue
        cid = comment_id or next(self._ids)
        created = created or (self.clock() - timedelta(minutes=1))
        url = f"https://github.com/{REPO_FULL}/{kind}/{issue}#issuecomment-{cid}"
        self.source.comments[cid] = {
            "id": cid, "html_url": url, "issue_url": f"https://api.github.com/repos/{REPO_FULL}/issues/{issue}",
            "user": {"login": login, "id": uid, "type": user_type}, "author_association": association,
            "performed_via_github_app": app, "created_at": _z(created),
            "updated_at": _z(created + timedelta(minutes=5)) if edited else _z(created), "body": body}
        return url

    def line(self, request: dict, *, intent: str, authorization_id: str | None = None, task_id: str | None = None,
             request_sha256: str | None = None, hours: int = 48) -> str:
        from strategy_agent.orchestration.request import request_hash  # noqa: PLC0415
        aref = request.get("authorization_ref")
        aid = authorization_id or (aref["authorization_id"] if isinstance(aref, dict) else "RA-UNSET")
        return approval_line(intent=intent, request_sha256=request_sha256 or request_hash(request),
                             authorization_id=aid, scope=scope_sha256(request), task_id=task_id,
                             expires_utc=_z(self.clock() + timedelta(hours=hours)))

    def approve_task(self, orch, task_id: str, **comment_kw) -> dict:
        doc = orch.request_of(task_id)
        url = self.comment("SYNTHETIC_FIXTURE approval\n" + self.line(doc, intent="APPROVE_TASK", task_id=task_id),
                           **comment_kw)
        return orch.approve(task_id, evidence_ref=url)

    def bind_url(self, source_request: dict, authorization_id: str, **comment_kw) -> str:
        return self.comment("SYNTHETIC_FIXTURE binding approval\n" +
                            self.line(source_request, intent="BIND_AUTHORIZATION", authorization_id=authorization_id),
                            **comment_kw)
