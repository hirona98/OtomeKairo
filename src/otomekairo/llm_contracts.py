from __future__ import annotations

from typing import Any


# Block: Errors
class LLMError(Exception):
    pass


# Block: Config
INTENT_VALUES = {
    "smalltalk",
    "reminisce",
    "commitment_check",
    "consult",
    "check_state",
    "preference_query",
    "fact_query",
    "meta_relationship",
}

TIME_REFERENCE_VALUES = {
    "none",
    "recent",
    "past",
    "future",
    "persistent",
}

MEMORY_TYPE_VALUES = {
    "fact",
    "preference",
    "relation",
    "commitment",
    "interpretation",
    "summary",
}

MEMORY_STATUS_VALUES = {
    "inferred",
    "confirmed",
    "superseded",
    "revoked",
    "dormant",
}

COMMITMENT_STATE_VALUES = {
    "open",
    "waiting_confirmation",
    "on_hold",
    "done",
    "cancelled",
}

AFFECT_LAYER_VALUES = {
    "surface",
    "background",
}
MAX_SECONDARY_INTENTS = 2
MAX_HINT_SCOPE_VALUES = 4


# Block: HelperValidation
def _validate_exact_keys(value: Any, required_keys: set[str], label: str) -> None:
    # Block: Shape
    if not isinstance(value, dict):
        raise LLMError(f"{label} must be an object.")

    # Block: KeyCheck
    actual_keys = set(value.keys())
    if actual_keys == required_keys:
        return

    # Block: Detail
    missing_keys = sorted(required_keys - actual_keys)
    extra_keys = sorted(actual_keys - required_keys)
    details: list[str] = []
    if missing_keys:
        details.append(f"missing={','.join(missing_keys)}")
    if extra_keys:
        details.append(f"extra={','.join(extra_keys)}")
    raise LLMError(f"{label} keys are invalid ({'; '.join(details)}).")


# Block: RecallHintValidation
def validate_recall_hint_contract(payload: dict[str, Any]) -> None:
    # Block: RequiredKeys
    required_keys = {
        "primary_intent",
        "secondary_intents",
        "confidence",
        "time_reference",
        "focus_scopes",
        "mentioned_entities",
        "mentioned_topics",
    }
    if set(payload.keys()) != required_keys:
        raise LLMError("RecallHint keys do not match the contract.")

    # Block: ValueChecks
    if payload["primary_intent"] not in INTENT_VALUES:
        raise LLMError("RecallHint primary_intent is invalid.")
    if payload["time_reference"] not in TIME_REFERENCE_VALUES:
        raise LLMError("RecallHint time_reference is invalid.")
    if not isinstance(payload["secondary_intents"], list):
        raise LLMError("RecallHint secondary_intents must be a list.")
    if len(payload["secondary_intents"]) > MAX_SECONDARY_INTENTS:
        raise LLMError("RecallHint secondary_intents exceed the maximum length.")
    for intent in payload["secondary_intents"]:
        if not isinstance(intent, str) or not intent.strip():
            raise LLMError("RecallHint secondary_intents entries must be non-empty strings.")
        if intent not in INTENT_VALUES:
            raise LLMError("RecallHint secondary_intent is invalid.")
    if len(payload["secondary_intents"]) != len(set(payload["secondary_intents"])):
        raise LLMError("RecallHint secondary_intents contain duplicates.")
    if payload["primary_intent"] in payload["secondary_intents"]:
        raise LLMError("RecallHint duplicates primary intent.")
    if not isinstance(payload["focus_scopes"], list):
        raise LLMError("RecallHint focus_scopes must be a list.")
    if len(payload["focus_scopes"]) > MAX_HINT_SCOPE_VALUES:
        raise LLMError("RecallHint focus_scopes exceed the maximum length.")
    if any(not isinstance(scope, str) or not scope.strip() for scope in payload["focus_scopes"]):
        raise LLMError("RecallHint focus_scopes entries must be non-empty strings.")
    if not isinstance(payload["mentioned_entities"], list):
        raise LLMError("RecallHint mentioned_entities must be a list.")
    if len(payload["mentioned_entities"]) > MAX_HINT_SCOPE_VALUES:
        raise LLMError("RecallHint mentioned_entities exceed the maximum length.")
    if any(not isinstance(entity, str) or not entity.strip() for entity in payload["mentioned_entities"]):
        raise LLMError("RecallHint mentioned_entities entries must be non-empty strings.")
    if not isinstance(payload["mentioned_topics"], list):
        raise LLMError("RecallHint mentioned_topics must be a list.")
    if len(payload["mentioned_topics"]) > MAX_HINT_SCOPE_VALUES:
        raise LLMError("RecallHint mentioned_topics exceed the maximum length.")
    if any(not isinstance(topic, str) or not topic.strip() for topic in payload["mentioned_topics"]):
        raise LLMError("RecallHint mentioned_topics entries must be non-empty strings.")
    if not isinstance(payload["confidence"], (int, float)):
        raise LLMError("RecallHint confidence must be numeric.")
    if not 0.0 <= float(payload["confidence"]) <= 1.0:
        raise LLMError("RecallHint confidence must be between 0.0 and 1.0.")


