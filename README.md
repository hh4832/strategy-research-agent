# strategy-research-agent

Phase 3 準備：自動量化策略研究 Agent 的架構、可驗證的策略規格、**獨立於回測引擎的 Benchmark Oracle**，以及防 data snooping 的評估流程。

> 本 repo 不修改 `market-research-core`；只有 `src/strategy_agent/adapters/mrc_adapter.py` import 它（固定 SHA）。不包含任何下單功能。

## 交付文件

| 文件 | 內容 |
|---|---|
| [`STRATEGY_AGENT_ARCHITECTURE.md`](STRATEGY_AGENT_ARCHITECTURE.md) | 七元件架構、研究／回測／交易信任區域、整合契約 C1–C8 |
| [`STRATEGY_SPEC_SCHEMA.json`](STRATEGY_SPEC_SCHEMA.json) | 機器可驗證的策略規格；範例在 [`specs/examples/`](specs/examples) |
| [`BENCHMARK_TEST_CASES.md`](BENCHMARK_TEST_CASES.md) | Execution Convention v1 與 12 個手算案例 |
| [`QUANT_EVALUATION_PROTOCOL.md`](QUANT_EVALUATION_PROTOCOL.md) | 分區、walk-forward、purge／embargo、DSR、多重檢定、Gate |
| [`PHASE3_IMPLEMENTATION_PLAN.md`](PHASE3_IMPLEMENTATION_PLAN.md) | 里程碑、Phase 2 驗收清單、介面風險與待確認事項、審查決策 |
| [`M2_GOVERNANCE.md`](M2_GOVERNANCE.md) | M2：Registry、holdout lock、family、budget、訊號純函式；GO／NO-GO |
| [`M1_INTEGRATION.md`](M1_INTEGRATION.md) | M1：market-research-core adapter、B01–B12／G16／G17／G14b 驗收 |
| [`M3B_RESEARCH_RUNNER.md`](M3B_RESEARCH_RUNNER.md) | M3-B：自動化研究 Runner（SMA 5/20 固定基準）、績效評估、研究報告、封存、市場 Gate |

## 快速開始

```bash
pip install -e '.[test]'
pytest -q                                                   # 142 tests
python -m strategy_agent.spec.validate specs/examples/*.json
python -m benchmark.harness.crosscheck export out/csv       # Phase 2 交叉驗收用輸入
```

## M3-B 研究 Runner

```bash
pip install -e '.[test,research]' \
  "market-research-core @ git+https://github.com/hh4832/market-research-core@2c6ba9ab7c336e7a4908744abef617e0dce713fd"
python -m strategy_agent.research readiness --market IBKR_TSM       # 真實市場 Gate（M3-A 未 GO 時 exit 3）
python -m strategy_agent.research synthetic --run-root /tmp/m3b_env --archive-root /tmp/m3b_archive --repeat 2
python -m strategy_agent.research synthetic --run-root /tmp/m3b_env --drive        # 封存到 MyDrive（Colab）
```

Colab：`notebooks/M3B_Research_Runner.ipynb`（Run All；只呼叫上面的 CLI，不含策略或回測邏輯）。

## 結構

```
benchmark/cases/        B01–B12 合成資料 + 手算預期值
benchmark/oracle/       獨立參考引擎（stdlib + Decimal）
benchmark/harness/      匯出 CSV、比對外部引擎輸出
src/strategy_agent/     spec、evaluation、ports、registry、governance、family、signals、runner、adapters（M1）、research（M3-B）
fixtures/synthetic/     M3-B 合成 15m 測試資料（seeded，非市場資料）
specs/research/         M3-B 固定基準規格（SMA 5/20 TSM 15m，DRAFT）
notebooks/              Colab Run All runner
governance/             holdout locks、approvers、research budget、controls、cost profiles、source confirmations
registry/               experiments.jsonl、families.jsonl（雜湊鏈、append-only）
schemas/                Experiment Registry 紀錄 schema
tests/                  oracle／mutants／harness／spec／evaluation／ports
```

## M3-B.2 configuration and Core handoff

Use [`schemas/RESEARCH_CONFIG_SCHEMA.json`](schemas/RESEARCH_CONFIG_SCHEMA.json) and
[`configs/research/synthetic_sma_v1.json`](configs/research/synthetic_sma_v1.json) for an explicit,
SHA-pinned synthetic run:

```bash
python -m strategy_agent.research synthetic --run-root /tmp/m3b2-env \
  --config configs/research/synthetic_sma_v1.json --batch-id review
```

The example archives locally under `/tmp/m3b2_config_archive`; this is not Drive evidence.
Configuration changes require a new version and SHA. See
[`M3_CORE_AGENT_INTEGRATION.md`](M3_CORE_AGENT_INTEGRATION.md) for schema semantics,
CUSTOM cost profile restrictions, unchanged Core pin and pending minimal integration.
[`docs/M3B2_HANDOFF.md`](docs/M3B2_HANDOFF.md) distinguishes synthetic engineering results,
Colab/Drive PENDING, and real TSM NO-GO.
