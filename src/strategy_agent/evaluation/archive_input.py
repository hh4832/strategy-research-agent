"""Read-only Phase 4-A archive normalization; no source data, executor or governance imports.

Only named result artifacts are opened. Raw bars, snapshot paths, Holdout files,
authorization logs and arbitrary manifest entries are NEVER traversed.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
import re

from .universal import available, absent


def phase4a_archive(archive_path, *, expected_manifest_sha256, scenario, context):
    """Context supplies identity absent from current archive, never performance values.

    Expected manifest SHA must be independently pinned by the caller. Missing
    archived trades/holdout/metadata are explicit MISSING; no reconstruction.
    Context is a caller attestation, not independent proof of source Git identity.
    """
    if not re.fullmatch('[0-9a-f]{64}',expected_manifest_sha256):raise ValueError('archive manifest SHA pin required')
    if not re.fullmatch('[A-Za-z0-9_-]+',scenario):raise ValueError('unsafe scenario name')
    root=Path(archive_path).resolve(strict=True)
    manifest_path=root/'MANIFEST.json'
    if manifest_path.is_symlink():raise ValueError('manifest symlink refused')
    raw=manifest_path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected_manifest_sha256:raise ValueError('ARCHIVE_MANIFEST_SHA')
    manifest=json.loads(raw)
    if manifest.get('status')!='COMPLETED':raise ValueError('archive must be COMPLETED; evaluator never resumes or runs it')
    if scenario not in manifest.get('scenarios',{}):raise ValueError('scenario absent from manifest')
    holdout=context.get('holdout',{})
    if holdout.get('status')!='AVAILABLE' or holdout.get('value',{}).get('status')!='NOT_ACCESSED':
        raise ValueError('HOLDOUT: unopened Development attestation required before result artifacts are read')
    if 'HOLDOUT' in str(manifest.get('scope','')).upper():raise ValueError('HOLDOUT archive refused')
    hashes=manifest.get('files_sha256',{})
    def read(name):
        relative=scenario+'/'+name
        if relative not in hashes:return None
        path=root/relative
        if path.is_symlink() or not path.resolve().is_relative_to(root):raise ValueError('UNSAFE_ARCHIVE_PATH')
        data=path.read_bytes()
        if hashlib.sha256(data).hexdigest()!=hashes[relative]:raise ValueError('ARCHIVE_ARTIFACT_SHA:'+relative)
        if name.endswith('.csv'):
            return list(csv.DictReader(io.StringIO(data.decode()))) if data.strip() else []
        return json.loads(data)
    # Fixed allowlist; do NOT verify arbitrary manifest files (could name source/Holdout OHLCV).
    observed={name:read(name) for name in ('metrics.json','fills.csv','trades.csv','rejections.csv',
                                        'equity_curve.json','cost_diagnostics.json','m3a4_checks.json')}
    source=dict(context.get('source',{}))
    if 'run_id' in source and source['run_id']!=manifest.get('run_id'):raise ValueError('SOURCE_RUN_ID')
    source.update(run_id=manifest.get('run_id'),archive_path=str(root))
    core=manifest.get('core_identity',{}).get('core_git_sha')
    if core and source.get('core_sha')!=core:raise ValueError('SOURCE_CORE_SHA')
    provenance=manifest.get('loader_provenance',{})
    for field in ('snapshot_id','manifest_sha256'):
        target={'snapshot_id':'dataset_snapshot','manifest_sha256':'manifest_sha256'}[field]
        if field in provenance and source.get(target)!=provenance[field]:raise ValueError('SOURCE_PROVENANCE:'+field)
    doc={'schema_version':'1.0.0','kind':context.get('kind','RESEARCH_RESULT'),'source':source,
         'sample_role':context.get('sample_role','UNKNOWN'),'equity_sampling':context.get('equity_sampling','UNKNOWN')}
    if 'initial_capital' in context:doc['initial_capital']=context['initial_capital']
    for key in ('holdout','data_quality'):
        doc[key]=context.get(key,absent('not established by Phase 4-A archive; external pinned attestation required','MISSING'))
    for key in ('trades','fills','equity_curve','metrics','rejections','cost_breakdown','execution_diagnostics','budget_consumption'):
        doc[key]=absent('not present or not expressible in current Phase 4-A archive','MISSING')
    for name,key in (('metrics.json','metrics'),('equity_curve.json','equity_curve')):
        if observed[name] is not None:doc[key]=available(observed[name])
    def numeric(row,keys):
        return {k:float(row[k]) for k in keys if row.get(k) not in (None,'')}
    if observed['fills.csv'] is not None:
        doc['fills']=available([{'fill_id':r.get('fill_id') or 'archive-row-'+str(i),'symbol':r['symbol'],
            'exec_ts':r['exec_ts'],**numeric(r,('qty','fill_price','notional')),**({'signal_available_at':r['signal_available_at']} if r.get('signal_available_at') else {})}
            for i,r in enumerate(observed['fills.csv'])])
    if observed['trades.csv'] is not None:
        # Future optional export only; current producer does not supply this file.
        doc['trades']=available([{'trade_id':r.get('trade_id') or 'archive-row-'+str(i),'symbol':r['symbol'],
            'entry_ts':r['entry_ts'],'exit_ts':r['exit_ts'],'status':r['status'],
            **numeric(r,('net_pnl','gross_pnl'))} for i,r in enumerate(observed['trades.csv'])])
    if observed['rejections.csv'] is not None:
        doc['rejections']=available([{'code':r.get('reason') or r.get('code') or 'UNSPECIFIED',
            **({'timestamp':r['exec_ts']} if r.get('exec_ts') else {}),
            **({'order_id':r['order_id']} if r.get('order_id') else {})} for r in observed['rejections.csv']])
    slot=manifest.get('slot')
    if isinstance(slot,dict) and slot.get('authorization_id')==manifest.get('authorization_id') and slot.get('run_id')==manifest.get('run_id'):
        doc['budget_consumption']=available({'authorization_id':manifest['authorization_id'],'batches_consumed':1})
    checks=observed['m3a4_checks.json']
    if isinstance(checks,dict):
        # Preserve raw checks inside diagnostics; never assert temporal safety from an archive completion flag.
        doc['execution_diagnostics']=available({'status':'UNKNOWN','fill_diagnostics':checks})
    official=observed['metrics.json'] or {}
    cost=official.get('costs')
    if isinstance(cost,dict) and all(k in cost for k in ('fees','taxes','regulatory_fees','total')):
        doc['cost_breakdown']=available({'explicit_total':float(cost['total']),
            'components':{k:float(cost[k]) for k in ('fees','taxes','regulatory_fees')},'slippage_accounting':'UNKNOWN'})
    # The envelope deliberately remains blocked when the original archive lacks required evidence.
    return doc
