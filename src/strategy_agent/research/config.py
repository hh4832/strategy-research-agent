"""Research configuration (M3-B.2): schema + version + sha256 + semantic rules.

A configuration is one reviewed research question. Every run started from it carries its id, version and sha256
(run identity, archive, report). Rules (all reported together; any ERROR refuses the configuration):

  RC01 JSON-schema valid (``schemas/RESEARCH_CONFIG_SCHEMA.json``); JSON or YAML (YAML needs PyYAML)
  RC02 ``provenance.config_sha256`` equals the canonical hash of the content (edit => re-hash => new config)
  RC03 the base strategy spec file exists, its sha256 matches, strategy_id / version match
  RC04 parameters pass R02 against the spec (reported here; the run is still submitted so that the registry
       records REJECTED_SPEC R02 - invalid parameters are never silently dropped)
  RC05 instrument symbol / timeframe / dataset pins equal the spec (REAL) or the synthetic fixture (SYNTHETIC)
  RC06 temporal policy is a REFERENCE: REAL configs must name the Core gate's policy with the gate's pinned
       evidence and may not claim ACCEPTED while the gate's TEMPORAL_SAFETY is not PASS (UNKNOWN never becomes
       CONFIRMED); SYNTHETIC configs must state the fixture's own definition
  RC07 cost profile ref equals the scenario set's BASE profile; scenario set file sha256 matches; scenario set
       rules CS01-CS07 (``research.cost_scenarios``)
  RC08 windows ordered: warmup <= in_sample.start < in_sample.end <= validation.start < validation.end <= holdout
  RC09 holdout id: SYNTHETIC -> the isolated test lock; REAL -> an approved lock (else HOLDOUT_APPROVAL fails)
  RC10 capital, position sizing and risk limits equal the spec (a change needs a new spec version)
  RC11 output: REAL archives go to Drive only; LOCAL output is allowed for SYNTHETIC only
  RC12 REAL configs: the market gate must be GO (all six gates PASS) and ``market.gate_version`` current
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import jsonschema

from strategy_agent.runner import parameter_issues
from strategy_agent.spec.validate import canonical_hash

from . import cost_scenarios as CS
from .data import readiness

ROOT = Path(__file__).resolve().parents[3]
SCHEMA = ROOT / "schemas" / "RESEARCH_CONFIG_SCHEMA.json"
SYNTHETIC_TEMPORAL_POLICY = "SYNTHETIC_FIXTURE_START_LABEL_AVAILABLE_AT_BAR_END"


class ResearchConfigError(ValueError):
    def __init__(self, issues: list[tuple[str, str]]):
        self.issues = issues
        super().__init__("; ".join(f"{r}: {m}" for r, m in issues))


@dataclass(frozen=True)
class ResearchConfig:
    data: dict
    path: Path | None
    sha256: str
    base_spec: dict
    scenario_meta: dict
    scenarios: tuple
    issues: tuple                      # (rule, message) including non-blocking RC04

    @property
    def ref(self) -> dict:
        d = self.data
        return {"config_id": d["config_id"], "version": d["version"], "sha256": self.sha256, "kind": d["kind"],
                "path": str(self.path.relative_to(ROOT)) if self.path and self.path.is_relative_to(ROOT) else
                str(self.path) if self.path else None,
                "scenario_set": {k: self.scenario_meta[k] for k in ("scenario_set_id", "version", "sha256")},
                "temporal_policy": dict(d["temporal_policy"]),
                "market": dict(d["market"]), "dataset": dict(d["dataset"]), "holdout": dict(d["holdout"]),
                "windows": d["windows"]}


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def config_hash(cfg: dict) -> str:
    c = copy.deepcopy(cfg)
    c["provenance"]["config_sha256"] = None
    return hashlib.sha256(json.dumps(c, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def load_raw(path: Path) -> dict:
    text = Path(path).read_text(encoding="utf-8")
    if Path(path).suffix in {".yaml", ".yml"}:
        try:
            import yaml  # noqa: PLC0415
        except ImportError as e:   # pragma: no cover - optional dependency
            raise ResearchConfigError([("RC01", "YAML config needs PyYAML (pip install pyyaml)")]) from e
        return yaml.safe_load(text)
    return json.loads(text)


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def validate_config(cfg: dict, *, path: Path | None = None, profiles: dict, fixture_dataset: dict | None = None,
                    synthetic_holdout: tuple[str, str] | None = None, holdout_book=None,
                    readiness_path: Path | None = None, core_sha: str | None = None) -> ResearchConfig:
    issues: list[tuple[str, str]] = []
    try:
        jsonschema.validate(cfg, json.loads(SCHEMA.read_text(encoding="utf-8")))
    except jsonschema.ValidationError as e:
        raise ResearchConfigError([("RC01", f"schema: {e.message} at /{'/'.join(map(str, e.absolute_path))}")])
    sha = config_hash(cfg)
    if cfg["provenance"]["config_sha256"] != sha:
        issues.append(("RC02", f"config_sha256 {cfg['provenance']['config_sha256']} != content hash {sha}: an edited "
                               "config must be re-hashed and given a new version"))
    real = cfg["kind"] == "REAL_MARKET"

    # RC03 spec
    sp = ROOT / cfg["strategy"]["spec_path"]
    if not sp.is_file() or not sp.resolve().is_relative_to(ROOT):
        raise ResearchConfigError(issues + [("RC03", f"spec {cfg['strategy']['spec_path']} not found in repository")])
    if file_sha256(sp) != cfg["strategy"]["spec_file_sha256"]:
        issues.append(("RC03", "spec file sha256 mismatch"))
    spec = json.loads(sp.read_text(encoding="utf-8"))
    if (spec["strategy_id"], spec["version"]) != (cfg["strategy"]["strategy_id"], cfg["strategy"]["version"]):
        issues.append(("RC03", f"spec is {spec['strategy_id']}@{spec['version']}"))

    # RC04 R02 (non-blocking here; recorded by the registry as REJECTED_SPEC when submitted)
    issues += [("RC04", f"{i.path} {i.message}") for i in parameter_issues(spec, dict(cfg["parameters"]))]

    # RC05 instrument / timeframe / dataset
    u = spec["universe"]
    if cfg["instrument"]["symbol"] not in u["symbols"] or cfg["timeframe"] != u["timeframe"]:
        issues.append(("RC05", f"instrument/timeframe {cfg['instrument']['symbol']}/{cfg['timeframe']} != spec "
                               f"{u['symbols']}/{u['timeframe']}"))
    want = fixture_dataset if not real else spec["data_requirements"]["datasets"][0]
    if want is None or any(cfg["dataset"][k] != want.get(k) for k in ("dataset_id", "snapshot_id", "producer_commit_sha")):
        issues.append(("RC05", f"dataset pins {cfg['dataset']} != {'spec' if real else 'synthetic fixture'}"))

    # RC06 temporal policy (reference only)
    tp = cfg["temporal_policy"]
    gate = readiness(cfg["market"]["market_id"], readiness_path) if readiness_path else readiness(cfg["market"]["market_id"])
    if real:
        facts = (gate.entry.get("facts") or {}).get("conservative_temporal_policy") or {}
        tgate = (gate.entry.get("gates") or {}).get("TEMPORAL_SAFETY") or {}
        if tp["source"] != "CORE_GATE" or tp["status"] == "FIXTURE_DEFINITION":
            issues.append(("RC06", "REAL configs must reference the Core gate's temporal policy"))
        if tp["policy_id"] != facts.get("policy_id"):
            issues.append(("RC06", f"policy {tp['policy_id']} != Core gate policy {facts.get('policy_id')}"))
        if tp["evidence_ref"] != tgate.get("evidence_ref"):
            issues.append(("RC06", "temporal evidence_ref differs from the gate's pinned evidence"))
        if tp["status"] == "ACCEPTED" and gate.gate_status().get("TEMPORAL_SAFETY") != "PASS":
            issues.append(("RC06", f"config claims ACCEPTED but gate TEMPORAL_SAFETY is "
                                   f"{gate.gate_status().get('TEMPORAL_SAFETY')} (UNKNOWN is never upgraded)"))
    else:
        if tp["source"] != "SYNTHETIC_FIXTURE" or tp["status"] != "FIXTURE_DEFINITION" or \
                tp["policy_id"] != SYNTHETIC_TEMPORAL_POLICY:
            issues.append(("RC06", f"SYNTHETIC configs use {SYNTHETIC_TEMPORAL_POLICY} / FIXTURE_DEFINITION"))

    # RC07 costs
    ss = ROOT / cfg["costs"]["scenario_set"]["path"]
    meta, scen = {"scenario_set_id": None, "version": None, "sha256": None, "scenarios": []}, ()
    if not ss.is_file():
        issues.append(("RC07", f"scenario set {cfg['costs']['scenario_set']['path']} not found"))
    elif file_sha256(ss) != cfg["costs"]["scenario_set"]["file_sha256"]:
        issues.append(("RC07", "scenario set file sha256 mismatch"))
    else:
        try:
            meta, sc = CS.load_set(ss, profiles=profiles, spec=spec, core_sha=core_sha,
                                   instrument_type="FUTURES" if u["market"] in {"TXF"} else "EQUITY")
            scen = tuple(sc)
            base = next(x for x in sc if x.kind == "BASE")
            if base.record["sha256"] != cfg["costs"]["profile_ref"]["sha256"] or \
                    (base.record["profile_id"], base.record["version"]) != (cfg["costs"]["profile_ref"]["profile_id"],
                                                                            cfg["costs"]["profile_ref"]["version"]):
                issues.append(("RC07", "costs.profile_ref is not the scenario set's BASE profile"))
            if cfg["capital"]["currency"] != base.record["currency"]:
                issues.append(("RC07", f"capital currency {cfg['capital']['currency']} != cost currency "
                                       f"{base.record['currency']}"))
        except CS.CostScenarioError as e:
            issues.append(("RC07", str(e)))
    if real:
        pr = spec["costs"].get("profile_ref") or {}
        if (pr.get("profile_id"), pr.get("version")) != (cfg["costs"]["profile_ref"]["profile_id"],
                                                         cfg["costs"]["profile_ref"]["version"]):
            issues.append(("RC07", "REAL config cost profile differs from the spec's profile_ref"))

    # RC08 windows / RC09 holdout
    w = cfg["windows"]
    try:
        ws, is_s, is_e = _dt(w["warmup_start"]), _dt(w["in_sample"]["start"]), _dt(w["in_sample"]["end"])
        va_s, va_e = _dt(w["validation"]["start"]), _dt(w["validation"]["end"])
        if not (ws <= is_s < is_e <= va_s < va_e):
            issues.append(("RC08", "windows must satisfy warmup <= IS.start < IS.end <= VAL.start < VAL.end"))
    except (ValueError, TypeError) as e:
        issues.append(("RC08", f"window timestamps: {e}"))
        va_e = None
    hid = cfg["holdout"]["holdout_id"]
    if not real:
        if synthetic_holdout is None or hid != synthetic_holdout[0]:
            issues.append(("RC09", f"SYNTHETIC holdout must be the isolated test lock {synthetic_holdout and synthetic_holdout[0]}"))
        elif va_e is not None and va_e > _dt(synthetic_holdout[1]):
            issues.append(("RC08", f"validation ends after the holdout start {synthetic_holdout[1]}"))
    else:
        lock = holdout_book.locks.get(hid) if holdout_book is not None else None
        if lock is None:
            issues.append(("RC09", f"holdout {hid} has no approved lock"))
        elif va_e is not None and va_e > lock.holdout_start:
            issues.append(("RC08", f"validation ends after the holdout start {lock.holdout_start.isoformat()}"))
        if spec["experiment"].get("holdout_lock_id") != hid:
            issues.append(("RC09", f"spec names holdout {spec['experiment'].get('holdout_lock_id')}"))

    # RC10 capital / sizing / risk
    ps = spec["position_sizing"]
    if cfg["capital"]["initial_capital"] != ps.get("initial_capital"):
        issues.append(("RC10", "initial_capital differs from the spec"))
    if cfg["position_sizing"] != {k: v for k, v in ps.items() if k != "initial_capital"}:
        issues.append(("RC10", "position_sizing differs from the spec"))
    if cfg["risk_limits"] != spec.get("risk_constraints", {}):
        issues.append(("RC10", "risk_limits differ from the spec's risk_constraints"))

    # RC11 output
    if cfg["output"]["target"] == "LOCAL" and real:
        issues.append(("RC11", "REAL configs archive to Drive only"))
    if cfg["output"]["target"] == "DRIVE" and cfg["output"].get("path"):
        issues.append(("RC11", "DRIVE output path is fixed (MyDrive/Quant_Research/strategy-research-agent)"))

    # RC12 market gate (REAL)
    if real:
        if cfg["market"]["gate_version"] != gate.entry.get("gate_version"):
            issues.append(("RC12", f"config reviewed against gate {cfg['market']['gate_version']}, current is "
                                   f"{gate.entry.get('gate_version')}"))
        miss = gate.missing()
        if miss:
            issues.append(("RC12", f"market gate {cfg['market']['market_id']} not GO: {miss[0]}"))

    blocking = [x for x in issues if x[0] != "RC04"]
    if blocking:
        raise ResearchConfigError(issues)
    return ResearchConfig(cfg, path, sha, spec, meta, scen, tuple(issues))


def stamp(cfg: dict) -> dict:
    """Return a copy with provenance.config_sha256 set (used when authoring a new config version)."""
    c = copy.deepcopy(cfg)
    c["provenance"]["config_sha256"] = config_hash(c)
    return c


def assert_spec_matches(cfg: ResearchConfig, runtime_spec: dict) -> None:
    """The executed spec must keep the base spec's strategy content (rules, parameters, sizing)."""
    for k in ("signal", "parameters", "position_sizing", "risk_exits", "risk_constraints"):
        if runtime_spec.get(k) != cfg.base_spec.get(k):
            raise ResearchConfigError([("RC03", f"runtime spec changed {k}")])
    if canonical_hash(runtime_spec) is None:   # pragma: no cover
        raise ResearchConfigError([("RC03", "runtime spec not hashable")])
