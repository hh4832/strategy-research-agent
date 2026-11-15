"""Read-only metadata acceptance of the confirmed approval; no data, task or execution."""
import copy
from datetime import datetime
import json
from pathlib import Path
import shutil

import pytest

from strategy_agent.governance.authorization_contracts import verified_contracts, contract_id, C1_RECOVERY
from strategy_agent.registry.chain import canonical, record_hash
from strategy_agent.execution.activation import eligible_authorizations
from strategy_agent.readiness import b3c

ROOT=Path(__file__).parents[1]
RECORD_SHA='719212edb4f0483def43188f416e1b62069e32c37c3567c9dde808df9053fd69'


def test_confirmed_event_matches_proposal_and_exact_approved_hash():
    _,rows=verified_contracts(ROOT)
    found=[r for r in rows if r.get('authorization_id')==C1_RECOVERY]
    assert len(found)==1
    r=found[0]
    assert r['record_sha256']==record_hash(r)==RECORD_SHA
    assert r['approved_by']=='hh4832'
    assert r['approval_ref']=='https://github.com/hh4832/strategy-research-agent/pull/27#issuecomment-6893178421'
    assert r['approved_at']=='2026-11-15T10:46:22+00:00'
    assert r['seq']==4 and r['prev_sha256']=='567dafcc44ba0300ac7504b56bdf3aafc46f420e26423e14be633429dbc30d26'
    proposal=json.loads((ROOT/'governance/proposals'/f'{C1_RECOVERY}.json').read_text())
    metadata={'proposal_status','proposed_by','proposed_at','approval_instructions'}
    event={'action','approved_by','approved_at','approval_ref','seq','prev_sha256','record_sha256'}
    assert {k:v for k,v in proposal.items() if k not in metadata}=={k:v for k,v in r.items() if k not in event}


@pytest.mark.parametrize('at,want', [('2026-11-15T23:59:59+08:00',False),
                                    ('2026-11-16T00:00:00+08:00',True),
                                    ('2026-11-23T00:00:00+08:00',False)])
def test_record_time_eligibility_is_half_open(at,want):
    got=eligible_authorizations(ROOT,'colab-hh4832-exec-01',datetime.fromisoformat(at),authorization_id=C1_RECOVERY)
    assert (C1_RECOVERY in got)==want


@pytest.mark.parametrize('damage', ['scope','unknown_id'])
def test_b3c_classifier_accepts_verified_recovery_but_rejects_mutations(tmp_path,monkeypatch,damage):
    assert b3c.formal_authorization_state()['state'] in ('NOT_GRANTED','GRANTED_UNUSED','GRANTED_USED')
    # Mutate a temporary historical snapshot only, with a valid recomputed chain hash.
    shutil.copytree(ROOT/'governance',tmp_path/'governance')
    _,rows=verified_contracts(ROOT)
    i=next(i for i,r in enumerate(rows) if r.get('authorization_id')==C1_RECOVERY)
    rows=copy.deepcopy(rows[:i+1])
    if damage=='scope':rows[-1]['strategy']['parameters']['fast']=6
    else:rows[-1]['authorization_id']='RA-IBKR_TSM-SMA520_P5C1-9999'
    rows[-1]['record_sha256']=record_hash(rows[-1])
    (tmp_path/'governance/research_authorizations.jsonl').write_bytes(b''.join(canonical(r)+b'\n' for r in rows))
    monkeypatch.setattr(b3c,'ROOT',tmp_path)
    result=b3c.formal_authorization_state()
    assert result['state']=='UNEXPECTED' and 'INVALID_AUTHORIZATION_BOOK' in str(result['why'])
