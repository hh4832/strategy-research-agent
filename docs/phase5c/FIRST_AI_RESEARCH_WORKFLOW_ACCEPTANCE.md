# First AI-Proposed Research Workflow — Lifecycle Matrix and Acceptance Design

> **Snapshot of 2026-11-20 (committed 2026-11-24, content unchanged).** Written before Q8X-0002, Q8XK-0001, the Core fix (`76902a1`) and the case closure. Where it differs, `TA01_CASE_CLOSURE.md` and `ta01_case_disposition.json` take precedence: the case is CLOSED (`FILL_FEASIBLE_PERFORMANCE_NOT_TESTED`), `CORE_FULL_MINUTE_CAP_LOOKAHEAD` is CLOSED, and gaps W-1, W-4 and W-5 are addressed.

- Status: **DESIGN + INVENTORY, not an acceptance.** Nothing here grants GO, COST_APPROVAL, authorization or Phase 5-C
  acceptance.
- Baseline: Agent `main` `231d3ed`; Core pinned `50ea3bf`. The case evidence is in `TA01_FIRST_RESEARCH_CASE_REVIEW.md`.

The goal is to show that an AI-proposed hypothesis can move through a reproducible, auditable, human-controlled
research lifecycle. **Profitability is not an acceptance criterion.** A research case that is lawfully stopped, with
evidence, is a successful system outcome.

## 1. Lifecycle matrix (TA-01; engineering and research status kept apart)

Status vocabulary:
- `IMPLEMENTED` — code exists.
- `TESTED` — synthetic or CI tests exist.
- `EXECUTED` — ran on real inputs for this case, with a record.
- `BLOCKED`
- `NOT_IMPLEMENTED`
- `NOT_EVALUATED`

| Stage | Required input | Component | Expected output | Engineering status | TA-01 research status | Evidence | Blocker |
|---|---|---|---|---|---|---|---|
| AI Proposal | Lead request; prior research list | `proposal/agent.py` (`draft_proposal`, A2 schema) | A2 `research_proposal.json` with origin and fingerprints | IMPLEMENTED, TESTED (used for SMA C1: `docs/phase5c1/research_proposal.json`) | **Proposal written, but not in A2 format and not in the repo** | Workspace `P5C_TA01_PROPOSAL.md` sha `da8fe5c1…` → `EVIDENCE_MISSING` | No A2 record |
| Proposal Validation | Draft spec | `spec/validate.py`, `family/canonical.py` | 0 issues; structure hash; duplicate check | IMPLEMENTED, TESTED | EXECUTED (static, no data), but **record not durable** | Workspace `static_validation.json` → `EVIDENCE_MISSING`; Q8/Q8X validation in `tests/test_phase5c_ta01_q8.py` | — |
| Research Specification | Validated draft + lead decisions | spec fixture + `signals/ta01.py` binding (`REVIEWED_SIZING`, `STRUCTURE_SHA256`) | Frozen spec with an identity and SHA; fail-closed binding | IMPLEMENTED, TESTED | TA-01 frozen as a fixture (DRAFT); Q8/Q8X pinned by canonical SHA | PRs #31, #37, #38 | The spec is a test fixture, not a registered spec |
| Market Gate | Market readiness report | `governance/market_readiness.json`; governance check G9 | GO / NO_GO | IMPLEMENTED | **BLOCKED (NO_GO)**; recorded, never read as a permission | `governance/market_readiness.json` | G-1 |
| Cost Gate | Approved cost profile or schedule | Core dated schedule + governance G4 | Cost approval or RESEARCH_ONLY use | IMPLEMENTED | **BLOCKED** for formal use (COST_APPROVAL FAIL); minimum commission at 8 units `NOT_EVALUATED` | PR #31 cost section | G-2 |
| Human Authorization | Authorization document; lead approval comment | `approval/verifier.py` (V01–V09), `governance/research_authorization.py`, diagnostic `run_attempt` | Live-verified approval bound to the document SHA | IMPLEMENTED, TESTED | **EXECUTED for 4 fill-diagnostic authorizations**; research authorization **none** | Comments on PRs #34/#35/#37/#38; `docs/phase5c/*authorization*.json` | G-3 |
| Engineering Preparation | Spec + Core semantics | `research_only/core_phase4a.py`, `runner.py`, `archive_contract.py` | Executable path with tests | IMPLEMENTED, TESTED | Done for signal, execution and diagnostic; **no TA-01 runner config** | PRs #31–#38; CI on `231d3ed` | I-1 |
| Execution Feasibility | Authorized data access | `research_only/ta01_fill_diagnostic.py` + notebook | Write-once counts, no performance | IMPLEMENTED, TESTED | **EXECUTED ×3** (0002, Q8, Q8X); 0001 pre-data failure recorded | Drive MANIFEST SHAs (review §4); `ta01_fill_diagnostic_0001_disposition.json` | I-1 remains |
| Research Execution | Research authorization; runner config | `research_only/runner.py` (governed; PREPARE / PREFLIGHT / CLAIM / EXECUTE) | Run archive | IMPLEMENTED, TESTED (SMA C1 EXECUTED: run `20261117_000458_a4cc87e`) | **NOT EXECUTED — BLOCKED** | `registry/research_authorization_uses.jsonl` (SMA only) | G-1..G-4, I-1 |
| Statistical Evaluation | Run archive; pre-registered plan | `evaluation/research.py`, `evaluation/random_entry.py`, C3/C6 evaluators | Evaluation report vs. pre-registered null | IMPLEMENTED, TESTED (random entry: synthetic only) | `NOT_EVALUATED` (no run) | `tests/test_phase5c_random_entry.py` | G-4 |
| AI Interpretation | Evaluation report | No dedicated component; interpretation was given in chat | Written interpretation bound to evidence SHAs | **NOT_IMPLEMENTED** as a component | Diagnostic summaries were given in chat only → `EVIDENCE_MISSING` (not durable) | — | Gap W-3 |
| Human Review | Packet + interpretation | GitHub approval comments (authorizations); chat decisions | Recorded lead decision | Approvals IMPLEMENTED; review decisions **NOT_IMPLEMENTED** as records | Lead decisions exist in chat and partially in spec provenance or doc notes | Spec `provenance.notes`; PR bodies | Gap W-2 |
| Archive / Termination | All of the above | Diagnostic archives (write-once, Drive); `*_disposition.json` (0001 only) | Case-level disposition: EXECUTED / TERMINATED / BLOCKED, with an evidence index | Partial: per-run archives IMPLEMENTED; **case disposition NOT_IMPLEMENTED** | No TA-01 case disposition record | — | Gap W-1 |

