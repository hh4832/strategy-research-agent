# M2 — Experiment Registry, Holdout Lock, Family Canonicalisation, Budget, Signals

| 項目 | 內容 |
|---|---|
| 文件版本 | v1.0（2026-11-03） |
| 基準 | Phase 3 Preparation PR #1（`7a73e77`）；架構沿用 `STRATEGY_AGENT_ARCHITECTURE.md`，未重新設計 |
| 邊界 | 未修改 `market-research-core`；未接 Phase 2 API（runner 透過 `ports.BacktestPort`，測試用 FakePort） |
| 判定 | 見 §8 |

## 1. 審查決策（2026-11-03）與落實方式

| # | 決策 | 本 PR 的落實 |
|---|---|---|
| Q1 | TXF／台股時間戳由 Codex 依來源證據確認，未確認前不得假設 | 訊號函式把 `available_at` 當成**必填輸入**，從不由 `ts` 推導；opening range 用完成時間（`available_at`）定義；測試證明 start-label 與 end-label 兩種標記下決策完全相同 |
| Q4 | `market-research-core` 為唯一正式回測執行器；Oracle 只作獨立驗收 | Runner 只呼叫 `BacktestPort`；`strategy_agent` 任何模組都不 import `benchmark` 或 `market_research_core`（測試掃描原始碼）；Runner 拒絕把 oracle 當成 port（`OracleNotAllowed`）；oracle 程式碼未被修改 |
| Q5 | 跨 exclusion window 交易 invalidated，不計入有效績效，並檢查排除後樣本充分性 | B11 改為核准狀態；`BacktestReport.invalidated_round_trips`；`evaluation/sufficiency.py`（Q5-1／Q5-2／Q5-3）；Runner 在每筆 COMPLETED 寫入 `sample_sufficiency`；Gate G4 已更新 |
| Q7 | Final holdout 由研究負責人事前核准並鎖定，Agent 不得自行設定或反覆測試 | `governance/holdout_locks.jsonl` + `approvers.json` + CI append-only；Agent 端 API 無寫入方法；沒有 lock 的資料集一律拒絕（H01）；每個 family 對同一 lock 只能存取一次，失敗也算（H06，§9 改為原子 reservation）。CODEOWNERS 尚未被 GitHub 強制（§9.3） |

## 2. Append-only Experiment Registry

- `registry/chain.py`：雜湊鏈 JSONL。每行含 `seq`、`prev_sha256`、`record_sha256`；只能 append；寫入前先驗證整條鏈，鏈已損壞則拒絕寫入；`fcntl` 檔案鎖處理並行寫入。
- `registry/experiments.py`：schema v1.1 驗證、生命週期狀態機（終態不可再寫）、family 試驗計數、holdout 存取紀錄、依時間統計。
- 記錄的結果：`COMPLETED`、`FAILED`、`TIMEOUT`、`CANCELLED`、`REJECTED_SPEC`、`REJECTED_DATA`、`REJECTED_CONTRACT`、`REJECTED_GOVERNANCE`、`BUDGET_EXCEEDED`，以及中間事件 `QUEUED`、`RUNNING`。
- `governance/append_only.py`（CI）：PR 上每個受管 JSONL 必須以 base 分支檔案的**完整位元組**為前綴，防止「整條鏈重算」型竄改。
- Schema v1.1 為**增量擴充**：新增 `TIMEOUT`、`REJECTED_GOVERNANCE`、鏈欄位、`structure_sha256`／`params_sha256`／`family_assignment`、`holdout_lock_id`、`sample_sufficiency`；`engine_git_sha` 允許 `NOT_RUN`（未進引擎即被拒絕時）。

## 3. Holdout lock（H01–H08）

| 代碼 | 規則 |
|---|---|
| H01 | 規格引用的資料集沒有研究負責人核准的 lock → 拒絕 |
| H02 | 規格的 `holdout_lock_id` 與核准 lock 不符 |
| H03 | 開發窗口（含 WF test）與任何 holdout 窗（含已撤銷的）重疊；半開區間，結束於 holdout 起點允許 |
| H04 | Final holdout 只允許 FROZEN 規格 |
| H05 | Final holdout 窗口必須與 lock 完全一致（不可切片偷看） |
| H06 | 同一 family 對同一 lock 只能一次；FAILED／TIMEOUT／CANCELLED 也算；改名或升版仍是同 family |
| H07 | Final holdout 只在 1× 成本評估 |
| H08 | `holdout_access` 旗標必須與角色一致 |

