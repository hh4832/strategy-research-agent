# TSM SMA 5/20 — Research-only smoke `20261110_141805_e3ce267`

> **RESEARCH_ONLY_EXPLORATORY.** One 4-week development window, one fixed parameter set. This is NOT evidence that SMA 5/20 works. No out-of-sample, cross-year or cross-regime robustness can be assessed from this run. Formal Market Eligibility remains **NO_GO**. Holdout: not accessed (BLINDNESS_UNVERIFIED applies to any later holdout use).

## Identity

| item | value |
|---|---|
| agent_git_sha | `e3ce26786054ff91d0d211e2f4dfd63a500e299f` |
| core_git_sha | `b16179a7cc077c36046574769a32c367fa476cae` |
| snapshot_id | `TSM_DEV_1M_20240529_20240628_18350b3adba8e28a7e5e` |
| manifest_sha256 | `8b6fc838b72ae3015f6eb418c3a77115e53a86f185e05c2249e9dcd58192cfc5` |
| parquet_sha256 | `239d5741d65cc0c7118f618a5ebb7b1dd946f385d843cae151a24edc1e0a8452` |
| lineage_sha256 | `3455f2b3d39f4fd05dda7ca9fed472c9ff0bcbf345f89f177e78eccbe5fd92a4` |
| canonical_config_sha256 | `8d03317ed7b6f5f67b33dfabe99d0699c06c2fd2ea402c6bf167b8752efcacfc` |
| session_config_sha256 | `a57caa43a441e7396e51132daa1a6abdce83dd3e60f585ae34d9f18da4544106` |
| authorization_id | `RA-IBKR_TSM-SMA520_P5B3C-0001` |
| execution_started_at_asia_taipei | `2026-11-10T14:18:06.111633+08:00` |
| execution_finished_at_asia_taipei | `2026-11-10T14:19:09.146947+08:00` |
| cost profile BASE | `ea2cb1932ba83eadcdc12720a56d7404d30686a03c6fe6ce10482cceca63c529` |
| cost profile LOW | `4111a7d0798162ff2c0cb6a8fd7f4e15f59d8f830e70f04da7b41e660efb101b` |
| cost profile STRESS | `80c00d97c8dc333ba7d39e54782e3af123cc09a46ca5703331faef82e105c401` |
| cost profile CAT_STRESS | `bd42c2661b3bf6dcf1b7d9f68b51762af2682ff13035384a49a340b1d574cbfc` |

## Data and timing

- 1m rows 21119; conservative 15m bars 1408; signal-eligible 506; buy/sell execution-eligible 528/528
- bar-level execution rejection reasons: {'OUTSIDE_CONSERVATIVE_RTH': 857, 'CLOSING_AUCTION_EXCLUDED': 22, 'INCOMPLETE_SOURCE_BUCKET': 1}
- last 1m timestamp 2024-06-28 23:59:00+00:00 (before holdout start: True)
- decisions in window 29 (incl. warm-up 32); orders 28
- CAT rounding: DIFFERENCE_VISIBLE

| scenario | temporal | fills | min/max minutes available→fill | fills on/before signal bar |
|---|---|---|---|---|
| BASE | PASS | 26 | 14.0000 / 14.0000 | 0 |
| LOW | PASS | 26 | 14.0000 / 14.0000 | 0 |
| STRESS | PASS | 26 | 14.0000 / 14.0000 | 0 |
| CAT_STRESS | PASS | 26 | 14.0000 / 14.0000 | 0 |

## Results (Core-computed; descriptive ratios are unthresholded)

| scenario | trades | sample | total return | closed-trade net return | gross return | win rate | payoff | expectancy | profit factor | max DD | rejections |
|---|---|---|---|---|---|---|---|---|---|---|---|
| BASE | 13 | INSUFFICIENT_SAMPLE | 1.844% | 1.844% | 1.877% | 46.154% | 3.6243 | 141.8731 | 3.1065 | -0.783% | 3 |
| LOW | 13 | INSUFFICIENT_SAMPLE | 1.896% | 1.896% | 1.929% | 46.154% | 3.7770 | 145.8731 | 3.2374 | -0.763% | 3 |
| STRESS | 13 | INSUFFICIENT_SAMPLE | 1.764% | 1.764% | 1.797% | 38.462% | 4.6682 | 135.7192 | 2.9176 | -0.815% | 3 |
| CAT_STRESS | 13 | INSUFFICIENT_SAMPLE | 1.844% | 1.844% | 1.877% | 46.154% | 3.6235 | 141.8531 | 3.1059 | -0.783% | 3 |

## Costs (USD, Core ledger)

| scenario | commission | slippage_cost | tick_rounding_cost | spread_cost | price_adjustment_cost | sec | taf | cat | regulatory_total | explicit_cost | total_cost |
|---|---|---|---|---|---|---|---|---|---|---|---|
| BASE | 26.000 | 87.943800000 | 16.056200000 | 0.00000 | 104.0000 | 6.130 | 0.399629629620 | 0.120370370380 | 6.650 | 32.650 | 136.6500 |
| LOW | 26.000 | 43.971900000 | 8.028100000 | 0.00000 | 52.0000 | 6.130 | 0.399629629620 | 0.120370370380 | 6.650 | 32.650 | 84.6500 |
| STRESS | 26.000 | 175.887600000 | 8.112400000 | 0.00000 | 184.0000 | 6.130 | 0.399629629620 | 0.120370370380 | 6.650 | 32.650 | 216.6500 |
| CAT_STRESS | 26.000 | 87.943800000 | 16.056200000 | 0.00000 | 104.0000 | 6.130 | 0.486766917286 | 0.293233082714 | 6.910 | 32.910 | 136.9100 |

## Holding period (Core 15m bars held, first scenario)

`{"unit": "Core 15m bars held (bar_end in (entry, exit])", "overnight_trades": 6, "count": 13, "min": 6, "median": 17, "max": 127, "mean": 36.53846153846154, "buckets": {"<=4": 0, "5-16": 5, "17-64": 6, ">=65": 2}}`

## Interpretation limits

- Not claimed: formal strategy validation
- Not claimed: Formal Market Eligibility (gate stays NO_GO)
- Not claimed: out-of-sample, cross-year or cross-regime robustness
- Not claimed: holdout evaluation
- Not claimed: family promotion / FROZEN / deployment
- Not claimed: account-verified (BROKER_ACTUAL) costs
- Trades below the configured minimum are labelled INSUFFICIENT_SAMPLE; ratios are descriptive only.
- Costs are RESEARCH_ONLY assumptions (IBKR Pro Fixed; TAF both sides uncapped; CAT assumed passed through).
