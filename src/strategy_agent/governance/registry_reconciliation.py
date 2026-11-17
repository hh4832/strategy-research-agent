"""Registry reconciliation: carry a settled execution's ledger records from the EXECUTE governance state into the
repository, byte for byte, append-only, idempotent and fail-closed.

    python -m strategy_agent.governance.registry_reconciliation --state <EXECUTE state dir> \\
        --authorization-id RA-... --run-id ... --task-id T-... --request-sha256 ... --output-manifest-sha256 ... \\
        --claim-refs <ls-remote output> --remote-claim <REMOTE_CLAIM.json> [--sibling-state <dir> ...] [--apply]

Nothing is generated, re-chained or renumbered: the new bytes are exactly the state's bytes after the repository's
bytes (append_only CI guard semantics), and every new record must belong to the one settled authorization.
Checks (plan): chain integrity on both sides with the existing record_hash algorithm; repository bytes and records
are an exact prefix of the state; all other governed files byte-identical (holdout locks, authorizations, market
readiness, proposals ...); new records only for the authorization; uses == 1 <= max_batches with the expected run;
budget fold (existing TRANSITIONS/MARKERS rules) ends CONSUMED with the full lifecycle, the committed use record,
identical claim identity and the expected output manifest; the binding's bound request / approval evidence exist
with matching SHA; exactly one remote CLAIM ref for the authorization, matching REMOTE_CLAIM.json.
``--sibling-state`` directories are only REPORTED (forks off the same repository prefix are never merged here).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from strategy_agent.execution.budget import CLAIM_IDENTITY, BudgetRefused, _fold
from strategy_agent.execution.c1_context import claim_ref
from strategy_agent.registry.chain import GENESIS, record_hash

LEDGERS = ("registry/research_authorization_uses.jsonl", "registry/research_budget_events.jsonl",
           "registry/authorization_bindings.jsonl")
USES, BUDGET, BINDINGS = LEDGERS
CONTENT_DIRS = ("registry/bound_requests", "registry/approval_evidence")
GOVERNED = ("governance", "registry")
LIFECYCLE = ["RESERVED", "COMMITTED", "EXECUTION_STARTED", "EXECUTION_COMPLETED", "EVIDENCE_VERIFIED", "CONSUMED"]


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _files(root: Path) -> dict[str, bytes]:
    out = {}
    for top in GOVERNED:
        for p in sorted((root / top).rglob("*")):
            if p.is_symlink():
                raise ValueError(f"symlink in governed tree: {p}")
            if p.is_file():
                out[p.relative_to(root).as_posix()] = p.read_bytes()
    return out


def parse_chain(data: bytes, label: str) -> tuple[list[dict], list[str]]:
    """Records + problems, using the repository's own chain algorithm (seq, prev_sha256, record_sha256)."""
    recs, problems, prev = [], [], GENESIS
    if data and not data.endswith(b"\n"):
        problems.append(f"{label}: no trailing newline")
    for i, line in enumerate(data.decode("utf-8").splitlines()):
        if not line.strip():
            problems.append(f"{label}: blank line at {i}")
            continue
        r = json.loads(line)
        if r.get("seq") != i or r.get("prev_sha256") != prev or r.get("record_sha256") != record_hash(r):
            problems.append(f"{label}: CHAIN_BROKEN at seq {i}")
            return recs, problems
        prev = r["record_sha256"]
        recs.append(r)
    return recs, problems


def _max_batches(files: dict[str, bytes], authorization_id: str) -> int | None:
    recs, _ = parse_chain(files.get("governance/research_authorizations.jsonl", b""), "authorizations")
    a = [r for r in recs if r.get("authorization_id") == authorization_id and r.get("action") == "APPROVE"]
    return a[-1].get("max_batches") if a else None


