"""All result inputs in this file are SYNTHETIC_FIXTURE, never real strategy evidence."""
import ast
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from strategy_agent.evaluation.universal import evaluate,write_evaluation,available,absent
from strategy_agent.evaluation.archive_input import phase4a_archive

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/'tests/fixtures/evaluator/positive_short_run.json'
AT='2026-11-06T21:00:00+08:00'
SHA='a'*40


def fixture():
    doc=json.loads(FIXTURE.read_text());assert doc['kind']=='SYNTHETIC_FIXTURE';return doc


def run(doc):return evaluate(doc,evaluator_commit_sha=SHA,evaluated_at=AT)


def pnls(values):
    doc=fixture();rows=doc['trades']['value'][:len(values)]
    for row,value in zip(rows,values):row['net_pnl']=value;row['gross_pnl']=value+2
    doc['trades']=available(rows)
    # Core-like equity is supplied independently; no invented reconciliation with closed-only PnL.
    doc['metrics']=available({})
    return doc


def test_positive_profit_and_engineering_pass_never_imply_keep():
    result=run(fixture())
    assert result['engineering_status']=='PASS'
    assert result['strategy_verdict']=='RETEST'
    assert result['metrics']['net_pnl']['value']==80
    assert set(result['layers'])=={'data_quality','execution_quality','statistical_evidence','cost_sensitivity','robustness','research_decision'}


def test_high_win_rate_large_loss_is_rejected():
    result=run(pnls([1]*29+[-100]))
    assert result['metrics']['win_rate']['value']==pytest.approx(29/30)
    assert result['strategy_verdict']=='REJECT'
    assert result['metrics']['net_pnl']['value']==-71


def test_positive_gross_negative_after_costs():
    result=run(pnls([-1]*30))
    assert result['strategy_verdict']=='REJECT'
    assert 'POSITIVE_GROSS_NEGATIVE_NET' in {f['code'] for f in result['risk_flags']}


def test_one_trade_contributes_all_profit():
    result=run(pnls([1000]+[-1]*29))
    assert 'SINGLE_TRADE_COVERS_ALL_NET_PROFIT' in {f['code'] for f in result['risk_flags']}
    assert result['strategy_verdict']=='RETEST'


def test_insufficient_sample_never_becomes_positive_conclusion():
    r=run(pnls([100]*4))
    assert r['evidence_sufficiency']=='UNDETERMINED' and r['strategy_verdict']=='RETEST'
    assert r['metrics']['profit_factor']['status']=='INSUFFICIENT_SAMPLE'


@pytest.mark.parametrize('section',['holdout','trades','fills','equity_curve','cost_breakdown','rejections','metrics','data_quality','execution_diagnostics','budget_consumption'])
@pytest.mark.parametrize('state',['MISSING','UNTRUSTED','NOT_APPLICABLE'])
def test_required_evidence_fails_closed(section,state):
    doc=fixture();doc[section]=absent('SYNTHETIC_FIXTURE_missing',state)
    result=run(doc)
    assert result['engineering_status']=='BLOCKED' and result['metrics']=={}
    assert result['strategy_verdict']=='RETEST' and result['evidence_sufficiency']=='UNDETERMINED'
    assert result['risk_flags']


def test_missing_required_source_field():
    d=fixture();del d['source']['manifest_sha256'];r=run(d)
    assert r['engineering_status']=='BLOCKED' and r['strategy_verdict']=='RETEST'


@pytest.mark.parametrize('bad',['not-a-time','2024-01-01T09:00:00'])
def test_invalid_timestamp_blocks(bad):
    d=fixture();d['trades']['value'][0]['entry_ts']=bad
    assert run(d)['engineering_status']=='BLOCKED'


def test_lookahead_and_chronology_fail():
    d=fixture();d['fills']['value'][0]['signal_available_at']=d['fills']['value'][0]['exec_ts']
    r=run(d);assert r['engineering_status']=='FAIL' and r['strategy_verdict']=='RETEST'
    d=fixture();d['equity_curve']['value'].reverse();assert run(d)['engineering_status']=='FAIL'


