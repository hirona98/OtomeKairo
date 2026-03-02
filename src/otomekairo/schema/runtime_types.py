"""Shared runtime data shapes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# Block: Pending input record
@dataclass(frozen=True, slots=True)
class PendingInputRecord:
    input_id: str
    source: str
    channel: str
    created_at: int
    payload: dict[str, Any]


# Block: Settings override record
@dataclass(frozen=True, slots=True)
class SettingsOverrideRecord:
    override_id: str
    key: str
    requested_value_json: dict[str, Any]
    apply_scope: str
    created_at: int


# Block: Action history record
@dataclass(frozen=True, slots=True)
class ActionHistoryRecord:
    result_id: str
    command_id: str
    action_type: str
    command: dict[str, Any]
    started_at: int
    finished_at: int
    status: str
    failure_mode: str | None
    observed_effects: dict[str, Any] | None
    raw_result_ref: dict[str, Any] | None
    adapter_trace_ref: dict[str, Any] | None


# Block: Cognition state snapshot
@dataclass(frozen=True, slots=True)
class CognitionStateSnapshot:
    self_state: dict[str, Any]
    attention_state: dict[str, Any]
    body_state: dict[str, Any]
    world_state: dict[str, Any]
    drive_state: dict[str, Any]
    effective_settings: dict[str, Any]
