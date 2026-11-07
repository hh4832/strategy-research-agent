# Archive contract mapping — Phase 4-A real archive → Phase 4-B evaluator input

Source run `20261107_081300_9439f8c`; archive MANIFEST.json `328f2cf99c33b4d9d427327d092aaf31da619b5f5ad948c815e75b04b92345a9` (independent pin); dataset manifest `8b6fc838b72ae3015f6eb418c3a77115e53a86f185e05c2249e9dcd58192cfc5` (a different file).

| Evaluator field | Actual archive source | Mapping | Evidence status |
|---|---|---|---|
| source.run_id | MANIFEST.json run_id (= directory name) | Direct | VERIFIED |
| source.commit_sha / core_sha | run_info.txt agent_git_sha / core_git_sha | Direct; cross-checked with MANIFEST.run_info and loader provenance core_git_sha | VERIFIED |
| source.dataset_snapshot / manifest_sha256 | run_info.txt snapshot_id / manifest_sha256 (DATASET manifest) | Direct; cross-checked with dataset_manifest.json loader_provenance | VERIFIED |
| source.strategy_id / parameter_family | config.json strategy | Direct (fixed parameters, no search) | VERIFIED |
| source.timeframe | config.json temporal_policy + Core conservative 15m | Constant '15m' | VERIFIED |
| source.sample_period | config.json windows warmup_start / end | Core equity is observed from the warm-up start; trading window [start,end) is narrower and recorded separately | VERIFIED |
| source.currency | spec position_sizing (USD account) | Constant 'USD' | VERIFIED |
| initial_capital | equity_curve.csv first Core observation (flat, equity == cash) | Direct when the first observation is flat; else MISSING | VERIFIED |
| holdout | governance/holdout_locks.jsonl + research_authorizations.jsonl holdout + run_info holdout_accessed + execution_diagnostics max_native_timestamp_before_holdout | NOT_ACCESSED only when all four agree; blindness copied from the authorization | VERIFIED |
| trades | trades.csv (Core round_trips, per scenario) | Direct; trade_id = scenario:entry_ts:exit_ts (content-derived, so duplicates collide); invalidated -> status | VERIFIED |
| fills | fills.csv (Core fill ledger) | Direct; fill_id = order_id@exec_ts; notional not exported by Core (turnover therefore NOT_AVAILABLE, not computed) | VERIFIED |
| equity_curve | equity_curve.csv (Core equity, every 15m bar incl. warm-up) | Direct | VERIFIED |
| cost_breakdown | cost_breakdown.csv (Core tsm_cost_diagnostics totals) | explicit_total = explicit_cost; components commission/SEC/TAF/CAT; slippage = price_adjustment_cost (embedded in fill prices, informational) | VERIFIED |
| rejections | rejected_orders.csv (Core rejection rows) | Direct, one row per Core rejection event (bar-level + terminal); unique rejected orders counted separately | VERIFIED |
| execution_diagnostics | execution_diagnostics.json temporal[scenario] + metrics.json m3a4_checks + decisions.orders | PASS only if temporal PASS and every M3-A.4 check PASS; lookahead_checked NOT asserted (fill chronology is verified, signal computation is not independently re-verified) | VERIFIED |
| data_quality | market_readiness DATA_QUALITY (FINAL GATE parent) + dataset_manifest.json loader provenance | PASS only when the formal DATA_QUALITY gate is PASS and loader provenance matches the dataset pins | VERIFIED |
| metrics | metrics.json[scenario] (M3-B evaluator over Core report) | Direct; preserved as upstream | VERIFIED |
| budget_consumption | registry/research_authorization_uses.jsonl (signed, pushed d456fed) vs run_info authorization_use | Copy of the SOURCE run's single consumption; evaluation consumes 0 | VERIFIED |
| cost_scenarios | cost_breakdown.csv + trades.csv (same run, same decisions/orders for all scenarios) | cost_multiplier = observed total cost / BASE total cost (factual ratio; no scenario is declared '2x') | VERIFIED |

Not reconstructed: trades are Core round trips from trades.csv; no trade, PnL, notional or cost is derived from fills or aggregates. Turnover and rejection rate stay NOT_AVAILABLE (no notional export; rejection rows repeat an order id). Order outcomes: see acceptance_checks.json C3 `orders_without_fill_or_rejection_record`.
