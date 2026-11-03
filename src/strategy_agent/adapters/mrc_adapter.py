"""M1 integration adapter: the ONLY module of the Strategy Agent that knows ``market_research_core``.

Target Core API (Phase 2.1, verified on ``a50f3902``)::

    ExecutionEngine().run(bars, orders, *, cost_profile=CostProfile, config=ExecutionConfig) -> ExecutionResult

Responsibilities (translation and pass-through only):
  * signal decisions        -> Core ``ExecutionOrder`` target-position orders   (``decisions_to_targets``,
                                ``expand_roll_schedule``, ``targets_to_orders``)
  * request + pins          -> ``ExecutionConfig``                              (``core_config``)
  * Agent CostProfile       -> Core ``CostProfile``                             (``core_cost_profile``)
  * Core-shaped bar frame   -> column check only                                (``check_core_bars``)
  * ``ExecutionResult``     -> ``ports.BacktestReport``                         (``core_result_to_report``)
  * provenance transfer, reviewed rejection-reason mapping, dataset / CostProfile / engine-SHA validation
  * a capability matrix measured on the REAL installed Core                     (``compatibility_matrix``)

It never simulates fills, computes PnL or fees, edits buy/sell eligibility, moves signal times, fills bars,
stitches futures series or calls the benchmark oracle. When Core cannot express a request the adapter raises
``CoreContractGap`` (fail-closed); it does not substitute behaviour of its own.
"""
from __future__ import annotations

import inspect
import json
import re
import subprocess
from decimal import Decimal
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from importlib import metadata
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from strategy_agent.costs.profile import cost_mismatches, profile_hash
from strategy_agent.ports import (CONVENTION, REQUIRED_PROVENANCE, BacktestReport, BacktestRequest,
                                  ContractViolation, DatasetRef)

ADAPTER_VERSION = "m1.3-0.4.0"
CORE_DIST = "market-research-core"
ALLOWED_DATASET_VERDICTS = {"GO", "SCOPED_GO"}
TS_FMT = "%Y-%m-%d %H:%M"
SHA40 = re.compile(r"[0-9a-fA-F]{40}")

# Core's execution bar contract (execution._REQUIRED_COLUMNS @ a50f390). The adapter checks presence only.
CORE_BAR_COLUMNS = (
    "bar_start", "bar_end", "available_at", "timezone", "session", "symbol", "market", "contract",
    "open", "high", "low", "close", "volume", "data_exists", "backtest_eligible",
    "buy_execution_eligible", "sell_execution_eligible", "execution_rejection_reason",
    "session_open", "session_close",
)


class CoreContractGap(RuntimeError):
    """The installed Core cannot execute this request without the adapter inventing behaviour."""

    def __init__(self, gaps: Sequence[str], context: str = ""):
        self.gaps = list(gaps)
        super().__init__(f"INTEGRATION_PENDING: core lacks {', '.join(self.gaps)}" + (f" ({context})" if context else ""))


# ------------------------------------------------------------------------------------------ reasons
# Core reason -> report reason. Oracle codes map 1:1; Core-only codes are reviewed and passed through under
# their own name (they are not oracle codes and are never renamed into one). Unknown codes fail closed.
ORACLE_REASONS = {"OUTSIDE_SESSION", "ZERO_VOLUME_BAR", "LIMIT_UP_LOCKED_BUY", "LIMIT_DOWN_LOCKED_SELL",
                  "BAR_IN_EXCLUSION_WINDOW", "ORDER_EXPIRED", "UNFILLED_AT_END_OF_DATA"}
CORE_ONLY_REASONS = {
    "PIT_VIOLATION", "TARGET_SIDE_MISMATCH", "BAR_NOT_ELIGIBLE", "BUY_NOT_ELIGIBLE", "SELL_NOT_ELIGIBLE",
    "LIMIT_NOT_TOUCHED", "INSUFFICIENT_MARGIN", "RISK_LIMIT",
    # rejection reasons carried on bars by Core's data adapters (taiwan.py / client.py @ a50f390)
    "BAR_NOT_BACKTEST_OR_LIMIT_ELIGIBLE", "BUY_RULE_INELIGIBLE", "SELL_RULE_INELIGIBLE",
    "CLOSING_AUCTION_NOT_EXECUTABLE", "TIMESTAMP_LABEL_OR_SESSION_UNKNOWN",
}
REASON_MAP: dict[str, str] = {r: r for r in ORACLE_REASONS | CORE_ONLY_REASONS}
# reasons whose cause is not stated (Phase-2 run_sma era); they cannot be mapped without guessing
AMBIGUOUS_CORE_REASONS = {"EXECUTION_INELIGIBLE", "INSUFFICIENT_CASH", "NO_LATER_ELIGIBLE_BAR"}


