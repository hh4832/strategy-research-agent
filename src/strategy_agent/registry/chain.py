"""Hash-chained, append-only JSONL log.

Every line is one JSON object carrying ``seq``, ``prev_sha256`` and
``record_sha256`` (sha256 of the canonical JSON of the line without
``record_sha256``). Editing, deleting, inserting or reordering any line breaks
the chain and ``verify()`` fails. The process can only append; there is no
update or delete API.

Tamper-evidence, not tamper-proofing: someone able to rewrite the whole file
could recompute the chain. That case is caught in CI by
``strategy_agent.governance.append_only`` (the new file must start with the
exact bytes of the base-branch file). That CI check only blocks a merge if it is
made a required status check; CODEOWNERS lists the intended reviewer, but GitHub does NOT enforce it: main has no branch protection or ruleset (verified 2026-11-03).
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

GENESIS = "0" * 64


class ChainError(RuntimeError):
    pass


class ConditionFailed(RuntimeError):
    """append_if refused: the guarded condition did not hold under the lock."""


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def record_hash(rec: dict) -> str:
    body = {k: v for k, v in rec.items() if k != "record_sha256"}
    return hashlib.sha256(canonical(body)).hexdigest()


class ChainedLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    @contextmanager
    def _locked(self) -> Iterator[Any]:
        with open(self.path, "a+", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield fh
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def read(self) -> list[dict]:
        out = []
        with open(self.path, encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                if not line.strip():
                    raise ChainError(f"{self.path}:{n}: blank line")
                out.append(json.loads(line))
        return out

    def verify(self) -> list[dict]:
        recs = self.read()
        prev = GENESIS
        for i, r in enumerate(recs):
            if r.get("seq") != i:
                raise ChainError(f"{self.path}: seq {r.get('seq')} at position {i}")
            if r.get("prev_sha256") != prev:
                raise ChainError(f"{self.path}: broken link at seq {i}")
            if r.get("record_sha256") != record_hash(r):
                raise ChainError(f"{self.path}: content hash mismatch at seq {i}")
            prev = r["record_sha256"]
        return recs

    def append(self, payload: dict) -> dict:
        rec = self.append_if(payload, lambda _records: None)
        assert rec is not None
        return rec

    def append_if(self, payload: dict, refuse: Callable[[list[dict]], str | None]) -> dict | None:
        """Atomic compare-and-append.

        ``refuse(records)`` runs while the exclusive file lock is held and sees
        the verified log; if it returns a reason, nothing is written and
        ``ConditionFailed`` is raised. Two processes can therefore never both
        pass the same check (e.g. two holdout reservations for one family).
        """
        if any(k in payload for k in ("seq", "prev_sha256", "record_sha256")):
            raise ChainError("chain fields are assigned by the log")
        with self._locked() as fh:
            fh.seek(0)
            lines = [ln for ln in fh.read().splitlines() if ln.strip()]
            recs = [json.loads(ln) for ln in lines]
            # verify before extending so a corrupted log is never silently continued
            prev = GENESIS
            for i, r in enumerate(recs):
                if r.get("seq") != i or r.get("prev_sha256") != prev or r.get("record_sha256") != record_hash(r):
                    raise ChainError(f"{self.path}: refusing to append to a corrupted log (seq {i})")
                prev = r["record_sha256"]
            reason = refuse(recs)
            if reason:
                raise ConditionFailed(reason)
            rec = dict(payload, seq=len(recs), prev_sha256=prev)
            rec["record_sha256"] = record_hash(rec)
            fh.seek(0, os.SEEK_END)
            fh.write(canonical(rec).decode("utf-8") + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return rec

    def head(self) -> str:
        recs = self.verify()
        return recs[-1]["record_sha256"] if recs else GENESIS
