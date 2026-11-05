"""Cross-environment reproducibility (M3-C N1).

Three different claims, never conflated:

1. **Numeric reproducibility** - two runs of the same identity agree on every metric within a tolerance fixed
   *here, before any comparison* (``NUMERIC_TOLERANCE``). Integers, strings, booleans, None and the dict/list
   structure must match exactly; only floats get the tolerance. A real performance difference (one cent of PnL on a
   100k account is ~1e-7 of capital; one trade is integer) is far outside it and fails.
2. **Archive integrity** - each archive is checked against ITSELF: manifest sha256 of every file, and the stored
   ``metrics_sha256`` equals the hash of the stored metrics. This must hold exactly, in every environment.
3. **Bitwise cross-environment reproducibility** - equal ``metrics_sha256`` across environments. It is reported
   as ``IDENTICAL`` when it happens and ``NOT_GUARANTEED`` otherwise: float summation order / libm differences
   between Python builds can move the last bit (observed: M3-C Phase A N1). It is never forced - stored metrics are
   never rewritten, rounded or re-hashed to make two files agree.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

# Fixed before comparison (M3-C N1). Relative 1e-9 / absolute 1e-9: ~6 orders of magnitude above the 1-ULP
# float noise seen across Python builds (2.2e-16 relative), ~2 orders below one cent on a 100k-capital run.
NUMERIC_TOLERANCE = {"rel_tol": 1e-9, "abs_tol": 1e-9}


def _walk(a: Any, b: Any, path: str, out: list, tol: dict) -> None:
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append({"path": f"{path}.{k}", "kind": "STRUCTURE", "a": a.get(k, "<absent>"),
                            "b": b.get(k, "<absent>")})
            else:
                _walk(a[k], b[k], f"{path}.{k}", out, tol)
        return
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append({"path": path, "kind": "STRUCTURE", "a": f"len {len(a)}", "b": f"len {len(b)}"})
            return
        for i, (x, y) in enumerate(zip(a, b)):
            _walk(x, y, f"{path}[{i}]", out, tol)
        return
    fa, fb = isinstance(a, float), isinstance(b, float)
    if (fa or fb) and isinstance(a, (int, float)) and isinstance(b, (int, float)) and \
            not isinstance(a, bool) and not isinstance(b, bool):
        if a == b:
            return
        if math.isfinite(a) and math.isfinite(b) and math.isclose(a, b, **tol):
            out.append({"path": path, "kind": "WITHIN_TOLERANCE", "a": a, "b": b, "abs_diff": abs(a - b),
                        "rel_diff": abs(a - b) / max(abs(a), abs(b))})
        else:
            out.append({"path": path, "kind": "NUMERIC", "a": a, "b": b})
        return
    if a != b or type(a) is not type(b):
        out.append({"path": path, "kind": "EXACT", "a": a, "b": b})


def compare_metrics(a: dict, b: dict, *, sha_a: str | None = None, sha_b: str | None = None,
                    tolerance: dict | None = None, schema_additions: dict | None = None) -> dict:
    """``schema_additions``: {path: required_value} for fields that exist only in ``b`` because ``b`` was produced
    by a newer metrics schema (e.g. ``metrics.costs.regulatory_fees`` from Core M3-A.4). Each must be declared and
    carry exactly the stated value; anything else structural still fails."""
    tol = dict(NUMERIC_TOLERANCE if tolerance is None else tolerance)
    diffs: list = []
    _walk(a, b, "metrics", diffs, tol)
    adds = schema_additions or {}
    declared = [d for d in diffs if d["kind"] == "STRUCTURE" and d["a"] == "<absent>" and d["path"] in adds
                and d["b"] == adds[d["path"]]]
    diffs = [d for d in diffs if d not in declared]
    hard = [d for d in diffs if d["kind"] != "WITHIN_TOLERANCE"]
    soft = [d for d in diffs if d["kind"] == "WITHIN_TOLERANCE"]
    bitwise = "IDENTICAL" if not diffs and not declared and (sha_a is None or sha_a == sha_b) else "NOT_GUARANTEED"
    return {"numeric_reproducibility": "PASS" if not hard else "FAIL", "tolerance": tol,
            "bitwise_cross_environment": bitwise, "metrics_sha256": [sha_a, sha_b],
            "within_tolerance": soft, "differences": hard, "declared_schema_additions": declared,
            "max_abs_diff": max((d["abs_diff"] for d in soft), default=0.0),
            "max_rel_diff": max((d["rel_diff"] for d in soft), default=0.0)}


def archive_integrity(archive: Path) -> dict:
    """Self-consistency of one archive (exact): manifest file hashes and stored metrics_sha256."""
    from .archive import ArchiveError, verify_manifest  # noqa: PLC0415
    from .pipeline import sha256_of  # noqa: PLC0415
    archive = Path(archive)
    out = {"archive": str(archive)}
    try:
        verify_manifest(archive)
        out["manifest"] = "PASS"
    except (ArchiveError, OSError, ValueError) as e:
        out["manifest"] = f"FAIL: {e}"
    m = json.loads((archive / "metrics.json").read_text(encoding="utf-8"))
    out["metrics_sha256_stored"] = m.get("metrics_sha256")
    out["metrics_sha256_recomputed"] = sha256_of(m.get("metrics"))
    out["metrics_self_hash"] = "PASS" if out["metrics_sha256_stored"] == out["metrics_sha256_recomputed"] else "FAIL"
    out["status"] = "PASS" if out["manifest"] == "PASS" and out["metrics_self_hash"] == "PASS" else "FAIL"
    return out


def compare_archives(a: Path, b: Path, tolerance: dict | None = None, schema_additions: dict | None = None) -> dict:
    ia, ib = archive_integrity(a), archive_integrity(b)
    ma = json.loads((Path(a) / "metrics.json").read_text(encoding="utf-8"))
    mb = json.loads((Path(b) / "metrics.json").read_text(encoding="utf-8"))
    cmp = compare_metrics(ma["metrics"], mb["metrics"], sha_a=ma.get("metrics_sha256"), sha_b=mb.get("metrics_sha256"),
                          tolerance=tolerance, schema_additions=schema_additions)
    return {"archive_integrity": [ia, ib], **cmp}
