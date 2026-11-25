# Post-data amendment governance (Phase 5-C2, C2-3 / W-6)

**Decision (Haw, 2026-11-25, D4):** each research case may have at most **2 post-data amendments**. Fixes to Core or
the framework do not count against that budget.

## Definitions

- **Post-data amendment:** any change to a case's strategy, execution rule, quantity or cost rule that is made after
  results from that case's data have been seen. A diagnostic result counts as such a result.
- **Framework fix:** a correction to Core or to the Agent framework that removes a defect, such as Core `76902a1`'s
  removal of the volume-cap look-ahead. It is not a strategy choice.
- **Case-initial:** the case's first authorization, planned entirely before any of its data was read.

## Rules

1. Every new authorization declares an `amendment` block: kind, case, index, and for amendments `amendment_of`,
   `trigger_result_sha256`, `change`, `stop_condition` and `evidence_level`. Authorizations issued before Phase 5-C2
   have no block and are treated as legacy.
2. A **post-data amendment** must name:
   - the earlier authorization of the same case it amends;
   - the SHA-256 of the result that prompted it;
   - what it changes, and only that;
   - a stop condition written before the run, for example "if any exit is still unfillable, close the case".

   Its evidence level is `EXPLORATORY_ONLY`.
3. Indexes are consecutive: `1`, then `2`. **A third post-data amendment is refused.** At that point the case is
   closed, or a new case starts with its own proposal, review and authorization.
4. A **framework fix** keeps the current index. It must still name its trigger and the change, and it is recorded in
   the evidence index.
5. Enforcement:
   - `schemas/RESEARCH_AUTHORIZATION_SCHEMA.json` holds the optional `amendment` block, with index 0–2;
   - `strategy_agent.governance.amendments` holds the budget, ordering and required fields;
   - `research_authorization.validate_events` refuses an authorization book that breaks them.

   Diagnostic authorizations use the same functions; they are wired in through the case-parameterized path (C2-0).

## TA-01 under this rule (disclosure only, not retroactive)

| Run | Kind under this rule |
|---|---|
| FILLDIAG-0002 (100 units) | Case-initial (0001 read no data) |
| Q8-0001 (quantity → 8) | Post-data amendment 1 |
| Q8X-0001 (exits persist) | Post-data amendment 2 |
| Q8X-0002 (Core 76902a1) | Framework fix (no budget) |
| Q8XK-0001 (per-candidate known volume) | **Would be refused:** third amendment |

TA-01 is closed and its records are not changed. The test `test_ta01_history_under_the_new_rule_disclosure_only`
reproduces this table.
