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

**GitHub CI**（HEAD `9f73a56`）：全部 job success，包含 `phase5b2-controlled (3.11)` 與 `(3.12)`。

**Colab 雲端驗收**（`executions/20261108_153034_9f73a56`；證據副本 `docs/evidence/phase5b2/20261108_153034_9f73a56/`；ZIP sha256 `f76c5dff…3858`）：
- 全新 runtime，Python 3.13.18；pin `9f73a56`，checkout 乾淨；pin 之後只有 docs 與 notebook 變更。
- 回歸 594 passed / 64 skipped（無 Core）；B2 + security suites 66 passed（Core b16179a）。
- B2-01..B2-10 全部 PASS；Layer B PASS（Orchestrator 流程，fresh-process evaluator PASS）；`cloud=true`；SCOPED_GO（SINGLE_EXECUTOR_ONLY）。
- `security_remediation.json`：P0-1（10 tests）、P0-2（10）、P0-3（15）皆 `FIXED_PENDING_INDEPENDENT_REVIEW`，0 failed。
- 雲端重現：P0-1 runner 呼叫 1 次（uses 1 筆）；P0-2 四種竄改都是 RECONCILIATION_REQUIRED；P0-3 Layer A 與 Layer B 皆 BLOCKED，runner 呼叫 0。
- Layer A、Layer B 帳本皆為 `RESERVED → COMMITTED → EXECUTION_STARTED → EXECUTION_COMPLETED → EVIDENCE_VERIFIED → CONSUMED`，uses 1 筆。
- ARCHIVE_INDEX 列出的 10 個檔案 SHA 全部吻合，ZIP 內沒有額外檔案。
- 修補前的 `executions/20261108_115819_019db14` 保留，未被覆寫。

## 5. 仍存在的限制

- **`SINGLE_EXECUTOR_ONLY`。** fcntl 鎖只在單一主機 / 檔案系統上有效，不是跨 Colab runtime 或 Google Drive 的分散式鎖。不宣稱 exactly-once，只保證每個帳本 at-most-once dispatch，以及每個 reservation 最多一次 backend 呼叫。
- **授權窗口。** `claim_execution` 寫入 `EXECUTION_STARTED` 時，backend 尚未啟動。若 process 在這之後、backend 啟動前 crash，帳本會停在 STARTED；恢復時進入 `RECONCILIATION_REQUIRED`（lease 過期），而且永遠不會重跑。這是刻意的保守選擇：寧可浪費一個批次，也不重複執行。
- **approval 來源。** 這是 Orchestrator 的 task log，存放在 state_dir 而非 governance root。能直接寫 task log 與帳本檔案的人，本來就在信任邊界內（chain hash 仍可偵測竄改，但無法阻止擁有檔案寫入權者重建整條 chain）。
- **評估輸出。** 驗證的是 evaluation reference 記錄的輸出檔 SHA。evaluator 的內部判斷不在本次範圍內。
- **Layer A stub 不是 producer 產出。** 它只用來驗證帳本與證據機制。producer 的成功路徑只由 Layer B（既有 runner + Core + synthetic snapshot）佐證。
- **Layer B 依賴 DEV authorization 提案的到期日。** 隔離帳本中的 authorization 沿用 DEV proposal 的 `expires_at = 2026-11-30T23:59:59+08:00`，過期後 Layer B 測試會 fail closed（`AUTHORIZATION_EXPIRED`）。這個限制修補前就存在。
- **Codex 原始報告不可得。** 若原報告中的攻擊路徑與本文件重新推導的不同，需由 Codex 複審補充。

## 6. Engineering Verdict

- **Engineering：三項 P0 已修補。** 在 Claude 可執行的環境中，重新推導的攻擊已無法重現；必要的 regression tests 與本機三組套件都已通過，唯一例外是已知的 `test_15`。CI（3.11 / 3.12）全綠；Colab 雲端驗收 `executions/20261108_153034_9f73a56` 為 B2-01..B2-10 全部 PASS，三項皆 FIXED_PENDING_INDEPENDENT_REVIEW。新 archive 為 `executions/<ts>_9f73a56/`（notebook `PHASE5B2-EXECUTION-v3` pin `9f73a56`：與 `86b068b` 相比只差文件、README 與 notebook，程式完全相同；v2 因 pin 之後改了 README 被 Version Sync 正確擋下），不覆寫修補前的 `executions/20261108_115819_019db14`。
- **B2 範圍：** `SCOPED_GO (SINGLE_EXECUTOR_ONLY)`，已由雲端驗收確認。
- **Security：不宣告 GO。** 狀態為 `FIXED_PENDING_INDEPENDENT_REVIEW`，交由 Codex 只針對 P0-1 / P0-2 / P0-3 做獨立複審。PR #13 維持 Draft、不 merge，也不進入 Phase 5-B3。

