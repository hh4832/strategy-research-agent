# Phase 5-A3：Research Proposal × Orchestrator 整合驗收

> 範圍：把 A2（Proposal Agent，PR #10，Codex）接到 A1（Research Orchestrator，PR #9）。
> 不含新策略、新資料、真實回測、正式 LLM、參數搜尋、Dashboard、下單。Phase 5-B 未開始。

## 1. 分支與 DAG

```
95b6ec5 main (MVP v1)
 ├─ 5d088c4 shared contract (Request schema v1.0.0) ─ cc22d68 A1 orchestrator      (PR #9,  未修改)
 └─ 48771ff ─ 4f712ac ─ 3e813d8 A2 proposal agent                                    (PR #10, 未修改)
feature/phase5a3-integration
 2cc03bc  merge --no-ff A1 (cc22d68)
 ba68306  merge --no-ff A2 (3e813d8)          schema 為 add/add 相同 blob，無衝突
 3e7aa3f  integration layer + A3 acceptance + CLI + CI
 dae1257  CI: full-history checkout for the schema diff   ← code pin（notebook 釘選）
 (next)   notebook + 本文件 + 範例（只動 docs/ notebooks/）
```

保留兩條分支的原始 commit；沒有 rebase、reset 或 force push；PR #9、#10 未合併。

## 2. 共用 Schema 檢查（未修改）

| 項目 | 值 | 結果 |
|---|---|---|
| 路徑 | `schemas/RESEARCH_REQUEST_SCHEMA.json` | A1 `request.SCHEMA_PATH` 與 A2 `exporter.SCHEMA_PATH` 指向同一檔 |
| git blob | `4836b8f5738d26a95d1fe6c6a0d55c683a810a93` | 5d088c4／A1／A2／整合分支一致 |
| sha256 | `1b0530f321c6c71e9eac1bbe5f9c33e3f92e4af4c4ce11b80777c9a94c5324eb` | = A2 `REQUEST_SCHEMA_SHA256` |
| A2 pin | `REQUEST_CONTRACT_PIN = 5d088c4…` | 一致 |
| version | `schema_version const 1.0.0` | PASS |
| PENDING | `authorization_ref`、`core_commit` 皆 `oneOf[…, {const: PENDING}]` | PASS（PENDING 一律阻擋執行） |
| closed | 頂層 `additionalProperties: false`；`holdout_policy.access const NO_ACCESS` | PASS |

由 `strategy_agent.phase5a.contract_check()` 執行；測試另以 `git diff --quiet 5d088c4 -- schemas/RESEARCH_REQUEST_SCHEMA.json` 確認位元組未變。
**未發現契約缺陷，未開 Blocking Issue。**

## 3. Proposal → Request 對應

| Request 欄位 | 來源 | 規則 |
|---|---|---|
| `schema_version`, `request_id` | A2 產生 | `RR-` + proposal_id；bindings 不得覆寫 |
| `hypothesis`, `market_mechanism` | Proposal `draft.hypothesis` / `market_mechanism.causal_logic` | synthetic 前綴 `SYNTHETIC_FIXTURE — `；bindings 不得覆寫 |
| `requested_change.description` | Proposal `research_variables.changes` | 由 A2 推導；bindings 只能給 `kind` |
| `authorization_ref` | **A2 強制 `PENDING`** | bindings 給任何 RA → `AUTHORIZATION_MUST_REMAIN_PENDING` |
| `core_commit` | bindings，預設 `PENDING` | 短 SHA 拒絕 |
| strategy / spec / dataset / cost / window / mode / budget / holdout / outputs | 呼叫者提供的既有 artefact bindings | A2 驗證 spec 檔 sha、spec 合法、= proposal fragment、參數 = spec default、單一 symbol、dataset 在 spec 內、cost 檔 sha、window 在 proposal coverage 內；synthetic proposal 只能 `SYNTHETIC_TEST` |

