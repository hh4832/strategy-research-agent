# Phase 5-B3-A：第一次正式真實研究的 Production Readiness

| 項目 | 值 |
|---|---|
| 分支 | `feature/phase5b3a-production-readiness`（Draft PR，base `main`） |
| Baseline | `main` merge commit `8d4338833c041b32bb026814f94c04fee59aa2eb`（PR #13） |
| Core | `hh4832/market-research-core`，**未修改**；候選的 pin 為 `b16179a7cc077c36046574769a32c367fa476cae` |
| 範圍 | **只做 readiness 工程。** 沒有執行新的真實研究、沒有讀市場資料或 Holdout、沒有建立／核准／消耗 Research Authorization；真實 Runner 維持 DISABLED |
| 機器可讀 | `docs/phase5b3a/readiness_manifest.json`、`docs/phase5b3a/authorization_draft.json`（由 `python -m strategy_agent.readiness.acceptance --emit-docs` 產生，CI 檢查兩者與程式輸出逐位元相同） |
| 程式 | `src/strategy_agent/readiness/`（candidate、manifest、checks、authorization_draft、acceptance） |
| 驗收 | [`PHASE5B3A_ACCEPTANCE.md`](PHASE5B3A_ACCEPTANCE.md)、notebook `notebooks/Phase5B3A_Readiness_Acceptance.ipynb` |

## 0. 結論（工程判定）

**`NO_GO`：有一個具體的阻擋，必須由人做決定。** B3A-01、03、04、05、06、07 全部 PASS；B3A-08 需要 Colab + Drive 證據（見驗收文件）。

**唯一的工程阻擋（B3A-02）：** 策略規格 `specs/research/sma_crossover_tsm_15m_5_20.json` 的 `data_requirements` 綁定的是 **FINAL_GATE 母快照**（`20261101_205431_06359faf7ef4_IBKR_FINAL_GATE_FULL`，producer `06359faf…`）。候選資料集是由它衍生的 **DEV 子快照**（`TSM_DEV_1M_20240529_20240628_18350b3adba8e28a7e5e`，producer `b16179a…`）。

Phase 5 的 A2 exporter 是 request 與 spec 綁定的權威，它會以 `SPEC_DATASET_REFERENCE_MISMATCH` 拒絕這個 request。這是直接呼叫 exporter 實際得到的結果，不是推論。Phase 4-A 的 DEV smoke run 沒遇到這個問題，因為那條路徑（RA07）只比對 spec SHA，不檢查 spec 內的資料集綁定。

**需要的決定：** 是否核准一個新的 spec 版本。新版只把 `data_requirements` 重新綁到 DEV 快照，規則和參數都不變（SMA 5/20），SHA 會改變。測試 `test_a_spec_repinned_to_the_dev_snapshot_would_satisfy_the_exporter` 已證明這樣改就足以通過 exporter。新的授權必須釘選新 spec 的 SHA。B3-A **沒有**自行建立這個 spec。

GO 和 SCOPED_GO 都**不**授權 Runner；真實執行仍需另外一份由研究負責人核准的授權。

## 1. Repository 檢查

- remote `hh4832/strategy-research-agent`，從 `main` `8d43388` 開新分支，working tree 乾淨。
- **沒有 AGENTS.md**（Phase 4-A 以來一直如此）。
- 已讀契約：README、`RESEARCH_REQUEST_CONTRACT.md`、`TSM_FIRST_REAL_RESEARCH_ACCEPTANCE.md`（Phase 4-A）、`PHASE4C_ACCEPTANCE.md`、5-B1／5-B2 驗收與安全修補文件。
- 既有 ResearchOnlyRunner：`src/strategy_agent/research_only/runner.py`（`ResearchOnlyRunner`：`preflight`／`probe`／`run`）。Phase 5 的 backend 包裝在 `execution/controlled.py` 的 `ResearchOnlyRunnerBackend`。
- 授權／binding／額度機制：
  - `governance/research_authorization*.py`（RA01–RA12）
  - `binding/`（5-B1，正式 GitHub 核准證據）
  - `execution/budget.py`（5-B2 原子 ledger、SINGLE_EXECUTOR_ONLY）
  - `execution/controlled.py`（P0-1／2／3 修補後的受控執行）
