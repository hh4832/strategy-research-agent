"""Phase 5-B3-C PREPARATION: draft / run-config derivation / proposal check / preflight. Nothing is created,
approved, bound or consumed; every B3C gate stays NOT_EXECUTED and real_research_pass is always False."""
from __future__ import annotations

import copy
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from strategy_agent.phase5a import acceptance as A5
from strategy_agent.readiness import b3c
from strategy_agent.readiness.candidate import CANDIDATE, ROOT

HDR = b3c.HDR
PRE = b3c.pre_authorization_book()     # proposal-time checks are made against the book before the B3-C approval
SPEC_SHA = "f4f60b447013062324b7dc10aa0a569aebbd542796bef6ccfd2e723d0f5cff34"


@pytest.fixture(scope="module")
def draft():
    return b3c.build_draft()


def _filled(draft, **over):
    p = copy.deepcopy(draft["proposal_fields"])
    now = datetime.now(timezone.utc).replace(microsecond=0)
    p.update(authorization_id=b3c.CANDIDATE_AUTHORIZATION_ID, max_batches=1, issued_at=now.isoformat(),
             expires_at=(now + timedelta(days=7)).isoformat())
    p["executor"]["executor_id"] = b3c.PROPOSED_EXECUTOR
    p.update(over)
    return p


def test_B3C_draft_is_unapproved_and_every_human_decision_is_open(draft):
    assert draft["kind"] == "PHASE5B3C_AUTHORIZATION_DRAFT" and draft["status"] == "DRAFT_NOT_APPROVED"
    assert draft["authorization_id"] == {"proposed": "RA-IBKR_TSM-SMA520_P5B3C-0001", "decision": HDR,
                                         "rule": draft["authorization_id"]["rule"]}
    assert draft["executor"]["executor_id"] == HDR and draft["executor"]["consistency"] == "SINGLE_EXECUTOR_ONLY"
    assert draft["validity_period"]["issued_at"] == HDR and draft["validity_period"]["expires_at"] == HDR
    assert draft["max_research_batches"] == {"proposed": 1, "decision": HDR}
    p = draft["proposal_fields"]
    for k in ("authorization_id", "max_batches", "issued_at", "expires_at"):
        assert p[k] == HDR
    assert p["executor"] == {"executor_id": HDR, "consistency": "SINGLE_EXECUTOR_ONLY"}
    assert "approved_by" not in p and "approval_ref" not in p and "action" not in p


def test_B3C_draft_pins(draft):
    p = draft["proposal_fields"]
    assert p["market_id"] == "IBKR_TSM" and p["symbol"] == "TSM"
    assert p["strategy"]["spec_file_sha256"] == SPEC_SHA and p["strategy"]["spec_path"] == CANDIDATE["strategy_spec"]
    assert p["dataset"]["snapshot_id"] == "TSM_DEV_1M_20240529_20240628_18350b3adba8e28a7e5e"
    assert draft["dataset_and_period"]["dataset_manifest_sha256"].startswith("8b6fc838b72a")
    assert draft["core_commit"] == "b16179a7cc077c36046574769a32c367fa476cae"
    assert p["research_window"]["start"].startswith("2024-05-29") and p["research_window"]["end"].startswith("2024-06-29")
    assert p["costs"]["model"] == "IBKR_PRO_FIXED" and len(p["costs"]["scenarios"]) == 4
    assert draft["holdout_policy"]["lock_id"] == "HOLDOUT-TSM-15M-v1" and draft["holdout_policy"]["access"] == "NO_ACCESS"
    assert [s["step"] for s in draft["binding_procedure"]] == list(range(1, 12))
    assert draft["archive_location"]["immutable"] is True


def test_B3C_committed_draft_matches_fresh_build(draft):
    on_disk = json.loads((ROOT / "docs/phase5b3c/authorization_draft.json").read_text(encoding="utf-8"))
    assert on_disk == json.loads(b3c._dumps(draft))          # noqa: SLF001


