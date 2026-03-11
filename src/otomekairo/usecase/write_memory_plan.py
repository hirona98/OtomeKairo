"""Build and validate structured write_memory plans."""

from __future__ import annotations

import re
from typing import Any

from otomekairo.usecase.about_time_text import about_years_from_text, life_stage_from_text


# Block: Plan constants
WRITE_MEMORY_PLAN_KEYS = (
    "event_annotations",
    "state_updates",
    "preference_updates",
    "event_affect",
    "context_updates",
    "revision_reasons",
)
WRITE_MEMORY_PLAN_CONTEXT_KEYS = (
    "event_links",
    "event_threads",
    "state_links",
)
WRITE_MEMORY_ABOUT_TIME_KEYS = (
    "about_start_ts",
    "about_end_ts",
    "about_year_start",
    "about_year_end",
    "life_stage",
    "about_time_confidence",
)
WRITE_MEMORY_EVENT_ENTITY_KEYS = (
    "entity_type_norm",
    "entity_name_raw",
    "confidence",
)
WRITE_MEMORY_STATE_UPDATE_OPERATIONS = (
    "upsert",
    "close",
    "mark_done",
    "revise_confidence",
)
WRITE_MEMORY_PREFERENCE_OWNER_SCOPES = (
    "self",
    "other_entity",
)
WRITE_MEMORY_PREFERENCE_POLARITIES = (
    "like",
    "dislike",
)
WRITE_MEMORY_PREFERENCE_STATUSES = (
    "candidate",
    "confirmed",
    "revoked",
)
WRITE_MEMORY_EVENT_LINK_LABELS = (
    "reply_to",
    "same_topic",
    "caused_by",
    "continuation",
)
WRITE_MEMORY_STATE_LINK_LABELS = (
    "relates_to",
    "derived_from",
    "supports",
    "contradicts",
)
WRITE_MEMORY_VAD_KEYS = (
    "v",
    "a",
    "d",
)
WRITE_MEMORY_ACTION_TYPE_ALIASES = {
    "enqueue_browse_task": "browse",
    "complete_browse_task": "browse",
    "control_camera_look": "look",
    "dispatch_notice": "notify",
    "emit_chat_response": "speak",
    "speak_ui_message": "speak",
}
LONG_MOOD_BASELINE_LERP = 0.12
LONG_MOOD_SHOCK_BLEND = 0.65
LONG_MOOD_SHOCK_HALF_LIFE_MS = 45 * 60 * 1000
LONG_MOOD_MAX_DELTA_PER_CYCLE = 0.24
LONG_MOOD_LABEL_PROTOTYPES = {
    "calm": {"v": 0.10, "a": 0.02, "d": 0.10},
    "curious": {"v": 0.18, "a": 0.26, "d": 0.10},
    "warm": {"v": 0.24, "a": 0.08, "d": 0.14},
    "guarded": {"v": -0.12, "a": 0.18, "d": -0.08},
    "tense": {"v": -0.28, "a": 0.34, "d": -0.18},
    "frustrated": {"v": -0.40, "a": 0.42, "d": -0.28},
}
DIALOGUE_CONTINUATION_CUES = (
    "それ",
    "その",
    "続き",
    "もう一度",
    "また",
    "同じ",
    "再検索",
    "やり直",
    "さっき",
    "前の",
    "この件",
)
EXPLICIT_LIKE_PREFERENCE_SUFFIXES = (
    "が好きです",
    "は好きです",
    "が好き",
    "は好き",
    "を優先して",
    "中心で",
    "メインで",
)
EXPLICIT_DISLIKE_PREFERENCE_SUFFIXES = (
    "が嫌いです",
    "は嫌いです",
    "が嫌い",
    "は嫌い",
    "が苦手です",
    "は苦手です",
    "が苦手",
    "は苦手",
    "を避けたい",
    "は避けたい",
    "は避けて",
    "をやめて",
)


# Block: Payload validation
def validate_write_memory_payload(payload: Any) -> dict[str, Any]:
    normalized_payload = _required_object(
        payload,
        "write_memory payload must be an object",
    )
    if normalized_payload.get("job_kind") != "write_memory":
        raise RuntimeError("write_memory payload.job_kind must be write_memory")
    cycle_id = _required_non_empty_string(
        normalized_payload.get("cycle_id"),
        "write_memory payload.cycle_id must be non-empty string",
    )
    source_event_ids = _required_non_empty_string_list(
        normalized_payload.get("source_event_ids"),
        "write_memory payload.source_event_ids must be non-empty string array",
    )
    primary_event_id = _required_non_empty_string(
        normalized_payload.get("primary_event_id"),
        "write_memory payload.primary_event_id must be non-empty string",
    )
    if primary_event_id not in source_event_ids:
        raise RuntimeError("write_memory payload.primary_event_id must exist in source_event_ids")
    reflection_seed_ref = _required_object(
        normalized_payload.get("reflection_seed_ref"),
        "write_memory payload.reflection_seed_ref must be an object",
    )
    normalized_reflection_seed_ref = {
        "ref_kind": _required_non_empty_string(
            reflection_seed_ref.get("ref_kind"),
            "write_memory payload.reflection_seed_ref.ref_kind must be non-empty string",
        ),
        "ref_id": _required_non_empty_string(
            reflection_seed_ref.get("ref_id"),
            "write_memory payload.reflection_seed_ref.ref_id must be non-empty string",
        ),
    }
    event_snapshot_refs = _required_list(
        normalized_payload.get("event_snapshot_refs"),
        "write_memory payload.event_snapshot_refs must be a non-empty array",
    )
    if not event_snapshot_refs:
        raise RuntimeError("write_memory payload.event_snapshot_refs must not be empty")
    normalized_event_snapshot_refs: list[dict[str, Any]] = []
    snapshot_event_ids: list[str] = []
    for event_snapshot_ref in event_snapshot_refs:
        snapshot_entry = _required_object(
            event_snapshot_ref,
            "write_memory payload.event_snapshot_refs entries must be objects",
        )
        event_id = _required_non_empty_string(
            snapshot_entry.get("event_id"),
            "write_memory payload.event_snapshot_refs.event_id must be non-empty string",
        )
        if event_id in snapshot_event_ids:
            raise RuntimeError("write_memory payload.event_snapshot_refs contains duplicate event_id")
        snapshot_event_ids.append(event_id)
        normalized_event_snapshot_refs.append(
            {
                "event_id": event_id,
                "event_updated_at": _required_integer(
                    snapshot_entry.get("event_updated_at"),
                    "write_memory payload.event_snapshot_refs.event_updated_at must be integer",
                ),
            }
        )
    if snapshot_event_ids != source_event_ids:
        raise RuntimeError("write_memory payload.event_snapshot_refs must match source_event_ids order")
    return {
        "job_kind": "write_memory",
        "cycle_id": cycle_id,
        "source_event_ids": source_event_ids,
        "created_at": _required_integer(
            normalized_payload.get("created_at"),
            "write_memory payload.created_at must be integer",
        ),
        "idempotency_key": _required_non_empty_string(
            normalized_payload.get("idempotency_key"),
            "write_memory payload.idempotency_key must be non-empty string",
        ),
        "primary_event_id": primary_event_id,
        "reflection_seed_ref": normalized_reflection_seed_ref,
        "event_snapshot_refs": normalized_event_snapshot_refs,
    }


# Block: Event snapshot validation
def validate_write_memory_event_snapshots(
    *,
    payload: dict[str, Any],
    event_entries: list[dict[str, Any]],
) -> None:
    if len(event_entries) != len(payload["event_snapshot_refs"]):
        raise RuntimeError("write_memory event entries must match event_snapshot_refs count")
    event_entries_by_id = {
        str(event_entry["event_id"]): event_entry
        for event_entry in event_entries
    }
    for event_snapshot_ref in payload["event_snapshot_refs"]:
        event_id = str(event_snapshot_ref["event_id"])
        event_entry = event_entries_by_id.get(event_id)
        if event_entry is None:
            raise RuntimeError("write_memory event snapshot target is missing")
        current_updated_at = _required_integer(
            event_entry.get("source_updated_at"),
            "write_memory event entry.source_updated_at must be integer",
        )
        if current_updated_at != int(event_snapshot_ref["event_updated_at"]):
            raise RuntimeError("write_memory event_snapshot_refs is stale")


# Block: Plan generation
def build_write_memory_plan(
    *,
    source_job_id: str,
    payload: dict[str, Any],
    event_entries: list[dict[str, Any]],
    action_entries: list[dict[str, Any]],
    browse_fact_entries: list[dict[str, Any]],
    current_emotion: dict[str, Any],
    existing_long_mood_state: dict[str, Any] | None,
    existing_preference_entries: list[dict[str, Any]],
    recent_dialogue_context: list[dict[str, Any]],
    applied_at: int,
) -> dict[str, Any]:
    cycle_id = str(payload["cycle_id"])
    primary_event_id = str(payload["primary_event_id"])
    source_event_ids = list(payload["source_event_ids"])
    cycle_thread_key = _cycle_thread_key(cycle_id)
    dialogue_thread_key = _dialogue_thread_key(
        cycle_id=cycle_id,
        event_entries=event_entries,
        recent_dialogue_context=recent_dialogue_context,
    )
    summary_state_ref = "summary_primary"
    event_affect_updates = [
        _build_event_affect_update(
            event_entry=event_entry,
            primary_event_id=primary_event_id,
        )
        for event_entry in event_entries
    ]
    state_updates = [
        {
            "state_ref": summary_state_ref,
            "operation": "upsert",
            "memory_kind": "summary",
            "body_text": _build_summary_memory_body_text(
                primary_event_id=primary_event_id,
                event_entries=event_entries,
            ),
            "payload": {
                "source_job_id": source_job_id,
                "job_kind": "write_memory",
                "source_cycle_id": cycle_id,
                "primary_event_id": primary_event_id,
                "source_event_ids": source_event_ids,
                "summary_kind": "minimal_write_memory",
            },
            "confidence": 0.50,
            "importance": 0.50,
            "memory_strength": 0.50,
            "last_confirmed_at": applied_at,
            "evidence_event_ids": source_event_ids,
            "revision_reason": "write_memory created summary",
        }
    ]
    state_links: list[dict[str, Any]] = []
    for index, browse_fact_entry in enumerate(browse_fact_entries, start=1):
        state_ref = f"fact_external_{index}"
        state_updates.append(
            {
                "state_ref": state_ref,
                "operation": "upsert",
                "memory_kind": "fact",
                "body_text": (
                    f"外部確認: {browse_fact_entry['query']} => {browse_fact_entry['summary_text']}"
                ),
                "payload": {
                    "source_job_id": source_job_id,
                    "job_kind": "write_memory",
                    "source_cycle_id": cycle_id,
                    "source_event_ids": source_event_ids,
                    "fact_kind": "external_search_result",
                    "query": str(browse_fact_entry["query"]),
                    "summary_text": str(browse_fact_entry["summary_text"]),
                    "source_task_id": str(browse_fact_entry["source_task_id"]),
                },
                "confidence": 0.85,
                "importance": 0.75,
                "memory_strength": 0.75,
                "last_confirmed_at": applied_at,
                "evidence_event_ids": source_event_ids,
                "revision_reason": "write_memory created external fact",
            }
        )
        state_links.append(
            {
                "from_state_ref": state_ref,
                "to_state_ref": summary_state_ref,
                "label": "supports",
                "confidence": 0.72,
                "evidence_event_ids": source_event_ids,
                "revision_reason": "write_memory linked external fact to summary",
            }
        )
    reflection_state_update = _build_reflection_state_update(
        source_job_id=source_job_id,
        payload=payload,
        event_entries=event_entries,
        action_entries=action_entries,
        applied_at=applied_at,
    )
    if reflection_state_update is not None:
        state_updates.append(reflection_state_update)
        state_links.append(
            {
                "from_state_ref": str(reflection_state_update["state_ref"]),
                "to_state_ref": summary_state_ref,
                "label": "derived_from",
                "confidence": 0.70,
                "evidence_event_ids": source_event_ids,
                "revision_reason": "write_memory linked reflection note to summary",
            }
        )
    long_mood_state_update = _build_long_mood_state_update(
        source_job_id=source_job_id,
        payload=payload,
        current_emotion=current_emotion,
        existing_long_mood_state=existing_long_mood_state,
        event_affect_updates=event_affect_updates,
        applied_at=applied_at,
    )
    if long_mood_state_update is not None:
        state_updates.append(long_mood_state_update)
        state_links.append(
            {
                "from_state_ref": str(long_mood_state_update["state_ref"]),
                "to_state_ref": summary_state_ref,
                "label": "derived_from",
                "confidence": 0.66,
                "evidence_event_ids": source_event_ids,
                "revision_reason": "write_memory linked long mood state to summary",
            }
        )
    return {
        "event_annotations": _build_event_annotations(
            event_entries=event_entries,
            cycle_thread_key=cycle_thread_key,
            dialogue_thread_key=dialogue_thread_key,
        ),
        "state_updates": state_updates,
        "preference_updates": _build_preference_updates(
            event_entries=event_entries,
            action_entries=action_entries,
            existing_preference_entries=existing_preference_entries,
            source_event_ids=source_event_ids,
        ),
        "event_affect": event_affect_updates,
        "context_updates": {
            "event_links": _build_event_links(
                event_entries=event_entries,
                primary_event_id=primary_event_id,
                recent_dialogue_context=recent_dialogue_context,
                source_event_ids=source_event_ids,
            ),
            "event_threads": _build_event_threads(
                event_entries=event_entries,
                cycle_thread_key=cycle_thread_key,
                dialogue_thread_key=dialogue_thread_key,
                primary_event_id=primary_event_id,
                source_event_ids=source_event_ids,
            ),
            "state_links": state_links,
        },
        "revision_reasons": [
            str(state_update["revision_reason"])
            for state_update in state_updates
        ],
    }