Lock 新增流程：研究負責人執行 `python -m strategy_agent.governance.holdout_admin approve ...` → 產生的新行以 PR 提交 → 研究負責人審核 → 合併後生效。CODEOWNERS 列出 `/governance/ @hh4832`，但 GitHub 目前**不強制**（§9.3）。工具在寫入前先對整本 book 做乾跑驗證，無效條目永遠不會寫入。撤銷不會釋放 holdout 給開發使用。

> 目前 `governance/holdout_locks.jsonl` 為空：依 Q7，所有真實資料集的回測都會被 H01 擋下，直到研究負責人核准 lock。這是預期行為。

## 4. Strategy family canonicalization

`family/canonical.py` 只保留策略「概念」：市場、時框、假說類別、合約政策、方向、指標型別／輸入／scope、規則邏輯、出場型別。以下改動**不會**產生新 family（皆有測試）：

改名（strategy_id、名稱、版本、假說文字、宣告的 family id）、指標 id 重新命名、參數改名、參數值與常數（含時間常數）、`and`/`or` 參數順序與巢狀、重複條件、鏡像比較（`lt(a,b)` ≡ `gt(b,a)`，`cross_below(a,b)` ≡ `cross_above(b,a)`）、成本、部位大小、資料快照。

指派順序：EXACT（結構雜湊相同）→ SIMILAR（同市場／時框／類別，predicate Jaccard ≥ 0.7，例如多加一個時間濾網）→ NEW。指派寫入 `registry/families.jsonl`（雜湊鏈）。Agent 宣告的 `trial_family_id` 會被記錄，但不用於計數。

試驗數 N（DSR 用）= family 內不同（結構, 參數）組合數，涵蓋所有結果（被 governance／budget 擋下、未觸及規格與資料者除外），並以預先登錄格點數為下限。

## 5. Research budget（`governance/research_budget.json`）

所有計數都**從 Registry 推導**，重啟或多個 worker 都無法歸零。

| 代碼 | 規則 | 預設 |
|---|---|---|
| B01 | 每 family 試驗上限 | 20 |
| B02 | 每日回測（進入 RUNNING）上限 | 50 |
| B03 | 每日新 family 上限 | 3 |
| B04 | 每日 wall-time 上限 | 14,400 秒 |
| B05 | 連續 FAILED／TIMEOUT 達上限 → HALTED，只有 allow-listed 研究負責人的 RESUME（`governance/controls.jsonl`）可解除 | 3 |
| B06 | 規格自身 `max_trials` | 依規格 |
| — | 單一實驗 timeout | 900 秒 |

Timeout 目前為 soft timeout：worker thread 被放棄，逾時後的結果一律丟棄並記錄 TIMEOUT。M6 會改為每個實驗獨立 process，以便強制終止。

## 6. 訊號純函式（`signals/`）

| 函式 | 規格 | 重點 |
|---|---|---|
| `sma_crossover` | `sma_crossover_tw_2330_5m` | 真正的交叉（同 segment 內需連續兩個已計算狀態）；換合約強制平倉 |
| `orb` | `orb_txf_1m` | 開盤區間以 `available_at` 定義；區間不完整則當日不交易；收盤確認突破；反向區間停損；時間平倉 |
| `wilder_atr`、`atr_breakout` | `atr_breakout_tsm_15m` | 進場門檻使用 t-1 的 ATR；停損以訊號棒收盤為參考 |

共同規則：無 I/O、無時鐘、無隨機；不合格 K 棒視同缺失（跳過、不補、不 reset）；exclusion 窗與換合約會 reset 指標。

**ATR 範例規格 v0.2.0**：停損參考由 `ENTRY_FILL_PRICE` 改為 `SIGNAL_BAR_CLOSE`，讓純函式層不需要成交回饋即可計算（成交屬於正式引擎，Q4）。這是範例參數的修正，不是架構變更。

