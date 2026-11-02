# Quantitative Evaluation Protocol v1.0

| 項目 | 內容 |
|---|---|
| 文件版本 | v1.0（2026-11-03） |
| 實作 | `src/strategy_agent/evaluation/metrics.py`、`splits.py`（含手算與性質測試） |
| 適用 | Phase 3 Strategy Agent 所有策略研究；規格欄位 `experiment.evaluation_protocol = "QUANT_EVALUATION_PROTOCOL v1.0"` |
| 核心禁止 | **不得用完整資料集反覆優化，再把同一資料集當成獨立驗證。** |

---

## 1. 為什麼需要這份規約

自主 Agent 最大的風險不是寫錯程式，而是**在同一份歷史資料上試太多次**。以每日報酬、一年樣本為例（Sharpe 估計量的變異數約 1/T），即使所有策略真實 Sharpe 都是 0：

| 試驗數 N | 最佳者的期望 Sharpe（年化，純屬運氣） |
|---|---|
| 10 | ≈ 1.6 |
| 100 | ≈ 2.5 |
| 1000 | ≈ 3.3 |

（由 `expected_max_sharpe(N, 1/250) × √252` 計算。）因此本規約的重點是：**資料分區先鎖定、試驗全部記帳、統計門檻隨試驗數提高。**

---

## 2. 資料分區（執行任何回測前登錄）

```
|<------------- Development（約 80%）------------->|<-- Final Holdout（約 20%，≥ 12 個月或 ≥ 20% 樣本）-->|
|  walk-forward: [train][test] → [train][test] …   |   LOCKED：每個 FROZEN 規格只評估一次                  |
```

1. **邊界由研究負責人事前核准並鎖定（審查決策 Q7）**：依資料集 coverage manifest 選定 holdout 起點，以 `strategy_agent.governance.holdout_admin approve` 寫入 `governance/holdout_locks.jsonl`（需 allow-listed approver 與核准連結），經 CODEOWNERS 審核的 PR 合併後生效。Agent 不得設定、修改或反覆測試 holdout。沒有核准 lock 的資料集，所有回測都被拒絕（H01）。
2. **Development 內只做 walk-forward**；不單獨保留一個反覆使用的「validation set」。若需要調參，參數選擇只在每個 fold 的 train 區間內進行（§3.3）。
3. **Final Holdout**：只允許 `status=FROZEN` 的規格，且同一 `holdout_lock_id` 只能讀一次（Executor 強制）。Holdout 結果只決定「保留／淘汰」，**不得**用來修改規格；修改後即為新版本，但它不能再使用同一個 holdout（holdout 已被看過，對這個 family 失去獨立性）。
4. **新資料**：快照升級後新增的期間，可作為新的「前推 holdout」（forward holdout），規則同上。

### 2.1 各資料集的特殊限制

| 資料集 | 分區方式 | 限制 |
|---|---|---|
| IBKR TSM（GO）、SOXX（SCOPED_GO） | 依日曆時間切分 | SOXX 必須套用 2017-01-05 14:30–15:56 UTC exclusion |
| IBKR NQ／ES（SCOPED_GO，逐季合約） | 依日曆時間切分，**以 roll 日為 fold 邊界** | 9 個季度合約約兩年資料 → 樣本有限；不得跨合約拼接；exclusion 窗依 B11 規則 |
| TXF 正式快照（185 抽樣日） | 以**交易日為單位的區塊**，依日期排序切分 | 非連續抽樣日 → 只允許當沖；統計檢定力低（§5.4），結果最多標為 `EXPLORATORY` |
| 台股 5m（SCOPED_GO） | 依日曆時間切分 | 只限 §26 普通股範圍、PIT universe、quality mask；跨股票研究時 split 以日期為準（同一日期所有股票同屬一個區塊） |

---

## 3. Walk-forward、Purging 與 Embargo

### 3.1 Walk-forward 設定

| 參數 | 預設 | 說明 |
|---|---|---|
| 形式 | rolling（固定長度）；可選 anchored（擴張） | 兩者都只能「train 在前、test 在後」 |
| train 長度 | 日內策略 ≥ 12 個月；跨日策略 ≥ 24 個月 | 至少涵蓋一個完整波動循環的近似 |
| test 長度 | 3 個月（NQ／ES：一個合約週期） | |
| step | = test 長度（OOS 不重疊） | 串接後的 OOS 序列才可做 PSR／DSR |
| 最少 fold 數 | 6（TXF 例外，見 §2.1） | |

**OOS 結果 = 把各 fold 的 test 區間串接**；每個 test 區間只被看過一次。

### 3.2 Purging／Embargo：依標籤重疊決定

每筆交易（樣本）有標籤區間 `[t0, t1]`：`t0` = 決策時間，`t1` = 出場成交時間（含成交延遲）。

