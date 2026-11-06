"""Phase 4-A: RESEARCH_ONLY_EXPLORATORY authorization, holdout boundaries, research-only preflight.

Approvals here happen ONLY in temporary copies of the governance files (test
fixtures). The repository's own lock book and authorization book stay empty.
"""
import copy
import hashlib
import inspect
import json
import shutil
import subprocess
import threading
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from strategy_agent.governance.holdout import HoldoutBook, HoldoutGuard, HoldoutReservations
from strategy_agent.governance.holdout_admin import approve as approve_lock
from strategy_agent.governance.research_authorization import (AuthorizationBookInvalid, ResearchAuthorizationBook,
                                                              ResearchAuthorizationGuard, gates_sha256)
from strategy_agent.governance.research_authorization_admin import approve as approve_auth
from strategy_agent.governance.research_authorization_admin import revoke as revoke_auth
from strategy_agent.registry.chain import ChainedLog, ConditionFailed
from strategy_agent.research_only.costs import CostSet, CostSetError, reference_fill_costs
from strategy_agent.research_only.runner import ResearchOnlyRunner, canonical_sha, run_id, write_once

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "configs" / "research_only" / "tsm_sma_5_20_smoke_2024_06_v1.json"
PROPOSAL = ROOT / "governance" / "proposals" / "RA-IBKR_TSM-SMA520_SMOKE-0001.json"
URL = "https://example.test/research-lead-approval"
TPE = timezone(timedelta(hours=8))
NOW = datetime(2026, 11, 6, 10, 0, tzinfo=TPE)
HOLDOUT = ("2025-01-01T00:00:00-05:00", "2026-10-17T00:00:00-04:00")


# ------------------------------------------------------------------ fixtures

def gov(tmp: Path, *, lock=True, lock_dates=HOLDOUT, auth=True, proposal_edit=None, readiness_edit=None):
    g = tmp / "gov"
    g.mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "governance" / "approvers.json", g / "approvers.json")
    for f in ("holdout_locks.jsonl", "research_authorizations.jsonl", "uses.jsonl"):
        (g / f).touch()
    r = json.loads((ROOT / "governance" / "market_readiness.json").read_text())
    if readiness_edit:
        readiness_edit(r["markets"]["IBKR_TSM"])
    (g / "market_readiness.json").write_text(json.dumps(r))
    if lock:  # TEST FIXTURE: the research lead's approval, simulated in a temp book only
        approve_lock(g / "holdout_locks.jsonl", g / "approvers.json", lock_id="HOLDOUT-TSM-15M-v1",
                     dataset_id="ibkr_tsm_1m_normalized", snapshot_id="20261101_205431_06359faf7ef4_IBKR_FINAL_GATE_FULL",
                     start=lock_dates[0], end=lock_dates[1], approved_by="hh4832", approval_ref=URL)
    if auth:
        p = json.loads(PROPOSAL.read_text())
        if proposal_edit:
            proposal_edit(p)
        approve_auth(g / "research_authorizations.jsonl", g / "approvers.json", p, approved_by="hh4832", approval_ref=URL,
                     now=NOW)
    runner = ResearchOnlyRunner(locks_path=g / "holdout_locks.jsonl", authorizations_path=g / "research_authorizations.jsonl",
                                uses_path=g / "uses.jsonl", approvers_path=g / "approvers.json",
                                readiness_path=g / "market_readiness.json", clock=lambda: NOW)
    return runner, g


def codes(pf):
    return {c for c, _ in pf.blockers}


def cfg_copy(tmp: Path, edit) -> Path:
    c = json.loads(CFG.read_text())
    edit(c)
    c["provenance"]["config_sha256"] = canonical_sha(c)
    p = tmp / "cfg.json"
    p.write_text(json.dumps(c))
    return p


def guard_and_request(tmp: Path, **kw):
    runner, g = gov(tmp, **kw)
    market = json.loads((g / "market_readiness.json").read_text())["markets"]["IBKR_TSM"]
    guard = ResearchAuthorizationGuard(ResearchAuthorizationBook(g / "research_authorizations.jsonl", g / "approvers.json"),
                                       HoldoutBook(g / "holdout_locks.jsonl", g / "approvers.json"), market,
                                       g / "uses.jsonl", clock=lambda: NOW)
    cfg = json.loads(CFG.read_text())
    return guard, runner.request(cfg, CostSet()), g


# ------------------------------------------------------- repository state

def test_repository_preflight_is_blocked_exactly_by_lock_authorization_and_core():
    pf = ResearchOnlyRunner().preflight(CFG)
    assert pf.status == "BLOCKED"
    assert codes(pf) == {"H01", "RA01", "RO04"}
    assert pf.governance["formal"]["market_status"] == "NO_GO"


