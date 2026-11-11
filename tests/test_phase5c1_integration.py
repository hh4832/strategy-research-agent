"""Synthetic engineering evidence only. No Drive, real approval, real budget or raw Holdout."""
import ast
from dataclasses import replace
from datetime import date,timedelta
import json
import math
from pathlib import Path
import shutil

import pytest

from strategy_agent.execution import c1_integration as C


def test_unauthorized_execution_never_constructs_backend_or_consumes(tmp_path):
    C.save(tmp_path/'readiness.json',{'data_binding':'OBSERVED_NOT_TRUSTED'})
    before=(C.ROOT/'registry/research_authorization_uses.jsonl').read_bytes()
    with pytest.raises(ValueError,match='PINS_PENDING'):C.execute_authorized(tmp_path,governance_root=C.ROOT)
    assert (C.ROOT/'registry/research_authorization_uses.jsonl').read_bytes()==before


def test_prepared_request_requires_reviewed_pins_and_formal_authorization(tmp_path):
    C.save(tmp_path/'readiness.json',{'data_binding':'OBSERVED_NOT_TRUSTED'})
    with pytest.raises(ValueError,match='reviewed real data pins'):
        C.prepare_governed_request(tmp_path,governance_root=C.ROOT,agent_commit='a'*40)
    (tmp_path/'readiness.json').write_text(json.dumps({'data_binding':'REVIEWED_PINS_VERIFIED','artifact_root':str(C.ROOT),
        'config_path':str(tmp_path/'none.json'),'ready_to_submit_formal_authorization':True,'cost_schedule_sha256':C.SCHEDULE_SHA256}))
    with pytest.raises(Exception,match='AUTHORIZATION_NOT_UNIQUE'):
        C.prepare_governed_request(tmp_path,governance_root=C.ROOT,agent_commit='a'*40)
    with pytest.raises(Exception,match='AUTHORIZATION_NOT_UNIQUE'):
        C.execute_authorized(tmp_path,governance_root=C.ROOT)


@pytest.fixture(scope='module')
def archived_template(tmp_path_factory):
    tmp_path=tmp_path_factory.mktemp('c1_synthetic_archive')
    monkeypatch=pytest.MonkeyPatch()
    pd=pytest.importorskip('pandas');pa=pytest.importorskip('pyarrow');pq=pytest.importorskip('pyarrow.parquet')
    from strategy_agent.adapters.mrc_phase4a import core_development
    D,N=core_development()
    if not hasattr(D,'LONG_WINDOW'):pytest.skip('requires Core v1.2 CI job')
    from importlib import import_module
    R=import_module('market_research_core.tsm_recovery')  # test fixture only, never production import site
    monkeypatch.setattr(D,'core_code_identity',lambda:{'core_git_sha':C.PRODUCER,'core_tree_dirty':False})
    monkeypatch.setattr(N,'core_code_identity',lambda:{'core_git_sha':C.CORE,'core_tree_dirty':False})
    monkeypatch.setattr(R,'core_code_identity',lambda:{'core_git_sha':C.CORE,'core_tree_dirty':False})
    parent=tmp_path/D.PINNED_SNAPSHOT.run_name;parent.mkdir()
    rows=[];day=date(2024,5,29);k=0
    while day<=date(2024,7,12):
        if day.weekday()<5 and day.isoformat() not in ('2024-06-19','2024-07-04'):
            t=pd.Timestamp(str(day)+' 09:30',tz='America/New_York')
            for i in range(390):
                price=100+3*math.sin((k*390+i)/900+math.pi)
                rows.append(dict(timestamp_utc=(t+pd.Timedelta(minutes=i)).tz_convert('UTC'),open=price,
                    high=price+.1,low=price-.1,close=price,volume=10000.))
            k+=1
        day+=timedelta(days=1)
    data=parent/'tsm_normalized.parquet';pq.write_table(pa.Table.from_pylist(rows),data)
    mp=parent/'final_gate_manifest.json';mp.write_text(json.dumps({'results':[{'symbol':'TSM','published_sha256':D.file_sha(data)}]}))
    plan=D.plan_conversion([{'path':str(data),'expected_sha256':D.file_sha(data),'lineage_ref':'SYNTHETIC_ONLY'}],
        parent_manifest_path=mp,expected_parent_manifest_sha256=D.file_sha(mp),window=D.LONG_WINDOW)
    approval=dict(status='APPROVED',approved_by='hh4832',approval_ref=C.APPROVAL.replace('#','?utm_source=chatgpt.com#'),plan_sha256=plan['plan_sha256'],
                  allow_full_source_opaque_hash=True,allow_holdout_decode_for_conversion_only=True,allow_strategy_run=False)
    bundle=D.produce_development_snapshot(plan,tmp_path/'outputs',producer_sha=C.PRODUCER,approval=approval)
    monkeypatch.setattr(C,'SNAPSHOT',bundle['snapshot_pin']['snapshot_id']);monkeypatch.setattr(C,'PLAN',plan['plan_sha256'])
    Droot=Path(bundle['snapshot_root']);(Droot/'FAILED.json').write_text('{"status":"FAILED","error":"SYNTHETIC stale Drive receipt"}')
    R.recover_development_snapshot(bundle,tmp_path/'recovered',tmp_path/'archive',expected_plan_sha256=C.PLAN,
        expected_producer_sha=C.PRODUCER,conversion_approval=approval,recovery_sha=C.CORE)
    shutil.rmtree(parent)
    def forbidden(*a,**kw):raise AssertionError('No conversion or source re-open permitted')
    monkeypatch.setattr(D,'produce_development_snapshot',forbidden);monkeypatch.setattr(D,'plan_conversion',forbidden)
    yield tmp_path/'archive',approval,rows
    monkeypatch.undo()


