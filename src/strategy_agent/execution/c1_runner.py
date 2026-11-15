"""C1-specific non-consuming preparation and guarded one-attempt execution composition.

Only execute() can reach B3-C dispatch. It requires live personal evidence, a server-side exclusive
claim and explicit human confirmations. This module never signs, commits, pushes, merges or retries.
"""
from __future__ import annotations

import copy
from datetime import datetime,timedelta
import hashlib
import json
from pathlib import Path

from strategy_agent.execution import c1_integration as C, b3c_run as B, identity as I
from strategy_agent.execution.c1_archive import archive_attempt, verify_archive
from strategy_agent.execution.c1_claim import GitHubClaim, ReconciliationRequired, AUTH, REF
from strategy_agent.governance.research_authorization import gates_sha256
from strategy_agent.registry.chain import ChainedLog
from strategy_agent.research_only.runner import canonical_sha
from strategy_agent.readiness.c1_authorization import PINS

GOVERNANCE_SHA='df56664a12a54762e222f141b50e337c6dfc5088'
RECORD_SHA='567dafcc44ba0300ac7504b56bdf3aafc46f420e26423e14be633429dbc30d26'
CHECKS={'Manifest','ParquetSHA256','IntegrityReceipt','ArrowSchema','RowGroupBoundaries',
        'HoldoutIsolation','CoreNativeLoader','1mTo15m','PITTemporalProtection'}


def read(path):
    path=Path(path)
    if path.is_symlink():raise ValueError('symlink input refused')
    return json.loads(path.read_text())


def unused(root):
    """No consumed slot, reservation (even released), prior binding or corrupt settlement is retryable."""
    from strategy_agent.execution.budget import _verify,_fold
    root=Path(root)
    try:
        uses=ChainedLog(root/'registry/research_authorization_uses.jsonl').verify()
        bindings=ChainedLog(root/'registry/authorization_bindings.jsonl').verify()
        path=root/'registry/research_budget_events.jsonl'
        events=[json.loads(x) for x in path.read_text().splitlines() if x.strip()] if path.exists() else []
        _verify(events,path)
        reserved=[r for r in _fold(events).values() if r.get('authorization_id')==AUTH]
        if reserved or any(e.get('authorization_id')==AUTH for e in uses+bindings):
            raise ReconciliationRequired('PRIOR_C1_STATE; no automatic retry')
    except ReconciliationRequired:raise
    except Exception:raise ReconciliationRequired('C1_SETTLEMENT_UNVERIFIABLE') from None


def authority(root, *, now=None):
    auth=B._authorization(Path(root),AUTH)
    records=ChainedLog(Path(root)/'governance/research_authorizations.jsonl').verify()
    found=[e for e in records if e.get('authorization_id')==AUTH]
    if len(found)!=1 or found[0]['record_sha256']!=RECORD_SHA:
        raise ValueError('C1_APPROVED_RECORD_MISMATCH')
    now=now or datetime.now(B.TPE)
    if not datetime.fromisoformat(auth['issued_at'])<=now<datetime.fromisoformat(auth['expires_at']):
        raise ValueError('C1_AUTHORIZATION_EXPIRED_OR_NOT_ISSUED')
    if datetime.fromisoformat(auth['expires_at'])-datetime.fromisoformat(auth['issued_at'])!=timedelta(days=7):
        raise ValueError('C1_VALIDITY_CHANGED')
    unused(root)
    return auth


def validate_data_report(report):
    if (report.get('status')!='PASS' or set(report.get('checks',{}))!=CHECKS
            or any(v!='PASS' for v in report['checks'].values()) or report.get('output_holdout_rows')!=0
            or report.get('batch_consumption')!=0 or report.get('strategy_run') is not False):
        raise ValueError('FRESH_C1_DATA_PREFLIGHT_REQUIRED')


