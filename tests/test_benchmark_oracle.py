"""The independent oracle must reproduce every HAND-COMPUTED expected value.

Two derivations (manual arithmetic in the case file, and the reference engine)
must agree exactly (Decimal equality, no tolerance). When Phase 2 is GO, the
same `expected` blocks are used by `benchmark/harness` to audit the real engine.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from benchmark.oracle.reference_engine import load_case, run_case

CASES = sorted((Path(__file__).parents[1] / "benchmark" / "cases").glob("B*.json"))
FILL_KEYS = ("order_ts", "exec_ts", "side", "qty", "contract", "ref_open", "fill_price", "fee", "tax")


def _norm(v):
    if isinstance(v, str):
        try:
            return Decimal(v).normalize()
        except Exception:
            return v
    return v


def _same(a, b, keys):
    return all(_norm(a[k]) == _norm(b[k]) for k in keys)


def test_twelve_cases_present():
    assert len(CASES) == 12


@pytest.mark.parametrize("path", CASES, ids=lambda p: p.stem)
def test_oracle_matches_hand_calculation(path):
    case = load_case(path)
    exp = case["expected"]
    got = run_case(case)

    assert got["signals"] == exp["signals"]
    assert len(got["fills"]) == len(exp["fills"])
    for g, e in zip(got["fills"], exp["fills"]):
        assert _same(g, e, FILL_KEYS), (g, e)

    assert got["rejections"] == exp["rejections"]
    assert [_norm(r["net_pnl"]) for r in got["round_trips"]] == [_norm(x) for x in exp["round_trip_net_pnl"]]
    assert _norm(got["realized_pnl"]) == _norm(exp["realized_pnl"])

    if exp["open_position"] is None:
        assert got["open_position"] is None
    else:
        for k, v in exp["open_position"].items():
            assert _norm(got["open_position"][k]) == _norm(v), k

    if "suppressed_signals" in exp:
        assert got["suppressed_signals"] == exp["suppressed_signals"]
    if "invalidated_net_pnl" in exp:
        assert [_norm(r["net_pnl"]) for r in got["invalidated_round_trips"]] == \
            [_norm(x) for x in exp["invalidated_net_pnl"]]

    fill_times = {f["exec_ts"] for f in got["fills"]}
    for bad in exp.get("forbidden_fills", []):
        assert bad["exec_ts"] not in fill_times, bad["why"]


def test_cost_breakdown_b06_adds_up():
    exp = load_case(next(p for p in CASES if p.stem.startswith("B06")))["expected"]["cost_breakdown"]
    d = {k: Decimal(v) for k, v in exp.items()}
    assert d["gross_at_ref_open"] - d["slippage_cost"] - d["fees"] - d["taxes"] == d["net"]


def test_b09_naive_stitch_is_wrong():
    case = load_case(next(p for p in CASES if p.stem.startswith("B09")))
    assert Decimal(case["expected"]["naive_stitched_wrong_answer"]["gross"]) != Decimal(
        case["expected"]["realized_pnl"])


def test_fixture_rejects_inconsistent_ohlc():
    from benchmark.oracle.reference_engine import ReferenceEngine
    case = load_case(CASES[0])
    case["bars"][0] = ["2026-01-05 09:00", 20000, 19990, 19995, 20002, 100]  # high < open
    with pytest.raises(ValueError):
        ReferenceEngine(case)
