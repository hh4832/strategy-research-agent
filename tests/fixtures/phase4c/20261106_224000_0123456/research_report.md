# TSM SMA 5/20 — Research-only smoke `20261106_224000_0123456`

> **RESEARCH_ONLY_EXPLORATORY.** One 4-week development window, one fixed parameter set. This is NOT evidence that SMA 5/20 works. No out-of-sample, cross-year or cross-regime robustness can be assessed from this run. Formal Market Eligibility remains **NO_GO**. Holdout: not accessed (BLINDNESS_UNVERIFIED applies to any later holdout use).

## Identity

| item | value |
|---|---|
| agent_git_sha | `0123456789abcdef0123456789abcdef01234567` |
| core_git_sha | `b16179a7cc077c36046574769a32c367fa476cae` |
| snapshot_id | `TSM_DEV_1M_20240529_20240628_529b6b7c5c6ff616a618` |
| manifest_sha256 | `90bba07364ac93a75d9efdefdd6a9dfd40840c9780d774f65ade3722a91ab288` |
| parquet_sha256 | `785161e81993df77f25c816a97ccb91f97ebfb3d76988b74b49d93e49ac1fbca` |
| lineage_sha256 | `6f5884dedf578971394290e98e6288ef16b0318d70e5e3d6a713b1af05f3140f` |
| canonical_config_sha256 | `76c8b41177a58cc0e4951461f30b1816c04e7f8f332f79ec95c92287752ecba1` |
| session_config_sha256 | `27389705d07e7d8f9d54e61ba44d1c223775353aa09c0035fe702d21007ca22c` |
| authorization_id | `RA-IBKR_TSM-SMA520_DEV-18350b3adba8e28a7e5e` |
| execution_started_at_asia_taipei | `2026-11-06T22:40:00+08:00` |
| execution_finished_at_asia_taipei | `2026-11-07T08:39:11.372189+08:00` |
| cost profile BASE | `ea2cb1932ba83eadcdc12720a56d7404d30686a03c6fe6ce10482cceca63c529` |
| cost profile LOW | `4111a7d0798162ff2c0cb6a8fd7f4e15f59d8f830e70f04da7b41e660efb101b` |
| cost profile STRESS | `80c00d97c8dc333ba7d39e54782e3af123cc09a46ca5703331faef82e105c401` |
| cost profile CAT_STRESS | `bd42c2661b3bf6dcf1b7d9f68b51762af2682ff13035384a49a340b1d574cbfc` |

## Data and timing

- 1m rows 8580; conservative 15m bars 572; signal-eligible 506; buy/sell execution-eligible 528/528
- bar-level execution rejection reasons: {'OUTSIDE_CONSERVATIVE_RTH': 22, 'CLOSING_AUCTION_EXCLUDED': 22}
- last 1m timestamp 2024-06-28 19:59:00+00:00 (before holdout start: True)
- decisions in window 43 (incl. warm-up 48); orders 42
- CAT rounding: DIFFERENCE_VISIBLE

| scenario | temporal | fills | min/max minutes available→fill | fills on/before signal bar |
|---|---|---|---|---|
| BASE | PASS | 40 | 14.0000 / 14.0000 | 0 |
| LOW | PASS | 40 | 14.0000 / 14.0000 | 0 |
| STRESS | PASS | 40 | 14.0000 / 14.0000 | 0 |
| CAT_STRESS | PASS | 40 | 14.0000 / 14.0000 | 0 |

## Results (Core-computed; descriptive ratios are unthresholded)

| scenario | trades | sample | total return | closed-trade net return | gross return | win rate | payoff | expectancy | profit factor | max DD | rejections |
|---|---|---|---|---|---|---|---|---|---|---|---|
| BASE | 20 | INSUFFICIENT_SAMPLE | -1.532% | -1.532% | -1.482% | 30.000% | 0.9197 | -76.6135 | 0.3942 | -1.827% | 2 |
| LOW | 20 | INSUFFICIENT_SAMPLE | -1.452% | -1.452% | -1.402% | 30.000% | 0.9632 | -72.6135 | 0.4128 | -1.749% | 2 |
| STRESS | 20 | INSUFFICIENT_SAMPLE | -1.657% | -1.657% | -1.607% | 30.000% | 0.8558 | -82.8635 | 0.3668 | -1.948% | 2 |
| CAT_STRESS | 20 | INSUFFICIENT_SAMPLE | -1.533% | -1.533% | -1.482% | 30.000% | 0.9195 | -76.6335 | 0.3941 | -1.827% | 2 |

## Costs (USD, Core ledger)

| scenario | commission | slippage_cost | tick_rounding_cost | spread_cost | price_adjustment_cost | sec | taf | cat | regulatory_total | explicit_cost | total_cost |
|---|---|---|---|---|---|---|---|---|---|---|---|
| BASE | 40.000 | 136.347200000 | 23.652800000 | 0.00000 | 160.0000 | 9.470 | 0.614814814800 | 0.185185185200 | 10.270 | 50.270 | 210.2700 |
| LOW | 40.000 | 68.173600000 | 11.826400000 | 0.00000 | 80.0000 | 9.470 | 0.614814814800 | 0.185185185200 | 10.270 | 50.270 | 130.2700 |
| STRESS | 40.000 | 272.694400000 | 12.305600000 | 0.00000 | 285.0000 | 9.470 | 0.614814814800 | 0.185185185200 | 10.270 | 50.270 | 335.2700 |
| CAT_STRESS | 40.000 | 136.347200000 | 23.652800000 | 0.00000 | 160.0000 | 9.470 | 0.748872180440 | 0.451127819560 | 10.670 | 50.670 | 210.6700 |

## Holding period (Core 15m bars held, first scenario)

`{"unit": "Core 15m bars held (bar_end in (entry, exit])", "overnight_trades": 9, "count": 20, "min": 3, "median": 9.5, "max": 29, "mean": 12.25, "buckets": {"<=4": 2, "5-16": 11, "17-64": 7, ">=65": 0}}`

## Interpretation limits

- Not claimed: formal strategy validation
- Not claimed: Formal Market Eligibility (gate stays NO_GO)
- Not claimed: out-of-sample, cross-year or cross-regime robustness
- Not claimed: holdout evaluation
- Not claimed: family promotion / FROZEN / deployment
- Not claimed: account-verified (BROKER_ACTUAL) costs
- Trades below the configured minimum are labelled INSUFFICIENT_SAMPLE; ratios are descriptive only.
- Costs are RESEARCH_ONLY assumptions (IBKR Pro Fixed; TAF both sides uncapped; CAT assumed passed through).
