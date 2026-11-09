"""Phase 5-B3-A acceptance: B3A-01..B3A-08 -> GO / SCOPED_GO / NO_GO (readiness only; GO never enables a Runner).

  python -m strategy_agent.readiness.acceptance --out-dir <new dir> --junit b3a.xml [--tests-rc 0] [--cloud]
         [--snapshot-dir <Drive DEV snapshot dir>] [--context-json ctx.json]
  python -m strategy_agent.readiness.acceptance --emit-docs          # regenerate the committed docs/phase5b3a files
  python -m strategy_agent.readiness.acceptance --verify-archive <dir> [--zip z]

Non-consuming by construction: no backtest, no market-data decoding, no Holdout, no authorization created or used.
The optional ``--snapshot-dir`` check only SHA-256 hashes the two pinned files of the DEVELOPMENT snapshot (which by
its contract contains no Holdout rows) to confirm the bytes are the pinned, immutable ones.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import zipfile

from strategy_agent.execution.acceptance import junit
from strategy_agent.phase5a import acceptance as A5

from . import authorization_draft as D
from . import checks as K
from . import manifest as M
from .candidate import CANDIDATE, ROOT

TPE = ZoneInfo("Asia/Taipei")
INDEX = "ARCHIVE_INDEX.json"
DOCS = ROOT / "docs/phase5b3a"
GATES = {
    "B3A-01": "Dataset and snapshot identity",
    "B3A-02": "Core and strategy specification identity",
    "B3A-03": "PIT and temporal execution rules",
    "B3A-04": "Costs / slippage / liquidity requirements",
    "B3A-05": "Holdout exclusion",
    "B3A-06": "Authorization and budget governance",
    "B3A-07": "Single-executor controlled Runner readiness",
    "B3A-08": "CI / Colab / Drive archive reproducibility",
}
GOV_SUITES = ("test_phase5b1", "test_phase5b2_controlled", "test_phase5b2_security_remediation",
              "test_phase5b2_independent_p0_2_gate", "test_phase5b3a")


OUTPUT_FILES = ("b3a_acceptance.json", "readiness_manifest.json", "authorization_draft.json", "readiness_checks.json",
                "notebook_context.json", "run_info.txt", INDEX)


def verify_archive(out_dir: Path, zip_path: Path | None = None) -> dict:
    """Every file listed with its sha, nothing unlisted, every required output present; optional ZIP equality."""
    out_dir = Path(out_dir)
    try:
        idx = json.loads((out_dir / INDEX).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {"status": "FAIL", "problems": [f"index unreadable: {e}"]}
    present = {p.name for p in out_dir.iterdir() if p.is_file() and p.name != INDEX}
    probs = [f"unlisted file: {n}" for n in sorted(present - set(idx["files"]))]
    probs += [f"missing file: {n}" for n in sorted(set(idx["files"]) - present)]
    probs += [f"altered file: {n}" for n in sorted(present & set(idx["files"]))
              if _sha((out_dir / n).read_bytes()) != idx["files"][n]["sha256"]]
    probs += [f"required output missing: {n}" for n in OUTPUT_FILES if n != INDEX and n not in present]
    probs += [f"unexpected directory: {p.name}" for p in out_dir.iterdir() if p.is_dir()]
    if zip_path is not None:
        with zipfile.ZipFile(zip_path) as z:
            names = set(z.namelist())
            want = set(idx["files"]) | {INDEX}
            probs += [f"zip missing: {n}" for n in sorted(want - names)] + [f"zip extra: {n}" for n in sorted(names - want)]
            for n in sorted(want & names):
                if _sha(z.read(n)) != (idx["files"][n]["sha256"] if n != INDEX else _sha((out_dir / INDEX).read_bytes())):
                    probs.append(f"zip differs: {n}")
    return {"status": "PASS" if not probs else "FAIL", "problems": probs, "files": len(present)}


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _dumps(o) -> bytes:
    return (json.dumps(o, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n").encode()


def snapshot_bytes_check(snapshot_dir: Path | None, m: dict) -> dict:
    """Optional, non-consuming: hash the pinned manifest + parquet of the DEV snapshot and compare with the pins."""
    if snapshot_dir is None:
        return {"status": "NOT_RUN", "detail": "no --snapshot-dir: immutability rests on archived receipt evidence"}
    d = Path(snapshot_dir)
    cfg = json.loads((ROOT / CANDIDATE["run_config"]).read_text())
    sb = cfg["snapshot_binding"]
    want = {sb["manifest_relative_path"]: sb["manifest_sha256"],
            sb["diagnostic_parquet_relative_path"]: sb["integrity_receipt"]["parquet_sha256"]}
    if d.name != m["fields"]["dataset_snapshot_id"]["value"] or not d.is_dir():
        return {"status": "FAIL", "detail": f"not the pinned snapshot directory: {d}"}
    got = {}
    for name in want:
        p = d / name
        got[name] = _sha(p.read_bytes()) if p.is_file() and not p.is_symlink() else None
    ok = got == want
    return {"status": "PASS" if ok else "FAIL", "expected": want, "observed": got,
            "detail": "SHA-256 of whole files only; no decoding, no Holdout bytes (development subset)"}


def gates(m: dict, ds: dict, cr: dict, gv: dict, draft_check: dict, tests: dict, *, tests_rc, cloud: bool,
          code: dict, drive: dict) -> dict:
    F, X = m["fields"], m["cross_checks"]

    def ok(*keys):
        return all(F[k]["status"] == "RESOLVED" for k in keys)

    def st(x):
        return x["status"] == "PASS"

    def suite(prefixes):
        sel = {k: v for k, v in tests.items() if any(p in k for p in prefixes)}
        return sel, (bool(sel) and all(v != "FAIL" for v in sel.values()) and any(v == "PASS" for v in sel.values()))

    g = {}

    def put(gid, status, why, evidence):
        g[gid] = {"requirement": GATES[gid], "status": status, "why": why, "evidence": evidence}

    s01 = ok("dataset_snapshot_id", "dataset_manifest_sha256") and st(ds["D1_snapshot_exists_and_immutable"]) \
        and st(ds["D2_manifest_matches_dataset"]) and drive["status"] in ("PASS", "NOT_RUN")
    put("B3A-01", "PASS" if s01 else "FAIL", [] if s01 else ["dataset identity / immutability not proven"],
        {"snapshot_id": F["dataset_snapshot_id"], "manifest": F["dataset_manifest_sha256"],
         "D1": ds["D1_snapshot_exists_and_immutable"], "D2": ds["D2_manifest_matches_dataset"], "drive_bytes": drive})
    spec_bind, rules = X["phase5_request_spec_dataset_binding"], X["spec_rules_unchanged_vs_parent"]
    s02 = ok("core_commit_sha", "strategy_spec_sha256", "strategy_id_version") and st(ds["D9_core_benchmark_and_readiness_evidence"])
    why02 = ([] if s02 else ["core / spec identity not resolved"]) + \
        ([] if spec_bind["status"] == "PASS" else ["SPEC_DATASET_REFERENCE_MISMATCH: the Phase 5 A2 exporter refuses a "
                                                    "request for the DEV snapshot with this spec (HUMAN DECISION: approve "
                                                    "a re-pinned spec version)"]) + \
        ([] if rules["status"] == "PASS" else ["candidate spec changes more than the data binding of its parent"])
    put("B3A-02", ("PASS" if spec_bind["status"] == "PASS" and rules["status"] == "PASS" else "BLOCKED") if s02 else "FAIL",
        why02, {"core": F["core_commit_sha"], "spec": F["strategy_spec_sha256"], "strategy": F["strategy_id_version"],
                "benchmark": ds["D9_core_benchmark_and_readiness_evidence"], "phase5_request_spec_dataset_binding": spec_bind,
                "spec_rules_unchanged_vs_parent": rules})
    s03 = ok("temporal_rules") and all(st(ds[k]) for k in ("D3_timestamps_and_available_at_PIT",
                                                            "D4_signal_to_execution_conservative",
                                                            "D5_calendar_and_RTH_boundaries", "D6_missing_data_explicit_policy"))
    put("B3A-03", "PASS" if s03 else "FAIL", [] if s03 else ["PIT / temporal evidence incomplete"],
        {"temporal_rules": F["temporal_rules"], **{k: ds[k] for k in ("D3_timestamps_and_available_at_PIT",
                                                                       "D4_signal_to_execution_conservative",
                                                                       "D5_calendar_and_RTH_boundaries",
                                                                       "D6_missing_data_explicit_policy")}})
    spec = json.loads((ROOT / CANDIDATE["strategy_spec"]).read_text())
    sizing = spec.get("position_sizing") or {}
    liq = {"position_sizing": sizing, "status": "PINNED" if sizing.get("method") == "FIXED_UNITS" and sizing.get("units") else "MISSING",
           "limitation": "no volume-participation / market-impact model: fixed units only (documented risk)"}
    s04 = ok("trading_cost_profile", "slippage_assumption") and st(ds["D7_cost_and_slippage_pinned"]) and liq["status"] == "PINNED"
    put("B3A-04", "PASS" if s04 else "FAIL", [] if s04 else ["cost / slippage / sizing not pinned"],
        {"costs": F["trading_cost_profile"], "slippage": F["slippage_assumption"], "D7": ds["D7_cost_and_slippage_pinned"],
         "liquidity": liq})
    s05 = ok("holdout_exclusion", "research_window") and st(ds["D8_research_period_excludes_holdout"])
    put("B3A-05", "PASS" if s05 else "FAIL", [] if s05 else ["Holdout exclusion not proven"],
        {"holdout": F["holdout_exclusion"], "window": F["research_window"], "D8": ds["D8_research_period_excludes_holdout"]})
    gsel, gok = suite(("test_phase5b1", "test_phase5b2_controlled", "test_phase5b3a"))
    s06 = all(st(v) for v in gv.values()) and draft_check["status"] == "PASS" and gok
    put("B3A-06", "PASS" if s06 else "FAIL", [] if s06 else ["authorization / budget governance not evidenced"],
        {"governance": gv, "authorization_draft_schema_dry_run": draft_check,
         "tests": {"passed": sum(v == "PASS" for v in gsel.values()), "failed": [k for k, v in gsel.items() if v == "FAIL"]}})
    csel, cok = suite(("test_phase5b2_security_remediation", "test_phase5b2_independent_p0_2_gate", "test_phase5b2_controlled"))
    mp = [k for k in csel if "several_processes" in k]
    s07 = all(st(v) for v in cr.values()) and cok and bool(mp) and all(csel[k] == "PASS" for k in mp)
    put("B3A-07", "PASS" if s07 else "FAIL", [] if s07 else ["controlled Runner readiness not evidenced"],
        {"controlled": cr, "tests": {"passed": sum(v == "PASS" for v in csel.values()),
                                     "failed": [k for k, v in csel.items() if v == "FAIL"], "multi_process": mp},
         "real_runner": "DISABLED (verified by C1-C3)"})
    clean = not code.get("dirty") and len(code.get("git_sha", "")) == 40 and set(code["git_sha"]) != {"0"}
    s08 = clean and tests_rc == 0 and not any(v == "FAIL" for v in tests.values())
    put("B3A-08", ("PASS" if cloud else "BLOCKED") if s08 else "FAIL",
        ([] if s08 else ["tree dirty / tests failed"]) + ([] if cloud else ["not a Colab + Drive run (cloud evidence pending)"]),
        {"code": code, "cloud": cloud, "tests_returncode": tests_rc, "tests_total": len(tests),
         "tests_failed": [k for k, v in tests.items() if v == "FAIL"]})
    return g


def verdict(g: dict) -> dict:
    fail = [k for k, v in g.items() if v["status"] == "FAIL"]
    blocked = [k for k, v in g.items() if v["status"] == "BLOCKED"]
    if fail or [b for b in blocked if b != "B3A-08"]:
        return {"verdict": "NO_GO", "blockers": {k: g[k]["why"] for k in fail + blocked},
                "meaning": "specific blockers prevent a first real research run; GO never enables the Runner"}
    if blocked:
        return {"verdict": "NOT_EVIDENCED", "blockers": {k: g[k]["why"] for k in blocked},
                "meaning": "all readiness gates pass except cloud reproducibility, which needs the Colab run"}
    return {"verdict": "SCOPED_GO", "restrictions": ["SINGLE_EXECUTOR_ONLY", "dataset readiness from archived evidence of "
                                                     "the prior real run (no new data read)",
                                                     "real Runner DISABLED until a separate authorization decision"],
            "meaning": "readiness established within documented restrictions; eligible for a separate human authorization "
                       "decision. Does NOT authorize the real Runner"}


def evaluate(*, code: dict) -> dict:
    m = M.build(code)
    draft = D.build(m)
    return {"manifest": m, "draft": draft, "draft_check": D.schema_dry_run(draft), "dataset": K.dataset_readiness(m),
            "controlled": K.controlled_readiness(), "governance": K.governance_readiness(m)}


def emit_docs() -> list[str]:
    """Committed, deterministic copies (no wall clock, no code identity) for review in the PR."""
    r = evaluate(code=None)
    DOCS.mkdir(parents=True, exist_ok=True)
    out = {"readiness_manifest.json": r["manifest"], "authorization_draft.json": r["draft"]}
    for n, o in out.items():
        (DOCS / n).write_bytes(_dumps(o))
    return sorted(out)


def run(out_dir: Path, *, junit_paths: list[Path], tests_rc, cloud: bool, context: dict | None,
        snapshot_dir: Path | None) -> dict:
    out_dir = Path(out_dir)
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FileExistsError(f"readiness acceptance already exists in {out_dir}")
    before = A5.tree_fingerprint(ROOT)
    code = A5.code_identity()
    r = evaluate(code=code)
    tests = junit(junit_paths)
    drive = snapshot_bytes_check(snapshot_dir, r["manifest"])
    g = gates(r["manifest"], r["dataset"], r["controlled"], r["governance"], r["draft_check"], tests, tests_rc=tests_rc,
              cloud=cloud, code=code, drive=drive)
    v = verdict(g)
    unchanged = A5.tree_fingerprint(ROOT) == before
    if not unchanged:
        v = {"verdict": "NO_GO", "blockers": {"REPOSITORY": ["governance/registry changed during acceptance"]}}
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "b3a_acceptance.json": {"verdict": v, "gates": g, "code": code, "cloud": cloud,
                                "generated_at": datetime.now(TPE).isoformat(timespec="seconds"),
                                "repository_books_unchanged": unchanged, "real_runner": "DISABLED",
                                "authorization_created": False, "market_data_read": False, "holdout_read": False,
                                "new_backtest_executed": False},
        "readiness_manifest.json": r["manifest"],
        "authorization_draft.json": {**r["draft"], "schema_dry_run": r["draft_check"]},
        "readiness_checks.json": {"dataset": r["dataset"], "controlled": r["controlled"], "governance": r["governance"],
                                  "snapshot_bytes": drive, "tests": tests},
        "notebook_context.json": context or {"note": "no notebook context (CLI / CI run)"},
    }
    for n, o in files.items():
        with open(out_dir / n, "xb") as fh:
            fh.write(_dumps(o))
    info = {"kind": "PHASE5B3A_READINESS_ACCEPTANCE", "verdict": v["verdict"], **code, "cloud": cloud,
            "gates": {k: x["status"] for k, x in g.items()}, "real_runner": "DISABLED", "authorization_created": False,
            "market_data_read": False, "holdout_read": False, "new_backtest_executed": False,
            "snapshot_bytes_check": drive["status"], "tests_returncode": tests_rc}
    with open(out_dir / "run_info.txt", "x", encoding="utf-8") as fh:
        fh.write("".join(f"{k}={x if isinstance(x, str) else json.dumps(x)}\n" for k, x in info.items()))
    idx = {"kind": "PHASE5B3A_ARCHIVE_INDEX", "work_tree": "NONE (B3-A writes no supporting work/ tree)",
           "files": {p.name: {"sha256": _sha(p.read_bytes()), "bytes": p.stat().st_size}
                     for p in sorted(out_dir.iterdir()) if p.is_file()}}
    with open(out_dir / INDEX, "x", encoding="utf-8") as fh:
        json.dump(idx, fh, indent=2, sort_keys=True)
    if verify_archive(out_dir)["status"] != "PASS":
        raise RuntimeError("archive index self-check failed")
    return {"verdict": v, "gates": {k: x["status"] for k, x in g.items()}, "snapshot_bytes": drive["status"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-dir")
    ap.add_argument("--junit", action="append", default=[])
    ap.add_argument("--tests-rc", type=int)
    ap.add_argument("--cloud", action="store_true")
    ap.add_argument("--snapshot-dir")
    ap.add_argument("--context-json")
    ap.add_argument("--emit-docs", action="store_true")
    ap.add_argument("--verify-archive")
    ap.add_argument("--zip")
    a = ap.parse_args(argv)
    if a.emit_docs:
        print(json.dumps(emit_docs()))
        return 0
    if a.verify_archive:
        r = verify_archive(Path(a.verify_archive), Path(a.zip) if a.zip else None)
        print(json.dumps(r, indent=2))
        return 0 if r["status"] == "PASS" else 1
    ctx = json.loads(Path(a.context_json).read_text()) if a.context_json else None
    r = run(Path(a.out_dir), junit_paths=[Path(p) for p in a.junit], tests_rc=a.tests_rc, cloud=a.cloud, context=ctx,
            snapshot_dir=Path(a.snapshot_dir) if a.snapshot_dir else None)
    print(json.dumps(r, indent=2))
    return 0 if r["verdict"]["verdict"] in ("GO", "SCOPED_GO", "NO_GO", "NOT_EVIDENCED") else 1


if __name__ == "__main__":
    sys.exit(main())
