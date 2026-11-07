# Phase 5-B1：Trusted Human Approval & Governed Authorization Binding — 驗收

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
- **正向真實核對**：要你本人在 5-B1 PR 貼上 notebook 第 5 格產生的 PROBE 核准行，再於 Colab 執行。目前 **PENDING**。

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

- 本地：**SCOPED_GO**。B1-01..B1-10 以隔離證據全 PASS；尚缺你本人留言經 live GitHub API 驗證，以及 Drive 雲端封存。
- 依 brief：「若真實 GitHub 核准者身分無法完成獨立驗證，應標示 SCOPED_GO／PENDING，不得宣告正式 Approval Security GO。」
