# Phase 5-B2：Acceptance（B2-01..B2-10）

> **安全修補注意（PR #13，Codex P0-1 / P0-2 / P0-3）。** 下表與 Colab 紀錄 `executions/20261108_115819_019db14`（pin `019db14`）都是**修補前**的結果，修補前的實作有三項 P0 漏洞，因此該結果不再代表目前的程式。修補後的驗收使用 `PHASE5B2-EXECUTION-v2`（pin `86b068b`），會寫入新的 `executions/<ts>_86b068b/`，舊紀錄保留、不覆寫。三項 finding 另行報告於 `security_remediation.json`，詳見 [PHASE5B2_SECURITY_REMEDIATION.md](PHASE5B2_SECURITY_REMEDIATION.md)。Security GO 不由本專案宣告。

每個 Gate 都由 `strategy_agent.execution.acceptance` 依**真實的 junit 結果**加 E2E artefact 計算；文件描述不能代替測試。
測試名稱以 Gate 為前綴（`test_B2_0X_…`）。

| Gate | Requirement | Tests | Expected | Actual（本地） | Status |
|---|---|---|---|---|---|
| B2-01 | Atomic Reservation | `test_B2_01_*`：8 個 spawn process、uses 與預留合併計數、冪等重試、帳本損毀 | 不超過上限；損毀時 fail closed | 2/8 成功（max 2）；LEDGER_CORRUPTED 時 BLOCKED | PASS |
| B2-02 | Authorization Scope | `test_B2_02_*`：PENDING、未綁定、核准後額度耗盡、授權撤銷、沒有 committed reservation 就 execute | 全部不執行 | backend 呼叫 0 次 | PASS |
| B2-03 | Human Approval Verification | `test_B2_03_*`：未核准、撤銷 | 不執行、不預留 | 0 次呼叫、0 筆預留 | PASS |
| B2-04 | Bound Request Integrity | `test_B2_04_*`：修改過的 bound request | G11 FAIL | BLOCKED | PASS |
| B2-05 | Synthetic Controlled Execution | `test_B2_05_*`（Layer A + Layer B） | 只執行 1 次；狀態依序 RESERVED→COMMITTED→CONSUMED；uses 1 筆 | Layer A ARCHIVED；Layer B 既有 Runner，C1–C8 PASS | PASS |
| B2-06 | Crash／Retry／Concurrency | `test_B2_06_*`：重複提交、6 個執行緒、第二個 executor、commit 後 crash、uses 與 commit 之間 crash、commit 前 crash、lease 過期、Layer B Runner crash | 不靜默釋放、不重跑 | 全部依 §7 處理 | PASS |
| B2-07 | PIT／Dataset／Cost Integrity | `test_B2_07_*`：spec 被改、PIT 規則、pin 缺失、Layer B 的 snapshot 或成本不一致 | 預留前 BLOCKED | 0 筆預留 | PASS |
| B2-08 | Evaluator／Archive Integration | `test_B2_08_*`：既有 evaluator、evaluator crash、archive 不完整 | 不重跑、絕不 KEEP、絕不 ARCHIVED | 均符合 | PASS |
| B2-09 | No Real Execution／Holdout／Budget | `test_B2_09_*`：正式帳本、環境變數、非 synthetic backend、repo 帳本不變 | 全部 DISABLED | `REAL_EXECUTION_ENABLED=False`；repo 內沒有 budget ledger | PASS |
| B2-10 | CI／Colab／Drive Reproducibility | 全套件 + B2 suites + Drive 封存（`ARCHIVE_INDEX` + ZIP 比對） | rc=0 且 cloud | Colab：回歸 565 passed（無 Core），B2 suites 31 passed（Core b16179a），cloud=true，封存與 ZIP 一致 | PASS |

## 測試（本地）

| 環境 | 結果 |
|---|---|
| 無 Core（v5，3.13） | 565 passed／58 skipped（含 Layer A 25 項、acceptance 2 項；Layer B 4 項 skip） |
| Core 699e3ad（v4） | 659 passed（另有 B2 新增檔） |
| Core b16179a（v8） | 673 passed，僅已知 `test_15` 失敗；Layer B 4 項 PASS |
| 驗收（v8，fresh process） | B2-01..B2-09 PASS；Layer B PASS；B2-10 BLOCKED；SCOPED_GO |
| CI | 新 job `phase5b2-controlled`（Core b16179a；Layer A + B + acceptance + verify-archive）；3.11／3.12 全套件 |

## 判定

- 本地：**SCOPED_GO**。原因是一致性限於 `SINGLE_EXECUTOR_ONLY`，B2-10 也還沒有 Colab／Drive 證據。
- Colab 跑完並寫入 `MyDrive/.../executions/<ts>_<sha7>/`、`ARCHIVE_INDEX` 與 ZIP 都比對一致後，B2-10 才會 PASS。即便如此判定仍是 SCOPED_GO，因為沒有分散式 atomicity；依 brief，跨 runtime 一致性未驗證時屬於 SCOPED_GO。

## Colab 雲端驗收（2026-11-08 11:58，`executions/20261108_115819_019db14`）

- 乾淨的 runtime，Version Sync PASS（pin 之後只有 notebook 變動），Python 3.13.18。
- 回歸測試（無 Core）565 passed／58 skipped；B2 suites（Core b16179a）31 passed，rc=0，Layer B 沒有被 skip。
- **B2-01..B2-10 全 PASS**。
  - Layer A：ARCHIVED，預留依序 RESERVED→COMMITTED→CONSUMED，backend 執行 1 次。
  - Layer B：既有 ResearchOnlyRunner 經 fresh-process evaluator 驗收 **PASS**，uses 1 筆，Runner 只執行 1 次。
- repo 帳本未變；`real_market_execution=false`、`holdout_read=false`、`real_authorization_consumed=false`、`broker_api_called=false`。
- `ARCHIVE_INDEX` 涵蓋 8 個檔案，hash 全部一致（含 ZIP 比對）。副本：`docs/evidence/phase5b2/20261108_115819_019db14/`
- **Engineering Verdict：SCOPED_GO**。全部 Gate PASS；但一致性限於 `SINGLE_EXECUTOR_ONLY`，沒有分散式 atomicity，也不宣稱 exactly-once。
