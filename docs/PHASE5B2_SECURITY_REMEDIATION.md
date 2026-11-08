# Phase 5-B2：PR #13 安全修補（Codex P0-1 / P0-2 / P0-3）

| 項目 | 值 |
|---|---|
| 審查對象 | PR #13，HEAD `71b9a6da829c652d7e1f89e107b770d730728598` |
| 審查結論（Codex） | Security **NO_GO / FIX_BEFORE_MERGE** |
| 修補前 commit | `71b9a6d`（重現腳本另以 `402aaaa` 提交，未改任何程式） |
| 修補 commit | `86b068b8ffa8941424b0f862091545a44fc62c25` |
| 一致性範圍 | 維持 `SINGLE_EXECUTOR_ONLY`；**不**宣稱已實現 Distributed Exactly-once |
| 本文件的結論 | Engineering 修補完成，**不宣告 Security GO**；交由 Codex 針對這三項做獨立複審 |

> **來源說明。** Codex 的原始報告 `/workspace/security-review-reports/pr13/SECURITY_REVIEW.md` 與原始重現程式 Claude 無法存取（該目錄不存在，上傳檔只有修補說明）。以下的根因分析、行號與重現都是 Claude 依修補說明中三項 finding 的描述**重新推導**的，**不代表**已核對過 Codex 原報告的行號或原始重現程式。

## 1. 三項 finding 與根因

### P0-1 Reservation Replay / Forgery

**根因。** `71b9a6d` 中 `ControlledRunnerAdapter.execute(doc, out_dir, *, use, reservation)`（controlled.py:76–81）直接信任呼叫端傳入的 `use` / `reservation` 物件，唯一的檢查是 `use.run_id == reservation.planned_run_id`。帳本只在 Orchestrator 的 `commit` 時寫一筆 uses 記錄，adapter 本身從不回頭查帳本。所以任何人只要拿到（或偽造）這兩個 dict，就能經由公開 adapter 讓 backend 再跑一次；`state: COMMITTED` 也只是呼叫端自己寫的欄位。

**修補。**
- `budget.py:240` `claim_execution()` 是**唯一**的執行授權。它在 uses-log 的 flock 臨界區內依序確認：帳本中的狀態是 COMMITTED；request / task / authorization / binding / attempt / approval evidence 都與帳本一致（`CLAIM_IDENTITY`，budget.py:58）；呼叫者是已註冊的 executor；權威 uses 記錄存在（以 COMMITTED 事件的 `use_record_sha256` 與 `use_seq` 對應）；而且尚未寫過 `EXECUTION_STARTED`。全部通過才寫入 `EXECUTION_STARTED`，並回傳**帳本自己的** reservation 與 uses 記錄。
- 新增三種 marker 事件 `EXECUTION_STARTED` / `EXECUTION_COMPLETED` / `EVIDENCE_VERIFIED`（budget.py:57）。它們只在 COMMITTED 狀態下有效，`_fold`（budget.py:377）會拒絕重複、順序錯誤或狀態不符的事件。這把「已開始執行」「已完成執行」「證據已驗證」「已結算」分成四個可區分的階段。
- `controlled.py:117` 的 `execute()` 只接受 `reservation_id` / `task_id` / `attempt_id`。只要呼叫端傳入任何 reservation / use / state 物件，一律回傳 `CALLER_SUPPLIED_RESERVATION_REFUSED`（controlled.py:123）。
- `controlled.py:83` `bind()`：adapter 只綁定**自己 governance root** 的帳本，不接受其他帳本。Orchestrator 在初始化時完成綁定（orchestrator.py:91）。approval evidence 由 `_running_approval`（orchestrator.py:351）從 task log 取得，而且只有在該 task 正處於 RUNNING、且 RUNNING 事件記載的正是這個 reservation 時才會回傳。
- 若 runner 已開始但無法確認完成，帳本只能進入 `RECONCILIATION_REQUIRED`：不會自動 release，也不會重跑。

### P0-2 Receipt / Archive Integrity Before Settlement