- 權威的 dataset／Core pin：
  - `configs/research_only/tsm_sma_5_20_smoke_2024_06_dev_18350b3adba8e28a7e5e.json`
  - 已用盡的授權 `RA-IBKR_TSM-SMA520_DEV-18350b3adba8e28a7e5e`
  - 先前真實 run 的封存證據 `docs/evidence/phase4a_dev_smoke/20261107_081300_9439f8c/`
  - CI 的 Core pin
- **沒有建立新的引擎。**

## 2. 候選與 Production Readiness Manifest

候選（`readiness/candidate.py`）只列出**權威紀錄的位置**，不宣告任何值。Manifest（`readiness/manifest.py`）從這些紀錄讀值並交叉比對：每個欄位都是 `RESOLVED`（所有來源一致），或 `BLOCKED`（類別為 `MISSING`、`CONTRADICTION` 或 `HUMAN_DECISION`）。缺值或來源矛盾一律 BLOCKED，不推論、不給預設值；有測試注入矛盾與缺值。

| 欄位 | 狀態 | 值／理由 | 來源（全部一致才算 RESOLVED） |
|---|---|---|---|
| Research market | RESOLVED | IBKR_TSM / TSM | run config、授權、spec universe |
| Strategy ID / version | RESOLVED | `sma_crossover_tsm_15m_5_20` 0.1.0，fast 5／slow 20 | config、授權、spec 檔、先前 run |
| Strategy Spec SHA256 | RESOLVED | `b358416e…4123` | 磁碟檔案、config、授權 |
| Dataset snapshot ID | RESOLVED | `TSM_DEV_1M_20240529_20240628_18350b3adba8e28a7e5e` | config、授權、snapshot pin、先前 run |
| Dataset manifest SHA256 | RESOLVED | `8b6fc838…cfc5` | config、integrity receipt、先前 run |
| Core commit SHA | RESOLVED | `b16179a7…6cae` | config、授權的 producer、先前 run、CI pin |
| Signal / available_at / execution | RESOLVED | policy `CONSERVATIVE_LABEL_AGNOSTIC` `7fa3cdca…59c4`（RESEARCH_ONLY）；signal `BAR_AVAILABLE_AT`，fill `NEXT_ELIGIBLE_BAR_OPEN`，MARKET | config、授權、market readiness 的 research approval、先前 run |
| Research window | RESOLVED | warm-up 2024-05-29、交易 `[2024-06-03, 2024-06-29)` ET | config、授權、先前 run |
| Holdout exclusion | RESOLVED | `HOLDOUT-TSM-15M-v1` `[2025-01-01, 2026-10-17)` ET；窗口終點早於 Holdout；先前 run `holdout_accessed=false`；`BLINDNESS_UNVERIFIED`（永久標記） | 授權、holdout book、先前 run 證據 |
| Trading cost profile | RESOLVED | `IBKR_PRO_FIXED` 成本集 `bf4f2365…9cd3`（RESEARCH_ONLY；只涵蓋 2016-10-19..2024-12-31） | 磁碟檔案、config、授權 |
| Slippage | RESOLVED | BASE 2／LOW 1／STRESS 4／CAT_STRESS 2 bps 每邊，half-spread 0（研究假設，非帳戶驗證） | 成本集 |
| Authorization ID | **BLOCKED／HUMAN_DECISION** | 需要**新**授權；DEV 授權已用盡（1/1），不得重用或重置 | — |
| Bound Request Hash | **BLOCKED／HUMAN_DECISION** | 授權核准並以正式 GitHub 核准完成 binding 後才會存在 | — |
| Budget batch limit | **BLOCKED／HUMAN_DECISION** | 草案提議 `max_batches = 1` | — |
| Executor identity | **BLOCKED／HUMAN_DECISION** | 單一具名 executor（SINGLE_EXECUTOR_ONLY），由授權決定 | — |
| Git／環境 identity | RESOLVED（執行時取得） | 由 acceptance 記錄實際 git SHA、clean、Python；B3-B 執行時必須再記錄一次 | 程式碼 |
| Archive destination | RESOLVED | `MyDrive/Quant_Research/strategy-research-agent/`，`YYYYMMDD_HHMMSS_<git_commit>`，immutable | config、brief |