`phase5a.proposal_to_request` 只包裝 A2：
- 驗證不過 → `REJECTED`
- `status=PENDING_INFORMATION` 或 bindings 缺 → `PENDING_INFORMATION`（不寫檔、`executable_request=false`）
- 同一路徑重匯出 → 位元組相同才視為冪等，否則拒絕且不覆寫

實際範例（A2 實際輸出 → A1 實際接收，SYNTHETIC_FIXTURE）：
[`docs/examples/phase5a3/SYNTHETIC_FIXTURE_proposal_to_request_example.json`](examples/phase5a3/SYNTHETIC_FIXTURE_proposal_to_request_example.json)。

## 4. E2E 路徑

```
A2 draft_proposal(FakeLLMAdapter/FileDraftAdapter)          Proposal (PROPOSED | PENDING_INFORMATION)
  → A2 export_request(bindings, artifact_root)              request.json + request_provenance.json (O_EXCL)
  → phase5a.submit_proposal: Q.load(檔案位元組) → A1 Orchestrator.submit
        → G1..G10 / G0 → PENDING_APPROVAL | BLOCKED
        → LinkLog(state_dir/proposal_links.jsonl, hash-chained): proposal_sha256 ↔ request_hash ↔ task_id
  → A1 approve（TEST_FIXTURE；正式：allow-list + https ref + 治理重驗）
  → A1 dispatch → Synthetic / ArchiveReplay adapter → Phase 4-C accept（Phase 4-B evaluator）
  → research_decision（acceptance≠PASS 時絕不 KEEP） → 7 檔 task archive（O_EXCL）
```

- **Route A（SYNTHETIC_FIXTURE）**：`phase5a.acceptance.route_a`。
- **Route B（Existing Archive Replay）**：`route_b`。Replay request 由人撰寫（A1 範例 `tsm_sma520_dev_archive_replay.json`），因為 A2 從不選擇授權，所以不可能、也不應該匯出 replay request。
  - 本地：使用 fixture archive。
  - Colab：重播 `20261107_081300_9439f8c`；只在 Drive 上有該 archive、且 MANIFEST sha = `328f2cf9…`、且研究負責人本人填入 approver 與 https 核准連結時才執行，否則為 `PENDING_USER_APPROVAL` / `PENDING_ARCHIVE`。

## 5. A3-01..A3-10（本地 fresh process，Python 3.13；CI 3.12）

| Gate | 判定依據 | 本地 |
|---|---|---|
| A3-01 A2 Proposal → A1 Request Schema | contract_check PASS；A2 輸出經 A1 `Q.validate` 無錯 | PASS |
| A3-02 完整 synthetic Proposal → Request | 見下方四項分開回報 | PASS |
| A3-03 不完整 proposal | `momentum` fixture → `PENDING_INFORMATION`（RULE_AST_PENDING、SIGNAL_TIME、PIT_UNKNOWN…）；無檔案、無 task、0 event；完整 proposal 無 bindings 亦同 | PASS |
| A3-04 `authorization_ref=PENDING` | A2（`FileDraftAdapter`，USER_HYPOTHESIS）匯出 → production orchestrator `PROPOSED→VALIDATED→BLOCKED`，G5 FAIL；approve → `GOVERNANCE_BLOCKED`；不曾 APPROVED/QUEUED/RUNNING；測試 orchestrator 亦 BLOCKED（G0）；偽造 RA binding → A2 拒絕、無檔 | PASS |
| A3-05 未授權正式研究 | `RESEARCH_ONLY_REAL`：G2/G4/G5/G9/G10 FAIL → BLOCKED；dispatch 仍 BLOCKED；synthetic request 送 production → G0 FAIL BLOCKED | PASS |
| A3-06 synthetic 生命週期 | 9 狀態完整；approver 僅 TEST_FIXTURE（真實 lead 在 test mode 被拒）；未核准 dispatch 不動；archive 在 `test_tasks/`；decision 標記 SYNTHETIC_TEST | PASS |
| A3-07 Existing Archive Replay + Evaluator | 本地 fixture：ARCHIVED、C1–C8 PASS、uses 1→1、consumed 0、governance 指紋不變；**真實 archive（Colab）：ARCHIVED、G1–G10 PASS、C1–C8 PASS、uses 1→1、0 batch** | PASS |
| A3-08 重複／重啟／並發 | 重複提交 → 同 task、0 新 event、1 link；同路徑重匯出冪等；重啟 recover = []、redispatch 不動、ARCHIVED 不可 retry、archive 目錄數不變；4 執行緒並發 → 1 task / 1 PROPOSED / 1 link；RUNNING 中 crash → lease 到期 `FAILED_NOT_REEXECUTED`、無 execution 目錄 | PASS |
| A3-09 Registry／Archive／Governance 完整性 | 6 個 state 的 task/link 鏈驗證 OK；link ↔ task request_hash 一致；archive 7 檔 hash 吻合、O_EXCL 覆寫被拒；repo governance/registry 指紋不變；缺檔 archive → FAILED（未 ARCHIVED）；Evaluator 失敗 → acceptance≠PASS、verdict≠KEEP；錯 SHA → REJECTED 無檔 | PASS |
| A3-10 Runner disabled／Holdout 隔離 | `REAL_EXECUTION_ENABLED=False`；`ResearchOnlyRunnerAdapter.enabled=False`；fresh process 無 executor module；A2 套件無 orchestration/runner/adapters/Core import；無券商 import；所有 request `holdout_policy.access=NO_ACCESS` | PASS |

