"""Phase 4-A: research-only integration with Core e31c57b (load_native_tsm / build_tsm_research_profile /
ExecutionEngine / tsm_cost_diagnostics), on a SYNTHETIC snapshot laid out exactly as Core requires.

All governance approvals happen in temporary copies (test fixtures). The synthetic Parquet is TEST_ONLY; the
opaque-byte receipt (verify_file_bytes) is produced here only for that synthetic file.
"""
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from strategy_agent.research_only.costs import CostSet
from strategy_agent.research_only.runner import ResearchOnlyRunner, canonical_sha

pd = pytest.importorskip("pandas")
pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")
tn = pytest.importorskip("market_research_core.tsm_native")
tc = pytest.importorskip("market_research_core.tsm_research_costs")

from strategy_agent.research_only import core_phase4a as P  # noqa: E402

from test_phase4a_research_only import NOW, gov  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CFG11 = ROOT / "configs" / "research_only" / "tsm_sma_5_20_smoke_2024_06_v1_1.json"
NY = "America/New_York"
HOLIDAYS = {date(2024, 5, 27), date(2024, 6, 19), date(2024, 7, 4), date(2025, 1, 1)}
needs_pinned_core = pytest.mark.skipif(tn.core_code_identity()["core_git_sha"] != P.CORE_PHASE4A_SHA,
                                       reason="installed Core is not the pinned Phase 4-A SHA")


def _day_rows(d: date, seed: float):
    t0 = pd.Timestamp(datetime(d.year, d.month, d.day, 9, 30), tz=NY)
    out = []
    for i in range(390):
        x = seed + i / 390
        mid = 170 + 6 * __import__("math").sin(x * 2.3) + 0.4 * __import__("math").sin(x * 41)
        out.append({"timestamp_utc": (t0 + pd.Timedelta(minutes=i)).tz_convert("UTC"), "open": round(mid, 2),
                    "high": round(mid + 0.05, 2), "low": round(mid - 0.05, 2), "close": round(mid + 0.01, 2),
                    "volume": float(1000 + i)})
    return out


def _days(a: date, b: date):
    d = a
    while d <= b:
        if d.weekday() < 5 and d not in HOLIDAYS:
            yield d
        d += timedelta(days=1)


def make_snapshot(tmp: Path, *, per_day_groups=True, extra_holdout=True):
    root = tmp / P.PINNED_RUN
    root.mkdir(parents=True)
    days = list(_days(date(2024, 5, 20), date(2024, 7, 3)))
    if extra_holdout:
        days += list(_days(date(2025, 1, 2), date(2025, 1, 3)))
    tables = [pa.Table.from_pandas(pd.DataFrame(_day_rows(d, i * 0.37)), preserve_index=False)
              for i, d in enumerate(days)]
    path = root / "tsm.parquet"
    if per_day_groups:                      # isolated existing partitions: one row group per trading day
        with pq.ParquetWriter(path, tables[0].schema) as w:
            for t in tables:
                w.write_table(t)
    else:                                   # one producer-default row group spanning research AND holdout
        pq.write_table(pa.concat_tables(tables), path)
    schema = {f.name: str(f.type) for f in pq.ParquetFile(path).schema_arrow}
    fsha = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {"run_name": P.PINNED_RUN, "producer": {"git_sha": P.PINNED_PRODUCER},
                "tsm": {"symbol": "TSM", "path": "tsm.parquet", "sha256": fsha,
                        "columns": {"timestamp": "timestamp_utc", "open": "open", "high": "high", "low": "low",
                                    "close": "close", "volume": "volume"}, "arrow_schema": schema}}
    (root / "manifest.json").write_text(json.dumps(manifest))
    msha = hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest()
    receipt = tn.verify_file_bytes(path, expected_sha256=fsha, manifest_sha256=msha,
                                   verification_ref="TEST_ONLY synthetic file", allow_full_file_hash=True)
    binding = {"manifest_relative_path": "manifest.json", "manifest_sha256": msha,
               "layout": {"snapshot_id": "/run_name", "producer_sha": "/producer/git_sha", "symbol": "/tsm/symbol",
                          "parquet_path": "/tsm/path", "parquet_sha256": "/tsm/sha256", "columns": "/tsm/columns",
                          "arrow_schema": "/tsm/arrow_schema"},
               "integrity_receipt": {"parquet_sha256": receipt.parquet_sha256,
                                     "manifest_sha256": receipt.manifest_sha256, "stat": receipt.stat,
                                     "verification_ref": receipt.verification_ref},
               "integrity_receipt_sha256": receipt.sha256, "diagnostic_parquet_relative_path": None}
    return root, binding


