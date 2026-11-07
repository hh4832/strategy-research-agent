# Research evaluation

- source_run_id: 20261107_081300_9439f8c
- source_archive_path: /content/drive/MyDrive/Quant_Research/strategy-research-agent/20261107_081300_9439f8c
- source_commit_sha: 9439f8c6f53b9c6c7f2f49b5a543ffdbc84f2f2a
- evaluator_commit_sha: 883d14c0059add270d596e4bdc86be46052219ea
- evaluation_timestamp: 2026-11-07T10:03:44+08:00
- schema_version: 1.0.0
- evaluator_version: 1.0.0

Engineering: **PASS**
Strategy Verdict: **RETEST**
Evidence Sufficiency: **UNDETERMINED**

RETEST is not evidence of a positive effect. KEEP is research retention, never permission to trade.
SYNTHETIC_FIXTURE outputs are engineering evidence only.

## Independent layers

### data_quality

```json
{
  "status": "AVAILABLE",
  "value": {
    "evidence_ref": "governance/market_readiness.json gates.DATA_QUALITY (https://github.com/hh4832/market-research-core/blob/699e3ad59951bbebb769bb69170be8d9a1a96c01/docs/diagnostics/m3a_ibkr_tsm_readiness.json); development snapshot lineage via Core loader provenance REUSED_TRUSTED_SHA256_RECEIPT_WITH_STAT_GUARD",
    "status": "PASS"
  }
}
```

### execution_quality

```json
{
  "status": "AVAILABLE",
  "value": {
    "exposure": 0.33735795454545453,
    "fill_diagnostics": {
      "m3a4_checks": {
        "K00_ledger_is_fills": "PASS",
        "K01_ledger_fields": "PASS",
        "K02_ledger_cost_identities": "PASS",
        "K03_round_trip_costs_equal_ledger": "PASS",
        "P01_engine_git_sha": "PASS",
        "P02_engine_version": "PASS",
        "P03_available_at_policy": "PASS",
        "P04_temporal_policy_sha256": "PASS",
        "P05_temporal_policy_id": "PASS",
        "P06_label_unknown": "PASS",
        "P07_modeled_rule": "PASS",
        "P08_research_only": "PASS",
        "P09_cost_profile_sha256": "PASS",
        "P10_cost_profile_version": "PASS",
        "P11_cost_components_declared": "PASS",
        "R01_rejection_codes_reviewed": "PASS",
        "T01_no_lookahead": "PASS"
      },
      "temporal": {
        "execution_assumptions": [
          "NEXT_ELIGIBLE_BAR_OPEN"
        ],
        "fills_checked": 26,
        "fills_not_strictly_after_signal_available_at": 0,
        "fills_on_or_before_signal_bar": 0,
        "max_minutes_signal_available_to_fill": 14.0,
        "min_minutes_signal_available_to_fill": 14.0,
        "order_time_equals_signal_time": true,
        "status": "PASS"
      }
    },
    "orders_submitted": 28,
    "status": "PASS"
  }
}
```

### statistical_evidence

```json
{
  "protocol_gates": {
    "G1": {
      "reason": "required protocol evidence absent",
      "status": "NOT_TESTED"
    },
    "G10": {
      "reason": "required protocol evidence absent",
      "status": "NOT_TESTED"
    },
    "G11": {
      "reason": "required protocol evidence absent",
      "status": "NOT_TESTED"
    },
    "G2": {
      "reason": "required protocol evidence absent",
      "status": "NOT_TESTED"
    },
    "G3": {
      "reason": "required protocol evidence absent",
      "status": "NOT_TESTED"
    },
    "G4": {
      "reason": "required protocol evidence absent",
      "status": "NOT_TESTED"
    },
    "G5": {
      "reason": "required protocol evidence absent",
      "status": "NOT_TESTED"
    },
    "G6": {
      "reason": "required protocol evidence absent",
      "status": "NOT_TESTED"
    },
    "G7": {
      "reason": "required protocol evidence absent",
      "status": "NOT_TESTED"
    },
    "G8": {
      "reason": "required protocol evidence absent",
      "status": "NOT_TESTED"
    },
    "G9": {
      "reason": "required protocol evidence absent",
      "status": "NOT_TESTED"
    }
  },
  "sample": {
    "effective_net_pnl": 1844.35,
    "invalidated_abs_pnl_share": 0.0,
    "invalidated_fraction": 0.0,
    "invalidated_net_pnl_for_reference": 0,
    "invalidated_trades": 0,
    "reasons": [
      {
        "code": "Q5-1",
        "message": "valid trades 13 < 30 after invalidation"
      }
    ],
    "status": "INSUFFICIENT",
    "valid_trades": 13
  }
}
```

