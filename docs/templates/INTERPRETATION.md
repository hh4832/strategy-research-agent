# Interpretation — <CASE_ID> — <RUN / AUTHORIZATION_ID>

<!-- Phase 5-C2 C2-4 (W-3). Written after a performance (or feasibility) result, BEFORE any further change to the case.
     Uses only the criteria fixed in the proposal; anything added afterwards is marked POST_HOC. Save as
     docs/cases/<case_id>/interpretation_<n>.md and add an evidence-index line (stage INTERPRETATION). -->

| Field | Value |
|---|---|
| Case / authorization | `<CASE_ID>` / `<RA-...>` |
| Result | `<location>`, sha256 `<64 hex>`; evaluator output sha256 `<64 hex>` |
| Evaluator verdict | `<PASS / FAIL + error codes>`; LOOK_AHEAD: `<none / ...>` |
| Data | `<dataset, window, partition>`; exposure at proposal time `<EXPOSED/UNEXPOSED>` (registry/dataset_exposure.jsonl) |
| Evidence level | `<EXPLORATORY_ONLY / CONFIRMATORY>` (ceiling from exposure and amendments) |
| Amendments used | `<0 / 1 / 2>` of 2 |

## 1. Pre-registered criteria (copied from the proposal, unchanged)
| Criterion (proposal section) | Threshold | Observed | Met? |
|---|---|---|---|
| `<e.g. mean net PnL per trade vs random-entry null at BASE>` | `<...>` | `<...>` | `<yes/no>` |

## 2. Robustness that the proposal required
- Costs: BASE `<...>`, STRESS `<...>`; conclusion changes under STRESS? `<yes/no>`
- Random-entry null (seed, draws, quantile as pre-registered): `<...>`
- Sample sufficiency (`evaluation/sufficiency.py`): `<sufficient / insufficient: reason>`
- Trade concentration / regime dependence (if pre-registered): `<...>`

## 3. Reading
`<Plain statement: does the result support, contradict, or fail to inform the hypothesis, and why — in terms of
the pre-registered criteria only.>`

## 4. What may NOT be claimed
- `<e.g. "not out-of-sample: Development window already exposed">`
- `<e.g. "8-unit result does not scale: minimum commission dominates">`
- `<...>`

## 5. POST_HOC observations (not evidence for the hypothesis)
- `<anything noticed after seeing the result; may motivate a NEW case, not an amendment of this one>`

## 6. Recommended disposition
`<CLOSE / CONTINUE with the next step and its authorization>` — decided by Haw in a REVIEW_RECORD (gate DISPOSITION).