def scenario(doc,name,net,multiplier):
    src=doc['source']
    return {'scenario_id':name,'source_run_id':src['run_id']+'_'+name,
        **{k:copy.deepcopy(src[k]) for k in ('core_sha','dataset_snapshot','sample_period','strategy_id','parameter_family','timeframe','currency')},
        'cost_multiplier':multiplier,'net_pnl':net,'comparison_basis':'SAME_SIGNALS_AND_POSITIONS','evidence_ref':'SYNTHETIC_FIXTURE_scenario'}


def test_cost_scenarios_are_observed_not_linear_repricing():
    d=fixture();d['cost_scenarios']=available([scenario(d,'BASE',80,1),scenario(d,'STRESS',-50,2)])
    r=run(d);assert 'COST_SCENARIO_TURNS_NON_POSITIVE' in {f['code'] for f in r['risk_flags']}
    assert len([x for x in r['robustness_matrix'] if x['dimension']=='cost_scenario'])==2
    # Evaluator never debits embedded slippage a second time.
    assert r['metrics']['net_pnl']['value']==80
    d['cost_scenarios']['value'][1]['currency']='TWD';assert run(d)['engineering_status']=='FAIL'


def test_multi_symbol_concentration():
    d=pnls([100]*15+[-1]*15)
    for i,t in enumerate(d['trades']['value']):t['symbol']='TSM' if i<15 else 'SOXX'
    r=run(d);assert 'SINGLE_SYMBOL_COVERS_ALL_NET_PROFIT' in {f['code'] for f in r['risk_flags']}
    assert {x['group'] for x in r['robustness_matrix'] if x['dimension']=='symbol'}=={'TSM','SOXX'}


def test_short_long_period_and_irregular_frequency():
    d=fixture();short=run(d)
    assert short['metrics']['annualized_return']['status']=='INSUFFICIENT_SAMPLE'
    assert short['metrics']['volatility']['status']=='NOT_AVAILABLE'
    d['source']['sample_period']['end']='2026-02-05T00:00:00+00:00'
    # A long declared period without actual equity coverage or cashflow information is not enough.
    assert run(d)['metrics']['annualized_return']['status']=='INSUFFICIENT_SAMPLE'


def test_no_holdout_result_reading_and_unknown_status_blocks():
    d=fixture();d['holdout']['value']['status']='UNKNOWN';assert run(d)['engineering_status']=='BLOCKED'
    d['holdout']['value']['status']='ACCESSED';assert run(d)['engineering_status']=='BLOCKED'


def test_source_sha_mismatch_and_core_metric_mismatch_fail():
    d=fixture();d['statistical_evidence']=available({'protocol':'QUANT_EVALUATION_PROTOCOL v1.0',
        'source_run_id':d['source']['run_id'],'core_sha':'b'*40,'manifest_sha256':'3'*64,'evidence_ref':'SYNTHETIC_FIXTURE_mismatch'})
    assert run(d)['engineering_status']=='FAIL'
    d=fixture();d['metrics']['value']['trades']['net_pnl']=99
    r=run(d);assert r['engineering_status']=='FAIL' and r['strategy_verdict']=='RETEST' and r['metrics']=={}


def test_reproducible_json_markdown_csv_and_exclusive_outputs(tmp_path):
    one=run(fixture());two=run(fixture());assert one==two
    a=write_evaluation(one,tmp_path/'a');b=write_evaluation(two,tmp_path/'b')
    names={'evaluation.json','evaluation_report.md','risk_flags.json','robustness_matrix.csv','research_decision.json'}
    assert {p.name for p in a.iterdir()}==names
    for name in names:
        assert (a/name).read_bytes()==(b/name).read_bytes()
        text=(a/name).read_text()
        for k in one['metadata']:assert k in text
    with pytest.raises(FileExistsError):write_evaluation(one,a)
    with pytest.raises(ValueError):write_evaluation(one,ROOT/'registry/new_eval')


