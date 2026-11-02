import json

from benchmark.harness.crosscheck import cases, compare, compare_one, export
from benchmark.oracle.reference_engine import run_case


def test_oracle_output_passes_harness(tmp_path):
    for c in cases():
        out = run_case(c)
        (tmp_path / f"{c['case_id']}.result.json").write_text(json.dumps(out))
    assert compare(tmp_path) == 0


def test_harness_flags_forbidden_fill():
    c = next(c for c in cases() if c["case_id"].startswith("B02"))
    bad = run_case(c)
    bad["fills"][0]["exec_ts"] = "2026-01-05 09:25"
    errs = compare_one(c, bad)
    assert any("FORBIDDEN" in e for e in errs)


def test_missing_results_fail(tmp_path):
    assert compare(tmp_path) == 1


def test_export_csv(tmp_path):
    export(tmp_path)
    files = sorted(tmp_path.glob("*.csv"))
    assert len(files) == 12
    header = files[0].read_text().splitlines()[0]
    assert "available_at_local" in header and "in_session" in header