**根因。** `71b9a6d` 的 `Orchestrator._settle`（orchestrator.py:424–434）只檢查兩件事：execution reference 裡有 `receipt_sha256` 字串、ARCHIVED 事件裡有 `task_archive` 路徑。它不會重新讀取 receipt 或 archive。帳本的 `settle()`（budget.py:202）也只要求狀態是 COMMITTED。因此即使 receipt 被刪除或竄改、archive 檔案被移除、MANIFEST 被破壞，結算照樣成為 CONSUMED。

**修補。**
- `controlled.py:188` `verify_execution_evidence()` 一律**從磁碟**重新驗證，不採信呼叫端的 reference：
  - receipt 存在，且 SHA 等於帳本 `EXECUTION_COMPLETED.receipt_sha256`；
  - receipt 各欄位等於帳本中的 reservation / task / attempt / request / authorization / binding / approval / run_id / use 記錄 / EXECUTION_STARTED 事件；
  - `phase4c.read_archive` 依帳本記錄的 MANIFEST SHA 重新驗證每個檔案；
  - archive 的身分與 uses 記錄等於 request（`identity.archive_problems`，identity.py:205）；
  - 評估 reference 指向同一份 archive，且評估輸出未被更改。
- 驗證會跑兩次：評估前（orchestrator.py:430，失敗就是 `EXECUTION_EVIDENCE_INVALID`，不進評估）與結算前（`_settle`，orchestrator.py:452/469）。只有結算前驗證通過才寫 `EVIDENCE_VERIFIED`。
- `budget.py:222` `settle()` 要求 COMPLETED 與 VERIFIED 都已存在，且 SHA 完全相同；否則一律 `SETTLE_REFUSED` / `SETTLE_EVIDENCE_MISMATCH`。
- 驗證失敗一律進 `RECONCILIATION_REQUIRED`，額度保持已消耗，不 release、不重跑。task 停在 ARCHIVED（終態），`status()` 新增的 `budget_state` 欄位會如實顯示帳本狀態。

### P0-3 Request / Execution Identity

**根因。** `71b9a6d` 的 precheck 有兩個缺口：
- `ControlledRunnerAdapter.precheck`（controlled.py:64–68）只檢查 pin 欄位「存在」，不檢查值是否正確；
- `ResearchOnlyRunnerBackend.precheck`（controlled.py:162–172）只比對 authorization、snapshot id 與 cost SHA，從不比對 Core、strategy spec 或 dataset manifest。

此外 Layer B 的 `core_world.request_for`（core_world.py:130）是**從 runner config 反推 request**（自我參照），所以 config 與 request 不可能不一致，也就從未真正測到這個檢查。結果是帶有錯誤 Core / spec / manifest pin 的 request 照樣通過 precheck，runner 也照樣執行。

**修補。**
- 新模組 `execution/identity.py:162` `verify_request()` 要求三個獨立來源全部一致：
  1. **request** 必須正是已登錄的 bound request，以 binding log 加上不可變副本驗證（`binding_problems`，identity.py:135）。authorization 必須有效且未過期（identity.py:146），範圍必須與 authorization 記錄相同（`authorization_problems`，identity.py:94）。範圍涵蓋：spec SHA / 路徑、strategy id / version / 參數、dataset id / snapshot / producer、cost SHA、temporal policy、holdout lock，以及 research window 在授權窗內；
  2. **磁碟上的 artefact**：strategy spec 與 cost 檔案的 SHA 必須符合（identity.py:124）；
  3. **backend 自己的執行身分**（`execution_identity`），不從 request 推得。Runner 版本（controlled.py:408）讀 runner config、snapshot root 上實際的 manifest 檔案 SHA，以及**已安裝 Core** 的 `core_git_sha`。
