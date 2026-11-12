"""Isolated metadata/concurrency tests; never dispatch a strategy or mutate production ledgers."""
import base64
import copy
from datetime import datetime
import json
import multiprocessing
from pathlib import Path
import pickle
import threading

import pytest

from strategy_agent.execution import c1_runner as R, c1_integration as C
from strategy_agent.execution import c1_claim as K
from strategy_agent.execution.c1_archive import archive_attempt, verify_archive
from strategy_agent.readiness.c1_authorization import PINS

ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture
def staged():
    cfg=R.read(ROOT/'configs/research_only/tsm_sma_5_20_p5b3c_2024_06_dev_18350b3adba8e28a7e5e.json')
    b=copy.deepcopy(cfg['snapshot_binding']);b['snapshot_root']='/SYNTHETIC_METADATA_ONLY/'+C.SNAPSHOT
    b['manifest_sha256']=PINS['manifest_sha256']
    b['snapshot_pin'].update(snapshot_id=C.SNAPSHOT,producer_sha=C.PRODUCER,start='2016-10-19',end='2024-12-31',manifest_sha256=PINS['manifest_sha256'])
    b['integrity_receipt'].update(manifest_sha256=PINS['manifest_sha256'],parquet_sha256=PINS['parquet_sha256'])
    return dict(bundle=b,inspection=dict(status='REVIEWED_PINS_VERIFIED',observed_pins=dict(PINS),recovery_core_sha=C.CORE),
        preflight=dict(status='PASS',checks={k:'PASS' for k in R.CHECKS},output_holdout_rows=0,batch_consumption=0,strategy_run=False),local_root=b['snapshot_root'])

@pytest.fixture
def prepared(tmp_path,staged,monkeypatch):
    pytest.importorskip('market_research_core')
    # Freeze only the current-time check, preserving the actual approved chain and contract checks.
    original=R.authority
    monkeypatch.setattr(R,'authority',lambda root:original(root,now=datetime.fromisoformat('2026-11-13T12:00:00+08:00')))
    path=tmp_path/'prepared'
    R.prepare(staged,path,code_sha='a'*40,governance_root=ROOT)
    return path


def test_actual_prepare_is_nonconsuming_and_uses_c1_a10(prepared,staged):
    before={p:p.read_bytes() for folder in ('governance','registry') for p in (ROOT/folder).rglob('*') if p.is_file()}
    result=R.preflight(prepared,staged,governance_root=ROOT,code_sha='a'*40)
    assert result[2]['kind']=='PHASE5C1_PRODUCTION_READINESS_MANIFEST'
    assert R.read(prepared/'C1_PINNED_CONTEXT.json')['batch_consumed']==0
    assert all(p.read_bytes()==v for p,v in before.items())
    R.unused(ROOT)

@pytest.mark.parametrize('key,value',[('status','FAIL'),('output_holdout_rows',1),('batch_consumption',1),('strategy_run',True)])
def test_data_report_fail_closed(staged,key,value):
    staged['preflight'][key]=value
    with pytest.raises(ValueError):R.validate_data_report(staged['preflight'])

@pytest.mark.parametrize('mutation',['core','snapshot','cost','strategy','schedule','window'])
def test_a10_artifact_mismatches_remain_rejected(prepared,staged,mutation):
    from strategy_agent.research_only.runner import canonical_sha
    cfg=R.read(prepared/'artifacts'/C.CONFIG)
    if mutation=='core':cfg['core']['phase4a_sha']='0'*40
    if mutation=='snapshot':cfg['snapshot_binding']['manifest_sha256']='0'*64
    if mutation=='cost':cfg['costs']['scenario_set_sha256']='0'*64
    if mutation=='strategy':cfg['strategy']['spec_file_sha256']='0'*64
    if mutation=='schedule':cfg['core_costs']['dated_schedule_sha256']='0'*64
    if mutation=='window':cfg['windows']={}
    cfg['provenance']['config_sha256']=canonical_sha(cfg)
    with pytest.raises(ValueError):R.readiness_manifest(R.authority(ROOT),cfg,prepared/'artifacts',staged,governance_root=ROOT,measured_core_sha=C.CORE)


