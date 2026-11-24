# Core volume-participation cap look-ahead — technical spec (SPEC ONLY)

| | |
|---|---|
| Status | **APPROVED by Haw 2026-11-24** (D1–D4 as recommended; D5: implement). Implementation: `market-research-core` branch `feat/participation-known-volume-basis` @ `ae372e0`, based on `50ea3bf`; not merged |
| Written | 2026-11-24 |
| Core reviewed | `hh4832/market-research-core` @ `50ea3bfa42df7708dd3a1b21f17798c206be054e` (pinned, unmodified) |
| Agent reviewed | `main` @ `1840ae1` |
| Blocker addressed | `CORE_FULL_MINUTE_CAP_LOOKAHEAD` (`research_only/ta01_fill_diagnostic.py` `OPEN_BLOCKERS`) |

**This document changes nothing.**
- No Core code, cost table, schedule, spec, signal, Registry, Governance, Ledger or authorization is changed.
- No data was read.
- Implementing it needs a separate Core task. Bumping the Agent's Core pin needs a separate decision by Haw.

---

## 1. The defect

`src/market_research_core/execution.py`, fill loop, line 413:

```python
elif fill_profile.volume_participation_cap is not None and qty > float(bar["volume"]) * fill_profile.volume_participation_cap:
    reason = "VOLUME_PARTICIPATION_EXCEEDED"
```

`bar` is the **candidate fill bar itself**. A MARKET fill on `bar` is priced at `bar["open"]`. Under
`CONSERVATIVE_LABEL_AGNOSTIC`, that bar can open as early as `bar_start`. Its volume is only complete at
`bar["modeled_available_at"]` (label + 1 min).

The decision "may this order fill at this bar's open?" therefore uses information that does not exist until the bar
has finished. This is look-ahead.

**Why it matters (bias, not just strictness):**
- The check admits a fill exactly when the fill minute *turned out* to be busy, and rejects it when the minute *turned
  out* to be quiet.
- Realised minute volume is correlated with that minute's price move.
- So the fill/no-fill outcome is selected on future information. In a performance run this can bias PnL in either
  direction.
- Making the quantity smaller only makes the bias rarer; it does not remove it.

**Observed size (fill-feasibility diagnostics, no PnL):**

