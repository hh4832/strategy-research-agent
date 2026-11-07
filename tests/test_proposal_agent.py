"""All model completions and research examples here are SYNTHETIC_FIXTURE. No live model/data call."""
import ast
import copy
import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from strategy_agent.proposal import FakeLLMAdapter,FileDraftAdapter,DraftRejected,draft_proposal,validate_proposal,write_proposal
from strategy_agent.proposal.agent import digest,fingerprints,inspect_draft
from strategy_agent.proposal.exporter import ContractPending,export_request,REQUEST_CONTRACT_PIN

ROOT=Path(__file__).resolve().parents[1]
AT='2026-11-07T12:00:00+08:00'
SHA='a'*40


def fixture(name='complete_momentum'):
    f=json.loads((ROOT/'fixtures/proposal'/f'{name}.json').read_text());assert f['kind']=='SYNTHETIC_FIXTURE';return f


class Completion:
    adapter_id='SYNTHETIC_FIXTURE_MUTATED_COMPLETION';input_kind='SYNTHETIC_FIXTURE'
    def __init__(self,draft):self.value=draft
    def draft(self,hypothesis):return json.dumps(self.value,ensure_ascii=False)


def generate(draft=None,**kwargs):
    f=fixture();return draft_proposal(f['user_hypothesis'],Completion(draft if draft is not None else f['llm_draft']),
                                      agent_commit_sha=SHA,created_at=AT,**kwargs)


def blockers(p):return {x['code'] for x in p['validation']['blockers']}
def flags(p):return {x['code'] for x in p['validation']['risk_flags']}


def test_complete_natural_language_design_is_proposed_never_authorized():
    f=fixture();p=draft_proposal(f['user_hypothesis'],FakeLLMAdapter(),agent_commit_sha=SHA,created_at=AT)
    assert p['status']=='PROPOSED' and p['review_status']=='NEEDS_REVIEW'
    assert p['request_contract']=='PINNED_SHARED_SCHEMA; BINDINGS_AND_HUMAN_APPROVAL_REQUIRED'
    assert validate_proposal(p)['valid'] and validate_proposal(p)['execution_eligible'] is False
    assert p['origin']['input_kind']=='SYNTHETIC_FIXTURE'
    Draft202012Validator(json.loads((ROOT/'schemas/RESEARCH_PROPOSAL_SCHEMA.json').read_text())).validate(p)


@pytest.mark.parametrize('name',['momentum','mean_reversion','margin_financing'])
def test_three_required_broad_ideas_do_not_invent_rules_or_data(name):
    f=fixture(name);p=draft_proposal(f['user_hypothesis'],FakeLLMAdapter(),agent_commit_sha=SHA,created_at=AT)
    assert p['status']=='PENDING_INFORMATION'
    assert p['draft']['strategy_spec_fragment'] is None
    assert p['draft']['required_data']['availability']=='UNKNOWN'
    assert {'RULE_AST_PENDING','SIGNAL_TIME','PIT_UNKNOWN'}<=blockers(p)


def test_arbitrary_natural_language_retained_without_silent_sma_defaults():
    text='研究完全不同、尚未定義規則的市場機制是否存在。'
    p=draft_proposal(text,FakeLLMAdapter(),agent_commit_sha=SHA,created_at=AT)
    assert p['status']=='PENDING_INFORMATION' and p['origin']['user_hypothesis']==text
    assert p['draft']['strategy_rules']['universe']['market']=='UNKNOWN'
    assert p['draft']['strategy_spec_fragment'] is None


@pytest.mark.parametrize('section,key,code',[
 ('market_mechanism','causal_logic','MISSING_MARKET_MECHANISM'),
 ('strategy_rules','entry_condition','MISSING_RULE'),('strategy_rules','exit_condition','MISSING_RULE'),
 ('strategy_rules','signal_time','SIGNAL_TIME'),('strategy_rules','execution_time','EXECUTION_TIME'),
 ('required_data','pit_timing','PIT_UNKNOWN'),('required_data','source_ref','DATA_SOURCE_UNRESOLVED')])
def test_unknown_necessary_design_information_stays_pending(section,key,code):
    d=fixture()['llm_draft'];d[section][key]='UNKNOWN';p=generate(d)
    assert p['status']=='PENDING_INFORMATION' and code in blockers(p)


