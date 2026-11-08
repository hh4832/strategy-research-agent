

import pytest as _pytest  # noqa: E402


@_pytest.fixture(scope="session")
def remediation_json(tmp_path_factory):
    """Phase 5-B1: the exploit reproductions run against THIS checkout (fresh process), for acceptance runs."""
    import subprocess
    import sys
    from pathlib import Path as _P
    root = _P(__file__).resolve().parents[1]
    out = tmp_path_factory.mktemp("remediation") / "reproduction.json"
    r = subprocess.run([sys.executable, str(root / "docs/evidence/phase5b1_remediation/reproduce.py"), str(root), str(out)],
                       capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, r.stderr[-2000:]
    return out