@pytest.fixture
def archived(archived_template,tmp_path):
    archive,approval,rows=archived_template
    target=tmp_path/'archive';shutil.copytree(archive,target)
    return target,dict(approval),rows


def test_archive_observations_are_not_trusted_and_runtime_receipt_is_local(archived,tmp_path,monkeypatch):
    archive,approval,_=archived
    observed=C.inspect_archive(archive,conversion_approval=approval)
    assert observed['status']=='OBSERVED_NOT_TRUSTED'
    verified=C.inspect_archive(archive,conversion_approval=approval,trusted_pins=observed['observed_pins'])
    assert verified['status']=='REVIEWED_PINS_VERIFIED' # explicit fixture reviewer input, not automated promotion
    staged=C.stage_archive(verified,tmp_path/'staging')
    assert staged['preflight']['status']=='PASS'
    assert staged['bundle']['integrity_receipt_sha256']!=verified['bundle']['integrity_receipt_sha256']
    assert (Path(staged['local_root'])/'FAILED.json').read_bytes()==(archive/C.SNAPSHOT/'FAILED.json').read_bytes()
    before={str(p.relative_to(C.ROOT)):C.sha(p) for base in ('governance','registry') for p in (C.ROOT/base).rglob('*') if p.is_file()}
    from strategy_agent.research_only import core_phase4a as P
    def no_strategy(*a,**kw):raise AssertionError('PREPARE_ONLY cannot execute strategy')
    monkeypatch.setattr(P,'execute_batch',no_strategy)
    prep=C.prepare(staged,tmp_path/'prepared',agent_commit='a'*40)
    assert prep['consumed_batches']==0 and prep['execution']=='NOT_EXECUTED'
    assert prep['ready_to_submit_formal_authorization'] is True
    assert prep['cost_scenario_compatibility']['status']=='PASS'
    cfg=json.loads(Path(prep['config_path']).read_text())
    assert cfg['windows']==C.WINDOW and cfg['authorization_id']==C.AUTHORIZATION
    assert cfg['dataset']['producer_commit_sha']==C.PRODUCER and cfg['core']['phase4a_sha']==C.CORE
    assert cfg['strategy']['version']=='0.2.0'
    assert cfg['strategy']['spec_file_sha256']==C.sha(Path(prep['artifact_root'])/C.SPEC)
    draft=json.loads((tmp_path/'prepared/authorization.DRAFT_NOT_APPROVED.json').read_text())
    assert draft['status']=='DRAFT_NOT_APPROVED' and draft['proposal_fields']['max_batches']==1
    assert draft['proposed_validity_days']==7
    assert before=={str(p.relative_to(C.ROOT)):C.sha(p) for base in ('governance','registry') for p in (C.ROOT/base).rglob('*') if p.is_file()}
    with pytest.raises(FileExistsError):C.stage_archive(verified,tmp_path/'staging')


