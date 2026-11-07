"""Pre-execution governance for a research request. Read-only; reuses the existing governed modules.

Nothing here approves, consumes, revokes or edits anything: ResearchAuthorizationGuard.check (never .consume),
HoldoutBook, market_readiness.json, spec.validate, research_only.costs.CostSet and the Core benchmark record
(evaluated by ``adapters.mrc_adapter.engine_benchmark_status`` in a SUBPROCESS so the orchestrator process never
loads an execution module). Any failure => BLOCKED with the exact reasons.

G1 request schema + semantics            G6 authorization scope (RA03–RA10, RA12; RA02 for real runs)
G2 strategy spec file, hash, validity    G7 research budget (real: slot free; replay: exactly the replayed run's use)
G3 dataset snapshot / manifest explicit  G8 holdout lock exists, matches, no access
G4 Core SHA benchmark + cost set valid   G9 formal gate recorded; NO_GO is never a trading permission
G5 authorization exists (not PENDING)    G10 execution mode enabled (RESEARCH_ONLY_REAL is DISABLED in 5-A1)
G11 (5-B1) authorization binding: a RESEARCH_ONLY_REAL request must be a registered bound request
    (binding.store); an ARCHIVE_REPLAY request is NOT_APPLICABLE unless it is itself a bound request.

Check status is PASS | FAIL | NOT_APPLICABLE | NOT_EVALUATED. A decision is PASS only if no check is FAIL or
NOT_EVALUATED; a SYNTHETIC_TEST never evaluates G5 (NOT_EVALUATED) and is at most TEST_MODE_ONLY.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from strategy_agent.governance.holdout import HoldoutBook
from strategy_agent.governance.research_authorization import (ResearchAuthorizationBook, ResearchAuthorizationGuard,
                                                              ResearchRequest)

from . import request as Q

REAL_EXECUTION_ENABLED = False          # Phase 5-A1: never. Enabling requires a separate authorization + acceptance.


@dataclass
class GovernanceDecision:
    status: str                          # PASS | BLOCKED | TEST_MODE_ONLY
    mode: str
    checks: list = field(default_factory=list)       # {"id","status","detail"}
    consumes_budget: bool = False
    formal_market_status: str | None = None
    trading_permission: bool = False

    def as_dict(self) -> dict:
        return {"status": self.status, "mode": self.mode, "checks": self.checks, "consumes_budget": self.consumes_budget,
                "formal_market_status": self.formal_market_status, "trading_permission": False,
                "note": "RESEARCH-ONLY governance. A PASS permits a read-only/research-only task, never trading; "
                        "Formal Market Eligibility is not evaluated or changed here."}

    @property
    def blockers(self) -> list:
        return [c for c in self.checks if c["status"] == "FAIL"]


def file_sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def core_benchmark_status(core_sha: str, root: Path) -> dict:
    """Existing rule, evaluated out of process (keeps this process free of execution modules)."""
    code = ("import json,sys;from pathlib import Path;from strategy_agent.adapters.mrc_adapter import engine_benchmark_status as f;"
            "print(json.dumps(f(sys.argv[1], Path(sys.argv[2]))))")
    r = subprocess.run([sys.executable, "-c", code, core_sha, str(Path(root) / "benchmark" / "results")],
                       capture_output=True, text=True, cwd=root, timeout=120)
    if r.returncode != 0:
        return {"status": "NOT_VERIFIED", "why": "benchmark status check failed: " + r.stderr.strip()[-300:]}
    return json.loads(r.stdout.strip().splitlines()[-1])


class Governance:
    def __init__(self, root: Path, *, governance_root: Path | None = None, clock=None, benchmark=None):
        self.root = Path(root)                         # code + specs + configs + benchmark records
        g = Path(governance_root or root)              # governed books (repo, or an injected fixture)
        self.paths = {"auth": g / "governance/research_authorizations.jsonl", "uses": g / "registry/research_authorization_uses.jsonl",
                      "locks": g / "governance/holdout_locks.jsonl", "approvers": g / "governance/approvers.json",
                      "readiness": g / "governance/market_readiness.json"}
        self.clock = clock
        self.benchmark = benchmark or (lambda sha: core_benchmark_status(sha, self.root))
        self.governance_root = g
        self.production_books = g.resolve() == Q.ROOT.resolve()     # the repository's own governed books

    def approvers(self) -> set[str]:
        return set(json.loads(self.paths["approvers"].read_text(encoding="utf-8")).get("research_leads", []))

    def uses(self, authorization_id: str) -> list[dict]:
        from strategy_agent.registry.chain import ChainedLog  # noqa: PLC0415
        p = self.paths["uses"]
        return [u for u in ChainedLog(p).verify() if u.get("authorization_id") == authorization_id] if p.exists() else []

    def check(self, doc: dict, *, test_mode: bool = False, binding_check: bool = True) -> GovernanceDecision:
        mode = doc.get("execution_mode") if isinstance(doc, dict) else None
        out: list = []

        def add(cid, ok, detail, status=None):
            out.append({"id": cid, "status": status or ("PASS" if ok else "FAIL"), "detail": detail})

        errs = Q.validate(doc)
        add("G1_REQUEST_SCHEMA", not errs, errs or "valid")
        if errs:
            return GovernanceDecision("BLOCKED", str(mode), out)
        if mode == "SYNTHETIC_TEST":
            add("G0_TEST_MODE", test_mode, "SYNTHETIC_TEST is accepted only by an isolated test orchestrator" if not test_mode
                else "isolated test orchestrator")
            add("G5_AUTHORIZATION", False, "a synthetic test is never authorized research", status="NOT_EVALUATED")
            ok = test_mode and not [c for c in out if c["status"] == "FAIL"]
            return GovernanceDecision("TEST_MODE_ONLY" if ok else "BLOCKED", mode, out)
        if test_mode:
            add("G0_TEST_MODE", False, "an isolated test orchestrator accepts SYNTHETIC_TEST requests only")
            return GovernanceDecision("BLOCKED", mode, out)

        # G2 strategy spec
        spec_path = self.root / doc["strategy_spec_ref"]
        if not spec_path.is_file():
            add("G2_STRATEGY_SPEC", False, f"spec not found: {doc['strategy_spec_ref']}")
        else:
            from strategy_agent.spec.validate import validate as spec_validate  # noqa: PLC0415
            sha = file_sha(spec_path)
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
            issues = [str(i) for i in spec_validate(spec) if i.level == "ERROR"]
            sf = doc["strategy_family"]
            ident_ok = spec.get("strategy_id") == sf["strategy_id"] and str(spec.get("version")) == sf["version"]
            add("G2_STRATEGY_SPEC", sha == doc["strategy_spec_sha256"] and not issues and ident_ok,
                {"file_sha256": sha, "matches_request": sha == doc["strategy_spec_sha256"], "errors": issues,
                 "identity_matches": ident_ok})
        # G3 dataset explicit (schema guarantees full hashes); scope match is RA04 under G6
        add("G3_DATASET", True, {"snapshot_id": doc["dataset_snapshot_id"], "manifest_sha256": doc["dataset_manifest_sha256"],
                                 "dataset_id": doc["dataset_ref"]["dataset_id"]})
        # G4 Core + costs
        core = doc["core_commit"]
        if core == "PENDING":
            add("G4_CORE_COST", False, "core_commit PENDING")
        else:
            bench = self.benchmark(core)
            cpath = self.root / doc["cost_profile_ref"]["scenario_set_path"]
            cost_ok, cost_detail = False, "scenario set missing"
            if cpath.is_file():
                from strategy_agent.research_only.costs import CostSet, CostSetError  # noqa: PLC0415 - pure loader
                try:
                    cs = CostSet(cpath, root=self.root)
                    missing = sorted(set(doc["cost_profile_ref"]["scenarios"]) - set(cs.raw["scenarios"]))
                    cost_ok = file_sha(cpath) == doc["cost_profile_ref"]["scenario_set_sha256"] and not missing
                    cost_detail = {"file_sha256": file_sha(cpath), "unknown_scenarios": missing}
                except (CostSetError, KeyError, ValueError) as e:
                    cost_detail = f"cost set invalid: {e}"
            add("G4_CORE_COST", bench.get("status") == "BENCHMARK_PASS" and cost_ok,
                {"core_commit": core, "benchmark": bench, "cost_set": cost_detail})
        # G5 authorization
        aref = doc["authorization_ref"]
        auth = None
        if aref == "PENDING":
            add("G5_AUTHORIZATION", False, "authorization_ref PENDING (no authorization is ever created here)")
        else:
            book = ResearchAuthorizationBook(self.paths["auth"], self.paths["approvers"])
            auth = book.get(aref["authorization_id"])
            add("G5_AUTHORIZATION", auth is not None, "approved, not revoked" if auth else
                f"no active approved authorization {aref['authorization_id']}")
        readiness = json.loads(self.paths["readiness"].read_text(encoding="utf-8"))
        market = readiness.get("markets", {}).get(doc["market"]["market_id"])
        formal = (market or {}).get("status")
        if auth is not None and market is not None:
            holdout = HoldoutBook(self.paths["locks"], self.paths["approvers"])
            guard = ResearchAuthorizationGuard(book, holdout, market, self.paths["uses"], clock=self.clock)
            w = doc["research_window"]
            rr = ResearchRequest(authorization_id=aref["authorization_id"], market_id=doc["market"]["market_id"],
                                 symbol=doc["market"]["symbol"],
                                 dataset={"dataset_id": doc["dataset_ref"]["dataset_id"], "snapshot_id": doc["dataset_snapshot_id"],
                                          "producer_commit_sha": doc["dataset_ref"]["producer_commit_sha"]},
                                 temporal_policy_sha256=doc["signal_time_rule"]["policy_sha256"],
                                 scenario_set_sha256=doc["cost_profile_ref"]["scenario_set_sha256"],
                                 scenarios=tuple(doc["cost_profile_ref"]["scenarios"]),
                                 spec_file_sha256=doc["strategy_spec_sha256"], strategy_id=doc["strategy_family"]["strategy_id"],
                                 strategy_version=doc["strategy_family"]["version"],
                                 parameters=dict(doc["strategy_family"]["parameters"]), warmup_start=w["warmup_start"],
                                 window_start=w["start"], window_end=w["end"])
            dec = guard.check(rr)                 # pure; consume() is never called by the orchestrator
            ignore = {"RA11"} | ({"RA02"} if mode == "ARCHIVE_REPLAY" else set())
            scope = [f"{c}: {m}" for c, m in dec.failures if c not in ignore and c != "RA09"]
            add("G6_AUTHORIZATION_SCOPE", not scope, scope or "RA03-RA08, RA10, RA12 PASS" +
                ("; RA02 not applied to a read-only replay of a run made while the authorization was valid"
                 if mode == "ARCHIVE_REPLAY" else ""))
            uses = self.uses(aref["authorization_id"])
            if mode == "ARCHIVE_REPLAY":
                rid = doc["replay_source"]["run_id"]
                ok = len(uses) == 1 and uses[0].get("run_id") == rid and doc["research_budget"]["batches_requested"] == 0
                add("G7_BUDGET", ok, {"uses": f"{len(uses)}/{auth['max_batches']}", "replayed_run_consumed_the_slot": ok,
                                      "replay_consumes": 0})
            else:
                ok = len(uses) < auth["max_batches"]
                add("G7_BUDGET", ok, {"uses": f"{len(uses)}/{auth['max_batches']}"})
            h = [m for c, m in dec.failures if c == "RA09"]
            lock_ok = doc["holdout_policy"]["lock_id"] == auth["holdout"]["lock_id"] and not h
            add("G8_HOLDOUT", lock_ok and doc["holdout_policy"]["access"] == "NO_ACCESS",
                {"lock_id": auth["holdout"]["lock_id"], "blindness": auth["holdout"].get("blindness"), "ra09": h or "PASS"})
        elif auth is not None:
            add("G6_AUTHORIZATION_SCOPE", False, f"market {doc['market']['market_id']} not in market_readiness.json")
        add("G9_FORMAL_GATE", formal is not None, {"formal_market_status": formal, "trading_permission": False,
                                                   "meaning": "recorded only; NO_GO stays NO_GO and is never read as a "
                                                              "trading permission"})
        enabled = mode == "ARCHIVE_REPLAY" or (mode == "RESEARCH_ONLY_REAL" and REAL_EXECUTION_ENABLED)
        add("G10_EXECUTION_MODE", enabled, "ARCHIVE_REPLAY (read-only, 0 batches)" if mode == "ARCHIVE_REPLAY"
            else "RESEARCH_ONLY_REAL is DISABLED in Phase 5-A1 (needs a separate user authorization and acceptance)")
        if binding_check:
            self._g11(doc, mode, add)
        status = "PASS" if all(c["status"] in ("PASS", "NOT_APPLICABLE") for c in out) else "BLOCKED"
        return GovernanceDecision(status, mode, out, consumes_budget=mode == "RESEARCH_ONLY_REAL",
                                  formal_market_status=formal)

    def _g11(self, doc: dict, mode, add) -> None:
        from strategy_agent.binding.store import BindingStore  # noqa: PLC0415 - read-only
        store = BindingStore(self.governance_root)
        if mode == "ARCHIVE_REPLAY":
            ok, detail = store.verify_bound(doc, require_production=self.production_books)
            if detail.get("binding_id") is None and "no binding record" in str(detail.get("error", "")):
                add("G11_AUTHORIZATION_BINDING", True, "read-only replay of a run made under its authorization; "
                    "no new authorization is bound", status="NOT_APPLICABLE")
            else:
                add("G11_AUTHORIZATION_BINDING", ok, detail)
            return
        ok, detail = store.verify_bound(doc, require_production=self.production_books)
        add("G11_AUTHORIZATION_BINDING", ok, detail)
