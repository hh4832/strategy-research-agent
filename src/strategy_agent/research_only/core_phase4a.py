"""Binding to market-research-core Phase 4-A (TSM RESEARCH_ONLY adapter contract v1.0.0, Core e31c57b).

Everything market-related is Core's: native loading + conservative 15m bars (``load_native_tsm``), cost profile
(``build_tsm_research_profile``), execution (``ExecutionEngine``) and the SEC / TAF / CAT attribution
(``tsm_cost_diagnostics``). The Agent supplies governance, the SMA 5/20 decisions (M2 pure function), the order
mapping (M1 adapter) and the archive. Nothing here re-times a bar or re-computes a fee.

``probe_snapshot`` is a metadata-only compatibility check run BEFORE the authorization batch slot is consumed:
it reads the manifest bytes and the Parquet footer (schema + timestamp row-group statistics only). It never
decodes OHLCV pages and never hashes the Parquet file. Its rejection codes mirror Core's
``NativeSnapshotRejected`` codes; Core still enforces every rule again inside ``load_native_tsm``.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

CORE_PHASE4A_SHA = "e31c57b6c9dd599d808516ce6664628bdc79319e"
PINNED_RUN = "20261101_205431_06359faf7ef4_IBKR_FINAL_GATE_FULL"
PINNED_PRODUCER = "06359faf7ef4b8ff702a7ab1a6d07521d3b68f28"
LAYOUT_FIELDS = ("snapshot_id", "producer_sha", "symbol", "parquet_path", "parquet_sha256", "columns", "arrow_schema")
SCENARIOS = {"BASE": ("BASE", "BASE"), "LOW": ("LOW", "BASE"), "STRESS": ("STRESS", "BASE"),
             "CAT_STRESS": ("BASE", "STRESS")}          # name -> (slippage_scenario, cat_scenario) in Core terms


class Phase4ABindingError(RuntimeError):
    pass


@dataclass(frozen=True)
class SnapshotBinding:
    """Caller pins for Core ``load_native_tsm``. ``None`` means 'the existing manifest has no such field' and is a
    blocker - never filled with a guessed default."""
    manifest_relative_path: str
    manifest_sha256: str | None
    layout: dict                                  # field -> JSON pointer or None
    integrity_receipt: dict | None                # FileIntegrityReceipt fields, or None
    integrity_receipt_sha256: str | None
    diagnostic_parquet_relative_path: str | None  # observed Drive file name; footer diagnostics ONLY, never loading
    snapshot_pin: dict | None = None  # explicit reviewed v1.1 development snapshot identity/lineage pin

    @classmethod
    def from_config(cls, cfg: dict) -> "SnapshotBinding":
        b = cfg["snapshot_binding"]
        return cls(b["manifest_relative_path"], b.get("manifest_sha256"), dict(b["layout"]), b.get("integrity_receipt"),
                   b.get("integrity_receipt_sha256"), b.get("diagnostic_parquet_relative_path"), b.get("snapshot_pin"))


@dataclass
class ProbeResult:
    status: str                                   # COMPATIBLE | BLOCKED
    blockers: list = field(default_factory=list)  # [(code, message)]
    observed: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"status": self.status, "blockers": [{"code": c, "message": m} for c, m in self.blockers],
                "observed": self.observed}


def _pointer(doc: Any, pointer: str):
    for token in pointer[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        doc = doc[int(token)] if isinstance(doc, list) else doc[token]
    return doc


def _date(ts) -> str:
    import pandas as pd  # noqa: PLC0415
    return pd.Timestamp(ts).tz_convert("America/New_York").date().isoformat()


def probe_snapshot(snapshot_root: str | Path, binding: SnapshotBinding, *, dates: tuple[str, str],
                   research_window: tuple[str, str], holdout: tuple[tuple[str, str], ...]) -> ProbeResult:
    """Metadata-only check of the ACTUAL snapshot against Core Phase 4-A requirements (no slot, no OHLCV)."""
    b: list = []
    obs: dict = {"snapshot_root_basename": Path(snapshot_root).name}
    root = Path(snapshot_root)
    if not root.is_dir():
        return ProbeResult("BLOCKED", [("SNAPSHOT_ID_MISMATCH", f"snapshot root not found: {root}")], obs)
    expected_run=(binding.snapshot_pin or {}).get("snapshot_id",PINNED_RUN)
    expected_producer=(binding.snapshot_pin or {}).get("producer_sha",PINNED_PRODUCER)
    if root.name != expected_run:
        b.append(("SNAPSHOT_ID_MISMATCH", f"root basename {root.name!r} != {expected_run}"))

    # -- manifest bytes (small metadata file; reading it is not data access)
    mpath = root / binding.manifest_relative_path
    if not mpath.is_file():
        return ProbeResult("BLOCKED", b + [("UNSAFE_PATH", f"manifest {binding.manifest_relative_path} not found")], obs)
    mbytes = mpath.read_bytes()
    msha = hashlib.sha256(mbytes).hexdigest()
    obs["manifest"] = {"relative_path": binding.manifest_relative_path, "sha256": msha, "size": len(mbytes)}
    if binding.manifest_sha256 is None:
        b.append(("SHA_REQUIRED", f"no pinned manifest sha256 (observed {msha}); pin it in a reviewed config"))
    elif binding.manifest_sha256 != msha:
        b.append(("SNAPSHOT_SHA_MISMATCH", f"manifest sha {msha} != pinned {binding.manifest_sha256}"))
    doc = json.loads(mbytes)
    obs["manifest"]["top_level_keys"] = sorted(doc) if isinstance(doc, dict) else type(doc).__name__
    if isinstance(doc, dict):
        obs["manifest"].update({k: doc.get(k) for k in ("gate", "schema_version", "mode", "git_sha", "overall_verdict")
                                if k in doc})
        tsm = [(i, r) for i, r in enumerate(doc.get("results", [])) if isinstance(r, dict) and r.get("symbol") == "TSM"]
        if tsm:
            i, r = tsm[0]
            obs["manifest"]["tsm_result_pointer"] = f"/results/{i}"
            obs["manifest"]["tsm_result_keys"] = sorted(r)
            obs["manifest"]["tsm_result_scalars"] = {k: r.get(k) for k in
                                                     ("symbol", "verdict", "rows", "start_utc", "end_utc",
                                                      "published_sha256") if k in r}

    # -- the seven Core layout pointers
    resolved: dict = {}
    for f in LAYOUT_FIELDS:
        p = binding.layout.get(f)
        if p is None:
            b.append(("MANIFEST_LAYOUT", f"existing manifest has no field for '{f}' (no pointer can be bound)"))
            continue
        try:
            resolved[f] = _pointer(doc, p)
        except (KeyError, IndexError, TypeError, ValueError):
            b.append(("MANIFEST_LAYOUT", f"pointer for '{f}' does not resolve: {p}"))
    if "snapshot_id" in resolved and resolved["snapshot_id"] != expected_run:
        b.append(("SNAPSHOT_ID_MISMATCH", f"manifest snapshot id {resolved['snapshot_id']!r} != {expected_run}"))
    if "producer_sha" in resolved and resolved["producer_sha"] != expected_producer:
        b.append(("SNAPSHOT_ID_MISMATCH", f"manifest producer {resolved['producer_sha']!r} != {expected_producer} "
                                          "(Core requires the full 40-hex producer sha)"))
    if "symbol" in resolved and resolved["symbol"] != "TSM":
        b.append(("SNAPSHOT_ID_MISMATCH", f"manifest symbol {resolved['symbol']!r} != TSM"))
    if binding.snapshot_pin is not None:
        try:
            from strategy_agent.adapters.mrc_phase4a import core_development
            development,native=core_development()
            DevelopmentSnapshotPin,validate_derived_manifest=development.DevelopmentSnapshotPin,development.validate_derived_manifest
            DateWindow=native.DateWindow
            validate_derived_manifest(root.resolve(),doc,DevelopmentSnapshotPin(**binding.snapshot_pin),
                                      DateWindow(*dates),tuple(DateWindow(*h) for h in holdout))
        except (ValueError,TypeError,ImportError,OSError) as e:
            b.append((getattr(e,'code','DERIVED_PIN'),str(e)))
    obs["layout_resolved"] = {k: (v if k not in ("columns", "arrow_schema") else "present") for k, v in resolved.items()}

    # -- Parquet footer: schema and timestamp row-group statistics only
    rel = resolved.get("parquet_path") or binding.diagnostic_parquet_relative_path
    obs["parquet_path_source"] = ("manifest" if resolved.get("parquet_path") else
                                  "diagnostic_only (observed Drive file; Core would not accept it without a manifest "
                                  "path)" if rel else None)
    if rel:
        ppath = root / rel
        if not ppath.is_file():
            b.append(("UNSAFE_PATH", f"parquet {rel} not found"))
        else:
            b += _footer_checks(ppath, resolved, dates, research_window, holdout, obs)
    else:
        b.append(("MANIFEST_LAYOUT", "no parquet path available to inspect"))

    # -- integrity receipt (stat-guarded, never a new hash here)
    if binding.integrity_receipt is None:
        b.append(("INTEGRITY_RECEIPT", "no trusted FileIntegrityReceipt; Core's verify_file_bytes would read the "
                                       "entire file's bytes (including holdout bytes) and needs explicit "
                                       "research-lead authorization"))
    elif rel and (root / rel).is_file():
        s = (root / rel).stat()
        stamp = dict(device=s.st_dev, inode=s.st_ino, size=s.st_size, mtime_ns=s.st_mtime_ns, ctime_ns=s.st_ctime_ns)
        if binding.integrity_receipt.get("stat") != stamp:
            b.append(("INTEGRITY_RECEIPT", "receipt stat does not match the file in this session (stale receipt)"))
    if binding.integrity_receipt is not None:
        receipt=binding.integrity_receipt
        expected_fields={'parquet_sha256','manifest_sha256','stat','verification_ref'}
        if (set(receipt)!=expected_fields or not receipt.get('verification_ref') or
                hashlib.sha256(json.dumps(receipt,sort_keys=True,separators=(',',':')).encode()).hexdigest()!=binding.integrity_receipt_sha256 or
                receipt.get('manifest_sha256')!=msha or receipt.get('parquet_sha256')!=resolved.get('parquet_sha256')):
            b.append(('INTEGRITY_RECEIPT','receipt pin does not bind verified file and manifest'))
    return ProbeResult("BLOCKED" if b else "COMPATIBLE", b, obs)


def _footer_checks(ppath: Path, resolved: dict, dates, research_window, holdout, obs) -> list:
    import pandas as pd  # noqa: PLC0415
    import pyarrow as pa  # noqa: PLC0415
    import pyarrow.parquet as pq  # noqa: PLC0415
    out: list = []
    pf = pq.ParquetFile(ppath)
    schema = {f.name: str(f.type) for f in pf.schema_arrow}
    obs["parquet"] = {"relative_path": str(ppath.name), "size": ppath.stat().st_size, "num_rows": pf.metadata.num_rows,
                      "num_row_groups": pf.num_row_groups, "arrow_schema": schema,
                      "created_by": pf.metadata.created_by}
    if "arrow_schema" in resolved and resolved["arrow_schema"] != schema:
        out.append(("SCHEMA_MISMATCH", "manifest arrow_schema differs from the file footer"))
    ts_candidates = [n for n, t in schema.items() if t.startswith("timestamp")]
    ts_name = (resolved.get("columns") or {}).get("timestamp") if isinstance(resolved.get("columns"), dict) else None
    ts_name = ts_name or (ts_candidates[0] if len(ts_candidates) == 1 else None)
    if ts_name is None:
        out.append(("SCHEMA_MISMATCH", f"cannot identify one timestamp column (candidates {ts_candidates})"))
        return out
    ts_type = pf.schema_arrow.field(ts_name).type
    obs["parquet"]["timestamp_column"] = ts_name
    obs["parquet"]["timestamp_tz"] = getattr(ts_type, "tz", None)
    if not pa.types.is_timestamp(ts_type) or not ts_type.tz:
        out.append(("SCHEMA_MISMATCH", "timestamp column has no Arrow timezone"))
    idx = pf.schema.names.index(ts_name)
    lo = pd.Timestamp(dates[0], tz="America/New_York")
    hi = pd.Timestamp(date.fromisoformat(dates[1]), tz="America/New_York") + pd.Timedelta(days=1)
    groups = []
    for i in range(pf.num_row_groups):
        rg = pf.metadata.row_group(i)
        st = rg.column(idx).statistics          # timestamp column ONLY; price statistics are never read
        if st is None or not st.has_min_max:
            out.append(("ROW_GROUP_BOUNDARY_UNKNOWN", f"row group {i} has no timestamp min/max"))
            continue
        first, last = pd.Timestamp(st.min), pd.Timestamp(st.max)
        if first.tzinfo is None or last.tzinfo is None:
            out.append(("ROW_GROUP_BOUNDARY_UNKNOWN", f"row group {i} statistics are not timezone-aware"))
            continue
        g = {"index": i, "rows": rg.num_rows, "first_date": _date(first), "last_date": _date(last),
             "candidate": bool(last >= lo and first < hi)}
        if g["candidate"]:
            inside = research_window[0] <= g["first_date"] and g["last_date"] <= research_window[1]
            crosses = [h for h in holdout if g["first_date"] <= h[1] and h[0] <= g["last_date"]]
            g["inside_authorized_window"], g["overlaps_holdout"] = inside, bool(crosses)
            if not inside or crosses:
                out.append(("HOLDOUT_ROW_GROUP_OVERLAP",
                            f"candidate row group {i} spans {g['first_date']}..{g['last_date']} ({g['rows']} rows): "
                            f"{'outside authorized window ' + str(research_window) if not inside else ''}"
                            f"{' overlaps holdout' if crosses else ''}; Core would refuse to decode it"))
        groups.append(g)
    obs["parquet"]["row_groups"] = groups
    return out


# ============================================================================== execution (Core does the work)

def research_profiles(core_costs, *, sec_config: Path, sec_sha: str, taf_record: Path, taf_sha: str,
                      scenarios: list[str]) -> dict:
    """Core ``build_tsm_research_profile`` per scenario (fails before any slot is consumed)."""
    out = {}
    for name in scenarios:
        slip, cat = SCENARIOS[name]
        out[name] = core_costs.build_tsm_research_profile(sec_config, taf_record, expected_sec_configuration_sha256=sec_sha,
                                                          expected_taf_sha256=taf_sha, slippage_scenario=slip,
                                                          cat_scenario=cat)
    return out


def conservative_rows_and_bars(frame, symbol: str = "TSM"):
    """Core conservative frame -> evaluator rows + SignalBars. Same mapping as NativeSyntheticSource: signal time =
    Core ``modeled_available_at``, eligibility = Core ``signal_eligible``; nothing is re-timed here."""
    import pandas as pd  # noqa: PLC0415

    from strategy_agent.signals.core import SignalBar  # noqa: PLC0415
    rows, sbars = [], []
    for b in frame.drop(columns=["source_rows"], errors="ignore").to_dict("records"):
        b = {k: (v.tz_convert("America/New_York").to_pydatetime() if isinstance(v, pd.Timestamp) and v.tzinfo else v)
             for k, v in b.items()}
        day = b["source_timestamp"].date().isoformat()
        rows.append(b | {"session_class": b["session"], "session": day})
        sbars.append(SignalBar(b["bar_start"], b["modeled_available_at"], float(b["open"]), float(b["high"]),
                               float(b["low"]), float(b["close"]), float(b["volume"]), symbol, day,
                               bool(b["signal_eligible"])))
    return rows, sbars


def execute_batch(core, core_costs, *, frame, core_sha: str, profiles: dict, spec: dict, parameters: dict,
                  window_start: str, window_end: str, warmup_start: str, dataset: dict, seed: int = 0) -> dict:
    """Run every authorized scenario on the SAME Core-prepared bars and the SAME orders (M3-B mapping reused)."""
    from strategy_agent.adapters import mrc_adapter as A  # noqa: PLC0415
    from strategy_agent.adapters.m3a4_contract import validate_conservative_result  # noqa: PLC0415
    from strategy_agent.evaluation.research import evaluate  # noqa: PLC0415
    from strategy_agent.ports import BacktestRequest, DatasetRef  # noqa: PLC0415
    from strategy_agent.signals.sma import sma_crossover  # noqa: PLC0415
    from strategy_agent.spec.validate import canonical_hash  # noqa: PLC0415

    A.check_core_bars(frame)
    rows, sbars = conservative_rows_and_bars(frame)
    decs = sma_crossover(sbars, int(parameters["fast"]), int(parameters["slow"]))
    ws, we = datetime.fromisoformat(window_start), datetime.fromisoformat(window_end)
    if spec.get("version")=="0.2.0" and sum(b.eligible and b.available_at<ws for b in sbars)<int(spec['data_requirements']['warmup_bars']):
        raise Phase4ABindingError('insufficient eligible pre-window warm-up bars')
    kept = [d for d in decs if ws <= d.decision_ts < we]
    if any(not any(b.available_at == d.decision_ts for b in sbars) for d in kept):
        raise Phase4ABindingError("decision not at a Core modeled_available_at")
    sizing = spec["position_sizing"]
    qty, cash = float(sizing["units"]), float(sizing["initial_capital"])
    spec_sha = canonical_hash(spec)
    request = BacktestRequest(spec=spec, spec_sha256=spec_sha,
                              datasets=(DatasetRef(dataset["dataset_id"], dataset["snapshot_id"],
                                                   dataset["producer_commit_sha"], "GO", "BACKTEST"),),
                              window_start=window_start, window_end=window_end, warmup_start=warmup_start,
                              cost_multiplier=1.0, seed=seed, parameters=dict(parameters))
    sem = A.MarketSemantics(market="IBKR_EQUITY", bar_label="UNKNOWN", session_semantics="CONFIRMED",
                            timezone="America/New_York", initial_cash=cash, constant_tick_size=0.01,
                            available_at_policy=A.CONSERVATIVE)
    orders = A.targets_to_orders(A.decisions_to_targets(kept), symbol="TSM", market=sem.market, quantity=qty,
                                 strategy_id=str(spec["strategy_id"]), experiment_id=spec_sha[:16], core=core)
    cfg = A.core_config(request, sem, core_sha, core=core)
    out = {"decisions": [(d.decision_ts.isoformat(), d.target, d.reason) for d in kept],
           "decisions_total": len(decs), "orders": len(orders), "rows": rows, "scenarios": {}}
    unsupported = A.unsupported_divergences(core)
    for name, profile in profiles.items():
        result = core.ExecutionEngine().run(frame, orders, cost_profile=profile, config=cfg)
        A.guard_known_divergences(result, orders, frame, unsupported)
        checks = validate_conservative_result(result, orders=orders, cost_profile=profile,
                                              policy=core.CONSERVATIVE_POLICY, core_sha=core_sha)
        report = A.core_result_to_report(result, request, core_sha, cost_profile=profile)
        diag = core_costs.tsm_cost_diagnostics(result, profile)
        exp = spec.get("experiment") or {}
        extra = {}                       # 5-C1: only when the spec pre-registers it (older specs: unchanged output)
        if "family_trials_including_this" in exp:
            extra.update(family_id=exp.get("trial_family_id", "UNASSIGNED"),
                         family_trials=int(exp["family_trials_including_this"]))
        metrics = evaluate(report, rows, initial_capital=cash, bar_minutes=15, bars_per_year=252 * 26,
                           parameters=parameters, symbol="TSM", order_qty=qty, **extra)
        metrics.update(preregistered_decompositions(report, rows, exp))
        out["scenarios"][name] = {"profile_sha256": profile.sha256, "result": result, "report": report,
                                  "m3a4_checks": checks, "cost_diagnostics": diag, "metrics": metrics}
    return out


def preregistered_decompositions(report, rows, experiment: dict) -> dict:
    """5-C1: the spec's pre-registered regime decomposition and drift-matched control (``evaluation.regimes`` /
    ``evaluation.controls``), added next to the M3-B metrics (which stay byte-identical). Nothing for older specs."""
    from zoneinfo import ZoneInfo  # noqa: PLC0415

    from strategy_agent.evaluation import controls as CT  # noqa: PLC0415
    from strategy_agent.evaluation import regimes as RG  # noqa: PLC0415
    from strategy_agent.evaluation.research import trade_stats  # noqa: PLC0415
    out: dict = {}
    trips = [dict(t) for t in report.round_trips]
    if experiment.get("regime_definition"):
        ny = ZoneInfo("America/New_York")

        def session_of(t):
            ts = t["entry_ts"] if isinstance(t["entry_ts"], datetime) else datetime.fromisoformat(str(t["entry_ts"]))
            return ts.astimezone(ny).date().isoformat()
        out["by_regime"] = RG.decompose(trips, rows, experiment["regime_definition"], stats_fn=trade_stats,
                                        session_of=session_of)
    if experiment.get("drift_control"):
        if experiment["drift_control"] != CT.VERSION:
            raise Phase4ABindingError(f"unknown drift control {experiment['drift_control']!r}")
        out["drift_control"] = CT.drift_matched(trips, rows)
        if experiment.get("family_trials_including_this")==3:
            out.update(CT.c1_decompositions(trips,rows))
    return out


def cost_set_consistency(cost_set, core_costs, taf_record: dict, on: date) -> list:
    """The Core research constants must equal the user-accepted Agent cost set (CO07). Read-only comparison."""
    from decimal import Decimal  # noqa: PLC0415
    out = []
    for name, (slip, cat) in SCENARIOS.items():
        r = cost_set.rates(name, on)
        if Decimal(str(core_costs.SLIPPAGE_BPS[slip])) != r.slippage_bps:
            out.append(("CO07", f"{name}: Core slippage {core_costs.SLIPPAGE_BPS[slip]} != cost set {r.slippage_bps}"))
        if Decimal(core_costs.CAT_PER_SHARE[cat]) != r.cat_per_share:
            out.append(("CO07", f"{name}: Core CAT {core_costs.CAT_PER_SHARE[cat]} != cost set {r.cat_per_share}"))
        if Decimal(str(taf_record["usd_per_share"])) != r.taf_per_share:
            out.append(("CO07", f"{name}: TAF record {taf_record['usd_per_share']} != cost set {r.taf_per_share}"))
    return out


def rounding_masked(batch: dict) -> dict:
    """ROUNDING_MASKED_DIFFERENCE: CAT_STRESS vs BASE differ in the unrounded CAT model but not in Core's debit."""
    s = batch["scenarios"]
    if "BASE" not in s or "CAT_STRESS" not in s:
        return {"status": "NOT_EVALUATED"}
    b, c = s["BASE"]["cost_diagnostics"]["totals"], s["CAT_STRESS"]["cost_diagnostics"]["totals"]
    unrounded_diff = c["cat_unrounded"] - b["cat_unrounded"]
    debit_diff = c["regulatory_total"] - b["regulatory_total"]
    masked = unrounded_diff != 0 and debit_diff == 0
    return {"status": "ROUNDING_MASKED_DIFFERENCE" if masked else ("DIFFERENCE_VISIBLE" if debit_diff else "NO_DIFFERENCE"),
            "cat_unrounded_difference": str(unrounded_diff), "regulatory_debit_difference": str(debit_diff),
            "interpretation": "a masked difference is a rounding artefact, NOT evidence that CAT has no cost impact"}


