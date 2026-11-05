"""Bounded M3-B.2 acceptance: configuration, governance, archive and evidence contract."""
import copy
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from strategy_agent.research import synthetic_setup
from strategy_agent.research.archive import Archiver, ArchiveError, verify_manifest
from strategy_agent.research.batch import run_batch
from strategy_agent.research.config import ConfigError, config_hash, configured_jobs, load_config, scenario_issues
from strategy_agent.research.contract import GATES, gate_issues, validate_handoff
from strategy_agent.research.environment import ROOT

EXAMPLE = ROOT / 'configs/research/synthetic_sma_v1.json'


def test_config_schema_sha_and_unknown_override(tmp_path):
    c = load_config(EXAMPLE)
    for mutate in [lambda x: x.update(initial_capital=2), lambda x: x.update(override=True),
                   lambda x: x['windows']['validation'].update(start='invalid')]:
        bad = copy.deepcopy(c)
        mutate(bad)
        path = tmp_path / 'bad.json'
        path.write_text(json.dumps(bad))
        with pytest.raises(ConfigError):
            load_config(path)


def test_scenarios_pins_approval_and_no_double_count(tmp_path):
    r, j = synthetic_setup(tmp_path / 'env')
    c = load_config(EXAMPLE)
    jobs = configured_jobs(c, j, r)
    assert [x.cost_multiplier for x in jobs] == [1, 0, 2]
    assert all(x.spec == j.spec and x.parameters == j.parameters for x in jobs)
    for key, value in [('sha256', '0'*64), ('approval_status', 'DRAFT'), ('currency', 'TWD'),
                       ('core_compatibility', 'PENDING'), ('assumption_status', 'EVIDENCED')]:
        bad = dict(c['cost_scenarios'][0], **{key: value})
        assert scenario_issues(bad, r.env.cost_profiles, set(r.env.governance.approvers))
    profile = copy.deepcopy(next(iter(r.env.cost_profiles.values())))
    profile['components'].append(dict(profile['components'][0], id='exchange', kind='EXCHANGE_FEE'))
    ref = (profile['profile_id'], profile['version'])
    bad = dict(c['cost_scenarios'][0], fee_basis='ALL_IN')
    assert any('double count' in s for s in scenario_issues(bad, {ref: profile}, set()))
    bad = copy.deepcopy(c)
    bad['temporal_policy']['status'] = 'APPROVED'
    with pytest.raises(ConfigError, match='PENDING'):
        configured_jobs(bad, j, r)
    bad = copy.deepcopy(c)
    bad['dataset']['snapshot_id'] = 'other'
    with pytest.raises(ConfigError, match='dataset'):
        configured_jobs(bad, j, r)


def test_resume_cannot_hide_r02_rejection(tmp_path):
    r, j = synthetic_setup(tmp_path / 'env', archive_root=tmp_path / 'archive')
    ledger = tmp_path / 'ledger'
    ledger.mkdir()
    (ledger / 'batch_resume.jsonl').write_text(json.dumps(dict(identity='forged', status='COMPLETED',
                                                            archive_status='ARCHIVED'))+'\n')
    out = run_batch(r, [replace(j, parameters={'fast':10, 'slow':20})], batch_id='resume', ledger_dir=ledger,
                    resume=True, identity_of=lambda _: 'forged')
    assert out[0].status == 'REJECTED_SPEC'
    events = r.env.registry.records()
    assert all(e['status'] != 'RUNNING' for e in events)
    assert any('R02' in x['code'] for e in events for x in e.get('rejection_reasons', []))


