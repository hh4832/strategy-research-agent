"""Deterministic proposal validation. A valid proposal is neither authority nor an executable request."""
from __future__ import annotations

import copy
from datetime import date,datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import unicodedata
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator

# Pure canonicalisation only. Never instantiate FamilyIndex (which writes its log).
from strategy_agent.family.canonical import structure_hash

ROOT=Path(__file__).resolve().parents[3]
VERSION='1.0.0'
DRAFT_SCHEMA=ROOT/'schemas/PROPOSAL_DRAFT_SCHEMA.json'
PROPOSAL_SCHEMA=ROOT/'schemas/RESEARCH_PROPOSAL_SCHEMA.json'
FORBIDDEN_KEYS={'approved_by','approval_ref','authorization_id','research_authorization','execution_eligible',
                'formal_go','strategy_keep','backtest_results','performance','realized_pnl','metrics',
                'sharpe','sharpe_ratio','net_return','profit_factor'}
FORBIDDEN_STATES={'APPROVED','EXECUTION_ELIGIBLE','STRATEGY_KEEP','FORMAL_GO','BENCHMARK_PASS'}
CATEGORIES={'MOMENTUM':'TREND_FOLLOWING','MEAN_REVERSION':'MEAN_REVERSION','VOLATILITY_REGIME':'VOLATILITY_BREAKOUT',
            'LIQUIDITY_IMBALANCE':'LIQUIDITY_PROVISION','BEHAVIORAL_BIAS':'OTHER','POSITIONING_LEVERAGE':'OTHER','OTHER':'OTHER','UNKNOWN':'OTHER'}
UNKNOWN={'','UNKNOWN','NOT_VERIFIED','PENDING_INFORMATION','UNRESOLVED'}


class DraftRejected(ValueError):
    def __init__(self,code,details):
        self.code=code;self.details=details
        super().__init__(code+': '+str(details))


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def walk(value,path=''):
    yield path,value
    if isinstance(value,dict):
        for key,item in value.items():yield from walk(item,path+'/'+key)
    elif isinstance(value,list):
        for i,item in enumerate(value):yield from walk(item,path+'/'+str(i))


def unknown(value):
    return not isinstance(value,str) or value.strip().upper() in UNKNOWN


def _reject_unsafe(draft):
    for path,node in walk(draft):
        if isinstance(node,dict) and FORBIDDEN_KEYS.intersection(str(k).casefold() for k in node):
            raise DraftRejected('FORBIDDEN_AUTHORITY_OR_PERFORMANCE',path)
        if isinstance(node,str):
            if node.strip().upper() in FORBIDDEN_STATES:raise DraftRejected('UNAUTHORIZED_STATE',path)
            if re.search(r'(?:Sharpe|profit\s*factor|win\s*rate|勝率|年化報酬|淨報酬|total\s*return)\s*(?:is|was|=|為|達到|達)?\s*-?\d',node,re.I):
                raise DraftRejected('UNOBSERVED_PERFORMANCE_CLAIM',path)
            if re.search(r'(?:回測顯示|實測|achieved|backtest(?:ed)?).{0,60}(?:報酬|收益|return|profit).{0,20}\d',node,re.I):
                raise DraftRejected('UNOBSERVED_PERFORMANCE_CLAIM',path)


def _load_draft(raw):
    try:
        def bad(value):raise ValueError('nonfinite '+value)
        draft=json.loads(raw,parse_constant=bad) if isinstance(raw,str) else copy.deepcopy(raw)
        digest(draft)
    except (ValueError,TypeError) as error:raise DraftRejected('INVALID_LLM_JSON',str(error)) from error
    _reject_unsafe(draft)
    errors=sorted((f'/{"/".join(map(str,e.absolute_path))}: {e.message}' for e in
                   Draft202012Validator(json.loads(DRAFT_SCHEMA.read_text())).iter_errors(draft)))
    if errors:raise DraftRejected('DRAFT_SCHEMA',errors)
    return draft


def _normal(text):
    return re.sub(r'\s+',' ',unicodedata.normalize('NFKC',text).casefold()).strip()


