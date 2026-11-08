"""Approval token grammar and request scope (pure; no I/O).

A GitHub comment is approval evidence only if its body contains EXACTLY ONE line matching ``APPROVAL_LINE``; every
other character of the comment is ignored and is never executed, evaluated or forwarded to a model.

  RESEARCH-APPROVAL v1 decision=APPROVE intent=BIND_AUTHORIZATION request=<sha256> authorization=<RA-id> scope=<sha256> expires=<UTC Z>
  RESEARCH-APPROVAL v1 decision=APPROVE intent=APPROVE_TASK request=<sha256> authorization=<RA-id> scope=<sha256> task=<T-id> expires=<UTC Z>
  RESEARCH-APPROVAL-REVOKE v1 comment=<comment id>

``request`` is the full canonical request hash (orchestration.request.request_hash) of the subject: the PENDING source
request for BIND_AUTHORIZATION, the bound request for APPROVE_TASK. Because the hash (and the task id derived from it)
cannot be known before that request exists, a valid token also proves the approval was written after the subject.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

APPROVAL_LINE = re.compile(
    r"^RESEARCH-APPROVAL v1 decision=(?P<decision>APPROVE) intent=(?P<intent>BIND_AUTHORIZATION|APPROVE_TASK) "
    r"request=(?P<request>[0-9a-f]{64}) authorization=(?P<authorization>RA-[A-Za-z0-9_.-]{3,120}) "
    r"scope=(?P<scope>[0-9a-f]{64})(?: task=(?P<task>T-[0-9a-f]{20}))? "
    r"expires=(?P<expires>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)$")
REVOKE_LINE = re.compile(r"^RESEARCH-APPROVAL-REVOKE v1 comment=(?P<comment>[0-9]{1,20})$")
ANY_MARKER = re.compile(r"RESEARCH-APPROVAL")

SCOPE_FIELDS = ("market", "strategy_family", "strategy_spec_ref", "strategy_spec_sha256", "dataset_ref",
                "dataset_snapshot_id", "dataset_manifest_sha256", "core_commit", "signal_time_rule",
                "execution_time_rule", "cost_profile_ref", "research_window", "requested_change", "execution_mode",
                "replay_source", "research_budget", "holdout_policy", "requested_outputs")


class TokenError(ValueError):
    def __init__(self, code: str, message: str = ""):
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


@dataclass(frozen=True)
class ApprovalToken:
    intent: str
    request: str
    authorization: str
    scope: str
    task: str | None
    expires: datetime
    line: str


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def scope_of(request: dict) -> dict:
    """Everything the approval is about except identity text and authorization_ref (which binding changes)."""
    return {k: request[k] for k in SCOPE_FIELDS if k in request}


def scope_sha256(request: dict) -> str:
    return hashlib.sha256(canonical(scope_of(request))).hexdigest()


def _utc(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def parse_approval(body: str) -> ApprovalToken:
    """Exactly one well-formed approval line; any other line mentioning the marker makes the comment ambiguous."""
    if not isinstance(body, str):
        raise TokenError("BODY_NOT_TEXT")
    lines = [ln.strip() for ln in body.replace("\r\n", "\n").split("\n")]
    marked = [ln for ln in lines if ANY_MARKER.search(ln)]
    good = [m for m in (APPROVAL_LINE.fullmatch(ln) for ln in marked) if m]
    if not marked:
        raise TokenError("NO_APPROVAL_TOKEN", "comment contains no RESEARCH-APPROVAL line")
    if len(good) != 1 or len(marked) != 1:
        raise TokenError("AMBIGUOUS_OR_MALFORMED_TOKEN",
                         f"{len(marked)} marker line(s), {len(good)} well-formed approval line(s); exactly one required")
    m = good[0]
    intent, task = m["intent"], m["task"]
    if (intent == "APPROVE_TASK") != (task is not None):
        raise TokenError("MALFORMED_TOKEN", "task= is required for APPROVE_TASK and forbidden for BIND_AUTHORIZATION")
    return ApprovalToken(intent, m["request"], m["authorization"], m["scope"], task, _utc(m["expires"]), m.group(0))


def revocations(body: str) -> set[int]:
    if not isinstance(body, str):
        return set()
    out = set()
    for ln in body.replace("\r\n", "\n").split("\n"):
        m = REVOKE_LINE.fullmatch(ln.strip())
        if m:
            out.add(int(m["comment"]))
    return out


def approval_line(*, intent: str, request_sha256: str, authorization_id: str, scope: str, expires_utc: str,
                  task_id: str | None = None) -> str:
    """The exact line a research lead posts. Round-trips through ``parse_approval``."""
    line = (f"RESEARCH-APPROVAL v1 decision=APPROVE intent={intent} request={request_sha256} "
            f"authorization={authorization_id} scope={scope}" + (f" task={task_id}" if task_id else "") +
            f" expires={expires_utc}")
    parse_approval(line)
    return line
