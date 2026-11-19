"""TA-01 fill diagnostic: authorization documents, approval binding, pre-data checks and the one-time attempt
protocol. Synthetic files only: no Core, no market data, no Drive, no network."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from strategy_agent.approval.testkit import FixtureApprovals
from strategy_agent.approval.verifier import ApprovalRejected
from strategy_agent.execution import c1_integration as C
from strategy_agent.readiness.c1_authorization import PINS
from strategy_agent.research_only import ta01_fill_diagnostic as D
from strategy_agent.research_only.runner import write_once

ROOT = Path(__file__).resolve().parents[1]
ID1, ID2, IDQ8, IDQ8X = ("RA-IBKR_TSM-TA01_FILLDIAG-0001", "RA-IBKR_TSM-TA01_FILLDIAG-0002",
                          "RA-IBKR_TSM-TA01Q8_FILLDIAG-0001", "RA-IBKR_TSM-TA01Q8X_FILLDIAG-0001")
DOC1 = D.load_authorization(ID1, ROOT, allow_retired=True)
DOC2 = D.load_authorization(ID2, ROOT, allow_retired=True)       # used (SUCCEEDED 2026-11-19): retired
DOCQ8 = D.load_authorization(IDQ8, ROOT, allow_retired=True)     # used (SUCCEEDED 2026-11-19): retired
DOCQ8X = D.load_authorization(IDQ8X, ROOT)
# hashes in hh4832's Q8 approval comment (pull/37#issuecomment-7001566877)
APPROVED_Q8 = ("230151b5f14a44137c99454e93bb94bc6a4f517b404e590a94485bb918a98013",
               "339fafba9e02519c864c0ec8acf63046c5ca2cb1e2eca621a0e680ffcef1c38a")
# hashes in hh4832's 0002 approval comment (pull/35#issuecomment-6994446298)
APPROVED_0002 = ("3175c1d148bf9cd84894803f39acc9507878df29d906af6bd127bc73afbf1b30",
                 "03b67ba69111feab71820e2a81a8311b50b055734b9a36bd01b6a5ba58e33517")
# hashes in hh4832's 0001 approval comment (pull/34#issuecomment-6978313388)
APPROVED_0001 = ("eacb3c0b8011faecff7bcd8cdf1b2bb0c72f4bd5d39ab79ac18ef03d261eccbf",
                 "d4ed88f48f6d4ca0f35f0c4cac1c15efd429bc4c7e2a3ba1bc7fb19f02448725")
BARE = "https://github.com/hh4832/strategy-research-agent/pull/34#issuecomment-6978313388"


def expires():
    return (datetime.now(timezone.utc) + timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------ documents

def test_0001_document_is_unchanged_and_retired_in_every_mode(tmp_path):
    assert (D.sha256_of(DOC1), D.sha256_of(DOC1["scope"])) == APPROVED_0001
    with pytest.raises(D.DiagnosticRefused, match="retired"):
        D.load_authorization(ID1, ROOT)
    disp = json.loads((ROOT / "docs/phase5c/ta01_fill_diagnostic_0001_disposition.json").read_text())
    assert disp["classification"] == "PRE_DATA_INPUT_PATH_FAILURE"
    assert (disp["approved_document_sha256"], disp["approved_scope_sha256"]) == APPROVED_0001
    assert disp["disposition"]["drive_directory"].startswith("LEFT_AS_IS")
    for mode in ("PREPARE", "RUN"):
        s = tmp_path / f"{mode}.json"
        s.write_text(json.dumps({"mode": mode, "authorization_id": ID1, "approval_expires_utc": expires()}))
        assert D.main(["--settings", str(s)]) == 2


def test_0002_document_is_unchanged_and_kept_the_0001_research_scope():
    s1, s2 = DOC1["scope"], DOC2["scope"]
    assert (D.sha256_of(DOC2), D.sha256_of(s2)) == APPROVED_0002          # bound by the 0002 approval comment
    assert sorted(k for k in set(s1) | set(s2) if s1.get(k) != s2.get(k)) == ["attempt_protocol", "inputs", "supersedes"]
    assert s2["dataset"]["reviewed_pins"] == PINS and s2["window"] == dict(C.WINDOW) and s2["holdout"]["access"] == "NONE"
    assert s2["strategy"]["spec_file_sha256"] == C.sha(ROOT / s2["strategy"]["spec_path"])
    assert s2["costs"]["dated_schedule_sha256"] == C.SCHEDULE_SHA256 and s2["max_runs"] == 1
    assert s2["inputs"]["recovery_archive"].startswith("/content/drive/MyDrive/00Quant_Research/data/development_recovery_archives/")
    assert s2["inputs"]["drive_output_root"] == D.DRIVE_OUTPUT_ROOT
    assert D.sha256_of(DOC2) not in APPROVED_0001 and D.sha256_of(s2) not in APPROVED_0001


def test_0002_approval_line_and_0001_comment_cannot_approve_0002():
    fx = FixtureApprovals()
    line = D.approval_line(DOC2, expires())
    assert f"authorization={ID2}" in line and D.sha256_of(DOC2) in line and D.sha256_of(DOC2["scope"]) in line
    ok = fx.comment(line)
    assert D.verify_approval(DOC2, ok, verifier=fx.verifier)["approval_source"]["trust"] == "ISOLATED_TEST"
    old = fx.comment(D.approval_line(DOC1, expires()))
    with pytest.raises(ApprovalRejected):
        D.verify_approval(DOC2, old, verifier=fx.verifier)
    other = fx.comment(line, login="someone", uid=1)
    with pytest.raises(ApprovalRejected):
        D.verify_approval(DOC2, other, verifier=fx.verifier)


@pytest.mark.parametrize("url", [f"[{BARE}]({BARE})", f"<{BARE}>", f" {BARE}", f"{BARE} ", f"{BARE}?x=1",
                                 BARE.replace("hh4832/strategy-research-agent", "evil/repo"), "", None])
def test_approval_url_must_be_bare(url):
    with pytest.raises(D.DiagnosticRefused, match="bare URL"):
        D.check_approval_url(url)


def test_bare_approval_url_is_accepted():
    assert D.check_approval_url(BARE) == BARE


# ------------------------------------------------------------------ pre-data precheck (synthetic archive)

def recovery_archive(tmp_path):
    root = tmp_path / "recovery"
    snap = root / C.SNAPSHOT
    snap.mkdir(parents=True)
    (root / "RECOVERY_VERIFIED.json").write_text('{"status": "PASS"}')
    (root / "recovery_provenance.json").write_text('{"synthetic": true}')
    (snap / "binding.json").write_text('{"manifest_relative_path": "manifest.json"}')
    (snap / "manifest.json").write_text("{}")
    pins = {"marker_sha256": D._sha_file(root / "RECOVERY_VERIFIED.json"),
            "recovery_provenance_sha256": D._sha_file(root / "recovery_provenance.json"),
            "binding_sha256": D._sha_file(snap / "binding.json")}
    return root, pins


def good_metadata(pins):
    return lambda archive: {"context": {"pin_review_evidence_ref": "synthetic"}, "pins": {"observed_pins": pins}}


def test_precheck_passes_on_complete_synthetic_archive(tmp_path):
    root, pins = recovery_archive(tmp_path)
    out = D.precheck_inputs(str(root), "orig", pins=pins, metadata_fn=good_metadata(pins))
    assert out == {"original_preparation_archive": "METADATA_VERIFIED", "recovery_archive": "PATHS_AND_METADATA_VERIFIED"}


@pytest.mark.parametrize("breakage", ["no_archive", "no_marker", "marker_changed", "marker_not_pass",
                                      "no_binding", "no_manifest"])
def test_precheck_recovery_failures(tmp_path, breakage):
    root, pins = recovery_archive(tmp_path)
    snap = root / C.SNAPSHOT
    if breakage == "no_archive":
        root = tmp_path / "Quant_Research/development_recovery_archives/missing"
    elif breakage == "no_marker":
        (root / "RECOVERY_VERIFIED.json").unlink()
    elif breakage == "marker_changed":
        (root / "RECOVERY_VERIFIED.json").write_text('{"status": "PASS", "x": 1}')
    elif breakage == "marker_not_pass":
        (root / "RECOVERY_VERIFIED.json").write_text('{"status": "FAIL"}')
        pins["marker_sha256"] = D._sha_file(root / "RECOVERY_VERIFIED.json")
    elif breakage == "no_binding":
        (snap / "binding.json").unlink()
    else:
        (snap / "manifest.json").unlink()
    with pytest.raises(D.PreDataInputFailure):
        D.precheck_inputs(str(root), "orig", pins=pins, metadata_fn=good_metadata(pins))


@pytest.mark.parametrize("meta", ["raises", "no_review_ref", "pins_differ"])
def test_precheck_metadata_failures(tmp_path, meta):
    root, pins = recovery_archive(tmp_path)

    def fn(archive):
        if meta == "raises":
            raise FileNotFoundError(archive)
        return {"context": {"pin_review_evidence_ref": None if meta == "no_review_ref" else "x"},
                "pins": {"observed_pins": pins if meta != "pins_differ" else {**pins, "binding_sha256": "0" * 64}}}
    with pytest.raises(D.PreDataInputFailure, match="ORIGINAL_PREPARATION_ARCHIVE"):
        D.precheck_inputs(str(root), "orig", pins=pins, metadata_fn=fn)


# ------------------------------------------------------------------ one-time attempt protocol

class Harness:
    def __init__(self, tmp_path, doc=DOCQ8X):
        self.fx = FixtureApprovals()
        self.doc = doc
        self.drive = tmp_path / "drive"
        self.drive.mkdir(parents=True)
        self.recovery, self.pins = recovery_archive(tmp_path)
        self.settings = {"confirm_authorization": doc["authorization_id"],
                         "approval_url": None, "code_sha": "c" * 40,
                         "recovery_archive": doc["scope"]["inputs"]["recovery_archive"],
                         "original_preparation_archive": doc["scope"]["inputs"]["original_preparation_archive"]}
        url = self.fx.comment(D.approval_line(doc, expires()))
        # fixture URLs are bare GitHub comment URLs of the same form
        self.settings["approval_url"] = url
        self.calls = []

    def precheck(self, recovery=None, metadata_fn=None):
        rec = recovery or self.recovery
        return lambda r, o: D.precheck_inputs(str(rec), o, pins=self.pins,
                                              metadata_fn=metadata_fn or good_metadata(self.pins))

    def run(self, execute=None, **kw):
        def default():
            self.calls.append("execute")
            return {"diagnostic_id": D.DIAGNOSTIC_ID, "status": "COMPLETED", "entry": {"intents": 1}}
        return D.run_attempt(self.doc, settings=self.settings, drive_root=self.drive, verifier=self.fx.verifier,
                             precheck=kw.pop("precheck", self.precheck()), execute=execute or default, **kw)

    def tree(self):
        return sorted(str(p.relative_to(self.drive)) for p in self.drive.rglob("*"))


def test_missing_recovery_path_creates_no_output_directory(tmp_path):
    h = Harness(tmp_path)
    with pytest.raises(D.PreDataInputFailure, match="does not exist"):
        h.run(precheck=h.precheck(recovery=tmp_path / "Quant_Research/development_recovery_archives/wrong"))
    assert h.tree() == [] and h.calls == []


def test_metadata_precheck_failure_creates_nothing(tmp_path):
    h = Harness(tmp_path)

    def bad(archive):
        raise ValueError("Drive archive index differs from retained original")
    with pytest.raises(D.PreDataInputFailure):
        h.run(precheck=h.precheck(metadata_fn=bad))
    assert h.tree() == [] and h.calls == []


def test_markdown_url_and_wrong_paths_are_refused_before_anything(tmp_path):
    h = Harness(tmp_path)
    url = h.settings["approval_url"]
    h.settings["approval_url"] = f"[{url}]({url})"
    with pytest.raises(D.DiagnosticRefused, match="bare URL"):
        h.run()
    h.settings["approval_url"] = url
    h.settings["recovery_archive"] = "/content/drive/MyDrive/Quant_Research/development_recovery_archives/x"
    with pytest.raises(D.PreDataInputFailure, match="differs from the path pinned"):
        h.run()
    assert h.tree() == [] and h.calls == []


def test_failure_after_attempt_marker_leaves_sanitized_failure_manifest(tmp_path):
    h = Harness(tmp_path)
    token = "ghp_" + "A" * 36

    def boom():
        raise RuntimeError(f"stage failed reading snapshot; header Authorization: {token}")
    with pytest.raises(RuntimeError):
        h.run(execute=boom, secrets=(token,))
    assert h.tree() == [IDQ8X, f"{IDQ8X}/ATTEMPT_STARTED.json", f"{IDQ8X}/FAILURE_MANIFEST.json",
                        "_attempt_witness", f"_attempt_witness/{IDQ8X}.ATTEMPT_STARTED.json"]
    fail = json.loads((h.drive / IDQ8X / "FAILURE_MANIFEST.json").read_text())
    assert fail["status"] == "FAILED" and token not in json.dumps(fail) and "[REDACTED]" in fail["error"]
    assert fail["files_sha256"]["ATTEMPT_STARTED.json"] == hashlib.sha256(
        (h.drive / IDQ8X / "ATTEMPT_STARTED.json").read_bytes()).hexdigest()
    with pytest.raises(D.DiagnosticRefused, match="already attempted"):    # no retry under the same id
        h.run()


def test_success_writes_manifest_and_outputs_cannot_be_overwritten(tmp_path):
    h = Harness(tmp_path)
    h.run()
    out = h.drive / IDQ8X
    man = json.loads((out / "MANIFEST.json").read_text())
    assert man["status"] == "SUCCEEDED" and set(man["files_sha256"]) == {"ATTEMPT_STARTED.json",
                                                                          "ta01_fill_diagnostic.json"}
    for name in ("MANIFEST.json", "ta01_fill_diagnostic.json", "ATTEMPT_STARTED.json"):
        with pytest.raises(FileExistsError):
            write_once(out / name, b"overwrite")
    with pytest.raises(D.DiagnosticRefused, match="already attempted"):
        h.run()
    assert h.calls == ["execute"]


def test_performance_like_result_is_refused_and_recorded_as_failure(tmp_path):
    h = Harness(tmp_path)
    with pytest.raises(D.DiagnosticRefused):
        h.run(execute=lambda: {"diagnostic_id": "x", "entry": {"net_pnl": 1}})
    assert (h.drive / IDQ8X / "FAILURE_MANIFEST.json").exists() and not (h.drive / IDQ8X / "MANIFEST.json").exists()


def test_existing_empty_directory_or_witness_blocks_and_deleting_the_directory_does_not_help(tmp_path):
    h = Harness(tmp_path)
    (h.drive / IDQ8X).mkdir()                                    # an empty directory counts as an attempt
    with pytest.raises(D.DiagnosticRefused, match="already attempted"):
        h.run()
    (h.drive / IDQ8X).rmdir()
    h2 = Harness(tmp_path / "second")

    def boom():
        raise RuntimeError("x")
    with pytest.raises(RuntimeError):
        h2.run(execute=boom)
    for p in sorted((h2.drive / IDQ8X).iterdir()):
        p.unlink()
    (h2.drive / IDQ8X).rmdir()                                    # directory removed, witness remains
    with pytest.raises(D.DiagnosticRefused, match="already attempted"):
        h2.run()


def test_0001_cannot_be_reused_even_with_an_empty_drive(tmp_path):
    h = Harness(tmp_path, doc={**DOC1, "scope": {**DOC1["scope"], "inputs": DOC2["scope"]["inputs"]}})
    with pytest.raises(D.DiagnosticRefused, match="retired"):
        h.run()
    assert h.tree() == []


def test_drive_root_cannot_be_changed_to_bypass(tmp_path):
    s = tmp_path / "s.json"
    s.write_text(json.dumps({"mode": "RUN", "authorization_id": IDQ8X, "drive_root": str(tmp_path / "elsewhere")}))
    assert D.main(["--settings", str(s)]) == 2
    assert not (tmp_path / "elsewhere").exists()


# ------------------------------------------------------------------ guards kept from v1

def test_output_guard_refuses_performance_fields():
    for bad in ({"entry": {"net_pnl": 1.0}}, {"entry": {}, "sharpe": 1}, {"entry": {"fill_price_median": 1}}):
        with pytest.raises(D.DiagnosticRefused):
            D.assert_no_performance_fields(bad)
    D.assert_no_performance_fields({"entry": {"intents": 3}, "not_output": ["PnL", "returns"]})


def test_prepare_mode_prints_q8_approval_line_without_data_and_refuses_used_0002(tmp_path, capsys):
    s = tmp_path / "s.json"
    s.write_text(json.dumps({"mode": "PREPARE", "authorization_id": IDQ8X, "approval_expires_utc": "2026-11-26T00:00:00Z"}))
    assert D.main(["--settings", str(s)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["data_read"] is False and out["status"] == "UNSIGNED_UNTIL_LEAD_APPROVAL_COMMENT"
    assert out["document_sha256"] == D.sha256_of(DOCQ8X) and f"authorization={IDQ8X}" in out["approval_line"]
    s.write_text(json.dumps({"mode": "PREPARE", "authorization_id": ID2, "approval_expires_utc": "2026-11-26T00:00:00Z"}))
    assert D.main(["--settings", str(s)]) == 2
    assert (D.sha256_of(DOC2), D.sha256_of(DOC2["scope"])) == APPROVED_0002      # 0002 document never changed


def test_main_prints_sanitized_complete_error(tmp_path, capsys, monkeypatch):
    token = "ghp_" + "B" * 36
    monkeypatch.setenv("GITHUB_TOKEN", token)
    s = tmp_path / "s.json"
    s.write_text(json.dumps({"mode": "RUN", "authorization_id": IDQ8X, "drive_root": D.DRIVE_OUTPUT_ROOT,
                             "confirm_authorization": IDQ8X, "approval_url": f"[x]({token})"}))
    monkeypatch.setattr(D, "_main", lambda settings, secrets: (_ for _ in ()).throw(
        RuntimeError(f"failed with {token}")))
    assert D.main(["--settings", str(s)]) == 2
    err = capsys.readouterr().err
    assert "TA01_FILL_DIAGNOSTIC_ERROR" in err and "Traceback" in err and token not in err


def test_notebook_prepare_never_mounts_drive_and_run_uses_pinned_paths():
    nb = json.loads((ROOT / "notebooks/Phase5C_TA01_Fill_Diagnostic.ipynb").read_text())
    settings, code = "".join(nb["cells"][1]["source"]), "".join(nb["cells"][2]["source"])
    lines = code.splitlines()
    gate = lines.index('if MODE == "RUN":  # PREPARE never mounts Drive and never reads a credential')
    for needle in ("from google.colab import drive", "drive.mount(", "userdata.get("):
        hits = [i for i, line in enumerate(lines) if needle in line]
        assert hits and all(i > gate and lines[i].startswith("    ") for i in hits), needle
    assert settings.startswith("# DO NOT set MODE=\"RUN\"") and 'MODE = "PREPARE"' in settings
    assert f'AUTHORIZATION_ID = "{IDQ8X}"' in settings
    assert f'RECOVERY_ARCHIVE = "{DOCQ8X["scope"]["inputs"]["recovery_archive"]}"' in settings
    assert f'ORIGINAL_PREPARATION_ARCHIVE = "{DOCQ8X["scope"]["inputs"]["original_preparation_archive"]}"' in settings
    assert ID1 in code and ID2 in code and "retired" in code
    assert "capture_output=True" in code and "STDERR (sanitized, complete)" in code
    assert "subprocess.run([" not in code.replace("subprocess.run(cmd", "")          # every step goes through run()


def test_sanitize_keeps_authorization_ids_and_the_approval_line_intact():
    line = D.approval_line(DOCQ8X, "2026-11-26T00:00:00Z")
    assert D.sanitize(line) == line
    assert D.sanitize("Authorization: ghp_" + "C" * 36) == "Authorization: [REDACTED]"
    assert D.sanitize("authorization=abc123secret") == "authorization=[REDACTED]"
    import re
    nb = json.loads((ROOT / "notebooks/Phase5C_TA01_Fill_Diagnostic.ipynb").read_text())
    code = "".join(nb["cells"][2]["source"])
    scope = {"re": re}
    exec(code[code.index("SECRETS = []"):code.index("def run(")], scope)      # the notebook's own clean()
    assert scope["clean"](line) == line and "[REDACTED]" in scope["clean"]("token=abcdefgh12345")


# ------------------------------------------------------------------ P5C-TA01-Q8 authorization draft

def test_q8_authorization_scope_matches_0002_except_strategy_and_labels():
    s2, sq = DOC2["scope"], DOCQ8["scope"]
    assert DOCQ8["status"] == "UNSIGNED_UNTIL_LEAD_APPROVAL_COMMENT" and "NOT approved" in DOCQ8["lead_decision_record"]
    for k in ("dataset", "window", "holdout", "costs", "core_sha", "executor", "outputs", "max_runs",
              "batch_consumption", "inputs", "attempt_protocol", "market_id", "kind"):
        assert sq[k] == s2[k], k
    assert sq["holdout"]["access"] == "NONE" and sq["max_runs"] == 1 and sq["batch_consumption"] == 0
    st = sq["strategy"]
    q8 = json.loads((ROOT / st["spec_path"]).read_text())
    assert st["quantity"] == 8 and st["tuning"] == "NONE" and st["strategy_id"] == q8["strategy_id"]
    assert st["spec_file_sha256"] == C.sha(ROOT / st["spec_path"]) and st["spec_canonical_sha256"] == q8["provenance"]["spec_sha256"]
    assert st["parameters"] == s2["strategy"]["parameters"]
    assert sq["variant_labels"] == ["POST_DIAGNOSTIC_VARIANT", "EXPLORATORY_ONLY"]
    assert sq["open_blockers"][0]["id"] == "CORE_FULL_MINUTE_CAP_LOOKAHEAD" and sq["open_blockers"][0]["status"] == "BLOCKER"
    assert sq["prior_diagnostic"]["authorization_id"] == ID2 and sq["prior_diagnostic"]["reuse"].startswith("NONE")
    assert sq["inputs"]["recovery_archive"] == ("/content/drive/MyDrive/00Quant_Research/data/development_recovery_archives/"
                                                "TSM_DEV_1M_20161019_20241231_ca9f87df83f31bd24211_20261110T142034_335006Z")
    assert {D.sha256_of(DOCQ8), D.sha256_of(sq)}.isdisjoint(set(APPROVED_0001) | set(APPROVED_0002))
    assert (D.sha256_of(DOCQ8), D.sha256_of(sq)) == APPROVED_Q8                  # Q8 document never changed


def test_q8_approval_binds_only_the_q8_document(tmp_path):
    fx = FixtureApprovals()
    line = D.approval_line(DOCQ8, expires())
    assert D.verify_approval(DOCQ8, fx.comment(line), verifier=fx.verifier)["approval_source"]["trust"] == "ISOLATED_TEST"
    with pytest.raises(ApprovalRejected):                         # 0002's approval cannot authorize Q8
        D.verify_approval(DOCQ8, fx.comment(D.approval_line(DOC2, expires())), verifier=fx.verifier)
    for i, retired in enumerate((DOC1, DOC2, DOCQ8)):
        doc = {**retired, "scope": {**retired["scope"], "inputs": DOCQ8["scope"]["inputs"]}}
        h = Harness(tmp_path / f"r{i}", doc=doc)
        with pytest.raises(D.DiagnosticRefused, match="retired"):
            h.run()
        assert h.tree() == []


def test_minimum_minute_volume_for_8_units_at_1_percent_is_800():
    assert D.min_minute_volume(8, 0.01) == 800 and D.min_minute_volume(100, 0.01) == 10000
    assert 8 <= 800 * 0.01 and not 8 <= 799 * 0.01


# ------------------------------------------------------------------ P5C-TA01-Q8X authorization draft

def test_q8x_scope_matches_q8_except_strategy_and_records():
    sq, sx = DOCQ8["scope"], DOCQ8X["scope"]
    assert DOCQ8X["status"] == "UNSIGNED_UNTIL_LEAD_APPROVAL_COMMENT" and "NOT approved" in DOCQ8X["lead_decision_record"]
    assert sorted(k for k in set(sq) | set(sx) if sq.get(k) != sx.get(k)) == [
        "evidence_status", "prior_diagnostic", "question", "required_outputs", "strategy"]
    st = sx["strategy"]
    x = json.loads((ROOT / st["spec_path"]).read_text())
    assert st["quantity"] == 8 and st["exit_fill_policy"] == "PERSIST_TO_SESSION_END" and st["strategy_id"] == x["strategy_id"]
    assert st["spec_file_sha256"] == C.sha(ROOT / st["spec_path"]) and st["parent_strategy_id"] == DOCQ8["scope"]["strategy"]["strategy_id"]
    assert [p["authorization_id"] for p in sx["prior_diagnostic"]] == [ID2, IDQ8]
    assert sx["holdout"]["access"] == "NONE" and sx["max_runs"] == 1 and sx["open_blockers"][0]["status"] == "BLOCKER"


def test_q8x_approval_binds_only_q8x_and_q8_is_retired(tmp_path, capsys):
    fx = FixtureApprovals()
    assert D.verify_approval(DOCQ8X, fx.comment(D.approval_line(DOCQ8X, expires())), verifier=fx.verifier)
    with pytest.raises(ApprovalRejected):
        D.verify_approval(DOCQ8X, fx.comment(D.approval_line(DOCQ8, expires())), verifier=fx.verifier)
    s = tmp_path / "s.json"
    s.write_text(json.dumps({"mode": "PREPARE", "authorization_id": IDQ8, "approval_expires_utc": "2026-11-26T00:00:00Z"}))
    assert D.main(["--settings", str(s)]) == 2
