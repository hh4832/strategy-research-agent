"""Admission lock: serialises every check-then-write step of the runner.

Budget counters, family assignment and holdout reservation all read the
registry and then write to it. Without serialisation two workers can both
pass a check (two runs admitted when one slot is left). The runner therefore
holds this exclusive ``fcntl`` lock from family assignment until the RUNNING
event is written; the backtest itself runs outside the lock.

Scope (stated, not assumed): ``fcntl.flock`` is only reliable for processes on
ONE host sharing a local filesystem. It is NOT valid across machines or on
network/synced folders (e.g. Google Drive). ``AdmissionLock`` records the
host that first used the registry and refuses a different host, so a second
machine fails closed instead of silently racing.
"""
from __future__ import annotations

import fcntl
import json
import os
import socket
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class AdmissionError(RuntimeError):
    pass


class AdmissionLock:
    def __init__(self, registry_dir: str | Path, host: str | None = None):
        self.dir = Path(registry_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.dir / ".admission.lock"
        self.host_path = self.dir / "runner_host.json"
        self.host = host or socket.gethostname()
        self._bind_host()

    def _bind_host(self) -> None:
        with open(self.lock_path, "a+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                if self.host_path.exists():
                    bound = json.loads(self.host_path.read_text())["host"]
                    if bound != self.host:
                        raise AdmissionError(
                            f"registry is bound to host {bound!r}; {self.host!r} must not run workers on it "
                            "(flock does not work across machines)")
                else:
                    self.host_path.write_text(json.dumps({"host": self.host}))
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    @contextmanager
    def hold(self) -> Iterator[dict]:
        with open(self.lock_path, "a+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            info = {"pid": os.getpid(), "host": self.host, "acquired": time.time()}
            try:
                fh.seek(0)
                fh.truncate()
                fh.write(json.dumps(info))
                fh.flush()
                yield info
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