def native_snapshot_options(binding: SnapshotBinding) -> dict:
    if binding.snapshot_pin is None:
        return {}
    from strategy_agent.adapters.mrc_phase4a import core_development
    development,_=core_development()
    return {'snapshot_pin':development.DevelopmentSnapshotPin(**binding.snapshot_pin)}


def load_continuous_native(core_native, snapshot_root, binding, *, dates, research_window, holdout):
    """Bounded IO only. Concatenate before SMA/orders/ONE engine call per scenario; never stitch backtests.

    Daily footer row counts prove every native record in the requested interval
    was returned exactly once. Queries partition New York dates (DST aware in Core).
    """
    from datetime import timedelta
    from types import SimpleNamespace
    import pandas as pd
    import pyarrow.parquet as pq
    DW=core_native.DateWindow
    kwargs=dict(manifest_relative_path=binding.manifest_relative_path,
        expected_manifest_sha256=binding.manifest_sha256,layout=core_native.ManifestLayout(**binding.layout),
        integrity_receipt=core_native.FileIntegrityReceipt(**binding.integrity_receipt),
        expected_receipt_sha256=binding.integrity_receipt_sha256,symbol='TSM',research_window=DW(*research_window),
        holdout=tuple(DW(*h) for h in holdout),**native_snapshot_options(binding))
    start,end=map(date.fromisoformat,dates)
    if not DW(*research_window).contains(DW(*dates)) or any(DW(*dates).overlaps(DW(*h)) for h in holdout):
        raise Phase4ABindingError("requested interval reaches Holdout or exceeds research window")
    if (end-start).days<31:
        return core_native.load_native_tsm(snapshot_root,dates=DW(*dates),**kwargs)
    if not binding.snapshot_pin:
        raise Phase4ABindingError('long reads require an explicit Development snapshot pin')
    root=Path(snapshot_root).resolve(strict=True)
    mp=(root/binding.manifest_relative_path).resolve(strict=True)
    if not mp.is_relative_to(root) or hashlib.sha256(mp.read_bytes()).hexdigest()!=binding.manifest_sha256:
        raise Phase4ABindingError('manifest pin mismatch')
    doc=json.loads(mp.read_text())
    p=(root/doc['parquet_path']).resolve(strict=True)
    if not p.is_relative_to(root):raise Phase4ABindingError('unsafe Parquet path')
    pf=pq.ParquetFile(p);idx=pf.schema.names.index(doc['columns']['timestamp']);daily={}
    for i in range(pf.num_row_groups):
        g=pf.metadata.row_group(i);st=g.column(idx).statistics
        if st is None or not st.has_min_max:raise Phase4ABindingError('unknown daily boundary')
        a,b=pd.Timestamp(st.min),pd.Timestamp(st.max)
        if a.tzinfo is None or b.tzinfo is None:raise Phase4ABindingError('naive boundary')
        day=_date(a)
        if day!=_date(b) or not (binding.snapshot_pin['start']<=day<=binding.snapshot_pin['end']) or any(h[0]<=day<=h[1] for h in holdout):
            raise Phase4ABindingError('unsafe Development row group')
        daily[day]=daily.get(day,0)+g.num_rows
    chunks=[];queries=[];cursor=start
    while cursor<=end:
        last=min(cursor+timedelta(days=30),end)
        expected=sum(n for day,n in daily.items() if cursor.isoformat()<=day<=last.isoformat())
        if expected:
            result=core_native.load_native_tsm(root,dates=DW(cursor.isoformat(),last.isoformat()),**kwargs)
            if len(result.native)!=expected:raise Phase4ABindingError('chunk row count differs from footer')
            chunks.append(result)
        queries.append({'start':cursor.isoformat(),'end':last.isoformat(),'native_rows':expected})
        cursor=last+timedelta(days=1)
    if not chunks:raise Phase4ABindingError('empty long research window')
    native=pd.concat([r.native for r in chunks],ignore_index=True)
    source=pd.concat([r.source_1m for r in chunks],ignore_index=True)
    bars=pd.concat([r.conservative_15m for r in chunks],ignore_index=True)
    ts=native[doc['columns']['timestamp']]
    if ts.duplicated().any() or not ts.is_monotonic_increasing:
        raise Phase4ABindingError('duplicate or unordered chunk records')
    if bars.source_timestamp.duplicated().any() or not bars.source_timestamp.is_monotonic_increasing:
        raise Phase4ABindingError('duplicate or unordered conservative buckets')
    provenance=dict(chunks[0].provenance,requested_dates={'start':dates[0],'end':dates[1]},
                    native_rows=len(native),conservative_buckets=len(bars),bounded_read_windows=queries,
                    execution_partitioning='NONE_ONE_CONTINUOUS_SMA_AND_ENGINE_RUN_PER_SCENARIO')
    return SimpleNamespace(native=native,source_1m=source,conservative_15m=bars,provenance=provenance)