- 比對涵蓋：authorization、strategy id / version / 參數 / spec SHA、Core commit、dataset snapshot id、dataset manifest SHA、cost profile SHA、temporal policy SHA、research window（以時間點比較）。另外檢查 signal / execution time policy、Holdout `NO_ACCESS`，以及 execution mode `RESEARCH_ONLY_REAL`。snapshot 或 manifest 不同一律拒絕，**不會換成最新版本**。
- 驗證會跑三次：precheck（reservation 前）、`execute()` 在 claim 前、runner 在 Core 執行前（controlled.py:454 `RUNNER_IDENTITY_CHANGED`，防 TOCTOU）。
- Layer B 改用真實的 A2 → 已驗證的 binding → bound request（`core_world._bind`，core_world.py:155），經 Orchestrator 跑完整流程（core_world.py:214）。runner 以 artefact root 為根（core_world.py:136）。spec 的 data_requirements 重新 pin 到 synthetic snapshot（TEST FIXTURE），因此 spec SHA 與 repo 中的不同；authorization 與 config 也一起重新 pin（core_world.py:124/129）。`request_for` 已移除。
- Layer A 改用 `SyntheticStubBackend`（controlled.py:296）：它的執行身分由測試世界的 authorization 記錄、artefact 檔案與 synthetic dataset pin 設定，不從 request 取得。它產出的 archive **不是 producer 產出**，現有 Phase 4-C evaluator 仍會拒絕（C1 FAIL → RETEST，從不 KEEP）。原本的 `SyntheticFixtureBackend` 帶的是 DEV authorization / snapshot / manifest 身分，現在對 Layer A request 會在 runner 執行前就被 BLOCKED（測試 `test_P0_3_foreign_fixture_identity_is_blocked_before_runner`）。

## 2. 修改的檔案

| 檔案 | 內容 |
|---|---|
| `src/strategy_agent/execution/identity.py`（新） | P0-3 權威身分驗證；P0-2 archive 身分 / uses 記錄驗證；`raw_run_info`（只由數字組成的 SHA 不會被轉成 int） |
| `src/strategy_agent/execution/budget.py` | `claim_execution`、`record_completion`、`record_evidence_verified`、`use_record`；marker 事件；`settle` 前置條件 |
| `src/strategy_agent/execution/controlled.py` | adapter 綁定帳本、拒絕呼叫端物件、claim、輸出一致性、receipt 欄位、`verify_execution_evidence`；backend `execution_identity`；`SyntheticStubBackend` |
| `src/strategy_agent/orchestration/orchestrator.py` | adapter 綁定、`_running_approval`、評估前驗證、`_settle` 重新驗證、evaluation reference 記錄 archive pin、`status().budget_state` |
| `src/strategy_agent/execution/core_world.py` | Layer B：artefact root、spec / authorization 重新 pin、A2 → binding、`bound_request`、`orchestrator` |
| `src/strategy_agent/execution/testkit.py` | stub backend 身分、`committed_running`、多 process 重放 worker `mp_execute` |
| `src/strategy_agent/execution/acceptance.py` | Layer B 改走 Orchestrator；三項 finding 分開報告（`security_remediation.json`）；重現腳本結果（`security_reproduction.json`） |
| `.github/workflows/ci.yml` | `phase5b2-controlled` 改為 Python 3.11 / 3.12 matrix，並斷言三項皆 `FIXED_PENDING_INDEPENDENT_REVIEW` |
| `tests/test_phase5b2_security_remediation.py`（新）、`tests/test_phase5b2_runner_core.py`、既有 B2 測試 | 見 §4 |

**未修改**：`market-research-core`、正式 Research Authorization 與 repo 治理帳本、`registry/chain.py`、`governance/append_only.py` 與其他 M3-B pin 檔案、approval / binding 套件、evaluator。

## 3. 修補前後的重現（重新推導；實際結果）

腳本：`docs/evidence/phase5b2_remediation/reproduce.py`。腳本會自動辨識 API 版本，在修補前後都能直接執行，記錄的是**實際**發生的 runner 呼叫次數與帳本狀態。