def test_B3C_schema_dry_run_passes_in_memory_and_writes_nothing(draft):
    before = A5.tree_fingerprint(ROOT)
    assert b3c.schema_dry_run(draft) == {"status": "PASS", "placeholders_used_for": [], "written": False}
    assert A5.tree_fingerprint(ROOT) == before
    assert b3c.formal_authorization_state()["state"] in ("NOT_GRANTED", "GRANTED_UNUSED")


def test_B3C_raw_draft_is_not_admissible_as_an_authorization(draft):
    from strategy_agent.governance.research_authorization import validate_events
    ev = copy.deepcopy(draft["proposal_fields"])
    ev.update(action="APPROVE", approved_by="hh4832", approval_ref="https://example.invalid/x",
              approved_at="2026-11-10T00:00:00+00:00")
    with pytest.raises(Exception):
        validate_events([ev], {"hh4832"})
    assert "HUMAN_DECISION_REQUIRED_LEFT" in b3c.proposal_problems(draft["proposal_fields"], book=PRE)


def test_B3C_check_proposal_accepts_a_fully_decided_proposal_only(draft):
    assert b3c.proposal_problems(_filled(draft), book=PRE) == []
    cases = {
        "AUTHORIZATION_ID_ALREADY_USED": _filled(draft, authorization_id=b3c.SPENT_DEV),
        "AUTHORIZATION_ID_PATTERN": _filled(draft, authorization_id="RA-bad"),
        "MAX_BATCHES_NOT_1": _filled(draft, max_batches=2),
        "VALIDITY_PERIOD": _filled(draft, expires_at="2020-01-01T00:00:00+00:00"),
    }
    for code, p in cases.items():
        assert code in b3c.proposal_problems(p, book=PRE), code
    p = _filled(draft)
    p["executor"]["executor_id"] = HDR
    assert "EXECUTOR_ID" in b3c.proposal_problems(p, book=PRE)
    p = _filled(draft)
    p["research_window"] = dict(p["research_window"], end="2025-02-01T00:00:00-05:00")
    assert "PIN_CHANGED:research_window" in b3c.proposal_problems(p, book=PRE)
    p = _filled(draft)
    p["strategy"] = dict(p["strategy"], spec_file_sha256="0" * 64)
    assert "SPEC_PIN" in b3c.proposal_problems(p, book=PRE)


def test_B3C_derived_run_config_is_self_consistent_and_minimal():
    from strategy_agent.research_only.runner import ResearchOnlyRunner
    cfg = b3c.derive_run_config(b3c.CANDIDATE_AUTHORIZATION_ID)
    dev = json.loads((ROOT / CANDIDATE["run_config"]).read_text(encoding="utf-8"))
    changed = {k for k in set(cfg) | set(dev) if cfg.get(k) != dev.get(k)}
    assert changed == {"authorization_id", "run_config_id", "strategy", "purpose", "provenance"}
    assert {k for k in cfg["strategy"] if cfg["strategy"][k] != dev["strategy"][k]} == \
        {"spec_path", "spec_file_sha256", "version"}
    assert cfg["strategy"]["spec_file_sha256"] == SPEC_SHA
    with tempfile.TemporaryDirectory() as t:
        p = Path(t) / "c.json"
        p.write_text(json.dumps(cfg))
        _, blockers = ResearchOnlyRunner().load_config(p)
    assert blockers == []
    for bad in (b3c.SPENT_DEV, "RA-bad", ""):
        with pytest.raises(ValueError):
            b3c.derive_run_config(bad)


