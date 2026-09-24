from __future__ import annotations

import re
from copy import deepcopy
from typing import Any


# server が正本として持つ capability manifest。
CAPABILITY_MANIFESTS: dict[str, dict[str, Any]] = {
    "vision.capture": {
        "id": "vision.capture",
        "version": "1",
        "kind": "observation",
        "decision_description": "現在の視覚状態を観測する",
        "when_to_use": [
            "ユーザーが見えている内容について質問した",
            "判断に現在の視覚状態が必要",
        ],
        "do_not_use_when": [
            "ユーザーが画面観測を拒否している",
            "現在の判断に視覚情報が不要",
        ],
        "required_permissions": ["observe_vision"],
        "input_schema": {
            "type": "object",
            "properties": {
                "vision_source_id": {"type": "string", "pattern": "^vision_source:"},
                "mode": {"type": "string", "enum": ["still"]},
            },
            "required": ["vision_source_id", "mode"],
            "additionalProperties": False,
        },
        "result_schema": {
            "type": "object",
            "properties": {
                "images": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 1,
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
        "timeout_ms": 45000,
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
            "unavailable_seconds_on_dispatch_failure": 15,
            "unavailable_seconds_on_timeout": 30,
        },
        "decision_readiness": {
            "family": "visual_observation",
            "world_state_type": "visual_context",
            "input_keys": ["vision_source_id", "mode"],
            "result_summary_keys": ["visual_summary_text"],
        },
        "inspection_fields": [
            "capability_id",
            "target_client_id",
            "vision_source_id",
            "source_kind",
            "source_label",
            "data_source",
            "unconnected_reason",
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
    "camera.ptz": {
        "id": "camera.ptz",
        "version": "1",
        "kind": "action",
        "decision_description": "OtomeKairo の camera source の向きや画角を調整する",
        "when_to_use": [
            "OtomeKairo 自身の camera 視覚の向きや画角を変える必要がある",
            "camera 観測前に対象を視野へ入れる必要がある",
            "camera.ptz result 後に同じ camera source を観測したい",
        ],
        "do_not_use_when": [
            "対象 source が camera ではない",
            "source が requested operation または amount をサポートしていない",
            "視覚根拠を得るだけで camera の向きや画角を変える必要がない",
        ],
        "required_permissions": ["control_camera_ptz"],
        "input_schema": {
            "type": "object",
            "properties": {
                "vision_source_id": {"type": "string", "pattern": "^vision_source:"},
                "operation": {
                    "type": "string",
                    "enum": [
                        "move_up",
                        "move_down",
                        "move_left",
                        "move_right",
                        "zoom_in",
                        "zoom_out",
                    ],
                },
                "amount": {
                    "type": "string",
                    "enum": ["small", "medium"],
                    "description": "通常のカメラ移動は medium。少しまたは微調整の意図が明示されている場合だけ small。",
                },
            },
            "required": ["vision_source_id", "operation", "amount"],
            "additionalProperties": False,
        },
        "result_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["completed", "rejected", "failed"]},
                "operation": {
                    "type": "string",
                    "enum": [
                        "move_up",
                        "move_down",
                        "move_left",
                        "move_right",
                        "zoom_in",
                        "zoom_out",
                    ],
                },
                "amount": {"type": "string", "enum": ["small", "medium"]},
                "client_context": {
                    "type": ["object", "null"],
                },
                "error": {
                    "type": ["string", "null"],
                },
            },
            "required": ["status", "operation", "amount"],
            "additionalProperties": False,
        },
        "side_effects": {
            "external_world": True,
            "user_visible": False,
            "stores_raw_payload": False,
        },
        "timeout_ms": 15000,
        "risk_level": "medium",
        "memory_policy": {
            "record_result_event": True,
            "allow_memory_update": True,
        },
        "state_policy": {
            "creates_ongoing_action": True,
            "blocks_parallel_capability": True,
            "result_context_hook": "camera_ptz",
            "followup_hint_hook": "camera_ptz",
            "allow_followup_capability_requests": [
                {
                    "capability_id": "vision.capture",
                    "constraint": "same_vision_source_id",
                }
            ],
            "unavailable_seconds_on_dispatch_failure": 15,
            "unavailable_seconds_on_timeout": 15,
        },
        "decision_readiness": {
            "family": "camera_control",
            "world_state_type": "visual_context",
            "input_keys": ["vision_source_id", "operation", "amount"],
            "result_summary_keys": ["status", "operation", "amount"],
        },
        "inspection_fields": [
            "capability_id",
            "target_client_id",
            "vision_source_id",
            "source_kind",
            "source_owner",
            "source_label",
            "operation",
            "amount",
            "status",
            "data_source",
            "unconnected_reason",
            "body_state_summary",
            "device_state_summary",
            "schedule_summary",
            "error",
        ],
    },
    "mcp.call_tool": {
        "id": "mcp.call_tool",
        "version": "1",
        "kind": "external_service",
        "decision_description": "接続中の MCP server で許可された tool を呼び出す",
        "when_to_use": [
            "判断に MCP tool 経由の外部情報取得や外部サービス操作が必要",
            "MCP tool catalog に目的へ合う tool が available として載っている",
        ],
        "do_not_use_when": [
            "目的に合う MCP tool が tool catalog に存在しない",
            "tool の入力 schema を満たす arguments を組み立てられない",
            "MCP tool を使わず既存の専用 capability で足りる",
        ],
        "required_permissions": ["use_mcp_tools"],
        "input_schema": {
            "type": "object",
            "properties": {
                "mcp_server_id": {"type": "string"},
                "tool_name": {"type": "string"},
                "arguments": {"type": "object"},
            },
            "required": ["mcp_server_id", "tool_name", "arguments"],
            "additionalProperties": False,
        },
        "result_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["completed", "failed"]},
                "mcp_server_id": {"type": "string"},
                "tool_name": {"type": "string"},
                "is_error": {"type": "boolean"},
                "content": {"type": "array"},
                "structured_content": {"type": ["object", "null"]},
                "client_context": {"type": ["object", "null"]},
                "error": {"type": ["string", "null"]},
            },
            "required": ["status", "mcp_server_id", "tool_name", "is_error", "content"],
            "additionalProperties": False,
        },
        "side_effects": {
            "external_world": True,
            "user_visible": True,
            "stores_raw_payload": False,
        },
        "timeout_ms": 30000,
        "risk_level": "high",
        "memory_policy": {
            "record_result_event": True,
            "allow_memory_update": True,
        },
        "state_policy": {
            "creates_ongoing_action": True,
            "blocks_parallel_capability": True,
            "result_context_hook": "mcp_call_tool",
            "followup_hint_hook": "mcp_call_tool",
            "unavailable_seconds_on_dispatch_failure": 30,
            "unavailable_seconds_on_timeout": 30,
        },
        "decision_readiness": {
            "family": "mcp_tool",
            "world_state_type": "external_service",
            "input_keys": ["mcp_server_id", "tool_name", "arguments"],
            "result_summary_keys": ["mcp_result_summary"],
        },
        "inspection_fields": [
            "capability_id",
            "target_client_id",
            "mcp_server_id",
            "tool_name",
            "status",
            "is_error",
            "mcp_result_summary",
            "error",
        ],
    },
}


