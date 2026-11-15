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

ROOT=Path(__file__).parents[1]


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
    for folder in ('governance','registry'):shutil.copytree(ROOT/folder,gov/folder)
    log=ChainedLog(gov/'governance/research_authorizations.jsonl')
    old=next(r for r in log.verify() if r.get('authorization_id')==OLD_AUTH)
    event={k:v for k,v in old.items() if k not in {'seq','prev_sha256','record_sha256'}}
    event['authorization_id']=C1_RECOVERY
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