def map_reason(core_reason: str) -> str:
    if core_reason in REASON_MAP:
        return REASON_MAP[core_reason]
    if core_reason in AMBIGUOUS_CORE_REASONS:
        raise ContractViolation(f"core reason {core_reason!r} is not specific enough to map (contract gap G15)")
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
    package version. Missing -> ``git_sha`` None (fail-closed)."""
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
    import market_research_core  # noqa: PLC0415 - the only import site of Core in the Agent
    return market_research_core


# ------------------------------------------------------------------------------------------ capabilities
@dataclass(frozen=True)
class Capability:
    cap_id: str
    contract: str
    description: str
    required_by: tuple[str, ...]


ALL = tuple(f"B{i:02d}" for i in range(1, 13))
CAPABILITIES = (
    Capability("K01", "C3", "generic execution entry point accepting externally computed orders/targets",
               ALL),
    Capability("K02", "C3", "short positions", ("B06",)),
    Capability("K03", "C8", "tick-based slippage with an instrument tick size", ALL),
    Capability("K04", "C8", "fixed fee per unit per side and per-side tax (futures)",
               ("B01", "B03", "B04", "B06", "B09", "B10", "B11", "B12")),
    Capability("K05", "C1", "side-specific execution eligibility (buy/sell)", ("B07",)),
    Capability("K06", "C1", "session awareness with OUTSIDE_SESSION rejection", ("B05",)),
    Capability("K07", "C1", "ZERO_VOLUME_BAR rejection", ("B04",)),
    Capability("K08", "C5", "exclusion windows and invalidated round trips", ("B11",)),
    Capability("K09", "C6", "futures roll from the spec schedule, contract-keyed FIFO, no implied roll", ("B09",)),
    Capability("K10", "C7", "open position reported at end of data (no implicit close)", ("B10",)),
    Capability("K11", "C4", "real engine git SHA in result provenance", ALL),
    Capability("K12", "C3", "exact arithmetic or a rounding rule able to reproduce convention v1 (no rounding)",
               ALL),
    Capability("K13", "C1", "PIT: execution never before information availability", ("B08",)),
    Capability("K14", "C3", "order time-in-force / expiry matching convention v1 §1.5", ("B05", "B07")),
    Capability("K15", "C3", "rejection log per skipped bar with specific codes (convention v1 §1.4)",
               ("B04", "B05", "B07")),
    Capability("K16", "C3", "fills applied in fill-time order (no exit before entry)", ()),
    Capability("K17", "C3", "target orders sized from filled position + live pending intent (convention v1)", ()),
    Capability("K14b", "C3", "DAY expiry half-open: bar_start == expiry is not a candidate", ()),
)


def _probe_run(core, two_sessions: bool, locked: tuple[int, ...], orders: list[tuple[int, str, float, str]]):
    """Run the real engine on a tiny synthetic tape (8 x 5m bars from 09:00, zero costs). Capability probe only:
    the expected behaviour is convention v1 (the full Oracle comparison is in benchmark/harness/mrc_crosscheck.py)."""
    import pandas as pd  # noqa: PLC0415
    t0 = pd.Timestamp("2026-01-05 09:00", tz="Asia/Taipei")
    mid = t0 + pd.Timedelta(minutes=20)

    def sess(i):
        if not two_sessions:
            return t0, t0 + pd.Timedelta(minutes=40)
        return (t0, mid) if i < 4 else (mid, t0 + pd.Timedelta(minutes=40))
    rows = [{"bar_start": t0 + pd.Timedelta(minutes=5 * i), "bar_end": t0 + pd.Timedelta(minutes=5 * i + 5),
             "available_at": t0 + pd.Timedelta(minutes=5 * i + 5), "timezone": "Asia/Taipei", "session": "s",
             "symbol": "P", "market": "TW_EQUITY", "contract": "P", "open": 60.0, "high": 60.5, "low": 59.5,
             "close": 60.0, "volume": 1000.0, "data_exists": True, "backtest_eligible": True,
             "buy_execution_eligible": i not in locked, "sell_execution_eligible": True,
             "execution_rejection_reason": "" if i not in locked else "LIMIT_UP_LOCKED_BUY",
             "session_open": sess(i)[0], "session_close": sess(i)[1]} for i in range(8)]
    mk = lambda m, side, tgt, oid: core.ExecutionOrder(  # noqa: E731
        symbol="P", market="TW_EQUITY", contract="P", signal_time=t0 + pd.Timedelta(minutes=m),
        signal_available_at=t0 + pd.Timedelta(minutes=m), order_time=t0 + pd.Timedelta(minutes=m),
        side=side, strategy_id="probe", experiment_id="probe", target_position=tgt, time_in_force="DAY",
        order_id=oid)
    cp = core.CostProfile(profile_id="p", version="1", instrument_type="EQUITY", approval_status="DRAFT",
                          source_evidence="probe", basis="ASSUMED", equity_commission_rate=0.0,
                          minimum_commission=0.0, sell_tax_rate=0.0, futures_fee_per_contract_per_side=0.0,
                          futures_transaction_tax_rate=0.0, contract_multiplier=1.0, tick_size=0.1,
                          slippage_ticks=0.0, fee_rounding="HALF_UP", tax_rounding="HALF_UP",
                          component_basis={k: "ASSUMED" for k in (
                              "equity_commission_rate", "minimum_commission", "sell_tax_rate",
                              "futures_fee_per_contract_per_side", "futures_transaction_tax_rate",
                              "contract_multiplier", "tick_size", "slippage_ticks", "fee_rounding", "tax_rounding")})
    cfg = core.ExecutionConfig(initial_cash=1e7, bar_label="START", session_semantics="CONFIRMED",
                               timestamp_timezone="Asia/Taipei", same_timestamp_open_assumption=True)
    return core.ExecutionEngine().run(pd.DataFrame(rows), [mk(*o) for o in orders], cost_profile=cp, config=cfg)


def _fill_list(res):
    return [(f["side"], str(f["exec_ts"])[11:16]) for f in res.fills.to_dict("records")] if len(res.fills) else []


def _probe_chronology(core) -> tuple[str, str]:
    """K16: BUY delayed by buy-locked bars, later target 0 -> fills applied in fill-time order."""
    try:
        res = _probe_run(core, False, (2, 3, 4), [(10, "BUY", 1000.0, "a"), (20, "SELL", 0.0, "b")])
        times = list(res.fills["exec_ts"]) if len(res.fills) else []
        back = res.round_trips.to_dict("records") if len(res.round_trips) else []
        bad = any(r["exit_ts"] < r["entry_ts"] for r in back) or times != sorted(times)
        if not times:
            return "MISSING", "probe produced no fills; chronology not demonstrated"
        return ("MISSING" if bad else "SUPPORTED",
                f"probe fills {_fill_list(res)}; "
                f"round trips {[(r['side'], str(r['entry_ts'])[11:16], str(r['exit_ts'])[11:16]) for r in back]}")
    except Exception as e:  # noqa: BLE001
        return ("MISSING", f"probe failed: {type(e).__name__}: {e}")


def _probe_target_supersession(core) -> tuple[str, str]:
    """K17: same tape; convention v1 sizes target 0 against filled(0) + pending(+1000) -> SELL 09:20, BUY 09:25,
    flat at the end."""
    try:
        res = _probe_run(core, False, (2, 3, 4), [(10, "BUY", 1000.0, "a"), (20, "SELL", 0.0, "b")])
        ok = _fill_list(res) == [("SELL", "09:20"), ("BUY", "09:25")] and len(res.open_positions) == 0
        return ("SUPPORTED" if ok else "MISSING",
                f"probe fills {_fill_list(res)}; open positions {len(res.open_positions)}")
    except Exception as e:  # noqa: BLE001
        return ("MISSING", f"probe failed: {type(e).__name__}: {e}")


def _probe_day_expiry_boundary(core) -> tuple[str, str]:
    """K14b: sessions [09:00,09:20) and [09:20,09:40); BUY at 09:10, locked 09:10-09:15 -> expires at 09:20 and must
    not fill on the 09:20 bar."""
    try:
        res = _probe_run(core, True, (2, 3), [(10, "BUY", 1000.0, "a")])
        reasons = list(res.rejections["reason"]) if len(res.rejections) else []
        ok = not _fill_list(res) and "ORDER_EXPIRED" in reasons
        return ("SUPPORTED" if ok else "MISSING", f"probe fills {_fill_list(res)}; rejections {reasons}")
    except Exception as e:  # noqa: BLE001
        return ("MISSING", f"probe failed: {type(e).__name__}: {e}")


def compatibility_matrix(core=None) -> list[dict]:
    """Measure every capability against the installed Core by introspecting its public API and sources."""
    ident = core_identity()
    try:
        core = core or _core()
    except ImportError as e:
        return [dict(asdict(c), status="MISSING", evidence=f"market_research_core not importable: {e}",
                     core_git_sha=None) for c in CAPABILITIES]
    eng = getattr(core, "ExecutionEngine", None)
    if eng is None:
        return [dict(asdict(c), status="MISSING", evidence="no ExecutionEngine in this Core",
                     core_git_sha=ident["git_sha"]) for c in CAPABILITIES]
    sig = list(inspect.signature(eng.run).parameters)
    ex_src = inspect.getsource(core.execution)
    co_src = inspect.getsource(core.costs)
    cp = {f.name: f for f in fields(core.CostProfile)}
    res = {f.name for f in fields(core.ExecutionResult)}
    order = {f.name for f in fields(core.ExecutionOrder)}
    cfg = {f.name for f in fields(core.ExecutionConfig)}
    required_cols = set(getattr(core.execution, "_REQUIRED_COLUMNS", set()))
    tif = str(next((f.type for f in fields(core.ExecutionOrder) if f.name == "time_in_force"), ""))
    rounding = str(cp["fee_rounding"].type) if "fee_rounding" in cp else ""

    def st(ok, partial=False):
        return "SUPPORTED" if ok else ("PARTIAL" if partial else "MISSING")

    ev = {
        "K01": (st(sig[:3] == ["self", "bars", "orders"] and {"cost_profile", "config"} <= set(sig)),
                f"ExecutionEngine.run{tuple(sig[1:])}"),
        "K02": (st("SHORT" in ex_src and "target_position" in order), "FIFO long/short lots; target_position signed"),
        "K03": (st({"tick_size", "slippage_ticks"} <= set(cp)), "CostProfile.tick_size + slippage_ticks; "
                "adverse_fill_price rounds against the trader to a valid tick"),
        "K04": (st({"futures_fee_per_contract_per_side", "futures_transaction_tax_rate"} <= set(cp)),
                "futures fee per contract per side, transaction tax rate, multiplier"),
        "K05": (st({"buy_execution_eligible", "sell_execution_eligible"} <= required_cols),
                "bars require buy_/sell_execution_eligible (+ per-side rejection reason columns)"),
        "K06": (st("OUTSIDE_SESSION" in ex_src and {"session_open", "session_close"} <= required_cols),
                "session_open/session_close per bar; OUTSIDE_SESSION when bar_start outside [open, close)"),
        "K07": (st("ZERO_VOLUME_BAR" in ex_src), "volume <= 0 -> ZERO_VOLUME_BAR"),
        "K08": (st("exclusion_windows" in cfg and "invalidated_round_trips" in res),
                "ExecutionConfig.exclusion_windows; invalidated_round_trips ledger"),
        "K09": (st(False, "contract" in order and "FIFO" in ex_src),
                "FIFO keyed by (symbol, contract); no roll schedule input: roll orders must be supplied by the caller"),
        "K10": (st("open_positions" in res and "No forced close" in (core.execution.__doc__ or "") + ex_src
                   or "No forced liquidation" in ex_src), "ExecutionResult.open_positions with mark_price, unrealized_pnl"),
        "K11": (st(False, "core_commit_sha" in cfg),
                "provenance.engine_git_sha = ExecutionConfig.core_commit_sha (caller-supplied; engine does not "
                "self-identify; engine_version hard-coded '2.1.0')"),
        "K12": (st("NONE" in rounding and "FLOOR_INTEGER" in rounding and "Decimal" in co_src, "HALF_UP" in rounding),
                f"rounding modes {rounding}; calculate_costs returns Decimal"),
        "K13": (st("PIT_VIOLATION" in ex_src and "signal_available_at" in order),
                "order_time < signal_available_at -> PIT_VIOLATION; bar_start >= available_at"),
        "K14": (st("DAY" in tif and "expiry" in ex_src and "session_close" in ex_src, "DAY" in tif),
                f"time_in_force {tif}; DAY expiry at the first session_close > order_time"
                if "expiry" in ex_src else f"time_in_force {tif}: DAY = session of the first candidate bar"),
        "K15": (st('"bar_ts": bar["bar_start"]' in ex_src, "rejects.append" in ex_src),
                "rejection per skipped bar with order_id, bar_ts, reason" if '"bar_ts": bar["bar_start"]' in ex_src
                else "one rejection per unfilled ORDER (final reason); skipped bars of a later-filled order not logged"),
        "K16": _probe_chronology(core),
        "K17": _probe_target_supersession(core),
        "K14b": _probe_day_expiry_boundary(core),
    }
    return [dict(asdict(c), status=ev[c.cap_id][0], evidence=ev[c.cap_id][1], core_git_sha=ident["git_sha"])
            for c in CAPABILITIES]


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


def check_core_bars(frame) -> None:
    """Presence check only. Eligibility, sessions and availability come from Core's data adapters."""
    missing = [c for c in CORE_BAR_COLUMNS if c not in set(frame.columns)]
    if missing:
        raise ContractViolation(f"bar frame lacks Core execution columns {missing}")


