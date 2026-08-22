from __future__ import annotations

from typing import Any

from otomekairo.llm.contracts import (
    ACTIVITY_ACTOR_VALUES,
    ACTIVITY_TRANSITION_VALUES,
    ANSWER_BOUNDARY_VALUES,
    ANSWER_CONTRACT_VALUES,
    ANSWER_TARGET_ACTOR_VALUES,
    AUTONOMOUS_COMPLETION_REVIEW_OUTCOMES,
    DECISION_COMPARISON_SCOPE_KINDS,
    DECISION_TARGET_STANCE_VALUES,
    DECISION_TARGET_VALUES,
    DISCLOSURE_REVIEW_OUTCOMES,
    INITIATIVE_ENTRY_BASIS_VALUES,
    MEMORY_CORRECTION_KIND_VALUES,
    MEMORY_CORRECTION_STATUS_VALUES,
    MEMORY_TYPE_VALUES,
    PRE_SEND_CHECK_OUTCOMES,
    RECALL_FOCUS_VALUES,
    RISK_FLAG_VALUES,
    SCOPE_TYPE_VALUES,
    TIME_REFERENCE_VALUES,
    VISUAL_OBSERVATION_CHANGE_BASIS_VALUES,
    VISUAL_OBSERVATION_CHANGE_STATE_VALUES,
    WORLD_STATE_HINT_VALUES,
    WORLD_STATE_TTL_HINT_VALUES,
)


SCHEMA_DIALECT_KEYS = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "minItems",
        "maxItems",
        "enum",
        "minimum",
        "maximum",
        "title",
        "description",
    }
)


def structured_response_format(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": _schema_name(name),
            "strict": True,
            "schema": schema,
        },
    }


def response_format_schema_name(response_format: dict[str, Any]) -> str:
    json_schema = response_format.get("json_schema")
    if not isinstance(json_schema, dict):
        return ""
    name = json_schema.get("name")
    if not isinstance(name, str):
        return ""
    return name


def agent_skill_selection_response_format() -> dict[str, Any]:
    return structured_response_format(
        "agent_skill_selection",
        closed_object(
            {
                "selected_skill_ids": string_array(),
                "reason_summary": {"type": "string"},
            }
        ),
    )


def agent_skill_material_selection_response_format() -> dict[str, Any]:
    return structured_response_format(
        "agent_skill_material_selection",
        closed_object(
            {
                "additional_skill_ids": string_array(),
                "resource_reads": {
                    "type": "array",
                    "items": closed_object(
                        {
                            "skill_id": {"type": "string"},
                            "path": {"type": "string"},
                        }
                    ),
                },
                "reason_summary": {"type": "string"},
            }
        ),
    )


def input_interpretation_response_format() -> dict[str, Any]:
    return structured_response_format(
        "input_interpretation",
        closed_object(
            {
                "recall_hint": closed_object(
                    {
                        "primary_recall_focus": string_enum(RECALL_FOCUS_VALUES),
                        "secondary_recall_focuses": string_array(item_enum=RECALL_FOCUS_VALUES),
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "time_reference": string_enum(TIME_REFERENCE_VALUES),
                        "focus_scopes": string_array(),
                        "mentioned_entities": string_array(),
                        "mentioned_topics": string_array(),
                        "risk_flags": string_array(item_enum=RISK_FLAG_VALUES),
                    }
                ),
                "answer_contract": closed_object(
                    {
                        "contract": string_enum(ANSWER_CONTRACT_VALUES),
                        "reason_codes": string_array(),
                        "boundary": string_enum(ANSWER_BOUNDARY_VALUES),
                        "target_actor": string_enum(ANSWER_TARGET_ACTOR_VALUES),
                        "query_terms": string_array(),
                    }
                ),
            }
        ),
    )


