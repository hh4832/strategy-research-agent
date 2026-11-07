"""Immutable research-only archive (Phase 4-A run contract).

Every number written here is copied from Core (fills, round trips, rejections, equity, cost diagnostics) or from
the existing M3-B evaluator. The only arithmetic done here is presentation-level: grouping by scenario, holding
period in bars ((exit - entry) / 15 min), unthresholded descriptive trade ratios (same formulas as the evaluator,
explicitly labelled when below the evaluator's minimum) and time differences used as temporal *checks*.
Nothing re-times a bar or re-computes a fee.

Files (all created with O_EXCL; nothing is ever overwritten):
run_info.txt, config.json, config_canonical.json, dataset_manifest.json, execution_diagnostics.json,
decisions.csv, trades.csv, fills.csv, rejected_orders.csv, cost_breakdown.csv, metrics.json, equity_curve.csv,
research_report.md, and MANIFEST.json (sha256 of every other file, written last).
"""
from __future__ import annotations

import csv
import io
import json
import math
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

TPE = ZoneInfo("Asia/Taipei")
NY = ZoneInfo("America/New_York")
COST_COLUMNS = ("commission", "slippage_cost", "tick_rounding_cost", "spread_cost", "price_adjustment_cost", "sec",
                "taf", "cat", "regulatory_total", "explicit_cost", "total_cost")
SCOPE = "RESEARCH_ONLY_EXPLORATORY"
NOT_CLAIMED = ["formal strategy validation", "Formal Market Eligibility (gate stays NO_GO)",
               "out-of-sample, cross-year or cross-regime robustness", "holdout evaluation",
               "family promotion / FROZEN / deployment", "account-verified (BROKER_ACTUAL) costs"]


def _jsonable(v):
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if hasattr(v, "item"):
        return v.item()
    if hasattr(v, "to_dict"):
        return v.to_dict()
    return str(v)


def dumps(obj) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True, default=_jsonable, ensure_ascii=False) + "\n").encode()


def _csv(rows: list[dict], columns: list[str] | None = None) -> bytes:
    cols = columns or sorted({k for r in rows for k in r})
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: (_jsonable(r.get(k)) if r.get(k) is not None and not isinstance(r.get(k), (str, int, float))
                        else r.get(k)) for k in cols})
    return buf.getvalue().encode()


def _records(df) -> list[dict]:
    return [] if df is None or len(df) == 0 else df.to_dict("records")


def _ts(v) -> datetime:
    import pandas as pd  # noqa: PLC0415
    return pd.Timestamp(v).to_pydatetime()


def descriptive(pnls: list[float]) -> dict:
    """Unthresholded trade ratios (same definitions as evaluation.research.trade_stats)."""
    n = len(pnls)
    wins, losses = [x for x in pnls if x > 0], [x for x in pnls if x < 0]
    aw = sum(wins) / len(wins) if wins else None
    al = sum(losses) / len(losses) if losses else None
    return {"trade_count": n, "wins": len(wins), "losses": len(losses), "net_pnl": math.fsum(pnls),
            "win_rate": len(wins) / n if n else None, "average_win": aw, "average_loss": al,
            "payoff_ratio": aw / abs(al) if aw is not None and al else None,
            "expectancy": math.fsum(pnls) / n if n else None,
            "profit_factor": math.fsum(wins) / abs(math.fsum(losses)) if losses else None,
            "label": "DESCRIPTIVE_ONLY" + ("_BELOW_EVALUATOR_MINIMUM_10_TRADES" if n < 10 else "")}


def temporal_diagnostics(fills: list[dict]) -> dict:
    """Checks on Core's own fill ledger (not re-computation): every fill is strictly after its signal's modeled
    availability, executes on a later bar than the signal bar, and the order time equals the signal time."""
    delays, before, same_bar = [], 0, 0
    for f in fills:
        ex, av = _ts(f["exec_ts"]), _ts(f["signal_available_at"])
        delays.append((ex - av).total_seconds() / 60)
        before += ex <= av
        same_bar += _ts(f["source_timestamp"]) <= _ts(f["signal_source_timestamp"])
    return {"fills_checked": len(fills),
            "fills_not_strictly_after_signal_available_at": int(before),
            "fills_on_or_before_signal_bar": int(same_bar),
            "order_time_equals_signal_time": all(_ts(f["signal_time"]) == _ts(f["order_time"]) for f in fills),
            "min_minutes_signal_available_to_fill": min(delays) if delays else None,
            "max_minutes_signal_available_to_fill": max(delays) if delays else None,
            "execution_assumptions": sorted({str(f.get("execution_assumption")) for f in fills}),
            "status": "PASS" if before == 0 and same_bar == 0 and
                      all(_ts(f["signal_time"]) == _ts(f["order_time"]) for f in fills) else "FAIL"}


