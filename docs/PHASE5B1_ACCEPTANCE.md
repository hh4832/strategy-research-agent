# Phase 5-B1：Trusted Human Approval & Governed Authorization Binding — 驗收

> **安全審核更新（2026-11-07）**：Codex 對 PR #12 @ `cf15654` 做獨立安全審核，判定 **NO_GO**（HIGH：S1、S2、S3；另有 MEDIUM）。
> 下方第 9 節的「Colab GO」（`approvals/20261107_173254_a3251d3`）是**修復前**的版本，**它的安全判定已被推翻**。該證據保留不動，不覆寫。
> 修復見 §10 與 [威脅模型 §6](PHASE5B1_APPROVAL_THREAT_MODEL.md)；修復後的程式 pin 為 `eb72cd0`，notebook `PHASE5B1-APPROVAL-v2`。

> 範圍：只做 Human Approval 與 Authorization Binding。不跑回測、不耗額度、不讀 Holdout、不呼叫券商 API、不啟用真實 Runner、不改 Formal Gate；Phase 5-B2／5-B3 未開始。
> 本次完成後，交由 Codex 做獨立安全審核。

## 1. 分支

- `feature/phase5b1-trusted-approval`，從 main `c863ce2` 建立。
- Code pin `a3251d3`；Notebook `notebooks/Phase5B1_Approval_Binding_Acceptance.ipynb`（`PHASE5B1-APPROVAL-v1`）。

## 2. 流程與三個獨立事件

```
A2 Proposal → PENDING Request (authorization_ref=PENDING)
   → 研究負責人在 GitHub 網頁留言：RESEARCH-APPROVAL v1 … intent=BIND_AUTHORIZATION request=<source sha> authorization=<RA> scope=<sha> expires=…
   → ApprovalEvidenceVerifier（live GitHub API）→ Approval Evidence（schema-valid）
   → AuthorizationBindingService（B01–B09；dry-run 預設）→ 新的不可變 Bound Request + binding log 紀錄
   → Orchestrator.submit：G1–G11（G11 = 綁定紀錄與檔案完整）
   → 另一則留言：… intent=APPROVE_TASK request=<bound sha> task=<T-…> …  → Orchestrator.approve（驗證後才 APPROVED）
   → dispatch：執行前再驗證 task 核准與綁定核准（未刪除、未編輯、未撤銷）
```

**Approval ≠ Binding ≠ Consumption。** 綁定的 `consumes_budget` 與 `approves_execution` 都是 const false；額度消耗仍只發生在未來經批准的執行路徑上。

## 3. 契約

- `schemas/RESEARCH_APPROVAL_EVIDENCE_SCHEMA.json` v1.0.0。
  - 涵蓋：來源（kind／transport／trust）、repo 身分、GitHub 作者（login／id／association／`performed_via_github_app = null`）、留言（id／URL／時間／未編輯／內文 sha／token 行）、intent、subject（request sha、task、authorization、scope sha）、decision、expiry、撤銷狀態、verified_at、verifier、驗證結果。
  - 驗證結果 const PASS：失敗的證據不可能通過 schema。
  - 若 trust 為 PRODUCTION，來源必須是 `GITHUB_REST_API` + `LIVE`。
- `schemas/AUTHORIZATION_BINDING_SCHEMA.json` v1.0.0。
  - 記錄 parent／derived relationship（`changed_fields = ["authorization_ref"]`）。
  - 記錄 source proposal id／sha、provenance sha、source／bound request sha 與檔案 sha、authorization id 與綁定當下的狀態、scope sha、approval evidence id／sha／路徑／trust、binding actor（取自已驗證的作者）、timestamp、治理檢查結果。
- `governance/approval_identities.json`：repo 數字 id `1443253343`、`hh4832 → 269247281`、允許的 association、有效期上限 336 小時。
- Research Request Schema v1.0.0 **未修改**（sha256 `1b0530f3…`）。

## 4. B1-01..B1-10（本地，全新 process）

| Gate | 判定依據 | 結果 |
|---|---|---|
| B1-01 可信來源與身分 | 威脅 1–5、16；證據作者為 GitHub `269247281` | 隔離 PASS；**真實 GitHub：PENDING（需 Colab 與你本人的留言）** |
| B1-02 證據綁定 Request／Spec／Dataset／Authorization | subject 為完整 request sha 與 scope sha；威脅 6–8 | PASS |
| B1-03 未核准／過期／撤銷／偽造全拒 | 威脅 2、3、11、12 | PASS |
| B1-04 Pending 與 Bound 可追溯、不可覆寫 | parent／child 只差 `authorization_ref`；原檔未改；dry-run 不寫入；O_EXCL | PASS |
| B1-05 Bound Request 仍經既有治理 | 送入 Orchestrator 後 G11 PASS，只剩 G10 FAIL（`ELIGIBLE_PENDING_EXECUTION_ENABLEMENT`）；只改 JSON 的「假綁定」被 G11 擋下；撤銷授權後 G5 FAIL | PASS |
| B1-06 證據不可重用；並發與重試冪等 | 重複綁定回 `ALREADY_BOUND`；6 個 worker 只產生 1 筆紀錄；crash 後重試完成；一則留言只能核准一個 task | PASS |
| B1-07 Synthetic／Production 隔離 | 威脅 15；fixture replay 的核准 trust 為 `ISOLATED_TEST` | PASS |
| B1-08 G5 語意一致 | SYNTHETIC：test mode 為 `TEST_MODE_ONLY` + G5 `NOT_EVALUATED`；production 為 `BLOCKED` + `NOT_EVALUATED` | PASS |
| B1-09 無回測、無額度、無 Holdout | 真實執行 DISABLED；uses 0→0；repo 帳本指紋不變；全新 process 無 executor module；威脅 17 | PASS |
| B1-10 Regression／CI／Colab | 見 §6 | 本地 PASS；Colab PENDING |

