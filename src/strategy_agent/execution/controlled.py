"""B2-2 Controlled Runner Adapter + execution receipts.

``ControlledRunnerAdapter`` is the only adapter that may consume a research batch. The Orchestrator drives it
(``Orchestrator._dispatch_controlled``) strictly in this order, re-reading the authoritative state at each step:
verified approval -> binding -> governance (G1-G11) -> precheck (identity / PIT pins, BEFORE any budget) ->
atomic reservation -> commit (uses record) -> backend execution -> receipt -> existing evaluator -> archive -> settle.

Enablement is NOT a flag or an environment variable: ``enabled`` is computed from the governance root (must be an
isolated test root carrying ISOLATED_TEST_GOVERNANCE_ROOT.json) AND the backend (must declare a synthetic snapshot).
On the repository's books it is always False, so RESEARCH_ONLY_REAL stays DISABLED.

Backends never re-implement PnL, costs or signals:
  * ``SyntheticFixtureBackend``   copies a labelled SYNTHETIC_FIXTURE Phase 4-A contract archive (no market data)
  * ``ResearchOnlyRunnerBackend`` the EXISTING ResearchOnlyRunner on a SYNTHETIC development snapshot; it runs the
    runner's own preflight/probe before the reservation and its own data checks + Core execution after the commit,
    and never calls ``runner.run()`` (which would consume a second time).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from strategy_agent.evaluation import phase4c as C
from strategy_agent.execution import identity as I
from strategy_agent.orchestration import request as Q
from strategy_agent.orchestration.adapters import AdapterDisabled, ExecutionAdapter, ExecutionFailed
from strategy_agent.orchestration.governance import is_isolated_test_root

RECEIPT = "EXECUTION_RECEIPT.json"
PIN_FIELDS = ("strategy_spec_sha256", "dataset_snapshot_id", "dataset_manifest_sha256", "core_commit")


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def write_receipt(out_dir: Path, receipt: dict) -> tuple[Path, str]:
    p = Path(out_dir) / RECEIPT
    b = (json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
    with open(p, "xb") as fh:                       # immutable
        fh.write(b)
    return p, _sha(b)


class ControlledRunnerAdapter(ExecutionAdapter):
    """Security remediation (Codex P0-1/P0-2/P0-3 on PR #13):

    * the adapter is BOUND to the authoritative BudgetLedger of its own governance root (``bind``); ``execute`` takes
      only a ``reservation_id`` + task / attempt ids. Caller-supplied reservation / use objects are refused outright;
      the ledger's ``claim_execution`` is the only grant to run (COMMITTED, identity match, registered executor, uses
      record present, no earlier EXECUTION_STARTED), so a replayed / forged / second-process call never reaches the
      backend;
    * before the claim the request is verified against authoritative sources (``identity.verify_request``: binding
      log + stored bound request, authorization record, artefact files, the backend's OWN execution identity);
    * the produced archive must carry this request's identity and the granted uses record, and the receipt +
      archive are re-verified from disk (``verify_evidence``) before the orchestrator may settle.
    """
    kind = "CONTROLLED_RESEARCH_ONLY"
    consumes_budget = True
    controlled = True
    evidence = "CONTROLLED_EXECUTION"

    def __init__(self, backend, *, governance_root: Path, ledger=None, artifact_root: Path | None = None,
                 approvals=None, worker_id: str | None = None, clock=None, activation=None):
        self.backend = backend
        self.governance_root = Path(governance_root)
        self.isolated = is_isolated_test_root(governance_root)
        synthetic = getattr(backend, "synthetic", False) is True
        # 5-B3-B modes (no switch other than these facts; never an environment variable):
        #   ISOLATED_SYNTHETIC  isolated test books + synthetic backend (5-B2, unchanged)
        #   REAL_GATED          non-synthetic backend + RealActivationGate whose books match this root; every request
        #                       must pass all ten activation conditions (precheck AND execute)
        #   DISABLED            anything else (synthetic backend on production books, real backend without a gate, ...)
        self.activation = activation
        if self.isolated and synthetic and activation is None:
            self.mode = "ISOLATED_SYNTHETIC"
        elif (not synthetic and activation is not None and Path(activation.governance_root) == self.governance_root
              and activation.production_books == (not self.isolated)):
            self.mode = "REAL_GATED"
        else:
            self.mode = "DISABLED"
        self._enabled = self.mode != "DISABLED"
        self._ledger, self._approvals, self._worker = None, None, None
        self.artifact_root = Path(artifact_root) if artifact_root is not None else None
        self.clock = clock or (lambda: datetime.now().astimezone())
        if ledger is not None:
            self.bind(ledger=ledger, artifact_root=artifact_root, approvals=approvals, worker_id=worker_id)

    @property
    def enabled(self) -> bool:                       # read-only: no setter, no env var
        return self._enabled

    def bind(self, *, ledger, artifact_root: Path | None, approvals, worker_id: str | None, clock=None) -> None:
        """Bind to THE ledger of this governance root (once). ``approvals(task_id, reservation_id)`` must return the
        approval-evidence sha of a task that is RUNNING for that reservation (the orchestrator's task log)."""
        uses = (self.governance_root / "registry/research_authorization_uses.jsonl").resolve()
        if Path(ledger.uses_path).resolve() != uses:
            raise ValueError("the controlled adapter accepts only the budget ledger of its own governance root")
        if self._ledger is not None and self._ledger is not ledger:
            raise ValueError("controlled adapter already bound to another ledger")
        self._ledger, self._approvals, self._worker = ledger, approvals, worker_id
        if artifact_root is not None:
            self.artifact_root = Path(artifact_root)
        if clock is not None:
            self.clock = clock

    @property
    def ledger(self):
        return self._ledger

    def planned_run_id(self, task_id: str, now: datetime) -> str:
        return self.backend.planned_run_id(task_id, now)

    def verify_identity(self, doc: dict) -> dict:
        if self.artifact_root is None:
            return {"problems": ["ARTIFACT_ROOT_UNBOUND"], "binding": None}
        return I.verify_request(doc, governance_root=self.governance_root, artifact_root=self.artifact_root,
                                backend=self.backend, now=self.clock())

    def gate(self, doc: dict, *, at_execute: bool = False) -> list[str]:
        """REAL_GATED only: the ten activation conditions. At execute time the budget condition (A07) is the ledger's
        own claim (the batch is already committed), every other condition is re-evaluated from the records."""
        if self.mode != "REAL_GATED":
            return []
        g = self.activation.evaluate(doc, self.backend)
        failed = [k for k in g["failed"] if not (at_execute and k.startswith("A07_"))]
        if self._worker is not None and self._worker != self.activation.executor_id:
            failed.append("A08_authorized_executor:BOUND_WORKER_DIFFERS")
        return [f"ACTIVATION_GATE:{k}" for k in failed]

    def precheck(self, doc: dict) -> list[str]:
        """Full identity binding BEFORE any budget is reserved. Any mismatch => BLOCKED (never 'use latest data')."""
        if not self.enabled:
            return ["CONTROLLED_EXECUTION_DISABLED: production books or non-synthetic backend"]
        bad = [f"REQUEST_FIELD_MISSING:{k}" for k in PIN_FIELDS if not doc.get(k) or doc.get(k) == "PENDING"]
        return sorted(set(bad + self.gate(doc) + self.verify_identity(doc)["problems"] + self.backend.precheck(doc)))

    def execute(self, doc: dict, out_dir: Path, *, reservation_id: str | None = None, task_id: str | None = None,
                attempt_id: str | None = None, started_at: datetime | None = None, **caller_objects) -> dict:
        if not self.enabled:
            raise AdapterDisabled("CONTROLLED_EXECUTION_DISABLED")
        if any(v is not None for v in caller_objects.values()):
            # P0-1: a reservation / use / state handed over by the caller is never execution eligibility
            raise ExecutionFailed("CALLER_SUPPLIED_RESERVATION_REFUSED",
                                  f"refused caller objects {sorted(caller_objects)}; only the ledger is authoritative")
        if self._ledger is None or self._approvals is None or not self._worker:
            raise ExecutionFailed("LEDGER_UNBOUND", "controlled execution requires the authoritative ledger binding")
        if not reservation_id or not task_id or not attempt_id:
            raise ExecutionFailed("NO_COMMITTED_RESERVATION", "reservation_id, task_id and attempt_id are required")
        from strategy_agent.execution.budget import BudgetRefused  # noqa: PLC0415
        v = self.verify_identity(doc)                               # P0-3: before the claim, before the runner
        problems = v["problems"] + self.gate(doc, at_execute=True)  # 5-B3-B: all activation conditions, again
        if problems:
            raise ExecutionFailed("IDENTITY_VERIFICATION_FAILED", "; ".join(problems)[:800])
        approval = self._approvals(task_id, reservation_id)
        expect = {"request_sha256": Q.request_hash(doc), "task_id": task_id, "attempt_id": attempt_id,
                  "authorization_id": (doc.get("authorization_ref") or {}).get("authorization_id"),
                  "binding_id": (v["binding"] or {}).get("binding_id"), "approval_evidence_sha256": approval}
        try:
            grant = self._ledger.claim_execution(reservation_id, worker_id=self._worker, expect=expect)
        except BudgetRefused as e:                                  # replay / forgery / not committed: no execution
            raise ExecutionFailed(e.code, str(e)) from None
        r, use = grant["reservation"], grant["use"]
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=False)
        started = started_at or datetime.now().astimezone()
        res = self.backend.run(doc, out_dir, use=use)             # EXECUTION STARTED: never re-run from here on
        arch = Path(res["archive_path"])
        mp = arch / "MANIFEST.json"
        if not mp.is_file():
            raise ExecutionFailed("RECEIPT_INCOMPLETE", "runner finished without an archive MANIFEST")
        manifest_sha = _sha(mp.read_bytes())
        try:
            archive = C.read_archive(arch, expected_manifest_sha256=manifest_sha)   # every listed file present + hashed
        except C.ArchiveRejected as e:
            raise ExecutionFailed("RECEIPT_ARCHIVE_INCOMPLETE", f"{e.code}: {e}") from None
        bad = I.archive_problems(archive, doc=doc, use=use, run_id=r["planned_run_id"])
        if bad:
            raise ExecutionFailed("RUNNER_RESULT_INCONSISTENT", "; ".join(bad)[:800])
        contract = execution_contract(doc, archive=archive, run_id=r["planned_run_id"], executor_id=self._worker,
                                      backend=self.backend, artifact_root=self.artifact_root, mode=self.mode)
        cbad = contract_problems(contract, doc=doc, reservation=r)
        if cbad:
            raise ExecutionFailed("EXECUTION_CONTRACT_INCOMPLETE", "; ".join(cbad)[:800])
        receipt = {"kind": "EXECUTION_RECEIPT", "reservation_id": reservation_id, "task_id": r["task_id"],
                   "attempt_id": r["attempt_id"], "request_sha256": r["request_sha256"],
                   "authorization_id": r["authorization_id"], "binding_id": r["binding_id"],
                   "approval_evidence_sha256": r["approval_evidence_sha256"], "run_id": r["planned_run_id"],
                   "use_record_sha256": use["record_sha256"],
                   "execution_started_event_sha256": grant["event"]["record_sha256"],
                   "backend": self.backend.kind, "backend_identity": res.get("identity", {}),
                   "started_at": started.isoformat(timespec="seconds"),
                   "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                   "archive_path": str(arch), "output_manifest_sha256": manifest_sha,
                   "synthetic": self.backend.synthetic is True, "execution_mode": self.mode,
                   "execution_contract": contract, "consumed_batches": 1}
        rp, rsha = write_receipt(out_dir, receipt)
        self._ledger.record_completion(reservation_id, worker_id=self._worker, receipt_sha256=rsha,
                                       output_manifest_sha256=manifest_sha, receipt_path=str(rp))
        return {"adapter": self.kind, "evidence": self.evidence, "archive_path": str(arch),
                "archive_manifest_sha256": manifest_sha, "receipt_path": str(rp), "receipt_sha256": rsha,
                "run_id": r["planned_run_id"], "consumed_batches": 1, "strategy_executed": True,
                "backend": self.backend.kind, "synthetic": self.backend.synthetic is True, "execution_mode": self.mode,
                "reservation_id": reservation_id}

    def verify_evidence(self, reservation_id: str, *, doc: dict, evaluation_reference: dict | None = None,
                        archive_ref: str | None = None, archived_files: dict | None = None, record: bool = False) -> dict:
        """P0-2: re-verify receipt + archive + identity (+ the TASK archive when ``archive_ref`` is given) FROM DISK
        against the ledger. ``record`` appends EVIDENCE_VERIFIED (with the verified task-archive hashes)."""
        if self._ledger is None:
            return {"ok": False, "problems": ["LEDGER_UNBOUND"]}
        return verify_execution_evidence(self._ledger, reservation_id, doc=doc, evaluation_reference=evaluation_reference,
                                         archive_ref=archive_ref, archived_files=archived_files, record=record,
                                         worker_id=self._worker, governance_root=self.governance_root,
                                         expected_mode=self.mode)


CONTRACT_KEYS = ("authorization_id", "request_sha256", "strategy_spec_sha256", "dataset_snapshot_id",
                 "dataset_manifest_sha256", "core_commit", "agent_git_sha", "signal_time_rule", "execution_time_rule",
                 "cost_profile", "slippage_bps_per_side", "executor_id", "run_id", "runner_receipt", "execution_result",
                 "execution_mode")


def execution_contract(doc: dict, *, archive: dict, run_id: str, executor_id: str | None, backend, artifact_root,
                       mode: str) -> dict:
    """5-B3-B execution contract: every field is READ from the request, the runner's own archive / config and the cost
    file on disk (never typed in); ``contract_problems`` fails closed on anything missing or contradictory."""
    info = I.raw_run_info(archive["files"]["run_info.txt"])
    cfg = json.loads(archive["files"]["config_canonical.json"])
    cp = doc.get("cost_profile_ref") or {}
    slip = None
    try:
        cset = json.loads((Path(artifact_root) / cp["scenario_set_path"]).read_text(encoding="utf-8"))
        slip = {k: v.get("slippage_bps") for k, v in (cset.get("scenarios") or {}).items()}
    except (OSError, KeyError, TypeError, ValueError):
        slip = None
    try:
        metrics = json.loads(archive["files"].get("metrics.json") or b"{}")
    except ValueError:
        metrics = {}
    try:
        rr = backend.runner_receipt()
    except Exception as e:  # noqa: BLE001 - no receipt => contract incomplete, never a fabricated one
        rr = {"error": f"{type(e).__name__}: {e}"}
    return {"authorization_id": info.get("authorization_id"), "request_sha256": Q.request_hash(doc),
            "strategy_spec_sha256": (cfg.get("strategy") or {}).get("spec_file_sha256"),
            "dataset_snapshot_id": info.get("snapshot_id"), "dataset_manifest_sha256": info.get("manifest_sha256"),
            "core_commit": info.get("core_git_sha"), "agent_git_sha": info.get("agent_git_sha"),
            "signal_time_rule": (doc.get("signal_time_rule") or {}).get("signal_time"),
            "execution_time_rule": (doc.get("execution_time_rule") or {}).get("rule"),
            "cost_profile": {"scenario_set_sha256": cp.get("scenario_set_sha256"), "scenarios": cp.get("scenarios"),
                             "core_profile_sha256": info.get("cost_profile_sha256")},
            "slippage_bps_per_side": slip, "executor_id": executor_id, "run_id": run_id, "runner_receipt": rr,
            "execution_result": {"archive_run_id": archive["manifest"].get("run_id"), "files": len(archive["files"]),
                                 "scenarios": sorted(metrics) if isinstance(metrics, dict) else None},
            "execution_mode": mode}


def contract_problems(contract, *, doc: dict, reservation: dict) -> list[str]:
    if not isinstance(contract, dict):
        return ["CONTRACT_MISSING"]
    bad = [f"CONTRACT_FIELD_MISSING:{k}" for k in CONTRACT_KEYS if contract.get(k) in (None, "", {}, [])]
    rid = I.request_identity(doc)
    for k, want in (("authorization_id", reservation.get("authorization_id")),
                    ("request_sha256", reservation.get("request_sha256")),
                    ("strategy_spec_sha256", rid["strategy_spec_sha256"]), ("dataset_snapshot_id", rid["dataset_snapshot_id"]),
                    ("dataset_manifest_sha256", rid["dataset_manifest_sha256"]), ("core_commit", rid["core_commit"]),
                    ("run_id", reservation.get("planned_run_id")), ("executor_id", reservation.get("worker_id"))):
        if contract.get(k) is not None and contract.get(k) != want:
            bad.append(f"CONTRACT_CONTRADICTION:{k}")
    if not re.fullmatch(r"[0-9a-f]{40}", str(contract.get("agent_git_sha") or "")):
        bad.append("CONTRACT_AGENT_GIT_SHA_NOT_FULL")
    rr = contract.get("runner_receipt") or {}
    if "error" in rr:
        bad.append("CONTRACT_RUNNER_RECEIPT_UNAVAILABLE")
    if contract.get("execution_mode") == "REAL_GATED" and not str(rr.get("verification_ref", "")).startswith(
            "DEVELOPMENT_RECEIPT_RENEWAL:"):
        bad.append("CONTRACT_RUNNER_RECEIPT_NOT_A_CORE_RENEWAL")
    if (contract.get("cost_profile") or {}).get("scenario_set_sha256") != (doc.get("cost_profile_ref") or {}).get("scenario_set_sha256"):
        bad.append("CONTRACT_CONTRADICTION:cost_profile")
    return bad


TASK_ARCHIVE_FILES = ("research_request.json", "task_events.jsonl", "governance_decision.json", "execution_reference.json",
                      "evaluation_reference.json", "research_decision.json", "run_info.txt")


def _read_regular(p: Path) -> bytes | None:
    return p.read_bytes() if p.is_file() and not p.is_symlink() else None


def task_archive_problems(task_archive: str | Path, *, r: dict, done: dict, archived_files: dict | None,
                          evaluation_reference: dict | None, governance_root: Path | None) -> tuple[list[str], dict]:
    """P0-2 residual: the TASK archive's research_request.json must be byte-identical to the request file committed
    in the ledger BEFORE execution (``request_file_sha256`` at reservation) - never only to a manifest the same
    attacker could rewrite - and must hash / bind to the reservation, receipt and binding record. The other task
    archive files must match the hashes recorded when the archive was written and the verified execution evidence.
    Nothing is ever rebuilt to make this pass."""
    root, bad, hashes = Path(task_archive), [], {}
    if root.is_symlink() or not root.is_dir():
        return ["TASK_ARCHIVE_MISSING"], {}
    present = {p.name for p in root.iterdir()}
    if present != set(TASK_ARCHIVE_FILES):
        bad.append(f"TASK_ARCHIVE_FILESET:{sorted(set(TASK_ARCHIVE_FILES) ^ present)}")
    raw = {n: _read_regular(root / n) for n in TASK_ARCHIVE_FILES}
    hashes = {n: _sha(b) for n, b in raw.items() if b is not None}
    # --- research_request.json: ledger commitment (pre-execution), content, identity chain
    req = raw["research_request.json"]
    want_file = r.get("request_file_sha256")
    if req is None:
        bad.append("TASK_ARCHIVE_REQUEST_MISSING")
    else:
        if not want_file:
            bad.append("TASK_ARCHIVE_REQUEST_UNCOMMITTED")
        elif _sha(req) != want_file:
            bad.append("TASK_ARCHIVE_REQUEST_SHA_MISMATCH")
        try:
            parsed = json.loads(req)
        except (ValueError, UnicodeDecodeError):
            parsed = None
            bad.append("TASK_ARCHIVE_REQUEST_INVALID_JSON")
        if parsed is not None:
            errs = Q.validate(parsed) if isinstance(parsed, dict) else ["not an object"]
            if errs:
                bad.append("TASK_ARCHIVE_REQUEST_SCHEMA")
            elif Q.request_hash(parsed) != r["request_sha256"]:
                bad.append("TASK_ARCHIVE_REQUEST_IDENTITY_MISMATCH")
    if governance_root is not None and want_file:                 # the commitment itself is tied to the binding
        from strategy_agent.binding.store import BindingStore  # noqa: PLC0415
        rec = BindingStore(governance_root).for_bound(r["request_sha256"])
        bound = _read_regular(Path(governance_root) / rec["bound_request_path"]) if rec else None
        if rec is None or rec["binding_id"] != r.get("binding_id") or bound is None \
                or _sha(bound) != rec["bound_request_file_sha256"]:
            bad.append("TASK_ARCHIVE_BINDING_UNVERIFIABLE")
        elif _sha(I.archive_request_bytes(json.loads(bound))) != want_file:
            bad.append("TASK_ARCHIVE_REQUEST_COMMITMENT_NOT_BOUND")
    # --- the other required evidence: hashes recorded at archive time + consistency with the verified execution
    if not archived_files:
        bad.append("TASK_ARCHIVE_HASHES_UNRECORDED")
    else:
        bad += [f"TASK_ARCHIVE_FILE_ALTERED:{n}" for n in TASK_ARCHIVE_FILES if hashes.get(n) != archived_files.get(n)]
    try:
        er = json.loads(raw["execution_reference.json"] or b"null") or {}
        if er.get("receipt_sha256") != done.get("receipt_sha256") or \
                er.get("archive_manifest_sha256") != done.get("output_manifest_sha256") or er.get("reservation_id") != r["reservation_id"]:
            bad.append("TASK_ARCHIVE_EXECUTION_REFERENCE_MISMATCH")
        ev = json.loads(raw["evaluation_reference.json"] or b"null")
        if evaluation_reference is not None and ev != json.loads(json.dumps(evaluation_reference, default=str)):
            bad.append("TASK_ARCHIVE_EVALUATION_REFERENCE_MISMATCH")
        info = I.raw_run_info(raw["run_info.txt"] or b"")
        if info.get("request_hash") != r["request_sha256"]:
            bad.append("TASK_ARCHIVE_RUN_INFO_REQUEST_MISMATCH")
    except (ValueError, UnicodeDecodeError, AttributeError):
        bad.append("TASK_ARCHIVE_EVIDENCE_UNPARSEABLE")
    return bad, hashes


def verify_execution_evidence(ledger, reservation_id: str, *, doc: dict, evaluation_reference: dict | None,
                              archive_ref: str | None, record: bool, worker_id: str | None,
                              archived_files: dict | None = None, governance_root: Path | None = None,
                              reservation: dict | None = None, use: dict | None = None,
                              expected_mode: str = "ISOLATED_SYNTHETIC") -> dict:
    """``reservation`` / ``use`` are passed only by ``BudgetLedger.settle`` from INSIDE its critical section (the
    ledger's own folded state); every other caller reads them from the ledger here."""
    bad: list[str] = []
    r = reservation
    if r is None:
        try:
            r = ledger.reservations().get(reservation_id)
        except Exception as e:  # noqa: BLE001 - unreadable ledger => not verified
            return {"ok": False, "problems": [f"LEDGER_UNREADABLE:{e}"]}
    if r is None:
        return {"ok": False, "problems": ["UNKNOWN_RESERVATION"]}
    started, done = r.get("execution_started") or {}, r.get("execution_completed") or {}
    if r["state"] != "COMMITTED" or not started or not done:
        return {"ok": False, "problems": [f"EXECUTION_NOT_COMPLETED:{r['state']}"]}
    if Q.request_hash(doc) != r["request_sha256"]:
        bad.append("REQUEST_IDENTITY_MISMATCH")
    rp = Path(done.get("receipt_path") or "")
    if not rp.is_file() or rp.is_symlink():
        return {"ok": False, "problems": bad + ["RECEIPT_MISSING"]}
    raw = rp.read_bytes()
    if _sha(raw) != done.get("receipt_sha256"):
        return {"ok": False, "problems": bad + ["RECEIPT_SHA_MISMATCH"]}
    rc = json.loads(raw)
    want = {"reservation_id": reservation_id, "task_id": r["task_id"], "attempt_id": r["attempt_id"],
            "request_sha256": r["request_sha256"], "authorization_id": r["authorization_id"],
            "binding_id": r["binding_id"], "approval_evidence_sha256": r["approval_evidence_sha256"],
            "run_id": r["planned_run_id"], "use_record_sha256": r.get("use_record_sha256"),
            "execution_started_event_sha256": started.get("record_sha256"),
            "output_manifest_sha256": done.get("output_manifest_sha256"), "consumed_batches": 1,
            "synthetic": expected_mode == "ISOLATED_SYNTHETIC", "execution_mode": expected_mode}
    bad += [f"RECEIPT_FIELD_MISMATCH:{k}" for k, val in want.items() if rc.get(k) != val]
    bad += [f"RECEIPT_CONTRACT:{p}" for p in contract_problems(rc.get("execution_contract"), doc=doc, reservation=r)]
    arch = Path(rc.get("archive_path") or "")
    try:
        archive = C.read_archive(arch, expected_manifest_sha256=done.get("output_manifest_sha256") or "")
    except C.ArchiveRejected as e:
        return {"ok": False, "problems": bad + [f"ARCHIVE_INVALID:{e.code}"]}
    if use is None and reservation is None:
        use = ledger.use_record(reservation_id)
    if use is None:
        bad.append("USE_RECORD_MISSING")
    else:
        bad += I.archive_problems(archive, doc=doc, use=use, run_id=r["planned_run_id"])
    if evaluation_reference is not None:
        ev = evaluation_reference
        if ev.get("archive_manifest_sha256") != done.get("output_manifest_sha256") or \
                Path(ev.get("archive_path") or "").resolve() != arch.resolve():
            bad.append("EVALUATION_REFERENCE_MISMATCH")
        d = Path(ev.get("dir") or "")
        for name, h in (ev.get("files_sha256") or {}).items():
            f = d / name
            if not f.is_file() or _sha(f.read_bytes()) != h:
                bad.append(f"EVALUATION_OUTPUT_ALTERED:{name}")
        if not ev.get("files_sha256"):
            bad.append("EVALUATION_OUTPUT_MISSING")
    task_hashes = None
    if archive_ref is not None:
        tp, task_hashes = task_archive_problems(archive_ref, r=r, done=done, archived_files=archived_files,
                                                evaluation_reference=evaluation_reference, governance_root=governance_root)
        bad += tp
        committed = (r.get("evidence_verified") or {}).get("task_archive_files_sha256")
        if committed is not None and committed != task_hashes:       # changed after EVIDENCE_VERIFIED
            bad.append("TASK_ARCHIVE_CHANGED_AFTER_VERIFICATION")
    out = {"ok": not bad, "problems": bad, "receipt_sha256": done.get("receipt_sha256"),
           "output_manifest_sha256": done.get("output_manifest_sha256"), "task_archive_files_sha256": task_hashes}
    if not bad and record:
        ledger.record_evidence_verified(reservation_id, worker_id=worker_id, receipt_sha256=done["receipt_sha256"],
                                        output_manifest_sha256=done["output_manifest_sha256"],
                                        archive_ref=archive_ref or str(arch),
                                        evaluation_input_sha256=(evaluation_reference or {}).get("input_sha256"),
                                        task_archive_files_sha256=task_hashes)
    return out


def _fixture_identity(archive_dir: Path) -> dict:
    """Execution identity a fixture archive WOULD produce (its own run_info / config_canonical)."""
    info = I.raw_run_info((Path(archive_dir) / "run_info.txt").read_bytes())
    cfg = json.loads((Path(archive_dir) / "config_canonical.json").read_text(encoding="utf-8"))
    st = cfg.get("strategy") or {}
    return {"authorization_id": info.get("authorization_id"), "strategy_id": st.get("strategy_id"),
            "strategy_version": st.get("version"), "strategy_parameters": st.get("parameters"),
            "strategy_spec_sha256": st.get("spec_file_sha256"), "core_commit": info.get("core_git_sha"),
            "dataset_snapshot_id": info.get("snapshot_id"), "dataset_manifest_sha256": info.get("manifest_sha256"),
            "cost_profile_sha256": (cfg.get("costs") or {}).get("scenario_set_sha256"),
            "temporal_policy_sha256": info.get("temporal_policy_sha256"), "research_window": cfg.get("windows")}


class SyntheticFixtureBackend:
    """Labelled SYNTHETIC_FIXTURE archive (produced earlier by the real Phase 4-A producer). No data, no PnL code.
    Its execution identity is the fixture's own (DEV authorization / snapshot / manifest / Core): a request with any
    other identity is BLOCKED before the backend runs (P0-3)."""
    kind = "SYNTHETIC_FIXTURE_ARCHIVE"
    synthetic = True

    def __init__(self, fixture_archive: Path, *, fail_with: str | None = None, drop_file: str | None = None):
        self.fixture = Path(fixture_archive)
        self.fail_with, self.drop_file = fail_with, drop_file      # crash / incomplete-archive injection for tests
        self.calls = 0

    def execution_identity(self) -> tuple[dict, list[str]]:
        return _fixture_identity(self.fixture), []

    def runner_receipt(self) -> dict:
        return {"kind": "SYNTHETIC_FIXTURE_NO_CORE_RECEIPT", "verification_ref": "NONE:SYNTHETIC_FIXTURE"}

    def planned_run_id(self, task_id: str, now: datetime) -> str:
        """The fixture's own run id (its MANIFEST binds it): one synthetic execution per ledger (RUN_ID_ALREADY_USED)."""
        return json.loads((self.fixture / "MANIFEST.json").read_text(encoding="utf-8"))["run_id"]

    def precheck(self, doc: dict) -> list[str]:
        return [] if (self.fixture / "MANIFEST.json").is_file() else ["SYNTHETIC_FIXTURE_MISSING"]

    def run(self, doc: dict, out_dir: Path, *, use: dict) -> dict:
        self.calls += 1
        if self.fail_with:
            raise RuntimeError(self.fail_with)
        dst = Path(out_dir) / use["run_id"]
        shutil.copytree(self.fixture, dst)
        if self.drop_file:
            (dst / self.drop_file).unlink()
        return {"archive_path": str(dst), "identity": {"fixture": self.fixture.name, "label": "SYNTHETIC_FIXTURE"}}


class SyntheticStubBackend:
    """Layer A (no Core) stand-in for a runner, for ledger / evidence mechanics ONLY.

    It is CONFIGURED with an execution identity (``identity``, built by the test world from the authorization record,
    the artefact files and the synthetic dataset pins - never from the request being executed) and writes a
    contract-shaped SYNTHETIC_STUB archive: the labelled fixture's artefacts with run_info / config_canonical
    re-stamped with that configured identity and the authoritative uses record it was granted, and a fresh MANIFEST.
    It is NOT producer output and carries no new PnL: the Phase 4-C evaluator is expected to reject it. The producer
    success path is evidenced only by Layer B (the existing ResearchOnlyRunner + Core on a SYNTHETIC snapshot).
    Injection knobs: ``fail_with`` (crash after start), ``drop_file`` (incomplete archive), ``stamp`` (override output
    identity fields, i.e. a runner result inconsistent with the request), ``calls_file`` (cross-process call counter).
    """
    kind = "SYNTHETIC_STUB_IDENTITY_CONFIGURED"
    synthetic = True

    def __init__(self, fixture_archive: Path, *, identity: dict, fail_with: str | None = None,
                 drop_file: str | None = None, stamp: dict | None = None, calls_file: Path | None = None,
                 agent_git_sha: str = "5" * 40):
        self.fixture, self.identity = Path(fixture_archive), dict(identity)
        self.fail_with, self.drop_file, self.stamp = fail_with, drop_file, dict(stamp or {})
        self.calls_file = Path(calls_file) if calls_file else None
        self._calls = 0
        self.agent_git_sha = agent_git_sha

    @property
    def calls(self) -> int:
        if self.calls_file is not None:
            return len(self.calls_file.read_text().splitlines()) if self.calls_file.exists() else 0
        return self._calls

    def _count(self):
        self._calls += 1
        if self.calls_file is not None:
            with open(self.calls_file, "a", encoding="utf-8") as fh:
                fh.write(f"{os.getpid()}\n")
                fh.flush()
                os.fsync(fh.fileno())

    def execution_identity(self) -> tuple[dict, list[str]]:
        return dict(self.identity), []

    def runner_receipt(self) -> dict:
        return {"kind": "SYNTHETIC_STUB_NO_CORE_RECEIPT", "verification_ref": "NONE:SYNTHETIC_STUB"}

    def planned_run_id(self, task_id: str, now: datetime) -> str:
        return "SYNSTUB_" + _sha(task_id.encode())[:16]

    def precheck(self, doc: dict) -> list[str]:
        return [] if (self.fixture / "MANIFEST.json").is_file() else ["SYNTHETIC_FIXTURE_MISSING"]

    def run(self, doc: dict, out_dir: Path, *, use: dict) -> dict:
        self._count()
        if self.fail_with:
            raise RuntimeError(self.fail_with)
        dst = Path(out_dir) / use["run_id"]
        shutil.copytree(self.fixture, dst)
        idn = {**self.identity, **self.stamp}
        q = json.dumps                                # JSON-quoted: a digits-only sha stays a string for every parser
        info_over = {"run_id": q(use["run_id"]), "authorization_id": q(idn["authorization_id"]),
                     "core_git_sha": q(idn["core_commit"]), "snapshot_id": q(idn["dataset_snapshot_id"]),
                     "manifest_sha256": q(idn["dataset_manifest_sha256"]),
                     "temporal_policy_sha256": q(idn["temporal_policy_sha256"]),
                     "authorization_use": json.dumps(use, sort_keys=True), "holdout_accessed": "false",
                     "agent_git_sha": q(self.agent_git_sha),
                     "notebook": "SYNTHETIC_STUB", "synthetic_stub": "true"}
        lines, seen = [], set()
        for ln in (dst / "run_info.txt").read_text(encoding="utf-8").splitlines():
            k = ln.split("=", 1)[0]
            if k in info_over:
                if k in seen:
                    continue
                seen.add(k)
                ln = f"{k}={info_over[k]}"
            lines.append(ln)
        lines += [f"{k}={v}" for k, v in info_over.items() if k not in seen]
        (dst / "run_info.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        cfg = json.loads((dst / "config_canonical.json").read_text(encoding="utf-8"))
        cfg["strategy"].update(strategy_id=idn["strategy_id"], version=idn["strategy_version"],
                               parameters=idn["strategy_parameters"], spec_file_sha256=idn["strategy_spec_sha256"])
        cfg["costs"]["scenario_set_sha256"] = idn["cost_profile_sha256"]
        cfg["windows"] = {**cfg.get("windows", {}), **idn["research_window"]}
        cfg["authorization_id"] = idn["authorization_id"]
        (dst / "config_canonical.json").write_text(json.dumps(cfg, indent=2, sort_keys=True), encoding="utf-8")
        m = json.loads((dst / "MANIFEST.json").read_text(encoding="utf-8"))
        m["run_id"] = use["run_id"]
        m["label"] = "SYNTHETIC_STUB (re-stamped fixture; not producer output)"
        m["files_sha256"] = {n: _sha((dst / n).read_bytes()) for n in m["files_sha256"]}
        (dst / "MANIFEST.json").write_text(json.dumps(m, indent=2, sort_keys=True), encoding="utf-8")
        if self.drop_file:
            (dst / self.drop_file).unlink()
        return {"archive_path": str(dst), "identity": {"label": "SYNTHETIC_STUB", "fixture": self.fixture.name}}


class ResearchOnlyRunnerBackend:
    """The existing ResearchOnlyRunner, split around the ledger commit (never ``runner.run()``)."""
    kind = "RESEARCH_ONLY_RUNNER_SYNTHETIC_SNAPSHOT"

    def __init__(self, runner, *, config_path: Path, snapshot_root: Path, git_commit: str, synthetic_snapshot: bool,
                 run_context: dict | None = None, core=None, core_costs=None, core_native=None,
                 canonical_config_path: Path | None = None):
        self.runner, self.config_path, self.snapshot_root = runner, Path(config_path), Path(snapshot_root)
        self.git_commit, self.run_context = git_commit, dict(run_context or {})
        self.synthetic = bool(synthetic_snapshot)
        if not self.synthetic:
            self.kind = "RESEARCH_ONLY_RUNNER_REAL_SNAPSHOT"
        # 5-B3-B real snapshot: ``config_path`` is the SESSION config produced by the existing Core receipt renewal
        # (research_only.development_run.session_config) and must differ from the signed canonical config ONLY in
        # the integrity receipt; a hand-made receipt never passes.
        self.canonical_config_path = Path(canonical_config_path) if canonical_config_path else None
        self.core, self.core_costs, self.core_native = core, core_costs, core_native
        self.calls = 0

    def _core(self):
        if self.core is None:
            from strategy_agent.adapters.mrc_adapter import _core  # noqa: PLC0415
            self.core = _core()
        if self.core_costs is None or self.core_native is None:
            from strategy_agent.adapters.mrc_phase4a import core_phase4a  # noqa: PLC0415
            n, c = core_phase4a()
            self.core_native, self.core_costs = self.core_native or n, self.core_costs or c

    def planned_run_id(self, task_id: str, now: datetime) -> str:
        from strategy_agent.research_only.runner import TAIPEI, run_id  # noqa: PLC0415
        return run_id(now.astimezone(TAIPEI), self.git_commit)

    def execution_identity(self) -> tuple[dict, list[str]]:
        """What the runner WILL execute, from its own config + the files on disk + the INSTALLED Core (P0-3).
        Never derived from the request; any internal inconsistency is reported as a problem."""
        cfg, _ = self.runner.load_config(self.config_path)
        root, bad = Path(self.runner.root), []
        st, sb = cfg["strategy"], cfg["snapshot_binding"]
        spec_sha = I._sha_file(root / st["spec_path"])                       # noqa: SLF001
        if spec_sha != st.get("spec_file_sha256"):
            bad.append("RUNNER_CONFIG_SPEC_FILE_SHA")
        cost_sha = I._sha_file(root / cfg["costs"]["scenario_set_path"])       # noqa: SLF001
        if cost_sha != cfg["costs"].get("scenario_set_sha256"):
            bad.append("RUNNER_CONFIG_COST_FILE_SHA")
        man_sha = I._sha_file(self.snapshot_root / sb.get("manifest_relative_path", "manifest.json"))  # noqa: SLF001
        if man_sha is None or man_sha != sb.get("manifest_sha256"):
            bad.append("RUNNER_SNAPSHOT_MANIFEST_SHA")                    # the snapshot actually read, never 'latest'
        if self.snapshot_root.name != cfg["dataset"].get("snapshot_id"):
            bad.append("RUNNER_SNAPSHOT_ROOT_ID")
        if not self.synthetic:
            bad += self._session_config_problems(cfg)
        self._core()
        installed = self.core_native.core_code_identity().get("core_git_sha")
        if installed != cfg["core"].get("phase4a_sha"):
            bad.append("RUNNER_INSTALLED_CORE_DIFFERS_FROM_CONFIG")
        return ({"authorization_id": cfg.get("authorization_id"), "strategy_id": st.get("strategy_id"),
                 "strategy_version": st.get("version"), "strategy_parameters": st.get("parameters"),
                 "strategy_spec_sha256": spec_sha, "core_commit": installed,
                 "dataset_snapshot_id": cfg["dataset"].get("snapshot_id"), "dataset_manifest_sha256": man_sha,
                 "cost_profile_sha256": cost_sha, "temporal_policy_sha256": (cfg.get("temporal_policy") or {}).get("sha256"),
                 "research_window": cfg.get("windows")}, bad)

    def _session_config_problems(self, cfg: dict) -> list[str]:
        from strategy_agent.research_only.development_run import _strip, canonical_sha  # noqa: PLC0415
        if self.canonical_config_path is None or not self.canonical_config_path.is_file():
            return ["RUNNER_CANONICAL_CONFIG_REQUIRED"]
        canon = json.loads(self.canonical_config_path.read_text(encoding="utf-8"))
        bad = []
        if canon["provenance"].get("config_sha256") != canonical_sha(canon):
            bad.append("RUNNER_CANONICAL_CONFIG_SELF_HASH")
        if cfg["provenance"].get("config_sha256") != canonical_sha(cfg):
            bad.append("RUNNER_SESSION_CONFIG_SELF_HASH")
        if _strip(cfg) != _strip(canon):
            bad.append("RUNNER_SESSION_CONFIG_DIFFERS_BEYOND_RECEIPT")
        ref = str(((cfg.get("snapshot_binding") or {}).get("integrity_receipt") or {}).get("verification_ref", ""))
        if not ref.startswith("DEVELOPMENT_RECEIPT_RENEWAL:"):
            bad.append("RUNNER_RECEIPT_NOT_RENEWED_BY_CORE")
        return bad

    def runner_receipt(self) -> dict:
        """The Core integrity receipt the runner will rely on (from its own config; never fabricated)."""
        cfg, _ = self.runner.load_config(self.config_path)
        sb = cfg.get("snapshot_binding") or {}
        r = sb.get("integrity_receipt") or {}
        return {"verification_ref": r.get("verification_ref"), "parquet_sha256": r.get("parquet_sha256"),
                "manifest_sha256": r.get("manifest_sha256"), "integrity_receipt_sha256": sb.get("integrity_receipt_sha256"),
                "config_sha256": (cfg.get("provenance") or {}).get("config_sha256")}

    def precheck(self, doc: dict) -> list[str]:
        """The runner's own preflight/probe (before any budget). Identity is compared by ``identity.verify_request``
        against this backend's ``execution_identity``; the probe adds the runner's own governance view."""
        bad = []
        self._core()
        probe = self.runner.probe(self.config_path, snapshot_root=self.snapshot_root, core_costs=self.core_costs,
                                  core_native=self.core_native)
        if probe["status"] != "COMPATIBLE_READY_TO_CONSUME" or probe["blockers"] or probe["slot_consumed"]:
            bad.append(f"RUNNER_PROBE:{probe['status']}:{[b.get('code') if isinstance(b, dict) else b for b in probe['blockers']]}")
        return bad

    def run(self, doc: dict, out_dir: Path, *, use: dict) -> dict:
        self.calls += 1
        self._core()
        r = self.runner
        idn, bad = self.execution_identity()                       # re-read right before Core runs (TOCTOU)
        bad += I.compare(I.request_identity(doc), idn, source="BACKEND")
        if bad:
            raise ExecutionFailed("RUNNER_IDENTITY_CHANGED", "; ".join(bad))
        cfg, _ = r.load_config(self.config_path)
        ident = self.core_native.core_code_identity()
        dc = r.data_checks(cfg, snapshot_root=self.snapshot_root, core=self.core, core_costs=self.core_costs,
                           core_identity=ident)
        if dc["blockers"]:
            raise ExecutionFailed("RUNNER_DATA_CHECKS", str(dc["blockers"]))
        base = {"core_identity": ident, "probe": dc["probe"].as_dict(), "controlled": True}
        dst = Path(out_dir) / use["run_id"]
        out = r._execute(cfg, use["run_id"], use, dst, dc, ident, base, self.core, self.core_costs, self.core_native,
                         run_context=self.run_context, started_at=r.clock())
        if not out.get("executed"):
            raise ExecutionFailed("RUNNER_NOT_EXECUTED", str(out)[:300])
        return {"archive_path": out["archive_dir"], "identity": {"core_git_sha": ident.get("core_git_sha"),
                                                                 "agent_git_sha": self.git_commit}}