def write_contract_archive(out_dir: Path, *, write_once, cfg: dict, run_id: str, use: dict, loaded, batch: dict,
                           core_identity: dict, holdout: tuple, context: dict, cat_rounding: dict,
                           started_at: datetime) -> dict:
    out_dir = Path(out_dir)
    frame = loaded.conservative_15m
    prov = dict(loaded.provenance)
    files: dict[str, str] = {}

    def put(name: str, data: bytes):
        files[name] = write_once(out_dir / name, data)

    scen = batch["scenarios"]
    bar_ends = [_ts(e) for e in frame["bar_end"]]
    fills_all, trades_all, rej_all, eq_all, cost_rows, metrics, temporal = [], [], [], [], [], {}, {}
    min_trades = int(cfg["sample"]["min_trades"])
    for name, sc in scen.items():
        r = sc["result"]
        fills = _records(r.fills)
        trips = _records(r.round_trips)
        rej = _records(r.rejections)
        eq = _records(r.equity_curve)
        fills_all += [{"scenario": name, **f} for f in fills]
        for t in trips:
            a, z = _ts(t["entry_ts"]), _ts(t["exit_ts"])
            held = sum(a < e <= z for e in bar_ends)          # Core 15m bars held (same rule as the evaluator)
            trades_all.append({"scenario": name, **t, "holding_bars_15m": held,
                               "holding_minutes_wall_clock": (z - a).total_seconds() / 60,
                               "overnight": a.astimezone(NY).date() != z.astimezone(NY).date()})
        rej_all += [{"scenario": name, **x} for x in rej]
        eq_all += [{"scenario": name, **e} for e in eq]
        tot = sc["cost_diagnostics"]["totals"]
        row = {"scenario": name, "cost_profile_sha256": sc["profile_sha256"],
               "slippage_bps": sc["cost_diagnostics"]["model"]["slippage_bps"],
               "cat_rate_usd_per_share": sc["cost_diagnostics"]["model"]["cat_rate_usd_per_share"],
               "taf_rate_usd_per_share": sc["cost_diagnostics"]["model"]["taf_rate_usd_per_share"]}
        for k in ("commission", "price_adjustment_cost", "sec", "taf", "cat", "regulatory_total", "explicit_cost"):
            row[k] = str(tot.get(k))
        for k in ("slippage_cost", "tick_rounding_cost", "spread_cost"):
            row[k] = str(sum((f[k] for f in fills), start=type(fills[0][k])(0)) if fills else 0)
        row["total_cost"] = str(Decimal(str(tot.get("explicit_cost"))) + Decimal(str(tot.get("price_adjustment_cost"))))
        row["fills"] = len(fills)
        cost_rows.append(row)
        pnls = [float(t["net_pnl"]) for t in trips if not t.get("invalidated")]
        m = dict(sc["metrics"])
        m["descriptive_trade_stats"] = descriptive(pnls)
        m["holding_period_bars_distribution"] = _holding(trades_all, name)
        m["sample_status"] = "INSUFFICIENT_SAMPLE" if len(pnls) < min_trades else "SAMPLE_OK"
        m["sample_minimum_trades"] = min_trades
        m["cost_profile_sha256"] = sc["profile_sha256"]
        m["m3a4_checks"] = sc["m3a4_checks"]
        metrics[name] = m
        temporal[name] = temporal_diagnostics(fills)

    native = loaded.native
    ts_col = "timestamp_utc" if "timestamp_utc" in native.columns else native.columns[0]
    hold_start = datetime.fromisoformat(holdout[0][0]).replace(tzinfo=NY) if holdout else None
    native_max = _ts(native[ts_col].max()) if len(native) else None
    data_window = {"native_1m_rows": int(len(native)),
                   "native_first_utc": _ts(native[ts_col].min()) if len(native) else None,
                   "native_last_utc": native_max,
                   "conservative_15m_bars": int(len(frame)),
                   "sessions": int(frame["session_open"].nunique()) if "session_open" in frame else None,
                   "signal_eligible_bars": int(frame["signal_eligible"].sum()),
                   "buy_execution_eligible_bars": int(frame["buy_execution_eligible"].sum()),
                   "sell_execution_eligible_bars": int(frame["sell_execution_eligible"].sum()),
                   "execution_rejection_reasons": {str(k): int(v) for k, v in
                                                   frame["execution_rejection_reason"].value_counts().items() if k},
                   "holdout_start": holdout[0][0] if holdout else None,
                   "max_native_timestamp_before_holdout": bool(native_max and hold_start and native_max < hold_start)}
    finished = datetime.now(timezone.utc)
    diag = {"scope": SCOPE, "data": data_window, "decisions": {"total_including_warmup": batch["decisions_total"],
                                                              "in_window": len(batch["decisions"]),
                                                              "orders": batch["orders"]},
            "temporal": temporal, "cat_rounding": cat_rounding,
            "m3a4_checks": {k: v["m3a4_checks"] for k, v in scen.items()},
            "rejections_by_scenario": {k: len(_records(v["result"].rejections)) for k, v in scen.items()},
            "loader_provenance": prov}

    sec_cfg = cfg["snapshot_binding"]
    info = {
        "run_id": run_id, "scope": SCOPE, "formal_market_status": "NO_GO (unchanged)",
        "execution_started_at_asia_taipei": started_at.astimezone(TPE).isoformat(),
        "execution_finished_at_asia_taipei": finished.astimezone(TPE).isoformat(),
        "agent_git_sha": context.get("agent_git_sha"), "agent_checkout_clean": context.get("agent_checkout_clean"),
        "notebook": context.get("notebook"),
        "core_git_sha": core_identity.get("core_git_sha"), "core_tree_dirty": core_identity.get("core_tree_dirty"),
        "snapshot_id": prov.get("snapshot_id"), "producer_sha": prov.get("producer_sha"),
        "manifest_sha256": prov.get("manifest_sha256"), "parquet_sha256": prov.get("parquet_sha256"),
        "lineage_sha256": (sec_cfg.get("snapshot_pin") or {}).get("lineage_sha256"),
        "integrity_receipt_sha256": prov.get("integrity_receipt_sha256"),
        "integrity_verification_ref": prov.get("integrity_verification_ref"),
        "canonical_config_path": context.get("canonical_config_path"),
        "canonical_config_sha256": context.get("canonical_config_sha256"),
        "session_config_sha256": cfg["provenance"]["config_sha256"],
        "session_config_difference": context.get("session_config_difference"),
        "authorization_id": cfg["authorization_id"], "authorization_record_sha256": context.get("authorization_record_sha256"),
        "authorization_use": use,
        "temporal_policy_sha256": cfg["temporal_policy"]["sha256"],
        "cost_profile_sha256": {k: v["profile_sha256"] for k, v in scen.items()},
        "strategy": f"{cfg['strategy']['strategy_id']} {cfg['strategy']['version']} {cfg['strategy']['parameters']}",
        "window": f"warm-up {cfg['windows']['warmup_start']}; [{cfg['windows']['start']}, {cfg['windows']['end']})",
        "holdout_accessed": False, "broker_api_called": False,
    }
    put("run_info.txt", "".join(f"{k}={json.dumps(v, default=_jsonable, ensure_ascii=False) if not isinstance(v, str) else v}\n"
                                for k, v in info.items()).encode())
    put("config.json", dumps(cfg))
    if context.get("canonical_config") is not None:
        put("config_canonical.json", dumps(context["canonical_config"]))
    put("dataset_manifest.json", dumps({"loader_provenance": prov, "snapshot_manifest": context.get("snapshot_manifest"),
                                        "snapshot_binding": sec_cfg}))
    put("execution_diagnostics.json", dumps(diag))
    put("decisions.csv", _csv([{"decision_ts": d[0], "target": d[1], "reason": d[2]} for d in batch["decisions"]],
                              ["decision_ts", "target", "reason"]))
    put("trades.csv", _csv(trades_all, _cols(trades_all, ["scenario", "entry_ts", "exit_ts", "side", "qty",
                                                          "entry_price", "exit_price", "gross_pnl", "net_pnl",
                                                          "holding_bars_15m", "holding_minutes_wall_clock", "overnight",
                                                          "invalidated", "invalidation_reason"])))
    put("fills.csv", _csv(fills_all, _cols(fills_all, ["scenario", "order_id", "side", "qty", "signal_time",
                                                       "signal_available_at", "order_time", "exec_ts", "raw_price",
                                                       "fill_price"])))
    put("rejected_orders.csv", _csv(rej_all, ["scenario", "order_id", "bar_ts", "reason", "detail", "expiry_ts"]))
    put("cost_breakdown.csv", _csv(cost_rows, ["scenario", "fills", "slippage_bps", "taf_rate_usd_per_share",
                                               "cat_rate_usd_per_share", *COST_COLUMNS, "cost_profile_sha256"]))
    put("metrics.json", dumps(metrics))
    put("equity_curve.csv", _csv(eq_all, ["scenario", "timestamp", "cash", "realized_net_pnl", "unrealized_pnl",
                                          "margin_requirement", "equity"]))
    put("research_report.md", research_report(info, diag, metrics, cost_rows).encode())
    summary = {"status": "COMPLETED", "run_id": run_id, "scope": SCOPE, "not": NOT_CLAIMED, "run_info": info,
               "files_sha256": dict(files)}
    put("MANIFEST.json", dumps(summary))
    compat = {name: {"profile_sha256": scen[name]["profile_sha256"], "fills": metrics[name].get("fills"),
                     "rejections": metrics[name].get("rejections"),
                     "trades": metrics[name]["descriptive_trade_stats"]["trade_count"],
                     "sample": metrics[name]["sample_status"],
                     "cost_totals": {k: str(v) for k, v in scen[name]["cost_diagnostics"]["totals"].items()}}
              for name in scen}
    counts = {"native_1m_rows": data_window["native_1m_rows"], "conservative_15m_bars": data_window["conservative_15m_bars"],
              "decisions_total": batch["decisions_total"], "decisions_in_window": len(batch["decisions"]),
              "orders": batch["orders"]}
    return summary | {"files_sha256": files, "metrics": metrics, "diagnostics": diag, "cost_breakdown": cost_rows,
                      "scenarios": compat, "counts": counts, "loader_provenance": prov, "cat_rounding": cat_rounding}


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _cols(rows, first):
    rest = sorted({k for r in rows for k in r} - set(first))
    return list(first) + rest