def test_empty_and_nonfinite_inputs_are_blocked():
    for d in ({},[],{'source':{}}):assert run(d)['engineering_status']=='BLOCKED'
    d=fixture();d['metrics']['value']['bad']=float('nan');assert run(d)['engineering_status']=='BLOCKED'


def test_strata_missing_and_small_group_statuses():
    r=run(fixture());rows=r['robustness_matrix']
    assert next(x for x in rows if x['dimension']=='market_regime')['status']=='NOT_AVAILABLE'
    assert next(x for x in rows if x['dimension']=='parameter_region')['status']=='NOT_AVAILABLE'
    assert next(x for x in rows if x.get('group')=='parameter_stability')['status']=='NOT_TESTED'


def test_rejection_rate_requires_order_denominator_and_deduplication():
    d=fixture();d['rejections']=available([{'order_id':'SYNTHETIC_FIXTURE_rej','code':'ZERO_VOLUME'}])
    assert run(d)['metrics']['rejection_rate']['value']==pytest.approx(1/64)
    del d['execution_diagnostics']['value']['orders_submitted']
    assert run(d)['metrics']['rejection_rate']['status']=='NOT_AVAILABLE'


def test_cli_writes_without_core_or_executor(tmp_path,monkeypatch):
    from strategy_agent.evaluation import __main__ as cli
    main=cli.main
    monkeypatch.setattr(cli,'evaluator_identity',lambda:(SHA,False))  # SYNTHETIC_FIXTURE identity only
    assert main(['--input',str(FIXTURE),'--output',str(tmp_path/'cli'),'--evaluator-commit-sha',SHA,'--evaluated-at',AT])==0


def test_import_boundary_and_governed_files_unchanged(tmp_path):
    forbidden=('market_research_core','strategy_agent.research_only','strategy_agent.adapters','strategy_agent.governance','strategy_agent.runner')
    for p in (ROOT/'src/strategy_agent/evaluation').glob('*.py'):
        if p.name not in {'universal.py','archive_input.py','__main__.py'}:continue
        for n in ast.walk(ast.parse(p.read_text())):
            modules=[a.name for a in n.names] if isinstance(n,ast.Import) else [n.module or ''] if isinstance(n,ast.ImportFrom) else []
            assert not any(m.startswith(forbidden) for m in modules)
    def fingerprint():
        return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for folder in ('registry','governance','configs','notebooks') for p in (ROOT/folder).rglob('*') if p.is_file()}
    before=fingerprint();write_evaluation(run(fixture()),tmp_path/'safe');assert fingerprint()==before


def test_manual_trade_cost_holding_and_drawdown_values():
    r=run(fixture());m=r['metrics']
    for key,expected in {'trade_count':32,'net_pnl':80,'win_rate':.5,'average_win':10,
                         'average_loss':-5,'payoff_ratio':2,'expectancy':2.5,'profit_factor':2,'worst_trade':-5}.items():
        assert m[key]['value']==pytest.approx(expected)
    assert m['holding_period_seconds']['value']=={'mean':3600,'min':3600,'max':3600}
    assert m['cost_to_gross_profit']['value']==pytest.approx(64/192)
    assert m['drawdown_duration_seconds']['value']==172800


def test_annualization_requires_actual_elapsed_observations_and_zero_flows():
    from datetime import datetime,timedelta,timezone
    d=fixture();d['source']['sample_period']['end']='2026-01-01T00:00:00+00:00'
    d['metrics']=available({'external_cash_flows':0})
    assert run(d)['metrics']['annualized_return']['status']=='INSUFFICIENT_SAMPLE'
    start=datetime(2024,1,1,tzinfo=timezone.utc)
    d['equity_curve']=available([{'timestamp':(start+timedelta(days=i)).isoformat(),'equity':100000+i*100} for i in range(401)])
    d['equity_sampling']='REGULAR'
    m=run(d)['metrics']
    assert m['annualized_return']['value']==pytest.approx(1.4**(365.2425/400)-1)
    assert m['volatility']['status']=='AVAILABLE' and m['tail_loss']['status']=='AVAILABLE'
    d['metrics']['value']['external_cash_flows']=1
    assert run(d)['metrics']['annualized_return']['status']=='INSUFFICIENT_SAMPLE'


