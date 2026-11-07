"""Phase 5-B1 trusted human approval: GitHub-comment approval evidence, verified against pinned identities."""
from .sources import GitHubCommentSource, SourceUnavailable, TestApprovalSource  # noqa: F401
from .tokens import approval_line, parse_approval, scope_sha256  # noqa: F401
from .verifier import ApprovalEvidenceVerifier, ApprovalRejected, Expectation, evidence_sha256  # noqa: F401


def production_verifier(repo: str = "hh4832/strategy-research-agent", **kw) -> ApprovalEvidenceVerifier:
    """The only verifier a production path may use: live GitHub REST API, token from the environment."""
    return ApprovalEvidenceVerifier.for_production(repo, **kw)