def cfg_with(tmp: Path, binding: dict | None = None, edit=None) -> Path:
    c = json.loads(CFG11.read_text())
    if binding is not None:
        c["snapshot_binding"] = binding | {"source": "TEST_ONLY", "null_means": "-"}
    if edit:
        edit(c)
    c["provenance"]["config_sha256"] = canonical_sha(c)
    p = tmp / "cfg11.json"
    p.write_text(json.dumps(c))
    return p


def run(runner, cfg, root, tmp, **kw):
    return runner.run(cfg, snapshot_root=root, archive_root=tmp / "archive", git_commit="abcdef1", actor="test",
                      **kw)


# ------------------------------------------------------------------------------------------------ repository

def test_repository_v1_1_config_is_ready_and_pins_core_phase4a():
    c = json.loads(CFG11.read_text())
    assert c["provenance"]["config_sha256"] == canonical_sha(c)
    assert c["core"]["phase4a_sha"] == P.CORE_PHASE4A_SHA
    assert P.CORE_PHASE4A_SHA in c["core"]["phase4a_contract_ref"]
    pf = ResearchOnlyRunner(clock=lambda: NOW).preflight(CFG11)
    lead_signed = (ROOT / "governance" / "research_authorizations.jsonl").read_text().strip() != ""
    if lead_signed:
        assert pf.status in ("READY_FOR_CORE_INTEGRATION", "BLOCKED")   # BLOCKED only once the slot is used (RA11)
        assert {k for k, _ in pf.blockers} <= {"RA11"}


def test_real_manifest_binding_records_missing_fields_as_null_not_guesses():
    b = json.loads(CFG11.read_text())["snapshot_binding"]
    assert [k for k, v in b["layout"].items() if v is None] == ["snapshot_id", "producer_sha", "parquet_path",
                                                                "columns", "arrow_schema"]
    assert b["manifest_sha256"] is None and b["integrity_receipt"] is None


def test_core_cost_profiles_and_cost_set_agree():
    c = json.loads(CFG11.read_text())["core_costs"]
    profiles = P.research_profiles(tc, sec_config=ROOT / c["sec_config_path"], sec_sha=c["sec_configuration_sha256"],
                                   taf_record=ROOT / c["taf_record_path"], taf_sha=c["taf_record_sha256"],
                                   scenarios=["BASE", "LOW", "STRESS", "CAT_STRESS"])
    assert {k: (v.equity_slippage_bps, v.regulatory_fee_per_unit) for k, v in profiles.items()} == {
        "BASE": (2.0, 0.000216), "LOW": (1.0, 0.000216), "STRESS": (4.0, 0.000216), "CAT_STRESS": (2.0, 0.000266)}
    taf = json.loads((ROOT / c["taf_record_path"]).read_text())
    assert taf["sha256"] == tc.rate_record_sha256(taf)
    assert P.cost_set_consistency(CostSet(), tc, taf, date(2024, 6, 3)) == []
    fake = SimpleNamespace(SLIPPAGE_BPS={"LOW": 1, "BASE": 3, "STRESS": 4}, CAT_PER_SHARE=tc.CAT_PER_SHARE)
    assert {c for c, _ in P.cost_set_consistency(CostSet(), fake, taf, date(2024, 6, 3))} == {"CO07"}


