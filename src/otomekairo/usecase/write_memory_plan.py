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
    applied_at: int,
) -> dict[str, Any]:
    cycle_id = str(payload["cycle_id"])
    primary_event_id = str(payload["primary_event_id"])
    source_event_ids = list(payload["source_event_ids"])
    cycle_thread_key = _cycle_thread_key(cycle_id)
    summary_state_ref = "summary_primary"
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
    return {
        "event_annotations": [
            {
                "event_id": str(event_entry["event_id"]),
                "about_time": None,
                "entities": [],
                "thread_hints": [cycle_thread_key],
            }
            for event_entry in event_entries
        ],
        "state_updates": state_updates,
        "preference_updates": _build_preference_updates(
            action_entries=action_entries,
            source_event_ids=source_event_ids,
        ),
        "event_affect": [
            _build_event_affect_update(
                event_entry=event_entry,
                primary_event_id=primary_event_id,
            )
            for event_entry in event_entries
        ],
        "context_updates": {
            "event_links": _build_event_links(
                event_entries=event_entries,
                primary_event_id=primary_event_id,
                source_event_ids=source_event_ids,
            ),
            "event_threads": _build_event_threads(
                event_entries=event_entries,
                cycle_thread_key=cycle_thread_key,
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
    for state_update in state_updates:
        normalized_state_update = _validate_state_update(
            state_update=state_update,
            source_event_ids=source_event_ids,
        )
        state_ref = str(normalized_state_update["state_ref"])
        if state_ref in known_state_refs:
            raise RuntimeError("write_memory plan.state_updates.state_ref must be unique")
        known_state_refs.append(state_ref)
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
        thread_hints = _required_string_list(
            annotation_entry.get("thread_hints"),
            "write_memory plan.event_annotations.thread_hints must be a list of non-empty strings",
        )
        normalized_entries.append(
            {
                "event_id": event_id,
                "about_time": annotation_entry.get("about_time"),
                "entities": entities,
                "thread_hints": thread_hints,
            }
        )
    return normalized_entries


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
    if operation != "upsert":
        raise RuntimeError("write_memory initial implementation only supports upsert state_updates")
    evidence_event_ids = _validate_evidence_event_ids(
        value=normalized_state_update.get("evidence_event_ids"),
        source_event_ids=source_event_ids,
        field_name="write_memory plan.state_updates.evidence_event_ids",
    )
    return {
        "state_ref": _required_non_empty_string(
            normalized_state_update.get("state_ref"),
            "write_memory plan.state_updates.state_ref must be non-empty string",
        ),
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


# Block: Preference update build
def _build_preference_updates(
    *,
    action_entries: list[dict[str, Any]],
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
    preference_updates: list[dict[str, Any]] = []
    for domain, target_key in sorted(aggregates.keys()):
        aggregate = aggregates[(domain, target_key)]
        like_score = float(aggregate["like_score"])
        dislike_score = float(aggregate["dislike_score"])
        if like_score == dislike_score:
            continue
        polarity = "like" if like_score > dislike_score else "dislike"
        dominant_score = like_score if polarity == "like" else dislike_score
        confidence = round(min(0.85, 0.45 + dominant_score * 0.14), 2)
        preference_updates.append(
            {
                "owner_scope": "self",
                "target_entity_ref": {
                    "target_kind": domain,
                    "target_key": target_key,
                },
                "domain": domain,
                "polarity": polarity,
                "status": "candidate",
                "confidence": confidence,
                "evidence_event_ids": source_event_ids,
                "revision_reason": (
                    f"write_memory observed {target_key} leaning {polarity}"
                ),
            }
        )
    return preference_updates


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
    source_event_ids: list[str],
) -> list[dict[str, Any]]:
    event_links: list[dict[str, Any]] = []
    for previous_event_entry, current_event_entry in zip(
        event_entries,
        event_entries[1:],
        strict=False,
    ):
        event_links.append(
            {
                "from_event_id": str(current_event_entry["event_id"]),
                "to_event_id": str(previous_event_entry["event_id"]),
                "label": _event_link_label(
                    previous_kind=str(previous_event_entry["kind"]),
                    current_kind=str(current_event_entry["kind"]),
                ),
                "confidence": 0.60,
                "evidence_event_ids": source_event_ids,
                "revision_reason": "write_memory linked ordered source events",
            }
        )
        current_event_id = str(current_event_entry["event_id"])
        if current_event_id == primary_event_id:
            continue
        event_links.append(
            {
                "from_event_id": current_event_id,
                "to_event_id": primary_event_id,
                "label": "same_topic",
                "confidence": 0.54,
                "evidence_event_ids": source_event_ids,
                "revision_reason": "write_memory linked source events by primary topic",
            }
        )
    return event_links


# Block: Event thread build
def _build_event_threads(
    *,
    event_entries: list[dict[str, Any]],
    cycle_thread_key: str,
    primary_event_id: str,
    source_event_ids: list[str],
) -> list[dict[str, Any]]:
    return [
        {
            "event_id": str(event_entry["event_id"]),
            "thread_key": cycle_thread_key,
            "confidence": 0.68 if str(event_entry["event_id"]) == primary_event_id else 0.60,
            "thread_role": (
                "primary"
                if str(event_entry["event_id"]) == primary_event_id
                else "supporting"
            ),
            "evidence_event_ids": source_event_ids,
            "revision_reason": "write_memory grouped source events into cycle thread",
        }
        for event_entry in event_entries
    ]


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


# Block: Event link label helper
def _event_link_label(*, previous_kind: str, current_kind: str) -> str:
    if current_kind == "action_result" and previous_kind == "action":
        return "caused_by"
    if current_kind == "external_response" and previous_kind in {"observation", "action_result"}:
        return "reply_to"
    return "continuation"


# Block: Cycle thread key helper
def _cycle_thread_key(cycle_id: str) -> str:
    return f"cycle:{cycle_id}"


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


def _required_non_empty_string(value: Any, message: str) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeError(message)
    return value


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


def _required_signed_score(value: Any, message: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(message)
    numeric_value = float(value)
    if numeric_value < -1.0 or numeric_value > 1.0:
        raise RuntimeError(message)
    return numeric_value