# Block: イベント注釈構築
def _build_event_annotations(
    *,
    event_entries: list[dict[str, Any]],
    cycle_thread_key: str,
    dialogue_thread_key: str | None,
) -> list[dict[str, Any]]:
    return [
        {
            "event_id": str(event_entry["event_id"]),
            "about_time": _build_event_about_time(event_entry=event_entry),
            "entities": _build_event_entities(event_entry=event_entry),
            "thread_hints": _thread_hints(
                cycle_thread_key=cycle_thread_key,
                dialogue_thread_key=dialogue_thread_key,
            ),
        }
        for event_entry in event_entries
    ]


def _build_event_entities(*, event_entry: dict[str, Any]) -> list[dict[str, Any]]:
    summary_text = str(event_entry["summary_text"]).strip()
    if not summary_text:
        return []
    entries: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()
    if summary_text.startswith("chat_message:"):
        _append_event_entity(
            entries=entries,
            seen_keys=seen_keys,
            entity_type_norm="utterance_excerpt",
            entity_name_raw=summary_text.removeprefix("chat_message:"),
            confidence=0.66,
        )
    elif summary_text.startswith("microphone_message:"):
        _append_event_entity(
            entries=entries,
            seen_keys=seen_keys,
            entity_type_norm="utterance_excerpt",
            entity_name_raw=summary_text.removeprefix("microphone_message:"),
            confidence=0.68,
        )
    elif summary_text.startswith("network_result:"):
        network_result_parts = summary_text.split(":", 2)
        if len(network_result_parts) == 3:
            _append_event_entity(
                entries=entries,
                seen_keys=seen_keys,
                entity_type_norm="topic",
                entity_name_raw=network_result_parts[1],
                confidence=0.84,
            )
            _append_event_entity(
                entries=entries,
                seen_keys=seen_keys,
                entity_type_norm="summary_phrase",
                entity_name_raw=network_result_parts[2],
                confidence=0.60,
            )
    kind = str(event_entry["kind"])
    if kind == "action":
        _append_event_entity(
            entries=entries,
            seen_keys=seen_keys,
            entity_type_norm="action_type",
            entity_name_raw=summary_text.split(" -> ", 1)[0],
            confidence=0.74,
        )
    elif kind == "action_result":
        action_result_parts = summary_text.split(" ", 2)
        _append_event_entity(
            entries=entries,
            seen_keys=seen_keys,
            entity_type_norm="action_type",
            entity_name_raw=action_result_parts[0],
            confidence=0.72,
        )
        if len(action_result_parts) >= 2:
            _append_event_entity(
                entries=entries,
                seen_keys=seen_keys,
                entity_type_norm="result_status",
                entity_name_raw=action_result_parts[1].rstrip(":"),
                confidence=0.56,
            )
        if ":" in summary_text:
            _append_event_entity(
                entries=entries,
                seen_keys=seen_keys,
                entity_type_norm="failure_mode",
                entity_name_raw=summary_text.split(":", 1)[1],
                confidence=0.52,
            )
    elif kind == "external_response":
        _append_event_entity(
            entries=entries,
            seen_keys=seen_keys,
            entity_type_norm="summary_phrase",
            entity_name_raw=summary_text,
            confidence=0.40,
        )
    return entries


def _build_event_about_time(*, event_entry: dict[str, Any]) -> dict[str, Any] | None:
    summary_text = str(event_entry["summary_text"]).strip()
    if not summary_text:
        return None
    about_years = about_years_from_text(summary_text)
    life_stage = life_stage_from_text(summary_text)
    if not about_years and life_stage is None:
        return None
    return {
        "about_start_ts": None,
        "about_end_ts": None,
        "about_year_start": about_years[0] if about_years else None,
        "about_year_end": about_years[-1] if about_years else None,
        "life_stage": life_stage,
        "about_time_confidence": 0.82 if about_years else 0.58,
    }


def _append_event_entity(
    *,
    entries: list[dict[str, Any]],
    seen_keys: set[tuple[str, str]],
    entity_type_norm: str,
    entity_name_raw: str,
    confidence: float,
) -> None:
    normalized_name = _normalize_event_entity_name(entity_name_raw)
    if not normalized_name:
        return
    entity_key = (entity_type_norm, normalized_name)
    if entity_key in seen_keys:
        return
    seen_keys.add(entity_key)
    entries.append(
        {
            "entity_type_norm": entity_type_norm,
            "entity_name_raw": entity_name_raw.strip()[:120],
            "confidence": confidence,
        }
    )


def _normalize_event_entity_name(text: str) -> str:
    return "".join(text.strip().lower().split())


# Block: Plan validation
def validate_write_memory_plan(
    *,
    plan: Any,
    payload: dict[str, Any],
) -> dict[str, Any]:
    normalized_plan = _required_object(
        plan,
        "write_memory plan must be an object",
    )
    if tuple(normalized_plan.keys()) != WRITE_MEMORY_PLAN_KEYS:
        raise RuntimeError("write_memory plan keys must match fixed shape")
    source_event_ids = list(payload["source_event_ids"])
    event_annotations = _validate_event_annotations(
        event_annotations=normalized_plan.get("event_annotations"),
        source_event_ids=source_event_ids,
    )
    state_updates = _required_list(
        normalized_plan.get("state_updates"),
        "write_memory plan.state_updates must be a list",
    )
    if not state_updates:
        raise RuntimeError("write_memory plan.state_updates must not be empty")
    normalized_state_updates: list[dict[str, Any]] = []
    known_state_refs: list[str] = []
    known_target_state_ids: set[str] = set()
    for state_update in state_updates:
        normalized_state_update = _validate_state_update(
            state_update=state_update,
            source_event_ids=source_event_ids,
        )
        state_ref = str(normalized_state_update["state_ref"])
        if state_ref in known_state_refs:
            raise RuntimeError("write_memory plan.state_updates.state_ref must be unique")
        known_state_refs.append(state_ref)
        target_state_id = normalized_state_update.get("target_state_id")
        if isinstance(target_state_id, str):
            if target_state_id in known_target_state_ids:
                raise RuntimeError("write_memory plan.state_updates.target_state_id must be unique")
            known_target_state_ids.add(target_state_id)
        normalized_state_updates.append(normalized_state_update)
    normalized_preference_updates = _validate_preference_updates(
        preference_updates=normalized_plan.get("preference_updates"),
        source_event_ids=source_event_ids,
    )
    normalized_event_affect = _validate_event_affect_updates(
        event_affect_updates=normalized_plan.get("event_affect"),
        source_event_ids=source_event_ids,
    )
    normalized_context_updates = _validate_context_updates(
        context_updates=normalized_plan.get("context_updates"),
        source_event_ids=source_event_ids,
        known_state_refs=known_state_refs,
    )
    revision_reasons = _required_list(
        normalized_plan.get("revision_reasons"),
        "write_memory plan.revision_reasons must be a list",
    )
    if len(revision_reasons) != len(normalized_state_updates):
        raise RuntimeError("write_memory plan.revision_reasons must match state_updates count")
    normalized_revision_reasons = [
        _required_non_empty_string(
            reason,
            "write_memory plan.revision_reasons entries must be non-empty strings",
        )
        for reason in revision_reasons
    ]
    if normalized_revision_reasons != [
        str(state_update["revision_reason"])
        for state_update in normalized_state_updates
    ]:
        raise RuntimeError("write_memory plan.revision_reasons must match state_updates revision_reason")
    return {
        "event_annotations": event_annotations,
        "state_updates": normalized_state_updates,
        "preference_updates": normalized_preference_updates,
        "event_affect": normalized_event_affect,
        "context_updates": normalized_context_updates,
        "revision_reasons": normalized_revision_reasons,
    }


# Block: Event annotation validation
def _validate_event_annotations(
    *,
    event_annotations: Any,
    source_event_ids: list[str],
) -> list[dict[str, Any]]:
    normalized_event_annotations = _required_list(
        event_annotations,
        "write_memory plan.event_annotations must be a list",
    )
    if len(normalized_event_annotations) != len(source_event_ids):
        raise RuntimeError("write_memory plan.event_annotations must match source_event_ids count")
    normalized_entries: list[dict[str, Any]] = []
    for event_annotation, expected_event_id in zip(
        normalized_event_annotations,
        source_event_ids,
        strict=True,
    ):
        annotation_entry = _required_object(
            event_annotation,
            "write_memory plan.event_annotations entries must be objects",
        )
        event_id = _required_non_empty_string(
            annotation_entry.get("event_id"),
            "write_memory plan.event_annotations.event_id must be non-empty string",
        )
        if event_id != expected_event_id:
            raise RuntimeError("write_memory plan.event_annotations order must match source_event_ids")
        if "about_time" not in annotation_entry:
            raise RuntimeError("write_memory plan.event_annotations.about_time must exist")
        entities = _required_list(
            annotation_entry.get("entities"),
            "write_memory plan.event_annotations.entities must be a list",
        )
        normalized_entities: list[dict[str, Any]] = []
        seen_entity_keys: set[tuple[str, str]] = set()
        for entity in entities:
            normalized_entity = _validate_event_entity(
                entity=entity,
            )
            entity_key = (
                str(normalized_entity["entity_type_norm"]),
                _normalize_event_entity_name(str(normalized_entity["entity_name_raw"])),
            )
            if entity_key in seen_entity_keys:
                raise RuntimeError("write_memory plan.event_annotations.entities must not contain duplicates")
            seen_entity_keys.add(entity_key)
            normalized_entities.append(normalized_entity)
        thread_hints = _required_string_list(
            annotation_entry.get("thread_hints"),
            "write_memory plan.event_annotations.thread_hints must be a list of non-empty strings",
        )
        normalized_entries.append(
            {
                "event_id": event_id,
                "about_time": _validate_about_time(
                    value=annotation_entry.get("about_time"),
                ),
                "entities": normalized_entities,
                "thread_hints": thread_hints,
            }
        )
    return normalized_entries


