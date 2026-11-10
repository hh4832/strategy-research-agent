# Phase 5-B3-B：受控真實研究執行工程（Controlled Real Research Execution Engineering）

| 項目 | 值 |
|---|---|
| 分支 | `feature/phase5b3b-controlled-real-research`（Draft PR，base `main`） |
| Baseline | `main` = PR #14 merge commit `5a739e0aa481df4295041d5326e521c6d9ab1695` |
| 程式 pin | `d9afac9d079d8cf5c3f3f2e1a3425bb1e3ff4e90` |
| Core | `b16179a7cc077c36046574769a32c367fa476cae`，**未修改** |
| 範圍 | **只做執行功能的開發與測試。** 不授權真實研究、不啟用正式 Runner、不執行新的真實市場回測、不讀 Holdout、不建立或核准正式授權、不重置額度 |
| 驗收 | [`PHASE5B3B_ACCEPTANCE.md`](PHASE5B3B_ACCEPTANCE.md)、notebook `notebooks/Phase5B3B_Controlled_Execution_Acceptance.ipynb` |

## 0. 四個層次（不得混為一談）

| 層次 | 目前狀態 | 由誰／如何改變 |
|---|---|---|
| 1. Engineering Readiness | **SCOPED_GO**：B3B-01..10 全部 PASS（Colab `phase5b3b/20261110_091207_d9afac9`） | 本階段 |
| 2. Research Authorization | **NOT_GRANTED**：repo 正式帳本中沒有任何核准的授權指名 executor（由 `real_acceptance.production_state` 即時讀取） | 研究負責人以 `research_authorization_admin approve` 核准一份新授權，必須含 `executor`、spec 0.1.1 sha 等，並以 GitHub 正式核准完成 binding |
| 3. Real Execution | **NOT_PERFORMED** | 只有層次 2 成立後、在另一個明確授權的階段（B3-C）才可能執行 |
| 4. Research Result Acceptance | **NOT_APPLICABLE**（沒有真實結果） | 真實執行後由既有 Phase 4-C evaluator 評估、研究負責人判讀 |

Synthetic／mock 的 PASS **不代表**真實市場 Runner 已通過正式實跑驗收。

## 1. 開始前檢查

- remote `hh4832/strategy-research-agent`，從合併 PR #14 後的 `main`（`5a739e0`）開新分支，working tree 乾淨。
- 沒有 AGENTS.md。
- 沿用的元件：
  - ResearchOnlyRunner：`research_only/runner.py`，以及 Core receipt renewal `research_only/development_run.session_config`；
  - 5-B1 binding：`binding/`；
  - 5-B2 受控執行：`execution/controlled.py`、`execution/budget.py`，含 P0-1／2／3 修補；
  - 5-B3-A readiness manifest：`readiness/`。
- **沒有重建 backtest engine，也沒有修改 market-research-core。**

## 2. 限定研究範圍（僅供 B3-C 參考；本階段沒有執行）

| 項目 | 規格 |
|---|---|
| Market／Symbol | IBKR_TSM／TSM |
| Strategy | SMA 5/20，spec 0.1.1，sha `f4f60b447013062324b7dc10aa0a569aebbd542796bef6ccfd2e723d0f5cff34` |
| Dataset | `TSM_DEV_1M_20240529_20240628_18350b3adba8e28a7e5e`（manifest `8b6fc838…`） |
| Core | `b16179a7cc077c36046574769a32c367fa476cae` |
| 研究窗口 | 2024-05-29～2024-06-28 |
| 額度 | 建議最多 1 batch（**尚未核准**） |
| Executor | 單一指定 Colab executor（**尚未核准**；本文件與測試以 `colab-hh4832-exec-01` 為例） |
| Holdout | `HOLDOUT-TSM-15M-v1`，NO_ACCESS |

目的只是驗證 Phase 5 的真實資料受控執行管線，不是證明 SMA 5/20 有效。不得改參數、不得搜尋參數、不得換股票、不得擴張日期、不得使用 Holdout，也不得把 DEV 結果當成新的獨立策略證據。

## 3. B3B-01：受控啟用路徑（fail closed）

**`execution/activation.py::RealActivationGate`**。在 precheck（任何額度預留之前）與 `execute`（ledger 授予執行之前）**都會**從權威紀錄重新評估十個條件；任一不成立就拒絕，runner 不會被呼叫：