**A3-02 四項分開回報：**

| 項目 | 結果 | 證據 |
|---|---|---|
| Contract compatibility | PASS | §2 |
| Actual Proposal-to-Request export | PASS | A2 `draft_proposal`（FakeLLMAdapter）+ `export_request` 寫出檔案；provenance `SCHEMA_VALID_PENDING_HUMAN_APPROVAL` |
| Orchestrator acceptance | PASS | A1 從磁碟讀同一檔 → `PENDING_APPROVAL`，governance `TEST_MODE_ONLY`，G1 PASS |
| Full E2E execution | PASS | `PROPOSED → … → ARCHIVED`；Phase 4-C C1–C8 PASS；decision `RETEST / UNDETERMINED`（synthetic） |

## 6. 測試與 CI

| 環境 | 結果 |
|---|---|
| 本地 3.13，無 Core（/tmp/v5） | 474 passed / 54 skipped |
| 本地 3.13，Core 699e3ad（/tmp/v4） | 568 passed / 3 skipped |
| 本地 3.13，Core b16179a（/tmp/v8） | 578 passed / 4 skipped / 1 failed — 已知 `test_15` Core SHA pin（未改寫） |
| 新增 | `tests/test_phase5a3_integration.py`（17）；CI 新 job `phase5a3-integration`（3.12；fresh-process acceptance；`git diff --exit-code -- registry governance schemas`） |
| CI 3.11 / 3.12 / Core-pinned / Synthetic integration | 見 PR checks |

## 7. 治理不變

- 未修改：Core、Research Authorization（1/1 不再消耗）、Holdout lock、`market_readiness.json`（IBKR_TSM 仍 NO_GO）、approvers、registry。
- 未修改：Backtest Engine、Universal Evaluator、M3-B Runner、Research Governance 模組、Phase 4-C Archive Adapter。
- 唯一的 A1 程式修正：`SyntheticExecutionAdapter` 將 `ArchiveRejected` 轉成 `ExecutionFailed`。原本壞掉的 fixture 會讓 task 卡在 RUNNING，要等 lease 到期才 FAILED；修正後立即 FAILED。屬 fail-closed 行為強化。
- CLI 沒有 approve／force／任意 dispatch：
  - 核准仍走 `strategy_agent.orchestration approve`。
  - `replay` 僅 `ARCHIVE_REPLAY`。
  - `submit-proposal` 只用 production orchestrator，A2 request 必定 G5 BLOCKED。

## 8. 限制

