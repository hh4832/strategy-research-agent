"""AuthorizationBindingService: bind an EXISTING Research Authorization to a PENDING A2 request after verified approval.

Never creates, edits, resets or consumes an authorization; never edits the proposal or the source request; never
unlocks the Holdout; never approves execution. Every check runs (failures are collected) before anything is
written; ``dry_run`` (the default of the CLI) writes nothing.

B01 source request: schema-valid, authorization_ref PENDING, not SYNTHETIC_TEST
B02 A2 provenance: status SCHEMA_VALID_PENDING_HUMAN_APPROVAL, request_sha256 == canonical hash of the source,
    pinned contract, proposal id/sha
B03 A2 proposal: validate_proposal() valid, PROPOSED, id/sha == provenance, request_id derived from it;
    SYNTHETIC_FIXTURE proposals only with isolated test evidence
B04 derived request: schema-valid; differs from the source ONLY in authorization_ref
B05 existing governance on the derived request: G1-G9 PASS (strategy spec, dataset, Core + costs, authorization
    exists / scope RA03-RA10 + RA12 / budget / holdout / formal gate recorded); G10 recorded only
B06 budget: uses < max_batches AND bindings already made for this authorization < max_batches - uses
B07 verified approval evidence: intent BIND_AUTHORIZATION, request = source hash, authorization, scope
B08 trust: production governance books accept PRODUCTION (live GitHub) evidence only
B09 uniqueness: the source is not already bound differently; the evidence is not used for another binding
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from strategy_agent.approval import ApprovalRejected, Expectation, evidence_sha256
from strategy_agent.approval.tokens import scope_sha256
from strategy_agent.orchestration import request as Q
from strategy_agent.orchestration.governance import Governance
from strategy_agent.registry.chain import ConditionFailed

from .store import BOUND, EVIDENCE, BindingStore, _body, dumps, schema, write_immutable

ROOT = Q.ROOT
TPE = ZoneInfo("Asia/Taipei")
REQUIRED_GOVERNANCE = ("G1_REQUEST_SCHEMA", "G2_STRATEGY_SPEC", "G3_DATASET", "G4_CORE_COST", "G5_AUTHORIZATION",
                       "G6_AUTHORIZATION_SCOPE", "G7_BUDGET", "G8_HOLDOUT", "G9_FORMAL_GATE")


class BindingRejected(RuntimeError):
    def __init__(self, failures: list[dict], plan: dict | None = None):
        super().__init__("; ".join(f"{f['code']}: {f['detail']}" for f in failures)[:600])
        self.failures, self.plan = failures, plan
        self.code = failures[0]["code"] if failures else "BINDING_REJECTED"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def binding_id(source_sha: str, authorization_id: str) -> str:
    return "AB-" + _sha(f"{source_sha}|{authorization_id}".encode())[:24]


class AuthorizationBindingService:
    def __init__(self, *, verifier, governance_root: Path = ROOT, root: Path = ROOT, clock=None, benchmark=None):
        self.verifier = verifier
        self.governance_root, self.root = Path(governance_root), Path(root)
        self.clock = clock or (lambda: datetime.now(TPE))
        self.gov = Governance(self.root, governance_root=self.governance_root, clock=self.clock, benchmark=benchmark)
        self.store = BindingStore(self.governance_root)

    # ------------------------------------------------------------------------------------------------ prepare
    def prepare(self, *, source_request_path: Path, provenance_path: Path, proposal_path: Path, authorization_id: str,
                evidence_url: str) -> dict:
        from strategy_agent.proposal.agent import validate_proposal  # noqa: PLC0415 - pure validator
        from strategy_agent.proposal.exporter import REQUEST_CONTRACT_PIN, REQUEST_SCHEMA_SHA256  # noqa: PLC0415
        F: list[dict] = []

        def fail(code, detail):
            F.append({"code": code, "detail": detail})

        plan: dict = {"authorization_id": authorization_id, "evidence_url": evidence_url, "checks": {}}
        # B01
        src_raw = Path(source_request_path).read_bytes()
        src = json.loads(src_raw)
        errs = Q.validate(src)
        src_sha = Q.request_hash(src) if isinstance(src, dict) else None
        plan.update(source_request_sha256=src_sha, source_request_file_sha256=_sha(src_raw))
        if errs:
            fail("B01_SOURCE_SCHEMA", errs)
        elif src.get("authorization_ref") != "PENDING":
            fail("B01_SOURCE_NOT_PENDING", "only a PENDING request can be bound (never re-bind or overwrite)")
        elif src.get("execution_mode") == "SYNTHETIC_TEST":
            fail("B01_SYNTHETIC_NOT_BINDABLE", "SYNTHETIC_TEST requests are never bound to an authorization")
        # B02
        prov_raw = Path(provenance_path).read_bytes()
        prov = json.loads(prov_raw)
        plan["source_provenance_sha256"] = _sha(prov_raw)
        if prov.get("status") != "SCHEMA_VALID_PENDING_HUMAN_APPROVAL" or prov.get("request_sha256") != src_sha or \
                prov.get("contract_commit") != REQUEST_CONTRACT_PIN or prov.get("schema_sha256") != REQUEST_SCHEMA_SHA256 \
                or prov.get("execution_eligible") is not False or prov.get("authorization_granted") is not False:
            fail("B02_PROVENANCE", "A2 provenance does not match this request / pinned contract")
        # B03
        prop = json.loads(Path(proposal_path).read_text(encoding="utf-8"))
        v = validate_proposal(prop) if isinstance(prop, dict) else {"valid": False, "errors": ["NOT_OBJECT"]}
        plan.update(source_proposal_id=prop.get("proposal_id") if isinstance(prop, dict) else None,
                    source_proposal_sha256=prop.get("proposal_sha256") if isinstance(prop, dict) else None)
        if not v["valid"] or prop.get("status") != "PROPOSED":
            fail("B03_PROPOSAL_INVALID", v.get("errors") or "proposal is not PROPOSED")
        elif prop["proposal_sha256"] != prov.get("proposal_sha256") or prop["proposal_id"] != prov.get("proposal_id") \
                or src.get("request_id") != "RR-" + prop["proposal_id"].upper():
            fail("B03_PROPOSAL_MISMATCH", "proposal id/sha do not match the provenance and request")
        elif prop.get("origin", {}).get("input_kind") == "SYNTHETIC_FIXTURE" and self.verifier.production:
            fail("B03_SYNTHETIC_PROPOSAL", "a SYNTHETIC_FIXTURE proposal is never bound with production evidence")
        # B04
        bound = copy.deepcopy(src)
        bound["authorization_ref"] = {"authorization_id": authorization_id}
        berr = Q.validate(bound)
        bound_sha = Q.request_hash(bound)
        diff = sorted(k for k in set(src) | set(bound) if src.get(k) != bound.get(k))
        if berr or diff != ["authorization_ref"]:
            fail("B04_DERIVED_REQUEST", berr or f"derived request changes {diff}")
        plan.update(bound_request=bound, bound_request_sha256=bound_sha, scope_sha256=scope_sha256(src))
        # B05
        dec = self.gov.check(bound, test_mode=False, binding_check=False)
        gchecks = {c["id"]: c["status"] for c in dec.checks}
        plan["governance"] = {"checks": gchecks, "execution_mode_enabled": gchecks.get("G10_EXECUTION_MODE") == "PASS",
                              "detail": dec.as_dict()}
        bad = [k for k in REQUIRED_GOVERNANCE if gchecks.get(k) != "PASS"]
        if bad:
            fail("B05_GOVERNANCE", {k: next((c["detail"] for c in dec.checks if c["id"] == k), "not evaluated") for k in bad})
        # B06
        from strategy_agent.governance.research_authorization import ResearchAuthorizationBook  # noqa: PLC0415
        auth = ResearchAuthorizationBook(self.gov.paths["auth"], self.gov.paths["approvers"]).get(authorization_id)
        uses = len(self.gov.uses(authorization_id)) if auth else 0
        try:
            prior = self.store.count_for_authorization(authorization_id)
        except Exception as e:  # noqa: BLE001
            prior = None
            fail("B09_BINDING_LOG_INVALID", str(e))
        if auth is None:
            fail("B06_AUTHORIZATION_MISSING", f"no active approved authorization {authorization_id} (never created here)")
        else:
            plan["authorization_state"] = {"max_batches": auth["max_batches"], "uses_at_binding": uses,
                                           "bindings_before": prior or 0, "record_sha256": auth.get("record_sha256", "0" * 64)}
            if uses >= auth["max_batches"]:
                fail("B06_BUDGET_EXHAUSTED", f"authorization used {uses}/{auth['max_batches']}; budget is never reset")
            elif prior is not None and prior >= auth["max_batches"] - uses and not self._same(src_sha, authorization_id):
                fail("B06_AUTHORIZATION_FULLY_BOUND", f"{prior} binding(s) already hold the remaining budget")
        # B07 + B08
        ev = None
        try:
            ev = self.verifier.verify(evidence_url, Expectation("BIND_AUTHORIZATION", src_sha or "", authorization_id,
                                                                scope_sha256(src) if isinstance(src, dict) else ""))
        except ApprovalRejected as e:
            fail("B07_APPROVAL_" + e.code, str(e))
            plan["approval_rejection"] = e.record
        if self.gov.production_books and not self.verifier.production:
            fail("B08_TRUST", "the repository's governance books accept PRODUCTION (live GitHub) evidence only")
        if ev is not None:
            plan["approval_evidence"] = ev
            plan["approval_evidence_sha256"] = evidence_sha256(ev)
        # B09
        try:
            for r in self.store.for_source(src_sha or ""):
                if not (r["authorization_id"] == authorization_id and ev and r["approval_evidence_id"] == ev["evidence_id"]):
                    fail("B09_REQUEST_ALREADY_BOUND", f"source request already bound by {r['binding_id']}")
            if ev and any(r["approval_evidence_id"] == ev["evidence_id"] and r["source_request_sha256"] != src_sha
                          for r in self.store.records()):
                fail("B09_EVIDENCE_REUSED", f"{ev['evidence_id']} already used for a different request")
        except Exception as e:  # noqa: BLE001
            if not any(f["code"] == "B09_BINDING_LOG_INVALID" for f in F):
                fail("B09_BINDING_LOG_INVALID", str(e))
        plan["failures"], plan["ok"] = F, not F
        return plan

    def _same(self, src_sha, aid) -> bool:
        return any(r["authorization_id"] == aid for r in self.store.for_source(src_sha))

    # --------------------------------------------------------------------------------------------------- bind
    def bind(self, *, dry_run: bool = True, **kw) -> dict:
        plan = self.prepare(**kw)
        if not plan["ok"]:
            raise BindingRejected(plan["failures"], plan)
        return self._commit(plan, dry_run=dry_run)

    def _authoritative_budget(self, authorization_id: str, recs: list[dict]) -> str | None:
        """M2: re-read the AUTHORITATIVE books (authorization book + uses log + binding log) at commit time,
        under the binding-log lock. The prepare()-time snapshot is never trusted for the budget decision."""
        from strategy_agent.governance.research_authorization import ResearchAuthorizationBook  # noqa: PLC0415
        try:
            auth = ResearchAuthorizationBook(self.gov.paths["auth"], self.gov.paths["approvers"]).get(authorization_id)
            uses = len(self.gov.uses(authorization_id))
        except Exception as e:  # noqa: BLE001 - unreadable books => fail closed
            return f"B06_AUTHORITATIVE_STATE_UNAVAILABLE: {e}"
        if auth is None:
            return "B06_AUTHORIZATION_MISSING"
        exp = auth.get("expires_at")
        if exp and datetime.fromisoformat(exp) <= self.clock():
            return "B06_AUTHORIZATION_EXPIRED"
        if uses >= auth["max_batches"]:
            return "B06_BUDGET_EXHAUSTED"
        if sum(1 for r in recs if r["authorization_id"] == authorization_id) >= auth["max_batches"] - uses:
            return "B06_AUTHORIZATION_FULLY_BOUND"
        return None

    def _commit(self, plan: dict, *, dry_run: bool) -> dict:
        ev, bound = plan["approval_evidence"], plan["bound_request"]
        bound_bytes, ev_bytes = dumps(bound), dumps(ev)
        rec = {"schema_version": "1.0.0", "binding_id": binding_id(plan["source_request_sha256"], plan["authorization_id"]),
               "relationship": {"kind": "AUTHORIZATION_BINDING", "parent_request_sha256": plan["source_request_sha256"],
                                "derived_request_sha256": plan["bound_request_sha256"], "changed_fields": ["authorization_ref"]},
               "source_proposal_id": plan["source_proposal_id"], "source_proposal_sha256": plan["source_proposal_sha256"],
               "source_provenance_sha256": plan["source_provenance_sha256"], "source_request_id": bound["request_id"],
               "source_request_sha256": plan["source_request_sha256"],
               "source_request_file_sha256": plan["source_request_file_sha256"],
               "bound_request_sha256": plan["bound_request_sha256"], "bound_request_file_sha256": _sha(bound_bytes),
               "bound_request_path": f"{BOUND}/{plan['bound_request_sha256']}.json",
               "authorization_id": plan["authorization_id"], "authorization_state": plan["authorization_state"],
               "scope_sha256": plan["scope_sha256"], "approval_evidence_id": ev["evidence_id"],
               "approval_evidence_sha256": _sha(ev_bytes),
               "approval_evidence_path": f"{EVIDENCE}/{_sha(ev_bytes)}.json",
               "approval_trust": ev["approval_source"]["trust"],
               "binding_actor": {"github_login": ev["author"]["login"], "github_id": ev["author"]["id"],
                                 "source": "VERIFIED_APPROVAL_EVIDENCE_AUTHOR"},
               "binding_timestamp": self.clock().isoformat(timespec="seconds"),
               "governance": {"checks": plan["governance"]["checks"],
                              "execution_mode_enabled": plan["governance"]["execution_mode_enabled"]},
               "consumes_budget": False, "approves_execution": False, "tool": "strategy_agent.binding"}
        from jsonschema import Draft202012Validator  # noqa: PLC0415
        errs = [e.message for e in Draft202012Validator(schema()).iter_errors(rec)]
        if errs:
            raise BindingRejected([{"code": "B10_RECORD_SCHEMA", "detail": errs}], plan)
        if dry_run:
            return {"status": "DRY_RUN_OK", "would_write": rec, "written": False, "plan": _public(plan)}
        write_immutable(self.governance_root / rec["approval_evidence_path"], ev_bytes)
        write_immutable(self.governance_root / rec["bound_request_path"], bound_bytes)
        key = (rec["source_request_sha256"], rec["authorization_id"], rec["approval_evidence_id"])

        def refuse(recs):
            for r in recs:
                if (r["source_request_sha256"], r["authorization_id"], r["approval_evidence_id"]) == key:
                    return "IDEMPOTENT"
                if r["source_request_sha256"] == rec["source_request_sha256"]:
                    return "B09_REQUEST_ALREADY_BOUND"
                if r["approval_evidence_id"] == rec["approval_evidence_id"]:
                    return "B09_EVIDENCE_REUSED"
            return self._authoritative_budget(rec["authorization_id"], recs)
        # the evidence is re-verified live right before the record is appended (revocation since prepare())
        try:
            self.verifier.verify(plan["evidence_url"], Expectation("BIND_AUTHORIZATION", plan["source_request_sha256"],
                                                                   plan["authorization_id"], plan["scope_sha256"]))
        except ApprovalRejected as e:
            raise BindingRejected([{"code": "B07_APPROVAL_" + e.code, "detail": str(e)}], plan) from None
        try:
            stored = self.store.log.append_if(rec, refuse)
            created = True
        except ConditionFailed as e:
            if "IDEMPOTENT" not in str(e):
                raise BindingRejected([{"code": str(e).split(":")[0], "detail": str(e)}], plan) from None
            stored, created = next(r for r in self.store.records()
                                   if (r["source_request_sha256"], r["authorization_id"], r["approval_evidence_id"]) == key), False
        return {"status": "BOUND" if created else "ALREADY_BOUND", "written": created, "record": stored,
                "bound_request_path": str(self.governance_root / stored["bound_request_path"])}


def _public(plan: dict) -> dict:
    return {k: v for k, v in plan.items() if k not in ("bound_request",)}


def inspect(governance_root: Path, *, bound_sha: str | None = None, source_sha: str | None = None) -> list[dict]:
    st = BindingStore(governance_root)
    recs = st.records()
    return [_body(r) | {"seq": r["seq"]} for r in recs
            if (bound_sha is None or r["bound_request_sha256"] == bound_sha)
            and (source_sha is None or r["source_request_sha256"] == source_sha)]
