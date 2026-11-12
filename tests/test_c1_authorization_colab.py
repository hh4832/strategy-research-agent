"""Synthetic metadata/UI and isolated governance tests; never a live approval."""
import ast
import copy
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator
from strategy_agent.readiness import c1_authorization as A
from strategy_agent.governance.research_authorization import (
    ResearchAuthorizationBook, dated_cost_failures,
)

ROOT=Path(__file__).parents[1]
NB=ROOT/'notebooks/Phase5C1_Research_Authorization.ipynb'
NOW=datetime.fromisoformat('2026-11-12T10:00:00+08:00')


def settings(**updates):
    return dict(mode='VALIDATE_ONLY',authorization_id=A.AUTH,lead='hh4832',validity_days=7,
                pr_url='',archive='SYNTHETIC_METADATA_ONLY',**updates)


def metadata():
    p=A.source_proposal()
    return dict(proposal=p,
        readiness=dict(data_binding='REVIEWED_PINS_VERIFIED',ready_to_submit_formal_authorization=True,
            observed_pins=A.PINS,execution='NOT_EXECUTED',consumed_batches=0,core_reader_sha=A.CORE,
            cost_scenario_compatibility={'status':'PASS'}),
        pins=dict(status='REVIEWED_PINS_VERIFIED',observed_pins=A.PINS),
        preflight=dict(status='PASS',checks={str(i):'PASS' for i in range(9)}),
        context=dict(pin_review_evidence_ref='SYNTHETIC_TEST_REFERENCE_ONLY'),
        cfg=dict(market={'market_id':'IBKR_TSM','symbol':'TSM'},core={'phase4a_sha':A.CORE},strategy={'parameters':{'fast':5,'slow':20}},dataset=p['dataset'],
            windows={'warmup_start':'2016-10-19T00:00:00-04:00','start':'2017-01-01T00:00:00-05:00','end':'2025-01-01T00:00:00-05:00'},
            core_costs={k:v for k,v in p['costs'].items() if k.startswith('dated_')}),
        spec=dict(strategy_id=p['strategy']['strategy_id'],universe={'timeframe':'15m','sessions':{'allowed':['RTH']}},position_sizing={'units':100,'initial_capital':100000}),scenario={},schedule=dict(schedule_sha256=A.SCHEDULE))


def test_legacy_book_and_new_unsigned_preview_schema():
    assert len(ResearchAuthorizationBook().active)==3
    p=A.source_proposal();p.update(issued_at=NOW.isoformat(),expires_at='2026-11-19T10:00:00+08:00')
    assert A.unsigned_errors(p)==[]
    for field in ('dated_schedule_path','dated_schedule_sha256'):
        bad=copy.deepcopy(p);del bad['costs'][field];assert A.unsigned_errors(bad)
    bad=copy.deepcopy(p);bad['costs']['dated_schedule_sha256']='bad';assert A.unsigned_errors(bad)
    assert dated_cost_failures({'costs':{}},{})==[]


@pytest.mark.parametrize('change',['path','sha','missing','legacy_authorization'])
def test_actual_execution_pin_comparison(change):
    a=A.source_proposal();cfg={k:v for k,v in a['costs'].items() if k.startswith('dated_')}
    assert dated_cost_failures(a,cfg)==[]
    if change=='path':cfg['dated_schedule_path']='different.json'
    if change=='sha':cfg['dated_schedule_sha256']='0'*64
    if change=='missing':cfg.pop('dated_schedule_path')
    if change=='legacy_authorization':a['costs'].pop('dated_schedule_sha256');a['costs'].pop('dated_schedule_path')
    assert dated_cost_failures(a,cfg)[0][0]=='RA06'


def test_runner_preflight_and_run_refuse_dated_mismatch_before_any_consumption(tmp_path,monkeypatch):
    from test_phase4a_research_only import gov, CFG, cfg_copy, codes
    from strategy_agent.execution import c1_integration as C
    def pin(p):p['costs'].update(dated_schedule_path=A.source_proposal()['costs']['dated_schedule_path'],dated_schedule_sha256=A.SCHEDULE)
    runner,g=gov(tmp_path,proposal_edit=pin)
    # Missing pair in real config: actual preflight and run refuse without touching a reader/budget.
    before=(g/'uses.jsonl').read_bytes()
    assert 'RA06' in codes(runner.preflight(CFG))
    assert runner.run(CFG)['executed'] is False
    assert (g/'uses.jsonl').read_bytes()==before
    # Same pair passes this check; other existing gates remain in force.
    monkeypatch.setattr(C,'load_dated_schedules',lambda *a,**k:{'BASE':SimpleNamespace(require_coverage=lambda *a:None)})
    path=cfg_copy(tmp_path,lambda c:c.setdefault('core_costs',{}).update(dated_schedule_path=A.source_proposal()['costs']['dated_schedule_path'],dated_schedule_sha256=A.SCHEDULE))
    assert 'RA06' not in codes(runner.preflight(path))


