"""Immutable full C1 attempt archive, including interrupted attempts. Never restores/runs anything."""
from __future__ import annotations

from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import uuid


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda:source.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def archive_attempt(parent, *, preparation, governance, work, runtime_audit, provenance, outcome, control=None, session=None):
    """Copy only explicit roots, never .git/settings/env/tokens or the staged input Snapshot.

    INDEX presence and every verified hash are required for completion. A partially copied directory is
    retained as evidence and must never be reused. All paths are new; nothing is overwritten.
    """
    root=Path(parent)/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')+'_'+uuid.uuid4().hex)
    root.mkdir(parents=True,exist_ok=False)
    def save(relative,value):
        target=root/relative;target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('x') as output:json.dump(value,output,indent=2,default=str)
    save('ARCHIVE_STARTED.json',{'status':'INCOMPLETE_UNTIL_INDEX_VERIFIED','provenance':provenance})
    hashes={}
    def copy_file(source,relative):
        if source.is_symlink():raise ValueError('archive refuses symlinks')
        before=digest(source)
        target=root/relative;target.parent.mkdir(parents=True,exist_ok=True)
        with source.open('rb') as src,target.open('xb') as dst:
            while block:=src.read(1024*1024):dst.write(block)
        if digest(source)!=before or digest(target)!=before:raise ValueError('archive byte verification failed')
        hashes[relative]=before
    sources=[('preparation',Path(preparation)),('execution',Path(work)),('runtime_audit',Path(runtime_audit))]
    sources.extend((label,Path(path)) for label,path in (('control',control),('session',session)) if path is not None)
    for label,source in sources:
        if source.is_symlink():raise ValueError('archive refuses symlink roots')
        if not source.is_dir():continue
        for path in sorted(source.rglob('*')):
            relative=path.relative_to(source)
            if any(part in ('.git','.env','settings.json','bootstrap','venv') for part in relative.parts):continue
            if path.is_symlink():raise ValueError('archive refuses symlinks')
            if path.is_file():copy_file(path,label+'/'+relative.as_posix())
    for folder in ('governance','registry'):
        source=Path(governance)/folder
        for path in sorted(source.rglob('*')):
            if path.is_symlink():raise ValueError('archive refuses symlinks')
            if path.is_file() and path.suffix in ('.json','.jsonl'):
                copy_file(path,'settlement/'+folder+'/'+path.relative_to(source).as_posix())
    save('outcome.json',outcome)
    info='\n'.join(f'{k}: {v}' for k,v in provenance.items())+'\nOutcome: '+str(outcome.get('status'))+'\n'
    with (root/'run_info.txt').open('x') as output:output.write(info)
    for name in ('ARCHIVE_STARTED.json','outcome.json','run_info.txt'):hashes[name]=digest(root/name)
    save('ARCHIVE_INDEX.json',{'status':'COMPLETE','files_sha256':hashes,'provenance':provenance})
    verify_archive(root)
    return root


def verify_archive(root):
    root=Path(root);index=json.loads((root/'ARCHIVE_INDEX.json').read_text())
    listed=index['files_sha256']
    actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p.name!='ARCHIVE_INDEX.json'}
    if actual!=set(listed):raise ValueError('archive inventory differs')
    for relative,expected in listed.items():
        candidate=root/relative
        source=candidate.resolve(strict=True)
        if candidate.is_symlink() or not source.is_relative_to(root.resolve()) or digest(source)!=expected:
            raise ValueError('archive checksum/path mismatch')
    return index
