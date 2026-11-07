"""Phase 4-C read-only archive acceptance.

SYNTHETIC_FIXTURE: tests/fixtures/phase4c/ was produced by the real Phase 4-A producer on a synthetic development
snapshot (see its README). Tampered variants re-hash MANIFEST.json to simulate a producer emitting bad data with
consistent hashes; a synthetic PASS never replaces the real-archive acceptance.
"""
import csv
import hashlib
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from strategy_agent.evaluation import phase4c as C

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests/fixtures/phase4c"
ARCH = FIX / "20261106_224000_0123456"
GOV = FIX / "governance_root"
SHA = "a" * 40
AT = "2026-11-07T09:00:00+08:00"


def msha(p):
    return hashlib.sha256((Path(p) / "MANIFEST.json").read_bytes()).hexdigest()


def copy(tmp, gov=True):
    a = tmp / ARCH.name
    shutil.copytree(ARCH, a)
    g = tmp / "gov"
    if gov:
        shutil.copytree(GOV, g)
    return a, g


def tamper(arch, name, fn):
    """Apply fn to a file and re-hash it in MANIFEST.json (bad producer with consistent hashes)."""
    p = arch / name
    p.write_bytes(fn(p.read_bytes()))
    m = json.loads((arch / "MANIFEST.json").read_text())
    m["files_sha256"][name] = hashlib.sha256(p.read_bytes()).hexdigest()
    (arch / "MANIFEST.json").write_text(json.dumps(m, indent=2, sort_keys=True))
    return msha(arch)


