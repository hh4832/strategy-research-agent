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
import re
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


def _live_transport(url: str, token: str) -> tuple[int, object, dict]:
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}",
                                               "X-GitHub-Api-Version": "2022-11-28",
                                               "User-Agent": "strategy-research-agent-approval-verifier"})
    cafile = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    ctx = ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as r:      # noqa: S310 - fixed https host
            return r.status, json.loads(r.read().decode("utf-8")), {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        return e.code, None, {}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise SourceUnavailable("GITHUB_API_UNAVAILABLE", type(e).__name__) from None


_LIVE_TRANSPORT = _live_transport
_NEXT = re.compile(r'<([^>]+)>\s*;\s*rel="next"')
MAX_PAGES = 1000                       # safety stop: reaching it is INCOMPLETE (fail closed), never a pass


def next_link(headers: dict) -> str | None:
    """GitHub's own pagination end condition: the Link header has (or lacks) rel="next"."""
    m = _NEXT.search((headers or {}).get("link", ""))
    return m.group(1) if m else None


class GitHubCommentSource:
    kind = "GITHUB_REST_API"

    def __init__(self, repo: str, *, token: str | None = None, transport=None):
        self.repo = repo
        self._token = token if token is not None else (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or "")
        self._transport = transport
        self.transport_kind = "INJECTED" if transport is not None else "LIVE"

    def live_untampered(self) -> bool:
        """True only for an unmodified live source: no injected transport, the module's own live transport."""
        return (self._transport is None and self.transport_kind == "LIVE" and type(self) is GitHubCommentSource
                and globals().get("_live_transport") is _LIVE_TRANSPORT)

    def __repr__(self):                       # never expose the token
        return f"GitHubCommentSource(repo={self.repo!r}, transport={self.transport_kind})"

    def _fetch(self, url: str):
        if self._transport is None and not self._token:
            raise SourceUnavailable("GITHUB_TOKEN_MISSING", "set GH_TOKEN/GITHUB_TOKEN (e.g. Colab Secret); fail closed")
        if not url.startswith(API + "/"):
            raise SourceUnavailable("GITHUB_API_ERROR", "refusing to follow a non-api.github.com URL")
        res = self._transport(url) if self._transport is not None else _live_transport(url, self._token)
        status, body, headers = (res + ({},))[:3] if len(res) == 2 else res
        if status == 404:
            raise NotFound(url[len(API):])
        if status in (401, 403):
            raise SourceUnavailable("GITHUB_API_FORBIDDEN", f"HTTP {status}")
        if status != 200 or body is None:
            raise SourceUnavailable("GITHUB_API_ERROR", f"HTTP {status}")
        return body, headers or {}

    def _get(self, path: str):
        return self._fetch(API + path)[0]

    def get_repo(self) -> dict:
        return self._get(f"/repos/{self.repo}")

    def get_comment(self, comment_id: int) -> dict:
        return self._get(f"/repos/{self.repo}/issues/comments/{int(comment_id)}")

    def list_issue_comments(self, issue_number: int) -> list[dict]:
        """ALL comments of the thread, or SourceUnavailable("REVOCATION_SCAN_INCOMPLETE"). Completion is decided
        only by GitHub's Link header (no rel="next"); the result is cross-checked against the issue's comment
        count read BEFORE the scan, ids must be unique, and every page must be a list. Never a partial result."""
        issue = self._get(f"/repos/{self.repo}/issues/{int(issue_number)}")
        expected = issue.get("comments") if isinstance(issue, dict) else None
        if not isinstance(expected, int) or expected < 0:
            raise SourceUnavailable("REVOCATION_SCAN_INCOMPLETE", "issue comment count unavailable")
        url = f"{API}/repos/{self.repo}/issues/{int(issue_number)}/comments?per_page=100&page=1"
        out, seen = [], set()
        for _ in range(MAX_PAGES):
            batch, headers = self._fetch(url)
            if not isinstance(batch, list):
                raise SourceUnavailable("REVOCATION_SCAN_INCOMPLETE", "comment page is not a list")
            for c in batch:
                cid = c.get("id") if isinstance(c, dict) else None
                if not isinstance(cid, int) or cid in seen:
                    raise SourceUnavailable("REVOCATION_SCAN_INCOMPLETE", "malformed or duplicated comment id")
                seen.add(cid)
                out.append(c)
            url = next_link(headers)
            if url is None:
                if len(out) < expected:
                    raise SourceUnavailable("REVOCATION_SCAN_INCOMPLETE",
                                            f"scanned {len(out)} comments, thread reports {expected}")
                return out
        raise SourceUnavailable("REVOCATION_SCAN_INCOMPLETE", f"more than {MAX_PAGES} pages")


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
