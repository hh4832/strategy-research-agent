# P5C-TA-01 — First AI-Proposed Research Case: Review Packet

> **Snapshot of 2026-11-20 (committed 2026-11-24, content unchanged).** Written before Q8X-0002, Q8XK-0001, the Core fix (`76902a1`) and the case closure. Where it differs, `TA01_CASE_CLOSURE.md` and `ta01_case_disposition.json` take precedence: the case is CLOSED (`FILL_FEASIBLE_PERFORMANCE_NOT_TESTED`), `CORE_FULL_MINUTE_CAP_LOOKAHEAD` is CLOSED, and gaps W-1, W-4 and W-5 are addressed.

- Status of this document: **REVIEW PACKET, not an acceptance.** It grants no GO, no COST_APPROVAL and no
  authorization.
- Baseline:
  - Agent `hh4832/strategy-research-agent` `main` = `231d3ed2f6e41efdf0b288a71932ddbdb6e4b872` (checked 2026-11-20;
    the push CI run is green, 25 success / 1 skipped).
  - Core `hh4832/market-research-core` pinned `50ea3bfa42df7708dd3a1b21f17798c206be054e`, never modified.
- Evidence labels:
  - `IN_REPO` — a file at the baseline commit.
  - `GITHUB` — a PR, comment or CI record.
  - `DRIVE` — a write-once output on the lead's Drive, outside the repo.
  - `EVIDENCE_MISSING` — not durable or not locatable.

---

## 1. Original hypothesis

| Item | Value | Evidence |
|---|---|---|
| Proposer | Claude, on Haw's request "提出一個新的技術分析假設（提案階段）", 2026-11-17 | Chat request; **no repo record of the request** → `EVIDENCE_MISSING` |
| Proposal document | `P5C_TA01_PROPOSAL.md` (3 candidates TA-01/02/03, TA-01 recommended), sha256 `da8fe5c1…f932` | **Session workspace only** (`/home/claude/p5c_demo/ta01/`), never committed → `EVIDENCE_MISSING` (not durable) |
| Static validation | `strategy_agent.spec.validate` @ `8a5dc47`: 0 issues; structure `0c7bde1a…246c` | Session workspace `static_validation.json` → `EVIDENCE_MISSING` (not durable); revalidated in repo tests for Q8/Q8X only (`tests/test_phase5c_ta01_q8.py`) |
| Feasibility review | `P5C_TA_FEASIBILITY_20261118.md`: TA-01 **BLOCKED** (needs a signal function and a null baseline) | Session workspace → `EVIDENCE_MISSING` (not durable) |
| A2 research proposal (the C1 format: `RESEARCH_PROPOSAL_SCHEMA`, `proposal.agent.draft_proposal`) | **Never produced for TA-01** | Compare `docs/phase5c1/research_proposal.json` (SMA C1 has one) → `NOT_IMPLEMENTED` for this case |
| Hypothesis statement | A completed 15m RTH bucket that **all** of: closes above the prior-20-bucket high; closes in the top quarter of its range; trades ≥ 2× the prior-20 average volume. Such breaks continue over the next hours. Includes a mechanism and a falsifiable prediction | `IN_REPO` `tests/fixtures/p5c_ta01/ta01_spec_proposal.json` → `hypothesis` |
| Original spec identity | `p5c_ta01_res20_strongclose_relvol_tsm_15m` (status DRAFT, PROPOSAL_ONLY fixture) | `IN_REPO` same file. The file was amended **0.0.1 → 0.0.2** by Haw's execution decisions (§2); the 0.0.1 draft `p5c_ta01_spec_draft.json` (sha `235288ee…6d58`) exists only in the session workspace |

**Observation.** `provenance.created_at` / `created_by` in the fixture read `2026-11-03` / `claude-phase3-prep`. These
were inherited from the template the draft was built from and do not describe this proposal. Record as a data-quality
note; do not edit the frozen file.

## 2. Research lineage

| Node | strategy_id | Spec SHA | Parent | Change | Reason | Post-diagnostic? | Classification | Evidence |
|---|---|---|---|---|---|---|---|---|
| TA-01 v0.0.1 | `p5c_ta01_res20_strongclose_relvol_tsm_15m` | file `235288ee…` (draft) | — | AI proposal | — | No | PROPOSAL_ONLY | workspace only → `EVIDENCE_MISSING` |
| TA-01 v0.0.2 | same id | file `f5292ef4…a53c` (canonical not pinned) | v0.0.1 | Intraday-only; 15:15 time exit; 15m signal + 1m execution; 2-minute window on `bar_start`; known-volume cap; delayed-open exclusion | Haw's engineering decisions 2026-11-18. These are execution semantics, not signal rules; the rule structure hash is unchanged | **No** (decided before any data diagnostic) | PROPOSAL_ONLY; **BLOCKED** after diagnostic 0002 | `IN_REPO` fixture notes; PR #31 |
| Q8 | `p5c_ta01_q8_res20_strongclose_relvol_tsm_15m` | canonical `d35076f7…8110` | TA-01 v0.0.2 | `units` / `max_units` 100 → 8 only | 0002: 163/278 exits not fillable at 100 units | **Yes** | POST_DIAGNOSTIC_VARIANT, EXPLORATORY_ONLY | `IN_REPO` `ta01_q8_spec_variant.json`, `signals/ta01.py` `REVIEWED_SIZING`; PR #37 |
| Q8X | `p5c_ta01_q8x_res20_strongclose_relvol_tsm_15m` | canonical `ff6ecc04…40a1` | Q8 | Exit fill policy `PERSIST_TO_SESSION_END` (Agent-reviewed, bound to id + spec SHA); spec fields equal to Q8 | Q8 diagnostic: 7/326 exits not fillable | **Yes** (after two diagnostics) | POST_DIAGNOSTIC_VARIANT, EXPLORATORY_ONLY | `IN_REPO` `ta01_q8x_spec_variant.json`; PR #38 |

