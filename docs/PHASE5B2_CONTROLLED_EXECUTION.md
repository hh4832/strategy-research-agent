# Phase 5-B2：Controlled Research Execution & Atomic Budget Governance

> 範圍：把既有的 `ResearchOnlyRunner` 接到 Orchestrator 的受治理執行介面，並加上原子化的研究額度控管。
> 只在**隔離的 synthetic 環境**中可執行；**正式 `RESEARCH_ONLY_REAL` 維持 DISABLED**。
> 沒有新策略、新回測引擎、真實市場執行、正式額度消耗、Holdout 讀取或券商 API。

## 1. Architecture

```
Proposal (A2) → PENDING Request → [B1] verified binding → Bound Request
  → [B1] verified human approval (APPROVE_TASK, GitHub evidence; isolated: TEST_FIXTURE)
  → Orchestrator.dispatch（每個 task 一把排他鎖 + RUNNING claim nonce）
      QUEUED: Governance G1–G11 重驗 → approval／binding 證據重新驗證
      ControlledRunnerAdapter.precheck（identity／PIT pins，任何額度動作之前）
      BudgetLedger.reserve   ── 臨界區 = uses log 的 flock（與 legacy guard.consume 共用）
      BudgetLedger.commit    ── 同一個臨界區寫入權威的 uses record → 從這一刻起才可能有 side effect
      RUNNING → backend.run（既有 Runner／synthetic fixture）→ EXECUTION_RECEIPT.json（O_EXCL）
      EVALUATING → 既有 Phase 4-C evaluator → EVALUATED → ARCHIVED → BudgetLedger.settle（CONSUMED）
```

| 模組 | 角色 |
|---|---|
| `execution/budget.py` | B2-1 原子額度帳本：建在既有的授權書、uses log 之上，另加一份 append-only 的 `research_budget_events.jsonl` |
| `execution/controlled.py` | B2-2 `ControlledRunnerAdapter`、執行收據、`SyntheticFixtureBackend`、`ResearchOnlyRunnerBackend` |
| `orchestration/orchestrator.py` | B2-4 `_dispatch_controlled`、`_settle`；crash recovery 時把預留標為 reconciliation |
| `orchestration/governance.py` | G10：只在「隔離帳本 + 已啟用的受控 adapter」時 PASS；正式帳本永遠 FAIL |
| `execution/testkit.py`、`execution/core_world.py` | Layer A／Layer B 的 SYNTHETIC 測試世界（都在 repo 外的暫存目錄） |
| `execution/acceptance.py` | B2-01..B2-10，判定依據是真實的 junit 結果與 E2E artefact |

## 2. Market Research Execution Boundary

- `ControlledRunnerAdapter.enabled` 是**唯讀的計算值**：governance root 必須帶 `ISOLATED_TEST_GOVERNANCE_ROOT.json` 標記，**而且** backend 的 `synthetic is True`。沒有 setter，也不讀任何環境變數（測試中設 `ENABLE_REAL_RUNNER=true` 也無效）。
- 對正式帳本建立受控 Orchestrator 會直接 `ValueError`，連 ledger 檔都不會產生。
- 只有 `ControlledRunnerAdapter` 能取代 `RESEARCH_ONLY_REAL` 的 adapter；其他會消耗額度的 adapter 一律 `BUDGET_RESERVATION_REQUIRED`。
- `ISOLATED_TEST` 核准只能花隔離帳本的額度；正式帳本仍要求 PRODUCTION 證據（B1 不變）。
- `ResearchOnlyRunnerBackend` **絕不呼叫 `runner.run()`**，因為那會自己再 consume 一次。執行前在預留之前跑 Runner 自己的 `probe`；commit 之後才跑 Runner 自己的 `data_checks`、`_execute`（Core 載入與執行），PnL／成本／訊號邏輯都在既有程式碼裡。

## 3. Budget State Machine（`research_budget_events.jsonl`）