def _holding(trades: list[dict], scenario: str) -> dict:
    h = [t["holding_bars_15m"] for t in trades if t["scenario"] == scenario]
    if not h:
        return {"count": 0}
    s = sorted(h)
    overnight = sum(bool(t.get("overnight")) for t in trades if t["scenario"] == scenario)
    return {"unit": "Core 15m bars held (bar_end in (entry, exit])", "overnight_trades": overnight,
            "count": len(s), "min": s[0], "median": s[len(s) // 2] if len(s) % 2 else (s[len(s) // 2 - 1] + s[len(s) // 2]) / 2,
            "max": s[-1], "mean": sum(s) / len(s),
            "buckets": {k: sum(lo <= x <= (hi if hi is not None else math.inf) for x in s)
                        for k, (lo, hi) in {"<=4": (0, 4), "5-16": (5, 16), "17-64": (17, 64), ">=65": (65, None)}.items()}}


def _fmt(v, pct=False):
    if v is None:
        return "—"
    if isinstance(v, dict):
        return "NOT_EVALUABLE" if v.get("status") == "NOT_EVALUABLE" or "reason" in v else "—"
    if isinstance(v, (int, float)):
        return f"{v * 100:.3f}%" if pct else f"{v:,.4f}"
    return str(v)


def research_report(info: dict, diag: dict, metrics: dict, cost_rows: list[dict]) -> str:
    L = [f"# TSM SMA 5/20 — Research-only smoke `{info['run_id']}`", "",
         "> **RESEARCH_ONLY_EXPLORATORY.** One 4-week development window, one fixed parameter set. "
         "This is NOT evidence that SMA 5/20 works. No out-of-sample, cross-year or cross-regime robustness can be "
         "assessed from this run. Formal Market Eligibility remains **NO_GO**. Holdout: not accessed "
         "(BLINDNESS_UNVERIFIED applies to any later holdout use).", "",
         "## Identity", "", "| item | value |", "|---|---|"]
    for k in ("agent_git_sha", "core_git_sha", "snapshot_id", "manifest_sha256", "parquet_sha256", "lineage_sha256",
              "canonical_config_sha256", "session_config_sha256", "authorization_id",
              "execution_started_at_asia_taipei", "execution_finished_at_asia_taipei"):
        L.append(f"| {k} | `{info.get(k)}` |")
    for k, v in info["cost_profile_sha256"].items():
        L.append(f"| cost profile {k} | `{v}` |")
    d = diag["data"]
    L += ["", "## Data and timing", "",
          f"- 1m rows {d['native_1m_rows']}; conservative 15m bars {d['conservative_15m_bars']}; "
          f"signal-eligible {d['signal_eligible_bars']}; buy/sell execution-eligible "
          f"{d['buy_execution_eligible_bars']}/{d['sell_execution_eligible_bars']}",
          f"- bar-level execution rejection reasons: {d['execution_rejection_reasons']}",
          f"- last 1m timestamp {d['native_last_utc']} (before holdout start: {d['max_native_timestamp_before_holdout']})",
          f"- decisions in window {diag['decisions']['in_window']} (incl. warm-up {diag['decisions']['total_including_warmup']}); orders {diag['decisions']['orders']}",
          f"- CAT rounding: {diag['cat_rounding'].get('status')}", ""]
    L += ["| scenario | temporal | fills | min/max minutes available→fill | fills on/before signal bar |", "|---|---|---|---|---|"]
    for k, t in diag["temporal"].items():
        L.append(f"| {k} | {t['status']} | {t['fills_checked']} | {_fmt(t['min_minutes_signal_available_to_fill'])} / "
                 f"{_fmt(t['max_minutes_signal_available_to_fill'])} | {t['fills_on_or_before_signal_bar']} |")
    L += ["", "## Results (Core-computed; descriptive ratios are unthresholded)", "",
          "| scenario | trades | sample | total return | closed-trade net return | gross return | win rate | payoff | "
          "expectancy | profit factor | max DD | rejections |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for k, m in metrics.items():
        ds = m["descriptive_trade_stats"]
        L.append(f"| {k} | {ds['trade_count']} | {m['sample_status']} | {_fmt(m.get('total_return'), True)} | "
                 f"{_fmt(m.get('cost_adjusted_net_return'), True)} | {_fmt(m.get('gross_return_closed_trades'), True)} | "
                 f"{_fmt(ds['win_rate'], True)} | {_fmt(ds['payoff_ratio'])} | {_fmt(ds['expectancy'])} | "
                 f"{_fmt(ds['profit_factor'])} | {_fmt(m.get('max_drawdown'), True)} | {m.get('rejections')} |")
    L += ["", "## Costs (USD, Core ledger)", "", "| scenario | " + " | ".join(COST_COLUMNS) + " |",
          "|---|" + "---|" * len(COST_COLUMNS)]
    for r in cost_rows:
        L.append(f"| {r['scenario']} | " + " | ".join(str(r.get(c)) for c in COST_COLUMNS) + " |")
    first = next(iter(metrics.values()))
    L += ["", "## Holding period (Core 15m bars held, first scenario)", "",
          f"`{json.dumps(first['holding_period_bars_distribution'])}`", "",
          "## Interpretation limits", "",
          *[f"- Not claimed: {x}" for x in NOT_CLAIMED],
          "- Trades below the configured minimum are labelled INSUFFICIENT_SAMPLE; ratios are descriptive only.",
          "- Costs are RESEARCH_ONLY assumptions (IBKR Pro Fixed; TAF both sides uncapped; CAT assumed passed through).",
          ""]
    return "\n".join(L)