@pytest.mark.parametrize('mutation',['marker','manifest','lineage','parquet','approval','plan','reviewed_pin'])
def test_corrupt_or_cross_snapshot_evidence_fails_closed(archived,mutation):
    archive,approval,_=archived
    if mutation=='reviewed_pin':
        pins=C.inspect_archive(archive,conversion_approval=approval)['observed_pins'];pins['manifest_sha256']='0'*64
        with pytest.raises(ValueError,match='reviewed pin mismatch'):C.inspect_archive(archive,conversion_approval=approval,trusted_pins=pins)
        return
    if mutation=='approval':approval={**approval,'plan_sha256':'0'*64}
    elif mutation=='plan':
        p=archive/'recovery_provenance.json';o=json.loads(p.read_text());o['original_plan_sha256']='0'*64;p.write_text(json.dumps(o))
        m=archive/'RECOVERY_VERIFIED.json';o=json.loads(m.read_text());o['recovery_provenance_sha256']=C.sha(p);m.write_text(json.dumps(o))
    else:
        name={'marker':'RECOVERY_VERIFIED.json','manifest':C.SNAPSHOT+'/manifest.json',
              'lineage':C.SNAPSHOT+'/lineage.json','parquet':C.SNAPSHOT+'/tsm_development_1m.parquet'}[mutation]
        with (archive/name).open('ab') as f:f.write(b'corruption')
    with pytest.raises(Exception):C.inspect_archive(archive,conversion_approval=approval)


def test_holdout_footer_is_refused_before_parquet_hash(archived,monkeypatch):
    archive,approval,_=archived
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq
    from strategy_agent.adapters.mrc_phase4a import core_development
    D,_=core_development();root=archive/C.SNAPSHOT;mp=root/'manifest.json';m=json.loads(mp.read_text());p=root/m['parquet_path']
    table=pq.read_table(p);row=table.slice(0,1).to_pylist()[0];row['timestamp_utc']=pd.Timestamp('2025-01-01T05:00Z').to_pydatetime()
    extra=pa.Table.from_pylist([row],schema=table.schema)
    with pq.ParquetWriter(p,table.schema) as w:w.write_table(table);w.write_table(extra)
    original=D.file_sha
    def guard(path):
        assert Path(path)!=p,'Holdout bytes hashed'
        return original(path)
    monkeypatch.setattr(D,'file_sha',guard)
    with pytest.raises(Exception,match='DERIVED_ROW_GROUP'):C.inspect_archive(archive,conversion_approval=approval)


