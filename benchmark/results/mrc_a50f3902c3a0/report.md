# Benchmark comparison — real market-research-core `a50f3902c3a0` (ExecutionEngine.run)

Verdict: **FAIL**  (PASS 1, FAIL 11, NOT_EXECUTABLE 0 of 12; would PASS if only the C2 rounding gap were fixed: 9)

| case | status | first divergent event | root cause tags | blocking |
|---|---|---|---|---|
| B01_normal_entry_exit | FAIL | fee/tax (tax) @ 2026-01-05 09:15: 80.044 → 80.04 | C2 | yes |
| B02_next_bar_execution_sma | PASS | — | — | no |
| B03_missing_bar | FAIL | fee/tax (tax) @ 2026-01-05 09:20: 80.124 → 80.12 | C2 | yes |
| B04_zero_volume | FAIL | rejection reasons @ 2026-01-05 09:15: [('2026-01-05 09:15', 'ZERO_VOLUME_BAR')] → [] | C2, K15 | yes |
| B05_outside_session | FAIL | rejection reasons @ 2026-01-05 09:20: [('2026-01-05 09:20', 'OUTSIDE_SESSION'), ('2026-01-06 08:55', 'OUTSIDE_SESSION')] → [('', 'ORDER_EXPIRED')] | K14, K15 | yes |
| B06_costs_slippage_short | FAIL | fee/tax (tax) @ 2026-01-05 09:10: 160.792 → 160.79 | C2 | yes |
| B07_limit_locked | FAIL | rejection reasons @ 2026-01-06 09:00: [('2026-01-06 09:00', 'LIMIT_UP_LOCKED_BUY'), ('2026-01-06 09:05', 'LIMIT_UP_LOCKED_BUY'), ('2026-01-07 09:00',  | C2, K15 | yes |
| B08_pit_not_yet_available | FAIL | fee/tax (fee) @ 2026-01-06 09:05: 87.0675 → 87.07 | C2 | yes |
| B09_futures_roll | FAIL | fee/tax (tax) @ 2026-01-06 09:05: 80.052 → 80.05 | C2 | yes |
| B10_open_position_at_end | FAIL | fee/tax (tax) @ 2026-01-05 09:15: 80.044 → 80.04 | C2 | yes |
| B11_exclusion_window | FAIL | fee/tax (tax) @ 2026-01-05 09:10: 80.044 → 80.04 | C2 | yes |
| B12_orb_lookahead_trap | FAIL | fee/tax (tax) @ 2026-01-05 09:25: 80.168 → 80.17 | C2 | yes |

C3 chronology probe: **FAIL** — fills [('BUY', '09:25'), ('SELL', '09:20')]; round trips [('LONG', '09:25', '09:20')]

## Differences per case

### B01_normal_entry_exit

Signals: fixture expected.signals (hand-calculated)

| dimension | # | expected | actual (real Core) | numeric difference |
|---|---|---|---|---|
| fee/tax (tax) | 0 | 80.044 | 80.04 | -0.004 |
| fee/tax (tax) | 1 | 80.196 | 80.2 | 0.004 |

C1 diagnostic (Core default `same_timestamp_open_assumption=False`): fill times [('2026-01-05 09:15', '2026-01-05 09:20'), ('2026-01-05 09:30', '2026-01-05 09:35')]

### B03_missing_bar

Signals: fixture expected.signals (hand-calculated)

| dimension | # | expected | actual (real Core) | numeric difference |
|---|---|---|---|---|
| fee/tax (tax) | 0 | 80.124 | 80.12 | -0.004 |
| fee/tax (tax) | 1 | 80.076 | 80.08 | 0.004 |

C1 diagnostic (Core default `same_timestamp_open_assumption=False`): fill times [('2026-01-05 09:30', '2026-01-05 09:35')]

### B04_zero_volume

Signals: fixture expected.signals (hand-calculated)

| dimension | # | expected | actual (real Core) | numeric difference |
|---|---|---|---|---|
| rejection reasons |  | [('2026-01-05 09:15', 'ZERO_VOLUME_BAR')] | [] |  |
| fee/tax (tax) | 0 | 80.084 | 80.08 | -0.004 |
| fee/tax (tax) | 1 | 80.156 | 80.16 | 0.004 |

C1 diagnostic (Core default `same_timestamp_open_assumption=False`): fill times [('2026-01-05 09:30', '2026-01-05 09:35')]

### B05_outside_session

Signals: fixture expected.signals (hand-calculated)

| dimension | # | expected | actual (real Core) | numeric difference |
|---|---|---|---|---|
| rejection reasons |  | [('2026-01-05 09:20', 'OUTSIDE_SESSION'), ('2026-01-06 08:55', 'OUTSIDE_SESSION' | [('', 'ORDER_EXPIRED')] |  |
| fill count |  | 2 | 0 |  |
| realized PnL |  | -155.395 | 0 | 155.395 |

C1 diagnostic (Core default `same_timestamp_open_assumption=False`): fill times [('2026-01-06 09:10', '2026-01-06 09:15')]

### B06_costs_slippage_short

Signals: fixture expected.signals (hand-calculated)

| dimension | # | expected | actual (real Core) | numeric difference |
|---|---|---|---|---|
| fee/tax (tax) | 0 | 160.792 | 160.79 | -0.002 |
| fee/tax (tax) | 1 | 160.008 | 160.01 | 0.002 |

C1 diagnostic (Core default `same_timestamp_open_assumption=False`): fill times [('2026-01-05 09:10', '2026-01-05 09:15'), ('2026-01-05 09:20', '2026-01-05 09:25')]

### B07_limit_locked

Signals: fixture expected.signals (hand-calculated)

| dimension | # | expected | actual (real Core) | numeric difference |
|---|---|---|---|---|
| rejection reasons |  | [('2026-01-06 09:00', 'LIMIT_UP_LOCKED_BUY'), ('2026-01-06 09:05', 'LIMIT_UP_LOC | [] |  |
| fee/tax (fee) | 1 | 83.6475 | 83.65 | 0.0025 |
| realized PnL |  | -7253.2275 | -7253.23 | -0.0025 |

### B08_pit_not_yet_available

Signals: fixture expected.signals (hand-calculated)

| dimension | # | expected | actual (real Core) | numeric difference |
|---|---|---|---|---|
| fee/tax (fee) | 0 | 87.0675 | 87.07 | 0.0025 |
| fee/tax (fee) | 1 | 87.495 | 87.5 | 0.005 |
| realized PnL |  | -58.7625 | -58.77 | -0.0075 |

C1 diagnostic (Core default `same_timestamp_open_assumption=False`): fill times [('2026-01-06 09:05', '2026-01-06 09:10')]

### B09_futures_roll

Signals: fixture expected.signals (hand-calculated)

| dimension | # | expected | actual (real Core) | numeric difference |
|---|---|---|---|---|
| fee/tax (tax) | 0 | 80.052 | 80.05 | -0.002 |
| fee/tax (tax) | 1 | 80.396 | 80.4 | 0.004 |
| fee/tax (tax) | 2 | 80.604 | 80.6 | -0.004 |
| fee/tax (tax) | 3 | 80.796 | 80.8 | 0.004 |
| realized PnL |  | 26278.152 | 26278.15 | -0.002 |

C1 diagnostic (Core default `same_timestamp_open_assumption=False`): fill times [('2026-01-06 09:05', '2026-01-06 09:10'), ('2026-01-07 09:00', '2026-01-07 09:05'), ('2026-01-07 09:00', '2026-01-07 09:05')]

### B10_open_position_at_end

Signals: fixture expected.signals (hand-calculated)

| dimension | # | expected | actual (real Core) | numeric difference |
|---|---|---|---|---|
| fee/tax (tax) | 0 | 80.044 | 80.04 | -0.004 |

C1 diagnostic (Core default `same_timestamp_open_assumption=False`): fill times [('2026-01-05 09:15', '2026-01-05 09:20')]

### B11_exclusion_window

Signals: fixture expected.signals (hand-calculated)

| dimension | # | expected | actual (real Core) | numeric difference |
|---|---|---|---|---|
| fee/tax (tax) | 0 | 80.044 | 80.04 | -0.004 |
| fee/tax (tax) | 1 | 80.256 | 80.26 | 0.004 |

C1 diagnostic (Core default `same_timestamp_open_assumption=False`): fill times [('2026-01-05 09:10', '2026-01-05 09:15')]

### B12_orb_lookahead_trap

Signals: strategy_agent.signals.orb

| dimension | # | expected | actual (real Core) | numeric difference |
|---|---|---|---|---|
| fee/tax (tax) | 0 | 80.168 | 80.17 | 0.002 |
| realized PnL |  | 1339.632 | 1339.63 | -0.002 |

C1 diagnostic (Core default `same_timestamp_open_assumption=False`): fill times [('2026-01-05 09:25', '2026-01-05 09:30')]

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
| K12 | C3 | PARTIAL | rounding modes Literal['HALF_UP', 'UP', 'DOWN']: quantize to 0.01 only; no unrounded mode, no FLOOR_INTEGER; float accounting (Decimal only inside rou |
| K13 | C1 | SUPPORTED | order_time < signal_available_at -> PIT_VIOLATION; bar_start >= available_at |
| K14 | C3 | PARTIAL | time_in_force Literal['NEXT_BAR', 'DAY']: DAY = session of the first candidate bar; convention v1 = first session ending after order_ts |
| K15 | C3 | PARTIAL | one rejection per unfilled ORDER (final reason); skipped bars of a later-filled order are not logged |
