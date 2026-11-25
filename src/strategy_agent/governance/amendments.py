"""Phase 5-C2 C2-3 (W-6): post-data amendment governance with a bounded budget.

A *post-data amendment* is any change to a case's strategy, execution, quantity or cost rules made after that case's
data results were seen. Each amendment is declared in the authorization that will run it (optional ``amendment``
block; legacy authorizations without it are case-initial runs). Rules (Haw, 2026-11-25, decision D4):

* at most ``MAX_POST_DATA_AMENDMENTS`` (2) per case; the next change needs a new case;
* index 0 = the case's initial plan; index k > 0 must name the authorization it amends (same case), the result
  SHA-256 that triggered it, what changes, and a stop condition stated before the run;
* every amendment caps the evidence level at EXPLORATORY_ONLY;
* fixes to Core or the framework (e.g. Core 76902a1) are not amendments; they are recorded as
  ``framework_fix`` and do not consume budget, but must still name the triggering result.

Pure functions; see docs/governance/AMENDMENT_GOVERNANCE.md.
"""
from __future__ import annotations

import re

MAX_POST_DATA_AMENDMENTS = 2
KINDS = ("CASE_INITIAL", "POST_DATA_AMENDMENT", "FRAMEWORK_FIX")
_SHA = re.compile(r"^[0-9a-f]{64}$")


class AmendmentRefused(ValueError):
    pass


def problems(amendment: dict, prior: list[dict]) -> list[str]:
    """``amendment`` of a new authorization; ``prior`` = amendment blocks of earlier authorizations (any case),
    each with ``authorization_id`` added. Returns reasons to refuse (empty = allowed)."""
    out = []
    kind, case, idx = amendment.get("kind"), amendment.get("case_id"), amendment.get("index")
    if kind not in KINDS:
        return [f"unknown amendment kind {kind!r}"]
    if not case:
        out.append("case_id required")
    same = [p for p in prior if p.get("case_id") == case]
    used = sum(1 for p in same if p.get("kind") == "POST_DATA_AMENDMENT")
    if kind == "CASE_INITIAL":
        if same:
            out.append(f"case {case} already has authorizations; a later run is an amendment or a framework fix")
        if idx != 0:
            out.append("CASE_INITIAL must have index 0")
        return out
    if not same:
        out.append(f"{kind} for case {case} without a prior authorization of that case")
    if amendment.get("amendment_of") not in {p.get("authorization_id") for p in same}:
        out.append("amendment_of must name an earlier authorization of the same case")
    if not _SHA.match(str(amendment.get("trigger_result_sha256", ""))):
        out.append("trigger_result_sha256 (the result that prompted the change) is required")
    if not str(amendment.get("change", "")).strip():
        out.append("change (what is changed, and only that) is required")
    if kind == "POST_DATA_AMENDMENT":
        if idx != used + 1:
            out.append(f"index must be {used + 1} (post-data amendments already used: {used})")
        if used + 1 > MAX_POST_DATA_AMENDMENTS:
            out.append(f"amendment budget exhausted: case {case} already used {used} of "
                       f"{MAX_POST_DATA_AMENDMENTS}; close the case or open a new one")
        if not str(amendment.get("stop_condition", "")).strip():
            out.append("stop_condition (stated before the run) is required")
        if amendment.get("evidence_level") != "EXPLORATORY_ONLY":
            out.append("evidence_level must be EXPLORATORY_ONLY after a post-data amendment")
    elif idx != used:
        out.append(f"FRAMEWORK_FIX keeps the current index ({used}); it does not consume budget")
    return out


def check_series(amendments: list[dict]) -> None:
    """Validate amendment blocks in authorization order (each with ``authorization_id``); raise on the first problem."""
    seen: list[dict] = []
    for a in amendments:
        p = problems(a, seen)
        if p:
            raise AmendmentRefused(f"{a.get('authorization_id')}: {'; '.join(p)}")
        seen.append(a)
