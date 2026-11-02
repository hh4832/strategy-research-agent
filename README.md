# strategy-research-agent

Phase 3 準備：自動量化策略研究 Agent 的架構、可驗證的策略規格、**獨立於回測引擎的 Benchmark Oracle**，以及防 data snooping 的評估流程。

> 本 repo 不 import、不修改 `market-research-core`，不依賴尚未確定的 Phase 2 API，不包含任何下單功能。

## 交付文件

| 文件 | 內容 |
|---|---|
| [`STRATEGY_AGENT_ARCHITECTURE.md`](STRATEGY_AGENT_ARCHITECTURE.md) | 七元件架構、研究／回測／交易信任區域、整合契約 C1–C8 |
| [`STRATEGY_SPEC_SCHEMA.json`](STRATEGY_SPEC_SCHEMA.json) | 機器可驗證的策略規格；範例在 [`specs/examples/`](specs/examples) |
| [`BENCHMARK_TEST_CASES.md`](BENCHMARK_TEST_CASES.md) | Execution Convention v1 與 12 個手算案例 |
| [`QUANT_EVALUATION_PROTOCOL.md`](QUANT_EVALUATION_PROTOCOL.md) | 分區、walk-forward、purge／embargo、DSR、多重檢定、Gate |
| [`PHASE3_IMPLEMENTATION_PLAN.md`](PHASE3_IMPLEMENTATION_PLAN.md) | 里程碑、Phase 2 驗收清單、介面風險與待確認事項、審查決策 |
| [`M2_GOVERNANCE.md`](M2_GOVERNANCE.md) | M2：Registry、holdout lock、family、budget、訊號純函式；GO／NO-GO |

## 快速開始

```bash
pip install -e '.[test]'
pytest -q                                                   # 142 tests
python -m strategy_agent.spec.validate specs/examples/*.json
python -m benchmark.harness.crosscheck export out/csv       # Phase 2 交叉驗收用輸入
```

## 結構

```
benchmark/cases/        B01–B12 合成資料 + 手算預期值
benchmark/oracle/       獨立參考引擎（stdlib + Decimal）
benchmark/harness/      匯出 CSV、比對外部引擎輸出
src/strategy_agent/     spec、evaluation、ports、registry、governance、family、signals、runner
governance/             holdout locks、approvers、research budget、controls、cost profiles、source confirmations
registry/               experiments.jsonl、families.jsonl（雜湊鏈、append-only）
schemas/                Experiment Registry 紀錄 schema
tests/                  oracle／mutants／harness／spec／evaluation／ports
```