# ------------------------------------------------------------------------------------------ signals -> orders
def decisions_to_targets(decisions: Sequence[Any]) -> list[dict]:
    """``signals.core.Decision`` list -> intended targets. Decision time (= bar available_at) passes through
    unchanged; repeats are collapsed; nothing is shifted, filled or re-timed."""
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


def expand_roll_schedule(targets: Sequence[Mapping], roll_schedule: Sequence[Mapping]) -> list[dict]:
    """Spec roll schedule -> explicit target changes (close the old contract, open the new one at the roll time).
    Uses only the INTENDED target before the roll (never fills); it does not stitch prices or infer rolls."""
    out = [dict(t) for t in targets]
    for r in sorted(roll_schedule, key=lambda x: x["at"]):
        at = str(r["at"])
        prior = [t for t in out if t["signal_ts"] <= at and t["contract"] == r["from_contract"]]
        held = prior[-1]["target"] if prior else 0
        if held:
            out.append({"signal_ts": at, "target": 0, "contract": r["from_contract"], "reason": "ROLL_CLOSE"})
            out.append({"signal_ts": at, "target": held, "contract": r["to_contract"], "reason": "ROLL_OPEN"})
    return sorted(out, key=lambda t: (t["signal_ts"], 0 if t["reason"] == "ROLL_CLOSE" else 1))


