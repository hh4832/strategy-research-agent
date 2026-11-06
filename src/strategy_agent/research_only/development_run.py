"""Single research-only smoke execution on the signed TSM development snapshot (shared logic for the Colab runner).

Order (fail-closed, nothing consumed until the last step):
1. ``session_config``: Core ``renew_development_receipt`` re-binds the session-dependent FileIntegrityReceipt
   (Core validates manifest + lineage + every row group as a single 2024 development day BEFORE hashing the
   development-only file). Only ``snapshot_binding.integrity_receipt`` / ``integrity_receipt_sha256`` change; the
   result is written write-once next to the archive and is never committed. The canonical config, the snapshot
   and every governance record are untouched.
2. ``prior_archived_attempts``: refuse if the Drive archive already holds a run or failure for this authorization
   (a second Colab checkout cannot see an un-pushed use record, so this is the cross-session guard).
3. ``ResearchOnlyRunner.probe`` must be COMPATIBLE_READY_TO_CONSUME.
4. ``ResearchOnlyRunner.run``: re-checks everything, atomically consumes the one slot, executes through Core and
   writes the immutable archive.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from .runner import ROOT, ResearchOnlyRunner, canonical_sha, write_once

RECEIPT_KEYS = ("integrity_receipt", "integrity_receipt_sha256")


class SmokeStopped(RuntimeError):
    """Raised before any consumption; carries a machine-readable reason."""


def _strip(cfg: dict) -> dict:
    c = copy.deepcopy(cfg)
    for k in RECEIPT_KEYS:
        c["snapshot_binding"].pop(k, None)
    c["provenance"].pop("config_sha256", None)
    return c


def session_config(canonical_path: Path, snapshot_root: Path, out_dir: Path, *, renew=None) -> dict:
    canonical_path, snapshot_root = Path(canonical_path), Path(snapshot_root)
    cfg = json.loads(canonical_path.read_text(encoding="utf-8"))
    if cfg["provenance"]["config_sha256"] != canonical_sha(cfg):
        raise SmokeStopped("CANONICAL_CONFIG_SHA: canonical config self-hash mismatch")
    sb = cfg["snapshot_binding"]
    if snapshot_root.name != cfg["dataset"]["snapshot_id"] or sb.get("snapshot_pin", {}).get("snapshot_id") != snapshot_root.name:
        raise SmokeStopped("SNAPSHOT_ID: snapshot root does not match the signed dataset")
    src = json.loads((snapshot_root / "binding.json").read_text(encoding="utf-8"))
    for k in ("snapshot_pin", "manifest_sha256", "integrity_receipt_sha256", "manifest_relative_path", "layout"):
        if src.get(k) != sb.get(k):
            raise SmokeStopped(f"BINDING_MISMATCH: snapshot binding.json {k} differs from the signed config")
    if Path(src["snapshot_root"]).resolve() != snapshot_root.resolve():
        raise SmokeStopped("BINDING_ROOT: binding.json snapshot_root is another location")
    if renew is None:
        from strategy_agent.adapters.mrc_phase4a import core_development  # noqa: PLC0415
        renew = core_development()[0].renew_development_receipt
    renewed = renew(src)
    if (renewed["snapshot_pin"] != sb["snapshot_pin"] or renewed["manifest_sha256"] != sb["manifest_sha256"]
            or renewed["integrity_receipt"]["parquet_sha256"] != sb["integrity_receipt"]["parquet_sha256"]
            or renewed["integrity_receipt"]["manifest_sha256"] != sb["manifest_sha256"]
            or not renewed["integrity_receipt"]["verification_ref"].startswith("DEVELOPMENT_RECEIPT_RENEWAL:")):
        raise SmokeStopped("RECEIPT_RENEWAL: renewed receipt does not bind the signed file and manifest")
    session = copy.deepcopy(cfg)
    for k in RECEIPT_KEYS:
        session["snapshot_binding"][k] = renewed[k]
    session["provenance"]["config_sha256"] = canonical_sha(session)
    if _strip(session) != _strip(cfg):
        raise SmokeStopped("SESSION_CONFIG: only the integrity receipt may differ")
    path = Path(out_dir) / "session_config.json"
    sha = write_once(path, (json.dumps(session, indent=2, ensure_ascii=False) + "\n").encode())
    return {"path": path, "file_sha256": sha, "config": session, "canonical_config": cfg,
            "canonical_config_sha256": cfg["provenance"]["config_sha256"],
            "session_config_sha256": session["provenance"]["config_sha256"],
            "difference": "snapshot_binding.integrity_receipt + integrity_receipt_sha256 (Core renew_development_receipt; "
                          f"verification_ref {renewed['integrity_receipt']['verification_ref']})"}


def prior_archived_attempts(archive_root: Path, authorization_id: str) -> list[str]:
    hits = []
    root = Path(archive_root)
    if not root.is_dir():
        return hits
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        for name in ("MANIFEST.json", "FAILURE.json"):
            f = d / name
            if f.is_file() and authorization_id in f.read_text(encoding="utf-8", errors="replace"):
                hits.append(str(f))
    return hits


def execute_development_smoke(canonical_config: Path, *, snapshot_root: Path, archive_root: Path, session_dir: Path,
                              git_commit: str, actor: str, context: dict, root: Path = ROOT,
                              runner: ResearchOnlyRunner | None = None, renew=None) -> dict:
    runner = runner or ResearchOnlyRunner(root=root)
    sc = session_config(canonical_config, snapshot_root, session_dir, renew=renew)
    aid = sc["config"]["authorization_id"]
    prior = prior_archived_attempts(archive_root, aid)
    if prior:
        raise SmokeStopped(f"PRIOR_ATTEMPT: archive already holds {prior}; the single batch is spent")
    probe = runner.probe(sc["path"], snapshot_root=snapshot_root)
    if probe["status"] != "COMPATIBLE_READY_TO_CONSUME" or probe["blockers"] or probe["slot_consumed"]:
        return {"executed": False, "slot_consumed": False, "status": "STOPPED_BY_PROBE", "probe": probe}
    records = [json.loads(x) for x in Path(runner.auth_path).read_text(encoding="utf-8").splitlines() if x.strip()]
    approvals = [r for r in records if r.get("authorization_id") == aid and r.get("action") == "APPROVE"]
    ctx = dict(context) | {"canonical_config_path": str(canonical_config),
                           "canonical_config_sha256": sc["canonical_config_sha256"],
                           "canonical_config": sc["canonical_config"],
                           "session_config_difference": sc["difference"],
                           "authorization_record_sha256": approvals[-1]["record_sha256"] if approvals else None}
    result = runner.run(sc["path"], snapshot_root=snapshot_root, archive_root=archive_root, git_commit=git_commit,
                        actor=actor, run_context=ctx)
    return result | {"probe_before_consumption": probe, "session_config": {k: str(v) for k, v in sc.items()
                                                                           if k in ("path", "file_sha256",
                                                                                    "canonical_config_sha256",
                                                                                    "session_config_sha256",
                                                                                    "difference")}}