| 情況 | 是否需要 purge | 是否需要 embargo |
|---|---|---|
| 日內策略、強制當日平倉，fold 邊界放在 session 邊界 | 否（標籤不跨 session） | 否 |
| 跨日策略（如 SMA swing），walk-forward（train 永遠在 test 前） | **是**：刪除 `t1 ≥ test_start` 的 train 樣本 | 否（train 不在 test 之後） |
| fold 內調參使用 purged k-fold（train 可能在 test 之後） | 是 | **是**：刪除 `t1(test) < t0 ≤ t1(test) + embargo` 的樣本；embargo ≥ 最大持有期，日內資料至少 1 個 session |
| 規格沒有 `max_holding_bars` 的跨日策略 | 用 train 區間觀察到的最大持有期 × 1.5；並在報告標記 | 同上 |

實作：`splits.walk_forward(..., t0, t1)`、`splits.purged_kfold(..., embargo)`；每個 fold 須通過 `assert_no_leakage`。

### 3.3 參數選擇（只在 PREREGISTERED_GRID 時）

1. 格點、`max_trials` 在規格中預先登錄；不可在看到結果後新增格點。
2. 每個 fold：只用該 fold train 區間（必要時 purged k-fold）挑選參數 → 以挑出的參數跑該 fold test。
3. 記錄每個 fold 選出的參數；參數在 fold 間劇烈跳動本身就是不穩健的證據（G10）。
4. **所有**格點的結果（不只最佳）寫入 Registry，供 PBO 與 DSR 使用。

---

## 4. 交易成本敏感度

- 每個規格以 `costs.sensitivity_multipliers`（必含 0、1、≥2）重跑同一組 OOS。
- 回報每個倍率的淨損益與 **break-even 倍率** = 毛利 ÷ 總成本（`cost_sensitivity()`）。
- 0× 只用來顯示「訊號本身」的毛利，**不得**用於選擇。
- 期貨每口費用、證交稅、最低手續費、取整規則由規格宣告；滑價以 tick 或 bps 宣告。v1 尚未模擬市場衝擊（成交量相依），因此大部位策略需在報告註明。

---

## 5. 指標定義、前提與限制

| 指標 | 定義（實作） | 前提 | 限制／注意 |
|---|---|---|---|
| Sharpe | mean／std（ddof=1）；年化 × √periods | 報酬 i.i.d. 才可用 √T 年化 | 自相關會使年化值偏誤（Lo 2002）；**同時回報每期值**；日內策略以「每日淨報酬」計算，不用分鐘報酬 |
| Sortino | mean ÷ √mean(min(r−τ,0)²)，分母用全部觀測 | 同上 | 下行樣本少時極不穩定 |
| Max Drawdown | min(equity／running peak − 1)；另報持續期 | equity > 0 | 單一路徑統計，無信賴區間；樣本越長越大，不可跨不同長度比較 |
| Profit factor | 總獲利 ÷ 總虧損（已平倉交易） | — | 少量交易時被單筆極端值主導；不含未平倉部位 |
| Turnover | Σ|成交名目|／2 ÷ 平均權益 ÷ 年數 | — | 用於成本合理性檢查 |
| Exposure | 持倉 K 棒比例 | — | 低 exposure 策略的 Sharpe 要搭配「持倉期間報酬」一起看 |
| PSR | Φ((SR̂−SR*)√(T−1)／√(1−γ₃SR̂+(γ₄−1)SR̂²／4)) | 平穩、近似獨立；T ≳ 30 | 自相關未校正；γ₄ 為非超額峰態 |
| MinTRL | 使 PSR ≥ 1−α 所需觀測數 | 同 PSR | 用來判斷「樣本是否足以下結論」 |
| DSR | PSR 以 SR* = E[max SR | N 次 null 試驗] 評估 | N 應為**有效**獨立試驗數；Var(SR) 取自同 family 的試驗分布 | N 低估 → 過度樂觀；N 用原始試驗數較保守 |
| PBO（CSCV） | IS 最佳設定在 OOS 排名低於中位數的比例 | 同期間比較 N ≥ 2 組設定；區塊夠長使區塊間相依可忽略 | 只衡量「選擇程序」的過擬合，不衡量策略本身好壞 |

### 5.4 樣本數與檢定力（必須在報告中出現）

以常態報酬近似、單尾 α = 0.05：

| 真實年化 Sharpe | 需要的交易日數（MinTRL） |
|---|---|
| 1.0 | ≈ 684 日（約 2.7 年） |
| 2.0 | ≈ 173 日 |

TXF 正式快照只有 185 個抽樣日：它最多只能確認年化 Sharpe 約 **1.9 以上**的效果。因此 TXF 上的結果一律標記 `EXPLORATORY`，不能單獨作為入選依據，必須在更完整資料（新快照）上重驗。

---

## 6. 多重檢定與試驗帳本

### 6.1 定義

- **Trial**：一個（規格版本 × 參數向量）在 development 資料上的一次評估。失敗、被拒、跑一半中止的也算。預先登錄的格點**全部**計入（即使未跑完）。
- **Family**：同一假說機制 × 同市場 × 同時框（`experiment.trial_family_id`）。Generator 不得透過改名、微調規則來開新 family；Validator 以 canonical hash 與規則結構相似度合併。

