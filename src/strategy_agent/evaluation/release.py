"""MVP v1 release acceptance: Existing Archive -> Read-only Evaluator -> Comparison -> Release Report.

Read-only. Never imports the runner, Core or any governance writer; never runs a backtest, reads a snapshot or the
holdout, or consumes authorization budget. The evaluation is the Phase 4-C acceptance (``phase4c.accept``) executed
at the pinned release-candidate commit, compared with the recorded Phase 4-C evaluation.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import phase4c as C
from .universal import ROOT, canonical

VERSION = "1.0.0"
SOURCE_RUN_ID = "20261107_081300_9439f8c"
ARCHIVE_MANIFEST_SHA = "328f2cf99c33b4d9d427327d092aaf31da619b5f5ad948c815e75b04b92345a9"
DATASET_MANIFEST_SHA = "8b6fc838b72ae3015f6eb418c3a77115e53a86f185e05c2249e9dcd58192cfc5"
REFERENCE_EVAL = "20261107_100344_883d14c"
REFERENCE_EVAL_DIR = ROOT / "docs/evidence/phase4c" / REFERENCE_EVAL
INTEGRATION = {"main_base": "d738f7fa38596cb4996d093c81ad3da4bdf8236a",
               "pr4_m3b": "8f3f6e1", "pr5_phase4a": "a6509151fee5a5c0d103e3860b41fcbc616777a4",
               "pr6_phase4b": "00f561aa51bce2d4b3974a0c27b46125c15d2209",
               "pr7_phase4c": "d455ed104817c44d5147dab0bcd460fb4c758635",
               "phase4a_real_run_agent": "9439f8c6f53b9c6c7f2f49b5a543ffdbc84f2f2a",
               "phase4c_evaluator": "883d14c0059add270d596e4bdc86be46052219ea"}
CORE_REAL_RUN = "b16179a7cc077c36046574769a32c367fa476cae"   # market-research-core (other repo; recorded, not an ancestor)
OUTPUTS = ("release_acceptance.json", "release_report.md", "integration_manifest.json", "test_results.json",
           "evaluation_comparison.json", "governance_checks.json", "run_info.txt")
PRESENTATION_ONLY = ("reason",)   # robustness_matrix fields allowed to differ (MVP v1 presentation fix)


def _git(root, *a) -> str:
    return subprocess.check_output(["git", "-C", str(root), *a], text=True).strip()


def integration_manifest(root: Path = ROOT) -> dict:
    head = _git(root, "rev-parse", "HEAD")
    anc = {}
    for k, sha in INTEGRATION.items():
        full = _git(root, "rev-parse", sha + "^{commit}")
        anc[k] = {"sha": full, "ancestor_of_rc": subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor",
                                                                 full, head]).returncode == 0}
    merges = _git(root, "log", "--merges", "--format=%H %P|%s", f"{INTEGRATION['main_base']}..{head}").splitlines()
    present = {p: (Path(root) / p).exists() for p in (
        "src/strategy_agent/research/pipeline.py",            # Phase 3 / M3-B
        "src/strategy_agent/research_only/runner.py",         # Phase 4-A runner
        "src/strategy_agent/evaluation/universal.py",         # Phase 4-B evaluator
        "src/strategy_agent/evaluation/phase4c.py",           # Phase 4-C real-archive adapter
        f"docs/evidence/phase4c/{REFERENCE_EVAL}/acceptance_checks.json",
        f"docs/evidence/phase4a_dev_smoke/{SOURCE_RUN_ID}/MANIFEST.json")}
    dirty = _git(root, "status", "--porcelain", "--untracked-files=all")
    return {"rc_commit": head, "core_real_run": CORE_REAL_RUN, "rc_parents": _git(root, "rev-list", "--parents", "-n", "1", head).split()[1:],
            "sources": anc, "merge_commits": [{"commit": m.split(" ")[0], "parents": m.split("|")[0].split()[1:],
                                               "subject": m.split("|", 1)[1]} for m in merges],
            "components_present": present, "working_tree_clean": dirty == "",
            "status": "PASS" if all(v["ancestor_of_rc"] for v in anc.values()) and all(present.values()) and not dirty
            else "FAIL"}


def _strip_eval(ev: dict) -> dict:
    """Canonical JSON copy without metadata and without the presentation-only text of robustness rows, wherever a
    robustness row appears (matrix, layers, flag evidence)."""
    e = json.loads(json.dumps(ev, default=str))
    e.pop("metadata", None)

    def walk(x):
        if isinstance(x, dict):
            if x.get("dimension") == "robustness":
                for k in PRESENTATION_ONLY:
                    x.pop(k, None)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(e)
    return e


def compare(acc: dict, reference_dir: Path = REFERENCE_EVAL_DIR) -> dict:
    ref_eval = json.loads((Path(reference_dir) / "evaluation.json").read_text())
    ref_acc = json.loads((Path(reference_dir) / "acceptance_checks.json").read_text())
    ev = acc["evaluation"]
    per, ok = {}, True
    for s in C.SCENARIOS:
        mine = acc["evaluations"][s]
        ref = ref_acc["per_scenario"][s]
        mv = {k: v.get("value") for k, v in mine["metrics"].items()}
        rv = {k: v.get("value") for k, v in ref["metrics"].items()}
        same = {"metrics_identical": mv == rv, "input_sha256_identical": mine["input_sha256"] == ref["input_sha256"],
                "verdict_identical": (mine["strategy_verdict"], mine["evidence_sufficiency"], mine["engineering_status"])
                == (ref["strategy_verdict"], ref["evidence_sufficiency"], ref["engineering_status"]),
                "risk_flags_identical": sorted(f["code"] for f in mine["risk_flags"]) == sorted(ref["risk_flags"])}
        diffs = sorted(k for k in set(mv) | set(rv) if mv.get(k) != rv.get(k))
        per[s] = {**same, "metric_differences": diffs, "net_return": mv.get("net_return"), "win_rate": mv.get("win_rate"),
                  "payoff_ratio": mv.get("payoff_ratio"), "expectancy": mv.get("expectancy"),
                  "profit_factor": mv.get("profit_factor"), "max_drawdown": mv.get("max_drawdown"),
                  "trade_count": mv.get("trade_count")}
        ok &= all(same.values())
    checks_now = {k: v["status"] for k, v in acc["checks"].items()}
    checks_ref = {k: v["status"] for k, v in ref_acc["checks"].items()}
    rows_now = {r["group"]: r.get("reason") for r in ev["robustness_matrix"] if r["dimension"] == "robustness"}
    rows_ref = {r["group"]: r.get("reason") for r in ref_eval["robustness_matrix"] if r["dimension"] == "robustness"}
    reason_changes = {g: {"reference": rows_ref.get(g), "release": rows_now.get(g)} for g in rows_now
                      if rows_now.get(g) != rows_ref.get(g)}
    payload_equal = canonical(_strip_eval(ev)) == canonical(_strip_eval(ref_eval))
    cost_now = acc["checks"]["C4_performance_consistency"]["scenarios"]
    total_costs = {s: cost_now[s]["rows"]["total_cost"]["core_upstream"] for s in C.SCENARIOS}
    result = {"reference_evaluation": REFERENCE_EVAL, "reference_dir": str(reference_dir),
              "scenarios": per, "total_costs": total_costs,
              "checks_now": checks_now, "checks_reference": checks_ref, "checks_identical": checks_now == checks_ref,
              "primary_verdict": {"strategy": ev["strategy_verdict"], "evidence_sufficiency": ev["evidence_sufficiency"],
                                  "engineering": ev["engineering_status"]},
              "payload_equal_except_metadata_and_presentation_reason": payload_equal,
              "presentation_reason_changes": reason_changes,
              "expected_presentation_change": reason_changes == {"cost_sensitivity": {
                  "reference": "required evidence absent",
                  "release": "cost scenarios supplied on the same signals and positions"}}}
    result["status"] = "PASS" if (ok and result["checks_identical"] and all(v == "PASS" for v in checks_now.values())
                                  and payload_equal and result["expected_presentation_change"]
                                  and ev["strategy_verdict"] == "RETEST" and ev["evidence_sufficiency"] == "UNDETERMINED"
                                  ) else "FAIL"
    return result


def reference_integrity(reference_dir: Path, committed_dir: Path = REFERENCE_EVAL_DIR) -> dict:
    """The Phase 4-C evaluation archive on Drive must equal the committed copy byte for byte."""
    ref = C.fingerprint(reference_dir)
    com = C.fingerprint(committed_dir)
    return {"drive": ref, "committed": com, "identical": ref == com and len(ref) == 8,
            "status": "PASS" if ref == com and len(ref) == 8 else "FAIL"}


def governance_checks(acc: dict, root: Path = ROOT) -> dict:
    c8 = acc["checks"]["C8_governance_isolation"]
    gov = C.load_governance(root)
    readiness = gov["readiness"]["markets"]["IBKR_TSM"]
    uses = [u for u in gov["uses"] if u.get("authorization_id") == "RA-IBKR_TSM-SMA520_DEV-18350b3adba8e28a7e5e"]
    out = {"authorization": "RA-IBKR_TSM-SMA520_DEV-18350b3adba8e28a7e5e", "authorization_uses": f"{len(uses)}/1",
           "use_records_total": len(gov["uses"]), "formal_market_status": readiness.get("status"),
           "formal_gates": {k: (v["status"] if isinstance(v, dict) else v) for k, v in readiness["gates"].items()},
           "holdout_lock": [x.get("lock_id") for x in gov["locks"]], "c8": c8,
           "research_only_runner_invoked": False, "new_batch_consumed": 0}
    out["status"] = "PASS" if (c8["status"] == "PASS" and len(uses) == 1 and out["formal_market_status"] == "NO_GO"
                               and out["holdout_lock"] == ["HOLDOUT-TSM-15M-v1"]) else "FAIL"
    return out


def run_pytest(root: Path = ROOT) -> dict:
    import sys  # noqa: PLC0415
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:warnings", "-p", "no:cacheprovider", "tests"],
                       cwd=root, text=True, capture_output=True)
    tail = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    counts = {k: int(n) for n, k in __import__("re").findall(r"(\d+) (passed|failed|skipped|error|errors)", tail)}
    try:
        import market_research_core  # noqa: F401, PLC0415 - probe only, inside the pytest record
        core = "installed"
    except ImportError:
        core = "absent (Core-dependent tests skip by design; covered by CI jobs pinned to 699e3ad/e31c57b/b16179a)"
    return {"python": sys.version.split()[0], "core": core, "summary": tail, "counts": counts,
            "returncode": r.returncode, "failures": [x for x in r.stdout.splitlines() if x.startswith("FAILED")],
            "status": "PASS" if r.returncode == 0 and counts.get("failed", 0) == 0 else "FAIL"}


def run(source_archive, reference_eval_dir, output_root, *, expected_rc_sha: str, root: Path = ROOT,
        test_results: dict | None = None, context: dict | None = None) -> dict:
    sha, dirty = C.evaluator_identity(root)
    if dirty or sha != expected_rc_sha:
        raise RuntimeError(f"release must run on a clean checkout of {expected_rc_sha} (got {sha}, dirty={dirty})")
    started = datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds")
    src_before = C.fingerprint(source_archive)
    ref_before = C.fingerprint(reference_eval_dir)
    integ = integration_manifest(root)
    acc = C.accept(source_archive, expected_manifest_sha256=ARCHIVE_MANIFEST_SHA, evaluator_commit_sha=sha,
                   evaluated_at=started, governance_root=root)
    if acc.get("evaluation") is None:
        comparison = {"status": "BLOCKED", "error": acc.get("error")}
        gov = {"status": "BLOCKED"}
    else:
        comparison = compare(acc, reference_eval_dir)
        gov = governance_checks(acc, root)
    ref_int = reference_integrity(reference_eval_dir)
    src_after = C.fingerprint(source_archive)
    archive = {"source_archive": str(source_archive), "files": len(src_before),
               "archive_manifest_sha256": ARCHIVE_MANIFEST_SHA, "dataset_manifest_sha256": DATASET_MANIFEST_SHA,
               "source_fingerprint_before": src_before, "source_unchanged": src_before == src_after,
               "manifest_pin_matches": src_before.get("MANIFEST.json") == ARCHIVE_MANIFEST_SHA,
               "reference_evaluation_unchanged": ref_before == C.fingerprint(reference_eval_dir),
               "reference_evaluation_equals_committed_copy": ref_int}
    archive["status"] = "PASS" if (archive["source_unchanged"] and archive["manifest_pin_matches"] and len(src_before) == 14
                                   and acc["status"] != "BLOCKED" and acc["checks"]["C2_archive_integrity"]["status"] == "PASS"
                                   and archive["reference_evaluation_unchanged"] and ref_int["status"] == "PASS") else "FAIL"
    tests = test_results or {"status": "NOT_RUN"}
    R = {"R1_repository_integrity": integ["status"], "R2_regression_tests": tests.get("status", "NOT_RUN"),
         "R3_real_archive_evaluation_consistency": comparison["status"], "R4_governance_isolation": gov["status"],
         "R5_archive_integrity": archive["status"],
         "R6_colab_portability": "PASS" if (context or {}).get("clean_colab_runtime") is True else "PENDING"}
    verdict = "MVP_V1_ENGINEERING_GO" if all(v == "PASS" for v in R.values()) else \
        ("BLOCKED" if "FAIL" in R.values() or "BLOCKED" in R.values() else "PENDING")
    out = Path(output_root) / f"{datetime.fromisoformat(started):%Y%m%d_%H%M%S}_{sha[:7]}"
    out.mkdir(parents=True, exist_ok=False)
    acceptance = {"kind": "MVP_V1_RELEASE_ACCEPTANCE", "version": VERSION, "rc_commit": sha, "started_at": started,
                  "results": R, "verdict": verdict, "phase4c_checks": {k: v["status"] for k, v in acc["checks"].items()},
                  "strategy_verdict": (acc.get("evaluation") or {}).get("strategy_verdict"),
                  "evidence_sufficiency": (acc.get("evaluation") or {}).get("evidence_sufficiency"),
                  "formal_market_status": gov.get("formal_market_status"), "not_claimed": [
                      "strategy effectiveness", "Formal Market Eligibility", "out-of-sample robustness"]}
    payload = {"release_acceptance.json": acceptance, "integration_manifest.json": integ, "test_results.json": tests,
               "evaluation_comparison.json": comparison, "governance_checks.json": gov | {"archive_integrity": archive}}
    files = {}
    for name, obj in payload.items():
        data = (json.dumps(obj, indent=2, sort_keys=True, default=str, ensure_ascii=False) + "\n").encode()
        with open(out / name, "xb") as fh:
            fh.write(data)
        files[name] = hashlib.sha256(data).hexdigest()
    info = {"kind": "MVP_V1_RELEASE_ACCEPTANCE", "rc_commit": sha, "release_verdict": verdict, "started_at": started,
            "source_run_id": SOURCE_RUN_ID, "source_archive": str(source_archive),
            "source_archive_manifest_sha256": ARCHIVE_MANIFEST_SHA, "dataset_manifest_sha256": DATASET_MANIFEST_SHA,
            "source_agent_commit": INTEGRATION["phase4a_real_run_agent"], "source_core_commit": CORE_REAL_RUN,
            "reference_evaluation": REFERENCE_EVAL, "reference_evaluator_commit": INTEGRATION["phase4c_evaluator"],
            "snapshot_id": "TSM_DEV_1M_20240529_20240628_18350b3adba8e28a7e5e",
            "research_only_runner_invoked": False, "holdout_read": False, "new_batch_consumed": 0} | (context or {})
    text = "".join(f"{k}={v if isinstance(v, str) else json.dumps(v, default=str)}\n" for k, v in info.items())
    with open(out / "run_info.txt", "x", encoding="utf-8") as fh:
        fh.write(text)
    report = _report(acceptance, comparison, gov, archive, integ, tests)
    with open(out / "release_report.md", "x", encoding="utf-8") as fh:
        fh.write(report)
    if C.fingerprint(source_archive) != src_before:
        raise RuntimeError("SOURCE_ARCHIVE_CHANGED")
    return {"output_dir": str(out), "verdict": verdict, "results": R, "acceptance": acceptance}


def _report(acc, comp, gov, archive, integ, tests) -> str:
    L = [f"# MVP v1 Release Acceptance — `{acc['rc_commit'][:7]}`", "",
         f"**Verdict: {acc['verdict']}** (research-only engineering scope; not a strategy or market-eligibility claim)", "",
         "| Item | Result |", "|---|---|"]
    for k, v in acc["results"].items():
        L.append(f"| {k} | {v} |")
    L += ["", f"Strategy {acc['strategy_verdict']} / evidence {acc['evidence_sufficiency']}; formal market "
          f"{acc['formal_market_status']}; Phase 4-C checks {acc['phase4c_checks']}", "",
          "## Real-archive comparison vs Phase 4-C evaluation " + comp.get("reference_evaluation", ""), "",
          "| Scenario | identical metrics | identical input | net return | win rate | payoff | expectancy | PF | MDD |",
          "|---|---|---|---|---|---|---|---|---|"]
    for s, r in (comp.get("scenarios") or {}).items():
        L.append(f"| {s} | {r['metrics_identical']} | {r['input_sha256_identical']} | {r['net_return']} | {r['win_rate']} | "
                 f"{r['payoff_ratio']} | {r['expectancy']} | {r['profit_factor']} | {r['max_drawdown']} |")
    L += ["", f"Total costs: {comp.get('total_costs')}",
          f"Presentation-only changes: {comp.get('presentation_reason_changes')}", "",
          f"Governance: {gov.get('authorization_uses')} uses; formal {gov.get('formal_market_status')}; "
          f"new batch consumed {gov.get('new_batch_consumed')}", "",
          f"Archive: source unchanged {archive['source_unchanged']}; MANIFEST pin {archive['manifest_pin_matches']}; "
          f"reference evaluation equals committed copy {archive['reference_evaluation_equals_committed_copy']['identical']}", "",
          f"Integration: RC {integ['rc_commit']} parents {integ['rc_parents']}", "",
          f"Tests (this runtime): {tests.get('summary')} (python {tests.get('python')}, core {tests.get('core')})", ""]
    return "\n".join(L)