def capability_manifests() -> dict[str, dict[str, Any]]:
    # 呼び出し側が静的定義を変更しないよう複製する。
    return deepcopy(CAPABILITY_MANIFESTS)


def validate_capability_payload(*, payload: Any, schema: Any, label: str) -> None:
    # manifest と接続中 MCP catalog が使う JSON Schema subset を共通検証する。
    if not isinstance(schema, dict):
        raise ValueError(f"{label} schema is invalid.")
    _validate_capability_schema_value(value=payload, schema=schema, path=label)


def _validate_capability_schema_value(*, value: Any, schema: dict[str, Any], path: str) -> None:
    any_of = schema.get("anyOf")
    if isinstance(any_of, list):
        matching_errors: list[str] = []
        for candidate in any_of:
            if not isinstance(candidate, dict):
                continue
            try:
                _validate_capability_schema_value(value=value, schema=candidate, path=path)
                break
            except ValueError as exc:
                matching_errors.append(str(exc))
        else:
            if matching_errors:
                raise ValueError(f"{path} does not match any allowed schema.")
            raise ValueError(f"{path} schema anyOf is invalid.")

    expected_type = schema.get("type")
    if expected_type is not None and not _capability_schema_type_matches(value, expected_type):
        raise ValueError(f"{path} type is invalid.")
    if "const" in schema and value != schema["const"]:
        raise ValueError(f"{path} value is not allowed.")
    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and value not in enum_values:
        raise ValueError(f"{path} value is not allowed.")
    pattern = schema.get("pattern")
    if isinstance(value, str) and isinstance(pattern, str) and re.search(pattern, value) is None:
        raise ValueError(f"{path} value does not match pattern.")
    if isinstance(value, str):
        min_length = schema.get("minLength")
        if isinstance(min_length, int) and not isinstance(min_length, bool) and len(value) < min_length:
            raise ValueError(f"{path} is too short.")
        max_length = schema.get("maxLength")
        if isinstance(max_length, int) and not isinstance(max_length, bool) and len(value) > max_length:
            raise ValueError(f"{path} is too long.")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        if isinstance(minimum, (int, float)) and not isinstance(minimum, bool) and value < minimum:
            raise ValueError(f"{path} is below minimum.")
        maximum = schema.get("maximum")
        if isinstance(maximum, (int, float)) and not isinstance(maximum, bool) and value > maximum:
            raise ValueError(f"{path} is above maximum.")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        required_names = schema.get("required", [])
        if isinstance(required_names, list):
            for required_name in required_names:
                if isinstance(required_name, str) and required_name not in value:
                    raise ValueError(f"{path}.{required_name} is required.")
        if schema.get("additionalProperties") is False and isinstance(properties, dict):
            extra_keys = sorted(set(value) - set(properties))
            if extra_keys:
                raise ValueError(f"{path} has unsupported properties: {', '.join(extra_keys)}")
        if isinstance(properties, dict):
            for key, child_schema in properties.items():
                if key in value and isinstance(child_schema, dict):
                    _validate_capability_schema_value(
                        value=value[key],
                        schema=child_schema,
                        path=f"{path}.{key}",
                    )
    if isinstance(value, list):
        min_items = schema.get("minItems")
        if isinstance(min_items, int) and not isinstance(min_items, bool) and len(value) < min_items:
            raise ValueError(f"{path} has too few items.")
        max_items = schema.get("maxItems")
        if isinstance(max_items, int) and not isinstance(max_items, bool) and len(value) > max_items:
            raise ValueError(f"{path} has too many items.")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_capability_schema_value(
                    value=item,
                    schema=item_schema,
                    path=f"{path}[{index}]",
                )


