# Review record — <CASE_ID> — <GATE>

<!-- Phase 5-C2 C2-4 (W-2). One record per lifecycle gate: PROPOSAL_REVIEW, AUTHORIZATION, RESULT_REVIEW,
     INTERPRETATION_REVIEW, DISPOSITION, FRAMEWORK_GO. Save as docs/cases/<case_id>/review_<gate>_<n>.md,
     add an evidence-index line (stage REVIEW) pinning this file, and do not edit it after the decision. -->

| Field | Value |
|---|---|
| Case | `<CASE_ID>` |
| Gate | `<PROPOSAL_REVIEW \| AUTHORIZATION \| RESULT_REVIEW \| INTERPRETATION_REVIEW \| DISPOSITION \| FRAMEWORK_GO>` |
| Object reviewed | `<path or Drive location>`, sha256 `<64 hex>` |
| Reviewer (decides) | Haw (hh4832) |
| Advisory input (optional) | `<e.g. GPT review, date; advisory only, not an approval>` |
| Decision | `<ACCEPT \| REVISE \| REJECT>` |
| Decision record | `<PR comment URL or chat date/time Asia/Taipei>` |
| Date | `<YYYY-MM-DD>` |

## What was checked
- `<item 1, against which rule or document>`
- `<item 2>`

## Findings
| # | Finding | Severity (BLOCKER / MAJOR / MINOR) | Resolution |
|---|---|---|---|
| 1 | `<...>` | `<...>` | `<fixed in <sha> / accepted as is / open>` |

## Conditions attached to the decision
- `<e.g. "authorization must state stop condition X">` (or "none")

## Not decided here
- `<what this record does NOT approve, e.g. "does not authorize a performance run">`