def _validate_about_time(*, value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    normalized_about_time = _required_object(
        value,
        "write_memory plan.event_annotations.about_time must be object or null",
    )
    if tuple(normalized_about_time.keys()) != WRITE_MEMORY_ABOUT_TIME_KEYS:
        raise RuntimeError("write_memory plan.event_annotations.about_time keys must match fixed shape")
    about_start_ts = _optional_positive_integer(
        normalized_about_time.get("about_start_ts"),
        "write_memory plan.event_annotations.about_time.about_start_ts must be positive integer when present",
    )
    about_end_ts = _optional_positive_integer(
        normalized_about_time.get("about_end_ts"),
        "write_memory plan.event_annotations.about_time.about_end_ts must be positive integer when present",
    )
    about_year_start = _optional_year_integer(
        normalized_about_time.get("about_year_start"),
        "write_memory plan.event_annotations.about_time.about_year_start must be year integer when present",
    )
    about_year_end = _optional_year_integer(
        normalized_about_time.get("about_year_end"),
        "write_memory plan.event_annotations.about_time.about_year_end must be year integer when present",
    )
    if about_year_start is not None and about_year_end is not None and about_year_start > about_year_end:
        raise RuntimeError("write_memory plan.event_annotations.about_time about_year range is invalid")
    life_stage = _optional_non_empty_string(
        normalized_about_time.get("life_stage"),
        "write_memory plan.event_annotations.about_time.life_stage must be non-empty string when present",
    )
    if (
        about_start_ts is None
        and about_end_ts is None
        and about_year_start is None
        and about_year_end is None
        and life_stage is None
    ):
        raise RuntimeError("write_memory plan.event_annotations.about_time must contain at least one hint")
    return {
        "about_start_ts": about_start_ts,
        "about_end_ts": about_end_ts,
        "about_year_start": about_year_start,
        "about_year_end": about_year_end,
        "life_stage": life_stage,
        "about_time_confidence": _required_score(
            normalized_about_time.get("about_time_confidence"),
            "write_memory plan.event_annotations.about_time.about_time_confidence must be numeric within 0.0..1.0",
        ),
    }


def _validate_event_entity(*, entity: Any) -> dict[str, Any]:
    normalized_entity = _required_object(
        entity,
        "write_memory plan.event_annotations.entities entries must be objects",
    )
    if tuple(normalized_entity.keys()) != WRITE_MEMORY_EVENT_ENTITY_KEYS:
        raise RuntimeError("write_memory plan.event_annotations.entities keys must match fixed shape")
    return {
        "entity_type_norm": _required_non_empty_string(
            normalized_entity.get("entity_type_norm"),
            "write_memory plan.event_annotations.entities.entity_type_norm must be non-empty string",
        ),
        "entity_name_raw": _required_non_empty_string(
            normalized_entity.get("entity_name_raw"),
            "write_memory plan.event_annotations.entities.entity_name_raw must be non-empty string",
        ),
        "confidence": _required_score(
            normalized_entity.get("confidence"),
            "write_memory plan.event_annotations.entities.confidence must be numeric within 0.0..1.0",
        ),
    }


# Block: State update validation
def _validate_state_update(
    *,
    state_update: Any,
    source_event_ids: list[str],
) -> dict[str, Any]:
    normalized_state_update = _required_object(
        state_update,
        "write_memory plan.state_updates entries must be objects",
    )
    operation = _required_non_empty_string(
        normalized_state_update.get("operation"),
        "write_memory plan.state_updates.operation must be non-empty string",
    )
    if operation not in WRITE_MEMORY_STATE_UPDATE_OPERATIONS:
        raise RuntimeError("write_memory plan.state_updates.operation is invalid")
    evidence_event_ids = _validate_evidence_event_ids(
        value=normalized_state_update.get("evidence_event_ids"),
        source_event_ids=source_event_ids,
        field_name="write_memory plan.state_updates.evidence_event_ids",
    )
    normalized_common_fields = {
        "state_ref": _required_non_empty_string(
            normalized_state_update.get("state_ref"),
            "write_memory plan.state_updates.state_ref must be non-empty string",
        ),
        "operation": operation,
        "evidence_event_ids": evidence_event_ids,
        "revision_reason": _required_non_empty_string(
            normalized_state_update.get("revision_reason"),
            "write_memory plan.state_updates.revision_reason must be non-empty string",
        ),
    }
    if operation == "upsert":
        return {
            **normalized_common_fields,
            "memory_kind": _required_non_empty_string(
                normalized_state_update.get("memory_kind"),
                "write_memory plan.state_updates.memory_kind must be non-empty string",
            ),
            "body_text": _required_non_empty_string(
                normalized_state_update.get("body_text"),
                "write_memory plan.state_updates.body_text must be non-empty string",
            ),
            "payload": _required_object(
                normalized_state_update.get("payload"),
                "write_memory plan.state_updates.payload must be an object",
            ),
            "confidence": _required_score(
                normalized_state_update.get("confidence"),
                "write_memory plan.state_updates.confidence must be numeric within 0.0..1.0",
            ),
            "importance": _required_score(
                normalized_state_update.get("importance"),
                "write_memory plan.state_updates.importance must be numeric within 0.0..1.0",
            ),
            "memory_strength": _required_score(
                normalized_state_update.get("memory_strength"),
                "write_memory plan.state_updates.memory_strength must be numeric within 0.0..1.0",
            ),
            "last_confirmed_at": _required_positive_integer(
                normalized_state_update.get("last_confirmed_at"),
                "write_memory plan.state_updates.last_confirmed_at must be positive integer",
            ),
        }
    target_state_id = _required_non_empty_string(
        normalized_state_update.get("target_state_id"),
        "write_memory plan.state_updates.target_state_id must be non-empty string",
    )
    memory_kind = _required_non_empty_string(
        normalized_state_update.get("memory_kind"),
        "write_memory plan.state_updates.memory_kind must be non-empty string",
    )
    if operation == "close":
        return {
            **normalized_common_fields,
            "target_state_id": target_state_id,
            "memory_kind": memory_kind,
            "valid_to_ts": _required_positive_integer(
                normalized_state_update.get("valid_to_ts"),
                "write_memory plan.state_updates.valid_to_ts must be positive integer",
            ),
        }
    if operation == "mark_done":
        if memory_kind != "task":
            raise RuntimeError("write_memory plan.state_updates.mark_done requires memory_kind=task")
        return {
            **normalized_common_fields,
            "target_state_id": target_state_id,
            "memory_kind": memory_kind,
            "done_at": _required_positive_integer(
                normalized_state_update.get("done_at"),
                "write_memory plan.state_updates.done_at must be positive integer",
            ),
            "done_reason": _required_non_empty_string(
                normalized_state_update.get("done_reason"),
                "write_memory plan.state_updates.done_reason must be non-empty string",
            ),
        }
    return {
        **normalized_common_fields,
        "target_state_id": target_state_id,
        "operation": operation,
        "memory_kind": memory_kind,
        "confidence": _required_score(
            normalized_state_update.get("confidence"),
            "write_memory plan.state_updates.confidence must be numeric within 0.0..1.0",
        ),
        "importance": _required_score(
            normalized_state_update.get("importance"),
            "write_memory plan.state_updates.importance must be numeric within 0.0..1.0",
        ),
        "memory_strength": _required_score(
            normalized_state_update.get("memory_strength"),
            "write_memory plan.state_updates.memory_strength must be numeric within 0.0..1.0",
        ),
        "last_confirmed_at": _required_positive_integer(
            normalized_state_update.get("last_confirmed_at"),
            "write_memory plan.state_updates.last_confirmed_at must be positive integer",
        ),
    }


# Block: Preference update validation
def _validate_preference_updates(
    *,
    preference_updates: Any,
    source_event_ids: list[str],
) -> list[dict[str, Any]]:
    normalized_preference_updates = _required_list(
        preference_updates,
        "write_memory plan.preference_updates must be a list",
    )
    normalized_entries: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str, str, str, str]] = set()
    for preference_update in normalized_preference_updates:
        preference_entry = _required_object(
            preference_update,
            "write_memory plan.preference_updates entries must be objects",
        )
        target_entity_ref = _required_object(
            preference_entry.get("target_entity_ref"),
            "write_memory plan.preference_updates.target_entity_ref must be an object",
        )
        target_kind = _required_non_empty_string(
            target_entity_ref.get("target_kind"),
            "write_memory plan.preference_updates.target_entity_ref.target_kind must be non-empty string",
        )
        target_key = _required_non_empty_string(
            target_entity_ref.get("target_key"),
            "write_memory plan.preference_updates.target_entity_ref.target_key must be non-empty string",
        )
        owner_scope = _required_non_empty_string(
            preference_entry.get("owner_scope"),
            "write_memory plan.preference_updates.owner_scope must be non-empty string",
        )
        if owner_scope not in WRITE_MEMORY_PREFERENCE_OWNER_SCOPES:
            raise RuntimeError("write_memory plan.preference_updates.owner_scope is invalid")
        polarity = _required_non_empty_string(
            preference_entry.get("polarity"),
            "write_memory plan.preference_updates.polarity must be non-empty string",
        )
        if polarity not in WRITE_MEMORY_PREFERENCE_POLARITIES:
            raise RuntimeError("write_memory plan.preference_updates.polarity is invalid")
        status = _required_non_empty_string(
            preference_entry.get("status"),
            "write_memory plan.preference_updates.status must be non-empty string",
        )
        if status not in WRITE_MEMORY_PREFERENCE_STATUSES:
            raise RuntimeError("write_memory plan.preference_updates.status is invalid")
        domain = _required_non_empty_string(
            preference_entry.get("domain"),
            "write_memory plan.preference_updates.domain must be non-empty string",
        )
        preference_key = (
            owner_scope,
            domain,
            polarity,
            status,
            target_key,
        )
        if preference_key in seen_keys:
            raise RuntimeError("write_memory plan.preference_updates must not contain duplicate target updates")
        seen_keys.add(preference_key)
        normalized_entries.append(
            {
                "owner_scope": owner_scope,
                "target_entity_ref": {
                    "target_kind": target_kind,
                    "target_key": target_key,
                },
                "domain": domain,
                "polarity": polarity,
                "status": status,
                "confidence": _required_score(
                    preference_entry.get("confidence"),
                    "write_memory plan.preference_updates.confidence must be numeric within 0.0..1.0",
                ),
                "evidence_event_ids": _validate_evidence_event_ids(
                    value=preference_entry.get("evidence_event_ids"),
                    source_event_ids=source_event_ids,
                    field_name="write_memory plan.preference_updates.evidence_event_ids",
                ),
                "revision_reason": _required_non_empty_string(
                    preference_entry.get("revision_reason"),
                    "write_memory plan.preference_updates.revision_reason must be non-empty string",
                ),
            }
        )
    return normalized_entries


# Block: Event affect validation
def _validate_event_affect_updates(
    *,
    event_affect_updates: Any,
    source_event_ids: list[str],
) -> list[dict[str, Any]]:
    normalized_event_affect_updates = _required_list(
        event_affect_updates,
        "write_memory plan.event_affect must be a list",
    )
    normalized_entries: list[dict[str, Any]] = []
    seen_event_ids: set[str] = set()
    for event_affect_update in normalized_event_affect_updates:
        affect_entry = _required_object(
            event_affect_update,
            "write_memory plan.event_affect entries must be objects",
        )
        event_id = _required_non_empty_string(
            affect_entry.get("event_id"),
            "write_memory plan.event_affect.event_id must be non-empty string",
        )
        if event_id not in source_event_ids:
            raise RuntimeError("write_memory plan.event_affect.event_id must exist in source_event_ids")
        if event_id in seen_event_ids:
            raise RuntimeError("write_memory plan.event_affect must not contain duplicate event_id")
        seen_event_ids.add(event_id)
        normalized_entries.append(
            {
                "event_id": event_id,
                "moment_affect_text": _required_non_empty_string(
                    affect_entry.get("moment_affect_text"),
                    "write_memory plan.event_affect.moment_affect_text must be non-empty string",
                ),
                "moment_affect_labels": _required_string_list(
                    affect_entry.get("moment_affect_labels"),
                    "write_memory plan.event_affect.moment_affect_labels must be a list of non-empty strings",
                ),
                "vad": _validate_vad_object(
                    affect_entry.get("vad"),
                    "write_memory plan.event_affect.vad",
                ),
                "confidence": _required_score(
                    affect_entry.get("confidence"),
                    "write_memory plan.event_affect.confidence must be numeric within 0.0..1.0",
                ),
                "evidence_event_ids": _validate_evidence_event_ids(
                    value=affect_entry.get("evidence_event_ids"),
                    source_event_ids=source_event_ids,
                    field_name="write_memory plan.event_affect.evidence_event_ids",
                ),
                "revision_reason": _required_non_empty_string(
                    affect_entry.get("revision_reason"),
                    "write_memory plan.event_affect.revision_reason must be non-empty string",
                ),
            }
        )
    return normalized_entries


# Block: Context update validation
def _validate_context_updates(
    *,
    context_updates: Any,
    source_event_ids: list[str],
    known_state_refs: list[str],
) -> dict[str, list[dict[str, Any]]]:
    normalized_context_updates = _required_object(
        context_updates,
        "write_memory plan.context_updates must be an object",
    )
    if tuple(normalized_context_updates.keys()) != WRITE_MEMORY_PLAN_CONTEXT_KEYS:
        raise RuntimeError("write_memory plan.context_updates keys must match fixed shape")
    return {
        "event_links": _validate_event_links(
            event_links=normalized_context_updates.get("event_links"),
            source_event_ids=source_event_ids,
        ),
        "event_threads": _validate_event_threads(
            event_threads=normalized_context_updates.get("event_threads"),
            source_event_ids=source_event_ids,
        ),
        "state_links": _validate_state_links(
            state_links=normalized_context_updates.get("state_links"),
            source_event_ids=source_event_ids,
            known_state_refs=known_state_refs,
        ),
    }


