"""Contract tests use Claude's byte-identical pinned schema; all result/binding data is SYNTHETIC_FIXTURE."""
import copy
import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from strategy_agent.proposal import draft_proposal,FakeLLMAdapter,write_proposal
from strategy_agent.proposal.exporter import (export_request,shared_schema,BindingsPending,ContractPending,
    REQUEST_CONTRACT_PIN,REQUEST_SCHEMA_SHA256,SCHEMA_PATH)

ROOT=Path(__file__).resolve().parents[1]
AT='2026-11-07T12:00:00+08:00'


def artifacts(tmp_path):
    fixture=json.loads((ROOT/'fixtures/proposal/complete_momentum.json').read_text())
    assert fixture['kind']=='SYNTHETIC_FIXTURE'
    proposal=draft_proposal(fixture['user_hypothesis'],FakeLLMAdapter(),agent_commit_sha='a'*40,created_at=AT)
    (tmp_path/'specs').mkdir();(tmp_path/'configs').mkdir()
    spec=json.loads((ROOT/'specs/examples/sma_crossover_tw_2330_5m.json').read_text())
    spec.update(copy.deepcopy(proposal['draft']['strategy_spec_fragment']))
    spec.update(strategy_id='synthetic_fixture_sma',name='SYNTHETIC_FIXTURE not a real registered strategy')
    spec['data_requirements']['datasets']=[{'dataset_id':'synthetic_fixture_dataset','snapshot_id':'SYNTHETIC_FIXTURE_snapshot',
        'producer_commit_sha':'1'*40,'manifest_sha256':'2'*64,'verdict':'GO','usage_scope':'BACKTEST'}]
    spec['provenance']['notes']='SYNTHETIC_FIXTURE schema demonstration only; no real dataset status or authorization.'
    sf=tmp_path/'specs/synthetic_fixture_spec.json';sf.write_text(json.dumps(spec))
    cf=tmp_path/'configs/synthetic_fixture_scenarios.json';cf.write_text(json.dumps({'kind':'SYNTHETIC_FIXTURE','status':'NOT_VERIFIED'}))
    bindings={
        'strategy_family':{'family_id':'synthetic-fixture-family','strategy_id':spec['strategy_id'],'version':spec['version'],'parameters':{'fast':5,'slow':20}},
        'strategy_spec_ref':str(sf.relative_to(tmp_path)),'strategy_spec_sha256':hashlib.sha256(sf.read_bytes()).hexdigest(),
        'market':{'market_id':'TW_EQUITY','symbol':'2330'},
        'dataset_ref':{'dataset_id':'synthetic_fixture_dataset','producer_commit_sha':'1'*40},
        'dataset_snapshot_id':'SYNTHETIC_FIXTURE_snapshot','dataset_manifest_sha256':'2'*64,
        'signal_time_rule':{'policy_id':'CONSERVATIVE_LABEL_AGNOSTIC','policy_version':'1.0.0',
            'policy_sha256':'7fa3cdcaf501651f749dbc72c31b362fcb9e10cc55920191cef87c1a050059c4','signal_time':'CORE_MODELED_AVAILABLE_AT'},
        'execution_time_rule':{'rule':'NEXT_ELIGIBLE_BAR_OPEN'},
        'cost_profile_ref':{'scenario_set_path':str(cf.relative_to(tmp_path)),'scenario_set_sha256':hashlib.sha256(cf.read_bytes()).hexdigest(),'scenarios':['BASE','STRESS']},
        'research_window':{'warmup_start':'2024-05-29T00:00:00+08:00','start':'2024-06-03T00:00:00+08:00','end':'2024-06-29T00:00:00+08:00'},
        'requested_change':{'kind':'SYNTHETIC_TEST'},'execution_mode':'SYNTHETIC_TEST','research_budget':{'batches_requested':0},
        'holdout_policy':{'access':'NO_ACCESS','lock_id':'HOLDOUT-SYNTHETIC-FIXTURE'},
        'requested_outputs':['RESEARCH_EVALUATION','RESEARCH_DECISION','RISK_FLAGS','ROBUSTNESS_MATRIX']}
    return proposal,bindings


def test_shared_schema_is_exact_upstream_contract_not_a_competing_version():
    assert REQUEST_CONTRACT_PIN=='5d088c4de362add925344d8e48b36c2e9d728d96'
    assert hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest()==REQUEST_SCHEMA_SHA256
    assert shared_schema()['$id'].endswith('/schemas/RESEARCH_REQUEST_SCHEMA.json')
    assert shared_schema()['properties']['authorization_ref']['oneOf'][1]=={'const':'PENDING'}


