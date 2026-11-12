"""Fixed pre-C1 historical test input; never truncate a mutable production chain."""
from pathlib import Path

LEGACY_BOOK = Path(__file__).parent/'fixtures/governance/pre_c1_authorizations.jsonl'


def pre_b3c_book():
    return b''.join(LEGACY_BOOK.read_bytes().splitlines(keepends=True)[:2])
