"""Data access for the research runner.

Two sources, both returning the same ``MarketData`` (Core execution frame + pure-signal bars + metadata):

* ``SyntheticSource`` - the committed ``synthetic_tsm_15m_v1`` fixture. Engineering evidence only.
* ``GatedMarketSource`` - real data. It refuses to load anything unless ``governance/market_readiness.json``
  records the market as GO with evidence, a pinned snapshot and research-lead approval, and the requested window
  lies inside the allowed window. There is no flag that skips the gate.

The research runner never fills bars, never re-times them and never edits eligibility: the synthetic source sets
the fixture's own definitions (START label, available_at = bar_end), and real sources must come from Core's data
adapter (contract C1).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from strategy_agent.signals.core import SignalBar

from . import fixtures as FX

ROOT = Path(__file__).resolve().parents[3]
READINESS = ROOT / "governance" / "market_readiness.json"


class MarketGateClosed(RuntimeError):
    """Real-market research is blocked: the M3-A gate is not GO (fail closed, with the missing conditions)."""

    def __init__(self, market: str, status: str, missing: list[str]):
        self.market, self.status, self.missing = market, status, list(missing)
        super().__init__(f"market gate {market} is {status}; missing: " + "; ".join(missing))


@dataclass(frozen=True)
class MarketData:
    frame: Any                       # pandas DataFrame with adapter.CORE_BAR_COLUMNS
    signal_bars: tuple[SignalBar, ...]
    rows: tuple[dict, ...]           # plain rows (same content as frame) for evaluation / archive
    metadata: dict = field(default_factory=dict)


def _frame_sha(rows: list[dict]) -> str:
    canon = json.dumps(rows, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()


class SyntheticSource:
    """Committed synthetic fixture -> Core frame. Window filter on bar_start in [warmup_start, window_end)."""

    kind = "SYNTHETIC"
    dataset_id = FX.FIXTURE_ID

    def __init__(self, directory: Path = FX.FIXTURE_DIR):
        self.fixture = FX.load(directory)

    def describe(self) -> dict:
        m = self.fixture.meta
        return {"dataset_id": FX.FIXTURE_ID, "snapshot_id": f"{FX.FIXTURE_ID}@{self.fixture.csv_sha256[:12]}",
                "producer_commit_sha": self.fixture.csv_sha256[:40], "verdict": "GO", "usage_scope": "BACKTEST",
                "access_contract": "strategy_agent.research.fixtures.load (committed CSV, sha256-verified)",
                "kind": "SYNTHETIC", "not_market_data": True, "first_day": m["first_day"], "last_day": m["last_day"]}

    def load(self, *, symbol: str, warmup_start: str, window_end: str) -> MarketData:
        import pandas as pd  # noqa: PLC0415
        lo, hi = datetime.fromisoformat(warmup_start), datetime.fromisoformat(window_end)
        rows, sbars = [], []
        for r in self.fixture.rows:
            start = datetime.fromisoformat(r["bar_start"])
            if not (lo <= start < hi):
                continue
            end = start + timedelta(minutes=FX.BAR_MINUTES)
            s_open, s_close = FX.session_bounds(start.date())
            o, h, lw, c, v = (float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]),
                              float(r["volume"]))
            rows.append({"bar_start": start, "bar_end": end, "available_at": end, "timezone": FX.TZ,
                         "session": start.date().isoformat(), "symbol": symbol, "market": "IBKR_EQUITY",
                         "contract": symbol, "open": o, "high": h, "low": lw, "close": c, "volume": v,
                         "data_exists": True, "backtest_eligible": True, "buy_execution_eligible": True,
                         "sell_execution_eligible": True, "execution_rejection_reason": "",
                         "session_open": s_open, "session_close": s_close})
            # ineligible for signals: zero volume (convention v1 §1.3: skipped, never synthesised)
            sbars.append(SignalBar(start, end, o, h, lw, c, v, symbol, start.date().isoformat(), v > 0))
        if not rows:
            raise ValueError(f"no fixture bars in [{warmup_start}, {window_end})")
        frame = pd.DataFrame(rows)
        md = self.describe() | {"rows": len(rows), "window": [warmup_start, window_end],
                                "frame_sha256": _frame_sha(rows), "fixture_csv_sha256": self.fixture.csv_sha256,
                                "pit_definition": "decision at bar available_at (= bar_end); fill at the first "
                                                  "eligible bar with bar_start >= decision (Core, convention v1)",
                                "fixture_meta": self.fixture.meta}
        return MarketData(frame, tuple(sbars), tuple(rows), md)


class NativeSyntheticSource:
    """Committed native-1m synthetic fixture -> Core M3-A.4 conservative frame (TEST_ONLY, not market data).

    Bucketing, START/END uncertainty envelopes, RTH / closing-auction exclusion and eligibility are computed by
    Core ``prepare_conservative_bars`` (bar_minutes=15). The Agent only filters native rows to the requested window
    and reads Core's columns; it never re-times a bar or edits eligibility. Signal time = Core
    ``modeled_available_at`` (latest START/END completion); signal eligibility = Core ``signal_eligible``.
    """

    kind = "SYNTHETIC"
    dataset_id = None            # set in __init__ from the native fixture
    temporal_policy = "CONSERVATIVE_LABEL_AGNOSTIC"

    def __init__(self, directory: Path | None = None, core=None):
        from . import native_fixture as NF  # noqa: PLC0415
        self.NF = NF
        self.fixture = NF.load(directory or NF.FIXTURE_DIR)
        self.dataset_id = NF.FIXTURE_ID
        self._core = core

    def describe(self) -> dict:
        m = self.fixture.meta
        return {"dataset_id": self.dataset_id, "snapshot_id": f"{self.dataset_id}@{self.fixture.csv_sha256[:12]}",
                "producer_commit_sha": self.fixture.csv_sha256[:40], "verdict": "GO", "usage_scope": "BACKTEST",
                "access_contract": "strategy_agent.research.native_fixture.load (generated, sha256-pinned) -> "
                                   "market_research_core.prepare_conservative_bars(bar_minutes=15)",
                "kind": "SYNTHETIC", "not_market_data": True, "first_day": m["first_day"], "last_day": m["last_day"]}

    def load(self, *, symbol: str, warmup_start: str, window_end: str) -> MarketData:
        import pandas as pd  # noqa: PLC0415
        core = self._core
        if core is None:                    # Core is reached only through the adapter (single import site)
            from strategy_agent.adapters.mrc_adapter import _core  # noqa: PLC0415
            core = _core()
        if symbol != self.NF.SYMBOL:
            raise ValueError(f"native fixture is labelled {self.NF.SYMBOL}, spec asks {symbol}")
        lo, hi = datetime.fromisoformat(warmup_start), datetime.fromisoformat(window_end)
        native = []
        for r in self.fixture.rows:
            t = datetime.fromisoformat(r["source_timestamp"])
            if lo <= t < hi:
                native.append({"source_timestamp": pd.Timestamp(t), "symbol": symbol, "contract": symbol,
                               "open": float(r["open"]), "high": float(r["high"]), "low": float(r["low"]),
                               "close": float(r["close"]), "volume": float(r["volume"])})
        if not native:
            raise ValueError(f"no native fixture rows in [{warmup_start}, {window_end})")
        frame = core.prepare_conservative_bars(pd.DataFrame(native), bar_minutes=self.NF.BUCKET_MINUTES)
        rows, sbars = [], []
        for b in frame.drop(columns=["source_rows"]).to_dict("records"):
            # evaluator rows: Core's columns in the market timezone (as Core reports fills), plus the trading
            # date as the evaluator's session key; Core's own session class is kept as session_class
            b = {k: (v.tz_convert(self.NF.TZ).to_pydatetime() if isinstance(v, pd.Timestamp) and v.tzinfo else v)
                 for k, v in b.items()}
            start, avail = b["bar_start"], b["modeled_available_at"]
            day = b["source_timestamp"].date().isoformat()
            rows.append(b | {"session_class": b["session"], "session": day})
            sbars.append(SignalBar(start, avail, float(b["open"]), float(b["high"]), float(b["low"]),
                                   float(b["close"]), float(b["volume"]), symbol, day, bool(b["signal_eligible"])))
        md = self.describe() | {
            "rows": len(rows), "native_rows": len(native), "window": [warmup_start, window_end],
            "frame_sha256": _frame_sha([{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()}
                                        for r in native]),
            "fixture_csv_sha256": self.fixture.csv_sha256, "bars_per_day": self.NF.BUCKETS_PER_DAY,
            "temporal_policy": {"policy_id": core.CONSERVATIVE_POLICY.policy_id,
                                "version": core.CONSERVATIVE_POLICY.version,
                                "sha256": core.CONSERVATIVE_POLICY.sha256},
            "pit_definition": "decision at Core modeled_available_at (latest START/END completion of a complete "
                              "15-label bucket); fill at Core's first candidate whose EARLIEST possible open is "
                              "strictly later (Core M3-A.4 CONSERVATIVE_LABEL_AGNOSTIC)",
            "eligibility_counts": {"buckets": int(len(frame)), "signal_eligible": int(frame.signal_eligible.sum()),
                                   "buy_execution_eligible": int(frame.buy_execution_eligible.sum()),
                                   "rejection_reasons": {k: int(v) for k, v in
                                                         frame.execution_rejection_reason.value_counts().items() if k}},
            "fixture_meta": self.fixture.meta}
        return MarketData(frame, tuple(sbars), tuple(rows), md)


GATES = ("DATA_QUALITY", "TEMPORAL_SAFETY", "COST_MODEL_COMPATIBILITY", "COST_APPROVAL", "HOLDOUT_APPROVAL",
         "FORMAL_EXECUTION_ELIGIBILITY")
# gates that need a named human approval on top of PASS + evidence
APPROVAL_GATES = {"TEMPORAL_SAFETY", "COST_APPROVAL", "HOLDOUT_APPROVAL"}
# gates whose PASS must come from a pinned Core document (the Agent never declares these itself)
CORE_GATES = {"DATA_QUALITY", "TEMPORAL_SAFETY", "COST_MODEL_COMPATIBILITY", "FORMAL_EXECUTION_ELIGIBILITY"}
_PINNED_CORE = re.compile(r"^https://github\.com/hh4832/market-research-core/blob/[0-9a-f]{40}/")


@dataclass(frozen=True)
class Readiness:
    market: str
    entry: dict
    required: tuple[str, ...] = GATES

    @property
    def status(self) -> str:
        return str(self.entry.get("status", "UNKNOWN"))

    def gate_status(self) -> dict[str, str]:
        """Effective status per gate. A recorded PASS that lacks pinned evidence or approval counts as FAIL."""
        gates = self.entry.get("gates") or {}
        out = {}
        for g in self.required:
            r = gates.get(g) or {}
            st = str(r.get("status", "UNKNOWN"))
            if st == "PASS":
                ev = r.get("evidence_ref") or ""
                if g in CORE_GATES and not _PINNED_CORE.match(ev):
                    st = "FAIL_UNPINNED_EVIDENCE"
                elif not ev:
                    st = "FAIL_NO_EVIDENCE"
                elif g in APPROVAL_GATES and not r.get("approved_by"):
                    st = "FAIL_NO_APPROVAL"
            out[g] = st
        return out

    def missing(self) -> list[str]:
        e = self.entry
        gates = e.get("gates") or {}
        out = []
        if self.status != "GO":
            out.append(f"status {self.status} (needs GO): {e.get('status_reason', '')}".strip())
        for g, st in self.gate_status().items():
            if st != "PASS":
                out.append(f"gate {g} {st}: {(gates.get(g) or {}).get('reason', 'not recorded')}")
        for k in ("snapshot_id", "producer_commit_sha"):
            if not e.get(k):
                out.append(f"{k} not set")
        w = e.get("allowed_window") or {}
        if not w.get("start") or not w.get("end"):
            out.append("allowed_window not set")
        if self.status != "GO":
            out += [f"required: {r}" for r in e.get("required_before_go", [])]
        return out


def readiness(market: str, path: Path = READINESS) -> Readiness:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    entry = d.get("markets", {}).get(market)
    req = tuple(d.get("required_gates") or GATES)
    if set(GATES) - set(req):                       # a registry may add gates, never drop the six
        req = GATES + tuple(g for g in req if g not in GATES)
    if entry is None:
        return Readiness(market, {"status": "UNKNOWN", "status_reason": f"no readiness entry for {market}"}, req)
    return Readiness(market, entry, req)


class GatedMarketSource:
    """Real market data behind the M3-A gate. Fails closed until the gate record is complete and GO."""

    kind = "REAL_MARKET"

    def __init__(self, market: str, loader=None, path: Path = READINESS):
        self.market, self.loader, self.path = market, loader, path

    def check(self, window_start: str | None = None, window_end: str | None = None) -> Readiness:
        r = readiness(self.market, self.path)
        miss = r.missing()
        if not miss and window_start and window_end:
            w = r.entry["allowed_window"]
            if not (date.fromisoformat(w["start"][:10]) <= date.fromisoformat(window_start[:10])
                    and date.fromisoformat(window_end[:10]) <= date.fromisoformat(w["end"][:10])):
                miss.append(f"requested window {window_start}..{window_end} outside allowed {w['start']}..{w['end']}")
        if miss:
            raise MarketGateClosed(self.market, r.status, miss)
        if self.loader is None:
            raise MarketGateClosed(self.market, r.status, ["no Core data loader configured for this market"])
        return r

    def load(self, *, symbol: str, warmup_start: str, window_end: str) -> MarketData:
        self.check(warmup_start, window_end)
        return self.loader(symbol=symbol, warmup_start=warmup_start, window_end=window_end)
