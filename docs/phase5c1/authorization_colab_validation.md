# Authorization Colab validation evidence

Status: READY_FOR_HUMAN_AUTHORIZATION (tooling delivery, not an approved authorization).

Base: ec06ba63c7164d4c633a23ef304dade8270951d9. Development branch: feature/phase5c1-authorization-colab.

Environment: isolated CPython 3.11.17 and project-local dependency installation in a temporary venv; system/other project Python environments unchanged.

Command:

```bash
PYTHONPATH=src python -m pytest -q \
  tests/test_c1_authorization_colab.py \
  tests/test_phase4a_research_only.py \
  tests/test_phase5b1_approval_binding.py \
  tests/test_phase5b3c_preparation.py
```

Result: **125 passed**. The new module has 31 tests within that total (counts overlap; do not add them). `git diff --check` passes. All Notebook code cells parse/compile and stored outputs are empty.

Covered:

- Existing authorization book/schema compatibility; unchanged RA checks.
- New C1 unsigned proposal schema with real source bytes; paired Schedule fields, invalid SHA and partial-pair rejection.
- Actual runner preflight and final guard refuse dated pin mismatch before any reservation/strategy, while matching pairs pass that check.
- VALIDATE_ONLY metadata checks are read-only; wrong pins, windows, preflight, identity and settings fail closed.
- Original index substitution is rejected before metadata traversal.
- GitHub repo/user/PR contract rejects wrong identity, forks/branches, Apps and merged/non-main PRs.
- APPROVE cannot reach network/admin without explicit mode/confirmation.
- The actual independent UI cell never invokes its handler during Run All, even with APPROVE settings and an enabled button.
- The actual seven cells run in a clean namespace under simulated Colab/network provisioning/private-archive boundaries; VALIDATE_ONLY finishes NOT_APPROVED, archives hashes and excludes a sentinel token. No live Drive/approval operation is represented by this fixture.
- No test changes the actual governance or registry books; valid synthetic events are confined to existing isolated test fixtures.

Limits:

No real human approval, authenticated Colab interaction, private Drive acceptance, commit/push from a Notebook, strategy, Holdout read or Batch use was performed. Live PR/source synchronization is deliberately a future human step. External downloads/API access are required at runtime; API failure is fail-closed. The schema/code PR and dependent research integration must be reviewed before main can host a compatible independent authorization PR. The older research Notebook pin is not silently upgraded.
