"""M1 integration adapter: the ONLY module of the Strategy Agent that knows ``market_research_core``.

Responsibilities (Phase 3 M1):
  * Strategy Spec + BacktestRequest  -> Core execution request   (``build_core_request``)
  * Signal decisions                 -> Core target orders        (``decisions_to_targets``)
  * Core result                      -> ``ports.BacktestReport``  (``core_result_to_report``)
  * provenance transfer, rejection-reason mapping, dataset / snapshot / CostProfile / engine-SHA validation
  * a capability matrix measured against the REAL installed Core (``compatibility_matrix``)

It never simulates fills, computes formal PnL, edits eligibility, fills bars, stitches futures contracts or
calls the benchmark oracle. When the installed Core cannot express a request, the adapter raises
``CoreContractGap`` (fail-closed); it does not substitute an execution engine of its own.

Core state at M1 (verified, not assumed): the only execution entry point is
``BacktestEngine.run_sma(UnifiedBars, BacktestAssumptions)`` on branch ``feature/phase2-unified-data-backtest``
(``f4276cb``) - a long-only SMA integration probe. The generic Phase 2.1 Execution Engine (Codex) is not
published yet; ``CORE_EXECUTION_REQUEST_V0`` / ``CORE_EXECUTION_RESULT_V0`` below are the Agent-side contract
proposal it would have to satisfy (see M1_INTEGRATION.md, contract gaps G01-G15).
"""
from __future__ import annotations

import inspect
import json
import re
import subprocess
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from importlib import metadata
from pathlib import Path
from typing import Any, Mapping, Sequence

from strategy_agent.costs.profile import cost_mismatches, profile_hash
from strategy_agent.ports import (CONVENTION, REQUIRED_PROVENANCE, BacktestReport, BacktestRequest,
                                  ContractViolation, DatasetRef)

ADAPTER_VERSION = "m1-0.1.0"
CORE_DIST = "market-research-core"
ALLOWED_DATASET_VERDICTS = {"GO", "SCOPED_GO", "PASS"}
TS_FMT = "%Y-%m-%d %H:%M"


class CoreContractGap(RuntimeError):
    """The installed Core cannot execute this request without the adapter inventing behaviour."""

    def __init__(self, gaps: Sequence[str], context: str = ""):
        self.gaps = list(gaps)
        super().__init__(f"INTEGRATION_PENDING: core lacks {', '.join(self.gaps)}" + (f" ({context})" if context else ""))


# ------------------------------------------------------------------------------------------ reasons
# Core reason -> oracle reason (benchmark/harness/crosscheck.REASON_CODES). Reviewed mapping, never improvised:
# an unmapped Core reason is a contract violation, not a silent pass-through.
REASON_MAP: dict[str, str] = {
    # Agent-proposed Core v0 codes (identity)
    "OUTSIDE_SESSION": "OUTSIDE_SESSION",
    "ZERO_VOLUME_BAR": "ZERO_VOLUME_BAR",
    "LIMIT_UP_LOCKED_BUY": "LIMIT_UP_LOCKED_BUY",
    "LIMIT_DOWN_LOCKED_SELL": "LIMIT_DOWN_LOCKED_SELL",
    "BAR_IN_EXCLUSION_WINDOW": "BAR_IN_EXCLUSION_WINDOW",
    "ORDER_EXPIRED": "ORDER_EXPIRED",
    "UNFILLED_AT_END_OF_DATA": "UNFILLED_AT_END_OF_DATA",
    # Core f4276cb (run_sma) codes
    "NO_LATER_ELIGIBLE_BAR": "UNFILLED_AT_END_OF_DATA",
}
# Core f4276cb emits EXECUTION_INELIGIBLE without saying why (side, session, volume, limit): it cannot be mapped
# onto one oracle code without the adapter guessing -> listed here so that it fails closed explicitly.
AMBIGUOUS_CORE_REASONS = {"EXECUTION_INELIGIBLE", "INSUFFICIENT_CASH"}


def map_reason(core_reason: str) -> str:
    if core_reason in REASON_MAP:
        return REASON_MAP[core_reason]
    if core_reason in AMBIGUOUS_CORE_REASONS:
        raise ContractViolation(f"core reason {core_reason!r} is not specific enough to map (contract gap G07)")
    raise ContractViolation(f"unmapped core rejection reason {core_reason!r}")