def test_repository_governance_files_untouched_vs_baseline():
    for f in ("governance/market_readiness.json", "governance/holdout_locks.jsonl", "governance/approvers.json"):
        r = subprocess.run(["git", "show", f"8f3f6e13248fe36e7e64cc59ec7c93cecfc78263:{f}"], cwd=ROOT, capture_output=True)
        if r.returncode == 0:
            assert r.stdout == (ROOT / f).read_bytes(), f
    assert (ROOT / "governance" / "holdout_locks.jsonl").read_bytes() == b""
    assert (ROOT / "governance" / "research_authorizations.jsonl").read_bytes() == b""


def test_proposal_is_unsigned_and_cannot_be_appended_as_is(tmp_path):
    p = json.loads(PROPOSAL.read_text())
    assert p["approved_by"] == "__RESEARCH_LEAD__"
    _, g = gov(tmp_path, auth=False)
    with pytest.raises(AuthorizationBookInvalid):          # placeholder approver is not allow-listed
        from strategy_agent.governance.research_authorization import validate_events
        validate_events([{k: v for k, v in p.items() if not k.startswith(("proposal_", "proposed_", "approval_instr"))}],
                        {"hh4832"})


# ------------------------------------------------------------- happy paths

def test_full_governance_then_waiting_for_core(tmp_path):
    runner, _ = gov(tmp_path)
    pf = runner.preflight(CFG)
    assert (pf.status, codes(pf)) == ("WAITING_FOR_CORE_ADAPTER", {"RO04"})
    assert runner.run(CFG)["executed"] is False


def test_core_pinned_reaches_integration_point_but_never_guesses_api(tmp_path):
    runner, _ = gov(tmp_path)
    p = cfg_copy(tmp_path, lambda c: c["core"].update(phase4a_sha="a" * 40, phase4a_contract_ref="https://x/contract"))
    assert runner.preflight(p).status == "READY_FOR_CORE_INTEGRATION"
    with pytest.raises(NotImplementedError):
        runner.run(p)


# --------------------------------------------------------- RA rules (guard)

@pytest.mark.parametrize("field,value,code", [
    ("market_id", "IBKR_NQ", "RA03"),
    ("dataset", {"dataset_id": "ibkr_tsm_1m_normalized", "snapshot_id": "other", "producer_commit_sha": "0" * 40}, "RA04"),
    ("temporal_policy_sha256", "0" * 64, "RA05"),
    ("scenario_set_sha256", "0" * 64, "RA06"),
    ("scenarios", ("BASE", "STRESS_10X"), "RA06"),
    ("parameters", {"fast": 5, "slow": 21}, "RA07"),
    ("window_end", "2024-07-01T00:00:00-04:00", "RA08"),
    ("warmup_start", "2016-10-18T00:00:00-04:00", "RA08"),
    ("role", "FORMAL", "RA12"),
    ("holdout_access", True, "RA12"),
])
def test_guard_rejects(tmp_path, field, value, code):
    guard, req, _ = guard_and_request(tmp_path)
    assert guard.check(req).allowed
    d = guard.check(replace(req, **{field: value}))
    assert not d.allowed and code in {c for c, _ in d.failures}


def test_ra02_expired(tmp_path):
    guard, req, _ = guard_and_request(tmp_path)
    guard.clock = lambda: datetime(2026, 12, 1, tzinfo=TPE)
    assert "RA02" in {c for c, _ in guard.check(req).failures}


def test_ra09_lock_boundaries_must_match_authorization(tmp_path):
    guard, req, _ = guard_and_request(tmp_path, lock_dates=("2025-01-01T00:00:00-05:00", "2026-10-16T00:00:00-04:00"))
    assert "RA09" in {c for c, _ in guard.check(req).failures}


@pytest.mark.parametrize("edit", [
    lambda m: m["gates"]["TEMPORAL_SAFETY"].__setitem__("status", "PASS"),
    lambda m: m["gates"]["HOLDOUT_APPROVAL"].__setitem__("status", "PASS"),
    lambda m: m.__setitem__("gate_version", "2026-11-06.1"),
    lambda m: m.__setitem__("status", "GO"),
])
def test_ra10_any_formal_gate_change_voids_authorization(tmp_path, edit):
    runner, _ = gov(tmp_path, readiness_edit=edit)
    pf = runner.preflight(CFG)
    assert pf.status == "BLOCKED" and "RA10" in codes(pf)


