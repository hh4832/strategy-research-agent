# Phase 5-B3-A：Acceptance（B3A-01..B3A-08）

| 項目 | 值 |
|---|---|
| 程式 pin | `5045d4af4130ae5dfd40be40643740d19acbf6bd`（readiness 程式、測試、CI、manifest／draft） |
| Notebook | `notebooks/Phase5B3A_Readiness_Acceptance.ipynb`，`PHASE5B3A-READINESS-v1` |
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

## Colab／Drive

（待執行：由研究負責人在全新 runtime 執行 notebook。結果記錄於此節與 `docs/evidence/phase5b3a/<ts>_<pin7>/`。）