def test_B3C_derive_cli_refuses_without_a_formal_authorization_and_writes_nothing(tmp_path, capsys):
    out = tmp_path / "cfg.json"
    assert b3c.main(["derive-run-config", "--authorization-id", "RA-IBKR_TSM-SMA520_P5B3C-0002", "--out", str(out)]) == 2
    assert not out.exists() and "AUTHORIZATION_NOT_IN_BOOK" in capsys.readouterr().out
    granted = b3c.formal_authorization_state()["state"] != "NOT_GRANTED"
    assert (ROOT / b3c.B3C_RUN_CONFIG_PATH).exists() == granted


def test_B3C_preflight_is_non_consuming_and_never_a_real_pass():
    before = A5.tree_fingerprint(ROOT)
    r = b3c.preflight()
    assert A5.tree_fingerprint(ROOT) == before
    assert set(r["gates"]) == {f"B3C-{i:02d}" for i in range(1, 11)}
    assert all(g["status"] == "NOT_EXECUTED" for g in r["gates"].values())
    assert r["real_research_pass"] is False and r["real_execution"] == "NOT_PERFORMED"
    st = b3c.formal_authorization_state()["state"]
    assert r["authorization_state"] == st and st in ("NOT_GRANTED", "GRANTED_UNUSED")
    assert r["research_authorization"] == ("GRANTED" if b3c.expected_eligible() else "NOT_GRANTED")
    assert r["preparation"] == {"NOT_GRANTED": "READY_FOR_HUMAN_DECISION",
                                "GRANTED_UNUSED": "AUTHORIZED_AWAITING_GITHUB_APPROVALS"}[st], r["preparation_problems"]
    for gid in ("B3C-07", "B3C-08", "B3C-09", "B3C-10"):
        assert r["gates"][gid]["prepared_status"] == "NOT_APPLICABLE_BEFORE_EXECUTION"
    assert r["gates"]["B3C-05"]["prepared_checks"]["spent_dev_authorization_uses"] == 1
    assert r["gates"]["B3C-06"]["prepared_checks"]["REAL_EXECUTION_ENABLED"] is False
    assert r["gates"]["B3C-02"]["prepared_checks"]["binding_records_for_candidate"] == 0
    assert not (ROOT / "registry/research_budget_events.jsonl").exists()


def test_B3C_preflight_out_is_exclusive(tmp_path):
    out = tmp_path / "p.json"
    out.write_text("x")
    with pytest.raises(FileExistsError):
        b3c.main(["preflight", "--out", str(out)])
    assert out.read_text() == "x"


# ---------------------------------------------------------------- candidate authorization proposal
def test_B3C_proposal_is_a_candidate_never_an_approval():
    p = b3c.build_proposal()
    for k in ("action", "approved_by", "approval_ref", "approved_at"):
        assert k not in p
    assert p["authorization_id"] == "RA-IBKR_TSM-SMA520_P5B3C-0001" and p["max_batches"] == 1
    assert p["executor"] == {"executor_id": "colab-hh4832-exec-01", "consistency": "SINGLE_EXECUTOR_ONLY"}
    assert p["issued_at"] == HDR and p["expires_at"] == HDR          # fixed only at formal approval
    assert "PIPELINE_ACCEPTANCE_ONLY" in p["notes"] and p["strategy"]["spec_file_sha256"] == SPEC_SHA
    r = b3c.check(p, book=PRE)
    assert r["ok"] is False and set(r["problems"]) == {"HUMAN_DECISION_REQUIRED_LEFT", "VALIDITY_PERIOD"}
    assert r["schema"]["status"] == "NOT_RUN"


def test_B3C_committed_proposal_and_check_match_fresh_build():
    d = ROOT / "docs/phase5b3c"
    assert json.loads((d / "authorization_proposal_RA-IBKR_TSM-SMA520_P5B3C-0001.json").read_text()) == \
        json.loads(b3c._dumps(b3c.build_proposal()))                  # noqa: SLF001
    c = json.loads((d / "authorization_proposal_check.json").read_text())
    assert c["illustrative_validity"]["ok"] is True and c["illustrative_validity"]["schema"]["status"] == "PASS"


