"""Non-consuming recovery preparation and a fail-closed bridge to the existing C1 gates.

Default PREPARE creates only an unsigned authorization packet, not a request, binding or CLAIM.
"""
from datetime import datetime, timezone
import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import uuid

from strategy_agent.execution.c1_context import OLD_AUTH, OLD_GOVERNANCE, recovery_context, claim_ref
from strategy_agent.execution import c1_integration as C
from strategy_agent.governance.authorization_contracts import EVENT_FIELDS, PROPOSAL_METADATA, C1_RECOVERY


def prepare_packet(code_root, destination, code_sha):
    root = Path(code_root)
    if not re.fullmatch('[0-9a-f]{40}',code_sha):
        raise ValueError('PUBLISHED_EXECUTION_SHA_REQUIRED')
    actual = subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
    if actual != code_sha:
        raise ValueError('EXECUTION_SHA_MISMATCH')
    records=[json.loads(line) for line in (root/'governance/research_authorizations.jsonl').read_text().splitlines()]
    previous=next(r for r in records if r.get('authorization_id')==OLD_AUTH and r.get('action')=='APPROVE')
    draft={k:copy.deepcopy(v) for k,v in previous.items() if k not in EVENT_FIELDS|PROPOSAL_METADATA}
    draft.update(authorization_id=C1_RECOVERY, proposal_status='DRAFT_NOT_APPROVED',
                 issued_at='HUMAN_DECISION_REQUIRED',expires_at='HUMAN_DECISION_REQUIRED')
    output=Path(destination)/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')+'_'+uuid.uuid4().hex)
    output.mkdir(parents=True,exist_ok=False)
    plan=dict(status='UNSIGNED_NEW_AUTHORIZATION_PACKET',authorization_id=C1_RECOVERY,
              execution_sha=code_sha,core_sha=C.CORE,governance_sha=None,
              previous_governance_sha=OLD_GOVERNANCE,authorization_record_sha256=None,
              dataset=previous['dataset'],reviewed_data_pins=__import__(
                  'strategy_agent.readiness.c1_authorization',fromlist=['PINS']).PINS,
              cost_schedule_sha256=C.SCHEDULE_SHA256,claim_ref=claim_ref(C1_RECOVERY),
              archive_namespace='phase5c1_recovery/attempts/'+C1_RECOVERY+'/batch-1',
              task_id=None,request_sha256=None,authorization_approved=False,execute_go=False,
              previous_attempt_disclosure={'authorization_id':OLD_AUTH,'task_id':'T-c357245378eeb8d5852b',
                  'state':'FAILED_NON_REUSABLE_RECONCILIATION_REQUIRED',
                  'partial_ledger_existed':True,'performance_visibility':'UNKNOWN'},
              classification='NEW_REGISTERED_DEVELOPMENT_ATTEMPT_FOR_TECHNICAL_RECOVERY',
              required_human_actions=['REVIEW_ORIGINAL_BATCH_DISPOSITION','APPROVE_NEW_AUTHORIZATION_IN_LEDGER',
                  'PIN_NEW_GOVERNANCE_COMMIT_AND_NEW_RECORD_SHA','NEW_PREPARE_AND_REQUEST_HASH',
                  'NEW_PERSONAL_BIND_AUTHORIZATION_AND_APPROVE_TASK','NONCONSUMING_PREFLIGHT',
                  'SEPARATE_EXPLICIT_EXECUTE_DECISION'])
    for name,value in [('authorization.DRAFT_NOT_APPROVED.json',draft),('recovery_plan.json',plan)]:
        with (output/name).open('x') as f:json.dump(value,f,indent=2)
    index={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir()}
    with (output/'PACKET_INDEX.json').open('x') as f:json.dump(index,f,indent=2)
    return dict(status=plan['status'],output=str(output),authorization_id=C1_RECOVERY,
                strategy_execution='NOT_EXECUTED',batch_consumption=0,request_sha256=None,task_id=None)


def main():
    import argparse,sys
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settings',required=True)
    args=parser.parse_args()
    settings=json.loads(Path(args.settings).read_text())
    mode=settings.get('mode','PREPARE')
    if mode not in ('PREPARE','PREFLIGHT','EXECUTE'):
        raise ValueError('UNKNOWN_MODE')
    # A draft packet requires no approval, data access, remote CLAIM reads or secret.
    if mode=='PREPARE' and not settings.get('execution_context'):
        print(json.dumps(prepare_packet(C.ROOT,Path(settings['drive_root'])/'authorization_preparations'/C1_RECOVERY,
                                        settings['code_sha']),indent=2),flush=True)
        return
    recovery_context(settings.get('execution_context',{}))
    if mode=='EXECUTE' and (settings.get('confirm_authorization')!=C1_RECOVERY
                            or settings.get('confirm_remote_claim') is not True):
        raise ValueError('SEPARATE_EXPLICIT_NEW_EXECUTION_DECISION_REQUIRED')
    # Validation of new record/chain/approval/unused capacity precedes every data read in c1_runner.main.
    from strategy_agent.execution import c1_runner
    sys.argv=['c1_runner','--settings',args.settings]
    c1_runner.main()


if __name__=='__main__':
    main()