| 狀態 | 意義 | 可轉到 |
|---|---|---|
| AVAILABLE | 剩餘容量 = `max_batches − |uses| − |RESERVED 且尚無 uses|` | RESERVED |
| RESERVED | 佔住容量，**沒有任何 side effect** | COMMITTED、RELEASED、RECONCILIATION_REQUIRED |
| COMMITTED | 已寫入權威 uses record（額度已消耗）；從此才可開始執行 | CONSUMED、RECONCILIATION_REQUIRED |
| CONSUMED | 有執行收據、archive 已封存並結算（終態） | — |
| RELEASED | commit 前釋放，且證明沒有 side effect：planned run id 不在 uses log 中（終態） | — |
| RECONCILIATION_REQUIRED | 已（或可能已）消耗，但結果未知或不完整；容量維持已用，**不自動重跑** | — |

每筆事件記錄：authorization_id、request_sha256、task_id、reservation_id、batch_units、attempt_id、approval_evidence_sha256、binding_id、transaction_timestamp、worker_id、git_commit、event_type、previous_event_sha256，加上 ChainedLog 自身的 seq／prev_sha256／record_sha256。

不平行計數：容量 = **uses（權威）** + 尚未 commit 的預留。binding 不佔容量，它只決定資格；B1 綁定時已限制綁定數不超過剩餘額度。commit 時在同一個臨界區重新讀取授權狀態與 uses。

## 4. Request／Execution Identity（每次執行都記錄）

記錄在 `execution_receipts.json → execution_identity`：

- Proposal、Bound Request、Approval Evidence 的 hash，以及 Authorization ID、Binding ID
- Git commit、Core commit
- Strategy Spec SHA256、Dataset Snapshot ID、Dataset Manifest SHA256
- Signal Time Rule、Execution Time Rule、Cost Scenarios、Holdout Policy、Research Window
- Batch Reservation ID、Execution Receipt（含 use record sha）、Output Manifest SHA256

## 5. PIT Policy

`signal_time（CORE_MODELED_AVAILABLE_AT）→ available_at → execution_time（NEXT_ELIGIBLE_BAR_OPEN）→ outcome_period`。

- precheck 會拒絕：其他 PIT 規則、缺少或為 `PENDING` 的 pin、`holdout_policy.access ≠ NO_ACCESS`。
- Layer B 另外要求 Runner config 的 snapshot、cost sha、authorization 與 request 一致；不一致就 BLOCKED，**不會改用最新資料**。
- 時間檢查由既有 Runner／Core 執行，並寫入 `execution_diagnostics.json`。

## 6. Concurrency Semantics

- **單一主機、單一檔案系統：** 執行緒與多 process 都由 fcntl 序列化。實測為 8 個 spawn process 同時預留 `max_batches = 2` 的授權，結果只有 2 個成功。
- **dispatch：** 每個 task 一把非阻塞排他鎖，加上 RUNNING claim nonce。實測 6 個並發 dispatcher，只執行 1 次。
- **重試與重複提交：** 同一 task 重試會回傳原預留（冪等）；同一 request 由另一個 task 提交會得到 `DUPLICATE_REQUEST`；同一 run id 會得到 `RUN_ID_ALREADY_USED`。
- **`SINGLE_EXECUTOR_ONLY`：** fcntl **不是**跨 Colab runtime／Google Drive 的分散式鎖。所以帳本只接受一個已註冊的 executor，第二個 executor id 會被拒（`SECOND_EXECUTOR_REFUSED`），直到第一個在沒有未結預留的狀態下 retire。**不宣稱分散式 atomicity。**
- **Exactly-once 不宣稱：** 保證的是每個帳本 at-most-once dispatch。Runner 沒有外部冪等鍵，收據只能證明「某次執行產生了這份 archive」。

## 7. Crash Recovery Semantics

| 時點 | 處理 |
|---|---|
| reserve 之後、commit 之前 | 可以 RELEASE，需附證明（planned run id 不在 uses log） |
| uses 已寫入、COMMITTED 事件尚未寫入 | `recover()` 轉 RECONCILIATION_REQUIRED；RELEASE 會被拒 |
| commit 之後執行失敗或 crash | RECONCILIATION_REQUIRED；task FAILED；retry 被拒（consumes_budget）；再次 dispatch 不重跑 |
| RUNNING 時 process 死掉 | lease 過期後 `recover()`：task FAILED_NOT_REEXECUTED，預留轉 RECONCILIATION_REQUIRED |
| Runner 完成但 archive 不完整 | RECEIPT_ARCHIVE_INCOMPLETE，轉 RECONCILIATION_REQUIRED，絕不 ARCHIVED |
| Evaluator／封存失敗 | EVALUATION_OR_ARCHIVE_FAILED_NO_RERUN，轉 RECONCILIATION_REQUIRED |
| 帳本損毀 | LEDGER_CORRUPTED（fail closed），不 dispatch |