def test_nonexistent_source_is_not_replaced_with_known_market_data():
    d=fixture()['llm_draft'];d['required_data']['availability']='UNAVAILABLE'
    p=generate(d);assert p['status']=='PENDING_INFORMATION' and 'DATA_SOURCE_UNRESOLVED' in blockers(p)


def test_two_core_variables_and_missing_baseline_are_not_optimized():
    d=fixture()['llm_draft'];d['research_variables']['changes'].append(copy.deepcopy(d['research_variables']['changes'][0]))
    p=generate(d);assert 'ONE_CORE_VARIABLE' in blockers(p) and p['status']=='PENDING_INFORMATION'
    d=fixture()['llm_draft'];d['research_variables']['changes'][0]['baseline']='UNKNOWN'
    assert 'CHANGE_RECORD_INCOMPLETE' in blockers(generate(d))


def test_renamed_strategy_does_not_reset_hypothesis_or_family():
    old=generate();d=fixture()['llm_draft'];d['title']='SYNTHETIC_FIXTURE renamed, same economic rules'
    d['hypothesis']['question']='Renamed question, identical typed strategy.'
    new=generate(d,prior_proposals=[old])
    assert old['fingerprints']==new['fingerprints']
    assert {'DUPLICATE_HYPOTHESIS','REPEATED_DATA_TEST_RISK'}<=flags(new)
    assert new['review_status']=='NEEDS_REVIEW'


def test_small_parameter_changes_share_family_and_prior_data_review():
    old=generate();d=fixture()['llm_draft'];p=d['strategy_spec_fragment']['parameters']['values']['fast']
    p.update(default=6,min=6,max=6)
    d['research_variables']['changes'][0].update(proposed_value=6,proposed_change='SMA6/20')
    new=generate(d,prior_proposals=[old])
    assert old['fingerprints']['strategy_family']==new['fingerprints']['strategy_family']
    assert old['fingerprints']['hypothesis']!=new['fingerprints']['hypothesis']
    assert 'SAME_FAMILY_PARAMETER_VARIANT' in flags(new)
    assert new['research_change_record']==d['research_variables']['changes']


def test_recent_best_return_selection_is_not_endorsed():
    d=fixture()['llm_draft'];d['research_variables']['selection_basis']='RECENT_BEST_RETURN'
    assert 'DATA_SNOOPING_SELECTION' in flags(generate(d))


@pytest.mark.parametrize('change',[{'default':30},{'min':10,'max':2},{'default':5.5}])
def test_invalid_parameter_ranges_pending_without_running_grid(change):
    d=fixture()['llm_draft'];d['strategy_spec_fragment']['parameters']['values']['fast'].update(change)
    p=generate(d);assert 'INVALID_PARAMETER_RANGE' in blockers(p) and p['status']=='PENDING_INFORMATION'


def test_illegal_json_and_nonfinite_completion_fail_closed(tmp_path):
    path=tmp_path/'llm.json'
    for text in ('not json','{"bad": NaN}','{"missing":true}'):
        path.write_text(text)
        with pytest.raises(DraftRejected):draft_proposal('Hypothesis',FileDraftAdapter(path),agent_commit_sha=SHA,created_at=AT)


@pytest.mark.parametrize('payload',[{'performance':{'sharpe':2}}, {'approved_by':'hh4832'},
 {'status':'APPROVED'},{'authorization_id':'RA_FAKE'},{'execution_eligible':True},{'formal_go':True}])
def test_llm_cannot_supply_authority_or_fake_performance(payload):
    d=fixture()['llm_draft'];d.update(payload)
    with pytest.raises(DraftRejected):generate(d)


@pytest.mark.parametrize('claim',['回測顯示 Sharpe 2.1','Win rate = 75%','實測收益達到 10000','achieved return 20%'])
def test_numeric_past_performance_claims_in_free_text_are_rejected(claim):
    d=fixture()['llm_draft'];d['hypothesis']['testable_claim']=claim
    with pytest.raises(DraftRejected,match='UNOBSERVED_PERFORMANCE_CLAIM'):generate(d)