def decision_response_format(
    *,
    comparison_scope: str = "full",
) -> dict[str, Any]:
    capability_request_object = closed_object(
        {
            "capability_id": {"type": "string"},
            "input": open_object(
                description="capability の request-local input。object であり、JSON 文字列ではない。"
            ),
        }
    )
    return _decision_response_format(
        comparison_scope=comparison_scope,
        capability_request_object=capability_request_object,
        name_suffix="",
    )


def decision_choice_response_format(
    *,
    comparison_scope: str = "full",
) -> dict[str, Any]:
    return _decision_response_format(
        comparison_scope=comparison_scope,
        capability_request_object=closed_object(
            {
                "capability_id": {"type": "string"},
                "target_ref": nullable({"type": "string"}),
            }
        ),
        name_suffix="_choice",
    )


def _decision_response_format(
    *,
    comparison_scope: str,
    capability_request_object: dict[str, Any],
    name_suffix: str,
) -> dict[str, Any]:
    if comparison_scope not in DECISION_COMPARISON_SCOPE_KINDS:
        raise ValueError(f"unsupported comparison_scope: {comparison_scope}")
    kind_values = DECISION_COMPARISON_SCOPE_KINDS[comparison_scope]
    capability_request = (
        nullable(capability_request_object)
        if "capability_request" in kind_values
        else {"type": "null"}
    )
    autonomous_run_object = closed_object(
        {
            "objective_summary": {"type": "string"},
            "initial_step_summary": {"type": "string"},
            "coordination": closed_object(
                {
                    "mode": string_enum({"create_new", "replace_existing"}),
                    "target_run_ids": string_array(),
                    "reason_summary": {"type": "string"},
                }
            ),
        }
    )
    autonomous_run = (
        nullable(autonomous_run_object)
        if "autonomous_run" in kind_values
        else {"type": "null"}
    )
    schema_name = f"decision_{comparison_scope}" if comparison_scope != "full" else "decision"
    return structured_response_format(
        schema_name + name_suffix,
        closed_object(
            {
                "kind": string_enum(kind_values),
                "reason_code": {"type": "string"},
                "reason_summary": {"type": "string"},
                "requires_confirmation": {"type": "boolean"},
                "pending_intent": nullable(
                    closed_object(
                        {
                            "intent_kind": {"type": "string"},
                            "intent_summary": {"type": "string"},
                            "dedupe_key": {"type": "string"},
                        }
                    )
                ),
                "capability_request": capability_request,
                "autonomous_run": autonomous_run,
                "foreground_selection": closed_object(
                    {
                        "primary_factor_ref": nullable({"type": "string"}),
                        "supporting_factor_refs": string_array(max_items=3),
                        "suppressed_factors": {
                            "type": "array",
                            "maxItems": 5,
                            "items": closed_object(
                                {
                                    "factor_ref": {"type": "string"},
                                    "reason_summary": {"type": "string"},
                                }
                            ),
                        },
                        "summary_text": {"type": "string"},
                    }
                ),
                "target_stances": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 2,
                    "items": closed_object(
                        {
                            "target": string_enum(DECISION_TARGET_VALUES),
                            "stance": string_enum(DECISION_TARGET_STANCE_VALUES),
                            "reason_summary": {"type": "string"},
                        }
                    ),
                },
            }
        ),
    )


def autonomous_step_response_format() -> dict[str, Any]:
    return _autonomous_step_response_format(
        capability_request_object=closed_object(
            {
                "capability_id": {"type": "string"},
                "input": open_object(
                    description=(
                        "capability の request-local input。"
                        "object であり、JSON 文字列ではない。"
                    )
                ),
            }
        ),
        name="autonomous_step",
    )


def autonomous_step_choice_response_format() -> dict[str, Any]:
    return _autonomous_step_response_format(
        capability_request_object=closed_object(
            {
                "capability_id": {"type": "string"},
                "target_ref": nullable({"type": "string"}),
            }
        ),
        name="autonomous_step_choice",
    )


