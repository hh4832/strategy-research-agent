"""Colab authorization administration only. No research, data reader or budget APIs.

VALIDATE_ONLY/PREPARE_APPROVAL never call the admin. The separate approve CLI is
for a human button click, after live GitHub identity and independent PR checks.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator
from strategy_agent.approval.sources import GitHubCommentSource
from strategy_agent.governance.research_authorization import (
    ResearchAuthorizationBook, gates_sha256, validate_events,
)
from strategy_agent.registry.chain import ChainedLog

ROOT = Path(__file__).resolve().parents[3]
REPO = 'hh4832/strategy-research-agent'
AUTH = 'RA-IBKR_TSM-SMA520_P5C1-0001'
CORE = '50ea3bfa42df7708dd3a1b21f17798c206be054e'
SCHEDULE = '43b6045758760a40ad88d741b1a97a73cfb0b6cf6b2a4a7f1f3b31d5085a4216'
DRAFT_SHA = 'a6615f050b62e5cc47e8e79355b345c30176a970bbb40f6c47a42343f916aad0'
INDEX_SHA = '39a7c09fe40886499ca94c008904db1bd6530708f44c39219908220589880b1a'
SOURCE = 'docs/phase5c1/authorization_source'
PROPOSAL = 'governance/proposals/'+AUTH+'.json'
TPE = ZoneInfo('Asia/Taipei')
PINS = {
    'marker_sha256':'8a980bd92045da59d573b249162ba5dce581a1c74802663b0b02cbb0756ee7a5',
    'recovery_provenance_sha256':'dddc8794c4bf52556c12d5c919e779d9bb3b7aba2770e73f34c838fe5ab8b6aa',
    'binding_sha256':'9822ad8db3bdde056c0192cb056577bdc91485af92f45feb47d1246f5c6a8f1f',
    'manifest_sha256':'edabc0abf38bab6cad013b2248b68344dc0073da105a6085dd5e8f05f85272e4',
    'lineage_sha256':'baeac6bc2e794038646ee7b313edc27038090862ea0da73cc64a87e87a9adffa',
    'parquet_sha256':'e1e7955401fcbe4a84e7486fa7b1cc8ae97a92513426086cf7902afcf958ee03',
}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as f:
        json.dump(value, f, indent=2, ensure_ascii=False)
        f.write('\n')


def git(root, *args):
    return subprocess.run(['git','-C',str(root),*args], check=True, text=True,
                          capture_output=True).stdout.strip()


def source_proposal(root=ROOT):
    root = Path(root)
    draft = root/SOURCE/'authorization.DRAFT_NOT_APPROVED.json'
    index = root/SOURCE/'ARCHIVE_INDEX.json'
    require(digest(draft)==DRAFT_SHA and draft.stat().st_size==3036, 'Original draft bytes/SHA mismatch')
    require(digest(index)==INDEX_SHA, 'Original archive index SHA mismatch')
    original = json.loads(draft.read_text())['proposal_fields']
    require(json.loads((root/PROPOSAL).read_text())==original, 'Proposal differs from original archived fields')
    return original


def archive_metadata(archive, root=ROOT):
    """Whitelisted audit metadata/cost evidence only; no snapshot or Parquet read."""
    archive = Path(archive).resolve(strict=True)
    index_path=(archive/'ARCHIVE_INDEX.json').resolve(strict=True)
    require(index_path.is_relative_to(archive) and index_path.is_file(), 'Unsafe archive index path')
    require(digest(index_path)==INDEX_SHA, 'Drive archive index differs from retained original')
    index = json.loads((archive/'ARCHIVE_INDEX.json').read_text())
    def read(relative):
        p = (archive/relative).resolve(strict=True)
        require(p.is_relative_to(archive) and p.is_file(), 'Unsafe archive metadata path')
        require(digest(p)==index['files_sha256'][relative], 'Archive metadata SHA mismatch: '+relative)
        return json.loads(p.read_text())
    original = read('notebook_output/draft/authorization.DRAFT_NOT_APPROVED.json')['proposal_fields']
    require(original==source_proposal(root), 'Drive draft differs from reviewed source')
    readiness = read('notebook_output/draft/readiness.json')
    pins = read('runtime_audit/pin_summary.json')
    preflight = read('runtime_audit/data_preflight.json')
    context = read('notebook_output/notebook_context.json')
    cfg = read('notebook_output/draft/artifacts/configs/research_only/tsm_sma520_p5c1.json')
    spec = read('notebook_output/draft/artifacts/'+original['strategy']['spec_path'])
    scenario = read('notebook_output/draft/artifacts/'+original['costs']['scenario_set_path'])
    require(index['files_sha256']['notebook_output/draft/artifacts/'+original['costs']['scenario_set_path']]==original['costs']['scenario_set_sha256'], 'Scenario-set file pin differs from proposal')
    require(index['files_sha256']['notebook_output/draft/artifacts/'+original['strategy']['spec_path']]==original['strategy']['spec_file_sha256'], 'Strategy spec file pin differs from proposal')
    schedule = read('notebook_output/draft/artifacts/'+original['costs']['dated_schedule_path'])
    # Canonical digest is different from the formatted JSON file SHA in ARCHIVE_INDEX.
    canonical = copy.deepcopy(schedule);canonical['schedule_sha256']=None
    pin = sha256(json.dumps(canonical,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    require(schedule['schedule_sha256']==pin==SCHEDULE, 'Canonical Schedule SHA mismatch')
    for row in schedule['sec_rates']+schedule['taf_rates']:
        h = row['source_document_sha256']
        matches = [p for p in index['files_sha256'] if p.startswith('notebook_output/draft/artifacts/docs/evidence/phase5c1_official_rates/sources/') and Path(p).stem==h]
        require(len(matches)==1, 'Official cost source missing/ambiguous')
        source = (archive/matches[0]).resolve(strict=True)
        require(source.is_relative_to(archive) and digest(source)==h==index['files_sha256'][matches[0]], 'Official source SHA mismatch')
    return dict(proposal=original,readiness=readiness,pins=pins,preflight=preflight,context=context,
                cfg=cfg,spec=spec,scenario=scenario,schedule=schedule)


def unsigned_errors(proposal, root=ROOT):
    """Project the official event schema onto an unsigned proposal; never invent approval evidence."""
    schema = json.loads((Path(root)/'schemas/RESEARCH_AUTHORIZATION_SCHEMA.json').read_text())
    schema.pop('oneOf');schema['$ref']='#/$defs/approve'
    event = schema['$defs']['approve']
    event['required']=[k for k in event['required'] if k not in ('action','approved_by','approval_ref','approved_at')]
    return [e.message for e in Draft202012Validator(schema).iter_errors(proposal)]


def validate(settings, root=ROOT, metadata=None, now=None):
    require(settings['mode'] in ('VALIDATE_ONLY','PREPARE_APPROVAL','APPROVE'), 'Unknown approval mode')
    require(settings['authorization_id']==AUTH and settings['lead']=='hh4832', 'Authorization/lead changed')
    require(type(settings['validity_days']) is int and settings['validity_days']==7, 'C1 reviewed validity is seven days')
    root = Path(root)
    p = source_proposal(root)
    m = archive_metadata(settings['archive'],root) if metadata is None else metadata
    issued = now or datetime.now(TPE)
    require(issued.tzinfo is not None, 'Timezone-aware approval time required')
    preview = copy.deepcopy(p)
    preview.update(issued_at=issued.astimezone(TPE).isoformat(timespec='seconds'),
                   expires_at=(issued.astimezone(TPE)+timedelta(days=7)).isoformat(timespec='seconds'))
    market = json.loads((root/'governance/market_readiness.json').read_text())['markets']['IBKR_TSM']
    r,ps,pf,cfg = m['readiness'],m['pins'],m['preflight'],m['cfg']
    checks={
        'OriginalProposal':m['proposal']==p,
        'SchemaUnsignedPreview':not unsigned_errors(preview,root),
        'SnapshotPins':ps['status']=='REVIEWED_PINS_VERIFIED' and ps['observed_pins']==PINS and r['observed_pins']==PINS,
        'Readiness':r['data_binding']=='REVIEWED_PINS_VERIFIED' and r['ready_to_submit_formal_authorization'] is True,
        'DataPreflight':pf['status']=='PASS' and len(pf['checks'])==9 and all(v=='PASS' for v in pf['checks'].values()),
        'NoResearchExecution':r['execution']=='NOT_EXECUTED' and r['consumed_batches']==0,
        'PersonalPinReviewReference':bool(m['context'].get('pin_review_evidence_ref')),
        'CorePin':r['core_reader_sha']==CORE and cfg['core']['phase4a_sha']==CORE,
        'Strategy':p['strategy']['parameters']=={'fast':5,'slow':20} and cfg['strategy']['parameters']=={'fast':5,'slow':20}
                   and m['spec']['strategy_id']==p['strategy']['strategy_id'],
        'MarketAndPosition':cfg['market']['market_id']==p['market_id']=='IBKR_TSM' and cfg['market']['symbol']==p['symbol']=='TSM'
            and m['spec']['universe']['timeframe']=='15m' and m['spec']['universe']['sessions']['allowed']==['RTH']
            and m['spec']['position_sizing']['units']==100 and m['spec']['position_sizing']['initial_capital']==100000,
        'Dataset':cfg['dataset']==p['dataset'],
        'ResearchWindow':cfg['windows']=={'warmup_start':'2016-10-19T00:00:00-04:00','start':'2017-01-01T00:00:00-05:00','end':'2025-01-01T00:00:00-05:00'},
        'DatedSchedule':p['costs']['dated_schedule_sha256']==cfg['core_costs']['dated_schedule_sha256']==m['schedule']['schedule_sha256']==SCHEDULE
                       and p['costs']['dated_schedule_path']==cfg['core_costs']['dated_schedule_path'],
        'CostScenarios':p['costs']['scenarios']==['BASE','LOW','STRESS','CAT_STRESS'] and p['costs']['model']=='IBKR_PRO_FIXED'
                        and r['cost_scenario_compatibility']['status']=='PASS',
        'ScopeAndBudget':p['scope']=='RESEARCH_ONLY_EXPLORATORY' and p['max_batches']==1 and p['executor']['executor_id']=='colab-hh4832-exec-01',
        'HoldoutNoAccess':'Holdout access' in p['does_not_authorize'] and p['development_boundary']['end']==p['research_window']['end']==p['holdout']['start']=='2025-01-01T00:00:00-05:00',
        'UnchangedFormalMarketGate':market['status']==p['formal_gate_pin']['market_status']=='NO_GO'
             and market['gate_version']==p['formal_gate_pin']['gate_version']
             and gates_sha256(market['gates'])==p['formal_gate_pin']['gates_sha256'],
    }
    book = ResearchAuthorizationBook(root/'governance/research_authorizations.jsonl',root/'governance/approvers.json')
    events = ChainedLog(root/'governance/research_authorizations.jsonl').verify()
    checks['AuthorizationContract']=not unsigned_errors(preview,root) and all(checks.values())
    checks['UnusedAuthorizationID']=not any(e['authorization_id']==AUTH for e in events) and book.get(AUTH) is None
    return {'status':'PASS' if all(checks.values()) else 'FAIL','checks':{k:'PASS' if v else 'FAIL' for k,v in checks.items()},
            'validity_preview':{'issued_at':preview['issued_at'],'expires_at':preview['expires_at'],'not_an_approval':True},
            'authorization_status':'VALIDATED_UNSIGNED' if all(checks.values()) else 'VALIDATION_FAILED',
            'authorization_id':AUTH,'formal_market_gate':'NO_GO','holdout_access':'NO_ACCESS','max_batches':1,
            'consumed_batches':0,'proposal_preview':preview,'github_pr_url':settings['pr_url']}


def check_github_payloads(repo,user,pr,settings):
    identities=json.loads((ROOT/'governance/approval_identities.json').read_text())
    require(repo['id']==identities['repository']['id'] and repo['full_name']==REPO, 'Wrong GitHub repository identity')
    require(user['login']==settings['lead']=='hh4832' and user['id']==identities['github_accounts']['hh4832'] and user['type']=='User', 'Authenticated GitHub user is not the research lead')
    require(pr['base']['repo']['id']==repo['id']==pr['head']['repo']['id'], 'PR repository/fork mismatch')
    require(pr['base']['ref']=='main' and pr['state']=='open' and not pr['merged'], 'Approval requires an open independent authorization PR targeting main')
    require(pr['head']['ref'] not in (settings['code_branch'],'feature/phase5c1-first-scientific-research')
            and pr['user']['login']=='hh4832' and pr['user']['id']==identities['github_accounts']['hh4832'] and pr['user']['type']=='User'
            and pr.get('performed_via_github_app') is None, 'Use a separate authorization PR created by the lead')
    require(pr['html_url']==settings['pr_url'], 'PR URL mismatch')
    return {'login':user['login'],'user_id':user['id'],'pr_url':pr['html_url'],'head_sha':pr['head']['sha'],
            'head_branch':pr['head']['ref'],'base_branch':pr['base']['ref'],'checked_at':datetime.now(TPE).isoformat(timespec='seconds')}


def github_identity(settings):
    match=re.fullmatch(r'https://github\.com/hh4832/strategy-research-agent/pull/([1-9][0-9]*)',settings['pr_url'])
    require(match is not None, 'Provide the actual separate authorization PR URL')
    token=os.environ.get('GITHUB_TOKEN','')
    source=GitHubCommentSource(REPO,token=token)
    require(source.live_untampered(), 'Live GitHub source required')
    return check_github_payloads(source.get_repo(),source._get('/user'),
        source._get('/repos/'+REPO+'/pulls/'+match[1]),settings)


def approve(settings, out, human_confirmation):
    """Human-only operation. Never called by validate, prepare or Notebook Run All."""
    require(settings['mode']=='APPROVE' and settings.get('confirm') is True and human_confirmation==AUTH,
            'Explicit human APPROVE + confirmation required')
    require(git(ROOT,'status','--porcelain')=='', 'Code checkout must be clean')
    require(git(ROOT,'rev-parse','HEAD')==settings['code_sha'], 'Code pin mismatch')
    first=github_identity(settings)
    report=validate(settings)
    require(report['status']=='PASS','Schema/contract/archive validation failed')
    work=Path(settings['work_root'])/'authorization-pr'
    require(not work.exists(), 'Use a new isolated authorization working tree; no retry/overwrite')
    subprocess.run(['git','clone','--branch',first['head_branch'],'--single-branch','https://github.com/'+REPO+'.git',str(work)],check=True,capture_output=True)
    require(git(work,'rev-parse','HEAD')==first['head_sha'] and git(work,'status','--porcelain')=='','PR head changed/dirty')
    # The PR must contain the reviewed compatibility code; never run arbitrary PR code.
    subprocess.run(['git','-C',str(work),'fetch','origin',settings['code_sha']],check=True,capture_output=True)
    subprocess.run(['git','-C',str(work),'fetch','origin','main:refs/remotes/origin/main'],check=True,capture_output=True)
    require(not git(work,'diff',settings['code_sha'],'origin/main','--','src','schemas','pyproject.toml'), 'Reviewed compatibility code/schema is not yet effective on main')
    require(not git(work,'diff',settings['code_sha'],'HEAD','--','src','schemas','pyproject.toml'), 'Authorization PR changes reviewed code/schema')
    changed=set(git(work,'diff','--name-only','origin/main','HEAD').splitlines())
    require(changed <= {PROPOSAL}, 'Authorization PR must change only its unsigned proposal before approval; update it to current main first')
    source=source_proposal(ROOT)
    pr_proposal=json.loads((work/PROPOSAL).read_text())
    require({k:v for k,v in pr_proposal.items() if k not in ('issued_at','expires_at')}==
            {k:v for k,v in source.items() if k not in ('issued_at','expires_at')}, 'PR proposal scope differs from archived draft')
    before=ChainedLog(work/'governance/research_authorizations.jsonl').verify()
    require(not any(e['authorization_id']==AUTH for e in before),'Authorization ID already exists in PR book')
    # Revalidate GitHub identity/head and choose actual human approval time at the click.
    second=github_identity(settings)
    require(first['head_sha']==second['head_sha'], 'PR changed while approving')
    report=validate(settings,now=datetime.now(TPE))
    require(report['status']=='PASS','Final contract check failed')
    proposal=report['proposal_preview']
    event=dict(proposal,action='APPROVE',approved_by=second['login'],approval_ref=settings['pr_url'])
    validate_events(before+[event],{second['login']})  # actual authenticated human operation, never a fake signature
    (work/PROPOSAL).write_text(json.dumps(proposal,indent=2)+'\n')
    env=dict(os.environ,PYTHONPATH=str(work/'src'))
    env.pop('GITHUB_TOKEN',None);env.pop('GH_TOKEN',None)
    subprocess.run([sys_executable(),'-m','strategy_agent.governance.research_authorization_admin','approve',
        '--proposal',str(work/PROPOSAL),'--approved-by',second['login'],'--approval-ref',settings['pr_url']],
        check=True,env=env,cwd=work,capture_output=True,text=True)
    book=ResearchAuthorizationBook(work/'governance/research_authorizations.jsonl',work/'governance/approvers.json')
    auth=book.get(AUTH)
    require(auth is not None and auth['approved_by']=='hh4832' and all(auth[k]==v for k,v in proposal.items()), 'Appended authorization differs from reviewed proposal')
    verified_events=ChainedLog(work/'governance/research_authorizations.jsonl').verify()
    require(len(verified_events)==len(before)+1, 'Unexpected book change')
    signed_event=verified_events[-1]
    require(signed_event['authorization_id']==AUTH,'Unexpected appended event')
    out=Path(out)
    save(out/'authorization_review.json',{'status':'LOCAL_APPROVED_PENDING_PR_SYNC','github_identity':second,
        'authorization':signed_event,'effective':False,'required_next_action':'Human must commit/push these exact files to the independent PR, review and merge it; then verify the merged book before B3-C preparation.'})
    # Exact, explicit whitelist: never copy git configuration, environment variables or tokens.
    for relative in (PROPOSAL,'governance/research_authorizations.jsonl'):
        dest=out/'human_pr_sync'/relative;dest.parent.mkdir(parents=True,exist_ok=True)
        with (work/relative).open('rb') as r,dest.open('xb') as w:w.write(r.read())
    patch=git(work,'diff','--',PROPOSAL,'governance/research_authorizations.jsonl')
    with (out/'human_pr_sync.patch').open('x') as f:f.write(patch+'\n')
    return {'status':'LOCAL_APPROVED_PENDING_PR_SYNC','effective':False,'work_root':str(work)}


def verify_merged(settings, out):
    """Read-only verification of the exact human-approved event after PR synchronization/merge."""
    match=re.fullmatch(r'https://github\.com/hh4832/strategy-research-agent/pull/([1-9][0-9]*)',settings['pr_url'])
    require(match is not None, 'Actual authorization PR URL required')
    source=GitHubCommentSource(REPO)
    require(source.live_untampered(), 'Live GitHub source required')
    repo=source.get_repo()
    identities=json.loads((ROOT/'governance/approval_identities.json').read_text())
    require(repo['id']==identities['repository']['id'] and repo['full_name']==REPO,'Repository identity mismatch')
    pr=source._get('/repos/'+REPO+'/pulls/'+match[1])
    require(pr['merged'] is True and pr['base']['ref']=='main' and pr['base']['repo']['id']==repo['id'], 'Authorization PR has not merged into main')
    require(pr['merged_by']['login']=='hh4832' and pr['merged_by']['id']==identities['github_accounts']['hh4832'], 'Research lead merge confirmation missing')
    review_path=Path(out)/'authorization_review.json'
    expected=settings.get('expected_record_sha256','')
    if review_path.is_file():
        review=json.loads(review_path.read_text())
        expected=review.get('authorization',{}).get('record_sha256',expected)
    require(re.fullmatch('[0-9a-f]{64}',expected or '') is not None,'Exact locally approved record SHA is required to verify synchronization')
    current_main=source._get('/repos/'+REPO+'/git/ref/heads/main')['object']['sha']
    checkout=Path(settings['work_root'])/('merged-governance-'+current_main)
    require(not checkout.exists(),'Merged verification output must be new')
    subprocess.run(['git','clone','https://github.com/'+REPO+'.git',str(checkout)],check=True,capture_output=True)
    subprocess.run(['git','-C',str(checkout),'checkout','--detach',current_main],check=True,capture_output=True)
    subprocess.run(['git','-C',str(checkout),'merge-base','--is-ancestor',pr['merge_commit_sha'],'HEAD'],check=True,capture_output=True)
    require(git(checkout,'status','--porcelain')=='','Dirty merged governance checkout')
    book=ResearchAuthorizationBook(checkout/'governance/research_authorizations.jsonl',checkout/'governance/approvers.json')
    auth=book.get(AUTH)
    events=ChainedLog(checkout/'governance/research_authorizations.jsonl').verify()
    matching=[e for e in events if e['authorization_id']==AUTH and e['action']=='APPROVE']
    require(auth is not None and len(matching)==1 and matching[0]['record_sha256']==expected,'Merged event does not match the exact approved event')
    original=source_proposal(ROOT)
    require(all(auth[k]==v for k,v in original.items() if k not in ('issued_at','expires_at')), 'Merged authorization scope changed')
    require(auth['approved_by']=='hh4832' and auth['approval_ref']==settings['pr_url'],'Merged approver/reference mismatch')
    now=datetime.now(TPE)
    require(datetime.fromisoformat(auth['issued_at'])<=now<datetime.fromisoformat(auth['expires_at']),'Authorization is not currently valid')
    require(datetime.fromisoformat(auth['expires_at'])-datetime.fromisoformat(auth['issued_at'])==timedelta(days=7),'Validity changed')
    market=json.loads((checkout/'governance/market_readiness.json').read_text())['markets']['IBKR_TSM']
    require(market['status']=='NO_GO' and market['gate_version']==auth['formal_gate_pin']['gate_version'] and gates_sha256(market['gates'])==auth['formal_gate_pin']['gates_sha256'],'Formal gates changed')
    uses=ChainedLog(checkout/'registry/research_authorization_uses.jsonl').verify()
    require(not any(e['authorization_id']==AUTH for e in uses),'Authorization budget is already used')
    require(source._get('/repos/'+REPO+'/git/ref/heads/main')['object']['sha']==current_main,'main changed during merged verification; inspect and retry read-only verification in a new directory')
    result={'status':'AUTHORIZATION_APPROVED_EFFECTIVE','authorization_id':AUTH,'record_sha256':expected,
        'github_pr_url':settings['pr_url'],'governance_commit':git(checkout,'rev-parse','HEAD'),
        'formal_market_gate':'NO_GO','holdout_access':'NO_ACCESS','max_batches':1,'strategy_execution':'NOT_EXECUTED'}
    save(Path(out)/'merged_verification.json',result)
    return result


def sys_executable():
    import sys
    return sys.executable


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('validate','prepare','approve','github','verify-merged'))
    parser.add_argument('--settings',required=True,type=Path)
    parser.add_argument('--out',required=True,type=Path)
    parser.add_argument('--human-confirmation',default='')
    a=parser.parse_args(argv);settings=json.loads(a.settings.read_text())
    if a.command=='verify-merged':
        print(json.dumps(verify_merged(settings,a.out)));return
    if a.command=='github':
        try:
            verification=dict(status='PASS',**github_identity(settings))
        except Exception as error:
            verification={'status':'FAIL','error':str(error)}
        save(a.out/'github_verification.json',verification)
        require(verification['status']=='PASS','GitHub identity/PR verification failed; approval blocked')
        return
    if a.command=='approve':
        result=approve(settings,a.out,a.human_confirmation);print(json.dumps(result));return
    require(settings['mode']!='APPROVE' or a.command=='validate','Prepare never performs approval')
    try:
        report=validate(settings)
    except Exception as error:
        report={'status':'FAIL','checks':{'ArchiveAndContract':'FAIL'},'error':str(error),
            'validity_preview':{'not_an_approval':True},'authorization_status':'VALIDATION_FAILED',
            'github_pr_url':settings['pr_url']}
    save(a.out/'authorization_validation.json',report)
    save(a.out/'authorization_review.json',{'status':'NOT_APPROVED','mode':settings['mode'],'effective':False,
        'github_pr_url':settings['pr_url'],'validity_preview':report['validity_preview']})
    if a.command=='prepare' and report['status']=='PASS':
        save(a.out/'proposal.UNSIGNED.PREVIEW.json',report['proposal_preview'])
    print(json.dumps({'status':report['status'],'checks':report['checks'],'authorization_status':report['authorization_status']},indent=2))
    require(report['status']=='PASS','Validation failed; no authorization written')


if __name__=='__main__':
    main()
