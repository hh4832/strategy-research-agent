import json
import subprocess
import threading
from pathlib import Path

import pytest

from strategy_agent.governance.append_only import check as append_only_check, is_append_only
from strategy_agent.governance.holdout import HoldoutBook, LockBookInvalid
from strategy_agent.governance.holdout_admin import approve, revoke
from strategy_agent.registry.chain import ChainError, ChainedLog
from strategy_agent.registry.experiments import RecordInvalid, TransitionError

from m2_helpers import LOCK_URL, FakePort, clone, dev_request, env, holdout_request, registered

# ============================================================ registry / chain


def test_chain_append_and_verify(tmp_path):
    log = ChainedLog(tmp_path / "l.jsonl")
    for i in range(3):
        log.append({"i": i})
    recs = log.verify()
    assert [r["seq"] for r in recs] == [0, 1, 2]
    assert recs[1]["prev_sha256"] == recs[0]["record_sha256"]
    with pytest.raises(ChainError):
        log.append({"seq": 9})


@pytest.mark.parametrize("tamper", ["edit", "delete", "reorder", "insert"])
def test_tampering_detected_and_append_refused(tmp_path, tamper):
    p = tmp_path / "l.jsonl"
    log = ChainedLog(p)
    for i in range(4):
        log.append({"status": "COMPLETED", "pnl": i})
    lines = p.read_text().splitlines()
    if tamper == "edit":
        lines[1] = lines[1].replace('"pnl":1', '"pnl":999')
    elif tamper == "delete":
        del lines[2]                       # e.g. hiding a bad result
    elif tamper == "reorder":
        lines[1], lines[2] = lines[2], lines[1]
    else:
        lines.insert(1, lines[0])
    p.write_text("\n".join(lines) + "\n")
    with pytest.raises(ChainError):
        log.verify()
    with pytest.raises(ChainError):
        log.append({"status": "COMPLETED"})


def test_concurrent_appends_keep_chain(tmp_path):
    log = ChainedLog(tmp_path / "l.jsonl")
    ts = [threading.Thread(target=lambda k=k: [log.append({"w": k, "j": j}) for j in range(20)]) for k in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(log.verify()) == 80


def test_lifecycle_rules(tmp_path):
    runner, reg = env(tmp_path)
    rec = runner.submit(dev_request(registered()))
    assert rec["status"] == "COMPLETED"
    assert [e["status"] for e in reg.events(rec["experiment_id"])] == ["QUEUED", "RUNNING", "COMPLETED"]
    with pytest.raises(TransitionError):          # terminal is final
        reg.append(dict(rec, status="RUNNING"))
    with pytest.raises(TransitionError):          # cannot jump straight to COMPLETED
        reg.append(dict(rec, experiment_id="EXP-20261103-0000000000aa", status="COMPLETED"))
    bad = {k: v for k, v in rec.items() if k not in ("seq", "prev_sha256", "record_sha256")}
    bad.pop("seed")
    with pytest.raises(RecordInvalid):
        reg.append(dict(bad, experiment_id="EXP-20261103-0000000000ab", status="QUEUED"))


def test_append_only_git_check(tmp_path, monkeypatch):
    def git(*a):
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)
    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    log = ChainedLog(tmp_path / "registry" / "experiments.jsonl")
    log.append({"a": 1})
    git("add", ".")
    git("commit", "-qm", "base")
    monkeypatch.chdir(tmp_path)
    log.append({"a": 2})
    assert append_only_check("main", Path(".")) == []           # growth is fine
    # rewrite history with a valid but different chain
    p = tmp_path / "registry" / "experiments.jsonl"
    p.write_text("")
    log.append({"a": 100})
    problems = append_only_check("main", Path("."))
    assert any("modified or removed" in x for x in problems)
    assert is_append_only(b"abc\n", b"abc\ndef\n") and not is_append_only(b"abc\n", b"abd\n")

# ================================================================== holdout


def _book(tmp_path):
    gov = tmp_path / "gov"
    gov.mkdir(parents=True)
    (gov / "approvers.json").write_text(json.dumps({"research_leads": ["hh4832"]}))
    return gov / "holdout_locks.jsonl", gov / "approvers.json"


def test_only_allowlisted_lead_can_approve(tmp_path):
    locks, appr = _book(tmp_path)
    with pytest.raises(PermissionError):
        approve(locks, appr, lock_id="L", dataset_id="d", snapshot_id="s", start="2026-01-01T00:00:00+08:00",
                end=None, approved_by="strategy-agent", approval_ref=LOCK_URL)
    # an agent bypassing the admin tool and appending directly is rejected on load
    ChainedLog(locks).append({"action": "APPROVE", "lock_id": "L", "dataset_id": "d", "snapshot_id": "s",
                              "holdout_start": "2026-01-01T00:00:00+08:00", "approved_by": "strategy-agent",
                              "approval_ref": LOCK_URL})
    with pytest.raises(LockBookInvalid):
        HoldoutBook(locks, appr)


