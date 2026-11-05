"""M3-B.2 merge acceptance: preserve published config API; harden cached/failed archives."""
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from strategy_agent.research import synthetic_setup
from strategy_agent.research.archive import Archiver, ArchiveError, verify_manifest
from strategy_agent.research.batch import done_identities, run_batch


def test_resume_rechecks_r02_and_manifest(tmp_path):
    runner, job = synthetic_setup(tmp_path/'env', archive_root=tmp_path/'archive')
    result = runner.run(job)
    assert result.status == 'COMPLETED'
    ledger = tmp_path/'ledger'
    ledger.mkdir()
    cache = ledger/'batch_check.jsonl'
    cache.write_text(json.dumps(dict(status='COMPLETED', archive_status='ARCHIVED', identity=result.identity,
                                    archive_path=result.archive['path']))+'\n')
    assert done_identities(cache) == {result.identity}
    outcomes = run_batch(runner, [replace(job, parameters={'fast':10,'slow':20})], batch_id='check',
                         ledger_dir=ledger, resume=True, identity_of=lambda _:result.identity)
    assert outcomes[0].status == 'REJECTED_SPEC'
    assert any('R02' in r['code'] for e in runner.env.registry.records() for r in e.get('rejection_reasons', []))
    (Path(result.archive['path'])/'metrics.json').write_text('tampered')
    assert done_identities(cache) == set()
    with pytest.raises(ArchiveError):
        verify_manifest(Path(result.archive['path']))


def test_resume_cannot_disable_r02(tmp_path, monkeypatch):
    from strategy_agent.research import pipeline
    runner, job = synthetic_setup(tmp_path/'env')
    monkeypatch.setattr(pipeline, 'R02_ENFORCED', False)
    with pytest.raises(pipeline.ResearchContractError, match='R02'):
        run_batch(runner, [job], batch_id='x', ledger_dir=tmp_path/'ledger', resume=True)


def _result():
    return SimpleNamespace(context={'agent':{'agent_sha':'a'*40}}, record={'experiment_id':'EXP-TEST'},
                           status='COMPLETED', identity='i'*64, metrics_sha256=None,
                           archive={'path':'unused','status':'ARCHIVED'})


def test_drive_allocation_failure_has_local_record(tmp_path, monkeypatch):
    arch = Archiver(tmp_path/'drive', local_fallback=tmp_path/'local')
    def fail(*_):
        raise PermissionError('Drive allocation denied')
    monkeypatch.setattr(arch, '_new_dir', fail)
    result = arch.write(_result())
    assert result['status'] == 'FAILED'
    receipts = list((tmp_path/'local').rglob('*.json'))
    assert receipts and json.loads(receipts[0].read_text())['status'] == 'FAILED'


def test_manifest_finalization_failure_is_recorded(tmp_path, monkeypatch):
    arch = Archiver(tmp_path/'drive', local_fallback=tmp_path/'local')
    def fail(*_):
        raise PermissionError('Drive manifest write denied')
    monkeypatch.setattr(arch, '_finalize', fail)
    result = _result()
    result.archive['path'] = str(tmp_path/'drive'/'run')
    with pytest.raises(ArchiveError):
        arch.finalize(result)
    assert result.archive['status'] == 'FAILED' and not result.archive['verified']
    assert list((tmp_path/'local').rglob('*.json'))
