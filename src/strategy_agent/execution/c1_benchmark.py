"""Pinned authentic REAL_CORE evidence; verification/copy/archive only, never runs a benchmark or research."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import uuid
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[3]
CORE='50ea3bfa42df7708dd3a1b21f17798c206be054e'
SOURCE_AGENT='3db1d75d9d6f9de1320f3e4542e24dd84d551713'
RELATIVE=Path('benchmark/results/mrc_'+CORE[:12])
RECORD_SHA='2c74d6d2fccd4233892f64b1447ceb2133a261b733f52aa1e8069d25df2f7ffc'
MANIFEST_SHA='049f043b0aaa64eaf524d8540071fa38de6603ddca8eb083bdd8164d88787821'


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify(root=ROOT, *, verify_inputs=False):
    from strategy_agent.adapters.mrc_adapter import engine_benchmark_status
    root=Path(root);directory=root/RELATIVE;mp=directory/'EVIDENCE_MANIFEST.json'
    if any(p.is_symlink() for p in (directory,*directory.parents)) or not mp.is_file():
        raise ValueError('C1_BENCHMARK_MISSING_OR_SYMLINK')
    if mp.is_symlink() or sha(mp)!=MANIFEST_SHA:raise ValueError('C1_BENCHMARK_MANIFEST_HASH')
    manifest=json.loads(mp.read_text())
    if manifest['core_sha']!=CORE or manifest['original_agent_commit']!=SOURCE_AGENT:
        raise ValueError('C1_BENCHMARK_SOURCE_IDENTITY')
    files=manifest['files_sha256']
    if {p.name for p in directory.iterdir()}!=set(files)|{'EVIDENCE_MANIFEST.json'}:
        raise ValueError('C1_BENCHMARK_INVENTORY')
    for name,expected in files.items():
        p=directory/name
        if Path(name).name!=name or p.is_symlink() or not p.is_file() or sha(p)!=expected:
            raise ValueError('C1_BENCHMARK_FILE_HASH:'+name)
    if sha(directory/'crosscheck.json')!=RECORD_SHA:raise ValueError('C1_BENCHMARK_RECORD_HASH')
    provenance=json.loads((directory/'execution_provenance.json').read_text())
    if provenance['agent_commit']!=SOURCE_AGENT or provenance['core']['git_sha']!=CORE:
        raise ValueError('C1_BENCHMARK_PROVENANCE')
    if verify_inputs:
        for name,expected in provenance['inputs_sha256'].items():
            p=root/name
            if not p.resolve().is_relative_to(root.resolve()) or p.is_symlink() or sha(p)!=expected:
                raise ValueError('C1_BENCHMARK_INPUT_CHANGED:'+name)
    status=engine_benchmark_status(CORE,root/'benchmark/results')
    if status['status']!='BENCHMARK_PASS':raise ValueError('C1_BENCHMARK_NOT_VERIFIED:'+status['why'])
    return {'core_sha':CORE,'record_sha256':RECORD_SHA,'manifest_sha256':MANIFEST_SHA,
            'original_agent_commit':SOURCE_AGENT,'derived_status':status}


def package(source_root, artifact_root):
    """Write once, preserving exact original bytes; validate from the final Governance root."""
    result=verify(source_root,verify_inputs=True)
    source=Path(source_root)/RELATIVE;target=Path(artifact_root)/RELATIVE
    target.mkdir(parents=True,exist_ok=False)
    for p in sorted(source.iterdir()):
        with (target/p.name).open('xb') as out:out.write(p.read_bytes())
    if verify(artifact_root)!=result:raise ValueError('C1_BENCHMARK_PACKAGING_CHANGED')
    return result


def archive(parent, *, execution_sha, source_root=ROOT):
    """New append-only Drive/local archive. No old directory is opened for writing."""
    if not re.fullmatch('[0-9a-f]{40}',execution_sha):raise ValueError('FULL_EXECUTION_SHA_REQUIRED')
    verify(source_root,verify_inputs=True)
    out=Path(parent)/'benchmark_evidence'/(datetime.now(ZoneInfo('Asia/Taipei')).strftime('%Y%m%d_%H%M%S_%f')+'_'+uuid.uuid4().hex)
    out.mkdir(parents=True,exist_ok=False)
    provenance={'execution_sha':execution_sha,'source_agent_commit':SOURCE_AGENT,'core_sha':CORE,
        'record_sha256':RECORD_SHA,'timezone':'Asia/Taipei','created_at':datetime.now(ZoneInfo('Asia/Taipei')).isoformat(),
        'batch_consumption':0,'holdout_access':'NO_ACCESS','operation':'COPY_AUTHENTIC_EVIDENCE_ONLY'}
    (out/'ARCHIVE_STARTED.json').write_text(json.dumps(provenance,indent=2)+'\n')
    package(source_root,out)
    (out/'run_info.txt').write_text('\n'.join(f'{k}: {v}' for k,v in provenance.items())+'\n')
    hashes={p.relative_to(out).as_posix():sha(p) for p in out.rglob('*') if p.is_file()}
    (out/'ARCHIVE_INDEX.json').write_text(json.dumps({'status':'COMPLETE','files_sha256':hashes,'provenance':provenance},indent=2)+'\n')
    from strategy_agent.execution.c1_archive import verify_archive
    verify_archive(out)
    return out