def fingerprints(draft):
    fragment=draft['strategy_spec_fragment'];universe=draft['strategy_rules']['universe']
    if fragment is not None:
        seed={**fragment,'hypothesis':{'rationale_category':CATEGORIES[draft['market_mechanism']['category']]}}
        try:family=structure_hash(seed)
        except (ValueError,KeyError,TypeError):
            # Invalid/incomplete rule shapes are pending information, not a reason to invent AST.
            pending=copy.deepcopy(draft);pending['strategy_spec_fragment']=None
            return fingerprints(pending)
        # Indicator role → actual defaults; parameter and indicator names cannot reset the hypothesis.
        parameters=fragment['parameters']['values']
        values=sorted([(p['type'],p['default'],p['min'],p['max'],p.get('step')) for p in parameters.values()],key=lambda v:digest(v))
        roles=[]
        for indicator in fragment['signal']['indicators']:
            actual={k:parameters[v['param']]['default'] if isinstance(v,dict) and v.get('param') in parameters else v
                    for k,v in indicator['params'].items()}
            roles.append({'type':indicator['type'],'input':indicator['input'],'params':actual,'lag':indicator.get('lag',0)})
        param=digest({'values':values,'indicator_roles':sorted(roles,key=digest)})
        method='EXISTING_CANONICAL_STRUCTURE'
    else:
        family=digest({'category':draft['market_mechanism']['category'],'market':universe['market'],
            'timeframe':universe['timeframe'],'entry':re.sub(r'\d+(?:\.\d+)?','#',_normal(draft['strategy_rules']['entry_condition'])),
            'exit':re.sub(r'\d+(?:\.\d+)?','#',_normal(draft['strategy_rules']['exit_condition']))})
        param=digest({'entry':_normal(draft['strategy_rules']['entry_condition']),'exit':_normal(draft['strategy_rules']['exit_condition'])})
        method='INCOMPLETE_DRAFT_FALLBACK_NOT_SEMANTIC_PROOF'
    hypothesis=digest({'family':family,'symbols':sorted(universe['symbols']),'parameters':param,
                       'direction':draft['hypothesis']['expected_direction']})
    return {'hypothesis':hypothesis,'strategy_family':family,'parameter_values':param,'method':method}