1. **Synthetic E2E 證明的是管線，不是「這個 request 驅動了這次執行」。** `SyntheticExecutionAdapter` 評估的是 Phase 4-C fixture archive（TSM SMA 5/20），而 A2 request 的內容是 TW 2330 synthetic spec；adapter 不比對 request ↔ archive 身分（ArchiveReplayAdapter 會比對）。
2. **身分：** approver／submitter 是呼叫者宣稱的字串，只比對 `approvers.json`，不是經驗證的身分。這是 Phase 5-B 前置工作。
3. **A2 request 永遠無法進入正式執行。** 這是設計（A2 不簽發也不選擇授權），但目前沒有「人類綁定授權」的受治理流程：要用 A1 治理新 request，只能由人撰寫 request。這也是 Phase 5-B 前置工作。
4. **G5 呈現：** A1 對 SYNTHETIC_TEST 回報 `G5_AUTHORIZATION: PASS`（detail `NOT_EVALUATED`）。呈現上易誤讀；本階段未改 A1 治理。
5. **真實 Route B** 需要 Colab + Drive + 研究負責人本人輸入核准。本地沒有真實 archive。
6. **Python 版本：** 本地只有 3.13；3.11／3.12 由 CI 證明。

## 9. Devil's Advocate

- 「A3-02 PASS 但 synthetic 跑的不是 request 的策略」——對，見限制 1。所以 A3-02 只宣稱 *實際 A2 輸出被實際 A1 接受並走完生命週期*，不宣稱 request 內容決定了結果。
- 「Route B 的 request 不是 A2 產的，算整合嗎？」——不算 A2 產物，報告中明確標註來源。A2 能產生 replay request 才是治理漏洞。
- 「並發測試在單一 process 的執行緒上，跨 process 呢？」——底層是 `ChainedLog.append_if`（fcntl 鎖），A1 已有跨 worker claim 測試。這裡沒有另做多 process 壓測。
- 「SCOPED_GO 會不會被讀成可以正式研究？」——不會：IBKR_TSM 仍 NO_GO、真實 runner DISABLED、A2 request 必 BLOCKED。

## 10. Colab 雲端驗收（2026-11-07 14:30，pin `dae1257`）

- Drive：`MyDrive/Quant_Research/strategy-research-agent/integrations/20261107_143007_dae1257/`（7 檔 + notebook_context.json；hash 與 run_info 一致）；副本：[`docs/evidence/phase5a3/20261107_143007_dae1257/`](evidence/phase5a3/20261107_143007_dae1257/)。
- Version Sync PASS（clean runtime；pin 之後只有 docs/notebooks 變更）；Python 3.13.18；fresh process；executor modules 未載入。
- Route B 真實重播 `20261107_081300_9439f8c`：
  - 狀態 `PROPOSED → … → ARCHIVED`，G1–G10 PASS，C1–C8 PASS。
  - 13 檔驗證，MANIFEST 等於 `328f2cf9…`。
  - consumed_batches 0，授權用量 1→1，governance 不變。
  - Decision `RETEST / UNDETERMINED`（不是新的研究結論）。
- 核准：`hh4832`，PR #11 留言 `issuecomment-6703348066`（由研究負責人本人發出）。核准事件寫在 Drive task archive 的 `task_events.jsonl`，不在 7 檔 zip 內。
- **Verdict：GO**（A3-01..A3-10 PASS；實際 A2→A1 synthetic E2E；真實 archive replay PASS；雲端封存）。

## 11. 判定與建議

- 本地：SCOPED_GO → Colab 雲端驗收後：**GO**（§10）。
- 跑 `notebooks/Phase5A3_Integration_Acceptance.ipynb`（pin `dae1257`）並由研究負責人填入核准後，若 Route B 真實重播 ARCHIVED、C1–C8 PASS、uses 1→1，且 7 檔寫入 Drive `integrations/`，即可升為 GO。
- Phase 5-A 建議：合併前先決定 PR #9／#10／本 PR 的合併順序（建議以本整合分支一次合併，#9、#10 關閉並指向本 PR）。
- Phase 5-B 前置工作：可信身分驗證、受治理的「人類綁定授權到 request」流程、synthetic adapter 的 request↔archive 身分比對。