def test_monthly_reads_preserve_native_bars_sma_positions_equity_and_costs(archived,tmp_path):
    import pandas as pd
    from strategy_agent.adapters.mrc_phase4a import core_development,core_phase4a
    from strategy_agent.adapters.mrc_adapter import _core
    from strategy_agent.research_only import core_phase4a as P
    archive,approval,_=archived
    s=C.stage_archive(C.inspect_archive(archive,conversion_approval=approval),tmp_path/'local')
    b=P.SnapshotBinding.from_config({'snapshot_binding':{**s['bundle'],'diagnostic_parquet_relative_path':None}})
    D,N=core_development();_,costs=core_phase4a();core=_core()
    loaded=P.load_continuous_native(N,s['local_root'],b,dates=('2024-05-29','2024-07-12'),
        research_window=('2016-10-19','2024-12-31'),holdout=(('2025-01-01','2026-10-16'),))
    assert len(loaded.provenance['bounded_read_windows'])==2
    assert not loaded.native.timestamp_utc.duplicated().any()
    reference=core.prepare_conservative_bars(loaded.source_1m,bar_minutes=15)
    pd.testing.assert_frame_equal(reference.reset_index(drop=True),loaded.conservative_15m.reset_index(drop=True))
    cfg=json.loads((C.ROOT/'configs/research_only/tsm_sma_5_20_p5b3c_2024_06_dev_18350b3adba8e28a7e5e.json').read_text())
    profiles=P.research_profiles(costs,sec_config=C.ROOT/cfg['core_costs']['sec_config_path'],
        sec_sha=cfg['core_costs']['sec_configuration_sha256'],taf_record=C.ROOT/cfg['core_costs']['taf_record_path'],
        taf_sha=cfg['core_costs']['taf_record_sha256'],scenarios=['BASE','LOW','STRESS','CAT_STRESS'])
    # Same-rate synthetic 2024 interval only. NEVER extrapolated in production to 2017-2024.
    profiles={k:replace(v,configuration_metadata={**v.configuration_metadata,'effective_from':'2024-05-29','effective_to':'2024-07-12'}) for k,v in profiles.items()}
    spec=json.loads((C.ROOT/'specs/research/sma_crossover_tsm_15m_5_20_v0_1_1.json').read_text())
    kw=dict(core_sha=C.CORE,profiles=profiles,spec=spec,parameters={'fast':5,'slow':20},
            window_start='2024-06-03T00:00:00-04:00',window_end='2024-07-13T00:00:00-04:00',
            warmup_start='2024-05-29T00:00:00-04:00',dataset=cfg['dataset'])
    a=P.execute_batch(core,costs,frame=loaded.conservative_15m,**kw)
    ref=P.execute_batch(core,costs,frame=reference,**kw)
    assert a['decisions']==ref['decisions'] and a['orders']==ref['orders']
    boundary=pd.Timestamp('2024-06-29',tz='America/New_York')
    assert any(pd.Timestamp(t['entry_ts'])<boundary<pd.Timestamp(t['exit_ts'])
               for t in a['scenarios']['BASE']['report'].round_trips), 'fixture must carry a position over the chunk boundary'
    for name in profiles:
        for field in ('fills','equity_curve'):
            av=getattr(a['scenarios'][name]['result'],field);bv=getattr(ref['scenarios'][name]['result'],field)
            pd.testing.assert_frame_equal(av,bv)
        assert a['scenarios'][name]['report'].round_trips==ref['scenarios'][name]['report'].round_trips
        assert a['scenarios'][name]['cost_diagnostics']==ref['scenarios'][name]['cost_diagnostics']


def test_unauthorized_execution_cannot_dispatch_or_consume_a_batch(tmp_path,monkeypatch):
    C.save(tmp_path/'readiness.json',{'data_binding':'REVIEWED_PINS_VERIFIED'})
    monkeypatch.setattr(C.B,'_authorization',lambda *a:{'max_batches':1,'executor':{'executor_id':C.B.EXECUTOR}})
    def forbidden(*a,**kw):raise AssertionError('B3-C dispatch must not start without its complete authorization inputs')
    monkeypatch.setattr(C.B,'execute',forbidden)
    before=(C.ROOT/'registry/research_authorization_uses.jsonl').read_bytes()
    with pytest.raises(ValueError,match='P5C1_READINESS_NOT_COMPLETE'):
        C.execute_authorized(tmp_path,governance_root=C.ROOT)
    assert before==(C.ROOT/'registry/research_authorization_uses.jsonl').read_bytes()


def test_drift_semantics_and_fixed_decompositions():
    from datetime import datetime,timezone
    from strategy_agent.evaluation.controls import drift_matched,c1_decompositions
    bars=[{'bar_end':datetime(2024,6,3,14,i,tzinfo=timezone.utc),'close':100+i} for i in range(5)]
    trips=[{'entry_ts':bars[0]['bar_end'],'exit_ts':bars[-1]['bar_end'],'entry_price':100.,'qty':100.,'net_pnl':10.}]
    m=drift_matched(trips,bars)
    assert m['method']=='DETERMINISTIC_UNCONDITIONAL_DRIFT'
    assert m['random_entry_simulation'] is False and m['independent_sample_test'] is False
    d=c1_decompositions(trips,bars)
    assert set(d['by_year'])==set(map(str,range(2017,2025)))
    assert set(d['by_half'])=={'2017-2020','2021-2024'}
    assert d['seen_month_sensitivity']['including']['trade_count']==1
    assert d['seen_month_sensitivity']['excluding']['trade_count']==0
    assert d['by_holding_period_bars']['1-4']['trade_count']==1


