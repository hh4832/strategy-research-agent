"""Synthetic identity/launcher tests only; no real data, approval, claim or execution."""
import copy
from datetime import datetime
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from strategy_agent.execution import c1_claim as K, c1_runner as R
from strategy_agent.execution.c1_context import CURRENT, ExecutionContext, OLD_AUTH, recovery_context, claim_ref
from strategy_agent.execution.c1_recovery import prepare_packet
from strategy_agent.execution.c1_observe import run_logged
from strategy_agent.governance.authorization_contracts import contract_id, C1_RECOVERY, EVENT_FIELDS
from strategy_agent.registry.chain import ChainedLog
from test_c1_execution_runner import staged, transport
from _c1_fixed_clock import c1_fixed_clock  # noqa: F401  (autouse: RA02 window, see module)

ROOT=Path(__file__).parents[1]


def pre_recovery_books(target):
    """Disposable historical prefix: these tests themselves introduce the new approval.

    A real approved recovery record may now exist in ROOT; never append a duplicate to that snapshot.
    """
    for folder in ('governance','registry'):shutil.copytree(ROOT/folder,target/folder)
    book=target/'governance/research_authorizations.jsonl'
    lines=book.read_bytes().splitlines(keepends=True)
    end=next(i for i,line in enumerate(lines) if json.loads(line).get('authorization_id')==OLD_AUTH)
    book.write_bytes(b''.join(lines[:end+1]))
    rows=ChainedLog(book).verify()
    assert rows[-1]['authorization_id']==OLD_AUTH
    assert not any(r.get('authorization_id')==C1_RECOVERY for r in rows)
    # The real registry may now also hold the settled recovery execution: cut each ledger to its true pre-recovery
    # chain prefix and drop the recovery's content-addressed files (they appear only after the recovery binding).
    for name in ('research_authorization_uses.jsonl','research_budget_events.jsonl','authorization_bindings.jsonl'):
        led=target/'registry'/name
        if led.is_file():
            lines=led.read_bytes().splitlines(keepends=True)
            cut=next((i for i,line in enumerate(lines) if json.loads(line).get('authorization_id')==C1_RECOVERY),len(lines))
            for line in lines[cut:]:
                rec=json.loads(line)
                for key in ('bound_request_path','approval_evidence_path'):
                    if rec.get(key):(target/rec[key]).unlink(missing_ok=True)
            led.write_bytes(b''.join(lines[:cut]))
            assert not any(r.get('authorization_id')==C1_RECOVERY for r in ChainedLog(led).verify())


def test_unsigned_packet_has_no_inherited_approval_or_reused_subject(tmp_path):
    sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    out=prepare_packet(ROOT,tmp_path,sha)
    draft=json.loads((Path(out['output'])/'authorization.DRAFT_NOT_APPROVED.json').read_text())
    assert not (set(draft)&(EVENT_FIELDS-{'issued_at','expires_at'}))
    assert draft['authorization_id']==C1_RECOVERY and draft['proposal_status']=='DRAFT_NOT_APPROVED'
    plan=json.loads((Path(out['output'])/'recovery_plan.json').read_text())
    assert plan['request_sha256'] is None and plan['task_id'] is None
    assert plan['governance_sha'] is None and not plan['execute_go']
    assert plan['claim_ref']!=claim_ref(OLD_AUTH)
    assert plan['previous_attempt_disclosure']['performance_visibility']=='UNKNOWN'


def test_new_contract_preserves_every_scientific_scope_field():
    old=next(json.loads(l) for l in (ROOT/'governance/research_authorizations.jsonl').read_text().splitlines()
             if json.loads(l).get('authorization_id')==OLD_AUTH)
    new={**old,'authorization_id':C1_RECOVERY}
    assert contract_id(new)==C1_RECOVERY
    for field,bad in [('max_batches',2),('symbol','OTHER')]:
        with pytest.raises(ValueError,match='CONTRACT_MISMATCH'):
            contract_id({**new,field:bad})
    changed=copy.deepcopy(new);changed['strategy']['parameters']['fast']=6
    with pytest.raises(ValueError,match='CONTRACT_MISMATCH'):contract_id(changed)
    with pytest.raises(ValueError,match='UNKNOWN_AUTHORIZATIONS'):
        contract_id({**new,'authorization_id':'RA-IBKR_TSM-SMA520_P5C1-9999'})


