# Phase 5-B 規劃草稿：可信核准 × 授權綁定（待研究負責人核可）

> 狀態：**DRAFT**，尚未開工。前提：PR #11（Phase 5-A3，GO）由研究負責人合併到 main，#9、#10 關閉。
> 目標：讓「AI 提案 → 人核准並綁定授權 → 受治理的研究任務」真正接通，且身分可驗證、全程可追溯。
> 本階段**不**啟用真實 Research-only Runner、不消耗研究額度、不讀 Holdout、不改 Core。

## 0. 分工原則

| 角色 | 擁有 | 本階段負責 |
|---|---|---|
| Claude | Agent repo 的 `orchestration/`、`governance/`、`phase5a/`、驗收 notebook | B1、B2（主體）、B3、B4、整合驗收 |
| Codex | A2 `proposal/`、Core repo | B2-P（A2 端配合）、B6（真實 bindings 產生器）、**獨立審查** B1／B2 |
| 研究負責人 | 授權、核准、合併 | 核可本規劃、核可 B2 契約、簽核每次綁定 |

- 每個工作項各開一條分支，從合併後的 main 開出，各自開 Draft PR。
- 只要一個 PR 動到治理（B1、B2），就由另一個 agent 做獨立審查，避免同一個 agent 自己驗收自己。
- 共用契約（B2 新 Schema）先由 Claude 起草，經 Codex 審查、研究負責人核可後凍結（記下 commit pin），再各自實作。這和 5-A 的做法相同。

## 1. 工作項

### B1 可信核准身分（Claude）
問題：`approve` 目前只比對 approvers.json 裡的字串，不能證明真的是本人核准。

做法：新增 `ApprovalVerifier`，正式模式下 `approval_ref` 必須是本 repo 的 GitHub PR 留言或 review URL。Verifier 用 REST API 讀回該留言，並逐項檢查：
- 作者 login 在 allow-list 內；
- 留言內文含 `task_id` 與 `request_hash` 前 12 碼；
- 留言時間晚於 PENDING_APPROVAL；
- 留言未被編輯，或最後編輯時間早於核准時間。

驗證通過後，把留言 id、作者、時間、內文 sha256 寫進 APPROVED 事件。任何一項失敗或 API 無法存取，一律 fail closed（BLOCKED）。

其他：
- Token 只放在 Colab Secrets 或環境變數，不印出、不寫進檔案。
- Test mode 保留 TEST_FIXTURE，並注入 fake verifier。

驗收：
- 假作者、錯 task_id、編輯過的留言、時間早於 PENDING、API 403／離線、無 token：全部被擋。
- 正確的留言：通過。
- 舊的未驗證流程在正式模式下不可再用。

### B2 受治理的授權綁定（Claude 主體 + Codex B2-P）
問題：A2 產出的 Request 永遠是 `authorization_ref=PENDING`，所以永遠 BLOCKED。

做法：不修改 Request Schema v1.0.0，改為新增 `schemas/AUTHORIZATION_BINDING_SCHEMA.json`。綁定紀錄包含：
- 原 request_hash 與 proposal_sha256；
- authorization_id；
- 經 B1 驗證的核准留言；
- 綁定後的 request_hash。

`python -m strategy_agent.orchestration bind` 的行為：
1. 讀 A2 的 PENDING Request；
2. 驗證授權存在、未撤銷、範圍吻合（RA03–RA12）；
3. 驗證額度仍有剩；
4. 產生新的 Request（僅把 `authorization_ref` 換成 RA，其餘位元組不變），並寫入 append-only 綁定紀錄。

綁定本身不消耗額度，也不自動核准；之後仍要走 submit → approve（B1）。

B2-P（Codex）：
- A2 provenance 加入綁定所需的鏈結欄位，或確認現有欄位已足夠；
- A2 仍然永遠不得選擇或簽發授權（現有測試保留）。

驗收：
- 未綁定 → G5 BLOCKED；綁定後 G5–G8 依實際授權判定；
- 範圍不符或額度用完 → 拒絕綁定；
- 同一授權重複綁定多個 Request 時，最多只能有 `max_batches` 個進入 RUNNING；
- 綁定紀錄被竄改 → ChainError；
- Proposal → 綁定 → Task → Archive 全鏈可追溯。

### B3 Synthetic adapter 身分比對（Claude）
`SyntheticExecutionAdapter` 比照 ArchiveReplay：比對 Request 和 fixture archive 的 strategy、參數、spec sha 與 snapshot。不一致就 FAILED。Route A 改用與 Request 一致的 synthetic fixture。

### B4 小修（Claude）
- SYNTHETIC_TEST 的 G5 改顯示 `NOT_EVALUATED`，不再顯示 PASS；
- 把核准紀錄（approver、approval_ref、驗證結果）放進驗收輸出；
- `route_b` 報告裡的 approver 欄位寫入 `governance_checks.json`。

### B6 真實 bindings 產生器（Codex）
從 repo 內既有、已釘選的 spec、dataset、cost 與 policy，產生 A2 的 bindings。不允許自由輸入路徑。只能綁定在 `market_readiness` 和既有授權範圍內的 artefact。輸出仍是 `authorization_ref=PENDING`。

### B5 啟用真實 Research-only Runner（**不在本階段**）
需要研究負責人另簽新授權，並做獨立驗收，之後單獨開 Phase 5-C。

## 2. 順序

1. 研究負責人：合併 PR #11 → 核可本規劃。
2. Claude 起草 B2 共用契約 → Codex 審查 → 研究負責人核可 → 契約凍結。
3. 平行進行：Claude 做 B1、B3、B4；Codex 做 B2-P、B6。
4. Claude 做 B2 主體（依賴 B1 與凍結的契約）。
5. 交叉審查：Codex 審 B1、B2；Claude 審 B2-P、B6。
6. Phase 5-B 整合驗收 notebook。整條路徑：A2 提案（真實 bindings）→ 綁定 → 經驗證的核准 → **Archive Replay**。仍不跑新回測。

## 3. 判定規則（草案）
- **GO**：B1–B4、B6 全部完成；在 Colab 上，以經驗證的核准跑完綁定 → replay 的全鏈；並有交叉審查紀錄。
- **SCOPED_GO**：核心完成，但缺 Colab 證據或交叉審查。
- **NO_GO**：出現任何核准或授權繞過、身分偽造、額度被消耗、Holdout 被讀取。

## 4. 反對者觀點
- 用 GitHub 留言當身分證明，前提是 GitHub 帳號本身安全。這比自填字串可靠，但仍不是密碼學簽章；若要更強，需要 commit 簽章或 OIDC，可列為後續選項。
- 綁定流程打通之後，AI 提案到正式研究只差人的一個動作。人的那一步必須真的審查，不能變成橡皮圖章。
