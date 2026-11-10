"""Phase 5-B3-B acceptance: B3B-01..B3B-10 from tests that ACTUALLY ran (junit) + live non-consuming checks.

  python -m strategy_agent.execution.real_acceptance --out-dir <new dir> --junit b3b.xml --tests-rc 0 [--cloud]
         [--context-json ctx.json]
  python -m strategy_agent.execution.real_acceptance --verify-archive <dir> [--zip z]

The four layers are reported separately and never merged:
  engineering_readiness       this acceptance (SCOPED_GO / NO_GO / NOT_EVIDENCED)
  research_authorization      read live from the repository books (today: NOT_GRANTED)
  real_execution              NOT_PERFORMED (B3-B never runs the real Runner on real data)
  research_result_acceptance  NOT_APPLICABLE (no real result exists)
A synthetic / mock PASS is never a real-market run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from strategy_agent.execution.acceptance import junit
from strategy_agent.phase5a import acceptance as A5

ROOT = Path(__file__).resolve().parents[3]
TPE = ZoneInfo("Asia/Taipei")
INDEX = "ARCHIVE_INDEX.json"
OUTPUT_FILES = ("b3b_acceptance.json", "governance_state.json", "b3b_junit.xml", "notebook_context.json",
                "run_info.txt", INDEX)
GATES = {
    "B3B-01": "Runner refuses without authorization",
    "B3B-02": "Authorization / Bound Request consistency",
    "B3B-03": "Dataset / Core / Spec identity",
    "B3B-04": "Holdout exclusion",
    "B3B-05": "Atomic budget reservation and replay protection",
    "B3B-06": "Single-executor restriction",
    "B3B-07": "Runner receipt consistent with the result",
    "B3B-08": "Archive integrity and settlement",
    "B3B-09": "Crash recovery / fail closed",
    "B3B-10": "GitHub CI / Colab reproducibility",
}
CORE_REQUIRED = {"B3B-07": "test_B3B_07_layer_b_", "B3B-03": "test_B3B_03_layer_b_"}


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _dumps(o) -> bytes:
    return (json.dumps(o, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n").encode()


def production_state() -> dict:
    """Live, non-consuming: the repository's own books cannot construct the real path today."""
    from strategy_agent.execution.activation import RealActivationGate, eligible_authorizations  # noqa: PLC0415
    from strategy_agent.execution.controlled import ControlledRunnerAdapter, SyntheticStubBackend  # noqa: PLC0415
    from strategy_agent.orchestration import adapters as AD  # noqa: PLC0415
    from strategy_agent.orchestration import governance as GV  # noqa: PLC0415
    from strategy_agent.orchestration.orchestrator import Orchestrator  # noqa: PLC0415

    class _Probe(SyntheticStubBackend):           # never runs: counts calls to prove it
        synthetic = False

    before = A5.tree_fingerprint(ROOT)
    be = _Probe(A5.FIX / A5.FIX_RUN, identity={})
    g = RealActivationGate(governance_root=ROOT, artifact_root=ROOT, executor_id="colab-hh4832-exec-01",
                           production_books=True)
    ad = ControlledRunnerAdapter(be, governance_root=ROOT, activation=g)
    with tempfile.TemporaryDirectory() as t:
        try:
            Orchestrator(Path(t) / "s", Path(t) / "o", root=ROOT, governance_root=ROOT, controlled_adapter=ad,
                         worker="colab-hh4832-exec-01")
            refused = None
        except ValueError as e:
            refused = str(e)
    try:
        AD.ResearchOnlyRunnerAdapter().execute({}, ROOT / "__never__")
        legacy = "NOT_REFUSED"
    except AD.AdapterDisabled:
        legacy = "AdapterDisabled"
    from strategy_agent.readiness.b3c import expected_eligible, formal_authorization_state  # noqa: PLC0415
    now = datetime.now(TPE)
    elig, want, fa = eligible_authorizations(ROOT, "colab-hh4832-exec-01", now), expected_eligible(now), \
        formal_authorization_state()
    unchanged = A5.tree_fingerprint(ROOT) == before
    # Without the production (live GitHub) verifier the real path is never constructed on production books: before
    # the research lead's B3-C approval there is nothing eligible at all; with the ONE governed B3-C authorization
    # active it is still refused here (no live verifier), and every request still needs a verified binding, an
    # approved task and budget. Nothing ran and nothing was written.
    path_ok = bool(refused)
    ok = path_ok and elig == want and fa["state"] != "UNEXPECTED" and be.calls == 0 and unchanged \
        and GV.REAL_EXECUTION_ENABLED is False and legacy == "AdapterDisabled" \
        and not (ROOT / "registry/research_budget_events.jsonl").exists()
    return {"status": "PASS" if ok else "FAIL", "real_gated_orchestrator_on_production_books": refused or "CONSTRUCTED",
            "eligible_authorizations": elig, "expected_eligible": want, "b3c_authorization_state": fa["state"],
            "probe_runner_calls": be.calls, "REAL_EXECUTION_ENABLED": GV.REAL_EXECUTION_ENABLED,
            "phase5a_adapter": legacy, "repository_books_unchanged": unchanged,
            "research_authorization": "GRANTED" if elig else "NOT_GRANTED",
            "real_runner": "DISABLED" if not elig else "GATED_BY_AN_APPROVED_AUTHORIZATION"}


