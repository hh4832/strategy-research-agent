"""Phase 5-C1 registry reconciliation (append-only carry-over of a settled execution).

SYNTHETIC_FIXTURE: unit cases build tiny repo/state governance trees with the repository's own ChainedLog in tmp
dirs. The final test checks the COMMITTED repository registry after the 0002 reconciliation (no Drive access).
"""
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from strategy_agent.execution.c1_context import claim_ref
from strategy_agent.governance import registry_reconciliation as R
from strategy_agent.registry.chain import ChainedLog

AUTH = "RA-IBKR_TSM-SMA520_P5C1-0009"
OTHER = "RA-IBKR_TSM-SMA520_P5C1-0008"
RUN, TASK, REQ, MAN = "20261117_000000_abcdef0", "T-test", "a" * 64, "b" * 64
ROOT = Path(__file__).resolve().parents[1]


def sha(b):
    return hashlib.sha256(b).hexdigest()


def base_repo(tmp):
    repo = tmp / "repo"
    ChainedLog(repo / "governance/research_authorizations.jsonl").append(
        {"action": "APPROVE", "authorization_id": AUTH, "max_batches": 1})
    ChainedLog(repo / "governance/holdout_locks.jsonl").append({"lock_id": "HOLDOUT-X", "action": "APPROVE"})
    ChainedLog(repo / "registry/research_authorization_uses.jsonl").append({"authorization_id": "RA-OLD", "run_id": "r0"})
    ChainedLog(repo / "registry/research_budget_events.jsonl").append(
        {"event_type": "RESERVED", "reservation_id": "BR-old", "authorization_id": "RA-OLD"})
    ChainedLog(repo / "registry/authorization_bindings.jsonl").append({"authorization_id": "RA-OLD", "binding_id": "AB-old"})
    return repo


def execute(state, *, auth=AUTH, run=RUN, lifecycle=R.LIFECYCLE, manifest=MAN, extra_use=False):
    """What a settled EXECUTE appends to its own copy of the governance state."""
    req_bytes, ev_bytes = b'{"request": 1}\n', b'{"evidence": 1}\n'
    (state / "registry/bound_requests").mkdir(parents=True, exist_ok=True)
    (state / "registry/approval_evidence").mkdir(parents=True, exist_ok=True)
    (state / f"registry/bound_requests/{REQ}.json").write_bytes(req_bytes)
    ev_name = f"registry/approval_evidence/{sha(ev_bytes)}.json"
    (state / ev_name).write_bytes(ev_bytes)
    ChainedLog(state / "registry/authorization_bindings.jsonl").append(
        {"authorization_id": auth, "binding_id": "AB-1", "bound_request_path": f"registry/bound_requests/{REQ}.json",
         "bound_request_file_sha256": sha(req_bytes), "approval_evidence_path": ev_name,
         "approval_evidence_sha256": sha(ev_bytes)})
    use = ChainedLog(state / "registry/research_authorization_uses.jsonl").append({"authorization_id": auth, "run_id": run})
    if extra_use:
        ChainedLog(state / "registry/research_authorization_uses.jsonl").append({"authorization_id": auth, "run_id": run})
    ident = {"reservation_id": "BR-1", "authorization_id": auth, "task_id": TASK, "attempt_id": TASK + ":1", "binding_id": "AB-1",
             "approval_evidence_sha256": "e" * 64}
    log = ChainedLog(state / "registry/research_budget_events.jsonl")
    for t in lifecycle:
        e = {"event_type": t, **ident}
        if t == "RESERVED":
            e |= {"request_sha256": REQ, "planned_run_id": run, "batch_units": 1,
                  "request_file_sha256": sha(req_bytes)}
        if t == "COMMITTED":
            e |= {"use_record_sha256": use["record_sha256"], "use_seq": use["seq"]}
        if t in ("EXECUTION_COMPLETED", "EVIDENCE_VERIFIED", "CONSUMED"):
            e |= {"output_manifest_sha256": manifest, "receipt_sha256": "c" * 64}
        log.append(e)


def setup(tmp, **kw):
    repo = base_repo(tmp)
    state = tmp / "state"
    shutil.copytree(repo, state)
    execute(state, **kw)
    return repo, state


