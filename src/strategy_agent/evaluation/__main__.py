"""Standalone evaluator CLI. No runner, backtest, signing, registry write or data-load commands."""
import argparse
import json
import subprocess
from pathlib import Path
from .universal import evaluate, write_evaluation, ROOT
from .archive_input import phase4a_archive


def evaluator_identity():
    sha=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip()
    dirty=bool(subprocess.check_output(['git','-C',str(ROOT),'status','--porcelain'],text=True).strip())
    return sha,dirty


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    group=ap.add_mutually_exclusive_group(required=True)
    group.add_argument('--input',type=Path,help='unified result envelope JSON')
    group.add_argument('--phase4a-archive',type=Path,help='finished result archive only; no snapshot or OHLCV')
    ap.add_argument('--archive-manifest-sha256');ap.add_argument('--scenario');ap.add_argument('--context',type=Path)
    ap.add_argument('--output',type=Path,required=True);ap.add_argument('--evaluator-commit-sha',help='optional expected full SHA; actual clean checkout identity is always checked')
    ap.add_argument('--evaluated-at',help='fixed aware timestamp for byte reproducibility; default Asia/Taipei now')
    args=ap.parse_args(argv)
    sha,dirty=evaluator_identity()
    if dirty or (args.evaluator_commit_sha is not None and sha!=args.evaluator_commit_sha):
        ap.error('evaluator must be a clean checkout of the expected commit')
    if args.input:
        doc=json.loads(args.input.read_text())
    else:
        if not all((args.archive_manifest_sha256,args.scenario,args.context)):
            ap.error('archive mode requires manifest SHA, scenario and context')
        doc=phase4a_archive(args.phase4a_archive,expected_manifest_sha256=args.archive_manifest_sha256,
                           scenario=args.scenario,context=json.loads(args.context.read_text()))
    result=evaluate(doc,evaluator_commit_sha=sha,evaluated_at=args.evaluated_at)
    write_evaluation(result,args.output)
    print(json.dumps({'engineering_status':result['engineering_status'],'strategy_verdict':result['strategy_verdict'],
                      'evidence_sufficiency':result['evidence_sufficiency'],'output':str(args.output),'batch_consumption':0}))
    return 0 if result['engineering_status']=='PASS' else 2


if __name__=='__main__':raise SystemExit(main())
