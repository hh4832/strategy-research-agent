"""Agent-owned evidence envelope. This is NOT a proposed Core API.

No timing conversion, fill simulation or cost calculation belongs here.
"""
from __future__ import annotations

import re

GATES = ('DATA_QUALITY', 'TEMPORAL_SAFETY', 'COST_MODEL_COMPATIBILITY', 'COST_APPROVAL',
         'HOLDOUT_APPROVAL', 'FORMAL_EXECUTION_ELIGIBILITY')


def gate_issues(entry: dict) -> list[str]:
    gates = entry.get('gates', {})
    return [f'{name}: missing GO and evidence' for name in GATES
            if not isinstance(gates.get(name), dict) or gates[name].get('status') != 'GO'
            or not gates[name].get('evidence_ref')]


def validate_handoff(expected: dict, returned: dict) -> None:
    """Validate an Agent-normalized receipt; future API binding remains PENDING.

    Require exact input/output pin agreement and complete payload references.
    Never turn a research-only or missing eligibility state into formal approval.
    """
    for key, size in [('engine_git_sha', 40), ('producer_sha', 40), ('temporal_policy_sha256', 64),
                      ('cost_profile_sha256', 64)]:
        value = expected.get(key)
        if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{%d}' % size, value):
            raise ValueError(f'invalid expected {key}')
    for key in ('engine_git_sha', 'producer_sha', 'snapshot_id', 'temporal_policy_id', 'temporal_policy_version',
                'temporal_policy_sha256', 'cost_profile_id', 'cost_profile_version', 'cost_profile_sha256'):
        if not expected.get(key) or returned.get(key) != expected[key]:
            raise ValueError(f'handoff pin mismatch: {key}')
    for key in ('fills', 'rejections', 'ledger', 'provenance'):
        if key not in returned or not isinstance(returned[key], dict if key == 'provenance' else list):
            raise ValueError(f'missing or malformed {key}')
    if any(returned['provenance'].get(k) != v for k, v in expected.items()):
        raise ValueError('provenance does not bind the expected pins')
    if returned.get('market_eligibility') != 'FORMAL_ELIGIBLE':
        raise ValueError('formal market eligibility not established')
