from __future__ import annotations

import json
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
    LLMError,
    MEMORY_CORRECTION_KIND_VALUES,
    MEMORY_CORRECTION_STATUS_VALUES,
    MEMORY_TYPE_VALUES,
    PRE_SEND_CHECK_OUTCOMES,
    RECALL_FOCUS_VALUES,
    RECALL_PACK_SECTION_NAMES,
    RISK_FLAG_VALUES,
    SCOPE_TYPE_VALUES,
    STATE_GROUNDING_EVIDENCE_KINDS,
    TIME_REFERENCE_VALUES,
    VISUAL_OBSERVATION_CHANGE_BASIS_VALUES,
    VISUAL_OBSERVATION_CHANGE_STATE_VALUES,
    WORLD_STATE_HINT_VALUES,
    WORLD_STATE_TTL_HINT_VALUES,
    USER_WORLD_REPORT_TYPES,
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
                        "target_person_ref": nullable({"type": "string"}),
                        "target_interaction_ref": nullable({"type": "string"}),
                        "query_terms": string_array(),
                    }
                ),
            }
        ),
    )


def decision_response_format(*, comparison_scope: str = "full") -> dict[str, Any]:
    if comparison_scope not in DECISION_COMPARISON_SCOPE_KINDS:
        raise ValueError(f"unsupported comparison_scope: {comparison_scope}")
    kind_values = DECISION_COMPARISON_SCOPE_KINDS[comparison_scope]
    capability_request = nullable(
        closed_object(
            {
                "capability_id": {"type": "string"},
                "input": json_object_text(
                    description="required_input に対応する JSON object を表す文字列。"
                ),
            }
        )
    )
    return structured_response_format(
        f"decision_{comparison_scope}" if comparison_scope != "full" else "decision",
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
                "autonomous_run": nullable(
                    closed_object(
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
                ),
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
    return structured_response_format(
        "autonomous_step",
        closed_object(
            {
                "action": closed_object(
                    {
                        "kind": string_enum({"capability_request", "speech", "none"}),
                        "capability_request": nullable(
                            closed_object(
                                {
                                    "capability_id": {"type": "string"},
                                    "input": json_object_text(
                                        description="required_input に対応する JSON object を表す文字列。"
                                    ),
                                }
                            )
                        ),
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


def speech_grounding_review_response_format() -> dict[str, Any]:
    return structured_response_format(
        "speech_grounding_review",
        closed_object({
            "reason_summary": {"type": "string"},
            "outcome": string_enum(["allow", "rewrite"]),
            "speech_text": nullable({"type": "string"}),
        }),
    )


def future_action_alignment_review_response_format() -> dict[str, Any]:
    return structured_response_format(
        "future_action_alignment_review",
        closed_object({
            "outcome": string_enum(["aligned", "requires_autonomous_run"]),
            "reason_summary": {"type": "string"},
        }),
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


def autonomous_start_review_response_format() -> dict[str, Any]:
    return structured_response_format(
        "autonomous_start_review",
        closed_object({
            "outcome": string_enum(["allow_start", "reject_start"]),
            "reason_summary": {"type": "string"},
        }),
    )


def autonomous_activity_alignment_review_response_format() -> dict[str, Any]:
    return structured_response_format(
        "autonomous_activity_alignment_review",
        closed_object({
            "outcome": string_enum(["allow", "reject"]),
            "reason_summary": {"type": "string"},
        }),
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


def memory_candidate_review_response_format() -> dict[str, Any]:
    return structured_response_format(
        "memory_candidate_review",
        closed_object({
            "episode_review": closed_object({
                "summary_text": {"type": "string"},
                "outcome_text": nullable({"type": "string"}),
                "open_loops": string_array(),
                "reason_summary": {"type": "string"},
            }),
            "correction_review": closed_object({
                "prior_claim_assessment": string_enum(["contradicted", "consistent", "undetermined", "not_reviewed"]),
                "contradicted_revision_ids": string_array(),
                "replacement_candidate_indices": {"type": "array", "items": {"type": "integer"}},
                "reason_summary": {"type": "string"},
            }),
        }),
    )


def memory_retention_review_response_format() -> dict[str, Any]:
    return structured_response_format("memory_retention_review", closed_object({
            "decisions": {
                "type": "array",
                "items": closed_object({
                    "reason_summary": {"type": "string"},
                    "retention_basis": string_enum(["explicit_pattern", "repeated_experience", "future_commitment", "current_episode", "unsupported"]),
                    "index": {"type": "integer"},
                }),
            },
    }))


def _episode_affect_schema() -> dict[str, Any]:
    return closed_object({
        "target_scope_type": string_enum(SCOPE_TYPE_VALUES),
        "target_scope_key": {"type": "string"},
        "affect_label": {"type": "string"},
        "vad": closed_object({
            "v": {"type": "number", "minimum": -1, "maximum": 1},
            "a": {"type": "number", "minimum": -1, "maximum": 1},
            "d": {"type": "number", "minimum": -1, "maximum": 1},
        }),
        "intensity": {"type": "number", "minimum": 0, "maximum": 1},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "summary_text": {"type": "string"},
    })


def affect_review_response_format() -> dict[str, Any]:
    return structured_response_format(
        "affect_review",
        closed_object({
            "self_reaction": closed_object({
                "affect": nullable(_episode_affect_schema()),
                "reason_summary": {"type": "string"},
            }),
            "other_affects": {
                "type": "array",
                "maxItems": 4,
                "items": _episode_affect_schema(),
            },
            "reason_summary": {"type": "string"},
        }),
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
                            "qualifiers_hint": json_object_text(
                                description="補助情報の JSON object を表す文字列。空の object は {}。"
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
                    "items": _episode_affect_schema(),
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
                "section_selection": {
                    "type": "array",
                    "description": "採らない section は載せない。candidate_refs は空配列にしない。",
                    "items": closed_object(
                        {
                            "section_name": string_enum(RECALL_PACK_SECTION_NAMES),
                            "candidate_refs": string_array(min_items=1),
                        }
                    ),
                },
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


def decision_grounding_review_response_format() -> dict[str, Any]:
    return structured_response_format("decision_grounding_review", closed_object({
        "outcome": string_enum({"allow", "reconsider"}),
        "reason_summary": {"type": "string"},
    }))


def world_state_source_selection_response_format() -> dict[str, Any]:
    return structured_response_format("world_state_source_selection", closed_object({
        "reported_states": {"type": "array", "maxItems": 5, "items": closed_object({
            "state_type": string_enum(USER_WORLD_REPORT_TYPES),
            "evidence_text": {"type": "string"},
        })},
    }))


def state_grounding_review_response_format() -> dict[str, Any]:
    return structured_response_format(
        "state_grounding_review",
        closed_object({"decisions": {"type": "array", "maxItems": 4, "items": closed_object({
            "reason_summary": {"type": "string"},
            "evidence_kind": string_enum(STATE_GROUNDING_EVIDENCE_KINDS),
            "index": {"type": "integer"},
        })}}),
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
        "decision_self_activity": decision_response_format(comparison_scope="self_activity"),
        "decision_outward_speech": decision_response_format(comparison_scope="outward_speech"),
        "autonomous_step": autonomous_step_response_format(),
        "disclosure_review": disclosure_review_response_format(),
        "speech_grounding_review": speech_grounding_review_response_format(),
        "future_action_alignment_review": future_action_alignment_review_response_format(),
        "pre_send_check": pre_send_check_response_format(),
        "autonomous_completion_review": autonomous_completion_review_response_format(),
        "autonomous_start_review": autonomous_start_review_response_format(),
        "autonomous_activity_alignment_review": autonomous_activity_alignment_review_response_format(),
        "memory_interpretation": memory_interpretation_response_format(),
        "memory_candidate_review": memory_candidate_review_response_format(),
        "memory_retention_review": memory_retention_review_response_format(),
        "affect_review": affect_review_response_format(),
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


def json_object_text(*, description: str) -> dict[str, Any]:
    return {
        "type": "string",
        "description": description,
    }


_DECISION_SCHEMA_NAMES = frozenset(
    {
        "decision",
        "decision_self_activity",
        "decision_outward_speech",
    }
)


def materialize_provider_open_maps(payload: dict[str, Any], *, schema_name: str) -> None:
    # strict structured output は開いた object を受けない。文字列で受けた map を意味検証の前に object へ戻す。
    if schema_name in _DECISION_SCHEMA_NAMES:
        _normalize_decision_supporting_refs(payload)
        _materialize_capability_request_input(
            payload.get("capability_request"),
            label="Decision capability_request.input",
        )
        return
    if schema_name == "autonomous_step":
        action = payload.get("action")
        if isinstance(action, dict):
            _materialize_capability_request_input(
                action.get("capability_request"),
                label="AutonomousStep action.capability_request.input",
            )
        return
    if schema_name != "memory_interpretation":
        return
    units = payload.get("candidate_memory_units")
    if not isinstance(units, list):
        return
    for index, unit in enumerate(units):
        if not isinstance(unit, dict) or "qualifiers_hint" not in unit:
            continue
        unit["qualifiers_hint"] = _json_object_text_to_dict(
            unit["qualifiers_hint"],
            label=f"MemoryInterpretation candidate_memory_units[{index}].qualifiers_hint",
        )


def _materialize_capability_request_input(request: Any, *, label: str) -> None:
    if not isinstance(request, dict) or "input" not in request:
        return
    request["input"] = _json_object_text_to_dict(request["input"], label=label)


def _json_object_text_to_dict(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, str):
        raise LLMError(f"{label} は JSON object を表す文字列である必要があります。")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise LLMError(f"{label} は JSON object として読めません。") from exc
    if not isinstance(parsed, dict):
        raise LLMError(f"{label} は JSON object である必要があります。")
    return parsed


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


def _normalize_decision_supporting_refs(payload: dict[str, Any]) -> None:
    selection = payload.get("foreground_selection")
    if not isinstance(selection, dict):
        return
    supporting = selection.get("supporting_factor_refs")
    if not isinstance(supporting, list) or not all(isinstance(ref, str) for ref in supporting):
        return
    primary = selection.get("primary_factor_ref")
    selection["supporting_factor_refs"] = list(dict.fromkeys(ref for ref in supporting if ref != primary))


def _schema_name(operation: str) -> str:
    return "".join(character if character.isalnum() or character == "_" else "_" for character in operation)