| 攻擊 | 修補前 `71b9a6d`（`before_71b9a6d.json`） | 修補後 `86b068b`（`after_86b068b.json`） |
|---|---|---|
| P0-1 同一 reservation 重放 ×3 | 3 次都執行 | 帶物件：`CALLER_SUPPLIED_RESERVATION_REFUSED`；只帶 id：`EXECUTION_NOT_ELIGIBLE` |
| P0-1 偽造 reservation / use | backend 被呼叫（archive 檢查前已計數） | `CALLER_SUPPLIED_…` / `UNKNOWN_RESERVATION` |
| P0-1 假 COMMITTED 狀態 | 執行 | `CALLER_SUPPLIED_RESERVATION_REFUSED` |
| **P0-1 runner 呼叫總數**（uses 記錄 1 筆） | **6** | **1** |
| P0-2 receipt 刪除 / 修改、archive 檔移除、MANIFEST 損毀 | 4/4 結算為 CONSUMED | 4/4 為 `RECONCILIATION_REQUIRED`，無結算 |
| P0-3 Layer A 錯誤 Core / spec / manifest | precheck 3/3 通過 | 3/3 BLOCKED（`BINDING_INVALID` + `IDENTITY_MISMATCH:BACKEND:*`） |
| P0-3 Layer B（runner + Core）錯誤 Core / spec / manifest | precheck 通過，runner 3/3 執行 | precheck BLOCKED；直接 execute 回 `IDENTITY_VERIFICATION_FAILED`；Orchestrator submit BLOCKED；runner 呼叫 0 |

## 4. 測試結果

Brief 要求的 regression tests：

| 要求 | 測試 | 結果 |
|---|---|---|
| 偽造 reservation → BLOCKED | `test_P0_1_forged_reservation_is_blocked_without_running` | PASS |
| 重用 reservation → runner 呼叫 ≤ 1 | `test_P0_1_reused_reservation_runs_at_most_once`、`test_P0_1_replay_while_in_flight_is_refused` | PASS |
| 兩個（及六個）process 同一 reservation → ≤ 1 | `test_P0_1_same_reservation_from_several_processes_runs_at_most_once[2,6]`（spawn，檔案計數器跨 process 計數） | PASS |
| 假 COMMITTED → BLOCKED | `test_P0_1_fake_committed_status_is_never_eligibility` | PASS |
| 結算前刪除 / 修改 receipt → RECONCILIATION_REQUIRED | `test_P0_2_receipt_deleted_or_modified_before_settlement_needs_reconciliation[2]` | PASS |
| archive 檔案移除 / manifest 損毀 → 無結算 | `test_P0_2_archive_or_evaluation_tampered_before_settlement_never_settles[3]` | PASS |
| 錯誤 Core / spec / manifest → runner 前 BLOCKED | `test_P0_3_wrong_core_spec_or_manifest_is_blocked_before_runner[4]`（Layer A），`test_P0_3_layer_b_wrong_core_spec_manifest_blocked_before_runner[3]`（Layer B，真 runner） | PASS |
| 執行開始後 crash → 不自動重跑 | `test_P0_2_crash_after_execution_started_is_never_rerun` | PASS |
| evaluator 失敗 → 不自動重跑 | `test_P0_2_evaluator_failure_is_never_rerun` | PASS |

額外的測試：
- P0-1：身分不符 / task 不在 RUNNING 時不授權；拒絕綁定其他帳本；結算必須先有已驗證的證據；偽造的 marker 事件會使 chain 失效。
- P0-2：評估前竄改；runner 結果與 request 不一致；archive 帶別筆 uses 記錄。
- P0-3：backend 身分與 bound request 不同；舊 fixture 身分；artefact 在 binding 後被改；commit 後才發生身分漂移；commit 後 authorization 被撤銷。
- Layer B：snapshot manifest 在磁碟上被換掉；已安裝 Core 與 request 不同；claim 後 config 被改（TOCTOU）。

本機套件結果：

