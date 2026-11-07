"""Research Request contract (schemas/RESEARCH_REQUEST_SCHEMA.json, schema_version 1.0.0).

Structural validation (JSON Schema) + the few semantic rules a schema cannot express. A valid request is only
well-formed: it grants nothing. Governance facts are read from governed records by ``governance.py``.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = ROOT / "schemas" / "RESEARCH_REQUEST_SCHEMA.json"
PENDABLE = ("core_commit", "authorization_ref")


def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def request_hash(doc: dict) -> str:
    """Identity of a request for de-duplication: sha256 of its canonical JSON."""
    return hashlib.sha256(canonical(doc)).hexdigest()


def pending_fields(doc: dict) -> list[str]:
    return [k for k in PENDABLE if doc.get(k) == "PENDING"]


def validate(doc) -> list[str]:
    """Return a list of error strings; empty means well-formed. Never raises on bad input."""
    if not isinstance(doc, dict):
        return ["request must be a JSON object"]
    errs = [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
            for e in sorted(Draft202012Validator(schema()).iter_errors(doc), key=lambda e: list(map(str, e.absolute_path)))]
    if errs:
        return errs
    w = doc["research_window"]
    try:
        wu, ws, we = (datetime.fromisoformat(w[k].replace("Z", "+00:00")) for k in ("warmup_start", "start", "end"))
    except ValueError as e:
        return [f"research_window: {e}"]
    if not (wu <= ws < we):
        errs.append("research_window: require warmup_start <= start < end")
    return errs


def load(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
