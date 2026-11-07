"""Phase 5-A1 orchestrator acceptance tests (1-18 of the brief + concurrency + CLI).

SYNTHETIC_FIXTURE: tests/fixtures/phase4c (archive produced by the real Phase 4-A producer on a synthetic snapshot,
with fixture governance). Governance books are always temporary COPIES; the repository's governance/registry is
fingerprinted and must be unchanged.
"""
import ast
import copy
import hashlib
import json
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from strategy_agent.evaluation import phase4c as C
from strategy_agent.orchestration import adapters as AD
from strategy_agent.orchestration import request as Q
from strategy_agent.orchestration.orchestrator import ARCHIVE_FILES, Orchestrator
from strategy_agent.orchestration.tasks import IllegalTransition, TaskLog
from strategy_agent.registry.chain import ChainError

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests/fixtures/phase4c"
RUN = "20261106_224000_0123456"
TPE = timezone(timedelta(hours=8))
URL = "https://example.test/approval"


class Clock:
    def __init__(self):
        self.t = datetime(2026, 11, 7, 12, 30, tzinfo=TPE)

    def __call__(self):
        self.t += timedelta(seconds=1)
        return self.t


def repo_fingerprint():
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for d in ("governance", "registry")
            for p in (ROOT / d).rglob("*") if p.is_file()}


@pytest.fixture
def env(tmp_path):
    gov = tmp_path / "gov"
    shutil.copytree(FIX / "governance_root", gov)
    src = tmp_path / "archives"
    shutil.copytree(FIX / RUN, src / RUN)
    return {"tmp": tmp_path, "gov": gov, "src": src, "clock": Clock(), "before": repo_fingerprint()}


def replay_request(src: Path) -> dict:
    a = C.read_archive(src / RUN, expected_manifest_sha256=C.sha256_bytes((src / RUN / "MANIFEST.json").read_bytes()))
    ri = C.parse(a)["run_info"]
    doc = Q.load(ROOT / "docs/examples/research_requests/tsm_sma520_dev_archive_replay.json")
    doc.update(request_id="RR-SYNTHETIC-FIXTURE-REPLAY", dataset_snapshot_id=ri["snapshot_id"],
               dataset_manifest_sha256=ri["manifest_sha256"],
               replay_source={"run_id": RUN, "archive_manifest_sha256": a["manifest_sha256"]})
    return doc


def orch(env, **kw):
    kw.setdefault("governance_root", env["gov"])
    kw.setdefault("source_archive_root", env["src"])
    return Orchestrator(env["tmp"] / kw.pop("state", "state"), env["tmp"] / "out", clock=env["clock"], **kw)


def synthetic_request(src):
    d = replay_request(src)
    d.update(request_id="RR-SYNTHETIC-LIFECYCLE", execution_mode="SYNTHETIC_TEST",
             requested_change={"kind": "SYNTHETIC_TEST", "description": "orchestrator lifecycle test"})
    return d


# ------------------------------------------------------------------------------------------------------- 1-8

def test_01_valid_request_reaches_pending_approval_and_never_auto_approves(env):
    o = orch(env)
    r = o.submit(replay_request(env["src"]), actor="hh4832")
    assert r["state"] == "PENDING_APPROVAL" and r["governance"]["status"] == "PASS"
    assert {c["id"] for c in r["governance"]["checks"]} >= {f"G{i}" for i in ()} | {
        "G1_REQUEST_SCHEMA", "G2_STRATEGY_SPEC", "G3_DATASET", "G4_CORE_COST", "G5_AUTHORIZATION", "G6_AUTHORIZATION_SCOPE",
        "G7_BUDGET", "G8_HOLDOUT", "G9_FORMAL_GATE", "G10_EXECUTION_MODE"}
    assert r["governance"]["trading_permission"] is False and r["governance"]["formal_market_status"] == "NO_GO"
    assert o.status(r["task_id"])["state"] == "PENDING_APPROVAL"        # nothing proceeds without a human


def test_02_invalid_schema_is_blocked(env):
    d = replay_request(env["src"])
    d["force_run"] = True
    r = orch(env).submit(d, actor="hh4832")
    assert r["state"] == "BLOCKED" and r["errors"]


