"""Approval evidence sources.

``GitHubCommentSource``  the only production source: GitHub REST API over HTTPS with a token taken from the
                         environment (GH_TOKEN / GITHUB_TOKEN, e.g. a Colab Secret). The token is never logged,
                         returned or written. ``transport`` may be injected for unit tests; an injected transport is
                         reported as ``transport="INJECTED"`` and is never production evidence.
``TestApprovalSource``   isolated tests only (kind TEST_FIXTURE); refused by a production verifier.

Everything a source returns is untrusted input; the verifier re-checks every field.
"""
from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request

API = "https://api.github.com"


class SourceUnavailable(RuntimeError):
    def __init__(self, code: str, message: str = ""):
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


class NotFound(SourceUnavailable):
    def __init__(self, what: str):
        super().__init__("EVIDENCE_NOT_FOUND", what)


def _live_transport(url: str, token: str) -> tuple[int, object]:
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}",
                                               "X-GitHub-Api-Version": "2022-11-28",
                                               "User-Agent": "strategy-research-agent-approval-verifier"})
    cafile = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    ctx = ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as r:      # noqa: S310 - fixed https host
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise SourceUnavailable("GITHUB_API_UNAVAILABLE", type(e).__name__) from None


class GitHubCommentSource:
    kind = "GITHUB_REST_API"

    def __init__(self, repo: str, *, token: str | None = None, transport=None):
        self.repo = repo
        self._token = token if token is not None else (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or "")
        self._transport = transport
        self.transport_kind = "INJECTED" if transport is not None else "LIVE"

    def __repr__(self):                       # never expose the token
        return f"GitHubCommentSource(repo={self.repo!r}, transport={self.transport_kind})"

    def _get(self, path: str):
        if self._transport is None and not self._token:
            raise SourceUnavailable("GITHUB_TOKEN_MISSING", "set GH_TOKEN/GITHUB_TOKEN (e.g. Colab Secret); fail closed")
        url = API + path
        status, body = (self._transport(url) if self._transport is not None else _live_transport(url, self._token))
        if status == 404:
            raise NotFound(path)
        if status in (401, 403):
            raise SourceUnavailable("GITHUB_API_FORBIDDEN", f"HTTP {status}")
        if status != 200 or body is None:
            raise SourceUnavailable("GITHUB_API_ERROR", f"HTTP {status}")
        return body

    def get_repo(self) -> dict:
        return self._get(f"/repos/{self.repo}")

    def get_comment(self, comment_id: int) -> dict:
        return self._get(f"/repos/{self.repo}/issues/comments/{int(comment_id)}")

    def list_issue_comments(self, issue_number: int) -> list[dict]:
        out, page = [], 1
        while True:
            batch = self._get(f"/repos/{self.repo}/issues/{int(issue_number)}/comments?per_page=100&page={page}")
            if not isinstance(batch, list):
                raise SourceUnavailable("GITHUB_API_ERROR", "comment list is not a list")
            out += batch
            if len(batch) < 100 or page >= 50:
                return out
            page += 1


class TestApprovalSource:
    """In-memory GitHub-shaped payloads for isolated tests. Kind TEST_FIXTURE: never production evidence."""
    kind = "TEST_FIXTURE"
    transport_kind = "TEST_FIXTURE"
    __test__ = False                          # not a pytest class

    def __init__(self, repo: dict, comments: dict | None = None, *, available: bool = True):
        self.repo_meta, self.comments, self.available = repo, dict(comments or {}), available

    def _check(self):
        if not self.available:
            raise SourceUnavailable("GITHUB_API_UNAVAILABLE", "test source offline")

    def get_repo(self) -> dict:
        self._check()
        return self.repo_meta

    def get_comment(self, comment_id: int) -> dict:
        self._check()
        if int(comment_id) not in self.comments:
            raise NotFound(f"comment {comment_id}")
        return self.comments[int(comment_id)]

    def list_issue_comments(self, issue_number: int) -> list[dict]:
        self._check()
        return [c for c in self.comments.values() if str(c.get("issue_url", "")).endswith(f"/issues/{issue_number}")]