def test_B3C_decided_proposal_passes_schema_and_check_with_7_day_validity():
    p = b3c.build_proposal(issued_at="2026-11-11T09:00:00+08:00")
    assert p["expires_at"] == "2026-11-18T09:00:00+08:00"
    assert b3c.check(p, book=PRE) == {"problems": [], "schema": {"status": "PASS", "written": False}, "ok": True}
    with pytest.raises(ValueError):
        b3c.build_proposal(issued_at="2026-11-11T09:00:00")
    q = copy.deepcopy(p)
    q["formal_gate_pin"]["gate_version"] = "changed"
    assert "RA10_FORMAL_GATES_CHANGED" in b3c.check(q, book=PRE)["problems"]


def test_B3C_propose_cli_is_exclusive_and_touches_no_book(tmp_path):
    before = A5.tree_fingerprint(ROOT)
    out = tmp_path / "P.json"
    assert b3c.main(["propose", "--issued-at", "2026-11-11T09:00:00+08:00", "--out", str(out)]) == 0
    assert b3c.check(json.loads(out.read_text()), book=PRE)["ok"] is True
    with pytest.raises(FileExistsError):
        b3c.main(["propose", "--issued-at", "2026-11-11T09:00:00+08:00", "--out", str(out)])
    assert A5.tree_fingerprint(ROOT) == before


def test_B3C_proposal_after_admin_approve_in_an_isolated_copy_is_eligible_for_the_named_executor_only(tmp_path):
    """Isolated copy of governance/: proves the shape the research lead will approve yields exactly one eligible
    authorization for exactly the named executor. The repository books are never touched."""
    import shutil
    from strategy_agent.execution.activation import eligible_authorizations
    from strategy_agent.governance import research_authorization_admin as ADM
    before = A5.tree_fingerprint(ROOT)
    shutil.copytree(ROOT / "governance", tmp_path / "governance")
    shutil.copytree(ROOT / "registry", tmp_path / "registry")
    bk = tmp_path / "governance/research_authorizations.jsonl"          # the pre-approval prefix of the chain
    bk.write_text("".join(x + "\n" for x in bk.read_text().splitlines()
                          if x.strip() and json.loads(x)["authorization_id"] != b3c.CANDIDATE_AUTHORIZATION_ID))
    now = datetime.now(timezone.utc).replace(microsecond=0)
    p = b3c.build_proposal(issued_at=now.isoformat())
    ADM.approve(tmp_path / "governance/research_authorizations.jsonl", tmp_path / "governance/approvers.json", p,
                approved_by="hh4832", approval_ref="https://example.invalid/ISOLATED_TEST_NOT_AN_APPROVAL")
    assert eligible_authorizations(tmp_path, "colab-hh4832-exec-01", now + timedelta(hours=1)) == \
        ["RA-IBKR_TSM-SMA520_P5B3C-0001"]
    assert eligible_authorizations(tmp_path, "another-runtime", now + timedelta(hours=1)) == []
    assert eligible_authorizations(tmp_path, "colab-hh4832-exec-01", now + timedelta(days=7, seconds=1)) == []
    assert eligible_authorizations(ROOT, "colab-hh4832-exec-01", now) == b3c.expected_eligible(now)
    assert A5.tree_fingerprint(ROOT) == before


# ---------------------------------------------------------------- state classification (isolated repository copy)
@pytest.fixture()
def repo_copy(tmp_path, monkeypatch):
    import shutil
    import strategy_agent.readiness.candidate as C
    shutil.copytree(ROOT, tmp_path, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(".git", "__pycache__", "*.egg-info", ".pytest_cache"))
    bk = tmp_path / "governance/research_authorizations.jsonl"
    bk.write_text("".join(x + "\n" for x in bk.read_text().splitlines()
                          if x.strip() and json.loads(x)["authorization_id"] != b3c.CANDIDATE_AUTHORIZATION_ID))
    for f in (tmp_path / b3c.PROPOSAL_PATH, tmp_path / b3c.B3C_RUN_CONFIG_PATH):
        f.unlink(missing_ok=True)
    monkeypatch.setattr(C, "ROOT", tmp_path)
    monkeypatch.setattr(b3c, "ROOT", tmp_path)
    return tmp_path