def inspect_draft(draft):
    blockers=[];flags=[{'code':'DATA_AND_CAUSAL_MECHANISM_NOT_VERIFIED','message':'Design assertions are not data approval or evidence of strategy effect.'}]
    def block(code,path,message):blockers.append({'code':code,'path':path,'message':message})
    for key,value in draft['hypothesis'].items():
        if unknown(value):block('MISSING_HYPOTHESIS','hypothesis/'+key,'Specify '+key)
    mechanism=draft['market_mechanism']
    if mechanism['category']=='UNKNOWN' or unknown(mechanism['causal_logic']) or len(mechanism['causal_logic'].strip())<20:
        block('MISSING_MARKET_MECHANISM','market_mechanism','Economic/transaction causal logic is required, not indicator performance.')
    if re.fullmatch(r'(?:because )?(?:the )?(?:backtest|indicator).*(?:good|profitable|works)[.! ]*',mechanism['causal_logic'],re.I):
        block('TAUTOLOGICAL_MECHANISM','market_mechanism','Backtest performance is not a causal mechanism.')
    rules=draft['strategy_rules'];data=draft['required_data'];uni=rules['universe']
    for key in ('entry_condition','exit_condition','position_sizing','exit_policy','holding_horizon'):
        if unknown(rules[key]):block('MISSING_RULE','strategy_rules/'+key,'User/model must specify '+key+'; no invented fallback.')
    if rules['signal_time']!='BAR_AVAILABLE_AT':block('SIGNAL_TIME','strategy_rules/signal_time','Require explicit completed-data availability.')
    if rules['execution_time']!='STRICTLY_AFTER_SIGNAL_AVAILABILITY':block('EXECUTION_TIME','strategy_rules/execution_time','Require strictly later eligible execution.')
    for key in ('market','timeframe'):
        if unknown(uni[key]):block('UNIVERSE_UNKNOWN','strategy_rules/universe/'+key,'Explicit universe required.')
        if uni[key]!=data[key]:block('UNIVERSE_MISMATCH','required_data/'+key,'Data and rule universe differ.')
    if not uni['symbols'] or set(uni['symbols'])!=set(data['symbols']):block('UNIVERSE_MISMATCH','required_data/symbols','Explicit identical symbols/universe required.')
    if not {'open','high','low','close','volume'}<=set(data['price_fields']):block('PRICE_FIELDS','required_data/price_fields','Specify required native OHLCV; no source is assumed.')
    if data['availability'] in ('UNKNOWN','UNAVAILABLE') or unknown(data['source_ref']):
        block('DATA_SOURCE_UNRESOLVED','required_data','Identify a planned source; no dataset GO inferred from existence.')
    if unknown(data['pit_timing']) or not data['pit_requirements']:
        block('PIT_UNKNOWN','required_data/pit_timing','Publication/available_at rules must be explicitly planned.')
    feature_rules=data.get('feature_availability_rules',{})
    for feature in data['additional_features']:
        if unknown(feature_rules.get(feature)):
            block('ADDITIONAL_FEATURE_PIT_PENDING','required_data/'+feature,'Each extra feature needs its own planned publication/available_at rule, never a same-date join.')
    coverage=data['coverage']
    try:
        if date.fromisoformat(coverage['start'])>date.fromisoformat(coverage['end']):raise ValueError('reversed dates')
    except ValueError:block('DATA_COVERAGE_UNKNOWN','required_data/coverage','Explicit ordered ISO dates required.')
    variables=draft['research_variables'];changes=variables['changes'];budget=variables['parameter_search']
    if len(changes)!=1:block('ONE_CORE_VARIABLE','research_variables/changes','Exactly one clearly described core-variable change is required.')
    for i,change in enumerate(changes):
        if any(unknown(change[k]) for k in ('core_variable','baseline','proposed_change','expected_improvement','potential_side_effects')):block('CHANGE_RECORD_INCOMPLETE','research_variables/changes/'+str(i),'Baseline/change/effect/side effect must be explicit.')
    if budget['mode']=='UNKNOWN' or budget['max_trials'] is None:block('SEARCH_BUDGET_UNKNOWN','research_variables/parameter_search','Proposed budget metadata required; this grants no budget.')
    if variables['selection_basis']=='RECENT_BEST_RETURN':flags.append({'code':'DATA_SNOOPING_SELECTION','message':'Selection by recent best return requires human review and complete trial accounting.'})
    if not variables['prior_experiment_refs']:flags.append({'code':'PRIOR_EXPERIMENT_COVERAGE_NOT_VERIFIED','message':'No prior experiment refs provided; absence is not evidence of novelty.'})
    for key,value in draft['challenger_view'].items():
        if unknown(value):block('CHALLENGER_INCOMPLETE','challenger_view/'+key,'Explicit challenger question required.')
    fragment=draft['strategy_spec_fragment']
    if fragment is None:
        block('RULE_AST_PENDING','strategy_spec_fragment','Typed Strategy Spec rule fragment is not supplied; no executable spec invented.')
    else:
        fu=fragment['universe']
        if any(fu[k]!=uni[k] for k in ('market','symbols','timeframe')):block('FRAGMENT_UNIVERSE_MISMATCH','strategy_spec_fragment/universe','Fragment and proposal differ.')
        if fragment['execution']['signal_time']!='BAR_AVAILABLE_AT':block('FRAGMENT_TEMPORAL','strategy_spec_fragment/execution','Availability contract mismatch.')
        params=fragment['parameters']['values'];search=fragment['parameters']['search']
        if search['mode']!=budget['mode'] or search.get('max_trials')!=budget['max_trials']:
            block('SEARCH_BUDGET_MISMATCH','strategy_spec_fragment/parameters','Proposed budget and fragment must agree.')
        combinations=1
        for name,p in params.items():
            lo,hi,default=(Decimal(str(p[k])) for k in ('min','max','default'))
            step=Decimal(str(p.get('step',1)))
            if not lo<=default<=hi or (p['type']=='integer' and any(x!=int(x) for x in (lo,hi,default))):
                block('INVALID_PARAMETER_RANGE','parameters/'+name,'Default/bounds/type are inconsistent.')
            elif (default-lo)%step!=0:block('INVALID_PARAMETER_STEP','parameters/'+name,'Default not on declared step grid.')
            if search['mode']=='FIXED' and (lo!=hi or default!=lo):block('FIXED_PARAMETER_RANGE','parameters/'+name,'FIXED cannot conceal a search range.')
            combinations*=max(1,int((hi-lo)/step)+1)
        if budget['max_trials'] is not None and combinations>budget['max_trials']:
            block('PARAMETER_GRID_EXCEEDS_PROPOSED_BUDGET','parameters','Grid cardinality exceeds proposed metadata budget; no budget is changed.')
        for change in changes:
            if 'parameter_name' in change:
                name=change['parameter_name']
                if name not in params or 'proposed_value' not in change or params[name]['default']!=change['proposed_value']:
                    block('CHANGE_PARAMETER_MISMATCH','research_variables/changes','Explicit change record must match the candidate default.')
        indicators={i['id'] for i in fragment['signal']['indicators']}
        if len(indicators)!=len(fragment['signal']['indicators']):block('DUPLICATE_INDICATOR','signal/indicators','IDs must be unique.')
        for path,node in walk(fragment):
            if isinstance(node,dict) and 'ref' in node and node['ref'] not in indicators|{'open','high','low','close','volume'}:
                block('UNRESOLVED_REFERENCE',path,'Unknown indicator/bar reference.')
            if isinstance(node,dict) and 'param' in node and node['param'] not in params:block('UNRESOLVED_PARAMETER',path,'Unknown parameter reference.')
            if isinstance(node,dict) and 'op' in node:
                op=node['op']
                if op in ('gt','ge','lt','le','cross_above','cross_below') and not {'left','right'}<=node.keys():block('CONDITION_ARITY',path,'Comparison needs both operands.')
                if op in ('and','or') and len(node.get('args',[]))<2:block('CONDITION_ARITY',path,'Logical condition needs >=2 operands.')
                if op=='not' and len(node.get('args',[]))!=1:block('CONDITION_ARITY',path,'not needs exactly one operand.')
                if op=='after_range_complete' and node.get('indicator') not in indicators:
                    block('UNRESOLVED_REFERENCE',path,'Unknown opening-range indicator.')
                if op in ('time_ge','time_lt'):
                    try:datetime.strptime(node.get('time_local',''),'%H:%M')
                    except ValueError:block('CONDITION_TIME',path,'Invalid local time.')
        for rule in fragment['signal']['entry_rules']:
            direction='SHORT' if rule['action']=='ENTER_SHORT' else 'LONG'
            if rule['action'] not in ('ENTER_LONG','ENTER_SHORT') or direction not in fragment['risk_constraints']['allowed_directions']:
                block('ENTRY_DIRECTION','signal/entry_rules','Entry action/allowed direction mismatch.')
        if not fragment['signal']['entry_rules']:block('ENTRY_RULE_MISSING','signal/entry_rules','Entry AST required.')
        if not fragment['signal']['exit_rules']:block('EXIT_RULE_MISSING','signal/exit_rules','Explicit exit AST required in this MVP.')
        for rule in fragment['signal']['exit_rules']:
            if rule['action'] not in ('EXIT_LONG','EXIT_SHORT','EXIT_ALL'):block('EXIT_ACTION','signal/exit_rules','Exit action must flatten exposure.')
        for constraint in fragment['parameters']['constraints']:
            def value(x):return params[x]['default'] if isinstance(x,str) and x in params else x
            left,right=value(constraint['left']),value(constraint['right'])
            ops={'<':lambda a,b:a<b,'<=':lambda a,b:a<=b,'>':lambda a,b:a>b,'>=':lambda a,b:a>=b,'!=':lambda a,b:a!=b}
            if not isinstance(left,(int,float)) or not isinstance(right,(int,float)) or not ops[constraint['op']](left,right):
                block('PARAMETER_CONSTRAINT','parameters/constraints','Defaults must satisfy declared constraint.')
    return {'schema_valid':True,'blockers':blockers,'risk_flags':flags}