def test_03_missing_strategy_spec_is_blocked(env):
    d = replay_request(env["src"])
    d["strategy_spec_ref"] = "specs/research/does_not_exist.json"
    r = orch(env).submit(d, actor="hh4832")
    assert r["state"] == "BLOCKED"
    assert [c for c in r["governance"]["checks"] if c["id"] == "G2_STRATEGY_SPEC"][0]["status"] == "FAIL"


def test_04_missing_dataset_sha_is_blocked(env):
    d = replay_request(env["src"])
    d.pop("dataset_manifest_sha256")
    assert orch(env).submit(d, actor="hh4832")["state"] == "BLOCKED"
    d = replay_request(env["src"])
    d["dataset_manifest_sha256"] = d["dataset_manifest_sha256"][:12]
    assert orch(env, state="s2").submit(d, actor="hh4832")["state"] == "BLOCKED"


@pytest.mark.parametrize("aref", ["PENDING", {"authorization_id": "RA-NOT-ISSUED-0001"}])
def test_05_no_valid_authorization_is_blocked(env, aref):
    d = replay_request(env["src"])
    d["authorization_ref"] = aref
    r = orch(env).submit(d, actor="hh4832")
    g5 = [c for c in r["governance"]["checks"] if c["id"] == "G5_AUTHORIZATION"][0]
    assert r["state"] == "BLOCKED" and g5["status"] == "FAIL"


def test_06_exhausted_authorization_blocks_a_real_run_and_real_is_disabled(env):
    d = replay_request(env["src"])
    d.update(request_id="RR-REAL-ATTEMPT", execution_mode="RESEARCH_ONLY_REAL", research_budget={"batches_requested": 1},
             requested_change={"kind": "NEW_HYPOTHESIS", "description": "would consume"})
    d.pop("replay_source")
    r = orch(env).submit(d, actor="hh4832")
    fails = {c["id"] for c in r["governance"]["checks"] if c["status"] == "FAIL"}
    assert r["state"] == "BLOCKED" and {"G7_BUDGET", "G10_EXECUTION_MODE"} <= fails
    with pytest.raises(AD.AdapterDisabled):
        AD.ResearchOnlyRunnerAdapter().execute(d, env["tmp"])


def test_07_illegal_holdout_use_is_blocked(env):
    d = replay_request(env["src"])
    d["holdout_policy"] = {"access": "NO_ACCESS", "lock_id": "HOLDOUT-OTHER-v9"}
    r = orch(env).submit(d, actor="hh4832")
    assert r["state"] == "BLOCKED" and [c for c in r["governance"]["checks"] if c["id"] == "G8_HOLDOUT"][0]["status"] == "FAIL"
    d = replay_request(env["src"])
    d["research_window"] = {"warmup_start": "2024-05-29T00:00:00-04:00", "start": "2024-06-03T00:00:00-04:00",
                            "end": "2025-02-01T00:00:00-05:00"}
    r = orch(env, state="s2").submit(d, actor="hh4832")
    assert r["state"] == "BLOCKED"
    assert d["holdout_policy"]["access"] == "NO_ACCESS"
    bad = copy.deepcopy(replay_request(env["src"]))
    bad["holdout_policy"]["access"] = "READ"
    assert orch(env, state="s3").submit(bad, actor="hh4832")["state"] == "BLOCKED"


def test_08_market_gate_change_is_blocked(env):
    p = env["gov"] / "governance/market_readiness.json"
    m = json.loads(p.read_text())
    m["markets"]["IBKR_TSM"]["gates"]["COST_APPROVAL"] = {"status": "PASS"} if isinstance(
        m["markets"]["IBKR_TSM"]["gates"]["COST_APPROVAL"], dict) else "PASS"
    p.write_text(json.dumps(m))
    r = orch(env).submit(replay_request(env["src"]), actor="hh4832")
    g6 = [c for c in r["governance"]["checks"] if c["id"] == "G6_AUTHORIZATION_SCOPE"][0]
    assert r["state"] == "BLOCKED" and "RA10" in json.dumps(g6)


# ------------------------------------------------------------------------------------------------------ 9-12