def plan(repo_root, state_root, *, authorization_id: str, run_id: str, task_id: str, request_sha256: str,
         output_manifest_sha256: str, claim_refs: list[str] | None, remote_claim: dict | None,
         sibling_states: list | None = None) -> dict:
    repo, state = _files(Path(repo_root)), _files(Path(state_root))
    problems: list[str] = []
    rep: dict = {"authorization_id": authorization_id, "ledgers": {}, "new_files": [], "forks": []}

    # 1. every other governed file identical; nothing missing on the state side
    for f in sorted(set(repo) | set(state)):
        if f in LEDGERS:
            continue
        if f not in state:
            problems.append(f"STATE_MISSING_FILE:{f}")
        elif f not in repo:
            if not any(f.startswith(d + "/") for d in CONTENT_DIRS):
                problems.append(f"UNEXPECTED_NEW_FILE:{f}")
            else:
                rep["new_files"].append(f)
        elif repo[f] != state[f]:
            problems.append(f"GOVERNED_FILE_DIFFERS:{f}")

    # 2. ledgers: chain on both sides, byte + record prefix, new records in scope
    new: dict[str, list[dict]] = {}
    merged: dict[str, list[dict]] = {}
    for f in LEDGERS:
        rb, sb = repo.get(f, b""), state.get(f)
        if sb is None:
            problems.append(f"STATE_MISSING_LEDGER:{f}")
            continue
        rr, p1 = parse_chain(rb, f"repo:{f}")
        sr, p2 = parse_chain(sb, f"state:{f}")
        problems += p1 + p2
        prefix_bytes = sb.startswith(rb)
        prefix_recs = sr[:len(rr)] == rr
        if not (prefix_bytes and prefix_recs):
            problems.append(f"REPO_NOT_PREFIX_OF_STATE:{f}")
        add = sr[len(rr):] if prefix_bytes and prefix_recs else []
        for r in add:
            if r.get("authorization_id") != authorization_id:
                problems.append(f"OUT_OF_SCOPE_RECORD:{f}:seq {r.get('seq')}:{r.get('authorization_id')}")
        new[f], merged[f] = add, sr
        rep["ledgers"][f] = {"repo_records": len(rr), "state_records": len(sr), "new_seqs": [r["seq"] for r in add],
                             "repo_head": rr[-1]["record_sha256"] if rr else GENESIS,
                             "state_head": sr[-1]["record_sha256"] if sr else GENESIS,
                             "repo_file_sha256": _sha(rb), "state_file_sha256": _sha(sb)}
    if problems:
        return {**rep, "status": "FAIL", "problems": problems}

    # 3. authorization uses: exactly one, for this run, within max_batches
    uses = [u for u in merged[USES] if u.get("authorization_id") == authorization_id]
    mb = _max_batches(state, authorization_id)
    rep["authorization"] = {"uses": len(uses), "max_batches": mb, "run_ids": [u.get("run_id") for u in uses]}
    if mb is None:
        problems.append("AUTHORIZATION_NOT_APPROVED")
    if len(uses) != 1 or (mb is not None and len(uses) > mb):
        problems.append(f"USE_COUNT:{len(uses)}/{mb}")
    if any(u.get("run_id") != run_id for u in uses):
        problems.append("USE_RUN_ID_MISMATCH")
    use = uses[0] if len(uses) == 1 else {}

    # 4. budget settlement
    try:
        folded = _fold(merged[BUDGET])
    except BudgetRefused as e:
        problems.append(f"BUDGET_ILLEGAL_TRANSITION:{e}")
        folded = {}
    res = [r for r in folded.values() if r.get("authorization_id") == authorization_id]
    ev = [e for e in merged[BUDGET] if e.get("authorization_id") == authorization_id
          or (res and e.get("reservation_id") == res[0]["reservation_id"])]
    if len(res) != 1:
        problems.append(f"RESERVATION_COUNT:{len(res)}")
    else:
        r = res[0]
        rep["budget"] = {"reservation_id": r["reservation_id"], "state": r["state"], "history": r["history"]}
        if r["state"] != "CONSUMED" or r["history"] != LIFECYCLE:
            problems.append(f"BUDGET_NOT_SETTLED:{r['state']}:{r['history']}")
        if (r.get("task_id"), r.get("request_sha256"), r.get("planned_run_id")) != (task_id, request_sha256, run_id):
            problems.append("BUDGET_IDENTITY_MISMATCH")
        for k in CLAIM_IDENTITY:
            vals = {e.get(k) for e in ev if k in e}
            if len(vals) > 1:
                problems.append(f"BUDGET_CLAIM_IDENTITY_DRIFT:{k}")
        com = next((e for e in ev if e["event_type"] == "COMMITTED"), {})
        if com.get("use_record_sha256") != use.get("record_sha256") or com.get("use_seq") != use.get("seq"):
            problems.append("BUDGET_USE_RECORD_MISMATCH")
        mans = {e.get("output_manifest_sha256") for e in ev if e["event_type"] in
                ("EXECUTION_COMPLETED", "EVIDENCE_VERIFIED", "CONSUMED")}
        if mans != {output_manifest_sha256}:
            problems.append(f"OUTPUT_MANIFEST_MISMATCH:{sorted(map(str, mans))}")
        recs = {e.get("receipt_sha256") for e in ev if e["event_type"] in
                ("EXECUTION_COMPLETED", "EVIDENCE_VERIFIED", "CONSUMED")}
        if len(recs) != 1 or None in recs:
            problems.append("RECEIPT_SHA_DRIFT")
        rep["budget"] |= {"output_manifest_sha256": sorted(map(str, mans)), "receipt_sha256": sorted(map(str, recs))}

    # 5. binding and content-addressed evidence
    b = [x for x in merged[BINDINGS] if x.get("authorization_id") == authorization_id]
    if len(b) != 1:
        problems.append(f"BINDING_COUNT:{len(b)}")
    else:
        b = b[0]
        if res and b.get("binding_id") != res[0].get("binding_id"):
            problems.append("BINDING_ID_MISMATCH")
        for path_k, sha_k in (("bound_request_path", "bound_request_file_sha256"),
                              ("approval_evidence_path", "approval_evidence_sha256")):
            data = state.get(b.get(path_k, ""))
            if data is None or _sha(data) != b.get(sha_k):
                problems.append(f"BINDING_FILE:{path_k}")
        if Path(b.get("bound_request_path", "")).stem != request_sha256:
            problems.append("BOUND_REQUEST_NAME_MISMATCH")
        if res and b.get("bound_request_file_sha256") != res[0].get("request_file_sha256"):
            problems.append("BOUND_REQUEST_FILE_VS_BUDGET")
        wanted = {b.get("bound_request_path"), b.get("approval_evidence_path")}
        for f in rep["new_files"]:
            if f not in wanted:
                problems.append(f"UNREFERENCED_NEW_FILE:{f}")

    # 6. remote CLAIM: exactly one ref for this authorization, consistent with the claim record
    want = claim_ref(authorization_id)
    if claim_refs is None or remote_claim is None:
        problems.append("CLAIM_EVIDENCE_MISSING")
    else:
        hits = [c for c in claim_refs if c == want]
        if len(claim_refs) != len(set(claim_refs)):
            problems.append("CLAIM_REF_DUPLICATED")
        if len(hits) != 1:
            problems.append(f"CLAIM_REF_COUNT:{len(hits)}")
        if (remote_claim.get("authorization_id"), remote_claim.get("claim_ref"), remote_claim.get("request_sha256")) \
                != (authorization_id, want, request_sha256):
            problems.append("REMOTE_CLAIM_MISMATCH")
        rep["claim"] = {"ref": want, "refs_for_authorization": len(hits), "all_refs": len(claim_refs),
                        "governance_sha": remote_claim.get("governance_sha")}

    # 7. informational: sibling states forking off the same repository prefix (never merged)
    for sib in sibling_states or []:
        sf = _files(Path(sib))
        for f in LEDGERS:
            if f not in sf:
                continue
            sr, sp = parse_chain(sf[f], f"sibling:{f}")
            rr, _ = parse_chain(repo.get(f, b""), "repo")
            k = next((i for i in range(min(len(sr), len(merged[f]))) if sr[i] != merged[f][i]),
                     min(len(sr), len(merged[f])))
            if k < len(sr):
                rep["forks"].append({"sibling": str(sib), "ledger": f, "chain_ok": not sp,
                                     "repo_is_prefix": sr[:len(rr)] == rr, "diverges_at_seq": k,
                                     "sibling_only": [{"seq": r["seq"], "authorization_id": r.get("authorization_id"),
                                                       "event_type": r.get("event_type"), "run_id": r.get("run_id"),
                                                       "record_sha256": r["record_sha256"]} for r in sr[k:]]})
    return {**rep, "status": "FAIL" if problems else "PASS", "problems": problems,
            "nothing_to_add": all(not v for v in new.values()) and not rep["new_files"]}