def test_validate_only_metadata_boundary_has_no_admin_or_ledger_writes(monkeypatch):
    protected={p:p.read_bytes() for base in ('governance','registry') for p in (ROOT/base).rglob('*') if p.is_file()}
    def forbidden(*a,**k):raise AssertionError('No subprocess/admin in validation')
    monkeypatch.setattr(A.subprocess,'run',forbidden)
    result=A.validate(settings(),metadata=metadata(),now=NOW)
    assert result['status']=='PASS' and result['authorization_status']=='VALIDATED_UNSIGNED'
    assert result['validity_preview']['not_an_approval'] is True
    assert all(p.read_bytes()==b for p,b in protected.items())


@pytest.mark.parametrize('field,value',[
    ('mode','EXECUTE_AUTHORIZED'),('authorization_id','OTHER'),('lead','someone'),('validity_days',True),('validity_days',8)])
def test_settings_fail_closed(field,value):
    s=settings();s[field]=value
    with pytest.raises(ValueError):A.validate(s,metadata=metadata(),now=NOW)


@pytest.mark.parametrize('mutation',[
    lambda m:m['pins'].update(status='OBSERVED_NOT_TRUSTED'),
    lambda m:m['readiness'].update(consumed_batches=1),
    lambda m:m['cfg']['core_costs'].update(dated_schedule_sha256='0'*64),
    lambda m:m['cfg']['windows'].update(end='2026-01-01T00:00:00-05:00'),
    lambda m:m['preflight']['checks'].update({'0':'FAIL'}),
    lambda m:m['context'].update(pin_review_evidence_ref=''),
])
def test_metadata_deviations_fail(mutation):
    m=copy.deepcopy(metadata());mutation(m)
    assert A.validate(settings(),metadata=m,now=NOW)['status']=='FAIL'


def test_archive_refuses_substituted_index_before_any_data_read(tmp_path):
    (tmp_path/'ARCHIVE_INDEX.json').write_text('{}')
    with pytest.raises(ValueError,match='index differs'):A.archive_metadata(tmp_path)


def payloads():
    repo={'id':1443253343,'full_name':A.REPO}
    user={'id':269247281,'login':'hh4832','type':'User'}
    pr=dict(html_url='https://github.com/'+A.REPO+'/pull/999',state='open',merged=False,
        user=user,base={'repo':repo,'ref':'main'},head={'repo':repo,'ref':'authorization-review','sha':'a'*40})
    return repo,user,pr


@pytest.mark.parametrize('mutation',[
    lambda r,u,p:u.update(login='someone'),lambda r,u,p:u.update(id=1),
    lambda r,u,p:p.update(merged=True),lambda r,u,p:p['head'].update(ref='feature/phase5c1-authorization-colab'),
    lambda r,u,p:p['head'].update(ref='feature/phase5c1-first-scientific-research'),
    lambda r,u,p:p.update(performed_via_github_app={'id':1}),lambda r,u,p:p['base'].update(ref='other'),
])
def test_live_identity_pr_contract_refuses_wrong_inputs(mutation):
    r,u,p=payloads();mutation(r,u,p)
    s=settings();s.update(pr_url='https://github.com/'+A.REPO+'/pull/999',code_branch='feature/phase5c1-authorization-colab')
    with pytest.raises(ValueError):A.check_github_payloads(r,u,p,s)


def test_approve_requires_all_explicit_conditions_before_network(tmp_path,monkeypatch):
    def forbidden(*a,**k):raise AssertionError('No network without human gate')
    monkeypatch.setattr(A,'github_identity',forbidden)
    for mode,confirm,typed in [('VALIDATE_ONLY',True,A.AUTH),('APPROVE',False,A.AUTH),('APPROVE',True,'')]:
        s=settings();s.update(mode=mode,confirm=confirm)
        with pytest.raises(ValueError,match='Explicit human'):A.approve(s,tmp_path,typed)


def test_notebook_static_and_no_top_level_approval_invocation():
    n=json.loads(NB.read_text());code=[''.join(c['source']) for c in n['cells'] if c['cell_type']=='code']
    assert len(code)==7 and "APPROVAL_MODE = 'VALIDATE_ONLY'" in code[0]
    assert 'CONFIRM_AUTHORIZATION = False' in code[0]
    for c in n['cells']:
        if c['cell_type']=='code':assert c['outputs']==[] and c['execution_count'] is None
    for source in code:
        ast.parse(source)
        assert not any(x in source for x in ('ResearchOnlyRunner','EXECUTE_AUTHORIZED','git push','git commit'))
    gate=ast.parse(code[4]);top=[x for x in gate.body if not isinstance(x,(ast.FunctionDef,ast.AsyncFunctionDef))]
    for node in top:
        for call in ast.walk(node):
            if isinstance(call,ast.Call):
                assert not (isinstance(call.func,ast.Name) and call.func.id in ('_human_approve','run_management'))
    assert 'approve_button.on_click(_human_approve)' in code[4]
    assert 'source.read_bytes()' in code[6] and "target.open('xb')" in code[6]