| # | 條件 | 權威來源 |
|---|---|---|
| A01 | 已核准、有效、未過期的**新**授權；已用盡的 DEV／SMOKE 授權永遠不算 | 授權 book（chain 驗證） |
| A02 | 已登錄的 bound request，核准證據可驗證；**正式帳本**必須是 PRODUCTION（live GitHub）trust | binding log＋不可變副本＋證據檔 |
| A03 | Authorization ID 與 Bound Request Hash 一致 | binding record |
| A04 | Strategy Spec SHA256：request＝授權＝磁碟檔案＝runner 自身身分 | 授權、artefact、runner config |
| A05 | Dataset snapshot＋manifest：request＝授權＝runner config＋snapshot 上實際的 manifest 檔 | 授權、runner、磁碟 |
| A06 | Core commit：request＝**已安裝的** Core（也就是 runner 自身身分） | `core_code_identity()` |
| A07 | Batch 額度尚未消耗 | uses log |
| A08 | Executor 等於授權中指名的 `executor.executor_id`（SINGLE_EXECUTOR_ONLY） | 授權；adapter 綁定的 worker |
| A09 | 排除 Holdout：NO_ACCESS、lock 與授權相同、窗口終點 ≤ Holdout 起點 | 授權、request |
| A10 | B3-A readiness manifest 沒有 MISSING／CONTRADICTION，且 spec／snapshot／manifest／Core／成本／時序與授權一致 | `readiness.manifest.build` |

`ControlledRunnerAdapter` 的模式（`controlled.py:84`）：
- `ISOLATED_SYNTHETIC`：5-B2 原樣；
- `REAL_GATED`：非 synthetic backend，加上與本 root 帳本相符的 gate；
- `DISABLED`：其他所有情況，例如正式帳本配 synthetic backend、沒有 gate 的 real backend、gate 的帳本與 root 不符。

**沒有任何環境變數開關**（B3-A 的 C4 掃描仍然 PASS）。`REAL_EXECUTION_ENABLED` 仍是 `False`（它只控制 Phase 5-A 的舊 adapter，那條路徑永遠 DISABLED）。

Orchestrator 在**正式帳本**上建構 REAL_GATED 有三個前提（`orchestrator.py:90`）：
1. 必須用 production verifier；
2. worker 必須是 gate 的 executor；
3. 必須存在一份指名該 executor、已核准、有效、未過期的授權。

**今天的 repo 帳本中沒有這樣的授權**，所以會在建立任何 ledger 檔案之前就被拒絕（`real_acceptance.production_state` 每次驗收都即時確認）。額度**不是**建構前提：已消耗的批次仍然可以建立 orchestrator 來 recover／reconcile；新 request 會被 G7 與 A07 擋下。

## 4. B3B-02：Approval → Authorization → Bound Request → Budget → Runner

- **Approval：** 5-B1 GitHub 核准證據；正式帳本只接受 PRODUCTION trust（A02），task 核准也一樣（`APPROVAL_TRUST_NOT_PRODUCTION`）。
- **Authorization：** schema 新增 optional 的 `executor {executor_id, consistency: SINGLE_EXECUTOR_ONLY}`。舊紀錄不受影響，但 gate 要求這個欄位。新授權只能由研究負責人用既有 admin 工具核准；程式碼沒有任何可以把授權寫成已核准狀態的路徑。
- **Bound Request：** 只能由 5-B1 binding service 產生；doc 被修改後就不再是 bound request（A02、A03 與 G11）。
- **Budget：** 5-B2 原子 ledger（reserve → commit → `claim_execution` 只授予一次 → completion → evidence verified → settle）。重放、重複 dispatch、第二個 process 都只會得到 `EXECUTION_ALREADY_STARTED` 或 `EXECUTION_NOT_ELIGIBLE`。
- **未核准的草案沒有執行效力：** 草案 ID 不在 book 中，A01 FAIL。
- **舊 DEV 授權（1/1）：** `SPENT_FOREVER` 永遠不符資格，而且 uses 已滿。

**SINGLE_EXECUTOR_ONLY 的支援範圍：**
- 一份授權只指名一個 executor ID；
- 一個 orchestrator worker 使用一個 ledger 的 flock；
- 跨 process 的重放在**同一個檔案系統**上由 flock 序列化（有 3／6 個 process 的測試）。

**不宣稱**跨 Colab runtime 或 Google Drive 具備分散式原子性。

## 5. B3B-03：執行契約（`controlled.py:229` `execution_contract`、`:264` `contract_problems`）

Receipt 的 `execution_contract` 每個欄位都**讀自** request、runner 自己的 archive／config，以及磁碟上的成本檔，從不手填：
- authorization ID、request hash、spec SHA、snapshot ID、manifest SHA、Core commit；
- agent git SHA（必須是 40 碼）；
- signal time 與 execution time 規則；
- 成本設定（成本集 SHA、情境、Core profile SHA）與滑價；
- executor ID、run ID；
- runner receipt：Core integrity receipt；
- 執行結果與執行模式。

**任何欄位缺漏或與 request／reservation 矛盾都會 fail closed**：`EXECUTION_CONTRACT_INCOMPLETE`，進入 RECONCILIATION_REQUIRED，不結算。evidence verification 也會再查一次。

