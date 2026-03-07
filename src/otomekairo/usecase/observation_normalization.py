"""Normalize pending input observations into runtime vocabulary."""

from __future__ import annotations

from typing import Any


# Block: Public source normalization
def normalize_observation_source(*, source: str, payload: dict[str, Any]) -> str:
    input_kind = _input_kind(payload)
    if input_kind == "camera_observation":
        if source == "post_action_followup":
            return "post_action_followup"
        return "camera"
    return source


# Block: Public kind normalization
def normalize_observation_kind(*, payload: dict[str, Any]) -> str:
    input_kind = _input_kind(payload)
    if input_kind == "chat_message":
        return _chat_observation_kind(payload)
    if input_kind == "camera_observation":
        return "scene_change"
    if input_kind == "network_result":
        return "search_result"
    if input_kind == "cancel":
        return "instruction"
    return input_kind


# Block: Public trigger reason normalization
def normalize_trigger_reason(*, source: str, payload: dict[str, Any]) -> str:
    trigger_reason = payload.get("trigger_reason")
    if isinstance(trigger_reason, str) and trigger_reason:
        return trigger_reason
    input_kind = _input_kind(payload)
    if input_kind == "network_result" or source == "network_result":
        return "external_result"
    if input_kind == "camera_observation":
        return "self_initiated"
    return "external_input"


# Block: Input kind reader
def _input_kind(payload: dict[str, Any]) -> str:
    input_kind = payload.get("input_kind")
    if not isinstance(input_kind, str) or not input_kind:
        raise RuntimeError("payload.input_kind must be non-empty string")
    return input_kind


# Block: Chat kind reader
def _chat_observation_kind(payload: dict[str, Any]) -> str:
    message_kind = payload.get("message_kind")
    if message_kind is None:
        return "dialogue_turn"
    if not isinstance(message_kind, str) or message_kind not in {"dialogue_turn", "instruction"}:
        raise RuntimeError("chat_message.message_kind must be dialogue_turn or instruction")
    return message_kind