def test_09_illegal_transitions_fail_closed(env):
    o = orch(env)
    tid = o.submit(replay_request(env["src"]), actor="hh4832")["task_id"]
    with pytest.raises(IllegalTransition):
        o.log.transition(tid, "RUNNING", actor="x", actor_kind="SYSTEM", worker="w")       # skip approval
    with pytest.raises(IllegalTransition):
        o.log.transition(tid, "ARCHIVED", actor="x", actor_kind="SYSTEM")
    assert o.dispatch(tid)["state"] == "PENDING_APPROVAL"                                  # dispatch does nothing
    with pytest.raises(IllegalTransition):
        o.approve(tid, approver="intruder", approval_ref=URL)
    with pytest.raises(IllegalTransition):
        o.approve(tid, approver="hh4832", approval_ref="http://insecure")
    with pytest.raises(IllegalTransition):
        o.log.transition(tid, "BLOCKED", actor="x", actor_kind="SYSTEM")                   # reason required
    o.approve(tid, approver="hh4832", approval_ref=URL)
    o.approve(tid, approver="hh4832", approval_ref=URL)                                    # idempotent, not twice
    assert sum(e["to"] == "APPROVED" for e in o.log.get(tid).events) == 1


def test_10_duplicate_request_returns_the_same_task(env):
    o = orch(env)
    a = o.submit(replay_request(env["src"]), actor="hh4832")
    b = o.submit(replay_request(env["src"]), actor="someone-else")
    assert b["duplicate"] and b["task_id"] == a["task_id"]
    assert sum(e["to"] == "PROPOSED" for e in o.log.records()) == 1


def test_11_retries_never_reconsume_and_dispatch_is_idempotent(env):
    o = orch(env)
    tid = o.submit(replay_request(env["src"]), actor="hh4832")["task_id"]
    o.approve(tid, approver="hh4832", approval_ref=URL)
    uses_before = (env["gov"] / "registry/research_authorization_uses.jsonl").read_bytes()
    assert o.dispatch(tid)["state"] == "ARCHIVED"
    assert o.dispatch(tid)["state"] == "ARCHIVED"                       # second dispatch is a no-op
    assert sum(e["to"] == "RUNNING" for e in o.log.get(tid).events) == 1
    assert (env["gov"] / "registry/research_authorization_uses.jsonl").read_bytes() == uses_before
    with pytest.raises(IllegalTransition):
        o.retry(tid, actor="hh4832", reason="not failed")
    d = replay_request(env["src"])
    d["request_id"] = "RR-SYNTHETIC-FIXTURE-REPLAY-2"
    empty = env["tmp"] / "empty_archives"
    empty.mkdir()
    o2 = orch(env, state="s2", source_archive_root=empty)              # archive not on disk -> FAILED
    t2 = o2.submit(d, actor="hh4832")["task_id"]
    o2.approve(t2, approver="hh4832", approval_ref=URL)
    st = o2.dispatch(t2)
    assert st["state"] == "FAILED" and st["last_data"]["consumed_batches"] == 0
    assert o2.dispatch(t2)["state"] == "FAILED"                         # no silent retry
    shutil.copytree(env["src"] / RUN, empty / RUN)                      # archive restored
    o2.retry(t2, actor="hh4832", reason="archive restored")             # explicit, non-consuming
    assert o2.dispatch(t2)["state"] == "ARCHIVED"
    assert sum(e["to"] == "RUNNING" for e in o2.log.get(t2).events) == 2
    assert (env["gov"] / "registry/research_authorization_uses.jsonl").read_bytes() == uses_before
    real = AD.ResearchOnlyRunnerAdapter()
    assert real.consumes_budget and not real.enabled