# ------------------------------------------------------------------------------------------ core identity
def _git_sha(path: Path) -> str | None:
    try:
        sha = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True,
                                      stderr=subprocess.DEVNULL).strip()
        dirty = subprocess.check_output(["git", "-C", str(path), "status", "--porcelain"], text=True,
                                        stderr=subprocess.DEVNULL).strip()
    except Exception:  # noqa: BLE001
        return None
    return None if dirty else sha


def core_identity() -> dict:
    """Commit of the INSTALLED Core: pip direct_url vcs commit, else a clean local git checkout; never the
    package version (``0.2.0`` / ``+source`` is not a commit). Missing -> ``git_sha`` None (fail-closed)."""
    try:
        dist = metadata.distribution(CORE_DIST)
    except metadata.PackageNotFoundError:
        return {"installed": False, "git_sha": None, "version": None, "source": None}
    sha, src = None, None
    raw = dist.read_text("direct_url.json")
    if raw:
        du = json.loads(raw)
        sha = (du.get("vcs_info") or {}).get("commit_id")
        src = du.get("url")
        if not sha and src and src.startswith("file://"):
            sha = _git_sha(Path(src[len("file://"):]))
    return {"installed": True, "git_sha": sha, "version": dist.version, "source": src}


def _core():
    import market_research_core  # noqa: PLC0415 - only import site of Core in the Agent
    return market_research_core


# ------------------------------------------------------------------------------------------ capabilities
@dataclass(frozen=True)
class Capability:
    cap_id: str
    contract: str            # architecture contract code(s)
    description: str
    required_by: tuple[str, ...]


CAPABILITIES = (
    Capability("K01", "C3", "generic execution entry point accepting externally computed targets/orders "
               "(not a fixed built-in strategy)", ("B01", "B03", "B04", "B05", "B06", "B07", "B08", "B09", "B10",
                                                    "B11", "B12")),
    Capability("K02", "C3", "short positions (target -1)", ("B06",)),
    Capability("K03", "C8", "tick-based slippage with an instrument tick size", tuple(f"B{i:02d}" for i in range(1, 13))),
    Capability("K04", "C8", "fixed fee per unit per side and per-side tax (futures)",
               ("B01", "B03", "B04", "B06", "B09", "B10", "B11", "B12")),
    Capability("K05", "C1", "side-specific execution eligibility (buy/sell)", ("B07",)),
    Capability("K06", "C1", "session awareness with OUTSIDE_SESSION rejection", ("B05",)),
    Capability("K07", "C1", "ZERO_VOLUME_BAR rejection", ("B04",)),
    Capability("K08", "C5", "exclusion windows: no signal/fill/valuation inside, invalidated round trips",
               ("B11",)),
    Capability("K09", "C6", "futures roll schedule from the spec, no implied roll", ("B09",)),
    Capability("K10", "C7", "open position reported at end of data (no implicit close)", ("B10",)),
    Capability("K11", "C4", "real engine git SHA in result provenance", tuple(f"B{i:02d}" for i in range(1, 13))),
    Capability("K12", "C3", "exact (Decimal) arithmetic or a documented rounding rule", tuple(f"B{i:02d}" for i in range(1, 13))),
    Capability("K13", "C1", "PIT membership flag gating entries", ("B08",)),
    Capability("K14", "C3", "order time-in-force / expiry (ORDER_EXPIRED)", ()),
    Capability("K15", "C3", "specific, documented rejection reason codes", ("B04", "B05", "B07")),
)


