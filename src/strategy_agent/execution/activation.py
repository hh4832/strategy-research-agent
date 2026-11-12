"""Phase 5-B3-B: the REAL-execution activation gate (fail closed; no environment switch exists).

The existing ResearchOnlyRunner may be dispatched by the Phase 5 controlled path in REAL mode only if EVERY condition
below holds for THIS request, evaluated from authoritative records at precheck (before any budget) and again inside
``execute`` (before the ledger grant):

  A01 a NEW research-only authorization is APPROVED, active and unexpired (the spent DEV authorization never counts)
  A02 the request is a registered bound request whose approval evidence is verifiable; on the repository's production
      books the binding must carry PRODUCTION (live GitHub) trust
  A03 authorization id and Bound Request Hash agree (request hash == binding record == bound copy)
  A04 strategy spec sha256: request == authorization == file on disk == runner's own identity
  A05 dataset snapshot id + manifest sha256: request == authorization (snapshot) == runner config + manifest file
  A06 Core commit: request == INSTALLED Core (and the runner config's Core, via the backend's own identity)
  A07 the authorization's batch budget is not consumed (uses < max_batches)
  A08 the executor is the authorization's named executor (``executor.executor_id``, SINGLE_EXECUTOR_ONLY)
  A09 the Holdout is excluded: NO_ACCESS, the authorization's lock, window end <= Holdout start
  A10 the B3-A readiness manifest resolves to the same pins as the authorization (no contradiction / missing value)

There is no other switch. ``REAL_EXECUTION_ENABLED`` stays False (it governs the Phase 5-A adapter, which stays
DISABLED); this gate is the only way a non-synthetic backend can run, and on today's repository books it is BLOCKED
because no such authorization exists.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from strategy_agent.orchestration import request as Q

from . import identity as I

SPENT_FOREVER = frozenset({"RA-IBKR_TSM-SMA520_DEV-18350b3adba8e28a7e5e", "RA-IBKR_TSM-SMA520_SMOKE-0001"})
CONDITIONS = ("A01_authorization_approved_active", "A02_verified_binding_evidence", "A03_authorization_bound_request",
              "A04_strategy_spec_identity", "A05_dataset_snapshot_manifest", "A06_core_commit",
              "A07_budget_not_consumed", "A08_authorized_executor", "A09_holdout_excluded", "A10_readiness_manifest")


def _uses(gov: Path, authorization_id: str) -> int:
    p = Path(gov) / "registry/research_authorization_uses.jsonl"
    if not p.is_file():
        return 0
    from strategy_agent.registry.chain import ChainedLog
    return sum(1 for x in ChainedLog(p).verify() if x.get("authorization_id") == authorization_id)


def _book_active(gov: Path) -> dict:
    from strategy_agent.governance.research_authorization import ResearchAuthorizationBook  # noqa: PLC0415
    g = Path(gov) / "governance"
    return ResearchAuthorizationBook(g / "research_authorizations.jsonl", g / "approvers.json").active


def eligible_authorizations(gov: Path, executor_id: str, now: datetime, *, authorization_id: str | None = None) -> list[str]:
    """Standing precondition (no request yet): approved, active, unexpired, NOT spent-forever, executor named and equal.
    Budget is NOT a standing condition: an orchestrator must still be constructible to RECOVER / reconcile a consumed
    batch; every new request is refused by A07 at precheck. Empty on today's repository books => the real path cannot
    even be constructed there."""
    try:
        from strategy_agent.governance.authorization_contracts import active_contracts
        from strategy_agent.orchestration.governance import is_isolated_test_root
        active = _book_active(gov) if is_isolated_test_root(gov) else active_contracts(gov)
    except Exception:  # noqa: BLE001 - an unreadable book is never eligibility
        return []
    out = []
    for aid, a in active.items():
        if (authorization_id is not None and aid != authorization_id) or aid in SPENT_FOREVER or (a.get("executor") or {}).get("executor_id") != executor_id:
            continue
        if not datetime.fromisoformat(a["issued_at"]) <= now < datetime.fromisoformat(a["expires_at"]):
            continue
        out.append(aid)
    return sorted(out)


class RealActivationGate:
    def __init__(self, *, governance_root: Path, artifact_root: Path, executor_id: str, production_books: bool,
                 readiness_manifest=None, clock=None, authorization_id=None):
        self.governance_root, self.artifact_root = Path(governance_root), Path(artifact_root)
        self.executor_id, self.production_books = executor_id, production_books
        self.authorization_id = authorization_id
        self.readiness_manifest = readiness_manifest      # callable -> manifest dict (B3-A); None => A10 BLOCKED
        self.clock = clock or (lambda: datetime.now().astimezone())

    def evaluate(self, doc: dict, backend) -> dict:
        c, now = {}, self.clock()

        def put(k, ok, detail):
            c[k] = {"status": "PASS" if ok else "FAIL", "detail": detail}

        rid = I.request_identity(doc)
        aid = rid["authorization_id"]
        try:
            from strategy_agent.governance.authorization_contracts import active_contracts
            from strategy_agent.orchestration.governance import is_isolated_test_root
            active = _book_active(self.governance_root) if is_isolated_test_root(self.governance_root) else active_contracts(self.governance_root)
            auth = active.get(aid)
            if self.authorization_id is not None and aid != self.authorization_id:
                raise ValueError("AUTHORIZATION_CONTRACT_MISMATCH")
            book_err = None
        except Exception as e:  # noqa: BLE001
            auth, book_err = None, f"{type(e).__name__}: {e}"
        exp = auth and datetime.fromisoformat(auth["expires_at"])
        issued = auth and datetime.fromisoformat(auth["issued_at"])
        put(CONDITIONS[0], bool(auth) and aid not in SPENT_FOREVER and issued <= now < exp,
            {"authorization_id": aid, "active": bool(auth), "expires_at": auth and auth["expires_at"],
             "spent_forever": aid in SPENT_FOREVER, "book_error": book_err})
        from strategy_agent.binding.store import BindingStore  # noqa: PLC0415
        ok_b, info = BindingStore(self.governance_root).verify_bound(doc, require_production=self.production_books)
        put(CONDITIONS[1], ok_b and (info.get("approval_trust") == "PRODUCTION" or not self.production_books),
            {"binding": info, "required_trust": "PRODUCTION" if self.production_books else "PRODUCTION or ISOLATED_TEST"})
        rh = Q.request_hash(doc)
        rec = BindingStore(self.governance_root).for_bound(rh) if ok_b else None
        put(CONDITIONS[2], bool(rec) and rec["authorization_id"] == aid and rec["bound_request_sha256"] == rh,
            {"request_sha256": rh, "binding_authorization_id": rec and rec["authorization_id"]})
        try:
            bid, bprob = backend.execution_identity()
        except Exception as e:  # noqa: BLE001
            bid, bprob = {}, [f"BACKEND_IDENTITY_UNAVAILABLE:{type(e).__name__}:{e}"]
        st = (auth or {}).get("strategy") or {}
        spec_disk = I._sha_file(self.artifact_root / (st.get("spec_path") or "__missing__"))  # noqa: SLF001
        put(CONDITIONS[3], bool(st) and rid["strategy_spec_sha256"] == st.get("spec_file_sha256") == spec_disk
            == bid.get("strategy_spec_sha256") and not any("SPEC" in p for p in bprob),
            {"request": rid["strategy_spec_sha256"], "authorization": st.get("spec_file_sha256"), "disk": spec_disk,
             "runner": bid.get("strategy_spec_sha256")})
        ds = (auth or {}).get("dataset") or {}
        put(CONDITIONS[4], bool(ds) and rid["dataset_snapshot_id"] == ds.get("snapshot_id") == bid.get("dataset_snapshot_id")
            and rid["dataset_manifest_sha256"] == bid.get("dataset_manifest_sha256")
            and not any("SNAPSHOT" in p for p in bprob),
            {"request": [rid["dataset_snapshot_id"], rid["dataset_manifest_sha256"]], "authorization": ds.get("snapshot_id"),
             "runner": [bid.get("dataset_snapshot_id"), bid.get("dataset_manifest_sha256")], "runner_problems": bprob})
        put(CONDITIONS[5], bool(rid["core_commit"]) and rid["core_commit"] == bid.get("core_commit")
            and not any("CORE" in p for p in bprob),
            {"request": rid["core_commit"], "installed": bid.get("core_commit"), "dataset_producer": ds.get("producer_commit_sha")})
        try:
            used, uses_error = _uses(self.governance_root, aid or ""), None
        except Exception as error:
            used, uses_error = 0, str(error)
        put(CONDITIONS[6], not uses_error and bool(auth) and used < int(auth.get("max_batches", 0)),
            {"used": used, "max_batches": (auth or {}).get("max_batches"), "uses_error": uses_error})
        ex = (auth or {}).get("executor") or {}
        put(CONDITIONS[7], ex.get("executor_id") == self.executor_id and ex.get("consistency") == "SINGLE_EXECUTOR_ONLY",
            {"authorized": ex.get("executor_id"), "this_executor": self.executor_id})
        ho = (auth or {}).get("holdout") or {}
        w = rid["research_window"] or {}
        try:
            before = datetime.fromisoformat(w["end"]) <= datetime.fromisoformat(ho["start"])
        except (KeyError, TypeError, ValueError):
            before = False
        put(CONDITIONS[8], before and (doc.get("holdout_policy") or {}).get("access") == "NO_ACCESS"
            and (doc.get("holdout_policy") or {}).get("lock_id") == ho.get("lock_id"),
            {"lock_id": ho.get("lock_id"), "window_end": w.get("end"), "holdout_start": ho.get("start")})
        put(CONDITIONS[9], *self._readiness(auth, rid))
        ok = all(v["status"] == "PASS" for v in c.values())
        return {"status": "PASS" if ok else "BLOCKED", "conditions": c,
                "failed": [k for k, v in c.items() if v["status"] != "PASS"],
                "mode": "PRODUCTION_BOOKS" if self.production_books else "ISOLATED_TEST_BOOKS"}

    def _readiness(self, auth, rid) -> tuple[bool, dict]:
        if self.readiness_manifest is None or auth is None:
            return False, {"error": "no readiness manifest" if auth else "no authorization"}
        try:
            m = self.readiness_manifest()
        except Exception as e:  # noqa: BLE001
            return False, {"error": f"{type(e).__name__}: {e}"}
        F = m.get("fields") or {}
        bad = [k for k, f in F.items() if f.get("status") == "BLOCKED" and f.get("category") in ("MISSING", "CONTRADICTION")]
        bad += [k for k, x in (m.get("cross_checks") or {}).items() if x.get("status") != "PASS"]

        def val(k):
            return (F.get(k) or {}).get("value")
        want = {"strategy_spec_sha256": (auth["strategy"].get("spec_file_sha256"), val("strategy_spec_sha256")),
                "dataset_snapshot_id": (auth["dataset"].get("snapshot_id"), val("dataset_snapshot_id")),
                "dataset_manifest_sha256": (rid["dataset_manifest_sha256"], val("dataset_manifest_sha256")),
                "core_commit_sha": (rid["core_commit"], val("core_commit_sha")),
                "cost_profile": (auth["costs"].get("scenario_set_sha256"), (val("trading_cost_profile") or {}).get("scenario_set_sha256")),
                "temporal_policy": (auth["temporal_policy"].get("sha256"), rid["temporal_policy_sha256"])}
        bad += [f"MANIFEST_DIFFERS:{k}" for k, (a, b) in want.items() if a is None or a != b]
        return not bad, {"problems": bad, "manifest_kind": m.get("kind")}


__all__ = ["RealActivationGate", "CONDITIONS", "SPENT_FOREVER", "eligible_authorizations"]