def test_12_crash_during_running_is_failed_not_reexecuted_and_evaluating_resumes(env):
    o = orch(env, worker="A", lease_seconds=60)
    tid = o.submit(replay_request(env["src"]), actor="hh4832")["task_id"]
    o.approve(tid, approver="hh4832", approval_ref=URL)
    o.log.transition(tid, "QUEUED", actor="orchestrator", actor_kind="SYSTEM", expect_from="APPROVED")
    o.log.transition(tid, "RUNNING", actor="orchestrator", actor_kind="SYSTEM", worker="A", lease_seconds=60,
                     data={"attempt": 1, "governance": {"status": "PASS"}})            # ...then worker A crashes
    b = orch(env, worker="B")                                             # restart: new process, same log
    with pytest.raises(IllegalTransition):                                # lease still held
        b.log.transition(tid, "FAILED", actor="orchestrator", actor_kind="SYSTEM", reason="x", worker="B")
    assert b.recover() == []
    env["clock"].t += timedelta(seconds=120)
    assert b.recover() == [{"task_id": tid, "action": "FAILED_NOT_REEXECUTED"}]
    assert b.status(tid)["state"] == "FAILED"

    o3 = orch(env, state="s3", worker="A", lease_seconds=60)
    t3 = o3.submit(replay_request(env["src"]), actor="hh4832")["task_id"]
    o3.approve(t3, approver="hh4832", approval_ref=URL)
    o3.log.transition(t3, "QUEUED", actor="orchestrator", actor_kind="SYSTEM")
    o3.log.transition(t3, "RUNNING", actor="orchestrator", actor_kind="SYSTEM", worker="A", lease_seconds=60,
                      data={"attempt": 1, "governance": {"status": "PASS", "formal_market_status": "NO_GO"}})
    ref = o3.adapters["ARCHIVE_REPLAY"].execute(o3.request_of(t3), env["tmp"] / "x")
    o3.log.transition(t3, "EVALUATING", actor="orchestrator", actor_kind="SYSTEM", worker="A", lease_seconds=60,
                      data={"execution_reference": ref})                    # ...crash during evaluation
    env["clock"].t += timedelta(seconds=120)
    b3 = orch(env, state="s3", worker="B")
    assert b3.recover() == [{"task_id": t3, "action": "EVALUATION_RESUMED"}]
    assert b3.status(t3)["state"] == "ARCHIVED"


# ----------------------------------------------------------------------------------------------------- 13-18

def test_13_replay_of_an_archived_result_is_read_only_and_archived(env, monkeypatch):
    # Phase 4-C C8 also asserts no execution module is loaded in the PROCESS; other tests in a shared pytest process
    # import the runner, so that guarantee is asserted in a fresh subprocess by test_16_17. Here: the replay path.
    monkeypatch.setattr(C, "EXECUTOR_MODULES", ())
    o = orch(env)
    tid = o.submit(replay_request(env["src"]), actor="hh4832")["task_id"]
    o.approve(tid, approver="hh4832", approval_ref=URL)
    src_before = C.fingerprint(env["src"] / RUN)
    st = o.dispatch(tid)
    assert st["state"] == "ARCHIVED"
    assert C.fingerprint(env["src"] / RUN) == src_before
    ev = next(e for e in o.log.get(tid).events if e["to"] == "EVALUATED")["data"]
    assert ev["research_decision"]["acceptance_status"] == "PASS"
    assert ev["research_decision"]["strategy_verdict"] == "RETEST" and ev["research_decision"]["evidence_sufficiency"] == "UNDETERMINED"
    arch = Path(next(e for e in o.log.get(tid).events if e["to"] == "ARCHIVED")["data"]["task_archive"])
    assert sorted(p.name for p in arch.iterdir()) == sorted(ARCHIVE_FILES)
    info = (arch / "run_info.txt").read_text()
    assert "consumed_batches=0" in info and "real_backtest_executed=false" in info and "trading_permission=false" in info
    ref = json.loads((arch / "execution_reference.json").read_text())
    assert ref["strategy_executed"] is False and ref["consumed_batches"] == 0
    assert repo_fingerprint() == env["before"]


def test_14_missing_evaluator_evidence_gives_blocked_or_retest_never_keep(env):
    a = env["src"] / RUN
    p = a / "trades.csv"
    rows = p.read_text().splitlines()
    p.write_text("\n".join([rows[0]] + [r for r in rows[1:] if not r.startswith("BASE,")]) + "\n")
    m = json.loads((a / "MANIFEST.json").read_text())
    m["files_sha256"]["trades.csv"] = hashlib.sha256(p.read_bytes()).hexdigest()
    (a / "MANIFEST.json").write_text(json.dumps(m))
    o = orch(env)
    d = replay_request(env["src"])
    tid = o.submit(d, actor="hh4832")["task_id"]
    o.approve(tid, approver="hh4832", approval_ref=URL)
    assert o.dispatch(tid)["state"] == "ARCHIVED"
    dec = next(e for e in o.log.get(tid).events if e["to"] == "EVALUATED")["data"]["research_decision"]
    assert dec["acceptance_status"] != "PASS" and dec["strategy_verdict"] != "KEEP"
    assert dec["evidence_sufficiency"] == "UNDETERMINED"
    # a manifest that no longer matches the request's independent pin never reaches the evaluator
    o2 = orch(env, state="s2")
    d2 = replay_request(env["src"])
    d2["replay_source"]["archive_manifest_sha256"] = "0" * 64
    t2 = o2.submit(d2, actor="hh4832")["task_id"]
    o2.approve(t2, approver="hh4832", approval_ref=URL)
    st = o2.dispatch(t2)
    assert st["state"] == "FAILED" and st["history"][-1]["reason"] == "ARCHIVE_MANIFEST_SHA"


