from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class DecisionContext:
    input_text: str
    trigger_kind: str
    recent_turns: list[dict[str, Any]]
    time_context: dict[str, Any]
    affect_context: dict[str, Any]
    drive_state_summary: list[dict[str, Any]] | None
    foreground_world_state: list[dict[str, Any]] | None
    ongoing_action_summary: dict[str, Any] | None
    capability_decision_view: list[dict[str, Any]] | None
    initiative_context: dict[str, Any] | None
    capability_result_context: dict[str, Any] | None
    visual_observation_context: dict[str, Any] | None
    recall_hint: dict[str, Any]
    recall_pack: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ReplyContext:
    input_text: str
    recent_turns: list[dict[str, Any]]
    time_context: dict[str, Any]
    affect_context: dict[str, Any]
    drive_state_summary: list[dict[str, Any]] | None
    foreground_world_state: list[dict[str, Any]] | None
    ongoing_action_summary: dict[str, Any] | None
    initiative_context: dict[str, Any] | None
    visual_observation_context: dict[str, Any] | None
    recall_hint: dict[str, Any]
    recall_pack: dict[str, Any]
    decision: dict[str, Any]