| 環境 | 結果 |
|---|---|
| Python 3.13，無 Core | 594 passed / 64 skipped |
| Core `699e3ad` | 688 passed / 13 skipped |
| Core `b16179a` | 708 passed / 4 skipped / **1 failed** = 已知的 `test_15`（Core-SHA 參考值綁定 699e3ad；未遮蔽） |
| B2 + security suites（Core `b16179a`） | 66 passed |
| 本機 acceptance | B2-01..B2-09 PASS；B2-10 BLOCKED（無 Drive）；Layer B PASS；SCOPED_GO；P0-1/2/3 = `FIXED_PENDING_INDEPENDENT_REVIEW` |

既有 B2 測試的調整只動「建構方式」與「更強的斷言」，沒有放寬任何預期：
- 狀態歷史現在包含 marker 事件；
- B2-08 的 Layer A 斷言改成 C1 FAIL，因為 stub 的身分與 request 一致，C8 已經不會再因 authorization 不符而 FAIL；仍斷言 acceptance FAIL、engineering BLOCKED，且從不 KEEP；
- 故障注入改由 stub 的 `fail_with` / `drop_file` 提供；
- Layer B precheck 錯誤碼改為 `IDENTITY_MISMATCH:*` / `BINDING_INVALID:*`。

CI（GitHub Actions）與 Colab 結果見 PR #13 與 §6。

## 5. 仍存在的限制

- **`SINGLE_EXECUTOR_ONLY`。** fcntl 鎖只在單一主機 / 檔案系統上有效，不是跨 Colab runtime 或 Google Drive 的分散式鎖。不宣稱 exactly-once，只保證每個帳本 at-most-once dispatch，以及每個 reservation 最多一次 backend 呼叫。
- **授權窗口。** `claim_execution` 寫入 `EXECUTION_STARTED` 時，backend 尚未啟動。若 process 在這之後、backend 啟動前 crash，帳本會停在 STARTED；恢復時進入 `RECONCILIATION_REQUIRED`（lease 過期），而且永遠不會重跑。這是刻意的保守選擇：寧可浪費一個批次，也不重複執行。
- **approval 來源。** 這是 Orchestrator 的 task log，存放在 state_dir 而非 governance root。能直接寫 task log 與帳本檔案的人，本來就在信任邊界內（chain hash 仍可偵測竄改，但無法阻止擁有檔案寫入權者重建整條 chain）。
- **評估輸出。** 驗證的是 evaluation reference 記錄的輸出檔 SHA。evaluator 的內部判斷不在本次範圍內。
- **Layer A stub 不是 producer 產出。** 它只用來驗證帳本與證據機制。producer 的成功路徑只由 Layer B（既有 runner + Core + synthetic snapshot）佐證。
- **Layer B 依賴 DEV authorization 提案的到期日。** 隔離帳本中的 authorization 沿用 DEV proposal 的 `expires_at = 2026-11-30T23:59:59+08:00`，過期後 Layer B 測試會 fail closed（`AUTHORIZATION_EXPIRED`）。這個限制修補前就存在。
- **Codex 原始報告不可得。** 若原報告中的攻擊路徑與本文件重新推導的不同，需由 Codex 複審補充。

## 6. Engineering Verdict

- **Engineering：三項 P0 已修補。** 在 Claude 可執行的環境中，重新推導的攻擊已無法重現；必要的 regression tests 與本機三組套件都已通過，唯一例外是已知的 `test_15`。CI（3.11 / 3.12）與 Colab acceptance 的結果以 PR #13 及新的 Drive archive 為準。新 archive 為 `executions/<ts>_9f73a56/`（notebook `PHASE5B2-EXECUTION-v3` pin `9f73a56`：與 `86b068b` 相比只差文件、README 與 notebook，程式完全相同；v2 因 pin 之後改了 README 被 Version Sync 正確擋下），不覆寫修補前的 `executions/20261108_115819_019db14`。
- **B2 範圍：** `SCOPED_GO (SINGLE_EXECUTOR_ONLY)`，前提是雲端 acceptance 通過。
- **Security：不宣告 GO。** 狀態為 `FIXED_PENDING_INDEPENDENT_REVIEW`，交由 Codex 只針對 P0-1 / P0-2 / P0-3 做獨立複審。PR #13 維持 Draft、不 merge，也不進入 Phase 5-B3。