def test_configured_run_archives_pins_and_scenarios_are_one_trial(tmp_path):
    r, j = synthetic_setup(tmp_path / 'env', archive_root=tmp_path / 'archive')
    config = load_config(EXAMPLE)
    results = [r.run(job) for job in configured_jobs(config, j, r)]
    assert all(x.status == 'COMPLETED' for x in results)
    assert len({x.record['trial']['family_id'] for x in results}) == 1
    assert r.env.registry.trial_count(results[0].record['trial']['family_id']) == 1
    for result in results:
        p = Path(result.archive['path'])
        verify_manifest(p)
        assert json.loads((p/'research_config.json').read_text()) == config
        assert json.loads((p/'snapshot_refs.json').read_text())['research_config']['sha256'] == config['sha256']
        assert result.context['verdicts']['strategy']['decision'] == '無法判定'
    from strategy_agent.research.batch import done_identities
    ledger = tmp_path / 'resume.jsonl'
    ledger.write_text(json.dumps(dict(status='COMPLETED', archive_status='ARCHIVED',
                                     identity=results[0].identity, archive_path=results[0].archive['path']))+'\n')
    assert done_identities(ledger) == {results[0].identity}
    p = Path(results[0].archive['path'])
    (p/'metrics.json').write_text('tampered')
    assert done_identities(ledger) == set()
    with pytest.raises(ArchiveError):
        verify_manifest(p)


def test_drive_write_failure_has_local_receipt(tmp_path, monkeypatch):
    from types import SimpleNamespace
    a = Archiver(tmp_path/'unwritable_drive', local_fallback=tmp_path/'local')
    original = a._new_dir
    def fail_target(base, sha):
        if base == a.root:
            raise PermissionError('simulated Drive write denial')
        return original(base, sha)
    monkeypatch.setattr(a, '_new_dir', fail_target)
    res = SimpleNamespace(context={'agent':{'agent_sha':'a'*40}}, identity='x')
    with pytest.raises(ArchiveError, match='local receipt'):
        a.write(res)
    receipts = list((tmp_path/'local').rglob('archive_failure.json'))
    assert len(receipts) == 1
    assert json.loads(receipts[0].read_text())['status'] == 'ARCHIVE_FAILED'


def test_six_gates_fail_closed_and_core_review_file_pins():
    assert len(gate_issues({})) == 6
    entry = {'gates': {k:{'status':'GO','evidence_ref':'test'} for k in GATES}}
    assert not gate_issues(entry)
    for gate in GATES:
        bad = copy.deepcopy(entry)
        bad['gates'][gate]['status'] = 'UNKNOWN'
        assert gate_issues(bad)
    gate = json.loads((ROOT/'governance/market_readiness.json').read_text())['markets']['IBKR_TSM']
    assert gate['status'] == 'NO_GO' and gate['closing_auction'] == 'UNKNOWN'
    for name, pin in gate['core_review']['files'].items():
        assert hashlib.sha256((ROOT/'docs/evidence'/Path(name).name).read_bytes()).hexdigest() == pin['sha256']


def test_core_handoff_requires_every_pin_payload_and_formal_eligibility():
    pins = dict(engine_git_sha='a'*40, producer_sha='b'*40, snapshot_id='snapshot', temporal_policy_id='policy',
                temporal_policy_version='1', temporal_policy_sha256='c'*64, cost_profile_id='cost',
                cost_profile_version='1', cost_profile_sha256='d'*64)
    receipt = dict(pins, fills=[], rejections=[], ledger=[], provenance=dict(pins), market_eligibility='FORMAL_ELIGIBLE')
    validate_handoff(pins, receipt)
    for key in receipt:
        bad = dict(receipt)
        del bad[key]
        with pytest.raises(ValueError):
            validate_handoff(pins, bad)
    with pytest.raises(ValueError):
        validate_handoff(pins, dict(receipt, market_eligibility='RESEARCH_ONLY'))


def test_finalization_write_denial_is_not_archive_pass(tmp_path, monkeypatch):
    from types import SimpleNamespace
    a = Archiver(tmp_path/'drive', local_fallback=tmp_path/'local')
    def fail(_):
        raise PermissionError('manifest write failed')
    monkeypatch.setattr(a, '_finalize', fail)
    res = SimpleNamespace(context={'agent':{'agent_sha':'a'*40}}, identity='x',
                          archive={'path':str(tmp_path/'drive'/'run'), 'status':'ARCHIVED'})
    with pytest.raises(ArchiveError, match='local receipt'):
        a.finalize(res)
    assert res.archive['status'] == 'ARCHIVE_FAILED' and res.archive['verified'] is False
    assert list((tmp_path/'local').rglob('archive_failure.json'))
