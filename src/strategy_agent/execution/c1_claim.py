"""Server-side at-most-once C1 claim. No retry, deletion, commit creation or automatic recovery."""
from __future__ import annotations

import base64
import copy
import threading
import hashlib
import json
import os
from pathlib import Path
import ssl
import urllib.error
import urllib.request

REPO = 'hh4832/strategy-research-agent'
REPO_ID = 1443253343
LEAD_ID = 269247281
AUTH = 'RA-IBKR_TSM-SMA520_P5C1-0001'
# One key for the ENTIRE authorized batch, independent of request hash and runtime.
REF = 'refs/heads/research-claims/phase5c1/'+hashlib.sha256(AUTH.encode()).hexdigest()+'/batch-1'
PROTECTED = ('governance/research_authorizations.jsonl', 'governance/proposals/'+AUTH+'.json',
             'governance/controls.jsonl', 'governance/research_budget.json', 'governance/source_confirmations.json',
             'governance/approval_identities.json', 'governance/market_readiness.json', 'governance/holdout_locks.jsonl', 'governance/approvers.json',
             'registry/research_authorization_uses.jsonl', 'registry/research_budget_events.jsonl',
             'registry/authorization_bindings.jsonl')


class ReconciliationRequired(RuntimeError):
    pass


_GRANT_CAPABILITY=object()


class ClaimGrant:
    """One-process, one-use capability issued only after an acknowledged exclusive server claim."""
    def __init__(self,record,production,*,_capability=None):
        if _capability is not _GRANT_CAPABILITY:raise ValueError('server-issued claim capability required')
        self.__record=copy.deepcopy(record);self.__production=production
        self.__pid=os.getpid();self.__used=False;self.__lock=threading.Lock()

    @property
    def record(self):return copy.deepcopy(self.__record)

    @property
    def production(self):return self.__production

    def __reduce__(self):raise TypeError('claim grants are not portable or serializable')

    def consume(self,authorization_id,request_sha):
        with self.__lock:
            if (os.getpid()!=self.__pid or self.__used or authorization_id!=AUTH
                    or self.__record['authorization_id']!=authorization_id
                    or self.__record['request_sha256']!=request_sha or self.__record['claim_ref']!=REF):
                raise ReconciliationRequired('CLAIM_CAPABILITY_MISMATCH_OR_ALREADY_USED')
            self.__used=True



def _live(method, path, payload, token):
    request=urllib.request.Request('https://api.github.com'+path,
        data=None if payload is None else json.dumps(payload).encode(),method=method,
        headers={'Accept':'application/vnd.github+json','Authorization':'Bearer '+token,
                 'Content-Type':'application/json','X-GitHub-Api-Version':'2022-11-28'})
    ca=os.environ.get('SSL_CERT_FILE') or os.environ.get('REQUESTS_CA_BUNDLE')
    context=ssl.create_default_context(cafile=ca) if ca else ssl.create_default_context()
    try:
        with urllib.request.urlopen(request,timeout=30,context=context) as response:
            return response.status,json.load(response)
    except urllib.error.HTTPError as error:
        return error.code,{}
    except Exception:
        # A write may have succeeded despite a lost response. Never retry or dispatch.
        raise ReconciliationRequired('CLAIM_TRANSPORT_UNKNOWN; inspect server/Drive evidence; do not retry') from None


_LIVE = _live


class GitHubClaim:
    def __init__(self, token=None, *, transport=None):
        self._token=token if token is not None else os.environ.get('GITHUB_TOKEN','')
        self._transport=transport

    @property
    def production(self):
        return type(self) is GitHubClaim and self._transport is None and _live is _LIVE

    def _call(self,method,path,payload=None):
        if self._transport is not None:
            return self._transport(method,path,payload)
        if not self._token:
            raise ReconciliationRequired('GITHUB_TOKEN_MISSING')
        return _live(method,path,payload,self._token)

    def _get(self,path):
        status,body=self._call('GET',path)
        if status!=200:
            raise ReconciliationRequired('CLAIM_READ_FAILED:'+str(status))
        return body

    def inspect(self, governance_root: Path, governance_sha: str):
        """Read-only identity, latest authoritative state and stable-claim check."""
        repo=self._get('/repos/'+REPO)
        user=self._get('/user')
        if (repo.get('id'),repo.get('full_name'))!=(REPO_ID,REPO) or (
                user.get('login'),user.get('id'),user.get('type'))!=('hh4832',LEAD_ID,'User'):
            raise ReconciliationRequired('CLAIM_WRONG_REPOSITORY_OR_HUMAN_IDENTITY')
        live=self._get('/repos/'+REPO+'/git/ref/heads/main')['object']['sha']
        for relative in PROTECTED:
            value=self._get('/repos/'+REPO+'/contents/'+relative+'?ref='+live)
            if value.get('encoding')!='base64':
                raise ReconciliationRequired('GOVERNANCE_CONTENT_UNAVAILABLE')
            if base64.b64decode(value['content'])!=(Path(governance_root)/relative).read_bytes():
                raise ReconciliationRequired('LIVE_GOVERNANCE_CHANGED:'+relative)
        status,_=self._call('GET','/repos/'+REPO+'/git/ref/'+REF.removeprefix('refs/'))
        if status!=404:
            raise ReconciliationRequired('EXISTING_OR_UNVERIFIABLE_REMOTE_CLAIM')
        return {'repository':REPO,'user':'hh4832','main_sha':live,'governance_sha':governance_sha,'claim_ref':REF}

    def acquire(self, *, governance_root, governance_sha, request_sha):
        if len(request_sha)!=64 or any(c not in '0123456789abcdef' for c in request_sha):
            raise ValueError('full request SHA required')
        proof=self.inspect(governance_root,governance_sha)
        # GitHub enforces ref-name uniqueness atomically. No GET-then-file-write lock is relied on.
        status,value=self._call('POST','/repos/'+REPO+'/git/refs',{'ref':REF,'sha':governance_sha})
        if status!=201 or value.get('ref')!=REF or value.get('object',{}).get('sha')!=governance_sha:
            raise ReconciliationRequired('CLAIM_NOT_EXCLUSIVELY_ACKNOWLEDGED; do not retry')
        record=dict(proof,authorization_id=AUTH,batch=1,request_sha256=request_sha,
                    status='CLAIMED_PENDING_SETTLEMENT',remote_url=value.get('url'))
        return ClaimGrant(record,self.production,_capability=_GRANT_CAPABILITY)
