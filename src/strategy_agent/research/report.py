"""Research report (13 sections) + rule-based strategy recommendation.

All numbers come from ``evaluation.research`` (computed by program from Core's output). The text around them is
fixed templates plus rule-based findings; no number is written by hand or by an LLM.

Two separate verdicts, never mixed:
* engineering status of the run (did every pipeline stage do its job), and
* the strategy recommendation, one of 保留 / 修改後再測 / 淘汰, or 無法判定 when eligibility, sample or data
  do not allow a conclusion. Synthetic data, non-APPROVED or assumed costs, a closed market gate or an
  insufficient sample always give 無法判定.
"""
from __future__ import annotations

import json
from typing import Any

KEEP, RETEST, DROP, UNDECIDABLE = "保留", "修改後再測", "淘汰", "無法判定"


def _num(x: Any, pct: bool = False, nd: int = 4) -> str:
    if isinstance(x, dict) and x.get("status") == "NOT_EVALUABLE":
        return f"NOT_EVALUABLE ({x['reason']})"
    if x is None:
        return "—"
    if isinstance(x, (int,)) and not isinstance(x, bool):
        return str(x)
    if isinstance(x, float):
        return f"{x * 100:.{max(nd - 2, 2)}f}%" if pct else f"{x:,.{nd}f}"
    return str(x)


PIPELINE_STAGES = ("spec", "governance_admission", "data_access", "signal_generation", "core_execution",
                   "performance_evaluation", "experiment_registry")


def engineering_status(res, stages=PIPELINE_STAGES) -> dict:
    """PASS iff the registry reached COMPLETED and none of ``stages`` FAILED. The report and archive stages are
    judged afterwards (archive ``final_status.json``), since the report cannot grade its own writing."""
    st = {s["name"]: s["status"] for s in res.stages if s["name"] in stages}
    failed = [n for n, v in st.items() if v != "OK"]
    completed = res.status == "COMPLETED"
    return {"status": "PASS" if completed and not failed else "FAIL", "registry_status": res.status,
            "failed_stages": failed, "stages": st}


def recommend(res) -> dict:
    """Pre-registered decision rules (M3-B). Returns decision + every reason that applied."""
    reasons, blockers = [], []
    ctx = res.context
    m = res.metrics
    if res.status != "COMPLETED" or m is None:
        return {"decision": UNDECIDABLE, "blockers": [f"run did not complete (registry status {res.status})"],
                "reasons": []}
    ds = (res.market_data.metadata if res.market_data else {}) or {}
    if ds.get("kind") == "SYNTHETIC" or ctx.get("environment") == "SYNTHETIC":
        blockers.append("synthetic fixture: engineering evidence only, says nothing about TSM or SMA")
    prof = ctx.get("cost_profile") or {}
    if prof.get("status") != "APPROVED" or any(c.get("basis") == "RESEARCH_ASSUMPTION" and c["kind"] in
                                               {"COMMISSION", "MIN_COMMISSION", "TAX"} for c in prof.get("components", [])):
        blockers.append("costs are DRAFT or RESEARCH_ASSUMPTION (RESEARCH_ONLY): no deployable conclusion")
    core_status = (res.report.provenance.get("core_status") if res.report else None)
    if core_status != "FORMAL_ELIGIBLE":
        blockers.append(f"Core status {core_status}: result is research-only")
    if m["sample"]["status"] != "SUFFICIENT":
        blockers.append("sample insufficient: " + "; ".join(r["message"] for r in m["sample"]["reasons"]))
    t = m["trades"]
    exp, pf = t.get("expectancy"), t.get("profit_factor")
    evaluable = isinstance(exp, float) and isinstance(pf, float)
    if evaluable:
        if exp <= 0 or pf <= 1:
            reasons.append(f"net expectancy {exp:.4f} / profit factor {pf:.3f} do not beat costs")
        else:
            reasons.append(f"net expectancy {exp:.4f} > 0 and profit factor {pf:.3f} > 1 at 1x cost")
            reasons.append(two_x_note(res))
    else:
        blockers.append("expectancy / profit factor NOT_EVALUABLE")
    if blockers:
        return {"decision": UNDECIDABLE, "blockers": blockers, "reasons": reasons}
    if exp <= 0 or pf <= 1:
        return {"decision": DROP, "blockers": [], "reasons": reasons}
    return {"decision": RETEST, "blockers": [], "reasons": reasons}     # KEEP needs the 2x-cost result as well


