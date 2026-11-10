"""Run the EXISTING Phase 4-C evaluator (``phase4c.accept``) in a FRESH interpreter.

C8 (governance isolation) requires that no executor module (runner / Core) is loaded in the evaluating process. A
real execution necessarily loads them in the dispatching process, so its evaluation must run in a separate one.
Read-only: the evaluator writes nothing here; a non-zero exit or unparseable output is a FAILED evaluation (never a
pass), and the subprocess cannot inherit any in-process monkeypatch.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
_CODE = ("import json,sys;from strategy_agent.evaluation import phase4c as C;"
         "a=C.accept(sys.argv[1],expected_manifest_sha256=sys.argv[2],evaluator_commit_sha=sys.argv[3],"
         "evaluated_at=sys.argv[4],governance_root=sys.argv[5]);"
         "a['evaluator_process']={'fresh':True,'executor_modules_loaded':[m for m in C.EXECUTOR_MODULES if m in sys.modules]};"
         "print(json.dumps(a,default=str))")


def accept(archive_path, *, expected_manifest_sha256: str, evaluator_commit_sha: str, evaluated_at: str,
           governance_root, timeout: int = 900) -> dict:
    r = subprocess.run([sys.executable, "-c", _CODE, str(archive_path), expected_manifest_sha256, evaluator_commit_sha,
                        evaluated_at, str(governance_root)], capture_output=True, text=True, timeout=timeout,
                       cwd=str(ROOT))
    try:
        if r.returncode != 0:
            raise ValueError(f"rc={r.returncode}")
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"status": "BLOCKED", "checks": {}, "evaluation": None,
                "error": {"code": "EVALUATOR_SUBPROCESS_FAILED", "rc": r.returncode, "stderr": r.stderr[-800:]}}