All three share rule structure hash `0c7bde1a…246c` (tested). **Q8 and Q8X are execution variants chosen after seeing
data diagnostics. They are not independent hypotheses and not blind or confirmatory checks.**

## 3. Engineering evidence (`GITHUB`, `IN_REPO`)

| PR | Merge | Scope | Tests |
|---|---|---|---|
| #31 | `121480e` | Signal, binding, 15m→1m execution, fill window/selection, known-volume rule, delayed open, no-overnight, random-entry null | `test_phase5c_ta01_signal.py` (28), `test_phase5c_random_entry.py` (10), `test_phase5c_ta01_core_batch.py` (Core) |
| #33 | `87acb03` | Runner passes the Core 1m frame for TA-01; archive fields | core_batch runner tests |
| #34 | `17c1d27` | Fill diagnostic tool + 0001 authorization draft + notebook | `test_phase5c_ta01_fill_diagnostic.py` |
| #35 | `e8f572e` | Pre-data precheck, attempt protocol v2, 0001 retirement, 0002 draft | same |
| #36 | `f04a491` | Cost schedule from the Core root; end-to-end test on the synthetic archive | `test_phase5c1_integration.py` (Core job) |
| #37 | `2368fb0` | Q8 + Q8 draft | `test_phase5c_ta01_q8.py` |
| #38 | `231d3ed` | Q8X + Q8X draft | same + core_batch |
| #39 | `e8fb81a` | C1 governance tests at a fixed clock (wall-clock expiry) | `test_c1_execution_runner.py`, `test_phase5c1_recovery_readiness.py` |

**Engineering limits (recorded, not resolved):**
- Core's full-minute participation check is a look-ahead (§5, I-1).
- `core_phase4a.execute_batch` dispatches on `family` (SMA/TA01) and hard-codes symbol `"TSM"`.
- The fill diagnostic is TA-01-specific.
- The governed runner has no TA-01 run config.

## 4. Diagnostic evidence (fill feasibility — **not performance**)

Shared conditions (from `docs/phase5c/*authorization*.json`, `IN_REPO`):
- Development window 2017-01-01..2025-01-01, warm-up from 2016-10-19; Holdout access NONE.
- Fixed quantity; the 1% cap from the pinned cost schedule.
- Core `50ea3bf`, ExecutionEngine **not** run; one run, Batch 0.
- Approval comment by hh4832, verified live (PRODUCTION) before data.

| Authorization | Spec | Approval (`GITHUB`) | Result (`DRIVE`, write-once) | Entry not fillable | Exit not fillable | Exit causes |
|---|---|---|---|---|---|---|
| `RA-IBKR_TSM-TA01_FILLDIAG-0001` | TA-01, 100 | pull/34#issuecomment-6978313388 | **None**: PRE_DATA_INPUT_PATH_FAILURE (empty dir left as is) | — | — | `IN_REPO` `ta01_fill_diagnostic_0001_disposition.json` |
| `…TA01_FILLDIAG-0002` | TA-01, 100 | pull/35#issuecomment-6994446298 | `ta01_fill_diagnostic.json` sha `683737a4…d30b`, MANIFEST SUCCEEDED | 48 / 326 | **163 / 278 (58.6%)** | known-volume 128; Core full-minute 35; window 0 |
| `…TA01Q8_FILLDIAG-0001` | Q8, 8 | pull/37#issuecomment-7001566877 | sha `7e641f46…6d78`, SUCCEEDED | 0 / 326 | 7 / 326 (2.1%) | known-volume 4; Core full-minute 3; window 0 |
| `…TA01Q8X_FILLDIAG-0001` | Q8X, 8 | pull/38#issuecomment-7010269865 | sha `34591210…09cb`, SUCCEEDED | 0 / 326 | **3 / 326 (0.9%)** | known-volume blocks 4, all filled after waiting 3 min; Core full-minute 3; no fill to session end 0 |

Definitions (consistent across runs, `IN_REPO` `research_only/ta01_fill_diagnostic.py`):
- **Entry intents:** all `TA01_ENTRY` decisions in the window (326 every time; the signal rules are identical).
- **Exit intents:** exist **only after a fillable entry** in the same session. That is why the 100-unit denominator
  is 278 (= 326 − 48), while Q8/Q8X use 326. Compare the ratios with this in mind.
