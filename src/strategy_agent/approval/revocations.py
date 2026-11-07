"""Append-only revocation ledger (S1).

GitHub only shows the CURRENT comments: a revocation that is later deleted or edited disappears from the API. So the
first time a verifier observes a valid revocation it is persisted here (hash-chained ChainedLog) and from then on the
revoked approval comment is rejected forever, whatever GitHub returns later. Nothing ever removes or overrides a
ledger entry. A ledger that fails chain verification makes every verification fail closed.

Limitation (external source): a revocation that was deleted BEFORE any verifier observed it cannot be recovered
from GitHub; ``observed_at`` records when this system first saw each revocation.
"""
from __future__ import annotations

from pathlib import Path

from strategy_agent.registry.chain import ChainedLog, ConditionFailed


class LedgerInvalid(RuntimeError):
    pass


class RevocationLedger:
    def __init__(self, path: Path):
        self.path = Path(path)

    def records(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            return ChainedLog(self.path).verify()
        except Exception as e:  # noqa: BLE001 - any integrity problem is fatal for approvals
            raise LedgerInvalid(f"revocation ledger failed verification: {e}") from None

    def revoked(self, approval_comment_id: int) -> dict | None:
        return next((r for r in self.records() if r["approval_comment_id"] == int(approval_comment_id)), None)

    def record(self, *, approval_comment_id: int, revoking_comment_id: int, revoker_login: str, revoker_id: int,
               revoke_body_sha256: str, observed_at: str, source: str, trust: str) -> dict:
        self.records()                                       # integrity first
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"kind": "APPROVAL_REVOCATION_OBSERVED", "approval_comment_id": int(approval_comment_id),
                   "revoking_comment_id": int(revoking_comment_id), "revoker_login": revoker_login,
                   "revoker_id": int(revoker_id), "revoke_body_sha256": revoke_body_sha256,
                   "observed_at": observed_at, "source": source, "trust": trust,
                   "note": "permanent: later deletion/edit of the revoking comment on GitHub does not reinstate the approval"}

        def refuse(recs):
            return "ALREADY_REVOKED" if any(r["approval_comment_id"] == int(approval_comment_id) for r in recs) else None
        try:
            return ChainedLog(self.path).append_if(payload, refuse)
        except ConditionFailed:
            return self.revoked(approval_comment_id)
