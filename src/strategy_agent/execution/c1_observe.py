"""Single-launch, redacted, durable subprocess diagnostics. Never retries or executes a strategy itself."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import threading
import time
import uuid


def redact(text, secrets):
    for secret in secrets:
        if secret:
            text=text.replace(secret,'[REDACTED]')
    return re.sub(r'(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)', '[REDACTED]', text)


def run_logged(command, *, cwd, parent, secrets=(), heartbeat_seconds=30):
    output=Path(parent)/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')+'_'+uuid.uuid4().hex)
    output.mkdir(parents=True,exist_ok=False)
    log=output/'runner.log';messages=queue.Queue();started=time.monotonic()
    # A browser disconnect cannot reenter this function or issue a second launch.
    child=subprocess.Popen(command,cwd=cwd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                           text=True,bufsize=1,env={**os.environ,'PYTHONUNBUFFERED':'1'})
    def read_lines():
        try:
            for line in child.stdout:messages.put(line)
        finally:messages.put(None)
    reader=threading.Thread(target=read_lines,daemon=True);reader.start()
    try:
        with log.open('x',buffering=1) as f:
            while True:
                try:line=messages.get(timeout=heartbeat_seconds)
                except queue.Empty:
                    usage=shutil.disk_usage(cwd)
                    memory=None
                    try:
                        status=Path('/proc/'+str(child.pid)+'/status').read_text()
                        match=re.search(r'^VmRSS:\s+(\d+) kB',status,re.MULTILINE)
                        if match:memory=int(match.group(1))*1024
                    except OSError:pass
                    line=json.dumps({'stage':'HEARTBEAT','elapsed_seconds':int(time.monotonic()-started),
                                     'disk_free_bytes':usage.free,'child_rss_bytes':memory})+'\n'
                if line is None:break
                clean=redact(line,secrets);f.write(clean);f.flush();print(clean,end='',flush=True)
        code=child.wait()
    except BaseException:
        child.terminate()
        # No exception text/credentials: the durable log survives, outcome remains unknown.
        raise RuntimeError('SUPERVISOR_INTERRUPTED; inspect '+str(output)+'; NEVER RETRY EXECUTE') from None
    summary={'status':'PROCESS_COMPLETED' if code==0 else 'PROCESS_FAILED','returncode':code,
             'elapsed_seconds':round(time.monotonic()-started,3),'log_path':str(log),
             'runner_log_sha256':hashlib.sha256(log.read_bytes()).hexdigest(),'automatic_retry':False}
    with (output/'process_outcome.json').open('x') as f:json.dump(summary,f,indent=2)
    print(json.dumps(summary),flush=True)
    if code:
        raise RuntimeError('RUNNER_NONZERO_EXIT; diagnostics='+str(output)+'; NEVER RETRY EXECUTE')
    return summary
