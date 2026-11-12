"""Synthetic, isolated read-only governance probes. No admin, runner, live approval or consumption."""
import copy
from datetime import datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from strategy_agent.governance.authorization_contracts import B3C, C1, active_contracts, contract_id
from strategy_agent.governance.research_authorization import ResearchAuthorizationBook, ResearchAuthorizationGuard, ResearchRequest
from strategy_agent.registry.chain import ChainedLog
from strategy_agent.execution.activation import eligible_authorizations
from strategy_agent.execution import b3c_run
from strategy_agent.readiness import b3c

NOW = datetime.fromisoformat('2026-11-12T12:00:00+08:00')
EXECUTOR = 'colab-hh4832-exec-01'


@pytest.fixture
def world(pre_c1_root):
    root = pre_c1_root
    (root/'registry').mkdir(exist_ok=True)
    (root/'registry/research_authorization_uses.jsonl').write_text('')
    p = json.loads((root/'docs/phase5c1/authorization_source/authorization.DRAFT_NOT_APPROVED.json').read_text())['proposal_fields']
    # This is a synthetic fixture event in pytest's temporary directory, never a human signature.
    ev = dict(p, issued_at=NOW.isoformat(), expires_at=(NOW+timedelta(days=7)).isoformat(),
              action='APPROVE', approved_by='hh4832', approved_at=NOW.isoformat(),
              approval_ref='https://example.invalid/ISOLATED_TEST_NOT_A_HUMAN_APPROVAL')
    log = ChainedLog(root/'governance/research_authorizations.jsonl')
    assert len(active_contracts(root)) == 3
    log.append(ev)
    return root, ev


def test_legacy_and_c1_coexist_and_eligibility_is_contract_scoped(world):
    root, ev = world
    assert len(active_contracts(root)) == 4
    assert contract_id(ev) == C1
    assert C1 in eligible_authorizations(root, EXECUTOR, NOW, authorization_id=C1)
    assert C1 not in eligible_authorizations(root, EXECUTOR, NOW, authorization_id=B3C)
    assert B3C not in eligible_authorizations(root, EXECUTOR, NOW, authorization_id=C1)
    assert eligible_authorizations(root, 'wrong-executor', NOW) == []
    assert eligible_authorizations(root, EXECUTOR, NOW+timedelta(days=8)) == []
    assert C1 not in eligible_authorizations(root, EXECUTOR, NOW-timedelta(seconds=1))


@pytest.mark.parametrize('field', ['authorization_id', 'scope', 'executor', 'contract', 'max_batches', 'costs'])
def test_changed_contract_field_and_unknown_id_fail_closed(world, field):
    root, ev = world
    broken = copy.deepcopy(ev)
    if field == 'executor': broken[field]['executor_id'] = 'other-runtime'
    elif field == 'max_batches': broken[field] = 2
    elif field == 'costs': broken[field]['dated_schedule_sha256'] = '0'*64
    else: broken[field] = 'UNKNOWN'
    with pytest.raises(ValueError): contract_id(broken)
    # Replace only the synthetic last event in the isolated fixture; never touch approved repository bytes.
    path = root/'governance/research_authorizations.jsonl'
    prefix = b''.join(path.read_bytes().splitlines(keepends=True)[:3])
    path.write_bytes(prefix)
    ChainedLog(path).append(broken)
    assert eligible_authorizations(root, EXECUTOR, NOW) == []


@pytest.mark.parametrize('mutation', ['duplicate', 'seq', 'prev_sha256', 'hash'])
def test_invalid_book_blocks_all_contracts(world, mutation):
    root, ev = world
    path = root/'governance/research_authorizations.jsonl'
    if mutation == 'duplicate':
        ChainedLog(path).append(ev)
    else:
        rows = [json.loads(x) for x in path.read_text().splitlines()]
        rows[-1][{'seq':'seq','prev_sha256':'prev_sha256','hash':'record_sha256'}[mutation]] = 999 if mutation == 'seq' else '0'*64
        path.write_text(''.join(json.dumps(x)+'\n' for x in rows))
    assert eligible_authorizations(root, EXECUTOR, NOW) == []
    with pytest.raises(Exception): b3c_run._authorization(root, C1)


def test_b3c_and_c1_manifests_cannot_swap_contracts(tmp_path):
    for kind, wrong in [('PHASE5B3C_PREPARE_MANIFEST', C1), ('PHASE5C1_PREPARE_MANIFEST', B3C)]:
        with pytest.raises(b3c_run.B3CStopped, match='AUTHORIZATION_CONTRACT_MISMATCH'):
            b3c_run.prepare(tmp_path/'never', authorization_id=wrong, manifest_kind=kind, agent_commit='a'*40)
    assert not (tmp_path/'never').exists()