def test_fresh_namespace_run_all_cannot_approve_even_in_approve_mode(monkeypatch):
    # Execute the actual independent gate cell as Run All does, with an enabled button.
    import sys
    from types import ModuleType
    class Widget:
        def __init__(self,**kw):self.__dict__.update(kw);self.handler=None
        def on_click(self,fn):self.handler=fn
        def __enter__(self):return self
        def __exit__(self,*a):pass
    w=ModuleType('ipywidgets')
    for name in ('Checkbox','Text','Button','Output'):setattr(w,name,Widget)
    d=ModuleType('IPython.display');d.display=lambda *a:None
    monkeypatch.setitem(sys.modules,'ipywidgets',w);monkeypatch.setitem(sys.modules,'IPython.display',d)
    def forbidden(*a,**k):raise AssertionError('Run All attempted approval')
    source=[''.join(c['source']) for c in json.loads(NB.read_text())['cells'] if c['cell_type']=='code'][4]
    ns=dict(APPROVAL_MODE='APPROVE',CONFIRM_AUTHORIZATION=True,GITHUB_PR_URL='SYNTHETIC',
        VALIDATION={'status':'PASS'},PR_VERIFIED=True,run_management=forbidden)
    exec(compile(source,'actual-approval-ui','exec'),ns)
    assert ns['approve_button'].handler is ns['_human_approve']
    assert ns['human_checkbox'].value is False and ns['human_id'].value==''


def test_final_runner_guard_rechecks_dated_pin_before_atomic_reservation(tmp_path,monkeypatch):
    from test_phase4a_research_only import gov, CFG
    from strategy_agent.governance.research_authorization import AuthorizationDecision
    from strategy_agent.research_only.runner import Preflight
    runner,g=gov(tmp_path)
    cfg=json.loads(CFG.read_text())
    approved={'costs':{'dated_schedule_path':'approved.json','dated_schedule_sha256':A.SCHEDULE}}
    def forbidden(*a,**k):raise AssertionError('No reservation/execution permitted')
    fakeguard=SimpleNamespace(check=lambda req:AuthorizationDecision(True,[],approved),consume=forbidden)
    monkeypatch.setattr(runner,'preflight',lambda p:Preflight('READY_FOR_CORE_INTEGRATION'))
    monkeypatch.setattr(runner,'_guard',lambda c:fakeguard)
    monkeypatch.setattr(runner,'data_checks',lambda *a,**k:{'blockers':[],'probe':SimpleNamespace(as_dict=lambda:{})})
    monkeypatch.setattr(runner,'_execute',forbidden)
    before=(g/'uses.jsonl').read_bytes()
    result=runner.run(CFG,snapshot_root=tmp_path,archive_root=tmp_path/'archive',git_commit='a'*40,
        actor='SYNTHETIC_TEST_ONLY',core=object(),core_costs=object(),
        core_native=SimpleNamespace(core_code_identity=lambda:{}))
    assert result['executed'] is False and result['slot_consumed'] is False
    assert result['blockers']==[{'code':'RA06','message':'dated Schedule path/SHA differs from the approved authorization'}]
    assert (g/'uses.jsonl').read_bytes()==before


