"""Binding registry (append-only, hash-chained) and read-only verification of a bound request.

Layout under a governance root:
  registry/authorization_bindings.jsonl          ChainedLog of binding records (AUTHORIZATION_BINDING_SCHEMA)
  registry/bound_requests/<bound_sha256>.json    immutable derived requests (O_EXCL, content-addressed)
  registry/approval_evidence/<evidence_sha>.json immutable verified approval evidence (O_EXCL, content-addressed)

A binding is valid ONLY if its record is in the log: a crash between the immutable file writes and the log append
leaves content-addressed orphans that nothing can use.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from strategy_agent.registry.chain import ChainedLog

ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = ROOT / "schemas/AUTHORIZATION_BINDING_SCHEMA.json"
LOG = "registry/authorization_bindings.jsonl"
BOUND = "registry/bound_requests"
EVIDENCE = "registry/approval_evidence"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def dumps(obj) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def write_immutable(path: Path, data: bytes) -> str:
    """O_EXCL. An existing file is accepted only if byte-identical (idempotent retry); it is never overwritten."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "xb") as fh:
            fh.write(data)
    except FileExistsError:
        if path.read_bytes() != data:
            raise FileExistsError(f"{path} exists with different content; never overwritten") from None
    return _sha(data)


class BindingStore:
    def __init__(self, governance_root: Path):
        self.root = Path(governance_root)
        self.path = self.root / LOG

    @property
    def log(self) -> ChainedLog:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return ChainedLog(self.path)

    def records(self) -> list[dict]:
        return ChainedLog(self.path).verify() if self.path.exists() else []

    def for_bound(self, bound_sha: str) -> dict | None:
        return next((r for r in self.records() if r["bound_request_sha256"] == bound_sha), None)

    def for_source(self, source_sha: str) -> list[dict]:
        return [r for r in self.records() if r["source_request_sha256"] == source_sha]

    def count_for_authorization(self, authorization_id: str) -> int:
        return sum(1 for r in self.records() if r["authorization_id"] == authorization_id)

    def verify_bound(self, doc: dict, *, require_production: bool) -> tuple[bool, dict]:
        """Is ``doc`` exactly a registered bound request whose stored artefacts are intact?"""
        from strategy_agent.orchestration.request import request_hash  # noqa: PLC0415
        h = request_hash(doc)
        try:
            rec = self.for_bound(h)
        except Exception as e:  # noqa: BLE001 - a broken chain is a failed check, never a pass
            return False, {"error": f"binding log invalid: {e}"}
        if rec is None:
            return False, {"error": "no binding record for this request hash (unbound, or modified after binding)",
                           "request_sha256": h}
        problems = [e.message for e in Draft202012Validator(schema()).iter_errors(_body(rec))]
        bound_p, ev_p = self.root / rec["bound_request_path"], self.root / rec["approval_evidence_path"]
        if not bound_p.is_file() or _sha(bound_p.read_bytes()) != rec["bound_request_file_sha256"]:
            problems.append("bound request file missing or altered")
        elif json.loads(bound_p.read_text(encoding="utf-8")) != doc:
            problems.append("request differs from the registered bound request")
        if not ev_p.is_file() or _sha(ev_p.read_bytes()) != rec["approval_evidence_sha256"]:
            problems.append("approval evidence file missing or altered")
        aref = doc.get("authorization_ref")
        if not isinstance(aref, dict) or aref.get("authorization_id") != rec["authorization_id"]:
            problems.append("authorization differs from the binding")
        if require_production and rec["approval_trust"] != "PRODUCTION":
            problems.append("binding approved with ISOLATED_TEST evidence cannot be used with production governance")
        return not problems, {"binding_id": rec["binding_id"], "seq": rec["seq"], "problems": problems,
                              "approval_trust": rec["approval_trust"], "evidence_path": rec["approval_evidence_path"]}


def _body(rec: dict) -> dict:
    return {k: v for k, v in rec.items() if k not in ("seq", "prev_sha256", "record_sha256")}
