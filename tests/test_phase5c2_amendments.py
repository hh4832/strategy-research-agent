"""Phase 5-C2 PR-D: post-data amendment governance (budget N = 2, Haw D4 2026-11-25)."""
import copy
import json
from pathlib import Path

import pytest

from strategy_agent.governance import amendments as A
from strategy_agent.governance import research_authorization as RA

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 64


def init(case="TA-02", aid="RA-X-0001"):
    return {"kind": "CASE_INITIAL", "case_id": case, "index": 0, "authorization_id": aid}


def amend(i, of, aid, **kw):
    a = {"kind": "POST_DATA_AMENDMENT", "case_id": "TA-02", "index": i, "amendment_of": of,
         "trigger_result_sha256": SHA, "change": "quantity 100 -> 8", "stop_condition": "close if any exit unfillable",
         "evidence_level": "EXPLORATORY_ONLY", "authorization_id": aid}
    a.update(kw)
    return a


def test_budget_is_two_and_the_third_is_refused():
    assert A.MAX_POST_DATA_AMENDMENTS == 2
    series = [init(), amend(1, "RA-X-0001", "RA-X-0002"), amend(2, "RA-X-0002", "RA-X-0003")]
    A.check_series(series)
    with pytest.raises(A.AmendmentRefused, match="budget exhausted"):
        A.check_series(series + [amend(3, "RA-X-0003", "RA-X-0004")])


def test_framework_fix_does_not_consume_budget():
    fix = {"kind": "FRAMEWORK_FIX", "case_id": "TA-02", "index": 1, "amendment_of": "RA-X-0002",
           "trigger_result_sha256": SHA, "change": "Core 76902a1 known-volume basis", "authorization_id": "RA-X-0003"}
    A.check_series([init(), amend(1, "RA-X-0001", "RA-X-0002"), fix, amend(2, "RA-X-0003", "RA-X-0004")])


@pytest.mark.parametrize("bad, reason", [
    ({"trigger_result_sha256": "x"}, "trigger_result_sha256"),
    ({"change": " "}, "change"),
    ({"stop_condition": ""}, "stop_condition"),
    ({"evidence_level": "CONFIRMATORY"}, "EXPLORATORY_ONLY"),
    ({"amendment_of": "RA-OTHER"}, "amendment_of"),
    ({"index": 2}, "index must be 1"),
])
def test_amendment_must_declare_trigger_change_and_stop_condition(bad, reason):
    assert any(reason in p for p in A.problems(amend(1, "RA-X-0001", "RA-X-0002", **bad), [init()]))


def test_initial_only_once_and_amendment_needs_a_case():
    assert any("already has" in p for p in A.problems(init(aid="RA-X-0009"), [init()]))
    assert any("without a prior" in p for p in A.problems(amend(1, "RA-X-0001", "RA-X-0002"), []))


def test_ta01_history_under_the_new_rule_disclosure_only():
    # Q8 (quantity), Q8X (exit persistence), Q8XK (candidate rule) are post-data amendments; Core 76902a1 is a fix.
    s = [init("P5C-TA-01", "TA01-0002"),
         amend(1, "TA01-0002", "Q8-0001", case_id="P5C-TA-01"),
         amend(2, "Q8-0001", "Q8X-0001", case_id="P5C-TA-01"),
         {"kind": "FRAMEWORK_FIX", "case_id": "P5C-TA-01", "index": 2, "amendment_of": "Q8X-0001",
          "trigger_result_sha256": SHA, "change": "Core 76902a1", "authorization_id": "Q8X-0002"}]
    A.check_series(s)
    with pytest.raises(A.AmendmentRefused, match="budget exhausted"):
        A.check_series(s + [amend(3, "Q8X-0002", "Q8XK-0001", case_id="P5C-TA-01")])


def _events():
    return [{k: v for k, v in json.loads(x).items()}
            for x in (ROOT / "governance/research_authorizations.jsonl").read_text().splitlines() if x.strip()]


def test_existing_authorization_book_still_valid_and_amendment_block_is_enforced():
    approvers = {"hh4832"}
    events = _events()
    assert RA.validate_events(events, approvers)               # legacy events (no amendment block) unchanged
    base = copy.deepcopy(next(e for e in events if e["action"] == "APPROVE"))
    def ev(aid, amendment):
        e = copy.deepcopy(base); e["authorization_id"] = aid; e["amendment"] = amendment
        return {k: v for k, v in e.items() if k not in ("seq", "prev_sha256", "record_sha256")}
    a0 = {k: v for k, v in init(aid="X").items() if k != "authorization_id"}
    strip = lambda d: {k: v for k, v in d.items() if k != "authorization_id"}
    ok = [ev("RA-C2TEST-TA02-0001", a0), ev("RA-C2TEST-TA02-0002", strip(amend(1, "RA-C2TEST-TA02-0001", "_"))),
          ev("RA-C2TEST-TA02-0003", strip(amend(2, "RA-C2TEST-TA02-0002", "_")))]
    RA.validate_events(ok, approvers)
    with pytest.raises(RA.AuthorizationBookInvalid, match="schema|budget exhausted"):
        RA.validate_events(ok + [ev("RA-C2TEST-TA02-0004", strip(amend(3, "RA-C2TEST-TA02-0003", "_")))], approvers)
