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


@dataclass(frozen=True)
class Readiness:
    market: str
    entry: dict

    @property
    def status(self) -> str:
        return str(self.entry.get("status", "UNKNOWN"))

    def missing(self) -> list[str]:
        e = self.entry
        from .contract import gate_issues
        out = gate_issues(e)
        if self.status != "GO":
            out.append(f"status {self.status} (needs GO): {e.get('status_reason', '')}".strip())
        for k in ("evidence_ref", "approved_by", "approved_at", "snapshot_id", "producer_commit_sha"):
            if not e.get(k):
                out.append(f"{k} not set")
        if e.get("bar_label") not in ("START", "END"):
            out.append(f"bar_label {e.get('bar_label')}")
        if e.get("session_coverage") in (None, "UNKNOWN"):
            out.append("session_coverage UNKNOWN")
        # fields M3-A added: absent fields count as unknown (fail closed)
        for k in ("available_at_rule", "per_bar_session_mask"):
            if e.get(k) in (None, "UNKNOWN"):
                out.append(f"{k} {e.get(k) or 'UNKNOWN'}")
        el = e.get("execution_eligibility") or {}
        if not (el.get("buy") is True and el.get("sell") is True):
            out.append(f"execution eligibility buy={el.get('buy')} sell={el.get('sell')} "
                       f"({el.get('rejection_reason') or 'not recorded'})")
        cp = e.get("cost_profile") or {}
        if cp.get("status") != "APPROVED":
            out.append(f"cost profile {cp.get('profile_id')}@{cp.get('version')} {cp.get('status') or 'not recorded'}")
        if cp.get("core_representable") is not True:
            out.append(f"cost profile not representable in Core: {cp.get('core_gap') or 'not recorded'}")
        m3a = e.get("m3a") or {}
        if m3a.get("formal_execution") != "GO":
            out.append(f"M3-A formal_execution {m3a.get('formal_execution') or 'not recorded'}")
        w = e.get("allowed_window") or {}
        if not w.get("start") or not w.get("end"):
            out.append("allowed_window not set")
        if self.status != "GO":
            out += [f"required: {r}" for r in e.get("required_before_go", [])]
        return out


def readiness(market: str, path: Path = READINESS) -> Readiness:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    entry = d.get("markets", {}).get(market)
    if entry is None:
        return Readiness(market, {"status": "UNKNOWN", "status_reason": f"no readiness entry for {market}"})
    return Readiness(market, entry)


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