# --------------------------------------------------------------------------------- blocked before consumption

def test_real_like_layout_is_blocked_before_any_slot_is_consumed(tmp_path):
    runner, g = gov(tmp_path)
    root, _ = make_snapshot(tmp_path)
    (root / "final_gate_manifest.json").write_text(json.dumps(
        {"git_sha": "06359faf7ef4", "results": [{"symbol": "NQ"}, {"symbol": "ES"},
                                                {"symbol": "TSM", "published_sha256": "0" * 64}]}))
    real = json.loads(CFG11.read_text())["snapshot_binding"] | {"diagnostic_parquet_relative_path": "tsm.parquet"}
    out = run(runner, cfg_with(tmp_path, real), root, tmp_path)
    codes = {b["code"] for b in out["blockers"]}
    assert out["status"] == "BLOCKED_BEFORE_CONSUMPTION" and out["slot_consumed"] is False
    assert {"MANIFEST_LAYOUT", "SHA_REQUIRED", "INTEGRITY_RECEIPT"} <= codes
    assert (g / "uses.jsonl").read_text() == "" and not (tmp_path / "archive").exists()


def test_single_row_group_crossing_holdout_is_blocked_before_consumption(tmp_path):
    runner, g = gov(tmp_path)
    root, binding = make_snapshot(tmp_path, per_day_groups=False)
    out = run(runner, cfg_with(tmp_path, binding), root, tmp_path)
    assert {b["code"] for b in out["blockers"]} == {"HOLDOUT_ROW_GROUP_OVERLAP"}
    assert out["slot_consumed"] is False and (g / "uses.jsonl").read_text() == ""
    # Core itself agrees (independent enforcement inside load_native_tsm)
    b = binding
    with pytest.raises(tn.NativeSnapshotRejected) as e:
        tn.load_native_tsm(root, manifest_relative_path=b["manifest_relative_path"],
                           expected_manifest_sha256=b["manifest_sha256"], layout=tn.ManifestLayout(**b["layout"]),
                           integrity_receipt=tn.FileIntegrityReceipt(**b["integrity_receipt"]),
                           expected_receipt_sha256=b["integrity_receipt_sha256"], symbol="TSM",
                           dates=tn.DateWindow("2024-05-29", "2024-06-28"),
                           research_window=tn.DateWindow("2024-05-29", "2024-06-28"),
                           holdout=(tn.DateWindow("2025-01-01", "2026-10-16"),))
    assert e.value.code == "HOLDOUT_ROW_GROUP_OVERLAP"


def test_unpinned_core_identity_is_blocked_before_consumption(tmp_path):
    runner, g = gov(tmp_path)
    root, binding = make_snapshot(tmp_path)
    fake_native = SimpleNamespace(core_code_identity=lambda: {"core_git_sha": "a" * 40, "core_tree_dirty": False})
    out = run(runner, cfg_with(tmp_path, binding), root, tmp_path, core_native=fake_native, core_costs=tc)
    assert {b["code"] for b in out["blockers"]} == {"RO05"} and (g / "uses.jsonl").read_text() == ""


def test_stale_receipt_is_blocked_before_consumption(tmp_path):
    runner, g = gov(tmp_path)
    root, binding = make_snapshot(tmp_path)
    binding["integrity_receipt"]["stat"] = dict(binding["integrity_receipt"]["stat"], inode=1)
    out = run(runner, cfg_with(tmp_path, binding), root, tmp_path)
    assert "INTEGRITY_RECEIPT" in {b["code"] for b in out["blockers"]} and (g / "uses.jsonl").read_text() == ""


# ------------------------------------------------------------------------------------------ end-to-end (synthetic)

