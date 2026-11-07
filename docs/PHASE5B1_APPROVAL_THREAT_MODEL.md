# Phase 5-B1：核准與授權綁定威脅模型

範圍：`Proposal → Pending Request → Human Review → Verified Approval → Authorization Binding → Governed Request → Orchestrator`。
三種事件彼此獨立：**Approval（核准）**、**Binding（綁定）**、**Authorization Consumption（額度消耗）**。5-B1 只做前兩者。

## 1. 信任邊界與資產

| 資產 | 保護方式 |
|---|---|
| 研究負責人身分 | GitHub API 回傳的作者 `login` **與**不可變的數字 `id`。兩者都要列在 `governance/approval_identities.json`，login 也要在 `approvers.json` 內。另外 `author_association` 必須是 OWNER／MEMBER／COLLABORATOR，且 `performed_via_github_app` 必須是 `null`。 |
| Repository | 數字 id `1443253343` 與 full_name 同時釘選；URL 正規式只接受這個 repo 的 `…/(pull\|issues)/N#issuecomment-ID`。 |
| 核准內容 | 留言中**恰好一行** `RESEARCH-APPROVAL v1 …`。這一行用完整 64-hex 寫明：request hash、authorization、scope hash、task id（僅 APPROVE_TASK）與到期時間。留言其餘文字一律不解析、不執行。 |
| 授權與額度 | 只讀 `ResearchAuthorizationBook`／`Guard`／`HoldoutBook`／uses log。綁定不建立、不修改、不重置授權，也不消耗額度。 |
| 綁定紀錄 | `registry/authorization_bindings.jsonl`（ChainedLog，append-only、hash-chained）。被綁定的 request 與核准證據用內容定址並 O_EXCL 寫入，不可覆寫。 |

GitHub API 回傳的資料、Request JSON、留言內文都視為**不可信輸入**，每個欄位都會重新驗證。

## 2. 驗證規則（`approval/verifier.py`）

| 代碼 | 規則 |
|---|---|
| V01 | URL 是釘選 repo 的留言 URL |
| V02 | API 回傳的 repo full_name 與 id 都相符 |
| V03 | 留言存在（刪除即 `EVIDENCE_NOT_FOUND`），id／issue_url／html_url 與 URL 一致 |
| V04 | 作者身分如上；**經 GitHub App 發出的留言一律拒絕** |
| V05 | 未編輯（`updated_at == created_at`） |
| V06 | 恰好一行 token；decision、intent、request、authorization、scope、task 全部等於**目前** request 推出的期望值 |
| V07 | 未過期、到期時間晚於建立時間，且有效期不超過 336 小時 |
| V08 | 同一 thread 內沒有 allow-list 身分發出的 `RESEARCH-APPROVAL-REVOKE v1 comment=<id>` |
| V09 | 正式模式只接受 LIVE GitHub REST 來源；TEST_FIXTURE 與注入的 transport 都不算正式 |
| V00 | API 不可用、403、沒有 token、payload 結構錯誤：**fail closed** |

時間先後不靠時鐘證明。token 裡必須寫出 request hash（APPROVE_TASK 還要寫出由 hash 推得的 task id），這些值在 request 產生前無法得知，所以合法的核准必然晚於 request 本身。

## 3. 威脅與對策（皆有實測，見 `security_negative_tests.json`）

