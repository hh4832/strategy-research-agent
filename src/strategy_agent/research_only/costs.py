"""Research-only cost scenario set (Phase 4-A): validation and per-fill REFERENCE arithmetic.

The reference arithmetic is for contract tests against Core's ledger only.
Reported performance must always come from Core's ExecutionEngine output,
never from this module (decision Q4).

Rules
-----
CO01 scope RESEARCH_ONLY; status never APPROVED / ACCOUNT_VERIFIED / BROKER_ACTUAL
CO02 every component carries label RESEARCH_ONLY
CO03 SEC schedule source file SHA matches the pinned Core-format configuration
CO04 scenario differences: LOW / STRESS differ from BASE only in slippage; CAT_STRESS only in CAT
CO05 TAF schedule is contiguous, inside the coverage period, which ends before the holdout
CO06 a run window must sit inside ONE SEC interval and ONE TAF interval (no silent rate change)
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SET = ROOT / "configs" / "research_only" / "ibkr_pro_fixed_tsm_research_only_costs_v1.json"
CENT = Decimal("0.01")


class CostSetError(ValueError):
    pass


def file_sha256(p: str | Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _d(s: str) -> date:
    return date.fromisoformat(s)


@dataclass(frozen=True)
class Rates:
    scenario: str
    commission_per_share: Decimal
    minimum: Decimal
    cap_rate: Decimal
    sec_rate: Decimal
    sec_version: str
    taf_per_share: Decimal
    cat_per_share: Decimal
    slippage_bps: Decimal


class CostSet:
    def __init__(self, path: str | Path = DEFAULT_SET, root: Path = ROOT):
        self.path = Path(path)
        self.raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.sha256 = file_sha256(self.path)
        self.root = root
        self.issues = self._validate()
        if self.issues:
            raise CostSetError("; ".join(f"{c}: {m}" for c, m in self.issues))
        src = self.raw["sec_section31"]["schedule_source"]
        self.sec = json.loads((root / src["path"]).read_text(encoding="utf-8"))[src["field"]]

    def _validate(self) -> list[tuple[str, str]]:
        c, out = self.raw, []
        if c.get("scope") != "RESEARCH_ONLY" or c.get("status") in {"APPROVED", "ACCOUNT_VERIFIED", "BROKER_ACTUAL"}:
            out.append(("CO01", f"scope/status {c.get('scope')}/{c.get('status')} not research-only"))
        for k in ("commission", "sec_section31", "finra_taf", "cat"):
            if c.get(k, {}).get("label") != "RESEARCH_ONLY":
                out.append(("CO02", f"{k} is not labelled RESEARCH_ONLY"))
        src = c["sec_section31"]["schedule_source"]
        p = self.root / src["path"]
        if not p.is_file() or file_sha256(p) != src["file_sha256"]:
            out.append(("CO03", "SEC schedule source missing or its sha256 changed"))
        base = c["scenarios"].get("BASE")
        if base is None:
            out.append(("CO04", "BASE scenario missing"))
        else:
            for name, sc in c["scenarios"].items():
                diff = sorted(k for k in set(base) | set(sc) if base.get(k) != sc.get(k))
                allowed = c["scenario_rules"].get(name, [])
                if name != "BASE" and (not diff or set(diff) - set(allowed)):
                    out.append(("CO04", f"{name} differs from BASE in {diff}; allowed {allowed}"))
        cov_s, cov_e = _d(c["coverage"]["start"]), _d(c["coverage"]["end"])
        if cov_e >= date(2025, 1, 1):
            out.append(("CO05", "cost coverage reaches the holdout period"))
        taf = c["finra_taf"]["schedule"]
        if _d(taf[0]["effective_from"]) != cov_s or _d(taf[-1]["effective_to"]) != cov_e:
            out.append(("CO05", "TAF schedule does not span the coverage period exactly"))
        for a, b in zip(taf, taf[1:]):
            if _d(b["effective_from"]) != _d(a["effective_to"]) + timedelta(days=1):
                out.append(("CO05", f"TAF schedule gap/overlap at {b['effective_from']}"))
        return out

    # ---------------------------------------------------------------- lookup

    def _interval(self, rows: list[dict], d: date, what: str) -> dict:
        hits = [r for r in rows if _d(r["effective_from"]) <= d <= _d(r["effective_to"])]
        if len(hits) != 1:
            raise CostSetError(f"CO06: {what} has {len(hits)} intervals for {d} (outside coverage fails closed)")
        return hits[0]

    def check_window(self, first_day: date, last_day: date) -> dict:
        cov_s, cov_e = _d(self.raw["coverage"]["start"]), _d(self.raw["coverage"]["end"])
        if not (cov_s <= first_day <= last_day <= cov_e):
            raise CostSetError(f"CO06: window {first_day}..{last_day} outside cost coverage {cov_s}..{cov_e}")
        sec_a, sec_b = self._interval(self.sec, first_day, "SEC"), self._interval(self.sec, last_day, "SEC")
        taf_rows = self.raw["finra_taf"]["schedule"]
        taf_a, taf_b = self._interval(taf_rows, first_day, "TAF"), self._interval(taf_rows, last_day, "TAF")
        if sec_a is not sec_b or taf_a is not taf_b:
            raise CostSetError("CO06: a SEC or TAF rate changes inside the window; split the window")
        return {"sec_version": sec_a["version"], "sec_rate": sec_a["rate"], "taf_per_share": taf_a["per_share"]}

    def rates(self, scenario: str, on: date) -> Rates:
        if scenario not in self.raw["scenarios"]:
            raise CostSetError(f"unknown scenario {scenario}")
        sc, c = self.raw["scenarios"][scenario], self.raw["commission"]
        sec = self._interval(self.sec, on, "SEC")
        taf = self._interval(self.raw["finra_taf"]["schedule"], on, "TAF")
        D = lambda x: Decimal(str(x))  # noqa: E731
        return Rates(scenario, D(c["per_share"]), D(c["minimum_per_order"]), D(c["cap_rate_of_notional"]),
                     D(sec["rate"]), sec["version"], D(taf["per_share"]), D(sc["cat_per_share"]), D(sc["slippage_bps"]))


def reference_fill_costs(price: Decimal, qty: int, side: str, r: Rates) -> dict[str, Decimal]:
    """Per-fill reference breakdown, each component rounded HALF_UP to cents (mirrors Core's per-line rounding).

    price = reference (pre-slippage) price. Slippage is reported as a cost: |fill - ref| * qty.
    """
    q, p = Decimal(qty), Decimal(str(price))
    notional = p * q
    slip_px = (p * r.slippage_bps / Decimal(10000))
    fill = p + slip_px if side == "BUY" else p - slip_px
    fill_notional = fill * q
    commission = min(max(q * r.commission_per_share, r.minimum), fill_notional * r.cap_rate)
    rnd = lambda x: x.quantize(CENT, rounding=ROUND_HALF_UP)  # noqa: E731
    return {
        "fill_price": fill,
        "commission": rnd(commission),
        "sec_fee": rnd(fill_notional * r.sec_rate) if side == "SELL" else Decimal("0.00"),
        "taf_fee": rnd(q * r.taf_per_share),
        "cat_fee": rnd(q * r.cat_per_share),
        "slippage_cost": rnd(abs(fill - p) * q),
        "reference_notional": notional,
    }
