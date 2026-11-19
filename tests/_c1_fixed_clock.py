"""Fixed clock for the Phase 5-C1 governance tests.

These tests exercise the real authorization book, whose RA02 check compares the wall clock with each authorization's
[issued_at, expires_at). `RA-IBKR_TSM-SMA520_P5C1-0001` expired at 2026-11-19T16:58:13+08:00, so from then on the
tests failed on every branch for a reason that has nothing to do with the code under test. The tests now run at a
fixed instant inside the windows of P5C1-0001 and P5C1-0002. Production code, the governance books and the
authorizations are unchanged; only the clock these two modules read during these tests is fixed.
"""
from datetime import datetime

import pytest

FIXED_NOW = datetime.fromisoformat("2026-11-17T12:00:00+08:00")


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return FIXED_NOW if tz is None else FIXED_NOW.astimezone(tz)


@pytest.fixture(autouse=True)
def c1_fixed_clock(monkeypatch):
    from strategy_agent.execution import c1_runner
    from strategy_agent.governance import research_authorization
    monkeypatch.setattr(research_authorization, "datetime", _FixedDatetime)
    monkeypatch.setattr(c1_runner, "datetime", _FixedDatetime)
    yield FIXED_NOW