| # | 威脅 | 對策（拒絕代碼） |
|---|---|---|
| 1 | 偽造核准者名稱 | 身分只取自 GitHub 作者；自填 approver 與實際作者不同時為 `APPROVER_MISMATCH`；沒有證據為 `APPROVAL_EVIDENCE_REQUIRED`；任意 https 網址為 `V01` |
| 2 | 偽造 GitHub URL | `V01`（其他 host、沒有留言錨點、相似 repo 名） |
| 3 | 偽造 comment payload | 數字 id 不符或 Bot 類型為 `V04`；html_url 與 URL 不一致為 `V03` |
| 4 | 不在 allow-list 的身分 | `V04_AUTHOR_NOT_ALLOWED`／`V04_AUTHOR_ASSOCIATION` |
| 5 | 拿其他 repo 的紀錄冒充 | URL 為 `V01`；API repo id 為 `V02` |
| 6 | 核准後修改 Request | `V06_SUBJECT_MISMATCH`；修改已綁定的 request 時 hash 對不上，為 `G11` FAIL |
| 7 | 核准後修改 Strategy Spec | `G2` 檔案 sha 不符 |
| 8 | 核准後修改 Dataset 或成本 | 成本檔改動為 `G4`；資料欄位改動使 hash 改變，為 `G11` |
| 9 | 已耗盡的授權被重複綁定 | `B06_BUDGET_EXHAUSTED`；剩餘額度已被其他綁定佔滿時為 `B06_AUTHORIZATION_FULLY_BOUND` |
| 10 | 核准證據重複使用 | token 明寫主體，所以拿去用在別的 request 會是 `V06`；同一留言再核准第二個 task 為 `APPROVAL_EVIDENCE_REUSED` |
| 11 | 過期、撤銷、編輯、刪除 | `V07`／`V08`／`V05`／`V03` |
| 12 | GitHub API 不可用 | `V00_APPROVAL_SOURCE_UNAVAILABLE`（含沒有 token 的情況） |
| 13 | 多個 worker 同時綁定 | `append_if` 在 fcntl 鎖下做 compare-and-append：只會有 1 筆紀錄，其餘為 `ALREADY_BOUND` 或 `B09_REQUEST_ALREADY_BOUND` |
| 14 | 中途 crash | 綁定是否有效只看 log 紀錄；孤兒檔不構成綁定（NOT_BOUND）；重試時位元組相同即冪等完成 |
| 15 | Synthetic 核准流入正式 | repo 正式帳本拒絕測試 verifier（`ValueError`、`B08_TRUST`）；TEST_FIXTURE 來源不能標成 production（`V09`）；ISOLATED_TEST 核准不能驅動會消耗額度的 adapter（`APPROVAL_TRUST_NOT_PRODUCTION`）；SYNTHETIC_TEST 不可綁定（`B01`） |
| 16 | LLM 嘗試自行核准 | `V04_POSTED_VIA_APP`：Claude 與 Codex 都透過 GitHub App 發文，GitHub 雖把作者顯示為使用者，但 `performed_via_github_app` 不是 null。prompt injection 文字為 `V06_NO_APPROVAL_TOKEN`；多行 token 為 `V06_AMBIGUOUS`；A2 仍強制 `AUTHORIZATION_MUST_REMAIN_PENDING` |
| 17 | Holdout 不當存取 | 研究窗口碰到 holdout 時，RA08／RA09 會使 `G6`／`G8` FAIL；`access` 不是 `NO_ACCESS` 時 schema 直接拒絕 |
| 18 | 透過 CLI 跳過驗證 | A1 `approve` 必須提供 `--evidence-ref`，而且只用 LIVE 驗證器（實測：假網址為 `V01`，沒有 token 為 `V00`）；binding CLI 沒有測試或 fixture 選項，預設 dry-run；phase5a CLI 沒有 approve 指令 |

## 4. 無法由 GitHub 證明、列為限制

1. **個人 token（PAT）代發。** agent 若持有研究負責人的 PAT 而不是 App token，它發的留言在 API 上與本人留言完全相同（`performed_via_github_app = null`），無法區分。對策只能靠營運面：PAT 只放在 Colab Secrets 給唯讀驗證使用，不交給任何 AI agent；agent 環境一律用 GitHub App。此項**不宣稱可證明**。
2. **帳號遭盜用。** GitHub 帳號本身被入侵時，所有證明都會失效。這不是密碼學簽章；更強的做法（signed commit 或 OIDC）留待後續。
3. **v1 只支援 issue／PR conversation comment。** review 與 review comment 不採用，一律以 `V01` 拒絕。
4. **編輯偵測依賴 `updated_at`。** 只要 `updated_at` 改變，例如部分 GitHub 操作，就一律判為編輯並拒絕，偏向 fail closed。
5. **撤銷只掃描同一個 thread。** 這是 v1 的撤銷契約，其他 thread 的撤銷不生效。
6. **ARCHIVE_REPLAY 不要求綁定。** 它只唯讀重播一個已在原授權下完成的 run，0 batch。G11 回報 `NOT_APPLICABLE`，但 task 核准仍須通過已驗證的 APPROVE_TASK 證據。

## 5. 營運規則

- 核准文字由 `python -m strategy_agent.binding approval-line …` 產生。研究負責人必須**自己**在 GitHub 網頁貼上，不可經由 agent。
- Token 只放在 Colab Secrets 或環境變數（`GH_TOKEN`／`GITHUB_TOKEN`），程式不會印出或寫入；`run_info.txt` 會記錄 `github_token_written=false`。
- 撤銷方式：在同一個 thread 貼 `RESEARCH-APPROVAL-REVOKE v1 comment=<id>`。執行前的重新驗證（dispatch 的 QUEUED 階段）會因此阻擋 task。