def _capability_schema_type_matches(value: Any, expected_type: Any) -> bool:
    if isinstance(expected_type, list):
        return any(_capability_schema_type_matches(value, item) for item in expected_type)
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "null":
        return value is None
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return True


# decision view と inspection が使う readiness 定義を manifest に集約する。
def capability_decision_readiness_from_manifest(manifest: dict[str, Any]) -> dict[str, Any] | None:
    readiness = manifest.get("decision_readiness")
    if not isinstance(readiness, dict):
        return None
    return deepcopy(readiness)


def capability_decision_readiness(capability_id: str) -> dict[str, Any] | None:
    manifest = CAPABILITY_MANIFESTS.get(capability_id)
    if not isinstance(manifest, dict):
        return None
    return capability_decision_readiness_from_manifest(manifest)


def capability_readiness_input_digest(
    capability_id: str,
    input_payload: dict[str, Any] | None,
) -> dict[str, Any] | None:
    digest = _capability_readiness_digest_base(capability_id)
    if digest is None:
        return None
    readiness = capability_decision_readiness(capability_id)
    if readiness is None:
        return None
    payload = input_payload if isinstance(input_payload, dict) else {}
    input_keys = _capability_readiness_key_list(readiness, "input_keys")
    present_keys = [key for key in input_keys if _has_capability_readiness_value(payload.get(key))]
    missing_keys = [key for key in input_keys if key not in present_keys]
    digest.update(
        {
            "input_keys": input_keys,
            "present_input_keys": present_keys,
            "missing_input_keys": missing_keys,
            "input_keys_satisfied": not missing_keys,
        }
    )
    return digest


def capability_readiness_result_digest(
    capability_id: str,
    result_payload: dict[str, Any] | None,
) -> dict[str, Any] | None:
    digest = _capability_readiness_digest_base(capability_id)
    if digest is None:
        return None
    readiness = capability_decision_readiness(capability_id)
    if readiness is None:
        return None
    payload = result_payload if isinstance(result_payload, dict) else {}
    summary_keys = _capability_readiness_key_list(readiness, "result_summary_keys")
    item_keys = _capability_readiness_key_list(readiness, "result_item_keys")
    present_summary_keys = [key for key in summary_keys if _has_capability_readiness_value(payload.get(key))]
    missing_summary_keys = [key for key in summary_keys if key not in present_summary_keys]
    present_item_keys = [key for key in item_keys if _has_capability_readiness_value(payload.get(key))]
    missing_item_keys = [key for key in item_keys if key not in present_item_keys]
    digest.update(
        {
            "result_summary_keys": summary_keys,
            "present_result_summary_keys": present_summary_keys,
            "missing_result_summary_keys": missing_summary_keys,
            "result_summary_keys_satisfied": not missing_summary_keys,
            "result_item_keys": item_keys,
            "present_result_item_keys": present_item_keys,
            "missing_result_item_keys": missing_item_keys,
            "result_item_keys_satisfied": not missing_item_keys,
        }
    )
    return digest


def _capability_readiness_digest_base(capability_id: str) -> dict[str, Any] | None:
    readiness = capability_decision_readiness(capability_id)
    if readiness is None:
        return None
    family = readiness.get("family")
    world_state_type = readiness.get("world_state_type")
    if not isinstance(family, str) or not family.strip():
        return None
    if not isinstance(world_state_type, str) or not world_state_type.strip():
        return None
    # raw payload は残さず、manifest 由来の期待値と対応成否だけを inspection に載せる。
    return {
        "family": family.strip(),
        "world_state_type": world_state_type.strip(),
    }


def _capability_readiness_key_list(readiness: dict[str, Any], key: str) -> list[str]:
    raw_keys = readiness.get(key)
    if not isinstance(raw_keys, list):
        return []
    return [item.strip() for item in raw_keys if isinstance(item, str) and item.strip()]


def _has_capability_readiness_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True