def gates(tests: dict, prod: dict, *, tests_rc, cloud: bool, code: dict) -> dict:
    g = {}
    for i in range(1, 10):
        gid = f"B3B-0{i}"
        sel = {k: v for k, v in tests.items() if f"test_B3B_0{i}_" in k}
        failed = [k for k, v in sel.items() if v == "FAIL"]
        passed = [k for k, v in sel.items() if v == "PASS"]
        need = CORE_REQUIRED.get(gid)
        core_ok = need is None or any(need in k and v == "PASS" for k, v in sel.items())
        live_ok = prod["status"] == "PASS" if gid == "B3B-01" else True
        status = "FAIL" if failed or not live_ok else ("PASS" if passed and core_ok else "BLOCKED")
        why = ([f"failed: {failed}"] if failed else []) + ([] if live_ok else ["production books not fail-closed"]) + \
              ([] if core_ok else [f"Core-pinned Layer B evidence missing ({need}*)"]) + ([] if passed else ["no test ran"])
        g[gid] = {"requirement": GATES[gid], "status": status, "why": why,
                  "evidence": {"tests_passed": sorted(passed), "tests_failed": failed,
                               **({"production_books": prod} if gid == "B3B-01" else {})}}
    clean = not code.get("dirty") and len(code.get("git_sha", "")) == 40 and set(code["git_sha"]) != {"0"}
    s10 = clean and tests_rc == 0 and not any(v == "FAIL" for v in tests.values()) and bool(tests)
    g["B3B-10"] = {"requirement": GATES["B3B-10"], "status": ("PASS" if cloud else "BLOCKED") if s10 else "FAIL",
                   "why": ([] if s10 else ["tree dirty / tests failed / no junit"]) + ([] if cloud else ["not a Colab + Drive run"]),
                   "evidence": {"code": code, "cloud": cloud, "tests_returncode": tests_rc, "tests_total": len(tests),
                                "tests_failed": [k for k, v in tests.items() if v == "FAIL"]}}
    return g


def verdict(g: dict) -> dict:
    fail = [k for k, v in g.items() if v["status"] == "FAIL"]
    blocked = [k for k, v in g.items() if v["status"] == "BLOCKED"]
    if fail:
        v = "NO_GO"
    elif blocked:
        v = "NOT_EVIDENCED"
    else:
        v = "SCOPED_GO"
    return {"verdict": v, "blockers": {k: g[k]["why"] for k in fail + blocked},
            "restrictions": ["SINGLE_EXECUTOR_ONLY (one named executor per authorization; no cross-runtime atomicity)",
                             "synthetic / mock / isolated-books evidence only; no real-market run",
                             "real Runner DISABLED until an approved authorization names the executor"],
            "meaning": "engineering readiness for a SEPARATE research-authorization decision; not an authorization, "
                       "not a real execution, not a research result"}