## 8. Security Threat Model（B2 增量；B1 威脅模型仍適用）

| 威脅 | 對策 |
|---|---|
| 跳過 Approval／Binding／Governance／Reservation 直接呼叫 Runner | 只有 `_dispatch_controlled` 會呼叫 `execute`；`execute` 沒有 committed reservation 時一律 `NO_COMMITTED_RESERVATION` |
| 只在 QUEUED 前檢查一次 | QUEUED 時重驗 G1–G11 與證據；reserve 與 commit 各自在臨界區重讀授權與 uses |
| 超額 | 單一臨界區、權威 uses、多 process 實測 |
| 重複正式執行 | dispatch 鎖、claim、預留冪等、run id 唯一、retry 拒絕 |
| 靠環境變數啟用 | `enabled` 由「帳本標記 + backend 型別」計算，不讀環境變數 |
| Test fixture 寫入正式帳本 | 對正式帳本建立受控 Orchestrator 會 `ValueError`；repo 內不存在 `registry/research_budget_events.jsonl`（測試確認） |
| 額度被 legacy 路徑同時消耗 | 共用 uses-log flock；commit 時重讀 uses |
| Holdout | precheck 檢查 NO_ACCESS；Runner／Core 檢查 holdout 邊界；run_info 為 `holdout_accessed=false` |
| B1 carry-over | revocation ledger 缺失或損毀即 fail closed；PAT 不交給 agent；正式核准不可用測試來源 |

## 9. Known Limitations

1. **`SINGLE_EXECUTOR_ONLY`：** 跨 runtime 或 Drive 沒有一致性保證。兩個 Colab runtime 各自持有一份帳本副本時，帳本本身無法互相看見對方。
2. **Exactly-once 未宣稱，只保證 at-most-once dispatch。**
3. **Layer A 的 Evaluator 結果是 FAIL：** fixture archive 帶有另一個授權的身分，與隔離世界的帳本不一致。這正好證明 evaluator 會拒絕不一致的 archive。成功路徑由 Layer B 證明：既有 Runner + Core，C1–C8 PASS。
4. **Layer B 只做到 Runner 與帳本的整合：** 它直接使用帳本，沒有經過 A2 提案 → 綁定 → Orchestrator 這段。原因是 A2 exporter 要求 spec 的 `data_requirements` 含有 dataset，而 DEV 授權綁定的 synthetic snapshot 不在真實 TSM spec 內。完整的 Orchestrator 流程由 Layer A 證明。
5. `ResearchOnlyRunnerBackend` 使用 Runner 的內部方法 `_execute`，屬於薄型包裝；Runner 介面若改變需要同步更新。
6. 真實執行的啟用需要另一份 user 授權、PRODUCTION 核准證據，以及跨 runtime 一致性方案。這些都不在本階段。

## 10. Devil's Advocate

- 「Synthetic 能跑，就代表正式可以開了？」不是。正式帳本在建構時就拒絕，G10 永遠 FAIL；B2 只證明「若要開，額度與執行會被這樣控管」。
- 「fcntl 在 Colab + Drive 上真的可靠嗎？」不可靠，所以限制為單一 executor；這是限制，不是保證。
- 「Layer A 的 evaluator FAIL 會不會表示整合是壞的？」它表示 evaluator 正確拒絕了身分不一致的 archive；同一套程式在 Layer B 對一致的 archive 給出 PASS。
- 「RECONCILIATION_REQUIRED 會不會卡死額度？」會，而且是刻意的。沒有收據就不能證明沒消耗，保守地算已用，交給人處理。

## 11. Engineering Verdict

最佳可能判定為 **SCOPED_GO**：核心完成，但一致性限於 `SINGLE_EXECUTOR_ONLY`。實際判定見 `PHASE5B2_ACCEPTANCE.md`。
