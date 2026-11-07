"""Synthetic NATIVE 1-minute fixture for the Core M3-A.4 conservative path (TEST_ONLY, NOT market data).

``synthetic_tsm_1m_native_v1``: a deterministic, seeded random walk shaped like native 1-minute RTH source rows of
a US-listed equity, labelled ``TSM`` only because Core's conservative temporal scope is TSM-only. It exists to
exercise Agent -> Core ``prepare_conservative_bars`` (15 source labels per bucket) -> ``ExecutionEngine`` ->
metrics -> registry -> report -> archive. Nothing computed on it is evidence about TSM, IBKR or SMA.

* Source labels: 09:30..15:59 America/New_York on NYSE full trading days from 2025-01-06 (the three NYSE closures
  in the span are left out). The label's START/END meaning is deliberately UNKNOWN: Core models both.
* Deliberate defects so fail-closed handling is exercised: one missing minute (incomplete 15-label bucket) and
  one zero-volume minute. Nothing is ever forward-filled.
* Rows are generated (``random.Random``, stable across Python versions); the committed metadata pins the
  canonical CSV sha256 and ``load`` refuses anything else.
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
FIXTURE_ID = "synthetic_tsm_1m_native_v1"
TZ = "America/New_York"
SYMBOL = "TSM"
SOURCE_MINUTES = 1
BUCKET_MINUTES = 15
LABELS_PER_DAY = 390                 # 09:30 .. 15:59
BUCKETS_PER_DAY = 26
N_DAYS = 30
SEED = 20261105
NYSE_CLOSED = (date(2025, 1, 9), date(2025, 1, 20), date(2025, 2, 17))   # national day of mourning, MLK, Presidents
MISSING = ((3, 47),)                 # (day index, minute index): bucket 10:15 of day 3 has 14 labels
ZERO_VOLUME = ((10, 200),)           # bucket 12:45 of day 10 contains a zero-volume label
COLUMNS = ("source_timestamp", "open", "high", "low", "close", "volume")


@dataclass(frozen=True)
class NativeFixture:
    fixture_id: str
    rows: list[dict]
    meta: dict
    csv_sha256: str


def trading_days(n: int = N_DAYS) -> list[date]:
    out, d = [], date(2025, 1, 6)
    while len(out) < n:
        if d.weekday() < 5 and d not in NYSE_CLOSED:
            out.append(d)
        d += timedelta(days=1)
    return out


def generate(seed: int = SEED) -> str:
    rng = random.Random(seed)
    tz = ZoneInfo(TZ)
    price = 100.0
    drifts = [0.00003, -0.00001, 0.0, -0.00003, 0.00002, -0.00001]           # per-minute drift by 5-day block
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(COLUMNS)
    for di, d in enumerate(trading_days()):
        drift = drifts[(di // 5) % len(drifts)]
        if di:
            price *= 1 + rng.gauss(0, 0.003)
        for mi in range(LABELS_PER_DAY):
            t = datetime.combine(d, time(9, 30), tz) + timedelta(minutes=mi)
            o = price
            c = o * (1 + drift + rng.gauss(0, 0.0007))
            hi = max(o, c) * (1 + abs(rng.gauss(0, 0.0002)))
            lo = min(o, c) * (1 - abs(rng.gauss(0, 0.0002)))
            vol = int(1500 + rng.random() * 6000)
            price = c
            if (di, mi) in MISSING:
                continue
            if (di, mi) in ZERO_VOLUME:
                vol = 0
            o2, c2 = round(o, 2), round(c, 2)
            w.writerow((t.isoformat(), f"{o2:.2f}", f"{max(round(hi, 2), o2, c2):.2f}",
                        f"{min(round(lo, 2), o2, c2):.2f}", f"{c2:.2f}", vol))
    return buf.getvalue()


def meta(csv_text: str) -> dict:
    days = trading_days()
    return {
        "fixture_id": FIXTURE_ID, "kind": "SYNTHETIC", "not_market_data": True, "test_only": True,
        "symbol_label": SYMBOL, "generator": "strategy_agent.research.native_fixture.generate", "seed": SEED,
        "timezone": TZ, "source_minutes": SOURCE_MINUTES, "bucket_minutes": BUCKET_MINUTES,
        "bars_per_day": BUCKETS_PER_DAY, "labels_per_day": LABELS_PER_DAY, "days": N_DAYS,
        "first_day": days[0].isoformat(), "last_day": days[-1].isoformat(),
        "excluded_nyse_closures": [d.isoformat() for d in NYSE_CLOSED],
        "source_timestamp_label": "UNKNOWN (deliberately; Core CONSERVATIVE_LABEL_AGNOSTIC models START and END)",
        "deliberate_defects": {"missing_labels": [list(x) for x in MISSING],
                               "zero_volume_labels": [list(x) for x in ZERO_VOLUME]},
        "csv_sha256": hashlib.sha256(csv_text.encode()).hexdigest(),
        "rows": csv_text.count("\n") - 1,
    }


def write_meta(directory: Path = FIXTURE_DIR) -> Path:  # pragma: no cover - one-off
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / f"{FIXTURE_ID}.meta.json"
    p.write_text(json.dumps(meta(generate()), indent=2) + "\n", encoding="utf-8")
    return p


def load(directory: Path = FIXTURE_DIR) -> NativeFixture:
    """Regenerate and verify against the committed metadata (fail closed on any mismatch)."""
    m = json.loads((directory / f"{FIXTURE_ID}.meta.json").read_text(encoding="utf-8"))
    text = generate(m["seed"])
    sha = hashlib.sha256(text.encode()).hexdigest()
    if sha != m["csv_sha256"]:
        raise ValueError(f"fixture {FIXTURE_ID} sha256 {sha} != metadata {m['csv_sha256']}")
    rows = list(csv.DictReader(io.StringIO(text)))
    if len(rows) != m["rows"]:
        raise ValueError(f"fixture {FIXTURE_ID} has {len(rows)} rows, metadata says {m['rows']}")
    return NativeFixture(FIXTURE_ID, rows, m, sha)


if __name__ == "__main__":  # pragma: no cover
    print(write_meta())