測試：

- **手算值**：SMA 對 B02、ORB 對 B12 的預期訊號（直接讀 fixture JSON，不經 oracle 程式碼）；Wilder ATR 逐步手算。
- **Prefix invariance**：3 種訊號 × 3 組隨機序列（含缺棒、不合格棒、換合約、exclusion 窗），對**每一個**截斷點，截斷後計算的決策 = 完整序列中 `decision_ts ≤ 截斷點` 的決策。
- **Future perturbation**：截斷點之後的價格大幅改變，截斷點之前的決策不變。
- **Label independence（Q1）**：相同 `available_at`、不同 `ts` 標記方式，決策完全相同。
- **有鑑別力**：故意偷看下一根的 mutant 必定違反 prefix invariance。
- 不合格棒（價格灌水）的影響 = 移除該棒；不完整開盤區間不交易；盤中觸價不觸發；ATR 門檻確實使用 t-1。

## 7. 驗收標準與結果

| # | 標準 | 證據 | 結果 |
|---|---|---|---|
| A1 | 所有結果（成功、失敗、拒絕、超時、取消）都寫入 Registry | `test_every_outcome_is_recorded`、`test_cancel_while_running_discards_result` | PASS |
| A2 | Registry 竄改（改、刪、重排、插入）可偵測，且損壞後拒絕再寫入 | `test_tampering_detected_and_append_refused` ×4 | PASS |
| A3 | 整條鏈重算型竄改在 CI 被擋 | `test_append_only_git_check`；CI job `append-only` | PASS |
| A4 | Agent 無法建立或修改 holdout lock；無 lock 則不能回測 | `test_only_allowlisted_lead_can_approve`、`test_agent_facing_book_has_no_write_api`、`test_h01_*` | PASS |
| A5 | Holdout 每 family 只能存取一次（含失敗、含改名）；開發窗口不得觸及 | `test_h06_*`、`test_h03_*`、`test_h04_h05_h07_h08` | PASS |
| A6 | 改名、重排、改參數、改常數、鏡像、加小濾網不能規避試驗計數 | `test_family.py` ×6、`test_b01_family_cap_counts_renamed_variants` | PASS |
| A7 | 預算上限與停止規則從 Registry 推導，重啟不歸零，HALT 只有研究負責人可解除 | `test_b01`–`test_b06`、`test_b05_halt_and_lead_resume` | PASS |
| A8 | SMA／ORB／ATR 純函式通過 prefix invariance 與 future perturbation；不依賴時間戳標記語意 | `test_signals.py` ×28 | PASS |
| A9 | Oracle 保持獨立，不能成為正式執行器 | `test_runner_refuses_oracle_as_executor`、`test_strategy_agent_never_imports_oracle_or_engine`；oracle 程式碼本 PR 未修改 | PASS |
| A10 | Q5：invalidated 交易不計入有效績效，並產出樣本充分性判定 | `test_q5_invalidated_trades_excluded_and_sufficiency_flagged` | PASS |
| A11 | PR #1 成果保留：既有 78 個測試仍通過 | 完整套件 | PASS |
| A12 | 不修改 `market-research-core` | 本 PR diff 只在本 repo | PASS |

## 8. M2 判定（初版，已由 §9 更新）

初版判定為 GO（範圍限定），A1–A12 通過。合併前 code review 找出的問題與修正見 §9，最終判定見 §9.6。

已知限制，不影響 M2 GO，但會帶入後續里程碑：

1. Timeout 為 soft timeout（thread）；強制終止需 M6 的 process 隔離。
2. Registry 的防竄改依賴 CI append-only 檢查；在本機直接重寫檔案只能被偵測，無法阻止。CODEOWNERS 與 CI 目前都不是 GitHub 強制的合併條件（§9.3）。
3. 尚未核准任何 holdout lock，所以目前**無法對真實資料執行任何回測**（Q7 的預期行為）。
4. Runner 目前只跑過 FakePort；接上 `market-research-core` 仍需 Phase 2 GO 與 M1 adapter，並先通過 B01–B12。
5. Family 相似度門檻 0.7 為初始值，需用真實 Generator 產出校準。

