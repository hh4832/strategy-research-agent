"""Strategy specification validator: JSON Schema + semantic (cross-field) rules.

Semantic rules are numbered (S01..S16) and documented in
STRATEGY_AGENT_ARCHITECTURE.md §4.3 so the Experiment Registry can store which
rule rejected a spec.
"""
from __future__ import annotations

import copy
import hashlib
import itertools
import json
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

from jsonschema import Draft202012Validator

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "STRATEGY_SPEC_SCHEMA.json"
BAR_FIELDS = {"open", "high", "low", "close", "volume"}
FUTURES_SYMBOLS = {"IBKR": {"NQ", "ES"}, "TXF": {"TXF"}}
WILDER_LIKE = {"EMA", "ATR_WILDER"}


@dataclass(frozen=True)
class Issue:
    level: str   # ERROR | WARNING
    rule: str
    path: str
    message: str

    def __str__(self) -> str:
        return f"[{self.level}] {self.rule} {self.path}: {self.message}"


def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def canonical_hash(spec: dict) -> str:
    s = copy.deepcopy(spec)
    s.setdefault("provenance", {})["spec_sha256"] = None
    blob = json.dumps(s, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


# ------------------------------------------------------------------ walkers


def _walk(obj: Any, path: str = "") -> Iterator[tuple[str, Any]]:
    yield path, obj
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk(v, f"{path}/{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk(v, f"{path}/{i}")


def _param_value(v: Any, params: dict, which: str) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, dict) and "param" in v and v["param"] in params:
        return float(params[v["param"]][which])
    return None


def _grid(p: dict) -> list[Decimal]:
    lo, hi = Decimal(str(p["min"])), Decimal(str(p["max"]))
    step = Decimal(str(p.get("step", 0) or 0))
    if step == 0:
        return [lo, hi]
    n = int((hi - lo) / step)
    return [lo + i * step for i in range(n + 1)]


# -------------------------------------------------------------------- rules


def semantic_issues(spec: dict) -> list[Issue]:
    out: list[Issue] = []
    E = lambda r, p, m: out.append(Issue("ERROR", r, p, m))  # noqa: E731
    W = lambda r, p, m: out.append(Issue("WARNING", r, p, m))  # noqa: E731

    params: dict = spec["parameters"]["values"]
    indicators = {i["id"]: i for i in spec["signal"]["indicators"]}
    uni = spec["universe"]

    # S01 parameter defaults inside range, integer typing, step alignment
    for name, p in params.items():
        if not (p["min"] <= p["default"] <= p["max"]):
            E("S01", f"/parameters/values/{name}", "default outside [min, max]")
        if p["min"] > p["max"]:
            E("S01", f"/parameters/values/{name}", "min > max")
        if p["type"] == "integer" and any(float(p[k]) != int(p[k]) for k in ("default", "min", "max")):
            E("S01", f"/parameters/values/{name}", "integer parameter has non-integer bound/default")
        if "step" in p:
            off = (Decimal(str(p["default"])) - Decimal(str(p["min"]))) % Decimal(str(p["step"]))
            if off != 0:
                W("S01", f"/parameters/values/{name}", "default not on the step grid")

    # S02 parameter constraints: names exist; default satisfies; at least one grid point feasible
    ops = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b,
           ">=": lambda a, b: a >= b, "!=": lambda a, b: a != b}
    for i, c in enumerate(spec["parameters"]["constraints"]):
        names = [x for x in (c["left"], c["right"]) if isinstance(x, str)]
        missing = [n for n in names if n not in params]
        if missing:
            E("S02", f"/parameters/constraints/{i}", f"unknown parameter(s) {missing}")
            continue
        val = lambda x, k="default": Decimal(str(params[x][k])) if isinstance(x, str) else Decimal(str(x))  # noqa: E731
        if not ops[c["op"]](val(c["left"]), val(c["right"])):
            E("S02", f"/parameters/constraints/{i}", "defaults violate constraint")
        lg = _grid(params[c["left"]]) if isinstance(c["left"], str) else [val(c["left"])]
        rg = _grid(params[c["right"]]) if isinstance(c["right"], str) else [val(c["right"])]
        infeasible = sum(1 for a, b in itertools.product(lg, rg) if not ops[c["op"]](a, b))
        if infeasible:
            W("S02", f"/parameters/constraints/{i}",
              f"{infeasible} grid combinations violate the constraint and must be skipped (not counted as trials)")

    # S03 every {param:x} and {ref:x} resolves
    used_params: set[str] = set()
    for path, node in _walk(spec):
        if isinstance(node, dict) and set(node) == {"param"}:
            used_params.add(node["param"])
            if node["param"] not in params:
                E("S03", path, f"unknown parameter '{node['param']}'")
        if isinstance(node, dict) and "ref" in node and isinstance(node.get("ref"), str) and path.startswith("/signal"):
            if node["ref"] not in indicators and node["ref"] not in BAR_FIELDS:
                E("S03", path, f"unknown reference '{node['ref']}'")
        if isinstance(node, dict) and node.get("op") == "after_range_complete":
            if node.get("indicator") not in indicators:
                E("S03", path, "after_range_complete needs a valid opening-range indicator")
    # constraints alone do not count as use: a parameter that only appears in a
    # constraint changes nothing in the strategy yet would multiply the trial grid.
    for name in params:
        if name not in used_params:
            W("S03", f"/parameters/values/{name}", "parameter declared but never used (silent trial inflation risk)")

    # S04 warm-up covers the largest lookback at the MAX of each parameter range
    need = 0
    for ind in indicators.values():
        period = _param_value(ind["params"].get("period"), params, "max")
        lag = ind.get("lag", 0)
        if ind["type"] in {"OPENING_RANGE_HIGH", "OPENING_RANGE_LOW"}:
            if ind.get("series_scope") != "PER_SESSION":
                E("S04", f"/signal/indicators/{ind['id']}", "opening-range indicators must be PER_SESSION")
            continue
        if period is None:
            if ind["type"] not in {"PRICE", "TRUE_RANGE"}:
                E("S04", f"/signal/indicators/{ind['id']}", "missing period")
            need = max(need, lag + 1)
            continue
        look = int(period) + lag + (1 if ind["type"] == "ATR_WILDER" else 0)
        need = max(need, look)
        if ind["type"] in WILDER_LIKE and spec["data_requirements"]["warmup_bars"] < 3 * int(period):
            W("S04", f"/signal/indicators/{ind['id']}", "recursive indicator: warm-up < 3x period, early values not converged")
    if spec["data_requirements"]["warmup_bars"] < need:
        E("S04", "/data_requirements/warmup_bars", f"warm-up {spec['data_requirements']['warmup_bars']} < required {need}")

    # S05 cost sensitivity grid must include 0, 1 and >= 2
    m = spec["costs"]["sensitivity_multipliers"]
    if 0 not in m or 1 not in m or max(m) < 2:
        E("S05", "/costs/sensitivity_multipliers", "must include 0, 1 and a value >= 2")

    # S06 futures need explicit contract handling (no inference)
    market, symbols = uni["market"], set(uni["symbols"])
    cp = uni["contract_policy"]
    if symbols & FUTURES_SYMBOLS.get(market, set()):
        if cp["type"] == "NOT_APPLICABLE":
            E("S06", "/universe/contract_policy", "futures require EXPLICIT_CONTRACTS, SCHEDULED_ROLL or PRODUCER_CONTINUOUS_WITH_ACTUAL_MONTH")
        if market == "IBKR" and cp["type"] == "PRODUCER_CONTINUOUS_WITH_ACTUAL_MONTH":
            E("S06", "/universe/contract_policy", "IBKR NQ/ES snapshots are per-contract; no producer continuous series exists")
        if cp.get("indicator_series") != "PER_CONTRACT":
            E("S06", "/universe/contract_policy/indicator_series", "futures indicators must be PER_CONTRACT")
    if cp["type"] == "SCHEDULED_ROLL" and not cp.get("roll_schedule"):
        E("S06", "/universe/contract_policy", "SCHEDULED_ROLL needs an explicit roll_schedule")
    if cp["type"] == "EXPLICIT_CONTRACTS" and not cp.get("contracts"):
        E("S06", "/universe/contract_policy", "EXPLICIT_CONTRACTS needs contracts")
    if cp["type"] == "PRODUCER_CONTINUOUS_WITH_ACTUAL_MONTH" and not cp.get("no_position_across_contract_change"):
        E("S06", "/universe/contract_policy", "must forbid positions across actual_contract_month changes")

    # S07 TXF formal snapshot is a bounded, non-contiguous sample -> intraday only
    if market == "TXF" and not uni["sessions"].get("no_overnight"):
        E("S07", "/universe/sessions/no_overnight", "TXF bounded sample (185 sampled dates) cannot support overnight holding")

    # S08 Taiwan equity: closing auction fills are not approved
    if market == "TW" and not uni["sessions"].get("exclude_closing_auction", True):
        E("S08", "/universe/sessions", "13:30 closing auction is execution-ineligible in the TW snapshot")

    # S09 SCOPED_GO / caveated datasets must carry their exclusions
    for i, d in enumerate(spec["data_requirements"]["datasets"]):
        if d["verdict"] in {"SCOPED_GO", "PASS_WITH_CAVEATS"} and not d.get("mandatory_exclusions"):
            E("S09", f"/data_requirements/datasets/{i}", f"{d['verdict']} dataset must list mandatory_exclusions")
        if d["usage_scope"] == "RESEARCH_ONLY":
            E("S09", f"/data_requirements/datasets/{i}", "RESEARCH_ONLY data cannot back a backtest spec")

    # S10 directions consistent with rules
    allowed = set(spec["risk_constraints"]["allowed_directions"])
    for r in spec["signal"]["entry_rules"]:
        if r["action"] == "ENTER_SHORT" and "SHORT" not in allowed:
            E("S10", f"/signal/entry_rules/{r['id']}", "short entry but SHORT not allowed")
        if r["action"] == "ENTER_LONG" and "LONG" not in allowed:
            E("S10", f"/signal/entry_rules/{r['id']}", "long entry but LONG not allowed")

    # S11 non-OHLCV fields need PIT availability rules
    pit = {p["field"] for p in spec["data_requirements"].get("pit_fields", [])}
    extra = set(spec["data_requirements"].get("fields", [])) - BAR_FIELDS - {"actual_contract_month"}
    for f in sorted(extra - pit):
        E("S11", "/data_requirements/fields", f"field '{f}' has no available_at_rule (PIT)")

    # S12 exits exist: every strategy must be able to go flat
    rx = spec["risk_exits"]
    has_exit = bool(spec["signal"]["exit_rules"]) or rx["stop_loss"]["type"] != "NONE" or \
        rx["take_profit"]["type"] != "NONE" or bool(rx["time_exit"])
    if not has_exit:
        E("S12", "/risk_exits", "no exit path: add exit_rules, stop/take-profit or time_exit")

    # S13 ATR-based exits must reference an ATR indicator computed with lag >= 1
    for key in ("stop_loss", "take_profit"):
        lvl = rx[key]
        if lvl["type"] == "ATR_MULTIPLE":
            ind = indicators.get(lvl.get("atr_indicator", ""))
            if not ind or ind["type"] != "ATR_WILDER":
                E("S13", f"/risk_exits/{key}", "ATR_MULTIPLE needs atr_indicator of type ATR_WILDER")
        if lvl["type"] == "RANGE_OPPOSITE_SIDE" and not any(
                i["type"].startswith("OPENING_RANGE") for i in indicators.values()):
            E("S13", f"/risk_exits/{key}", "RANGE_OPPOSITE_SIDE needs opening-range indicators")

    # S14 aggregation declared iff timeframe differs from source
    if uni.get("source_timeframe") and uni["source_timeframe"] != uni["timeframe"] and "aggregation" not in uni:
        E("S14", "/universe/aggregation", "aggregated bars need an explicit aggregation rule")

    # S15 search discipline
    s = spec["parameters"]["search"]
    if spec["status"] in {"REGISTERED", "FROZEN"} and s["mode"] != "FIXED" and "max_trials" not in s:
        E("S15", "/parameters/search", "PREREGISTERED_GRID requires max_trials")

    # S16 pinning for non-draft specs
    if spec["status"] != "DRAFT":
        for path, node in _walk(spec):
            if node == "UNRESOLVED":
                E("S16", path, "UNRESOLVED value in a non-DRAFT spec")
        declared = spec["provenance"].get("spec_sha256")
        if declared is None or declared != canonical_hash(spec):
            E("S16", "/provenance/spec_sha256", "missing or wrong spec hash for a non-DRAFT spec")

    return out


def validate(spec: dict) -> list[Issue]:
    schema_errors = sorted(Draft202012Validator(load_schema()).iter_errors(spec), key=lambda e: list(e.path))
    if schema_errors:
        return [Issue("ERROR", "SCHEMA", "/" + "/".join(map(str, e.path)), e.message) for e in schema_errors]
    return semantic_issues(spec)


def is_valid(spec: dict) -> bool:
    return not any(i.level == "ERROR" for i in validate(spec))


def main(argv: list[str]) -> int:
    rc = 0
    for p in argv:
        spec = json.loads(Path(p).read_text(encoding="utf-8"))
        issues = validate(spec)
        errors = [i for i in issues if i.level == "ERROR"]
        print(f"{'FAIL' if errors else 'OK  '} {p}  sha256={canonical_hash(spec)[:16]}")
        for i in issues:
            print(f"     {i}")
        rc |= bool(errors)
    return rc


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
