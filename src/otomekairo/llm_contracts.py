from __future__ import annotations

import re
from typing import Any


# エラー
class LLMError(Exception):
    pass


class LLMContractError(LLMError):
    pass


# 設定
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
SCOPE_TYPE_VALUES = {
    "self",
    "user",
    "entity",
    "topic",
    "relationship",
    "world",
}
MAX_SECONDARY_INTENTS = 2
MAX_HINT_SCOPE_VALUES = 4
MAX_MEMORY_REFLECTION_SUMMARY_SENTENCES = 2
MAX_MEMORY_REFLECTION_SUMMARY_LENGTH = 140
MAX_EVENT_EVIDENCE_SLOT_SENTENCES = 1
MAX_RECALL_PACK_CONFLICT_SUMMARY_SENTENCES = 1
RECALL_PACK_SECTION_NAMES = (
    "self_model",
    "user_model",
    "relationship_model",
    "active_topics",
    "active_commitments",
    "episodic_evidence",
)
INTERNAL_IDENTIFIER_PATTERN = re.compile(
    r"\b(?:event|episode|memory_unit|cycle|reflection_run|retrieval_run|pending_intent|candidate|conflict):[A-Za-z0-9._-]+\b"
)


# 補助検証
def _validate_exact_keys(value: Any, required_keys: set[str], label: str) -> None:
    # 形状
    if not isinstance(value, dict):
        raise LLMError(f"{label} must be an object.")

    # キー確認
    actual_keys = set(value.keys())
    if actual_keys == required_keys:
        return

    # 詳細
    missing_keys = sorted(required_keys - actual_keys)
    extra_keys = sorted(actual_keys - required_keys)
    details: list[str] = []
    if missing_keys:
        details.append(f"missing={','.join(missing_keys)}")
    if extra_keys:
        details.append(f"extra={','.join(extra_keys)}")
    raise LLMError(f"{label} keys are invalid ({'; '.join(details)}).")


def _has_named_ref_prefix(value: str) -> bool:
    # 名前付き参照プレフィックス
    for prefix in ("person:", "place:", "tool:"):
        if value.startswith(prefix) and value != prefix:
            return True
    return False


def _is_relationship_ref(value: str) -> bool:
    # 中核参照
    if value in {"self", "user"}:
        return True

    # 名前付き参照
    return _has_named_ref_prefix(value)


def _normalized_relationship_refs(values: list[str]) -> list[str]:
    # 順序付け
    unique_values = list(dict.fromkeys(values))
    if "self" in unique_values:
        tail = sorted(value for value in unique_values if value != "self")
        return ["self", *tail]
    return sorted(unique_values)


def _validate_scope_identity(*, scope_type: Any, scope_key: Any, label: str) -> None:
    # 型確認
    if scope_type not in SCOPE_TYPE_VALUES:
        raise LLMError(f"{label}.scope_type is invalid.")
    if not isinstance(scope_key, str) or not scope_key.strip():
        raise LLMError(f"{label}.scope_key is invalid.")

    # 正規化済み
    normalized_scope_key = scope_key.strip()

    # 固定Scopes
    if scope_type == "self" and normalized_scope_key != "self":
        raise LLMError(f"{label}.scope_key must be 'self' when scope_type is self.")
    if scope_type == "user" and normalized_scope_key != "user":
        raise LLMError(f"{label}.scope_key must be 'user' when scope_type is user.")
    if scope_type == "world" and normalized_scope_key != "world":
        raise LLMError(f"{label}.scope_key must be 'world' when scope_type is world.")

    # トピックスコープ
    if scope_type == "topic":
        if not normalized_scope_key.startswith("topic:") or normalized_scope_key == "topic:":
            raise LLMError(f"{label}.scope_key must be topic:<name> when scope_type is topic.")
        return

    # エンティティスコープ
    if scope_type == "entity":
        if not _has_named_ref_prefix(normalized_scope_key):
            raise LLMError(f"{label}.scope_key must be person:/place:/tool: when scope_type is entity.")
        return

    # 関係スコープ
    if scope_type == "relationship":
        refs = normalized_scope_key.split("|")
        if len(refs) < 2:
            raise LLMError(f"{label}.scope_key must join two or more refs with '|'.")
        if any(not _is_relationship_ref(ref) for ref in refs):
            raise LLMError(f"{label}.scope_key contains an invalid relationship ref.")
        if len(refs) != len(set(refs)):
            raise LLMError(f"{label}.scope_key contains duplicate relationship refs.")
        if refs != _normalized_relationship_refs(refs):
            raise LLMError(f"{label}.scope_key must be normalized for relationship scope.")


