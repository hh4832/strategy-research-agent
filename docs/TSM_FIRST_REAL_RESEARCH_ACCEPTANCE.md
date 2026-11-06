# Phase 4-A — TSM 第一次真實資料研究：治理與 Research-only Runner 驗收

| 項目 | 值 |
|---|---|
| 日期 | 2026-11-06（Asia/Taipei） |
| Agent baseline | `8f3f6e13248fe36e7e64cc59ec7c93cecfc78263`（PR #4 head，未合併） |
| Agent 分支 | `feature/phase4a-tsm-research-only`（stacked PR，base = `feature/m3b-research-runner`） |
| Core baseline | `699e3ad59951bbebb769bb69170be8d9a1a96c01`（2026-11-05；未修改） |
| Core Phase 4-A | **未交付**：`market-research-core` 沒有 699e3ad 之後的 commit，也沒有 Phase 4-A 分支（2026-11-06 查核） |
| 機器可讀 | `docs/evidence/tsm_first_real_research.json` |

## 0. 判定

| 面向 | 判定 | 理由 |
|---|---|---|
| Engineering | **SCOPED_GO** | Phase A／B 中不依賴 Codex 的部分都已完成並通過測試；Core 整合點刻意停在 `WAITING_FOR_CORE_ADAPTER` |
| Research-only Eligibility | **NO-GO** | Holdout lock 與 research-only 授權都還沒由研究負責人簽核，Core Phase 4-A 也未交付 |
| Formal Market Eligibility | **NO-GO** | 所有 formal gate 原封不動：market NO_GO、TEMPORAL_SAFETY UNKNOWN、COST_APPROVAL FAIL、FORMAL_EXECUTION_ELIGIBILITY FAIL |
| Strategy Effectiveness | **無法判定** | 沒有執行任何真實回測，不存在任何績效數字 |

**狀態：`WAITING_FOR_CORE_ADAPTER`，同時等待研究負責人執行兩個簽核指令（§2、§3）。** 依任務規定，Holdout lock 和研究授權都未成立，所以沒有執行真實回測，也沒有寫入 Google Drive。

## 1. 起始狀態查核

- `strategy-research-agent`：origin = `hh4832/strategy-research-agent`。PR #1–#3 已合併；PR #4（`feature/m3b-research-runner`）open、mergeable clean，head = baseline `8f3f6e1`。本機沒有未提交變更。
- Repo 內**沒有 AGENTS.md**，Core 699e3ad 也沒有。
- Core：最新 commit 是 `699e3ad`（`feature/m3a-ibkr-tsm-readiness`，2026-11-05 04:25 UTC），沒有 Phase 4-A 分支或 commit。
- Baseline 測試：無 Core 237 passed／51 skipped；Core 699e3ad 下 331 passed。

## 2. Holdout lock — 需研究負責人本人執行

### 2.1 為什麼 Agent 不執行

M2 規約（`M2_GOVERNANCE.md` §3、`holdout_admin.py` 檔頭）規定 `holdout_admin` 由研究負責人執行，並明定「NOT for agents」。Agent 若以 `--approved-by hh4832` 執行，就等於替你簽名。依任務書第 2 節，這一步停止，改提供精確指令。`governance/holdout_locks.jsonl` 維持空白。

### 2.2 核准的邊界（半開區間，America/New_York）

| 區段 | 區間 | 實作 |
|---|---|---|
| Research development | 2016-10-19 … 2024-12-31 | `[2016-10-19T00:00:00-04:00, 2025-01-01T00:00:00-05:00)` |
| Holdout `HOLDOUT-TSM-15M-v1` | 2025-01-01 … 2026-10-16 | `[2025-01-01T00:00:00-05:00, 2026-10-17T00:00:00-04:00)` |

以下一天後的 00:00 為終點，2026-10-16 這個交易日因此完整包含在 Holdout 內，不會因時區或收盤時間產生歧義。H03 確保開發窗口最晚只能在 holdout 起點結束（測試：剛好在 `2025-01-01T00:00:00-05:00` 結束允許，晚 1 秒就拒絕）。

### 2.3 指令