**Core receipt（real 模式）：**
- runner 讀的 session config 必須由既有的 `development_run.session_config`（Core `renew_development_receipt`）產生；
- session config 與已簽署的 canonical config **只能**在 receipt 欄位上不同（`RUNNER_SESSION_CONFIG_DIFFERS_BEYOND_RECEIPT`）；
- receipt 的 `verification_ref` 必須以 `DEVELOPMENT_RECEIPT_RENEWAL:` 開頭（`RUNNER_RECEIPT_NOT_RENEWED_BY_CORE`）；
- 缺 canonical config 一律 BLOCKED。

手工製作的 receipt 永遠過不了這些檢查（Layer B 有測試）。

**Budget settlement：** 結算成功後，在 orchestrator 的 archive root 底下以 O_EXCL 寫入一份不可覆寫的 `execution_records/<task_id>.json`，內容包括：
- 契約；
- binding 與核准證據 SHA；
- receipt 與 runner archive 的 SHA；
- 評估 reference；
- task archive 的 hash；
- ledger 的結算歷程。

## 6. B3B-04：Runner → Evaluator → Evidence Verification → Archive → Settlement

- Evaluator 讀的是**實際**的 runner archive（Phase 4-C；Layer B 另外在全新 process 中跑 evaluator，結果 PASS）。
- 所有輸出都追溯到同一個 request 與 git commit（P0-2／P0-3 的驗證＋契約）。
- 未驗證的 archive 不會結算；archive 遺失、損壞或被修改，就進入既有的 RECONCILIATION_REQUIRED。
- crash 後不會重新執行已 dispatch 的研究，而且不覆寫任何舊結果（O_EXCL）。
- B3-C 的 Drive 路徑：`MyDrive/Quant_Research/strategy-research-agent/`，目錄 `YYYYMMDD_HHMMSS_<git_commit>`，沿用既有 archive conventions。

## 7. 主要修改

| 檔案 | 內容 |
|---|---|
| `src/strategy_agent/execution/activation.py`（新） | RealActivationGate、`eligible_authorizations`、`SPENT_FOREVER` |
| `src/strategy_agent/execution/controlled.py` | adapter 模式、gate（precheck＋execute）、執行契約、ResearchOnlyRunnerBackend 的 real 模式（canonical config、Core receipt 檢查、`runner_receipt`） |
| `src/strategy_agent/orchestration/orchestrator.py` | REAL_GATED 的建構前提、`gated_real_execution`、recheck 帶入模式、`_execution_record` |
| `src/strategy_agent/orchestration/governance.py` | G10 的 REAL_GATED 分支（委由 gate 判斷） |
| `schemas/RESEARCH_AUTHORIZATION_SCHEMA.json` | optional `executor` |
| `src/strategy_agent/execution/real_testkit.py`（新）、`execution/testkit.py`、`binding/testkit.py`、`execution/core_world.py` | 測試世界：executor、新的測試授權 ID、mock runner、Layer B real 模式 |
| `src/strategy_agent/execution/real_acceptance.py`（新） | B3B-01..10、四層報告、封存 |
| `tests/test_phase5b3b_*.py`（新） | 31＋4＋3 個測試 |
| `.github/workflows/ci.yml` | job `phase5b3b-real-execution`（3.11／3.12，Core b16179a） |

**沒有修改：**
- Holdout、正式授權 book、uses log、舊研究封存、market-research-core；
- 策略參數、spec 0.1.0／0.1.1、ResearchOnlyRunner 的市場運算邏輯。

## 8. 尚未解決的阻擋（工程以外，需要人決定）

1. **新授權**：ID、`max_batches`（建議 1）、有效期間、`executor.executor_id`、釘選 spec 0.1.1 sha。
2. **B3-C 的 run config**：釘選新授權 ID 與 spec 0.1.1，並以 `canonical_sha` 自我雜湊；它需要授權 ID，所以本階段沒有建立。
3. **以 GitHub 正式核准完成 binding**，取得 PRODUCTION trust 與 Bound Request Hash。
4. **正式 B3-C 的 A10**：直接使用 B3-A 的 `readiness.manifest.build`；它的 candidate 已指向 spec 0.1.1。
5. **由研究負責人明確授權 B3-C 執行。**

## 9. 剩餘風險

- SINGLE_EXECUTOR_ONLY：沒有跨 runtime 的原子性，也不宣稱 exactly-once。
- Layer A 用的是 mock runner（`MOCK_TEST_ONLY_NOT_CORE`）；Layer B 用既有 runner＋真 Core，但跑在 synthetic snapshot 上。**真實資料的路徑沒有被執行過。**
- 5-B2 與 5-B3-B 的 Layer B 測試沿用 DEV 提案的到期日 2026-11-30，過期後 Layer B 會 fail closed。
- 正式帳本的 PRODUCTION trust 只能在真實的 GitHub 核准下驗證，CI／Colab 沒有實測這條路徑（設計上需要人的核准）。
