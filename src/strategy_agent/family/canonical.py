"""Strategy-family canonicalisation (protocol §6.1).

Goal: an agent must not be able to reset the multiple-testing count by
renaming, re-ordering, re-parameterising or slightly decorating a strategy.

``canonical_structure(spec)`` keeps only what defines the *idea*:
market, timeframe, rationale category, contract policy type, directions,
indicator types/inputs/scopes, rule logic and exit types. It removes or
normalises:

* names, ids, descriptions, versions, status, provenance, experiment block
* parameter names, defaults, ranges, and every numeric / time constant (→ "#")
* indicator ids (references are replaced by the indicator's structural token)
* operand order of commutative ops (and/or sorted, flattened, de-duplicated)
* mirror comparisons (lt(a,b) → gt(b,a); le → ge; cross_below(a,b) → cross_above(b,a))
* costs, sizing and data snapshot (same idea on new data stays in the family)

``FamilyIndex.assign`` then maps a spec to a family:
EXACT (same structure hash) → SIMILAR (same market/timeframe/category and
predicate-set Jaccard ≥ threshold) → NEW. Assignments are stored in a
hash-chained log so they survive restarts and can be audited.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from strategy_agent.registry.chain import ChainedLog

MIRROR = {"lt": "gt", "le": "ge"}
COMMUTATIVE = {"and", "or"}
SIMILARITY_THRESHOLD = 0.7


def _h(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _key(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _indicator_token(ind: dict) -> str:
    return f"{ind['type']}:{ind['input']}:{ind.get('series_scope', '-')}:lag{ind.get('lag', 0)}"


def _operand(o: dict, tokens: dict[str, str]) -> Any:
    if "ref" in o:
        base = tokens.get(o["ref"], f"BAR:{o['ref']}")
        return f"{base}@lag{o.get('lag', 0)}" if o.get("lag") else base
    if "const" in o or "param" in o:
        return "#"
    if "op" in o:
        args = [_operand(a, tokens) for a in o["args"]]
        if o["op"] in {"add", "mul"}:
            args = sorted(args, key=_key)
        return {o["op"]: args}
    raise ValueError(f"unknown operand {o}")


def _condition(c: dict, tokens: dict[str, str]) -> Any:
    op = c["op"]
    if op in COMMUTATIVE:
        flat = []
        for a in c["args"]:
            n = _condition(a, tokens)
            flat.extend(n[op] if isinstance(n, dict) and op in n else [n])
        uniq = {_key(x): x for x in flat}
        return {op: [uniq[k] for k in sorted(uniq)]}
    if op == "not":
        return {"not": _condition(c["args"][0], tokens)}
    if op in ("time_ge", "time_lt"):
        return {op: "T#"}
    if op == "after_range_complete":
        return {op: tokens.get(c.get("indicator", ""), "?")}
    if op in ("is_flat", "is_long", "is_short"):
        return {op: True}
    left, right = _operand(c["left"], tokens), _operand(c["right"], tokens)
    if op in MIRROR:
        op, left, right = MIRROR[op], right, left
    if op == "cross_below":
        op, left, right = "cross_above", right, left
    return {op: [left, right]}


def canonical_structure(spec: dict) -> dict:
    u = spec["universe"]
    tokens = {i["id"]: _indicator_token(i) for i in spec["signal"]["indicators"]}
    rules = [{"action": r["action"], "when": _condition(r["when"], tokens)}
             for r in spec["signal"]["entry_rules"] + spec["signal"]["exit_rules"]]
    rx = spec["risk_exits"]
    return {
        "market": u["market"],
        "timeframe": u["timeframe"],
        "category": spec["hypothesis"]["rationale_category"],
        "contract_policy": u["contract_policy"]["type"],
        "directions": sorted(spec["risk_constraints"]["allowed_directions"]),
        "indicators": sorted(tokens.values()),
        "rules": sorted(rules, key=_key),
        "exits": {"stop": rx["stop_loss"]["type"], "take": rx["take_profit"]["type"],
                  "time": sorted(rx["time_exit"].keys())},
    }


def structure_hash(spec: dict) -> str:
    return _h(canonical_structure(spec))


def _atoms(node: Any, action: str, out: set[str]) -> None:
    if isinstance(node, dict):
        (op, val), = node.items()
        if op in COMMUTATIVE:
            for v in val:
                _atoms(v, action, out)
            return
        out.add(f"{action}|{_key(node)}")
    else:
        out.add(f"{action}|{_key(node)}")


def predicate_atoms(structure: dict) -> set[str]:
    atoms: set[str] = {f"IND|{t}" for t in structure["indicators"]}
    for r in structure["rules"]:
        _atoms(r["when"], r["action"], atoms)
    atoms |= {f"EXIT|{k}:{v}" for k, v in structure["exits"].items() if v not in ("NONE", [])}
    return atoms


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 1.0


@dataclass(frozen=True)
class Assignment:
    family_id: str
    structure_sha256: str
    mode: str          # EXACT | SIMILAR | NEW
    similarity: float
    declared_family_id: str | None


class FamilyIndex:
    def __init__(self, path: str | Path, threshold: float = SIMILARITY_THRESHOLD):
        self.log = ChainedLog(path)
        self.threshold = threshold

    def assign(self, spec: dict) -> Assignment:
        st = canonical_structure(spec)
        sha = _h(st)
        declared = spec.get("experiment", {}).get("trial_family_id")
        entries = self.log.verify()
        for e in entries:
            if e["structure_sha256"] == sha:
                return Assignment(e["family_id"], sha, "EXACT", 1.0, declared)
        atoms = predicate_atoms(st)
        best, best_sim = None, 0.0
        for e in entries:
            if (e["market"], e["timeframe"], e["category"]) != (st["market"], st["timeframe"], st["category"]):
                continue
            sim = jaccard(atoms, set(e["atoms"]))
            if sim > best_sim:
                best, best_sim = e, sim
        if best is not None and best_sim >= self.threshold:
            fam, mode = best["family_id"], "SIMILAR"
        else:
            fam, mode, best_sim = f"FAM-{sha[:12]}", "NEW", best_sim
        self.log.append({"family_id": fam, "structure_sha256": sha, "mode": mode, "similarity": round(best_sim, 6),
                         "market": st["market"], "timeframe": st["timeframe"], "category": st["category"],
                         "atoms": sorted(atoms), "declared_family_id": declared,
                         "strategy_id": spec.get("strategy_id")})
        return Assignment(fam, sha, mode, best_sim, declared)

    def families(self) -> set[str]:
        return {e["family_id"] for e in self.log.verify()}