@pytest.mark.parametrize('bad', [{}, {'authorization_id':OLD_AUTH},
    {'authorization_id':C1_RECOVERY,'governance_sha':R.GOVERNANCE_SHA,'record_sha256':'1'*64},
    {'authorization_id':C1_RECOVERY,'governance_sha':'1'*40,'record_sha256':R.RECORD_SHA}])
def test_new_context_cannot_reuse_old_or_missing_authority(bad):
    with pytest.raises((ValueError,TypeError)):recovery_context(bad)


def test_new_capability_is_bound_to_new_subject_and_single_use(tmp_path, monkeypatch):
    import base64
    # Stub only the read-only server validation; acquire still checks exclusive creation acknowledgment.
    ref=claim_ref(C1_RECOVERY);posts=[]
    def server(method,path,value):
        if method=='POST':
            posts.append(value)
            return 201,{'ref':ref,'object':{'sha':'1'*40}}
        raise AssertionError('unexpected network read')
    client=K.GitHubClaim(transport=server,authorization_id=C1_RECOVERY)
    monkeypatch.setattr(client,'inspect',lambda *a,**kw:{'claim_ref':ref})
    grant=client.acquire(governance_root=tmp_path,governance_sha='1'*40,request_sha='2'*64)
    with pytest.raises(K.ReconciliationRequired):grant.consume(OLD_AUTH,'2'*64)
    with pytest.raises(K.ReconciliationRequired):grant.consume(C1_RECOVERY,'3'*64)
    grant.consume(C1_RECOVERY,'2'*64)
    with pytest.raises(K.ReconciliationRequired):grant.consume(C1_RECOVERY,'2'*64)
    assert posts==[{'ref':ref,'sha':'1'*40}]


def test_new_preparation_uses_new_approved_record_without_changing_old_books(tmp_path,staged):
    pytest.importorskip('market_research_core.dated_costs')
    gov=tmp_path/'synthetic-governance'
    pre_recovery_books(gov)
    log=ChainedLog(gov/'governance/research_authorizations.jsonl')
    old=next(r for r in log.verify() if r.get('authorization_id')==OLD_AUTH)
    event={k:v for k,v in old.items() if k not in {'seq','prev_sha256','record_sha256'}}
    event['authorization_id']=C1_RECOVERY
    event['approval_ref']='https://github.com/hh4832/strategy-research-agent/pull/27#issuecomment-123456789'
    record=log.append(event)  # disposable test books only, never a real approval
    token=CURRENT.set(ExecutionContext(C1_RECOVERY,'1'*40,record['record_sha256']))
    before={p:p.read_bytes() for folder in ('governance','registry') for p in (gov/folder).rglob('*') if p.is_file()}
    try:
        assert R.authority(gov,now=datetime.fromisoformat('2026-11-15T12:00:00+08:00'))['authorization_id']==C1_RECOVERY
        prepared=R.prepare(staged,tmp_path/'prep',code_sha='2'*40,governance_root=gov)
        assert prepared['authorization_id']==C1_RECOVERY
        ctx=R.read(tmp_path/'prep/C1_PINNED_CONTEXT.json')
        assert ctx['authorization_id']==C1_RECOVERY and ctx['governance_sha']=='1'*40
        assert ctx['request_sha256']!='c357245378eeb8d5852b5114aa27af350a9e4965c2687d45cfffadb4f073548e'
        assert all(p.read_bytes()==raw for p,raw in before.items())
    finally:CURRENT.reset(token)


