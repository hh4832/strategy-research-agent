"""Real multi-process races (fork + barrier). Decisions 1 and 2 of the M2 review."""
import multiprocessing as mp
import threading
from pathlib import Path

import pytest

from strategy_agent.governance.holdout import HoldoutReservations
from strategy_agent.registry.admission import AdmissionError, AdmissionLock
from strategy_agent.registry.chain import ChainedLog, ConditionFailed

from m2_helpers import FakePort, dev_request, env, holdout_request, registered

CTX = mp.get_context("fork")
N = 8


def _code(rec):
    return rec["rejection_reasons"][0]["code"] if rec.get("rejection_reasons") else None


class _Barrier:
    """Barrier with a timeout so a crashed worker fails the test instead of hanging it."""

    def __init__(self, n):
        self.b = CTX.Barrier(n)

    def wait(self):
        self.b.wait(timeout=20)


def _race(target, args_list):
    barrier, q = _Barrier(len(args_list)), CTX.Queue()
    ps = [CTX.Process(target=target, args=(barrier, q, *a)) for a in args_list]
    [p.start() for p in ps]
    out = [q.get(timeout=40) for _ in ps]
    for p in ps:
        p.join(10)
        if p.is_alive():
            p.kill()
    assert all(p.exitcode == 0 for p in ps), [p.exitcode for p in ps]
    return out


# ---------------------------------------------------------- holdout single use

def _reserve_worker(barrier, q, path, i):
    res = HoldoutReservations(path)
    barrier.wait()
    try:
        res.reserve(lock_id="L", family_id="F", experiment_id=f"E{i}", spec_sha256="s", actor=f"w{i}", at="t")
        q.put("OK")
    except ConditionFailed:
        q.put("REFUSED")


def test_atomic_reservation_exactly_one_winner(tmp_path):
    out = _race(_reserve_worker, [(tmp_path / "r.jsonl", i) for i in range(N)])
    assert sorted(out) == ["OK"] + ["REFUSED"] * (N - 1)
    assert len(ChainedLog(tmp_path / "r.jsonl").verify()) == 1


def _holdout_worker(barrier, q, tmp, sleep):
    runner, _ = env(Path(tmp), port=FakePort(sleep=sleep))
    spec = registered(status="FROZEN")
    barrier.wait()
    rec = runner.submit(holdout_request(spec))
    q.put((rec["status"], _code(rec)))


def test_concurrent_holdout_runs_only_one_admitted(tmp_path):
    env(tmp_path)                                    # set up lock book once
    out = _race(_holdout_worker, [(str(tmp_path), 0.2)] * N)
    admitted = [o for o in out if o[0] != "REJECTED_GOVERNANCE"]
    assert len(admitted) == 1 and admitted[0][0] == "COMPLETED"
    assert all(o == ("REJECTED_GOVERNANCE", "H06") for o in out if o[0] == "REJECTED_GOVERNANCE")
    assert len(ChainedLog(tmp_path / "holdout_reservations.jsonl").verify()) == 1


@pytest.mark.parametrize("outcome", ["FAILED", "TIMEOUT", "CANCELLED"])
def test_failed_timeout_cancelled_consume_the_holdout(tmp_path, outcome):
    port = FakePort(mode="raise" if outcome == "FAILED" else "ok", sleep=5 if outcome == "TIMEOUT" else 0)
    port.release.clear()
    runner, _ = env(tmp_path, port=port)
    spec = registered(status="FROZEN")
    cancel = threading.Event()
    if outcome == "CANCELLED":
        cancel.set()
    first = runner.submit(holdout_request(spec), cancel=cancel, timeout_seconds=0.05)
    port.release.set()
    assert first["status"] == outcome
    port.mode, port.sleep = "ok", 0
    again = runner.submit(holdout_request(spec))
    assert (again["status"], _code(again)) == ("REJECTED_GOVERNANCE", "H06")


# ------------------------------------------------------------- budget quotas

def _budget_worker(barrier, q, tmp, i, budget, sleep):
    runner, _ = env(Path(tmp), budget=budget, port=FakePort(sleep=sleep))
    spec = registered()
    barrier.wait()
    rec = runner.submit(dev_request(spec, params={"fast": 5 + i, "slow": 50}))
    q.put((rec["status"], _code(rec)))


@pytest.mark.parametrize("budget,limit,code", [
    ({"max_backtests_per_day": 3}, 3, "B02"),
    ({"max_trials_per_family": 2}, 2, "B01"),
    ({"max_wall_seconds_per_day": 1.0, "timeout_seconds_per_experiment": 0.4}, 2, "B04"),
])
def test_concurrent_admission_respects_quota(tmp_path, budget, limit, code):
    env(tmp_path, budget=budget)
    out = _race(_budget_worker, [(str(tmp_path), i, budget, 0.3) for i in range(N)])
    admitted = [o for o in out if o[0] == "COMPLETED"]
    assert len(admitted) == limit, out
    assert all(o == ("BUDGET_EXCEEDED", code) for o in out if o[0] != "COMPLETED"), out


# ------------------------------------------------------------- single host

def test_admission_lock_refuses_second_host(tmp_path):
    AdmissionLock(tmp_path, host="worker-a")
    AdmissionLock(tmp_path, host="worker-a")          # same host is fine
    with pytest.raises(AdmissionError):
        AdmissionLock(tmp_path, host="laptop-b")
