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

已執行的工程範例：[`SYNTHETIC_FIXTURE evaluation_report.md`](docs/examples/phase4b/SYNTHETIC_FIXTURE_example/evaluation_report.md)；
輸出 Schema：[`RESEARCH_EVALUATION_SCHEMA.json`](schemas/RESEARCH_EVALUATION_SCHEMA.json)；
驗收範圍：[`PHASE4B_ACCEPTANCE.md`](docs/PHASE4B_ACCEPTANCE.md)。

## 開發狀態（Phase 5-A）

| 階段 | 內容 | 狀態 |
|---|---|---|
| MVP v1 | Research-only 工程版本（Phase 4-A smoke + 4-C 評估 + 發行驗收） | `MVP_V1_ENGINEERING_GO`（已併入 main） |
| 5-A1 | Research Orchestrator：狀態機、治理 G1–G10、Archive Replay／Synthetic adapter、評估、封存、CLI（[文件](docs/PHASE5A1_ORCHESTRATOR.md)） | PR #9（Draft，SCOPED_GO） |
| 5-A2 | Research Proposal Agent：自然語言 → Proposal → 共用 Request 匯出（[文件](docs/PHASE5A2_PROPOSAL_AGENT.md)） | PR #10（Draft，Codex） |
| 5-A3 | Proposal × Orchestrator 端到端整合 A3-01..A3-10（[文件](docs/PHASE5A3_INTEGRATION_ACCEPTANCE.md)） | GO，已併入 main（PR #11） |
| 5-B1 | 可信人工核准（GitHub 留言證據）× 受治理授權綁定 B1-01..B1-10（[驗收](docs/PHASE5B1_ACCEPTANCE.md)、[威脅模型](docs/PHASE5B1_APPROVAL_THREAT_MODEL.md)） | 已併入 main（PR #12） |
| 5-B2 | 受控研究執行 × 原子額度帳本 B2-01..B2-10（[設計](docs/PHASE5B2_CONTROLLED_EXECUTION.md)、[驗收](docs/PHASE5B2_ACCEPTANCE.md)）；正式 RESEARCH_ONLY_REAL 仍 DISABLED，SINGLE_EXECUTOR_ONLY | `feature/phase5b2-controlled-execution`（Draft PR） |

共用契約：[`schemas/RESEARCH_REQUEST_SCHEMA.json`](schemas/RESEARCH_REQUEST_SCHEMA.json) v1.0.0（commit `5d088c4`，sha256 `1b0530f3…`）。

```bash
python -m strategy_agent.phase5a validate        --proposal P.json
python -m strategy_agent.phase5a submit-proposal --proposal P.json --bindings B.json --export R.json \
    --artifact-root DIR --state-dir D --archive-root A --actor ID   # A2 request 一律 authorization_ref=PENDING → BLOCKED
python -m strategy_agent.phase5a status          --task-id T --state-dir D --archive-root A
python -m strategy_agent.phase5a replay          --task-id T --state-dir D --archive-root A --source-archive-root S
python -m strategy_agent.phase5a acceptance      --out-dir OUT                     # A3-01..A3-10（SYNTHETIC_FIXTURE）
```

5-B1 起，正式核准只能透過 `python -m strategy_agent.orchestration approve --evidence-ref <GitHub 留言 URL>`：
身分取自 GitHub API 的作者（login + 數字 id），經 GitHub App（AI agent）發出的留言一律拒絕。
授權綁定：`python -m strategy_agent.binding approval-line | verify-approval | bind-authorization（預設 dry-run）| validate-bound-request | inspect-binding`。
真實 Research-only Runner 維持 DISABLED，不讀 Holdout，不含任何券商 API。