def _probe_run_sma(core) -> dict:
    """Execute the REAL Core probe once on a 3-bar frame ending long: observes provenance and end-of-data
    behaviour instead of assuming them."""
    import pandas as pd  # noqa: PLC0415
    ts = pd.date_range("2026-01-05 09:00", periods=6, freq="5min", tz="Asia/Taipei")
    close = [10.0, 10.0, 10.0, 11.0, 12.0, 13.0]
    frame = pd.DataFrame({"timestamp": ts, "open": close, "high": close, "low": close, "close": close,
                          "volume": [100] * 6, "available_at": ts + pd.Timedelta(minutes=5), "data_exists": True,
                          "research_eligible": True, "backtest_eligible": True, "execution_eligible": True})
    prov = core.models.UnifiedProvenance(market="TW", symbol="PROBE", snapshot_id="M1_CAPABILITY_PROBE",
                                         producer_commit_sha="none", data_verdict="SYNTHETIC",
                                         source_files=("synthetic",), permitted_usage_scope="BACKTEST")
    res = core.BacktestEngine().run_sma(core.UnifiedBars(frame, prov),
                                        core.BacktestAssumptions(fast_window=1, slow_window=2))
    return {"result_fields": [f.name for f in fields(res)], "provenance": res.provenance,
            "trades": len(res.trades), "last_position": float(res.equity_curve["position"].iloc[-1])}


def compatibility_matrix(core=None) -> list[dict]:
    """Measure every capability against the installed Core (introspection + one real probe run)."""
    ident = core_identity()
    rows = []
    try:
        core = core or _core()
    except ImportError as e:
        return [dict(asdict(c), status="MISSING", evidence=f"market_research_core not importable: {e}",
                     core_git_sha=None) for c in CAPABILITIES]
    eng = getattr(core, "BacktestEngine", None)
    methods = sorted(n for n, _ in inspect.getmembers(eng, inspect.isfunction) if not n.startswith("_")) if eng else []
    generic = [n for n in dir(core) if n.lower() in {"executionengine", "execute", "executionrequest"}]
    assumptions = {f.name for f in fields(core.BacktestAssumptions)} if hasattr(core, "BacktestAssumptions") else set()
    required_cols = set(getattr(getattr(core, "backtest", None), "REQUIRED", set()))
    probe: dict | Exception
    try:
        probe = _probe_run_sma(core)
    except Exception as e:  # noqa: BLE001
        probe = e
    src = inspect.getsource(core.backtest) if hasattr(core, "backtest") else ""

    def st(ok: bool, partial: bool = False) -> str:
        return "SUPPORTED" if ok else ("PARTIAL" if partial else "MISSING")

    ev: dict[str, tuple[str, str]] = {
        "K01": (st(bool(generic)), f"BacktestEngine public methods {methods}; generic entry points {generic or 'none'}"),
        "K02": (st(any("short" in a for a in assumptions)), "run_sma is long-only (desired = fast > slow -> 0/1)"),
        "K03": (st("slippage_ticks" in assumptions or "tick_size" in assumptions, "slippage_bps" in assumptions),
                f"assumptions {sorted(a for a in assumptions if 'slip' in a or 'tick' in a)}: bps only"),
        "K04": (st("fee_per_unit" in assumptions), f"fee model: {sorted(a for a in assumptions if 'fee' in a or 'tax' in a)}"),
        "K05": (st({"buy_execution_eligible", "sell_execution_eligible"} <= required_cols),
                f"required columns {sorted(required_cols)}"),
        "K06": (st("OUTSIDE_SESSION" in src), "no session handling in backtest.py"),
        "K07": (st("ZERO_VOLUME" in src), "zero volume not distinguished"),
        "K08": (st("exclusion" in src.lower() or "invalidated" in src.lower()), "no exclusion-window logic"),
        "K09": (st(bool(re.search(r"roll_schedule|contract_roll|\broll\b(?!ing)", src))),
                "no roll schedule (pandas .rolling() is not a contract roll)"),
        "K10": (st(not isinstance(probe, Exception) and "open_position" in probe["result_fields"],
                   not isinstance(probe, Exception) and probe["last_position"] != 0),
                ("probe ended long: " + (f"position {probe['last_position']}, BacktestResult fields "
                                         f"{probe['result_fields']}" if not isinstance(probe, Exception) else repr(probe)))),
        "K11": (st(not isinstance(probe, Exception) and "engine_git_sha" in probe["provenance"]),
                "probe provenance keys " + (str(sorted(probe["provenance"])) if not isinstance(probe, Exception) else "n/a")
                + f"; engine_version {probe['provenance'].get('engine_version') if not isinstance(probe, Exception) else 'n/a'}"),
        "K12": (st("Decimal" in src, "round(" in src), "float arithmetic, no rounding rule"),
        "K13": (st(bool(re.search(r"universe_member|\bpit_", src))), "no PIT flag"),
        "K14": (st("time_in_force" in src.lower() or "expire" in src.lower()), "no TIF / expiry"),
        "K15": (st(False, "EXECUTION_INELIGIBLE" in src), "reasons: EXECUTION_INELIGIBLE (side/cause unspecified), "
                "NO_LATER_ELIGIBLE_BAR, INSUFFICIENT_CASH"),
    }
    for c in CAPABILITIES:
        s, e = ev[c.cap_id]
        rows.append(dict(asdict(c), status=s, evidence=e, core_git_sha=ident["git_sha"]))
    return rows