**最簡單的方式：Colab notebook**
[`notebooks/Phase4A_Research_Lead_Signoff.ipynb`](https://colab.research.google.com/github/hh4832/strategy-research-agent/blob/feature/phase4a-tsm-research-only/notebooks/Phase4A_Research_Lead_Signoff.ipynb)

它依序完成以下步驟：
1. 匿名 clone，並確認程式碼與已審查的 `8933f94` 完全相同。
2. 顯示將要簽核的內容。
3. 要你手動把 `I_CONFIRM_I_AM_THE_RESEARCH_LEAD` 改成 True。
4. 執行下面兩個指令。
5. 驗證 preflight 只剩 RO04、formal gates 未變，並跑測試與 append-only 檢查。
6. 用 Colab Secret `GITHUB_TOKEN` push；沒有 token 時改成下載檔案，再到 GitHub 網頁上傳。

這個 notebook 不讀 TSM 資料，也不掛載 Drive。

**手動方式**（在本分支的 clone 中執行）：

```bash
pip install -e .
python -m strategy_agent.governance.holdout_admin approve \
  --lock-id HOLDOUT-TSM-15M-v1 \
  --dataset ibkr_tsm_1m_normalized \
  --snapshot 20261101_205431_06359faf7ef4_IBKR_FINAL_GATE_FULL \
  --start 2025-01-01T00:00:00-05:00 \
  --end 2026-10-17T00:00:00-04:00 \
  --approved-by hh4832 \
  --approval-ref https://github.com/hh4832/strategy-research-agent/pull/5 \
  --notes "Phase 4-A: dev [2016-10-19, 2025-01-01) ET; holdout [2025-01-01, 2026-10-17) ET; BLINDNESS_UNVERIFIED"
git add governance/holdout_locks.jsonl && git commit -m "governance: approve HOLDOUT-TSM-15M-v1 (research lead)"
```

`--approval-ref` 可換成任何你認可的 https 核准紀錄。工具會先對整本 lock book 做乾跑驗證，無效條目不會寫入。

**注意**：這不會改變 `governance/market_readiness.json` 的 formal `HOLDOUT_APPROVAL` gate（維持 FAIL）。如果之後改了任何 formal gate，§3 的授權會因 RA10 自動失效，需要重新簽一份。

### 2.4 盲測性查核：`BLINDNESS_UNVERIFIED`

**無法證明 Holdout 未被用於先前的策略選擇。** 證據：

1. **Core Phase 2 Drive smoke**（`notebooks/colab_phase2_drive_smoke_test.ipynb` @699e3ad cell 3）：`client.load(market='IBKR', symbol='TSM')` 後取 `loaded.frame.tail(2000)`，以 `BacktestAssumptions(fast_window=5, slow_window=20)` 執行 `run_sma`。快照結束於 2026-10-16，所以最後 2,000 根 1 分 K **落在 Holdout 期間內**，而且用的正是本次的 SMA 5/20。Notebook 只印出 `trades=`，但結果物件含報酬與 drawdown，無法證明沒有人看過。
2. FINAL GATE 資料驗收涵蓋整個 2016-10-19 … 2026-10-16 期間。這是資料品質檢查，不是策略選擇，但代表 Holdout 期間的資料曾被檢視。
3. Agent 的 registry（`registry/experiments.jsonl`、`families.jsonl`）是空的；Agent 端所有 M3 回測都是合成資料。這只能證明 **Agent** 沒用過 Holdout，不能證明研究流程整體沒用過。
4. SMA 5/20 是預先指定的 baseline；repo 內沒有參數搜尋紀錄，但沒有文件記錄它是在看過 TSM 資料之前就選定的。

已寫入授權提案（`holdout.blindness = BLINDNESS_UNVERIFIED`）。之後任何 Holdout 結果都不得宣稱為完全盲測。

## 3. RESEARCH_ONLY_EXPLORATORY 授權 — 需研究負責人本人簽核

### 3.1 契約

`research-only-authorization-v1` 是獨立於 formal 市場資格的契約：
- `schemas/RESEARCH_AUTHORIZATION_SCHEMA.json`
- `governance/research_authorization.py`：唯讀 book、guard 規則 RA01–RA12、原子批次額度
- `governance/research_authorization_admin.py`：研究負責人簽核工具，寫入前先乾跑驗證

| 代碼 | 規則 |
|---|---|
| RA01 | 授權存在、由 allow-list 研究負責人以 https 核准連結簽核、未撤銷 |
| RA02 | 現在時間在 `[issued_at, expires_at)` 內 |
| RA03／RA04 | 只限 IBKR_TSM／TSM，指定 snapshot 與 producer SHA |
| RA05 | Temporal policy SHA 同時等於授權與 gate 中記錄的 RESEARCH_ONLY 接受紀錄 |
| RA06 | 成本情境集檔案 SHA 相符；情境必須是授權清單的子集 |
| RA07 | 規格檔 SHA、id、版本相符，參數固定 SMA 5/20（runner 另外強制 R02） |
| RA08 | Warm-up 與研究窗口位於授權研究窗口與 development 邊界內 |
| RA09 | 已核准的 holdout lock 存在，邊界與授權完全一致；warm-up、窗口、development 邊界都不得觸及 holdout |
| RA10 | Formal gates 與簽核時的快照逐一相同（狀態 NO_GO）；任何變更都會讓授權失效 |
| RA11 | 批次額度：已用 < `max_batches`，以原子 reservation 實作，不會釋放 |
| RA12 | 只能用於 RESEARCH_ONLY_EXPLORATORY，`holdout_access` 必須為 false |

沒有 `ignore_gate`、`force_run`、`skip_*`、`override` 參數，config 也不接受未知欄位（有測試）。Formal gate 只讀不寫。

### 3.2 提案內容（`governance/proposals/RA-IBKR_TSM-SMA520_SMOKE-0001.json`，未簽核）

| 欄位 | 值 |
|---|---|
| scope | RESEARCH_ONLY_EXPLORATORY |
| 資料 | `ibkr_tsm_1m_normalized` @ `20261101_205431_06359faf7ef4_IBKR_FINAL_GATE_FULL`，producer `06359faf…f28` |
| Temporal | CONSERVATIVE_LABEL_AGNOSTIC 1.0.0，sha `7fa3cdca…59c4`，RESEARCH_ONLY |
| 成本 | `ibkr_pro_fixed_tsm_research_only` v1.0.0，sha `bf4f2365…9cd3`；情境 BASE、LOW、STRESS、CAT_STRESS |
| 策略 | `sma_crossover_tsm_15m_5_20` 0.1.0，spec sha `b358416e…4123`，fast 5／slow 20 |
| 研究窗口 | `[2024-05-29, 2024-06-29)` ET（含 warm-up；回測窗口 `[2024-06-03, 2024-06-29)`） |
| Formal gate pin | gate_version 2026-11-05.4，NO_GO，gates sha `11837b36…ea32` |
| 次數 | `max_batches = 1`（一個批次 = 三個滑價情境 + CAT 敏感度） |
| 期限 | issued 2026-11-06 09:00 +08:00；expires 2026-11-30 23:59:59 +08:00（可用 `--expires-at` 改） |

### 3.3 指令（先完成 §2.3）

```bash
python -m strategy_agent.governance.research_authorization_admin approve \
  --proposal governance/proposals/RA-IBKR_TSM-SMA520_SMOKE-0001.json \
  --approved-by hh4832 \
  --approval-ref https://github.com/hh4832/strategy-research-agent/pull/5
git add governance/research_authorizations.jsonl && git commit -m "governance: approve RA-IBKR_TSM-SMA520_SMOKE-0001 (research lead)"
git push
```

簽核後，`python -c` 執行 `ResearchOnlyRunner().preflight(...)` 應該只剩 RO04（等待 Core）。

## 4. Phase B — Research-only Runner（不依賴 Core 的部分）

| 項目 | 位置 | 狀態 |
|---|---|---|
| 授權 schema 與 guard | `schemas/RESEARCH_AUTHORIZATION_SCHEMA.json`、`governance/research_authorization*.py` | 完成 |
| 真實 snapshot 的 run config 與 provenance | `configs/research_only/tsm_sma_5_20_smoke_2024_06_v1.json`（config sha `7c7f53f4…087e`） | 完成；`core.phase4a_sha = null` |
| 研究成本情境集 | `configs/research_only/ibkr_pro_fixed_tsm_research_only_costs_v1.json` | 完成（§5） |
| Fail-closed preflight | `research_only/runner.py`：RO01–RO04 + RA + CO + R02 + H01 | 完成 |
| Holdout 邊界測試 | `tests/test_phase4a_research_only.py` | 完成 |
| Synthetic contract tests | 同上（36 個） | 完成 |
| Immutable archive helper | `write_once`（`O_EXCL`）、`run_id` = `YYYYMMDD_HHMMSS_<commit7>` | 完成 |
| Core Phase 4-A 呼叫 | `ResearchOnlyRunner.run` | **刻意未實作**：READY 時丟出 `NotImplementedError`，不猜 API |

本 repo 的 preflight 現況（程式實際輸出）：`BLOCKED`，阻擋項目 `H01`、`RA01`、`RO04`；formal gates 為 NO_GO／UNKNOWN／FAIL；成本窗口落在 SEC31-2024-05-22（27.80/M）與 TAF 0.000166。

在測試用的暫存治理檔中簽好 lock 與授權後，preflight 會變成 `WAITING_FOR_CORE_ADAPTER`，只剩 RO04。若再釘上一個 Core SHA，就是 `READY_FOR_CORE_INTEGRATION`，而 `run()` 會拒絕繼續。

## 5. 成本

情境集只涵蓋 development 期間（2016-10-19 … 2024-12-31），**不記錄任何 Holdout 期間的費率**。

| 元件 | 假設 | 標記 |
|---|---|---|
| Commission | 0.005／股／邊，最低 1 USD／筆，上限 1% 名目 | RESEARCH_ONLY |
| SEC Section 31 | 日期化官方費率，只收賣方；引用 `ibkr_pro_fixed_tsm_research_base_v1.json` 的 `regulatory_schedule`（file sha `af4a29c0…eb1`） | RESEARCH_ONLY |
| FINRA TAF | 日期化；**雙側、無上限**（研究用保守高估）；2024 年 0.000166／股 | RESEARCH_ONLY |
| CAT | BASE 0.00005，CAT_STRESS 0.00010／股／邊；IBKR 是否轉嫁未知，假設會收 | RESEARCH_ONLY |
| 滑價 | BASE 2、LOW 1、STRESS 4 bps／邊；half-spread 0 | RESEARCH_ONLY |

規則 CO01–CO06：
- LOW／STRESS 與 BASE 只差滑價，CAT_STRESS 只差 CAT。
- 窗口內不得跨越 SEC 或 TAF 的費率變更，所以 2024-05-15 起算的窗口會被拒絕。
- 不得延伸到 Holdout。

本次窗口 2024-05-29 … 2024-06-28 落在單一區間內。

**兩個要交給 Codex 的成本問題：**

1. **Core 699e3ad 無法分開列出 SEC／TAF／CAT。** Per-unit 規費不分日期，而且和 SEC 合在同一條 `regulatory_fee` ledger line。如果把 TAF＋CAT 合併成一個 per-unit 費率，逐行 HALF_UP 取整會改變金額：100 股時分開是 0.02＋0.01，合併只有 0.02。所以 Agent 不做 mapping，交給 Core Phase 4-A 的交付契約決定。
2. **CAT 敏感度在每筆 100 股時看不出來。** 100 × 0.00005 = 0.005 → 0.01，100 × 0.00010 = 0.010 → 0.01，逐筆取整後兩者都是 0.01（有測試）。除非 Core 改成以整筆或整日計算後再取整，CAT_STRESS 和 BASE 的 CAT 成本會完全相同。報告必須標示「取整後無差異」，不能解讀成「CAT 不重要」。

## 6. Codex 交付後的整合步驟（只修最小必要部分）

1. 在 run config 釘上 `core.phase4a_sha` 與 `core.phase4a_contract_ref`（改 config 必須升版、重算 hash）。
2. 讀交付契約，確認以下各項：
   - Snapshot adapter 的進入點；
   - 原生 1 分 K 經保守聚合成 15 分 K（RTH，不含收盤競價）；
   - CostProfile 能否以日期化方式分開表達 SEC（賣方）、TAF（雙側無上限）、CAT（雙側），且 ledger 分行；
   - ExecutionEngine 回傳 RESEARCH_ONLY 資格，而不是 FORMAL_ELIGIBLE。
3. 用 Core 跑 B01–B12（BENCHMARK_PASS），再跑本檔的 synthetic contract tests。
4. 寫出 `ResearchOnlyRunner.run` 的 Core 呼叫。在讀任何資料之前，必須先用 `consume` 消耗批次額度。
5. 封存（§7），驗證 fills、ledger、metrics；不重跑已通過的資料驗證。

## 7. 執行與封存（本輪未執行）

| Phase C 前提 | 狀態 |
|---|---|
| Holdout lock 經正式管理流程成立 | **未成立**（等待 §2.3） |
| Research-only authorization PASS | **未成立**（等待 §3.3） |
| Core Phase 4-A 交付且測試 PASS | **未交付** |
| Snapshot SHA 驗證 PASS | 未執行（需要 Core adapter） |
| 日期化成本可正確表達 | **不完全**：Core 699e3ad 無法分開 SEC／TAF／CAT（§5） |
| 時序與規費假設符合授權 | 設定已備妥，等待 Core |
| Formal Market Gate 未被修改 | ✔（與 baseline 逐位元相同，有測試） |

- **Google Drive**：沒有寫入任何檔案。日後封存路徑為 `MyDrive/Quant_Research/strategy-research-agent/<run_id>/`，用 `write_once` 建立，不覆蓋。
- **N3**：本輪沒有觸發，因為沒有 batch resume。新的 research-only 路徑每次 run 都用唯一 `run_id`，所有寫入都是 `O_EXCL`，結構上就不會覆蓋 batch summary。既有 M3 batch 的 N3 沒有修改。
- **N4／N5**：不適用，本輪沒有產生報告。

## 8. 未解決問題

| ID | 問題 | 誰來處理 |
|---|---|---|
| LEAD_SIGN_HOLDOUT | 執行 §2.3 | 研究負責人 |
| LEAD_SIGN_AUTH | 執行 §3.3 | 研究負責人 |
| CORE_PHASE4A | Snapshot adapter、保守聚合、CostProfile、ExecutionEngine 交付與契約 | Codex |
| CORE_FEE_SEPARATION | SEC／TAF／CAT 分行、TAF 日期化 | Codex（Phase 4-A 契約） |
| CAT_ROUNDING | CAT 敏感度在每筆 100 股時被取整抹平 | Codex 決定取整粒度，或研究負責人接受這個限制 |
| BLINDNESS_UNVERIFIED | Holdout 曾被 Phase 2 smoke 以 SMA 5/20 觸及 | 永久標記；之後的 Holdout 結果不得稱為完全盲測 |
| CODEOWNERS | `main` 仍沒有 branch protection／ruleset | 研究負責人（repo 設定） |

## 9. 驗證

- 新測試：`tests/test_phase4a_research_only.py`，36 passed。
- **簽核後狀態已預先驗證**：在暫存 worktree 中依 notebook 執行兩個簽核並 commit 後，preflight = `WAITING_FOR_CORE_ADAPTER`（只剩 RO04）；全套件在有 Core 和無 Core 下都通過，append-only 也 PASS。為此調整了兩個原本把 lock book 寫死為空的 repo 狀態測試：
  - `test_tsm_cost_assumptions.py::test_research_acceptance_never_upgrades_formal_gates`
  - Phase 4-A 自己的兩個測試
  - 調整後只接受兩種狀態：「空」，或「只含研究負責人簽核、邊界完全一致的那一筆」。
- **調整一個既有的 Phase 3 測試**（`tests/test_m3b_notebook.py::test_n2_pin_…`，最小必要）：原本要求 notebook pin 與 HEAD 在 `src/`、`schemas/` 等路徑「完全沒有差異」，`governance/` 只准 `market_readiness.json` 變動。Phase 4-A 只新增檔案，第一次 CI 因此失敗。調整後的規則：
  - pin 當時存在的每個檔案都必須逐位元相同（不得 M/D/R/T）；
  - 新增檔案允許，但 pin 內的程式碼不得引用它們；
  - `governance/` 允許新增檔案，以及既有 `*.jsonl` 只能追加；`market_readiness.json` 的狀態檢查不變。

  N2 的保證（Colab 從 pin 執行的內容與這棵樹相同）沒有減弱。這也避免了一件事：研究負責人日後簽 holdout lock 時，那個 commit 不會讓 CI 失敗。驗證方式：在暫存 worktree 中分別測試只有新增／追加 lock（通過），以及改 pin 程式碼／刪改 approvers／formal gate 改 PASS（失敗）。Notebook pin 沒有改動，既有 Colab 證據的對應關係也維持不變。
- 全套件：Core 699e3ad 下 367 passed；無 Core 273 passed／51 skipped；append-only check PASS。
- 沒有修改 Core、Oracle 或任何手算答案，也沒有改 formal registry、formal gates、Phase 3 文件和 SMA 參數。沒有讀取 Holdout，沒有執行真實回測，也沒有合併任何 PR。
