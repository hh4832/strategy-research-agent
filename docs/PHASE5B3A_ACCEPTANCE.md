# Phase 5-B3-A：Acceptance（B3A-01..B3A-08）

| 項目 | 值 |
|---|---|
| 程式 pin | 第一輪 `5045d4a`（NO_GO）；**目前 `c906d48845fee26199b0382f88eead20c625480e`**（研究負責人決定後：spec 0.1.1） |
| Notebook | `notebooks/Phase5B3A_Readiness_Acceptance.ipynb`，目前 `PHASE5B3A-READINESS-v2`（pin `c906d48`） |
| 執行 | `python -m strategy_agent.readiness.acceptance --out-dir … --junit … --tests-rc … [--cloud] [--snapshot-dir …]` |
| 封存 | `MyDrive/Quant_Research/strategy-research-agent/readiness/<YYYYMMDD_HHMMSS>_<pin7>/`：`b3a_acceptance.json`、`readiness_manifest.json`、`authorization_draft.json`、`readiness_checks.json`、`notebook_context.json`、`run_info.txt`、`ARCHIVE_INDEX.json`；O_EXCL 寫入、不可覆寫；沒有 `work/` tree |

所有測試只用 synthetic fixture、封存的既有證據或非消耗性驗證。**沒有執行新的真實回測**，沒有讀 Holdout，沒有建立或消耗授權。

## Gate 定義

| Gate | 需求 | PASS 條件（程式：`readiness/acceptance.py::gates`） |
|---|---|---|
| B3A-01 | Dataset 與 snapshot 身分 | snapshot ID、manifest SHA 都是 RESOLVED；D1（不可變，receipt＋lineage）、D2（manifest 與 loader provenance 相同）PASS；可選的 Drive 整檔 hash 不得 FAIL |
| B3A-02 | Core 與策略規格身分 | Core、spec SHA、strategy 都是 RESOLVED；D9 BENCHMARK_PASS；**Phase 5 A2 exporter 接受 spec 與 dataset 的綁定**，否則 BLOCKED（需要人決定） |
| B3A-03 | PIT 與時序執行規則 | temporal_rules RESOLVED；D3–D6 PASS |
| B3A-04 | 成本／滑價／流動性 | 成本與滑價 RESOLVED；D7 PASS；部位大小已釘選（FIXED_UNITS 100；沒有參與率模型，記為風險） |
| B3A-05 | Holdout 排除 | holdout_exclusion、research_window RESOLVED；D8 PASS |
| B3A-06 | 授權與額度治理 | G1–G4 PASS（book 有效、DEV 授權已用盡不可重用、沒有新授權、新授權屬於人的決定）；草案 schema dry-run PASS；5-B1／5-B2／5-B3-A 測試沒有失敗 |
| B3A-07 | 單一 executor 受控 Runner readiness | C1–C5 PASS（Runner DISABLED、正式帳本拒絕、沒有環境變數開關、SINGLE_EXECUTOR_ONLY）；5-B2 安全與獨立 P0-2 測試沒有失敗，其中跨 process 重放測試存在且 PASS |
| B3A-08 | CI／Colab／Drive 可重現性 | tree 乾淨、40 碼 SHA、測試 rc=0 且沒有失敗；而且是 Colab＋Drive（`--cloud`），否則 BLOCKED |

判定（`verdict`）：
- B3A-01..07 任一 FAIL 或 BLOCKED → **NO_GO**，並列出阻擋；
- 只剩 B3A-08 BLOCKED → **NOT_EVIDENCED**；
- 全部 PASS → **SCOPED_GO**（SINGLE_EXECUTOR_ONLY、資料集 readiness 來自封存證據、Runner 仍 DISABLED）。

GO／SCOPED_GO 都**不**授權 Runner。

## 測試

`tests/test_phase5b3a_readiness.py`（21 個）：
- 17 個欄位的解析；HUMAN_DECISION 欄位從不給預設值。
- exporter 實際找出 spec／dataset 矛盾；重新綁定的 spec 能通過 exporter。
- 注入 7 種矛盾或缺值 → BLOCKED。
- 窗口觸及 Holdout → BLOCKED。
- 9 項資料集檢查、5 項受控檢查、4 項治理檢查。
- 草案未核准、從不寫入，dry-run PASS。
- 已提交的 manifest／draft 與程式輸出逐位元相同。
- gate 與判定邏輯：目前 NO_GO；spec 綁定通過且在 cloud 上 → SCOPED_GO；非 cloud → NOT_EVIDENCED；Drive hash 不符 → FAIL；測試失敗 → FAIL。
- 封存不可覆寫、有索引、竄改可偵測；repo 帳本前後不變。