def stage(archive, original_preparation_archive, local, *, code_root=C.ROOT):
    """Fresh Runtime: verified original metadata, exact reviewed pins, receipt renewal, full Core PIT preflight."""
    from strategy_agent.readiness.c1_authorization import archive_metadata
    metadata=archive_metadata(original_preparation_archive,code_root)
    if not metadata['context'].get('pin_review_evidence_ref') or metadata['pins']['observed_pins']!=PINS:
        raise ValueError('PERSONAL_DATA_PIN_REVIEW_REQUIRED')
    provenance=read(Path(archive)/'recovery_provenance.json')
    inspected=C.inspect_archive(archive,conversion_approval=provenance['conversion_approval'],trusted_pins=PINS)
    staged=C.stage_archive(inspected,local)
    validate_data_report(staged['preflight'])
    return staged


def readiness_manifest(auth, config, artifact_root, staged, *, governance_root, measured_core_sha):
    """A10 evidence from independently measured artifacts + fresh Core preflight, not the old B3-A candidate."""
    artifact=Path(artifact_root)
    validate_data_report(staged['preflight'])
    if staged['inspection']['status']!='REVIEWED_PINS_VERIFIED' or staged['inspection']['observed_pins']!=PINS:
        raise ValueError('C1_REVIEWED_DATA_PINS_REQUIRED')
    if config['provenance']['config_sha256']!=canonical_sha(config):raise ValueError('C1_CONFIG_SELF_HASH')
    spec=artifact/config['strategy']['spec_path'];cost=artifact/config['costs']['scenario_set_path']
    if (C.sha(spec)!=auth['strategy']['spec_file_sha256'] or config['strategy']['spec_file_sha256']!=C.sha(spec)
            or any(config['strategy'].get(k)!=v for k,v in auth['strategy'].items()) or config['dataset']!=auth['dataset']
            or tuple(config['costs']['scenarios']+config['costs']['sensitivity'])!=C.SCENARIOS
            or config['costs']['scenario_set_path']!=auth['costs']['scenario_set_path']
            or config['costs']['scenario_set_sha256']!=auth['costs']['scenario_set_sha256']
            or config['authorization_id']!=AUTH or config['windows']!=C.WINDOW
            or config['core']['phase4a_sha']!=measured_core_sha or measured_core_sha!=C.CORE
            or config['snapshot_binding']['manifest_sha256']!=PINS['manifest_sha256']
            or staged['bundle']['manifest_sha256']!=PINS['manifest_sha256']
            or config['snapshot_binding']['snapshot_pin']!=staged['bundle']['snapshot_pin']
            or staged['bundle']['snapshot_pin']['snapshot_id']!=auth['dataset']['snapshot_id']
            or staged['bundle']['snapshot_pin']['producer_sha']!=auth['dataset']['producer_commit_sha']
            or any(config['temporal_policy'].get(k)!=v for k,v in auth['temporal_policy'].items())
            or config['temporal_policy'].get('source')!='CORE_POLICY'
            or config['core_costs'].get('dated_schedule_path')!=auth['costs']['dated_schedule_path']
            or config['core_costs'].get('dated_schedule_sha256')!=auth['costs']['dated_schedule_sha256']
            or C.sha(cost)!=auth['costs']['scenario_set_sha256']):
        raise ValueError('C1_ARTIFACT_AUTHORIZATION_MISMATCH')
    market=read(Path(governance_root)/'governance/market_readiness.json')['markets']['IBKR_TSM']
    pin=auth['formal_gate_pin']
    if (market['status']!='NO_GO' or market['gate_version']!=pin['gate_version']
            or gates_sha256(market['gates'])!=pin['gates_sha256']):raise ValueError('C1_FORMAL_GATE_CHANGED')
    schedules=C.load_dated_schedules(artifact)
    costs=C.validate_cost_scenario_compatibility(artifact,schedules)
    values={'strategy_spec_sha256':C.sha(spec),'dataset_snapshot_id':config['dataset']['snapshot_id'],
        'dataset_manifest_sha256':staged['bundle']['manifest_sha256'],'core_commit_sha':measured_core_sha,
        'trading_cost_profile':{'scenario_set_sha256':C.sha(cost)},
        'temporal_policy':auth['temporal_policy']['sha256'],'holdout_exclusion':{'access':'NO_ACCESS'},
        'formal_market_gate':'NO_GO'}
    return {'kind':'PHASE5C1_PRODUCTION_READINESS_MANIFEST',
        'fields':{k:{'value':v,'status':'RESOLVED','category':None} for k,v in values.items()},
        'cross_checks':{'fresh_data':{'status':'PASS'},'artifact_authorization':{'status':'PASS'},
                        'dated_costs':{'status':costs['status']}}}


