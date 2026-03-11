"""Build minimal cognition input from runtime state."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from otomekairo.gateway.camera_controller import CameraCandidate, CameraPresetCandidate
from otomekairo.gateway.cognition_client import CognitionClient
from otomekairo.schema.runtime_types import CognitionStateSnapshot, PendingInputRecord
from otomekairo.usecase.completion_settings import build_completion_settings
from otomekairo.usecase.observation_normalization import (
    normalize_observation_kind,
    normalize_observation_source,
    normalize_trigger_reason,
)
from otomekairo.usecase.persona_prompt_projection import build_persona_prompt_projection
from otomekairo.usecase.persona_projection import build_attention_snapshot, build_skill_candidates
from otomekairo.usecase.retrieval_flow import build_retrieval_artifacts


# Block: Context budget constants
CONTEXT_OUTPUT_CONTRACT_WEIGHT = 15
CONTEXT_MEMORY_WEIGHT = 35
CONTEXT_SELF_WEIGHT = 40
CONTEXT_BEHAVIOR_WEIGHT = 22
CONTEXT_SITUATION_WEIGHT = 38
ESTIMATED_TOKEN_CHAR_RATIO = 4
MEMORY_PROMPT_TEXT_LIMIT = 120
MEMORY_TRIM_SLOT_ORDER = (
    "recent_event_window",
    "reflection_items",
    "episodic_items",
    "semantic_items",
    "affective_items",
    "relationship_items",
    "working_memory_items",
)


# Block: Build result
@dataclass(frozen=True, slots=True)
class BuiltCognitionInput:
    cognition_input: dict[str, Any]
    retrieval_run: dict[str, Any]


# Block: Public builder
def build_cognition_input(
    *,
    pending_input: PendingInputRecord,
    cycle_id: str,
    resolved_at: int,
    state_snapshot: CognitionStateSnapshot,
    cognition_client: CognitionClient,
    camera_candidates: list[CameraCandidate],
    camera_available: bool,
) -> BuiltCognitionInput:
    input_kind = str(pending_input.payload["input_kind"])
    if input_kind not in {"chat_message", "microphone_message", "camera_observation", "network_result", "idle_tick"}:
        raise ValueError(
            "cognition_input is only supported for chat_message, microphone_message, camera_observation, network_result, and idle_tick"
        )
    camera_candidates_payload = _build_camera_candidates(camera_candidates)
    behavior_settings = _build_behavior_settings(state_snapshot.effective_settings)
    preference_selection_state = _build_preference_selection_state(
        preference_items=state_snapshot.stable_preference_items,
    )
    selection_profile = _build_selection_profile(
        state_snapshot=state_snapshot,
        behavior_settings=behavior_settings,
        preference_selection_state=preference_selection_state,
    )
    current_observation = _build_current_observation(
        pending_input=pending_input,
        resolved_at=resolved_at,
    )
    completion_settings = build_completion_settings(state_snapshot.effective_settings)
    retrieval_artifacts = build_retrieval_artifacts(
        cycle_id=cycle_id,
        memory_snapshot=state_snapshot.memory_snapshot,
        retrieval_profile=state_snapshot.retrieval_profile,
        current_observation=current_observation,
        task_snapshot=state_snapshot.task_snapshot,
        resolved_at=resolved_at,
        completion_settings=completion_settings,
        cognition_client=cognition_client,
    )
    self_snapshot = {
        "personality": state_snapshot.self_state["personality"],
        "current_emotion": state_snapshot.self_state["current_emotion"],
        "long_term_goals": state_snapshot.self_state["long_term_goals"],
        "relationship_overview": state_snapshot.self_state["relationship_overview"],
        "invariants": state_snapshot.self_state["invariants"],
    }
    latest_persona_update = state_snapshot.self_state.get("latest_persona_update")
    if isinstance(latest_persona_update, dict):
        self_snapshot["last_persona_update"] = latest_persona_update
    cycle_meta = {
        "cycle_id": cycle_id,
        "trigger_reason": normalize_trigger_reason(
            source=pending_input.source,
            payload=pending_input.payload,
        ),
        "input_id": pending_input.input_id,
        "input_kind": input_kind,
    }
    time_context = _build_time_context(resolved_at)
    task_snapshot = _build_task_snapshot(
        task_snapshot=state_snapshot.task_snapshot,
        resolved_at=resolved_at,
    )
    stable_self_state = _build_stable_self_state(
        self_snapshot=self_snapshot,
        task_snapshot=task_snapshot,
    )
    confirmed_preferences = dict(preference_selection_state["confirmed_preferences"])
    long_mood_state = _build_long_mood_state_context(
        long_mood_item=state_snapshot.stable_long_mood_item,
    )
    attention_snapshot = build_attention_snapshot(
        current_observation=current_observation,
        selection_profile=selection_profile,
        task_snapshot=state_snapshot.task_snapshot,
        resolved_at=resolved_at,
    )
    skill_candidates = build_skill_candidates(
        current_observation=current_observation,
        selection_profile=selection_profile,
        behavior_settings=behavior_settings,
        body_state=state_snapshot.body_state,
        task_snapshot=state_snapshot.task_snapshot,
    )
    policy_snapshot = {
        "system_policy": _build_system_policy(),
        "runtime_policy": _build_runtime_policy(
            effective_settings=state_snapshot.effective_settings,
            camera_candidates=camera_candidates_payload,
            camera_available=camera_available,
        ),
        "input_evaluation": _build_input_evaluation(current_observation=current_observation),
    }
    trimmed_memory_bundle, trimmed_memory_item_refs = _trim_memory_bundle_for_context_budget(
        effective_settings=state_snapshot.effective_settings,
        cycle_meta=cycle_meta,
        time_context=time_context,
        self_snapshot=self_snapshot,
        behavior_settings=behavior_settings,
        selection_profile=selection_profile,
        body_snapshot=state_snapshot.body_state,
        world_snapshot=state_snapshot.world_state,
        drive_snapshot=state_snapshot.drive_state,
        task_snapshot=task_snapshot,
        attention_snapshot=attention_snapshot,
        policy_snapshot=policy_snapshot,
        skill_candidates=skill_candidates,
        current_observation=current_observation,
        camera_candidates=camera_candidates_payload,
        memory_bundle=retrieval_artifacts.memory_bundle,
    )
    retrieval_selected_json = _build_retrieval_selected_json(
        memory_bundle=trimmed_memory_bundle,
        source_selected_json=retrieval_artifacts.selected_json,
        trimmed_memory_item_refs=trimmed_memory_item_refs,
    )
    recent_dialog = _build_recent_dialog(
        recent_event_window=trimmed_memory_bundle["recent_event_window"],
    )
    selected_memory_pack = _build_selected_memory_pack(
        memory_bundle=trimmed_memory_bundle,
    )
    action_selection_context = _build_action_selection_context(
        current_observation=current_observation,
        memory_bundle=trimmed_memory_bundle,
        recent_dialog=recent_dialog,
        selected_memory_pack=selected_memory_pack,
        confirmed_preferences=confirmed_preferences,
        long_mood_state=long_mood_state,
    )
    retrieval_context = {
        "plan": retrieval_artifacts.retrieval_plan,
        "selected": retrieval_selected_json,
    }
    context_budget = _build_context_budget(
        effective_settings=state_snapshot.effective_settings,
        cycle_meta=cycle_meta,
        time_context=time_context,
        self_snapshot=self_snapshot,
        stable_self_state=stable_self_state,
        confirmed_preferences=confirmed_preferences,
        long_mood_state=long_mood_state,
        behavior_settings=behavior_settings,
        selection_profile=selection_profile,
        body_snapshot=state_snapshot.body_state,
        world_snapshot=state_snapshot.world_state,
        drive_snapshot=state_snapshot.drive_state,
        task_snapshot=task_snapshot,
        attention_snapshot=attention_snapshot,
        retrieval_context=retrieval_context,
        policy_snapshot=policy_snapshot,
        skill_candidates=skill_candidates,
        current_observation=current_observation,
        camera_candidates=camera_candidates_payload,
        recent_dialog=recent_dialog,
        selected_memory_pack=selected_memory_pack,
        trimmed_memory_item_refs=trimmed_memory_item_refs,
    )
    reply_render_input = _build_reply_render_input(
        current_observation=current_observation,
        time_context=time_context,
        attention_snapshot=attention_snapshot,
        retrieval_context=retrieval_context,
        stable_self_state=stable_self_state,
        confirmed_preferences=confirmed_preferences,
        long_mood_state=long_mood_state,
        recent_dialog=recent_dialog,
        selected_memory_pack=selected_memory_pack,
        selection_profile=selection_profile,
    )
    return BuiltCognitionInput(
        cognition_input={
            "cycle_meta": cycle_meta,
            "time_context": time_context,
            "self_snapshot": self_snapshot,
            "stable_self_state": stable_self_state,
            "confirmed_preferences": confirmed_preferences,
            "long_mood_state": long_mood_state,
            "behavior_settings": behavior_settings,
            "selection_profile": selection_profile,
            "body_snapshot": state_snapshot.body_state,
            "world_snapshot": state_snapshot.world_state,
            "drive_snapshot": state_snapshot.drive_state,
            "task_snapshot": task_snapshot,
            "attention_snapshot": attention_snapshot,
            "memory_bundle": trimmed_memory_bundle,
            "recent_dialog": recent_dialog,
            "selected_memory_pack": selected_memory_pack,
            "action_selection_context": action_selection_context,
            "retrieval_context": retrieval_context,
            "policy_snapshot": policy_snapshot,
            "camera_candidates": camera_candidates_payload,
            "skill_candidates": skill_candidates,
            "current_observation": current_observation,
            "context_budget": context_budget,
            "reply_render_input": reply_render_input,
        },
        retrieval_run={
            "plan_json": retrieval_artifacts.retrieval_plan,
            "candidates_json": retrieval_artifacts.candidates_json,
            "selected_json": retrieval_selected_json,
        },
    )


# Block: Current observation builder
def _build_current_observation(
    *,
    pending_input: PendingInputRecord,
    resolved_at: int,
) -> dict[str, Any]:
    input_kind = str(pending_input.payload["input_kind"])
    base_observation = {
        "source": pending_input.source,
        "kind": normalize_observation_kind(payload=pending_input.payload),
        "trigger_reason": normalize_trigger_reason(
            source=pending_input.source,
            payload=pending_input.payload,
        ),
        "channel": pending_input.channel,
        "input_kind": input_kind,
        "captured_at": pending_input.created_at,
        "captured_at_utc_text": _utc_text(pending_input.created_at),
        "captured_at_local_text": _local_text(pending_input.created_at),
        "relative_time_text": _relative_time_text(resolved_at, pending_input.created_at),
    }
    base_observation["source"] = normalize_observation_source(
        source=str(base_observation["source"]),
        payload=pending_input.payload,
    )
    if input_kind == "chat_message":
        text = _validated_message_text(
            pending_input.payload.get("text"),
            input_kind="chat_message",
        )
        attachments = _validated_camera_attachments(
            pending_input.payload.get("attachments"),
            input_kind="chat_message",
        )
        return {
            **base_observation,
            "observation_text": _chat_observation_text(text=text, attachments=attachments),
            "attachment_count": len(attachments),
            "attachment_summary_text": _camera_attachment_summary_text(attachments),
            "attachments": attachments,
            **({"text": text} if text is not None else {}),
        }
    if input_kind == "microphone_message":
        text = _validated_message_text(
            pending_input.payload.get("text"),
            input_kind="microphone_message",
        )
        if text is None:
            raise ValueError("microphone_message.text is required")
        stt_provider = pending_input.payload.get("stt_provider")
        stt_language = pending_input.payload.get("stt_language")
        if not isinstance(stt_provider, str) or not stt_provider:
            raise ValueError("microphone_message.stt_provider must be non-empty string")
        if not isinstance(stt_language, str) or not stt_language:
            raise ValueError("microphone_message.stt_language must be non-empty string")
        return {
            **base_observation,
            "observation_text": text,
            "text": text,
            "stt_provider": stt_provider,
            "stt_language": stt_language,
        }
    if input_kind == "camera_observation":
        attachments = _validated_camera_attachments(
            pending_input.payload.get("attachments"),
            input_kind="camera_observation",
        )
        if not attachments:
            raise ValueError("camera_observation requires attachments")
        trigger_reason = pending_input.payload.get("trigger_reason")
        return {
            **base_observation,
            "observation_text": _camera_observation_text(
                attachments,
                trigger_reason=trigger_reason,
            ),
            "attachment_count": len(attachments),
            "attachment_summary_text": _camera_attachment_summary_text(attachments),
            "attachments": attachments,
        }
    if input_kind == "network_result":
        summary_text = pending_input.payload.get("summary_text")
        query = pending_input.payload.get("query")
        source_task_id = pending_input.payload.get("source_task_id")
        if not isinstance(summary_text, str) or not summary_text:
            raise ValueError("network_result.summary_text must be non-empty string")
        if not isinstance(query, str) or not query:
            raise ValueError("network_result.query must be non-empty string")
        if not isinstance(source_task_id, str) or not source_task_id:
            raise ValueError("network_result.source_task_id must be non-empty string")
        return {
            **base_observation,
            "observation_text": summary_text,
            "query": query,
            "summary_text": summary_text,
            "source_task_id": source_task_id,
        }
    if input_kind == "idle_tick":
        idle_duration_ms = pending_input.payload.get("idle_duration_ms")
        if isinstance(idle_duration_ms, bool) or not isinstance(idle_duration_ms, int):
            raise ValueError("idle_tick.idle_duration_ms must be integer")
        if idle_duration_ms <= 0:
            raise ValueError("idle_tick.idle_duration_ms must be positive")
        return {
            **base_observation,
            "observation_text": _idle_tick_observation_text(idle_duration_ms),
            "idle_duration_ms": idle_duration_ms,
        }
    raise ValueError("unsupported current_observation input_kind")


# Block: Message text validation
def _validated_message_text(raw_text: Any, *, input_kind: str) -> str | None:
    if raw_text is None:
        return None
    if not isinstance(raw_text, str):
        raise ValueError(f"{input_kind}.text must be string when present")
    if not raw_text:
        raise ValueError(f"{input_kind}.text must not be empty string")
    return raw_text


# Block: Camera attachment helpers
def _validated_camera_attachments(
    raw_attachments: Any,
    *,
    input_kind: str,
) -> list[dict[str, Any]]:
    if raw_attachments is None:
        return []
    if not isinstance(raw_attachments, list):
        raise ValueError(f"{input_kind}.attachments must be a list")
    attachments: list[dict[str, Any]] = []
    for attachment in raw_attachments:
        if not isinstance(attachment, dict):
            raise ValueError(f"{input_kind}.attachments must contain only objects")
        attachment_kind = attachment.get("attachment_kind")
        media_kind = attachment.get("media_kind")
        camera_connection_id = attachment.get("camera_connection_id")
        camera_display_name = attachment.get("camera_display_name")
        capture_id = attachment.get("capture_id")
        mime_type = attachment.get("mime_type")
        storage_path = attachment.get("storage_path")
        content_url = attachment.get("content_url")
        captured_at = attachment.get("captured_at")
        if attachment_kind != "camera_still_image":
            raise ValueError(f"{input_kind}.attachments.attachment_kind is invalid")
        if media_kind != "image":
            raise ValueError(f"{input_kind}.attachments.media_kind is invalid")
        if not isinstance(capture_id, str) or not capture_id:
            raise ValueError(f"{input_kind}.attachments.capture_id must be non-empty string")
        if not isinstance(mime_type, str) or not mime_type:
            raise ValueError(f"{input_kind}.attachments.mime_type must be non-empty string")
        if not isinstance(storage_path, str) or not storage_path:
            raise ValueError(f"{input_kind}.attachments.storage_path must be non-empty string")
        if not isinstance(content_url, str) or not content_url:
            raise ValueError(f"{input_kind}.attachments.content_url must be non-empty string")
        if isinstance(captured_at, bool) or not isinstance(captured_at, int):
            raise ValueError(f"{input_kind}.attachments.captured_at must be integer")
        validated_attachment = {
            "attachment_kind": attachment_kind,
            "media_kind": media_kind,
            "capture_id": capture_id,
            "mime_type": mime_type,
            "storage_path": storage_path,
            "content_url": content_url,
            "captured_at": captured_at,
        }
        if camera_connection_id is not None:
            if not isinstance(camera_connection_id, str) or not camera_connection_id:
                raise ValueError(f"{input_kind}.attachments.camera_connection_id must be non-empty string")
            validated_attachment["camera_connection_id"] = camera_connection_id
        if camera_display_name is not None:
            if not isinstance(camera_display_name, str) or not camera_display_name:
                raise ValueError(f"{input_kind}.attachments.camera_display_name must be non-empty string")
            validated_attachment["camera_display_name"] = camera_display_name
        attachments.append(validated_attachment)
    return attachments


# Block: Chat observation text
def _chat_observation_text(*, text: str | None, attachments: list[dict[str, Any]]) -> str:
    if text is not None and attachments:
        return f"{text}\n（{_camera_attachment_summary_text(attachments)}付き）"
    if text is not None:
        return text
    if attachments:
        return f"{_camera_attachment_summary_text(attachments)}が添付された入力"
    raise ValueError("chat_message requires text or attachments")


# Block: Camera observation text
def _camera_observation_text(
    attachments: list[dict[str, Any]],
    *,
    trigger_reason: Any,
) -> str:
    attachment_summary = _camera_attachment_summary_text(attachments)
    if trigger_reason == "post_action_followup":
        return f"{attachment_summary}を追跡観測した"
    return f"{attachment_summary}を自発観測した"


# Block: Camera attachment summary
def _camera_attachment_summary_text(attachments: list[dict[str, Any]]) -> str:
    if not attachments:
        return "添付なし"
    camera_names = [
        str(attachment["camera_display_name"])
        for attachment in attachments
        if isinstance(attachment.get("camera_display_name"), str) and attachment["camera_display_name"]
    ]
    if not camera_names:
        return f"カメラ画像 {len(attachments)} 枚"
    unique_names = list(dict.fromkeys(camera_names))
    joined_names = " / ".join(unique_names[:3])
    return f"{joined_names} のカメラ画像 {len(attachments)} 枚"


# Block: Camera candidate builder
def _build_camera_candidates(camera_candidates: list[CameraCandidate]) -> list[dict[str, Any]]:
    built_candidates: list[dict[str, Any]] = []
    for camera_candidate in camera_candidates:
        if not isinstance(camera_candidate, CameraCandidate):
            raise ValueError("camera_candidates must contain only CameraCandidate entries")
        built_candidates.append(
            {
                "camera_connection_id": camera_candidate.camera_connection_id,
                "display_name": camera_candidate.display_name,
                "can_look": bool(camera_candidate.can_look),
                "can_capture": bool(camera_candidate.can_capture),
                "presets": _build_camera_preset_candidates(camera_candidate.presets),
            }
        )
    return built_candidates


# Block: Camera preset candidate builder
def _build_camera_preset_candidates(
    preset_candidates: tuple[CameraPresetCandidate, ...],
) -> list[dict[str, Any]]:
    built_presets: list[dict[str, Any]] = []
    for preset_candidate in preset_candidates:
        if not isinstance(preset_candidate, CameraPresetCandidate):
            raise ValueError("camera_candidates.presets must contain only CameraPresetCandidate entries")
        built_presets.append(
            {
                "preset_id": preset_candidate.preset_id,
                "preset_name": preset_candidate.preset_name,
            }
        )
    return built_presets


# Block: Idle tick observation text
def _idle_tick_observation_text(idle_duration_ms: int) -> str:
    return f"{idle_duration_ms}ms の idle_tick が到来した"


# Block: Task snapshot builder
def _build_task_snapshot(
    *,
    task_snapshot: dict[str, Any],
    resolved_at: int,
) -> dict[str, Any]:
    return {
        "active_tasks": [
            _task_snapshot_entry_for_cognition(task_entry, resolved_at=resolved_at)
            for task_entry in task_snapshot["active_tasks"]
        ],
        "waiting_external_tasks": [
            _task_snapshot_entry_for_cognition(task_entry, resolved_at=resolved_at)
            for task_entry in task_snapshot["waiting_external_tasks"]
        ],
    }


def _task_snapshot_entry_for_cognition(
    task_entry: dict[str, Any],
    *,
    resolved_at: int,
) -> dict[str, Any]:
    updated_at = int(task_entry["updated_at"])
    created_at = int(task_entry["created_at"])
    return {
        **task_entry,
        "created_at_utc_text": _utc_text(created_at),
        "created_at_local_text": _local_text(created_at),
        "updated_at_utc_text": _utc_text(updated_at),
        "updated_at_local_text": _local_text(updated_at),
        "relative_time_text": _relative_time_text(resolved_at, updated_at),
    }


# Block: Selection profile
def _build_selection_profile(
    state_snapshot: CognitionStateSnapshot,
    *,
    behavior_settings: dict[str, str],
    preference_selection_state: dict[str, Any],
) -> dict[str, Any]:
    personality = state_snapshot.self_state["personality"]
    current_emotion = state_snapshot.self_state["current_emotion"]
    relationship_overview = state_snapshot.self_state["relationship_overview"]
    priority_effects = state_snapshot.drive_state["priority_effects"]
    interaction_style = dict(personality["preferred_interaction_style"])
    interaction_style["speech_tone"] = behavior_settings["speech_style"]
    interaction_style["response_pace"] = behavior_settings["response_pace"]
    return {
        "trait_values": dict(personality["trait_values"]),
        "interaction_style": interaction_style,
        "relationship_priorities": _build_relationship_priorities(relationship_overview),
        "learned_preferences": list(preference_selection_state["learned_preferences"]),
        "learned_aversions": list(preference_selection_state["learned_aversions"]),
        "revoked_preferences": list(preference_selection_state["revoked_preferences"]),
        "habit_biases": dict(personality["habit_biases"]),
        "emotion_bias": dict(current_emotion["active_biases"]),
        "drive_bias": {
            "task_progress_bias": _normalized_signed_number(
                priority_effects["task_progress_bias"],
                field_name="drive_state.priority_effects.task_progress_bias",
            ),
            "exploration_bias": _normalized_signed_number(
                priority_effects["exploration_bias"],
                field_name="drive_state.priority_effects.exploration_bias",
            ),
            "maintenance_bias": _normalized_signed_number(
                priority_effects["maintenance_bias"],
                field_name="drive_state.priority_effects.maintenance_bias",
            ),
            "social_bias": _normalized_signed_number(
                priority_effects["social_bias"],
                field_name="drive_state.priority_effects.social_bias",
            ),
        },
}


# Block: Preference selection state
def _build_preference_selection_state(*, preference_items: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(preference_items, list):
        raise RuntimeError("state_snapshot.stable_preference_items must be a list")
    learned_preferences: list[dict[str, Any]] = []
    learned_aversions: list[dict[str, Any]] = []
    revoked_preferences: list[dict[str, Any]] = []
    confirmed_likes: list[dict[str, Any]] = []
    confirmed_dislikes: list[dict[str, Any]] = []
    seen_preference_keys: set[tuple[str, str, str]] = set()
    for preference_item in preference_items:
        if not isinstance(preference_item, dict):
            continue
        if str(preference_item.get("memory_kind")) != "preference":
            continue
        payload = preference_item.get("payload")
        if not isinstance(payload, dict):
            raise RuntimeError("preference payload must be an object")
        target_entity_ref = payload.get("target_entity_ref")
        if not isinstance(target_entity_ref, dict):
            raise RuntimeError("preference payload.target_entity_ref must be an object")
        target_key = target_entity_ref.get("target_key")
        if not isinstance(target_key, str) or not target_key:
            raise RuntimeError("preference payload.target_entity_ref.target_key must be non-empty string")
        domain = str(payload.get("domain", ""))
        polarity = str(payload.get("polarity", ""))
        status = str(payload.get("status", ""))
        confidence = float(preference_item.get("confidence", 0.0))
        preference_key = (domain, target_key, polarity)
        if preference_key in seen_preference_keys:
            continue
        seen_preference_keys.add(preference_key)
        evidence_event_ids = payload.get("evidence_event_ids")
        evidence_count = 1
        if isinstance(evidence_event_ids, list):
            evidence_count = max(
                1,
                len([event_id for event_id in evidence_event_ids if isinstance(event_id, str) and event_id]),
            )
        selection_entry = {
            "domain": domain,
            "target_key": target_key,
            "weight": round(confidence, 4),
            "evidence_count": evidence_count,
        }
        if status == "confirmed":
            projected_entry = {
                "domain": domain,
                "target_key": target_key,
                "confidence": confidence,
            }
            if polarity == "like":
                confirmed_likes.append(projected_entry)
                learned_preferences.append(selection_entry)
                continue
            if polarity == "dislike":
                confirmed_dislikes.append(projected_entry)
                learned_aversions.append(selection_entry)
                continue
            raise RuntimeError("preference payload.polarity must be like or dislike")
        if status == "revoked":
            if polarity not in {"like", "dislike"}:
                raise RuntimeError("preference payload.polarity must be like or dislike")
            revoked_preferences.append(
                {
                    **selection_entry,
                    "polarity": polarity,
                }
            )
            continue
        if status == "candidate":
            continue
        raise RuntimeError("preference payload.status must be candidate, confirmed, or revoked")
    learned_preferences.sort(key=lambda item: float(item["weight"]), reverse=True)
    learned_aversions.sort(key=lambda item: float(item["weight"]), reverse=True)
    revoked_preferences.sort(key=lambda item: float(item["weight"]), reverse=True)
    confirmed_likes.sort(key=lambda item: float(item["confidence"]), reverse=True)
    confirmed_dislikes.sort(key=lambda item: float(item["confidence"]), reverse=True)
    return {
        "learned_preferences": learned_preferences[:8],
        "learned_aversions": learned_aversions[:8],
        "revoked_preferences": revoked_preferences[:8],
        "confirmed_preferences": {
            "likes": confirmed_likes[:6],
            "dislikes": confirmed_dislikes[:6],
        },
    }


# Block: Policy snapshot builders
def _build_system_policy() -> dict[str, Any]:
    return {
        "respect_invariants": True,
        "allow_direct_state_write": False,
    }


def _build_runtime_policy(
    *,
    effective_settings: dict[str, Any],
    camera_candidates: list[dict[str, Any]],
    camera_available: bool,
) -> dict[str, Any]:
    return {
        "camera_enabled": bool(effective_settings["sensors.camera.enabled"]),
        "camera_available": bool(camera_available),
        "camera_candidate_count": len(camera_candidates),
        "microphone_enabled": bool(effective_settings["sensors.microphone.enabled"]),
    }


def _build_input_evaluation(*, current_observation: dict[str, Any]) -> dict[str, Any]:
    input_kind = str(current_observation["input_kind"])
    observation_kind = str(current_observation["kind"])
    if input_kind in {"chat_message", "microphone_message"}:
        return {
            "input_role": "instruction" if observation_kind == "instruction" else "dialogue",
            "attention_priority": "high",
            "factuality": "unverified_user_report",
            "should_reply_in_channel": True,
            "can_override_persona": False,
            "must_preserve_invariants": True,
        }
    if input_kind == "network_result":
        return {
            "input_role": "task_result",
            "attention_priority": "high",
            "factuality": "external_tool_result",
            "should_reply_in_channel": True,
            "can_override_persona": False,
            "must_preserve_invariants": True,
        }
    if input_kind == "camera_observation":
        trigger_reason = str(current_observation["trigger_reason"])
        return {
            "input_role": "followup_observation" if trigger_reason == "post_action_followup" else "observation",
            "attention_priority": "medium",
            "factuality": "runtime_observation",
            "should_reply_in_channel": False,
            "can_override_persona": False,
            "must_preserve_invariants": True,
        }
    if input_kind == "idle_tick":
        return {
            "input_role": "self_maintenance",
            "attention_priority": "low",
            "factuality": "internal_signal",
            "should_reply_in_channel": False,
            "can_override_persona": False,
            "must_preserve_invariants": True,
        }
    raise ValueError("unsupported input_kind for input_evaluation")


# Block: Context budget builders
def _trim_memory_bundle_for_context_budget(
    *,
    effective_settings: dict[str, Any],
    cycle_meta: dict[str, Any],
    time_context: dict[str, Any],
    self_snapshot: dict[str, Any],
    stable_self_state: dict[str, Any],
    confirmed_preferences: dict[str, Any],
    long_mood_state: dict[str, Any] | None,
    behavior_settings: dict[str, Any],
    selection_profile: dict[str, Any],
    body_snapshot: dict[str, Any],
    world_snapshot: dict[str, Any],
    drive_snapshot: dict[str, Any],
    task_snapshot: dict[str, Any],
    attention_snapshot: dict[str, Any],
    policy_snapshot: dict[str, Any],
    skill_candidates: list[dict[str, Any]],
    current_observation: dict[str, Any],
    camera_candidates: list[dict[str, Any]],
    memory_bundle: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    total_limit = _required_positive_integer_setting(
        effective_settings=effective_settings,
        key="runtime.context_budget_tokens",
    )
    preferred_memory_limit = _required_positive_integer_setting(
        effective_settings=effective_settings,
        key="memory.max_inject_tokens",
    )
    layer_limits = _build_context_layer_limits(
        total_limit=total_limit,
        preferred_memory_limit=preferred_memory_limit,
    )
    self_tokens = _estimate_self_layer_tokens(
        self_snapshot=self_snapshot,
        stable_self_state=stable_self_state,
        confirmed_preferences=confirmed_preferences,
        long_mood_state=long_mood_state,
        selection_profile=selection_profile,
    )
    behavior_tokens = _estimate_behavior_layer_tokens(behavior_settings=behavior_settings)
    situation_tokens = _estimate_situation_layer_tokens(
        cycle_meta=cycle_meta,
        time_context=time_context,
        body_snapshot=body_snapshot,
        world_snapshot=world_snapshot,
        drive_snapshot=drive_snapshot,
        task_snapshot=task_snapshot,
        attention_snapshot=attention_snapshot,
        policy_snapshot=policy_snapshot,
        skill_candidates=skill_candidates,
        current_observation=current_observation,
        camera_candidates=camera_candidates,
    )
    _validate_fixed_layer_budget(
        layer_name="self",
        estimated_tokens=self_tokens,
        layer_limit=layer_limits["self"],
    )
    _validate_fixed_layer_budget(
        layer_name="behavior",
        estimated_tokens=behavior_tokens,
        layer_limit=layer_limits["behavior"],
    )
    _validate_fixed_layer_budget(
        layer_name="situation",
        estimated_tokens=situation_tokens,
        layer_limit=layer_limits["situation"],
    )
    remaining_memory_budget = (
        total_limit
        - self_tokens
        - behavior_tokens
        - situation_tokens
        - layer_limits["output_contract"]
    )
    if remaining_memory_budget <= 0:
        raise RuntimeError("fixed cognition layers exceed runtime.context_budget_tokens")
    return _trim_memory_bundle_to_token_limit(
        memory_bundle=memory_bundle,
        token_limit=min(layer_limits["memory"], remaining_memory_budget),
    )


def _build_context_budget(
    *,
    effective_settings: dict[str, Any],
    cycle_meta: dict[str, Any],
    time_context: dict[str, Any],
    self_snapshot: dict[str, Any],
    stable_self_state: dict[str, Any],
    confirmed_preferences: dict[str, Any],
    long_mood_state: dict[str, Any] | None,
    behavior_settings: dict[str, Any],
    selection_profile: dict[str, Any],
    body_snapshot: dict[str, Any],
    world_snapshot: dict[str, Any],
    drive_snapshot: dict[str, Any],
    task_snapshot: dict[str, Any],
    attention_snapshot: dict[str, Any],
    retrieval_context: dict[str, Any],
    policy_snapshot: dict[str, Any],
    skill_candidates: list[dict[str, Any]],
    current_observation: dict[str, Any],
    camera_candidates: list[dict[str, Any]],
    recent_dialog: list[dict[str, Any]],
    selected_memory_pack: dict[str, Any],
    trimmed_memory_item_refs: list[str],
) -> dict[str, Any]:
    total_limit = _required_positive_integer_setting(
        effective_settings=effective_settings,
        key="runtime.context_budget_tokens",
    )
    preferred_memory_limit = _required_positive_integer_setting(
        effective_settings=effective_settings,
        key="memory.max_inject_tokens",
    )
    layer_limits = _build_context_layer_limits(
        total_limit=total_limit,
        preferred_memory_limit=preferred_memory_limit,
    )
    estimated_layer_tokens = {
        "self": _estimate_self_layer_tokens(
            self_snapshot=self_snapshot,
            stable_self_state=stable_self_state,
            confirmed_preferences=confirmed_preferences,
            long_mood_state=long_mood_state,
            selection_profile=selection_profile,
        ),
        "behavior": _estimate_behavior_layer_tokens(
            behavior_settings=behavior_settings,
        ),
        "situation": _estimate_situation_layer_tokens(
            cycle_meta=cycle_meta,
            time_context=time_context,
            body_snapshot=body_snapshot,
            world_snapshot=world_snapshot,
            drive_snapshot=drive_snapshot,
            task_snapshot=task_snapshot,
            attention_snapshot=attention_snapshot,
            retrieval_context=retrieval_context,
            policy_snapshot=policy_snapshot,
            skill_candidates=skill_candidates,
            current_observation=current_observation,
            camera_candidates=camera_candidates,
        ),
        "memory": _estimate_memory_layer_tokens(
            recent_dialog=recent_dialog,
            selected_memory_pack=selected_memory_pack,
        ),
        "output_contract": layer_limits["output_contract"],
    }
    for layer_name in ("self", "behavior", "situation", "memory"):
        _validate_fixed_layer_budget(
            layer_name=layer_name,
            estimated_tokens=estimated_layer_tokens[layer_name],
            layer_limit=layer_limits[layer_name],
        )
    estimated_total_tokens = sum(estimated_layer_tokens.values())
    if estimated_total_tokens > total_limit:
        raise RuntimeError("cognition_input exceeds runtime.context_budget_tokens after trimming")
    return {
        "total_limit": total_limit,
        "layer_limits": layer_limits,
        "estimated_layer_tokens": estimated_layer_tokens,
        "estimated_total_tokens": estimated_total_tokens,
        "trimmed_memory_item_refs": trimmed_memory_item_refs,
    }


def _build_context_layer_limits(
    *,
    total_limit: int,
    preferred_memory_limit: int,
) -> dict[str, int]:
    output_contract_limit = max(
        1,
        int(total_limit * CONTEXT_OUTPUT_CONTRACT_WEIGHT / 100),
    )
    memory_limit = min(
        preferred_memory_limit,
        max(1, int(total_limit * CONTEXT_MEMORY_WEIGHT / 100)),
    )
    remaining_limit = total_limit - output_contract_limit - memory_limit
    if remaining_limit <= 0:
        raise RuntimeError("runtime.context_budget_tokens is too small for fixed layer allocation")
    fixed_layer_limits = _split_weighted_budget(
        total_budget=remaining_limit,
        weights=(
            ("self", CONTEXT_SELF_WEIGHT),
            ("behavior", CONTEXT_BEHAVIOR_WEIGHT),
            ("situation", CONTEXT_SITUATION_WEIGHT),
        ),
    )
    return {
        **fixed_layer_limits,
        "memory": memory_limit,
        "output_contract": output_contract_limit,
    }


def _split_weighted_budget(
    *,
    total_budget: int,
    weights: tuple[tuple[str, int], ...],
) -> dict[str, int]:
    total_weight = sum(weight for _, weight in weights)
    raw_allocations: list[tuple[str, int, float]] = []
    assigned_total = 0
    for name, weight in weights:
        raw_value = total_budget * weight / total_weight
        base_value = int(raw_value)
        raw_allocations.append((name, base_value, raw_value - base_value))
        assigned_total += base_value
    remaining = total_budget - assigned_total
    allocations = {name: base_value for name, base_value, _ in raw_allocations}
    for name, _, _ in sorted(raw_allocations, key=lambda item: item[2], reverse=True):
        if remaining <= 0:
            break
        allocations[name] += 1
        remaining -= 1
    return allocations


def _estimate_self_layer_tokens(
    *,
    self_snapshot: dict[str, Any],
    stable_self_state: dict[str, Any],
    confirmed_preferences: dict[str, Any],
    long_mood_state: dict[str, Any] | None,
    selection_profile: dict[str, Any],
) -> int:
    return _estimate_token_count(
        _self_layer_budget_projection(
            self_snapshot=self_snapshot,
            stable_self_state=stable_self_state,
            confirmed_preferences=confirmed_preferences,
            long_mood_state=long_mood_state,
            selection_profile=selection_profile,
        )
    )


def _estimate_behavior_layer_tokens(*, behavior_settings: dict[str, Any]) -> int:
    return _estimate_token_count({"behavior_settings": behavior_settings})


def _estimate_situation_layer_tokens(
    *,
    cycle_meta: dict[str, Any],
    time_context: dict[str, Any],
    body_snapshot: dict[str, Any],
    world_snapshot: dict[str, Any],
    drive_snapshot: dict[str, Any],
    task_snapshot: dict[str, Any],
    attention_snapshot: dict[str, Any],
    retrieval_context: dict[str, Any] | None = None,
    policy_snapshot: dict[str, Any],
    skill_candidates: list[dict[str, Any]],
    current_observation: dict[str, Any],
    camera_candidates: list[dict[str, Any]],
) -> int:
    return _estimate_token_count(
        _situation_layer_budget_projection(
            cycle_meta=cycle_meta,
            time_context=time_context,
            body_snapshot=body_snapshot,
            world_snapshot=world_snapshot,
            drive_snapshot=drive_snapshot,
            task_snapshot=task_snapshot,
            attention_snapshot=attention_snapshot,
            retrieval_context=retrieval_context,
            policy_snapshot=policy_snapshot,
            skill_candidates=skill_candidates,
            current_observation=current_observation,
            camera_candidates=camera_candidates,
        )
    )


def _estimate_memory_layer_tokens(
    *,
    recent_dialog: list[dict[str, Any]],
    selected_memory_pack: dict[str, Any],
) -> int:
    return _estimate_token_count(
        _memory_layer_budget_projection(
            recent_dialog=recent_dialog,
            selected_memory_pack=selected_memory_pack,
        )
    )


def _estimate_token_count(value: Any) -> int:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if not serialized:
        return 0
    return max(1, (len(serialized) + ESTIMATED_TOKEN_CHAR_RATIO - 1) // ESTIMATED_TOKEN_CHAR_RATIO)


def _validate_fixed_layer_budget(
    *,
    layer_name: str,
    estimated_tokens: int,
    layer_limit: int,
) -> None:
    if estimated_tokens > layer_limit:
        raise RuntimeError(f"{layer_name} layer exceeds context budget")


def _trim_memory_bundle_to_token_limit(
    *,
    memory_bundle: dict[str, Any],
    token_limit: int,
) -> tuple[dict[str, Any], list[str]]:
    trimmed_bundle = {
        slot_name: list(memory_bundle[slot_name])
        for slot_name in (
            "working_memory_items",
            "episodic_items",
            "semantic_items",
            "affective_items",
            "relationship_items",
            "reflection_items",
            "recent_event_window",
        )
    }
    trimmed_refs: list[str] = []
    while _estimate_memory_layer_tokens(
        recent_dialog=_build_recent_dialog(
            recent_event_window=trimmed_bundle["recent_event_window"],
        ),
        selected_memory_pack=_build_selected_memory_pack(memory_bundle=trimmed_bundle),
    ) > token_limit:
        removed_ref = _pop_low_priority_memory_item(trimmed_bundle)
        if removed_ref is None:
            break
        trimmed_refs.append(removed_ref)
    if _estimate_memory_layer_tokens(
        recent_dialog=_build_recent_dialog(
            recent_event_window=trimmed_bundle["recent_event_window"],
        ),
        selected_memory_pack=_build_selected_memory_pack(memory_bundle=trimmed_bundle),
    ) > token_limit:
        raise RuntimeError("memory layer exceeds context budget even after trimming")
    return trimmed_bundle, trimmed_refs


def _pop_low_priority_memory_item(memory_bundle: dict[str, Any]) -> str | None:
    for slot_name in MEMORY_TRIM_SLOT_ORDER:
        slot_items = memory_bundle[slot_name]
        if not slot_items:
            continue
        removed_item = slot_items.pop()
        return _memory_bundle_item_ref(slot_name=slot_name, item=removed_item)
    return None


def _build_retrieval_selected_json(
    *,
    memory_bundle: dict[str, Any],
    source_selected_json: dict[str, Any],
    trimmed_memory_item_refs: list[str],
) -> dict[str, Any]:
    kept_refs = _selected_item_refs(memory_bundle=memory_bundle)
    filtered_selection_trace = [
        trace_entry
        for trace_entry in _required_selection_trace(source_selected_json)
        if isinstance(trace_entry, dict) and trace_entry.get("item_ref") in kept_refs
    ]
    collector_counts = _selection_trace_collector_counts(filtered_selection_trace)
    selector_summary = _build_selector_summary(
        source_selected_json=source_selected_json,
        selected_count=len(filtered_selection_trace),
        trimmed_memory_item_refs=trimmed_memory_item_refs,
    )
    reserve_trace = _build_reserve_trace(source_selected_json)
    return {
        "selected_counts": {
            "working_memory_items": len(memory_bundle["working_memory_items"]),
            "episodic_items": len(memory_bundle["episodic_items"]),
            "semantic_items": len(memory_bundle["semantic_items"]),
            "affective_items": len(memory_bundle["affective_items"]),
            "relationship_items": len(memory_bundle["relationship_items"]),
            "reflection_items": len(memory_bundle["reflection_items"]),
            "recent_event_window": len(memory_bundle["recent_event_window"]),
        },
        "selected_refs": {
            "working_memory_item_ids": [
                str(item["memory_state_id"])
                for item in memory_bundle["working_memory_items"]
            ],
            "episodic_item_ids": [
                str(item["memory_state_id"])
                for item in memory_bundle["episodic_items"]
            ],
            "semantic_item_ids": [
                str(item["memory_state_id"])
                for item in memory_bundle["semantic_items"]
            ],
            "affective_item_ids": [
                str(item["memory_state_id"])
                for item in memory_bundle["affective_items"]
            ],
            "relationship_item_ids": [
                str(item["memory_state_id"])
                for item in memory_bundle["relationship_items"]
            ],
            "reflection_item_ids": [
                str(item["memory_state_id"])
                for item in memory_bundle["reflection_items"]
            ],
            "recent_event_ids": [
                str(item["event_id"])
                for item in memory_bundle["recent_event_window"]
            ],
        },
        "selection_trace": filtered_selection_trace,
        **({"collector_counts": collector_counts} if collector_counts else {}),
        **({"selector_summary": selector_summary} if selector_summary else {}),
        **({"reserve_trace": reserve_trace} if reserve_trace else {}),
        **({"trimmed_item_refs": trimmed_memory_item_refs} if trimmed_memory_item_refs else {}),
    }


def _selected_item_refs(*, memory_bundle: dict[str, Any]) -> set[str]:
    refs: set[str] = set()
    for slot_name in (
        "working_memory_items",
        "episodic_items",
        "semantic_items",
        "affective_items",
        "relationship_items",
        "reflection_items",
        "recent_event_window",
    ):
        for item in memory_bundle[slot_name]:
            refs.add(_memory_bundle_item_ref(slot_name=slot_name, item=item))
    return refs


def _memory_bundle_item_ref(*, slot_name: str, item: dict[str, Any]) -> str:
    if slot_name == "recent_event_window":
        return f"event:{item['event_id']}"
    memory_kind = str(item["memory_kind"])
    memory_state_id = str(item["memory_state_id"])
    if memory_kind == "episodic_event":
        return f"event:{memory_state_id}"
    if memory_kind == "event_affect":
        return f"event_affect:{memory_state_id}"
    if memory_kind == "preference":
        return f"preference:{memory_state_id}"
    return f"memory_state:{memory_state_id}"


def _required_selection_trace(selected_json: dict[str, Any]) -> list[dict[str, Any]]:
    selection_trace = selected_json.get("selection_trace")
    if not isinstance(selection_trace, list):
        raise RuntimeError("retrieval selected_json.selection_trace must be list")
    return selection_trace


def _selection_trace_collector_counts(selection_trace: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for trace_entry in selection_trace:
        collector_names = trace_entry.get("collector_names")
        if not isinstance(collector_names, list):
            continue
        for collector_name in collector_names:
            if not isinstance(collector_name, str) or not collector_name:
                continue
            counts[collector_name] = counts.get(collector_name, 0) + 1
    return counts


def _build_selector_summary(
    *,
    source_selected_json: dict[str, Any],
    selected_count: int,
    trimmed_memory_item_refs: list[str],
) -> dict[str, Any]:
    raw_summary = source_selected_json.get("selector_summary")
    if not isinstance(raw_summary, dict):
        if not trimmed_memory_item_refs:
            return {}
        return {
            "selected_candidate_count": selected_count,
            "trimmed_candidate_count": len(trimmed_memory_item_refs),
        }
    summary: dict[str, Any] = {}
    for key in (
        "selector_mode",
        "selection_reason",
    ):
        value = raw_summary.get(key)
        if isinstance(value, str) and value:
            summary[key] = value
    for key in (
        "raw_candidate_count",
        "merged_candidate_count",
        "selector_input_candidate_count",
        "selector_candidate_limit",
        "llm_selected_ref_count",
        "duplicate_hit_count",
        "reserve_candidate_count",
        "slot_skipped_count",
    ):
        value = raw_summary.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            summary[key] = value
    summary["selected_candidate_count"] = selected_count
    if trimmed_memory_item_refs:
        summary["trimmed_candidate_count"] = len(trimmed_memory_item_refs)
    return summary


def _build_reserve_trace(source_selected_json: dict[str, Any]) -> list[dict[str, Any]]:
    reserve_trace = source_selected_json.get("reserve_trace")
    if not isinstance(reserve_trace, list):
        return []
    return [
        trace_entry
        for trace_entry in reserve_trace
        if isinstance(trace_entry, dict)
    ]


def _self_layer_budget_projection(
    *,
    self_snapshot: dict[str, Any],
    stable_self_state: dict[str, Any],
    confirmed_preferences: dict[str, Any],
    long_mood_state: dict[str, Any] | None,
    selection_profile: dict[str, Any],
) -> dict[str, Any]:
    persona_projection = build_persona_prompt_projection(selection_profile=selection_profile)
    return {
        "current_emotion_label": str(stable_self_state.get("current_emotion_label", "")),
        "persona_projection": persona_projection,
        "stable_self_state": stable_self_state,
        "confirmed_preferences": confirmed_preferences,
        "long_mood_state": long_mood_state,
        "relationship_priorities": [
            {
                "target_ref": str(item.get("target_ref", "")),
                "reason_tag": str(item.get("reason_tag", "")),
            }
            for item in selection_profile["relationship_priorities"][:3]
            if isinstance(item, dict)
        ],
        "last_persona_update": _persona_update_budget_projection(self_snapshot=self_snapshot),
    }


def _persona_update_budget_projection(*, self_snapshot: dict[str, Any]) -> dict[str, Any] | None:
    last_persona_update = self_snapshot.get("last_persona_update")
    if not isinstance(last_persona_update, dict):
        return None
    updated_traits = last_persona_update.get("updated_traits")
    if not isinstance(updated_traits, list):
        return None
    return {
        "reason": str(last_persona_update.get("reason", "")),
        "updated_traits": [
            str(trait_entry.get("trait_name"))
            for trait_entry in updated_traits[:4]
            if isinstance(trait_entry, dict) and isinstance(trait_entry.get("trait_name"), str)
        ],
    }


def _situation_layer_budget_projection(
    *,
    cycle_meta: dict[str, Any],
    time_context: dict[str, Any],
    body_snapshot: dict[str, Any],
    world_snapshot: dict[str, Any],
    drive_snapshot: dict[str, Any],
    task_snapshot: dict[str, Any],
    attention_snapshot: dict[str, Any],
    retrieval_context: dict[str, Any] | None,
    policy_snapshot: dict[str, Any],
    skill_candidates: list[dict[str, Any]],
    current_observation: dict[str, Any],
    camera_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    projection = {
        "cycle_id": str(cycle_meta["cycle_id"]),
        "current_time_local_text": str(time_context.get("current_time_local_text", "")),
        "body_snapshot": _body_snapshot_budget_projection(body_snapshot=body_snapshot),
        "world_summary": str(world_snapshot.get("situation_summary", "")),
        "drive_snapshot": _drive_snapshot_budget_projection(drive_snapshot=drive_snapshot),
        "task_snapshot": _task_snapshot_budget_projection(task_snapshot=task_snapshot),
        "attention": _attention_budget_projection(attention_snapshot=attention_snapshot),
        "input_evaluation": dict(policy_snapshot["input_evaluation"]),
        "skill_candidates": _skill_candidate_budget_projection(skill_candidates=skill_candidates),
        "current_observation": _current_observation_budget_projection(current_observation=current_observation),
        "camera_candidates": _camera_candidate_budget_projection(camera_candidates=camera_candidates),
    }
    if retrieval_context is not None:
        projection["retrieval_context"] = _retrieval_context_budget_projection(retrieval_context=retrieval_context)
    return projection


def _attention_budget_projection(*, attention_snapshot: dict[str, Any]) -> dict[str, Any]:
    primary_focus = attention_snapshot.get("primary_focus")
    if not isinstance(primary_focus, dict):
        return {"summary": "", "reason_codes": []}
    return {
        "summary": str(primary_focus.get("summary", "")),
        "reason_codes": [
            str(reason_code)
            for reason_code in primary_focus.get("reason_codes", [])[:3]
        ],
    }


# Block: Situation sub projections
def _body_snapshot_budget_projection(*, body_snapshot: dict[str, Any]) -> dict[str, Any]:
    posture = body_snapshot.get("posture")
    sensor_availability = body_snapshot.get("sensor_availability")
    load = body_snapshot.get("load")
    if not isinstance(posture, dict) or not isinstance(sensor_availability, dict) or not isinstance(load, dict):
        return {}
    return {
        "posture_mode": str(posture.get("mode", "")),
        "sensor_availability": {
            "camera": bool(sensor_availability.get("camera")),
            "microphone": bool(sensor_availability.get("microphone")),
        },
        "load": {
            "task_queue_pressure": _safe_numeric_projection(load.get("task_queue_pressure")),
            "interaction_load": _safe_numeric_projection(load.get("interaction_load")),
        },
    }


def _drive_snapshot_budget_projection(*, drive_snapshot: dict[str, Any]) -> dict[str, Any]:
    priority_effects = drive_snapshot.get("priority_effects")
    if not isinstance(priority_effects, dict):
        return {}
    return {
        "task_progress_bias": _safe_numeric_projection(priority_effects.get("task_progress_bias")),
        "exploration_bias": _safe_numeric_projection(priority_effects.get("exploration_bias")),
        "maintenance_bias": _safe_numeric_projection(priority_effects.get("maintenance_bias")),
        "social_bias": _safe_numeric_projection(priority_effects.get("social_bias")),
    }


def _task_snapshot_budget_projection(*, task_snapshot: dict[str, Any]) -> dict[str, Any]:
    active_tasks = task_snapshot.get("active_tasks")
    waiting_external_tasks = task_snapshot.get("waiting_external_tasks")
    return {
        "active_tasks": _task_entries_budget_projection(active_tasks),
        "waiting_external_tasks": _task_entries_budget_projection(waiting_external_tasks),
    }


def _task_entries_budget_projection(task_entries: Any) -> list[dict[str, Any]]:
    if not isinstance(task_entries, list):
        return []
    projected_entries: list[dict[str, Any]] = []
    for task_entry in task_entries[:3]:
        if not isinstance(task_entry, dict):
            continue
        projected_entries.append(
            {
                "task_kind": str(task_entry.get("task_kind", "")),
                "goal_hint": _prompt_text(str(task_entry.get("goal_hint", ""))),
                "relative_time_text": str(task_entry.get("relative_time_text", "")),
            }
        )
    return projected_entries


def _skill_candidate_budget_projection(*, skill_candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    projected_candidates: list[dict[str, Any]] = []
    for candidate in skill_candidates[:3]:
        if not isinstance(candidate, dict):
            continue
        projected_candidates.append(
            {
                "skill_id": str(candidate.get("skill_id", "")),
                "initiative_kind": str(candidate.get("initiative_kind", "")),
                "fit_score": float(candidate.get("fit_score", 0.0)),
                "suggested_action_types": [
                    str(action_type)
                    for action_type in candidate.get("suggested_action_types", [])[:2]
                ],
            }
        )
    return projected_candidates


def _current_observation_budget_projection(*, current_observation: dict[str, Any]) -> dict[str, Any]:
    projected_observation = {
        "input_kind": str(current_observation["input_kind"]),
        "observation_text": _prompt_text(str(current_observation["observation_text"])),
        "captured_at_local_text": str(current_observation.get("captured_at_local_text", "")),
        "relative_time_text": str(current_observation.get("relative_time_text", "")),
    }
    attachment_summary_text = current_observation.get("attachment_summary_text")
    if isinstance(attachment_summary_text, str) and attachment_summary_text:
        projected_observation["attachment_summary_text"] = attachment_summary_text
    query = current_observation.get("query")
    if isinstance(query, str) and query:
        projected_observation["query"] = query
    source_task_id = current_observation.get("source_task_id")
    if isinstance(source_task_id, str) and source_task_id:
        projected_observation["source_task_id"] = source_task_id
    return projected_observation


def _camera_candidate_budget_projection(*, camera_candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    projected_candidates: list[dict[str, Any]] = []
    for candidate in camera_candidates[:5]:
        if not isinstance(candidate, dict):
            continue
        projected_candidates.append(
            {
                "camera_connection_id": str(candidate.get("camera_connection_id", "")),
                "display_name": str(candidate.get("display_name", "")),
                "presets": [
                    str(preset.get("preset_name", ""))
                    for preset in candidate.get("presets", [])[:4]
                    if isinstance(preset, dict)
                ],
            }
        )
    return projected_candidates


def _retrieval_context_budget_projection(*, retrieval_context: dict[str, Any]) -> dict[str, Any]:
    plan = retrieval_context.get("plan")
    selected = retrieval_context.get("selected")
    projected_context = {
        "mode": "",
        "queries": [],
        "selected_counts": {},
    }
    if isinstance(plan, dict):
        projected_context["mode"] = str(plan.get("mode", ""))
        projected_context["queries"] = [
            str(query)
            for query in plan.get("queries", [])[:3]
        ]
    if isinstance(selected, dict) and isinstance(selected.get("selected_counts"), dict):
        projected_context["selected_counts"] = {
            str(key): int(value)
            for key, value in selected["selected_counts"].items()
            if isinstance(value, int) and not isinstance(value, bool) and value > 0
        }
    return projected_context


def _memory_layer_budget_projection(
    *,
    recent_dialog: list[dict[str, Any]],
    selected_memory_pack: dict[str, Any],
) -> dict[str, Any]:
    return {
        "recent_dialog": [
            {
                "role": str(dialog_entry.get("role", "")),
                "text": _prompt_text(str(dialog_entry.get("text", ""))),
                "relative_time_text": str(dialog_entry.get("relative_time_text", "")),
            }
            for dialog_entry in recent_dialog
            if isinstance(dialog_entry, dict)
        ],
        "selected_memory_pack": {
            str(key): [
                _prompt_text(str(value))
                for value in values
                if isinstance(value, str) and value
            ]
            for key, values in selected_memory_pack.items()
            if isinstance(values, list)
        },
    }


# Block: Stable self state
def _goal_summary_texts(*, long_term_goals: dict[str, Any]) -> list[str]:
    goals = long_term_goals.get("goals")
    if not isinstance(goals, list):
        raise RuntimeError("self_snapshot.long_term_goals.goals must be a list")
    summaries: list[str] = []
    for goal_entry in goals[:3]:
        if not isinstance(goal_entry, dict):
            raise RuntimeError("self_snapshot.long_term_goals.goals must contain only objects")
        summary = goal_entry.get("summary")
        if not isinstance(summary, str) or not summary:
            raise RuntimeError("self_snapshot.long_term_goals.goals[].summary must be non-empty string")
        summaries.append(_prompt_text(summary))
    return summaries


# Block: Relationship summaries
def _relationship_summary_texts(*, relationship_overview: dict[str, Any]) -> list[str]:
    relationships = relationship_overview.get("relationships")
    if not isinstance(relationships, list):
        raise RuntimeError("self_snapshot.relationship_overview.relationships must be a list")
    summaries: list[str] = []
    for relationship_entry in relationships[:3]:
        if not isinstance(relationship_entry, dict):
            raise RuntimeError("self_snapshot.relationship_overview.relationships must contain only objects")
        target_ref = relationship_entry.get("target_ref")
        relation_kind = relationship_entry.get("relation_kind")
        if not isinstance(target_ref, str) or not target_ref:
            raise RuntimeError("relationship_overview.target_ref must be non-empty string")
        if not isinstance(relation_kind, str) or not relation_kind:
            raise RuntimeError("relationship_overview.relation_kind must be non-empty string")
        summaries.append(
            _prompt_text(
                f"{target_ref}との{relation_kind}関係({_relationship_reason_tag(relationship_entry)})"
            )
        )
    return summaries


# Block: Task summaries
def _task_summary_texts(*, task_entries: list[dict[str, Any]]) -> list[str]:
    summaries: list[str] = []
    for task_entry in task_entries[:3]:
        if not isinstance(task_entry, dict):
            raise RuntimeError("task_snapshot entries must contain only objects")
        task_kind = task_entry.get("task_kind")
        goal_hint = task_entry.get("goal_hint")
        relative_time_text = task_entry.get("relative_time_text")
        if not isinstance(task_kind, str) or not task_kind:
            raise RuntimeError("task_snapshot.task_kind must be non-empty string")
        if not isinstance(goal_hint, str) or not goal_hint:
            raise RuntimeError("task_snapshot.goal_hint must be non-empty string")
        if not isinstance(relative_time_text, str):
            raise RuntimeError("task_snapshot.relative_time_text must be string")
        summaries.append(_prompt_text(f"{task_kind}: {goal_hint} ({relative_time_text})"))
    return summaries


# Block: Stable self state
def _build_stable_self_state(
    *,
    self_snapshot: dict[str, Any],
    task_snapshot: dict[str, Any],
) -> dict[str, Any]:
    long_term_goals = self_snapshot.get("long_term_goals")
    relationship_overview = self_snapshot.get("relationship_overview")
    invariants = self_snapshot.get("invariants")
    if not isinstance(long_term_goals, dict):
        raise RuntimeError("self_snapshot.long_term_goals must be an object")
    if not isinstance(relationship_overview, dict):
        raise RuntimeError("self_snapshot.relationship_overview must be an object")
    if not isinstance(invariants, dict):
        raise RuntimeError("self_snapshot.invariants must be an object")
    return {
        "current_emotion_label": str(self_snapshot["current_emotion"].get("primary_label", "")),
        "goal_summaries": _goal_summary_texts(long_term_goals=long_term_goals),
        "relationship_summaries": _relationship_summary_texts(
            relationship_overview=relationship_overview,
        ),
        "active_task_summaries": _task_summary_texts(
            task_entries=task_snapshot["active_tasks"],
        ),
        "waiting_task_summaries": _task_summary_texts(
            task_entries=task_snapshot["waiting_external_tasks"],
        ),
        "invariants": {
            "forbidden_action_types": [
                str(action_type)
                for action_type in invariants.get("forbidden_action_types", [])[:8]
                if isinstance(action_type, str) and action_type
            ],
            "forbidden_action_styles": [
                str(action_style)
                for action_style in invariants.get("forbidden_action_styles", [])[:8]
                if isinstance(action_style, str) and action_style
            ],
            "required_confirmation_for": [
                str(rule)
                for rule in invariants.get("required_confirmation_for", [])[:6]
                if isinstance(rule, str) and rule
            ],
            "protected_targets": [
                str(target.get("target_ref", ""))
                for target in invariants.get("protected_targets", [])[:6]
                if isinstance(target, dict) and isinstance(target.get("target_ref"), str)
            ],
        },
    }


# Block: Long mood state context
def _build_long_mood_state_context(*, long_mood_item: dict[str, Any] | None) -> dict[str, Any] | None:
    if long_mood_item is None:
        return None
    if not isinstance(long_mood_item, dict):
        raise RuntimeError("state_snapshot.stable_long_mood_item must be object or null")
    if str(long_mood_item.get("memory_kind")) != "long_mood_state":
        raise RuntimeError("state_snapshot.stable_long_mood_item.memory_kind must be long_mood_state")
    payload = long_mood_item.get("payload")
    if not isinstance(payload, dict):
        raise RuntimeError("long_mood_state payload must be an object")
    baseline = payload.get("baseline")
    shock = payload.get("shock")
    return {
        "summary_text": str(long_mood_item.get("body_text", "")),
        "primary_label": str(payload.get("primary_label", "")),
        "baseline_label": (
            str(baseline.get("primary_label", ""))
            if isinstance(baseline, dict)
            else ""
        ),
        "shock_label": (
            str(shock.get("primary_label", ""))
            if isinstance(shock, dict)
            else ""
        ),
        "stability": (
            round(float(payload.get("stability")), 2)
            if isinstance(payload.get("stability"), (int, float))
            and not isinstance(payload.get("stability"), bool)
            else None
        ),
        "source_affect_labels": [
            str(label)
            for label in payload.get("source_affect_labels", [])[:4]
            if isinstance(label, str) and label
        ],
    }


# Block: Action selection context
def _build_action_selection_context(
    *,
    current_observation: dict[str, Any],
    memory_bundle: dict[str, Any],
    recent_dialog: list[dict[str, Any]],
    selected_memory_pack: dict[str, Any],
    confirmed_preferences: dict[str, Any],
    long_mood_state: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "current_input_kind": str(current_observation["input_kind"]),
        "recent_dialog": list(recent_dialog[-4:]),
        "recent_context_texts": list(selected_memory_pack["recent_context"][:4]),
        "working_memory_texts": list(selected_memory_pack["working_memory"][:4]),
        "episodic_texts": list(selected_memory_pack["episodic"][:4]),
        "fact_entries": _action_selection_fact_entries(
            semantic_items=memory_bundle["semantic_items"],
        ),
        "affect_entries": _action_selection_affect_entries(
            affective_items=memory_bundle["affective_items"],
        ),
        "relationship_texts": list(selected_memory_pack["relationship"][:4]),
        "reflection_entries": _action_selection_reflection_entries(
            reflection_items=memory_bundle["reflection_items"],
        ),
        "confirmed_preferences": confirmed_preferences,
        "long_mood_state": long_mood_state,
    }


# Block: Action selection fact entries
def _action_selection_fact_entries(*, semantic_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    projected_entries: list[dict[str, Any]] = []
    for semantic_item in semantic_items[:4]:
        if not isinstance(semantic_item, dict):
            raise RuntimeError("memory_bundle.semantic_items must contain only objects")
        payload = semantic_item.get("payload")
        if not isinstance(payload, dict):
            raise RuntimeError("memory_bundle.semantic_items.payload must be an object")
        projected_entry = {
            "text": _memory_pack_text(semantic_item, text_getter=_memory_body_text),
        }
        query = payload.get("query")
        if isinstance(query, str) and query:
            projected_entry["query"] = query
        projected_entries.append(projected_entry)
    return projected_entries


# Block: Action selection affect entries
def _action_selection_affect_entries(*, affective_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    projected_entries: list[dict[str, Any]] = []
    for affective_item in affective_items[:4]:
        if not isinstance(affective_item, dict):
            raise RuntimeError("memory_bundle.affective_items must contain only objects")
        payload = affective_item.get("payload")
        if not isinstance(payload, dict):
            raise RuntimeError("memory_bundle.affective_items.payload must be an object")
        projected_entry: dict[str, Any] = {
            "text": _memory_pack_text(affective_item, text_getter=_memory_body_text),
            "labels": [
                str(label)
                for label in payload.get("labels", [])[:4]
                if isinstance(label, str) and label
            ],
        }
        vad = payload.get("vad")
        if isinstance(vad, dict):
            valence = vad.get("v")
            arousal = vad.get("a")
            if isinstance(valence, (int, float)) and not isinstance(valence, bool):
                projected_entry["valence"] = float(valence)
            if isinstance(arousal, (int, float)) and not isinstance(arousal, bool):
                projected_entry["arousal"] = float(arousal)
        projected_entries.append(projected_entry)
    return projected_entries


# Block: Action selection reflection entries
def _action_selection_reflection_entries(*, reflection_items: list[dict[str, Any]]) -> list[dict[str, str]]:
    projected_entries: list[dict[str, str]] = []
    for reflection_item in reflection_items[:4]:
        if not isinstance(reflection_item, dict):
            raise RuntimeError("memory_bundle.reflection_items must contain only objects")
        projected_entries.append(
            {
                "text": _memory_pack_text(reflection_item, text_getter=_reflection_memory_text),
            }
        )
    return projected_entries


# Block: Reply render input
def _build_reply_render_input(
    *,
    current_observation: dict[str, Any],
    time_context: dict[str, Any],
    attention_snapshot: dict[str, Any],
    retrieval_context: dict[str, Any],
    stable_self_state: dict[str, Any],
    confirmed_preferences: dict[str, Any],
    long_mood_state: dict[str, Any] | None,
    recent_dialog: list[dict[str, Any]],
    selected_memory_pack: dict[str, Any],
    selection_profile: dict[str, Any],
) -> dict[str, Any]:
    return {
        "observation_text": _build_reply_render_observation_text(
            current_observation=current_observation,
        ),
        "time_reference_text": _build_reply_render_time_reference_text(
            time_context=time_context,
        ),
        "attention_summary_text": _build_reply_render_attention_summary_text(
            attention_snapshot=attention_snapshot,
        ),
        "retrieval_summary_text": _build_reply_render_retrieval_summary_text(
            retrieval_context=retrieval_context,
        ),
        "stable_self_state": stable_self_state,
        "confirmed_preferences": confirmed_preferences,
        "long_mood_state": long_mood_state,
        "recent_dialog": recent_dialog,
        "selected_memory_pack": selected_memory_pack,
        "reply_style": {
            "speech_tone": str(selection_profile["interaction_style"]["speech_tone"]),
            "response_pace": str(selection_profile["interaction_style"]["response_pace"]),
        },
    }


# Block: Reply render observation text
def _build_reply_render_observation_text(*, current_observation: dict[str, Any]) -> str:
    observation_text = current_observation.get("observation_text")
    if not isinstance(observation_text, str) or not observation_text:
        raise RuntimeError("current_observation.observation_text must be non-empty string")
    return _prompt_text(observation_text)


# Block: Reply render time reference text
def _build_reply_render_time_reference_text(*, time_context: dict[str, Any]) -> str:
    current_time_local_text = time_context.get("current_time_local_text")
    relative_reference_text = time_context.get("relative_reference_text")
    if not isinstance(current_time_local_text, str) or not current_time_local_text:
        raise RuntimeError("time_context.current_time_local_text must be non-empty string")
    if not isinstance(relative_reference_text, str) or not relative_reference_text:
        raise RuntimeError("time_context.relative_reference_text must be non-empty string")
    return f"{current_time_local_text} ({relative_reference_text})"


# Block: Reply render attention summary text
def _build_reply_render_attention_summary_text(*, attention_snapshot: dict[str, Any]) -> str:
    primary_focus = attention_snapshot.get("primary_focus")
    if not isinstance(primary_focus, dict):
        raise RuntimeError("attention_snapshot.primary_focus must be an object")
    focus_kind = primary_focus.get("focus_kind")
    summary = primary_focus.get("summary")
    reason_codes = primary_focus.get("reason_codes")
    if not isinstance(focus_kind, str) or not focus_kind:
        raise RuntimeError("attention_snapshot.primary_focus.focus_kind must be non-empty string")
    if not isinstance(summary, str) or not summary:
        raise RuntimeError("attention_snapshot.primary_focus.summary must be non-empty string")
    if not isinstance(reason_codes, list):
        raise RuntimeError("attention_snapshot.primary_focus.reason_codes must be a list")
    normalized_reasons = [
        str(reason_code)
        for reason_code in reason_codes[:3]
        if isinstance(reason_code, str) and reason_code
    ]
    reason_text = ",".join(normalized_reasons) if normalized_reasons else "none"
    return f"kind={focus_kind} summary={_prompt_text(summary)} reasons={reason_text}"


# Block: Reply render retrieval summary text
def _build_reply_render_retrieval_summary_text(*, retrieval_context: dict[str, Any]) -> str:
    plan = retrieval_context.get("plan")
    selected = retrieval_context.get("selected")
    if not isinstance(plan, dict) or not isinstance(selected, dict):
        raise RuntimeError("retrieval_context must contain plan and selected")
    mode = plan.get("mode")
    queries = plan.get("queries")
    selected_counts = selected.get("selected_counts")
    if not isinstance(mode, str) or not mode:
        raise RuntimeError("retrieval_context.plan.mode must be non-empty string")
    if not isinstance(queries, list):
        raise RuntimeError("retrieval_context.plan.queries must be a list")
    if not isinstance(selected_counts, dict):
        raise RuntimeError("retrieval_context.selected.selected_counts must be an object")
    query_text = ",".join(
        _prompt_text(query_text)
        for query_text in queries[:3]
        if isinstance(query_text, str) and query_text
    )
    if not query_text:
        query_text = "なし"
    selected_text_parts = [
        f"{key}={value}"
        for key, value in selected_counts.items()
        if isinstance(key, str) and isinstance(value, int) and value > 0
    ]
    selected_text = ",".join(selected_text_parts) if selected_text_parts else "なし"
    return f"mode={mode} queries={query_text} selected={selected_text}"


# Block: Recent dialog
def _build_recent_dialog(*, recent_event_window: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _recent_dialog_entries(recent_event_window=recent_event_window)


# Block: Selected memory pack
def _build_selected_memory_pack(*, memory_bundle: dict[str, Any]) -> dict[str, Any]:
    return {
        "recent_context": _recent_context_texts(
            recent_event_window=memory_bundle["recent_event_window"],
        ),
        "working_memory": _memory_pack_texts(
            entries=memory_bundle["working_memory_items"],
            text_getter=_memory_body_text,
        ),
        "episodic": _memory_pack_texts(
            entries=memory_bundle["episodic_items"],
            text_getter=_memory_body_text,
        ),
        "facts": _memory_pack_texts(
            entries=memory_bundle["semantic_items"],
            text_getter=_memory_body_text,
        ),
        "affective": _memory_pack_texts(
            entries=memory_bundle["affective_items"],
            text_getter=_memory_body_text,
        ),
        "relationship": _memory_pack_texts(
            entries=memory_bundle["relationship_items"],
            text_getter=_memory_body_text,
        ),
        "reflection": _memory_pack_texts(
            entries=memory_bundle["reflection_items"],
            text_getter=_reflection_memory_text,
        ),
    }


# Block: 最近会話抽出
def _recent_dialog_entries(*, recent_event_window: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized_events: list[dict[str, Any]] = []
    for event_entry in recent_event_window:
        if not isinstance(event_entry, dict):
            raise RuntimeError("memory_bundle.recent_event_window must contain only objects")
        normalized_events.append(event_entry)
    dialog_entries: list[dict[str, Any]] = []
    for event_entry in sorted(
        normalized_events,
        key=lambda item: int(item["created_at"]),
    ):
        dialog_role = event_entry.get("dialog_role")
        dialog_text = event_entry.get("dialog_text")
        if not isinstance(dialog_role, str) or not dialog_role:
            continue
        if not isinstance(dialog_text, str) or not dialog_text:
            continue
        dialog_entry = {
            "role": dialog_role,
            "text": _prompt_text(dialog_text),
            "relative_time_text": str(event_entry.get("relative_time_text", "")),
        }
        dialog_entries.append(dialog_entry)
    return dialog_entries[-6:]


# Block: 直近文脈抽出
def _recent_context_texts(*, recent_event_window: list[dict[str, Any]]) -> list[str]:
    context_texts: list[str] = []
    for event_entry in recent_event_window:
        if not isinstance(event_entry, dict):
            raise RuntimeError("memory_bundle.recent_event_window must contain only objects")
        if isinstance(event_entry.get("dialog_role"), str):
            continue
        context_texts.append(_memory_pack_text(event_entry, text_getter=_recent_event_text))
    return context_texts


# Block: 記憶パック本文抽出
def _memory_pack_texts(
    *,
    entries: list[dict[str, Any]],
    text_getter: Any,
) -> list[str]:
    return [
        _memory_pack_text(entry, text_getter=text_getter)
        for entry in entries
        if isinstance(entry, dict)
    ]


# Block: 記憶パック本文
def _memory_pack_text(memory_entry: dict[str, Any], *, text_getter: Any) -> str:
    base_text = _prompt_text(text_getter(memory_entry))
    about_time_hint_text = memory_entry.get("about_time_hint_text")
    if isinstance(about_time_hint_text, str) and about_time_hint_text:
        return f"{base_text} [時期: {about_time_hint_text}]"
    return base_text


# Block: 直近イベント本文
def _recent_event_text(event_entry: dict[str, Any]) -> str:
    summary_text = event_entry.get("summary_text")
    if not isinstance(summary_text, str) or not summary_text:
        raise RuntimeError("recent_event_window.summary_text must be non-empty string")
    return summary_text


def _memory_body_text(memory_entry: dict[str, Any]) -> str:
    return str(memory_entry["body_text"])


def _reflection_memory_text(memory_entry: dict[str, Any]) -> str:
    payload = memory_entry.get("payload")
    if isinstance(payload, dict):
        what_happened = payload.get("what_happened")
        if isinstance(what_happened, str) and what_happened:
            return what_happened
    return str(memory_entry["body_text"])


def _prompt_text(text: str) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= MEMORY_PROMPT_TEXT_LIMIT:
        return normalized
    return normalized[: MEMORY_PROMPT_TEXT_LIMIT - 1] + "…"


def _safe_numeric_projection(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(float(value), 4)


# Block: Behavior settings
def _build_behavior_settings(effective_settings: dict[str, Any]) -> dict[str, str]:
    return {
        "second_person_label": _required_string_setting(effective_settings, "behavior.second_person_label"),
        "system_prompt": _required_string_setting(effective_settings, "behavior.system_prompt"),
        "addon_prompt": _required_string_setting(effective_settings, "behavior.addon_prompt"),
        "response_pace": _required_string_setting(effective_settings, "behavior.response_pace"),
        "proactivity_level": _required_string_setting(effective_settings, "behavior.proactivity_level"),
        "browse_preference": _required_string_setting(effective_settings, "behavior.browse_preference"),
        "notify_preference": _required_string_setting(effective_settings, "behavior.notify_preference"),
        "speech_style": _required_string_setting(effective_settings, "behavior.speech_style"),
        "verbosity_bias": _required_string_setting(effective_settings, "behavior.verbosity_bias"),
    }


# Block: Relationship priorities
def _build_relationship_priorities(relationship_overview: dict[str, Any]) -> list[dict[str, Any]]:
    relationships = relationship_overview["relationships"]
    if not isinstance(relationships, list):
        raise ValueError("self_state.relationship_overview.relationships must be list")
    priorities: list[dict[str, Any]] = []
    for relationship in relationships[:3]:
        if not isinstance(relationship, dict):
            raise ValueError("self_state.relationship_overview.relationships item must be object")
        target_ref = relationship["target_ref"]
        if not isinstance(target_ref, str) or not target_ref:
            raise ValueError("relationship.target_ref must be non-empty string")
        priorities.append(
            {
                "target_ref": target_ref,
                "priority_weight": _normalized_number(
                    relationship["attention_weight"],
                    field_name="relationship.attention_weight",
                ),
                "reason_tag": _relationship_reason_tag(relationship),
            }
        )
    return priorities


# Block: Relationship reason
def _relationship_reason_tag(relationship: dict[str, Any]) -> str:
    waiting_response = relationship["waiting_response"]
    if not isinstance(waiting_response, bool):
        raise ValueError("relationship.waiting_response must be boolean")
    if waiting_response is True:
        return "pending_relation"
    if _normalized_number(
        relationship["care_commitment"],
        field_name="relationship.care_commitment",
    ) >= 0.70:
        return "care_target"
    if _normalized_number(
        relationship["recent_tension"],
        field_name="relationship.recent_tension",
    ) >= 0.60:
        return "recent_tension"
    if _normalized_number(
        relationship["recent_positive_contact"],
        field_name="relationship.recent_positive_contact",
    ) >= 0.60:
        return "recent_positive_contact"
    return "care_target"


# Block: Time helpers
def _build_time_context(resolved_at: int) -> dict[str, Any]:
    local_now = datetime.fromtimestamp(resolved_at / 1000, tz=timezone.utc).astimezone()
    timezone_name = local_now.tzname() or "UTC"
    return {
        "current_time_unix_ms": resolved_at,
        "current_time_utc_text": _utc_text(resolved_at),
        "current_time_local_text": _local_text(resolved_at),
        "timezone_name": timezone_name,
        "relative_reference_text": "0秒前",
    }


def _utc_text(unix_ms: int) -> str:
    return datetime.fromtimestamp(unix_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _local_text(unix_ms: int) -> str:
    local_dt = datetime.fromtimestamp(unix_ms / 1000, tz=timezone.utc).astimezone()
    timezone_name = local_dt.tzname() or "UTC"
    return local_dt.strftime(f"%Y-%m-%d %H:%M:%S {timezone_name}")


# Block: Required setting helpers
def _required_string_setting(effective_settings: dict[str, Any], key: str) -> str:
    value = effective_settings.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be string")
    return value


def _required_positive_integer_setting(effective_settings: dict[str, Any], key: str) -> int:
    value = effective_settings.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeError(f"{key} must be integer")
    if value <= 0:
        raise RuntimeError(f"{key} must be positive")
    return value


def _relative_time_text(now_ms: int, past_ms: int) -> str:
    delta_seconds = max(0, (now_ms - past_ms) // 1000)
    if delta_seconds < 60:
        return f"{delta_seconds}秒前"
    delta_minutes = delta_seconds // 60
    if delta_minutes < 60:
        return f"{delta_minutes}分前"
    delta_hours = delta_minutes // 60
    if delta_hours < 24:
        return f"{delta_hours}時間前"
    delta_days = delta_hours // 24
    return f"{delta_days}日前"


# Block: Numeric helper
def _normalized_number(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be numeric")
    if not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be numeric")
    numeric_value = float(value)
    if numeric_value < 0.0:
        return 0.0
    if numeric_value > 1.0:
        return 1.0
    return numeric_value


def _normalized_signed_number(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be numeric")
    if not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be numeric")
    numeric_value = float(value)
    if numeric_value < -1.0:
        return -1.0
    if numeric_value > 1.0:
        return 1.0
    return numeric_value