def test_no_research_authorization_is_required_to_propose_or_granted():
    p=generate();text=json.dumps(p)
    assert 'authorization_id' not in text and 'approved_by' not in text
    assert validate_proposal(p)['authorization']=='NOT_GRANTED'
    assert p['holdout_policy']=='NO_ACCESS; PROPOSAL_ONLY; NO_AUTHORIZATION'


def test_shared_request_contract_absence_is_explicit_and_writes_nothing(tmp_path,monkeypatch):
    from strategy_agent.proposal import exporter
    assert REQUEST_CONTRACT_PIN=='5d088c4de362add925344d8e48b36c2e9d728d96'
    monkeypatch.setattr(exporter,'SCHEMA_PATH',tmp_path/'missing_schema.json')
    out=tmp_path/'research_request.json'
    with pytest.raises(ContractPending,match='CONTRACT_PENDING'):export_request(generate(),out)
    assert not out.exists()
    assert (ROOT/'schemas/RESEARCH_REQUEST_SCHEMA.json').exists()  # exact adopted shared bytes; missing runtime contract still stops


def test_fixed_model_completion_and_timestamp_reproduce_all_six_outputs(tmp_path):
    a=generate();b=generate();assert a==b
    one=write_proposal(a,tmp_path/'one');two=write_proposal(b,tmp_path/'two')
    names={'proposal.json','proposal_report.md','strategy_spec_draft.json','data_requirements.json','bias_risk_checklist.json','challenger_review.md'}
    assert {p.name for p in one.iterdir()}==names
    for name in names:assert (one/name).read_bytes()==(two/name).read_bytes()
    spec=json.loads((one/'strategy_spec_draft.json').read_text())
    assert spec['full_spec_validated'] is False and spec['execution_eligible'] is False
    assert spec['status']=='PARTIAL_DRAFT_NOT_EXECUTABLE'
    assert 'data_requirements' not in spec['strategy_spec_fragment']
    with pytest.raises(FileExistsError):write_proposal(a,one)


def test_partial_spec_fragments_use_existing_spec_schema():
    p=generate();spec=json.loads((ROOT/'STRATEGY_SPEC_SCHEMA.json').read_text())
    for key,value in p['draft']['strategy_spec_fragment'].items():
        Draft202012Validator({'$defs':spec['$defs'],**spec['properties'][key]}).validate(value)


def test_untrusted_prior_and_self_rehashed_pending_status_fail_closed():
    old=generate();old['draft']['title']='tampered'
    with pytest.raises(DraftRejected,match='UNTRUSTED_PRIOR'):generate(prior_proposals=[old])
    d=fixture()['llm_draft'];d['strategy_rules']['entry_condition']='UNKNOWN';pending=generate(d)
    pending['status']='PROPOSED';pending['proposal_sha256']=digest({k:v for k,v in pending.items() if k!='proposal_sha256'})
    checked=validate_proposal(pending);assert not checked['valid'] and 'STATUS_MISMATCH' in checked['errors']


def test_no_backtest_imports_or_registry_governance_writes(tmp_path,monkeypatch):
    from strategy_agent.family.canonical import FamilyIndex
    monkeypatch.setattr(FamilyIndex,'assign',lambda *a,**k:pytest.fail('proposal must never assign/write a registered family'))
    forbidden=('strategy_agent.orchestration','strategy_agent.research_only','strategy_agent.governance',
               'strategy_agent.evaluation','strategy_agent.adapters','market_research_core','strategy_agent.runner')
    for path in (ROOT/'src/strategy_agent/proposal').glob('*.py'):
        for n in ast.walk(ast.parse(path.read_text())):
            imports=[a.name for a in n.names] if isinstance(n,ast.Import) else [n.module or ''] if isinstance(n,ast.ImportFrom) else []
            assert not any(m.startswith(forbidden) for m in imports)
    def fingerprint():
        return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for d in ('registry','governance','configs','notebooks','specs','src/strategy_agent/research_only','src/strategy_agent/evaluation') for p in (ROOT/d).rglob('*') if p.is_file() and '__pycache__' not in str(p)}
    before=fingerprint();write_proposal(generate(),tmp_path/'out');assert fingerprint()==before
    for d in ('registry','governance','releases','configs','notebooks','docs/evidence','src/strategy_agent/governance','src/strategy_agent/research_only','src/strategy_agent/evaluation'):
        with pytest.raises(ValueError):write_proposal(generate(),ROOT/d/'forbidden_proposal')


