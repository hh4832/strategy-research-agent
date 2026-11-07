"""Export only against Claude's exact pinned shared contract; never call an orchestrator or grant authority."""
from __future__ import annotations

import copy
from datetime import date,datetime,timedelta
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator
from strategy_agent.spec.validate import validate as validate_spec

from .agent import ROOT,validate_proposal,digest

REQUEST_CONTRACT_PIN='5d088c4de362add925344d8e48b36c2e9d728d96'
REQUEST_SCHEMA_SHA256='1b0530f321c6c71e9eac1bbe5f9c33e3f92e4af4c4ce11b80777c9a94c5324eb'
SCHEMA_PATH=ROOT/'schemas/RESEARCH_REQUEST_SCHEMA.json'


class ContractPending(RuntimeError):
    code='CONTRACT_PENDING'


class BindingsPending(ValueError):
    code='PENDING_INFORMATION'


def shared_schema():
    if not SCHEMA_PATH.is_file():raise ContractPending('CONTRACT_PENDING: pinned shared schema unavailable; no request written')
    data=SCHEMA_PATH.read_bytes()
    if hashlib.sha256(data).hexdigest()!=REQUEST_SCHEMA_SHA256:
        raise ValueError('REQUEST_CONTRACT_SHA_MISMATCH: contract must match pinned upstream bytes')
    return json.loads(data)


def _reference(root,relative,expected_sha,prefix):
    if not relative.startswith(prefix+'/'):raise ValueError('REFERENCE_NAMESPACE_MISMATCH')
    path=(root/relative).resolve(strict=True)
    if not path.is_relative_to(root) or path.suffix!='.json':raise ValueError('UNSAFE_REFERENCE: JSON repository artefacts only')
    data=path.read_bytes()
    if hashlib.sha256(data).hexdigest()!=expected_sha:raise ValueError('REFERENCE_SHA_MISMATCH:'+relative)
    return json.loads(data)


def _check_references(request,proposal,root):
    spec=_reference(root,request['strategy_spec_ref'],request['strategy_spec_sha256'],'specs')
    if any(i.level=='ERROR' for i in validate_spec(spec)):raise ValueError('REFERENCED_STRATEGY_SPEC_INVALID')
    fragment=proposal['draft']['strategy_spec_fragment']
    if any(spec.get(k)!=v for k,v in fragment.items()):raise ValueError('STRATEGY_SPEC_DIFFERS_FROM_PROPOSAL_FRAGMENT')
    family=request['strategy_family']
    defaults={k:p['default'] for k,p in spec['parameters']['values'].items()}
    if family['strategy_id']!=spec['strategy_id'] or family['version']!=spec['version'] or family['parameters']!=defaults:
        raise ValueError('STRATEGY_ID_OR_PARAMETERS_MISMATCH')
    symbols=spec['universe']['symbols']
    if len(symbols)!=1 or symbols[0]!=request['market']['symbol']:raise BindingsPending('SINGLE_SYMBOL_REQUEST_BINDING_REQUIRED')
    ds=request['dataset_ref']
    matched=[d for d in spec['data_requirements']['datasets'] if d['dataset_id']==ds['dataset_id'] and d['snapshot_id']==request['dataset_snapshot_id'] and d['producer_commit_sha']==ds['producer_commit_sha']]
    if not matched or any(d.get('manifest_sha256',request['dataset_manifest_sha256'])!=request['dataset_manifest_sha256'] for d in matched):
        raise ValueError('SPEC_DATASET_REFERENCE_MISMATCH')
    cost=request['cost_profile_ref'];_reference(root,cost['scenario_set_path'],cost['scenario_set_sha256'],'configs')
    window=request['research_window']
    wu,ws,we=(datetime.fromisoformat(window[k].replace('Z','+00:00')) for k in ('warmup_start','start','end'))
    if not wu<=ws<we:raise ValueError('research_window: require warmup_start <= start < end')
    tz=ZoneInfo(spec['universe']['timezone']);coverage=proposal['draft']['required_data']['coverage']
    lo=datetime.combine(date.fromisoformat(coverage['start']),datetime.min.time(),tz)
    hi=datetime.combine(date.fromisoformat(coverage['end'])+timedelta(days=1),datetime.min.time(),tz)
    if not lo<=wu<=ws<we<=hi:raise ValueError('REQUEST_WINDOW_OUTSIDE_PROPOSAL_COVERAGE')


