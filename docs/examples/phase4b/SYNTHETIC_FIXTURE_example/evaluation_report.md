# Research evaluation

- source_run_id: SYNTHETIC_FIXTURE_positive_short_run
- source_archive_path: SYNTHETIC_FIXTURE_NO_REAL_ARCHIVE
- source_commit_sha: 1111111111111111111111111111111111111111
- evaluator_commit_sha: 9258a48f58a18a502e52a13b2bdf1137485bb0e2
- evaluation_timestamp: 2026-11-06T23:00:26.634111+08:00
- schema_version: 1.0.0
- evaluator_version: 1.0.0

Engineering: **PASS**
Strategy Verdict: **RETEST**
Evidence Sufficiency: **DESCRIPTIVE_ONLY**

RETEST is not evidence of a positive effect. KEEP is research retention, never permission to trade.
SYNTHETIC_FIXTURE outputs are engineering evidence only.

## Independent layers

### data_quality

```json
{
  "status": "AVAILABLE",
  "value": {
    "evidence_ref": "SYNTHETIC_FIXTURE_known_complete",
    "status": "PASS"
  }
}
```

### execution_quality

```json
{
  "status": "AVAILABLE",
  "value": {
    "exposure": 0.5,
    "lookahead_checked": true,
    "lookahead_violations": 0,
    "orders_submitted": 64,
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
    "effective_net_pnl": 80.0,
    "invalidated_abs_pnl_share": 0.0,
    "invalidated_fraction": 0.0,
    "invalidated_net_pnl_for_reference": 0,
    "invalidated_trades": 0,
    "reasons": [],
    "status": "SUFFICIENT",
    "valid_trades": 32
  }
}
```

### cost_sensitivity

```json
{
  "reason": "not supplied",
  "status": "NOT_TESTED"
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
    "group": "cost_sensitivity",
    "reason": "required evidence absent",
    "status": "NOT_TESTED"
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
    "group": "look_ahead",
    "reason": "upstream check reported; fill times also verified where supplied",
    "status": "AVAILABLE"
  }
]
```

### research_decision