def test_cli_draft_validate_export_is_nonexecuting(tmp_path,monkeypatch,capsys):
    from strategy_agent.proposal import __main__ as cli
    monkeypatch.setattr(cli,'identity',lambda:SHA)  # synthetic code identity, never governance
    inp=tmp_path/'hypothesis.txt';inp.write_text(fixture()['user_hypothesis']);out=tmp_path/'proposal'
    assert cli.main(['draft','--input',str(inp),'--output',str(out),'--created-at',AT])==0
    assert cli.main(['validate','--proposal',str(out/'proposal.json')])==0
    request=tmp_path/'request.json'
    assert cli.main(['export-request','--proposal',str(out/'proposal.json'),'--output',str(request)])==2
    assert not request.exists() and 'PENDING_INFORMATION' in capsys.readouterr().out


def test_incomplete_comparison_ast_is_pending_not_fingerprinting_exception():
    d=fixture()['llm_draft'];d['strategy_spec_fragment']['signal']['entry_rules'][0]['when']={'op':'gt'}
    p=generate(d);assert 'CONDITION_ARITY' in blockers(p)
    assert p['status']=='PENDING_INFORMATION'


def test_additional_features_need_individual_publication_rules():
    d=fixture()['llm_draft'];d['required_data']['additional_features']=['margin_financing_balance']
    assert 'ADDITIONAL_FEATURE_PIT_PENDING' in blockers(generate(d))
    d['required_data']['feature_availability_rules']={'margin_financing_balance':'Use actual source publication available_at, never same as_of date. This planned source is NOT_VERIFIED.'}
    assert 'ADDITIONAL_FEATURE_PIT_PENDING' not in blockers(generate(d))


def test_explicit_change_record_cannot_disagree_with_candidate_parameter():
    d=fixture()['llm_draft'];d['research_variables']['changes'][0]['proposed_value']=6
    assert 'CHANGE_PARAMETER_MISMATCH' in blockers(generate(d))


def test_no_data_quality_approval_or_go_in_model_fragment():
    d=fixture()['llm_draft'];d['required_data']['availability']='GO'
    with pytest.raises(DraftRejected):generate(d)
    d=fixture()['llm_draft'];d['strategy_spec_fragment']['data_requirements']={'datasets':[{'verdict':'GO'}]}
    with pytest.raises(DraftRejected):generate(d)


def test_same_bar_and_future_lag_cannot_become_eligible():
    d=fixture()['llm_draft'];d['strategy_rules']['execution_time']='SAME_BAR_AS_SIGNAL'
    p=generate(d);assert 'EXECUTION_TIME' in blockers(p) and p['status']=='PENDING_INFORMATION'
    d=fixture()['llm_draft'];d['strategy_spec_fragment']['signal']['indicators'][0]['lag']=-1
    with pytest.raises(DraftRejected):generate(d)


def test_parameter_and_indicator_renaming_preserve_fingerprints():
    old=generate();d=fixture()['llm_draft'];f=d['strategy_spec_fragment']
    f['parameters']['values']['renamed_fast']=f['parameters']['values'].pop('fast')
    d['research_variables']['changes'][0]['parameter_name']='renamed_fast'
    for path,node in __import__('strategy_agent.proposal.agent',fromlist=['walk']).walk(f):
        if isinstance(node,dict) and node.get('param')=='fast':node['param']='renamed_fast'
    for c in f['parameters']['constraints']:
        for key in ('left','right'):
            if c[key]=='fast':c[key]='renamed_fast'
    f['signal']['indicators'][0]['id']='renamed_indicator'
    old_id=fixture()['llm_draft']['strategy_spec_fragment']['signal']['indicators'][0]['id']
    for path,node in __import__('strategy_agent.proposal.agent',fromlist=['walk']).walk(f):
        if isinstance(node,dict) and node.get('ref')==old_id:node['ref']='renamed_indicator'
    new=generate(d,prior_proposals=[old])
    assert old['fingerprints']==new['fingerprints'] and 'DUPLICATE_HYPOTHESIS' in flags(new)
