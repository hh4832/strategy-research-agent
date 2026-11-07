# Benchmark comparison — real market-research-core `e31c57b6c9dd` (ExecutionEngine.run)

Verdict: **PASS**  (PASS 12, FAIL 0, NOT_EXECUTABLE 0 of 12; would PASS if only the C2 rounding gap were fixed: 12)

| case | status | first divergent event | root cause tags | blocking |
|---|---|---|---|---|
| B01_normal_entry_exit | PASS | — | — | no |
| B02_next_bar_execution_sma | PASS | — | — | no |
| B03_missing_bar | PASS | — | — | no |
| B04_zero_volume | PASS | — | — | no |
| B05_outside_session | PASS | — | — | no |
| B06_costs_slippage_short | PASS | — | — | no |
| B07_limit_locked | PASS | — | — | no |
| B08_pit_not_yet_available | PASS | — | — | no |
| B09_futures_roll | PASS | — | — | no |
| B10_open_position_at_end | PASS | — | — | no |
| B11_exclusion_window | PASS | — | — | no |
| B12_orb_lookahead_trap | PASS | — | — | no |

G16 chronology acceptance: **PASS** — fills_in_fill_time_order=PASS, no_exit_before_entry=PASS, equity_starts_at_initial_cash_and_reconciles_when_flat=PASS, prefix_invariance_no_future_dependent_fills=PASS, unfilled_order_does_not_change_state_before_fill=PASS, same_timestamp_orders_deterministic=PASS

Oracle probe G17 (`X16_delayed_buy_later_target`, outside B01-B12): **PASS**; adapter guard hits 0
Oracle probe G14b (`X14b_day_expiry_back_to_back_sessions`, outside B01-B12): **PASS**; adapter guard hits 0

## Differences per case

## Capability matrix (measured on the installed Core)

| cap | contract | status | evidence |
|---|---|---|---|
| K01 | C3 | SUPPORTED | ExecutionEngine.run('bars', 'orders', 'cost_profile', 'config') |
| K02 | C3 | SUPPORTED | FIFO long/short lots; target_position signed |
| K03 | C8 | SUPPORTED | CostProfile.tick_size + slippage_ticks; adverse_fill_price rounds against the trader to a valid tick |
| K04 | C8 | SUPPORTED | futures fee per contract per side, transaction tax rate, multiplier |
| K05 | C1 | SUPPORTED | bars require buy_/sell_execution_eligible (+ per-side rejection reason columns) |
| K06 | C1 | SUPPORTED | session_open/session_close per bar; OUTSIDE_SESSION when bar_start outside [open, close) |
| K07 | C1 | SUPPORTED | volume <= 0 -> ZERO_VOLUME_BAR |
| K08 | C5 | SUPPORTED | ExecutionConfig.exclusion_windows; invalidated_round_trips ledger |
| K09 | C6 | PARTIAL | FIFO keyed by (symbol, contract); no roll schedule input: roll orders must be supplied by the caller |
| K10 | C7 | SUPPORTED | ExecutionResult.open_positions with mark_price, unrealized_pnl |
| K11 | C4 | PARTIAL | provenance.engine_git_sha = ExecutionConfig.core_commit_sha (caller-supplied; engine does not self-identify; engine_version hard-coded '2.1.0') |
| K12 | C3 | SUPPORTED | rounding modes Literal['HALF_UP', 'UP', 'DOWN', 'NONE', 'FLOOR_INTEGER']; calculate_costs returns Decimal |
| K13 | C1 | SUPPORTED | order_time < signal_available_at -> PIT_VIOLATION; bar_start >= available_at |
| K14 | C3 | SUPPORTED | time_in_force Literal['NEXT_BAR', 'DAY']; DAY expiry at the first session_close > order_time |
| K15 | C3 | SUPPORTED | rejection per skipped bar with order_id, bar_ts, reason |
| K16 | C3 | SUPPORTED | probe fills [('SELL', '09:20'), ('BUY', '09:25')]; round trips [('SHORT', '09:20', '09:25')] |
| K17 | C3 | SUPPORTED | probe fills [('SELL', '09:20'), ('BUY', '09:25')]; open positions 0 |
| K14b | C3 | SUPPORTED | probe fills []; rejections ['LIMIT_UP_LOCKED_BUY', 'LIMIT_UP_LOCKED_BUY', 'ORDER_EXPIRED'] |
