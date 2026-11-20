# Phase 5-C 新技術分析假設提案：P5C-TA-01

## 1. 狀態

**`PROPOSAL_ONLY`**。研究執行狀態為 **`BLOCKED`**，另有多項 `NEEDS_CONFIRMATION`。

- 日期：2026-11-17（Asia/Taipei）。
- 依據：repo `hh4832/strategy-research-agent` main `8a5dc47`（唯讀 worktree）。
- **這不是 H1 的修改版**。P5C-TA-01 是獨立的假設名稱與規格：不使用開盤區間，策略家族結構雜湊也不同。

### 聲明

本提案從頭到尾：

- **沒有回測**，也沒有呼叫任何研究執行器。
- **沒有讀取 Holdout 或任何市場資料**，也沒有計算任何資料統計。
- **沒有消耗授權或 Batch**，沒有執行 PREPARE、PREFLIGHT、CLAIM、EXECUTE。
- **沒有產生任何績效數字**。
- 沒有修改策略程式、Core、成本、資料、期間、Registry、Governance、Ledger、授權或 CLAIM。
- 沒有 commit 或 push。

唯一執行過的計算有兩項，都不碰資料：

1. 用既有的純函式驗證器 `strategy_agent.spec.validate.validate`，對本提案的規格草稿 JSON 做靜態檢查。
2. 用 `family.canonical.structure_hash` 比對策略家族結構。

---

## 2. 候選方案比較

三個候選都是「線型 + K 線 + 成交量」組合成同一個訊號，每個條件都能用固定公式判定。以下以 15 分鐘 K 棒舉例；最終市場未定（見 §5）。

| 項目 | **TA-01（建議）** 20 根壓力突破 + 強勢收盤 + 相對量 | TA-02 支撐假跌破 + 長下影錘子線 + 量能高潮 | TA-03 下跌後多頭吞噬 + 量增 |
|---|---|---|---|
| 直覺 | 近期高點堆積賣單（壓力）。收在 K 棒區間高位、成交量又異常放大，代表賣壓被主動買盤吃掉，突破較可能延續 | 跌破近期低點後當根收回（假跌破），長下影加上大量，代表恐慌賣壓被吸收，可能反彈 | 連續下跌後出現吞噬前一根的陽線，而且量比前一根大，代表賣方力竭，主導權易手 |
| 線型 | 壓力位 R = 前 N 根 high 的最大值（不含當根）；`close > R` | 支撐位 S = 前 N 根 low 的最小值（不含當根）；`low < S` 且 `close > S` | 前一根陰線（`close[1] < open[1]`）；`close[1] < close[1+N]`（先前下跌） |
| K 線 | 陽線 `close > open`；強勢收盤 `close − low ≥ 0.75·(high − low)`；`high > low` | 錘子線（只取陽線）：`close ≥ open`；`open − low ≥ 2·(close − open)`；`high − close ≤ 0.25·(high − low)` | 吞噬：`close > open`、`open ≤ close[1]`、`close ≥ open[1]` |
| 成交量 | `volume ≥ 2.0 × SMA(volume, 20)`，均量取前 20 根（不含當根）；作用是**確認突破被承接** | `volume ≥ 2.0 × SMA(volume, 20)`（前 20 根）；作用是**辨識量能高潮** | `volume > volume[1]` 且 `volume ≥ 1.5 × SMA(volume, 20)`（前 20 根） |
| 出場 | `close < 前 10 根 low 的最小值`；或持有滿 8 根；或時間出場 | 持有滿 N 根，或 `close < 當根 low`（需要記住進場時的狀態，現有規格語法**無法表達**） | 持有滿 N 根，或跌破吞噬 K 棒低點（同樣需要進場狀態，**無法表達**） |
| 現有規格語法能否表達 | **能**：全部條件都已通過靜態驗證（§4） | 進場條件能；以進場 K 棒為參考的停損**不能** | 進場條件能（`PRICE` 指標加 `lag`）；以進場 K 棒為參考的停損**不能** |
| 主要風險 | 突破失敗與追高；成交量有日內 U 形季節性，會偏向早盤訊號；競價成交量歸屬 | 接刀；訊號罕見，樣本不足；長下影可能是資料異常 | 跨隔夜跳空的兩根 K 棒比較；訊號定義較多變體，多重檢定負擔較重 |
| 參數數量（全部固定，不搜尋） | 5 | 4（加上停損未定） | 4（加上停損未定） |

### 為什麼建議 TA-01