### cost_sensitivity

```json
{
  "status": "AVAILABLE",
  "value": [
    {
      "comparison_basis": "SAME_SIGNALS_AND_POSITIONS",
      "core_sha": "b16179a7cc077c36046574769a32c367fa476cae",
      "cost_multiplier": 1.0,
      "currency": "USD",
      "dataset_snapshot": "TSM_DEV_1M_20240529_20240628_18350b3adba8e28a7e5e",
      "evidence_ref": "20261107_081300_9439f8c/cost_breakdown.csv#BASE + trades.csv",
      "net_pnl": 1844.35,
      "parameter_family": "sma_crossover_tsm_15m_5_20@0.1.0:fast=5,slow=20",
      "sample_period": {
        "end": "2024-06-29T00:00:00-04:00",
        "start": "2024-05-29T00:00:00-04:00",
        "timezone": "America/New_York"
      },
      "scenario_id": "BASE",
      "source_run_id": "20261107_081300_9439f8c",
      "strategy_id": "sma_crossover_tsm_15m_5_20",
      "timeframe": "15m"
    },
    {
      "comparison_basis": "SAME_SIGNALS_AND_POSITIONS",
      "core_sha": "b16179a7cc077c36046574769a32c367fa476cae",
      "cost_multiplier": 0.619465788510794,
      "currency": "USD",
      "dataset_snapshot": "TSM_DEV_1M_20240529_20240628_18350b3adba8e28a7e5e",
      "evidence_ref": "20261107_081300_9439f8c/cost_breakdown.csv#LOW + trades.csv",
      "net_pnl": 1896.35,
      "parameter_family": "sma_crossover_tsm_15m_5_20@0.1.0:fast=5,slow=20",
      "sample_period": {
        "end": "2024-06-29T00:00:00-04:00",
        "start": "2024-05-29T00:00:00-04:00",
        "timezone": "America/New_York"
      },
      "scenario_id": "LOW",
      "source_run_id": "20261107_081300_9439f8c",
      "strategy_id": "sma_crossover_tsm_15m_5_20",
      "timeframe": "15m"
    },
    {
      "comparison_basis": "SAME_SIGNALS_AND_POSITIONS",
      "core_sha": "b16179a7cc077c36046574769a32c367fa476cae",
      "cost_multiplier": 1.5854372484449324,
      "currency": "USD",
      "dataset_snapshot": "TSM_DEV_1M_20240529_20240628_18350b3adba8e28a7e5e",
      "evidence_ref": "20261107_081300_9439f8c/cost_breakdown.csv#STRESS + trades.csv",
      "net_pnl": 1764.35,
      "parameter_family": "sma_crossover_tsm_15m_5_20@0.1.0:fast=5,slow=20",
      "sample_period": {
        "end": "2024-06-29T00:00:00-04:00",
        "start": "2024-05-29T00:00:00-04:00",
        "timezone": "America/New_York"
      },
      "scenario_id": "STRESS",
      "source_run_id": "20261107_081300_9439f8c",
      "strategy_id": "sma_crossover_tsm_15m_5_20",
      "timeframe": "15m"
    },
    {
      "comparison_basis": "SAME_SIGNALS_AND_POSITIONS",
      "core_sha": "b16179a7cc077c36046574769a32c367fa476cae",
      "cost_multiplier": 1.001902671057446,
      "currency": "USD",
      "dataset_snapshot": "TSM_DEV_1M_20240529_20240628_18350b3adba8e28a7e5e",
      "evidence_ref": "20261107_081300_9439f8c/cost_breakdown.csv#CAT_STRESS + trades.csv",
      "net_pnl": 1844.0900000000001,
      "parameter_family": "sma_crossover_tsm_15m_5_20@0.1.0:fast=5,slow=20",
      "sample_period": {
        "end": "2024-06-29T00:00:00-04:00",
        "start": "2024-05-29T00:00:00-04:00",
        "timezone": "America/New_York"
      },
      "scenario_id": "CAT_STRESS",
      "source_run_id": "20261107_081300_9439f8c",
      "strategy_id": "sma_crossover_tsm_15m_5_20",
      "timeframe": "15m"
    }
  ]
}
```

