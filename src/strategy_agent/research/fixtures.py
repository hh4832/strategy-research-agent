"""Synthetic market fixture for the M3-B research runner (NOT market data).

``synthetic_tsm_15m_v1`` is a deterministic, seeded random walk shaped like 15-minute RTH bars of a US-listed
equity. It exists only to exercise Spec -> Signal -> Adapter -> Core -> Report -> Registry end to end. Nothing
computed on it is evidence about TSM or about SMA profitability.

* Calendar: weekdays from 2025-01-06, 26 bars per day 09:30-16:00 America/New_York (synthetic calendar: US
  holidays are NOT removed; DST handled by zoneinfo).
* Label: bar_start (START); available_at = bar_end; session = trading date. These are fixture definitions, not
  claims about IBKR data (that is the M3-A question).
* Deliberate data-quality defects so the checks have something to find: zero-volume bars and missing bars.
* The CSV is committed; ``generate`` must reproduce it byte for byte (test_fixture_is_reproducible).
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
FIXTURE_DIR = ROOT / "fixtures" / "synthetic"
FIXTURE_ID = "synthetic_tsm_15m_v1"
TZ = "America/New_York"
SYMBOL = "TSM"
BAR_MINUTES = 15
BARS_PER_DAY = 26
N_DAYS = 80
SEED = 20261104
ZERO_VOLUME = ((7, 3), (21, 25), (44, 12))          # (day index, bar index)
MISSING = ((15, 9), (58, 0))
COLUMNS = ("bar_start", "open", "high", "low", "close", "volume")


@dataclass(frozen=True)
class Fixture:
    fixture_id: str
    rows: list[dict]
    meta: dict
    csv_sha256: str


def _days(n: int) -> list[date]:
    out, d = [], date(2025, 1, 6)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def generate(seed: int = SEED) -> str:
    """Return the fixture CSV text. Uses ``random.Random`` (stable across Python versions) and fixed regimes."""
    rng = random.Random(seed)
    tz = ZoneInfo(TZ)
    price = 100.0
    # drift per bar by 10-day block: alternating trend / range regimes so the crossover trades both ways
    drifts = [0.0009, -0.0002, 0.0, -0.0010, 0.0006, 0.0, 0.0011, -0.0008]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(COLUMNS)
    for di, d in enumerate(_days(N_DAYS)):
        drift = drifts[(di // 10) % len(drifts)]
        if di:
            price *= 1 + rng.gauss(0, 0.004)                     # overnight gap
        for bi in range(BARS_PER_DAY):
            start = datetime.combine(d, time(9, 30), tz) + timedelta(minutes=BAR_MINUTES * bi)
            o = price
            path = [o]
            for _ in range(3):
                path.append(path[-1] * (1 + drift / 3 + rng.gauss(0, 0.0016)))
            c = path[-1]
            hi = max(path) * (1 + abs(rng.gauss(0, 0.0005)))
            lo = min(path) * (1 - abs(rng.gauss(0, 0.0005)))
            vol = int(20000 + rng.random() * 80000)
            price = c
            if (di, bi) in MISSING:
                continue
            if (di, bi) in ZERO_VOLUME:
                vol = 0
            w.writerow((start.isoformat(), f"{o:.2f}", f"{max(hi, o, c):.2f}", f"{min(lo, o, c):.2f}", f"{c:.2f}",
                        vol))
    return buf.getvalue()


def meta(csv_text: str) -> dict:
    days = _days(N_DAYS)
    return {
        "fixture_id": FIXTURE_ID, "kind": "SYNTHETIC", "not_market_data": True, "symbol_label": SYMBOL,
        "generator": "strategy_agent.research.fixtures.generate", "seed": SEED, "timezone": TZ,
        "bar_minutes": BAR_MINUTES, "bars_per_day": BARS_PER_DAY, "days": N_DAYS,
        "first_day": days[0].isoformat(), "last_day": days[-1].isoformat(),
        "bar_label": "START", "available_at_rule": "bar_end (bar_start + 15 minutes)",
        "session_rule": "synthetic RTH 09:30-16:00 America/New_York; US holidays not removed",
        "deliberate_defects": {"zero_volume_bars": [list(x) for x in ZERO_VOLUME],
                               "missing_bars": [list(x) for x in MISSING]},
        "csv_sha256": hashlib.sha256(csv_text.encode()).hexdigest(),
        "rows": csv_text.count("\n") - 1,
    }


def write(directory: Path = FIXTURE_DIR) -> Path:  # pragma: no cover - one-off generator
    directory.mkdir(parents=True, exist_ok=True)
    text = generate()
    (directory / f"{FIXTURE_ID}.csv").write_text(text, encoding="utf-8")
    (directory / f"{FIXTURE_ID}.meta.json").write_text(json.dumps(meta(text), indent=2) + "\n", encoding="utf-8")
    return directory / f"{FIXTURE_ID}.csv"


def load(directory: Path = FIXTURE_DIR) -> Fixture:
    """Load the committed fixture and verify it against its metadata (fail closed on any mismatch)."""
    text = (directory / f"{FIXTURE_ID}.csv").read_text(encoding="utf-8")
    m = json.loads((directory / f"{FIXTURE_ID}.meta.json").read_text(encoding="utf-8"))
    sha = hashlib.sha256(text.encode()).hexdigest()
    if sha != m["csv_sha256"]:
        raise ValueError(f"fixture {FIXTURE_ID} sha256 {sha} != metadata {m['csv_sha256']}")
    rows = list(csv.DictReader(io.StringIO(text)))
    if len(rows) != m["rows"]:
        raise ValueError(f"fixture {FIXTURE_ID} has {len(rows)} rows, metadata says {m['rows']}")
    return Fixture(FIXTURE_ID, rows, m, sha)


def session_bounds(day: date) -> tuple[datetime, datetime]:
    tz = ZoneInfo(TZ)
    return datetime.combine(day, time(9, 30), tz), datetime.combine(day, time(16, 0), tz)


if __name__ == "__main__":  # pragma: no cover
    print(write())