def test_real_multiyear_cost_window_stays_fail_closed():
    from strategy_agent.research_only.costs import CostSet,CostSetError
    costs=CostSet(C.ROOT/'configs/research_only/ibkr_pro_fixed_tsm_research_only_costs_v1.json',root=C.ROOT)
    with pytest.raises(CostSetError,match='rate changes'):
        costs.check_window(date(2016,10,19),date(2024,12,31))


def test_official_dated_schedule_covers_all_years_and_rejects_sha_or_source_drift(tmp_path):
    from strategy_agent.adapters.mrc_phase4a import core_repository_root
    schedules=C.load_dated_schedules(core_repository_root())
    assert set(schedules)=={'BASE','LOW','STRESS','CAT_STRESS'}
    for schedule in schedules.values():
        schedule.require_coverage('2017-01-01','2024-12-31')
        assert schedule.sha256==C.SCHEDULE_SHA256
    with pytest.raises(ValueError,match='canonical SHA mismatch'):
        C.load_dated_schedules(core_repository_root(),expected_sha256='0'*64)
    copied=tmp_path/'core';shutil.copytree(core_repository_root()/'configs',copied/'configs')
    shutil.copytree(core_repository_root()/C.SCHEDULE_SOURCES,copied/C.SCHEDULE_SOURCES)
    copied_doc=json.loads((copied/C.SCHEDULE).read_text())
    digest=copied_doc['sec_rates'][0]['source_document_sha256']
    source=next((copied/C.SCHEDULE_SOURCES).glob(digest+'.*'))
    source.write_bytes(source.read_bytes()+b' tamper')
    with pytest.raises(ValueError,match='source document SHA mismatch'):
        C.load_dated_schedules(copied)


def test_dated_profile_is_selected_by_each_fill_date_across_rate_boundaries():
    from strategy_agent.adapters.mrc_phase4a import core_repository_root
    schedule=C.load_dated_schedules(core_repository_root())['BASE']
    dates=[r['effective_from'] for r in schedule.document['sec_rates']]
    selected={day:schedule.profile_for(day+'T15:00:00Z').configuration_metadata['phase4a_cost_model']['sec_rate_record_sha256']
              for day in dates}
    assert len(set(selected.values()))==len(dates)
    assert schedule.profile_for('2024-12-31T15:00:00Z').configuration_metadata['phase4a_cost_model']['sec_rate_record_sha256']==selected[dates[-1]]


@pytest.mark.parametrize('reference,accepted',[
    (C.APPROVAL,True),
    (C.APPROVAL.replace('#','?utm_source=chatgpt.com#'),True),
    (C.APPROVAL.replace('#','?utm_source=review-note_1~2#'),True),
    (C.APPROVAL.replace('6768321310','6768321311'),False),
    (C.APPROVAL.replace('/pull/4','/pull/5'),False),
    (C.APPROVAL.replace('github.com','github.com.example.com'),False),
    (C.APPROVAL.replace('https://','http://'),False),
    (C.APPROVAL.replace('github.com','user@github.com'),False),
    (C.APPROVAL.replace('github.com','github.com:443'),False),
    (C.APPROVAL.replace('#','?redirect=https://evil.example#'),False),
    (C.APPROVAL.replace('#','?utm_medium=chatgpt.com#'),False),
    (C.APPROVAL.replace('#','?utm_source=chatgpt.com&redirect=x#'),False),
    (C.APPROVAL.replace('#','?utm_source=x&utm_source=y#'),False),
    (C.APPROVAL.replace('#','?utm_source=chatgpt%2ecom#'),False),
    (C.APPROVAL.replace('#','?utm_source=#'),False),
    (C.APPROVAL+'?utm_source=chatgpt.com',False),
    (' '+C.APPROVAL,False),
    (C.APPROVAL+'\n',False),
    (None,False),
])
def test_original_approval_url_only_ignores_allowlisted_tracking(reference,accepted):
    assert C.is_original_approval_url(reference) is accepted


