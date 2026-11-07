"""Phase 4-C: read-only acceptance of a finished Phase 4-A research archive (archive contract of
``research_only/archive_contract.py``: top-level run_info.txt ... MANIFEST.json).

Existing Real Archive -> Archive Normalizer -> Research Evaluator -> Research Decision -> Immutable Evaluation Archive.

Never imports the runner, Core, the execution engine or any governance writer; never reads a snapshot, raw bars or
the holdout; never writes to the source archive, ``governance/`` or ``registry/``. Every value comes from an archived
file (byte-verified against an independently pinned MANIFEST.json SHA) or a committed governance record. Nothing is
reconstructed: trades are the archived Core round trips, never rebuilt from fills or from aggregate returns.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .universal import ROOT, absent, available, canonical, digest, evaluate, write_evaluation

VERSION = "1.0.0"
CONTRACT_FILES = ("run_info.txt", "config.json", "config_canonical.json", "dataset_manifest.json",
                  "execution_diagnostics.json", "decisions.csv", "trades.csv", "fills.csv", "rejected_orders.csv",
                  "cost_breakdown.csv", "metrics.json", "equity_curve.csv", "research_report.md")
SCENARIOS = ("BASE", "LOW", "STRESS", "CAT_STRESS")
PRIMARY = "BASE"
TOL = 1e-9                     # QUANT_EVALUATION_PROTOCOL §重現性: 指標差 < 1e-9 (same as universal._equal)
EXECUTOR_MODULES = ("strategy_agent.research_only.runner", "strategy_agent.research_only.development_run",
                    "strategy_agent.adapters.mrc_adapter", "strategy_agent.adapters.mrc_phase4a", "market_research_core")
OUTPUTS = ("evaluation.json", "evaluation_report.md", "risk_flags.json", "robustness_matrix.csv",
           "research_decision.json", "archive_contract_mapping.md", "acceptance_checks.json", "run_info.txt")


class ArchiveRejected(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


# ================================================================================== C1/C2: identity and integrity

def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def read_archive(path, *, expected_manifest_sha256: str) -> dict:
    """Verify MANIFEST.json against the independent pin, then every contract file against MANIFEST.json."""
    if not re.fullmatch("[0-9a-f]{64}", expected_manifest_sha256 or ""):
        raise ArchiveRejected("ARCHIVE_MANIFEST_PIN", "independently pinned 64-hex archive MANIFEST.json SHA required")
    root = Path(path)
    if root.is_symlink() or not root.is_dir():
        raise ArchiveRejected("ARCHIVE_PATH", f"not a real directory: {root}")
    root = root.resolve(strict=True)
    mp = root / "MANIFEST.json"
    if mp.is_symlink() or not mp.is_file():
        raise ArchiveRejected("ARCHIVE_MANIFEST", "MANIFEST.json missing or a symlink")
    raw = mp.read_bytes()
    if sha256_bytes(raw) != expected_manifest_sha256:
        raise ArchiveRejected("ARCHIVE_MANIFEST_SHA", f"MANIFEST.json sha {sha256_bytes(raw)} != pin {expected_manifest_sha256}")
    manifest = json.loads(raw)
    if manifest.get("status") != "COMPLETED":
        raise ArchiveRejected("ARCHIVE_STATUS", "archive must be COMPLETED")
    if manifest.get("run_id") != root.name:
        raise ArchiveRejected("ARCHIVE_RUN_ID", f"directory {root.name} != MANIFEST run_id {manifest.get('run_id')}")
    listed = manifest.get("files_sha256", {})
    if set(listed) != set(CONTRACT_FILES):
        raise ArchiveRejected("ARCHIVE_CONTRACT", f"listed files {sorted(listed)} != contract {sorted(CONTRACT_FILES)}")
    present = {p.name for p in root.iterdir()}
    extra = present - set(CONTRACT_FILES) - {"MANIFEST.json"}
    if extra:
        raise ArchiveRejected("ARCHIVE_UNLISTED_FILES", f"unlisted entries in archive: {sorted(extra)}")
    files = {}
    for name in CONTRACT_FILES:
        f = root / name
        if f.is_symlink() or not f.is_file() or f.resolve().parent != root:
            raise ArchiveRejected("UNSAFE_ARCHIVE_PATH", name)
        data = f.read_bytes()
        if sha256_bytes(data) != listed[name]:
            raise ArchiveRejected("ARCHIVE_ARTIFACT_SHA", name)
        files[name] = data
    return {"root": root, "manifest": manifest, "manifest_sha256": expected_manifest_sha256, "files": files,
            "fingerprint": {n: listed[n] for n in CONTRACT_FILES} | {"MANIFEST.json": expected_manifest_sha256}}


def fingerprint(root) -> dict:
    root = Path(root)
    return {p.name: sha256_bytes(p.read_bytes()) for p in sorted(root.iterdir()) if p.is_file()}


def _csv(data: bytes) -> list[dict]:
    return list(csv.DictReader(io.StringIO(data.decode()))) if data.strip() else []


def _run_info(data: bytes) -> dict:
    out = {}
    for line in data.decode().splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        try:
            out[k] = json.loads(v)
        except json.JSONDecodeError:
            out[k] = v
    return out


def parse(archive: dict) -> dict:
    f = archive["files"]
    return {"run_info": _run_info(f["run_info.txt"]), "config": json.loads(f["config.json"]),
            "config_canonical": json.loads(f["config_canonical.json"]),
            "dataset_manifest": json.loads(f["dataset_manifest.json"]),
            "diagnostics": json.loads(f["execution_diagnostics.json"]), "metrics": json.loads(f["metrics.json"]),
            "decisions": _csv(f["decisions.csv"]), "trades": _csv(f["trades.csv"]), "fills": _csv(f["fills.csv"]),
            "rejections": _csv(f["rejected_orders.csv"]), "costs": _csv(f["cost_breakdown.csv"]),
            "equity": _csv(f["equity_curve.csv"])}


# ============================================================================================ governance (read-only)

def load_governance(root: Path = ROOT) -> dict:
    def jl(p):
        p = Path(root) / p
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.is_file() else []
    readiness = Path(root) / "governance/market_readiness.json"
    return {"uses": jl("registry/research_authorization_uses.jsonl"),
            "authorizations": jl("governance/research_authorizations.jsonl"),
            "locks": jl("governance/holdout_locks.jsonl"),
            "readiness": json.loads(readiness.read_text(encoding="utf-8")) if readiness.is_file() else None}


def governance_fingerprint(root: Path = ROOT) -> dict:
    out = {}
    for d in ("governance", "registry"):
        for p in sorted((Path(root) / d).rglob("*")):
            if p.is_file():
                out[str(p.relative_to(root))] = sha256_bytes(p.read_bytes())
    return out


def _gates_sha(gates: dict) -> str:
    flat = {k: (v["status"] if isinstance(v, dict) else v) for k, v in gates.items()}
    return hashlib.sha256(json.dumps(flat, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# ================================================================================================ normalizer

def _f(v):
    return float(v)


def _ny(cfg):
    return cfg["windows"]


def envelope(parsed: dict, archive: dict, gov: dict, scenario: str) -> tuple[dict, list[dict]]:
    """Real archive -> RESEARCH_EVALUATOR_INPUT_SCHEMA envelope for one scenario, plus the mapping table."""
    ri, cfg, dm, diag, m = (parsed[k] for k in ("run_info", "config", "dataset_manifest", "diagnostics", "metrics"))
    mapping: list[dict] = []

    def note(field, source, how, status):
        mapping.append({"field": field, "source": source, "mapping": how, "status": status})

    prov = dm.get("loader_provenance", {})
    w = _ny(cfg)
    source = {"run_id": archive["manifest"]["run_id"], "archive_path": str(archive["root"]),
              "commit_sha": ri.get("agent_git_sha"), "core_sha": ri.get("core_git_sha"),
              "dataset_snapshot": ri.get("snapshot_id"), "manifest_sha256": ri.get("manifest_sha256"),
              "strategy_id": cfg["strategy"]["strategy_id"],
              "parameter_family": f"{cfg['strategy']['strategy_id']}@{cfg['strategy']['version']}:"
                                  + ",".join(f"{k}={v}" for k, v in sorted(cfg['strategy']['parameters'].items())),
              "timeframe": "15m",
              "sample_period": {"start": w["warmup_start"], "end": w["end"], "timezone": "America/New_York"},
              "currency": "USD"}
    note("source.run_id", "MANIFEST.json run_id (= directory name)", "Direct", "VERIFIED")
    note("source.commit_sha / core_sha", "run_info.txt agent_git_sha / core_git_sha", "Direct; cross-checked with "
         "MANIFEST.run_info and loader provenance core_git_sha", "VERIFIED")
    note("source.dataset_snapshot / manifest_sha256", "run_info.txt snapshot_id / manifest_sha256 (DATASET manifest)",
         "Direct; cross-checked with dataset_manifest.json loader_provenance", "VERIFIED")
    note("source.strategy_id / parameter_family", "config.json strategy", "Direct (fixed parameters, no search)", "VERIFIED")
    note("source.timeframe", "config.json temporal_policy + Core conservative 15m", "Constant '15m'", "VERIFIED")
    note("source.sample_period", "config.json windows warmup_start / end", "Core equity is observed from the warm-up "
         "start; trading window [start,end) is narrower and recorded separately", "VERIFIED")
    note("source.currency", "spec position_sizing (USD account)", "Constant 'USD'", "VERIFIED")

    initial = None
    eq = [r for r in parsed["equity"] if r["scenario"] == scenario]
    if eq:
        initial = _f(eq[0]["cash"]) if _f(eq[0]["equity"]) == _f(eq[0]["cash"]) else None
    note("initial_capital", "equity_curve.csv first Core observation (flat, equity == cash)",
         "Direct when the first observation is flat; else MISSING", "VERIFIED" if initial else "MISSING")

    doc = {"schema_version": "1.0.0", "kind": "RESEARCH_RESULT", "source": source, "equity_sampling": "IRREGULAR",
           "sample_role": "IN_SAMPLE"}
    if initial:
        doc["initial_capital"] = initial

    # holdout: governance lock + run attestation + observed data boundary
    lock = next((x for x in gov["locks"] if x.get("lock_id") == "HOLDOUT-TSM-15M-v1" and x.get("action") == "APPROVE"), None)
    auth = next((x for x in gov["authorizations"] if x.get("authorization_id") == ri.get("authorization_id")
                 and x.get("action") == "APPROVE"), None)
    h_ok = (lock is not None and auth is not None and ri.get("holdout_accessed") is False
            and diag["data"].get("max_native_timestamp_before_holdout") is True
            and auth["holdout"]["lock_id"] == lock["lock_id"])
    if h_ok:
        doc["holdout"] = available({"status": "NOT_ACCESSED", "lock_id": lock["lock_id"],
                                    "blindness": auth["holdout"].get("blindness", "BLINDNESS_UNVERIFIED")})
    else:
        doc["holdout"] = absent("holdout lock, authorization holdout pin or run attestation missing/inconsistent", "MISSING")
    note("holdout", "governance/holdout_locks.jsonl + research_authorizations.jsonl holdout + run_info "
         "holdout_accessed + execution_diagnostics max_native_timestamp_before_holdout",
         "NOT_ACCESSED only when all four agree; blindness copied from the authorization", "VERIFIED" if h_ok else "MISSING")

    trades = [r for r in parsed["trades"] if r["scenario"] == scenario]
    doc["trades"] = available([{"trade_id": f"{scenario}:{r['entry_ts']}:{r['exit_ts']}", "symbol": r["symbol"],
                                "entry_ts": r["entry_ts"], "exit_ts": r["exit_ts"], "net_pnl": _f(r["net_pnl"]),
                                "gross_pnl": _f(r["gross_pnl"]),
                                "status": "INVALIDATED" if r.get("invalidated") == "True" else "VALID"} for r in trades]
                              ) if trades or "trades.csv" in archive["files"] else absent("trades.csv absent", "MISSING")
    note("trades", "trades.csv (Core round_trips, per scenario)", "Direct; trade_id = scenario:entry_ts:exit_ts "
         "(content-derived, so duplicates collide); invalidated -> status", "VERIFIED")

    fills = [r for r in parsed["fills"] if r["scenario"] == scenario]
    doc["fills"] = available([{"fill_id": f"{r['order_id']}@{r['exec_ts']}", "symbol": r["symbol"], "exec_ts": r["exec_ts"],
                               "qty": _f(r["qty"]), "fill_price": _f(r["fill_price"]),
                               "signal_available_at": r["signal_available_at"]} for r in fills])
    note("fills", "fills.csv (Core fill ledger)", "Direct; fill_id = order_id@exec_ts; notional not exported by Core "
         "(turnover therefore NOT_AVAILABLE, not computed)", "VERIFIED")

    doc["equity_curve"] = available([{"timestamp": r["timestamp"], "equity": _f(r["equity"])} for r in eq]) if eq else \
        absent("no equity rows for scenario", "MISSING")
    note("equity_curve", "equity_curve.csv (Core equity, every 15m bar incl. warm-up)", "Direct", "VERIFIED" if eq else "MISSING")

    cost = next((r for r in parsed["costs"] if r["scenario"] == scenario), None)
    if cost:
        comps = {k: _f(cost[k]) for k in ("commission", "sec", "taf", "cat")}
        doc["cost_breakdown"] = available({"explicit_total": _f(cost["explicit_cost"]), "components": comps,
                                           "slippage_accounting": "EMBEDDED_IN_PRICES",
                                           "slippage_cost": _f(cost["price_adjustment_cost"])})
    else:
        doc["cost_breakdown"] = absent("no cost_breakdown row", "MISSING")
    note("cost_breakdown", "cost_breakdown.csv (Core tsm_cost_diagnostics totals)", "explicit_total = explicit_cost; "
         "components commission/SEC/TAF/CAT; slippage = price_adjustment_cost (embedded in fill prices, informational)",
         "VERIFIED" if cost else "MISSING")

    rej = [r for r in parsed["rejections"] if r["scenario"] == scenario]
    doc["rejections"] = available([{"code": r["reason"], "order_id": r["order_id"],
                                    **({"timestamp": r["bar_ts"]} if r["bar_ts"] not in ("", "NaT") else
                                       {"timestamp": r["expiry_ts"]} if r["expiry_ts"] not in ("", "NaT") else {})}
                                   for r in rej])
    note("rejections", "rejected_orders.csv (Core rejection rows)", "Direct, one row per Core rejection event (bar-level "
         "+ terminal); unique rejected orders counted separately", "VERIFIED")

    t = diag["temporal"].get(scenario, {})
    checks = m.get(scenario, {}).get("m3a4_checks", {})
    exec_ok = t.get("status") == "PASS" and checks and all(v == "PASS" for v in checks.values())
    doc["execution_diagnostics"] = available({
        "status": "PASS" if exec_ok else "FAIL", "orders_submitted": int(diag["decisions"]["orders"]),
        "exposure": float(m[scenario]["exposure"]),
        "fill_diagnostics": {"temporal": t, "m3a4_checks": checks}})
    note("execution_diagnostics", "execution_diagnostics.json temporal[scenario] + metrics.json m3a4_checks + decisions.orders",
         "PASS only if temporal PASS and every M3-A.4 check PASS; lookahead_checked NOT asserted (fill chronology is "
         "verified, signal computation is not independently re-verified)", "VERIFIED" if exec_ok else "FAIL")

    gates = (gov["readiness"] or {}).get("markets", {}).get(cfg["market"]["market_id"], {}).get("gates", {})
    dq = gates.get("DATA_QUALITY", {})
    dq_ok = dq.get("status") == "PASS" and prov.get("manifest_sha256") == ri.get("manifest_sha256") and \
        prov.get("snapshot_id") == ri.get("snapshot_id")
    doc["data_quality"] = available({"status": "PASS", "evidence_ref": "governance/market_readiness.json gates.DATA_QUALITY "
                                     f"({dq.get('evidence_ref')}); development snapshot lineage via Core loader provenance "
                                     f"{prov.get('integrity_method')}"}) if dq_ok else absent("no data-quality PASS evidence", "MISSING")
    note("data_quality", "market_readiness DATA_QUALITY (FINAL GATE parent) + dataset_manifest.json loader provenance",
         "PASS only when the formal DATA_QUALITY gate is PASS and loader provenance matches the dataset pins",
         "VERIFIED" if dq_ok else "MISSING")

    doc["metrics"] = available(m[scenario]) if scenario in m else absent("no metrics", "MISSING")
    note("metrics", "metrics.json[scenario] (M3-B evaluator over Core report)", "Direct; preserved as upstream", "VERIFIED")

    use = ri.get("authorization_use") or {}
    uses = [u for u in gov["uses"] if u.get("authorization_id") == ri.get("authorization_id")]
    b_ok = len(uses) == 1 and uses[0].get("record_sha256") == use.get("record_sha256") and \
        uses[0].get("run_id") == source["run_id"]
    doc["budget_consumption"] = available({"authorization_id": ri["authorization_id"], "batches_consumed": 1}) if b_ok \
        else absent("signed use record missing or inconsistent with the run", "MISSING")
    note("budget_consumption", "registry/research_authorization_uses.jsonl (signed, pushed d456fed) vs run_info "
         "authorization_use", "Copy of the SOURCE run's single consumption; evaluation consumes 0",
         "VERIFIED" if b_ok else "MISSING")

    base = next((r for r in parsed["costs"] if r["scenario"] == PRIMARY), None)
    scen = []
    for r in parsed["costs"]:
        net = math.fsum(_f(x["net_pnl"]) for x in parsed["trades"] if x["scenario"] == r["scenario"])
        scen.append({"scenario_id": r["scenario"], "source_run_id": source["run_id"], "core_sha": source["core_sha"],
                     "dataset_snapshot": source["dataset_snapshot"], "sample_period": source["sample_period"],
                     "strategy_id": source["strategy_id"], "parameter_family": source["parameter_family"],
                     "timeframe": "15m", "currency": "USD",
                     "cost_multiplier": _f(r["total_cost"]) / _f(base["total_cost"]) if base else 1.0,
                     "net_pnl": net, "comparison_basis": "SAME_SIGNALS_AND_POSITIONS",
                     "evidence_ref": f"{source['run_id']}/cost_breakdown.csv#{r['scenario']} + trades.csv"})
    doc["cost_scenarios"] = available(scen)
    note("cost_scenarios", "cost_breakdown.csv + trades.csv (same run, same decisions/orders for all scenarios)",
         "cost_multiplier = observed total cost / BASE total cost (factual ratio; no scenario is declared '2x')", "VERIFIED")
    return doc, mapping


# =============================================================================================== C3–C8 checks

def _close(a, b) -> bool:
    return math.isclose(float(a), float(b), rel_tol=TOL, abs_tol=TOL)


def trade_integrity(parsed: dict) -> dict:
    out = {}
    orders = int(parsed["diagnostics"]["decisions"]["orders"])
    for s in SCENARIOS:
        tr = [r for r in parsed["trades"] if r["scenario"] == s]
        fl = [r for r in parsed["fills"] if r["scenario"] == s]
        rj = [r for r in parsed["rejections"] if r["scenario"] == s]
        problems = []
        keys = [(r["entry_ts"], r["exit_ts"]) for r in tr]
        if len(set(keys)) != len(keys):
            problems.append("DUPLICATE_TRADE")
        fid = [(r["order_id"], r["exec_ts"]) for r in fl]
        if len(set(fid)) != len(fid):
            problems.append("DUPLICATE_FILL")
        if len(set(r["order_id"] for r in fl)) != len(fl):
            problems.append("ORDER_FILLED_TWICE")
        buys = {(r["exec_ts"]): r for r in fl if r["side"] == "BUY"}
        sells = {(r["exec_ts"]): r for r in fl if r["side"] == "SELL"}
        matched = 0
        for r in tr:
            b, z = buys.get(r["entry_ts"]), sells.get(r["exit_ts"])
            if not b or not z:
                problems.append(f"TRADE_WITHOUT_FILL:{r['entry_ts']}")
                continue
            if not (_close(b["fill_price"], r["entry_price"]) and _close(z["fill_price"], r["exit_price"])
                    and _close(b["qty"], r["qty"]) and _close(z["qty"], r["qty"])):
                problems.append(f"TRADE_FILL_MISMATCH:{r['entry_ts']}")
            if not _close((float(r["exit_price"]) - float(r["entry_price"])) * float(r["qty"]), r["gross_pnl"]):
                problems.append(f"TRADE_GROSS_PNL:{r['entry_ts']}")
            if not datetime.fromisoformat(r["entry_ts"]) < datetime.fromisoformat(r["exit_ts"]):
                problems.append(f"TRADE_CHRONOLOGY:{r['entry_ts']}")
            matched += 1
        if len(fl) != 2 * len(tr):
            problems.append(f"FILL_COUNT:{len(fl)} != 2 x trades {len(tr)} (open position or orphan fill)")
        allocated = math.fsum(float(r["gross_pnl"]) - float(r["net_pnl"]) for r in tr)
        ledger = math.fsum(float(r["explicit_cost"]) for r in fl)
        if not _close(allocated, ledger):
            problems.append(f"COST_ALLOCATION:{allocated} != fill ledger {ledger}")
        m = parsed["metrics"].get(s, {})
        if not _close(math.fsum(float(r["net_pnl"]) for r in tr), m.get("trades", {}).get("net_pnl", float("nan"))):
            problems.append("NET_PNL_VS_METRICS")
        if m.get("fills") != len(fl) or m.get("rejections") != len(rj):
            problems.append("COUNTS_VS_METRICS")
        rejected_orders = sorted({r["order_id"] for r in rj})
        terminal = sorted({r["order_id"] for r in rj if r["reason"] in ("ORDER_EXPIRED",)})
        filled = {r["order_id"] for r in fl}
        if set(rejected_orders) & filled:
            problems.append("ORDER_FILLED_AND_REJECTED")
        out[s] = {"trades": len(tr), "trades_matched_to_entry_and_exit_fills": matched, "fills": len(fl),
                  "rejection_rows": len(rj), "rejected_orders": rejected_orders, "terminal_rejections": terminal,
                  "orders_submitted": orders, "orders_filled": len(filled),
                  "orders_without_fill_or_rejection_record": orders - len(filled) - len(rejected_orders),
                  "allocated_trade_costs": allocated, "fill_ledger_explicit_costs": ledger,
                  "status": "PASS" if not problems else "FAIL", "problems": problems}
    return out


def performance_consistency(parsed: dict, evaluations: dict) -> dict:
    out = {}
    pairs = {"net_return": ("cost_adjusted_net_return",), "total_return": ("total_return",),
             "max_drawdown": ("max_drawdown",), "win_rate": ("trades", "win_rate"),
             "payoff_ratio": ("trades", "payoff_ratio"), "expectancy": ("trades", "expectancy"),
             "profit_factor": ("trades", "profit_factor"), "net_pnl": ("trades", "net_pnl"),
             "trade_count": ("trades", "trade_count")}
    for s, ev in evaluations.items():
        m = parsed["metrics"][s]
        rows = {}
        for k, path in pairs.items():
            up = m
            for p in path:
                up = up.get(p) if isinstance(up, dict) else None
            mine = ev["metrics"].get(k, {})
            val = mine.get("value") if mine.get("status") == "AVAILABLE" else None
            rows[k] = {"core_upstream": up, "evaluator": val, "verification": mine.get("verification"),
                       "match": up is not None and val is not None and _close(up, val)}
        cost = next(r for r in parsed["costs"] if r["scenario"] == s)
        rows["total_cost"] = {"core_upstream": cost["total_cost"],
                              "evaluator": None, "match": _close(float(cost["explicit_cost"]) +
                                                                 float(cost["price_adjustment_cost"]), cost["total_cost"]),
                              "note": "cost ledger identity explicit_cost + price_adjustment_cost = total_cost (Core)"}
        rows["explicit_cost_vs_metrics"] = {"core_upstream": m["costs"]["total"], "evaluator": cost["explicit_cost"],
                                            "match": _close(m["costs"]["total"], cost["explicit_cost"])}
        out[s] = {"engineering_status": ev["engineering_status"], "rows": rows,
                  "status": "PASS" if ev["engineering_status"] == "PASS" and all(r["match"] for r in rows.values())
                  else "FAIL"}
    return out


def temporal_safety(parsed: dict, gov: dict) -> dict:
    d = parsed["diagnostics"]
    ri = parsed["run_info"]
    per = {}
    for s in SCENARIOS:
        t = d["temporal"][s]
        fl = [r for r in parsed["fills"] if r["scenario"] == s]
        strictly_after = all(datetime.fromisoformat(r["exec_ts"]) > datetime.fromisoformat(r["signal_available_at"])
                             for r in fl)
        later_bar = all(datetime.fromisoformat(r["source_timestamp"]) > datetime.fromisoformat(r["signal_source_timestamp"])
                        for r in fl)
        next_bar = all(r["execution_assumption"] == "NEXT_ELIGIBLE_BAR_OPEN" for r in fl)
        per[s] = {"diagnostic_status": t["status"], "fills_strictly_after_signal_available_at": strictly_after,
                  "fills_on_later_bar_than_signal": later_bar, "next_eligible_bar_open": next_bar,
                  "order_time_equals_signal_time": t["order_time_equals_signal_time"],
                  "m3a4_T01_no_lookahead": parsed["metrics"][s]["m3a4_checks"].get("T01_no_lookahead"),
                  "status": "PASS" if t["status"] == "PASS" and strictly_after and later_bar and next_bar
                  and parsed["metrics"][s]["m3a4_checks"].get("T01_no_lookahead") == "PASS" else "FAIL"}
    lock = next((x for x in gov["locks"] if x.get("lock_id") == "HOLDOUT-TSM-15M-v1"), None)
    hold_start = datetime.fromisoformat(lock["holdout_start"]) if lock else None
    last = datetime.fromisoformat(d["data"]["native_last_utc"])
    no_holdout = bool(lock) and ri.get("holdout_accessed") is False and hold_start is not None and last < hold_start \
        and d["data"]["max_native_timestamp_before_holdout"] is True
    return {"scenarios": per, "holdout": {"lock_id": lock and lock["lock_id"], "holdout_start": lock and lock["holdout_start"],
                                          "last_native_timestamp": d["data"]["native_last_utc"], "not_accessed": no_holdout,
                                          "evaluator_read_holdout": False},
            "limitation": "signal computation look-ahead is guaranteed structurally (pure SMA over Core modeled_available_at) "
                          "and checked at the fill level; it is not independently re-verified here",
            "status": "PASS" if no_holdout and all(v["status"] == "PASS" for v in per.values()) else "FAIL"}


def governance_isolation(gov: dict, parsed: dict, before: dict, after: dict) -> dict:
    ri = parsed["run_info"]
    aid = ri["authorization_id"]
    uses = [u for u in gov["uses"] if u.get("authorization_id") == aid]
    auth = next((x for x in gov["authorizations"] if x.get("authorization_id") == aid and x.get("action") == "APPROVE"), {})
    gates = (gov["readiness"] or {}).get("markets", {}).get(parsed["config"]["market"]["market_id"], {})
    pin = auth.get("formal_gate_pin", {})
    executors = [m for m in EXECUTOR_MODULES if m in sys.modules]
    checks = {"authorization_uses": f"{len(uses)}/{auth.get('max_batches')}",
              "authorization_spent": len(uses) == 1 and auth.get("max_batches") == 1,
              "use_record_matches_run": bool(uses) and uses[0]["run_id"] == parsed["run_info"]["run_id"],
              "governance_and_registry_unchanged": before == after,
              "formal_market_status": gates.get("status"),
              "formal_gates_match_authorization_pin": bool(pin) and _gates_sha(gates.get("gates", {})) == pin.get("gates_sha256")
              and gates.get("status") == pin.get("market_status"),
              "holdout_lock_present_unchanged": any(x.get("lock_id") == "HOLDOUT-TSM-15M-v1" for x in gov["locks"]),
              "executor_modules_loaded": executors, "new_backtest_executed": False, "broker_api_called": False,
              "evaluation_batch_consumption": 0}
    ok = (checks["authorization_spent"] and checks["use_record_matches_run"] and checks["governance_and_registry_unchanged"]
          and checks["formal_market_status"] == "NO_GO" and checks["formal_gates_match_authorization_pin"]
          and checks["holdout_lock_present_unchanged"] and not executors)
    return {**checks, "status": "PASS" if ok else "FAIL"}


# ===================================================================================================== driver

def accept(archive_path, *, expected_manifest_sha256: str, evaluator_commit_sha: str, evaluated_at: str,
           governance_root: Path = ROOT) -> dict:
    """Pure (no writes). Returns every check, the primary evaluation and per-scenario evaluations."""
    gov_before = governance_fingerprint(governance_root)
    src_before = fingerprint(archive_path)
    checks: dict = {}
    try:
        archive = read_archive(archive_path, expected_manifest_sha256=expected_manifest_sha256)
    except ArchiveRejected as e:
        return {"status": "BLOCKED", "blocked_at": "C2", "error": {"code": e.code, "message": str(e)},
                "checks": {"C2_archive_integrity": {"status": "FAIL", "error": e.code}}, "source_fingerprint": src_before}
    parsed = parse(archive)
    gov = load_governance(governance_root)
    ri = parsed["run_info"]
    prov = parsed["dataset_manifest"]["loader_provenance"]
    ident = {"run_id": archive["manifest"]["run_id"], "agent_sha": ri.get("agent_git_sha"), "core_sha": ri.get("core_git_sha"),
             "canonical_config_sha256": ri.get("canonical_config_sha256"),
             "session_config_sha256": ri.get("session_config_sha256"), "snapshot_id": ri.get("snapshot_id"),
             "dataset_manifest_sha256": ri.get("manifest_sha256"), "archive_manifest_sha256": expected_manifest_sha256,
             "authorization_id": ri.get("authorization_id")}
    agree = {"run_id_equals_run_info": ri.get("run_id") == ident["run_id"],
             "manifest_run_info_equals_run_info_txt": all(archive["manifest"]["run_info"].get(k) == ri.get(k) for k in
                                                          ("agent_git_sha", "core_git_sha", "snapshot_id", "manifest_sha256",
                                                           "session_config_sha256", "authorization_id")),
             "loader_provenance_matches": prov.get("snapshot_id") == ri.get("snapshot_id") and
             prov.get("manifest_sha256") == ri.get("manifest_sha256") and prov.get("core_git_sha") == ri.get("core_git_sha"),
             "config_json_sha_matches": parsed["config"]["provenance"]["config_sha256"] == ri.get("session_config_sha256"),
             "canonical_config_sha_matches": parsed["config_canonical"]["provenance"]["config_sha256"] == ri.get("canonical_config_sha256"),
             "config_authorization_matches": parsed["config"]["authorization_id"] == ri.get("authorization_id"),
             "dataset_manifest_is_not_archive_manifest": ri.get("manifest_sha256") != expected_manifest_sha256,
             "full_shas": all(re.fullmatch("[0-9a-f]{40}", ident[k] or "") for k in ("agent_sha", "core_sha")) and
             all(re.fullmatch("[0-9a-f]{64}", ident[k] or "") for k in ("canonical_config_sha256", "session_config_sha256",
                                                                       "dataset_manifest_sha256"))}
    checks["C1_archive_identity"] = {**ident, "agreement": agree, "status": "PASS" if all(agree.values()) else "FAIL"}
    checks["C2_archive_integrity"] = {"manifest_sha256_pinned": expected_manifest_sha256, "files_verified": len(archive["files"]),
                                      "contract_files_exact": True, "unlisted_entries": [], "symlinks": [],
                                      "status": "PASS"}
    checks["C3_trade_integrity"] = {"scenarios": trade_integrity(parsed)}
    checks["C3_trade_integrity"]["status"] = "PASS" if all(v["status"] == "PASS" for v in
                                                           checks["C3_trade_integrity"]["scenarios"].values()) else "FAIL"
    evaluations, mappings = {}, {}
    for s in SCENARIOS:
        doc, mp = envelope(parsed, archive, gov, s)
        evaluations[s] = evaluate(doc, evaluator_commit_sha=evaluator_commit_sha, evaluated_at=evaluated_at)
        mappings[s] = mp
    checks["C4_performance_consistency"] = {"tolerance": f"rel/abs {TOL} (QUANT_EVALUATION_PROTOCOL 重現性 < 1e-9)",
                                            "scenarios": performance_consistency(parsed, evaluations)}
    checks["C4_performance_consistency"]["status"] = "PASS" if all(
        v["status"] == "PASS" for v in checks["C4_performance_consistency"]["scenarios"].values()) else "FAIL"
    checks["C5_temporal_safety"] = temporal_safety(parsed, gov)
    primary = evaluations[PRIMARY]
    codes = {f["code"] for f in primary["risk_flags"]}
    want = {"INSUFFICIENT_SAMPLE": "INSUFFICIENT_SAMPLE", "concentrated_profits": "NET_PROFIT_COVERED_BY_TOP_TRADES",
            "missing_oos": "IN_SAMPLE_VS_OUT_OF_SAMPLE_NOT_TESTED", "BLINDNESS_UNVERIFIED": "BLINDNESS_UNVERIFIED",
            "limited_regime_coverage": "MARKET_REGIME_COVERAGE_NOT_ESTABLISHED"}
    cost_row = next((r for r in primary["robustness_matrix"] if r.get("group") == "cost_sensitivity"), {})
    pnl = [float(r["net_pnl"]) for r in parsed["trades"] if r["scenario"] == PRIMARY]
    if not (sum(pnl) > 0 and any(x < 0 for x in pnl)):   # concentration fact is only defined for positive net with losers
        want.pop("concentrated_profits")
    c6 = {k: v in codes for k, v in want.items()} | {"cost_sensitivity_observed": cost_row.get("status") == "AVAILABLE",
                                                     "less_than_one_year": "LESS_THAN_ONE_YEAR_OBSERVED" in codes,
                                                     "verdict_not_keep": primary["strategy_verdict"] != "KEEP",
                                                     "verdict": primary["strategy_verdict"],
                                                     "evidence_sufficiency": primary["evidence_sufficiency"]}
    checks["C6_research_evaluation"] = {**c6, "status": "PASS" if all(
        v for k, v in c6.items() if k not in ("verdict", "evidence_sufficiency")) and primary["strategy_verdict"] == "RETEST"
        and primary["evidence_sufficiency"] == "UNDETERMINED" else "FAIL"}
    doc, _ = envelope(parsed, archive, gov, PRIMARY)
    again = evaluate(doc, evaluator_commit_sha=evaluator_commit_sha, evaluated_at=evaluated_at)
    other = evaluate(doc, evaluator_commit_sha=evaluator_commit_sha, evaluated_at="2000-01-01T00:00:00+08:00")
    strip = lambda r: {k: v for k, v in r.items() if k != "metadata"}  # noqa: E731
    checks["C7_reproducibility"] = {"input_sha256": primary["input_sha256"], "payload_sha256": digest(primary),
                                    "same_input_same_time_identical": canonical(again) == canonical(primary),
                                    "different_time_only_metadata_differs": canonical(strip(other)) == canonical(strip(primary)),
                                    "non_deterministic_metadata": ["metadata.evaluation_timestamp", "metadata.source_archive_path"]}
    checks["C7_reproducibility"]["status"] = "PASS" if checks["C7_reproducibility"]["same_input_same_time_identical"] and \
        checks["C7_reproducibility"]["different_time_only_metadata_differs"] else "FAIL"
    src_after = fingerprint(archive_path)
    checks["C2_archive_integrity"]["source_unchanged_during_evaluation"] = src_before == src_after
    if src_before != src_after:
        checks["C2_archive_integrity"]["status"] = "FAIL"
    checks["C8_governance_isolation"] = governance_isolation(gov, parsed, gov_before, governance_fingerprint(governance_root))
    status = "PASS" if all(c["status"] == "PASS" for c in checks.values()) else "FAIL"
    return {"status": status, "checks": checks, "evaluation": primary, "evaluations": evaluations,
            "mapping": mappings[PRIMARY], "identity": ident, "source_fingerprint": src_after,
            "archive_root": str(archive["root"])}


def _mapping_md(acc: dict) -> str:
    L = ["# Archive contract mapping — Phase 4-A real archive → Phase 4-B evaluator input", "",
         f"Source run `{acc['identity']['run_id']}`; archive MANIFEST.json `{acc['identity']['archive_manifest_sha256']}` "
         f"(independent pin); dataset manifest `{acc['identity']['dataset_manifest_sha256']}` (a different file).", "",
         "| Evaluator field | Actual archive source | Mapping | Evidence status |", "|---|---|---|---|"]
    for r in acc["mapping"]:
        L.append(f"| {r['field']} | {r['source']} | {r['mapping']} | {r['status']} |")
    L += ["", "Not reconstructed: trades are Core round trips from trades.csv; no trade, PnL, notional or cost is derived "
          "from fills or aggregates. Turnover and rejection rate stay NOT_AVAILABLE (no notional export; rejection rows "
          "repeat an order id). Order outcomes: see acceptance_checks.json C3 `orders_without_fill_or_rejection_record`.", ""]
    return "\n".join(L)


def write_acceptance(acc: dict, out_dir, *, evaluator_commit_sha: str, evaluated_at: str, context: dict | None = None) -> dict:
    """Write the eight-file evaluation archive (O_EXCL). Refuses existing directories and governed/source paths."""
    out = Path(out_dir)
    if acc.get("evaluation") is None:          # blocked before evaluation: failure diagnostic only
        out.mkdir(parents=True, exist_ok=False)
        files = {}
        for name, payload in (("acceptance_checks.json", acc), ("run_info.txt", None)):
            data = (json.dumps(acc, indent=2, sort_keys=True, default=str) + "\n").encode() if payload is not None else \
                _info(acc, evaluator_commit_sha, evaluated_at, context).encode()
            with open(out / name, "xb") as fh:
                fh.write(data)
            files[name] = sha256_bytes(data)
        return files
    write_evaluation(acc["evaluation"], out)   # five evaluator files (refuses existing dir, source and governed paths)
    extra = {"archive_contract_mapping.md": _mapping_md(acc).encode(),
             "acceptance_checks.json": (json.dumps({k: v for k, v in acc.items() if k not in ("evaluation", "evaluations")}
                                                   | {"per_scenario": {s: {"engineering_status": e["engineering_status"],
                                                                           "strategy_verdict": e["strategy_verdict"],
                                                                           "evidence_sufficiency": e["evidence_sufficiency"],
                                                                           "input_sha256": e["input_sha256"],
                                                                           "metrics": e["metrics"],
                                                                           "risk_flags": [f["code"] for f in e["risk_flags"]]}
                                                                       for s, e in acc["evaluations"].items()}},
                                                   indent=2, sort_keys=True, default=str, allow_nan=False) + "\n").encode(),
             "run_info.txt": _info(acc, evaluator_commit_sha, evaluated_at, context).encode()}
    for name, data in extra.items():
        with open(out / name, "xb") as fh:
            fh.write(data)
    return {p.name: sha256_bytes(p.read_bytes()) for p in sorted(out.iterdir())}


def _info(acc, sha, at, context) -> str:
    i = acc.get("identity", {})
    ev = acc.get("evaluation") or {}
    lines = {"kind": "PHASE4C_REAL_EVALUATION_ACCEPTANCE", "phase4c_version": VERSION, "acceptance_status": acc["status"],
             "source_run_id": i.get("run_id"), "source_archive_path": acc.get("archive_root"),
             "source_archive_manifest_sha256": i.get("archive_manifest_sha256"),
             "source_dataset_manifest_sha256": i.get("dataset_manifest_sha256"),
             "source_agent_commit": i.get("agent_sha"), "source_core_commit": i.get("core_sha"),
             "source_snapshot_id": i.get("snapshot_id"), "source_authorization_id": i.get("authorization_id"),
             "evaluator_commit": sha, "evaluation_timestamp": at, "schema_version": "1.0.0",
             "engineering_status": ev.get("engineering_status"), "strategy_verdict": ev.get("strategy_verdict"),
             "evidence_sufficiency": ev.get("evidence_sufficiency"), "evaluation_batch_consumption": 0,
             "new_backtest_executed": False, "holdout_read": False,
             "limitations": "RESEARCH_ONLY development sample; 13 trades < 30; no OOS; BLINDNESS_UNVERIFIED; "
                            "Formal Market NO_GO; not evidence the strategy works"} | (context or {})
    return "".join(f"{k}={v if isinstance(v, str) else json.dumps(v, default=str)}\n" for k, v in lines.items())


def evaluation_dir_name(evaluated_at: str, evaluator_commit_sha: str) -> str:
    t = datetime.fromisoformat(evaluated_at).astimezone(ZoneInfo("Asia/Taipei"))
    return f"{t:%Y%m%d_%H%M%S}_{evaluator_commit_sha[:7]}"


def evaluator_identity(root: Path = ROOT) -> tuple[str, bool]:
    import subprocess  # noqa: PLC0415
    sha = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    dirty = bool(subprocess.check_output(["git", "-C", str(root), "status", "--porcelain"], text=True).strip())
    return sha, dirty


def run(archive_path, *, expected_manifest_sha256: str, output_root, expected_evaluator_sha: str | None = None,
        evaluated_at: str | None = None, governance_root: Path = ROOT, context: dict | None = None) -> dict:
    """Clean pinned evaluator checkout -> accept -> new immutable evaluation directory (also on FAIL/BLOCKED)."""
    sha, dirty = evaluator_identity(governance_root)
    if dirty or (expected_evaluator_sha and sha != expected_evaluator_sha):
        raise RuntimeError(f"evaluator must be a clean checkout of {expected_evaluator_sha or 'HEAD'} (got {sha}, dirty={dirty})")
    at = evaluated_at or datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds")
    out_root = Path(output_root).resolve()
    src = Path(archive_path).resolve()
    if out_root == src or src in out_root.parents:
        raise ValueError("evaluation output must not be inside the source archive")
    acc = accept(src, expected_manifest_sha256=expected_manifest_sha256, evaluator_commit_sha=sha, evaluated_at=at,
                 governance_root=governance_root)
    out = out_root / evaluation_dir_name(at, sha)
    files = write_acceptance(acc, out, evaluator_commit_sha=sha, evaluated_at=at, context=context)
    if fingerprint(src) != acc["source_fingerprint"]:
        raise RuntimeError("SOURCE_ARCHIVE_CHANGED during evaluation")
    return {"status": acc["status"], "output_dir": str(out), "files_sha256": files, "evaluator_commit": sha,
            "evaluated_at": at, "checks": {k: v["status"] for k, v in acc["checks"].items()},
            "engineering_status": (acc.get("evaluation") or {}).get("engineering_status"),
            "strategy_verdict": (acc.get("evaluation") or {}).get("strategy_verdict"),
            "evidence_sufficiency": (acc.get("evaluation") or {}).get("evidence_sufficiency")}
