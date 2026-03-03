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


# Block: Memory job record
@dataclass(frozen=True, slots=True)
class MemoryJobRecord:
    job_id: str
    job_kind: str
    created_at: int
    payload: dict[str, Any]


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


# Block: Task state mutation record
@dataclass(frozen=True, slots=True)
class TaskStateMutationRecord:
    task_id: str
    task_kind: str
    task_status: str
    goal_hint: str
    completion_hint: dict[str, Any]
    resume_condition: dict[str, Any]
    interruptible: bool
    priority: int
    title: str | None
    step_hints: list[dict[str, Any]]
    created_at: int


# Block: Task state record
@dataclass(frozen=True, slots=True)
class TaskStateRecord:
    task_id: str
    task_kind: str
    task_status: str
    goal_hint: str
    completion_hint: dict[str, Any]
    resume_condition: dict[str, Any]
    interruptible: bool
    priority: int
    title: str | None
    step_hints: list[dict[str, Any]]
    created_at: int
    updated_at: int


# Block: Pending input mutation record
@dataclass(frozen=True, slots=True)
class PendingInputMutationRecord:
    source: str
    channel: str
    payload: dict[str, Any]
    priority: int
    created_at: int


# Block: Cognition state snapshot
@dataclass(frozen=True, slots=True)
class CognitionStateSnapshot:
    self_state: dict[str, Any]
    attention_state: dict[str, Any]
    body_state: dict[str, Any]
    world_state: dict[str, Any]
    drive_state: dict[str, Any]
    effective_settings: dict[str, Any]
