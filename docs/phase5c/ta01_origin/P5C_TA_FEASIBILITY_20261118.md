# Phase 5-C 技術分析假設可行性收斂（唯讀評估）

> 日期：2026-11-18（Asia/Taipei）
> 範圍：`P5C-TA-01`，以及「能否簡化到既有執行路徑」。

## 1. 結論：`BLOCKED`

**目前沒有任何技術分析方案可以直接進入研究執行。** 進入研究執行需要同時滿足四項：資料可用、治理允許、成本有來源、既有執行路徑可支援。沒有一個候選能同時滿足。

| 判斷 | 原因 |
|---|---|
| TA-01：BLOCKED | 需要新增兩項能力（訊號函式、評估用的虛無基準），另有未解決的市場與資料語意問題 |
| 簡化候選：**不提出** | 既有執行路徑只能執行 SMA 交叉（`fast`、`slow`），無法加入 K 線或成交量條件。把 K 線和成交量條件拿掉，剩下的就是既有的 SMA 策略家族：既不符合「線型＋K 線＋成交量」的要求，也會與已執行的研究重複。依指示，不為了產出候選而降低標準 |

`P5C-TA-01` 規格草稿通過靜態驗證，只代表**格式與語意正確**。這**不代表**它可以回測、可以執行或策略有效。

---

## 2. Repo 實測值

| 項目 | 值 |
|---|---|
| Repository | `hh4832/strategy-research-agent` |
| 檢查用工作目錄 | `/home/claude/main_8a5dc47`（唯讀 worktree，detached HEAD，未修改檔案數 0） |
| HEAD | `8a5dc47f30d7b33884e565b9dc3edb4ade7f74bd` |
| 遠端 main | `8a5dc47f30d7b33884e565b9dc3edb4ade7f74bd`（`git ls-remote` 確認，兩者一致） |
| Branch | 無（detached，指向 main） |
| 其他本機 branch | `feat/phase5c-mvp-controlled-research`（Phase 5-C MVP，未 commit），**不在本次範圍**，不影響下列判斷 |

---

## 3. 既有執行器與指標能力（檔案證據）

| 能力 | 證據 | 實際狀態 |
|---|---|---|
| 受治理的執行路徑會呼叫哪個訊號 | `src/strategy_agent/research_only/core_phase4a.py:276` 匯入 `sma_crossover`；`:281` 呼叫 `sma_crossover(sbars, int(parameters["fast"]), int(parameters["slow"]))` | **只有 SMA 交叉** |
| 其他執行路徑 | `src/strategy_agent/research/pipeline.py:31,182`（M3-B 合成資料流程）也只呼叫 `sma_crossover` | 只有 SMA |
| 正式執行鏈 | `execution/b3c_run.py`、`execution/controlled.py`、`execution/acceptance.py` 都經由既有的 `ResearchOnlyRunner`，最後走 `core_phase4a` | 只有 SMA |
| SMA 函式能力 | `src/strategy_agent/signals/sma.py:12`：`sma_crossover(bars, fast, slow, exclusions)` 只做多，快線上穿慢線進場、下穿出場 | **沒有成交量、K 線或線型參數** |
| 其他已寫好的訊號函式 | `signals/orb.py:18` 的 `orb`；`signals/atr.py:46` 的 `atr_breakout` | 是純函式，**任何執行器都沒有呼叫**（repo 全域搜尋確認） |
| 通用規格語法解譯器 | 未找到。`STRATEGY_SPEC_SCHEMA.json` 與 `spec/validate.py` 只做驗證 | 規格語法「能寫」不等於「能跑」 |
| 規格語法支援的指標與運算 | `STRATEGY_SPEC_SCHEMA.json` `$defs`。指標：SMA、EMA、ATR_WILDER、TRUE_RANGE、OPENING_RANGE_HIGH／LOW、ROLLING_MAX／MIN、PRICE；輸入可為 OHLC 或 volume；運算 add／sub／mul；條件 gt／ge／lt／le、cross、and／or／not、time、is_flat | 只用於驗證 |
| 訊號輸入欄位 | `signals/core.py:33` 的 `SignalBar` 含 open／high／low／close／volume／eligible | 資料欄位已就緒 |
| Core 成交與 K 棒 | `docs/M3C_FINAL_ACCEPTANCE.md` §4：保守 bucket、決策時間 `modeled_available_at`、嚴格較晚成交 | Core 接收「目標部位」決策，**不限策略類型**。限制在 Agent 端的分派，不在 Core |
| 評估用的虛無基準 | `src/strategy_agent/evaluation/controls.py` 只有 `DRIFT-MATCHED-v1`，文件明寫不是隨機進場模擬 | 沒有隨機進場虛無分布 |

---

## 4. TA-01 的阻擋項，以及能否簡化