def targets_to_orders(targets: Sequence[Mapping], *, symbol: str, market: str, quantity: float, strategy_id: str,
                      experiment_id: str, time_in_force: str = "DAY", core=None) -> list:
    """Intended targets -> Core target-position MARKET orders. signal_time = signal_available_at = order_time =
    the decision time (convention v1 §1.2). Side follows the intended target path per contract; Core decides
    fills, rejections and the effective target state (C3: an unfilled order does not move Core's target)."""
    core = core or _core()
    prev: dict[str, int] = {}
    orders = []
    for i, t in enumerate(targets):
        c, tgt = t["contract"], int(t["target"])
        if tgt == prev.get(c, 0):
            continue
        side = "BUY" if tgt > prev.get(c, 0) else "SELL"
        orders.append(core.ExecutionOrder(
            symbol=symbol, market=market, contract=c, signal_time=t["signal_ts"],
            signal_available_at=t["signal_ts"], order_time=t["signal_ts"], side=side, strategy_id=strategy_id,
            experiment_id=experiment_id, target_position=float(tgt * quantity), order_type="MARKET",
            time_in_force=time_in_force, order_id=f"{i:04d}-{t.get('reason', 'SIGNAL')}"))
        prev[c] = tgt
    return orders


# ------------------------------------------------------------------------------------------ costs / config
_BASIS = {"OFFICIAL_RULE": "OFFICIAL", "BROKER_ACTUAL": "BROKER_ACTUAL", "RESEARCH_ASSUMPTION": "ASSUMED"}