### robustness

```json
[
  {
    "dimension": "robustness",
    "group": "in_sample_vs_out_of_sample",
    "reason": "required evidence absent",
    "status": "NOT_TESTED"
  },
  {
    "dimension": "robustness",
    "evidence": [
      {
        "comparison_basis": "SAME_SIGNALS_AND_POSITIONS",
        "core_sha": "b16179a7cc077c36046574769a32c367fa476cae",
        "cost_multiplier": 1.0,
        "currency": "USD",
        "dataset_snapshot": "TSM_DEV_1M_20240529_20240628_18350b3adba8e28a7e5e",
        "evidence_ref": "20261107_081300_9439f8c/cost_breakdown.csv#BASE + trades.csv",
        "net_pnl": 1844.35,
        "parameter_family": "sma_crossover_tsm_15m_5_20@0.1.0:fast=5,slow=20",
        "sample_period": {
          "end": "2024-06-29T00:00:00-04:00",
          "start": "2024-05-29T00:00:00-04:00",
          "timezone": "America/New_York"
        },
        "scenario_id": "BASE",
        "source_run_id": "20261107_081300_9439f8c",
        "strategy_id": "sma_crossover_tsm_15m_5_20",
        "timeframe": "15m"
      },
      {
        "comparison_basis": "SAME_SIGNALS_AND_POSITIONS",
        "core_sha": "b16179a7cc077c36046574769a32c367fa476cae",
        "cost_multiplier": 0.619465788510794,
        "currency": "USD",
        "dataset_snapshot": "TSM_DEV_1M_20240529_20240628_18350b3adba8e28a7e5e",
        "evidence_ref": "20261107_081300_9439f8c/cost_breakdown.csv#LOW + trades.csv",
        "net_pnl": 1896.35,
        "parameter_family": "sma_crossover_tsm_15m_5_20@0.1.0:fast=5,slow=20",
        "sample_period": {
          "end": "2024-06-29T00:00:00-04:00",
          "start": "2024-05-29T00:00:00-04:00",
          "timezone": "America/New_York"
        },
        "scenario_id": "LOW",
        "source_run_id": "20261107_081300_9439f8c",
        "strategy_id": "sma_crossover_tsm_15m_5_20",
        "timeframe": "15m"
      },
      {
        "comparison_basis": "SAME_SIGNALS_AND_POSITIONS",
        "core_sha": "b16179a7cc077c36046574769a32c367fa476cae",
        "cost_multiplier": 1.5854372484449324,
        "currency": "USD",
        "dataset_snapshot": "TSM_DEV_1M_20240529_20240628_18350b3adba8e28a7e5e",
        "evidence_ref": "20261107_081300_9439f8c/cost_breakdown.csv#STRESS + trades.csv",
        "net_pnl": 1764.35,
        "parameter_family": "sma_crossover_tsm_15m_5_20@0.1.0:fast=5,slow=20",
        "sample_period": {
          "end": "2024-06-29T00:00:00-04:00",
          "start": "2024-05-29T00:00:00-04:00",
          "timezone": "America/New_York"
        },
        "scenario_id": "STRESS",
        "source_run_id": "20261107_081300_9439f8c",
        "strategy_id": "sma_crossover_tsm_15m_5_20",
        "timeframe": "15m"
      },
      {
        "comparison_basis": "SAME_SIGNALS_AND_POSITIONS",
        "core_sha": "b16179a7cc077c36046574769a32c367fa476cae",
        "cost_multiplier": 1.001902671057446,
        "currency": "USD",
        "dataset_snapshot": "TSM_DEV_1M_20240529_20240628_18350b3adba8e28a7e5e",
        "evidence_ref": "20261107_081300_9439f8c/cost_breakdown.csv#CAT_STRESS + trades.csv",
        "net_pnl": 1844.0900000000001,
        "parameter_family": "sma_crossover_tsm_15m_5_20@0.1.0:fast=5,slow=20",
        "sample_period": {
          "end": "2024-06-29T00:00:00-04:00",
          "start": "2024-05-29T00:00:00-04:00",
          "timezone": "America/New_York"
        },
        "scenario_id": "CAT_STRESS",
        "source_run_id": "20261107_081300_9439f8c",
        "strategy_id": "sma_crossover_tsm_15m_5_20",
        "timeframe": "15m"
      }
    ],
    "group": "cost_sensitivity",
    "reason": "required evidence absent",
    "status": "AVAILABLE"
  },
  {
    "dimension": "robustness",
    "group": "parameter_stability",
    "reason": "required evidence absent",
    "status": "NOT_TESTED"
  },
  {
    "dimension": "robustness",
    "group": "temporal_stability",
    "reason": "required evidence absent",
    "status": "NOT_TESTED"
  },
  {
    "dimension": "robustness",
    "group": "concentration",
    "reason": "trade/year/symbol contribution flags; no new cutoff",
    "status": "AVAILABLE"
  },
  {
    "dimension": "robustness",
    "group": "multiple_testing",
    "reason": "required evidence absent",
    "status": "NOT_TESTED"
  },
  {
    "dimension": "robustness",
    "group": "selection_bias",
    "reason": "required evidence absent",
    "status": "NOT_TESTED"
  },
  {
    "dimension": "robustness",
    "group": "survivorship_bias",
    "reason": "required evidence absent",
    "status": "NOT_TESTED"
  },
  {
    "dimension": "robustness",
    "evidence": {
      "observed_fill_timing": "PASS"
    },
    "group": "look_ahead",
    "reason": "observed fills strictly after signal availability; signal calculation itself not verified",
    "status": "NOT_TESTED"
  }
]
```