| # | 阻擋項 | 能否用既有能力解決 |
|---|---|---|
| T1 | 執行器沒有 TA-01 訊號（ROLLING_MAX 壓力位、強勢收盤、相對量） | **否**。必須新增一個 Agent 端純訊號函式，並在 `core_phase4a` 加上分派與測試。Core 不需要改 |
| T2 | 評估器沒有隨機進場虛無分布 | **否**。若改用既有的 `DRIFT-MATCHED-v1`，文件明寫它無法證明擇時能力 |
| T3 | 1m 時間標籤、開盤與收盤競價歸屬 UNKNOWN；競價成交量可能偽造量增訊號 | 否。需要外部證據，或由 Haw 事先登記排除規則 |
| T4 | 15m bucket 的成交量是否為 1m 成交量的總和、TSM 成交量單位、零量分鐘的頻率 | `NEEDS_CONFIRMATION`（Core 原始碼不在本機；repo 與 Notion 查不到） |
| T5 | 成交量有日內 U 形季節性，跨日均量會讓早盤更容易觸發 | 否。改用同時段均量需要新指標 |
| T6 | 15:15 時間出場能否當日成交 | `UNVERIFIED`（由文件推得，未核對 Core 原始碼） |

### 為什麼無法簡化到既有路徑

既有路徑唯一可調的只有 `fast` 和 `slow` 兩個整數。任何線型、K 線或成交量條件，都必須寫成新的訊號函式。因此「簡化」只有兩種結果，兩種都不符合要求：

- **把條件全部拿掉**：剩下的就是 SMA 交叉，屬於既有的 SMA 策略家族，而且已被 5 個授權研究過。
- **只換參數**：等於搜尋參數，也會跟已執行的 SMA 研究重複。

---

## 5. 簡化候選

**無。** 理由見 §4。

供 Haw 參考：讓任何「線型＋K 線＋成交量」假設可以執行，**最少**需要以下三項新能力。這些是開發工作，本次不做。

1. Agent 端新增 1 個純訊號函式，並在 `core_phase4a` 加上分派。
2. 評估器新增隨機進場虛無分布，或決定接受 `DRIFT-MATCHED-v1` 的限制。
3. 新的授權。現有授權都綁定 SMA 的 spec，而且都不允許變更規則（見 §6）。

---

## 6. 資料、治理、成本與授權狀態（只讀 metadata）

| 項目 | 狀態 | 證據 |
|---|---|---|
| 已治理的市場 | 只有 `IBKR_TSM`，status **NO_GO** | `governance/market_readiness.json`（最後修改 2026-11-12） |
| TSM 各項閘門 | DATA_QUALITY PASS、TEMPORAL_SAFETY **UNKNOWN**、COST_MODEL_COMPATIBILITY PASS、COST_APPROVAL **FAIL**、HOLDOUT_APPROVAL **FAIL**、FORMAL_EXECUTION_ELIGIBILITY **FAIL** | 同上 `gates` |
| TSM 開發期資料 | [2016-10-19, 2025-01-01) ET，**已被接觸，只能探索性** | `docs/PHASE5C1_DATA_AVAILABILITY.md`；5 個授權的 `development_boundary` |
| 台股 2330、SOXX、NQ／ES | 不在 `market_readiness`；狀態只見於 Notion 摘錄 | `NEEDS_CONFIRMATION`（本次沒有開啟 Data Catalog 試算表） |
| TSM 成本 | `configs/research_only/ibkr_pro_fixed_tsm_research_only_costs_v1.json`（`bf4f2365…`），屬於 RESEARCH_ONLY 假設；閘門 COST_APPROVAL = FAIL | 對新策略：`NEEDS_CONFIRMATION` |
| 台股成本 | `configs/` 中沒有 | `NEEDS_CONFIRMATION` |
| 授權 | 5 筆都屬 `sma_crossover_tsm_15m_5_20`。0002 已用 1/1，2026-11-23 到期。0001 於 2026-11-19 16:58 到期（依約定不處理）。P5C1 系列明文不授權「any change of rules/costs/pins」 | `governance/research_authorizations.jsonl` |
| 新策略可用的授權 | **無** | 同上 |

---

## 7. 聲明

- **沒有**下載、開啟、讀取或計算任何市場資料；**沒有**碰 Holdout。
- **沒有**回測，**沒有**呼叫研究執行器，**沒有**執行 PREPARE、PREFLIGHT、CLAIM、EXECUTE。
- **沒有**消耗 Batch 或授權。
- **沒有**新增或修改任何程式、測試、Core、策略、成本、資料、期間、Registry、Governance、Ledger、授權或 CLAIM。
- **沒有** commit 或 push。
- 本次只做了三件事：讀 repo 原始碼與文件、讀治理 metadata（`market_readiness.json`、`research_authorizations.jsonl`）、執行 `git ls-remote`。

---

## 8. 需要 Haw 決定的事項

1. **是否投資新增能力。** 是否開發 Agent 端的 TA 訊號函式與分派，以及隨機進場評估能力？不做的話，技術分析方向維持 `BLOCKED`。
2. **市場。** 是否只在已治理的 TSM 上做（只能探索性，且 TEMPORAL_SAFETY 仍是 UNKNOWN）？還是先把其他市場納入治理？
3. **競價與成交量語意。** 是否先取得證據（時間標籤、競價歸屬、bucket 成交量聚合、成交量單位），再談成交量類假設？
4. **主要比較基準。** 接受 `DRIFT-MATCHED-v1` 的限制，還是要求新增隨機進場虛無分布？
5. **新授權與成本核准。** 任何新策略都需要新的研究授權，以及核准的成本設定。