# recall_hint検証
def validate_recall_hint_contract(payload: dict[str, Any]) -> None:
    # 必須キー群
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

    # 値Checks
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


# decision検証
def validate_decision_contract(payload: dict[str, Any]) -> None:
    # 必須キー群
    required_keys = {
        "kind",
        "reason_code",
        "reason_summary",
        "requires_confirmation",
        "pending_intent",
    }
    _validate_exact_keys(payload, required_keys, "Decision")

    # 値Checks
    if payload["kind"] not in {"reply", "noop", "pending_intent"}:
        raise LLMError("Decision kind is invalid.")
    if not isinstance(payload["reason_code"], str) or not payload["reason_code"].strip():
        raise LLMError("Decision reason_code must be a non-empty string.")
    if not isinstance(payload["reason_summary"], str) or not payload["reason_summary"].strip():
        raise LLMError("Decision reason_summary must be a non-empty string.")
    if not isinstance(payload["requires_confirmation"], bool):
        raise LLMError("Decision requires_confirmation must be a boolean.")
    if payload["kind"] == "pending_intent":
        pending_intent = payload["pending_intent"]
        required_pending_keys = {
            "intent_kind",
            "intent_summary",
            "dedupe_key",
        }
        if not isinstance(pending_intent, dict) or set(pending_intent.keys()) != required_pending_keys:
            raise LLMError("Decision pending_intent is invalid.")
        for key in required_pending_keys:
            value = pending_intent.get(key)
            if not isinstance(value, str) or not value.strip():
                raise LLMError(f"Decision pending_intent.{key} must be a non-empty string.")
        if payload["requires_confirmation"]:
            raise LLMError("Decision pending_intent cannot require confirmation.")
    elif payload["pending_intent"] is not None:
        raise LLMError("Decision pending_intent must be null unless kind is pending_intent.")


# memory interpretation検証
def validate_memory_interpretation_contract(payload: dict[str, Any]) -> None:
    # 必須キー群
    required_keys = {
        "episode",
        "candidate_memory_units",
        "affect_updates",
    }
    _validate_exact_keys(payload, required_keys, "MemoryInterpretation")

    # episode検証
    episode = payload["episode"]
    required_episode_keys = {
        "episode_type",
        "episode_series_id",
        "primary_scope_type",
        "primary_scope_key",
        "summary_text",
        "outcome_text",
        "open_loops",
        "salience",
    }
    _validate_exact_keys(episode, required_episode_keys, "MemoryInterpretation episode")
    if not isinstance(episode["summary_text"], str) or not episode["summary_text"].strip():
        raise LLMError("MemoryInterpretation episode.summary_text is invalid.")
    if episode["episode_series_id"] is not None and (
        not isinstance(episode["episode_series_id"], str) or not episode["episode_series_id"].strip()
    ):
        raise LLMError("MemoryInterpretation episode.episode_series_id is invalid.")
    if episode["outcome_text"] is not None and not isinstance(episode["outcome_text"], str):
        raise LLMError("MemoryInterpretation episode.outcome_text is invalid.")
    if not isinstance(episode["open_loops"], list):
        raise LLMError("MemoryInterpretation episode.open_loops must be a list.")
    if not isinstance(episode["salience"], (int, float)):
        raise LLMError("MemoryInterpretation episode.salience must be numeric.")
    _validate_scope_identity(
        scope_type=episode["primary_scope_type"],
        scope_key=episode["primary_scope_key"],
        label="MemoryInterpretation episode",
    )

    # 候補検証
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
        _validate_scope_identity(
            scope_type=candidate["scope_type"],
            scope_key=candidate["scope_key"],
            label="MemoryInterpretation candidate_memory_unit",
        )
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

    # affect検証
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
        _validate_scope_identity(
            scope_type=affect_update["target_scope_type"],
            scope_key=affect_update["target_scope_key"],
            label="MemoryInterpretation affect_update",
        )
        if not isinstance(affect_update["affect_label"], str) or not affect_update["affect_label"].strip():
            raise LLMError("MemoryInterpretation affect_update.affect_label is invalid.")
        if not isinstance(affect_update["intensity"], (int, float)):
            raise LLMError("MemoryInterpretation affect_update.intensity must be numeric.")