### research_decision

```json
{
  "reasons": [
    "INSUFFICIENT_SAMPLE; evidence sufficiency UNDETERMINED"
  ],
  "verdict": "RETEST"
}
```

## Metrics

```json
{
  "annualized_return": {
    "reason": "needs >=365.2425 elapsed days and explicit zero external cash flows",
    "status": "INSUFFICIENT_SAMPLE"
  },
  "average_loss": {
    "authority": "UPSTREAM_METRIC",
    "definition_ref": "RESEARCH_EVALUATOR_INPUT_SCHEMA.json#/$defs/metric_definitions/trade_statistics",
    "status": "AVAILABLE",
    "value": -125.07857142857142,
    "verification": "MATCH"
  },
  "average_win": {
    "authority": "UPSTREAM_METRIC",
    "definition_ref": "RESEARCH_EVALUATOR_INPUT_SCHEMA.json#/$defs/metric_definitions/trade_statistics",
    "status": "AVAILABLE",
    "value": 453.31666666666666,
    "verification": "MATCH"
  },
  "cost_to_gross_profit": {
    "authority": "AGGREGATE_OF_CORE_RECORDS",
    "definition_ref": "RESEARCH_EVALUATOR_INPUT_SCHEMA.json#/$defs/metric_definitions/cost_to_gross_profit",
    "status": "AVAILABLE",
    "value": 0.011937842778793418
  },
  "drawdown_duration_seconds": {
    "authority": "AGGREGATE_OF_CORE_RECORDS",
    "definition_ref": "RESEARCH_EVALUATOR_INPUT_SCHEMA.json#/$defs/metric_definitions/max_drawdown",
    "status": "AVAILABLE",
    "value": 897300.0
  },
  "expectancy": {
    "authority": "UPSTREAM_METRIC",
    "definition_ref": "RESEARCH_EVALUATOR_INPUT_SCHEMA.json#/$defs/metric_definitions/trade_statistics",
    "status": "AVAILABLE",
    "value": 141.87307692307692,
    "verification": "MATCH"
  },
  "exposure": {
    "authority": "UPSTREAM_EXECUTION_DIAGNOSTIC",
    "definition_ref": "RESEARCH_EVALUATOR_INPUT_SCHEMA.json#/$defs/metric_definitions/exposure",
    "status": "AVAILABLE",
    "value": 0.33735795454545453
  },
  "holding_period_seconds": {
    "authority": "AGGREGATE_OF_CORE_RECORDS",
    "definition_ref": "RESEARCH_EVALUATOR_INPUT_SCHEMA.json#/$defs/metric_definitions/holding_period",
    "status": "AVAILABLE",
    "value": {
      "max": 344700.0,
      "mean": 61684.61538461538,
      "min": 5400.0
    }
  },
  "max_drawdown": {
    "authority": "UPSTREAM_METRIC",
    "definition_ref": "RESEARCH_EVALUATOR_INPUT_SCHEMA.json#/$defs/metric_definitions/max_drawdown",
    "status": "AVAILABLE",
    "value": -0.00782950773686697,
    "verification": "MATCH"
  },
  "net_pnl": {
    "authority": "UPSTREAM_METRIC",
    "definition_ref": "RESEARCH_EVALUATOR_INPUT_SCHEMA.json#/$defs/metric_definitions/trade_statistics",
    "status": "AVAILABLE",
    "value": 1844.35,
    "verification": "MATCH"
  },
  "net_return": {
    "authority": "UPSTREAM_METRIC",
    "definition_ref": "RESEARCH_EVALUATOR_INPUT_SCHEMA.json#/$defs/metric_definitions/net_return",
    "status": "AVAILABLE",
    "value": 0.018443499999999998,
    "verification": "MATCH"
  },
  "payoff_ratio": {
    "authority": "UPSTREAM_METRIC",
    "definition_ref": "RESEARCH_EVALUATOR_INPUT_SCHEMA.json#/$defs/metric_definitions/trade_statistics",
    "status": "AVAILABLE",
    "value": 3.624255230045876,
    "verification": "MATCH"
  },
  "profit_factor": {
    "authority": "UPSTREAM_METRIC",
    "definition_ref": "RESEARCH_EVALUATOR_INPUT_SCHEMA.json#/$defs/metric_definitions/trade_statistics",
    "status": "AVAILABLE",
    "value": 3.106504482896465,
    "verification": "MATCH"
  },
  "rejection_rate": {
    "reason": "required observations or context not supplied",
    "status": "NOT_AVAILABLE"
  },
  "tail_loss": {
    "reason": "irregular or unknown equity intervals; no assumed daily frequency",
    "status": "NOT_AVAILABLE"
  },
  "total_return": {
    "authority": "UPSTREAM_METRIC",
    "definition_ref": "RESEARCH_EVALUATOR_INPUT_SCHEMA.json#/$defs/metric_definitions/total_return",
    "status": "AVAILABLE",
    "value": 0.01844349999999939,
    "verification": "MATCH"
  },
  "trade_count": {
    "authority": "UPSTREAM_METRIC",
    "definition_ref": "RESEARCH_EVALUATOR_INPUT_SCHEMA.json#/$defs/metric_definitions/trade_statistics",
    "status": "AVAILABLE",
    "value": 13,
    "verification": "MATCH"
  },
  "turnover": {
    "reason": "required observations or context not supplied",
    "status": "NOT_AVAILABLE"
  },
  "volatility": {
    "reason": "irregular or unknown equity intervals; no assumed daily frequency",
    "status": "NOT_AVAILABLE"
  },
  "win_rate": {
    "authority": "UPSTREAM_METRIC",
    "definition_ref": "RESEARCH_EVALUATOR_INPUT_SCHEMA.json#/$defs/metric_definitions/trade_statistics",
    "status": "AVAILABLE",
    "value": 0.46153846153846156,
    "verification": "MATCH"
  },
  "worst_trade": {
    "authority": "AGGREGATE_OF_CORE_RECORDS",
    "definition_ref": "RESEARCH_EVALUATOR_INPUT_SCHEMA.json#/$defs/metric_definitions/trade_statistics",
    "status": "AVAILABLE",
    "value": -216.49
  }
}
```