---

## 7. P0-2 殘留漏洞修補（Codex 獨立複審第二輪）

| 項目 | 值 |
|---|---|
| 複審結果（Codex） | P0-1 FIXED、**P0-2 STILL_EXPLOITABLE**、P0-3 FIXED → Security **NO_GO / FIX_BEFORE_MERGE** |
| 起始 HEAD | `126813670f6ccd923b950b4da40f420d68b5b357`（與 brief 預期一致） |
| 重現 commit | `1cad02e`（只加攻擊腳本與修補前結果，未改程式） |
| 修補 commit | `1bec8e0aee004d97ba8603f9ecb75fc20b538801` |
| Notebook | `PHASE5B2-EXECUTION-v4`，pin `1bec8e0` |

> Codex 的 `/workspace/security-review-reports/pr13-remediation/SECURITY_REVIEW.md` 在本環境同樣不存在（`/workspace` 不存在），Claude 沒有讀到這份報告。攻擊依 brief 描述的原始路徑重現：修改或刪除 task archive 的 `research_request.json`，看是否仍能 EVIDENCE_VERIFIED → CONSUMED。

### 7.1 Root cause

第一輪修補驗證了 **execution** archive（runner 產出，MANIFEST pin 記錄在帳本）和 receipt。但對 Orchestrator 寫的 **task** archive，`verify_execution_evidence` 只做了一件事：`archive_ref is not None and not Path(archive_ref).is_dir()`（`1268136` controlled.py:239）。它沒有讀取任何檔案內容，所以 `research_request.json` 被刪除、修改或換成別的 request，結算照樣通過。task archive 唯一的「清單」是 task log 裡 ARCHIVED 事件的 `files_sha256`。task log 也在 state_dir，攻擊者可以把檔案和清單一起改寫，再重算整條 chain。所以這份清單不能當唯一的信任來源。

### 7.2 `research_request.json` 的可信 hash 綁定

1. **執行前在帳本固定（ledger commitment）。** `reserve(..., request_file_sha256=…)`（budget.py:166；orchestrator.py:393）在 uses-log 臨界區內，把 task archive 之後必須寫出的 `research_request.json` **確切位元組**的 SHA256 寫進 RESERVED 事件。位元組的產生方式見 `identity.archive_request_bytes`（identity.py:185），與 `orchestrator._dumps` 的輸出相同，有測試證明。這發生在 `EXECUTION_STARTED` 之前，攻擊者事後改 archive 或 task log 都動不到它；帳本本身有 chain hash。
2. **結算前比對**（`task_archive_problems`，controlled.py:197）：
   - 檔案必須存在、是一般檔案（不是 symlink）、可以解析成 JSON、通過 request schema；
   - 原始位元組 SHA == 帳本承諾；
   - `request_hash(parsed)` == reservation 的 `request_sha256`；receipt 的 `request_sha256` 已在先前驗證中與 reservation 比對過；
   - 帳本承諾本身必須能從 binding store 的不可變 bound request 重新推得，而且 bound request 檔案的 SHA 必須等於 binding record。
3. **其他 task archive 證據**：
   - 檔案集合必須正好是 7 個指定檔案；
   - 每個檔案都必須等於寫入當下記錄的 hash；
   - `execution_reference.json` 必須對應帳本的 receipt / manifest / reservation；
   - `evaluation_reference.json` 必須與評估事件一致；
   - `run_info.txt` 的 `request_hash` 必須正確。
4. **不重建、不修復。** 驗證失敗不會重寫任何檔案；帳本直接進入 `RECONCILIATION_REQUIRED`，不 release、不重跑。

### 7.3 Settlement 時如何阻擋竄改（TOCTOU）