def export_request(proposal,output_path=None,*,bindings=None,artifact_root=ROOT):
    """Bindings are explicit operator-provided references, not model fields or a competing request schema.

    core_commit may remain PENDING. authorization_ref is ALWAYS PENDING in this
    design-only producer. A syntactically valid request still requires human
    approval and Orchestrator governance; this function never sends it anywhere.
    """
    schema=shared_schema()
    checked=validate_proposal(proposal)
    if not checked['valid']:raise ValueError('invalid proposal: '+str(checked['errors']))
    if proposal['status']!='PROPOSED':raise BindingsPending('proposal still has unresolved design information')
    if bindings is None:raise BindingsPending('explicit existing-artefact bindings are missing; no references guessed')
    if not isinstance(bindings,dict):raise ValueError('bindings must be a JSON object')
    controlled={'schema_version','request_id','hypothesis','market_mechanism'}
    if controlled.intersection(bindings):raise ValueError('bindings cannot override producer-controlled proposal content')
    if bindings.get('authorization_ref','PENDING')!='PENDING':raise ValueError('AUTHORIZATION_MUST_REMAIN_PENDING; Proposal Agent never selects or grants authorization')
    draft=proposal['draft'];h=draft['hypothesis']
    request=copy.deepcopy(bindings)
    request.update(schema_version='1.0.0',request_id='RR-'+proposal['proposal_id'].upper(),
        hypothesis=('SYNTHETIC_FIXTURE — ' if proposal['origin']['input_kind']=='SYNTHETIC_FIXTURE' else '')+
                   h['question']+'\nHypothesis: '+h['testable_claim']+'\nNull: '+h['null_hypothesis'],
        market_mechanism=draft['market_mechanism']['causal_logic'],authorization_ref='PENDING')
    request.setdefault('core_commit','PENDING')
    change=request.get('requested_change')
    if not isinstance(change,dict):raise BindingsPending('requested_change.kind binding required')
    if 'description' in change:raise ValueError('requested_change.description is derived from the reviewed proposal')
    change['description']='; '.join(c['baseline']+' → '+c['proposed_change'] for c in draft['research_variables']['changes'])
    errors=[f'{"/".join(map(str,e.absolute_path))}: {e.message}' for e in Draft202012Validator(schema).iter_errors(request)]
    if errors:raise BindingsPending('REQUEST_BINDINGS_INVALID: '+str(errors))
    if proposal['origin']['input_kind']=='SYNTHETIC_FIXTURE' and request['execution_mode']!='SYNTHETIC_TEST':
        raise ValueError('synthetic proposal must remain SYNTHETIC_TEST; never export it as real research')
    _check_references(request,proposal,Path(artifact_root).resolve(strict=True))
    provenance={'status':'SCHEMA_VALID_PENDING_HUMAN_APPROVAL','proposal_id':proposal['proposal_id'],
        'proposal_sha256':proposal['proposal_sha256'],'contract_commit':REQUEST_CONTRACT_PIN,'schema_sha256':REQUEST_SCHEMA_SHA256,
        'request_sha256':digest(request),'pending_fields':[k for k in ('core_commit','authorization_ref') if request[k]=='PENDING'],
        'input_kind':proposal['origin']['input_kind'],'reference_checks':'FILE_SHA_AND_SPEC_COMPATIBILITY_ONLY; GOVERNANCE_NOT_EVALUATED',
        'execution_eligible':False,'authorization_granted':False,'request_submitted':False,'batch_consumption':0}
    if output_path is not None:
        out=Path(output_path).resolve()
        forbidden=[ROOT/p for p in ('registry','governance','releases','configs','notebooks','docs/evidence','src','schemas','tests','specs','.git')]
        if out==ROOT or any(out==p or p in out.parents for p in forbidden):raise ValueError('cannot write protected artefacts')
        if out.suffix!='.json':raise ValueError('request output must be a new JSON file')
        # Check both targets before allocation. All validation/reference checks precede writes.
        sidecar=out.with_name(out.stem+'_provenance.json')
        if out.exists() or sidecar.exists():raise FileExistsError('request or provenance already exists')
        out.parent.mkdir(parents=True,exist_ok=True)
        with out.open('x',encoding='utf-8') as f:f.write(json.dumps(request,indent=2,sort_keys=True,ensure_ascii=False,allow_nan=False)+'\n')
        with sidecar.open('x',encoding='utf-8') as f:f.write(json.dumps(provenance,indent=2,sort_keys=True,ensure_ascii=False,allow_nan=False)+'\n')
    return {'request':request,'provenance':provenance}