def kw(refs=None, claim=None):
    ref = claim_ref(AUTH)
    return dict(authorization_id=AUTH, run_id=RUN, task_id=TASK, request_sha256=REQ, output_manifest_sha256=MAN,
                claim_refs=[ref, claim_ref(OTHER)] if refs is None else refs,
                remote_claim={"authorization_id": AUTH, "claim_ref": ref, "request_sha256": REQ,
                              "governance_sha": "0" * 40} if claim is None else claim)


def has(p, prefix):
    return any(x.startswith(prefix) for x in p["problems"])


# 1. hash chain PASS / 5. main == state governance
def test_01_settled_execution_plans_pass_and_reports_exact_new_seqs(tmp_path):
    repo, state = setup(tmp_path)
    p = R.plan(repo, state, **kw())
    assert p["status"] == "PASS", p["problems"]
    assert p["ledgers"][R.USES]["new_seqs"] == [1] and p["ledgers"][R.BUDGET]["new_seqs"] == [1, 2, 3, 4, 5, 6]
    assert p["ledgers"][R.BINDINGS]["new_seqs"] == [1] and len(p["new_files"]) == 2


# 2. authorization use count / run id
def test_02_authorization_use_count_and_run_id(tmp_path):
    repo, state = setup(tmp_path / "a", extra_use=True)
    assert has(R.plan(repo, state, **kw()), "USE_COUNT:2/1")
    repo, state = setup(tmp_path / "b", run="other_run")
    p = R.plan(repo, state, **kw())
    assert has(p, "USE_RUN_ID_MISMATCH") and has(p, "BUDGET_IDENTITY_MISMATCH")


# 3. budget settlement
@pytest.mark.parametrize("life,code", [
    (["RESERVED", "COMMITTED", "EXECUTION_STARTED"], "BUDGET_NOT_SETTLED"),
    (["RESERVED", "COMMITTED", "EXECUTION_STARTED", "RECONCILIATION_REQUIRED"], "BUDGET_NOT_SETTLED"),
    (["RESERVED", "CONSUMED"], "BUDGET_ILLEGAL_TRANSITION"),
    (["RESERVED", "COMMITTED", "EVIDENCE_VERIFIED", "CONSUMED"], "BUDGET_ILLEGAL_TRANSITION"),
])
def test_03_budget_settlement_must_be_complete_and_legal(tmp_path, life, code):
    repo, state = setup(tmp_path, lifecycle=life)
    assert has(R.plan(repo, state, **kw()), code)


def test_03b_output_manifest_must_match(tmp_path):
    repo, state = setup(tmp_path, manifest="f" * 64)
    assert has(R.plan(repo, state, **kw()), "OUTPUT_MANIFEST_MISMATCH")


# 4. CLAIM not duplicated / present / consistent
def test_04_claim_ref_unique_and_consistent(tmp_path):
    repo, state = setup(tmp_path)
    ref = claim_ref(AUTH)
    assert has(R.plan(repo, state, **kw(refs=[ref, ref])), "CLAIM_REF_DUPLICATED")
    assert has(R.plan(repo, state, **kw(refs=[claim_ref(OTHER)])), "CLAIM_REF_COUNT:0")
    bad = {"authorization_id": AUTH, "claim_ref": ref, "request_sha256": "9" * 64}
    assert has(R.plan(repo, state, **kw(claim=bad)), "REMOTE_CLAIM_MISMATCH")
    p = R.plan(repo, state, **dict(kw(), claim_refs=None))
    assert has(p, "CLAIM_EVIDENCE_MISSING")


# 5. governance files must be identical / 6. holdout untouched
def test_05_06_governance_and_holdout_must_be_identical(tmp_path):
    repo, state = setup(tmp_path)
    ChainedLog(state / "governance/holdout_locks.jsonl").append({"lock_id": "HOLDOUT-X", "action": "RELEASE"})
    assert has(R.plan(repo, state, **kw()), "GOVERNED_FILE_DIFFERS:governance/holdout_locks.jsonl")
    repo, state = setup(tmp_path / "b")
    (state / "governance/new_policy.json").write_text("{}")
    assert has(R.plan(repo, state, **kw()), "UNEXPECTED_NEW_FILE:governance/new_policy.json")