| 時間點 | 防護 |
|---|---|
| Archive 建立完成 | 寫入當下的 hash 記錄在 task log；`research_request.json` 另以執行前的帳本承諾為準 |
| Evidence Verification | 從磁碟完整驗證（§7.2 加上第一輪的 receipt / archive / 身分） |
| 寫入 `EVIDENCE_VERIFIED` | 只在驗證通過後寫入，並把**已驗證的 task archive 檔案 hash** 一併寫進帳本（`task_archive_files_sha256`） |
| 寫入 `CONSUMED` | `settle()` **必須**帶 `recheck`（budget.py:244），沒有就 `SETTLE_REFUSED`。`recheck` 在帳本臨界區內、CONSUMED 寫入前一刻重新從磁碟驗證全部證據，並要求 task archive hash 與 EVIDENCE_VERIFIED 的承諾完全相同（controlled.py:327 `TASK_ARCHIVE_CHANGED_AFTER_VERIFICATION`）。不一致就 `SETTLE_EVIDENCE_CHANGED`，進入 `RECONCILIATION_REQUIRED` |

**保證範圍。**
- 帳本的 flock 只能序列化遵守帳本鎖的寫入者（本專案的 Orchestrator / Ledger）。
- 若有不受信任的程序在檢查與 CONSUMED 寫入之間的微小窗口內持續改檔，flock 擋不住；它也擋不住 CONSUMED **之後**的修改。
- 本修補保證的是：CONSUMED 寫入前一刻的磁碟內容，與執行前的帳本承諾以及 EVIDENCE_VERIFIED 的承諾一致；而且帳本永久保存這些承諾，事後任何竄改都可以偵測。
- 不宣稱跨 runtime 的 atomicity 或 exactly-once。

### 7.4 修補前後的原始攻擊

結果檔：`before_p02_task_archive_1268136.json` / `after_p02_task_archive_1bec8e0.json`。

| 攻擊 | 修補前 `1268136` | 修補後 `1bec8e0` |
|---|---|---|
| 刪除 `research_request.json` | EVIDENCE_VERIFIED → **CONSUMED** | RECONCILIATION_REQUIRED（無 EVIDENCE_VERIFIED） |
| 修改合法 JSON 中的 request identity | **CONSUMED** | RECONCILIATION_REQUIRED |
| 只改非身分位元組（內容 hash 改變） | **CONSUMED** | RECONCILIATION_REQUIRED |
| 換成另一個 request 的 JSON | **CONSUMED** | RECONCILIATION_REQUIRED |
| 換成無效 JSON | **CONSUMED** | RECONCILIATION_REQUIRED |
| 同時改檔案與清單（重算整條 task log chain） | **CONSUMED** | RECONCILIATION_REQUIRED |
| 驗證後、結算前竄改 | **CONSUMED** | EVIDENCE_VERIFIED 之後被 in-lock recheck 擋下 → RECONCILIATION_REQUIRED |
| 對照組：證據正常 | CONSUMED | CONSUMED（剛好一次） |

所有案例的 runner 呼叫次數都是 1，uses 都是 1 筆。

### 7.5 測試

新增 regression tests（`test_P0_2_*`），security suite 從 29 個增為 43 個：
- 6 種竄改（parametrize）
- 驗證後竄改
- 正常結算剛好一次（並確認帳本承諾的 hash 等於磁碟上的 hash）
- 執行前承諾存在，且與 archive 的渲染方式相同
- 沒有承諾的 reservation 永遠無法驗證（即使檔案位元組正確）
- 其他 3 種 task archive 檔案被竄改
- `settle()` 沒有 recheck 就拒絕，recheck 發現變更也拒絕

本機結果：

| 環境 | 結果 |
|---|---|
| 無 Core | 608 passed / 64 skipped |
| Core `699e3ad` | 702 passed / 13 skipped |
| Core `b16179a` | 722 passed / 4 skipped / 1 failed = 已知 `test_15` |
| B2 + security suites（Core `b16179a`） | 80 passed |
| 本機 acceptance | B2-01..09 PASS；B2-10 BLOCKED（無 Drive）；Layer B PASS（真 runner 的路徑在新檢查下仍會結算）；P0-1/2/3 = `FIXED_PENDING_INDEPENDENT_REVIEW` |

P0-1 與 P0-3 的 regression tests 全部照舊通過，語意未改。

### 7.6 證據的三種形式

