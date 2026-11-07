# Phase 5-A2 — Research Proposal Agent MVP：Notion 交班草稿

**文件狀態：READY_TO_PUBLISH / NOT_PUBLISHED_TO_NOTION**

目標區域：
https://app.notion.com/p/2ceb65aa15fa80e9a322eea10bd87e4f?v=2ceb65aa15fa80b38478000c58450521&source=copy_link

使用者要求在該區域找適當子頁更新。尚未能列出或核對子頁，因此沒有宣稱
找到、建立或更新任何 Notion 頁面。以下內容可交班，不能視為已寫入 Notion。

## 工程交付（2026-11-07，Asia/Taipei）

- Repository：hh4832/strategy-research-agent。
- Branch：feature/phase5a2-proposal-agent。
- 起始 main：95b6ec52e1b5dc7d618cd8586c6a384e26b4f621。
- Implementation commit：48771ff0202be05a1be39e680a9e9985bff84dd1。
- 後續範例／交班文件 commit 為此實作的後裔；最新 HEAD 與 CI 見交付回覆及分支。
- 工程判定：SCOPED_GO，僅研究設計與待審核輸出，不是執行／策略效果核准。

## 已完成

- LLM Draft + Deterministic Validation；FakeLLMAdapter 與 FileDraftAdapter，無 API Key／大型模型套件。
- Proposal Draft／Proposal Schema；自然語言、機制、假設、null、條件、資料／PIT、單一變數變更、搜尋預算 metadata、評估計畫、偏誤清單、反對者與 provenance。
- 重用既有 Strategy Spec fragments 與純 family canonicalization；不建立或修改 Strategy Registry。
- Hypothesis／family／parameter fingerprints；重複、微調參數、重複資料與依近期最高報酬挑選的審查旗標。
- CLI draft／validate／export-request；新提案僅 PROPOSED／PENDING_INFORMATION，永遠 NEEDS_REVIEW。
- Momentum、Mean Reversion、Margin Financing 三個 SYNTHETIC_FIXTURE 及一個完整結構設計；三個廣泛構想不補造規則。
- 完整例子的六份輸出已由實際 CLI 生成；缺少操作者綁定時 export 回報 PENDING_INFORMATION，未產生 Request。
- 同一 model completion／commit／固定時間／history 的六份輸出可逐位元重現。

## 共用 Request Contract

開發途中 Claude 發布共用契約，已 fetch 並以原始位元組採用，沒有另定不相容版本。

- Commit：5d088c4de362add925344d8e48b36c2e9d728d96。
- Schema：schemas/RESEARCH_REQUEST_SCHEMA.json v1.0.0。
- SHA256：1b0530f321c6c71e9eac1bbe5f9c33e3f92e4af4c4ce11b80777c9a94c5324eb。
- Schema compatibility／pin／drift／reference／window tests：PASS。
- Exporter 只接受明確既有產物引用並核對檔案 hash／策略結構；不猜測正式 IDs。
- authorization_ref 固定 PENDING；request_submitted=false、execution_eligible=false、本輪消耗0。
- Schema 缺失則 CONTRACT_PENDING；規格、綁定缺失則 PENDING_INFORMATION；都不寫入自稱正式的 Request。
- Orchestrator／governance 端到端未執行；後續仍需個別核對及本人核准。

## 測試與隔離

- 新 Proposal／Contract tests：54 PASS。
- Python3.11／3.12 完整無 Core regression：各416 PASS／54 optional-Core skips。
- Append-only：PASS。
- 未修改 orchestration、research_only、governance、evaluation、Core、Registry、設定、正式 Gates、授權／使用紀錄、Holdout、既有結果或 Notebook。
- 未下載融資資料、未執行真實回測、本輪研究額度消耗0。
- GitHub CI、Draft PR 建立實際結果另見最終交付；不以分支網址冒充已建立 PR。

## 尚未整合

正式 LLM provider；對自然語意／因果／資料可用性的人工核對；完整可執行 Strategy Spec
及真實既有產物綁定；Orchestrator governance/E2E 與本人研究授權。

## Notion 寫入阻擋

已對指定 app.notion.com URL 實際連線；使用環境提供的 proxy 重試後為 HTTP403。
目前沒有 Notion connector 或 cua_repl.js／桌面控制工具；桌面 capability 為
 enabled=true、state=unknown，不能據此宣稱已登入或可控制 Notion。

環境 ID（非憑證）：ccarenv_b64_Y2NhcmVudl81ODhkZjg4MzE3MWM4MTkxYTlmNmNhOWRmZGI5Y2IwYQ。

沒有繞過 proxy、尋找憑證、要求在聊天貼 API Key，或虛報 Notion 寫入成功。

## GitHub 交付觀測（已驗證）

- 程式＋範例 candidate commit：4f712acc2f95c11aa377cd3ff63a6ae72a44c17b。
- GitHub CI：PASS，https://github.com/hh4832/strategy-research-agent/actions/runs/42338597512 。
  包含 Python3.11/3.12、core-compat、research-synthetic、phase4a-core、development-snapshot-preflight、phase4a-dev-smoke。
  append-only 在 push workflow 依既有設定 skip；本機 append-only PASS。
- Draft PR：**NOT_CREATED**。已實際 POST 標題「Phase 5-A2: Research Proposal Agent MVP」、
  base=main、head=feature/phase5a2-proposal-agent、draft=true；GitHub API 回覆 Forbidden。
- 比較／建立頁：https://github.com/hh4832/strategy-research-agent/compare/main...feature/phase5a2-proposal-agent?expand=1
- 這次後續提交只補交班文件；最新文件 HEAD 以分支與最終交付 SHA 為準。
- Notion：**NOT_PUBLISHED**，原因仍為指定 URL HTTP403 且缺少可用連接／控制工具。