def test_15_registry_and_archives_are_never_overwritten(env):
    o = orch(env)
    tid = o.submit(replay_request(env["src"]), actor="hh4832")["task_id"]
    o.approve(tid, approver="hh4832", approval_ref=URL)
    o.dispatch(tid)
    arch = Path(next(e for e in o.log.get(tid).events if e["to"] == "ARCHIVED")["data"]["task_archive"])
    with pytest.raises(IllegalTransition):
        o.log.transition(tid, "ARCHIVED", actor="x", actor_kind="SYSTEM")
    n_dirs = len(list(arch.parent.iterdir()))
    with pytest.raises(IllegalTransition):
        o._archive(tid, o.request_of(tid))                              # refused before creating anything
    assert len(list(arch.parent.iterdir())) == n_dirs
    # archive-name collision: the existing directory is never reused; the task stays EVALUATED and is archived later
    class Frozen:
        t = datetime(2026, 11, 7, 13, 0, tzinfo=TPE)
        def __call__(self):
            return self.t
    fz = Frozen()
    o2 = Orchestrator(env["tmp"] / "s2", env["tmp"] / "out", clock=fz, governance_root=env["gov"],
                      source_archive_root=env["src"])
    d = replay_request(env["src"]); d["request_id"] = "RR-COLLISION-TEST"
    t2 = o2.submit(d, actor="hh4832")["task_id"]
    o2.approve(t2, approver="hh4832", approval_ref=URL)
    taken = env["tmp"] / "out/tasks" / f"{fz.t:%Y%m%d_%H%M%S}_{o2.code['git_sha'][:7]}"
    taken.mkdir(parents=True)
    with pytest.raises(FileExistsError):
        o2.dispatch(t2)
    assert o2.status(t2)["state"] == "EVALUATED" and list(taken.iterdir()) == []
    fz.t += timedelta(seconds=1)
    assert o2.recover() == [{"task_id": t2, "action": "ARCHIVED_AFTER_RESTART"}]
    assert o2.status(t2)["state"] == "ARCHIVED"
    log = env["tmp"] / "state/task_events.jsonl"
    lines = log.read_text().splitlines()
    lines[1] = lines[1].replace('"VALIDATED"', '"APPROVED"')
    log.write_text("\n".join(lines) + "\n")
    with pytest.raises(ChainError):
        TaskLog(log, env["clock"]).records()