def core_sha():
    from strategy_agent.adapters.mrc_phase4a import core_development
    identity=core_development()[1].core_code_identity()
    if identity['core_tree_dirty']:raise ValueError('DIRTY_CORE')
    return identity['core_git_sha']


def seal_preparation(root):
    root=Path(root)
    files={p.relative_to(root).as_posix():C.sha(p) for p in root.rglob('*') if p.is_file()}
    C.save(root/'C1_PREPARATION_INDEX.json',{'files_sha256':files})


def verify_preparation(root):
    root=Path(root);index=read(root/'C1_PREPARATION_INDEX.json')['files_sha256']
    actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p.name!='C1_PREPARATION_INDEX.json'}
    if actual!=set(index):raise ValueError('C1_PREPARATION_INVENTORY_CHANGED')
    for relative,h in index.items():
        candidate=root/relative
        if any(p.is_symlink() for p in (candidate,*candidate.parents) if p.is_relative_to(root)):
            raise ValueError('C1_PREPARATION_SYMLINK')
        p=candidate.resolve(strict=True)
        if not p.is_relative_to(root.resolve()) or C.sha(p)!=h:raise ValueError('C1_PREPARATION_CHANGED')
    return index


def prepare(staged, destination, *, code_sha, governance_root, code_root=C.ROOT):
    """Only PREPARE, request and exact personal approval lines; no signer, binding, remote claim or reservation."""
    auth=authority(governance_root)
    before={p:p.read_bytes() for folder in ('governance','registry') for p in (Path(governance_root)/folder).rglob('*') if p.is_file()}
    root=Path(destination)
    C.prepare(staged,root,agent_commit=code_sha,root=code_root)
    artifact=root/'artifacts';config=read(artifact/C.CONFIG)
    manifest=readiness_manifest(auth,config,artifact,staged,governance_root=governance_root,measured_core_sha=core_sha())
    C.save(root/'C1_READINESS.json',manifest)
    prepared=B.prepare(root/'governed_request',artifact_root=artifact,governance_root=Path(governance_root),
        authorization_id=AUTH,canonical_config=artifact/C.CONFIG,agent_commit=code_sha,
        completion=read(root/'completion.json'),hypothesis=read(root/'proposal.json')['draft']['hypothesis']['question'],
        adapter_id='CLAUDE_AUTHORED_P5C1_COMPLETION_FILE',manifest_kind='PHASE5C1_PREPARE_MANIFEST')
    from strategy_agent.orchestration.governance import Governance
    from strategy_agent.binding.service import REQUIRED_GOVERNANCE
    _,source=B.load_prepared(root/'governed_request',governance_root=governance_root)
    bound=dict(copy.deepcopy(source),authorization_ref={'authorization_id':AUTH})
    decision=Governance(artifact,governance_root=governance_root).check(bound,binding_check=False)
    checks={item['id']:item['status'] for item in decision.checks}
    if any(checks.get(key)!='PASS' for key in REQUIRED_GOVERNANCE):
        raise ValueError('C1_PREPARE_GOVERNANCE_BLOCKED:'+json.dumps(decision.as_dict()))
    # Governance.consumes_budget describes a future dispatch, not this read-only check.
    C.save(root/'C1_PRE_BIND_GOVERNANCE.json',{'operation':'READ_ONLY_PRE_BIND_CHECK',
        'batch_consumption':0,'decision':decision.as_dict()})
    C.save(root/'C1_PINNED_CONTEXT.json',{'code_sha':code_sha,'governance_sha':GOVERNANCE_SHA,'core_sha':C.CORE,
        'authorization_record_sha256':RECORD_SHA,'authorization_id':AUTH,'request_sha256':prepared['bound_request_sha256'],
        'batch':1,'batch_consumed':0,'strategy_execution':'NOT_EXECUTED'})
    if any(p.read_bytes()!=raw for p,raw in before.items()):raise ValueError('PREPARE_CHANGED_GOVERNANCE')
    seal_preparation(root)
    return prepared


