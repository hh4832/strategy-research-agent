"""Mutation tests: deliberately broken engines MUST fail the benchmark.

If a mutant passes, the benchmark has no teeth for that defect class.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from benchmark.oracle.reference_engine import ReferenceEngine, load_case, result_to_dict

CASES = {p.stem[:3]: p for p in (Path(__file__).parents[1] / "benchmark" / "cases").glob("B*.json")}


def _passes(engine_cls, case_id: str) -> bool:
    case = load_case(CASES[case_id])
    got = result_to_dict(engine_cls(case).run())
    exp = case["expected"]
    same_fills = [(f["exec_ts"], Decimal(f["fill_price"])) for f in got["fills"]] == \
        [(f["exec_ts"], Decimal(f["fill_price"])) for f in exp["fills"]]
    return same_fills and Decimal(got["realized_pnl"]) == Decimal(exp["realized_pnl"])


class IgnoresLocks(ReferenceEngine):
    def __init__(self, case):
        super().__init__(case)
        for b in self.bars:
            b.limit_up_locked = b.limit_down_locked = False


class FillsOutsideSession(ReferenceEngine):
    def __init__(self, case):
        super().__init__(case)
        for b in self.bars:
            b.in_session = True


class NoSlippage(ReferenceEngine):
    def fill_price(self, side, ref_open):
        return ref_open


class FillsZeroVolume(ReferenceEngine):
    def __init__(self, case):
        super().__init__(case)
        for b in self.bars:
            if b.v == 0:
                b.v = 1


class PitJoinOnAsOf(ReferenceEngine):
    """Uses the as_of date instead of available_at (classic PIT leak)."""

    def __init__(self, case):
        case = dict(case)
        sm = dict(case["signal_model"])
        sm["events"] = [dict(e, available_at=f"{e['as_of']} 00:00") for e in sm["events"]]
        case["signal_model"] = sm
        super().__init__(case)


class OrbUsesIntrabarHigh(ReferenceEngine):
    """Treats a touch of the range high as a confirmed breakout."""

    def __init__(self, case):
        super().__init__(case)
        for b in self.bars:
            b.c = b.h


class IgnoresExclusions(ReferenceEngine):
    def __init__(self, case):
        case = dict(case, exclusions=[])
        super().__init__(case)


class SinglePriceSeriesRoll(ReferenceEngine):
    """Ignores the roll schedule and keeps trading the front contract."""

    def __init__(self, case):
        case = dict(case, roll_schedule=[])
        super().__init__(case)


def test_reference_engine_passes_all():
    for cid in CASES:
        assert _passes(ReferenceEngine, cid), cid


def test_mutants_are_caught():
    caught = {
        "IgnoresLocks": not _passes(IgnoresLocks, "B07"),
        "FillsOutsideSession": not _passes(FillsOutsideSession, "B05"),
        "NoSlippage": not _passes(NoSlippage, "B06"),
        "FillsZeroVolume": not _passes(FillsZeroVolume, "B04"),
        "PitJoinOnAsOf": not _passes(PitJoinOnAsOf, "B08"),
        "OrbUsesIntrabarHigh": not _passes(OrbUsesIntrabarHigh, "B12"),
        "IgnoresExclusions": not _passes(IgnoresExclusions, "B11"),
        "SinglePriceSeriesRoll": not _passes(SinglePriceSeriesRoll, "B09"),
    }
    assert all(caught.values()), caught