def transport(state,lock,root,method,path,payload):
    if path=='/user':return 200,dict(login='hh4832',id=K.LEAD_ID,type='User')
    if path=='/repos/'+K.REPO:return 200,dict(id=K.REPO_ID,full_name=K.REPO)
    if '/contents/' in path:
        relative=path.split('/contents/')[1].split('?')[0]
        return 200,dict(encoding='base64',content=base64.b64encode((Path(root)/relative).read_bytes()).decode())
    if path.endswith('/git/ref/heads/main'):return 200,{'object':{'sha':R.GOVERNANCE_SHA}}
    if method=='GET':return (200,{}) if state.get('claimed') else (404,{})
    with lock:
        if state.get('claimed'):return 422,{}
        state['claimed']=True
        return 201,{'ref':K.REF,'object':{'sha':R.GOVERNANCE_SHA}}


def runtime_worker(state,lock,root,queue,index):
    client=K.GitHubClaim(transport=lambda m,p,v:transport(state,lock,root,m,p,v))
    try:
        grant=client.acquire(governance_root=root,governance_sha=R.GOVERNANCE_SHA,request_sha=f'{index:064x}')
        queue.put(('winner',grant.record['claim_ref']))
    except K.ReconciliationRequired:queue.put(('stopped',K.REF))


def test_distinct_runtime_and_request_share_one_atomic_claim():
    ctx=multiprocessing.get_context('spawn')
    with ctx.Manager() as manager:
        state=manager.dict();lock=manager.Lock();q=ctx.Queue()
        jobs=[ctx.Process(target=runtime_worker,args=(state,lock,str(ROOT),q,i)) for i in range(4)]
        for p in jobs:p.start()
        for p in jobs:p.join(30);assert p.exitcode==0
        results=[q.get(timeout=2) for p in jobs]
        assert sum(s=='winner' for s,ref in results)==1
        assert {ref for s,ref in results}=={K.REF}


def grant():
    state={};lock=threading.Lock()
    return K.GitHubClaim(transport=lambda m,p,v:transport(state,lock,ROOT,m,p,v)).acquire(
        governance_root=ROOT,governance_sha=R.GOVERNANCE_SHA,request_sha='a'*64)


def test_claim_cannot_be_reused_or_serialized():
    g=grant()
    with pytest.raises(TypeError):pickle.dumps(g)
    with pytest.raises(K.ReconciliationRequired):g.consume(K.AUTH,'b'*64)
    g.consume(K.AUTH,'a'*64)
    with pytest.raises(K.ReconciliationRequired):g.consume(K.AUTH,'a'*64)


def test_lost_claim_acknowledgement_stops_without_retry():
    state={};calls=[]
    def call(m,p,v):
        calls.append(m)
        result=transport(state,threading.Lock(),ROOT,m,p,v)
        if m=='POST':raise K.ReconciliationRequired('lost acknowledgement')
        return result
    c=K.GitHubClaim(transport=call)
    with pytest.raises(K.ReconciliationRequired):c.acquire(governance_root=ROOT,governance_sha=R.GOVERNANCE_SHA,request_sha='a'*64)
    with pytest.raises(K.ReconciliationRequired):c.acquire(governance_root=ROOT,governance_sha=R.GOVERNANCE_SHA,request_sha='a'*64)
    assert calls.count('POST')==1


def test_archive_covers_interrupted_attempt_and_rejects_tamper(tmp_path):
    for folder in ('prep','work','audit','gov/governance','gov/registry'):(tmp_path/folder).mkdir(parents=True)
    for folder in ('prep','work','audit','gov/governance','gov/registry'):(tmp_path/folder/'state.json').write_text('{}')
    archive=archive_attempt(tmp_path/'archives',preparation=tmp_path/'prep',work=tmp_path/'work',runtime_audit=tmp_path/'audit',
        governance=tmp_path/'gov',provenance={'code_sha':'a'*40},outcome={'status':'RECONCILIATION_REQUIRED'})
    index=verify_archive(archive)
    assert {'preparation/state.json','execution/state.json','runtime_audit/state.json',
        'settlement/governance/state.json','settlement/registry/state.json','run_info.txt'}<=set(index['files_sha256'])
    (archive/'execution/state.json').write_text('changed')
    with pytest.raises(ValueError):verify_archive(archive)