def _proposal_identity(proposal):
    return 'PROP-'+digest({'origin':proposal['origin'],'draft':proposal['draft'],'priors':proposal['prior_proposal_refs']})[:20]


def _checksum(proposal):
    return digest({k:v for k,v in proposal.items() if k!='proposal_sha256'})


def draft_proposal(hypothesis,adapter,*,agent_commit_sha,created_at=None,prior_proposals=()):
    if not isinstance(hypothesis,str) or not hypothesis.strip():raise ValueError('nonempty user hypothesis required')
    if not re.fullmatch('[0-9a-f]{40}',agent_commit_sha):raise ValueError('full source commit SHA required')
    at=created_at or datetime.now(ZoneInfo('Asia/Taipei')).isoformat()
    if datetime.fromisoformat(at.replace('Z','+00:00')).utcoffset() is None:raise ValueError('aware creation time required')
    draft=_load_draft(adapter.draft(hypothesis));validation=inspect_draft(draft);fp=fingerprints(draft)
    refs=[]
    for prior in sorted(prior_proposals,key=lambda p:digest(p)):
        checked=validate_proposal(prior)
        if not checked['valid']:raise DraftRejected('UNTRUSTED_PRIOR_PROPOSAL',checked['errors'])
        refs.append({'proposal_id':prior['proposal_id'],'proposal_sha256':prior['proposal_sha256']})
        old=fingerprints(prior['draft'])
        if old['hypothesis']==fp['hypothesis']:
            typed=old['method']==fp['method']=='EXISTING_CANONICAL_STRUCTURE'
            validation['risk_flags'].append({'code':'DUPLICATE_HYPOTHESIS' if typed else 'POSSIBLE_DUPLICATE_INCOMPLETE_DESIGN',
                'message':('Same typed fingerprint: ' if typed else 'Incomplete fallback collision; not semantic proof: ')+prior['proposal_id']})
        elif old['strategy_family']==fp['strategy_family']:
            code='SAME_FAMILY_PARAMETER_VARIANT' if old['parameter_values']!=fp['parameter_values'] else 'SAME_FAMILY_DESIGN_VARIANT'
            validation['risk_flags'].append({'code':code,'message':'Design variants do not reset strategy family: '+prior['proposal_id']})
        od=prior['draft']['required_data'];nd=draft['required_data']
        if not unknown(nd['source_ref']) and od['source_ref']==nd['source_ref']:
            try:overlap=max(date.fromisoformat(od['coverage']['start']),date.fromisoformat(nd['coverage']['start']))<=min(date.fromisoformat(od['coverage']['end']),date.fromisoformat(nd['coverage']['end']))
            except (TypeError,ValueError):overlap=False
            if overlap:validation['risk_flags'].append({'code':'REPEATED_DATA_TEST_RISK','message':'Prior proposal references overlapping data; this is not proof of a completed test: '+prior['proposal_id']})
    schema=json.loads(PROPOSAL_SCHEMA.read_text())
    plan=schema['properties']['evaluation_plan']['properties']
    risks=schema['properties']['bias_risk_checklist']['items']['properties']['risk']['enum']
    result={'schema_version':VERSION,'status':'PENDING_INFORMATION' if validation['blockers'] else 'PROPOSED','review_status':'NEEDS_REVIEW',
        'origin':{'user_hypothesis':hypothesis,'adapter_id':adapter.adapter_id,'draft_sha256':digest(draft),
                  'agent_commit_sha':agent_commit_sha,'created_at':at,'input_kind':adapter.input_kind},
        'draft':draft,'evaluation_plan':{k:v['const'] for k,v in plan.items()},
        'bias_risk_checklist':[{'risk':r,'status':'NOT_VERIFIED','required_check':'Review '+r+' under existing Quant Evaluation Protocol before any request can be executed.'} for r in risks],
        'fingerprints':fp,'research_change_record':copy.deepcopy(draft['research_variables']['changes']),
        'validation':validation,'prior_proposal_refs':sorted(refs,key=lambda x:(x['proposal_id'],x['proposal_sha256'])),
        'holdout_policy':'NO_ACCESS; PROPOSAL_ONLY; NO_AUTHORIZATION','request_contract':'PINNED_SHARED_SCHEMA; BINDINGS_AND_HUMAN_APPROVAL_REQUIRED',
        'execution_policy':'PENDING_HUMAN_REVIEW; NEVER_EXECUTE_OR_AUTHORIZE'}
    result['request_contract_pin']=copy.deepcopy(schema['properties']['request_contract_pin']['const'])
    result['proposal_id']=_proposal_identity(result);result['proposal_sha256']=_checksum(result)
    checked=validate_proposal(result)
    if not checked['valid']:raise DraftRejected('GENERATED_PROPOSAL_INVALID',checked['errors'])
    return result


