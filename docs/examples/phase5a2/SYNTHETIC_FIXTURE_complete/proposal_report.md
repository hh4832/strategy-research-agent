# Research Proposal — NOT_VERIFIED

Proposal: PROP-a976b02d8853385eaa2f
Status: PROPOSED
Review: NEEDS_REVIEW
Origin: SYNTHETIC_FIXTURE
Commit: 48771ff0202be05a1be39e680a9e9985bff84dd1
SHA256: 6a958260722ce9a104ff3ac21e8807c3eeefb5dff16dcfb3ec8673a30c5435eb

No authorization, execution, performance or strategy approval is asserted.

## hypothesis

```json
{
  "expected_direction": "CONDITIONAL",
  "null_hypothesis": "Changing fast length produces no improvement in net expectancy relative to the unchanged baseline.",
  "purpose": "Unverified research design, not a performance or execution claim.",
  "question": "Does changing only fast SMA10 to SMA5 improve the unchanged SMA10/20 baseline?",
  "testable_claim": "Faster trend detection may improve net expectancy relative to the unchanged baseline; no performance has been measured."
}
```

## market_mechanism

```json
{
  "category": "MOMENTUM",
  "causal_logic": "Institutional order splitting may sustain short trends; reacting earlier could capture a different portion of those trends, but whipsaw and costs can offset it.",
  "failure_modes": [
    "Range-bound markets and whipsaw.",
    "Transaction costs and overnight gaps."
  ]
}
```

## strategy_rules

```json
{
  "entry_condition": "Enter long when completed-bar SMA5 crosses above SMA20 while flat.",
  "execution_time": "STRICTLY_AFTER_SIGNAL_AVAILABILITY",
  "exit_condition": "Exit long when completed-bar SMA5 crosses below SMA20.",
  "exit_policy": "No additional stop or take-profit; exit on opposite crossover. Overnight gap risk requires human review.",
  "holding_horizon": "Hours to several sessions.",
  "position_sizing": "FIXED_UNITS 1000 shares; initial capital 1000000 TWD.",
  "signal_time": "BAR_AVAILABLE_AT",
  "universe": {
    "market": "TW",
    "symbols": [
      "2330"
    ],
    "timeframe": "5m"
  }
}
```

## required_data

```json
{
  "additional_features": [],
  "availability": "DECLARED_AVAILABLE_NOT_VERIFIED",
  "coverage": {
    "end": "2024-12-31",
    "start": "2024-01-01"
  },
  "market": "TW",
  "pit_requirements": [
    "Use only fully completed bars and original timestamps.",
    "Never access Holdout; research design is Development-only."
  ],
  "pit_timing": "Signal at completed-bar available_at; fill strictly later on an eligible bar; source latency is NOT_VERIFIED.",
  "price_fields": [
    "open",
    "high",
    "low",
    "close",
    "volume"
  ],
  "source_ref": "SYNTHETIC_FIXTURE_catalog_NOT_VERIFIED",
  "symbols": [
    "2330"
  ],
  "timeframe": "5m"
}
```

## research_variables

```json
{
  "changes": [
    {
      "baseline": "SMA10/20",
      "baseline_value": 10,
      "core_variable": "fast SMA length",
      "expected_improvement": "Earlier response may change net expectancy, NOT_VERIFIED.",
      "parameter_name": "fast",
      "potential_side_effects": "More whipsaw, turnover and transaction cost.",
      "proposed_change": "SMA5/20",
      "proposed_value": 5
    }
  ],
  "parameter_search": {
    "max_trials": 1,
    "mode": "FIXED"
  },
  "prior_experiment_refs": [
    "SYNTHETIC_FIXTURE_baseline_no_real_experiment"
  ],
  "selection_basis": "HYPOTHESIS_DRIVEN"
}
```

## Evaluation plan

{"metrics": ["Trade Count", "Win Rate", "Payoff Ratio", "Expectancy", "Profit Factor", "Maximum Drawdown", "Tail Risk", "Cost Sensitivity", "Market Regime", "Annual Performance", "Symbol Concentration", "Holding Duration", "Parameter Robustness"], "protocol": "QUANT_EVALUATION_PROTOCOL v1.0", "threshold_policy": "USE_EXISTING_PROTOCOL_ONLY; NO_NEW_THRESHOLDS"}

## Deterministic validation

```json
{
  "blockers": [],
  "risk_flags": [
    {
      "code": "DATA_AND_CAUSAL_MECHANISM_NOT_VERIFIED",
      "message": "Design assertions are not data approval or evidence of strategy effect."
    }
  ],
  "schema_valid": true
}
```

Request contract is pinned. Export requires explicit existing-artefact bindings; human approval remains pending. Full executable Strategy Spec not materialized.