1. **能完整表達。** 三類條件與出場全部可用既有規格語法寫出，靜態驗證 0 錯誤、0 警告。TA-02 和 TA-03 的停損需要「進場 K 棒參考」，現有語法做不到。
2. **只用單根 K 棒判定。** 不需要跨隔夜比較兩根 K 棒，比較不受隔夜跳空與時段邊界影響。
3. **成交量的作用單一、清楚。** 量只用來確認「突破被承接」，可以事先登記一個次要的「有量／無量」對照分析。
4. **不是重複實驗。** 與既有 SMA、ORB、ATR 規格的策略家族結構雜湊都不同；repo 內所有 spec 比對結果為 0 筆重複。

### 參數來源

參數值（20、2.0、0.75、10、8）是**事先選定的常見慣例值，沒有根據任何資料或回測結果挑選**。Haw 可在事先登記前修改，修改後就固定，不得搜尋。

---

## 3. 建議方案 TA-01 的完整規格

規格草稿檔：`/home/claude/p5c_demo/ta01/p5c_ta01_spec_draft.json`

- sha256：`235288ee…6d58`
- strategy_id：`p5c_ta01_res20_strongclose_relvol_tsm_15m`
- version：`0.0.1`
- 這只是提案草稿，**不是已登錄的 spec**。

### 3.1 市場、週期、時區

| 項目 | 定義 | 狀態 |
|---|---|---|
| 標的與市場 | 候選 A：IBKR TSM ADR（1m 聚合成 15m）。候選 B：台股 2330 5m。候選 C：SOXX 1m。詳見 §5 | **NEEDS_CONFIRMATION**（Haw 決定） |
| K 線週期 | 15m（以候選 A 撰寫；若選候選 B，週期改為 5m，並需重新寫定所有以「根數」表示的參數） | DEFINED（依市場而定） |
| 時區與時段 | America/New_York；只用 NYSE RTH；來源為 UTC，由 Core 依 NYSE 日曆換算（含夏令時間） | DEFINED |
| K 棒來源 | Core conservative 15m bucket（`CONSERVATIVE_LABEL_AGNOSTIC` 1.0.0）。任一來源分鐘缺漏或零量，該 bucket 即不合格，不得用於任何計算或成交。開盤 bucket 與收盤 bucket 都不合格 | DEFINED（依 `docs/M3C_FINAL_ACCEPTANCE.md` §4） |

### 3.2 機器可判定的進場條件

所有條件都在**同一根已完成的合格 bucket t** 上判定；`[k]` 表示往前第 k 根合格 bucket。

```
R(t)      = max(high[1..20])                  # 壓力：前 20 根合格 bucket 的最高價，不含當根
V̄(t)      = mean(volume[1..20])               # 均量：前 20 根合格 bucket 的平均量，不含當根
S(t)      = min(low[1..10])                   # 出場用支撐：前 10 根最低價，不含當根

ENTRY_LONG(t) =  is_flat
             AND decision_time(t) < 14:30 ET          # 決策時間 = Core modeled_available_at
             AND close(t) > R(t)                      # 線型：收盤突破壓力，只用收盤確認，不用盤中觸價
             AND close(t) > open(t)                   # K 線：陽線
             AND high(t) > low(t)                     # 排除零振幅
             AND close(t) − low(t) ≥ 0.75·(high(t) − low(t))   # K 線：收在區間最上方四分之一
             AND volume(t) ≥ 2.0·V̄(t)                 # 成交量：相對量 ≥ 2 倍
             AND 當日尚未進場
```

| 項目 | 定義 |
|---|---|
| 指標序列 | `CONTINUOUS_ELIGIBLE_BARS`：只計合格 bucket，**會跨日延續**，例如當日早盤的 R 和 V̄ 包含前一交易日的 bucket |
| 暖機 | 至少 60 根合格 bucket，之後才產生訊號 |

### 3.3 下單、成交、出場

| 項目 | 定義 | 狀態 |
|---|---|---|
| 訊號時間 | bucket t 的 Core `modeled_available_at` | DEFINED |
| 下單 | 訊號時間；MARKET 單；每筆 100 股；只做多；最多 1 筆持倉；每日最多進場 1 次 | DEFINED |
| 成交 | 第一個「最早可能開盤時間**嚴格晚於**訊號時間」的合格 bucket，以其開盤價成交；最多延遲 2 根，否則到期 | DEFINED |
| 出場 1：支撐跌破 | `close(t') < S(t')`，只用收盤確認；下一個合格 bucket 開盤成交；跳空時以該開盤價成交，不保證價格 | DEFINED |
| 出場 2：持有上限 | 持有滿 8 根合格 bucket 後的決策時間 | DEFINED |
| 出場 3：時間出場 | 第一個決策時間 ≥ 15:15 ET 的 bucket。依保守政策，收盤 bucket 不合格；15:15 決策可成交於最後一個合格 bucket，但未以 Core 原始碼核對 | **UNVERIFIED** |
| 出場成交失敗 | 不得默默留過夜；必須標記並列入報告。Core 對這種情況的行為未核對 | **UNVERIFIED** |
| 早收盤日 | 不進場 | DEFINED |

