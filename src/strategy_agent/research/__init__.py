"""M3-B automated research runner (assembly of M0/M1/M2 components; no second backtest engine)."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RESEARCH_SPEC = ROOT / "specs" / "research" / "sma_crossover_tsm_15m_5_20.json"
SYNTH_WINDOW = {"warmup_start": "2025-01-06T00:00:00-05:00", "window_start": "2025-01-07T00:00:00-05:00",
                "window_end": "2025-04-01T00:00:00-04:00"}


def load_research_spec(path: Path = RESEARCH_SPEC) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def synthetic_setup(run_root: Path, *, archive_root: Path | None = None, require_drive: bool = False, core=None,
                    engine=None, agent=None, progress=None, clock=None, archive_clock=None):
    """Runner + job for the fixed SMA 5/20 baseline on the synthetic fixture, in an isolated environment."""
    from strategy_agent.adapters import mrc_adapter as A

    from .archive import Archiver
    from .data import SyntheticSource
    from .environment import synthetic_environment, synthetic_spec
    from .pipeline import FIXED_BASELINE, ResearchJob, ResearchRunner

    src = SyntheticSource()
    env = synthetic_environment(Path(run_root), dataset_id=src.dataset_id, clock=clock)
    prof = next(iter(env.cost_profiles.values()))
    base = load_research_spec()
    spec = synthetic_spec(base, src.describe(), prof)
    sem = A.MarketSemantics(market="IBKR_EQUITY", bar_label="START", session_semantics="CONFIRMED",
                            timezone="America/New_York", initial_cash=float(spec["position_sizing"]["initial_capital"]),
                            constant_tick_size=0.01)
    arch = Archiver(archive_root, require_drive=require_drive, local_fallback=Path(run_root) / "_local_archive",
                    clock=archive_clock) if archive_root is not None or require_drive else None
    kw = {"clock": clock} if clock else {}
    runner = ResearchRunner(env, src, sem, core=core, engine=engine, agent=agent, archiver=arch, progress=progress,
                            **kw)
    job = ResearchJob(spec=spec, parameters=dict(FIXED_BASELINE), seed=spec["experiment"]["seed"],
                      split_ref=spec["experiment"]["split_ref"], label="sma_5_20_synthetic", **SYNTH_WINDOW)
    return runner, job