def _lead_approves(root, *, ref="https://github.com/hh4832/strategy-research-agent/pull/18", issued=None):
    """What the research lead's own authorization PR does (here: on an isolated copy, never the repository)."""
    from strategy_agent.governance import research_authorization_admin as ADM
    issued = issued or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    assert b3c.main(["propose", "--issued-at", issued, "--out", str(root / b3c.PROPOSAL_PATH)]) == 0
    p = json.loads((root / b3c.PROPOSAL_PATH).read_text())
    assert b3c.check(p)["ok"] is True
    ADM.approve(root / "governance/research_authorizations.jsonl", root / "governance/approvers.json", p,
                approved_by="hh4832", approval_ref=ref)
    assert b3c.main(["derive-run-config", "--authorization-id", b3c.CANDIDATE_AUTHORIZATION_ID,
                     "--out", str(root / b3c.B3C_RUN_CONFIG_PATH)]) == 0


def test_B3C_state_not_granted_then_granted_unused_after_the_leads_steps(repo_copy):
    before = A5.tree_fingerprint(ROOT)
    assert b3c.formal_authorization_state()["state"] == "NOT_GRANTED" and b3c.expected_eligible() == []
    _lead_approves(repo_copy)
    st = b3c.formal_authorization_state()
    assert st["state"] == "GRANTED_UNUSED" and st["why"] == [] and st["uses"] == 0
    assert b3c.expected_eligible() == [b3c.CANDIDATE_AUTHORIZATION_ID]
    v = st["validity"]
    assert datetime.fromisoformat(v["expires_at"]) - datetime.fromisoformat(v["issued_at"]) == timedelta(days=7)
    assert b3c.expected_eligible(datetime.fromisoformat(v["expires_at"])) == []
    r = b3c.preflight()
    assert r["preparation"] == "AUTHORIZED_AWAITING_GITHUB_APPROVALS" and r["real_research_pass"] is False
    assert {g["status"] for g in r["gates"].values()} == {"NOT_EXECUTED"}
    assert A5.tree_fingerprint(ROOT) == before


@pytest.mark.parametrize("tamper", ["config", "proposal", "ref", "second_line", "unknown_auth", "no_config"])
def test_B3C_any_deviation_from_the_governed_authorization_is_unexpected(repo_copy, tamper):
    from strategy_agent.registry.chain import ChainedLog
    _lead_approves(repo_copy, ref="https://example.invalid/x" if tamper == "ref" else
                   "https://github.com/hh4832/strategy-research-agent/pull/18")
    cfg, prop = repo_copy / b3c.B3C_RUN_CONFIG_PATH, repo_copy / b3c.PROPOSAL_PATH
    book = repo_copy / "governance/research_authorizations.jsonl"
    if tamper == "config":
        c = json.loads(cfg.read_text()); c["sample"]["min_trades"] = 1; cfg.write_text(json.dumps(c))
    elif tamper == "proposal":
        p = json.loads(prop.read_text()); p["notes"] = "edited"; prop.write_text(json.dumps(p))
    elif tamper == "no_config":
        cfg.unlink()
    elif tamper in ("second_line", "unknown_auth"):
        last = json.loads(book.read_text().splitlines()[-1])
        ev = {k: v for k, v in last.items() if k not in b3c.CHAIN_FIELDS}
        if tamper == "unknown_auth":
            ev["authorization_id"] = "RA-IBKR_TSM-SMA520_OTHER-0001"
        ChainedLog(book).append(ev)
    assert b3c.formal_authorization_state()["state"] == "UNEXPECTED"
    assert b3c.expected_eligible() == []