def test_book_rejects_missing_ref_and_second_lock(tmp_path):
    locks, appr = _book(tmp_path)
    with pytest.raises(LockBookInvalid):
        approve(locks, appr, lock_id="L1", dataset_id="d", snapshot_id="s", start="2026-01-01T00:00:00+08:00",
                end=None, approved_by="hh4832", approval_ref="verbal ok")
    assert ChainedLog(locks).verify() == []          # rejected event was never written
    locks2, appr2 = _book(tmp_path / "x")
    approve(locks2, appr2, lock_id="L1", dataset_id="d", snapshot_id="s", start="2026-01-01T00:00:00+08:00",
            end=None, approved_by="hh4832", approval_ref=LOCK_URL)
    with pytest.raises(LockBookInvalid):     # an agent cannot "move" the holdout by adding a second lock
        approve(locks2, appr2, lock_id="L2", dataset_id="d", snapshot_id="s", start="2026-06-01T00:00:00+08:00",
                end=None, approved_by="hh4832", approval_ref=LOCK_URL)
    assert len(ChainedLog(locks2).verify()) == 1


def test_agent_facing_book_has_no_write_api():
    public = {n for n in dir(HoldoutBook) if not n.startswith("_")}
    assert not public & {"append", "approve", "add", "write", "set", "update", "revoke"}


def _code(rec):
    return rec["rejection_reasons"][0]["code"] if rec.get("rejection_reasons") else None


def test_h01_no_lock_blocks_everything(tmp_path):
    runner, _ = env(tmp_path, lock=False)
    rec = runner.submit(dev_request(registered()))
    assert (rec["status"], _code(rec)) == ("REJECTED_GOVERNANCE", "H01")


def test_h02_wrong_lock_id(tmp_path):
    runner, _ = env(tmp_path)
    s = registered(mutate=lambda s: s["experiment"].__setitem__("holdout_lock_id", "MY-OWN-LOCK"))
    assert _code(runner.submit(dev_request(s))) == "H02"


def test_h03_dev_window_cannot_touch_holdout(tmp_path):
    runner, _ = env(tmp_path)
    s = registered()
    r = runner.submit(dev_request(s, window_start="2025-10-01T00:00:00+08:00", window_end="2026-01-02T00:00:00+08:00"))
    assert _code(r) == "H03"
    ok = runner.submit(dev_request(s, window_start="2025-10-01T00:00:00+08:00", window_end="2026-01-01T00:00:00+08:00"))
    assert ok["status"] == "COMPLETED"                 # half-open: ending exactly at holdout start is allowed


def test_h04_h05_h07_h08(tmp_path):
    runner, _ = env(tmp_path)
    assert _code(runner.submit(holdout_request(registered()))) == "H04"           # not FROZEN
    frozen = registered(status="FROZEN")
    assert _code(runner.submit(holdout_request(frozen, cost_multiplier=2))) == "H07"
    assert _code(runner.submit(dev_request(frozen, role="FINAL_HOLDOUT", window_start="2026-03-01T00:00:00+08:00",
                                           window_end="2026-04-01T00:00:00+08:00", holdout_access=True))) == "H05"
    assert _code(runner.submit(dev_request(frozen, holdout_access=True))) == "H08"


def test_h06_single_access_per_family_even_after_failure(tmp_path):
    port = FakePort(mode="raise")
    runner, _ = env(tmp_path, port=port)
    frozen = registered(status="FROZEN")
    first = runner.submit(holdout_request(frozen))
    assert first["status"] == "FAILED"                 # the look still counts
    port.mode = "ok"
    again = runner.submit(holdout_request(frozen))
    assert _code(again) == "H06"
    # a renamed / re-versioned copy is the same family -> also blocked
    renamed = registered(status="FROZEN", mutate=lambda s: (s.__setitem__("strategy_id", "brand_new_name"),
                                                            s.__setitem__("version", "2.0.0")))
    assert _code(runner.submit(holdout_request(renamed))) == "H06"


def test_revoked_lock_window_stays_closed(tmp_path):
    runner, _ = env(tmp_path)
    gov = tmp_path / "gov"
    revoke(gov / "holdout_locks.jsonl", gov / "approvers.json", lock_id="HOLDOUT-TW-5M-v1", approved_by="hh4832",
           approval_ref=LOCK_URL, notes="test")
    runner2, _ = env(tmp_path / "b")
    book = HoldoutBook(gov / "holdout_locks.jsonl", gov / "approvers.json")
    assert book.for_dataset("tw_equity_fubon_5m") is None
    assert len(book.all_windows("tw_equity_fubon_5m")) == 1

