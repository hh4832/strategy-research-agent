"""Phase 5-A3: Research Proposal (A2) x Research Orchestrator (A1) integration.

Research Hypothesis -> Proposal (A2) -> Research Request (A2 exporter, shared schema 1.0.0) -> Governance (A1)
-> Orchestrator (A1) -> Synthetic / Archive Replay -> Evaluator (Phase 4-C) -> Decision -> Archive.

This package only wires the two existing components together. It never approves, never creates or selects a
Research Authorization, never consumes a batch, never reads the Holdout and never loads the research-only runner.
"""
from .integration import (CONTRACT, contract_check, proposal_to_request, submit_proposal, trace,  # noqa: F401
                          LinkLog)