# 7. idempotent: a second sync adds nothing
def test_07_apply_is_byte_exact_and_idempotent(tmp_path):
    repo, state = setup(tmp_path)
    out = R.apply(repo, state, **kw())
    assert out["applied"] is True
    for f in R.LEDGERS:
        assert (repo / f).read_bytes() == (state / f).read_bytes()
    snap = {f: (repo / f).read_bytes() for f in R.LEDGERS}
    again = R.apply(repo, state, **kw())
    assert again["applied"] is False and again["nothing_to_add"] is True
    assert {f: (repo / f).read_bytes() for f in R.LEDGERS} == snap
    for f in R.LEDGERS:
        ChainedLog(repo / f).verify()


# 8. broken / rewritten / out-of-scope chains FAIL (and apply refuses, writing nothing)
def test_08_broken_chain_fails_and_nothing_is_written(tmp_path):
    repo, state = setup(tmp_path)
    p = state / R.USES
    lines = p.read_text().splitlines()
    rec = json.loads(lines[-1])
    rec["run_id"] = "tampered"
    p.write_text("\n".join(lines[:-1] + [json.dumps(rec, sort_keys=True, separators=(",", ":"))]) + "\n")
    before = (repo / R.USES).read_bytes()
    assert has(R.plan(repo, state, **kw()), "state:registry/research_authorization_uses.jsonl: CHAIN_BROKEN")
    with pytest.raises(RuntimeError, match="RECONCILIATION_REFUSED"):
        R.apply(repo, state, **kw())
    assert (repo / R.USES).read_bytes() == before


def test_08b_repo_not_prefix_and_out_of_scope_records_fail(tmp_path):
    repo, state = setup(tmp_path / "a")
    ChainedLog(repo / R.USES).append({"authorization_id": "RA-OLD", "run_id": "diverged"})   # main moved elsewhere
    assert has(R.plan(repo, state, **kw()), "REPO_NOT_PREFIX_OF_STATE:registry/research_authorization_uses.jsonl")
    repo, state = setup(tmp_path / "b")
    ChainedLog(state / R.BINDINGS).append({"authorization_id": OTHER, "binding_id": "AB-x"})
    assert has(R.plan(repo, state, **kw()), "OUT_OF_SCOPE_RECORD:registry/authorization_bindings.jsonl")


def test_08c_sibling_fork_is_reported_never_merged(tmp_path):
    repo, state = setup(tmp_path)
    sib = tmp_path / "sibling"
    shutil.copytree(tmp_path / "repo", sib)
    ChainedLog(sib / R.USES).append({"authorization_id": OTHER, "run_id": "failed_run"})
    p = R.plan(repo, state, **dict(kw(), sibling_states=[sib]))
    assert p["status"] == "PASS"
    f = next(x for x in p["forks"] if x["ledger"] == R.USES)
    assert f["diverges_at_seq"] == 1 and f["sibling_only"][0]["authorization_id"] == OTHER and f["repo_is_prefix"]
    R.apply(repo, state, **kw())
    assert OTHER not in (repo / R.USES).read_text()


# committed repository state after the 0002 reconciliation
def test_repository_registry_records_p5c1_0002_once_and_settled():
    uses = ChainedLog(ROOT / R.USES).verify()
    mine = [u for u in uses if u["authorization_id"] == "RA-IBKR_TSM-SMA520_P5C1-0002"]
    assert len(mine) == 1 and mine[0]["run_id"] == "20261117_000458_a4cc87e"
    assert mine[0]["record_sha256"] == "cbdd8fe26ba5d6030a4f9a9c6802f9b09a2e22d399c4c0cea2becf7f400e777e"
    assert not [u for u in uses if u["authorization_id"] == "RA-IBKR_TSM-SMA520_P5C1-0001"]   # no invented 0001 use
    from strategy_agent.execution.budget import _fold
    res = [r for r in _fold(ChainedLog(ROOT / R.BUDGET).verify()).values()
           if r["authorization_id"] == "RA-IBKR_TSM-SMA520_P5C1-0002"]
    assert len(res) == 1 and res[0]["state"] == "CONSUMED" and res[0]["history"] == R.LIFECYCLE
    assert res[0]["evidence_verified"]["output_manifest_sha256"] == \
        "579283777cdff1864ced571de79fa322449353efcbb40e55f65f49d0e2860a69"
    b = [x for x in ChainedLog(ROOT / R.BINDINGS).verify() if x["authorization_id"] == "RA-IBKR_TSM-SMA520_P5C1-0002"]
    assert len(b) == 1
    for k, s in (("bound_request_path", "bound_request_file_sha256"), ("approval_evidence_path", "approval_evidence_sha256")):
        assert sha((ROOT / b[0][k]).read_bytes()) == b[0][s]
    holdout = (ROOT / "governance/holdout_locks.jsonl").read_bytes()
    assert sha(holdout) == "1e463eb998d5ed89e558002df03025d7bac2c463ab1903adee82237d87ebe69c"