# =================================================================== budget


def test_b01_family_cap_counts_renamed_variants(tmp_path):
    runner, reg = env(tmp_path, budget={"max_trials_per_family": 2})
    s = registered()
    assert runner.submit(dev_request(s, params={"fast": 5, "slow": 50}))["status"] == "COMPLETED"
    renamed = registered(mutate=lambda x: x.__setitem__("strategy_id", "another_name_entirely"))
    assert runner.submit(dev_request(renamed, params={"fast": 10, "slow": 50}))["status"] == "COMPLETED"
    third = runner.submit(dev_request(renamed, params={"fast": 15, "slow": 50}))
    assert (third["status"], _code(third)) == ("BUDGET_EXCEEDED", "B01")
    # re-running an already-counted trial does not add to N
    assert runner.submit(dev_request(s, params={"fast": 5, "slow": 50}))["status"] == "COMPLETED"
    fam = third["trial"]["family_id"]
    assert reg.trial_count(fam) == 2


def test_b02_daily_cap_and_counters_survive_restart(tmp_path):
    runner, reg = env(tmp_path, budget={"max_backtests_per_day": 2})
    s = registered()
    for f in (5, 10):
        runner.submit(dev_request(s, params={"fast": f, "slow": 50}))
    fresh, _ = env(tmp_path, budget={"max_backtests_per_day": 2}, lock=False)   # new process, same registry
    fresh.holdout = runner.holdout
    assert _code(fresh.submit(dev_request(s, params={"fast": 15, "slow": 50}))) == "B02"


def test_b03_new_family_cap(tmp_path):
    runner, _ = env(tmp_path, budget={"max_new_families_per_day": 1})
    assert runner.submit(dev_request(registered()))["status"] == "COMPLETED"
    other = registered(mutate=lambda s: s["signal"]["indicators"][0].__setitem__("type", "EMA"))
    assert _code(runner.submit(dev_request(other))) == "B03"


def test_b04_wall_time(tmp_path):
    # admission charges this run's timeout up front: 0 + 0.5 <= 0.6 admits; after ~0.2s of use,
    # 0.2 + 0.5 > 0.6 refuses
    runner, _ = env(tmp_path, budget={"max_wall_seconds_per_day": 0.6, "timeout_seconds_per_experiment": 0.5},
                    port=FakePort(sleep=0.2))
    runner.port.release.clear()
    s = registered()
    assert runner.submit(dev_request(s, params={"fast": 5, "slow": 50}))["status"] == "COMPLETED"
    assert _code(runner.submit(dev_request(s, params={"fast": 10, "slow": 50}))) == "B04"


def test_b05_halt_and_lead_resume(tmp_path):
    port = FakePort(mode="raise")
    runner, reg = env(tmp_path, port=port)
    s = registered()
    for f in (5, 10, 15):
        assert runner.submit(dev_request(s, params={"fast": f, "slow": 50}))["status"] == "FAILED"
    port.mode = "ok"
    halted = runner.submit(dev_request(s, params={"fast": 20, "slow": 50}))
    assert _code(halted) == "B05"
    controls = runner.budget.controls.log
    controls.append({"action": "RESUME", "approved_by": "strategy-agent", "at": runner.clock().isoformat()})
    assert _code(runner.submit(dev_request(s, params={"fast": 20, "slow": 50}))) == "B05"   # agent cannot resume
    controls.append({"action": "RESUME", "approved_by": "hh4832", "at": runner.clock().isoformat(),
                     "approval_ref": LOCK_URL})
    assert runner.submit(dev_request(s, params={"fast": 20, "slow": 50}))["status"] == "COMPLETED"


def test_b06_spec_max_trials(tmp_path):
    runner, _ = env(tmp_path)
    s = registered(max_trials=1)           # search FIXED, max_trials=1
    assert runner.submit(dev_request(s, params={"fast": 5, "slow": 50}))["status"] == "COMPLETED"
    assert _code(runner.submit(dev_request(s, params={"fast": 10, "slow": 50}))) == "B06"

# =================================================================== runner


