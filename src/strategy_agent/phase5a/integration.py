"""Proposal -> Request -> Orchestrator wiring (Phase 5-A3).

* ``contract_check``      re-verifies that A1 and A2 use the SAME shared Research Request schema (bytes, sha256, git
                          blob, version 1.0.0, PENDING semantics, closed objects). Never edits the schema.
* ``proposal_to_request`` runs A2's real exporter. An incomplete proposal (status PENDING_INFORMATION) or missing /
                          invalid bindings returns PENDING_INFORMATION and writes NO request file.
* ``submit_proposal``     exports with A2, re-loads the written file from disk, submits those bytes to A1
                          ``Orchestrator.submit`` and records an append-only Proposal->Request->Task link.

Nothing here approves (A1 ``approve`` stays the only way and needs a human / TEST_FIXTURE approver), creates or
selects an authorization (A2 forces ``authorization_ref=PENDING``), dispatches, consumes a batch or touches the
Holdout. A request whose ``authorization_ref`` is PENDING can therefore only reach PENDING_APPROVAL in an isolated
test orchestrator as a SYNTHETIC_TEST, and is BLOCKED (G5) everywhere else.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

from strategy_agent.orchestration import request as Q
from strategy_agent.registry.chain import ChainedLog, ConditionFailed

CONTRACT = {"commit": "5d088c4de362add925344d8e48b36c2e9d728d96",
            "schema_path": "schemas/RESEARCH_REQUEST_SCHEMA.json",
            "schema_sha256": "1b0530f321c6c71e9eac1bbe5f9c33e3f92e4af4c4ce11b80777c9a94c5324eb",
            "git_blob": "4836b8f5738d26a95d1fe6c6a0d55c683a810a93",
            "schema_version": "1.0.0"}


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def git_blob(b: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(b) + b).hexdigest()


def contract_check() -> dict:
    """A1 and A2 must resolve to the same, pinned, unmodified shared schema."""
    from strategy_agent.proposal import exporter as X  # noqa: PLC0415
    raw = Q.SCHEMA_PATH.read_bytes()
    a1 = Q.schema()
    try:
        a2, a2_err = X.shared_schema(), None
    except (X.ContractPending, ValueError, OSError) as e:    # fail closed: no schema => no compatibility
        a2, a2_err = None, f"{type(e).__name__}: {e}"
    props = a1.get("properties", {})
    checks = {
        "same_schema_file": Q.SCHEMA_PATH.resolve() == Path(X.SCHEMA_PATH).resolve(),
        "sha256_matches_pin": _sha(raw) == CONTRACT["schema_sha256"] == X.REQUEST_SCHEMA_SHA256,
        "git_blob_matches_pin": git_blob(raw) == CONTRACT["git_blob"],
        "a2_contract_commit_pin": X.REQUEST_CONTRACT_PIN == CONTRACT["commit"],
        "schema_version_1_0_0": props.get("schema_version", {}).get("const") == CONTRACT["schema_version"],
        "a1_a2_parse_identical": a2 is not None and a1 == a2,
        "authorization_ref_pending_allowed": {"const": "PENDING"} in props.get("authorization_ref", {}).get("oneOf", []),
        "core_commit_pending_allowed": {"const": "PENDING"} in props.get("core_commit", {}).get("oneOf", []),
        "closed_top_level_object": a1.get("additionalProperties") is False,
        "holdout_access_no_access_only": props.get("holdout_policy", {}).get("properties", {}).get("access")
        == {"const": "NO_ACCESS"},
    }
    return {"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks, "pin": CONTRACT,
            "observed": {"sha256": _sha(raw), "git_blob": git_blob(raw), "bytes": len(raw)}, "a2_error": a2_err,
            "schema_modified_by_phase5a3": False}


# ------------------------------------------------------------------------------------------- proposal -> request
def _base(proposal) -> dict:
    p = proposal if isinstance(proposal, dict) else {}
    return {"proposal_id": p.get("proposal_id"), "proposal_sha256": p.get("proposal_sha256"),
            "proposal_status": p.get("status"), "input_kind": (p.get("origin") or {}).get("input_kind"),
            "request_written": False, "executable_request": False, "authorization_granted": False,
            "batch_consumption": 0}


def proposal_to_request(proposal: dict, *, bindings: dict | None, output: Path, artifact_root: Path) -> dict:
    """Run A2's exporter. Status EXPORTED | PENDING_INFORMATION | CONTRACT_PENDING | REJECTED."""
    from strategy_agent.proposal.agent import validate_proposal  # noqa: PLC0415
    from strategy_agent.proposal.exporter import BindingsPending, ContractPending, export_request  # noqa: PLC0415
    output = Path(output)
    base = _base(proposal)
    v = validate_proposal(proposal) if isinstance(proposal, dict) else {"valid": False, "errors": ["NOT_OBJECT"]}
    if not v["valid"]:
        return {**base, "status": "REJECTED", "errors": v["errors"]}
    if proposal["status"] != "PROPOSED":
        return {**base, "status": "PENDING_INFORMATION",
                "blockers": sorted({b["code"] for b in proposal["validation"]["blockers"]})}
    try:
        res = export_request(proposal, output, bindings=bindings, artifact_root=artifact_root)
    except BindingsPending as e:
        out = {**base, "status": "PENDING_INFORMATION", "errors": [str(e)]}
    except ContractPending as e:
        out = {**base, "status": "CONTRACT_PENDING", "errors": [str(e)]}
    except FileExistsError:
        return _already_exported(proposal, bindings=bindings, output=output, artifact_root=artifact_root, base=base)
    except (ValueError, KeyError, TypeError) as e:
        out = {**base, "status": "REJECTED", "errors": [f"{type(e).__name__}: {e}"]}
    else:
        raw = output.read_bytes()
        return {**base, "status": "EXPORTED", "request_written": True, "request_path": str(output),
                "request_file_sha256": _sha(raw), "request_hash": Q.request_hash(json.loads(raw)),
                "provenance_path": str(output.with_name(output.stem + "_provenance.json")),
                "provenance": res["provenance"], "already_exported": False}
    assert not output.exists(), "a non-exported proposal must never leave a request file behind"
    return out


