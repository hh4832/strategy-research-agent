"""Read-only identities of the two reviewed research contracts; never approval or budget APIs."""
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path

from .research_authorization import validate_events

B3C = 'RA-IBKR_TSM-SMA520_P5B3C-0001'
C1 = 'RA-IBKR_TSM-SMA520_P5C1-0001'
LEGACY = frozenset({'RA-IBKR_TSM-SMA520_SMOKE-0001', 'RA-IBKR_TSM-SMA520_DEV-18350b3adba8e28a7e5e'})
# Canonical immutable fields of the already reviewed proposals (not new cost/data pins).
SCOPE_SHA256 = {B3C: '98bf9774bbe429c3d51ef7b5050f323262d0710ce2d74beae10dbfcae7858434',
                C1: '8d378125593bad48a454b69b76eccbe6912627415ef9003109591d7d21f2a2b3'}
EVENT_FIELDS = frozenset({'action', 'approved_by', 'approved_at', 'approval_ref',
                          'seq', 'prev_sha256', 'record_sha256', 'issued_at', 'expires_at'})
PROPOSAL_METADATA = frozenset({'proposal_status', 'proposed_by', 'proposed_at', 'approval_instructions'})


def contract_id(record):
    """Require the exact ID, scope, executor and every reviewed immutable contract field."""
    aid = record.get('authorization_id')
    if aid not in SCOPE_SHA256:
        raise ValueError('UNKNOWN_AUTHORIZATIONS:' + str(aid))
    excluded = EVENT_FIELDS | PROPOSAL_METADATA | ({'notes'} if aid == B3C else set())
    scope = {k:v for k,v in record.items() if k not in excluded}
    observed = hashlib.sha256(json.dumps(scope, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if observed != SCOPE_SHA256[aid]:
        raise ValueError('AUTHORIZATION_CONTRACT_MISMATCH:' + str(aid))
    issued, expires = (datetime.fromisoformat(record[k]) for k in ('issued_at', 'expires_at'))
    if issued.tzinfo is None or expires.tzinfo is None or expires-issued != timedelta(days=7):
        raise ValueError('AUTHORIZATION_VALIDITY:' + aid)
    return aid


def verified_contracts(root):
    """Fold and classify one verified snapshot, including revoked historical records."""
    from strategy_agent.registry.chain import ChainedLog
    g = Path(root)/'governance'
    events = ChainedLog(g/'research_authorizations.jsonl').verify()
    approvers = set(json.loads((g/'approvers.json').read_text())['research_leads'])
    active = validate_events(events, approvers)
    for record in events:
        if record.get('action') == 'APPROVE' and record['authorization_id'] not in LEGACY:
            contract_id(record)
    return active, events


def active_contracts(root):
    """Verify the whole hash chain/schema/approver/uniqueness before separating contract families."""
    return verified_contracts(root)[0]