- **"Not fillable":**
  - the known-volume rule failed (or, for Q8X exits, no fill until the session end); or
  - no tradable bar in (t, t+2]; or
  - Core's full-minute check would reject the chosen bar.

  For Q8X, a Core rejection ends the exit; persistence is not tried after it.
- Every result file has `not_output` = prices, PnL, returns, win rate, PF, drawdown and validity, guarded by
  `assert_no_performance_fields`.

**Common findings.**
- The 2-minute window was never binding: 0 no-tradable cases and 0 missing minutes; 1 zero-volume minute.
- One delayed-open session (2020-03-16).
- The remaining 3 Q8X failures (2017-03-16, 2017-10-26, 2018-01-18; 12:00 ET exits) are **only** Core's full-minute
  look-ahead.

## 5. Known blockers

| ID | Blocker | Track | Status | Evidence |
|---|---|---|---|---|
| I-1 | Core full-minute participation check: `execution.py:413`, `qty > fill-bar volume × cap`, with that volume unknown at the open. It conflicts with the Agent's known-volume rule | Infrastructure | **BLOCKER**; any change needs a separate technical spec and authorization (not done) | Core source; `OPEN_BLOCKERS` in `ta01_fill_diagnostic.py`; Q8X: 3 exits |
| G-1 | Market Gate `IBKR_TSM` = **NO_GO** (gate 2026-11-05.4) | Research governance | Open | `IN_REPO` `governance/market_readiness.json` |
| G-2 | Cost: the schedule `TSM-OFFICIAL-HISTORY-2017-2024` (sha `43b60457…`) is RESEARCH_ONLY; formal `COST_APPROVAL` FAIL; base profile DRAFT; cap 0.01 ASSUMED/PENDING; **per-order minimum commission at 8 units not reviewed** | Research governance | Open | `IN_REPO` PR #31 body, `docs/PHASE5C_TA01_ENGINEERING.md` |
| G-3 | No TA-01 / Q8 / Q8X **research** authorization (only fill-diagnostic authorizations, all used) | Research governance | Open | `IN_REPO` `governance/research_authorizations.jsonl` (SMA only) |
| G-4 | No pre-registered evaluation plan for TA-01: random-entry null parameters (seed, draws, quantile) undecided; no A2 proposal | Research governance | Open | §1; `evaluation/random_entry.py` requires an explicit config |
| D-1 | Volume unit inferred as shares (median daily RTH 4.57–8.15 M) but not checked against a public consolidated volume | Data quality | UNVERIFIED | `DRIVE` 0002 result `data` section |
| D-2 | Source timestamp label convention UNKNOWN; opening-auction placement at normal opens is unverifiable from OHLCV | Data quality | UNVERIFIED (mitigated by Core's opening-bucket exclusion and the delayed-open policy) | `docs/PHASE5C_TA01_ENGINEERING.md` |
| D-3 | Intraday U-shaped volume biases the 2× relative-volume rule toward the morning | Research design | Documented; report by time of day | same |
| E-1 | **No performance evidence of any kind** | — | Expected: research execution never authorized | — |

## 6. Research integrity

- **Development data:** TSM Development 2016-10-19..2025-01-01 has been used before (SMA research and these
  diagnostics). Nothing from it may be called blind, confirmatory or out-of-sample.
- **Holdout:** never accessed. Every diagnostic records `holdout_accessed: false`, and the loader refuses any Holdout
  overlap.
- **Post-diagnostic variants:** Q8 and Q8X are POST_DIAGNOSTIC_VARIANT; current evidence supports
  **EXPLORATORY_ONLY** at most.
- **No performance conclusion** exists or is implied.
- **CI and merges:** CI PASS, merged PRs, PREPARE output and fill-diagnostic results are **not** research passes or
  authorizations.

## 7. Current disposition (as evidenced)

`P5C-TA-01`:
- **Proposal stage:** passed static validation, but its proposal record is not durable in the repo.
- **Engineering:** complete for the signal, execution and diagnostic paths.
- **Execution feasibility:** **diagnosed three times**. Original TA-01 (100 units): **BLOCKED** by liquidity under the
  1% cap. Exploratory Q8X: blocked only by infrastructure blocker I-1.
- **Research execution:** **NOT AUTHORIZED, NOT EXECUTED**. Blocked by G-1..G-4, with I-1 open.

Conditions that would have to change before any research execution (none is decided here):
- I-1 resolved by a reviewed Core change, followed by a new authorized fill re-diagnosis;
- the G-2 cost review, including the minimum commission;
- a decision on G-1 (the Market Gate stays NO_GO unless the governance process changes it);
- a pre-registered evaluation plan (G-4);
- a new research authorization (G-3).

Alternatively, the lead may **terminate** the case with the present evidence (Path B in
`FIRST_AI_RESEARCH_WORKFLOW_ACCEPTANCE.md`). No disposition record exists yet → `EVIDENCE_MISSING`.