## 2. Two legitimate end states

### Path A — Executed research

AI Proposal → Validation → Specification → Gates (Market and Cost satisfied, or explicitly RESEARCH_ONLY under the
governance rules) → Human Authorization → Engineering → Feasibility → Research Execution → Statistical Evaluation →
AI Interpretation → Human Review → Archive.

### Path B — Properly blocked or terminated research

AI Proposal → Validation → Specification → (Gate failure **or** execution infeasibility) → Evidence → AI
interpretation of the evidence → Human Review → Case disposition `TERMINATED` or `BLOCKED_PENDING_<item>` → Archive.

Path B produces **no performance metrics** and must not compute any.

## 3. Acceptance criteria

### 3.1 System process success (either path)

1. **Durable proposal.** The proposal is in the repo in a schema-valid format, with origin (AI), request reference
   and fingerprints.
2. **Static validation.** It is recorded with the validator version and the result.
3. **Frozen identity.** Every executed artifact references a frozen spec identity (strategy_id + SHA) and the lineage
   to the original proposal.
4. **Recorded gates.** Each gate decision is recorded with its source; no gate is lowered or reinterpreted to make
   progress.
5. **Authorized data access.** Every data access has a live-verified lead approval bound to a document SHA, is used
   once, and leaves a write-once outcome (success or failure manifest).
6. **Integrity labels.** Post-diagnostic changes are labelled as such, and their classification (EXPLORATORY_ONLY,
   etc.) carries forward.
7. **Human review.** A lead review decision is recorded in the repo.
8. **Case disposition.** A disposition record lists every evidence item by path or SHA, plus open blockers and
   whether reopening needs a new proposal.

### 3.2 Research may execute only if

- Market Gate is GO, or RESEARCH_ONLY explicitly permitted by governance for this market.
- The cost schedule is accepted for this use, including minimum-commission effects at the chosen size.
- A pre-registered evaluation plan exists (null model, metrics, decision rule).
- A live-verified research authorization exists for this spec SHA.
- No open infrastructure blocker affects the result.
- Data integrity status is stated (exploratory vs. confirmatory).

### 3.3 Lawful handling of failure or blocking

Record the failure as evidence and never "fix" it by weakening a gate. A failed or used authorization is never
re-run; a new attempt needs a new authorization.

### 3.4 Human review is mandatory before

- any authorization;
- any post-diagnostic variant;
- any Core change;
- any case disposition.

### 3.5 Minimum archive evidence (per case)

- the proposal;
- the validation record;
- the spec identities and lineage;
- gate states;
- authorizations and approval comment URLs;
- run or diagnostic manifests (SHA);
- interpretation;
- the review decision;
- the disposition.

### 3.6 Back to Proposal (new hypothesis) when

- a signal rule, the universe, a parameter, a horizon or the hypothesis changes (the structure hash changes); or
- the evidence falsifies the hypothesis's premise.