def unmappable_cost_components(profile: Mapping, instrument_type: str) -> list[str]:
    """Components the field mapping below would otherwise DROP silently (gap G18). Core equities take only a
    notional rate + minimum and a SELL tax; futures only a per-contract fee + BOTH-side tax; slippage only in ticks.
    Anything else must fail closed rather than vanish from the cost model."""
    fut = instrument_type == "FUTURES"
    out = []
    for c in profile["components"]:
        k, side = c["kind"], c.get("side", "BOTH")
        if k == "COMMISSION":
            if side != "BOTH":
                out.append(f"{c['id']}: one-sided commission")
            if not fut and c.get("per_unit"):
                out.append(f"{c['id']}: per-unit equity commission {c['per_unit']} (Core: notional rate only)")
            if fut and c.get("rate"):
                out.append(f"{c['id']}: notional-rate futures commission {c['rate']} (Core: per contract only)")
        elif k in {"EXCHANGE_FEE", "REGULATORY_FEE"}:
            out.append(f"{c['id']}: {k} has no Core field")
        elif k == "TAX":
            if not fut and side != "SELL":
                out.append(f"{c['id']}: equity TAX side {side} (Core: SELL only)")
            if fut and side != "BOTH":
                out.append(f"{c['id']}: futures TAX side {side} (Core: BOTH only)")
            if c.get("per_unit"):
                out.append(f"{c['id']}: per-unit tax")
        elif k == "SLIPPAGE" and "ticks" not in c:
            out.append(f"{c['id']}: slippage {c.get('bps')} bps (Core: ticks only)")
        elif k == "MIN_COMMISSION" and fut and c.get("amount"):
            out.append(f"{c['id']}: futures minimum commission")
    return out


def core_cost_profile(profile: Mapping, *, instrument_type: str, tick_size: float, contract_multiplier: float = 1.0,
                      core=None):
    """Agent CostProfile (governance JSON) -> Core CostProfile. Field mapping only; rules Core cannot express
    (e.g. FLOOR_INTEGER rounding) raise CoreContractGap instead of being approximated."""
    unmappable = unmappable_cost_components(profile, instrument_type)
    if unmappable:
        raise CoreContractGap(["K18"], "cost components not expressible in Core CostProfile: " + "; ".join(unmappable))
    core = core or _core()
    comp = {c["kind"]: c for c in profile["components"]}
    rnd = comp.get("ROUNDING", {}).get("rule", "NONE")
    allowed = str(next(f.type for f in fields(core.CostProfile) if f.name == "fee_rounding"))
    if rnd not in allowed:
        raise CoreContractGap(["K12"], f"rounding rule {rnd} not expressible (Core modes {allowed})")
    basis = lambda kind: _BASIS.get(comp.get(kind, {}).get("basis", "RESEARCH_ASSUMPTION"), "ASSUMED")  # noqa: E731
    is_fut = instrument_type == "FUTURES"
    vals = dict(
        equity_commission_rate=0.0 if is_fut else comp.get("COMMISSION", {}).get("rate", 0.0),
        minimum_commission=comp.get("MIN_COMMISSION", {}).get("amount", 0.0),
        sell_tax_rate=0.0 if is_fut else sum(c.get("rate", 0.0) for c in profile["components"]
                                             if c["kind"] == "TAX" and c["side"] == "SELL"),
        futures_fee_per_contract_per_side=comp.get("COMMISSION", {}).get("per_unit", 0.0) if is_fut else 0.0,
        futures_transaction_tax_rate=sum(c.get("rate", 0.0) for c in profile["components"]
                                         if c["kind"] == "TAX" and c["side"] == "BOTH") if is_fut else 0.0,
        contract_multiplier=contract_multiplier, tick_size=tick_size,
        slippage_ticks=comp.get("SLIPPAGE", {}).get("ticks", 0.0))
    cb = {k: "ASSUMED" for k in vals} | {"fee_rounding": basis("ROUNDING"), "tax_rounding": basis("ROUNDING")}
    cb.update(equity_commission_rate=basis("COMMISSION"), futures_fee_per_contract_per_side=basis("COMMISSION"),
              minimum_commission=basis("MIN_COMMISSION"), sell_tax_rate=basis("TAX"),
              futures_transaction_tax_rate=basis("TAX"), slippage_ticks=basis("SLIPPAGE"))
    return core.CostProfile(
        profile_id=profile["profile_id"], version=profile["version"], instrument_type=instrument_type,
        approval_status="APPROVED" if profile.get("status") == "APPROVED" else "DRAFT",
        source_evidence=profile.get("provenance", {}).get("approval_ref") or "agent CostProfile " + profile["profile_id"],
        basis="ASSUMED" if "ASSUMED" in cb.values() else "OFFICIAL", fee_rounding=rnd, tax_rounding=rnd,
        component_basis=cb, **vals)


@dataclass(frozen=True)
class MarketSemantics:
    """Evidence-backed market timing for one dataset (from Core's data contract / producer evidence)."""
    market: str                       # TW_EQUITY | IBKR_EQUITY | IBKR_FUTURES | TXF
    bar_label: str                    # START | END | UNKNOWN
    session_semantics: str            # CONFIRMED | UNKNOWN
    timezone: str
    initial_cash: float
    initial_margin_per_contract: float | None = None
    # constant instrument tick (e.g. TXF = 1 point). None = price-banded ladder (TWSE/TPEx equities), which a
    # single Core CostProfile.tick_size cannot express -> CoreContractGap K03 (gap G03b), never approximated
    constant_tick_size: float | None = None