def preflight(preparation, staged, *, governance_root, code_sha):
    verify_preparation(preparation)
    root=Path(preparation);context=read(root/'C1_PINNED_CONTEXT.json')
    if context['code_sha']!=code_sha or context['governance_sha']!=GOVERNANCE_SHA or context['core_sha']!=C.CORE:
        raise ValueError('C1_CODE_OR_GOVERNANCE_PIN_CHANGED')
    from strategy_agent.execution.c1_benchmark import verify
    verify(root/'artifacts')
    auth=authority(governance_root)
    manifest=readiness_manifest(auth,read(root/'artifacts'/C.CONFIG),root/'artifacts',staged,
                                governance_root=governance_root,measured_core_sha=core_sha())
    prepared,source=B.load_prepared(root/'governed_request',governance_root=governance_root)
    if context['request_sha256']!=prepared['bound_request_sha256']:raise ValueError('C1_REQUEST_CHANGED')
    if I.authorization_problems(source,auth) or I.artefact_problems(source,root/'artifacts'):
        raise ValueError('C1_REQUEST_SCOPE_CHANGED')
    from strategy_agent.execution.activation import RealActivationGate
    gate=RealActivationGate(governance_root=governance_root,artifact_root=root/'artifacts',executor_id=B.EXECUTOR,
                            production_books=True,readiness_manifest=lambda:manifest,authorization_id=AUTH)
    bound=dict(copy.deepcopy(source),authorization_ref={'authorization_id':AUTH})
    ok,info=gate._readiness(auth,I.request_identity(bound))
    if not ok:raise ValueError('C1_A10_BLOCKED:'+json.dumps(info))
    return prepared,source,manifest