# legacy B3-C classifier: settled C1 records are recognized, nothing else is
@pytest.fixture
def b3c_copy(tmp_path, monkeypatch):
    import strategy_agent.readiness.candidate as candidate
    from strategy_agent.readiness import b3c
    shutil.copytree(ROOT, tmp_path / "r", ignore=shutil.ignore_patterns(".git", "__pycache__", "*.egg-info", ".pytest_cache"))
    monkeypatch.setattr(candidate, "ROOT", tmp_path / "r")
    monkeypatch.setattr(b3c, "ROOT", tmp_path / "r")
    return tmp_path / "r", b3c


def test_b3c_classifier_accepts_only_the_settled_c1_reservation(b3c_copy):
    root, b3c = b3c_copy
    st = b3c.formal_authorization_state()
    assert st["state"] == "GRANTED_USED" and st["execution"]["other_reservations"] == 0
    assert st["execution"]["c1_recognized_reservations"] == {"BR-d5b550522c2cfd0c5b828fc9": "CONSUMED"}


@pytest.mark.parametrize("case", ["unknown_authorization", "second_c1_reservation", "unsettled_c1", "second_c1_use"])
def test_b3c_classifier_still_fails_closed(b3c_copy, case):
    root, b3c = b3c_copy
    ev = ChainedLog(root / R.BUDGET)
    if case == "unknown_authorization":
        ev.append({"event_type": "RESERVED", "reservation_id": "BR-x", "authorization_id": "RA-UNKNOWN-0001", "task_id": "T-x"})
    elif case == "second_c1_reservation":
        ev.append({"event_type": "RESERVED", "reservation_id": "BR-x", "authorization_id": "RA-IBKR_TSM-SMA520_P5C1-0002",
                   "task_id": "T-x"})
    elif case == "unsettled_c1":
        ev.append({"event_type": "RESERVED", "reservation_id": "BR-x", "authorization_id": "RA-IBKR_TSM-SMA520_P5C1-0001",
                   "task_id": "T-x"})
    else:
        ChainedLog(root / R.USES).append({"authorization_id": "RA-IBKR_TSM-SMA520_P5C1-0002", "run_id": "again"})
    assert b3c.formal_authorization_state()["state"] == "UNEXPECTED"


# P5C1-0001 sealed fork reference (no 0001 bytes in Git; consistency with main's chains only)
def test_0001_fork_reference_is_consistent_with_main_and_not_appended():
    ref = json.loads((ROOT / "docs/evidence/phase5c1/p5c1_0001_fork/FORK_ARCHIVE_REFERENCE.json").read_text())
    assert ref["authorization_id"] == "RA-IBKR_TSM-SMA520_P5C1-0001"
    d = ref["disposition"]
    assert d["settlement_state"] == "RECONCILIATION_REQUIRED" and d["appended_to_main"] is False
    assert not any(d[k] for k in ("renumbered_or_rehashed", "use_fact_revoked", "budget_restored", "new_claim",
                                  "counts_as_completed_research"))
    assert ref["claim"]["ref"] == claim_ref("RA-IBKR_TSM-SMA520_P5C1-0001")
    assert ref["archive"]["archive_index_status"] == "COMPLETE"
    for ledger, f in ref["fork_ledgers"].items():
        main = ChainedLog(ROOT / ledger).verify()
        k = f["forks_at_seq"]
        assert main[k - 1]["record_sha256"] == f["fork_parent_record_sha256"]          # forks off main's prefix
        assert f["fork_records"][0]["prev_sha256"] == f["fork_parent_record_sha256"]
        assert [r["seq"] for r in f["fork_records"]] == list(range(k, f["records"]))
        main_shas = {r["record_sha256"] for r in main}
        assert not main_shas & {r["record_sha256"] for r in f["fork_records"]}       # never appended
        assert main[k]["authorization_id"] == "RA-IBKR_TSM-SMA520_P5C1-0002"          # 0002 occupies that position
    assert ref["fork_ledgers"]["registry/research_budget_events.jsonl"]["fork_records"][-1]["event_type"] == \
        "RECONCILIATION_REQUIRED"