```json
{
  "reasons": [
    "positive PnL alone is insufficient; complete protocol evidence or independent validation absent"
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
    "authority": "AGGREGATE_OF_CORE_RECORDS",
    "definition_ref": "RESEARCH_EVALUATOR_INPUT_SCHEMA.json#/$defs/metric_definitions/trade_statistics",
    "status": "AVAILABLE",
    "value": -5.0
  },
  "average_win": {
    "authority": "AGGREGATE_OF_CORE_RECORDS",
    "definition_ref": "RESEARCH_EVALUATOR_INPUT_SCHEMA.json#/$defs/metric_definitions/trade_statistics",
    "status": "AVAILABLE",
    "value": 10.0
  },
  "cost_to_gross_profit": {
    "authority": "AGGREGATE_OF_CORE_RECORDS",
    "definition_ref": "RESEARCH_EVALUATOR_INPUT_SCHEMA.json#/$defs/metric_definitions/cost_to_gross_profit",
    "status": "AVAILABLE",
    "value": 0.3333333333333333
  },
  "drawdown_duration_seconds": {
    "authority": "AGGREGATE_OF_CORE_RECORDS",
    "definition_ref": "RESEARCH_EVALUATOR_INPUT_SCHEMA.json#/$defs/metric_definitions/max_drawdown",
    "status": "AVAILABLE",
    "value": 172800.0
  },
  "expectancy": {
    "authority": "AGGREGATE_OF_CORE_RECORDS",
    "definition_ref": "RESEARCH_EVALUATOR_INPUT_SCHEMA.json#/$defs/metric_definitions/trade_statistics",
    "status": "AVAILABLE",
    "value": 2.5
  },
  "exposure": {
    "authority": "UPSTREAM_EXECUTION_DIAGNOSTIC",
    "definition_ref": "RESEARCH_EVALUATOR_INPUT_SCHEMA.json#/$defs/metric_definitions/exposure",
    "status": "AVAILABLE",
    "value": 0.5
  },
  "holding_period_seconds": {
    "authority": "AGGREGATE_OF_CORE_RECORDS",
    "definition_ref": "RESEARCH_EVALUATOR_INPUT_SCHEMA.json#/$defs/metric_definitions/holding_period",
    "status": "AVAILABLE",
    "value": {
      "max": 3600.0,
      "mean": 3600.0,
      "min": 3600.0
    }
  },
  "max_drawdown": {
    "authority": "AGGREGATE_OF_CORE_RECORDS",
    "definition_ref": "RESEARCH_EVALUATOR_INPUT_SCHEMA.json#/$defs/metric_definitions/max_drawdown",
    "status": "AVAILABLE",
    "value": -4.999500049995831e-05
  },
  "net_pnl": {
    "authority": "UPSTREAM_METRIC",
    "definition_ref": "RESEARCH_EVALUATOR_INPUT_SCHEMA.json#/$defs/metric_definitions/trade_statistics",
    "status": "AVAILABLE",
    "value": 80.0,
    "verification": "MATCH"
  },
  "net_return": {
    "authority": "UPSTREAM_METRIC",
    "definition_ref": "RESEARCH_EVALUATOR_INPUT_SCHEMA.json#/$defs/metric_definitions/net_return",
    "status": "AVAILABLE",
    "value": 0.0008,
    "verification": "MATCH"
  },
  "payoff_ratio": {
    "authority": "AGGREGATE_OF_CORE_RECORDS",
    "definition_ref": "RESEARCH_EVALUATOR_INPUT_SCHEMA.json#/$defs/metric_definitions/trade_statistics",
    "status": "AVAILABLE",
    "value": 2.0
  },
  "profit_factor": {
    "authority": "AGGREGATE_OF_CORE_RECORDS",
    "definition_ref": "RESEARCH_EVALUATOR_INPUT_SCHEMA.json#/$defs/metric_definitions/trade_statistics",
    "status": "AVAILABLE",
    "value": 2.0
  },
  "rejection_rate": {
    "authority": "AGGREGATE_OF_CORE_RECORDS",
    "definition_ref": "RESEARCH_EVALUATOR_INPUT_SCHEMA.json#/$defs/metric_definitions/rejection_rate",
    "status": "AVAILABLE",
    "value": 0.0
  },
  "tail_loss": {
    "reason": "irregular or unknown equity intervals; no assumed daily frequency",
    "status": "NOT_AVAILABLE"
  },
  "total_return": {
    "authority": "UPSTREAM_METRIC",
    "definition_ref": "RESEARCH_EVALUATOR_INPUT_SCHEMA.json#/$defs/metric_definitions/total_return",
    "status": "AVAILABLE",
    "value": 0.0007999999999999119,
    "verification": "MATCH"
  },
  "trade_count": {
    "authority": "UPSTREAM_METRIC",
    "definition_ref": "RESEARCH_EVALUATOR_INPUT_SCHEMA.json#/$defs/metric_definitions/trade_statistics",
    "status": "AVAILABLE",
    "value": 32,
    "verification": "MATCH"
  },
  "turnover": {
    "authority": "AGGREGATE_OF_CORE_RECORDS",
    "definition_ref": "RESEARCH_EVALUATOR_INPUT_SCHEMA.json#/$defs/metric_definitions/turnover",
    "status": "AVAILABLE",
    "value": 0.319860424542018
  },
  "volatility": {
    "reason": "irregular or unknown equity intervals; no assumed daily frequency",
    "status": "NOT_AVAILABLE"
  },
  "win_rate": {
    "authority": "AGGREGATE_OF_CORE_RECORDS",
    "definition_ref": "RESEARCH_EVALUATOR_INPUT_SCHEMA.json#/$defs/metric_definitions/trade_statistics",
    "status": "AVAILABLE",
    "value": 0.5
  },
  "worst_trade": {
    "authority": "AGGREGATE_OF_CORE_RECORDS",
    "definition_ref": "RESEARCH_EVALUATOR_INPUT_SCHEMA.json#/$defs/metric_definitions/trade_statistics",
    "status": "AVAILABLE",
    "value": -5
  }
}
```

## Errors and opponent flags

```json
{
  "errors": [],
  "flags": [
    {
      "code": "COST_SENSITIVITY_NOT_TESTED",
      "evidence": {
        "dimension": "robustness",
        "group": "cost_sensitivity",
        "reason": "required evidence absent",
        "status": "NOT_TESTED"
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
        "elapsed_days": 35.0
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
        "net_pnl": 80.0,
        "status": "AVAILABLE",
        "trade_count": 32
      },
      "severity": "REVIEW"
    },
    {
      "code": "SINGLE_SYMBOL_COVERS_ALL_NET_PROFIT",
      "evidence": {
        "dimension": "symbol",
        "group": "TSM",
        "net_pnl": 80.0,
        "status": "AVAILABLE",
        "trade_count": 32
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
      "code": "SYNTHETIC_FIXTURE",
      "evidence": {
        "meaning": "engineering test only; no real strategy conclusion"
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