def test_16_17_never_calls_the_real_runner_core_or_a_broker(env):
    code = f"""
import json,sys,shutil
sys.path.insert(0,{str(ROOT / 'tests')!r})
import test_orchestrator_lifecycle as T
from pathlib import Path
env={{'tmp':Path({str(env['tmp'] / 'sub')!r}),'gov':Path({str(env['gov'])!r}),'src':Path({str(env['src'])!r}),'clock':T.Clock()}}
env['tmp'].mkdir()
o=T.orch(env)
tid=o.submit(T.replay_request(env['src']),actor='hh4832')['task_id']
o.approve(tid,approver='hh4832',approval_ref=T.URL)
st=o.dispatch(tid)
bad=[m for m in sys.modules if m.startswith(('strategy_agent.research_only.runner','strategy_agent.research_only.development_run',
  'strategy_agent.research_only.core_phase4a','strategy_agent.adapters','market_research_core','ib_insync','ibapi','requests','urllib3'))]
dec=next(e for e in o.log.get(tid).events if e['to']=='EVALUATED')['data']['research_decision']
print(json.dumps({{'state':st['state'],'bad':bad,'acceptance':dec['acceptance_status'],'c8':dec['acceptance_checks']['C8_governance_isolation']}}))
"""
    out = json.loads(subprocess.check_output([sys.executable, "-c", code], text=True, cwd=ROOT).strip().splitlines()[-1])
    assert out == {"state": "ARCHIVED", "bad": [], "acceptance": "PASS", "c8": "PASS"}   # clean process: C1-C8 PASS
    for f in (ROOT / "src/strategy_agent/orchestration").glob("*.py"):
        tree = ast.parse(f.read_text())
        mods = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | \
               {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not [m for m in mods if m.startswith(("strategy_agent.research_only.runner", "strategy_agent.research_only.development",
                                                     "strategy_agent.research_only.core_phase4a", "strategy_agent.adapters",
                                                     "market_research_core", "ib_insync", "ibapi", "requests", "socket",
                                                     "http", "urllib"))], (f, mods)


def test_18_synthetic_task_completes_the_full_lifecycle_in_an_isolated_test_orchestrator(env):
    o = orch(env, test_mode=True, synthetic_fixture=FIX / RUN, governance_root=FIX / "governance_root")
    r = o.submit(synthetic_request(env["src"]), actor="test", actor_kind="TEST_FIXTURE")
    assert r["state"] == "PENDING_APPROVAL" and r["governance"]["status"] == "TEST_MODE_ONLY"
    with pytest.raises(IllegalTransition):
        o.approve(r["task_id"], approver="hh4832", approval_ref=URL)       # real approvers are not used in test mode
    o.approve(r["task_id"], approver="TEST_FIXTURE", approval_ref="test")
    st = o.dispatch(r["task_id"])
    assert st["state"] == "ARCHIVED"
    states = [h["to"] for h in st["history"]]
    assert states == ["PROPOSED", "VALIDATED", "PENDING_APPROVAL", "APPROVED", "QUEUED", "RUNNING", "EVALUATING",
                      "EVALUATED", "ARCHIVED"]
    arch = Path(next(e for e in o.log.get(r["task_id"]).events if e["to"] == "ARCHIVED")["data"]["task_archive"])
    assert arch.parent.name == "test_tasks"
    dec = json.loads((arch / "research_decision.json").read_text())
    assert dec["test_mode"] is True and "SYNTHETIC_TEST" in dec["label"]
    # the synthetic request is refused by a production orchestrator, and fixtures by a production constructor
    assert orch(env, state="prod").submit(synthetic_request(env["src"]), actor="x")["state"] == "BLOCKED"
    with pytest.raises(ValueError):
        orch(env, state="p2", synthetic_fixture=FIX / RUN)
    with pytest.raises(ValueError):
        Orchestrator(ROOT / "registry/tmp_state", env["tmp"] / "o", test_mode=True)


# ------------------------------------------------------------------------------------------ concurrency + CLI

def test_concurrent_workers_claim_a_task_once(env):
    o = orch(env)
    tid = o.submit(replay_request(env["src"]), actor="hh4832")["task_id"]
    o.approve(tid, approver="hh4832", approval_ref=URL)
    o.log.transition(tid, "QUEUED", actor="orchestrator", actor_kind="SYSTEM")
    wins, errors = [], []

    def claim(w):
        try:
            TaskLog(env["tmp"] / "state/task_events.jsonl", env["clock"]).transition(
                tid, "RUNNING", actor="orchestrator", actor_kind="SYSTEM", worker=w, expect_from="QUEUED",
                idempotency_key=f"{tid}:RUNNING:{w}")
            wins.append(w)
        except IllegalTransition as e:
            errors.append(e.code)
    ts = [threading.Thread(target=claim, args=(f"w{i}",)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(wins) == 1 and len(errors) == 7
    assert sum(e["to"] == "RUNNING" for e in o.log.get(tid).events) == 1


def test_cli_validate_submit_status_and_replay_guard(env):
    def cli(*args):
        r = subprocess.run([sys.executable, "-m", "strategy_agent.orchestration", *args], capture_output=True, text=True,
                           cwd=ROOT)
        return r.returncode, json.loads(r.stdout)
    req = env["tmp"] / "real.json"
    shutil.copy(ROOT / "docs/examples/research_requests/tsm_sma520_dev_research_only_real_DISABLED.json", req)
    rc, out = cli("validate", "--request", str(req))
    assert rc == 0 and out["valid"]
    common = ["--state-dir", str(env["tmp"] / "cli_state"), "--archive-root", str(env["tmp"] / "cli_out")]
    rc, out = cli("submit", "--request", str(req), "--actor", "hh4832", *common)
    assert rc == 1 and out["state"] == "BLOCKED"                        # real execution disabled / budget spent
    rc, st = cli("status", "--task-id", out["task_id"], *common)
    assert st["state"] == "BLOCKED"
    rc, err = cli("replay", "--task-id", out["task_id"], *common)
    assert rc == 2 and err["error"] == "REPLAY_ONLY"
    assert repo_fingerprint() == env["before"]