### 3.7 Infrastructure fix only when

- a defect in shared code or Core makes results wrong for **any** strategy (example: I-1); or
- data or interface plumbing is broken.

Fix it under its own spec and authorization, re-run the affected diagnostics, and keep the research case otherwise
unchanged.

### 3.8 Anti-goal (explicitly prohibited)

Do not iterate on gates, cost, caps or execution limits to make a case pass. Each relaxation needs:
- its own justification **independent of the case outcome**;
- lead approval;
- a POST_DIAGNOSTIC label when it is seen after data.

Recommended: cap the number of post-diagnostic execution variants per case (for example 2), and require a new
proposal after that.

## 4. Infrastructure vs. research tracks

| Track | Item | Depends on | Note |
|---|---|---|---|
| Infrastructure | **I-1** Core full-minute participation look-ahead (`execution.py:413`) | Core technical spec + review + authorization | Affects every 1m-execution strategy, not just TA-01. **Do not change Core because of TA-01's 3 exits.** |
| Infrastructure | **I-2** Agent/Core participation rule divergence (Agent: known volume; Core: full minute) | I-1 | Converge on one rule after I-1 |
| Infrastructure | **I-3** Hard-coded `"TSM"` and the family dispatch in `core_phase4a`; TA-01-specific diagnostic | — | See §5 |
| Infrastructure | **I-4** Wall-clock-dependent governance tests (fixed in PR #39 for C1 only) | — | Audit other tests for hidden expiry |
| Research | **R-1** TA-01 original hypothesis | G-1..G-4 | 100 units: liquidity-blocked |
| Research | **R-2** Q8/Q8X exploratory execution variants | I-1, G-2 (minimum commission) | EXPLORATORY_ONLY |
| Research | **R-3** Cost and liquidity feasibility at the intended size | G-2 | Includes the economic meaning of 8 units |
| Research | **R-4** Null model and statistical plan (random-entry config) | — | Must be pre-registered before any run |
| Governance | **G-1** Market Gate, **G-3** research authorization, **G-4** pre-registration | — | Lead decisions |

## 5. Reusability for a second AI proposal

| Area | Status |
|---|---|
| **Generic** (reusable as is) | Spec schema and validator; structure hash; approval verifier (V01–V09); authorization book and use ledger; Holdout locks; write-once archives; the attempt protocol (`run_attempt`, `precheck_inputs`); temporal-safe Core frames (15m/1m); `evaluation/research.py`; random-entry framework (`evaluation/random_entry.py`, given a decision function) |
| **Coupled to TA-01 / SMA** | `core_phase4a.execute_batch` (an `if family == "TA01"` / SMA branch, symbol `"TSM"`, 15m/1m constants); `signal_family` dispatch; `signals/ta01.py` (`REVIEWED_SIZING`, the delayed-open policy lives there); `ta01_fill_diagnostic.py` (named and wired for TA-01); the notebook defaults; C1 modules (`execution/c1_*`) are SMA/C1-specific |
| **Not reusable without manual work** | Authorization documents (hand-built JSON per run); the proposal → spec → fixture path (TA-01 skipped A2); lead decisions recorded only in chat or provenance notes; no case ID or disposition record |
| **Standardization needed** | Yes: a Research Case ID, a lineage record, a review record and a disposition record (see below) |

**Minimal improvements (no large refactor).**
1. **`research_cases/<CASE_ID>/case.json`** — an append-only, schema-validated record with:
   - case id, proposal ref (A2), spec identities and lineage, labels;
   - gate snapshots, authorizations, run or diagnostic manifests;
   - review decisions, disposition, open blockers.

   One small schema plus a validator test.
2. **Require the A2 proposal for every AI case.** Make the TA-01-style fixture path produce or attach an A2
   `research_proposal.json`.
3. **A signal-family registry.** Map `family → (decision function, sizing/exit policy, frame requirements)`, replacing
   the `if family == "TA01"` branches; take the symbol from the spec.
4. **A generic fill-feasibility diagnostic.** It takes the family's decision function; the TA-01 module becomes one
   registration.
5. **An authorization-document generator** from the case record, so documents are not hand-edited.

## 6. Workflow acceptance gaps

| ID | Gap |
|---|---|
| W-1 | No case-level disposition record (TA-01 or generic) |
| W-2 | Human review decisions are not recorded as durable artifacts |
| W-3 | No durable AI-interpretation artifact bound to evidence SHAs |
| W-4 | The original TA-01 proposal, static validation and feasibility review are not in the repo (session workspace only) |
| W-5 | No A2 proposal for TA-01 (the SMA C1 has one) |
| W-6 | No cap or rule on post-diagnostic variants per case |
| W-7 | Diagnostic results (`DRIVE`) are referenced by SHA in chat, not indexed in the repo |