# Block: DecisionValidation
def validate_decision_contract(payload: dict[str, Any]) -> None:
    # Block: RequiredKeys
    required_keys = {
        "kind",
        "reason_code",
        "reason_summary",
        "requires_confirmation",
        "future_act",
    }
    _validate_exact_keys(payload, required_keys, "Decision")

    # Block: ValueChecks
    if payload["kind"] not in {"reply", "noop", "future_act"}:
        raise LLMError("Decision kind is invalid.")
    if not isinstance(payload["reason_code"], str) or not payload["reason_code"].strip():
        raise LLMError("Decision reason_code must be a non-empty string.")
    if not isinstance(payload["reason_summary"], str) or not payload["reason_summary"].strip():
        raise LLMError("Decision reason_summary must be a non-empty string.")
    if not isinstance(payload["requires_confirmation"], bool):
        raise LLMError("Decision requires_confirmation must be a boolean.")
    if payload["kind"] == "future_act":
        future_act = payload["future_act"]
        required_future_keys = {
            "intent_kind",
            "intent_summary",
            "dedupe_key",
        }
        if not isinstance(future_act, dict) or set(future_act.keys()) != required_future_keys:
            raise LLMError("Decision future_act is invalid.")
        for key in required_future_keys:
            value = future_act.get(key)
            if not isinstance(value, str) or not value.strip():
                raise LLMError(f"Decision future_act.{key} must be a non-empty string.")
        if payload["requires_confirmation"]:
            raise LLMError("Decision future_act cannot require confirmation.")
    elif payload["future_act"] is not None:
        raise LLMError("Decision future_act must be null unless kind is future_act.")


