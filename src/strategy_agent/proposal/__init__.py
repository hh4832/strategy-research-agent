"""Research design only: draft → deterministic checks → pending human review. No execution API."""
from .agent import draft_proposal, validate_proposal, write_proposal, DraftRejected
from .adapters import FakeLLMAdapter, FileDraftAdapter

__all__=['draft_proposal','validate_proposal','write_proposal','DraftRejected','FakeLLMAdapter','FileDraftAdapter']