def test_every_outcome_is_recorded(tmp_path):
    port = FakePort()
    runner, reg = env(tmp_path, port=port, budget={"max_trials_per_family": 50})
    mk = lambda i: registered(mutate=lambda s: s["parameters"]["search"].__setitem__("max_trials", 50))  # noqa: E731
    s = mk(0)
    outcomes = {}
    outcomes["COMPLETED"] = runner.submit(dev_request(s, params={"fast": 1, "slow": 50}))
    port.mode = "raise"
    outcomes["FAILED"] = runner.submit(dev_request(s, params={"fast": 2, "slow": 50}))
    port.mode, port.sleep = "ok", 5
    port.release.clear()
    outcomes["TIMEOUT"] = runner.submit(dev_request(s, params={"fast": 3, "slow": 50}), timeout_seconds=0.05)
    port.release.set()
    ev = threading.Event()
    ev.set()
    outcomes["CANCELLED"] = runner.submit(dev_request(s, params={"fast": 4, "slow": 50}), cancel=ev)
    port.sleep = 0
    outcomes["REJECTED_SPEC"] = runner.submit(dev_request(registered(status="DRAFT")))
    port.engine_sha = "0.2.0+source"
    outcomes["REJECTED_CONTRACT"] = runner.submit(dev_request(s, params={"fast": 5, "slow": 50}))
    for status, rec in outcomes.items():
        assert rec["status"] == status, (status, rec.get("rejection_reasons"))
    assert {r["status"] for r in reg.final_records()} == set(outcomes)
    reg.records()                                   # chain still valid


def test_cancel_while_running_discards_result(tmp_path):
    port = FakePort(sleep=5)
    port.release.clear()
    runner, _ = env(tmp_path, port=port)
    ev = threading.Event()
    threading.Timer(0.05, ev.set).start()
    rec = runner.submit(dev_request(registered()), cancel=ev)
    port.release.set()
    assert rec["status"] == "CANCELLED" and "metrics" not in rec


def test_unbenchmarked_engine_rejected(tmp_path):
    runner, _ = env(tmp_path, benchmark="NOT_VERIFIED")
    rec = runner.submit(dev_request(registered()))
    assert (rec["status"], _code(rec)) == ("REJECTED_CONTRACT", "C00")
    assert runner.port.calls == 0


def test_q5_invalidated_trades_excluded_and_sufficiency_flagged(tmp_path):
    port = FakePort(round_trips=[{"net_pnl": "10"}] * 20, invalidated=[{"net_pnl": "5000"}] * 5)
    runner, _ = env(tmp_path, port=port)
    rec = runner.submit(dev_request(registered()))
    suff = rec["sample_sufficiency"]
    assert rec["metrics"]["effective_net_pnl"] == 200              # invalidated 25000 never counted
    assert suff["status"] == "INSUFFICIENT"
    assert {r["code"] for r in suff["reasons"]} == {"Q5-1", "Q5-2", "Q5-3"}
    ok = FakePort(round_trips=[{"net_pnl": "10"}] * 40)
    runner2, _ = env(tmp_path / "ok", port=ok)
    assert runner2.submit(dev_request(registered()))["sample_sufficiency"]["status"] == "SUFFICIENT"

# ============================================================ independence (Q4)


SRC = Path(__file__).parents[1] / "src" / "strategy_agent"


def test_strategy_agent_never_imports_oracle_or_engine():
    # M1: ARCHITECTURE §6 designates adapters/mrc_adapter.py as the ONLY module that may know Core.
    # The exemption is this exact path and the Core import only; the oracle ban applies everywhere.
    # Phase 4-A: adapters/mrc_phase4a.py is the second (research-only) Core import site; mrc_adapter.py is pinned.
    adapter = SRC / "adapters" / "mrc_adapter.py"
    phase4a = SRC / "adapters" / "mrc_phase4a.py"
    for p in SRC.rglob("*.py"):
        txt = p.read_text()
        banned = ("import benchmark", "from benchmark")
        if p not in (adapter, phase4a):
            banned += ("import market_research_core", "from market_research_core")
        for bad in banned:
            assert bad not in txt, f"{p} contains '{bad}'"


def test_runner_does_not_import_admin_tools():
    txt = (SRC / "runner.py").read_text()
    assert "holdout_admin" not in txt and "append(" not in txt.replace("self.registry.append(", "")


def test_runner_refuses_oracle_as_executor(tmp_path):
    """Q4: the oracle audits the engine; it must never be wired in as the engine.
    (A runtime_checkable Protocol only checks for a `run` attribute, so the oracle
    would pass isinstance; the runner therefore checks the implementation's origin.)"""
    from benchmark.oracle.reference_engine import ReferenceEngine, load_case
    from strategy_agent.runner import OracleNotAllowed
    case = load_case(next((Path(__file__).parents[1] / "benchmark" / "cases").glob("B01*.json")))
    with pytest.raises(OracleNotAllowed):
        env(tmp_path, port=ReferenceEngine(case))
