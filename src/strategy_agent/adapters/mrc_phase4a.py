"""Core import site for market-research-core Phase 4-A (research-only TSM native loader + research cost profile).

Second, Phase-4-A-only sibling of ``mrc_adapter`` (whose code is pinned by the Phase 3 Colab notebook and is
therefore not edited). Only these two adapter modules may import Core; tests enforce the exact list.
"""
from __future__ import annotations


def core_phase4a():
    """Return (tsm_native, tsm_research_costs) from the installed Core."""
    import market_research_core.tsm_native as native  # noqa: PLC0415
    import market_research_core.tsm_research_costs as costs  # noqa: PLC0415
    return native, costs


def core_dated_costs():
    """Return Core's pinned, immutable DatedCostSchedule API."""
    import market_research_core.dated_costs as dated_costs  # noqa: PLC0415
    return dated_costs


def core_repository_root():
    """Resolve the root of the exact installed Core checkout for pinned read-only assets."""
    from pathlib import Path  # noqa: PLC0415
    dated_costs = core_dated_costs()
    root = Path(dated_costs.__file__).resolve().parents[2]
    if not (root / "configs/research/tsm_official_dated_costs_2017_2024.json").is_file():
        raise RuntimeError("pinned Core checkout does not contain the official dated TSM cost schedule")
    return root


def core_development():
    """Return the reviewed v1.1 producer/reader modules; this never decodes or signs by itself."""
    import market_research_core.tsm_development as development
    import market_research_core.tsm_native as native
    return development, native