# Block: MemoryInterpretationValidation
def validate_memory_interpretation_contract(payload: dict[str, Any]) -> None:
    # Block: RequiredKeys
    required_keys = {
        "episode_digest",
        "candidate_memory_units",
        "affect_updates",
    }
    _validate_exact_keys(payload, required_keys, "MemoryInterpretation")

    # Block: EpisodeDigestValidation
    episode_digest = payload["episode_digest"]
    required_episode_keys = {
        "episode_type",
        "primary_scope_type",
        "primary_scope_key",
        "summary_text",
        "outcome_text",
        "open_loops",
        "salience",
    }
    _validate_exact_keys(episode_digest, required_episode_keys, "MemoryInterpretation episode_digest")
    if not isinstance(episode_digest["summary_text"], str) or not episode_digest["summary_text"].strip():
        raise LLMError("MemoryInterpretation episode_digest.summary_text is invalid.")
    if episode_digest["outcome_text"] is not None and not isinstance(episode_digest["outcome_text"], str):
        raise LLMError("MemoryInterpretation episode_digest.outcome_text is invalid.")
    if not isinstance(episode_digest["open_loops"], list):
        raise LLMError("MemoryInterpretation episode_digest.open_loops must be a list.")
    if not isinstance(episode_digest["salience"], (int, float)):
        raise LLMError("MemoryInterpretation episode_digest.salience must be numeric.")

    # Block: CandidateValidation
    if not isinstance(payload["candidate_memory_units"], list):
        raise LLMError("MemoryInterpretation candidate_memory_units must be a list.")
    for candidate in payload["candidate_memory_units"]:
        required_candidate_keys = {
            "memory_type",
            "scope_type",
            "scope_key",
            "subject_ref",
            "predicate",
            "object_ref_or_value",
            "summary_text",
            "status",
            "commitment_state",
            "confidence",
            "salience",
            "valid_from",
            "valid_to",
            "qualifiers",
            "reason",
        }
        _validate_exact_keys(candidate, required_candidate_keys, "MemoryInterpretation candidate_memory_unit")
        if candidate["memory_type"] not in MEMORY_TYPE_VALUES:
            raise LLMError("MemoryInterpretation candidate_memory_unit.memory_type is invalid.")
        if candidate["status"] not in MEMORY_STATUS_VALUES:
            raise LLMError("MemoryInterpretation candidate_memory_unit.status is invalid.")
        if not isinstance(candidate["scope_type"], str) or not candidate["scope_type"].strip():
            raise LLMError("MemoryInterpretation candidate_memory_unit.scope_type is invalid.")
        if not isinstance(candidate["scope_key"], str) or not candidate["scope_key"].strip():
            raise LLMError("MemoryInterpretation candidate_memory_unit.scope_key is invalid.")
        if not isinstance(candidate["subject_ref"], str) or not candidate["subject_ref"].strip():
            raise LLMError("MemoryInterpretation candidate_memory_unit.subject_ref is invalid.")
        if not isinstance(candidate["predicate"], str) or not candidate["predicate"].strip():
            raise LLMError("MemoryInterpretation candidate_memory_unit.predicate is invalid.")
        if candidate["object_ref_or_value"] is not None and not isinstance(candidate["object_ref_or_value"], str):
            raise LLMError("MemoryInterpretation candidate_memory_unit.object_ref_or_value is invalid.")
        if not isinstance(candidate["summary_text"], str) or not candidate["summary_text"].strip():
            raise LLMError("MemoryInterpretation candidate_memory_unit.summary_text is invalid.")
        if candidate["commitment_state"] is not None and candidate["commitment_state"] not in COMMITMENT_STATE_VALUES:
            raise LLMError("MemoryInterpretation candidate_memory_unit.commitment_state is invalid.")
        if not isinstance(candidate["confidence"], (int, float)):
            raise LLMError("MemoryInterpretation candidate_memory_unit.confidence must be numeric.")
        if not isinstance(candidate["salience"], (int, float)):
            raise LLMError("MemoryInterpretation candidate_memory_unit.salience must be numeric.")
        if candidate["valid_from"] is not None and not isinstance(candidate["valid_from"], str):
            raise LLMError("MemoryInterpretation candidate_memory_unit.valid_from is invalid.")
        if candidate["valid_to"] is not None and not isinstance(candidate["valid_to"], str):
            raise LLMError("MemoryInterpretation candidate_memory_unit.valid_to is invalid.")
        if not isinstance(candidate["qualifiers"], dict):
            raise LLMError("MemoryInterpretation candidate_memory_unit.qualifiers must be an object.")
        if not isinstance(candidate["reason"], str) or not candidate["reason"].strip():
            raise LLMError("MemoryInterpretation candidate_memory_unit.reason is invalid.")

    # Block: AffectValidation
    if not isinstance(payload["affect_updates"], list):
        raise LLMError("MemoryInterpretation affect_updates must be a list.")
    for affect_update in payload["affect_updates"]:
        required_affect_keys = {
            "layer",
            "target_scope_type",
            "target_scope_key",
            "affect_label",
            "intensity",
        }
        _validate_exact_keys(affect_update, required_affect_keys, "MemoryInterpretation affect_update")
        if affect_update["layer"] not in AFFECT_LAYER_VALUES:
            raise LLMError(
                "MemoryInterpretation affect_update.layer is invalid "
                f"(got={affect_update['layer']!r}, expected=surface|background)."
            )
        if not isinstance(affect_update["target_scope_type"], str) or not affect_update["target_scope_type"].strip():
            raise LLMError("MemoryInterpretation affect_update.target_scope_type is invalid.")
        if not isinstance(affect_update["target_scope_key"], str) or not affect_update["target_scope_key"].strip():
            raise LLMError("MemoryInterpretation affect_update.target_scope_key is invalid.")
        if not isinstance(affect_update["affect_label"], str) or not affect_update["affect_label"].strip():
            raise LLMError("MemoryInterpretation affect_update.affect_label is invalid.")
        if not isinstance(affect_update["intensity"], (int, float)):
            raise LLMError("MemoryInterpretation affect_update.intensity must be numeric.")