18 項威脅逐案的拒絕代碼見 [`PHASE5B1_APPROVAL_THREAT_MODEL.md`](PHASE5B1_APPROVAL_THREAT_MODEL.md)；實測輸出在 `security_negative_tests.json`。

## 5. 真實 GitHub 證據

- **Live API 讀取已驗證（本 session，唯讀）**：
  - PR #11 留言 `6703348066` 的 V01–V05 皆 PASS：作者 `hh4832`／`269247281`／OWNER，`performed_via_github_app = null`。
  - 該留言沒有 token，因此在 V06 被拒（`V06_NO_APPROVAL_TOKEN`），符合預期。
  - 不存在的留言 → `V03_EVIDENCE_NOT_FOUND`；沒有 token → `V00`。
- **AI agent 發文判別**：同一個 GitHub 帳號經 Claude App（id 1236702）或 Codex Connector（id 1144995）發出的 issue／PR，API 會顯示 `performed_via_github_app`；研究負責人本人的留言則為 `null`。
- **真實環境負面測試（威脅 16，live API）**：
  - Claude 經 GitHub App 在 PR #12 貼了一則格式完全正確的 PROBE 核准行：[`issuecomment-6704318568`](https://github.com/hh4832/strategy-research-agent/pull/12#issuecomment-6704318568)。
  - GitHub 把作者顯示為 `hh4832`／`269247281`／OWNER，和本人留言完全一樣，但 `performed_via_github_app = claude`。
  - Verifier 結果：V01–V03 PASS，**V04 FAIL `V04_POSTED_VIA_APP`** → REJECTED。這是 AI agent 不能自行核准的真實證據。