def test_nonzero_child_keeps_redacted_diagnostics_and_does_not_retry(tmp_path):
    secret='SYNTHETIC_PRIVATE_CREDENTIAL'
    with pytest.raises(RuntimeError,match='RUNNER_NONZERO_EXIT'):
        run_logged([sys.executable,'-c',"print('stage=SYNTHETIC_TASK '+"+repr(secret)+");raise SystemExit(7)"],
                   cwd=tmp_path,parent=tmp_path/'logs',secrets=(secret,),heartbeat_seconds=.1)
    outputs=list((tmp_path/'logs').iterdir());assert len(outputs)==1
    text=(outputs[0]/'runner.log').read_text()
    assert secret not in text and '[REDACTED]' in text
    outcome=json.loads((outputs[0]/'process_outcome.json').read_text())
    assert outcome['returncode']==7 and outcome['automatic_retry'] is False


def test_recovery_notebook_safe_default_and_missing_execution_authority_fails_before_colab():
    import ast
    nb=json.loads((ROOT/'notebooks/Phase5C1_Recovery_Execution.ipynb').read_text())
    code=[''.join(c['source']) for c in nb['cells'] if c['cell_type']=='code']
    for source in code:ast.parse(source)
    assert all(c['outputs']==[] and c['execution_count'] is None for c in nb['cells'] if c['cell_type']=='code')
    ns={};exec(code[0],ns)
    assert ns['MODE']=='PREPARE' and not ns['GOVERNANCE_SHA'] and not ns['CONFIRM_REMOTE_CLAIM']
    assert ns['NEW_AUTHORIZATION_ID']==C1_RECOVERY
    assert ns['EXECUTION_CODE_SHA']=='a4cc87e91dde6fbbba3b71e294224745a8d240f9'
    assert '/workspace' not in '\n'.join(code)
    ns['MODE']='EXECUTE'
    with pytest.raises(RuntimeError,match='Separate explicit'):exec(code[1],ns)
    ns['CONFIRM_AUTHORIZATION']=C1_RECOVERY;ns['CONFIRM_REMOTE_CLAIM']=True
    with pytest.raises(RuntimeError,match='New approved governance'):exec(code[1],ns)
    assert 'userdata.get("GITHUB_TOKEN")' in code[1]
    assert 'run_logged(' in code[1] and 'requirements/phase5c1-runtime.txt' in code[1]


def test_recovery_cannot_relabel_original_authorization_approval(tmp_path):
    from strategy_agent.governance.authorization_contracts import verified_contracts
    pre_recovery_books(tmp_path)
    log=ChainedLog(tmp_path/'governance/research_authorizations.jsonl')
    old=next(r for r in log.verify() if r.get('authorization_id')==OLD_AUTH)
    event={k:v for k,v in old.items() if k not in {'seq','prev_sha256','record_sha256'}}
    event['authorization_id']=C1_RECOVERY
    log.append(event)
    with pytest.raises(ValueError,match='NEW_AUTHORIZATION_APPROVAL_REFERENCE'):
        verified_contracts(tmp_path)


def test_recorded_recovery_execution_can_never_be_prepared_again():
    """With the settled 0002 records in the repository registry, the runner refuses any new C1 attempt for 0002."""
    uses=[u for u in ChainedLog(ROOT/'registry/research_authorization_uses.jsonl').verify() if u['authorization_id']==C1_RECOVERY]
    if not uses:pytest.skip('repository registry does not yet carry the recovery execution records')
    record=next(r for r in ChainedLog(ROOT/'governance/research_authorizations.jsonl').verify()
                if r.get('authorization_id')==C1_RECOVERY)
    token=CURRENT.set(ExecutionContext(C1_RECOVERY,'1'*40,record['record_sha256']))
    try:
        with pytest.raises(K.ReconciliationRequired,match='PRIOR_C1_STATE'):
            R.unused(ROOT)
    finally:CURRENT.reset(token)