# Block: Event link validation
def _validate_event_links(
    *,
    event_links: Any,
    source_event_ids: list[str],
) -> list[dict[str, Any]]:
    normalized_event_links = _required_list(
        event_links,
        "write_memory plan.context_updates.event_links must be a list",
    )
    normalized_entries: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str, str]] = set()
    for event_link in normalized_event_links:
        link_entry = _required_object(
            event_link,
            "write_memory plan.context_updates.event_links entries must be objects",
        )
        from_event_id = _required_non_empty_string(
            link_entry.get("from_event_id"),
            "write_memory plan.context_updates.event_links.from_event_id must be non-empty string",
        )
        to_event_id = _required_non_empty_string(
            link_entry.get("to_event_id"),
            "write_memory plan.context_updates.event_links.to_event_id must be non-empty string",
        )
        if from_event_id not in source_event_ids or to_event_id not in source_event_ids:
            raise RuntimeError("write_memory plan.context_updates.event_links must target source_event_ids only")
        label = _required_non_empty_string(
            link_entry.get("label"),
            "write_memory plan.context_updates.event_links.label must be non-empty string",
        )
        if label not in WRITE_MEMORY_EVENT_LINK_LABELS:
            raise RuntimeError("write_memory plan.context_updates.event_links.label is invalid")
        link_key = (from_event_id, to_event_id, label)
        if link_key in seen_keys:
            raise RuntimeError("write_memory plan.context_updates.event_links must not contain duplicates")
        seen_keys.add(link_key)
        normalized_entries.append(
            {
                "from_event_id": from_event_id,
                "to_event_id": to_event_id,
                "label": label,
                "confidence": _required_score(
                    link_entry.get("confidence"),
                    "write_memory plan.context_updates.event_links.confidence must be numeric within 0.0..1.0",
                ),
                "evidence_event_ids": _validate_evidence_event_ids(
                    value=link_entry.get("evidence_event_ids"),
                    source_event_ids=source_event_ids,
                    field_name="write_memory plan.context_updates.event_links.evidence_event_ids",
                ),
                "revision_reason": _required_non_empty_string(
                    link_entry.get("revision_reason"),
                    "write_memory plan.context_updates.event_links.revision_reason must be non-empty string",
                ),
            }
        )
    return normalized_entries


# Block: Event thread validation
def _validate_event_threads(
    *,
    event_threads: Any,
    source_event_ids: list[str],
) -> list[dict[str, Any]]:
    normalized_event_threads = _required_list(
        event_threads,
        "write_memory plan.context_updates.event_threads must be a list",
    )
    normalized_entries: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()
    for event_thread in normalized_event_threads:
        thread_entry = _required_object(
            event_thread,
            "write_memory plan.context_updates.event_threads entries must be objects",
        )
        event_id = _required_non_empty_string(
            thread_entry.get("event_id"),
            "write_memory plan.context_updates.event_threads.event_id must be non-empty string",
        )
        if event_id not in source_event_ids:
            raise RuntimeError("write_memory plan.context_updates.event_threads.event_id must exist in source_event_ids")
        thread_key = _required_non_empty_string(
            thread_entry.get("thread_key"),
            "write_memory plan.context_updates.event_threads.thread_key must be non-empty string",
        )
        event_thread_key = (event_id, thread_key)
        if event_thread_key in seen_keys:
            raise RuntimeError("write_memory plan.context_updates.event_threads must not contain duplicates")
        seen_keys.add(event_thread_key)
        thread_role = thread_entry.get("thread_role")
        if thread_role is not None and (not isinstance(thread_role, str) or not thread_role):
            raise RuntimeError("write_memory plan.context_updates.event_threads.thread_role must be non-empty string when present")
        normalized_entries.append(
            {
                "event_id": event_id,
                "thread_key": thread_key,
                "confidence": _required_score(
                    thread_entry.get("confidence"),
                    "write_memory plan.context_updates.event_threads.confidence must be numeric within 0.0..1.0",
                ),
                "thread_role": thread_role,
                "evidence_event_ids": _validate_evidence_event_ids(
                    value=thread_entry.get("evidence_event_ids"),
                    source_event_ids=source_event_ids,
                    field_name="write_memory plan.context_updates.event_threads.evidence_event_ids",
                ),
                "revision_reason": _required_non_empty_string(
                    thread_entry.get("revision_reason"),
                    "write_memory plan.context_updates.event_threads.revision_reason must be non-empty string",
                ),
            }
        )
    return normalized_entries


# Block: State link validation
def _validate_state_links(
    *,
    state_links: Any,
    source_event_ids: list[str],
    known_state_refs: list[str],
) -> list[dict[str, Any]]:
    normalized_state_links = _required_list(
        state_links,
        "write_memory plan.context_updates.state_links must be a list",
    )
    normalized_entries: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str, str]] = set()
    for state_link in normalized_state_links:
        link_entry = _required_object(
            state_link,
            "write_memory plan.context_updates.state_links entries must be objects",
        )
        from_state_ref = _required_non_empty_string(
            link_entry.get("from_state_ref"),
            "write_memory plan.context_updates.state_links.from_state_ref must be non-empty string",
        )
        to_state_ref = _required_non_empty_string(
            link_entry.get("to_state_ref"),
            "write_memory plan.context_updates.state_links.to_state_ref must be non-empty string",
        )
        if from_state_ref not in known_state_refs or to_state_ref not in known_state_refs:
            raise RuntimeError("write_memory plan.context_updates.state_links must target known state_refs")
        label = _required_non_empty_string(
            link_entry.get("label"),
            "write_memory plan.context_updates.state_links.label must be non-empty string",
        )
        if label not in WRITE_MEMORY_STATE_LINK_LABELS:
            raise RuntimeError("write_memory plan.context_updates.state_links.label is invalid")
        link_key = (from_state_ref, to_state_ref, label)
        if link_key in seen_keys:
            raise RuntimeError("write_memory plan.context_updates.state_links must not contain duplicates")
        seen_keys.add(link_key)
        normalized_entries.append(
            {
                "from_state_ref": from_state_ref,
                "to_state_ref": to_state_ref,
                "label": label,
                "confidence": _required_score(
                    link_entry.get("confidence"),
                    "write_memory plan.context_updates.state_links.confidence must be numeric within 0.0..1.0",
                ),
                "evidence_event_ids": _validate_evidence_event_ids(
                    value=link_entry.get("evidence_event_ids"),
                    source_event_ids=source_event_ids,
                    field_name="write_memory plan.context_updates.state_links.evidence_event_ids",
                ),
                "revision_reason": _required_non_empty_string(
                    link_entry.get("revision_reason"),
                    "write_memory plan.context_updates.state_links.revision_reason must be non-empty string",
                ),
            }
        )
    return normalized_entries


# Block: Reflection state update build
def _build_reflection_state_update(
    *,
    source_job_id: str,
    payload: dict[str, Any],
    event_entries: list[dict[str, Any]],
    action_entries: list[dict[str, Any]],
    applied_at: int,
) -> dict[str, Any] | None:
    event_summaries = _event_summary_texts(event_entries=event_entries)
    if not event_summaries:
        return None
    source_event_ids = list(payload["source_event_ids"])
    primary_action_entry = _primary_reflection_action_entry(action_entries=action_entries)
    what_happened = _reflection_what_happened(
        event_summaries=event_summaries,
        primary_action_entry=primary_action_entry,
    )
    what_worked = _reflection_what_worked(primary_action_entry=primary_action_entry)
    what_failed = _reflection_what_failed(primary_action_entry=primary_action_entry)
    retry_hint = _reflection_retry_hint(primary_action_entry=primary_action_entry)
    avoid_pattern = _reflection_avoid_pattern(primary_action_entry=primary_action_entry)
    body_text = _build_reflection_memory_body_text(
        what_happened=what_happened,
        what_worked=what_worked,
        what_failed=what_failed,
        retry_hint=retry_hint,
        avoid_pattern=avoid_pattern,
    )
    confidence = _reflection_confidence(primary_action_entry=primary_action_entry)
    payload_json = {
        "source_job_id": source_job_id,
        "job_kind": "write_memory",
        "source_cycle_id": str(payload["cycle_id"]),
        "primary_event_id": str(payload["primary_event_id"]),
        "source_event_ids": source_event_ids,
        "reflection_seed_ref": dict(payload["reflection_seed_ref"]),
        "what_happened": what_happened,
        "event_summaries": event_summaries,
        "action_outcomes": _reflection_action_outcomes(action_entries=action_entries),
    }
    reflection_seed = _reflection_seed(action_entries=action_entries)
    if reflection_seed is not None:
        payload_json["reflection_seed"] = reflection_seed
    if what_worked is not None:
        payload_json["what_worked"] = what_worked
    if what_failed is not None:
        payload_json["what_failed"] = what_failed
    if retry_hint is not None:
        payload_json["retry_hint"] = retry_hint
    if avoid_pattern is not None:
        payload_json["avoid_pattern"] = avoid_pattern
    return {
        "state_ref": "reflection_primary",
        "operation": "upsert",
        "memory_kind": "reflection_note",
        "body_text": body_text,
        "payload": payload_json,
        "confidence": confidence,
        "importance": round(min(0.95, confidence + 0.12), 2),
        "memory_strength": round(min(0.90, confidence + 0.08), 2),
        "last_confirmed_at": applied_at,
        "evidence_event_ids": source_event_ids,
        "revision_reason": "write_memory created reflection note",
    }


# Block: Reflection event summaries
def _event_summary_texts(*, event_entries: list[dict[str, Any]]) -> list[str]:
    summaries: list[str] = []
    for event_entry in event_entries:
        summary_text = _required_non_empty_string(
            event_entry.get("summary_text"),
            "write_memory event entry.summary_text must be non-empty string",
        )
        summaries.append(summary_text)
    return summaries