### 3.4 成本

| 項目 | 內容 | 狀態 |
|---|---|---|
| 候選 A（TSM） | `configs/research_only/ibkr_pro_fixed_tsm_research_only_costs_v1.json`（sha256 `bf4f2365…49cd3`）；情境 BASE / LOW / STRESS / CAT_STRESS。這是 RESEARCH_ONLY 假設，只在 SMA 授權下核准過，**不代表 TA-01 可用** | **NEEDS_CONFIRMATION** |
| 候選 B（台股 2330） | `configs/` 中**找不到**台股成本設定 | **NEEDS_CONFIRMATION** |

### 3.5 假設、指標與比較基準

| 項目 | 定義 |
|---|---|
| 主要假設 | 符合 TA-01 三項條件的進場，在 BASE 成本下每筆已平倉交易的平均淨損益，高於隨機進場虛無分布（相同的進場時窗、出場、股數、成交與成本規則） |
| 虛無假設 | 平均淨損益沒有超過事先登記的虛無分布百分位 |
| 主要指標 | BASE 成本下每筆已平倉交易的平均淨損益（USD） |
| 唯一主要比較基準 | 隨機進場虛無分布。母體：決策時間在 [10:00, 14:30) 內、且暖機完成的所有合格 bucket。抽法：每次重抽，不放回抽出與實際交易數相同的交易日，每日隨機抽 1 個 bucket |
| 隨機抽樣參數 | 種子、重抽次數、判定百分位：**NEEDS_CONFIRMATION**，不自行填寫 |
| 次要分析（事先登記，探索性） | (a) 成交量的作用：同樣符合線型與 K 線條件時，相對量 ≥ 2 與 < 2 的兩組平均淨損益差；(b) 既有 `DRIFT-MATCHED-v1` 漂移基準，只作描述；(c) STRESS 成本 |

---

## 4. 資料與 Core 可行性證據

| 項目 | 證據（路徑、設定、程式位置） | 判定 |
|---|---|---|
| 規格語法能否表達三類條件 | `STRATEGY_SPEC_SCHEMA.json`：指標有 `ROLLING_MAX`、`ROLLING_MIN`、`SMA`（輸入可為 `volume`）、`PRICE`，支援 `lag`；運算有 `add`、`sub`、`mul`；條件有 `gt`、`ge`、`lt`、`le`、`and`、`time_lt`、`is_flat`。**沒有 `div`、`abs`、`min`、`max` 運算**，所以比例都改寫成乘法形式 | **可表達** |
| 靜態驗證 | `strategy_agent.spec.validate.validate`：0 ERROR、0 WARNING（`ta01/static_validation.json`） | PASS（只是靜態檢查） |
| 重複檢查 | 策略家族結構雜湊 `0c7bde1a…246c`；repo 內所有 spec 比對結果為 0 筆重複 | 新策略家族 |
| 能否被現有執行器執行 | `src/strategy_agent/research_only/core_phase4a.py:276` 寫死 `sma_crossover`；`src/strategy_agent/signals/` 只有 `sma`、`orb`、`atr`；**沒有通用的規格語法解譯器** | **需要新增 Agent 端能力**：一個純訊號函式加上執行器分派與測試。**不需要修改 Core** |
| Core 成交與資格 | `docs/M3C_FINAL_ACCEPTANCE.md` §4、`M3_CORE_AGENT_INTEGRATION.md`：保守 bucket、嚴格晚於訊號成交、MARKET 單、只做多（C1 已用過同一路徑） | 可沿用 |
| bucket 成交量怎麼聚合 | 15m bucket 的 volume 是否等於 15 個 1m 成交量的總和：Core 原始碼不在本機，repo 文件查不到 | **NEEDS_CONFIRMATION** |
| TSM 成交量單位 | IBKR TRADES 成交量的單位（股或 100 股）：repo 與 Notion 都查不到 | **NEEDS_CONFIRMATION**（比例條件不受固定倍數影響，但四捨五入可能造成零量分鐘） |
| 隨機進場虛無分布 | `src/strategy_agent/evaluation/controls.py` 只有 `DRIFT-MATCHED-v1`，文件明寫它「不是隨機進場模擬」 | **需要新增評估能力** |
| 資料集（候選 A） | Notion §13：TSM `GO`，2,193,372 rows；`governance/market_readiness.json` IBKR_TSM = **NO_GO**；開發期為 [2016-10-19, 2025-01-01)（`docs/PHASE5C1_DATA_AVAILABILITY.md`），**已被 5 個 SMA 授權使用 → 只能探索性** | 已接觸資料 |
| 資料集（候選 B） | Notion §16：台股 FINAL GATE seq 68 `SCOPED_GO`、`bar_start` 有來源證據、`volume_raw` 單位為張；不在 `market_readiness`；台股跳動單位級距（G03b）尚未實作 | **BLOCKED／NEEDS_CONFIRMATION** |
| 資料集（候選 C） | Notion §13：SOXX `SCOPED_GO`，有 87 分鐘排除；不在 `market_readiness`；沒有成本設定 | **NEEDS_CONFIRMATION** |
| Data Catalog 試算表 | 本次**沒有開啟**；上述資料集狀態取自 Notion §13、§16 的摘錄 | NEEDS_CONFIRMATION |

