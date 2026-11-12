# Phase 5-C1 cost assumptions and pin review

## Cost schedule

- Core is pinned to `50ea3bfa42df7708dd3a1b21f17798c206be054e`.
- Official schedule: `configs/research/tsm_official_dated_costs_2017_2024.json`.
- Canonical schedule SHA256: `43b6045758760a40ad88d741b1a97a73cfb0b6cf6b2a4a7f1f3b31d5085a4216`.
- The 2017–2024 schedule carries the official historical SEC and FINRA TAF records. The retained source documents are checked by SHA at load time.
- Four immutable scenarios remain `BASE`, `LOW`, `STRESS`, and `CAT_STRESS`; commissions, minimums, caps, spread and scenario assumptions are checked against the Agent cost definition before readiness.
- Each scenario is run over one continuous bar stream and order list. Core selects a dated profile for each Fill. No rate boundary resets positions, cash, or performance.

## Research-only assumptions

- SEC historical rates and FINRA TAF rates are source-verified.
- SEC New York Fill-date mapping is a research convention, not a broker-actual charge-date assertion.
- TAF and CAT are conservatively modeled on both sides with no cap. CAT broker pass-through is not confirmed.
- A predeclared diagnostic compares the baseline SEC Fill-date mapping with a fixed next-calendar-date proxy. It reports fee differences only; the baseline remains in the execution and no mapping may be selected by backtest performance.
- These assumptions do not change the formal Market Gate. `NO_GO` remains in force and the cost model is not formal cost approval.

## One-time data-pin review

The following are PREPARE_ONLY observations. They are not `INDEPENDENTLY_REVIEWED` merely because they match prior observations.

| Pin | Observed SHA256 |
|---|---|
| Recovery marker | `8a980bd92045da59d573b249162ba5dce581a1c74802663b0b02cbb0756ee7a5` |
| Recovery provenance | `dddc8794c4bf52556c12d5c919e779d9bb3b7aba2770e73f34c838fe5ab8b6aa` |
| Snapshot binding | `9822ad8db3bdde056c0192cb056577bdc91485af92f45feb47d1246f5c6a8f1f` |
| Manifest | `edabc0abf38bab6cad013b2248b68344dc0073da105a6085dd5e8f05f85272e4` |
| Lineage | `baeac6bc2e794038646ee7b313edc27038090862ea0da73cc64a87e87a9adffa` |
| Parquet | `e1e7955401fcbe4a84e7486fa7b1cc8ae97a92513426086cf7902afcf958ee03` |

To review once, compare all six independently against the new PREPARE_ONLY archive and its manifest in a separate review context. If all match, enter the six values in the Notebook's `REVIEWED_DATA_PINS` and cite the review record in `PIN_REVIEW_EVIDENCE_REF`. The Notebook checks the human-entered values against the current observations; it never sets the reviewed field or evidence reference itself. A mismatch stops readiness.

The authorization draft remains unapproved. Formal authorization, the researcher's separate GitHub approvals, B3-C A01–A10, and the one-Batch ledger are still required before any real execution.