下一步：M1（待 Phase 2 GO）；研究負責人核准第一批 holdout lock；M3 訊號接正式引擎（依 Q4，由 `market-research-core` 執行，Phase 3 提供規格與目標部位）。


---

## 9. 合併前 Code Review 修正（2026-11-03）

M2 沒有重新設計。Oracle 的程式碼與手算答案未修改；`market-research-core` 未修改。

### 9.1 Holdout 單次使用：原子 reservation（決策 1）

- **問題**：H06 原本先讀 Registry 判斷「是否用過」，之後才寫 QUEUED。兩個 worker 可能同時通過檢查。
- **修正**：`HoldoutReservations.reserve` 透過 `ChainedLog.append_if` 做 compare-and-append：檢查與寫入在同一個排他檔案鎖內完成，(lock_id, family_id) 只能有一筆，最多一個 worker 成功。Reservation 在 QUEUED **之前**寫入，且永不釋放：FAILED、TIMEOUT、CANCELLED、contract 拒絕、worker 中途崩潰，都會消耗這次機會。
- **證據**：8 個真實 process 同時 reserve，恰好 1 個成功；8 個 process 同時提交 holdout 回測，恰好 1 個 COMPLETED，其餘 7 個是 H06；FAILED、TIMEOUT、CANCELLED 三種情況之後再提交都是 H06。

### 9.2 Research Budget 並行配額：admission lock（決策 2）

- **問題**：預算檢查與 RUNNING 寫入之間沒有互斥。多個 worker 可能同時看到「還剩 1 個名額」。
- **修正**：
  - `AdmissionLock`（`fcntl` 排他鎖）涵蓋 family 指派、holdout 檢查、預算檢查、reservation、QUEUED、RUNNING，直到 RUNNING 寫入後才釋放。回測本身在鎖外執行。
  - 預算計算納入 in-flight（QUEUED／RUNNING）實驗。B04 預先扣除每個 in-flight 實驗與本次實驗的完整 timeout，所以並行時不會超支。
  - **可驗證範圍**：flock 只在同一主機、本機檔案系統上可靠，跨機器或 Google Drive 類同步資料夾無效。Registry 第一次使用時綁定主機名稱，其他主機建立 runner 會直接失敗（`AdmissionError`），不會默默競態。
- **證據**：8 個真實 process 同時提交：B02 上限 3 → 恰好 3 個；B01 上限 2 → 恰好 2 個；B04（上限 1.0 秒、timeout 0.4 秒）→ 恰好 2 個。連跑 5 次結果相同。**反證**：暫時拿掉 admission lock 後，B02 與 B04 測試失敗（過量放行），證明測試能抓到這個競態；B01 在該次未重現競態，所以 B01 的並行保證依賴同一把 admission lock，而不是靠該測試單獨證明。
- **限制**：worker 崩潰會留下永遠是 RUNNING 的紀錄，它會持續占用當日配額與 trial 數。這是保守方向；需要人工以新事件處理（M6）。

### 9.3 CODEOWNERS 是否被強制（決策 3）

以 GitHub API 查詢（2026-11-03）：

| 查詢 | 結果 |
|---|---|
| `GET /repos/hh4832/strategy-research-agent/branches/main/protection` | 404 `Branch not protected` |
| `GET /repos/.../rulesets` | `[]` |
| `GET /repos/.../rules/branches/main` | `[]` |
| `GET /repos/.../codeowners/errors` | `[]`（檔案語法正確） |

**結論：CODEOWNERS 沒有被強制。** `.github/CODEOWNERS` 只列出預期的審核者；CI append-only 檢查也不是 required status check。所有文件與程式註解已改為不宣稱強制保護。

若要強制，需由 repo 擁有者在 `main` 設定 ruleset 或 branch protection：require pull request、require review from Code Owners、required status checks（`test (3.11)`、`test (3.12)`、`append-only`）、禁止 force push 與刪除。注意：目前唯一的 code owner 就是 `hh4832`，而 GitHub 不承認 PR 作者自己的核准。開啟「require review from Code Owners」後，`hh4832` 自己開的 PR 需要第二位 code owner，或保留管理員 bypass。這需要你決定，我沒有更改任何 repo 設定。