def _autonomous_step_response_format(
    *,
    capability_request_object: dict[str, Any],
    name: str,
) -> dict[str, Any]:
    return structured_response_format(
        name,
        closed_object(
            {
                "action": closed_object(
                    {
                        "kind": string_enum({"capability_request", "speech", "none"}),
                        "capability_request": nullable(capability_request_object),
                        "speech": nullable(
                            closed_object(
                                {
                                    "reason_code": {"type": "string"},
                                    "reason_summary": {"type": "string"},
                                }
                            )
                        ),
                    }
                ),
                "transition": closed_object(
                    {
                        "kind": string_enum({"continue", "wait_until", "complete", "cancel"}),
                        "next_run_at": nullable({"type": "string"}),
                    }
                ),
                "run_update": closed_object(
                    {
                        "current_step_summary": {"type": "string"},
                        "history_summary": {"type": "string"},
                    }
                ),
            }
        ),
    )


def capability_input_response_format() -> dict[str, Any]:
    return structured_response_format(
        "capability_input",
        closed_object(
            {
                "input": open_object(
                    description="選択済み capability の未固定 request-local input。"
                )
            }
        ),
    )


def disclosure_review_response_format() -> dict[str, Any]:
    return structured_response_format(
        "disclosure_review",
        closed_object(
            {
                "outcome": string_enum(DISCLOSURE_REVIEW_OUTCOMES),
                "speech_text": nullable({"type": "string"}),
                "reason_code": {"type": "string"},
            }
        ),
    )


def pre_send_check_response_format() -> dict[str, Any]:
    return structured_response_format(
        "pre_send_check",
        closed_object(
            {
                "outcome": string_enum(PRE_SEND_CHECK_OUTCOMES),
                "reason_summary": {"type": "string"},
            }
        ),
    )


def autonomous_completion_review_response_format() -> dict[str, Any]:
    return structured_response_format(
        "autonomous_completion_review",
        closed_object(
            {
                "outcome": string_enum(AUTONOMOUS_COMPLETION_REVIEW_OUTCOMES),
                "reason_summary": {"type": "string"},
            }
        ),
    )


def memory_interpretation_response_format() -> dict[str, Any]:
    return structured_response_format(
        "memory_interpretation",
        closed_object(
            {
                "episode": closed_object(
                    {
                        "episode_type": {"type": "string"},
                        "episode_series_id": nullable({"type": "string"}),
                        "primary_scope_type": string_enum(SCOPE_TYPE_VALUES),
                        "primary_scope_key": {"type": "string"},
                        "summary_text": {"type": "string"},
                        "outcome_text": nullable({"type": "string"}),
                        "open_loops": string_array(),
                        "salience": {"type": "number"},
                    }
                ),
                "candidate_memory_units": {
                    "type": "array",
                    "items": closed_object(
                        {
                            "memory_type": string_enum(MEMORY_TYPE_VALUES),
                            "scope": string_enum(SCOPE_TYPE_VALUES),
                            "subject_hint": {
                                "type": "string",
                                "description": (
                                    "null にはしない。scope=self は self、"
                                    "scope=entity は person:/place:/tool:、"
                                    "scope=topic は topic:<key>、"
                                    "scope=world は短い主語、"
                                    "scope=relationship は self|<person_ref>。"
                                ),
                            },
                            "predicate_hint": {"type": "string"},
                            "object_hint": nullable({"type": "string"}),
                            "qualifiers_hint": open_object(
                                description="記憶ヒントの付加情報。object であり、null ではない。"
                            ),
                            "summary_text": {"type": "string"},
                            "evidence_text": {"type": "string"},
                            "confidence_hint": string_enum({"low", "medium", "high"}),
                        }
                    ),
                },
                "episode_affects": {
                    "type": "array",
                    "maxItems": 4,
                    "items": closed_object(
                        {
                            "target_scope_type": string_enum(SCOPE_TYPE_VALUES),
                            "target_scope_key": {"type": "string"},
                            "affect_label": {"type": "string"},
                            "vad": closed_object(
                                {
                                    "v": {"type": "number"},
                                    "a": {"type": "number"},
                                    "d": {"type": "number"},
                                }
                            ),
                            "intensity": {"type": "number", "minimum": 0, "maximum": 1},
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                            "summary_text": {"type": "string"},
                        }
                    ),
                },
                "correction_status": string_enum(MEMORY_CORRECTION_STATUS_VALUES),
                "selected_targets": {
                    "type": "array",
                    "maxItems": 8,
                    "items": closed_object(
                        {
                            "revision_id": {"type": "string"},
                            "memory_unit_id": {"type": "string"},
                            "correction_kind": string_enum(MEMORY_CORRECTION_KIND_VALUES),
                            "reason_summary": {"type": "string"},
                        }
                    ),
                },
            }
        ),
    )