def missing_for(case_or_caps: Sequence[str], matrix: Sequence[Mapping]) -> list[str]:
    need = set(case_or_caps)
    return [r["cap_id"] for r in matrix if r["status"] != "SUPPORTED" and r["cap_id"] in need]


def caps_required_by_case(case_id: str) -> list[str]:
    short = case_id[:3]
    return [c.cap_id for c in CAPABILITIES if short in c.required_by]


# ------------------------------------------------------------------------------------------ validation
def validate_dataset_refs(datasets: Sequence[DatasetRef]) -> None:
    if not datasets:
        raise ContractViolation("no dataset pinned")
    for d in datasets:
        for k in ("dataset_id", "snapshot_id", "producer_commit_sha"):
            v = getattr(d, k)
            if not v or str(v).upper() in {"UNKNOWN", "LATEST", "HEAD"}:
                raise ContractViolation(f"dataset {d.dataset_id}: {k} must be pinned, got {v!r}")
        if d.verdict not in ALLOWED_DATASET_VERDICTS:
            raise ContractViolation(f"dataset {d.dataset_id}: verdict {d.verdict} not executable")
        if d.usage_scope != "BACKTEST":
            raise ContractViolation(f"dataset {d.dataset_id}: usage_scope {d.usage_scope} is not BACKTEST")


def validate_cost_profile(spec: Mapping, profile: Mapping | None) -> None:
    ref = spec.get("costs", {}).get("profile_ref")
    if not ref:
        raise ContractViolation("spec has no CostProfile reference")
    if profile is None:
        raise ContractViolation(f"CostProfile {ref.get('profile_id')}@{ref.get('version')} not provided")
    if (profile.get("profile_id"), profile.get("version")) != (ref.get("profile_id"), ref.get("version")):
        raise ContractViolation("CostProfile id/version differs from spec reference")
    if profile.get("status") != "APPROVED":
        raise ContractViolation(f"CostProfile status {profile.get('status')} is not APPROVED")
    sha = profile_hash(dict(profile))
    if ref.get("sha256") != sha or profile.get("provenance", {}).get("profile_sha256") != sha:
        raise ContractViolation("CostProfile sha256 does not match the spec reference")
    diffs = cost_mismatches(spec["costs"], dict(profile))
    if diffs:
        raise ContractViolation("spec costs differ from CostProfile: " + "; ".join(diffs))


def validate_core_provenance(core_prov: Mapping, request: BacktestRequest, pinned_core_sha: str) -> None:
    sha = str(core_prov.get("engine_git_sha") or "")
    if not sha or sha.endswith("+source") or len(sha) < 7:
        raise ContractViolation("core result carries no real engine git SHA (gap G11)")
    if not (sha.startswith(pinned_core_sha) or pinned_core_sha.startswith(sha)):
        raise ContractViolation(f"core result from {sha}, pinned {pinned_core_sha}")
    want = {(d.dataset_id, d.snapshot_id, d.producer_commit_sha) for d in request.datasets}
    got = {(d.get("dataset_id"), d.get("snapshot_id"), d.get("producer_commit_sha"))
           for d in core_prov.get("datasets", [])}
    if want != got:
        raise ContractViolation(f"core ran on datasets {sorted(got)} but request pinned {sorted(want)}")
    if core_prov.get("execution_convention") != CONVENTION:
        raise ContractViolation(f"core convention {core_prov.get('execution_convention')} != {CONVENTION}")


# ------------------------------------------------------------------------------------------ translation
CORE_EXECUTION_REQUEST_V0 = "agent-proposed-core-execution-request-v0"


