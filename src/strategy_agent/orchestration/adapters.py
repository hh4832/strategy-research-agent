"""Execution adapters: the only way the orchestrator obtains a research result.

Phase 5-A1 implements two:
* ``ArchiveReplayAdapter``: verifies an EXISTING research archive (pinned MANIFEST.json, every file hash, identity
  consistent with the request). Reads only; never re-runs a strategy, rebuilds trades or consumes a batch.
* ``SyntheticExecutionAdapter``: TEST mode only; materialises a provided SYNTHETIC_FIXTURE archive. Its output is
  labelled synthetic everywhere and is never research evidence.
``ResearchOnlyRunnerAdapter`` defines the interface for a real research-only run and is DISABLED: ``execute`` refuses
without importing the runner.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from strategy_agent.evaluation import phase4c as C


class AdapterDisabled(RuntimeError):
    pass


class ExecutionFailed(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


class ExecutionAdapter:
    kind = "ABSTRACT"
    enabled = False
    consumes_budget = False
    evidence = "NONE"

    def execute(self, doc: dict, out_dir: Path) -> dict:           # pragma: no cover - interface
        raise NotImplementedError


def _identity_checks(doc: dict, archive: dict, parsed: dict) -> list[str]:
    ri = parsed["run_info"]
    bad = []
    pairs = {"snapshot_id": ("dataset_snapshot_id", None), "manifest_sha256": ("dataset_manifest_sha256", None),
             "core_git_sha": ("core_commit", None)}
    for ri_key, (doc_key, _) in pairs.items():
        if ri.get(ri_key) != doc.get(doc_key):
            bad.append(f"{ri_key}: archive {ri.get(ri_key)} != request {doc.get(doc_key)}")
    aref = doc.get("authorization_ref")
    if isinstance(aref, dict) and ri.get("authorization_id") != aref["authorization_id"]:
        bad.append(f"authorization_id: archive {ri.get('authorization_id')} != request {aref['authorization_id']}")
    cfg = parsed["config"]["strategy"]
    sf = doc["strategy_family"]
    if (cfg["strategy_id"], cfg["version"], cfg["parameters"]) != (sf["strategy_id"], sf["version"], sf["parameters"]):
        bad.append("strategy identity/parameters differ from the request")
    if cfg.get("spec_file_sha256") != doc["strategy_spec_sha256"]:
        bad.append("strategy spec sha differs from the request")
    return bad


class ArchiveReplayAdapter(ExecutionAdapter):
    kind = "ARCHIVE_REPLAY"
    enabled = True
    consumes_budget = False
    evidence = "EXISTING_ARCHIVE"

    def __init__(self, archive_root: Path):
        self.archive_root = Path(archive_root)

    def execute(self, doc: dict, out_dir: Path) -> dict:
        src = doc["replay_source"]
        path = self.archive_root / src["run_id"]
        try:
            archive = C.read_archive(path, expected_manifest_sha256=src["archive_manifest_sha256"])
        except C.ArchiveRejected as e:
            raise ExecutionFailed(e.code, str(e)) from None
        parsed = C.parse(archive)
        bad = _identity_checks(doc, archive, parsed)
        if bad:
            raise ExecutionFailed("ARCHIVE_REQUEST_MISMATCH", "; ".join(bad))
        return {"adapter": self.kind, "evidence": self.evidence, "consumed_batches": 0, "strategy_executed": False,
                "archive_path": str(archive["root"]), "run_id": src["run_id"],
                "archive_manifest_sha256": src["archive_manifest_sha256"], "files_verified": len(archive["files"]),
                "source_fingerprint": archive["fingerprint"],
                "source_identity": {k: parsed["run_info"].get(k) for k in ("agent_git_sha", "core_git_sha", "snapshot_id",
                                                                           "manifest_sha256", "authorization_id",
                                                                           "canonical_config_sha256")}}


class SyntheticExecutionAdapter(ExecutionAdapter):
    kind = "SYNTHETIC_TEST"
    consumes_budget = False
    evidence = "SYNTHETIC_FIXTURE"

    def __init__(self, fixture_archive: Path | None):
        self.fixture = Path(fixture_archive) if fixture_archive else None
        self.enabled = self.fixture is not None

    def execute(self, doc: dict, out_dir: Path) -> dict:
        if not self.enabled:
            raise AdapterDisabled("synthetic adapter has no fixture archive")
        dst = Path(out_dir) / self.fixture.name
        shutil.copytree(self.fixture, dst)                 # fails if it exists: never overwrites
        manifest_sha = C.sha256_bytes((dst / "MANIFEST.json").read_bytes())
        archive = C.read_archive(dst, expected_manifest_sha256=manifest_sha)
        return {"adapter": self.kind, "evidence": self.evidence, "consumed_batches": 0, "strategy_executed": False,
                "label": "SYNTHETIC_FIXTURE — not research evidence", "archive_path": str(dst),
                "run_id": archive["manifest"]["run_id"], "archive_manifest_sha256": manifest_sha,
                "files_verified": len(archive["files"]), "source_fingerprint": archive["fingerprint"]}


class ResearchOnlyRunnerAdapter(ExecutionAdapter):
    """Interface for a real research-only run (would consume one authorization batch). DISABLED in Phase 5-A1."""
    kind = "RESEARCH_ONLY_REAL"
    enabled = False
    consumes_budget = True
    evidence = "NEW_RESEARCH_ONLY_RUN"

    def execute(self, doc: dict, out_dir: Path) -> dict:
        raise AdapterDisabled("RESEARCH_ONLY_REAL is DISABLED in Phase 5-A1; enabling it needs a separate user "
                              "authorization and an independent acceptance")