def memory_reflection_summary_response_format() -> dict[str, Any]:
    return structured_response_format(
        "memory_reflection_summary",
        closed_object(
            {
                "summaries": {
                    "type": "array",
                    "items": closed_object(
                        {
                            "scope_ref": {"type": "string"},
                            "summary_text": {"type": "string"},
                        }
                    ),
                }
            }
        ),
    )


def event_evidence_response_format() -> dict[str, Any]:
    slot = nullable({"type": "string"})
    return structured_response_format(
        "event_evidence",
        closed_object(
            {
                "evidence": {
                    "type": "array",
                    "maxItems": 16,
                    "items": closed_object(
                        {
                            "event_ref": {"type": "string"},
                            "anchor": slot,
                            "topic": slot,
                            "decision_or_result": slot,
                            "tone_or_note": slot,
                        }
                    ),
                }
            }
        ),
    )


def recall_pack_selection_response_format() -> dict[str, Any]:
    return structured_response_format(
        "recall_pack_selection",
        closed_object(
            {
                "selected_candidate_refs": string_array(),
                "conflict_summaries": {
                    "type": "array",
                    "items": closed_object(
                        {
                            "conflict_ref": {"type": "string"},
                            "summary_text": {"type": "string"},
                        }
                    ),
                },
            }
        ),
    )


def pending_intent_selection_response_format() -> dict[str, Any]:
    return structured_response_format(
        "pending_intent_selection",
        closed_object(
            {
                "selected_candidate_ref": {"type": "string"},
                "selection_reason": {"type": "string"},
            }
        ),
    )


def initiative_entry_check_response_format() -> dict[str, Any]:
    return structured_response_format(
        "initiative_entry_check",
        closed_object(
            {
                "entry_kind": string_enum({"enter", "skip"}),
                "entry_basis": string_enum(INITIATIVE_ENTRY_BASIS_VALUES),
                "reason_summary": {"type": "string"},
            }
        ),
    )


def world_state_response_format() -> dict[str, Any]:
    return structured_response_format(
        "world_state",
        closed_object(
            {
                "state_candidates": {
                    "type": "array",
                    "maxItems": 4,
                    "items": closed_object(
                        {
                            "candidate_ref": {"type": "string"},
                            "summary_text": {"type": "string"},
                            "confidence_hint": string_enum(WORLD_STATE_HINT_VALUES),
                            "salience_hint": string_enum(WORLD_STATE_HINT_VALUES),
                            "ttl_hint": string_enum(WORLD_STATE_TTL_HINT_VALUES),
                        }
                    ),
                }
            }
        ),
    )


def activity_state_response_format() -> dict[str, Any]:
    return structured_response_format(
        "activity_state",
        closed_object(
            {
                "activity_candidates": {
                    "type": "array",
                    "maxItems": 1,
                    "items": closed_object(
                        {
                            "actor": string_enum(ACTIVITY_ACTOR_VALUES),
                            "label": {"type": "string"},
                            "target": {
                                "type": "string",
                                "description": "不明なときは空文字。null にはしない。",
                            },
                            "confidence_hint": string_enum(WORLD_STATE_HINT_VALUES),
                            "salience_hint": string_enum(WORLD_STATE_HINT_VALUES),
                            "ttl_hint": string_enum(WORLD_STATE_TTL_HINT_VALUES),
                            "transition": string_enum(ACTIVITY_TRANSITION_VALUES),
                            "reason_summary": {"type": "string"},
                        }
                    ),
                }
            }
        ),
    )