| Run | Exits rejected only by this check |
|---|---|
| Q8 (8 units) | 3/326 exits (another 4 failed the Agent's no-look-ahead known-volume rule) |
| Q8X (8 units, exits persist to session end) | 3/326 exits; these are **all** of the remaining unfillable exits |

No entry was affected at 8 units.

**Current mitigation in the Agent (stays as is):**
- `ta01_select_fills` already enforces a known-volume rule (`KNOWN_VOLUME_RULE`) before Core sees the order.
- The diagnostic counts Core's residual rejection separately (`core_full_minute_cap_would_reject`).
- Core's own check cannot be bypassed from the Agent without changing Core. This is why it stays a BLOCKER for any
  strategy execution, including a performance backtest.

---

## 2. Required behaviour

Core must decide participation from volume that is **known when the candidate bar can first open**.

### 2.1 New explicit setting

Add to `ExecutionConfig`:

```python
participation_volume_basis: Literal["FILL_BAR_REALIZED", "LAST_COMPLETED_AT_FILL_OPEN"] = "FILL_BAR_REALIZED"
```

- **`FILL_BAR_REALIZED`** — today's behaviour, unchanged byte-for-byte. Kept so that every result already produced
  at `50ea3bf` stays reproducible. It must be recorded as look-ahead in provenance (§2.4).
- **`LAST_COMPLETED_AT_FILL_OPEN`** — new behaviour, defined below. Allowed only with
  `available_at_policy == "CONSERVATIVE_LABEL_AGNOSTIC"`. Any other mode raises `ValueError` in `__post_init__`; no
  semantics are defined for those modes in this spec.

The setting lives on `ExecutionConfig`, not on the cost profile: it is a timing rule, not a cost.
`volume_participation_cap` stays the cost profile's number (0.01 in the TSM schedule), unchanged.

### 2.2 Reference bar

For a candidate row `c` of contract `k`, the reference bar `r` is the row of the **same frame, same symbol, market
and contract `k`, same session** (`session_close` equal, and the row's own `session` is `RTH`) with:

```
r.modeled_available_at == c.bar_start
```

This is the completed 1m bar whose data is first available at the earliest moment `c` can open.

Under Core's conservative frame, a row with label `L` has `bar_start = L − 1 min` and `modeled_available_at = L + 1 min`.
So for a candidate labelled `L`, `r` is the row labelled **`L − 2 min`**, not the row immediately before it:
- the row labelled `L − 1` may still be trading when the candidate can open (`L − 1`), so its volume is not yet known;
- the row labelled `L − 2` is the latest one guaranteed complete.

This is the same row the Agent's persist rule already uses (`known_at[row.bar_start]`).

- If no such row exists, the check fails with `KNOWN_VOLUME_UNAVAILABLE`. Causes: first bar of the session, previous
  minute missing, or a different session.
- **No fallback to an older bar.** A stale bar would quietly reintroduce an arbitrary rule.
- A reference row that exists but has `data_exists`/`backtest_eligible` false, or zero volume, gives known volume = its
  volume (0 if missing). The check then fails as below. Eligibility of the *reference* row is not otherwise required,
  because only its volume is used.

### 2.3 Check

Order of checks in the fill loop is unchanged. Only line 413 is replaced. With basis `LAST_COMPLETED_AT_FILL_OPEN`:

```
known = volume(r)                       # §2.2
if r is None:                     reason = "KNOWN_VOLUME_UNAVAILABLE"
elif qty > known * cap:           reason = "KNOWN_VOLUME_PARTICIPATION_EXCEEDED"
```

- **All-or-nothing, as today.** No partial fills; the order quantity is never adjusted.
- **Retry behaviour is unchanged.** A NEXT_BAR order is done after one rejection. A DAY order keeps waiting until the
  next candidate or expiry. These reasons are ordinary bar-level rejections, not exclusion-window rejections.
- The fill bar's own volume is still required to be > 0 (existing `ZERO_VOLUME_BAR`, line 411). This is not
  look-ahead for *feasibility*: a bar with zero volume had no trade at its open to fill against. It stays as is.
- `cap is None` means no participation check, as today.

### 2.4 Provenance

Under `LAST_COMPLETED_AT_FILL_OPEN` (with a cap set), every fill and every `KNOWN_VOLUME_*` rejection carries the
fields below. Under the legacy basis, fill and rejection rows are left exactly as before; only run-level provenance
changes:
- `participation_volume_basis`;
- `participation_reference_modeled_available_at`, `participation_reference_volume`;
- `participation_cap`.

Run-level provenance (`ExecutionResult.provenance`) records the basis.

When the basis is `FILL_BAR_REALIZED`, provenance also records:

```
participation_lookahead: "FILL_BAR_REALIZED_VOLUME_USED"
```

so a downstream evaluator can refuse it. The Agent evaluator should then treat `FILL_BAR_REALIZED` + cap as a
`LOOK_AHEAD` error for formal runs. That evaluator change is a separate Agent task.

The reference row's timestamps are taken from Core's own columns; nothing is re-timed.

---

## 3. Relationship to the Agent's existing rule

| Point | Agent today (`ta01_fill_examine`) | Core after this spec |
|---|---|---|
| Window stage | known volume = the bar available at the **order time** `t` | known volume = the bar available at the **candidate's open** |
| Persist stage (Q8X exits) | the bar available at the candidate's `bar_start` | same rule; identical |
| Missing reference | persist: treated as volume 0 → skip | `KNOWN_VOLUME_UNAVAILABLE` |

In the window stage the two rules can differ.
- The Agent's rule (Haw, 2026-11-18: 用下單前已知量) is stricter and stays in the Agent as a strategy rule.
- Core's rule is the generic no-look-ahead floor.

An order that passes the Agent rule can still fail Core's rule, because the minute before the chosen bar may be
thinner than the minute at `t`. That is a real, non-look-ahead rejection and should be reported as such.

**Expected effect on TA-01 (not verified; no data read):**
- `core_full_minute_cap_would_reject` becomes impossible by construction.
- Any rejection that remains is a known-volume rejection.
- The count must be measured, not assumed, in a separately authorized run.

---

## 4. Tests required in Core (synthetic data only)

1. **Legacy unchanged:** with the default basis, the existing tests `test_ibkr_tsm_gate.py::test_volume_participation_cap_rejects_full_quantity_without_partial_fill`
   and `test_m3a4_final.py::test_partial_exit_long_short_ledger_cost_attribution` pass byte-identically. Full
   existing suite green.
2. **The look-ahead case is fixed:** reference minute volume 100, fill minute volume 10 000, cap 0.01, qty 8.
   - Legacy: fill.
   - New basis: `KNOWN_VOLUME_PARTICIPATION_EXCEEDED`.
3. **Reverse case:** reference 10 000, fill minute 100, qty 8.
   - Legacy: `VOLUME_PARTICIPATION_EXCEEDED`.
   - New basis: fill at the fill bar's open.
4. **Boundary:** qty == known × cap fills; qty = known × cap + ε rejects (`>` as today).
5. **Missing reference:** first bar of session, a gap minute, and a reference row in another session each give
   `KNOWN_VOLUME_UNAVAILABLE`. No older bar is used.
6. **DAY persistence:** after a `KNOWN_VOLUME_*` rejection the DAY order fills on the next candidate whose reference
   passes, and expires at the session close otherwise. NEXT_BAR is done after one rejection.
7. **Invariance:** changing *only* the fill bar's volume (keeping it > 0) never changes the new-basis outcome. This is
   the defining no-look-ahead property; run it as a property test over random volumes.
8. **Mode guard:** the new basis with `OBSERVED`, `BAR_END_STRICT_LATER` or `UNKNOWN` raises `ValueError`.
9. **Provenance:** fields in §2.4 present on fills and rejections; `participation_lookahead` set only for the legacy
   basis.
10. **Cost schedule path:** the dated cost schedule's per-date profile cap is the one used, as today.

## 5. Acceptance criteria

- Diff limited to `execution.py` (setting, reference lookup, check, provenance) and new tests.
- Note on D4: under the current temporal policy (signals from 10:00 ET, same-session candidates only), a candidate
  whose reference would be a non-RTH row cannot be reached by an order. The code still rejects it
  (`KNOWN_VOLUME_UNAVAILABLE`), but no synthetic order can exercise it; it is covered by the missing-reference test.
- No change to `costs.py`, cost configs, temporal policy, data access or bar preparation.
- Existing Core tests pass unchanged.
- New tests 2–10 pass.
- Core CI green.
- A new Core commit SHA. `50ea3bf` stays as the reproducible reference for all earlier results.

## 6. Rollout (each step needs its own go-ahead)

1. **Core task:** implement §2 to §5 in `market-research-core`, on a branch with a PR; merge only on Haw's word.
2. **Agent pin bump:** a separate PR moves the pinned Core SHA. Agent runners for TA-01 set
   `participation_volume_basis="LAST_COMPLETED_AT_FILL_OPEN"`. The evaluator treats legacy + cap as `LOOK_AHEAD` for
   formal runs. Earlier diagnostics stay tied to `50ea3bf` and are not re-labelled.
3. **Measure:** any re-run of the fill diagnostic under the new Core needs a new authorization. TSM Development
   2016-10-19..2025-01-01 is already used; results remain EXPLORATORY_ONLY.
4. **Only then** can `CORE_FULL_MINUTE_CAP_LOOKAHEAD` move from BLOCKER to resolved. Clearing it does not authorize a
   performance backtest.

## 7. Decisions for Haw

Haw, 2026-11-24: D1–D4 as recommended; D5 go ahead with the Core implementation.

| # | Question | Recommendation (approved) |
|---|---|---|
| D1 | Reference point for Core: at the candidate's open (this spec), or at the order time? | Candidate's open. It is the generic rule; the stricter order-time rule already lives in the Agent |
| D2 | Missing reference minute: reject (`KNOWN_VOLUME_UNAVAILABLE`), or fall back to the latest older bar? | Reject; no fallback |
| D3 | Default basis: keep the legacy default for reproducibility, or make the setting required (no default)? | Keep the legacy default, flag it in provenance, and have the Agent refuse it for formal runs |
| D4 | First minute of a session (reference would be the prior session): reject? | Reject. TA-01 already excludes delayed opens and never fills on the first minute of a session |
| D5 | Start the Core implementation task after approving this spec? | Your call; separate task |

## 8. Evidence

- Core line references are at `50ea3bf`: the check (413), the zero-volume check (411), candidate set construction
  (264–292) and the fill provenance block (495–515).
- Agent references are at `1840ae1`:
  - `research_only/core_phase4a.py` (`KNOWN_VOLUME_RULE`, `ta01_fill_examine`);
  - `research_only/ta01_fill_diagnostic.py` (`OPEN_BLOCKERS`);
  - `docs/PHASE5C_TA01_FILL_DIAGNOSTIC.md`, `docs/PHASE5C_TA01_Q8_VARIANT.md`.
- Diagnostic counts are from the write-once Drive results:
  - Q8: `7e641f46…6d78`;
  - Q8X: `34591210…09cb`.
