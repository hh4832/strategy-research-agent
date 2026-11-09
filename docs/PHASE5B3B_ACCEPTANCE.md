# Phase 5-B3-B：Acceptance（B3B-01..B3B-10）

| 項目 | 值 |
|---|---|
| 程式 pin | `d9afac9d079d8cf5c3f3f2e1a3425bb1e3ff4e90` |
| Notebook | `notebooks/Phase5B3B_Controlled_Execution_Acceptance.ipynb`，`PHASE5B3B-EXECUTION-v1` |
| 執行 | `python -m strategy_agent.execution.real_acceptance --out-dir … --junit … --tests-rc … [--cloud]` |
| 封存 | `MyDrive/Quant_Research/strategy-research-agent/phase5b3b/<YYYYMMDD_HHMMSS>_<pin7>/`：`b3b_acceptance.json`（判定＋四個層次＋gates）、`governance_state.json`（正式帳本的即時檢查）、`b3b_junit.xml`、`notebook_context.json`、`run_info.txt`、`ARCHIVE_INDEX.json`；O_EXCL 寫入、不可覆寫；沒有 `work/` tree |

Gate 結果只來自**實際執行的測試**（junit）與即時的非消耗檢查，不讀任何預先填好的 PASS 檔。所有拒絕路徑的測試都斷言 runner 沒有被呼叫（`backend.calls == 0`，或跨 process 的檔案計數器）。

## Gate 定義（`execution/real_acceptance.py::gates`）

| Gate | 需求 | PASS 條件 |
|---|---|---|
| B3B-01 | 未授權時拒絕 | `test_B3B_01_*` 全部 PASS；**並且**即時檢查 repo 正式帳本：REAL_GATED orchestrator 建構被拒、沒有任何符合資格的授權、probe runner 呼叫 0、`REAL_EXECUTION_ENABLED=False`、舊 adapter 仍 `AdapterDisabled`、沒有產生 budget events 檔、帳本前後不變 |
| B3B-02 | Authorization／Bound Request 一致 | `test_B3B_02_*` |
| B3B-03 | Dataset／Core／Spec 身分一致 | `test_B3B_03_*`，並且必須有 Layer B（真 runner＋Core）的 `test_B3B_03_layer_b_*` PASS |
| B3B-04 | Holdout 排除 | `test_B3B_04_*` |
| B3B-05 | Budget 原子預留與重放防護 | `test_B3B_05_*` |
| B3B-06 | 單一 executor | `test_B3B_06_*`（含 3 個 process 同時重放） |
| B3B-07 | Runner receipt 與結果一致 | `test_B3B_07_*`，並且必須有 Layer B 的 `test_B3B_07_layer_b_*` PASS |
| B3B-08 | Archive 完整性與結算 | `test_B3B_08_*` |
| B3B-09 | Crash recovery／fail closed | `test_B3B_09_*` |
| B3B-10 | CI／Colab 可重現 | tree 乾淨、40 碼 SHA、測試 rc=0 且沒有任何失敗、有 junit；而且是 Colab＋Drive（`--cloud`），否則 BLOCKED |

判定：
- 任何 FAIL → **NO_GO**；
- 任何 BLOCKED → **NOT_EVIDENCED**，例如沒有 Core 或不是 cloud；
- 全部 PASS → **SCOPED_GO**，限制為 SINGLE_EXECUTOR_ONLY、只有 synthetic／mock／隔離帳本的證據、真實 Runner 在核准授權之前 DISABLED。

## 負面測試對照（brief 要求）

| 要求 | 測試 |
|---|---|
| 未核准的 Authorization | `test_B3B_01_unapproved_draft_authorization_never_executes`、`…_no_authorization_naming_this_executor…`、`…_production_books_refuse_the_real_path_today` |
| 已消耗的 Authorization | `test_B3B_01_spent_dev_authorization_is_never_eligible`、`test_B3B_05_second_use_of_the_same_budget_is_refused` |
| 過期的 Authorization | `test_B3B_01_expired_authorization_refused`；撤銷：`…_revoked_after_construction…` |
| 錯誤的 Request Hash | `test_B3B_02_wrong_request_hash_is_blocked` |
| 錯誤的 Dataset／Core／Spec SHA | `test_B3B_03_runner_identity_mismatch_blocks_before_runner[4]`、`…_readiness_manifest_disagreeing…`、Layer B `…_session_config_beyond_the_core_receipt…[2]`、`…_without_the_signed_canonical_config…` |
| Holdout 跨界 | `test_B3B_04_holdout_crossing_or_access_is_refused` |
| 第二次使用同一額度 | `test_B3B_05_second_use_of_the_same_budget_is_refused`、`…_replay_and_duplicate_dispatch_run_at_most_once` |
| 非授權的 Executor | `test_B3B_06_unauthorized_executor_is_refused`、`…_executor_bound_to_the_adapter_must_equal_the_gate`、`…_second_process_cannot_run_the_same_reservation` |
| Archive 遺失或被修改 | `test_B3B_08_archive_lost_or_modified_never_settles[2]`、`test_B3B_07_receipt_contract_modified_before_settlement…` |
| Execution Receipt 不一致 | `test_B3B_07_missing_runner_receipt_never_settles`、`…_receipt_that_is_not_a_core_renewal…`、`…_runner_result_inconsistent…` |
| 中斷後重複 dispatch | `test_B3B_09_crash_after_start_is_never_rerun`、`test_B3B_09_interrupted_dispatch_is_not_dispatched_again` |

## 本機結果（pin `d9afac9` 之前的工作樹）

| 環境 | 結果 |
|---|---|
| 無 Core | 675 passed / 68 skipped |
| Core `699e3ad` | 769 passed / 17 skipped |
| Core `b16179a` | 793 passed / 4 skipped / 1 failed = 已知 `test_15`（Core-SHA 參考值綁定 699e3ad，未遮蔽） |
| B3-B suites | 31（mock）＋4（Layer B，Core `b16179a`）＋3（acceptance）全部 PASS |

## CI

Job `phase5b3b-real-execution`，Python 3.11 與 3.12，Core `b16179a`：
1. 跑 B3-B＋B2＋B1 suites（junit）；
2. 執行 `real_acceptance`，斷言 B3B-01..09 PASS、B3B-10 BLOCKED（CI 不算 cloud）、`real_execution=NOT_PERFORMED`、`research_authorization=NOT_GRANTED`；
3. 驗證封存；
4. 對 `registry`、`governance`、`configs`、`specs` 執行 `git diff --exit-code`；
5. 上傳 artifact（junit＋封存）。

既有的回歸 job 照常執行（3.11／3.12）。

## Colab／Drive

（待執行）