- **ZIP（正式封存內容）：** 由 Colab 從 Drive 的 `executions/<ts>_1bec8e0/` 打包，只包含頂層的 11 個檔案（含 `ARCHIVE_INDEX.json`），由 `--verify-archive --zip` 驗證。
- **GitHub 證據副本：** `docs/evidence/phase5b2/<ts>_1bec8e0/`，是 ZIP 頂層檔案的複本。
- **未提交的 `work/` supporting artifacts：** Drive 封存下的 `work/` 目錄，包含 Layer A / B 的隔離帳本、task archive、runner archive 等。它的 tree hash 記錄在 `ARCHIVE_INDEX.json` 的 `work_tree`。這些檔案**不在** ZIP 和 GitHub 副本中，因此光憑 GitHub 副本無法獨立驗證 `work/` 的每個檔案；要驗證需存取 Drive 上的原封存。

### 7.6.1 雲端驗收結果（`executions/20261108_192245_1bec8e0`）

- **環境：** Notebook `PHASE5B2-EXECUTION-v5`（GitHub HEAD `ad97624`），全新 runtime，Python 3.13.18，`cloud=true`。程式 pin `1bec8e0`，checkout 乾淨；pin 之後只有 docs 與 notebook 變更。
- **回歸：** 607 passed / 65 skipped（無 Core；比本機多 1 個 skipped，是環境條件造成的 skip，沒有失敗）。B2 + security suites 80 passed（Core b16179a）。
- **B2：** B2-01..B2-10 全部 PASS；Layer B PASS；SCOPED_GO（SINGLE_EXECUTOR_ONLY）；repo 帳本未變。
- **安全修補：** P0-1（10 tests）、P0-2（24）、P0-3（15）皆 `FIXED_PENDING_INDEPENDENT_REVIEW`，0 failed。
- **雲端重現：** P0-2 殘留攻擊 7/7 為 `RECONCILIATION_REQUIRED`（「驗證後竄改」在 EVIDENCE_VERIFIED 之後被 in-lock recheck 擋下），對照組 CONSUMED 一次；第一輪 P0-2 四種竄改仍為 `RECONCILIATION_REQUIRED`；P0-1 runner 呼叫 1 次、uses 1 筆；P0-3 Layer A / B 皆 BLOCKED，runner 呼叫 0。
- **帳本：** Layer A / Layer B 皆為 `RESERVED → COMMITTED → EXECUTION_STARTED → EXECUTION_COMPLETED → EVIDENCE_VERIFIED → CONSUMED`，uses 1 筆。
- **封存：** Colab 端 `--verify-archive --zip` PASS（10 個檔案）；Claude 對上傳的 ZIP 重新核對，`ARCHIVE_INDEX.json` 列出的 10 個檔案 SHA 全部吻合、無額外檔案。ZIP sha256 `b0d5afff291af0161a307c1ff38b48a492e2ef7540c0a924d415a1273ea1e2b8`。
- **證據的三種形式（依 §7.6）：**
  - ZIP：上述 11 個頂層檔案（含 `ARCHIVE_INDEX.json`）。
  - GitHub 副本：`docs/evidence/phase5b2/20261108_192245_1bec8e0/`，與 ZIP 內容相同。
  - `work/`：只在 Drive 上；它的 tree hash 記錄在 `ARCHIVE_INDEX.json` 的 `work_tree`，Claude **未能**取得 `work/` 本身，因此無法獨立驗證其中的檔案。
- **同日其他 run 不列為證據：** `1bec8e0` 的兩次 v4 執行（`cloud=false`，Drive 未授權）與兩次在舊 runtime 上 STOP 的執行，都只是除錯紀錄；舊封存 `20261108_115819_019db14`、`20261108_153034_9f73a56` 保留未覆寫。

### 7.7 尚未解決的限制

- 同 §5：SINGLE_EXECUTOR_ONLY；不受帳本鎖約束的程序在微小窗口內或 CONSUMED 之後的修改，只能事後偵測，無法事前阻止。
- 這個承諾機制只在 reservation 帶有 `request_file_sha256` 時有效。測試工具直接呼叫 `reserve` 而沒有給承諾時，永遠無法結算（fail closed，有測試）。
- Codex 第二輪報告不可得；若原始攻擊另有細節（例如不同的檔案替換方式），需由 Codex 以原始步驟複驗。
