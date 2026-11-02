"""Phase-3 ports: the ONLY interfaces Strategy Agent code may depend on.

Phase 2 (`market-research-core`) is integrated later through a thin adapter
that implements these Protocols. Nothing here imports market_research_core,
so an unfinished or changed Phase-2 API cannot leak into Phase-3 logic.
See STRATEGY_AGENT_ARCHITECTURE.md §6 (integration contract).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

CONVENTION = "oracle-execution-convention-v1"

# Columns every bar frame handed to the strategy layer must carry (contract C1).
REQUIRED_BAR_COLUMNS = (
    "timestamp", "available_at", "open", "high", "low", "close", "volume",
    "contract", "session", "data_exists", "research_eligible", "backtest_eligible",
    "buy_execution_eligible", "sell_execution_eligible",
)

# Provenance every backtest report must return (contract C4).
REQUIRED_PROVENANCE = (
    "engine_name", "engine_git_sha", "engine_version", "execution_convention",
    "dataset_snapshot_id", "dataset_producer_sha", "dataset_verdict", "exclusions_applied",
    "spec_sha256", "parameters", "cost_multiplier", "seed", "window_start", "window_end",
)


@dataclass(frozen=True)
class DatasetRef:
    dataset_id: str
    snapshot_id: str
    producer_commit_sha: str
    verdict: str
    usage_scope: str
    mandatory_exclusions: tuple[str, ...] = ()


@dataclass(frozen=True)
class BacktestRequest:
    spec: Mapping[str, Any]
    spec_sha256: str
    datasets: Sequence[DatasetRef]
    window_start: str            # inclusive, ISO-8601 with offset
    window_end: str              # exclusive, ISO-8601 with offset
    warmup_start: str            # data before window_start used ONLY for indicator warm-up
    cost_multiplier: float = 1.0
    seed: int = 0
    parameters: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class BacktestReport:
    fills: Sequence[Mapping[str, Any]]
    round_trips: Sequence[Mapping[str, Any]]
    rejections: Sequence[Mapping[str, Any]]
    equity_curve: Sequence[Mapping[str, Any]]      # timestamp, equity, position
    open_position: Mapping[str, Any] | None
    provenance: Mapping[str, Any]
    status: str = "RESEARCH_BACKTEST"               # never "LIVE" / "TRADABLE"


@runtime_checkable
class MarketDataPort(Protocol):
    def load(self, ref: DatasetRef, symbol: str, start: str, end: str,
             contract: str | None = None) -> Any: ...


@runtime_checkable
class BacktestPort(Protocol):
    def run(self, request: BacktestRequest) -> BacktestReport: ...


class ContractViolation(ValueError):
    pass


def check_bar_columns(columns: Sequence[str]) -> None:
    missing = [c for c in REQUIRED_BAR_COLUMNS if c not in set(columns)]
    if missing:
        raise ContractViolation(f"bar frame missing contract columns: {missing}")


def check_report(report: BacktestReport, request: BacktestRequest) -> None:
    p = report.provenance
    missing = [k for k in REQUIRED_PROVENANCE if k not in p or p[k] in (None, "")]
    if missing:
        raise ContractViolation(f"report provenance incomplete: {missing}")
    if p["execution_convention"] != CONVENTION:
        raise ContractViolation(f"engine convention {p['execution_convention']} != {CONVENTION}")
    if p["spec_sha256"] != request.spec_sha256:
        raise ContractViolation("report is for a different spec")
    if len(str(p["engine_git_sha"])) < 7 or str(p["engine_git_sha"]).endswith("+source"):
        raise ContractViolation("engine_git_sha must be a real commit, not a package version")
    if report.status not in {"RESEARCH_BACKTEST", "RESEARCH_INTEGRATION_DEMO"}:
        raise ContractViolation("research reports cannot claim live/tradable status")
    for f in report.fills:
        if str(f.get("exec_ts", "")) < request.window_start or str(f.get("exec_ts", "")) >= request.window_end:
            raise ContractViolation(f"fill outside evaluation window: {f.get('exec_ts')}")