def validate_proposal(proposal):
    errors=[f'/{"/".join(map(str,e.absolute_path))}: {e.message}' for e in Draft202012Validator(json.loads(PROPOSAL_SCHEMA.read_text())).iter_errors(proposal)]
    if errors:return {'valid':False,'errors':errors,'execution_eligible':False}
    try:
        draft=_load_draft(proposal['draft']);check=inspect_draft(draft)
        if proposal['origin']['draft_sha256']!=digest(draft):errors.append('DRAFT_SHA_MISMATCH')
        if proposal['proposal_sha256']!=_checksum(proposal):errors.append('PROPOSAL_SHA_MISMATCH')
        if proposal['proposal_id']!=_proposal_identity(proposal):errors.append('PROPOSAL_ID_MISMATCH')
        if proposal['fingerprints']!=fingerprints(draft):errors.append('FINGERPRINT_MISMATCH')
        if proposal['research_change_record']!=draft['research_variables']['changes']:errors.append('CHANGE_RECORD_MISMATCH')
        if proposal['status']!=('PENDING_INFORMATION' if check['blockers'] else 'PROPOSED'):errors.append('STATUS_MISMATCH')
        if proposal['validation']['blockers']!=check['blockers']:errors.append('VALIDATION_MISMATCH')
        risks=[i['risk'] for i in proposal['bias_risk_checklist']]
        expected=json.loads(PROPOSAL_SCHEMA.read_text())['properties']['bias_risk_checklist']['items']['properties']['risk']['enum']
        if sorted(risks)!=sorted(expected):errors.append('BIAS_CHECKLIST_INCOMPLETE')
        if datetime.fromisoformat(proposal['origin']['created_at'].replace('Z','+00:00')).utcoffset() is None:errors.append('TIMESTAMP_NAIVE')
    except (ValueError,KeyError,TypeError,ArithmeticError) as error:errors.append(str(error))
    return {'valid':not errors,'errors':errors,'proposal_status':proposal['status'],'execution_eligible':False,
            'authorization':'NOT_GRANTED','request_contract':'PINNED_SHARED_SCHEMA; BINDINGS_AND_HUMAN_APPROVAL_REQUIRED'}