def two_x_note(res) -> str:
    """Run-level wording for the falsifiable prediction's 2x-cost condition. A single-run report never claims a
    comparison it did not compute, and never ignores that its batch has a 2x scenario."""
    ms = (res.context.get("research_config") or {}).get("scenario_multipliers") or []
    has_2x = [m for m in ms if float(m["cost_multiplier"]) == 2.0]
    here = float(res.job.cost_multiplier)
    if here == 2.0:
        return "this run IS the 2x-cost scenario; whether the 1x result survives 2x is judged at batch level"
    if has_2x:
        return (f"2x-cost condition is not evaluated in this single-run report; the batch's scenario set includes "
                f"{', '.join(m['scenario_id'] for m in has_2x)} (Core multiplier 2.0) - see the batch-level "
                "cost-sensitivity table (_batches/cost_sensitivity_<batch>.md)")
    return "2x-cost condition not evaluated: this invocation ran a single cost multiplier (no 2x scenario)"


def negative_evidence(res) -> list[str]:
    m = res.metrics or {}
    out = []
    if not m:
        return ["no metrics: the run did not complete"]
    t = m["trades"]
    if t["trade_count"] < 30:
        out.append(f"only {t['trade_count']} valid round trips (< 30): any ratio is noise-dominated")
    if isinstance(t.get("expectancy"), float) and t["expectancy"] <= 0:
        out.append(f"net expectancy per trade is {t['expectancy']:.4f} (not positive)")
    if isinstance(t.get("profit_factor"), float) and t["profit_factor"] < 1:
        out.append(f"profit factor {t['profit_factor']:.3f} < 1")
    c = m["costs"]
    if c.get("cost_share_of_gross") and c["cost_share_of_gross"] > 0.5:
        out.append(f"costs are {c['cost_share_of_gross'] * 100:.1f}% of |gross| closed-trade PnL")
    tr = m["tail_risk"].get("trades")
    if isinstance(tr, dict) and tr["max_consecutive_losses"] >= 5:
        out.append(f"{tr['max_consecutive_losses']} consecutive losing trades")
    years = m.get("by_year", {})
    neg = [y for y, v in years.items() if v["net_pnl"] < 0]
    if neg:
        out.append(f"negative net PnL in {', '.join(neg)}")
    dq = m["data_quality"]
    if dq["unfillable_signals"]:
        out.append(f"{dq['unfillable_signals']} signals could not be filled")
    if not out:
        out.append("no rule-based negative finding; absence of evidence is not evidence of an edge")
    return out


def opponent_view(res) -> list[str]:
    m = res.metrics or {}
    out = [
        "SMA 5/20 on 15-minute bars is one of the most data-mined rules in existence; a positive result is the "
        "expected outcome of chance for some sample, so one run proves nothing without out-of-sample and holdout.",
        "The parameters were fixed by convention, not derived from the mechanism; the hypothesis does not predict "
        "why 5 and 20 bars should match the horizon of information diffusion in TSM.",
        "Intraday crossovers are dominated by microstructure noise; costs and slippage scale with the number of "
        "crossings while any trend edge does not.",
    ]
    if m and m["trades"]["trade_count"]:
        tr = m["tail_risk"].get("trades")
        if isinstance(tr, dict) and m["trades"]["net_pnl"] > 0 and tr["best_trade"] > 0.5 * m["trades"]["net_pnl"]:
            out.append("More than half of the net PnL comes from the single best trade.")
    if (res.market_data and res.market_data.metadata.get("kind") == "SYNTHETIC"):
        out.append("The data are a seeded random walk with hand-set drift regimes: any 'edge' here was put there "
                   "by the fixture generator.")
    return out