def verify_settlement(root, request_sha):
    from strategy_agent.execution.budget import _verify,_fold
    root=Path(root)
    uses=[e for e in ChainedLog(root/'registry/research_authorization_uses.jsonl').verify() if e.get('authorization_id')==AUTH]
    path=root/'registry/research_budget_events.jsonl'
    events=[json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    _verify(events,path)
    reservations=[r for r in _fold(events).values() if r.get('authorization_id')==AUTH]
    if (len(uses)!=1 or len(reservations)!=1 or reservations[0]['state']!='CONSUMED'
            or reservations[0]['request_sha256']!=request_sha):
        raise ReconciliationRequired('C1_SETTLEMENT_NOT_PROVEN')


def execution_preflight(preparation,staged,*,governance_root,code_sha,session_dir,bind_url,task_url,verifier):
    """Non-consuming live BIND dry run, personal TASK evidence, fresh backend and unchanged A10."""
    prepared,source,manifest=preflight(preparation,staged,governance_root=governance_root,code_sha=code_sha)
    from strategy_agent.approval import Expectation
    from strategy_agent.approval.tokens import scope_sha256
    from strategy_agent.orchestration.request import request_hash
    bound=dict(copy.deepcopy(source),authorization_ref={'authorization_id':AUTH})
    verifier.verify(bind_url,Expectation(intent='BIND_AUTHORIZATION',authorization_id=AUTH,
        request_sha256=request_hash(source),scope_sha256=scope_sha256(source)))
    verifier.verify(task_url,Expectation.for_request(bound,intent='APPROVE_TASK',task_id=prepared['task_id']))
    if bind_url==task_url:raise ValueError('TWO_DISTINCT_PERSONAL_COMMENTS_REQUIRED')
    root=Path(preparation)
    backend=B.production_backend(root=Path(governance_root),runner_root=root/'artifacts',governance_root=Path(governance_root),
        snapshot_root=Path(staged['local_root']),session_dir=Path(session_dir),git_commit=code_sha,
        canonical_config=root/'artifacts'/C.CONFIG,authorization_id=AUTH,
        runtime_bundle=staged['bundle'],context={'notebook':'PHASE5C1_FINAL_EXECUTION','schedule_sha256':C.SCHEDULE_SHA256})
    if backend.precheck(bound):raise ValueError('C1_BACKEND_PREFLIGHT_BLOCKED')
    from strategy_agent.binding.service import AuthorizationBindingService
    dry=AuthorizationBindingService(verifier=verifier,governance_root=governance_root,root=root/'artifacts').bind(
        dry_run=True,source_request_path=root/'governed_request/request.json',
        provenance_path=root/'governed_request/request_provenance.json',proposal_path=root/'governed_request/proposal.json',
        authorization_id=AUTH,evidence_url=bind_url)
    identity,failures=backend.execution_identity()
    if failures or I.compare(I.request_identity(bound),identity,source='BACKEND'):
        raise ValueError('C1_BACKEND_IDENTITY_MISMATCH')
    if dry['would_write']['bound_request_sha256']!=prepared['bound_request_sha256']:
        raise ValueError('C1_BINDING_REQUEST_MISMATCH')
    return prepared,source,manifest,backend


def execute(preparation, staged, *, governance_root, code_sha, claim_client, drive_control,
            archive_parent, runtime_audit, work_dir, session_dir, bind_url, task_url, verifier,
            confirm_authorization='', confirm_remote_claim=False):
    """Human EXECUTE entry. Never called by PREPARE/PREFLIGHT. No retry on any failure."""
    if confirm_authorization!=AUTH or confirm_remote_claim is not True:
        raise ValueError('EXPLICIT_HUMAN_EXECUTION_AND_REMOTE_CLAIM_CONFIRMATIONS_REQUIRED')
    from strategy_agent.orchestration.governance import is_isolated_test_root
    isolated=is_isolated_test_root(governance_root)
    if not isolated and (not isinstance(claim_client,GitHubClaim) or not claim_client.production or not verifier.production):
        raise ValueError('LIVE_PRODUCTION_CLAIM_AND_APPROVAL_SOURCES_REQUIRED')
    control=Path(drive_control)/AUTH/'batch-1'
    if control.exists():raise ReconciliationRequired('PRIOR_CONTROL_STATE; inspect, never retry')
    prepared,source,manifest=preflight(preparation,staged,governance_root=governance_root,code_sha=code_sha)
    prepared,source,manifest,backend=execution_preflight(preparation,staged,governance_root=governance_root,
        code_sha=code_sha,session_dir=session_dir,bind_url=bind_url,task_url=task_url,verifier=verifier)
    root=Path(preparation)
    # Creating a local marker is NOT the global lock. The server ref is the sole authoritative atomic winner.
    try:control.mkdir(parents=True,exist_ok=False)
    except FileExistsError:raise ReconciliationRequired('CONCURRENT_OR_PREVIOUS_CONTROL_STATE') from None
    provenance={'code_sha':code_sha,'governance_sha':GOVERNANCE_SHA,'core_sha':C.CORE,
        'authorization_id':AUTH,'authorization_record_sha256':RECORD_SHA,
        'request_sha256':prepared['bound_request_sha256'],'claim_ref':REF}
    outcome={'status':'RECONCILIATION_REQUIRED','batch_consumption':'UNKNOWN_UNTIL_LEDGER_RECONCILED'}
    try:
        proof=claim_client.acquire(governance_root=Path(governance_root),governance_sha=GOVERNANCE_SHA,
                                   request_sha=prepared['bound_request_sha256'])
        C.save(control/'REMOTE_CLAIM.json',proof.record)
        result=B.execute(root/'governed_request',bind_url=bind_url,task_url=task_url,verifier=verifier,backend=backend,
            work_dir=Path(work_dir),claim_dir=control,artifact_root=root/'artifacts',governance_root=Path(governance_root),
            executor_id=B.EXECUTOR,production_books=not isolated,readiness_manifest=lambda:manifest,external_claim=proof)
        if not result.get('settled') or not result.get('execution_record'):
            raise ReconciliationRequired('C1_ATTEMPT_NOT_SETTLED')
        record_path=Path(result['execution_record']).resolve(strict=True)
        if not record_path.is_relative_to(Path(work_dir).resolve()):raise ValueError('EXECUTION_RECORD_OUTSIDE_ARCHIVED_WORK')
        record=read(record_path)
        for key in ('runner_archive','task_archive','runner_receipt_file'):
            if not Path(record[key]).resolve(strict=True).is_relative_to(Path(work_dir).resolve()):
                raise ValueError('RESULT_OUTSIDE_ARCHIVED_WORK')
        verify_settlement(governance_root,prepared['bound_request_sha256'])
        outcome={'status':'SETTLED_PENDING_HUMAN_RESULT_REVIEW','result':result,'batch_consumption':1}
        return outcome
    except BaseException as error:
        # Do not serialize exception text, settings, environment or credentials.
        outcome['error_type']=type(error).__name__
        raise ReconciliationRequired('C1_ATTEMPT_INTERRUPTED; inspect immutable archive and server claim; never retry') from None
    finally:
        try:
            archived=archive_attempt(archive_parent,preparation=root,governance=governance_root,work=work_dir,
                                    runtime_audit=runtime_audit,provenance=provenance,outcome=outcome,control=control,session=session_dir)
            import uuid
            C.save(control/('ARCHIVE_REFERENCE_'+uuid.uuid4().hex+'.json'),{'archive':str(archived),'outcome':outcome['status']})
            outcome['archive']=str(archived)
        except BaseException:
            raise ReconciliationRequired('ARCHIVE_OR_CHECKPOINT_INCOMPLETE; server claim stays claimed; no retry') from None


def main():
    """Notebook entry; default PREPARE. Settings contain paths/pins, never credentials."""
    import argparse, subprocess, tempfile, shutil
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settings',required=True)
    args=parser.parse_args();settings=read(args.settings)
    code_sha=settings['code_sha'];mode=settings.get('mode','PREPARE')
    if len(code_sha)!=40 or any(c not in '0123456789abcdef' for c in code_sha):
        raise ValueError('PUBLISHED_REVIEWED_FULL_CODE_SHA_REQUIRED')
    def git(root,*args):
        return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()
    if git(C.ROOT,'rev-parse','HEAD')!=code_sha or git(C.ROOT,'status','--porcelain','--untracked-files=all'):
        raise ValueError('EXECUTION_CODE_PIN_OR_TREE_MISMATCH')
    gov=Path(settings['governance_root'])
    if git(gov,'rev-parse','HEAD')!=GOVERNANCE_SHA or git(gov,'status','--porcelain','--untracked-files=all'):
        raise ValueError('GOVERNANCE_PIN_OR_TREE_MISMATCH')
    if mode not in ('PREPARE','PREFLIGHT','EXECUTE'):raise ValueError('UNKNOWN_MODE')
    authority(gov)
    claim=GitHubClaim()
    claim.inspect(gov,GOVERNANCE_SHA)  # GET only; stops on prior remote claim.
    parent=Path(settings['drive_root'])
    if any((parent/folder/AUTH/'batch-1').exists() for folder in ('control','attempts')):
        raise ReconciliationRequired('PRIOR_CONTROL_OR_DURABLE_ATTEMPT; manual reconciliation required')
    local=Path(tempfile.mkdtemp(prefix='c1-runtime-'))
    staged=stage(settings['recovery_archive'],settings['original_preparation_archive'],local/'staging')
    audit=local/'audit';audit.mkdir()
    for name in ('runtime_binding.json','data_preflight.json','pin_summary.json'):
        shutil.copyfile(local/'staging'/name,audit/name)
    provenance={'code_sha':code_sha,'governance_sha':GOVERNANCE_SHA,'core_sha':C.CORE,'mode':mode,
                'authorization_id':AUTH,'strategy_execution':'NOT_EXECUTED' if mode!='EXECUTE' else 'HUMAN_REQUESTED'}
    if mode=='PREPARE':
        from strategy_agent.execution.c1_benchmark import archive
        benchmark_archive=archive(parent,execution_sha=code_sha)
        prepared=prepare(staged,local/'preparation',code_sha=code_sha,governance_root=gov)
        archived=archive_attempt(parent/'archives',preparation=local/'preparation',governance=gov,
            work=local/'unused',runtime_audit=audit,provenance=provenance,
            outcome={'status':'PREPARED_UNSIGNED_TASK','batch_consumption':0})
        print(json.dumps({'preparation':str(archived/'preparation'),'benchmark_archive':str(benchmark_archive),
            'task_id':prepared['task_id'],'bound_request_sha256':prepared['bound_request_sha256'],
            'BIND_AUTHORIZATION':prepared['BIND_AUTHORIZATION'],'APPROVE_TASK':prepared['APPROVE_TASK'],
            'comments_posted':False,'batch_consumption':0},indent=2));return
    from strategy_agent.approval import production_verifier
    verifier=production_verifier(revocation_ledger=audit/'approval_revocations.jsonl',
        identities_path=gov/'governance/approval_identities.json',approvers_path=gov/'governance/approvers.json')
    kw=dict(governance_root=gov,code_sha=code_sha,session_dir=local/'session',
            bind_url=settings['bind_url'],task_url=settings['task_url'],verifier=verifier)
    if mode=='PREFLIGHT':
        execution_preflight(settings['preparation'],staged,**kw)
        archived=archive_attempt(parent/'archives',preparation=settings['preparation'],governance=gov,
            work=local/'unused',runtime_audit=audit,provenance=provenance,
            outcome={'status':'NONCONSUMING_EXECUTION_PREFLIGHT_PASS','batch_consumption':0})
        print(json.dumps({'status':'PREFLIGHT_PASS','archive':str(archived),'batch_consumption':0}));return
    # Persist mutable execution books and partial results on Drive BEFORE any remote claim.
    # A hard Runtime loss cannot run finally; the durable workspace remains for reconciliation.
    durable=parent/'attempts'/AUTH/'batch-1'
    durable.mkdir(parents=True,exist_ok=False)
    for folder in ('governance','registry'):
        shutil.copytree(gov/folder,durable/'state'/folder)
    shutil.copytree(audit,durable/'audit')
    kw.update(governance_root=durable/'state',session_dir=durable/'session',
        verifier=production_verifier(revocation_ledger=durable/'audit/approval_revocations.jsonl',
            identities_path=durable/'state/governance/approval_identities.json',
            approvers_path=durable/'state/governance/approvers.json'))
    result=execute(settings['preparation'],staged,**kw,claim_client=claim,drive_control=parent/'control',
        archive_parent=parent/'archives',runtime_audit=durable/'audit',work_dir=durable/'work',
        confirm_authorization=settings.get('confirm_authorization',''),
        confirm_remote_claim=settings.get('confirm_remote_claim',False))
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
