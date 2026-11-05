"""Agent-side validation of what Core returns (M3_CORE_AGENT_INTEGRATION.md §4).

Called inside the instrumented port right after ``ExecutionEngine.run`` returns, so a mismatch is raised before
the M2 runner records the run: the registry then records FAILED with the reason (fail closed), never COMPLETED.

The Agent does not know (and does not guess) field names Core has not released. Checks that need a Core field
the pinned Core does not provide are PENDING for SYNTHETIC runs and blocking for REAL_MARKET runs.
"""
from __future__ import annotations

from dataclasses import dataclass, field

TEMPORAL_FIELD_PENDING = "temporal policy id/sha in Core provenance (released by Core M3-A.4; absent here)"


class CoreContractMismatch(RuntimeError):
    pass


@dataclass
class ContractResult:
    checks: dict = field(default_factory=dict)      # id -> PASS | FAIL | PENDING
    messages: list = field(default_factory=list)

    @property
    def failed(self) -> list:
        return [k for k, v in self.checks.items() if v == "FAIL"]


def check(report, *, job, engine_git_sha: str, real_market: bool) -> ContractResult:
    r = ContractResult()
    p = dict(report.provenance or {})
    ds = (job.spec.get("data_requirements", {}).get("datasets") or [{}])[0]

    def put(cid, ok, msg):
        r.checks[cid] = "PASS" if ok else "FAIL"
        if not ok:
            r.messages.append(f"{cid}: {msg}")

    # CC01 engine identity
    put("CC01_engine_git_sha", p.get("engine_git_sha") == engine_git_sha,
        f"engine_git_sha {p.get('engine_git_sha')} != benchmarked {engine_git_sha}")
    # CC02 cost profile chain: agent profile pinned by the spec / scenario, Core hash of what it executed
    want = (job.scenario or {}).get("sha256") or (job.spec.get("costs", {}).get("profile_ref") or {}).get("sha256")
    put("CC02_agent_cost_profile_sha256", p.get("agent_cost_profile_sha256") == want,
        f"agent cost profile {p.get('agent_cost_profile_sha256')} != pinned {want}")
    put("CC02_core_cost_profile_sha256", bool(p.get("cost_profile_sha256")), "Core did not return a cost profile hash")
    # CC03 dataset snapshot / producer
    put("CC03_dataset_snapshot", p.get("dataset_snapshot_id") == ds.get("snapshot_id"),
        f"snapshot {p.get('dataset_snapshot_id')} != pinned {ds.get('snapshot_id')}")
    put("CC03_dataset_producer", p.get("dataset_producer_sha") == ds.get("producer_commit_sha"),
        f"producer {p.get('dataset_producer_sha')} != pinned {ds.get('producer_commit_sha')}")
    # CC04 spec identity
    put("CC04_spec_sha256", bool(p.get("spec_sha256")), "spec sha256 missing from Core provenance")
    # CC05 temporal policy (Core-enforced; the Agent only cross-checks)
    want_tp = ((job.config or {}).get("temporal_policy") or {}).get("policy_id")
    got_tp = p.get("temporal_policy_id")
    if got_tp is None:
        r.checks["CC05_temporal_policy"] = "FAIL" if real_market else "PENDING"
        if real_market:
            r.messages.append(f"CC05_temporal_policy: {TEMPORAL_FIELD_PENDING}; REAL_MARKET runs need it")
    else:
        want_sha = ((job.config or {}).get("temporal_policy") or {}).get("sha256")
        got_sha = p.get("temporal_policy_sha256")
        put("CC05_temporal_policy", got_tp == want_tp and (want_sha is None or got_sha == want_sha),
            f"Core temporal policy {got_tp} sha {str(got_sha)[:12]} != config {want_tp} sha {str(want_sha)[:12]}")
        # CC08 Core M3-A.4 conservative research model: label stays UNKNOWN, scope RESEARCH_ONLY, never formal
        if got_tp == "CONSERVATIVE_LABEL_AGNOSTIC":
            put("CC08_conservative_research_only",
                p.get("source_timestamp_label") == "UNKNOWN" and p.get("execution_eligibility_scope") ==
                "RESEARCH_ONLY" and p.get("core_status") == "RESEARCH_ONLY",
                f"label {p.get('source_timestamp_label')} / scope {p.get('execution_eligibility_scope')} / "
                f"status {p.get('core_status')}")
    # CC06 market eligibility: real-market research needs Core's formal eligibility
    if real_market:
        put("CC06_core_status", p.get("core_status") == "FORMAL_ELIGIBLE",
            f"Core status {p.get('core_status')} (real-market research needs FORMAL_ELIGIBLE)")
    else:
        r.checks["CC06_core_status"] = "PASS" if p.get("core_status") in ("RESEARCH_ONLY", "FORMAL_ELIGIBLE") else "FAIL"
        if r.checks["CC06_core_status"] == "FAIL":
            r.messages.append(f"CC06_core_status: unknown Core status {p.get('core_status')}")
    # CC07 cost multiplier actually applied is Core's job; the Agent checks it asked for a pre-registered one
    allowed = [float(m) for m in job.spec.get("costs", {}).get("sensitivity_multipliers", [])]
    put("CC07_cost_multiplier_preregistered", float(job.cost_multiplier) in allowed,
        f"cost multiplier {job.cost_multiplier} not in {allowed}")
    return r


def enforce(report, *, job, engine_git_sha: str, real_market: bool) -> ContractResult:
    r = check(report, job=job, engine_git_sha=engine_git_sha, real_market=real_market)
    if r.failed:
        raise CoreContractMismatch("; ".join(r.messages))
    return r
