"""Explicit per-process C1 execution identities; these are pins, never approval grants."""
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import re

OLD_AUTH = 'RA-IBKR_TSM-SMA520_P5C1-0001'
OLD_GOVERNANCE = 'df56664a12a54762e222f141b50e337c6dfc5088'
OLD_RECORD = '567dafcc44ba0300ac7504b56bdf3aafc46f420e26423e14be633429dbc30d26'


def claim_ref(authorization_id):
    if not re.fullmatch(r'RA-IBKR_TSM-SMA520_P5C1-\d{4}', authorization_id):
        raise ValueError('EXPLICIT_C1_AUTHORIZATION_ID_REQUIRED')
    return 'refs/heads/research-claims/phase5c1/'+hashlib.sha256(authorization_id.encode()).hexdigest()+'/batch-1'


@dataclass(frozen=True)
class ExecutionContext:
    authorization_id: str = OLD_AUTH
    governance_sha: str = OLD_GOVERNANCE
    record_sha256: str = OLD_RECORD

    def __post_init__(self):
        claim_ref(self.authorization_id)
        for value, size in ((self.governance_sha,40),(self.record_sha256,64)):
            if not re.fullmatch('[0-9a-f]{'+str(size)+'}',value):
                raise ValueError('APPROVED_GOVERNANCE_AND_RECORD_PINS_REQUIRED')

    @property
    def claim_ref(self):
        return claim_ref(self.authorization_id)


CURRENT = ContextVar('c1_execution_context', default=ExecutionContext())


def context():
    return CURRENT.get()


def recovery_context(value):
    ctx = ExecutionContext(**value)
    if ctx.authorization_id != 'RA-IBKR_TSM-SMA520_P5C1-0002':
        raise ValueError('ONLY_REGISTERED_RECOVERY_CONTRACT_SUPPORTED')
    if ctx.authorization_id == OLD_AUTH or ctx.governance_sha == OLD_GOVERNANCE or ctx.record_sha256 == OLD_RECORD:
        raise ValueError('FAILED_AUTHORIZATION_AND_OLD_GOVERNANCE_NOT_REUSABLE')
    return ctx
