"""Producer + preflight only; no SMA, run, consume, signoff or formal log writes."""
import hashlib
import json
from pathlib import Path

import pytest

D=pytest.importorskip('market_research_core.tsm_development')
pd=pytest.importorskip('pandas');pa=pytest.importorskip('pyarrow');pq=pytest.importorskip('pyarrow.parquet')
from strategy_agent.research_only.development_snapshot import prepare_development_proposal
from strategy_agent.research_only.runner import ResearchOnlyRunner,ROOT,canonical_sha
from strategy_agent.research_only.core_phase4a import probe_snapshot,SnapshotBinding


def protected():
    dirs=['governance','registry','configs/core_cost_profiles','configs/research_only']
    return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for d in dirs for p in (ROOT/d).rglob('*') if p.is_file()}


def test_nonconsuming_new_proposal_retains_old_authorization_and_guards(tmp_path,monkeypatch):
    before=protected()
    monkeypatch.setattr(D,'core_code_identity',lambda:{'core_git_sha':'a'*40,'core_tree_dirty':False})
    root=tmp_path/D.PINNED_SNAPSHOT.run_name;root.mkdir()
    rows=[]
    for day in ('2024-05-29','2024-06-03'):
        for t in pd.date_range(day+' 13:45',periods=30,freq='min',tz='UTC'):
            rows.append(dict(timestamp_utc=t,open=100.,high=101.,low=99.,close=100.,volume=1000.))
    table=pa.Table.from_pylist(rows);p=root/'test-only.parquet';pq.write_table(table,p)
    mp=root/'manifest.json';mp.write_text(json.dumps({'results':[{'symbol':'TSM','published_sha256':D.file_sha(p)}]}))
    plan=D.plan_conversion([{'path':str(p),'expected_sha256':D.file_sha(p),'lineage_ref':'SYNTHETIC_ONLY'}],
                          parent_manifest_path=mp,expected_parent_manifest_sha256=D.file_sha(mp))
    b=D.produce_development_snapshot(plan,tmp_path/'derived',producer_sha='a'*40)
    from market_research_core.tsm_native import core_code_identity
    result=prepare_development_proposal(b,core_sha=core_code_identity()['core_git_sha'],output_directory=tmp_path/'proposal')
    assert result['data_preflight']['status']=='PASS' and result['slot_consumed'] is False
    assert not result['eligible_to_backtest']
    proposal=json.loads(Path(result['proposal_path']).read_text());cfg=json.loads(Path(result['config_path']).read_text())
    assert proposal['approved_by']=='__RESEARCH_LEAD__'
    assert proposal['authorization_id']!='RA-IBKR_TSM-SMA520_SMOKE-0001'
    assert proposal['dataset']['snapshot_id']==b['snapshot_pin']['snapshot_id']
    assert cfg['provenance']['config_sha256']==canonical_sha(cfg)
    probe=probe_snapshot(b['snapshot_root'],SnapshotBinding.from_config(cfg),dates=('2024-05-29','2024-06-28'),
                         research_window=('2024-05-29','2024-06-28'),holdout=(('2025-01-01','2026-10-16'),))
    assert probe.status=='COMPATIBLE',probe.blockers
    pf=ResearchOnlyRunner().preflight(Path(result['config_path']))
    assert 'RA01' in {c for c,_ in pf.blockers}
    assert protected()==before
    # The old signed authorization cannot authorize the new dataset pin.
    cfg['authorization_id']='RA-IBKR_TSM-SMA520_SMOKE-0001';cfg['provenance']['config_sha256']=canonical_sha(cfg)
    wrong=tmp_path/'old-auth-new-data.json';wrong.write_text(json.dumps(cfg))
    pf=ResearchOnlyRunner().preflight(wrong)
    assert 'RA04' in {c for c,_ in pf.blockers}
    assert protected()==before
