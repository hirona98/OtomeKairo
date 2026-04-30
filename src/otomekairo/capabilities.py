from __future__ import annotations

from copy import deepcopy
from typing import Any


# server が正本として持つ capability manifest。
CAPABILITY_MANIFESTS: dict[str, dict[str, Any]] = {
    "vision.capture": {
        "id": "vision.capture",
        "version": "1",
        "kind": "observation",
        "decision_description": "現在の画面状態を観測する",
        "when_to_use": [
            "ユーザーが画面内容について質問した",
            "判断に現在の画面状態が必要",
        ],
        "do_not_use_when": [
            "ユーザーが画面観測を拒否している",
            "現在の判断に画面情報が不要",
        ],
        "required_permissions": ["observe_desktop"],
        "input_schema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "enum": ["desktop"]},
                "mode": {"type": "string", "enum": ["still"]},
            },
            "required": ["source", "mode"],
            "additionalProperties": False,
        },
        "result_schema": {
            "type": "object",
            "properties": {
                "images": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "client_context": {
                    "type": ["object", "null"],
                },
                "error": {
                    "type": ["string", "null"],
                },
            },
            "required": ["images"],
            "additionalProperties": False,
        },
        "side_effects": {
            "external_world": False,
            "user_visible": False,
            "stores_raw_payload": False,
        },
        "timeout_ms": 5000,
        "risk_level": "low",
        "memory_policy": {
            "record_result_event": True,
            "allow_memory_update": True,
        },
        "state_policy": {
            "creates_ongoing_action": True,
            "blocks_parallel_capability": True,
            "result_context_hook": "vision_capture",
            "followup_hint_hook": "vision_capture",
            "error_cooldown_seconds": 15,
            "unavailable_seconds_on_dispatch_failure": 15,
            "unavailable_seconds_on_timeout": 15,
        },
        "inspection_fields": [
            "capability_id",
            "target_client_id",
            "image_count",
            "image_interpreted",
            "visual_summary_text",
            "visual_confidence_hint",
            "body_state_summary",
            "device_state_summary",
            "schedule_summary",
            "error",
        ],
    },
    "external.status": {
        "id": "external.status",
        "version": "1",
        "kind": "external_service",
        "decision_description": "外部サービスの現在状態を確認する",
        "when_to_use": [
            "判断に外部サービスの現在状態が必要",
            "最新のサービス状態を短く確認したい",
        ],
        "do_not_use_when": [
            "現在の判断に外部サービス状態が不要",
            "すでに十分新しい状態要約が手元にある",
        ],
        "required_permissions": [],
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {"type": "string"},
            },
            "required": ["service"],
            "additionalProperties": False,
        },
        "result_schema": {
            "type": "object",
            "properties": {
                "status_text": {"type": "string"},
                "client_context": {
                    "type": ["object", "null"],
                },
                "error": {
                    "type": ["string", "null"],
                },
            },
            "required": ["status_text"],
            "additionalProperties": False,
        },
        "side_effects": {
            "external_world": False,
            "user_visible": False,
            "stores_raw_payload": False,
        },
        "timeout_ms": 5000,
        "risk_level": "low",
        "memory_policy": {
            "record_result_event": True,
            "allow_memory_update": True,
        },
        "state_policy": {
            "creates_ongoing_action": True,
            "blocks_parallel_capability": True,
            "result_context_hook": "external_status",
            "followup_hint_hook": "external_status",
            "success_cooldown_seconds": 60,
            "error_cooldown_seconds": 60,
            "unavailable_seconds_on_dispatch_failure": 60,
            "unavailable_seconds_on_timeout": 60,
        },
        "inspection_fields": [
            "capability_id",
            "target_client_id",
            "service",
            "status_text",
            "body_state_summary",
            "device_state_summary",
            "schedule_summary",
            "error",
        ],
    },
}


def capability_manifests() -> dict[str, dict[str, Any]]:
    # 呼び出し側が静的定義を変更しないよう複製する。
    return deepcopy(CAPABILITY_MANIFESTS)