def build_core_request(request: BacktestRequest, targets: Sequence[Mapping], profile: Mapping | None) -> dict:
    """Spec + request + targets -> the Core execution request (Agent-side contract proposal v0).
    Validation is fail-closed; the adapter adds no execution semantics of its own."""
    spec = request.spec
    validate_dataset_refs(request.datasets)
    validate_cost_profile(spec, profile)
    exe = spec.get("execution", {})
    if exe.get("convention") != CONVENTION:
        raise ContractViolation(f"spec execution convention {exe.get('convention')} != {CONVENTION}")
    return {
        "schema": CORE_EXECUTION_REQUEST_V0,
        "execution_convention": CONVENTION,
        "spec_sha256": request.spec_sha256,
        "instrument": spec.get("instrument"),
        "datasets": [asdict(d) | {"mandatory_exclusions": list(d.mandatory_exclusions)} for d in request.datasets],
        "window": {"start": request.window_start, "end": request.window_end, "warmup_start": request.warmup_start},
        "execution": {k: exe.get(k) for k in ("fill_rule", "order_type", "time_in_force", "max_fill_delay_bars",
                                              "partial_fills", "same_bar_reentry")},
        "costs": {"profile_id": profile["profile_id"], "version": profile["version"],
                  "profile_sha256": profile["provenance"]["profile_sha256"], "spec_costs": spec["costs"],
                  "cost_multiplier": request.cost_multiplier},
        "targets": list(targets),
        "seed": request.seed,
        "parameters": dict(request.parameters),
    }


def decisions_to_targets(decisions: Sequence[Any]) -> list[dict]:
    """``signals.core.Decision`` list -> target orders. Decision time is passed through unchanged (it already is
    the bar's available_at); repeated targets are collapsed; nothing is shifted, filled or re-timed here."""
    out, last = [], None
    for d in sorted(decisions, key=lambda x: x.decision_ts):
        if d.target not in (-1, 0, 1):
            raise ContractViolation(f"target {d.target} outside -1/0/+1")
        if d.decision_ts.tzinfo is None:
            raise ContractViolation("decision_ts must be timezone-aware")
        key = (d.target, d.contract)
        if key == last:
            continue
        out.append({"signal_ts": d.decision_ts.isoformat(), "target": d.target, "contract": d.contract,
                    "reason": d.reason})
        last = key
    return out


def core_result_to_report(result: Mapping, request: BacktestRequest, pinned_core_sha: str) -> BacktestReport:
    """Core result (``CORE_EXECUTION_RESULT_V0`` shape) -> BacktestReport. Values are copied, never recomputed."""
    prov = result.get("provenance", {})
    validate_core_provenance(prov, request, pinned_core_sha)
    rej = [dict(r, reason=map_reason(str(r.get("reason")))) for r in result.get("rejections", [])]
    d0 = request.datasets[0]
    provenance = {
        "engine_name": prov.get("engine_name", CORE_DIST), "engine_git_sha": prov["engine_git_sha"],
        "engine_version": prov.get("engine_version"), "execution_convention": prov["execution_convention"],
        "dataset_snapshot_id": d0.snapshot_id, "dataset_producer_sha": d0.producer_commit_sha,
        "dataset_verdict": d0.verdict, "exclusions_applied": list(d0.mandatory_exclusions),
        "spec_sha256": request.spec_sha256, "parameters": dict(request.parameters),
        "cost_multiplier": request.cost_multiplier, "seed": request.seed,
        "window_start": request.window_start, "window_end": request.window_end,
        "adapter_version": ADAPTER_VERSION,
    }
    missing = [k for k in REQUIRED_PROVENANCE if provenance.get(k) in (None, "")]
    if missing:
        raise ContractViolation(f"core result lacks provenance {missing}")
    status = prov.get("performance_status", "RESEARCH_BACKTEST")
    return BacktestReport(fills=list(result.get("fills", [])), round_trips=list(result.get("round_trips", [])),
                          rejections=rej, equity_curve=list(result.get("equity_curve", [])),
                          open_position=result.get("open_position"), provenance=provenance, status=status,
                          invalidated_round_trips=list(result.get("invalidated_round_trips", [])))