@pytest.mark.parametrize('change',[
    {'approval_ref':C.APPROVAL}, # Same canonical comment, but not the original evidence bytes/content.
    {'approval_ref':C.APPROVAL.replace('#','?utm_source=other#')},
    {'note':'tampered evidence'},
])
def test_url_matching_never_relaxes_original_approval_content(archived,change):
    archive,approval,_=archived
    assert '?utm_source=chatgpt.com#' in approval['approval_ref']
    before=json.dumps(approval,sort_keys=True)
    evidence_before={str(p.relative_to(archive)):C.sha(p) for p in archive.rglob('*') if p.is_file()}
    assert C.inspect_archive(archive,conversion_approval=approval)['status']=='OBSERVED_NOT_TRUSTED'
    with pytest.raises(ValueError,match='original conversion approval evidence differs'):
        C.inspect_archive(archive,conversion_approval={**approval,**change})
    assert json.dumps(approval,sort_keys=True)==before
    assert evidence_before=={str(p.relative_to(archive)):C.sha(p) for p in archive.rglob('*') if p.is_file()}


def test_notebook_prepare_only_accepts_original_tracking_url_without_execution(archived,tmp_path,monkeypatch):
    from strategy_agent.research_only import core_phase4a as P
    from strategy_agent.research_only.runner import ResearchOnlyRunner
    archive,approval,_=archived
    notebook=json.loads((C.ROOT/'notebooks/Phase5C1_Long_Development_Readiness.ipynb').read_text())
    code=[''.join(c['source']) for c in notebook['cells'] if c['cell_type']=='code']
    def forbidden(*a,**kw):raise AssertionError('PREPARE_ONLY must not execute strategy or authorized mode')
    monkeypatch.setattr(P,'execute_batch',forbidden)
    monkeypatch.setattr(ResearchOnlyRunner,'run',forbidden)
    monkeypatch.setattr(C,'execute_authorized',forbidden)
    books_before={str(p.relative_to(C.ROOT)):C.sha(p) for base in ('governance','registry') for p in (C.ROOT/base).rglob('*') if p.is_file()}
    context=dict(C=C,Path=Path,json=json,shutil=shutil,
        ORIGINAL_APPROVAL_EVIDENCE_PATH=archive/'recovery_provenance.json',APPROVAL_IS_IN_RECOVERY_RECORD=True,
        ORIGINAL_CONVERSION_APPROVAL_REF=C.APPROVAL,RECOVERY_ARCHIVE=archive,REVIEWED_DATA_PINS=None,
        LOCAL_SESSION=tmp_path/'notebook-session',MODE='PREPARE_ONLY',AGENT_SHA='FETCH_BRANCH_HEAD',FETCHED_AGENT_SHA='a'*40,CORE_SHA=C.CORE,
        AGENT_ROOT=C.ROOT,SNAPSHOT_ID=C.SNAPSHOT,PIN_REVIEW_EVIDENCE_REF='',DRIVE_OUTPUT=tmp_path/'evidence-archive')
    # Execute the actual validation/staging and preparation/archive cells, not a parallel notebook implementation.
    exec(compile(code[2],'notebook-archive-cell','exec'),context)
    exec(compile(code[3],'notebook-prepare-cell','exec'),context)
    result=context['result']
    assert result['mode']=='PREPARE_ONLY' and result['execution']=='NOT_EXECUTED' and result['consumed_batches']==0
    assert context['approval']==approval and '?utm_source=chatgpt.com#' in context['approval']['approval_ref']
    assert (context['DRIVE_OUTPUT']/'ARCHIVE_INDEX.json').is_file()
    assert books_before=={str(p.relative_to(C.ROOT)):C.sha(p) for base in ('governance','registry') for p in (C.ROOT/base).rglob('*') if p.is_file()}
