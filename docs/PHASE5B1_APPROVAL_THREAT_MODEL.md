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

## 6. 安全審核後修復（Codex review of PR #12 @ `cf15654`，NO_GO → remediation `eb72cd0`）

Codex 的 `SECURITY_REVIEW.md`、`reproduce.py`、`reproduction_results.json` 未交付到本 session（PR、repo、上傳檔、Drive、Notion 都找不到）。
以下依 remediation brief 列出的 Finding，對照程式碼逐項重新推導根因並修復。每一項都用 `docs/evidence/phase5b1_remediation/reproduce.py` 分別對修復前與修復後重現：

- 修復前：`reproduction_before_cf15654.json`，11 案全部 **EXPLOITABLE**
- 修復後：`reproduction_after.json`，11 案全部 **BLOCKED**

| Finding | 根因 | 修復 | 回歸測試 |
|---|---|---|---|
| S1 撤銷永久性 | 撤銷只看 GitHub「目前」的留言列表；撤銷留言被刪除或編輯後，舊核准又變回有效 | `approval/revocations.py` 改為 append-only、hash-chained 的撤銷 ledger：<br>- 第一次觀察到的撤銷就是**永久**的，並記錄 `observed_at`<br>- 驗證時先查 ledger 再查 GitHub<br>- ledger 損壞即 fail closed（`V08_REVOCATION_LEDGER_INVALID`）<br>- 隔離用的 verifier 不能寫入正式 ledger<br>- binding 在 commit 前會重新驗證 | `test_S1_*`（刪除、編輯、ledger 竄改、prepare→commit 之間撤銷、ledger 隔離） |
| S2 分頁不完整 | 每頁少於 100 筆就停，或最多只掃 50 頁，接著直接判 NOT_REVOKED | 只依 GitHub `Link rel="next"` 判斷掃描是否結束，並與 issue 的 `comments` 數量交叉核對。以下任一情況都視為 `REVOCATION_SCAN_INCOMPLETE`（fail closed）：<br>- id 重複<br>- 回傳不是 list<br>- API 錯誤<br>- 回傳被截斷<br>- 超過 `MAX_PAGES` | `test_S2_*`（第 51 頁出現撤銷、中途 500、Link 提前消失、數量不符、頁數上限、99／100／200 筆邊界） |
| S3 正式信任隔離 | `production` 依據的是 source 物件自報、可被修改的 `kind`／`transport_kind`；正式帳本以「路徑 == ROOT」判斷 | 正式信任改成三道防線：<br>- **只能**透過 `ApprovalEvidenceVerifier.for_production()` 取得 PRODUCTION：使用模組私有的 capability 與自建的 live source，每次 verify 都重新確認 source 是同一個且未被改動<br>- 證據的來源標籤由實際型別推得，不讀物件自報的屬性<br>- 帳本一律視為正式，除非帶有 `ISOLATED_TEST_GOVERNANCE_ROOT.json` 標記（不接受 symlink 標記）；路徑別名、相對路徑、真實帳本的副本都算正式 | `test_S3_*`（假冒 production 的 source、替換或修改 source、事後注入 transport、改標籤、副本／別名／相對路徑、isolated binding 不能進正式帳本、會消耗額度的 adapter 不執行） |
| M1 到期與時區 | 時鐘可為 naive；沒有拒絕「建立時間在未來」的留言 | 時鐘必須帶時區，統一換算成 UTC；條件嚴格為 `now < expires`；建立時間超前時鐘 5 分鐘以上即 `V07_CREATED_IN_FUTURE` | `test_M1_*`（剛好到期、到期前 1 秒、Asia/Taipei 時區、naive 時鐘、未來時間、336 小時邊界） |
| M2 額度 stale snapshot／TOCTOU | binding commit 沿用 prepare 時的 `uses` 快照 | commit 時在 binding log 的鎖內，從授權帳本、uses log、binding log 重新讀取權威狀態；append 前再驗證一次證據。會消耗額度的 dispatch 在有「原子化額度預留」之前一律 `BUDGET_RESERVATION_NOT_IMPLEMENTED`（fail closed） | `test_M2_*`（驗證後額度被用掉、驗證後授權被撤銷、兩個 request 並發綁定） |
| M3 重複 dispatch 競態 | RUNNING 的冪等重放會把別的 worker 的事件傳回來，呼叫端照樣執行 → 執行兩次 | 每個 task 一把非阻塞的排他檔案鎖；RUNNING 帶 claim nonce，nonce 不是自己的就絕不執行 | `test_M3_*`（6 個並發 dispatcher 只執行 1 次、完成後重派、重放的 claim） |
| M4 CI 失敗遮蔽 | `pytest \| tee` 沒有 pipefail；`returncode: 0` 寫死；`append_only` 在 base ref 不存在時判 PASS | workflow 預設 `shell: bash`（即 `-eo pipefail`）；記錄真實 exit code；新增 `append_only_guard`：base ref 或 git diff 失敗即 FAIL（`append_only.py` 被已結案的 Phase 3 notebook 釘選，維持原樣） | `test_M4_*`（CI 靜態檢查、子程序測試失敗 → 驗收 FAIL、base ref 不存在） |
| M5 封存 hash index 不完整 | `run_info` 的 hash 清單沒有涵蓋 `run_info.txt` 與 `notebook_context.json` | `notebook_context.json` 改由驗收程式寫入；最後寫入 `ARCHIVE_INDEX.json`，涵蓋所有頂層檔案與 work/ tree；並提供 `verify_archive()`／`--verify-archive` | `test_M5_*`（多出、修改、缺少檔案；binding 證據檔被修改或刪除） |
| 第 9 項 | 原始報告未取得，無法確認內容 | — | **PENDING**（待 Codex 原始報告複驗） |

新增 gate **B1-SEC**：驗收時在 pinned checkout 重新執行重現案例，必須**全部 BLOCKED**，否則 NO_GO。

**殘餘限制（修復後仍然存在）：**
- 撤銷若在任何 verifier 觀察到之前就被刪除，GitHub API 無從得知，無法補救。
- 撤銷 ledger 存在各執行環境內；要讓所有環境共用，需要把 `registry/approval_revocations.jsonl` 經 PR 提交。
- in-process 的 Python 程式碼仍可 monkeypatch 任何東西：capability 能防的是參數或物件層級的偽裝與誤用，防不了任意程式碼執行。
- 原子化的額度預留尚未實作；在它完成之前，真實 Runner 必須維持 DISABLED。