def _already_exported(proposal, *, bindings, output, artifact_root, base) -> dict:
    """Idempotent re-export: the existing file must be byte-equal to what A2 would write now."""
    from strategy_agent.proposal.exporter import export_request  # noqa: PLC0415
    with tempfile.TemporaryDirectory() as d:
        probe = Path(d) / output.name
        export_request(proposal, probe, bindings=bindings, artifact_root=artifact_root)
        same = probe.read_bytes() == output.read_bytes()
    if not same:
        return {**base, "status": "REJECTED", "errors": ["EXISTING_REQUEST_FILE_DIFFERS: never overwritten"]}
    raw = output.read_bytes()
    return {**base, "status": "EXPORTED", "request_written": False, "request_path": str(output),
            "request_file_sha256": _sha(raw), "request_hash": Q.request_hash(json.loads(raw)),
            "provenance_path": str(output.with_name(output.stem + "_provenance.json")), "already_exported": True}


# --------------------------------------------------------------------------------------------- link registry
class LinkLog:
    """Append-only, hash-chained Proposal -> Request -> Task links (one per proposal_sha256 + request_hash)."""

    def __init__(self, state_dir: Path):
        self.path = Path(state_dir) / "proposal_links.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.log = ChainedLog(self.path)

    def records(self) -> list[dict]:
        return self.log.verify() if self.path.exists() else []

    def find(self, *, task_id=None, request_hash=None) -> list[dict]:
        return [r for r in self.records() if (task_id is None or r["task_id"] == task_id)
                and (request_hash is None or r["request_hash"] == request_hash)]

    def link(self, payload: dict) -> tuple[dict, bool]:
        key = (payload["proposal_sha256"], payload["request_hash"])

        def refuse(recs):
            for r in recs:
                if (r["proposal_sha256"], r["request_hash"]) == key:
                    return "DUPLICATE_LINK"
                if r["request_hash"] == payload["request_hash"] and r["task_id"] != payload["task_id"]:
                    return "REQUEST_HASH_BOUND_TO_OTHER_TASK"
            return None
        try:
            return self.log.append_if(payload, refuse), True
        except ConditionFailed as e:
            if "DUPLICATE_LINK" in str(e):
                return next(r for r in self.records() if (r["proposal_sha256"], r["request_hash"]) == key), False
            raise


def submit_proposal(orch, proposal: dict, *, bindings: dict | None, export_path: Path, artifact_root: Path,
                    actor: str, actor_kind: str = "HUMAN") -> dict:
    """A2 export -> load written bytes -> A1 submit -> link. Stops at export when the proposal is not exportable."""
    conv = proposal_to_request(proposal, bindings=bindings, output=export_path, artifact_root=artifact_root)
    if conv["status"] != "EXPORTED":
        return {"stage": "EXPORT", "export": conv, "task_id": None, "state": None, "submitted": False}
    doc = Q.load(conv["request_path"])                      # what the orchestrator sees is exactly the file on disk
    if Q.request_hash(doc) != conv["request_hash"]:
        raise RuntimeError("request file changed between export and submission")
    res = orch.submit(doc, actor=actor, actor_kind=actor_kind)
    link, created = LinkLog(orch.state_dir).link({
        "kind": "PHASE5A3_PROPOSAL_REQUEST_TASK_LINK", "proposal_id": conv["proposal_id"],
        "proposal_sha256": conv["proposal_sha256"], "input_kind": conv["input_kind"], "request_id": doc["request_id"],
        "request_path": conv["request_path"], "request_file_sha256": conv["request_file_sha256"],
        "request_hash": conv["request_hash"], "provenance_path": conv["provenance_path"],
        "task_id": res["task_id"], "state_after_submit": res["state"], "test_mode": orch.test_mode,
        "submitted_by": actor, "submitted_by_kind": actor_kind,
        "note": "submitted_by is caller-declared; it is not an authenticated identity (Phase 5-B prerequisite)"})
    return {"stage": "ORCHESTRATOR", "export": conv, "task_id": res["task_id"], "state": res["state"],
            "duplicate": bool(res.get("duplicate")) or not created, "submitted": True, "orchestrator": res,
            "link_seq": link["seq"]}


def trace(orch, task_id: str) -> dict:
    """Full chain for one task: proposal -> request -> task events -> evaluation -> archive."""
    st = orch.status(task_id)
    links = LinkLog(orch.state_dir).find(task_id=task_id)
    last = {e["to"]: e for e in orch.log.get(task_id).events}
    return {"task_id": task_id, "state": st["state"], "links": links,
            "request_hash": st["request_hash"], "states": [h["to"] for h in st["history"]],
            "evaluation_reference": (last.get("EVALUATED") or {}).get("data", {}).get("evaluation_reference"),
            "research_decision": (last.get("EVALUATED") or {}).get("data", {}).get("research_decision"),
            "task_archive": (last.get("ARCHIVED") or {}).get("data", {}).get("task_archive")}