def validate_memory_reflection_summary_contract(payload: dict[str, Any]) -> None:
    # 必須キー群
    _validate_exact_keys(payload, {"summary_text"}, "MemoryReflectionSummary")

    # summary_text
    summary_text = payload["summary_text"]
    if not isinstance(summary_text, str):
        raise LLMError("MemoryReflectionSummary summary_text must be a string.")

    normalized = summary_text.strip()
    if not normalized:
        raise LLMError("MemoryReflectionSummary summary_text must not be empty.")
    if "\n" in normalized or "\r" in normalized:
        raise LLMError("MemoryReflectionSummary summary_text must not contain newlines.")
    if len(normalized) > MAX_MEMORY_REFLECTION_SUMMARY_LENGTH:
        raise LLMError("MemoryReflectionSummary summary_text exceeds the maximum length.")
    if INTERNAL_IDENTIFIER_PATTERN.search(normalized) is not None:
        raise LLMError("MemoryReflectionSummary summary_text must not contain internal identifiers.")

    sentence_count = len([part for part in re.split(r"[。!?！？]+", normalized) if part.strip()])
    if not 1 <= sentence_count <= MAX_MEMORY_REFLECTION_SUMMARY_SENTENCES:
        raise LLMError("MemoryReflectionSummary summary_text must be one or two sentences.")


def validate_event_evidence_contract(payload: dict[str, Any]) -> None:
    # 必須キー群
    required_keys = {
        "anchor",
        "topic",
        "decision_or_result",
        "tone_or_note",
    }
    _validate_exact_keys(payload, required_keys, "EventEvidence")

    # slot 群
    present_slot_count = 0
    for slot_name in ("anchor", "topic", "decision_or_result", "tone_or_note"):
        value = payload[slot_name]
        if value is None:
            continue
        if not isinstance(value, str):
            raise LLMError(f"EventEvidence {slot_name} must be a string or null.")
        normalized = value.strip()
        if not normalized:
            raise LLMError(f"EventEvidence {slot_name} must not be empty when present.")
        if "\n" in normalized or "\r" in normalized:
            raise LLMError(f"EventEvidence {slot_name} must not contain newlines.")
        if INTERNAL_IDENTIFIER_PATTERN.search(normalized) is not None:
            raise LLMError(f"EventEvidence {slot_name} must not contain internal identifiers.")
        sentence_count = len([part for part in re.split(r"[。!?！？]+", normalized) if part.strip()])
        if sentence_count != MAX_EVENT_EVIDENCE_SLOT_SENTENCES:
            raise LLMError(f"EventEvidence {slot_name} must be exactly one sentence when present.")
        present_slot_count += 1

    if present_slot_count == 0:
        raise LLMError("EventEvidence must include at least one non-null slot.")


def _recall_pack_candidate_refs_by_section(source_pack: dict[str, Any]) -> dict[str, set[str]]:
    # source pack
    candidate_sections = source_pack.get("candidate_sections", [])
    if not isinstance(candidate_sections, list):
        raise LLMError("RecallPackSelection source_pack.candidate_sections must be a list.")

    # 収集
    refs_by_section = {
        section_name: set()
        for section_name in RECALL_PACK_SECTION_NAMES
    }
    seen_sections: set[str] = set()
    seen_candidate_refs: set[str] = set()
    for section in candidate_sections:
        _validate_exact_keys(
            section,
            {"section_name", "candidates"},
            "RecallPackSelection source_pack candidate_section",
        )
        section_name = section["section_name"]
        if section_name not in refs_by_section:
            raise LLMError("RecallPackSelection source_pack section_name is invalid.")
        if section_name in seen_sections:
            raise LLMError("RecallPackSelection source_pack section_name must not repeat.")
        seen_sections.add(section_name)

        candidates = section["candidates"]
        if not isinstance(candidates, list):
            raise LLMError("RecallPackSelection source_pack candidates must be a list.")
        for candidate in candidates:
            if not isinstance(candidate, dict):
                raise LLMError("RecallPackSelection source_pack candidate must be an object.")
            candidate_ref = candidate.get("candidate_ref")
            if not isinstance(candidate_ref, str) or not candidate_ref.strip():
                raise LLMError("RecallPackSelection source_pack candidate_ref is invalid.")
            normalized_ref = candidate_ref.strip()
            if normalized_ref in seen_candidate_refs:
                raise LLMError("RecallPackSelection source_pack candidate_ref must be unique.")
            refs_by_section[section_name].add(normalized_ref)
            seen_candidate_refs.add(normalized_ref)

    # 結果
    return refs_by_section


def _recall_pack_conflict_refs(source_pack: dict[str, Any]) -> set[str]:
    # source pack
    conflicts = source_pack.get("conflicts", [])
    if not isinstance(conflicts, list):
        raise LLMError("RecallPackSelection source_pack.conflicts must be a list.")

    # 収集
    refs: set[str] = set()
    for conflict in conflicts:
        if not isinstance(conflict, dict):
            raise LLMError("RecallPackSelection source_pack conflict must be an object.")
        conflict_ref = conflict.get("conflict_ref")
        if not isinstance(conflict_ref, str) or not conflict_ref.strip():
            raise LLMError("RecallPackSelection source_pack conflict_ref is invalid.")
        normalized_ref = conflict_ref.strip()
        if normalized_ref in refs:
            raise LLMError("RecallPackSelection source_pack conflict_ref must be unique.")
        refs.add(normalized_ref)

    # 結果
    return refs


