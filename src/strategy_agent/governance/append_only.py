"""CI guard: governed logs may only grow.

For every tracked ``*.jsonl`` under ``registry/`` and ``governance/``, the
file on this branch must start with the exact bytes of the same file on the
base ref, and its hash chain must verify. Catches edits, deletions and full
chain rewrites that ``ChainedLog.verify`` alone cannot.

    python -m strategy_agent.governance.append_only --base origin/main
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from strategy_agent.registry.chain import ChainError, ChainedLog

GOVERNED_DIRS = ("registry", "governance")


def is_append_only(old: bytes, new: bytes) -> bool:
    return new.startswith(old)


def _git_show(ref: str, path: str) -> bytes | None:
    r = subprocess.run(["git", "show", f"{ref}:{path}"], capture_output=True)
    return r.stdout if r.returncode == 0 else None


def check(base: str, root: Path = Path(".")) -> list[str]:
    problems = []
    for d in GOVERNED_DIRS:
        for p in sorted((root / d).rglob("*.jsonl")):
            rel = p.relative_to(root).as_posix()
            try:
                ChainedLog(p).verify()
            except ChainError as e:
                problems.append(f"{rel}: chain invalid: {e}")
            old = _git_show(base, rel)
            if old is not None and not is_append_only(old, p.read_bytes()):
                problems.append(f"{rel}: existing lines were modified or removed relative to {base}")
    # files deleted on this branch
    r = subprocess.run(["git", "diff", "--name-only", "--diff-filter=D", base, "--", *GOVERNED_DIRS],
                       capture_output=True, text=True)
    problems += [f"{f}: governed log deleted" for f in r.stdout.split() if f.endswith(".jsonl")]
    return problems


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
