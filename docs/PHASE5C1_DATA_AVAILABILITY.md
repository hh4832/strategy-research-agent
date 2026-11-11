> Update 2026-11-11: the user reports a recovered long Development snapshot. Real Agent file pins remain PENDING; see [integration status and Core cost blocker](PHASE5C1_INTEGRATION.md). Earlier gap assessment below is historical.

# Phase 5-C1 — Development data availability (TSM)

## Conclusion

**No longer qualified Development snapshot exists today.**

**What qualified data exists now.** The only snapshot the governed runner can read is `TSM_DEV_1M_20240529_20240628_18350b3adba8e28a7e5e`. It covers one month: 22 sessions, 13 trades. It cannot support an interpretable strategy result.

**What longer data exists.** A longer, quality-verified source does exist: the parent final-gate file. It has not been converted into a Development-only snapshot.

**The one blocking issue.** It sits in Core, not in the data:
- `market_research_core.tsm_development` (pinned `b16179a`) hard-codes `WINDOW = 2024-05-29..2024-06-28`.
- It also hard-codes the snapshot ID pattern `TSM_DEV_1M_20240529_20240628_*`.

So no other Development window can be produced or read through the governed path.

## Evidence

Metadata only: no OHLCV, Holdout rows or per-day coverage were read.

| Item | Value | Source |
|---|---|---|
| Parent snapshot | `20261101_205431_06359faf7ef4_IBKR_FINAL_GATE_FULL`, producer `06359faf…` | `governance/market_readiness.json`; Core `policy.PINNED_SNAPSHOT` |
| Parent file | `tsm_normalized.parquet`: 29,363,599 bytes, sha256 `572d1947…8c96e7`. Hash **independently computed and matched** during the 4-A conversion. | DEV snapshot `lineage.json` on Drive |
| Parent manifest | `final_gate_manifest.json` sha256 `d960602c…818828` | `lineage.json` |
| Quality (final gate) | TSM **GO**: 2,193,372 rows, 0 errors, 0 unresolved minutes, 0 excluded / review candidates | `final_gate_summary.csv` (Drive) |
| Trading-day gaps / RTH missing intervals / corporate-action candidates | **none**: all three QA files have a header only | `tsm_trading_day_gaps.csv`, `tsm_rth_missing_intervals.csv`, `tsm_corporate_action_candidates.csv` |
| Development boundary | [2016-10-19, 2025-01-01) ET | signed authorization records (`development_boundary`) |
| Holdout | `HOLDOUT-TSM-15M-v1` [2025-01-01, 2026-10-17) ET, `BLINDNESS_UNVERIFIED` | `governance/holdout_locks.jsonl` |
| Physical layout | Row group 1 spans **2021-12-14 → 2026-05-26** (1,048,576 rows), so it mixes Development and Holdout. Row group 0 covers the earlier period. | `lineage.json` `selected_row_groups` |
| PIT / session rules | `CONSERVATIVE_LABEL_AGNOSTIC` 1.0.0 (`7fa3cdca…`): START/END envelopes, RTH only, no closing auction, fills strictly later. Timestamp label, publication latency and closing auction remain UNKNOWN (RESEARCH_ONLY acceptance). | `market_readiness.json` facts |
| Corporate actions | No candidates flagged by the final gate. Prices are not dividend-adjusted (ex-dividend gaps stay in the data). TSM ADR had no split in 2016–2024. | final gate; stated, not re-audited |

**Longest verified, continuous period suitable for back-testing: 2016-10-19 → 2024-12-31 (≈ 8.2 years).** It lies entirely inside the Development boundary.

This is a statement about the parent file's quality verdict. It has **not** yet been produced as a Development-only snapshot.

## Why it is not usable today, and the minimal fix

1. **Core contract v1.1 is window-locked.** `WINDOW`, `DevelopmentSnapshotPin` (start / end defaults and the ID regex) and `produce_development_snapshot` all hard-code 2024-05-29..06-28.
2. **The source row group mixes Holdout.** As in Phase 4-A, any conversion must hash the whole source and decode row group 1, which contains Holdout rows. That needs a **conversion plan signed personally by the research lead**, with conversion-only Holdout decode and 0 Holdout rows in the output. The precedent is Core PR #4 (plan `bbb3cb2e…`, approved 2026-11-06).

**Minimal solution:** one Core change and one Colab conversion. No Data Lake re-validation.

- **Core (Codex, `market-research-core`), development contract v1.2.** Add exactly one more pre-registered window, `2016-10-19..2024-12-31`, as an allow-listed constant next to the v1.1 window. Everything else is unchanged:
  - the same lineage, plan sha, receipt renewal and preflight;
  - the same `output_holdout_rows == 0` guarantee.

  The snapshot ID becomes `TSM_DEV_1M_20161019_20241231_<digest>`. No free-form windows are allowed, so no one can pick a window after seeing results.
- **Research lead (Colab, Core's preparation notebook).** Review the new plan sha, type it personally to approve the conversion-only Holdout decode, and produce the snapshot on Drive. The plan sha is the same kind of signed artefact as the 4-A plan.
- **Agent, after that.** Pin the new Core sha, snapshot, manifest and lineage, using the existing `snapshot_pin` / receipt mechanism. Spec 0.2.0 then gets its data pins only, nothing else.

The agent has no Core write access and does not modify Core.

## Reuse of the B3-C flow

The governed path can be reused as-is: A2 proposal → GitHub approvals → binding → REAL_GATED A01–A10 → reservation → existing ResearchOnlyRunner → fresh-process evaluation → settlement.

Several values are hard-wired to the B3-C candidate and must become parameters when the new snapshot exists. This is a small follow-up, deliberately not done now because nothing could test it without the real pins:
- `readiness/candidate.py` and `readiness/b3c.py` (DEV snapshot, spec 0.1.1, authorization ID);
- `execution/b3c_run.py` (`AUTHORIZATION_ID`, `COMPLETION_PATH`);
- the A10 readiness manifest.