def apply(repo_root, state_root, **kw) -> dict:
    """Plan, then append exactly the state's suffix bytes and copy referenced new files (O_EXCL). Idempotent."""
    p = plan(repo_root, state_root, **kw)
    if p["status"] != "PASS":
        raise RuntimeError("RECONCILIATION_REFUSED: " + "; ".join(p["problems"]))
    if p["nothing_to_add"]:
        return {**p, "applied": False}
    repo, state = Path(repo_root), Path(state_root)
    for f in LEDGERS:
        old, full = (repo / f).read_bytes(), (state / f).read_bytes()
        suffix = full[len(old):]
        if suffix:
            with open(repo / f, "ab") as fh:
                fh.write(suffix)
                fh.flush()
                os.fsync(fh.fileno())
    for f in p["new_files"]:
        (repo / f).parent.mkdir(parents=True, exist_ok=True)
        with open(repo / f, "xb") as fh:
            fh.write((state / f).read_bytes())
    after = plan(repo_root, state_root, **kw)
    if after["status"] != "PASS" or not after["nothing_to_add"]:
        raise RuntimeError("POST_APPLY_VERIFY_FAILED: " + "; ".join(after["problems"]))
    return {**p, "applied": True, "after": after["ledgers"]}


def _refs(path: Path) -> list[str]:
    return [ln.split()[-1] for ln in path.read_text().splitlines() if ln.strip()]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=Path("."))
    ap.add_argument("--state", type=Path, required=True)
    for k in ("authorization-id", "run-id", "task-id", "request-sha256", "output-manifest-sha256"):
        ap.add_argument("--" + k, required=True)
    ap.add_argument("--claim-refs", type=Path, required=True, help="git ls-remote origin 'refs/heads/research-claims/*'")
    ap.add_argument("--remote-claim", type=Path, required=True)
    ap.add_argument("--sibling-state", type=Path, action="append", default=[])
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    kw = dict(authorization_id=a.authorization_id, run_id=a.run_id, task_id=a.task_id, request_sha256=a.request_sha256,
              output_manifest_sha256=a.output_manifest_sha256, claim_refs=_refs(a.claim_refs),
              remote_claim=json.loads(a.remote_claim.read_text()), sibling_states=a.sibling_state)
    try:
        out = apply(a.repo, a.state, **kw) if a.apply else plan(a.repo, a.state, **kw)
    except RuntimeError as e:
        print(json.dumps({"status": "FAIL", "error": str(e)}, indent=2))
        return 1
    print(json.dumps(out, indent=2, sort_keys=True, default=str))
    return 0 if out["status"] == "PASS" else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
