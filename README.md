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
| [`M3_CORE_AGENT_INTEGRATION.md`](M3_CORE_AGENT_INTEGRATION.md) | M3-B.2：Core ↔ Agent 交接規約、六道市場 Gate、fail-closed、M3-A.4 後的最小整合測試 |

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
  "market-research-core @ git+https://github.com/hh4832/market-research-core@699e3ad59951bbebb769bb69170be8d9a1a96c01"
python -m strategy_agent.research readiness --market IBKR_TSM       # 真實市場 Gate（M3-A 未 GO 時 exit 3）
python -m strategy_agent.research synthetic --run-root /tmp/m3b_env --archive-root /tmp/m3b_archive --repeat 2
python -m strategy_agent.research synthetic --run-root /tmp/m3b_env --drive        # 封存到 MyDrive（Colab）
# M3-B.2：以審核過的研究設定執行（2 個期間 × 4 個成本情境；唯一的設定入口）
python -m strategy_agent.research run --config configs/research/sma_5_20_tsm_synthetic_local_v1.json \
  --run-root /tmp/m3b_env --archive-root /tmp/m3b_archive
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
configs/research/       M3-B.2 研究設定（schemas/RESEARCH_CONFIG_SCHEMA.json）
configs/cost_scenarios/ M3-B.2 成本情境（schemas/COST_SCENARIO_SET_SCHEMA.json）
notebooks/              Colab Run All runner
governance/             holdout locks、approvers、research budget、controls、cost profiles、source confirmations
registry/               experiments.jsonl、families.jsonl（雜湊鏈、append-only）
schemas/                Experiment Registry 紀錄 schema
tests/                  oracle／mutants／harness／spec／evaluation／ports
```

## Phase 4-B 共用 Research Evaluator

獨立、唯讀的結果評估入口；不啟動回測、不讀原始 OHLCV／Holdout、不消耗授權。
沿用既有 trade statistics、Q5 與 G1–G11；工程狀態和策略結論分開。
必要證據缺失時為 `BLOCKED / RETEST / UNDETERMINED`，正報酬不等於 `KEEP`。

```bash
# 需 clean checkout；使用實際 Git HEAD 作為 Evaluator provenance。
python -m strategy_agent.evaluation \
  --input tests/fixtures/evaluator/positive_short_run.json \
  --output /tmp/new_synthetic_evaluation
pytest -q tests/test_universal_evaluator.py
```

輸入 Schema：[`RESEARCH_EVALUATOR_INPUT_SCHEMA.json`](schemas/RESEARCH_EVALUATOR_INPUT_SCHEMA.json)。
完整 API、指標定義、Phase 4-A Archive 缺失欄位與限制：
[`RESEARCH_EVALUATOR_CONTRACT.md`](docs/RESEARCH_EVALUATOR_CONTRACT.md)。
Synthetic 範例僅證明工程行為，不是真實策略研究結論。