def test_ra11_batch_slot_is_atomic_and_never_released(tmp_path):
    guard, req, g = guard_and_request(tmp_path)
    results = []

    def take(i):
        try:
            guard.consume(req, run_id=f"r{i}", actor=f"w{i}")
            results.append("OK")
        except ConditionFailed:
            results.append("REFUSED")
    ts = [threading.Thread(target=take, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(results) == ["OK"] + ["REFUSED"] * 7
    assert "RA11" in {c for c, _ in guard.check(req).failures}
    assert len(ChainedLog(g / "uses.jsonl").verify()) == 1


# ---------------------------------------------------------------- admin tool

def test_only_lead_can_approve_and_invalid_events_are_never_written(tmp_path):
    _, g = gov(tmp_path, auth=False)
    book = g / "research_authorizations.jsonl"
    p = json.loads(PROPOSAL.read_text())
    with pytest.raises(PermissionError):
        approve_auth(book, g / "approvers.json", p, approved_by="strategy-agent", approval_ref=URL, now=NOW)
    bad = [
        lambda q: q["development_boundary"].__setitem__("end", "2025-02-01T00:00:00-05:00"),     # into the holdout
        lambda q: q["formal_gate_pin"]["gates"].__setitem__("COST_APPROVAL", "PASS"),           # pins a passed formal gate
        lambda q: q["strategy"]["parameters"].__setitem__("slow", 30),                           # not 5/20
        lambda q: q.__setitem__("expires_at", "2026-11-01T00:00:00+08:00"),                       # before issued_at
        lambda q: q.__setitem__("ignore_gate", True),                                            # no bypass field exists
    ]
    for edit in bad:
        q = copy.deepcopy(p)
        edit(q)
        with pytest.raises(AuthorizationBookInvalid):
            approve_auth(book, g / "approvers.json", q, approved_by="hh4832", approval_ref=URL, now=NOW)
    assert ChainedLog(book).verify() == []
    with pytest.raises(AuthorizationBookInvalid):     # placeholder ref is rejected by schema (must be https)
        approve_auth(book, g / "approvers.json", p, approved_by="hh4832", approval_ref="verbal", now=NOW)


def test_book_has_no_write_api_and_revoke_closes(tmp_path):
    public = {n for n in dir(ResearchAuthorizationBook) if not n.startswith("_")}
    assert not public & {"append", "approve", "add", "write", "update", "revoke"}
    runner, g = gov(tmp_path)
    revoke_auth(g / "research_authorizations.jsonl", g / "approvers.json", authorization_id="RA-IBKR_TSM-SMA520_SMOKE-0001",
                approved_by="hh4832", approval_ref=URL, reason="test revoke", now=NOW)
    assert "RA01" in codes(runner.preflight(CFG))


# -------------------------------------------------------- holdout boundaries

def test_holdout_lock_is_half_open_at_the_approved_dates(tmp_path):
    _, g = gov(tmp_path, auth=False)
    lock = HoldoutBook(g / "holdout_locks.jsonl", g / "approvers.json").for_dataset("ibkr_tsm_1m_normalized")
    assert lock.lock_id == "HOLDOUT-TSM-15M-v1"
    assert lock.holdout_start == datetime.fromisoformat("2025-01-01T00:00:00-05:00")
    assert lock.holdout_end == datetime.fromisoformat("2026-10-17T00:00:00-04:00")    # 2026-10-16 fully inside


@pytest.mark.parametrize("end,ok", [("2025-01-01T00:00:00-05:00", True), ("2025-01-01T00:00:01-05:00", False),
                                    ("2024-12-31T16:00:00-05:00", True)])
def test_formal_guard_dev_window_touching_holdout(tmp_path, end, ok):
    _, g = gov(tmp_path, auth=False)
    hg = HoldoutGuard(HoldoutBook(g / "holdout_locks.jsonl", g / "approvers.json"), HoldoutReservations(g / "res.jsonl"))
    spec = json.loads((ROOT / "specs/research/sma_crossover_tsm_15m_5_20.json").read_text())
    d = hg.check(spec=spec, family_id="F", role="WF_TEST", dataset_ids=["ibkr_tsm_1m_normalized"],
                 window_start="2024-06-03T00:00:00-04:00", window_end=end, cost_multiplier=1, holdout_access=False)
    assert d.allowed is ok and (ok or d.code == "H03")


def test_warmup_or_window_in_holdout_is_rejected(tmp_path):
    guard, req, _ = guard_and_request(tmp_path)
    for kw in ({"window_start": "2025-01-02T00:00:00-05:00", "window_end": "2025-01-10T00:00:00-05:00"},
               {"warmup_start": "2025-01-02T00:00:00-05:00"}):
        assert {"RA08", "RA09"} & {c for c, _ in guard.check(replace(req, **kw)).failures}


# ----------------------------------------------------------------- no bypass

def test_no_bypass_switches_exist(tmp_path):
    for fn in (ResearchOnlyRunner.preflight, ResearchOnlyRunner.run, ResearchOnlyRunner.__init__,
               ResearchAuthorizationGuard.check, ResearchAuthorizationGuard.consume):
        names = set(inspect.signature(fn).parameters)
        assert not {n for n in names if any(w in n for w in ("ignore", "force", "skip", "bypass", "override"))}, fn
    runner, _ = gov(tmp_path)
    p = cfg_copy(tmp_path, lambda c: c.__setitem__("force_run", True))
    assert "RO01" in codes(runner.preflight(p))


def test_edited_config_without_rehash_is_refused(tmp_path):
    runner, _ = gov(tmp_path)
    c = json.loads(CFG.read_text())
    c["strategy"]["parameters"]["slow"] = 30
    p = tmp_path / "edited.json"
    p.write_text(json.dumps(c))
    assert {"RO01", "R02", "RA07"} <= codes(runner.preflight(p))


# --------------------------------------------------------------------- costs

def test_cost_set_scenarios_differ_only_where_allowed(tmp_path):
    raw = json.loads((ROOT / "configs/research_only/ibkr_pro_fixed_tsm_research_only_costs_v1.json").read_text())
    for edit, code in [(lambda c: c["scenarios"]["LOW"].__setitem__("cat_per_share", 0.0001), "CO04"),
                       (lambda c: c["scenarios"]["CAT_STRESS"].__setitem__("slippage_bps", 4), "CO04"),
                       (lambda c: c["coverage"].__setitem__("end", "2025-06-30"), "CO05"),
                       (lambda c: c.__setitem__("status", "APPROVED"), "CO01"),
                       (lambda c: c["sec_section31"]["schedule_source"].__setitem__("file_sha256", "0" * 64), "CO03")]:
        c = copy.deepcopy(raw)
        edit(c)
        p = tmp_path / "c.json"
        p.write_text(json.dumps(c))
        with pytest.raises(CostSetError, match=code):
            CostSet(p)


def test_cost_window_must_not_span_a_rate_change():
    cs = CostSet()
    assert cs.check_window(date(2024, 5, 29), date(2024, 6, 28))["sec_version"] == "SEC31-2024-05-22"
    with pytest.raises(CostSetError, match="CO06"):
        cs.check_window(date(2024, 5, 15), date(2024, 6, 28))          # SEC change on 2024-05-22
    with pytest.raises(CostSetError, match="CO06"):
        cs.check_window(date(2024, 12, 20), date(2025, 1, 3))          # beyond coverage (holdout)


def test_reference_fill_cost_known_answer():
    cs = CostSet()
    r = cs.rates("BASE", date(2024, 6, 10))
    sell = reference_fill_costs(Decimal("170.00"), 100, "SELL", r)
    # commission max(100*0.005, 1) = 1 (cap 1% of 16996.60 not binding); SEC 16996.60*27.8e-6 = 0.4725 -> 0.47
    assert sell["commission"] == Decimal("1.00") and sell["sec_fee"] == Decimal("0.47")
    assert sell["taf_fee"] == Decimal("0.02") and sell["cat_fee"] == Decimal("0.01") and sell["slippage_cost"] == Decimal("3.40")
    buy = reference_fill_costs(Decimal("170.00"), 100, "BUY", r)
    assert buy["sec_fee"] == 0 and buy["taf_fee"] == Decimal("0.02")              # TAF both sides (conservative)


def test_cat_stress_is_invisible_at_100_shares_under_cent_rounding():
    """Documented finding: 100 x 0.00005 = 0.005 -> 0.01 and 100 x 0.00010 = 0.010 -> 0.01."""
    cs = CostSet()
    b = reference_fill_costs(Decimal("170"), 100, "SELL", cs.rates("BASE", date(2024, 6, 10)))
    s = reference_fill_costs(Decimal("170"), 100, "SELL", cs.rates("CAT_STRESS", date(2024, 6, 10)))
    assert b["cat_fee"] == s["cat_fee"] == Decimal("0.01")


# ------------------------------------------------------------------- archive

def test_write_once_refuses_overwrite_and_run_id_format(tmp_path):
    p = tmp_path / "a" / "summary.json"
    sha = write_once(p, b"one")
    assert sha == hashlib.sha256(b"one").hexdigest()
    with pytest.raises(FileExistsError):
        write_once(p, b"two")
    assert p.read_bytes() == b"one"
    assert run_id(datetime(2026, 11, 6, 9, 30, 5), "8f3f6e13248fe36e") == "20261106_093005_8f3f6e1"
    with pytest.raises(ValueError):
        run_id(datetime(2026, 11, 6), "main")