def verify_archive(out_dir: Path, zip_path: Path | None = None) -> dict:
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
            names, want = set(z.namelist()), set(idx["files"]) | {INDEX}
            probs += [f"zip missing: {n}" for n in sorted(want - names)] + [f"zip extra: {n}" for n in sorted(names - want)]
            probs += [f"zip differs: {n}" for n in sorted(want & names)
                      if _sha(z.read(n)) != _sha((out_dir / n).read_bytes())]
    return {"status": "PASS" if not probs else "FAIL", "problems": probs, "files": len(present)}


def run(out_dir: Path, *, junit_path: Path, tests_rc, cloud: bool, context: dict | None) -> dict:
    out_dir = Path(out_dir)
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FileExistsError(f"B3-B acceptance already exists in {out_dir}")
    code = A5.code_identity()
    tests = junit([junit_path]) if junit_path and Path(junit_path).is_file() else {}
    prod = production_state()
    g = gates(tests, prod, tests_rc=tests_rc, cloud=cloud, code=code)
    v = verdict(g)
    layers = {"engineering_readiness": v["verdict"], "research_authorization": prod["research_authorization"],
              "real_execution": "NOT_PERFORMED", "research_result_acceptance": "NOT_APPLICABLE"}
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {"b3b_acceptance.json": {"verdict": v, "layers": layers, "gates": g, "code": code, "cloud": cloud,
                                     "generated_at": datetime.now(TPE).isoformat(timespec="seconds"),
                                     "real_runner": prod["real_runner"], "authorization_created": False,
                                     "market_data_read": False, "holdout_read": False, "new_backtest_executed": False},
             "governance_state.json": prod,
             "notebook_context.json": context or {"note": "no notebook context (CLI / CI run)"}}
    for n, o in files.items():
        with open(out_dir / n, "xb") as fh:
            fh.write(_dumps(o))
    if junit_path and Path(junit_path).is_file():
        with open(out_dir / "b3b_junit.xml", "xb") as fh, open(junit_path, "rb") as src:
            shutil.copyfileobj(src, fh)
    info = {"kind": "PHASE5B3B_ACCEPTANCE", "verdict": v["verdict"], **code, "cloud": cloud, **layers,
            "gates": {k: x["status"] for k, x in g.items()}, "real_runner": prod["real_runner"],
            "authorization_created": False, "market_data_read": False, "holdout_read": False,
            "new_backtest_executed": False, "tests_returncode": tests_rc, "tests_total": len(tests)}
    with open(out_dir / "run_info.txt", "x", encoding="utf-8") as fh:
        fh.write("".join(f"{k}={x if isinstance(x, str) else json.dumps(x)}\n" for k, x in info.items()))
    idx = {"kind": "PHASE5B3B_ARCHIVE_INDEX", "work_tree": "NONE (B3-B acceptance writes no work/ tree)",
           "files": {p.name: {"sha256": _sha(p.read_bytes()), "bytes": p.stat().st_size}
                     for p in sorted(out_dir.iterdir()) if p.is_file()}}
    with open(out_dir / INDEX, "x", encoding="utf-8") as fh:
        json.dump(idx, fh, indent=2, sort_keys=True)
    chk = verify_archive(out_dir)
    if chk["status"] != "PASS":
        raise RuntimeError(f"archive index self-check failed: {chk}")
    return {"verdict": v, "layers": layers, "gates": {k: x["status"] for k, x in g.items()}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-dir")
    ap.add_argument("--junit")
    ap.add_argument("--tests-rc", type=int)
    ap.add_argument("--cloud", action="store_true")
    ap.add_argument("--context-json")
    ap.add_argument("--verify-archive")
    ap.add_argument("--zip")
    a = ap.parse_args(argv)
    if a.verify_archive:
        r = verify_archive(Path(a.verify_archive), Path(a.zip) if a.zip else None)
        print(json.dumps(r, indent=2))
        return 0 if r["status"] == "PASS" else 1
    r = run(Path(a.out_dir), junit_path=Path(a.junit) if a.junit else None, tests_rc=a.tests_rc, cloud=a.cloud,
            context=json.loads(Path(a.context_json).read_text()) if a.context_json else None)
    print(json.dumps(r, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