# Block: Reflection action pick
def _primary_reflection_action_entry(
    *,
    action_entries: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not action_entries:
        return None
    return action_entries[-1]


# Block: Reflection happened summary
def _reflection_what_happened(
    *,
    event_summaries: list[str],
    primary_action_entry: dict[str, Any] | None,
) -> str:
    happened_text = " / ".join(event_summaries[:3])
    if primary_action_entry is None:
        return happened_text[:600]
    action_label = _reflection_action_label(
        action_type=_required_non_empty_string(
            primary_action_entry.get("action_type"),
            "write_memory action entry.action_type must be non-empty string",
        )
    )
    status = _required_non_empty_string(
        primary_action_entry.get("status"),
        "write_memory action entry.status must be non-empty string",
    )
    return f"{happened_text} / {action_label}は{_reflection_status_text(status=status)}"[:600]


# Block: Reflection worked summary
def _reflection_what_worked(*, primary_action_entry: dict[str, Any] | None) -> str | None:
    if primary_action_entry is None:
        return None
    status = _required_non_empty_string(
        primary_action_entry.get("status"),
        "write_memory action entry.status must be non-empty string",
    )
    action_type = _required_non_empty_string(
        primary_action_entry.get("action_type"),
        "write_memory action entry.action_type must be non-empty string",
    )
    if status != "succeeded":
        return None
    if action_type in {"hold_chat_response", "reject_chat_response"}:
        return f"{_reflection_action_label(action_type=action_type)} 判断が妥当だった"
    return f"{_reflection_action_label(action_type=action_type)} を最後まで実行できた"


# Block: Reflection failed summary
def _reflection_what_failed(*, primary_action_entry: dict[str, Any] | None) -> str | None:
    if primary_action_entry is None:
        return None
    status = _required_non_empty_string(
        primary_action_entry.get("status"),
        "write_memory action entry.status must be non-empty string",
    )
    action_type = _required_non_empty_string(
        primary_action_entry.get("action_type"),
        "write_memory action entry.action_type must be non-empty string",
    )
    if action_type in {"hold_chat_response", "reject_chat_response"}:
        return None
    if status == "failed":
        failure_mode = primary_action_entry.get("failure_mode")
        if isinstance(failure_mode, str) and failure_mode:
            return f"{_reflection_action_label(action_type=action_type)} が失敗した: {failure_mode}"
        return f"{_reflection_action_label(action_type=action_type)} が失敗した"
    if status == "stopped":
        return f"{_reflection_action_label(action_type=action_type)} は途中で止まった"
    return None


# Block: Reflection retry hint
def _reflection_retry_hint(*, primary_action_entry: dict[str, Any] | None) -> str | None:
    if primary_action_entry is None:
        return None
    action_type = _required_non_empty_string(
        primary_action_entry.get("action_type"),
        "write_memory action entry.action_type must be non-empty string",
    )
    status = _required_non_empty_string(
        primary_action_entry.get("status"),
        "write_memory action entry.status must be non-empty string",
    )
    if action_type == "hold_chat_response":
        return "追加条件が揃ったときだけ再判断する"
    if action_type == "reject_chat_response":
        return "hard gate を満たす候補だけで再判断する"
    if status == "failed":
        return _failed_retry_hint(action_type=action_type)
    if status == "stopped":
        return _stopped_retry_hint(action_type=action_type)
    return None


# Block: Reflection avoid pattern
def _reflection_avoid_pattern(*, primary_action_entry: dict[str, Any] | None) -> str | None:
    if primary_action_entry is None:
        return None
    action_type = _required_non_empty_string(
        primary_action_entry.get("action_type"),
        "write_memory action entry.action_type must be non-empty string",
    )
    status = _required_non_empty_string(
        primary_action_entry.get("status"),
        "write_memory action entry.status must be non-empty string",
    )
    if action_type in {"hold_chat_response", "reject_chat_response"}:
        return "整合性の低い候補をそのまま実行しない"
    if status == "failed":
        return _failed_avoid_pattern(action_type=action_type)
    if status == "stopped":
        return "中断直後に同じ条件で出力を続けない"
    return None


# Block: Reflection memory text
def _build_reflection_memory_body_text(
    *,
    what_happened: str,
    what_worked: str | None,
    what_failed: str | None,
    retry_hint: str | None,
    avoid_pattern: str | None,
) -> str:
    reflection_parts = [what_happened]
    if what_worked is not None:
        reflection_parts.append(f"work:{what_worked}")
    if what_failed is not None:
        reflection_parts.append(f"fail:{what_failed}")
    if retry_hint is not None:
        reflection_parts.append(f"retry:{retry_hint}")
    if avoid_pattern is not None:
        reflection_parts.append(f"avoid:{avoid_pattern}")
    return " / ".join(reflection_parts)[:1000]


# Block: Reflection confidence
def _reflection_confidence(*, primary_action_entry: dict[str, Any] | None) -> float:
    if primary_action_entry is None:
        return 0.60
    action_type = _required_non_empty_string(
        primary_action_entry.get("action_type"),
        "write_memory action entry.action_type must be non-empty string",
    )
    status = _required_non_empty_string(
        primary_action_entry.get("status"),
        "write_memory action entry.status must be non-empty string",
    )
    if action_type in {"hold_chat_response", "reject_chat_response"}:
        return 0.72
    if status == "failed":
        return 0.82
    if status == "stopped":
        return 0.76
    return 0.66


# Block: Reflection action outcomes
def _reflection_action_outcomes(*, action_entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    outcomes: list[dict[str, Any]] = []
    for action_entry in action_entries:
        action_type = _required_non_empty_string(
            action_entry.get("action_type"),
            "write_memory action entry.action_type must be non-empty string",
        )
        status = _required_non_empty_string(
            action_entry.get("status"),
            "write_memory action entry.status must be non-empty string",
        )
        outcome = {
            "action_type": action_type,
            "status": status,
        }
        failure_mode = action_entry.get("failure_mode")
        if isinstance(failure_mode, str) and failure_mode:
            outcome["failure_mode"] = failure_mode
        command = action_entry.get("command")
        if isinstance(command, dict):
            decision_reason = command.get("decision_reason")
            if isinstance(decision_reason, str) and decision_reason:
                outcome["decision_reason"] = decision_reason
        observed_effects = action_entry.get("observed_effects")
        if isinstance(observed_effects, dict):
            validator_reason = observed_effects.get("validator_reason")
            if isinstance(validator_reason, str) and validator_reason:
                outcome["validator_reason"] = validator_reason
            selected_action_type = observed_effects.get("selected_action_type")
            if isinstance(selected_action_type, str) and selected_action_type:
                outcome["selected_action_type"] = selected_action_type
        outcomes.append(outcome)
    return outcomes


# Block: Reflection seed extract
def _reflection_seed(*, action_entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    for action_entry in reversed(action_entries):
        adapter_trace = action_entry.get("adapter_trace")
        if not isinstance(adapter_trace, dict):
            continue
        cognition_result = adapter_trace.get("cognition_result")
        if not isinstance(cognition_result, dict):
            continue
        reflection_seed = cognition_result.get("reflection_seed")
        if isinstance(reflection_seed, dict):
            return dict(reflection_seed)
    return None


# Block: Reflection text helpers
def _reflection_action_label(*, action_type: str) -> str:
    normalized_action_type = _normalized_action_type(action_type=action_type)
    if normalized_action_type == "browse":
        return "browse"
    if normalized_action_type == "look":
        return "look"
    if normalized_action_type == "notify":
        return "notify"
    if normalized_action_type == "speak":
        return "speak"
    if action_type == "hold_chat_response":
        return "hold"
    if action_type == "reject_chat_response":
        return "reject"
    return action_type


def _reflection_status_text(*, status: str) -> str:
    if status == "succeeded":
        return "成功した"
    if status == "failed":
        return "失敗した"
    if status == "stopped":
        return "停止した"
    raise RuntimeError("write_memory action entry.status is invalid")


def _failed_retry_hint(*, action_type: str) -> str:
    normalized_action_type = _normalized_action_type(action_type=action_type)
    if normalized_action_type == "browse":
        return "query と source_task_id を確認してから browse をやり直す"
    if normalized_action_type == "look":
        return "カメラ接続と target を確認してから look をやり直す"
    if normalized_action_type == "notify":
        return "通知先と route を確認してから notify をやり直す"
    if normalized_action_type == "speak":
        return "発話を短く整えてから speak をやり直す"
    return "条件を確認してから同系統の行動をやり直す"


def _stopped_retry_hint(*, action_type: str) -> str:
    normalized_action_type = _normalized_action_type(action_type=action_type)
    if normalized_action_type == "speak":
        return "中断理由を解消してから speak を再開する"
    return "中断理由を解消してから同系統の行動を再開する"


def _failed_avoid_pattern(*, action_type: str) -> str:
    normalized_action_type = _normalized_action_type(action_type=action_type)
    if normalized_action_type == "browse":
        return "同じ query を条件未確認のまま連打しない"
    if normalized_action_type == "look":
        return "camera unavailable のまま look を連打しない"
    if normalized_action_type == "notify":
        return "通知経路が不確かなまま notify を繰り返さない"
    if normalized_action_type == "speak":
        return "同じ長文をそのまま再送しない"
    return "失敗条件を解消せずに同じ行動を繰り返さない"


# Block: Preference update build
def _build_preference_updates(
    *,
    event_entries: list[dict[str, Any]],
    action_entries: list[dict[str, Any]],
    existing_preference_entries: list[dict[str, Any]],
    source_event_ids: list[str],
) -> list[dict[str, Any]]:
    preference_index = _existing_preference_index(
        existing_preference_entries=existing_preference_entries,
    )
    preference_updates = _build_explicit_preference_updates(
        event_entries=event_entries,
        source_event_ids=source_event_ids,
        preference_index=preference_index,
    )
    preference_updates.extend(
        _build_action_preference_updates(
            action_entries=action_entries,
            existing_preference_entries=existing_preference_entries,
            source_event_ids=source_event_ids,
        )
    )
    return preference_updates


# Block: Action preference update build
def _build_action_preference_updates(
    *,
    action_entries: list[dict[str, Any]],
    existing_preference_entries: list[dict[str, Any]],
    source_event_ids: list[str],
) -> list[dict[str, Any]]:
    aggregates: dict[tuple[str, str], dict[str, float]] = {}
    for action_entry in action_entries:
        normalized_action_type = _normalized_action_type(
            action_type=_required_non_empty_string(
                action_entry.get("action_type"),
                "write_memory action entry.action_type must be non-empty string",
            )
        )
        if normalized_action_type is None:
            continue
        status = _required_non_empty_string(
            action_entry.get("status"),
            "write_memory action entry.status must be non-empty string",
        )
        signal = _preference_signal(status)
        if signal is None:
            continue
        _accumulate_preference_signal(
            aggregates=aggregates,
            domain="action_type",
            target_key=normalized_action_type,
            signal=signal,
        )
        observation_kind = _observation_kind_for_action(normalized_action_type)
        if observation_kind is None:
            continue
        _accumulate_preference_signal(
            aggregates=aggregates,
            domain="observation_kind",
            target_key=observation_kind,
            signal=signal,
        )
    preference_index = _existing_preference_index(
        existing_preference_entries=existing_preference_entries,
    )
    preference_updates: list[dict[str, Any]] = []
    for domain, target_key in sorted(aggregates.keys()):
        aggregate = aggregates[(domain, target_key)]
        like_score = float(aggregate["like_score"])
        dislike_score = float(aggregate["dislike_score"])
        if like_score == dislike_score:
            continue
        polarity = "like" if like_score > dislike_score else "dislike"
        opposite_polarity = "dislike" if polarity == "like" else "like"
        dominant_score = like_score if polarity == "like" else dislike_score
        opposite_score = dislike_score if polarity == "like" else like_score
        existing_same_entry = preference_index.get((domain, target_key, polarity))
        existing_opposite_entry = preference_index.get((domain, target_key, opposite_polarity))
        status, confidence = _resolved_preference_status(
            dominant_score=dominant_score,
            existing_entry=existing_same_entry,
        )
        preference_updates.append(
            {
                "owner_scope": "self",
                "target_entity_ref": {
                    "target_kind": domain,
                    "target_key": target_key,
                },
                "domain": domain,
                "polarity": polarity,
                "status": status,
                "confidence": confidence,
                "evidence_event_ids": source_event_ids,
                "revision_reason": (
                    _preference_revision_reason(
                        target_key=target_key,
                        polarity=polarity,
                        status=status,
                        existing_entry=existing_same_entry,
                    )
                ),
            }
        )
        if _should_revoke_preference(
            dominant_score=dominant_score,
            opposite_score=opposite_score,
            existing_entry=existing_opposite_entry,
        ):
            preference_updates.append(
                {
                    "owner_scope": "self",
                    "target_entity_ref": {
                        "target_kind": domain,
                        "target_key": target_key,
                    },
                    "domain": domain,
                    "polarity": opposite_polarity,
                    "status": "revoked",
                    "confidence": _revoked_preference_confidence(
                        dominant_score=dominant_score,
                        existing_entry=existing_opposite_entry,
                    ),
                    "evidence_event_ids": source_event_ids,
                    "revision_reason": (
                        f"write_memory revoked {target_key} {opposite_polarity} preference"
                    ),
                }
            )
    return preference_updates


# Block: Explicit preference update build
def _build_explicit_preference_updates(
    *,
    event_entries: list[dict[str, Any]],
    source_event_ids: list[str],
    preference_index: dict[tuple[str, str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    preference_updates: list[dict[str, Any]] = []
    seen_targets: set[tuple[str, str, str]] = set()
    for preference_signal in _explicit_preference_signals(event_entries=event_entries):
        domain = str(preference_signal["domain"])
        target_key = str(preference_signal["target_key"])
        polarity = str(preference_signal["polarity"])
        signal_key = (domain, target_key, polarity)
        if signal_key in seen_targets:
            continue
        seen_targets.add(signal_key)
        existing_same_entry = preference_index.get(signal_key)
        opposite_polarity = "dislike" if polarity == "like" else "like"
        existing_opposite_entry = preference_index.get((domain, target_key, opposite_polarity))
        preference_updates.append(
            {
                "owner_scope": "self",
                "target_entity_ref": {
                    "target_kind": domain,
                    "target_key": target_key,
                },
                "domain": domain,
                "polarity": polarity,
                "status": "confirmed",
                "confidence": 0.92,
                "evidence_event_ids": source_event_ids,
                "revision_reason": _explicit_preference_revision_reason(
                    target_key=target_key,
                    polarity=polarity,
                    existing_entry=existing_same_entry,
                ),
            }
        )
        if existing_opposite_entry is None:
            continue
        existing_status = _required_non_empty_string(
            existing_opposite_entry.get("status"),
            "write_memory existing preference status must be non-empty string",
        )
        if existing_status not in {"candidate", "confirmed"}:
            continue
        preference_updates.append(
            {
                "owner_scope": "self",
                "target_entity_ref": {
                    "target_kind": domain,
                    "target_key": target_key,
                },
                "domain": domain,
                "polarity": opposite_polarity,
                "status": "revoked",
                "confidence": 0.84,
                "evidence_event_ids": source_event_ids,
                "revision_reason": (
                    f"write_memory explicit preference revoked {target_key} {opposite_polarity}"
                ),
            }
        )
    return preference_updates


# Block: Long mood state build
def _build_long_mood_state_update(
    *,
    source_job_id: str,
    payload: dict[str, Any],
    current_emotion: dict[str, Any],
    existing_long_mood_state: dict[str, Any] | None,
    event_affect_updates: list[dict[str, Any]],
    applied_at: int,
) -> dict[str, Any] | None:
    if not event_affect_updates:
        return None
    source_event_ids = list(payload["source_event_ids"])
    cycle_vad = _cycle_affect_vad(
        event_affect_updates=event_affect_updates,
        primary_event_id=str(payload["primary_event_id"]),
    )
    baseline_before = _long_mood_seed_vad(
        existing_long_mood_state=existing_long_mood_state,
        current_emotion=current_emotion,
        key="baseline",
    )
    current_before = _long_mood_seed_vad(
        existing_long_mood_state=existing_long_mood_state,
        current_emotion=current_emotion,
        key="current",
    )
    shock_before = _long_mood_seed_shock(
        existing_long_mood_state=existing_long_mood_state,
    )
    elapsed_ms = _long_mood_elapsed_ms(
        existing_long_mood_state=existing_long_mood_state,
        applied_at=applied_at,
    )
    decay_factor = 0.5 ** (elapsed_ms / LONG_MOOD_SHOCK_HALF_LIFE_MS)
    baseline_after = _lerp_vad(
        start_vad=baseline_before,
        target_vad=cycle_vad,
        alpha=LONG_MOOD_BASELINE_LERP,
    )
    shock_target = _subtract_vad(cycle_vad, baseline_after)
    shock_after = _clamp_vad(
        _add_vad(
            _scale_vad(shock_before, decay_factor),
            _scale_vad(shock_target, LONG_MOOD_SHOCK_BLEND),
        )
    )
    current_target = _clamp_vad(
        _add_vad(baseline_after, shock_after)
    )
    current_after = _clamp_delta_vad(
        previous_vad=current_before,
        target_vad=current_target,
        max_delta=LONG_MOOD_MAX_DELTA_PER_CYCLE,
    )
    primary_label = _mood_label_from_vad(current_after)
    baseline_label = _mood_label_from_vad(baseline_after)
    shock_label = _mood_label_from_vad(shock_after)
    shock_magnitude = _vad_magnitude(shock_after)
    stability = round(max(0.0, min(1.0, 1.0 - shock_magnitude * 0.85)), 2)
    biases = _emotion_biases_from_vad(current_after)
    source_affect_labels = _unique_non_empty_strings(
        [
            str(event_affect_update["moment_affect_labels"][0])
            for event_affect_update in event_affect_updates
            if list(event_affect_update["moment_affect_labels"])
        ]
    )
    labels = _unique_non_empty_strings(
        [
            primary_label,
            baseline_label,
            shock_label if shock_magnitude >= 0.12 else "",
            *source_affect_labels,
        ]
    )
    revision_reason = (
        "write_memory updated long mood state"
        if existing_long_mood_state is not None
        else "write_memory created long mood state"
    )
    confidence = round(
        min(
            0.92,
            0.58
            + _average_affect_confidence(event_affect_updates) * 0.18
            + shock_magnitude * 0.12,
        ),
        2,
    )
    importance = round(min(0.90, 0.54 + shock_magnitude * 0.20), 2)
    memory_strength = round(min(0.90, 0.56 + stability * 0.18), 2)
    body_text = _build_long_mood_body_text(
        baseline_label=baseline_label,
        shock_label=shock_label,
        primary_label=primary_label,
        shock_magnitude=shock_magnitude,
    )
    return {
        "state_ref": "long_mood_primary",
        "operation": "upsert",
        "memory_kind": "long_mood_state",
        "body_text": body_text,
        "payload": {
            "source_job_id": source_job_id,
            "job_kind": "write_memory",
            "source_cycle_id": str(payload["cycle_id"]),
            "primary_event_id": str(payload["primary_event_id"]),
            "source_event_ids": source_event_ids,
            "primary_label": primary_label,
            "labels": labels,
            "summary_text": body_text,
            "baseline": {
                "v": baseline_after["v"],
                "a": baseline_after["a"],
                "d": baseline_after["d"],
                "primary_label": baseline_label,
                "labels": [baseline_label],
                "alpha": LONG_MOOD_BASELINE_LERP,
            },
            "shock": {
                "v": shock_after["v"],
                "a": shock_after["a"],
                "d": shock_after["d"],
                "primary_label": shock_label,
                "labels": [shock_label],
                "magnitude": round(shock_magnitude, 2),
                "half_life_ms": LONG_MOOD_SHOCK_HALF_LIFE_MS,
                "updated_at": applied_at,
            },
            "current": {
                "v": current_after["v"],
                "a": current_after["a"],
                "d": current_after["d"],
            },
            "stability": stability,
            "active_biases": biases,
            "source_affect_labels": source_affect_labels,
            "updated_at": applied_at,
        },
        "confidence": confidence,
        "importance": importance,
        "memory_strength": memory_strength,
        "last_confirmed_at": applied_at,
        "evidence_event_ids": source_event_ids,
        "revision_reason": revision_reason,
    }


# Block: Event affect build
def _build_event_affect_update(
    *,
    event_entry: dict[str, Any],
    primary_event_id: str,
) -> dict[str, Any]:
    event_id = str(event_entry["event_id"])
    label, moment_affect_text, vad, confidence = _event_affect_profile(
        event_entry=event_entry,
        is_primary_event=(event_id == primary_event_id),
    )
    return {
        "event_id": event_id,
        "moment_affect_text": moment_affect_text,
        "moment_affect_labels": [label],
        "vad": vad,
        "confidence": confidence,
        "evidence_event_ids": [event_id],
        "revision_reason": "write_memory inferred event affect",
    }


# Block: Event link build
def _build_event_links(
    *,
    event_entries: list[dict[str, Any]],
    primary_event_id: str,
    recent_dialogue_context: list[dict[str, Any]],
    source_event_ids: list[str],
) -> list[dict[str, Any]]:
    event_links: list[dict[str, Any]] = []
    seen_link_keys: set[tuple[str, str, str]] = set()
    primary_event_entry = _primary_observation_event_entry(event_entries=event_entries)
    dialogue_continuation = _is_primary_dialogue_continuation(
        event_entries=event_entries,
        recent_dialogue_context=recent_dialogue_context,
    )
    for previous_event_entry, current_event_entry in zip(
        event_entries,
        event_entries[1:],
        strict=False,
    ):
        ordered_label = _ordered_event_link_label(
            previous_event_entry=previous_event_entry,
            current_event_entry=current_event_entry,
            dialogue_continuation=dialogue_continuation,
        )
        _append_event_link(
            entries=event_links,
            seen_link_keys=seen_link_keys,
            from_event_id=str(current_event_entry["event_id"]),
            to_event_id=str(previous_event_entry["event_id"]),
            label=ordered_label,
            confidence=_event_link_confidence(label=ordered_label, anchor=False),
            evidence_event_ids=source_event_ids,
            revision_reason="write_memory linked ordered source events",
        )
        current_event_id = str(current_event_entry["event_id"])
        if current_event_id == primary_event_id or primary_event_entry is None:
            continue
        anchor_label = _primary_anchor_link_label(
            current_event_entry=current_event_entry,
            primary_event_entry=primary_event_entry,
            dialogue_continuation=dialogue_continuation,
        )
        _append_event_link(
            entries=event_links,
            seen_link_keys=seen_link_keys,
            from_event_id=current_event_id,
            to_event_id=primary_event_id,
            label=anchor_label,
            confidence=_event_link_confidence(label=anchor_label, anchor=True),
            evidence_event_ids=source_event_ids,
            revision_reason="write_memory linked source events by primary topic",
        )
    return event_links


# Block: Event thread build
def _build_event_threads(
    *,
    event_entries: list[dict[str, Any]],
    cycle_thread_key: str,
    dialogue_thread_key: str | None,
    primary_event_id: str,
    source_event_ids: list[str],
) -> list[dict[str, Any]]:
    event_threads: list[dict[str, Any]] = []
    for event_entry in event_entries:
        event_id = str(event_entry["event_id"])
        event_kind = str(event_entry["kind"])
        primary_role = "primary" if event_id == primary_event_id else "supporting"
        event_threads.append(
            {
                "event_id": event_id,
                "thread_key": cycle_thread_key,
                "confidence": 0.68 if event_id == primary_event_id else 0.60,
                "thread_role": primary_role,
                "evidence_event_ids": source_event_ids,
                "revision_reason": "write_memory grouped source events into cycle thread",
            }
        )
        if dialogue_thread_key is None:
            continue
        event_threads.append(
            {
                "event_id": event_id,
                "thread_key": dialogue_thread_key,
                "confidence": _dialogue_thread_confidence(
                    event_id=event_id,
                    primary_event_id=primary_event_id,
                    event_kind=event_kind,
                ),
                "thread_role": _dialogue_thread_role(
                    event_id=event_id,
                    primary_event_id=primary_event_id,
                    event_kind=event_kind,
                ),
                "evidence_event_ids": source_event_ids,
                "revision_reason": "write_memory attached source events to dialogue thread",
            }
        )
    return event_threads


# Block: Summary text builder
def _build_summary_memory_body_text(
    *,
    primary_event_id: str,
    event_entries: list[dict[str, Any]],
) -> str:
    summary_parts: list[str] = []
    for event_entry in event_entries:
        event_id = str(event_entry["event_id"])
        summary_text = _required_non_empty_string(
            event_entry.get("summary_text"),
            "write_memory event entry.summary_text must be non-empty string",
        )
        if event_id == primary_event_id:
            summary_parts.append(f"中心:{summary_text}")
            continue
        summary_parts.append(summary_text)
    combined_text = " / ".join(summary_parts)
    if combined_text:
        return combined_text[:1000]
    return "短周期で確定した出来事を要約した記憶"


# Block: Preference signal helper
def _preference_signal(status: str) -> tuple[str, float] | None:
    if status == "succeeded":
        return ("like", 1.0)
    if status == "failed":
        return ("dislike", 1.0)
    if status == "stopped":
        return ("dislike", 0.6)
    return None


# Block: Preference signal accumulate
def _accumulate_preference_signal(
    *,
    aggregates: dict[tuple[str, str], dict[str, float]],
    domain: str,
    target_key: str,
    signal: tuple[str, float],
) -> None:
    aggregate_key = (domain, target_key)
    if aggregate_key not in aggregates:
        aggregates[aggregate_key] = {
            "like_score": 0.0,
            "dislike_score": 0.0,
        }
    polarity, weight = signal
    score_key = "like_score" if polarity == "like" else "dislike_score"
    aggregates[aggregate_key][score_key] += weight


# Block: Preference existing index
def _existing_preference_index(
    *,
    existing_preference_entries: list[dict[str, Any]],
) -> dict[tuple[str, str, str], dict[str, Any]]:
    index: dict[tuple[str, str, str], dict[str, Any]] = {}
    for preference_entry in existing_preference_entries:
        target_entity_ref = _required_object(
            preference_entry.get("target_entity_ref"),
            "write_memory existing preference target_entity_ref must be object",
        )
        target_key = _required_non_empty_string(
            target_entity_ref.get("target_key"),
            "write_memory existing preference target_entity_ref.target_key must be non-empty string",
        )
        polarity = _required_non_empty_string(
            preference_entry.get("polarity"),
            "write_memory existing preference polarity must be non-empty string",
        )
        if polarity not in WRITE_MEMORY_PREFERENCE_POLARITIES:
            raise RuntimeError("write_memory existing preference polarity is invalid")
        index[
            (
                _required_non_empty_string(
                    preference_entry.get("domain"),
                    "write_memory existing preference domain must be non-empty string",
                ),
                target_key,
                polarity,
            )
        ] = preference_entry
    return index


# Block: Preference status resolve
def _resolved_preference_status(
    *,
    dominant_score: float,
    existing_entry: dict[str, Any] | None,
) -> tuple[str, float]:
    prior_status = None
    prior_support = 0.0
    if existing_entry is not None:
        prior_status = _required_non_empty_string(
            existing_entry.get("status"),
            "write_memory existing preference status must be non-empty string",
        )
        if prior_status == "candidate":
            prior_support = 0.45 + float(existing_entry["confidence"]) * 0.30
        elif prior_status == "confirmed":
            prior_support = 0.85 + float(existing_entry["confidence"]) * 0.35
        elif prior_status == "revoked":
            prior_support = 0.0
    support_score = dominant_score + prior_support
    if prior_status == "confirmed" or support_score >= 1.55 or dominant_score >= 2.0:
        return (
            "confirmed",
            round(min(0.95, 0.62 + dominant_score * 0.12 + prior_support * 0.06), 2),
        )
    return (
        "candidate",
        round(min(0.86, 0.44 + dominant_score * 0.16 + prior_support * 0.08), 2),
    )


# Block: Preference revoke decision
def _should_revoke_preference(
    *,
    dominant_score: float,
    opposite_score: float,
    existing_entry: dict[str, Any] | None,
) -> bool:
    if existing_entry is None:
        return False
    existing_status = _required_non_empty_string(
        existing_entry.get("status"),
        "write_memory existing preference status must be non-empty string",
    )
    if existing_status not in {"candidate", "confirmed"}:
        return False
    return dominant_score >= 1.0 and (dominant_score - opposite_score) >= 0.40


# Block: Preference revoke confidence
def _revoked_preference_confidence(
    *,
    dominant_score: float,
    existing_entry: dict[str, Any] | None,
) -> float:
    prior_confidence = 0.0
    if existing_entry is not None:
        prior_confidence = float(existing_entry["confidence"])
    return round(min(0.90, 0.46 + dominant_score * 0.14 + prior_confidence * 0.08), 2)


# Block: Preference revision reason
def _preference_revision_reason(
    *,
    target_key: str,
    polarity: str,
    status: str,
    existing_entry: dict[str, Any] | None,
) -> str:
    if status == "confirmed":
        if existing_entry is not None and str(existing_entry.get("status")) == "candidate":
            return f"write_memory confirmed {target_key} {polarity} preference"
        if existing_entry is not None and str(existing_entry.get("status")) == "revoked":
            return f"write_memory restored {target_key} {polarity} preference"
        return f"write_memory reinforced {target_key} {polarity} preference"
    return f"write_memory observed {target_key} leaning {polarity}"


# Block: Explicit preference signals
def _explicit_preference_signals(*, event_entries: list[dict[str, Any]]) -> list[dict[str, str]]:
    signals: list[dict[str, str]] = []
    for event_entry in event_entries:
        if str(event_entry.get("kind")) != "observation":
            continue
        observation_text = _dialogue_event_text(
            summary_text=_required_non_empty_string(
                event_entry.get("summary_text"),
                "write_memory event entry.summary_text must be non-empty string",
            )
        )
        if not observation_text:
            continue
        signals.extend(_explicit_preference_signals_from_text(text=observation_text))
    return signals


def _explicit_preference_signals_from_text(*, text: str) -> list[dict[str, str]]:
    normalized_text = text.strip()
    if not normalized_text:
        return []
    signals: list[dict[str, str]] = []
    for fragment in _preference_text_fragments(normalized_text):
        signals.extend(
            _explicit_suffix_signals(
                text=fragment,
                suffixes=EXPLICIT_LIKE_PREFERENCE_SUFFIXES,
                polarity="like",
            )
        )
        signals.extend(
            _explicit_suffix_signals(
                text=fragment,
                suffixes=EXPLICIT_DISLIKE_PREFERENCE_SUFFIXES,
                polarity="dislike",
            )
        )
    deduped: list[dict[str, str]] = []
    seen_keys: set[tuple[str, str, str]] = set()
    for signal in signals:
        signal_key = (
            str(signal["domain"]),
            str(signal["target_key"]),
            str(signal["polarity"]),
        )
        if signal_key in seen_keys:
            continue
        seen_keys.add(signal_key)
        deduped.append(signal)
    return deduped


def _preference_text_fragments(text: str) -> list[str]:
    fragments: list[str] = []
    current = ""
    for character in text:
        if character in "。！？!?\\n":
            stripped_current = current.strip(" 、,")
            if stripped_current:
                fragments.append(stripped_current)
            current = ""
            continue
        current += character
    stripped_current = current.strip(" 、,")
    if stripped_current:
        fragments.append(stripped_current)
    return fragments


def _explicit_suffix_signals(
    *,
    text: str,
    suffixes: tuple[str, ...],
    polarity: str,
) -> list[dict[str, str]]:
    signals: list[dict[str, str]] = []
    for suffix in suffixes:
        if suffix not in text:
            continue
        target_key = _normalized_topic_keyword(text.split(suffix, 1)[0])
        if not target_key:
            continue
        signals.append(
            {
                "domain": "topic_keyword",
                "target_key": target_key,
                "polarity": polarity,
            }
        )
    return signals


def _normalized_topic_keyword(text: str) -> str:
    normalized = text.strip().strip("「」『』\"'。、！？!?., ")
    for suffix in ("のこと", "の話", "の件", "について", "とか", "って話"):
        if normalized.endswith(suffix):
            normalized = normalized.removesuffix(suffix).strip()
    for suffix in ("を", "は", "が", "も", "で", "に", "って"):
        if normalized.endswith(suffix):
            normalized = normalized.removesuffix(suffix).strip()
    return " ".join(normalized.lower().split())[:80]


def _explicit_preference_revision_reason(
    *,
    target_key: str,
    polarity: str,
    existing_entry: dict[str, Any] | None,
) -> str:
    if existing_entry is not None and str(existing_entry.get("status")) == "revoked":
        return f"write_memory restored explicit {target_key} {polarity} preference"
    return f"write_memory confirmed explicit {target_key} {polarity} preference"


# Block: Action type normalize
def _normalized_action_type(*, action_type: str) -> str | None:
    if action_type in {"browse", "look", "notify", "speak"}:
        return action_type
    return WRITE_MEMORY_ACTION_TYPE_ALIASES.get(action_type)


# Block: Observation kind helper
def _observation_kind_for_action(action_type: str) -> str | None:
    return {
        "browse": "web_search",
        "look": "camera_scene",
    }.get(action_type)


# Block: Event affect profile
def _event_affect_profile(
    *,
    event_entry: dict[str, Any],
    is_primary_event: bool,
) -> tuple[str, str, dict[str, float], float]:
    summary_text = str(event_entry["summary_text"]).lower()
    kind = str(event_entry["kind"])
    confidence = 0.58
    if _text_has_any(summary_text, ("failed", "error", "timeout", "missing", "失敗", "エラー")):
        label = "tense"
        moment_affect_text = "予定どおりに進まず、少し緊張が高まった"
        vad = {"v": -0.28, "a": 0.34, "d": -0.18}
        confidence = 0.72
    elif _text_has_any(summary_text, ("stopped", "cancel", "停止", "中断")):
        label = "guarded"
        moment_affect_text = "進行をいったん止めて、慎重さが前に出た"
        vad = {"v": -0.16, "a": 0.18, "d": -0.10}
        confidence = 0.68
    elif kind == "external_response":
        label = "warm"
        moment_affect_text = "応答がまとまり、関係維持へ少し意識が向いた"
        vad = {"v": 0.22, "a": 0.08, "d": 0.14}
        confidence = 0.60
    elif kind == "observation" or _text_has_any(summary_text, ("browse", "look", "camera", "network", "観測", "検索")):
        label = "curious"
        moment_affect_text = "新しい観測に触れて、好奇心が少し動いた"
        vad = {"v": 0.16, "a": 0.24, "d": 0.08}
        confidence = 0.60
    else:
        label = "calm"
        moment_affect_text = "流れを受け止めて、ひとまず落ち着いた"
        vad = {"v": 0.12, "a": 0.04, "d": 0.10}
        confidence = 0.56
    if is_primary_event:
        confidence = min(0.90, confidence + 0.05)
    return (
        label,
        moment_affect_text,
        vad,
        round(confidence, 2),
    )


# Block: Cycle affect average
def _cycle_affect_vad(
    *,
    event_affect_updates: list[dict[str, Any]],
    primary_event_id: str,
) -> dict[str, float]:
    totals = {"v": 0.0, "a": 0.0, "d": 0.0}
    total_weight = 0.0
    for event_affect_update in event_affect_updates:
        weight = 1.35 if str(event_affect_update["event_id"]) == primary_event_id else 1.0
        total_weight += weight
        vad = dict(event_affect_update["vad"])
        totals["v"] += float(vad["v"]) * weight
        totals["a"] += float(vad["a"]) * weight
        totals["d"] += float(vad["d"]) * weight
    return {
        axis: round(totals[axis] / total_weight, 2)
        for axis in ("v", "a", "d")
    }


# Block: Long mood seed vad
def _long_mood_seed_vad(
    *,
    existing_long_mood_state: dict[str, Any] | None,
    current_emotion: dict[str, Any],
    key: str,
) -> dict[str, float]:
    if existing_long_mood_state is not None:
        payload = _required_object(
            existing_long_mood_state.get("payload"),
            "write_memory existing long mood payload must be object",
        )
        payload_entry = payload.get(key)
        if isinstance(payload_entry, dict):
            return _vad_from_mapping(
                payload_entry,
                field_name=f"write_memory existing long mood payload.{key}",
            )
    return _vad_from_current_emotion(current_emotion=current_emotion)


# Block: Long mood seed shock
def _long_mood_seed_shock(
    *,
    existing_long_mood_state: dict[str, Any] | None,
) -> dict[str, float]:
    if existing_long_mood_state is None:
        return {"v": 0.0, "a": 0.0, "d": 0.0}
    payload = _required_object(
        existing_long_mood_state.get("payload"),
        "write_memory existing long mood payload must be object",
    )
    shock_entry = payload.get("shock")
    if isinstance(shock_entry, dict):
        return _vad_from_mapping(
            shock_entry,
            field_name="write_memory existing long mood payload.shock",
        )
    return {"v": 0.0, "a": 0.0, "d": 0.0}


# Block: Long mood elapsed helper
def _long_mood_elapsed_ms(
    *,
    existing_long_mood_state: dict[str, Any] | None,
    applied_at: int,
) -> int:
    if existing_long_mood_state is None:
        return 0
    payload = existing_long_mood_state.get("payload")
    if isinstance(payload, dict):
        payload_updated_at = payload.get("updated_at")
        if isinstance(payload_updated_at, int):
            return max(0, applied_at - payload_updated_at)
    updated_at = existing_long_mood_state.get("updated_at")
    if isinstance(updated_at, int):
        return max(0, applied_at - updated_at)
    return 0


# Block: Current emotion vad
def _vad_from_current_emotion(*, current_emotion: dict[str, Any]) -> dict[str, float]:
    return {
        "v": _required_float_like(
            current_emotion.get("valence"),
            "write_memory current_emotion.valence must be numeric",
        ),
        "a": _required_float_like(
            current_emotion.get("arousal"),
            "write_memory current_emotion.arousal must be numeric",
        ),
        "d": _required_float_like(
            current_emotion.get("dominance"),
            "write_memory current_emotion.dominance must be numeric",
        ),
    }


# Block: Mapping vad
def _vad_from_mapping(
    value: dict[str, Any],
    *,
    field_name: str,
) -> dict[str, float]:
    return {
        "v": _required_float_like(value.get("v"), f"{field_name}.v must be numeric"),
        "a": _required_float_like(value.get("a"), f"{field_name}.a must be numeric"),
        "d": _required_float_like(value.get("d"), f"{field_name}.d must be numeric"),
    }


# Block: Float like helper
def _required_float_like(value: Any, error_message: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(error_message)
    return round(float(value), 2)


# Block: Vad math
def _lerp_vad(
    *,
    start_vad: dict[str, float],
    target_vad: dict[str, float],
    alpha: float,
) -> dict[str, float]:
    return _clamp_vad(
        {
            axis: round(
                float(start_vad[axis]) + (float(target_vad[axis]) - float(start_vad[axis])) * alpha,
                2,
            )
            for axis in ("v", "a", "d")
        }
    )


def _subtract_vad(left_vad: dict[str, float], right_vad: dict[str, float]) -> dict[str, float]:
    return {
        axis: round(float(left_vad[axis]) - float(right_vad[axis]), 2)
        for axis in ("v", "a", "d")
    }


def _add_vad(left_vad: dict[str, float], right_vad: dict[str, float]) -> dict[str, float]:
    return {
        axis: round(float(left_vad[axis]) + float(right_vad[axis]), 2)
        for axis in ("v", "a", "d")
    }


def _scale_vad(vad: dict[str, float], factor: float) -> dict[str, float]:
    return {
        axis: round(float(vad[axis]) * factor, 2)
        for axis in ("v", "a", "d")
    }


def _clamp_vad(vad: dict[str, float]) -> dict[str, float]:
    return {
        axis: round(max(-1.0, min(1.0, float(vad[axis]))), 2)
        for axis in ("v", "a", "d")
    }


def _clamp_delta_vad(
    *,
    previous_vad: dict[str, float],
    target_vad: dict[str, float],
    max_delta: float,
) -> dict[str, float]:
    clamped: dict[str, float] = {}
    for axis in ("v", "a", "d"):
        delta = float(target_vad[axis]) - float(previous_vad[axis])
        if delta > max_delta:
            delta = max_delta
        if delta < -max_delta:
            delta = -max_delta
        clamped[axis] = round(float(previous_vad[axis]) + delta, 2)
    return _clamp_vad(clamped)


def _vad_magnitude(vad: dict[str, float]) -> float:
    return round(
        (
            float(vad["v"]) * float(vad["v"])
            + float(vad["a"]) * float(vad["a"])
            + float(vad["d"]) * float(vad["d"])
        )
        ** 0.5,
        2,
    )


# Block: Mood labels
def _mood_label_from_vad(vad: dict[str, float]) -> str:
    best_label = "calm"
    best_distance = float("inf")
    for label, prototype in LONG_MOOD_LABEL_PROTOTYPES.items():
        distance = (
            (float(vad["v"]) - float(prototype["v"])) ** 2
            + (float(vad["a"]) - float(prototype["a"])) ** 2
            + (float(vad["d"]) - float(prototype["d"])) ** 2
        )
        if distance < best_distance:
            best_distance = distance
            best_label = label
    return best_label


def _build_long_mood_body_text(
    *,
    baseline_label: str,
    shock_label: str,
    primary_label: str,
    shock_magnitude: float,
) -> str:
    if shock_magnitude < 0.12:
        return f"基調は {baseline_label} で、現在も {primary_label} に近い"
    return (
        f"基調は {baseline_label} だが、{shock_label} の余韻を含みつつ "
        f"現在は {primary_label} に寄っている"
    )[:240]


# Block: Emotion bias build
def _emotion_biases_from_vad(vad: dict[str, float]) -> dict[str, float]:
    valence = float(vad["v"])
    arousal = float(vad["a"])
    dominance = float(vad["d"])
    return {
        "caution_bias": round(max(0.0, -valence) * 0.28 + max(0.0, arousal) * 0.22, 2),
        "approach_bias": round(max(0.0, valence) * 0.30 + max(0.0, dominance) * 0.18, 2),
        "avoidance_bias": round(max(0.0, -valence) * 0.30 + max(0.0, arousal) * 0.18, 2),
        "speech_intensity_bias": round(max(0.0, arousal) * 0.34 + abs(valence) * 0.08, 2),
    }


# Block: Average affect confidence
def _average_affect_confidence(event_affect_updates: list[dict[str, Any]]) -> float:
    if not event_affect_updates:
        return 0.0
    return round(
        sum(float(event_affect_update["confidence"]) for event_affect_update in event_affect_updates)
        / len(event_affect_updates),
        2,
    )


# Block: Unique string helper
def _unique_non_empty_strings(values: list[str]) -> list[str]:
    unique_values: list[str] = []
    for value in values:
        if not value:
            continue
        if value not in unique_values:
            unique_values.append(value)
    return unique_values


# Block: Event link append helper
def _append_event_link(
    *,
    entries: list[dict[str, Any]],
    seen_link_keys: set[tuple[str, str, str]],
    from_event_id: str,
    to_event_id: str,
    label: str,
    confidence: float,
    evidence_event_ids: list[str],
    revision_reason: str,
) -> None:
    link_key = (from_event_id, to_event_id, label)
    if link_key in seen_link_keys:
        return
    seen_link_keys.add(link_key)
    entries.append(
        {
            "from_event_id": from_event_id,
            "to_event_id": to_event_id,
            "label": label,
            "confidence": confidence,
            "evidence_event_ids": evidence_event_ids,
            "revision_reason": revision_reason,
        }
    )


# Block: Ordered event link label helper
def _ordered_event_link_label(
    *,
    previous_event_entry: dict[str, Any],
    current_event_entry: dict[str, Any],
    dialogue_continuation: bool,
) -> str:
    previous_kind = str(previous_event_entry["kind"])
    current_kind = str(current_event_entry["kind"])
    if current_kind == "action_result" and previous_kind == "action":
        return "caused_by"
    if current_kind == "external_response" and previous_kind in {"observation", "action_result"}:
        return "reply_to"
    if _event_entries_share_topic(
        left_event_entry=previous_event_entry,
        right_event_entry=current_event_entry,
    ):
        return "same_topic"
    if dialogue_continuation:
        return "continuation"
    return "continuation"


# Block: Primary anchor link label helper
def _primary_anchor_link_label(
    *,
    current_event_entry: dict[str, Any],
    primary_event_entry: dict[str, Any],
    dialogue_continuation: bool,
) -> str:
    current_kind = str(current_event_entry["kind"])
    if current_kind == "external_response":
        return "reply_to"
    if _event_entries_share_topic(
        left_event_entry=current_event_entry,
        right_event_entry=primary_event_entry,
    ):
        return "same_topic"
    if dialogue_continuation:
        return "continuation"
    return "continuation"


# Block: Event link confidence helper
def _event_link_confidence(*, label: str, anchor: bool) -> float:
    if label == "caused_by":
        return 0.78
    if label == "reply_to":
        return 0.74 if anchor else 0.70
    if label == "same_topic":
        return 0.60 if anchor else 0.56
    return 0.64 if anchor else 0.60


# Block: Cycle thread key helper
def _cycle_thread_key(cycle_id: str) -> str:
    return f"cycle:{cycle_id}"


# Block: Dialogue thread key helper
def _dialogue_thread_key(
    *,
    cycle_id: str,
    event_entries: list[dict[str, Any]],
    recent_dialogue_context: list[dict[str, Any]],
) -> str | None:
    primary_event_entry = _primary_observation_event_entry(event_entries=event_entries)
    if primary_event_entry is None:
        return None
    primary_text = _dialogue_event_text(summary_text=str(primary_event_entry["summary_text"]))
    for context_entry in recent_dialogue_context:
        if _is_dialogue_continuation(
            primary_text=primary_text,
            context_summary_text=str(context_entry["summary_text"]),
        ):
            for thread_key in context_entry["thread_keys"]:
                if _is_dialogue_thread_key(thread_key):
                    return thread_key
            return f"dialogue:{context_entry['event_id']}"
    return f"dialogue:{cycle_id}"


# Block: Primary observation pick
def _primary_observation_event_entry(
    *,
    event_entries: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not event_entries:
        return None
    primary_event_entry = event_entries[0]
    if str(primary_event_entry["kind"]) != "observation":
        return None
    summary_text = str(primary_event_entry["summary_text"])
    if not summary_text.startswith(("chat_message:", "microphone_message:")):
        return None
    return primary_event_entry


# Block: Dialogue continuation decision
def _is_dialogue_continuation(
    *,
    primary_text: str,
    context_summary_text: str,
) -> bool:
    if not primary_text:
        return False
    for cue in DIALOGUE_CONTINUATION_CUES:
        if cue in primary_text:
            return True
    primary_dates = _iso_date_tokens(primary_text)
    context_dates = _iso_date_tokens(_dialogue_event_text(summary_text=context_summary_text))
    return bool(primary_dates and context_dates and primary_dates.intersection(context_dates))


# Block: Primary dialogue continuation helper
def _is_primary_dialogue_continuation(
    *,
    event_entries: list[dict[str, Any]],
    recent_dialogue_context: list[dict[str, Any]],
) -> bool:
    primary_event_entry = _primary_observation_event_entry(event_entries=event_entries)
    if primary_event_entry is None:
        return False
    primary_text = _dialogue_event_text(summary_text=str(primary_event_entry["summary_text"]))
    for context_entry in recent_dialogue_context:
        if _is_dialogue_continuation(
            primary_text=primary_text,
            context_summary_text=str(context_entry["summary_text"]),
        ):
            return True
    return False


# Block: Event topic overlap helper
def _event_entries_share_topic(
    *,
    left_event_entry: dict[str, Any],
    right_event_entry: dict[str, Any],
) -> bool:
    left_text = _dialogue_event_text(summary_text=str(left_event_entry["summary_text"]))
    right_text = _dialogue_event_text(summary_text=str(right_event_entry["summary_text"]))
    left_dates = _iso_date_tokens(left_text)
    right_dates = _iso_date_tokens(right_text)
    if left_dates and right_dates and left_dates.intersection(right_dates):
        return True
    left_terms = _topic_term_candidates(left_text)
    right_terms = _topic_term_candidates(right_text)
    return bool(left_terms and right_terms and left_terms.intersection(right_terms))


def _topic_term_candidates(text: str) -> set[str]:
    normalized_text = text.strip().lower()
    for separator in ("を", "は", "が", "に", "で", "の", "と", "へ", "も", "や", "から", "まで", "より", "って"):
        normalized_text = normalized_text.replace(separator, " ")
    terms = {
        match.group(0)
        for match in re.finditer(r"[0-9a-zぁ-んァ-ヶ一-龠ー]{2,}", normalized_text)
    }
    return {
        term
        for term in terms
        if term not in {"また", "同じ", "もう一度", "続き", "確認", "お願い"}
    }


# Block: Dialogue event text
def _dialogue_event_text(*, summary_text: str) -> str:
    normalized_summary_text = summary_text.strip()
    for prefix in ("chat_message:", "microphone_message:"):
        if normalized_summary_text.startswith(prefix):
            return normalized_summary_text.removeprefix(prefix)
    return normalized_summary_text


# Block: Dialogue thread key predicate
def _is_dialogue_thread_key(thread_key: str) -> bool:
    return thread_key.startswith("dialogue:")


# Block: ISO date token extract
def _iso_date_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    candidate = ""
    for character in text:
        if character.isdigit() or character == "-":
            candidate += character
            continue
        if _is_iso_date_token(candidate):
            tokens.add(candidate)
        candidate = ""
    if _is_iso_date_token(candidate):
        tokens.add(candidate)
    return tokens


# Block: ISO date token predicate
def _is_iso_date_token(candidate: str) -> bool:
    if len(candidate) != 10:
        return False
    if candidate[4] != "-" or candidate[7] != "-":
        return False
    return candidate[:4].isdigit() and candidate[5:7].isdigit() and candidate[8:10].isdigit()


# Block: Thread hint list
def _thread_hints(
    *,
    cycle_thread_key: str,
    dialogue_thread_key: str | None,
) -> list[str]:
    hints = [cycle_thread_key]
    if dialogue_thread_key is not None:
        hints.append(dialogue_thread_key)
    return hints


# Block: Dialogue thread role
def _dialogue_thread_role(
    *,
    event_id: str,
    primary_event_id: str,
    event_kind: str,
) -> str:
    if event_id == primary_event_id:
        return "reply"
    if event_kind == "external_response":
        return "response"
    return "continuation"


# Block: Dialogue thread confidence
def _dialogue_thread_confidence(
    *,
    event_id: str,
    primary_event_id: str,
    event_kind: str,
) -> float:
    if event_id == primary_event_id:
        return 0.82
    if event_kind == "external_response":
        return 0.76
    return 0.68


# Block: Evidence event validation
def _validate_evidence_event_ids(
    *,
    value: Any,
    source_event_ids: list[str],
    field_name: str,
) -> list[str]:
    evidence_event_ids = _required_non_empty_string_list(
        value,
        f"{field_name} must be non-empty string array",
    )
    for event_id in evidence_event_ids:
        if event_id not in source_event_ids:
            raise RuntimeError(f"{field_name} must exist in source_event_ids")
    return evidence_event_ids


# Block: VAD validation
def _validate_vad_object(value: Any, field_name: str) -> dict[str, float]:
    vad = _required_object(
        value,
        f"{field_name} must be an object",
    )
    if tuple(vad.keys()) != WRITE_MEMORY_VAD_KEYS:
        raise RuntimeError(f"{field_name} keys must be v/a/d")
    return {
        "v": _required_signed_score(
            vad.get("v"),
            f"{field_name}.v must be numeric within -1.0..1.0",
        ),
        "a": _required_signed_score(
            vad.get("a"),
            f"{field_name}.a must be numeric within -1.0..1.0",
        ),
        "d": _required_signed_score(
            vad.get("d"),
            f"{field_name}.d must be numeric within -1.0..1.0",
        ),
    }


# Block: Text cue helper
def _text_has_any(text: str, cues: tuple[str, ...]) -> bool:
    return any(cue in text for cue in cues)


# Block: Generic validators
def _required_object(value: Any, message: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(message)
    return value


def _required_list(value: Any, message: str) -> list[Any]:
    if not isinstance(value, list):
        raise RuntimeError(message)
    return value


def _required_integer(value: Any, message: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeError(message)
    return value


def _required_positive_integer(value: Any, message: str) -> int:
    integer_value = _required_integer(value, message)
    if integer_value <= 0:
        raise RuntimeError(message)
    return integer_value


def _optional_positive_integer(value: Any, message: str) -> int | None:
    if value is None:
        return None
    return _required_positive_integer(value, message)


def _required_non_empty_string(value: Any, message: str) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeError(message)
    return value


def _optional_non_empty_string(value: Any, message: str) -> str | None:
    if value is None:
        return None
    return _required_non_empty_string(value, message)


def _required_non_empty_string_list(value: Any, message: str) -> list[str]:
    values = _required_list(value, message)
    normalized_values: list[str] = []
    for entry in values:
        if not isinstance(entry, str) or not entry:
            raise RuntimeError(message)
        normalized_values.append(entry)
    return normalized_values


def _required_string_list(value: Any, message: str) -> list[str]:
    values = _required_list(value, message)
    normalized_values: list[str] = []
    for entry in values:
        if not isinstance(entry, str) or not entry:
            raise RuntimeError(message)
        normalized_values.append(entry)
    return normalized_values


def _required_score(value: Any, message: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(message)
    numeric_value = float(value)
    if numeric_value < 0.0 or numeric_value > 1.0:
        raise RuntimeError(message)
    return numeric_value


def _optional_year_integer(value: Any, message: str) -> int | None:
    if value is None:
        return None
    year_value = _required_integer(value, message)
    if year_value < 1900 or year_value > 2100:
        raise RuntimeError(message)
    return year_value


def _required_signed_score(value: Any, message: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(message)
    numeric_value = float(value)
    if numeric_value < -1.0 or numeric_value > 1.0:
        raise RuntimeError(message)
    return numeric_value