def visual_observation_response_format() -> dict[str, Any]:
    return structured_response_format(
        "visual_observation",
        closed_object(
            {
                "summary_text": {"type": "string"},
                "confidence_hint": string_enum(WORLD_STATE_HINT_VALUES),
                "change_state": string_enum(VISUAL_OBSERVATION_CHANGE_STATE_VALUES),
                "change_basis": string_enum(VISUAL_OBSERVATION_CHANGE_BASIS_VALUES),
                "change_reason_summary": {"type": "string"},
            }
        ),
    )


def all_response_formats() -> dict[str, dict[str, Any]]:
    formats = {
        "agent_skill_selection": agent_skill_selection_response_format(),
        "agent_skill_material_selection": agent_skill_material_selection_response_format(),
        "input_interpretation": input_interpretation_response_format(),
        "decision": decision_response_format(comparison_scope="full"),
        "decision_choice": decision_choice_response_format(comparison_scope="full"),
        "decision_self_activity": decision_response_format(comparison_scope="self_activity"),
        "decision_self_activity_choice": decision_choice_response_format(
            comparison_scope="self_activity"
        ),
        "decision_outward_speech": decision_response_format(comparison_scope="outward_speech"),
        "decision_outward_speech_choice": decision_choice_response_format(
            comparison_scope="outward_speech"
        ),
        "autonomous_step": autonomous_step_response_format(),
        "autonomous_step_choice": autonomous_step_choice_response_format(),
        "capability_input": capability_input_response_format(),
        "disclosure_review": disclosure_review_response_format(),
        "pre_send_check": pre_send_check_response_format(),
        "autonomous_completion_review": autonomous_completion_review_response_format(),
        "memory_interpretation": memory_interpretation_response_format(),
        "memory_reflection_summary": memory_reflection_summary_response_format(),
        "event_evidence": event_evidence_response_format(),
        "recall_pack_selection": recall_pack_selection_response_format(),
        "pending_intent_selection": pending_intent_selection_response_format(),
        "initiative_entry_check": initiative_entry_check_response_format(),
        "world_state": world_state_response_format(),
        "activity_state": activity_state_response_format(),
        "visual_observation": visual_observation_response_format(),
    }
    return formats


def closed_object(properties: dict[str, Any], *, description: str | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "required": list(properties.keys()),
        "additionalProperties": False,
    }
    if description is not None:
        schema["description"] = description
    return schema


def open_object(*, description: str | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": True,
    }
    if description is not None:
        schema["description"] = description
    return schema


def nullable(schema: dict[str, Any]) -> dict[str, Any]:
    merged = dict(schema)
    current_type = merged.get("type")
    if isinstance(current_type, str):
        merged["type"] = [current_type, "null"]
    elif isinstance(current_type, list):
        types = list(current_type)
        if "null" not in types:
            types.append("null")
        merged["type"] = types
    else:
        merged["type"] = ["null"]
    return merged


def string_enum(values: set[str] | frozenset[str] | tuple[str, ...] | list[str]) -> dict[str, Any]:
    return {"type": "string", "enum": sorted(values)}


def string_array(
    *,
    min_items: int | None = None,
    max_items: int | None = None,
    item_enum: set[str] | frozenset[str] | tuple[str, ...] | list[str] | None = None,
) -> dict[str, Any]:
    items: dict[str, Any] = {"type": "string"}
    if item_enum is not None:
        items["enum"] = sorted(item_enum)
    schema: dict[str, Any] = {
        "type": "array",
        "items": items,
    }
    if min_items is not None:
        schema["minItems"] = min_items
    if max_items is not None:
        schema["maxItems"] = max_items
    return schema


def _schema_name(operation: str) -> str:
    return "".join(character if character.isalnum() or character == "_" else "_" for character in operation)
