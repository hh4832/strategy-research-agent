"""Non-consuming development preflight + UNSIGNED proposal generation. Never calls run/consume/signoff."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from datetime import datetime,timezone

from .runner import ResearchOnlyRunner,canonical_sha,ROOT

OLD_CONFIG=ROOT/'configs/research_only/tsm_sma_5_20_smoke_2024_06_v1_1.json'
OLD_PROPOSAL=ROOT/'governance/proposals/RA-IBKR_TSM-SMA520_SMOKE-0001.json'


def prepare_development_proposal(bundle: dict, *, core_sha: str, output_directory: str|Path,
                                 root: Path=ROOT) -> dict:
    from strategy_agent.adapters.mrc_phase4a import core_development
    development,_=core_development()
    DevelopmentSnapshotPin,preflight_development_snapshot=development.DevelopmentSnapshotPin,development.preflight_development_snapshot
    pin=DevelopmentSnapshotPin(**bundle['snapshot_pin'])
    report=preflight_development_snapshot(bundle)  # only development rows; no strategy engine, no batch reservation
    if report['status']!='PASS':raise ValueError('development data preflight failed')
    if not re.fullmatch('[0-9a-f]{40}',core_sha) or report['provenance']['core_git_sha']!=core_sha:
        raise ValueError('Core SHA must match actual preflight reader identity')
    cfg=json.loads((root/OLD_CONFIG.relative_to(ROOT)).read_text())
    original=json.loads((root/OLD_PROPOSAL.relative_to(ROOT)).read_text())
    cfg=copy.deepcopy(cfg);proposal=copy.deepcopy(original)
    aid='RA-IBKR_TSM-SMA520_DEV-'+pin.snapshot_id.rsplit('_',1)[1]
    cfg.update(run_config_id=cfg['run_config_id']+'_dev_'+pin.snapshot_id.rsplit('_',1)[1],version='1.0.0',authorization_id=aid)
    cfg['dataset'].update(snapshot_id=pin.snapshot_id,producer_commit_sha=pin.producer_sha)
    cfg['core'].update(phase4a_ci_run=None,phase4a_review='PENDING_DERIVED_SNAPSHOT_AND_NEW_LEAD_AUTHORIZATION',phase4a_sha=core_sha,phase4a_contract_ref='https://github.com/hh4832/market-research-core/blob/'+core_sha+'/docs/TSM_DEVELOPMENT_SNAPSHOT_CONTRACT.md')
    cfg['snapshot_binding']={k:bundle[k] for k in ('manifest_relative_path','layout','integrity_receipt','integrity_receipt_sha256','snapshot_pin')}
    cfg['snapshot_binding']['manifest_sha256']=bundle['manifest_sha256']
    cfg['snapshot_binding']['diagnostic_parquet_relative_path']='tsm_development_1m.parquet'
    cfg['provenance'].update(created_at=datetime.now(timezone.utc).isoformat(),created_by='codex-development-proposal-only',supersedes='old configuration preserved; new snapshot requires new signed authorization',config_sha256=None)
    cfg['provenance']['config_sha256']=canonical_sha(cfg)
    proposal.update(authorization_id=aid,proposal_status='PROPOSED_UNSIGNED; research lead must personally approve',
                    proposed_at=datetime.now(timezone.utc).isoformat(),proposed_by='codex-proposal-only',approved_by='__RESEARCH_LEAD__',approval_ref='https://__set_by_research_lead__')
    proposal['dataset']=copy.deepcopy(cfg['dataset'])
    proposal['notes']+=' New immutable development snapshot. Old RA-IBKR_TSM-SMA520_SMOKE-0001 and use records remain unchanged. Review manifest '+bundle['manifest_sha256']+'; lineage '+pin.lineage_sha256+'; config '+cfg['provenance']['config_sha256']+'.'
    out=Path(output_directory).resolve()
    snapshot_root=Path(bundle['snapshot_root']).resolve()
    if out==snapshot_root or snapshot_root in out.parents:
        raise ValueError('proposal output must not modify immutable snapshot')
    if out==root.resolve() or any((root/d).resolve()==out or (root/d).resolve() in out.parents for d in ('governance','registry')):
        raise ValueError('generated artifacts must not write formal governance/registry')
    out.mkdir(parents=True,exist_ok=True)
    for name,value in (('research_config.proposed.json',cfg),('research_authorization.proposed.json',proposal),('data_preflight.json',report)):
        with (out/name).open('x') as f:json.dump(value,f,indent=2);f.write('\n')
    return {'status':'DATA_PREFLIGHT_PASS_AUTHORIZATION_UNSIGNED','authorization_id':aid,'config_path':str(out/'research_config.proposed.json'),
            'proposal_path':str(out/'research_authorization.proposed.json'),'data_preflight':report,'slot_consumed':False,
            'eligible_to_backtest':False,'requires':'personal research-lead approval of new authorization; original records untouched'}