## Errors and opponent flags

```json
{
  "errors": [],
  "flags": [
    {
      "code": "BLINDNESS_UNVERIFIED",
      "evidence": {
        "lock_id": "HOLDOUT-TSM-15M-v1",
        "meaning": "holdout period was touched before the lock; any later holdout result is not a blind test"
      },
      "severity": "REVIEW"
    },
    {
      "code": "INSUFFICIENT_SAMPLE",
      "evidence": {
        "effective_net_pnl": 1844.35,
        "invalidated_abs_pnl_share": 0.0,
        "invalidated_fraction": 0.0,
        "invalidated_net_pnl_for_reference": 0,
        "invalidated_trades": 0,
        "reasons": [
          {
            "code": "Q5-1",
            "message": "valid trades 13 < 30 after invalidation"
          }
        ],
        "status": "INSUFFICIENT",
        "valid_trades": 13
      },
      "severity": "REVIEW"
    },
    {
      "code": "IN_SAMPLE_VS_OUT_OF_SAMPLE_NOT_TESTED",
      "evidence": {
        "dimension": "robustness",
        "group": "in_sample_vs_out_of_sample",
        "reason": "required evidence absent",
        "status": "NOT_TESTED"
      },
      "severity": "REVIEW"
    },
    {
      "code": "LESS_THAN_ONE_YEAR_OBSERVED",
      "evidence": {
        "elapsed_days": 31.0
      },
      "severity": "REVIEW"
    },
    {
      "code": "LOOK_AHEAD_NOT_TESTED",
      "evidence": {
        "dimension": "robustness",
        "evidence": {
          "observed_fill_timing": "PASS"
        },
        "group": "look_ahead",
        "reason": "observed fills strictly after signal availability; signal calculation itself not verified",
        "status": "NOT_TESTED"
      },
      "severity": "REVIEW"
    },
    {
      "code": "MARKET_REGIME_COVERAGE_NOT_ESTABLISHED",
      "evidence": {
        "reason": "no pre-registered regime labels; regime coverage cannot be assessed"
      },
      "severity": "REVIEW"
    },
    {
      "code": "MULTIPLE_TESTING_NOT_TESTED",
      "evidence": {
        "dimension": "robustness",
        "group": "multiple_testing",
        "reason": "required evidence absent",
        "status": "NOT_TESTED"
      },
      "severity": "REVIEW"
    },
    {
      "code": "NET_PROFIT_COVERED_BY_TOP_TRADES",
      "evidence": {
        "meaning": "smallest k whose combined net PnL is at least the total; descriptive, no threshold",
        "remaining_net_pnl": -1.6200000000000223,
        "top_k": 2,
        "top_k_net_pnl": 1845.97,
        "total_net_pnl": 1844.35,
        "valid_trades": 13
      },
      "severity": "REVIEW"
    },
    {
      "code": "PARAMETER_STABILITY_NOT_TESTED",
      "evidence": {
        "dimension": "robustness",
        "group": "parameter_stability",
        "reason": "required evidence absent",
        "status": "NOT_TESTED"
      },
      "severity": "REVIEW"
    },
    {
      "code": "SELECTION_BIAS_NOT_TESTED",
      "evidence": {
        "dimension": "robustness",
        "group": "selection_bias",
        "reason": "required evidence absent",
        "status": "NOT_TESTED"
      },
      "severity": "REVIEW"
    },
    {
      "code": "SINGLE_CALENDAR_YEAR_COVERS_ALL_NET_PROFIT",
      "evidence": {
        "dimension": "calendar_year",
        "group": "2024",
        "net_pnl": 1844.35,
        "status": "INSUFFICIENT_SAMPLE",
        "trade_count": 13
      },
      "severity": "REVIEW"
    },
    {
      "code": "SINGLE_SYMBOL_COVERS_ALL_NET_PROFIT",
      "evidence": {
        "dimension": "symbol",
        "group": "TSM",
        "net_pnl": 1844.35,
        "status": "INSUFFICIENT_SAMPLE",
        "trade_count": 13
      },
      "severity": "REVIEW"
    },
    {
      "code": "SURVIVORSHIP_BIAS_NOT_TESTED",
      "evidence": {
        "dimension": "robustness",
        "group": "survivorship_bias",
        "reason": "required evidence absent",
        "status": "NOT_TESTED"
      },
      "severity": "REVIEW"
    },
    {
      "code": "TAIL_RISK_NOT_ESTABLISHED",
      "evidence": {
        "reason": "irregular or unknown equity intervals; no assumed daily frequency",
        "status": "NOT_AVAILABLE"
      },
      "severity": "REVIEW"
    },
    {
      "code": "TEMPORAL_STABILITY_NOT_TESTED",
      "evidence": {
        "dimension": "robustness",
        "group": "temporal_stability",
        "reason": "required evidence absent",
        "status": "NOT_TESTED"
      },
      "severity": "REVIEW"
    }
  ]
}
```

Evaluator batch consumption: 0. No registry updates.