def core_config(request: BacktestRequest, sem: MarketSemantics, core_sha: str, exclusions=(), core=None):
    """Request + pins -> ExecutionConfig. ``same_timestamp_open_assumption`` is set ONLY for START-labelled bars:
    then available_at == bar_end == next bar_start, and convention v1 §1.4 fills at the first bar with
    bar_start >= order_ts (= available_at). UNKNOWN label/session fails closed in Core itself."""
    core = core or _core()
    if sem.bar_label != "START" or sem.session_semantics != "CONFIRMED":
        raise ContractViolation(f"{sem.market}: bar label {sem.bar_label} / session {sem.session_semantics} "
                                "not confirmed - formal execution blocked")
    d0 = request.datasets[0]
    return core.ExecutionConfig(
        initial_cash=sem.initial_cash, bar_label=sem.bar_label, session_semantics=sem.session_semantics,
        timestamp_timezone=sem.timezone, same_timestamp_open_assumption=True,
        initial_margin_per_contract=sem.initial_margin_per_contract, exclusion_windows=tuple(exclusions),
        core_commit_sha=core_sha, dataset_snapshot_id=d0.snapshot_id, dataset_producer_sha=d0.producer_commit_sha,
        dataset_verdict=d0.verdict, permitted_usage_scope=d0.usage_scope, spec_sha256=request.spec_sha256,
        parameters=dict(request.parameters), cost_multiplier=request.cost_multiplier, seed=request.seed,
        window_start=request.window_start, window_end=request.window_end)


# ------------------------------------------------------------------------------------------ result -> report
def _records(df) -> list[dict]:
    if df is None or len(df) == 0:
        return []
    rows = df if isinstance(df, list) else df.to_dict("records")
    out = []
    for r in rows:
        # Decimal costs/PnL stay exact as strings; timestamps as ISO-8601
        out.append({k: (str(v) if isinstance(v, Decimal) else v.isoformat() if hasattr(v, "isoformat") else v)
                    for k, v in r.items()})
    return out


def validate_core_provenance(prov: Mapping, request: BacktestRequest, pinned_core_sha: str) -> None:
    sha = str(prov.get("engine_git_sha") or "")
    if not SHA40.fullmatch(sha):
        raise ContractViolation(f"core result engine_git_sha {sha!r} is not a full commit (G11)")
    if sha != pinned_core_sha:
        raise ContractViolation(f"core result from {sha}, pinned {pinned_core_sha}")
    if prov.get("execution_convention") != CONVENTION:
        raise ContractViolation(f"core convention {prov.get('execution_convention')} != {CONVENTION}")
    if prov.get("spec_sha256") != request.spec_sha256:
        raise ContractViolation("core result is for a different spec")
    d0 = request.datasets[0]
    if (prov.get("dataset_snapshot_id"), prov.get("dataset_producer_sha")) != (d0.snapshot_id, d0.producer_commit_sha):
        raise ContractViolation(f"core ran on {prov.get('dataset_snapshot_id')}@{prov.get('dataset_producer_sha')}, "
                                f"request pinned {d0.snapshot_id}@{d0.producer_commit_sha}")


def core_result_to_report(result, request: BacktestRequest, pinned_core_sha: str, cost_profile=None,
                          agent_profile: Mapping | None = None) -> BacktestReport:
    """``ExecutionResult`` -> BacktestReport. Values are copied from Core, never recomputed.
    When the CostProfile objects are given, the CostProfile chain is verified: Core's provenance hash must equal the
    hash of the Core CostProfile the adapter passed, and that profile must have been mapped from the Agent profile
    whose sha256 the spec pins."""
    prov = dict(result.provenance)
    validate_core_provenance(prov, request, pinned_core_sha)
    agent_sha = None
    if cost_profile is not None and prov.get("cost_profile_sha256") != cost_profile.sha256:
        raise ContractViolation(f"core cost_profile_sha256 {prov.get('cost_profile_sha256')} != submitted profile "
                                f"{cost_profile.sha256}")
    if agent_profile is not None:
        agent_sha = agent_profile.get("provenance", {}).get("profile_sha256")
        pinned = request.spec.get("costs", {}).get("profile_ref", {}).get("sha256")
        if not agent_sha or agent_sha != pinned:
            raise ContractViolation(f"agent CostProfile sha {agent_sha} != spec pin {pinned}")
    rej = [dict(r, reason=map_reason(str(r.get("reason")))) for r in _records(result.rejections)]
    opens = _records(result.open_positions)
    provenance = {k: prov.get(k) for k in REQUIRED_PROVENANCE}
    provenance.update(engine_name=prov.get("engine_name"), adapter_version=ADAPTER_VERSION,
                      core_status=result.status, cost_profile_id=prov.get("cost_profile_id"),
                      cost_profile_sha256=prov.get("cost_profile_sha256"), agent_cost_profile_sha256=agent_sha,
                      core_assumptions=prov.get("assumptions"))
    missing = [k for k in REQUIRED_PROVENANCE if provenance.get(k) in (None, "")]
    if missing:
        raise ContractViolation(f"core result lacks provenance {missing}")
    return BacktestReport(
        fills=_records(result.fills), round_trips=_records(result.round_trips), rejections=rej,
        equity_curve=_records(result.equity_curve), open_position={"positions": opens} if opens else None,
        provenance=provenance,
        status="RESEARCH_BACKTEST" if result.status == "FORMAL_ELIGIBLE" else "RESEARCH_INTEGRATION_DEMO",
        invalidated_round_trips=_records(result.invalidated_round_trips))