def protocol_fixture():
    from datetime import datetime,timedelta,timezone
    d=fixture();start=datetime(2024,1,1,tzinfo=timezone.utc)
    d['source']['sample_period']['end']='2024-02-15T00:00:00+00:00';d['sample_role']='OUT_OF_SAMPLE'
    folds=[]
    for i in range(6):
        f={'fold_id':'SYNTHETIC_FIXTURE_fold_'+str(i),'start':(start+timedelta(days=i*6)).isoformat(),
           'end':(start+timedelta(days=(i+1)*6)).isoformat(),'net_pnl':0,'is_score':1,'oos_score':.8}
        for t in d['trades']['value']:
            if f['start']<=t['entry_ts']<f['end']:t['fold_id']=f['fold_id'];f['net_pnl']+=t['net_pnl']
        folds.append(f)
    src=d['source']
    d['statistical_evidence']=available({'protocol':'QUANT_EVALUATION_PROTOCOL v1.0',
       'source_run_id':src['run_id'],'core_sha':src['core_sha'],'manifest_sha256':src['manifest_sha256'],
       'evidence_ref':'SYNTHETIC_FIXTURE_protocol_not_real_benchmark','spec_status':'REGISTERED','validator_errors':0,
       'spec_sha256':'4'*64,'spec_hash_verified':True,'benchmark_core_sha':src['core_sha'],'benchmark_status':'BENCHMARK_PASS',
       'data_pins_verified':True,'return_basis':'OOS_DAILY_NET','folds':folds,'oos_sharpe':1,'psr':.99,'dsr':.99,'holm_adjusted_p':.01,
       'family_trials':1,'family_trial_count_complete':True,'break_even_multiplier':3,'search_mode':'FIXED',
       'max_drawdown_halt':.1,'selection_bias_checked':True,'survivorship_bias_checked':True,'mintrl_satisfied':True})
    d['cost_scenarios']=available([scenario(d,'BASE',80,1),scenario(d,'STRESS',50,2)])
    return d


def test_existing_protocol_gates_and_synthetic_never_keep():
    d=protocol_fixture();r=run(d)
    assert r['engineering_status']=='PASS'
    assert {v['status'] for v in r['protocol_gates'].values()}=={'PASS'}
    assert r['strategy_verdict']=='RETEST'  # never publish a synthetic KEEP as research evidence
    d['statistical_evidence']['value']['psr']=.9
    assert run(d)['strategy_verdict']=='REJECT'


def test_cost_ledger_tampering_and_fold_future_overlap_fail():
    d=fixture();d['cost_breakdown']['value']['explicit_total']=65
    assert run(d)['engineering_status']=='FAIL'
    d=protocol_fixture();d['statistical_evidence']['value']['folds'][1]['start']='2024-01-01T00:00:00+00:00'
    assert run(d)['engineering_status']=='FAIL'