### 9.4 資料與成本決策（決策 4–7）

| # | 決策 | 落實 |
|---|---|---|
| Q2 | TSM RTH／ETH 由 Codex 確認，未確認前維持 UNKNOWN | `governance/source_confirmations.json`：`ibkr_tsm_session_coverage = UNKNOWN`。Validator **S17**：非 DRAFT 規格只要依賴 UNKNOWN 項目就被拒絕。TXF／台股時間戳（Q1）同樣列為 UNKNOWN |
| Q3 | 買賣方向的可成交資格由 market-research-core 提供 | 契約 C1 要求 `buy_execution_eligible`／`sell_execution_eligible`；adapter 只能傳遞，**不得**自行由其他欄位推導（契約註明於 `ports.py`，M1 adapter 審查時檢查；測試只驗證欄位為必填）。Phase 2 風險 R3 列為給 Codex 的需求 |
| Q6 | 版本化 CostProfile，區分官方規則、券商實際費率、研究假設 | `schemas/COST_PROFILE_SCHEMA.json`、`costs/profile.py` 規則 P01–P07、`governance/cost_profiles/*.json`；規格新增 `costs.profile_ref`；Validator **S18**：REGISTERED／FROZEN 必須引用 APPROVED profile、hash 一致、數字一致；FROZEN 不得以研究假設作為手續費或稅（P07） |
| Q7 | 正式 Holdout 日期暫不核准 | `governance/holdout_locks.jsonl` 維持空白；待資料覆蓋報告完成後由研究負責人決定 |

**CostProfile 現況（誠實標示）**：三份 profile 都是 DRAFT，數字與範例規格一致。

- 證交稅 0.3%、期交稅 0.00002 標為 `OFFICIAL_RULE`，但來源連結尚未核對（`ref = null`）。依 P03，它們在補上連結與核對紀錄前不能 APPROVED。
- 手續費、最低手續費、取整、滑價都標為 `RESEARCH_ASSUMPTION`，因為還沒有券商實際費率的證據。

所以目前**任何規格都不能 REGISTERED**，必須先補齊 S17（Codex 確認）、S18（profile 核准），以及 holdout lock。這是預期的 fail-closed 行為。

### 9.5 新增驗收項目

| # | 標準 | 證據 | 結果 |
|---|---|---|---|
| A13 | Holdout 並行只放行一次；失敗、取消、逾時都消耗 | `test_m2_concurrency.py`：reservation race、holdout race、consume ×3 | PASS |
| A14 | 並行預算不超額；admission lock 可驗證，跨主機會失敗 | quota race ×3（B01／B02／B04）、反證測試、`test_admission_lock_refuses_second_host` | PASS |
| A15 | 不宣稱未強制的保護 | §9.3 API 查詢；文件與註解已更正 | PASS |
| A16 | Q2／Q1 未確認項目阻擋非 DRAFT 規格 | `test_m2_review.py`：S17 | PASS |
| A17 | CostProfile 版本化、依來源分類、hash 釘選、FROZEN gate | `test_m2_review.py`：S18、P02–P07、repo profile 檢查 | PASS |
| A18 | Q3 side-specific 欄位屬於引擎契約 | `test_side_specific_eligibility_is_part_of_the_engine_contract` | PASS |
| A19 | Oracle 手算答案未改；`market-research-core` 未改 | `benchmark/oracle`、`benchmark/harness`、`expected` diff 為 0 | PASS |

### 9.6 最終判定

**M2：GO。PR #2：可以合併（MERGE-READY），前提如下：**

1. 先合併 PR #1，並把 PR #2 的 base 改為 `main`；改 base 後 CI 須重跑通過，包括 `append-only` job。
2. 合併由你執行，我沒有合併任何 PR。

合併**不代表**系統已可對真實資料研究。依設計，以下事項完成前所有真實回測都會被拒絕：Codex 確認 Q1／Q2、核准 CostProfile、研究負責人核准 holdout lock、Phase 2 GO＋M1 adapter 通過 B01–B12。

建議但不阻擋合併：啟用 `main` 的 ruleset（§9.3），並決定 sole-owner 的審核方式。