---

## 5. 阻擋項與需 Haw 決定的問題

### 阻擋項

| # | 阻擋項 | 類型 |
|---|---|---|
| X1 | 現有執行器只支援 SMA，需要新增 TA-01 訊號函式與分派 | 新能力（Agent 端） |
| X2 | 評估器沒有隨機進場虛無分布 | 新能力（評估端） |
| X3 | K 棒時間標籤、開盤與收盤競價歸屬 UNKNOWN。如果開盤競價成交量落入第一個合格 bucket，會偽造出「量增」訊號，**不得自行假設** | BLOCKED |
| X4 | bucket 成交量的聚合方式、TSM 成交量單位、零量分鐘的頻率 | NEEDS_CONFIRMATION |
| X5 | 成交量有日內 U 形季節性：跨日延續的 20 根均量，會讓早盤 bucket 系統性更容易達到 2 倍。改用「同時段均量」需要新指標，現有語法做不到 | 方法風險，需要決定 |
| X6 | 15:15 時間出場能否在當日成交未以 Core 原始碼核對 | UNVERIFIED |
| X7 | 沒有授權（0002 已用完）；Market Gate NO_GO | BLOCKED |
| X8 | TSM 開發期資料已被使用，不是盲測，Holdout 也不是；研究總數 K 未知 | 只能探索性 |

### 需要 Haw 決定的問題

1. **市場與資料集**：TSM 15m、台股 2330 5m，還是 SOXX？選台股的話，需要先處理跳動單位級距（G03b）與成本設定。
2. **參數**：是否接受 20 / 2.0 / 0.75 / 10 / 8 這組事先選定的值？一旦登記就固定。
3. **競價成交量（X3）**：先取得競價歸屬證據？還是事先登記「每日第一個合格 bucket 不產生訊號」？後者是設計選擇，必須由你決定。
4. **成交量基準（X5）**：維持跨日延續的 20 根均量（記錄偏誤風險），還是投入新增「同時段均量」指標？
5. **主要比較基準**：使用需要新增的隨機進場虛無分布，還是改用既有的 DRIFT-MATCHED-v1？後者文件明寫無法證明擇時能力。另外需要決定種子、重抽次數、判定百分位。
6. **成本**：核准哪一份成本設定、主要情境與費率來源。
7. **研究定位**：在已接觸的 TSM 開發期資料上只做探索性研究，還是等待前瞻資料，再做確認性驗證？
8. **研究總數 K**：TSM 資料上過去的研究總數，供多重檢定使用。

---

## 6. 偏誤與資料品質風險

| 風險 | 說明 |
|---|---|
| 前視偏誤 | 壓力位、均量、支撐都排除當根（`lag 1`）；只用已完成的 bucket；決策時間採 Core 的保守可用時間；成交嚴格晚於決策。但時間標籤與發布延遲仍 UNKNOWN |
| 選擇偏誤 | 參數沒有依資料挑選；開發期資料已被使用；市場選擇如果偏好「已被研究過」的 TSM，代表重複使用同一份樣本 |
| 多重檢定 | 主要假設只有 1 個；次要分析 (a)–(c) 都是探索性；研究總數 K 未知；同一份資料上的所有研究都必須計入 |
| 資料品質 | 零量或缺漏分鐘會讓整個 bucket 不合格，可能系統性排除低量時段，使相對量的基準偏高；成交量單位與聚合方式未確認 |

---

## 7. 建議下一步（本次不執行）

1. Haw 回答 §5 的 8 個問題。
2. 確認 X4：bucket 成交量聚合與成交量單位。只看文件或 Core 原始碼，不讀資料。
3. 若決定繼續：另開任務，新增 TA-01 純訊號函式與隨機進場評估能力（只用合成資料測試），再走既有的提案到授權流程。

---

## 檔案

| 檔案 | 說明 |
|---|---|
| `/home/claude/p5c_demo/ta01/P5C_TA01_PROPOSAL.md` | 本文件 |
| `/home/claude/p5c_demo/ta01/p5c_ta01_spec_draft.json` | 機器可判定的規格草稿 |
| `/home/claude/p5c_demo/ta01/static_validation.json` | 靜態驗證與重複檢查結果 |
