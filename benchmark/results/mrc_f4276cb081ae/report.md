# Benchmark comparison — real market-research-core `f4276cb081ae`

Verdict: **INTEGRATION_PENDING**  (PASS 0, FAIL 1, NOT_EXECUTABLE 11 of 12)

| case | status | core API | root cause | responsible repo |
|---|---|---|---|---|
| B01_normal_entry_exit | NOT_EXECUTABLE | — | CORE_CAPABILITY_GAP:K01/K03/K04/K11/K12 | market-research-core |
| B02_next_bar_execution_sma | FAIL | BacktestEngine.run_sma | CORE_CAPABILITY_GAP:K03/K12 (bps slippage cannot express ticks; float arithmetic) | market-research-core |
| B03_missing_bar | NOT_EXECUTABLE | — | CORE_CAPABILITY_GAP:K01/K03/K04/K11/K12 | market-research-core |
| B04_zero_volume | NOT_EXECUTABLE | — | CORE_CAPABILITY_GAP:K01/K03/K04/K07/K11/K12/K15 | market-research-core |
| B05_outside_session | NOT_EXECUTABLE | — | CORE_CAPABILITY_GAP:K01/K03/K06/K11/K12/K15 | market-research-core |
| B06_costs_slippage_short | NOT_EXECUTABLE | — | CORE_CAPABILITY_GAP:K01/K02/K03/K04/K11/K12 | market-research-core |
| B07_limit_locked | NOT_EXECUTABLE | — | CORE_CAPABILITY_GAP:K01/K03/K05/K11/K12/K15 | market-research-core |
| B08_pit_not_yet_available | NOT_EXECUTABLE | — | CORE_CAPABILITY_GAP:K01/K03/K11/K12/K13 | market-research-core |
| B09_futures_roll | NOT_EXECUTABLE | — | CORE_CAPABILITY_GAP:K01/K03/K04/K09/K11/K12 | market-research-core |
| B10_open_position_at_end | NOT_EXECUTABLE | — | CORE_CAPABILITY_GAP:K01/K03/K04/K10/K11/K12 | market-research-core |
| B11_exclusion_window | NOT_EXECUTABLE | — | CORE_CAPABILITY_GAP:K01/K03/K04/K08/K11/K12 | market-research-core |
| B12_orb_lookahead_trap | NOT_EXECUTABLE | — | CORE_CAPABILITY_GAP:K01/K03/K04/K11/K12 | market-research-core |

## Executed cases: expected vs actual

### B02_next_bar_execution_sma — FAIL

| | expected | actual (real Core) |
|---|---|---|
| fill[0].exec_ts | 2026-01-05 09:30 | 2026-01-05 09:30 |
| fill[0].side | BUY | BUY |
| fill[0].qty | 1000 | 1000 |
| fill[0].fill_price | 60.4 | 60.4005 |
| fill[0].fee | 86.07 | 86.0707125 |
| fill[0].tax | 0 | 0.0 |
| fill[1].exec_ts | 2026-01-05 09:45 | 2026-01-05 09:45 |
| fill[1].side | SELL | SELL |
| fill[1].qty | 1000 | 1000 |
| fill[1].fill_price | 59.6 | 59.600500000000004 |
| fill[1].fee | 84.93 | 84.93071250000001 |
| fill[1].tax | 178.8 | 178.80150000000003 |
| realized_pnl | -1149.80 | -1149.802924999989 |
| open_position | None | None |

Differences:

- fill[0].fill_price: expected 60.4 got 60.4005
- fill[0].fee: expected 86.07 got 86.0707125
- fill[1].fill_price: expected 59.6 got 59.600500000000004
- fill[1].fee: expected 84.93 got 84.93071250000001
- fill[1].tax: expected 178.8 got 178.80150000000003
- realized_pnl expected -1149.80 got -1149.802924999989

Notes: 1 tick (0.1) expressed as 16.666667 bps of the first open

## Capability matrix (measured on the installed Core)

| cap | contract | status | needed by | evidence |
|---|---|---|---|---|
| K01 | C3 | MISSING | B01, B03, B04, B05, B06, B07, B08, B09, B10, B11, B12 | BacktestEngine public methods ['run_sma']; generic entry points none |
| K02 | C3 | MISSING | B06 | run_sma is long-only (desired = fast > slow -> 0/1) |
| K03 | C8 | PARTIAL | B01, B02, B03, B04, B05, B06, B07, B08, B09, B10, B11, B12 | assumptions ['slippage_bps']: bps only |
| K04 | C8 | MISSING | B01, B03, B04, B06, B09, B10, B11, B12 | fee model: ['fee_rate', 'sell_tax_rate'] |
| K05 | C1 | MISSING | B07 | required columns ['available_at', 'backtest_eligible', 'close', 'data_exists', 'execution_eligible', 'high', 'low', 'open', 'research_eligib |
| K06 | C1 | MISSING | B05 | no session handling in backtest.py |
| K07 | C1 | MISSING | B04 | zero volume not distinguished |
| K08 | C5 | MISSING | B11 | no exclusion-window logic |
| K09 | C6 | MISSING | B09 | no roll schedule (pandas .rolling() is not a contract roll) |
| K10 | C7 | PARTIAL | B10 | probe ended long: position 1.0, BacktestResult fields ['trades', 'equity_curve', 'total_return', 'max_drawdown', 'trade_count', 'rejections' |
| K11 | C4 | MISSING | B01, B02, B03, B04, B05, B06, B07, B08, B09, B10, B11, B12 | probe provenance keys ['data', 'engine_version', 'lookahead_rule', 'performance_status', 'strategy', 'strategy_parameters']; engine_version  |
| K12 | C3 | MISSING | B01, B02, B03, B04, B05, B06, B07, B08, B09, B10, B11, B12 | float arithmetic, no rounding rule |
| K13 | C1 | MISSING | B08 | no PIT flag |
| K14 | C3 | MISSING | — | no TIF / expiry |
| K15 | C3 | PARTIAL | B04, B05, B07 | reasons: EXECUTION_INELIGIBLE (side/cause unspecified), NO_LATER_ELIGIBLE_BAR, INSUFFICIENT_CASH |