另外有兩項交叉檢查（`cross_checks`），呼叫的是真實的程式路徑：
- `phase5_request_spec_dataset_binding`：**BLOCKED**（§0）。
- `snapshot_integrity_receipt`：PASS。先前的 run 每個 session 都會更新 receipt（Core `renew_development_receipt`）；B3-B 必須再更新一次，而且 session config 只能在 receipt 欄位上與正式 config 不同。

## 3. 真實資料集 readiness（9 項；使用先前真實 run 的封存證據，不讀新資料）

| # | 需求 | 結果 | 依據 |
|---|---|---|---|
| D1 | Snapshot 存在且不可變 | PASS（證據背書） | Core development-snapshot receipt（`REUSED_TRUSTED_SHA256_RECEIPT_WITH_STAT_GUARD`）、lineage `3455f2b3…` 與 pin 相同；Colab 可選擇對 Drive 上的兩個檔案重新做整檔 SHA-256 |
| D2 | Manifest 與資料集相符 | PASS | loader provenance 的 manifest／snapshot 與 pin 相同 |
| D3 | 時間戳與 available_at 符合 PIT | PASS | 四個情境的 M3-A.4 P03／P04／P07／T01_no_lookahead 皆 PASS；policy SHA 相符 |
| D4 | 訊號到成交保守 | PASS | 每筆成交都嚴格晚於訊號可用時間（0 違規，最少 14 分鐘），`NEXT_ELIGIBLE_BAR_OPEN` |
| D5 | 交易日曆／RTH 邊界 | PASS | 22 個交易日；`OUTSIDE_CONSERVATIVE_RTH` 857、`CLOSING_AUCTION_EXCLUDED` 22 筆被拒 |
| D6 | 缺漏資料依明確政策處理 | PASS | `INCOMPLETE_SOURCE_BUCKET` 永不可執行；`max_fill_delay_bars` 到期即 `ORDER_EXPIRED`；不 forward fill |
| D7 | 成本與滑價已釘選 | PASS | 成本集 SHA＋四個情境的 Core profile SHA（依交易日而定，B3-B 以 CO07 重新推導） |
| D8 | 研究期間排除 Holdout | PASS | 窗口終點 ≤ Holdout 起點；原生最後時間 2024-06-28T23:59Z |
| D9 | Core benchmark 與 readiness 證據仍適用 | PASS | `benchmark/results/mrc_b16179a7cc07` = BENCHMARK_PASS（B01–B12、G16、G17、G14b）；DATA_QUALITY gate PASS。formal market status 仍是 NO_GO（未變） |

沒有重新開啟 Data Lake 驗證；也沒有發現可重現的執行阻擋缺陷。

## 4. 受控執行 readiness

| 檢查 | 結果 |
|---|---|
| C1 真實 Runner DISABLED | `REAL_EXECUTION_ENABLED = False`；`ResearchOnlyRunnerAdapter.execute` 直接 raise `AdapterDisabled` |
| C2 受控 adapter 在正式帳本上不啟用 | `enabled = False`、`isolated = False`（非 synthetic backend） |
| C3 Orchestrator 拒絕在正式帳本上做受控執行 | `ValueError`，而且在建立任何 budget ledger 之前就拒絕 |
| C4 沒有環境變數啟用路徑 | 掃描 `src/` 全部 `os.environ`／`getenv`：只有 TLS（`SSL_CERT_FILE`、`REQUESTS_CA_BUNDLE`）與讀取核准證據用的 `GH_TOKEN`／`GITHUB_TOKEN` |
| C5 SINGLE_EXECUTOR_ONLY | 是；不宣稱 exactly-once |

其餘項目以測試證據佐證（B3A-07 的 junit）：
- 每個 reservation 最多一次 runner 呼叫，包括 2 個和 6 個 process 同時重放；
- 原子 reservation；
- 權威的 approval 與 binding；
- request／runner／dataset 身分一致；
- 證據驗證會擋下無效的結算（P0-2，含 `research_request.json`）；
- crash 後 fail closed；
- 沒有重複 dispatch。