# ------------------------------------------------------------------------------------------ the port
class MrcBacktestPort:
    """``ports.BacktestPort`` executed by market-research-core ``ExecutionEngine.run``.

    The runner reaches ``run`` only for an engine SHA with a BENCHMARK_PASS record; this class additionally
    refuses to run while a required capability is missing on the installed Core."""

    REQUIRED_FOR_FORMAL_RUN = ("K01", "K02", "K03", "K04", "K05", "K06", "K07", "K08", "K10", "K13")

    def __init__(self, cost_profiles: Mapping[tuple[str, str], Mapping],
                 decisions_provider: Callable[[BacktestRequest], Sequence[Any]] | None = None,
                 bars_provider: Callable[[BacktestRequest], Any] | None = None,
                 semantics: Mapping[str, MarketSemantics] | None = None, core=None,
                 matrix: Sequence[Mapping] | None = None):
        self.cost_profiles = cost_profiles
        self.decisions_provider, self.bars_provider = decisions_provider, bars_provider
        self.semantics = dict(semantics or {})
        self._core = core
        self.identity = core_identity()
        self.matrix = list(matrix) if matrix is not None else compatibility_matrix(core)

    def gaps(self) -> list[str]:
        return missing_for(self.REQUIRED_FOR_FORMAL_RUN, self.matrix)

    def run(self, request: BacktestRequest) -> BacktestReport:
        sha = self.identity.get("git_sha")
        if not sha:
            raise CoreContractGap(["K11"], "installed Core has no resolvable commit")
        if self.decisions_provider is None or self.bars_provider is None:
            raise ContractViolation("decisions_provider and bars_provider are required")
        spec = request.spec
        validate_dataset_refs(request.datasets)
        ref = spec.get("costs", {}).get("profile_ref", {})
        profile = self.cost_profiles.get((ref.get("profile_id"), ref.get("version")))
        validate_cost_profile(spec, profile)
        if spec.get("execution", {}).get("convention") != CONVENTION:
            raise ContractViolation(f"spec execution convention != {CONVENTION}")
        gaps = self.gaps()
        if gaps:
            raise CoreContractGap(gaps, f"core {sha[:7]}")
        sem = self.semantics.get(request.datasets[0].dataset_id)
        if sem is None:
            raise ContractViolation(f"no confirmed market semantics for {request.datasets[0].dataset_id}")
        if sem.bar_label != "START" or sem.session_semantics != "CONFIRMED":
            raise ContractViolation(f"{sem.market}: bar label {sem.bar_label} / session {sem.session_semantics} "
                                    "not confirmed - formal execution blocked")
        core = self._core or _core()
        bars = self.bars_provider(request)
        check_core_bars(bars)
        uni, sizing = spec.get("universe", {}), spec.get("position_sizing", {})
        symbols = list(uni.get("symbols", []))
        if len(symbols) != 1:
            raise ContractViolation(f"M1 adapter executes single-symbol specs only, got {symbols}")
        if sizing.get("method") != "FIXED_UNITS":
            raise ContractViolation(f"position_sizing {sizing.get('method')} not mapped (FIXED_UNITS only)")
        if sem.constant_tick_size is None:
            raise CoreContractGap(["K03"], "price-banded tick ladder ("
                                  f"{spec.get('costs', {}).get('slippage', {}).get('tick_size_ref')}) not expressible "
                                  "as one CostProfile.tick_size")
        targets = decisions_to_targets(self.decisions_provider(request))
        orders = targets_to_orders(targets, symbol=symbols[0], market=sem.market, quantity=float(sizing["units"]),
                                   strategy_id=str(spec.get("strategy_id")), experiment_id=request.spec_sha256[:16],
                                   core=core)
        futures = sem.market in ("TXF", "IBKR_FUTURES")
        cp = core_cost_profile(profile, instrument_type="FUTURES" if futures else "EQUITY",
                               tick_size=sem.constant_tick_size,
                               contract_multiplier=float(sizing.get("contract_multiplier", 1.0)), core=core)
        cfg = core_config(request, sem, sha, core=core)
        result = core.ExecutionEngine().run(bars, orders, cost_profile=cp, config=cfg)
        guard_known_divergences(result, orders, bars, unsupported_divergences(matrix=self.matrix))
        return core_result_to_report(result, request, sha, cost_profile=cp, agent_profile=profile)


# ------------------------------------------------------------------------------------------ divergence guard
GUARD_CAPS = {"G17": "K17", "G14b": "K14b"}


def unsupported_divergences(core=None, matrix=None) -> set[str]:
    """Gap ids whose capability the installed Core does not demonstrate (measured, never assumed)."""
    m = {r["cap_id"]: r["status"] for r in (matrix or compatibility_matrix(core))}
    return {g for g, cap in GUARD_CAPS.items() if m.get(cap) != "SUPPORTED"}