def test_existing_c1_use_requires_reconciliation(tmp_path):
    from strategy_agent.registry.chain import ChainedLog
    # An isolated legitimate chain, not an event appended to the formal ledger.
    path=tmp_path/'registry/research_authorization_uses.jsonl';path.parent.mkdir()
    log=ChainedLog(path)
    log.append({'authorization_id':K.AUTH})
    with pytest.raises(K.ReconciliationRequired):R.unused(tmp_path)


def test_direct_c1_dispatch_without_server_capability_stops(prepared,tmp_path):
    with pytest.raises(R.B.B3CStopped,match='C1_SERVER_CLAIM_CAPABILITY_REQUIRED'):
        R.B.execute(prepared/'governed_request',bind_url='',task_url='',verifier=None,backend=None,
            work_dir=tmp_path/'work',claim_dir=tmp_path/'control',artifact_root=prepared/'artifacts',governance_root=ROOT)


def test_notebook_defaults_to_prepare_and_unpublished_pin_stops():
    notebook=R.read(ROOT/'notebooks/Phase5C1_Development_Execution.ipynb')
    sources=[''.join(c['source']) for c in notebook['cells'] if c['cell_type']=='code']
    namespace={};exec(sources[0],namespace)
    assert namespace['MODE']=='PREPARE' and namespace['CONFIRM_REMOTE_CLAIM'] is False
    namespace['EXECUTION_CODE_SHA']=None
    with pytest.raises(RuntimeError,match='reviewed published'):exec(sources[1],namespace)


def test_interrupt_after_claim_archives_and_blocks_reentry(tmp_path,monkeypatch):
    import strategy_agent.orchestration.governance as G
    gov=tmp_path/'gov';(gov/'governance').mkdir(parents=True);(gov/'registry').mkdir()
    prep=tmp_path/'prep';prep.mkdir();(prep/'manifest.json').write_text('{}')
    work=tmp_path/'work';work.mkdir();(work/'reservation.json').write_text('{"state":"UNKNOWN"}')
    audit=tmp_path/'audit';audit.mkdir()
    monkeypatch.setattr(G,'is_isolated_test_root',lambda root:True)
    monkeypatch.setattr(R,'preflight',lambda *a,**kw:({'bound_request_sha256':'a'*64},{},{}))
    monkeypatch.setattr(R,'execution_preflight',lambda *a,**kw:({'bound_request_sha256':'a'*64},{},{},object()))
    calls=[]
    def interrupt(*a,**kw):calls.append('dispatch');raise KeyboardInterrupt()
    monkeypatch.setattr(R.B,'execute',interrupt)
    class Client:
        def acquire(self,**kwargs):calls.append('claim');return grant()
    kwargs=dict(governance_root=gov,code_sha='a'*40,claim_client=Client(),drive_control=tmp_path/'control',
        archive_parent=tmp_path/'archives',runtime_audit=audit,work_dir=work,session_dir=tmp_path/'session',
        bind_url='personal-bind',task_url='personal-task',verifier=object(),
        confirm_authorization=K.AUTH,confirm_remote_claim=True)
    with pytest.raises(K.ReconciliationRequired,match='INTERRUPTED'):R.execute(prep,{},**kwargs)
    archives=list((tmp_path/'archives').iterdir());assert len(archives)==1
    assert R.read(archives[0]/'outcome.json')['status']=='RECONCILIATION_REQUIRED'
    assert (archives[0]/'execution/reservation.json').read_bytes()==(work/'reservation.json').read_bytes()
    verify_archive(archives[0])
    with pytest.raises(K.ReconciliationRequired,match='PRIOR_CONTROL_STATE'):R.execute(prep,{},**kwargs)
    assert calls==['claim','dispatch']


def test_preparation_symlink_is_rejected(prepared,tmp_path):
    candidate=prepared/'C1_PINNED_CONTEXT.json';saved=tmp_path/'saved.json';candidate.rename(saved);candidate.symlink_to(saved)
    with pytest.raises(ValueError):R.verify_preparation(prepared)


def test_archive_refuses_symlinks(tmp_path):
    prep=tmp_path/'prep';prep.mkdir();(prep/'linked').symlink_to('/etc/hostname')
    with pytest.raises(ValueError,match='symlinks'):
        archive_attempt(tmp_path/'archives',preparation=prep,governance=tmp_path/'gov',work=tmp_path/'none',
            runtime_audit=tmp_path/'none',provenance={},outcome={})


