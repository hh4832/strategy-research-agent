"""ApprovalEvidenceVerifier: turns a GitHub comment URL into verified, schema-valid approval evidence, or rejects it.

Identity comes from the GitHub-authenticated comment author (login AND immutable numeric id), never from a CLI
string. Checks (any failure => ApprovalRejected, nothing accepted):

V01 URL is exactly https://github.com/<pinned repo>/(pull|issues)/<n>#issuecomment-<id>
V02 repository returned by the API has the pinned full_name AND numeric id
V03 comment exists (deleted => EVIDENCE_NOT_FOUND), its id / html_url / issue_url match V01
V04 author is a GitHub ``User`` whose login AND id are in approval_identities.json and whose login is a research
    lead in approvers.json; author_association is OWNER / MEMBER / COLLABORATOR; and the comment was NOT posted
    through a GitHub App (``performed_via_github_app`` is null) -- AI agents (Claude, Codex) act through Apps
    and therefore cannot produce approval evidence even though GitHub shows the user as author
V05 comment was never edited (updated_at == created_at); an edit voids the evidence (cannot re-verify the original)
V06 body has exactly one approval line; decision APPROVE; intent / request / authorization / scope / task all equal
    the expectation computed from the CURRENT request
V07 not expired (now < expires), expires after creation and within the maximum validity window
V08 not revoked by any allow-listed author anywhere on the same issue / PR thread
V09 production: source is GITHUB_REST_API with a LIVE transport (TEST_FIXTURE / injected transports are refused)
GitHub API unavailable / forbidden / no token => fail closed (APPROVAL_SOURCE_UNAVAILABLE).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jsonschema import Draft202012Validator

from . import tokens as T
from .sources import NotFound, SourceUnavailable

ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = ROOT / "schemas/RESEARCH_APPROVAL_EVIDENCE_SCHEMA.json"
VERSION = "1.0.0"
NAME = "strategy_agent.approval.verifier"


class ApprovalRejected(RuntimeError):
    def __init__(self, code: str, message: str, record: dict | None = None):
        super().__init__(f"{code}: {message}")
        self.code, self.record = code, record


@dataclass(frozen=True)
class Expectation:
    intent: str
    request_sha256: str
    authorization_id: str
    scope_sha256: str
    task_id: str | None = None

    @classmethod
    def for_request(cls, request: dict, *, intent: str, task_id: str | None = None) -> "Expectation":
        from strategy_agent.orchestration.request import request_hash  # noqa: PLC0415
        aref = request.get("authorization_ref")
        aid = aref["authorization_id"] if isinstance(aref, dict) else None
        return cls(intent, request_hash(request), aid or "", T.scope_sha256(request), task_id)


def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def evidence_sha256(record: dict) -> str:
    return hashlib.sha256(T.canonical(record)).hexdigest()


def _ts(s) -> datetime:
    if not isinstance(s, str):
        raise ValueError("timestamp not a string")
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ApprovalEvidenceVerifier:
    def __init__(self, source, *, identities_path: Path | None = None, approvers_path: Path | None = None,
                 production: bool = True, clock=None):
        gov = ROOT / "governance"
        self.identities = json.loads(Path(identities_path or gov / "approval_identities.json").read_text(encoding="utf-8"))
        self.leads = set(json.loads(Path(approvers_path or gov / "approvers.json").read_text(encoding="utf-8"))
                         .get("research_leads", []))
        self.source, self.production = source, production
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        repo = self.identities["repository"]["full_name"]
        self.url_re = re.compile(r"^https://github\.com/" + re.escape(repo) +
                                 r"/(?P<kind>pull|issues)/(?P<issue>[0-9]{1,10})#issuecomment-(?P<comment>[0-9]{1,20})$")
        if production and (getattr(source, "kind", None) != "GITHUB_REST_API" or
                           getattr(source, "transport_kind", None) != "LIVE"):
            raise ApprovalRejected("V09_PRODUCTION_SOURCE", "production verification requires the live GitHub REST API "
                                   f"source (got {getattr(source, 'kind', None)}/{getattr(source, 'transport_kind', None)})")

    @property
    def trust(self) -> str:
        return "PRODUCTION" if self.production else "ISOLATED_TEST"

    # --------------------------------------------------------------------------------------------- verify
    def verify(self, url: str, expect: Expectation, *, check_expiry: bool = True) -> dict:
        now = self.clock()
        checks: dict = {}
        rec = {"evidence_version": VERSION, "evidence_id": None,
               "approval_source": {"kind": getattr(self.source, "kind", "UNKNOWN"),
                                   "transport": getattr(self.source, "transport_kind", "UNKNOWN"), "trust": self.trust},
               "repository": dict(self.identities["repository"]), "author": None,
               "comment": {"url": url if isinstance(url, str) else None},
               "intent": expect.intent,
               "subject": {"request_sha256": expect.request_sha256, "task_id": expect.task_id,
                           "authorization_id": expect.authorization_id, "scope_sha256": expect.scope_sha256},
               "decision": None, "expires_at": None, "revocation": {"status": "NOT_CHECKED"},
               "verified_at": _iso(now), "verifier": {"name": NAME, "version": VERSION},
               "verification": {"result": "REJECTED", "code": None, "checks": checks}}

        def fail(code, msg):
            checks[code.split("_")[0]] = "FAIL"
            rec["verification"]["code"] = code
            rec["verification"]["message"] = msg[:300]
            raise ApprovalRejected(code, msg, rec)

        try:
            m = self.url_re.fullmatch(url) if isinstance(url, str) else None
            if not m:
                fail("V01_UNTRUSTED_URL", "evidence must be a comment URL on the pinned repository")
            checks["V01"] = "PASS"
            cid, issue = int(m["comment"]), int(m["issue"])
            rec["evidence_id"] = f"AE-GH-{cid}"
            repo = self.source.get_repo()
            pin = self.identities["repository"]
            if not isinstance(repo, dict) or repo.get("full_name") != pin["full_name"] or repo.get("id") != pin["id"]:
                fail("V02_REPOSITORY_IDENTITY", "repository full_name/id differ from the pinned identity")
            checks["V02"] = "PASS"
            try:
                c = self.source.get_comment(cid)
            except NotFound:
                fail("V03_EVIDENCE_NOT_FOUND", "comment does not exist (deleted or wrong repository)")
            api_issue = f"https://api.github.com/repos/{pin['full_name']}/issues/{issue}"
            if not isinstance(c, dict) or c.get("id") != cid or c.get("issue_url") != api_issue or \
                    c.get("html_url") != url:
                fail("V03_COMMENT_IDENTITY", "comment id / issue / html_url do not match the evidence URL")
            checks["V03"] = "PASS"
            u = c.get("user") or {}
            login, uid = u.get("login"), u.get("id")
            rec["author"] = {"login": login, "id": uid, "type": u.get("type"),
                             "author_association": c.get("author_association")}
            known = self.identities["github_accounts"]
            if u.get("type") != "User" or login not in known or known.get(login) != uid or login not in self.leads:
                fail("V04_AUTHOR_NOT_ALLOWED", f"author {login!r}/{uid!r} is not an allow-listed research lead identity")
            app = c.get("performed_via_github_app")
            rec["author"]["performed_via_github_app"] = (app or {}).get("slug") if isinstance(app, dict) else app
            if app is not None:
                fail("V04_POSTED_VIA_APP", f"comment was posted through GitHub App {rec['author']['performed_via_github_app']!r}"
                     " (e.g. an AI agent acting for the user); approvals must be written by the human directly")
            if c.get("author_association") not in self.identities["allowed_author_associations"]:
                fail("V04_AUTHOR_ASSOCIATION", f"author_association {c.get('author_association')!r} not allowed")
            checks["V04"] = "PASS"
            created, updated = _ts(c.get("created_at")), _ts(c.get("updated_at"))
            body = c.get("body")
            rec["comment"] = {"id": cid, "url": url, "issue_number": issue, "created_at": _iso(created),
                              "updated_at": _iso(updated), "edited": updated != created,
                              "body_sha256": hashlib.sha256((body or "").encode("utf-8")).hexdigest(), "token_line": None}
            if updated != created:
                fail("V05_EDITED", "comment was edited after creation; original approval cannot be re-verified")
            checks["V05"] = "PASS"
            try:
                tok = T.parse_approval(body)
            except T.TokenError as e:
                fail("V06_" + e.code, str(e))
            rec["comment"]["token_line"] = tok.line
            rec["decision"], rec["expires_at"] = "APPROVE", _iso(tok.expires)
            want = {"intent": expect.intent, "request": expect.request_sha256, "authorization": expect.authorization_id,
                    "scope": expect.scope_sha256, "task": expect.task_id}
            got = {"intent": tok.intent, "request": tok.request, "authorization": tok.authorization, "scope": tok.scope,
                   "task": tok.task}
            diff = sorted(k for k in want if want[k] != got[k])
            if diff:
                fail("V06_SUBJECT_MISMATCH", f"approval does not match the current request on {diff}")
            checks["V06"] = "PASS"
            maxh = int(self.identities.get("max_approval_validity_hours", 336))
            if not created < tok.expires <= created + timedelta(hours=maxh):
                fail("V07_VALIDITY_WINDOW", f"expires must be after creation and within {maxh} h")
            if check_expiry and now >= tok.expires:
                fail("V07_EXPIRED", f"approval expired at {_iso(tok.expires)}")
            checks["V07"] = "PASS"
            revoked = []
            for o in self.source.list_issue_comments(issue):
                ou = (o or {}).get("user") or {}
                if ou.get("type") == "User" and known.get(ou.get("login")) == ou.get("id") and \
                        ou.get("login") in self.leads and cid in T.revocations((o or {}).get("body")):
                    revoked.append(o.get("id"))
            rec["revocation"] = {"status": "REVOKED" if revoked else "NOT_REVOKED", "revoked_by_comments": revoked}
            if revoked:
                fail("V08_REVOKED", f"revoked by comment(s) {revoked}")
            checks["V08"] = "PASS"
            checks["V09"] = "PASS" if self.production else "NOT_APPLICABLE"
        except SourceUnavailable as e:
            if isinstance(e, NotFound):
                fail("V03_EVIDENCE_NOT_FOUND", str(e))
            fail("V00_APPROVAL_SOURCE_UNAVAILABLE", f"{e.code}: fail closed")
        except (ValueError, TypeError, KeyError, AttributeError) as e:
            fail("V00_UNTRUSTED_PAYLOAD", f"{type(e).__name__}: {e}")
        rec["verification"] = {"result": "PASS", "code": None, "checks": checks}
        errs = [e.message for e in Draft202012Validator(schema()).iter_errors(rec)]
        if errs:
            rec["verification"] = {"result": "REJECTED", "code": "V10_SCHEMA", "checks": checks, "message": str(errs)[:300]}
            raise ApprovalRejected("V10_SCHEMA", str(errs)[:300], rec)
        return rec