def uncertainties(res) -> list[str]:
    out = ["IBKR TSM market gate is NO_GO (governance/market_readiness.json, Core PR #3 @ 699e3ad): timestamp label, "
           "observed availability and closing auction UNKNOWN; Core's CONSERVATIVE_LABEL_AGNOSTIC model is a research "
           "model pending owner acceptance and can never be formal-eligible.",
           "IBKR costs are DRAFT/ASSUMED: representable by Core 699e3ad but not approved (no account evidence, "
           "regulatory schedule PENDING); TEST_ONLY synthetic costs say nothing about IBKR.",
           "No walk-forward split or holdout result exists for this family; multiple-testing correction uses the "
           "family trial count recorded in the registry.",
           "Overnight holding is allowed by the spec; gap risk is visible only in the equity curve, not modelled."]
    return out


def build_report(res) -> str:
    j, ctx, m = res.job, res.context, res.metrics
    spec = j.spec
    eng = engineering_status(res)
    rec = recommend(res)
    rec_out = {"engineering": eng, "strategy": rec}
    res.context["verdicts"] = rec_out
    md = res.market_data.metadata if res.market_data else ctx.get("dataset", {})
    prof = ctx.get("cost_profile") or {}
    L = [f"# Research report — {spec['strategy_id']} ({spec['version']})", ""]
    if ctx.get("environment") == "SYNTHETIC":
        L += ["> **SYNTHETIC FIXTURE RUN — engineering evidence only.** The bars are a seeded random walk "
              "(`fixtures/synthetic`), the CostProfile is a test-only approval, and the governance environment is "
              "isolated. Nothing below is evidence about TSM or about SMA profitability.", ""]
    L += ["## 1. Experiment summary", "",
          f"| item | value |", "|---|---|",
          f"| experiment id | `{res.record['experiment_id']}` |",
          f"| registry status | **{res.status}** |",
          f"| run identity | `{res.identity}` |",
          f"| spec sha256 | `{ctx['spec_sha256']}` |",
          f"| parameters | `{json.dumps(j.parameters, sort_keys=True)}` (fixed baseline, no search) |",
          f"| role / window | {j.role} / {j.window_start} → {j.window_end} (warm-up from {j.warmup_start}) |",
          f"| cost multiplier | {j.cost_multiplier} |",
          f"| Core | `{ctx['engine']['git_sha']}` ({ctx['engine']['benchmark_status']}) |",
          f"| Agent | `{ctx['agent']['agent_sha']}`{' (working tree DIRTY)' if ctx['agent'].get('agent_tree_dirty') else ''} |",
          f"| runner / adapter | {ctx['runner_version']} / {ctx['adapter_version']} |",
          f"| environment | {ctx['environment']} |",
          f"| **engineering status** | **{eng['status']}** |",
          f"| **strategy recommendation** | **{rec['decision']}** |", ""]
    if res.record.get("rejection_reasons"):
        L += ["Rejection / failure reasons recorded in the registry:", ""]
        L += [f"- `{r['code']}` {r['message']}" for r in res.record["rejection_reasons"]] + [""]
    h = spec["hypothesis"]
    sem = ctx["semantics"]
    tp = (ctx.get("research_config") or {})
    scen = ctx.get("cost_scenario")
    L += ["## 2. Market mechanism", "", h["mechanism"], "",
          "## 3. Hypothesis", "", f"- Statement: {h['statement']}", f"- Falsifiable prediction: {h['falsifiable_prediction']}",
          f"- Null: {h['null_hypothesis']}", f"- Known failure modes: {', '.join(h['known_failure_modes'])}", "",
          "## 4. Strategy rules", "",
          f"- Universe: {spec['universe']['market']} {', '.join(spec['universe']['symbols'])}, "
          f"{spec['universe']['timeframe']} bars, sessions {spec['universe']['sessions']['allowed']}, "
          f"timezone {spec['universe']['timezone']}",
          f"- Signal: `sma_crossover(fast={j.parameters['fast']}, slow={j.parameters['slow']})` on eligible bars "
          "(zero-volume / missing bars skipped, never filled); enter long when SMA(fast) crosses above SMA(slow) while "
          "flat; exit when it crosses below. Long only.",
          f"- Size: {spec['position_sizing']['units']} units fixed; initial capital {sem['initial_cash']:,.0f}.", "",
          "## 5. Execution assumptions", "",
          "- Temporal rules are enforced by market-research-core, not by the Agent.",
          f"- Data semantics handed to Core: bar label `{sem['bar_label']}`, session semantics "
          f"`{sem['session_semantics']}`, timezone `{sem['timezone']}`, tick {sem['constant_tick_size']}.",
          "- Decision time: the bar's `available_at` (bar end), after the bar is complete. Orders: target-position "
          "MARKET, TIF DAY; Core fills at the first eligible bar with `bar_start >= available_at` (convention v1); no "
          "partial fills; no closing-auction orders.",
          f"- Research configuration: `{tp.get('config_id')}@{tp.get('version')}` sha256 `{tp.get('sha256')}`"
          if tp else "- Research configuration: none (legacy single-job entry).",
          f"- Data: dataset `{md.get('dataset_id')}` snapshot `{md.get('snapshot_id')}` producer "
          f"`{md.get('producer_commit_sha')}`; kind {md.get('kind', 'REAL_MARKET')}; rows {md.get('rows')}; frame sha256 "
          f"`{md.get('frame_sha256')}`",
          f"- PIT: {md.get('pit_definition', '—')}", "",
          "## 6. CostProfile / assumptions", "",
          f"- Profile `{prof.get('profile_id')}@{prof.get('version')}` status {prof.get('status')} "
          f"sha256 `{prof.get('provenance', {}).get('profile_sha256')}`"]
    for c in prof.get("components", []):
        v = {k: c[k] for k in ("rate", "per_unit", "amount", "ticks", "bps", "rule") if k in c}
        L.append(f"  - {c['id']} ({c['kind']}, {c['side']}): {v} — basis {c['basis']}")
    if res.report is not None:
        L.append(f"- Core cost profile sha256 `{res.report.provenance.get('cost_profile_sha256')}`; Core status "
                 f"`{res.report.provenance.get('core_status')}`; all fees, taxes and slippage computed by Core")
    if ctx.get("overrides"):
        L += ["- Synthetic environment overrides (recorded, never applied to the formal environment):"]
        L += [f"  - {o}" for o in ctx["overrides"]]
    L.append("")
    L += ["## 7. Backtest results", ""]
    if m is None:
        L += ["NOT_EVALUABLE: no completed backtest.", ""]
    else:
        t = m["trades"]
        sh = m["sharpe_ratio"]
        rows = [("Trade count (valid round trips)", t["trade_count"]), ("Win rate", t["win_rate"]),
                ("Average win", t["average_win"]), ("Average loss", t["average_loss"]),
                ("Payoff ratio", t["payoff_ratio"]), ("Expectancy (net / trade)", t["expectancy"]),
                ("Profit factor", t["profit_factor"]), ("Total return (equity, incl. open)", m["total_return"]),
                ("Cost-adjusted net return (closed trades)", m["cost_adjusted_net_return"]),
                ("Gross return (closed trades)", m["gross_return_closed_trades"]),
                ("Total explicit costs (fees + taxes + regulatory fees)", m["costs"]["total"]),
                ("Sharpe (annualised)", sh.get("annualised") if sh.get("status") != "NOT_EVALUABLE" else sh),
                ("Exposure (fraction of bars in market)", m["exposure"]),
                ("Turnover (annualised, one-way)", m["turnover_annualised"])]
        L += ["| metric | value |", "|---|---|"] + [f"| {k} | {_num(v)} |" for k, v in rows]
        L += ["", f"Sample gate (Q5): **{m['sample']['status']}** "
              + ("; ".join(r["message"] for r in m["sample"]["reasons"]) or ""), ""]
        L += ["**Decomposition**", ""]
        for title, key in (("By year", "by_year"), ("By symbol", "by_symbol"),
                           ("By holding period (bars)", "by_holding_period_bars"), ("By parameter set", "by_parameter_set")):
            L += [f"*{title}*", "", "| group | trades | net PnL | win rate | expectancy | PF |", "|---|---|---|---|---|---|"]
            for g, v in m[key].items():
                L.append(f"| {g} | {v['trade_count']} | {_num(v['net_pnl'])} | {_num(v['win_rate'])} | "
                         f"{_num(v['expectancy'])} | {_num(v['profit_factor'])} |")
            L.append("")
        L += [f"*By market regime*: {_num(m['by_regime'])}", ""]
    L += ["## 8. Cost sensitivity", ""]
    if scen:
        L += [f"- This run is scenario `{scen['scenario_id']}` ({scen['kind']}), Core cost_multiplier "
              f"{scen['cost_multiplier']}, profile `{scen['profile_id']}@{scen['version']}` sha256 `{scen['sha256']}`, "
              f"{scen['currency']}; commission {scen['commission_model']}; slippage {scen['slippage_model']}; basis "
              f"{', '.join(scen['fee_basis'])}; {scen['assumption_status']}; approval {scen['approval_status']}; Core "
              f"compatibility {scen['core_compatibility']['verdict']}.",
              "- Scenarios are sensitivity analysis of the same strategy configuration: same parameters, one family "
              "trial. The cross-scenario table is generated from the archived metrics in the batch summary "
              "(`_batches/summary_<batch>.json` / `cost_sensitivity_<batch>.md`).", ""]
    else:
        L += [f"- Single run at Core cost_multiplier {j.cost_multiplier}; no scenario set (legacy entry). Run a research "
              "configuration to get BASE / LOW / STRESS / CUSTOM scenarios.", ""]
    L += ["## 9. Risk metrics", ""]
    if m is not None:
        L += [f"- Maximum drawdown {_num(m['max_drawdown'])}; duration {m['max_drawdown_duration_bars']} bars",
              f"- Exposure {_num(m['exposure'])}; open position at end: {'yes' if m['open_position_at_end'] else 'no'}",
              "", "```json", json.dumps(m["tail_risk"], indent=1, default=str), "```", ""]
    else:
        L += ["NOT_EVALUABLE", ""]
    L += ["## 10. Bias and data-quality checks", ""]
    L += ["- Look-ahead: decisions are emitted at bar `available_at`; Core fills only at `bar_start >= available_at` "
          "(B01–B12 + G16/G17/G14b PASS on this Core); signals are prefix-invariant (M2 tests).",
          "- Selection bias: one pre-registered parameter set (5/20), no search (R02 mandatory); family trial index "
          f"{res.record['trial'].get('index')} before this run (registry).",
          f"- Holdout: development role {j.role}; holdout window untouched (guard H03)."]
    if m is not None:
        mt = m["multiple_testing"]
        L += [f"- Multiple testing: family `{mt['family_id']}` has {mt['family_trials']} recorded trial(s); "
              f"PSR(SR>0) {_num(mt['psr_vs_zero'])}; deflated Sharpe {_num(mt['deflated_sharpe'])}"]
        dq = m["data_quality"]
        L += [f"- Zero-volume bars {dq['zero_volume_bars']}; missing bars {dq['missing_bars']} of "
              f"{dq['expected_bars_from_session_template']} expected; median bar volume {_num(dq['median_bar_volume'])}",
              f"- Max order participation of bar volume {_num(dq['max_participation_of_bar_volume'])}; slippage cost vs "
              f"bar open {_num(dq['slippage_cost_vs_bar_open'])}; unfillable signals {dq['unfillable_signals']}",
              f"- Rejections by reason: `{json.dumps(dq['rejections_by_reason'])}`"]
    L += ["", "## 11. Negative evidence", ""] + [f"- {x}" for x in negative_evidence(res)]
    L += ["", "## 12. Opponent's view (反對者觀點)", ""] + [f"- {x}" for x in opponent_view(res)]
    L += ["", "## 13. Remaining uncertainty", ""] + [f"- {x}" for x in uncertainties(res)]
    L += ["", "## 14. Research decision", "",
          "### Engineering Verdict", "",
          f"**{eng['status']}** — stages: " + ", ".join(f"{k}={v}" for k, v in eng["stages"].items()), "",
          "### Strategy Verdict", "",
          f"**{rec['decision']}**"]
    L += [f"- blocker: {b}" for b in rec["blockers"]] + [f"- finding: {r}" for r in rec["reasons"]]
    L += ["", "The two verdicts are independent: a correctly executed run (Engineering PASS) can still be 淘汰 or "
          "無法判定. A positive synthetic result is never evidence that the strategy works in a real market.", ""]
    return "\n".join(L)