def csv_edit(fn):
    def go(b):
        rows = list(csv.DictReader(io.StringIO(b.decode())))
        cols = list(rows[0])
        rows = fn(rows)
        out = io.StringIO()
        w = csv.DictWriter(out, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
        return out.getvalue().encode()
    return go


def accept(a, g, pin=None):
    return C.accept(a, expected_manifest_sha256=pin or msha(a), evaluator_commit_sha=SHA, evaluated_at=AT,
                    governance_root=g)


def test_correct_archive_passes_all_checks_and_is_retest_undetermined():
    before = C.fingerprint(ARCH)
    acc = accept(ARCH, GOV)
    st = {k: v["status"] for k, v in acc["checks"].items()}
    # C8 also asserts no executor module is loaded in the PROCESS; other tests in this pytest process import the runner,
    # so the fresh-process guarantee is tested separately (test_spent_budget_read_only_flow_and_no_executor).
    c8 = acc["checks"]["C8_governance_isolation"]
    assert all(v == "PASS" for k, v in st.items() if k != "C8_governance_isolation"), st
    assert c8["status"] == "PASS" or c8["executor_modules_loaded"]
    ev = acc["evaluation"]
    assert ev["engineering_status"] == "PASS"
    assert (ev["strategy_verdict"], ev["evidence_sufficiency"]) == ("RETEST", "UNDETERMINED")
    codes = {f["code"] for f in ev["risk_flags"]}
    assert {"INSUFFICIENT_SAMPLE", "BLINDNESS_UNVERIFIED", "IN_SAMPLE_VS_OUT_OF_SAMPLE_NOT_TESTED",
            "MARKET_REGIME_COVERAGE_NOT_ESTABLISHED", "LESS_THAN_ONE_YEAR_OBSERVED"} <= codes
    assert C.fingerprint(ARCH) == before
    c3 = acc["checks"]["C3_trade_integrity"]["scenarios"]["BASE"]
    assert c3["trades_matched_to_entry_and_exit_fills"] == c3["trades"] and c3["fills"] == 2 * c3["trades"]
    for s, row in acc["checks"]["C4_performance_consistency"]["scenarios"].items():
        assert all(r["match"] for r in row["rows"].values()), s


def test_manifest_sha_mismatch_blocks_before_reading_anything(tmp_path):
    acc = accept(ARCH, GOV, pin="0" * 64)
    assert acc["status"] == "BLOCKED" and acc["error"]["code"] == "ARCHIVE_MANIFEST_SHA"
    assert "evaluation" not in acc


def test_dataset_manifest_sha_is_not_accepted_as_archive_manifest_pin():
    ds = json.loads((ARCH / "dataset_manifest.json").read_text())["loader_provenance"]["manifest_sha256"]
    assert accept(ARCH, GOV, pin=ds)["error"]["code"] == "ARCHIVE_MANIFEST_SHA"


def test_artifact_byte_tamper_without_manifest_update_is_rejected(tmp_path):
    a, g = copy(tmp_path)
    pin = msha(a)
    (a / "trades.csv").write_bytes((a / "trades.csv").read_bytes() + b"\n")
    assert accept(a, g, pin)["error"]["code"] == "ARCHIVE_ARTIFACT_SHA"


def test_missing_trades_file_and_unlisted_file_are_rejected(tmp_path):
    a, g = copy(tmp_path)
    (a / "trades.csv").unlink()
    m = json.loads((a / "MANIFEST.json").read_text())
    del m["files_sha256"]["trades.csv"]
    (a / "MANIFEST.json").write_text(json.dumps(m))
    assert accept(a, g)["error"]["code"] == "ARCHIVE_CONTRACT"
    b, _ = copy(tmp_path / "b", gov=False)
    (b / "holdout_ohlcv.parquet").write_bytes(b"x")
    assert accept(b, g)["error"]["code"] == "ARCHIVE_UNLISTED_FILES"


def test_missing_trade_rows_are_not_reconstructed(tmp_path):
    a, g = copy(tmp_path)
    pin = tamper(a, "trades.csv", csv_edit(lambda rows: [r for r in rows if r["scenario"] != "BASE"]))
    acc = accept(a, g, pin)
    assert acc["status"] == "FAIL" and acc["checks"]["C3_trade_integrity"]["status"] == "FAIL"
    assert acc["evaluation"]["engineering_status"] != "PASS"     # upstream metrics disagree, nothing rebuilt
    assert acc["evaluation"]["strategy_verdict"] != "KEEP"


def test_wrong_cost_data_fails(tmp_path):
    a, g = copy(tmp_path)
    pin = tamper(a, "cost_breakdown.csv", csv_edit(lambda rows: [dict(r, commission="999.0") if r["scenario"] == "BASE"
                                                                 else r for r in rows]))
    acc = accept(a, g, pin)
    assert acc["checks"]["C4_performance_consistency"]["status"] == "FAIL"
    assert any("COST_RECONCILIATION" in e for e in acc["evaluations"]["BASE"]["errors"])


def test_wrong_timestamp_fails_temporal_and_evaluator(tmp_path):
    a, g = copy(tmp_path)

    def early(rows):
        rows[0] = dict(rows[0], exec_ts=rows[0]["signal_available_at"])
        return rows
    pin = tamper(a, "fills.csv", csv_edit(early))
    acc = accept(a, g, pin)
    assert acc["checks"]["C5_temporal_safety"]["status"] == "FAIL"
    assert any("LOOK_AHEAD" in e for e in acc["evaluations"]["BASE"]["errors"])


def test_missing_provenance_blocks(tmp_path):
    a, g = copy(tmp_path)
    pin = tamper(a, "run_info.txt", lambda b: b"".join(x for x in b.splitlines(True) if not x.startswith(b"agent_git_sha=")))
    acc = accept(a, g, pin)
    assert acc["checks"]["C1_archive_identity"]["status"] == "FAIL"
    assert acc["evaluation"]["engineering_status"] == "BLOCKED"


def test_duplicate_trade_fails(tmp_path):
    a, g = copy(tmp_path)
    pin = tamper(a, "trades.csv", csv_edit(lambda rows: rows + [rows[0]]))
    acc = accept(a, g, pin)
    assert "DUPLICATE_TRADE" in acc["checks"]["C3_trade_integrity"]["scenarios"]["BASE"]["problems"]
    assert any("duplicate" in e for e in acc["evaluations"]["BASE"]["errors"])


def test_inconsistent_trade_count_fails(tmp_path):
    a, g = copy(tmp_path)

    def bump(b):
        m = json.loads(b)
        m["BASE"]["trades"]["trade_count"] += 1
        return json.dumps(m).encode()
    pin = tamper(a, "metrics.json", bump)
    acc = accept(a, g, pin)
    assert acc["checks"]["C4_performance_consistency"]["scenarios"]["BASE"]["status"] == "FAIL"


def test_missing_holdout_evidence_blocks(tmp_path):
    a, g = copy(tmp_path)
    (g / "governance/holdout_locks.jsonl").write_text("")
    acc = accept(a, g)
    assert acc["evaluation"]["engineering_status"] == "BLOCKED"
    assert acc["checks"]["C5_temporal_safety"]["status"] == "FAIL"
    assert any("REQUIRED_EVIDENCE:holdout" in e for e in acc["evaluation"]["errors"])


def test_spent_budget_read_only_flow_and_no_executor(tmp_path):
    a, g = copy(tmp_path)
    before = (g / "registry/research_authorization_uses.jsonl").read_bytes()
    code = f"""
import json,sys
from strategy_agent.evaluation import phase4c as C
acc=C.accept({str(a)!r},expected_manifest_sha256={msha(a)!r},evaluator_commit_sha={SHA!r},evaluated_at={AT!r},
             governance_root=__import__('pathlib').Path({str(g)!r}))
bad=[m for m in C.EXECUTOR_MODULES if m in sys.modules]
print(json.dumps({{'c8':acc['checks']['C8_governance_isolation'],'bad':bad}}))
"""
    out = json.loads(subprocess.check_output([sys.executable, "-c", code], text=True, cwd=ROOT).strip().splitlines()[-1])
    assert out["bad"] == [] and out["c8"]["status"] == "PASS"
    assert out["c8"]["authorization_uses"] == "1/1" and out["c8"]["evaluation_batch_consumption"] == 0
    assert (g / "registry/research_authorization_uses.jsonl").read_bytes() == before


def test_outputs_are_immutable_and_rerun_never_overwrites(tmp_path):
    acc = accept(ARCH, GOV)
    out = tmp_path / "evaluations" / C.evaluation_dir_name(AT, SHA)
    files = C.write_acceptance(acc, out, evaluator_commit_sha=SHA, evaluated_at=AT)
    assert set(files) == set(C.OUTPUTS)
    with pytest.raises(FileExistsError):
        C.write_acceptance(acc, out, evaluator_commit_sha=SHA, evaluated_at=AT)
    again = tmp_path / "evaluations" / "second"
    files2 = C.write_acceptance(accept(ARCH, GOV), again, evaluator_commit_sha=SHA, evaluated_at=AT)
    for n in ("evaluation.json", "risk_flags.json", "research_decision.json", "robustness_matrix.csv"):
        assert files[n] == files2[n], n                        # C7: byte-identical with fixed time
    info = (out / "run_info.txt").read_text()
    for k in ("source_run_id=20261106_224000_0123456", "source_archive_manifest_sha256=" + msha(ARCH),
              "evaluator_commit=" + SHA, "evaluation_timestamp=" + AT, "schema_version=1.0.0"):
        assert k in info
    with pytest.raises(ValueError):
        C.write_acceptance(acc, ARCH / "evaluation", evaluator_commit_sha=SHA, evaluated_at=AT)
    assert not (ARCH / "evaluation").exists()


def test_blocked_acceptance_still_writes_failure_diagnostics(tmp_path):
    acc = accept(ARCH, GOV, pin="1" * 64)
    files = C.write_acceptance(acc, tmp_path / "fail", evaluator_commit_sha=SHA, evaluated_at=AT)
    assert set(files) == {"acceptance_checks.json", "run_info.txt"}
    assert json.loads((tmp_path / "fail/acceptance_checks.json").read_text())["status"] == "BLOCKED"


def test_phase4c_modules_never_import_an_executor():
    import ast
    for f in ("phase4c.py", "universal.py", "archive_input.py"):
        tree = ast.parse((ROOT / "src/strategy_agent/evaluation" / f).read_text())
        mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module} | \
               {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not [m for m in mods if any(m.startswith(x) for x in ("strategy_agent.research_only", "strategy_agent.adapters",
                                                                     "market_research_core", "research_only", "adapters"))], f