def write_proposal(proposal,output_directory):
    checked=validate_proposal(proposal)
    if not checked['valid']:raise DraftRejected('INVALID_PROPOSAL',checked['errors'])
    out=Path(output_directory).resolve()
    forbidden=[ROOT/p for p in ('registry','governance','releases','configs','notebooks','docs/evidence','src','schemas','tests','specs','.git')]
    if any(out==p or p in out.parents for p in forbidden):raise ValueError('cannot write formal registry/governance/research evidence')
    out.mkdir(parents=True,exist_ok=False)
    draft=proposal['draft'];fragment=draft['strategy_spec_fragment']
    # This typed fragment intentionally lacks dataset verdicts, costs, experiment pins and full provenance.
    spec={'status':'PARTIAL_DRAFT_NOT_EXECUTABLE','strategy_spec_fragment':fragment,
          'strategy_spec_schema':'STRATEGY_SPEC_SCHEMA.json','full_spec_validated':False,
          'missing_materialization':['data_requirements with independently verified dataset refs','approved costs',
                                     'experiment/governance bindings','full Strategy Spec metadata/provenance'],
          'execution_eligible':False}
    files={'proposal.json':proposal,'strategy_spec_draft.json':spec,'data_requirements.json':draft['required_data'],
           'bias_risk_checklist.json':{'proposal_id':proposal['proposal_id'],'items':proposal['bias_risk_checklist'],
                                     'risk_flags':proposal['validation']['risk_flags']}}
    for name,value in files.items():
        with (out/name).open('x',encoding='utf-8') as f:f.write(json.dumps(value,indent=2,sort_keys=True,ensure_ascii=False,allow_nan=False)+'\n')
    meta=f"Proposal: {proposal['proposal_id']}\nStatus: {proposal['status']}\nReview: NEEDS_REVIEW\nOrigin: {proposal['origin']['input_kind']}\nCommit: {proposal['origin']['agent_commit_sha']}\nSHA256: {proposal['proposal_sha256']}\n"
    report='# Research Proposal — NOT_VERIFIED\n\n'+meta+'\nNo authorization, execution, performance or strategy approval is asserted.\n\n'
    for key in ('hypothesis','market_mechanism','strategy_rules','required_data','research_variables'):
        report+='## '+key+'\n\n```json\n'+json.dumps(draft[key],indent=2,ensure_ascii=False,sort_keys=True)+'\n```\n\n'
    report+='## Evaluation plan\n\n'+json.dumps(proposal['evaluation_plan'],ensure_ascii=False,sort_keys=True)+'\n\n## Deterministic validation\n\n```json\n'+json.dumps(proposal['validation'],indent=2,ensure_ascii=False,sort_keys=True)+'\n```\n\nRequest contract is pinned. Export requires explicit existing-artefact bindings; human approval remains pending. Full executable Strategy Spec not materialized.\n'
    with (out/'proposal_report.md').open('x',encoding='utf-8') as f:f.write(report)
    challenger='# Challenger review — NOT_VERIFIED\n\n'+meta+'\nThese are questions/hypotheses, not observed backtest findings.\n\n'
    for key,value in draft['challenger_view'].items():challenger+='- '+key+': '+value+'\n'
    challenger+='\nIndependent review required: market beta, failure regimes, concentration, full trial history, PIT and costs.\n'
    with (out/'challenger_review.md').open('x',encoding='utf-8') as f:f.write(challenger)
    return out
