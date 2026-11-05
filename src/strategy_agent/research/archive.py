"""Run archive: one new directory per run, never overwritten.

Layout (Asia/Taipei clock):  <root>/YYYYMMDD_HHMMSS_<agent_sha7>[-N]/
    run_info.txt              human-readable summary + archive status
    experiment_record.json    every registry event of this experiment (hash-chained copies)
    stages.json               stage log (inputs / outputs / status / error per stage)
    fills.csv trades.csv invalidated_trades.csv rejections.csv equity_curve.csv decisions.csv
    metrics.json              evaluation output (+ sha256)
    research_report.md
    snapshot_refs.json        dataset / snapshot / producer / CostProfile / Core / Agent pins
    dataset_metadata.json     source metadata (no raw market data)
    manifest.json             sha256 + size of every file above (written last, then re-verified)

The default root is Google Drive ``MyDrive/Quant_Research/strategy-research-agent``. With ``require_drive`` the
archiver refuses to fall back: if Drive is not mounted it writes the run to a LOCAL directory marked
``LOCAL_ONLY_DRIVE_UNAVAILABLE`` and returns status ``DRIVE_UNAVAILABLE`` - it never reports a backup it did not
make. Raw market data (Parquet) and secrets are never written here.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

DRIVE_MOUNT = Path("/content/drive/MyDrive")
DRIVE_ROOT = DRIVE_MOUNT / "Quant_Research" / "strategy-research-agent"
TZ = ZoneInfo("Asia/Taipei")
FORBIDDEN_SUFFIXES = (".parquet", ".env", ".key", ".pem")


class ArchiveError(RuntimeError):
    pass


def _dump(obj) -> str:
    return json.dumps(obj, indent=1, ensure_ascii=False, sort_keys=True, default=str) + "\n"


def _csv(rows, columns=None) -> str:
    rows = [dict(r) for r in rows]
    cols = columns or sorted({k for r in rows for k in r})
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n", extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k) for k in cols})
    return buf.getvalue()


class Archiver:
    def __init__(self, root: str | Path | None = None, *, require_drive: bool = False,
                 local_fallback: str | Path = "_local_runs", clock=None):
        self.require_drive = require_drive
        self.clock = clock or (lambda: datetime.now(TZ))
        self.local_fallback = Path(local_fallback)
        if root is None:
            root = DRIVE_ROOT
        self.root = Path(root)

    # ------------------------------------------------------------------ target
    def _target_root(self) -> tuple[Path, str, str | None]:
        on_drive = DRIVE_MOUNT in (self.root, *self.root.parents)
        if on_drive and not DRIVE_MOUNT.is_dir():
            # keep a traceable local copy, but report the failure: never claim a Drive backup
            return self.local_fallback, "DRIVE_UNAVAILABLE", f"Google Drive not mounted at {DRIVE_MOUNT}"
        if self.require_drive and not on_drive:
            return self.root, "DRIVE_UNAVAILABLE", f"require_drive set but {self.root} is not under {DRIVE_MOUNT}"
        return self.root, "ARCHIVED", None

    def _new_dir(self, base: Path, agent_sha: str) -> Path:
        base.mkdir(parents=True, exist_ok=True)
        name = f"{self.clock().astimezone(TZ):%Y%m%d_%H%M%S}_{agent_sha[:7]}"
        for i in range(1, 100):
            d = base / (name if i == 1 else f"{name}-{i}")
            try:
                d.mkdir()                      # exclusive: an existing run directory is never reused
                return d
            except FileExistsError:
                continue
        raise ArchiveError(f"could not allocate a new run directory under {base}")

    # ------------------------------------------------------------------ write
    def write(self, res) -> dict:
        base, status, reason = self._target_root()
        d = self._new_dir(base, res.context["agent"]["agent_sha"])
        files = self._files(res, status, reason, d)
        written = []
        try:
            for name, text in files.items():
                if name.endswith(FORBIDDEN_SUFFIXES):
                    raise ArchiveError(f"refusing to archive {name}")
                (d / name).write_text(text, encoding="utf-8")
                written.append(name)
        except (OSError, ArchiveError) as e:
            return self._record_failure(res, d, written, f"{type(e).__name__}: {e}")
        return {"status": status, "reason": reason, "path": str(d), "files": sorted(files) + ["manifest.json"]}

    def _record_failure(self, res, d: Path, written: list[str], reason: str) -> dict:
        """A write that fails midway leaves an explicit, traceable failure record - never a half archive that
        looks complete: ARCHIVE_FAILED.json in the run dir (if writable) and a copy under the local fallback."""
        rec = {"status": "FAILED", "reason": reason, "run_dir": str(d), "files_written_before_failure": written,
               "experiment_id": res.record.get("experiment_id"), "registry_status": res.status,
               "run_identity": res.identity, "metrics_sha256": res.metrics_sha256,
               "at": self.clock().astimezone(TZ).isoformat(), "note": "no manifest was written; this run is NOT archived"}
        where = []
        for target in (d / "ARCHIVE_FAILED.json",
                       self.local_fallback / "_archive_failures" / f"{d.name}.json"):
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(_dump(rec), encoding="utf-8")
                where.append(str(target))
            except OSError:
                continue
        return {"status": "FAILED", "reason": reason, "path": str(d), "failure_records": where, "files": written}

    def finalize(self, res) -> None:
        """Rewrite the stage log with the archive stage included, then write and verify the manifest."""
        from .report import engineering_status  # noqa: PLC0415
        d = Path(res.archive["path"])
        (d / "stages.json").write_text(_dump(res.stages), encoding="utf-8")
        final = engineering_status(res, stages=tuple(s["name"] for s in res.stages))
        final["archive_status"] = res.archive["status"]
        final["strategy_recommendation"] = res.context.get("verdicts", {}).get("strategy", {}).get("decision")
        (d / "final_status.json").write_text(_dump(final), encoding="utf-8")
        man = {}
        for p in sorted(d.iterdir()):
            if p.name == "manifest.json":
                continue
            b = p.read_bytes()
            man[p.name] = {"sha256": hashlib.sha256(b).hexdigest(), "bytes": len(b)}
        (d / "manifest.json").write_text(_dump({"run_dir": d.name, "files": man}), encoding="utf-8")
        check = json.loads((d / "manifest.json").read_text(encoding="utf-8"))["files"]
        for name, v in check.items():
            if hashlib.sha256((d / name).read_bytes()).hexdigest() != v["sha256"]:
                raise ArchiveError(f"archive verification failed for {name}")
        res.archive["manifest_sha256"] = hashlib.sha256((d / "manifest.json").read_bytes()).hexdigest()
        res.archive["verified"] = True

    def _files(self, res, status: str, reason: str | None, d: Path) -> dict[str, str]:
        ctx, j, rep = res.context, res.job, res.report
        md = res.market_data.metadata if res.market_data else {}
        verdicts = ctx.get("verdicts", {})
        prof = ctx.get("cost_profile") or {}
        info = [
            f"run_dir: {d.name}",
            f"archive_status: {status}" + (f" ({reason}); this is a LOCAL copy, NOT a Drive backup" if reason else ""),
            f"created_at_asia_taipei: {self.clock().astimezone(TZ).isoformat()}",
            f"environment: {ctx['environment']}",
            f"experiment_id: {res.record['experiment_id']}",
            f"registry_status: {res.status}",
            f"run_identity: {res.identity}",
            f"strategy: {j.spec['strategy_id']}@{j.spec['version']} spec_sha256={ctx['spec_sha256']}",
            f"parameters: {json.dumps(j.parameters, sort_keys=True)}",
            f"research_config: {(ctx.get('research_config') or {}).get('config_id')}@"
            f"{(ctx.get('research_config') or {}).get('version')} sha256={(ctx.get('research_config') or {}).get('sha256')}",
            f"cost_scenario: {(ctx.get('cost_scenario') or {}).get('scenario_id')} multiplier={j.cost_multiplier}",
            f"window: warmup {j.warmup_start} | {j.window_start} -> {j.window_end} | role {j.role}",
            f"agent_sha: {ctx['agent']['agent_sha']} dirty={ctx['agent'].get('agent_tree_dirty')}",
            f"core_sha: {ctx['engine']['git_sha']} ({ctx['engine']['benchmark_status']})",
            f"dataset: {md.get('dataset_id')} snapshot={md.get('snapshot_id')} frame_sha256={md.get('frame_sha256')}",
            f"cost_profile: {prof.get('profile_id')}@{prof.get('version')} status={prof.get('status')} "
            f"sha256={prof.get('provenance', {}).get('profile_sha256')}",
            f"metrics_sha256: {res.metrics_sha256}",
            f"engineering_status: {verdicts.get('engineering', {}).get('status')}",
            f"strategy_recommendation: {verdicts.get('strategy', {}).get('decision')}",
        ]
        if ctx.get("overrides"):
            info += ["synthetic_overrides:"] + [f"  - {o}" for o in ctx["overrides"]]
        files = {
            "run_info.txt": "\n".join(info) + "\n",
            "experiment_record.json": _dump(res.events),
            "stages.json": _dump(res.stages),
            "research_report.md": res.research_report_md or "report not generated\n",
            "snapshot_refs.json": _dump({
                "dataset": {k: md.get(k) for k in ("dataset_id", "snapshot_id", "producer_commit_sha", "verdict",
                                                    "frame_sha256", "fixture_csv_sha256", "kind")},
                "cost_profile": {"profile_id": prof.get("profile_id"), "version": prof.get("version"),
                                 "status": prof.get("status"),
                                 "agent_sha256": prof.get("provenance", {}).get("profile_sha256"),
                                 "core_sha256": rep.provenance.get("cost_profile_sha256") if rep else None},
                "core": ctx["engine"], "agent": ctx["agent"], "spec_sha256": ctx["spec_sha256"],
                "runner_version": ctx["runner_version"], "adapter_version": ctx["adapter_version"],
                "run_identity": res.identity, "research_config": ctx.get("research_config"),
                "cost_scenario": {k: (ctx.get("cost_scenario") or {}).get(k) for k in (
                    "scenario_id", "kind", "cost_multiplier", "profile_id", "version", "sha256", "approval_status")}
                if ctx.get("cost_scenario") else None}),
            "dataset_metadata.json": _dump(md),
        }
        if ctx.get("research_config"):
            files["research_config.json"] = _dump(ctx["research_config"])
        if ctx.get("cost_scenario"):
            files["cost_scenario.json"] = _dump(ctx["cost_scenario"])
        if res.metrics is not None:
            files["metrics.json"] = _dump({"metrics_sha256": res.metrics_sha256, "metrics": res.metrics,
                                           "verdicts": verdicts})
        if rep is not None:
            files["fills.csv"] = _csv(rep.fills)
            files["trades.csv"] = _csv(rep.round_trips)
            files["invalidated_trades.csv"] = _csv(rep.invalidated_round_trips) if rep.invalidated_round_trips else ""
            files["rejections.csv"] = _csv(rep.rejections)
            files["equity_curve.csv"] = _csv(rep.equity_curve)
        if res.decisions:
            files["decisions.csv"] = _csv([{"decision_ts": x.decision_ts.isoformat(), "target": x.target,
                                            "contract": x.contract, "reason": x.reason} for x in res.decisions],
                                          ["decision_ts", "target", "contract", "reason"])
        return files


def latest_runs(root: str | Path) -> list[Path]:
    p = Path(root)
    return sorted((x for x in p.iterdir() if x.is_dir() and (x / "manifest.json").exists()), key=lambda x: x.name) \
        if p.exists() else []


def write_text_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
