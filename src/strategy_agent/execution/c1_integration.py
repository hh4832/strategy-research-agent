"""Phase 5-C1: reviewed Development bytes -> dated-cost preflight -> existing gated B3-C pipeline.

PREPARE_ONLY never consumes, signs or executes. Observed hashes are not trusted pins. The official Dated Cost Schedule
is loaded independently for each fixed scenario and Core prices every Fill in one continuous full-window execution.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path
import re
import shutil
from zoneinfo import ZoneInfo

from strategy_agent.execution import b3c_run as B
from strategy_agent.research_only.runner import canonical_sha

ROOT=B.ROOT
SNAPSHOT='TSM_DEV_1M_20161019_20241231_ca9f87df83f31bd24211'
PRODUCER='7bcc61813a6cf2956c6df69ea796bf1c7f2b495f'
PLAN='4dd605385229b1b062fccbe5900b097cff3525fe13f66964273f8e63166f286a'
CORE='50ea3bfa42df7708dd3a1b21f17798c206be054e'
SCHEDULE='configs/research/tsm_official_dated_costs_2017_2024.json'
SCHEDULE_SHA256='43b6045758760a40ad88d741b1a97a73cfb0b6cf6b2a4a7f1f3b31d5085a4216'
SCHEDULE_SOURCES='docs/evidence/phase5c1_official_rates/sources'
SCENARIOS=('BASE','LOW','STRESS','CAT_STRESS')
APPROVAL='https://github.com/hh4832/market-research-core/pull/4#issuecomment-6768321310'
AUTHORIZATION='RA-IBKR_TSM-SMA520_P5C1-0001'
SPEC='specs/research/c1/tsm_sma520_v0_2_0.json'
CONFIG='configs/research_only/tsm_sma520_p5c1.json'
WINDOW={'warmup_start':'2016-10-19T00:00:00-04:00','start':'2017-01-01T00:00:00-05:00','end':'2025-01-01T00:00:00-05:00'}
PIN_KEYS={'marker_sha256','recovery_provenance_sha256','binding_sha256','manifest_sha256','lineage_sha256','parquet_sha256'}
SEC_MAPPING_CONVENTION='NEW_YORK_FILL_DATE_RESEARCH_ASSUMPTION_NOT_BROKER_ACTUAL'


def is_original_approval_url(reference: str) -> bool:
    """Match only the pinned HTTPS comment, optionally with one literal utm_source.

    Tracking values are 1–128 ASCII unreserved characters. Other parameters,
    duplicates, escapes and URL identity changes are refused. Evidence dictionaries
    remain untouched and must still match lineage/provenance in full.
    """
    if not isinstance(reference,str):return False
    permalink,fragment=APPROVAL.split('#',1)
    return re.fullmatch(re.escape(permalink)+r'(?:\?utm_source=[A-Za-z0-9._~-]{1,128})?#'+re.escape(fragment),reference) is not None


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def inside(root,relative):
    if not isinstance(relative,str) or Path(relative).is_absolute() or '..' in Path(relative).parts:
        raise ValueError('unsafe archive path')
    p=(root/relative).resolve(strict=True)
    if not p.is_relative_to(root) or not p.is_file():raise ValueError('archive path escapes root')
    return p


def save(path,value):
    B._write_once(Path(path),B._dumps(value))


def load_dated_schedules(root, *, expected_sha256=SCHEDULE_SHA256):
    """Load all four schedules through the exact Core API, with every pinned official source byte verified."""
    from strategy_agent.adapters.mrc_phase4a import core_dated_costs
    root=Path(root)
    path=root/SCHEDULE
    source_root=root/SCHEDULE_SOURCES
    if not path.is_file():
        raise ValueError('dated cost schedule file is missing')
    document=json.loads(path.read_text())
    if document.get('schedule_sha256')!=expected_sha256 or document.get('schedule_sha256')!=SCHEDULE_SHA256:
        raise ValueError('dated cost schedule canonical SHA mismatch')
    hashes={r['source_document_sha256'] for r in document['sec_rates']+document['taf_rates']}
    sources={}
    for digest in hashes:
        matches=list(source_root.glob(digest+'.*'))
        if len(matches)!=1:
            raise ValueError(f'official source document missing or ambiguous: {digest}')
        sources[digest]=matches[0]
    if any(not p.is_file() or sha(p)!=digest for digest,p in sources.items()):
        raise ValueError('official SEC/FINRA TAF source document SHA mismatch')
    loader=core_dated_costs().load_dated_cost_schedule
    schedules={name:loader(path,expected_sha256=expected_sha256,scenario=name,source_documents=sources)
               for name in SCENARIOS}
    return schedules


def validate_cost_scenario_compatibility(root, schedules):
    """The existing four scenario assumptions must match the immutable Core Schedule byte-for-byte in meaning."""
    from decimal import Decimal
    root=Path(root)
    config=json.loads((root/'configs/research_only/ibkr_pro_fixed_tsm_research_only_costs_v1.json').read_text())
    failures=[]
    if tuple(config['scenarios'])!=SCENARIOS:
        failures.append('fixed four scenario names/order changed')
    for name in SCENARIOS:
        expected=schedules[name].document['scenarios'][name]
        actual=config['scenarios'][name]
        if Decimal(str(expected['slippage_bps']))!=Decimal(str(actual['slippage_bps'])):
            failures.append(f'{name} slippage differs from pinned Core schedule')
        if Decimal(str(expected['cat_per_share']))!=Decimal(str(actual['cat_per_share'])):
            failures.append(f'{name} CAT differs from pinned Core schedule')
    base=schedules['BASE'].base_profile
    if (Decimal(str(base.equity_commission_per_share))!=Decimal(str(config['commission']['per_share']))
            or Decimal(str(base.minimum_commission))!=Decimal(str(config['commission']['minimum_per_order']))
            or Decimal(str(base.commission_cap_rate))!=Decimal(str(config['commission']['cap_rate_of_notional']))
            or Decimal(str(base.half_spread_bps or 0))!=Decimal(str(config['half_spread_bps']))):
        failures.append('fixed commission/minimum/cap/spread differs from pinned Core base profile')
    for name,schedule in schedules.items():
        d=schedule.document
        if d['schedule_sha256']!=SCHEDULE_SHA256 or set(d['scenarios'])!=set(SCENARIOS):
            failures.append(f'{name} schedule identity/scenario coverage mismatch')
        schedule.require_coverage('2017-01-01','2024-12-31')
        model=schedule.profile_for('2017-01-03T15:00:00-05:00').configuration_metadata['phase4a_cost_model']
        if (model.get('taf_side')!='BOTH' or model.get('taf_cap') is not None
                or model.get('cat_side')!='BOTH' or model.get('sec_side')!='SELL'
                or model.get('formal_cost_approval') is not False
                or model.get('cost_schedule_sha256')!=SCHEDULE_SHA256):
            failures.append(f'{name} dated cost model semantics differ from the fixed research assumptions')
    if failures:
        raise ValueError('; '.join(failures))
    return {'status':'PASS','scenarios':list(SCENARIOS),'schedule_sha256':SCHEDULE_SHA256,
            'scope':'RESEARCH_ONLY','sec_date_convention':SEC_MAPPING_CONVENTION,
            'official_sec_rates':'VERIFIED_SOURCE_RECORDS','official_finra_taf_rates':'VERIFIED_SOURCE_RECORDS',
            'taf_cat_assumption':'BOTH_SIDE_NO_CAP_CONSERVATIVE_RESEARCH_ASSUMPTION'}


def inspect_archive(archive_root, *, conversion_approval: dict, trusted_pins: dict | None=None):
    """Verify only the given recovery archive. Does not open any conversion-source path.

    Without independently reviewed pins results stay OBSERVED_NOT_TRUSTED.
    The archived local receipt is retained as historical evidence, never reused.
    """
    from strategy_agent.adapters.mrc_phase4a import core_development
    dev,native=core_development()
    identity=native.core_code_identity()
    if identity['core_git_sha']!=CORE or identity['core_tree_dirty'] is not False:
        raise ValueError('clean pinned Core required')
    root=Path(archive_root).resolve(strict=True)
    marker_path=inside(root,'RECOVERY_VERIFIED.json');marker=json.loads(marker_path.read_text())
    rp=inside(root,'recovery_provenance.json')
    if marker.get('status')!='PASS' or marker.get('recovery_provenance_sha256')!=sha(rp):
        raise ValueError('recovery marker/provenance SHA mismatch')
    rec=json.loads(rp.read_text())
    if (rec.get('snapshot_id'),rec.get('original_producer_sha'),rec.get('original_plan_sha256'))!=(SNAPSHOT,PRODUCER,PLAN):
        raise ValueError('Snapshot/Producer/Plan mismatch')
    if rec.get('recovery_core_sha') not in (CORE,'21d0936b467fc1ea94ab434e086b212bfb6868f4'):
        raise ValueError('unreviewed recovery Core')
    if (rec.get('preflight_status')!='PASS' or rec.get('output_holdout_rows')!=0
            or rec.get('conversion_performed') is not False or rec.get('original_source_opened') is not False):
        raise ValueError('recovery isolation evidence missing')
    sr=(root/SNAPSHOT).resolve(strict=True)
    if not sr.is_relative_to(root):raise ValueError('snapshot escapes archive')
    bp=inside(sr,'binding.json');bundle=json.loads(bp.read_text());bundle['snapshot_root']=str(sr)
    if (bundle['snapshot_pin']['snapshot_id'],bundle['snapshot_pin']['producer_sha'])!=(SNAPSHOT,PRODUCER):
        raise ValueError('binding identity mismatch')
    doc,data=dev._development_file(bundle)  # pinned metadata + all daily footers BEFORE Parquet body copying/hash
    line=json.loads(inside(sr,doc['lineage_path']).read_text())
    if line['plan_sha256']!=PLAN:raise ValueError('lineage plan mismatch')
    dev._validate_approval(line['producer_plan'],conversion_approval)
    if (not is_original_approval_url(conversion_approval.get('approval_ref'))
            or conversion_approval!=line.get('conversion_approval')
            or conversion_approval!=rec.get('conversion_approval')):
        raise ValueError('original conversion approval evidence differs')
    # Whitelist archived snapshot artifacts; unknown entries are refused, not opened.
    allowed={'binding.json',bundle['manifest_relative_path'],doc['lineage_path'],doc['parquet_path'],'FAILED.json','preflight.json'}
    inventory=rec.get('snapshot_files_sha256',{})
    if not {bp.name,bundle['manifest_relative_path'],doc['lineage_path'],doc['parquet_path']}<=set(inventory) or set(inventory)-allowed:
        raise ValueError('archive inventory mismatch')
    for name,expected in inventory.items():
        if sha(inside(sr,name))!=expected:raise ValueError('archive file SHA mismatch: '+name)
    observed={'marker_sha256':sha(marker_path),'recovery_provenance_sha256':sha(rp),'binding_sha256':sha(bp),
              'manifest_sha256':sha(inside(sr,bundle['manifest_relative_path'])),
              'lineage_sha256':sha(inside(sr,doc['lineage_path'])),'parquet_sha256':sha(data)}
    if observed['parquet_sha256']!=doc['parquet_sha256']:raise ValueError('Parquet SHA mismatch')
    if trusted_pins is not None:
        if set(trusted_pins)!=PIN_KEYS or any(not re.fullmatch('[0-9a-f]{64}',str(v)) for v in trusted_pins.values()):
            raise ValueError('complete independently reviewed pins required')
        if trusted_pins!=observed:raise ValueError('reviewed pin mismatch')
    return {'status':'REVIEWED_PINS_VERIFIED' if trusted_pins is not None else 'OBSERVED_NOT_TRUSTED',
            'observed_pins':observed,'bundle':bundle,'snapshot_root':str(sr),'snapshot_files':inventory,
            'original_producer_sha':PRODUCER,'recovery_core_sha':rec['recovery_core_sha'],
            'reader_core_sha':CORE,'conversion_plan_sha256':PLAN,'strategy_execution':'NOT_EXECUTED'}


def stage_archive(inspection: dict,local_directory):
    """Fresh local copy, SHA-checked, Core-renewed runtime receipt and full data preflight.

    Observed-only staging is preparation evidence; it is not authorization.
    """
    from strategy_agent.adapters.mrc_phase4a import core_development
    dev,_=core_development()
    out=Path(local_directory).resolve();source=Path(inspection['snapshot_root']).resolve(strict=True)
    if out.is_relative_to(source) or source.is_relative_to(out):raise ValueError('local staging overlaps source')
    out.mkdir(parents=True,exist_ok=False);staged=out/SNAPSHOT;staged.mkdir()
    for name,expected in inspection['snapshot_files'].items():
        src=inside(source,name);dest=staged/name;dest.parent.mkdir(parents=True,exist_ok=True)
        with src.open('rb') as f,dest.open('xb') as w:shutil.copyfileobj(f,w)
        if sha(dest)!=expected:raise ValueError('staged file SHA mismatch')
    bundle=copy.deepcopy(inspection['bundle']);bundle['snapshot_root']=str(staged)
    renewed=dev.renew_development_receipt(bundle)
    report=dev.preflight_development_snapshot(renewed)
    if report['status']!='PASS':raise ValueError('Core preflight failed')
    save(out/'runtime_binding.json',renewed);save(out/'data_preflight.json',report)
    save(out/'pin_summary.json',{k:v for k,v in inspection.items() if k not in ('bundle','snapshot_files')})
    return {'bundle':renewed,'preflight':report,'inspection':inspection,'local_root':str(staged)}


def prepare(staged: dict,output_directory, *, agent_commit: str,root=ROOT):
    """Draft artifacts only. Never calls ResearchOnlyRunner/run/budget/admin approval.

    Emits a self-contained artifact root for the existing exporter and runner.
    Unresolved pins remain clearly labelled; official schedule and source bytes are included in the draft artifact.
    """
    from strategy_agent.execution import c1_proposal as P
    from strategy_agent.proposal import FileDraftAdapter,draft_proposal
    out=Path(output_directory).resolve();out.mkdir(parents=True,exist_ok=False);root=Path(root)
    if not re.fullmatch('[0-9a-f]{40}',agent_commit):raise ValueError('full Agent commit required')
    artifact=out/'artifacts'
    cfg=json.loads((root/'configs/research_only/tsm_sma_5_20_p5b3c_2024_06_dev_18350b3adba8e28a7e5e.json').read_text())
    spec=json.loads((root/P.SPEC).read_text());bundle=staged['bundle']
    spec['data_requirements']['datasets'][0].update(snapshot_id=SNAPSHOT,producer_commit_sha=PRODUCER,
          manifest_sha256=bundle['manifest_sha256'],access_contract='Core Development Contract v1.2; reviewed pins required')
    spec['provenance']['spec_sha256']=None
    save(artifact/SPEC,spec)
    cfg.update(authorization_id=AUTHORIZATION,run_config_id='tsm_sma520_p5c1',purpose='PRE_REGISTERED_HYPOTHESIS_TEST')
    cfg['dataset'].update(snapshot_id=SNAPSHOT,producer_commit_sha=PRODUCER)
    cfg['windows']=dict(WINDOW)
    cfg['strategy'].update(spec_path=SPEC,spec_file_sha256=sha(artifact/SPEC),version='0.2.0')
    cfg['core'].update(phase4a_sha=CORE,phase4a_contract_ref='https://github.com/hh4832/market-research-core/blob/'+CORE+'/docs/TSM_DEVELOPMENT_SNAPSHOT_CONTRACT.md')
    cfg['snapshot_binding']={k:bundle[k] for k in ('manifest_relative_path','manifest_sha256','layout','integrity_receipt','integrity_receipt_sha256','snapshot_pin')}
    cfg['snapshot_binding']['diagnostic_parquet_relative_path']='tsm_development_1m.parquet'
    now=datetime.now(ZoneInfo('Asia/Taipei')).isoformat(timespec='seconds')
    cfg['provenance'].update(created_at=now,created_by='Phase5C1 PREPARE_ONLY',config_sha256=None)
    from strategy_agent.adapters.mrc_phase4a import core_repository_root
    core_root=core_repository_root()
    schedule_target=artifact/SCHEDULE
    schedule_target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(core_root/SCHEDULE,schedule_target)
    source_target=artifact/SCHEDULE_SOURCES
    source_target.mkdir(parents=True,exist_ok=True)
    for source in sorted((core_root/SCHEDULE_SOURCES).iterdir()):
        if source.is_file():shutil.copyfile(source,source_target/source.name)
    cfg['core_costs'].update(dated_schedule_path=SCHEDULE,dated_schedule_sha256=SCHEDULE_SHA256,
                             dated_schedule_source_directory=SCHEDULE_SOURCES,
                             sec_fill_date_convention=SEC_MAPPING_CONVENTION)
    cfg['provenance']['config_sha256']=canonical_sha(cfg)
    save(artifact/CONFIG,cfg)
    for path in (cfg['costs']['scenario_set_path'],cfg['core_costs']['sec_config_path'],cfg['core_costs']['taf_record_path']):
        target=artifact/path;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(root/path,target)
    auth=json.loads((root/'docs/phase5b3c/authorization_draft.json').read_text())['proposal_fields']
    auth.update(authorization_id=AUTHORIZATION,max_batches=1,issued_at='HUMAN_DECISION_REQUIRED',expires_at='HUMAN_DECISION_REQUIRED')
    auth['executor']={'consistency':'SINGLE_EXECUTOR_ONLY','executor_id':B.EXECUTOR}
    auth['dataset']=copy.deepcopy(cfg['dataset']);auth['research_window']={'interval':'[start,end)','start':WINDOW['warmup_start'],'end':WINDOW['end']}
    auth['strategy'].update(spec_path=SPEC,spec_file_sha256=sha(artifact/SPEC),version='0.2.0')
    auth['costs']['dated_schedule_path']=SCHEDULE
    auth['costs']['dated_schedule_sha256']=SCHEDULE_SHA256
    auth['does_not_authorize']=['Holdout access','any second batch or retry','formal GO','any change of rules/costs/pins',
        'any executor other than colab-hh4832-exec-01','BROKER_ACTUAL costs or any alternative SEC date mapping']
    save(out/'authorization.DRAFT_NOT_APPROVED.json',{'status':'DRAFT_NOT_APPROVED','proposed_validity_days':7,'proposal_fields':auth})
    fx=json.loads((root/'fixtures/proposal/complete_momentum.json').read_text())
    completion=P.build_completion(spec,challenger_view=fx['llm_draft']['challenger_view'])
    save(out/'completion.json',completion)
    proposal=draft_proposal(spec['hypothesis']['statement'],FileDraftAdapter(out/'completion.json',adapter_id=P.ADAPTER_ID),
        agent_commit_sha=agent_commit,created_at=now)
    if proposal['status']!='PROPOSED':raise ValueError(proposal['validation'])
    save(out/'proposal.json',proposal)
    schedules=load_dated_schedules(artifact)
    cost_compatibility=validate_cost_scenario_compatibility(root,schedules)
    from strategy_agent.research_only.runner import ResearchOnlyRunner
    runner=ResearchOnlyRunner(root=artifact,
        locks_path=root/'governance/holdout_locks.jsonl',authorizations_path=root/'governance/research_authorizations.jsonl',
        uses_path=root/'registry/research_authorization_uses.jsonl',approvers_path=root/'governance/approvers.json',
        readiness_path=root/'governance/market_readiness.json')
    non_consuming_preflight=runner.preflight(artifact/CONFIG).as_dict()
    blockers=[]
    if staged['inspection']['status']!='REVIEWED_PINS_VERIFIED':blockers.append('REAL_DATA_PINS_PENDING_RESEARCH_LEAD_REVIEW')
    summary={'mode':'PREPARE_ONLY','data_binding':staged['inspection']['status'],'observed_pins':staged['inspection']['observed_pins'],
        'core_reader_sha':CORE,'original_producer_sha':PRODUCER,'recovery_core_sha':staged['inspection']['recovery_core_sha'],
        'config_path':str(artifact/CONFIG),'artifact_root':str(artifact),'authorization_id':AUTHORIZATION,
        'authorization':'NOT_GRANTED','execution':'NOT_EXECUTED','consumed_batches':0,'family_trials':3,
        'cost_schedule_sha256':SCHEDULE_SHA256,'cost_scenario_compatibility':cost_compatibility,
        'sec_fill_date_convention':SEC_MAPPING_CONVENTION,
        'official_sec_rates':'VERIFIED','official_finra_taf_rates':'VERIFIED',
        'taf_cat_assumption':'BOTH_SIDE_NO_CAP_RESEARCH_ONLY',
        'formal_market_gate':'NO_GO_UNCHANGED','non_consuming_runner_preflight':non_consuming_preflight,
        'ready_to_submit_formal_authorization':not blockers,
        'blockers':blockers,'agent_commit':agent_commit}
    save(out/'readiness.json',summary)
    return summary


def execute_authorized(preparation_directory, *, governance_root, **execution_options):
    """Dispatch only through B3-C's full GitHub approval, A01-A10, REAL_GATED and one-Batch pipeline."""
    out=Path(preparation_directory)
    summary=json.loads((out/'readiness.json').read_text())
    if summary['data_binding']!='REVIEWED_PINS_VERIFIED':raise ValueError('REAL_DATA_PINS_PENDING')
    if not summary.get('ready_to_submit_formal_authorization') or summary.get('cost_schedule_sha256')!=SCHEDULE_SHA256:
        raise ValueError('P5C1_READINESS_NOT_COMPLETE')
    auth=B._authorization(Path(governance_root),AUTHORIZATION)
    if (auth.get('max_batches')!=1 or auth.get('executor',{}).get('executor_id')!=B.EXECUTOR
            or auth.get('costs',{}).get('dated_schedule_sha256')!=SCHEDULE_SHA256):
        raise ValueError('P5C1_AUTHORIZATION_SCOPE')
    required={'bind_url','task_url','verifier','backend','work_dir','claim_dir'}
    missing=required-set(execution_options)
    if missing:raise ValueError('P5C1_B3C_EXECUTION_INPUTS_MISSING: '+','.join(sorted(missing)))
    artifact_root=out/'artifacts'
    return B.execute(execution_options.pop('prepared_dir'),bind_url=execution_options.pop('bind_url'),
        task_url=execution_options.pop('task_url'),verifier=execution_options.pop('verifier'),
        backend=execution_options.pop('backend'),work_dir=execution_options.pop('work_dir'),
        claim_dir=execution_options.pop('claim_dir'),artifact_root=artifact_root,
        governance_root=Path(governance_root),executor_id=B.EXECUTOR,production_books=True,**execution_options)


def prepare_governed_request(preparation_directory, *, governance_root,agent_commit):
    """Use the existing B3-C A2 exporter and exact GitHub approval lines after formal authorization exists."""
    out=Path(preparation_directory);summary=json.loads((out/'readiness.json').read_text())
    if summary['data_binding']!='REVIEWED_PINS_VERIFIED':raise ValueError('reviewed real data pins required')
    if not summary.get('ready_to_submit_formal_authorization') or summary.get('cost_schedule_sha256')!=SCHEDULE_SHA256:
        raise ValueError('P5C1_READINESS_NOT_COMPLETE')
    B._authorization(Path(governance_root),AUTHORIZATION)
    return B.prepare(out/'governed_request',artifact_root=out/'artifacts',governance_root=Path(governance_root),
        authorization_id=AUTHORIZATION,canonical_config=out/'artifacts'/CONFIG,agent_commit=agent_commit,
        completion=json.loads((out/'completion.json').read_text()),
        hypothesis=json.loads((out/'proposal.json').read_text())['hypothesis']['question'],
        adapter_id='CLAUDE_AUTHORED_P5C1_COMPLETION_FILE',manifest_kind='PHASE5C1_PREPARE_MANIFEST')
