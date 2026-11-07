"""Strict CI wrapper around ``append_only.check`` (Phase 5-B1 remediation M4).

``append_only.check`` is pinned by closed Phase 3 notebooks and stays byte-identical. Its git calls treat a missing
base ref as "every file is new / nothing deleted", which PASSES. This wrapper makes every precondition explicit and
fails closed: the base must resolve to a commit, ``git diff`` must succeed, and only then are the original checks run.

    python -m strategy_agent.governance.append_only_guard --base origin/main
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from .append_only import GOVERNED_DIRS, check as _check


def check(base: str, root: Path = Path(".")) -> list[str]:
    def git(*a):
        return subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True)
    if git("rev-parse", "--verify", "--quiet", f"{base}^{{commit}}").returncode != 0:
        return [f"base ref {base!r} is not available (shallow clone / missing fetch): cannot prove append-only"]
    d = git("diff", "--name-only", "--diff-filter=D", base, "--", *GOVERNED_DIRS)
    if d.returncode != 0:
        return [f"git diff against {base} failed (rc={d.returncode}): {d.stderr.strip()[-200:]}"]
    return _check(base, Path(root))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    args = ap.parse_args(argv)
    problems = check(args.base)
    for p in problems:
        print(f"APPEND-ONLY VIOLATION: {p}")
    print("append-only check:", "FAIL" if problems else "PASS")
    return 1 if problems else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