def test_archive_read_only_allowlist_sha_and_missing_trades(tmp_path):
    d=fixture();archive=tmp_path/'SYNTHETIC_FIXTURE_archive';archive.mkdir();(archive/'BASE').mkdir()
    objects={'metrics.json':d['metrics']['value'],'equity_curve.json':d['equity_curve']['value'],
             'm3a4_checks.json':{'P01_engine_git_sha':'PASS','SYNTHETIC_FIXTURE':'PASS'}}
    hashes={}
    for name,obj in objects.items():
        p=archive/'BASE'/name;p.write_text(json.dumps(obj));hashes['BASE/'+name]=hashlib.sha256(p.read_bytes()).hexdigest()
    # A malicious/unrelated manifest entry MUST NOT make the evaluator open any Holdout/source file.
    (archive/'holdout.parquet').symlink_to(tmp_path/'DO_NOT_READ_HOLDOUT')
    hashes['holdout.parquet']='0'*64
    manifest={'status':'COMPLETED','run_id':d['source']['run_id'],'core_identity':{'core_git_sha':d['source']['core_sha']},
              'scenarios':{'BASE':{}},'files_sha256':hashes}
    m=archive/'MANIFEST.json';m.write_text(json.dumps(manifest));sha=hashlib.sha256(m.read_bytes()).hexdigest()
    before={str(p):p.read_bytes() for p in archive.rglob('*') if p.is_file() and not p.is_symlink()}
    context={k:d[k] for k in ('kind','source','holdout','data_quality','initial_capital','sample_role','equity_sampling')}
    normalized=phase4a_archive(archive,expected_manifest_sha256=sha,scenario='BASE',context=context)
    assert normalized['kind']=='SYNTHETIC_FIXTURE'
    assert normalized['trades']['status']=='MISSING'
    assert run(normalized)['strategy_verdict']=='RETEST' and run(normalized)['engineering_status']=='BLOCKED'
    assert before=={str(p):p.read_bytes() for p in archive.rglob('*') if p.is_file() and not p.is_symlink()}
    with pytest.raises(ValueError,match='ARCHIVE_MANIFEST_SHA'):
        phase4a_archive(archive,expected_manifest_sha256='0'*64,scenario='BASE',context=context)
    (archive/'BASE/metrics.json').write_text('{}')
    with pytest.raises(ValueError,match='ARCHIVE_ARTIFACT_SHA'):
        phase4a_archive(archive,expected_manifest_sha256=sha,scenario='BASE',context=context)
    with pytest.raises(ValueError):phase4a_archive(archive,expected_manifest_sha256=sha,scenario='../Holdout',context=context)


def test_output_schema_covers_success_and_blocked_results():
    from jsonschema import Draft202012Validator
    schema=json.loads((ROOT/'schemas/RESEARCH_EVALUATION_SCHEMA.json').read_text())
    for d in (fixture(),{},[],{'source':3}):
        result=run(d);Draft202012Validator(schema).validate(result)
        assert result['strategy_verdict'] in {'KEEP','RETEST','REJECT'}


def test_cli_refuses_dirty_or_falsely_pinned_evaluator(tmp_path,monkeypatch):
    from strategy_agent.evaluation import __main__ as cli
    monkeypatch.setattr(cli,'evaluator_identity',lambda:(SHA,True))
    with pytest.raises(SystemExit):cli.main(['--input',str(FIXTURE),'--output',str(tmp_path/'refused')])
    assert not (tmp_path/'refused').exists()
    monkeypatch.setattr(cli,'evaluator_identity',lambda:(SHA,False))
    with pytest.raises(SystemExit):cli.main(['--input',str(FIXTURE),'--output',str(tmp_path/'refused'),'--evaluator-commit-sha','b'*40])
    assert not (tmp_path/'refused').exists()


def test_parameter_region_and_invalidation_bias():
    d=pnls([100]*15+[-1]*15)
    for i,t in enumerate(d['trades']['value']):t['parameter_region']='SYNTHETIC_FIXTURE_A' if i<15 else 'SYNTHETIC_FIXTURE_B'
    assert 'ONLY_ONE_OBSERVED_PARAMETER_REGION_POSITIVE' in {f['code'] for f in run(d)['risk_flags']}
    d=fixture()
    for t in d['trades']['value'][:8]:t['status']='INVALIDATED'
    d['metrics']=available({});r=run(d)
    assert r['strategy_verdict']=='RETEST' and r['evidence_sufficiency']=='UNDETERMINED'
    assert r['layers']['statistical_evidence']['sample']['invalidated_trades']==8


@pytest.mark.parametrize('section',['data_quality','execution_diagnostics'])
def test_observed_quality_fail_is_distinct_from_missing_evidence(section):
    d=fixture();d[section]['value']['status']='FAIL';r=run(d)
    assert r['engineering_status']=='FAIL' and r['strategy_verdict']=='RETEST'
    assert r['evidence_sufficiency']=='UNDETERMINED' and not r['metrics']