### 6.2 方法選擇

| 目的 | 方法 | 控制 | 依賴假設 |
|---|---|---|---|
| 單一 family 內挑最佳設定是否只是運氣 | **DSR**（N = family 試驗數） | 選擇偏誤 | 見 §5 |
| 一輪報告中多個候選「SR > 0」 | **Holm**（FWER） | 任意相依 | 保守 |
| 大量初篩 | **Benjamini–Yekutieli**（FDR，任意相依）；確定獨立或 PRDS 才用 BH | FDR | BH 需獨立／PRDS |
| 選擇程序是否過擬合 | **PBO** | — | 見 §5 |

p 值來源：串接 OOS 日報酬的 1 − PSR(0)。

### 6.3 有效試驗數

先用原始 N（保守）。若要用較小的有效 N，必須以 Registry 中的試驗報酬相關矩陣做分群（例如平均相關 ≥ 0.8 的視為同一群），並在報告中同時列出兩種 N 的 DSR。

---

## 7. 樣本外穩定度

- 正報酬 fold 比例（`oos_positive_fraction`）
- 中位數 OOS Sharpe 與 IS／OOS 衰退率（`is_oos_degradation`）
- 最差 fold、單一 fold 對總 OOS 淨利的貢獻占比
- 跨 fold 選出參數的變動（PREREGISTERED_GRID 時）
- 與 **時間對應的隨機進場基準**（同日、同持有時間分布、固定 seed）比較，對應規格的 null hypothesis

---

## 8. Strategy Selector Gates（全部通過才是 CANDIDATE）

| Gate | 條件 |
|---|---|
| G1 | 規格 REGISTERED、Validator 0 個 ERROR、hash 正確 |
| G2 | 引擎 SHA 在 Registry 有 `BENCHMARK_PASS`（B01–B10、B12） |
| G3 | 資料 provenance 與規格釘選一致；SCOPED_GO exclusions 已套用 |
| G4 | OOS **有效**已平倉交易 ≥ 30、fold ≥ 6；跨 exclusion 窗交易已 invalidated（Q5）且 `sample_sufficiency.status = SUFFICIENT`（Q5-1 有效交易 ≥ 30、Q5-2 invalidated 比例 ≤ 10%、Q5-3 invalidated \|PnL\| 占比 ≤ 25%）；未達 MinTRL 則最高只能標 `EXPLORATORY` |
| G5 | 1× 成本下 OOS 淨 Sharpe > 0，且 PSR(0) ≥ 0.95 |
| G6 | DSR ≥ 0.95（N = family 原始試驗數） |
| G7 | 本輪 Holm 調整後 p ≤ 0.05 |
| G8 | break-even 成本倍率 ≥ 2，且 2× 成本淨損益 > 0 |
| G9 | 正報酬 fold ≥ 60%；單一 fold 貢獻 ≤ 40% 總 OOS 淨利 |
| G10 | （有格點時）鄰近參數 OOS Sharpe 中位數 ≥ 所選參數的 50%；設定 ≥ 10 組時 PBO < 0.2 |
| G11 | OOS 最大回撤 ≤ `risk_constraints.max_drawdown_halt`；turnover／exposure 已回報 |

任何 Gate 失敗 → `REJECTED`，報告寫明 Gate 代碼與數值。門檻變更需人工核准並升版本規約，不能由 Agent 調整。

### 8.1 Final Holdout 判定（每個 FROZEN 規格一次）

- 1× 成本淨損益 > 0 且 holdout Sharpe ≥ 0 → `CANDIDATE_FOR_HUMAN_REVIEW`
- 否則 → `RETIRED`；結果照樣寫入報告與 Registry。

---

## 9. 實驗可重現性

每筆實驗必須能由 Registry 單獨重建：

1. 規格 canonical SHA-256（`spec_sha256`）
2. 資料：snapshot ID、producer SHA、manifest SHA（若有）、exclusions 清單、載入／排除列數
3. 程式：strategy-agent SHA、引擎 git SHA（不可是套件版本字串）、execution convention 版本、環境 lock SHA
4. split／fold ID、角色、窗口；seed；成本倍率
5. 產出檔（trades、equity、metrics）以 URI + SHA-256 保存

重現性檢查：每週隨機抽 5% 已完成實驗重跑，成交必須逐筆一致、指標差 < 1e-9；不一致即凍結該引擎 SHA 並通知。

---

## 10. 明確禁止事項

- 用全部資料優化後，把同一資料當驗證或「樣本外」。
- 看完 OOS／holdout 後新增格點、改規則、改成本或改 split，卻沿用原 family 或原 holdout。
- 只回報最佳試驗；刪除或覆寫失敗紀錄。
- 用 0× 成本結果做選擇。
- 以單一 Sharpe 或累積報酬排序決定入選。
- 把 TXF 抽樣快照的結果當作確認性證據。
- 把 Phase 2 `RESEARCH_INTEGRATION_DEMO` 輸出當作策略績效。