def test_export_schema_valid_request_pending_human_approval_only(tmp_path):
    proposal,bindings=artifacts(tmp_path)
    before={str(p):p.read_bytes() for p in tmp_path.rglob('*.json')}
    target=tmp_path/'exports/research_request.json'
    result=export_request(proposal,target,bindings=bindings,artifact_root=tmp_path)
    request=json.loads(target.read_text());Draft202012Validator(shared_schema()).validate(request)
    assert proposal['request_contract_pin']['commit_sha']==REQUEST_CONTRACT_PIN
    assert proposal['request_contract_pin']['schema_sha256']==REQUEST_SCHEMA_SHA256
    assert request['authorization_ref']==request['core_commit']=='PENDING'
    assert result['provenance']['status']=='SCHEMA_VALID_PENDING_HUMAN_APPROVAL'
    assert result['provenance']['execution_eligible'] is False and result['provenance']['batch_consumption']==0
    assert result['provenance']['request_submitted'] is False
    assert 'SYNTHETIC_FIXTURE' in request['hypothesis']
    assert all(Path(p).read_bytes()==v for p,v in before.items())
    assert target.with_name('research_request_provenance.json').exists()
    with pytest.raises(FileExistsError):export_request(proposal,target,bindings=bindings,artifact_root=tmp_path)


def test_no_bindings_means_pending_information_not_guessed_references(tmp_path):
    proposal,_=artifacts(tmp_path);target=tmp_path/'exports/research_request.json'
    with pytest.raises(BindingsPending):export_request(proposal,target)
    assert not target.exists()


@pytest.mark.parametrize('field,value',[('authorization_ref',{'authorization_id':'RA-SYNTHETIC_FAKE'}),
    ('core_commit','123abc'),('execution_eligible',True),('hypothesis','override model content')])
def test_no_authority_override_short_sha_or_unknown_request_fields(tmp_path,field,value):
    proposal,b=artifacts(tmp_path);b[field]=value
    with pytest.raises(ValueError):export_request(proposal,tmp_path/'no_request.json',bindings=b,artifact_root=tmp_path)
    assert not (tmp_path/'no_request.json').exists()


def test_reference_tampering_and_mismatched_parameters_stop_before_output(tmp_path):
    proposal,b=artifacts(tmp_path)
    b['strategy_family']['parameters']['fast']=6
    with pytest.raises(ValueError,match='STRATEGY_ID_OR_PARAMETERS'):export_request(proposal,bindings=b,artifact_root=tmp_path)
    b['strategy_family']['parameters']['fast']=5
    (tmp_path/b['strategy_spec_ref']).write_text('{}')
    with pytest.raises(ValueError,match='REFERENCE_SHA'):export_request(proposal,bindings=b,artifact_root=tmp_path)


def test_timestamp_coverage_holdout_and_synthetic_scope_guards(tmp_path):
    proposal,b=artifacts(tmp_path)
    b['research_window']['start']=b['research_window']['end']
    with pytest.raises(ValueError,match='warmup_start'):export_request(proposal,bindings=b,artifact_root=tmp_path)
    b['research_window']['start']='2024-06-03T00:00:00+08:00';b['research_window']['end']='2025-06-03T00:00:00+08:00'
    with pytest.raises(ValueError,match='COVERAGE'):export_request(proposal,bindings=b,artifact_root=tmp_path)
    b['research_window']['end']='2024-06-29T00:00:00+08:00';b['holdout_policy']['access']='ALLOW_ACCESS'
    with pytest.raises(ValueError):export_request(proposal,bindings=b,artifact_root=tmp_path)
    b['holdout_policy']['access']='NO_ACCESS';b['execution_mode']='RESEARCH_ONLY_REAL';b['research_budget']['batches_requested']=1
    with pytest.raises(ValueError,match='synthetic proposal'):export_request(proposal,bindings=b,artifact_root=tmp_path)


def test_contract_drift_is_rejected_instead_of_accepting_unpinned_schema(tmp_path,monkeypatch):
    from strategy_agent.proposal import exporter
    p=tmp_path/'bad_schema.json';p.write_text('{}');monkeypatch.setattr(exporter,'SCHEMA_PATH',p)
    with pytest.raises(ValueError,match='CONTRACT_SHA_MISMATCH'):shared_schema()