def validate_recall_pack_selection_contract(payload: dict[str, Any], *, source_pack: dict[str, Any]) -> None:
    # 必須キー群
    _validate_exact_keys(payload, {"section_selection", "conflict_summaries"}, "RecallPackSelection")

    # source pack refs
    valid_candidate_refs_by_section = _recall_pack_candidate_refs_by_section(source_pack)
    valid_conflict_refs = _recall_pack_conflict_refs(source_pack)

    # section_selection
    section_selection = payload["section_selection"]
    if not isinstance(section_selection, list):
        raise LLMError("RecallPackSelection section_selection must be a list.")

    seen_sections: set[str] = set()
    seen_candidate_refs: set[str] = set()
    for section_item in section_selection:
        _validate_exact_keys(
            section_item,
            {"section_name", "candidate_refs"},
            "RecallPackSelection section_selection item",
        )
        section_name = section_item["section_name"]
        if section_name not in valid_candidate_refs_by_section:
            raise LLMError("RecallPackSelection section_name is invalid.")
        if section_name in seen_sections:
            raise LLMError("RecallPackSelection section_name must not repeat.")
        seen_sections.add(section_name)

        candidate_refs = section_item["candidate_refs"]
        if not isinstance(candidate_refs, list) or not candidate_refs:
            raise LLMError("RecallPackSelection candidate_refs must be a non-empty list.")

        local_seen_refs: set[str] = set()
        for candidate_ref in candidate_refs:
            if not isinstance(candidate_ref, str) or not candidate_ref.strip():
                raise LLMError("RecallPackSelection candidate_ref is invalid.")
            normalized_ref = candidate_ref.strip()
            if normalized_ref not in valid_candidate_refs_by_section[section_name]:
                raise LLMError("RecallPackSelection candidate_ref must belong to its section in source_pack.")
            if normalized_ref in local_seen_refs:
                raise LLMError("RecallPackSelection candidate_refs must not repeat within a section.")
            if normalized_ref in seen_candidate_refs:
                raise LLMError("RecallPackSelection candidate_refs must not repeat across sections.")
            local_seen_refs.add(normalized_ref)
            seen_candidate_refs.add(normalized_ref)

    # conflict_summaries
    conflict_summaries = payload["conflict_summaries"]
    if not isinstance(conflict_summaries, list):
        raise LLMError("RecallPackSelection conflict_summaries must be a list.")

    seen_conflict_refs: set[str] = set()
    for conflict_item in conflict_summaries:
        _validate_exact_keys(
            conflict_item,
            {"conflict_ref", "summary_text"},
            "RecallPackSelection conflict_summary",
        )
        conflict_ref = conflict_item["conflict_ref"]
        if not isinstance(conflict_ref, str) or not conflict_ref.strip():
            raise LLMError("RecallPackSelection conflict_ref is invalid.")
        normalized_ref = conflict_ref.strip()
        if normalized_ref not in valid_conflict_refs:
            raise LLMError("RecallPackSelection conflict_ref must exist in source_pack.")
        if normalized_ref in seen_conflict_refs:
            raise LLMError("RecallPackSelection conflict_ref must not repeat.")
        seen_conflict_refs.add(normalized_ref)

        summary_text = conflict_item["summary_text"]
        if not isinstance(summary_text, str):
            raise LLMError("RecallPackSelection summary_text must be a string.")
        normalized_summary = summary_text.strip()
        if not normalized_summary:
            raise LLMError("RecallPackSelection summary_text must not be empty.")
        if "\n" in normalized_summary or "\r" in normalized_summary:
            raise LLMError("RecallPackSelection summary_text must not contain newlines.")
        if INTERNAL_IDENTIFIER_PATTERN.search(normalized_summary) is not None:
            raise LLMError("RecallPackSelection summary_text must not contain internal identifiers.")
        sentence_count = len([part for part in re.split(r"[。!?！？]+", normalized_summary) if part.strip()])
        if sentence_count != MAX_RECALL_PACK_CONFLICT_SUMMARY_SENTENCES:
            raise LLMError("RecallPackSelection summary_text must be exactly one sentence.")

    if seen_conflict_refs != valid_conflict_refs:
        raise LLMError("RecallPackSelection conflict_summaries must cover all conflict refs.")