def test_budget_holdout_and_formal_gate_still_fail_closed(world):
    root, ev = world
    market = json.loads((root/'governance/market_readiness.json').read_text())['markets']['IBKR_TSM']
    assert market['status'] == 'NO_GO' and 'Holdout access' in ev['does_not_authorize']
    book = ResearchAuthorizationBook(root/'governance/research_authorizations.jsonl',root/'governance/approvers.json')
    req = ResearchRequest(C1,ev['market_id'],ev['symbol'],ev['dataset'],ev['temporal_policy']['sha256'],
        ev['costs']['scenario_set_sha256'],tuple(ev['costs']['scenarios']),ev['strategy']['spec_file_sha256'],
        ev['strategy']['strategy_id'],ev['strategy']['version'],ev['strategy']['parameters'],
        ev['research_window']['start'],ev['research_window']['start'],ev['research_window']['end'],holdout_access=True)
    lock = SimpleNamespace(lock_id=ev['holdout']['lock_id'],holdout_start=datetime.fromisoformat(ev['holdout']['start']),
                           holdout_end=datetime.fromisoformat(ev['holdout']['end']))
    uses = root/'registry/research_authorization_uses.jsonl'
    ChainedLog(uses).append({'authorization_id':C1})  # synthetic used-budget fixture, never consume()
    guard = ResearchAuthorizationGuard(book,SimpleNamespace(for_dataset=lambda _:lock),market,uses,clock=lambda:NOW)
    codes = lambda: {c for c,_ in guard.check(req).failures}
    assert {'RA11','RA12'} <= codes()
    market['status'] = 'GO'
    assert 'RA10' in codes()
    guard.clock = lambda: NOW+timedelta(days=8)
    assert 'RA02' in codes()


def test_b3c_state_ignores_only_valid_c1_and_rejects_corrupt_chain(world,monkeypatch):
    root, _ = world
    import strategy_agent.readiness.candidate as candidate
    # Copy immutable B3-C artifacts needed by its existing classifier; governance stays isolated.
    import shutil
    real = Path(__file__).parents[1]
    for relative in ('configs','specs','docs','.github'):
        shutil.copytree(real/relative,root/relative,dirs_exist_ok=True)
    shutil.copytree(real/'registry',root/'registry',dirs_exist_ok=True)
    monkeypatch.setattr(candidate,'ROOT',root);monkeypatch.setattr(b3c,'ROOT',root)
    assert b3c.formal_authorization_state()['state']=='GRANTED_USED'
    p=root/'governance/research_authorizations.jsonl'
    p.write_bytes(p.read_bytes().replace(b'"seq":3',b'"seq":9'))
    assert b3c.formal_authorization_state()['state']=='UNEXPECTED'


def test_revoked_contract_is_not_returned_as_an_approval(world):
    root, _ = world
    ChainedLog(root/'governance/research_authorizations.jsonl').append({
        'action':'REVOKE', 'contract':'research-only-authorization-v1', 'authorization_id':C1,
        'approved_by':'hh4832', 'approval_ref':'https://example.invalid/ISOLATED_TEST_REVOKE',
        'approved_at':NOW.isoformat(), 'reason':'synthetic fixture revocation'})
    assert eligible_authorizations(root, EXECUTOR, NOW, authorization_id=C1) == []
    with pytest.raises(b3c_run.B3CStopped, match='AUTHORIZATION_INACTIVE'):
        b3c_run._authorization(root, C1)


def test_gate_rejects_cross_contract_and_corrupted_uses_without_runner(world):
    from strategy_agent.execution.activation import RealActivationGate
    root, ev = world
    doc = {'authorization_ref':{'authorization_id':C1},
           'research_window':{'start':ev['research_window']['start'], 'end':ev['research_window']['end']}}
    backend = SimpleNamespace(execution_identity=lambda: ({}, []))
    gate = RealActivationGate(governance_root=root, artifact_root=root, executor_id=EXECUTOR,
                              production_books=True, clock=lambda:NOW, authorization_id=B3C)
    assert 'A01_authorization_approved_active' in gate.evaluate(doc,backend)['failed']
    gate.authorization_id = C1
    p=root/'registry/research_authorization_uses.jsonl'
    p.write_text('{"seq":9,"authorization_id":"'+C1+'"}\n')
    result=gate.evaluate(doc,backend)
    assert result['status']=='BLOCKED' and 'A07_budget_not_consumed' in result['failed']