本機結果：

| 環境 | 結果 |
|---|---|
| 無 Core | 635 passed / 64 skipped |
| Core `699e3ad` | 729 passed / 13 skipped |
| Core `b16179a` | 749 passed / 4 skipped / 1 failed = 已知 `test_15`（Core-SHA 參考值綁定 699e3ad，未遮蔽） |
| 5-B1＋5-B2＋5-B3-A suites（Core `b16179a`） | 170 passed |
| 本機 acceptance | B3A-01、03、04、05、06、07 PASS；B3A-02 BLOCKED；B3A-08 非 cloud；判定 NO_GO（唯一的 B3A-01..07 阻擋是 B3A-02） |

## CI

新增 job `phase5b3a-readiness`，Python 3.11 與 3.12 都跑，安裝 Core `b16179a`：
1. 跑 suites（junit）；
2. `--emit-docs` 後執行 `git diff --exit-code docs/phase5b3a`；
3. 執行 acceptance，斷言沒有任何 gate FAIL、B3A-08 = BLOCKED（CI 不算 cloud 證據）；
4. 驗證封存；
5. 對 `registry`、`governance`、`configs`、`schemas`、`specs` 執行 `git diff --exit-code`。

## 研究負責人決定後（pin `c906d48`）

決定內容見 [`PHASE5B3A_READINESS.md`](PHASE5B3A_READINESS.md) §0.2：spec 0.1.1 只重新綁定資料集。

- 測試增為 27 個。原 spec 的 NO_GO 路徑保留為測試；新增 0.1.1 通過 exporter、只改資料綁定的測試；新增 5 種「改到資料綁定以外的內容」→ BLOCKED 的注入測試。
- 本機回歸：
  - 無 Core：641 passed / 64 skipped；
  - Core `699e3ad`：735 passed / 13 skipped；
  - Core `b16179a`：755 passed / 4 skipped / 1 failed（已知 `test_15`）。
- 治理相關 suites：176 passed。
- 本機 acceptance：B3A-01..07 全部 PASS。B3A-08 在本機因 tree 未提交顯示 FAIL，提交後的 CI 為 BLOCKED（CI 不是 cloud），要由 Colab 證明。

## Colab／Drive

### 第一輪（pin `5045d4a`，NO_GO）：**`readiness/20261108_225409_5045d4a`**（notebook v1，全新 runtime，Python 3.13.18，`cloud=true`）。

- **程式版本：** pin `5045d4a`，checkout 乾淨；pin 之後只有 docs 與 notebook 變更。
- **回歸：** 無 Core 634 passed / 65 skipped（比本機多 1 個 skipped，是環境條件造成的 skip，0 failed）。5-B1＋5-B2＋5-B3-A suites 在 Core `b16179a` 下 170 passed。
- **Gates：** B3A-01、03、04、05、06、07、**08** 皆 PASS；**B3A-02 BLOCKED**（`SPEC_DATASET_REFERENCE_MISMATCH`）。
- **判定：NO_GO**，唯一阻擋是 B3A-02，需要人做決定（核准重新綁定資料集的 spec 版本）。
- **DEV snapshot 整檔位元組檢查：PASS。** `manifest.json` = `8b6fc838…cfc5`、`tsm_development_1m.parquet` = `239d5741…0a8452`，與 pin 完全相同。只做 SHA-256，不解碼，不含 Holdout。
- **其他確認：** repo 帳本前後不變；`real_runner=DISABLED`、`authorization_created=false`、`market_data_read=false`、`holdout_read=false`、`new_backtest_executed=false`。
- **相依套件 pin：** market-research-core @ `b16179a`、pandas 2.2.3、pyarrow 23.0.1、numpy 2.4.6、jsonschema 4.26.0、pytest 8.4.2（完整清單見 `notebook_context.json`）。
- **封存：** Colab 端 `--verify-archive --zip` PASS。Claude 對上傳的 ZIP 重新核對，`ARCHIVE_INDEX.json` 列出的 6 個檔案 SHA 全部吻合，沒有額外檔案。ZIP sha256 為 `10ec42e702f51306b289ea2d078455684d41bf5716c81a22f3f0a19cba07e966`。
- **證據的形式：**
  - ZIP：7 個頂層檔案（含索引）。
  - GitHub 副本：`docs/evidence/phase5b3a/20261108_225409_5045d4a/`，內容與 ZIP 相同。
  - 本階段**沒有** `work/` tree，所以沒有未提交的 supporting artifacts。
