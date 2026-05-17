from __future__ import annotations

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
        "decision_readiness": {
            "family": "visual_observation",
            "world_state_type": "visual_context",
            "input_keys": ["source", "mode"],
            "result_summary_keys": ["visual_summary_text"],
        },
        "inspection_fields": [
            "capability_id",
            "target_client_id",
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
        "decision_readiness": {
            "family": "external_status",
            "world_state_type": "external_service",
            "input_keys": ["service"],
            "result_summary_keys": ["status_text"],
        },
        "inspection_fields": [
            "capability_id",
            "target_client_id",
            "data_source",
            "unconnected_reason",
            "service",
            "status_text",
            "body_state_summary",
            "device_state_summary",
            "schedule_summary",
            "error",
        ],
    },
    "schedule.status": {
        "id": "schedule.status",
        "version": "1",
        "kind": "external_service",
        "decision_description": "近い予定やカレンダー状態を確認する",
        "when_to_use": [
            "判断に近い予定やカレンダー状態が必要",
            "このあと、今日、近日の予定を短く確認したい",
        ],
        "do_not_use_when": [
            "現在の判断に予定情報が不要",
            "すでに十分新しい予定要約が手元にある",
        ],
        "required_permissions": [],
        "input_schema": {
            "type": "object",
            "properties": {
                "range": {"type": "string"},
            },
            "required": ["range"],
            "additionalProperties": False,
        },
        "result_schema": {
            "type": "object",
            "properties": {
                "schedule_summary": {"type": "string"},
                "schedule_slots": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "slot_key": {"type": "string"},
                            "summary_text": {"type": "string"},
                            "not_before": {"type": "string"},
                            "expires_at": {"type": "string"},
                        },
                        "required": ["slot_key", "summary_text"],
                        "additionalProperties": False,
                    },
                },
                "client_context": {
                    "type": ["object", "null"],
                },
                "error": {
                    "type": ["string", "null"],
                },
            },
            "required": ["schedule_summary", "schedule_slots"],
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
            "result_context_hook": "schedule_status",
            "followup_hint_hook": "schedule_status",
            "success_cooldown_seconds": 60,
            "error_cooldown_seconds": 60,
            "unavailable_seconds_on_dispatch_failure": 60,
            "unavailable_seconds_on_timeout": 60,
        },
        "decision_readiness": {
            "family": "schedule_status",
            "world_state_type": "schedule",
            "input_keys": ["range"],
            "result_summary_keys": ["schedule_summary"],
            "result_item_keys": ["schedule_slots"],
        },
        "inspection_fields": [
            "capability_id",
            "target_client_id",
            "data_source",
            "unconnected_reason",
            "range",
            "schedule_summary",
            "schedule_slots",
            "body_state_summary",
            "device_state_summary",
            "error",
        ],
    },
    "device.status": {
        "id": "device.status",
        "version": "1",
        "kind": "observation",
        "decision_description": "端末や接続状態を確認する",
        "when_to_use": [
            "判断に端末や接続の現在状態が必要",
            "ユーザーが端末、接続、電源、バッテリー状態を短く確認したい",
        ],
        "do_not_use_when": [
            "現在の判断に端末状態が不要",
            "すでに十分新しい端末状態要約が手元にある",
        ],
        "required_permissions": [],
        "input_schema": {
            "type": "object",
            "properties": {
                "scope": {"type": "string"},
            },
            "required": ["scope"],
            "additionalProperties": False,
        },
        "result_schema": {
            "type": "object",
            "properties": {
                "device_state_summary": {"type": "string"},
                "client_context": {
                    "type": ["object", "null"],
                },
                "error": {
                    "type": ["string", "null"],
                },
            },
            "required": ["device_state_summary"],
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
            "result_context_hook": "device_status",
            "followup_hint_hook": "device_status",
            "success_cooldown_seconds": 60,
            "error_cooldown_seconds": 60,
            "unavailable_seconds_on_dispatch_failure": 60,
            "unavailable_seconds_on_timeout": 60,
        },
        "decision_readiness": {
            "family": "device_status",
            "world_state_type": "device",
            "input_keys": ["scope"],
            "result_summary_keys": ["device_state_summary"],
        },
        "inspection_fields": [
            "capability_id",
            "target_client_id",
            "data_source",
            "unconnected_reason",
            "scope",
            "device_state_summary",
            "body_state_summary",
            "schedule_summary",
            "error",
        ],
    },
    "body.status": {
        "id": "body.status",
        "version": "1",
        "kind": "observation",
        "decision_description": "身体や体調の現在状態を確認する",
        "when_to_use": [
            "判断に身体や体調の現在状態が必要",
            "ユーザーが疲労、眠気、姿勢、体調を短く確認したい",
        ],
        "do_not_use_when": [
            "現在の判断に身体状態が不要",
            "すでに十分新しい身体状態要約が手元にある",
        ],
        "required_permissions": [],
        "input_schema": {
            "type": "object",
            "properties": {
                "scope": {"type": "string"},
            },
            "required": ["scope"],
            "additionalProperties": False,
        },
        "result_schema": {
            "type": "object",
            "properties": {
                "body_state_summary": {"type": "string"},
                "client_context": {
                    "type": ["object", "null"],
                },
                "error": {
                    "type": ["string", "null"],
                },
            },
            "required": ["body_state_summary"],
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
            "result_context_hook": "body_status",
            "followup_hint_hook": "body_status",
            "success_cooldown_seconds": 60,
            "error_cooldown_seconds": 60,
            "unavailable_seconds_on_dispatch_failure": 60,
            "unavailable_seconds_on_timeout": 60,
        },
        "decision_readiness": {
            "family": "body_status",
            "world_state_type": "body",
            "input_keys": ["scope"],
            "result_summary_keys": ["body_state_summary"],
        },
        "inspection_fields": [
            "capability_id",
            "target_client_id",
            "data_source",
            "unconnected_reason",
            "scope",
            "body_state_summary",
            "device_state_summary",
            "schedule_summary",
            "environment_summary",
            "error",
        ],
    },
    "environment.status": {
        "id": "environment.status",
        "version": "1",
        "kind": "observation",
        "decision_description": "周囲や作業環境の現在状態を確認する",
        "when_to_use": [
            "判断に周囲や作業環境の現在状態が必要",
            "ユーザーが部屋、騒音、明るさ、作業環境を短く確認したい",
        ],
        "do_not_use_when": [
            "現在の判断に周囲環境が不要",
            "すでに十分新しい環境状態要約が手元にある",
        ],
        "required_permissions": [],
        "input_schema": {
            "type": "object",
            "properties": {
                "scope": {"type": "string"},
            },
            "required": ["scope"],
            "additionalProperties": False,
        },
        "result_schema": {
            "type": "object",
            "properties": {
                "environment_summary": {"type": "string"},
                "client_context": {
                    "type": ["object", "null"],
                },
                "error": {
                    "type": ["string", "null"],
                },
            },
            "required": ["environment_summary"],
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
            "result_context_hook": "environment_status",
            "followup_hint_hook": "environment_status",
            "success_cooldown_seconds": 60,
            "error_cooldown_seconds": 60,
            "unavailable_seconds_on_dispatch_failure": 60,
            "unavailable_seconds_on_timeout": 60,
        },
        "decision_readiness": {
            "family": "environment_status",
            "world_state_type": "environment",
            "input_keys": ["scope"],
            "result_summary_keys": ["environment_summary"],
        },
        "inspection_fields": [
            "capability_id",
            "target_client_id",
            "data_source",
            "unconnected_reason",
            "scope",
            "environment_summary",
            "body_state_summary",
            "device_state_summary",
            "schedule_summary",
            "error",
        ],
    },
    "location.status": {
        "id": "location.status",
        "version": "1",
        "kind": "observation",
        "decision_description": "場所や移動に関わる現在状態を確認する",
        "when_to_use": [
            "判断に現在場所や移動状態が必要",
            "ユーザーが居場所、移動中か、作業場所を短く確認したい",
        ],
        "do_not_use_when": [
            "現在の判断に場所状態が不要",
            "すでに十分新しい場所状態要約が手元にある",
        ],
        "required_permissions": [],
        "input_schema": {
            "type": "object",
            "properties": {
                "scope": {"type": "string"},
            },
            "required": ["scope"],
            "additionalProperties": False,
        },
        "result_schema": {
            "type": "object",
            "properties": {
                "location_summary": {"type": "string"},
                "client_context": {
                    "type": ["object", "null"],
                },
                "error": {
                    "type": ["string", "null"],
                },
            },
            "required": ["location_summary"],
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
            "result_context_hook": "location_status",
            "followup_hint_hook": "location_status",
            "success_cooldown_seconds": 60,
            "error_cooldown_seconds": 60,
            "unavailable_seconds_on_dispatch_failure": 60,
            "unavailable_seconds_on_timeout": 60,
        },
        "decision_readiness": {
            "family": "location_status",
            "world_state_type": "location",
            "input_keys": ["scope"],
            "result_summary_keys": ["location_summary"],
        },
        "inspection_fields": [
            "capability_id",
            "target_client_id",
            "data_source",
            "unconnected_reason",
            "scope",
            "location_summary",
            "environment_summary",
            "body_state_summary",
            "device_state_summary",
            "schedule_summary",
            "error",
        ],
    },
    "social.status": {
        "id": "social.status",
        "version": "1",
        "kind": "observation",
        "decision_description": "対人文脈や連絡状況の現在状態を確認する",
        "when_to_use": [
            "判断に現在の対人文脈や連絡状況が必要",
            "ユーザーが会話、連絡、通知、会議文脈を短く確認したい",
        ],
        "do_not_use_when": [
            "現在の判断に対人文脈が不要",
            "すでに十分新しい対人文脈要約が手元にある",
        ],
        "required_permissions": [],
        "input_schema": {
            "type": "object",
            "properties": {
                "scope": {"type": "string"},
            },
            "required": ["scope"],
            "additionalProperties": False,
        },
        "result_schema": {
            "type": "object",
            "properties": {
                "social_context_summary": {"type": "string"},
                "client_context": {
                    "type": ["object", "null"],
                },
                "error": {
                    "type": ["string", "null"],
                },
            },
            "required": ["social_context_summary"],
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
            "result_context_hook": "social_status",
            "followup_hint_hook": "social_status",
            "success_cooldown_seconds": 60,
            "error_cooldown_seconds": 60,
            "unavailable_seconds_on_dispatch_failure": 60,
            "unavailable_seconds_on_timeout": 60,
        },
        "decision_readiness": {
            "family": "social_status",
            "world_state_type": "social_context",
            "input_keys": ["scope"],
            "result_summary_keys": ["social_context_summary"],
        },
        "inspection_fields": [
            "capability_id",
            "target_client_id",
            "data_source",
            "unconnected_reason",
            "scope",
            "social_context_summary",
            "environment_summary",
            "location_summary",
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


# decision view / inspection と fresh world_state 再利用の対応を manifest に集約する。
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


def capability_world_state_type(capability_id: str) -> str | None:
    manifest = CAPABILITY_MANIFESTS.get(capability_id)
    if not isinstance(manifest, dict):
        return None
    readiness = manifest.get("decision_readiness")
    if not isinstance(readiness, dict):
        return None
    world_state_type = readiness.get("world_state_type")
    if not isinstance(world_state_type, str) or not world_state_type.strip():
        return None
    return world_state_type.strip()


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


def capability_readiness_world_state_digest(
    capability_id: str,
    foreground_world_state_type: Any,
) -> dict[str, Any] | None:
    digest = _capability_readiness_digest_base(capability_id)
    if digest is None:
        return None
    observed_type = foreground_world_state_type.strip() if isinstance(foreground_world_state_type, str) else None
    digest.update(
        {
            "foreground_world_state_type": observed_type,
            "world_state_type_matched": (
                isinstance(observed_type, str)
                and bool(observed_type)
                and observed_type == digest.get("world_state_type")
            ),
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