def known_divergences(result, orders, bars, only: set[str] | None = None) -> list[dict]:
    """Detect the two Core-vs-Oracle-v1 differences found in M1.2 (G17, G14b) on a finished run. Read-only: it does
    not change fills or PnL; it only tells the caller that the run touched a situation where Core and the
    approved convention disagree, so the report must not be used.
      G17 - a target_position order became active while an earlier order on the same contract was still live
            (unfilled and unexpired); Core sizes it from the filled position, the convention from position+pending.
      G14b - a DAY order filled on a bar whose bar_start equals its expiry (the first session_close after
            order_time), i.e. on the opening bar of the next session; the convention expires it."""
    import pandas as pd

    def T(x):
        return pd.Timestamp(x)

    rows = bars if isinstance(bars, list) else bars.to_dict("records")
    closes = sorted({T(r["session_close"]) for r in rows})
    fills = result.fills.to_dict("records") if len(result.fills) else []
    filled_at = {}
    for f in fills:
        filled_at.setdefault(f["order_id"], T(f["exec_ts"]))
    out = []

    def expiry(o):
        return next((c for c in closes if c > T(o.order_time)), None)

    live = []
    for o in sorted(orders, key=lambda o: (o.order_time, o.order_id)):
        end = filled_at.get(o.order_id) or (expiry(o) if o.time_in_force == "DAY" else None)
        for j, jend in live:
            if (only is None or "G17" in only) and j.contract == o.contract and \
                    (jend is None or jend > T(o.order_time)) and o.target_position is not None:
                out.append({"gap": "G17", "order_id": o.order_id, "while_live": j.order_id,
                            "order_time": str(o.order_time)})
        live.append((o, end))
        ex = expiry(o) if o.time_in_force == "DAY" else None
        if (only is None or "G14b" in only) and o.order_id in filled_at and ex is not None \
                and filled_at[o.order_id] >= ex:
            out.append({"gap": "G14b", "order_id": o.order_id, "fill_ts": str(filled_at[o.order_id]),
                        "expiry": str(ex)})
    return out


def guard_known_divergences(result, orders, bars, only: set[str] | None = None) -> None:
    """Refuse the report when the run touched a G17/G14b situation AND the installed Core does not demonstrate the
    corresponding capability (K17/K14b). With both SUPPORTED this is a no-op."""
    if only is not None and not only:
        return
    hits = known_divergences(result, orders, bars, only)
    if hits:
        raise CoreContractGap(sorted({"K17" if h["gap"] == "G17" else "K14b" for h in hits}),
                              f"run hit a Core/convention difference: {hits[:3]}")


# ------------------------------------------------------------------------------------------ engine status
BENCHMARK_RECORD_DIR = Path(__file__).resolve().parents[3] / "benchmark" / "results"


REQUIRED_ORACLE_PROBES = ("G17", "G14b")


def engine_benchmark_status(core_sha: str | None, record_dir: Path = BENCHMARK_RECORD_DIR) -> dict:
    """BENCHMARK_PASS only when a cross-check record produced by the REAL Core at exactly this commit shows every
    case B01-B12 PASS (exact Decimal comparison) AND the independent G16 chronology acceptance PASS AND the
    oracle-generated probes G17 (pending target rebalance) and G14b (half-open DAY expiry) PASS. Anything else
    (missing, other SHA, fake engine, any non-PASS, no G16 evidence) is NOT_VERIFIED. Never hand-set."""
    if not core_sha:
        return {"status": "NOT_VERIFIED", "why": "core commit unknown"}
    p = Path(record_dir) / f"mrc_{core_sha[:12]}" / "crosscheck.json"
    if not p.exists():
        return {"status": "NOT_VERIFIED", "why": f"no cross-check record {p.parent.name} for {core_sha[:12]}"}
    rec = json.loads(p.read_text(encoding="utf-8"))
    cases = {r["case_id"][:3]: r["status"] for r in rec.get("cases", [])}
    g16 = rec.get("probes", {}).get("G16_acceptance", {})
    g16_ok = g16.get("status") == "PASS" and bool(g16.get("checks")) and all(g16["checks"].values())
    probes = rec.get("probes", {}).get("oracle_probes", {})
    probe_ok = all(probes.get(g, {}).get("status") == "PASS" for g in REQUIRED_ORACLE_PROBES)
    ok_cases = (rec.get("engine", {}).get("git_sha") == core_sha and rec.get("engine", {}).get("kind") == "REAL_CORE"
                and sorted(cases) == list(ALL) and all(s == "PASS" for s in cases.values()))
    ok = ok_cases and g16_ok and probe_ok
    if ok:
        why = "all 12 cases, G16 acceptance and oracle probes G17/G14b PASS on the real Core"
    else:
        bad = [f"{k}={v}" for k, v in sorted(cases.items()) if v != "PASS"]
        if not g16_ok:
            bad.append(f"G16={g16.get('status', 'MISSING')}")
        bad += [f"{g}={probes.get(g, {}).get('status', 'MISSING')}" for g in REQUIRED_ORACLE_PROBES
                if probes.get(g, {}).get("status") != "PASS"]
        why = f"record verdict {rec.get('verdict')}: " + ", ".join(bad)
    return {"status": "BENCHMARK_PASS" if ok else "NOT_VERIFIED", "why": why}


def engine_info():
    """EngineInfo for the runner, derived from the installed Core and its cross-check record (never hand-set)."""
    from strategy_agent.runner import EngineInfo  # noqa: PLC0415
    ident = core_identity()
    st = engine_benchmark_status(ident.get("git_sha"))
    return EngineInfo(name=CORE_DIST, git_sha=ident.get("git_sha") or "", version=ident.get("version") or "",
                      benchmark_status=st["status"])


def ts_label(ts: Any, tz: str | None = None) -> str:
    """Timestamp -> benchmark label 'YYYY-MM-DD HH:MM' in the case timezone (formatting only)."""
    if isinstance(ts, str):
        return ts[:16].replace("T", " ")
    if tz is not None and getattr(ts, "tzinfo", None) is not None:
        ts = ts.tz_convert(tz) if hasattr(ts, "tz_convert") else ts.astimezone()
    return ts.strftime(TS_FMT)
