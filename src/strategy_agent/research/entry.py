"""The one entry point for configuration-driven research runs (M3-B.2).

CLI ``run``, the Colab notebook, batch, resume and any future automation call ``run_config``. There is no other
way to start a research run from a configuration, and none of these surfaces accepts parameters or governance
switches: parameters come only from the reviewed config, the R02 check is mandatory in ``ResearchRunner``, and
an edited config must carry a new sha256 and (for the same config_id) a new version.

Flow: load + validate config (RC01-RC12)  ->  config ledger (same id+version must keep the same sha256)
      ->  plan jobs (windows x cost scenarios, <= MAX_BATCH_RUNS)  ->  run_batch (progress, failures, resume)
      ->  cost-sensitivity summary written next to the batch ledger.
Config-level refusals (anything but RC04) never reach the registry; they are written as a refusal record next
to the batch ledger and raised. RC04 (R02) refusals ARE submitted so the registry records REJECTED_SPEC.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from strategy_agent.adapters import mrc_adapter as A

from .archive import DRIVE_ROOT, Archiver
from .batch import MAX_BATCH_RUNS, BatchLimitExceeded, run_batch
from .config import ResearchConfig, ResearchConfigError, load_raw, validate_config
from .cost_scenarios import require_executable

TZ = ZoneInfo("Asia/Taipei")


class ConfigLedgerConflict(ResearchConfigError):
    pass


def _ledger_check(run_root: Path, cfg: ResearchConfig) -> None:
    """Append-only ledger of configs seen in this environment: (config_id, version) is bound to one sha256."""
    p = Path(run_root) / "research_configs.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    seen = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []
    key = (cfg.data["config_id"], cfg.data["version"])
    for r in seen:
        if (r["config_id"], r["version"]) == key and r["sha256"] != cfg.sha256:
            raise ConfigLedgerConflict([("RC02", f"{key[0]}@{key[1]} was already run with sha256 {r['sha256'][:12]}; "
                                                 "changed content (e.g. parameters) needs a new version")])
    if not any((r["config_id"], r["version"], r["sha256"]) == (*key, cfg.sha256) for r in seen):
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"config_id": key[0], "version": key[1], "sha256": cfg.sha256,
                                "first_seen": datetime.now(TZ).isoformat()}, sort_keys=True) + "\n")


def _refusal(dir_: Path, config_path: Path, err: Exception) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    p = dir_ / f"refused_{datetime.now(TZ):%Y%m%d_%H%M%S}_{os.getpid()}.json"
    p.write_text(json.dumps({"config": str(config_path), "refused_at": datetime.now(TZ).isoformat(),
                             "issues": getattr(err, "issues", [[type(err).__name__, str(err)]])}, indent=1),
                 encoding="utf-8")
    return p


def build_synthetic(config_path: Path, *, run_root: Path, archive_root: Path | None, agent=None, core=None,
                    engine=None, progress=print, clock=None, archive_clock=None):
    """Validate a SYNTHETIC config and return (runner, config, jobs). Real-market configs are refused here."""
    from . import fixtures as FX  # noqa: PLC0415
    from .data import SyntheticSource  # noqa: PLC0415
    from .environment import SYNTH_HOLDOUT_START, SYNTH_LOCK_ID, synthetic_environment, synthetic_spec  # noqa: PLC0415
    from .pipeline import ResearchJob, ResearchRunner  # noqa: PLC0415

    raw = load_raw(config_path)
    if raw.get("kind") != "SYNTHETIC":
        raise ResearchConfigError([("RC12", "only SYNTHETIC configs are executable in M3-B; REAL_MARKET waits for "
                                            "the market gate (python -m strategy_agent.research readiness)")])
    src = SyntheticSource()
    env = synthetic_environment(Path(run_root), dataset_id=src.dataset_id, clock=clock)
    cfg = validate_config(raw, path=Path(config_path).resolve(), profiles=env.cost_profiles,
                          fixture_dataset=src.describe(), synthetic_holdout=(SYNTH_LOCK_ID, SYNTH_HOLDOUT_START),
                          core_sha=A.core_identity().get("git_sha"))
    if cfg.data["output"]["target"] == "DRIVE" and archive_root is not None and Path(archive_root) != DRIVE_ROOT:
        raise ResearchConfigError([("RC11", "config archives to Drive; --archive-root is only for LOCAL configs")])
    if cfg.data["output"]["target"] == "LOCAL" and archive_root is None:
        raise ResearchConfigError([("RC11", "LOCAL config needs --archive-root")])
    _ledger_check(Path(run_root), cfg)
    sem = A.MarketSemantics(market="IBKR_EQUITY", bar_label=src.fixture.meta["bar_label"],
                            session_semantics="CONFIRMED", timezone=FX.TZ,
                            initial_cash=float(cfg.data["capital"]["initial_capital"]), constant_tick_size=0.01)
    root = DRIVE_ROOT if cfg.data["output"]["target"] == "DRIVE" else Path(archive_root)
    arch = Archiver(root, require_drive=cfg.data["output"]["target"] == "DRIVE",
                    local_fallback=Path(run_root) / "_local_archive", clock=archive_clock)
    kw = {"clock": clock} if clock else {}
    runner = ResearchRunner(env, src, sem, core=core, engine=engine, agent=agent, archiver=arch, progress=progress, **kw)
    w = cfg.data["windows"]
    jobs = []
    for sc in cfg.scenarios:
        require_executable(sc)                                   # CS06: never run an inexpressible cost profile
        spec = synthetic_spec(cfg.base_spec, src.describe(), sc.profile)
        for role, win in (("TRAIN", w["in_sample"]), ("VALIDATION", w["validation"])):
            jobs.append(ResearchJob(spec=spec, parameters=dict(cfg.data["parameters"]), window_start=win["start"],
                                    window_end=win["end"], warmup_start=w["warmup_start"], role=role,
                                    split_ref=spec["experiment"]["split_ref"], fold_id=f"{role}-01",
                                    cost_multiplier=sc.cost_multiplier, seed=spec["experiment"]["seed"],
                                    label=f"{cfg.data['config_id']}_{role.lower()}_{sc.scenario_id.lower()}",
                                    config=cfg.ref, scenario=sc.record))
    if len(jobs) > MAX_BATCH_RUNS:
        raise BatchLimitExceeded(f"config plans {len(jobs)} runs > {MAX_BATCH_RUNS}")
    return runner, cfg, jobs


def cost_sensitivity_summary(outcomes, jobs) -> dict:
    """Program-generated table of the archived metrics per window x scenario (no number typed by hand)."""
    rows = []
    for o, j in zip(outcomes, jobs):
        m = None
        if o.archive_path and (Path(o.archive_path) / "metrics.json").exists():
            m = json.loads((Path(o.archive_path) / "metrics.json").read_text(encoding="utf-8"))["metrics"]
        t = (m or {}).get("trades", {})
        rows.append({"role": j.role, "scenario_id": j.scenario["scenario_id"], "kind": j.scenario["kind"],
                     "cost_multiplier": j.cost_multiplier, "status": o.status, "experiment_id": o.experiment_id,
                     "trade_count": t.get("trade_count"), "net_pnl": t.get("net_pnl"),
                     "profit_factor": t.get("profit_factor"), "expectancy": t.get("expectancy"),
                     "total_return": (m or {}).get("total_return"), "metrics_sha256": o.metrics_sha256,
                     "archive_path": o.archive_path})
    return {"sensitivity_only": True, "counts_as_new_family_or_trial": False, "rows": rows}


def sensitivity_markdown(summ: dict) -> str:
    c = summ["config"]
    L = [f"# Cost sensitivity — {c['config_id']}@{c['version']}", "",
         f"config sha256 `{c['sha256']}`; scenario set `{c['scenario_set']['scenario_set_id']}@"
         f"{c['scenario_set']['version']}` sha256 `{c['scenario_set']['sha256']}`", "",
         "Sensitivity of ONE strategy configuration (same parameters, one family trial). Costs computed by Core; "
         "numbers below are copied from each run's archived metrics.json.", "",
         "| role | scenario | kind | Core multiplier | status | trades | net PnL | PF | expectancy | total return |",
         "|---|---|---|---|---|---|---|---|---|---|"]

    def f(x):
        return "NOT_EVALUABLE" if isinstance(x, dict) else "—" if x is None else f"{x:,.4f}" if isinstance(x, float) else str(x)
    for r in summ["cost_sensitivity"]["rows"]:
        L.append(f"| {r['role']} | {r['scenario_id']} | {r['kind']} | {r['cost_multiplier']} | {r['status']} | "
                 f"{f(r['trade_count'])} | {f(r['net_pnl'])} | {f(r['profit_factor'])} | {f(r['expectancy'])} | "
                 f"{f(r['total_return'])} |")
    L += ["", "## Batch-level 2x-cost check", "",
          "Falsifiable prediction: positive net PnL at 1x that survives the Core 2x multiplier. Computed here from the "
          "archived metrics of this batch (a single-run report cannot evaluate it).", ""]
    for role, chk in batch_two_x(summ["cost_sensitivity"]["rows"]).items():
        L.append(f"- {role}: " + (f"1x net {f(chk['net_1x'])}, 2x net {f(chk['net_2x'])} -> survives 2x: "
                                  f"**{'YES' if chk['survives_2x'] else 'NO'}**" if chk["evaluable"]
                                  else f"NOT_EVALUABLE ({chk['reason']})"))
    if c.get("kind") == "SYNTHETIC":
        L += ["", "SYNTHETIC fixture: engineering evidence only, not evidence about any market."]
    return "\n".join(L) + "\n"


def batch_two_x(rows) -> dict:
    out = {}
    for role in dict.fromkeys(r["role"] for r in rows):
        r1 = [r for r in rows if r["role"] == role and float(r["cost_multiplier"]) == 1.0 and r["status"] == "COMPLETED"]
        r2 = [r for r in rows if r["role"] == role and float(r["cost_multiplier"]) == 2.0 and r["status"] == "COMPLETED"]
        if not r1 or not r2 or not isinstance(r1[0]["net_pnl"], float) or not isinstance(r2[0]["net_pnl"], float):
            out[role] = {"evaluable": False, "reason": "needs COMPLETED 1x and 2x runs in this batch"}
        else:
            out[role] = {"evaluable": True, "net_1x": r1[0]["net_pnl"], "net_2x": r2[0]["net_pnl"],
                         "survives_2x": r1[0]["net_pnl"] > 0 and r2[0]["net_pnl"] > 0}
    return out


def run_config(config_path: Path, *, run_root: Path, archive_root: Path | None = None, resume: bool = False,
               batch_id: str | None = None, progress=print, agent=None, core=None, engine=None) -> dict:
    config_path = Path(config_path)
    try:
        runner, cfg, jobs = build_synthetic(config_path, run_root=Path(run_root), archive_root=archive_root,
                                            agent=agent, core=core, engine=engine, progress=progress)
    except (ResearchConfigError, BatchLimitExceeded, A.CoreContractGap) as e:
        rec = _refusal(Path(run_root) / "_refused_configs", config_path, e)
        progress(f"CONFIG REFUSED ({rec}): {e}")
        raise
    arch_root = runner.archiver.root
    ledger_dir = (arch_root if (cfg.data["output"]["target"] == "LOCAL" or DRIVE_ROOT.parent.parent.is_dir())
                  else Path(run_root)) / "_batches"
    rc04 = [m for r, m in cfg.issues if r == "RC04"]
    if rc04:
        progress("R02 (RC04): parameters violate the spec; runs are submitted so the registry records REJECTED_SPEC: "
                 + "; ".join(rc04))
    batch_id = batch_id or f"{cfg.data['config_id']}_{datetime.now(TZ):%Y%m%d_%H%M%S}"
    out = run_batch(runner, jobs, batch_id=batch_id, ledger_dir=ledger_dir, resume=resume, progress=progress,
                    identity_of=runner.identity)
    summ = {"batch_id": batch_id, "config": cfg.ref, "config_issues": list(cfg.issues),
            "scenario_set": cfg.scenario_meta, "runs": [o.__dict__ for o in out],
            "cost_sensitivity": cost_sensitivity_summary(out, jobs),
            "batch_two_x_check": None,
            "ledger": str(ledger_dir / f"batch_{batch_id}.jsonl")}
    summ["batch_two_x_check"] = batch_two_x(summ["cost_sensitivity"]["rows"])
    (ledger_dir / f"summary_{batch_id}.json").write_text(json.dumps(summ, indent=1, default=str), encoding="utf-8")
    (ledger_dir / f"cost_sensitivity_{batch_id}.md").write_text(sensitivity_markdown(summ), encoding="utf-8")
    summ["summary_path"] = str(ledger_dir / f"summary_{batch_id}.json")
    return summ