@needs_pinned_core
def test_synthetic_end_to_end_completes_once_then_slot_is_spent(tmp_path):
    runner, g = gov(tmp_path)
    root, binding = make_snapshot(tmp_path)
    cfg = cfg_with(tmp_path, binding)
    out = run(runner, cfg, root, tmp_path)
    assert out["status"] == "COMPLETED", out.get("blockers") or out.get("error")
    assert out["slot_consumed"] and len((g / "uses.jsonl").read_text().splitlines()) == 1
    assert set(out["scenarios"]) == {"BASE", "LOW", "STRESS", "CAT_STRESS"}
    prov = out["loader_provenance"]
    assert prov["integrity_method"] == "REUSED_TRUSTED_SHA256_RECEIPT_WITH_STAT_GUARD"
    assert prov["requested_dates"] == {"start": "2024-05-29", "end": "2024-06-28"}
    assert prov["core_git_sha"] == P.CORE_PHASE4A_SHA
    # holdout rows (2025-01-02/03) and pre-warm-up rows exist in the file but were never returned
    assert out["counts"]["native_1m_rows"] == 390 * len(list(_days(date(2024, 5, 29), date(2024, 6, 28))))
    for name, s in out["scenarios"].items():
        t = s["cost_totals"]
        assert set(t) >= {"sec", "taf", "cat", "commission", "price_adjustment_cost", "regulatory_total"}
        assert s["sample"] in ("INSUFFICIENT_SAMPLE", "SAMPLE_OK")
    # same decisions/orders in every scenario; slippage ordering LOW <= BASE <= STRESS on price adjustment
    pac = {k: float(v["cost_totals"]["price_adjustment_cost"]) for k, v in out["scenarios"].items()}
    if out["counts"]["orders"]:
        assert pac["LOW"] < pac["BASE"] < pac["STRESS"]
        assert out["cat_rounding"]["status"] in ("ROUNDING_MASKED_DIFFERENCE", "DIFFERENCE_VISIBLE")
    arch = tmp_path / "archive" / out["run_id"]
    m = json.loads((arch / "MANIFEST.json").read_text())
    for rel, sha in m["files_sha256"].items():
        assert hashlib.sha256((arch / rel).read_bytes()).hexdigest() == sha
    again = run(runner, cfg, root, tmp_path)
    assert again["executed"] is False and "RA11" in {b["code"] for b in again["blockers"]}


@needs_pinned_core
def test_failure_after_consumption_is_archived_and_never_reset(tmp_path):
    runner, g = gov(tmp_path)
    root, binding = make_snapshot(tmp_path)
    cfg = cfg_with(tmp_path, binding)

    class Boom(SimpleNamespace):
        def load_native_tsm(self, *a, **k):
            raise tn.NativeSnapshotRejected("SOURCE_CHANGED", "simulated")

    native = Boom(**{k: getattr(tn, k) for k in ("core_code_identity", "DateWindow", "ManifestLayout",
                                                 "FileIntegrityReceipt")})
    out = run(runner, cfg, root, tmp_path, core_native=native, core_costs=tc)
    assert out["status"] == "FAILED_AFTER_CONSUMPTION" and out["slot_consumed"] and out["code"] == "SOURCE_CHANGED"
    assert (tmp_path / "archive" / out["run_id"] / "FAILURE.json").is_file()
    again = run(runner, cfg, root, tmp_path)
    assert again["executed"] is False and "RA11" in {b["code"] for b in again["blockers"]}


def test_probe_never_consumes_even_when_compatible(tmp_path):
    runner, g = gov(tmp_path)
    root, binding = make_snapshot(tmp_path)
    out = runner.probe(cfg_with(tmp_path, binding), snapshot_root=root)
    if tn.core_code_identity()["core_git_sha"] == P.CORE_PHASE4A_SHA:
        assert out["status"] == "COMPATIBLE_READY_TO_CONSUME", out["blockers"]
    assert out["slot_consumed"] is False and (g / "uses.jsonl").read_text() == ""
    rgs = out["probe"]["observed"]["parquet"]["row_groups"]
    assert all(r["inside_authorized_window"] and not r["overlaps_holdout"] for r in rgs if r["candidate"])
    assert set(out["profiles"]) == {"BASE", "LOW", "STRESS", "CAT_STRESS"}
