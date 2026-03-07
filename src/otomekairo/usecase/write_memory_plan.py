"""Build and validate structured write_memory plans."""

from __future__ import annotations

from typing import Any


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
WRITE_MEMORY_STATE_UPDATE_OPERATIONS = (
    "upsert",
    "close",
    "mark_done",
    "revise_confidence",
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
    browse_fact_entries: list[dict[str, Any]],
    applied_at: int,
) -> dict[str, Any]:
    source_event_ids = list(payload["source_event_ids"])
    summary_body_text = _build_summary_memory_body_text(
        primary_event_id=str(payload["primary_event_id"]),
        event_entries=event_entries,
    )
    state_updates = [
        {
            "operation": "upsert",
            "memory_kind": "summary",
            "body_text": summary_body_text,
            "payload": {
                "source_job_id": source_job_id,
                "job_kind": "write_memory",
                "source_cycle_id": str(payload["cycle_id"]),
                "primary_event_id": str(payload["primary_event_id"]),
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
    for browse_fact_entry in browse_fact_entries:
        state_updates.append(
            {
                "operation": "upsert",
                "memory_kind": "fact",
                "body_text": (
                    f"外部確認: {browse_fact_entry['query']} => {browse_fact_entry['summary_text']}"
                ),
                "payload": {
                    "source_job_id": source_job_id,
                    "job_kind": "write_memory",
                    "source_cycle_id": str(payload["cycle_id"]),
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
    return {
        "event_annotations": [
            {
                "event_id": str(event_entry["event_id"]),
                "about_time": None,
                "entities": [],
                "thread_hints": [],
            }
            for event_entry in event_entries
        ],
        "state_updates": state_updates,
        "preference_updates": [],
        "event_affect": [],
        "context_updates": {
            "event_links": [],
            "event_threads": [],
            "state_links": [],
        },
        "revision_reasons": [
            str(state_update["revision_reason"])
            for state_update in state_updates
        ],
    }


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
    event_annotations = _required_list(
        normalized_plan.get("event_annotations"),
        "write_memory plan.event_annotations must be a list",
    )
    if len(event_annotations) != len(payload["source_event_ids"]):
        raise RuntimeError("write_memory plan.event_annotations must match source_event_ids count")
    for event_annotation, expected_event_id in zip(event_annotations, payload["source_event_ids"], strict=True):
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
        _required_list(
            annotation_entry.get("entities"),
            "write_memory plan.event_annotations.entities must be a list",
        )
        _required_list(
            annotation_entry.get("thread_hints"),
            "write_memory plan.event_annotations.thread_hints must be a list",
        )
    state_updates = _required_list(
        normalized_plan.get("state_updates"),
        "write_memory plan.state_updates must be a list",
    )
    if not state_updates:
        raise RuntimeError("write_memory plan.state_updates must not be empty")
    normalized_state_updates: list[dict[str, Any]] = []
    for state_update in state_updates:
        normalized_state_updates.append(_validate_state_update(state_update=state_update))
    preference_updates = _required_list(
        normalized_plan.get("preference_updates"),
        "write_memory plan.preference_updates must be a list",
    )
    if preference_updates:
        raise RuntimeError("write_memory initial implementation does not support preference_updates")
    event_affect = _required_list(
        normalized_plan.get("event_affect"),
        "write_memory plan.event_affect must be a list",
    )
    if event_affect:
        raise RuntimeError("write_memory initial implementation does not support event_affect")
    context_updates = _required_object(
        normalized_plan.get("context_updates"),
        "write_memory plan.context_updates must be an object",
    )
    if tuple(context_updates.keys()) != WRITE_MEMORY_PLAN_CONTEXT_KEYS:
        raise RuntimeError("write_memory plan.context_updates keys must match fixed shape")
    normalized_context_updates = {
        key: _required_list(
            context_updates.get(key),
            f"write_memory plan.context_updates.{key} must be a list",
        )
        for key in WRITE_MEMORY_PLAN_CONTEXT_KEYS
    }
    if any(normalized_context_updates[key] for key in WRITE_MEMORY_PLAN_CONTEXT_KEYS):
        raise RuntimeError("write_memory initial implementation does not support non-empty context_updates")
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
        "preference_updates": preference_updates,
        "event_affect": event_affect,
        "context_updates": normalized_context_updates,
        "revision_reasons": normalized_revision_reasons,
    }


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


# Block: State update validation
def _validate_state_update(*, state_update: Any) -> dict[str, Any]:
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
    if operation != "upsert":
        raise RuntimeError("write_memory initial implementation only supports upsert state_updates")
    evidence_event_ids = _required_non_empty_string_list(
        normalized_state_update.get("evidence_event_ids"),
        "write_memory plan.state_updates.evidence_event_ids must be non-empty string array",
    )
    return {
        "operation": operation,
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
        "last_confirmed_at": _required_integer(
            normalized_state_update.get("last_confirmed_at"),
            "write_memory plan.state_updates.last_confirmed_at must be integer",
        ),
        "evidence_event_ids": evidence_event_ids,
        "revision_reason": _required_non_empty_string(
            normalized_state_update.get("revision_reason"),
            "write_memory plan.state_updates.revision_reason must be non-empty string",
        ),
    }


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


def _required_non_empty_string(value: Any, message: str) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeError(message)
    return value


def _required_non_empty_string_list(value: Any, message: str) -> list[str]:
    items = _required_list(value, message)
    normalized_items: list[str] = []
    for item in items:
        normalized_item = _required_non_empty_string(item, message)
        if normalized_item in normalized_items:
            raise RuntimeError(message)
        normalized_items.append(normalized_item)
    if not normalized_items:
        raise RuntimeError(message)
    return normalized_items


def _required_score(value: Any, message: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(message)
    normalized_value = float(value)
    if normalized_value < 0.0 or normalized_value > 1.0:
        raise RuntimeError(message)
    return normalized_value