**沒有新增任何啟用開關。** B3-B 若要啟用，必須另有設計與授權；不能用環境變數繞過治理。

## 5. 授權準備（只有草案）

`docs/phase5b3a/authorization_draft.json`（`DRAFT_NOT_APPROVED`；不在 `governance/` 底下，沒有寫入任何 book）：

| 項目 | 草案 |
|---|---|
| 研究目的與假設 | 第一次經由治理過的 Phase 5 管線執行真實研究；假設只有預先登錄的 SMA 5/20 baseline。**重跑已評估過的 DEV 窗口只增加管線證據，不增加關於 SMA 的統計證據** → 是否要做，由人決定 |
| 市場／標的 | IBKR_TSM / TSM |
| 資料集與期間 | DEV 快照，研究窗口 `[2024-05-29, 2024-06-29)` ET，manifest `8b6fc838…` |
| 策略規格 | **HUMAN_DECISION_REQUIRED**（§0：需要重新綁定資料集的 spec 新版本與新 SHA） |
| 成本假設 | 成本集 `bf4f2365…`，四個情境 |
| 最大批次 | 提議 1 → 由人決定 |
| 有效期間 | `issued_at`／`expires_at` → 由人決定（RA10：formal gate 一旦變動即失效） |
| Executor | 單一具名 executor → 由人決定 |
| Holdout | `HOLDOUT-TSM-15M-v1`，`NO_ACCESS`，`BLINDNESS_UNVERIFIED` |
| 封存位置 | `MyDrive/Quant_Research/strategy-research-agent/`，`YYYYMMDD_HHMMSS_<git_commit>` |
| 授權 ID | 由人決定（格式 `RA-IBKR_TSM-SMA520_<NEW>`） |

**Schema dry-run：PASS。** 在記憶體中以真實的 `RESEARCH_AUTHORIZATION_SCHEMA` 驗證；核准欄位是明顯的假值（`dry-run-not-an-approver`、`https://example.invalid/…`），spec 欄位用 placeholder；沒有寫入任何檔案（有測試）。用盡的 1/1 授權沒有被重用或重置。synthetic 驗收只使用測試授權。

## 6. 需要人核准的 pin／決定

1. **Spec 新版本**：只重新綁定 `data_requirements`，規則和參數不變；新 SHA 由新授權釘選。
2. **是否以 DEV 窗口作為第一次 Phase 5 真實 run**。它只驗證管線，不產生新的研究證據。替代方案是更長的 development 期間，但目前**沒有**這樣的不可變快照，需要 Codex／producer 產生新快照並由研究負責人核准（範圍外，未啟動）。
3. **新授權**：ID、`max_batches`、有效期間、executor、approval ref；由研究負責人以 `research_authorization_admin approve` 執行。
4. **Binding**：以正式 GitHub 核准證據（5-B1）完成；Bound Request Hash 在此之後才存在。
5. **真實 Runner 的啟用設計**：屬於 B3-B，需另行審查。目前受控路徑只接受隔離帳本與 synthetic backend。
6. **Core receipt 更新**：B3-B 的 session 中要做一次（不改 snapshot pin）。

## 7. 剩餘風險

- `SINGLE_EXECUTOR_ONLY`：跨 Colab runtime 沒有分散式原子性；不宣稱 exactly-once。
- 資料集 readiness 來自先前真實 run 的封存證據；除了 Colab 可選的整檔 hash 之外，沒有重新讀取位元組。
- 流動性：固定 100 股，沒有成交量參與率或市場衝擊模型。
- 成本是 RESEARCH_ONLY 假設（未經帳戶驗證）；CAT 是否轉嫁未知。
- `BLINDNESS_UNVERIFIED`：Holdout 曾在 Phase 2 被 SMA 5/20 觸及，之後任何 Holdout 結果都不能稱為完全盲測。
- formal gates 維持 NO_GO／UNKNOWN／FAIL；research-only 的資格完全依賴授權契約。
- 5-B2 的 Layer B 測試沿用 DEV 提案的到期日 2026-11-30，過期後 Layer B 測試會 fail closed。
- Drive 上 5-B2 的 `work/` tree 未被獨立核對（沿用 5-B2 的限制）。
