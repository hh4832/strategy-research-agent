"""Offline proposal CLI. No orchestrator, backtest, approval, Holdout or registry entry point."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

from .agent import ROOT,DraftRejected,draft_proposal,validate_proposal,write_proposal
from .adapters import FakeLLMAdapter,FileDraftAdapter
from .exporter import export_request,ContractPending


def identity():
    sha=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip()
    if subprocess.check_output(['git','-C',str(ROOT),'status','--porcelain'],text=True).strip():
        raise ValueError('draft generation requires a clean checkout for truthful code provenance')
    return sha


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__);sub=ap.add_subparsers(dest='command',required=True)
    p=sub.add_parser('draft');p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path)
    p.add_argument('--llm-draft',type=Path,help='explicit JSON completion from a configured external model; no API calls here')
    p.add_argument('--prior-proposals',type=Path,help='explicit JSON array of prior proposal artifacts, read only')
    p.add_argument('--created-at',help='aware ISO timestamp for deterministic replay; default Asia/Taipei now')
    p=sub.add_parser('validate');p.add_argument('--proposal',type=Path,required=True)
    p=sub.add_parser('export-request');p.add_argument('--proposal',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--bindings',type=Path)
    args=ap.parse_args(argv)
    try:
        if args.command=='draft':
            adapter=FileDraftAdapter(args.llm_draft) if args.llm_draft else FakeLLMAdapter()
            priors=json.loads(args.prior_proposals.read_text()) if args.prior_proposals else []
            proposal=draft_proposal(args.input.read_text(encoding='utf-8'),adapter,agent_commit_sha=identity(),
                                    created_at=args.created_at,prior_proposals=priors)
            out=args.output or Path(tempfile.gettempdir())/'strategy-proposals'/proposal['proposal_id']
            write_proposal(proposal,out)
            print(json.dumps({'proposal_id':proposal['proposal_id'],'status':proposal['status'],'output':str(out),
                              'request_contract':proposal['request_contract'],'execution_eligible':False,'batch_consumption':0}))
            return 0
        proposal=json.loads(args.proposal.read_text(encoding='utf-8'))
        if args.command=='validate':
            result=validate_proposal(proposal);print(json.dumps(result));return 0 if result['valid'] else 2
        result=export_request(proposal,args.output,bindings=json.loads(args.bindings.read_text()) if args.bindings else None)
        print(json.dumps(result['provenance']));return 0
    except ContractPending as error:
        print(json.dumps({'status':error.code,'message':str(error),'request_written':False,'batch_consumption':0}));return 3
    except (ValueError,OSError,TypeError,KeyError) as error:
        print(json.dumps({'status':getattr(error,'code','INVALID_INPUT'),'message':str(error),
                          'execution_eligible':False,'batch_consumption':0}));return 2


if __name__=='__main__':raise SystemExit(main())