# RECONCILIATION_REQUIRED is never CONSUMED and never restores eligibility
def test_reconciliation_required_is_not_consumed_and_does_not_restore_budget(tmp_path):
    from datetime import datetime
    from strategy_agent.execution.budget import BudgetRefused, _fold
    from strategy_agent.execution.testkit import ledger_for
    root = tmp_path / "r"
    for d in ("governance", "registry"):
        shutil.copytree(ROOT / d, root / d)
    aid, worker = "RA-IBKR_TSM-SMA520_P5C1-0001", "colab-hh4832-exec-01"
    L = ledger_for(root, clock=lambda: datetime.fromisoformat("2026-11-17T10:00:00+08:00"))
    L.register_executor(worker, actor="test")
    r = L.reserve(authorization_id=aid, request_sha256="c" * 64, task_id="T-" + "c" * 20, attempt_id="1",
                  approval_evidence_sha256="e" * 64, binding_id=None, worker_id=worker, planned_run_id="run_0001")
    L.commit(r["reservation_id"], worker_id=worker, actor=worker, scenarios=["BASE"])
    L.require_reconciliation(r["reservation_id"], reason="synthetic failure after commit", worker_id=worker)
    st = _fold(L.events())[r["reservation_id"]]
    assert st["state"] == "RECONCILIATION_REQUIRED" and st["state"] != "CONSUMED"
    assert L.capacity(aid)["used"] == 1                                             # the use fact stays counted
    with pytest.raises(BudgetRefused, match="BUDGET_EXHAUSTED"):                    # no second slot
        L.reserve(authorization_id=aid, request_sha256="d" * 64, task_id="T-" + "d" * 20, attempt_id="1",
                  approval_evidence_sha256="e" * 64, binding_id=None, worker_id=worker, planned_run_id="run_0001b")
    with pytest.raises(BudgetRefused):                                              # cannot be settled as CONSUMED
        L.settle(r["reservation_id"], receipt_sha256="1" * 64, output_manifest_sha256="2" * 64, archive_ref="x",
                 worker_id=worker, recheck=lambda *_: None)
    ev = ChainedLog(root / R.BUDGET)                                                 # forged CONSUMED: illegal
    ev.append({"event_type": "CONSUMED", "reservation_id": r["reservation_id"], "authorization_id": aid})
    with pytest.raises(BudgetRefused, match="LEDGER_INCONSISTENT"):
        _fold(ev.verify())


def test_existing_remote_claim_ref_blocks_any_new_c1_execution():
    import base64
    from strategy_agent.execution import c1_claim as K
    aid = "RA-IBKR_TSM-SMA520_P5C1-0001"

    def transport(method, path, payload=None):
        assert method == "GET", "no write may be attempted"
        if path == "/repos/" + K.REPO:
            return 200, {"id": K.REPO_ID, "full_name": K.REPO}
        if path == "/user":
            return 200, {"login": "hh4832", "id": K.LEAD_ID, "type": "User"}
        if path.endswith("/git/ref/heads/main"):
            return 200, {"object": {"sha": "f" * 40}}
        if "/contents/" in path:
            rel = path.split("/contents/")[1].split("?")[0]
            return 200, {"encoding": "base64", "content": base64.b64encode((ROOT / rel).read_bytes()).decode()}
        if "/git/ref/heads/research-claims/" in path:
            return 200, {"ref": claim_ref(aid)}                                          # the 0001 ref exists
        raise AssertionError(path)
    with pytest.raises(K.ReconciliationRequired, match="EXISTING_OR_UNVERIFIABLE_REMOTE_CLAIM"):
        K.GitHubClaim(transport=transport, authorization_id=aid).acquire(
            governance_root=ROOT, governance_sha="f" * 40, request_sha="a" * 64)
