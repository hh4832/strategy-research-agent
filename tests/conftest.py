

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


@_pytest.fixture
def pre_c1_root(tmp_path):
    """Isolated validation world with a fixed three-record historical ledger."""
    import shutil
    from pathlib import Path
    from governance_fixtures import LEGACY_BOOK
    root = Path(__file__).resolve().parents[1]
    target = tmp_path/'pre_c1'
    for relative in ('governance', 'schemas', 'docs/phase5c1/authorization_source'):
        shutil.copytree(root/relative, target/relative)
    (target/'governance/research_authorizations.jsonl').write_bytes(LEGACY_BOOK.read_bytes())
    # The source proposal is deliberately unsigned, independent of the working checkout's human decision.
    import json
    original = json.loads((target/'docs/phase5c1/authorization_source/authorization.DRAFT_NOT_APPROVED.json').read_text())['proposal_fields']
    (target/'governance/proposals/RA-IBKR_TSM-SMA520_P5C1-0001.json').write_text(json.dumps(original))
    return target