def test_production_backend_forwards_fresh_runtime_bundle(tmp_path,monkeypatch):
    from strategy_agent.research_only import development_run as DR
    observed={};bundle={'fresh_receipt':'test-only'}
    class StopProbe(Exception):pass
    def probe(*args,**kwargs):observed.update(kwargs);raise StopProbe()
    monkeypatch.setattr(DR,'session_config',probe)
    with pytest.raises(StopProbe):R.B.production_backend(root=ROOT,snapshot_root=tmp_path,session_dir=tmp_path/'session',
        git_commit='a'*40,context={},authorization_id=K.AUTH,runtime_bundle=bundle)
    assert observed['runtime_bundle'] is bundle


def test_execution_archive_includes_claim_and_session_receipt(tmp_path):
    for name in ('prep','work','audit','control','session'):
        (tmp_path/name).mkdir();(tmp_path/name/'evidence.json').write_text('{}')
    archive=archive_attempt(tmp_path/'archives',preparation=tmp_path/'prep',governance=tmp_path/'gov',
        work=tmp_path/'work',runtime_audit=tmp_path/'audit',control=tmp_path/'control',session=tmp_path/'session',
        provenance={},outcome={'status':'RECONCILIATION_REQUIRED'})
    assert {'control/evidence.json','session/evidence.json'}<=set(verify_archive(archive)['files_sha256'])


def test_prepare_cli_is_nonconsuming_with_metadata_fixture(tmp_path,staged,prepared,monkeypatch,capsys):
    import shutil, subprocess, sys
    gov=tmp_path/'governance-checkout'
    for folder in ('governance','registry'):shutil.copytree(ROOT/folder,gov/folder)
    before={p:p.read_bytes() for p in gov.rglob('*') if p.is_file()}
    def git_probe(command,**kwargs):
        if 'status' in command:return ''
        return R.GOVERNANCE_SHA if str(gov) in command else 'a'*40
    monkeypatch.setattr(subprocess,'check_output',git_probe)
    reads=[]
    monkeypatch.setattr(K.GitHubClaim,'inspect',lambda *a:reads.append('GET_ONLY'))
    def fresh_stage(archive,original,local):
        local.mkdir(parents=True)
        for name in ('runtime_binding.json','data_preflight.json','pin_summary.json'):
            (local/name).write_text('{}')
        return staged
    monkeypatch.setattr(R,'stage',fresh_stage)
    settings=tmp_path/'settings.json'
    settings.write_text(json.dumps(dict(code_sha='a'*40,mode='PREPARE',governance_root=str(gov),drive_root=str(tmp_path/'drive'),
        recovery_archive='FIXTURE_ONLY',original_preparation_archive='FIXTURE_ONLY')))
    monkeypatch.setattr(sys,'argv',['c1_runner','--settings',str(settings)])
    R.main()
    result=json.loads(capsys.readouterr().out)
    assert result['batch_consumption']==0 and result['comments_posted'] is False
    assert reads==['GET_ONLY'] and not (tmp_path/'drive/control').exists()
    assert all(p.read_bytes()==v for p,v in before.items())
    verify_archive(Path(result['preparation']).parent)


def test_expiration_is_half_open_without_new_authorization():
    with pytest.raises(ValueError,match='EXPIRED'):
        R.authority(ROOT,now=datetime.fromisoformat('2026-11-19T16:58:13+08:00'))


def test_published_notebook_pin_matches_runtime_sources():
    import subprocess
    notebook=R.read(ROOT/'notebooks/Phase5C1_Development_Execution.ipynb')
    namespace={};exec(next(''.join(c['source']) for c in notebook['cells'] if c['cell_type']=='code'),namespace)
    sha=namespace['EXECUTION_CODE_SHA']
    if sha is None:return  # pre-publication template; always stops before bootstrap
    assert len(sha)==40 and all(c in '0123456789abcdef' for c in sha)
    for name in ('c1_runner.py','c1_claim.py','c1_archive.py','c1_integration.py','b3c_run.py'):
        relative='src/strategy_agent/execution/'+name
        assert subprocess.check_output(['git','show',sha+':'+relative],cwd=ROOT)==(ROOT/relative).read_bytes()