# ------------------------------------------------------------------------------------------ the port
class MrcBacktestPort:
    """``ports.BacktestPort`` backed by the installed market-research-core.

    ``run`` hands the translated request to Core's generic execution entry point. Until Core publishes one
    (capability K01 and the rest of the required set), ``run`` raises ``CoreContractGap``: the runner records
    the experiment as FAILED with the gap list; no fill is ever produced here."""

    REQUIRED_FOR_FORMAL_RUN = ("K01", "K03", "K04", "K05", "K10", "K11", "K15")

    def __init__(self, cost_profiles: Mapping[tuple[str, str], Mapping], decisions_provider=None,
                 core=None, matrix: Sequence[Mapping] | None = None):
        self.cost_profiles = cost_profiles
        self.decisions_provider = decisions_provider
        self._core = core
        self.identity = core_identity()
        self.matrix = list(matrix) if matrix is not None else compatibility_matrix(core)

    def gaps(self) -> list[str]:
        return missing_for(self.REQUIRED_FOR_FORMAL_RUN, self.matrix)

    def run(self, request: BacktestRequest) -> BacktestReport:
        ref = request.spec.get("costs", {}).get("profile_ref", {})
        profile = self.cost_profiles.get((ref.get("profile_id"), ref.get("version")))
        if self.identity.get("git_sha") is None:
            raise CoreContractGap(["K11"], "installed Core has no resolvable commit")
        if self.decisions_provider is None:
            raise ContractViolation("no signal decisions provider configured")
        targets = decisions_to_targets(self.decisions_provider(request))
        core_request = build_core_request(request, targets, profile)      # validation happens even when pending
        gaps = self.gaps()
        if gaps:
            raise CoreContractGap(gaps, f"core {self.identity['git_sha'][:7]}")
        core = self._core or _core()
        result = core.execute(core_request)          # pragma: no cover - unreachable until Core ships K01
        return core_result_to_report(result, request, self.identity["git_sha"])


# ------------------------------------------------------------------------------------------ engine status
BENCHMARK_RECORD_DIR = Path(__file__).resolve().parents[3] / "benchmark" / "results"


def engine_benchmark_status(core_sha: str | None, record_dir: Path = BENCHMARK_RECORD_DIR) -> dict:
    """BENCHMARK_PASS only when a cross-check record produced by the REAL Core at exactly this commit shows every
    case B01-B12 PASS. Anything else (missing, other SHA, fake engine, any non-PASS) is NOT_VERIFIED."""
    if not core_sha:
        return {"status": "NOT_VERIFIED", "why": "core commit unknown"}
    p = Path(record_dir) / f"mrc_{core_sha[:12]}" / "crosscheck.json"
    if not p.exists():
        return {"status": "NOT_VERIFIED", "why": f"no cross-check record {p.name} for {core_sha[:12]}"}
    rec = json.loads(p.read_text(encoding="utf-8"))
    cases = {r["case_id"][:3]: r["status"] for r in rec.get("cases", [])}
    ok = (rec.get("engine", {}).get("git_sha") == core_sha and rec.get("engine", {}).get("kind") == "REAL_CORE"
          and sorted(cases) == [f"B{i:02d}" for i in range(1, 13)] and all(s == "PASS" for s in cases.values()))
    return {"status": "BENCHMARK_PASS" if ok else "NOT_VERIFIED",
            "why": "all 12 cases PASS on the real Core" if ok else f"record verdict {rec.get('verdict')}: "
            + ", ".join(f"{k}={v}" for k, v in sorted(cases.items()) if v != "PASS")}


def engine_info():
    """EngineInfo for the runner, derived from the installed Core and its cross-check record (never hand-set)."""
    from strategy_agent.runner import EngineInfo  # noqa: PLC0415
    ident = core_identity()
    st = engine_benchmark_status(ident.get("git_sha"))
    return EngineInfo(name=CORE_DIST, git_sha=ident.get("git_sha") or "", version=ident.get("version") or "",
                      benchmark_status=st["status"])


def ts_label(ts: Any) -> str:
    """Core timestamp -> benchmark label 'YYYY-MM-DD HH:MM' in the case timezone (formatting only)."""
    if isinstance(ts, str):
        return ts[:16].replace("T", " ")
    if isinstance(ts, datetime):
        return ts.strftime(TS_FMT)
    return ts.strftime(TS_FMT)