def test_fresh_runtime_validate_only_runs_all_seven_cells_without_approval(tmp_path,monkeypatch):
    """Execute actual cells, mocking Colab/network provisioning and the private-Drive input boundary only."""
    import sys
    from types import ModuleType
    n=json.loads(NB.read_text());cells=[''.join(c['source']) for c in n['cells'] if c['cell_type']=='code']
    protected={p:p.read_bytes() for base in ('governance','registry') for p in (ROOT/base).rglob('*') if p.is_file()}
    google=ModuleType('google');colab=ModuleType('google.colab')
    colab.drive=SimpleNamespace(mount=lambda p:None)
    colab.userdata=SimpleNamespace(get=lambda k:'SYNTHETIC_TOKEN_MUST_NOT_BE_ARCHIVED')
    google.colab=colab
    monkeypatch.setitem(sys.modules,'google',google);monkeypatch.setitem(sys.modules,'google.colab',colab)
    class Widget:
        def __init__(self,**kw):self.__dict__.update(kw)
        def on_click(self,fn):self.handler=fn
    w=ModuleType('ipywidgets')
    for name in ('Checkbox','Text','Button','Output'):setattr(w,name,Widget)
    display=ModuleType('IPython.display');display.display=lambda *a:None
    monkeypatch.setitem(sys.modules,'ipywidgets',w);monkeypatch.setitem(sys.modules,'IPython.display',display)
    def forbidden(*a,**k):raise AssertionError('Run All must never call approve')
    monkeypatch.setattr(A,'approve',forbidden)
    monkeypatch.setattr(A,'archive_metadata',lambda *a,**k:metadata())
    import shutil
    monkeypatch.setattr(shutil,'which',lambda n:'/synthetic/python3.11')
    def fake_run(args,**kwargs):
        args=list(map(str,args));stdout=''
        if args[0]=='git':
            if args[1]=='clone':
                dest=Path(args[-1]);(dest/'schemas').mkdir(parents=True)
                shutil.copyfile(ROOT/'schemas/RESEARCH_AUTHORIZATION_SCHEMA.json',dest/'schemas/RESEARCH_AUTHORIZATION_SCHEMA.json')
            elif 'get-url' in args:stdout='https://github.com/'+A.REPO+'.git'
            elif 'rev-parse' in args:stdout='a'*40
        elif '--version' in args:stdout='Python 3.11.17'
        elif '-c' in args:stdout='3.11'
        elif '-m' in args and 'strategy_agent.readiness.c1_authorization' in args:
            start=args.index('strategy_agent.readiness.c1_authorization')+1
            assert args[start]=='validate'
            A.main(args[start:])
        return subprocess.CompletedProcess(args,0,stdout,'')
    monkeypatch.setattr(subprocess,'run',fake_run)
    class Setup(ast.NodeTransformer):
        def visit_Constant(self,node):
            if isinstance(node.value,str) and node.value.startswith('/content'):
                return ast.copy_location(ast.Constant(str(tmp_path/'content')+node.value[len('/content'):]),node)
            return node
        def visit_Assign(self,node):
            self.generic_visit(node)
            names=[t.id for t in node.targets if isinstance(t,ast.Name)]
            if 'CODE_SHA' in names:node.value=ast.Constant('a'*40)
            if 'CONFIRM_CODE_REVIEW' in names:node.value=ast.Constant(True)
            return node
    ns={}
    for i,source in enumerate(cells):
        tree=ast.fix_missing_locations(Setup().visit(ast.parse(source)))
        exec(compile(tree,f'actual-auth-cell-{i+1}','exec'),ns)
    assert ns['VALIDATION']['status']=='PASS'
    assert ns['approve_button'].disabled is True
    archive=ns['DRIVE_EVIDENCE_ARCHIVE'];index=json.loads((archive/'ARCHIVE_INDEX.json').read_text())
    assert index['authorization_status']=='NOT_APPROVED'
    for relative,h in index['files_sha256'].items():
        b=(archive/relative).read_bytes()
        assert sha256(b).hexdigest()==h and b'SYNTHETIC_TOKEN_MUST_NOT_BE_ARCHIVED' not in b
    assert all(p.read_bytes()==b for p,b in protected.items())


@pytest.mark.parametrize('mutation', ['valid', 'partial', 'duration', 'naive', 'scope', 'signature', 'pin'])
def test_source_proposal_accepts_only_strict_unsigned_time_preview(tmp_path, mutation):
    import shutil
    for relative in (A.SOURCE, 'schemas'):
        shutil.copytree(ROOT/relative, tmp_path/relative)
    original = json.loads((tmp_path/A.SOURCE/'authorization.DRAFT_NOT_APPROVED.json').read_text())['proposal_fields']
    candidate = copy.deepcopy(original)
    path = tmp_path/A.PROPOSAL
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(candidate))
    assert A.source_proposal(tmp_path) == original
    candidate.update(issued_at='2026-11-12T10:00:00+08:00', expires_at='2026-11-19T10:00:00+08:00')
    if mutation == 'partial': candidate['expires_at'] = 'HUMAN_DECISION_REQUIRED'
    if mutation == 'duration': candidate['expires_at'] = '2026-11-20T10:00:00+08:00'
    if mutation == 'naive': candidate['issued_at'] = '2026-11-12T10:00:00'
    if mutation == 'scope': candidate['max_batches'] = 2
    if mutation == 'signature': candidate['approval_ref'] = 'https://github.com/example'
    if mutation == 'pin': candidate['costs']['dated_schedule_sha256'] = '0'*64
    path.write_text(json.dumps(candidate))
    if mutation == 'valid':
        assert A.source_proposal(tmp_path) == original
    else:
        with pytest.raises(ValueError): A.source_proposal(tmp_path)