- **研究負責人本人留言（live API，唯讀，本 session 驗證）**：
  - 留言：[`issuecomment-6704937578`](https://github.com/hh4832/strategy-research-agent/pull/12#issuecomment-6704937578)
  - V01–V09 全 PASS：作者 `hh4832`／`269247281`／OWNER，`performed_via_github_app = null`，未編輯、未撤銷，到期時間 `2026-11-09T08:52:11Z`。
  - 對真實帳本做 dry-run 綁定，**被拒**：`B02_PROVENANCE`、`B03_PROPOSAL_INVALID`、`B05_GOVERNANCE`（G7 FAIL）、`B06_BUDGET_EXHAUSTED`。repo 未被寫入。
  - 證據：`docs/evidence/phase5b1/session_live_verification_6704937578.json`
- **Colab 雲端驗收 `approvals/20261107_171621_a3251d3`**：
  - 全新 runtime，Version Sync PASS，Colab 回歸測試 509 passed，B1-01..B1-10 全 PASS，cloud=true，SCOPED_GO。
  - 該次執行**沒有讀到 `GH_TOKEN`**，所以真實核對寫成 `PENDING_USER_EVIDENCE`。
  - 副本：`docs/evidence/phase5b1/20261107_171621_a3251d3/`
- **Colab 雲端驗收（正式）`approvals/20261107_173254_a3251d3`：GO**
  - 全新 runtime，Version Sync PASS，`GH_TOKEN` 取自 Colab Secret（未印出、未寫檔，掃描輸出中沒有 token）。
  - Colab 回歸測試 508 passed／55 skipped。多出的 1 個 skip 是 `Drive is mounted here`，依設計跳過。
  - 真實核對 `VERIFIED`：留言 `6704937578` 的 V01–V09 全 PASS，來源 `GITHUB_REST_API`／`LIVE`／`PRODUCTION`。
  - 對真實帳本做 dry-run 綁定，**正確被拒**（B02／B03／B05／B06）；repo 未寫入。
  - B1-01..B1-10 全 PASS，cloud=true。7 個檔的 hash 與 run_info 一致。
  - 副本：`docs/evidence/phase5b1/20261107_173254_a3251d3/`
- **正向真實核對（舊說明）**：要你本人在 5-B1 PR 貼上 notebook 第 5 格產生的 PROBE 核准行，再於 Colab 執行。目前 **PENDING**。

## 6. 測試

| 環境 | 結果 |
|---|---|
| 本地 3.13，無 Core | 509 passed／54 skipped |
| 本地 3.13，Core 699e3ad | 603 passed／3 skipped |
| 本地 3.13，Core b16179a | 613 passed／4 skipped／1 failed（已知 `test_15` SHA pin，未修改） |
| 新增 | `tests/test_phase5b1_approval_binding.py`（34 項，含 18 威脅參數化與全新 process 驗收） |
| 遷移 | `tests/test_orchestrator_lifecycle.py`：正式模式核准改用隔離的 TEST_FIXTURE 證據（`approval.testkit`）。屬 fixture 遷移，不是安全退化；正式模式沒有加入 bypass。 |
| CI | 新 job `phase5b1-approval`（3.12）；3.11／3.12 全套件 |

## 7. 治理不變

- 未修改：Core、Research Authorization（真實 DEV 授權仍 1/1）、Holdout lock、market_readiness（IBKR_TSM NO_GO）、approvers、既有 registry、Phase 5-A Archive Replay 驗收紀錄。
- 新增的治理檔只有 `governance/approval_identities.json`（身分釘選），需研究負責人在 PR 審核。
- 未重構 Backtest Engine、Universal Evaluator、Phase 4-C Adapter、A2 Proposal Agent。

## 8. 尚未解決的風險

1. **PAT 代發無法區分。** agent 若取得研究負責人的個人 token，它發的留言與本人無異。只能靠營運規則：PAT 不交給 agent，agent 一律用 GitHub App。
2. **帳號安全即核准安全。** 這不是密碼學簽章；signed commit／OIDC 留待後續。
3. **v1 不支援 PR review 作為證據**；撤銷只掃描同一個 thread。
4. **正式執行路徑仍 DISABLED。** 綁定後的 request 只到「有資格」（G10 以外全部 PASS）；額度消耗流程（第三個事件）尚未實作。
5. **ARCHIVE_REPLAY 不要求綁定**（G11 `NOT_APPLICABLE`），但需要經驗證的 task 核准。

## 9. 判定

- 本地：SCOPED_GO → **Colab 雲端驗收後：GO**（`approvals/20261107_173254_a3251d3`，pin `a3251d3`）。
  - B1-01..B1-10 全 PASS；研究負責人本人的留言已經由 live GitHub API 驗證；有雲端封存。
- GO 的範圍只限 5-B1 的核准與綁定工程。它不代表真實執行或額度消耗已啟用，也不代表 Formal Market GO，更不涵蓋 §8 的殘餘風險（PAT 代發無法區分）。
- 合併前仍須完成 Codex 的獨立安全審核。
- 依 brief：「若真實 GitHub 核准者身分無法完成獨立驗證，應標示 SCOPED_GO／PENDING，不得宣告正式 Approval Security GO。」

## 10. Security Remediation（`cf15654` → `eb72cd0`）

| | |
|---|---|
| 修復前 commit | `cf1565495a687a6214d427e79b31af79bc23a23b`（Codex reviewed HEAD，NO_GO） |
| 修復 commit（pin） | `eb72cd02770793a168b704af94824a7300e2010d` |
| 重現案例 | `docs/evidence/phase5b1_remediation/reproduce.py`：修復前 11／11 EXPLOITABLE，修復後 11／11 BLOCKED（結果 JSON 在同一目錄） |
| 新增測試 | `tests/test_phase5b1_security_remediation.py`（29 項）；沒有放寬任何既有預期，也沒有跳過測試 |
| 本地測試 | 無 Core 538 passed／54 skipped；Core 699e3ad 632 passed；Core b16179a 642 passed + 已知 `test_15` |
| 新 gate | B1-SEC：Colab 驗收時重跑重現案例，必須全部 BLOCKED |
| Codex 原始檔案 | `SECURITY_REVIEW.md`／`reproduce.py`／`reproduction_results.json` **未取得**；第 9 項 Finding = **PENDING** |
| Colab 第 1 次（2026-11-08 08:17，`20261108_081706_eb72cd0`） | 乾淨的 runtime，Version Sync PASS，Colab 回歸測試 538 passed。B1-01..B1-10 + **B1-SEC 全 PASS**（11 個重現案例全部 BLOCKED）。真實 GitHub 證據 **VERIFIED**（V01–V09 PASS，PRODUCTION／LIVE）；對真實帳本的 dry-run 綁定被正確拒絕。ARCHIVE_INDEX 涵蓋 7 個檔案，hash 全部一致，輸出中沒有 token。**但 Drive 沒有掛載（`cloud=false`）**，輸出寫到 Colab 本機，所以判定是 SCOPED_GO。副本：`docs/evidence/phase5b1_remediation/20261108_081706_eb72cd0_LOCAL_CLOUD_PENDING/` |

**判定：在 Codex 複驗前，Security Verdict = SCOPED_GO（工程修復完成，等待獨立複驗）。** 任何 HIGH Finding 若無法複驗，都不得宣告 Security GO。
