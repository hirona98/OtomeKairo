from __future__ import annotations

from datetime import datetime
import math
import re
from typing import Any


# エラー
class LLMError(Exception):
    pass


class LLMContractError(LLMError):
    pass


# 設定
RECALL_FOCUS_VALUES = {
    "self",
    "user",
    "relationship",
    "commitment",
    "topic",
    "preference",
    "fact",
    "state",
    "episodic",
}
RISK_FLAG_VALUES = {
    "mixed_intent",
    "ambiguous_reference",
    "weak_memory_cue",
    "time_ambiguous",
}

TIME_REFERENCE_VALUES = {
    "none",
    "recent",
    "past",
    "future",
    "persistent",
}
WORLD_STATE_TYPE_VALUES = {
    "visual_context",
    "environment",
    "location",
    "external_service",
    "body",
    "device",
    "schedule",
    "social_context",
}
WORLD_STATE_HINT_VALUES = {
    "low",
    "medium",
    "high",
}
WORLD_STATE_TTL_HINT_VALUES = {
    "short",
    "medium",
    "long",
}
ACTIVITY_TRANSITION_VALUES = {
    "start",
    "continue",
    "switch",
    "end",
    "none",
}
ACTIVITY_ACTOR_VALUES = {
    "user",
    "self",
    "unknown",
}
INITIATIVE_ENTRY_BASIS_VALUES = {
    "activity_mode_transition",
    "strong_interest",
    "same_activity_detail_change",
    "observation_only",
}
INITIATIVE_ENTRY_ENTER_BASIS_VALUES = {
    "activity_mode_transition",
    "strong_interest",
}

MEMORY_TYPE_VALUES = {
    "fact",
    "preference",
    "relation",
    "commitment",
    "interpretation",
    "summary",
}

MEMORY_CORRECTION_STATUS_VALUES = {
    "no_correction",
    "selected",
}
MEMORY_CORRECTION_KIND_VALUES = {
    "revoke_created",
    "restore_previous",
    "supersede_compensation",
}

SCOPE_TYPE_VALUES = {
    "self",
    "user",
    "entity",
    "topic",
    "relationship",
    "world",
}
MAX_SECONDARY_RECALL_FOCUSES = 2
MAX_RISK_FLAGS = 3
MAX_HINT_SCOPE_VALUES = 4
MAX_MEMORY_REFLECTION_SUMMARY_LENGTH = 140
MAX_VISUAL_OBSERVATION_SUMMARY_LENGTH = 1200
RECALL_PACK_SECTION_NAMES = (
    "self_model",
    "user_model",
    "relationship_model",
    "active_topics",
    "active_commitments",
    "episodic_evidence",
)
ANSWER_CONTRACT_VALUES = {
    "summary",
    "exact_boundary",
    "exact_statement",
    "provenance",
    "conflict_check",
}
DIRECT_GROUNDING_CONTRACTS = {
    "exact_boundary",
    "exact_statement",
    "provenance",
    "conflict_check",
}
ANSWER_BOUNDARY_VALUES = {
    "none",
    "first",
    "latest",
}
ANSWER_TARGET_ACTOR_VALUES = {
    "any",
    "user",
    "assistant",
}
MAX_ANSWER_CONTRACT_REASON_CODES = 3
MAX_ANSWER_CONTRACT_QUERY_TERMS = 5
INTERNAL_IDENTIFIER_PATTERN = re.compile(
    r"\b(?:event|episode|memory_unit|cycle|reflection_run|retrieval_run|pending_intent|candidate|conflict):[A-Za-z0-9._-]+\b"
)


# 補助検証
def _validate_exact_keys(value: Any, required_keys: set[str], label: str) -> None:
    # 形状
    if not isinstance(value, dict):
        raise LLMError(f"{label} はオブジェクトである必要があります。")

    # キー確認
    actual_keys = set(value.keys())
    if actual_keys == required_keys:
        return

    # 詳細
    missing_keys = sorted(required_keys - actual_keys)
    extra_keys = sorted(actual_keys - required_keys)
    details: list[str] = []
    if missing_keys:
        details.append(f"不足={','.join(missing_keys)}")
    if extra_keys:
        details.append(f"余計={','.join(extra_keys)}")
    raise LLMError(f"{label} のキーが不正です（{'; '.join(details)}）。")


def _has_named_ref_prefix(value: str) -> bool:
    # 名前付き参照プレフィックス
    for prefix in ("person:", "place:", "tool:"):
        if value.startswith(prefix) and value != prefix:
            return True
    return False


def _has_focus_scope_shape(value: str) -> bool:
    # 固定scope
    if value in {"self", "user"}:
        return True

    # 関係・話題scope
    for prefix in ("relationship:", "topic:"):
        if value.startswith(prefix) and value != prefix:
            return True
    return False


def normalize_recall_hint_payload(payload: dict[str, Any]) -> dict[str, Any]:
    # 不正な focus scope は意味補完せず検索ヒントから外す。
    normalized = dict(payload)
    if "confidence" in normalized:
        normalized["confidence"] = _normalized_recall_confidence(normalized["confidence"])
    primary_focus = normalized.get("primary_recall_focus")
    if isinstance(normalized.get("secondary_recall_focuses"), list):
        normalized["secondary_recall_focuses"] = _normalized_recall_focuses(
            normalized["secondary_recall_focuses"],
            primary_focus=primary_focus if isinstance(primary_focus, str) else None,
        )
    focus_scopes = normalized.get("focus_scopes")
    if isinstance(focus_scopes, list):
        normalized["focus_scopes"] = _normalized_recall_focus_scopes(focus_scopes)
    mentioned_entities = normalized.get("mentioned_entities")
    if isinstance(mentioned_entities, list):
        normalized["mentioned_entities"] = _normalized_named_refs(mentioned_entities)
    mentioned_topics = normalized.get("mentioned_topics")
    if isinstance(mentioned_topics, list):
        normalized["mentioned_topics"] = _normalized_topic_refs(mentioned_topics)
    risk_flags = normalized.get("risk_flags")
    if isinstance(risk_flags, list):
        normalized["risk_flags"] = _normalized_risk_flags(risk_flags)
    return normalized


def _normalized_recall_confidence(value: Any) -> Any:
    # JSON number の引用符付き表現だけを数値へ戻す。意味ラベルや百分率は契約違反として残す。
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped:
        return value
    try:
        parsed = float(stripped)
    except ValueError:
        return value
    if not math.isfinite(parsed):
        return value
    return parsed


def _normalized_unique_enum_values(
    values: list[Any],
    *,
    allowed_values: set[str],
    max_items: int,
    excluded_values: set[str] | None = None,
) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    excluded = excluded_values or set()
    for value in values:
        if not isinstance(value, str):
            continue
        candidate = value.strip()
        if not candidate or candidate in seen or candidate in excluded or candidate not in allowed_values:
            continue
        normalized.append(candidate)
        seen.add(candidate)
        if len(normalized) >= max_items:
            break
    return normalized


def _normalized_recall_focuses(values: list[Any], *, primary_focus: str | None) -> list[str]:
    excluded_values = {primary_focus} if primary_focus else set()
    return _normalized_unique_enum_values(
        values,
        allowed_values=RECALL_FOCUS_VALUES,
        max_items=MAX_SECONDARY_RECALL_FOCUSES,
        excluded_values=excluded_values,
    )


def _normalized_recall_focus_scopes(scopes: list[Any]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for scope in scopes:
        if not isinstance(scope, str):
            continue
        value = scope.strip()
        if not value or not _has_focus_scope_shape(value) or value in seen:
            continue
        normalized.append(value)
        seen.add(value)
        if len(normalized) >= MAX_HINT_SCOPE_VALUES:
            break
    return normalized


def _normalized_risk_flags(values: list[Any]) -> list[str]:
    return _normalized_unique_enum_values(
        values,
        allowed_values=RISK_FLAG_VALUES,
        max_items=MAX_RISK_FLAGS,
    )


def _normalized_named_refs(values: list[Any]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        candidate = value.strip()
        if not candidate or not _has_named_ref_prefix(candidate) or candidate in seen:
            continue
        normalized.append(candidate)
        seen.add(candidate)
        if len(normalized) >= MAX_HINT_SCOPE_VALUES:
            break
    return normalized


def _normalized_topic_refs(values: list[Any]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        candidate = value.strip()
        if not candidate:
            continue
        if ":" not in candidate:
            candidate = f"topic:{candidate}"
        if not candidate.startswith("topic:") or candidate == "topic:" or candidate in seen:
            continue
        normalized.append(candidate)
        seen.add(candidate)
        if len(normalized) >= MAX_HINT_SCOPE_VALUES:
            break
    return normalized


def normalize_answer_contract_payload(payload: dict[str, Any]) -> dict[str, Any]:
    # 実行側で扱う派生値だけを補う。意味判断は LLM 出力を正本にする。
    normalized = dict(payload)
    contract = normalized.get("contract")
    normalized["requires_direct_evidence"] = contract in DIRECT_GROUNDING_CONTRACTS
    query_terms = normalized.get("query_terms")
    if isinstance(query_terms, list):
        normalized["query_terms"] = _normalized_query_terms(query_terms)
    return normalized


def _normalized_query_terms(values: list[Any]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        candidate = value.strip()
        if not candidate or candidate in seen:
            continue
        normalized.append(candidate)
        seen.add(candidate)
        if len(normalized) >= MAX_ANSWER_CONTRACT_QUERY_TERMS:
            break
    return normalized


def validate_answer_contract_contract(payload: dict[str, Any]) -> None:
    # 形状
    _validate_exact_keys(
        payload,
        {"contract", "reason_codes", "boundary", "target_actor", "query_terms"},
        "AnswerContract",
    )

    # contract
    contract = payload["contract"]
    if contract not in ANSWER_CONTRACT_VALUES:
        raise LLMError("AnswerContract.contract が不正です。")

    # reason_codes
    reason_codes = payload["reason_codes"]
    if not isinstance(reason_codes, list):
        raise LLMError("AnswerContract.reason_codes は配列である必要があります。")
    if len(reason_codes) > MAX_ANSWER_CONTRACT_REASON_CODES:
        raise LLMError("AnswerContract.reason_codes が多すぎます。")
    for reason_code in reason_codes:
        if not isinstance(reason_code, str) or not reason_code.strip():
            raise LLMError("AnswerContract.reason_codes に空または文字列以外の値が含まれています。")

    # boundary
    boundary = payload["boundary"]
    if boundary not in ANSWER_BOUNDARY_VALUES:
        raise LLMError("AnswerContract.boundary が不正です。")
    if contract == "exact_boundary" and boundary == "none":
        raise LLMError("AnswerContract.contract=exact_boundary のとき boundary は first または latest です。")
    if contract not in {"exact_boundary", "exact_statement"} and boundary != "none":
        raise LLMError("AnswerContract.contract が exact_boundary / exact_statement 以外のとき boundary は none です。")

    # target_actor
    target_actor = payload["target_actor"]
    if target_actor not in ANSWER_TARGET_ACTOR_VALUES:
        raise LLMError("AnswerContract.target_actor が不正です。")

    # query_terms
    query_terms = payload["query_terms"]
    if not isinstance(query_terms, list):
        raise LLMError("AnswerContract.query_terms は配列である必要があります。")
    for term in query_terms:
        if not isinstance(term, str) or not term.strip():
            raise LLMError("AnswerContract.query_terms に空または文字列以外の値が含まれています。")


def validate_activity_state_contract(payload: dict[str, Any]) -> None:
    # 形状
    _validate_exact_keys(payload, {"activity_candidates"}, "ActivityState")
    candidates = payload["activity_candidates"]
    if not isinstance(candidates, list):
        raise LLMError("ActivityState.activity_candidates は配列である必要があります。")
    if len(candidates) > 1:
        raise LLMError("ActivityState.activity_candidates は最大 1 件です。")

    # 候補
    for candidate in candidates:
        _validate_exact_keys(
            candidate,
            {
                "actor",
                "label",
                "target",
                "confidence_hint",
                "salience_hint",
                "ttl_hint",
                "transition",
                "reason_summary",
            },
            "ActivityState.activity_candidate",
        )
        if candidate["confidence_hint"] not in WORLD_STATE_HINT_VALUES:
            raise LLMError("ActivityState.confidence_hint が不正です。")
        if candidate["salience_hint"] not in WORLD_STATE_HINT_VALUES:
            raise LLMError("ActivityState.salience_hint が不正です。")
        if candidate["ttl_hint"] not in WORLD_STATE_TTL_HINT_VALUES:
            raise LLMError("ActivityState.ttl_hint が不正です。")
        if candidate["transition"] not in ACTIVITY_TRANSITION_VALUES:
            raise LLMError("ActivityState.transition が不正です。")
        if candidate["actor"] not in ACTIVITY_ACTOR_VALUES:
            raise LLMError("ActivityState.actor が不正です。")
        for key in ("label", "target", "reason_summary"):
            value = candidate[key]
            if not isinstance(value, str):
                raise LLMError(f"ActivityState.{key} は文字列である必要があります。")
            if key in {"label", "reason_summary"} and not value.strip():
                raise LLMError(f"ActivityState.{key} は空にできません。")
            if INTERNAL_IDENTIFIER_PATTERN.search(value):
                raise LLMError(f"ActivityState.{key} に内部識別子を含めてはいけません。")


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
        raise LLMError(f"{label}.scope_type が不正です。")
    if not isinstance(scope_key, str) or not scope_key.strip():
        raise LLMError(f"{label}.scope_key が不正です。")

    # 正規化済み
    normalized_scope_key = scope_key.strip()

    # 固定Scopes
    if scope_type == "self" and normalized_scope_key != "self":
        raise LLMError(f"{label}.scope_type が self のとき、scope_key は 'self' である必要があります。")
    if scope_type == "user" and normalized_scope_key != "user":
        raise LLMError(f"{label}.scope_type が user のとき、scope_key は 'user' である必要があります。")
    if scope_type == "world" and normalized_scope_key != "world":
        raise LLMError(f"{label}.scope_type が world のとき、scope_key は 'world' である必要があります。")

    # トピックスコープ
    if scope_type == "topic":
        if not normalized_scope_key.startswith("topic:") or normalized_scope_key == "topic:":
            raise LLMError(f"{label}.scope_type が topic のとき、scope_key は topic:<name> 形式である必要があります。")
        return

    # エンティティスコープ
    if scope_type == "entity":
        if not _has_named_ref_prefix(normalized_scope_key):
            raise LLMError(f"{label}.scope_type が entity のとき、scope_key は person:/place:/tool: のいずれかで始まる必要があります。")
        return

    # 関係スコープ
    if scope_type == "relationship":
        refs = normalized_scope_key.split("|")
        if len(refs) < 2:
            raise LLMError(f"{label}.scope_key は 2 つ以上の ref を '|' で連結する必要があります。")
        if any(not _is_relationship_ref(ref) for ref in refs):
            raise LLMError(f"{label}.scope_key に不正な relationship ref が含まれています。")
        if len(refs) != len(set(refs)):
            raise LLMError(f"{label}.scope_key に重複した relationship ref が含まれています。")
        if refs != _normalized_relationship_refs(refs):
            raise LLMError(f"{label}.scope_key は relationship scope 用に正規化されている必要があります。")


def _validate_vad(value: Any, label: str) -> None:
    # 形状
    _validate_exact_keys(value, {"v", "a", "d"}, label)

    # 値
    for axis in ("v", "a", "d"):
        axis_value = value[axis]
        if not isinstance(axis_value, (int, float)):
            raise LLMError(f"{label}.{axis} は数値である必要があります。")


def _validate_world_state_scope_ref(value: Any, label: str) -> None:
    # 型確認
    if not isinstance(value, str) or not value.strip():
        raise LLMError(f"{label} が不正です。")

    # 固定scope
    normalized = value.strip()
    if normalized in {"self", "user", "world"}:
        return

    # 分解
    scope_type, separator, scope_key = normalized.partition(":")
    if not separator or not scope_key.strip():
        raise LLMError(f"{label} は self / user / world / entity:<key> / topic:<key> / relationship:<key> 形式である必要があります。")

    # 実体scope
    if scope_type == "entity":
        if not _has_named_ref_prefix(scope_key.strip()):
            raise LLMError(f"{label} entity scope が不正です。")
        return

    # topic scope
    if scope_type == "topic":
        _validate_scope_identity(
            scope_type="topic",
            scope_key=f"topic:{scope_key.strip()}",
            label=label,
        )
        return

    # relationship scope
    if scope_type == "relationship":
        _validate_scope_identity(
            scope_type="relationship",
            scope_key=scope_key.strip(),
            label=label,
        )
        return

    raise LLMError(f"{label} scope_type が不正です。")


# recall_hint検証
def validate_recall_hint_contract(payload: dict[str, Any]) -> None:
    # 必須キー群
    required_keys = {
        "primary_recall_focus",
        "secondary_recall_focuses",
        "confidence",
        "time_reference",
        "focus_scopes",
        "mentioned_entities",
        "mentioned_topics",
        "risk_flags",
    }
    if set(payload.keys()) != required_keys:
        raise LLMError("RecallHint のキーが契約と一致しません。")

    # 値検証
    if not isinstance(payload["primary_recall_focus"], str) or not payload["primary_recall_focus"].strip():
        raise LLMError("RecallHint primary_recall_focus は空でない文字列である必要があります。")
    if not isinstance(payload["time_reference"], str) or not payload["time_reference"].strip():
        raise LLMError("RecallHint time_reference は空でない文字列である必要があります。")
    if payload["primary_recall_focus"] not in RECALL_FOCUS_VALUES:
        raise LLMError("RecallHint primary_recall_focus が不正です。")
    if payload["time_reference"] not in TIME_REFERENCE_VALUES:
        raise LLMError("RecallHint time_reference が不正です。")
    if not isinstance(payload["secondary_recall_focuses"], list):
        raise LLMError("RecallHint secondary_recall_focuses は配列である必要があります。")
    for focus in payload["secondary_recall_focuses"]:
        if not isinstance(focus, str) or not focus.strip():
            raise LLMError("RecallHint secondary_recall_focuses の各要素は空でない文字列である必要があります。")
        if focus not in RECALL_FOCUS_VALUES:
            raise LLMError("RecallHint secondary_recall_focus が不正です。")
    if not isinstance(payload["focus_scopes"], list):
        raise LLMError("RecallHint focus_scopes は配列である必要があります。")
    if any(not isinstance(scope, str) or not scope.strip() for scope in payload["focus_scopes"]):
        raise LLMError("RecallHint focus_scopes の各要素は空でない文字列である必要があります。")
    if any(not _has_focus_scope_shape(scope.strip()) for scope in payload["focus_scopes"]):
        raise LLMError("RecallHint focus_scopes は self/user/relationship:<key>/topic:<key> 形式である必要があります。")
    if not isinstance(payload["mentioned_entities"], list):
        raise LLMError("RecallHint mentioned_entities は配列である必要があります。")
    if any(not isinstance(entity, str) or not entity.strip() for entity in payload["mentioned_entities"]):
        raise LLMError("RecallHint mentioned_entities の各要素は空でない文字列である必要があります。")
    if any(not _has_named_ref_prefix(entity.strip()) for entity in payload["mentioned_entities"]):
        raise LLMError("RecallHint mentioned_entities は person:/place:/tool: 形式である必要があります。")
    if not isinstance(payload["mentioned_topics"], list):
        raise LLMError("RecallHint mentioned_topics は配列である必要があります。")
    if any(not isinstance(topic, str) or not topic.strip() for topic in payload["mentioned_topics"]):
        raise LLMError("RecallHint mentioned_topics の各要素は空でない文字列である必要があります。")
    if any(
        not topic.strip().startswith("topic:") or topic.strip() == "topic:"
        for topic in payload["mentioned_topics"]
    ):
        raise LLMError("RecallHint mentioned_topics は topic:<name> 形式である必要があります。")
    if not isinstance(payload["risk_flags"], list):
        raise LLMError("RecallHint risk_flags は配列である必要があります。")
    for risk_flag in payload["risk_flags"]:
        if not isinstance(risk_flag, str) or not risk_flag.strip():
            raise LLMError("RecallHint risk_flags の各要素は空でない文字列である必要があります。")
        if risk_flag not in RISK_FLAG_VALUES:
            raise LLMError("RecallHint risk_flag が不正です。")
    if isinstance(payload["confidence"], bool) or not isinstance(payload["confidence"], (int, float)):
        raise LLMError("RecallHint confidence は数値である必要があります。")
    if not 0.0 <= float(payload["confidence"]) <= 1.0:
        raise LLMError("RecallHint confidence は 0.0 以上 1.0 以下である必要があります。")


# decision検証
def validate_decision_contract(payload: dict[str, Any]) -> None:
    # 必須キー群
    required_keys = {
        "kind",
        "reason_code",
        "reason_summary",
        "requires_confirmation",
        "pending_intent",
        "capability_request",
        "autonomous_run",
    }
    _validate_exact_keys(payload, required_keys, "Decision")

    # 値Checks
    if payload["kind"] not in {"speech", "noop", "pending_intent", "capability_request", "autonomous_run"}:
        raise LLMError("Decision kind が不正です。")
    if not isinstance(payload["reason_code"], str) or not payload["reason_code"].strip():
        raise LLMError("Decision reason_code は空でない文字列である必要があります。")
    if not isinstance(payload["reason_summary"], str) or not payload["reason_summary"].strip():
        raise LLMError("Decision reason_summary は空でない文字列である必要があります。")
    if not isinstance(payload["requires_confirmation"], bool):
        raise LLMError("Decision requires_confirmation は真偽値である必要があります。")
    if payload["kind"] == "pending_intent":
        pending_intent = payload["pending_intent"]
        required_pending_keys = {
            "intent_kind",
            "intent_summary",
            "dedupe_key",
        }
        if not isinstance(pending_intent, dict) or set(pending_intent.keys()) != required_pending_keys:
            raise LLMError("Decision pending_intent が不正です。")
        for key in required_pending_keys:
            value = pending_intent.get(key)
            if not isinstance(value, str) or not value.strip():
                raise LLMError(f"Decision pending_intent.{key} は空でない文字列である必要があります。")
        if payload["requires_confirmation"]:
            raise LLMError("Decision pending_intent では requires_confirmation=true を指定できません。")
    elif payload["pending_intent"] is not None:
        raise LLMError("Decision kind が pending_intent 以外のとき、pending_intent は null である必要があります。")
    if payload["kind"] == "capability_request":
        capability_request = payload["capability_request"]
        required_capability_request_keys = {
            "capability_id",
            "input",
        }
        if (
            not isinstance(capability_request, dict)
            or set(capability_request.keys()) != required_capability_request_keys
        ):
            raise LLMError("Decision capability_request が不正です。")
        capability_id = capability_request.get("capability_id")
        if not isinstance(capability_id, str) or not capability_id.strip():
            raise LLMError("Decision capability_request.capability_id は空でない文字列である必要があります。")
        if not isinstance(capability_request.get("input"), dict):
            raise LLMError("Decision capability_request.input は object である必要があります。")
        if payload["requires_confirmation"]:
            raise LLMError("Decision capability_request では requires_confirmation=true を指定できません。")
    elif payload["capability_request"] is not None:
        raise LLMError("Decision kind が capability_request 以外のとき、capability_request は null である必要があります。")
    if payload["kind"] == "autonomous_run":
        autonomous_run = payload["autonomous_run"]
        required_autonomous_run_keys = {
            "objective_summary",
            "initial_step_summary",
            "coordination",
        }
        if not isinstance(autonomous_run, dict) or set(autonomous_run.keys()) != required_autonomous_run_keys:
            raise LLMError("Decision autonomous_run が不正です。")
        for key in ("objective_summary", "initial_step_summary"):
            value = autonomous_run.get(key)
            if not isinstance(value, str) or not value.strip():
                raise LLMError(f"Decision autonomous_run.{key} は空でない文字列である必要があります。")
        coordination = autonomous_run.get("coordination")
        required_coordination_keys = {
            "mode",
            "target_run_ids",
            "reason_summary",
        }
        if not isinstance(coordination, dict) or set(coordination.keys()) != required_coordination_keys:
            raise LLMError("Decision autonomous_run.coordination が不正です。")
        mode = coordination.get("mode")
        if mode not in {"create_new", "replace_existing"}:
            raise LLMError("Decision autonomous_run.coordination.mode が不正です。")
        target_run_ids = coordination.get("target_run_ids")
        if not isinstance(target_run_ids, list) or not all(
            isinstance(run_id, str) and run_id.strip()
            for run_id in target_run_ids
        ):
            raise LLMError("Decision autonomous_run.coordination.target_run_ids は空でない文字列の配列です。")
        if len(set(target_run_ids)) != len(target_run_ids):
            raise LLMError("Decision autonomous_run.coordination.target_run_ids に重複があります。")
        if mode == "create_new" and target_run_ids:
            raise LLMError("Decision autonomous_run.coordination create_new では target_run_ids を空にします。")
        if mode == "replace_existing" and not target_run_ids:
            raise LLMError("Decision autonomous_run.coordination replace_existing では target_run_ids が必要です。")
        reason_summary = coordination.get("reason_summary")
        if not isinstance(reason_summary, str) or not reason_summary.strip():
            raise LLMError("Decision autonomous_run.coordination.reason_summary は空でない文字列です。")
        if payload["requires_confirmation"]:
            raise LLMError("Decision autonomous_run では requires_confirmation=true を指定できません。")
        if payload["pending_intent"] is not None or payload["capability_request"] is not None:
            raise LLMError("Decision autonomous_run では pending_intent と capability_request を null にしてください。")
    elif payload["autonomous_run"] is not None:
        raise LLMError("Decision kind が autonomous_run 以外のとき、autonomous_run は null である必要があります。")


# autonomous step検証
def validate_autonomous_step_contract(payload: dict[str, Any]) -> None:
    _validate_exact_keys(payload, {"action", "transition", "run_update"}, "AutonomousStep")

    action = payload["action"]
    _validate_exact_keys(action, {"kind", "capability_request", "speech"}, "AutonomousStep action")
    if action["kind"] not in {"capability_request", "speech", "none"}:
        raise LLMError("AutonomousStep action.kind が不正です。")
    if action["kind"] == "capability_request":
        capability_request = action["capability_request"]
        if not isinstance(capability_request, dict) or set(capability_request.keys()) != {"capability_id", "input"}:
            raise LLMError("AutonomousStep action.capability_request が不正です。")
        capability_id = capability_request.get("capability_id")
        if not isinstance(capability_id, str) or not capability_id.strip():
            raise LLMError("AutonomousStep action.capability_request.capability_id が不正です。")
        if not isinstance(capability_request.get("input"), dict):
            raise LLMError("AutonomousStep action.capability_request.input は object である必要があります。")
        if action["speech"] is not None:
            raise LLMError("AutonomousStep capability_request action では speech は null です。")
    elif action["capability_request"] is not None:
        raise LLMError("AutonomousStep capability_request 以外の action では capability_request は null です。")

    if action["kind"] == "speech":
        speech = action["speech"]
        if not isinstance(speech, dict) or set(speech.keys()) != {"reason_code", "reason_summary"}:
            raise LLMError("AutonomousStep action.speech が不正です。")
        if not isinstance(speech["reason_code"], str) or not speech["reason_code"].strip():
            raise LLMError("AutonomousStep action.speech.reason_code は空でない文字列です。")
        if not isinstance(speech["reason_summary"], str) or not speech["reason_summary"].strip():
            raise LLMError("AutonomousStep action.speech.reason_summary は空でない文字列です。")
    elif action["speech"] is not None:
        raise LLMError("AutonomousStep speech 以外の action では speech は null です。")

    transition = payload["transition"]
    _validate_exact_keys(
        transition,
        {"kind", "next_run_at"},
        "AutonomousStep transition",
    )
    if transition["kind"] not in {"continue", "wait_until", "complete", "cancel"}:
        raise LLMError("AutonomousStep transition.kind が不正です。")
    if action["kind"] != "capability_request" and transition["kind"] == "wait_until":
        if not isinstance(transition["next_run_at"], str) or not transition["next_run_at"].strip():
            raise LLMError("AutonomousStep wait_until では next_run_at が必要です。")
        try:
            next_run_at = datetime.fromisoformat(transition["next_run_at"].strip())
        except ValueError as exc:
            raise LLMError("AutonomousStep wait_until.next_run_at は ISO timestamp である必要があります。") from exc
        if next_run_at.tzinfo is None:
            raise LLMError("AutonomousStep wait_until.next_run_at は offset 付き ISO timestamp である必要があります。")
    elif action["kind"] != "capability_request" and transition["next_run_at"] is not None:
        raise LLMError("AutonomousStep wait_until 以外では next_run_at は null です。")
    if action["kind"] == "speech" and transition["kind"] == "continue":
        raise LLMError("AutonomousStep speech action では transition.kind=continue を使いません。")

    run_update = payload["run_update"]
    _validate_exact_keys(
        run_update,
        {"current_step_summary", "history_summary"},
        "AutonomousStep run_update",
    )
    for key in ("current_step_summary", "history_summary"):
        value = run_update[key]
        if not isinstance(value, str):
            raise LLMError(f"AutonomousStep run_update.{key} は文字列です。")
    if not run_update["current_step_summary"].strip():
        raise LLMError("AutonomousStep run_update.current_step_summary は空にできません。")


# memory interpretation検証
def validate_memory_interpretation_contract(payload: dict[str, Any]) -> None:
    # 必須キー群
    required_keys = {
        "episode",
        "candidate_memory_units",
        "episode_affects",
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
        raise LLMError("MemoryInterpretation episode.summary_text が不正です。")
    if episode["episode_series_id"] is not None and (
        not isinstance(episode["episode_series_id"], str) or not episode["episode_series_id"].strip()
    ):
        raise LLMError("MemoryInterpretation episode.episode_series_id が不正です。")
    if episode["outcome_text"] is not None and not isinstance(episode["outcome_text"], str):
        raise LLMError("MemoryInterpretation episode.outcome_text が不正です。")
    if not isinstance(episode["open_loops"], list):
        raise LLMError("MemoryInterpretation episode.open_loops は配列である必要があります。")
    if not isinstance(episode["salience"], (int, float)):
        raise LLMError("MemoryInterpretation episode.salience は数値である必要があります。")
    _validate_scope_identity(
        scope_type=episode["primary_scope_type"],
        scope_key=episode["primary_scope_key"],
        label="MemoryInterpretation episode",
    )

    # 候補検証
    if not isinstance(payload["candidate_memory_units"], list):
        raise LLMError("MemoryInterpretation candidate_memory_units は配列である必要があります。")
    for candidate in payload["candidate_memory_units"]:
        required_candidate_keys = {
            "memory_type",
            "scope",
            "subject_hint",
            "predicate_hint",
            "object_hint",
            "qualifiers_hint",
            "summary_text",
            "evidence_text",
            "confidence_hint",
        }
        _validate_exact_keys(candidate, required_candidate_keys, "MemoryInterpretation candidate_memory_unit")
        if candidate["memory_type"] not in MEMORY_TYPE_VALUES:
            raise LLMError("MemoryInterpretation candidate_memory_unit.memory_type が不正です。")
        if candidate["scope"] not in SCOPE_TYPE_VALUES:
            raise LLMError(
                "MemoryInterpretation candidate_memory_unit.scope が不正です。"
                f" scope={candidate['scope']!r}。self, user, entity, topic, relationship, world のいずれかだけを使ってください。"
            )
        if not isinstance(candidate["subject_hint"], str) or not candidate["subject_hint"].strip():
            raise LLMError("MemoryInterpretation candidate_memory_unit.subject_hint が不正です。")
        if not isinstance(candidate["predicate_hint"], str) or not candidate["predicate_hint"].strip():
            raise LLMError("MemoryInterpretation candidate_memory_unit.predicate_hint が不正です。")
        if not isinstance(candidate["object_hint"], str) or not candidate["object_hint"].strip():
            raise LLMError("MemoryInterpretation candidate_memory_unit.object_hint が不正です。")
        if candidate["subject_hint"].strip().startswith("entity:") or candidate["object_hint"].strip().startswith(
            "entity:"
        ):
            raise LLMError(
                "MemoryInterpretation candidate_memory_unit では entity:<key> を使えません。"
                " person:/place:/tool: の型付き参照を使ってください。"
            )
        if candidate["scope"] == "entity" and not _has_named_ref_prefix(candidate["subject_hint"].strip()):
            raise LLMError(
                "MemoryInterpretation candidate_memory_unit.scope が entity のとき、"
                "subject_hint は person:/place:/tool: 形式である必要があります。"
            )
        if candidate["scope"] == "relationship":
            _validate_scope_identity(
                scope_type="relationship",
                scope_key=candidate["subject_hint"],
                label="MemoryInterpretation candidate_memory_unit.subject_hint",
            )
        if not isinstance(candidate["qualifiers_hint"], dict):
            raise LLMError("MemoryInterpretation candidate_memory_unit.qualifiers_hint はオブジェクトである必要があります。")
        if not isinstance(candidate["summary_text"], str) or not candidate["summary_text"].strip():
            raise LLMError("MemoryInterpretation candidate_memory_unit.summary_text が不正です。")
        if not isinstance(candidate["evidence_text"], str) or not candidate["evidence_text"].strip():
            raise LLMError("MemoryInterpretation candidate_memory_unit.evidence_text が不正です。")
        if candidate["confidence_hint"] not in {"low", "medium", "high"}:
            raise LLMError("MemoryInterpretation candidate_memory_unit.confidence_hint が不正です。")

    # episode affect検証
    episode_affects = payload["episode_affects"]
    if not isinstance(episode_affects, list):
        raise LLMError("MemoryInterpretation episode_affects は配列である必要があります。")
    if len(episode_affects) > 4:
        raise LLMError("MemoryInterpretation episode_affects は最大 4 件までである必要があります。")

    seen_episode_affects: set[tuple[str, str, str]] = set()
    for episode_affect in episode_affects:
        required_affect_keys = {
            "target_scope_type",
            "target_scope_key",
            "affect_label",
            "vad",
            "intensity",
            "confidence",
            "summary_text",
        }
        _validate_exact_keys(episode_affect, required_affect_keys, "MemoryInterpretation episode_affect")
        _validate_scope_identity(
            scope_type=episode_affect["target_scope_type"],
            scope_key=episode_affect["target_scope_key"],
            label="MemoryInterpretation episode_affect",
        )
        if not isinstance(episode_affect["affect_label"], str) or not episode_affect["affect_label"].strip():
            raise LLMError("MemoryInterpretation episode_affect.affect_label が不正です。")
        if not isinstance(episode_affect["summary_text"], str) or not episode_affect["summary_text"].strip():
            raise LLMError("MemoryInterpretation episode_affect.summary_text が不正です。")
        _validate_vad(episode_affect["vad"], "MemoryInterpretation episode_affect.vad")
        if isinstance(episode_affect["intensity"], bool) or not isinstance(episode_affect["intensity"], (int, float)):
            raise LLMError("MemoryInterpretation episode_affect.intensity は数値である必要があります。")
        if isinstance(episode_affect["confidence"], bool) or not isinstance(episode_affect["confidence"], (int, float)):
            raise LLMError("MemoryInterpretation episode_affect.confidence は数値である必要があります。")
        if not 0.0 <= float(episode_affect["intensity"]) <= 1.0:
            raise LLMError("MemoryInterpretation episode_affect.intensity は 0.0 以上 1.0 以下である必要があります。")
        if not 0.0 <= float(episode_affect["confidence"]) <= 1.0:
            raise LLMError("MemoryInterpretation episode_affect.confidence は 0.0 以上 1.0 以下である必要があります。")

        affect_key = (
            episode_affect["target_scope_type"],
            episode_affect["target_scope_key"],
            episode_affect["affect_label"].strip(),
        )
        if affect_key in seen_episode_affects:
            raise LLMError("MemoryInterpretation episode_affects に重複した target/label の組を含めてはいけません。")
        seen_episode_affects.add(affect_key)


def validate_memory_reflection_summary_contract(payload: dict[str, Any]) -> None:
    # 必須キー群
    _validate_exact_keys(payload, {"summary_text"}, "MemoryReflectionSummary")

    # summary_text
    summary_text = payload["summary_text"]
    if not isinstance(summary_text, str):
        raise LLMError("MemoryReflectionSummary summary_text は文字列である必要があります。")

    normalized = summary_text.strip()
    if not normalized:
        raise LLMError("MemoryReflectionSummary summary_text は空にできません。")
    if "\n" in normalized or "\r" in normalized:
        raise LLMError("MemoryReflectionSummary summary_text に改行を含めてはいけません。")
    if len(normalized) > MAX_MEMORY_REFLECTION_SUMMARY_LENGTH:
        raise LLMError("MemoryReflectionSummary summary_text が最大長を超えています。")
    if INTERNAL_IDENTIFIER_PATTERN.search(normalized) is not None:
        raise LLMError("MemoryReflectionSummary summary_text に内部識別子を含めてはいけません。")


def validate_memory_correction_reconciliation_contract(payload: dict[str, Any]) -> None:
    # 必須キー群
    _validate_exact_keys(payload, {"correction_status", "selected_targets"}, "MemoryCorrectionReconciliation")

    # status
    if payload["correction_status"] not in MEMORY_CORRECTION_STATUS_VALUES:
        raise LLMError("MemoryCorrectionReconciliation correction_status が不正です。")

    # selected targets
    selected_targets = payload["selected_targets"]
    if not isinstance(selected_targets, list):
        raise LLMError("MemoryCorrectionReconciliation selected_targets は配列である必要があります。")
    if payload["correction_status"] == "no_correction" and selected_targets:
        raise LLMError("MemoryCorrectionReconciliation no_correction では selected_targets を空にしてください。")
    if payload["correction_status"] == "selected" and not selected_targets:
        raise LLMError("MemoryCorrectionReconciliation selected では selected_targets を 1 件以上入れてください。")
    if len(selected_targets) > 8:
        raise LLMError("MemoryCorrectionReconciliation selected_targets は最大 8 件までです。")

    seen_revision_ids: set[str] = set()
    for item in selected_targets:
        required_keys = {"revision_id", "memory_unit_id", "correction_kind", "reason_summary"}
        _validate_exact_keys(item, required_keys, "MemoryCorrectionReconciliation selected_target")
        revision_id = item["revision_id"]
        if not isinstance(revision_id, str) or not revision_id.startswith("revision:"):
            raise LLMError("MemoryCorrectionReconciliation selected_target.revision_id が不正です。")
        if revision_id in seen_revision_ids:
            raise LLMError("MemoryCorrectionReconciliation selected_targets に重複した revision_id があります。")
        seen_revision_ids.add(revision_id)

        memory_unit_id = item["memory_unit_id"]
        if not isinstance(memory_unit_id, str) or not memory_unit_id.startswith("memory_unit:"):
            raise LLMError("MemoryCorrectionReconciliation selected_target.memory_unit_id が不正です。")
        if item["correction_kind"] not in MEMORY_CORRECTION_KIND_VALUES:
            raise LLMError("MemoryCorrectionReconciliation selected_target.correction_kind が不正です。")
        reason_summary = item["reason_summary"]
        if not isinstance(reason_summary, str) or not reason_summary.strip():
            raise LLMError("MemoryCorrectionReconciliation selected_target.reason_summary が不正です。")
        if "\n" in reason_summary or "\r" in reason_summary:
            raise LLMError("MemoryCorrectionReconciliation selected_target.reason_summary に改行を含めてはいけません。")


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
            raise LLMError(f"EventEvidence {slot_name} は文字列または null である必要があります。")
        normalized = value.strip()
        if not normalized:
            raise LLMError(f"EventEvidence {slot_name} は指定する場合、空にできません。")
        if "\n" in normalized or "\r" in normalized:
            raise LLMError(f"EventEvidence {slot_name} に改行を含めてはいけません。")
        if INTERNAL_IDENTIFIER_PATTERN.search(normalized) is not None:
            raise LLMError(f"EventEvidence {slot_name} に内部識別子を含めてはいけません。")
        present_slot_count += 1

    if present_slot_count == 0:
        raise LLMError("EventEvidence には少なくとも 1 つの null でない slot が必要です。")


def validate_world_state_contract(payload: dict[str, Any]) -> None:
    # 必須キー群
    _validate_exact_keys(payload, {"state_candidates"}, "WorldState")

    # 候補群
    state_candidates = payload["state_candidates"]
    if not isinstance(state_candidates, list):
        raise LLMError("WorldState state_candidates は配列である必要があります。")
    if len(state_candidates) > 4:
        raise LLMError("WorldState state_candidates は最大 4 件までである必要があります。")

    seen_keys: set[tuple[str, str]] = set()
    for candidate in state_candidates:
        _validate_exact_keys(
            candidate,
            {"state_type", "scope", "summary_text", "confidence_hint", "salience_hint", "ttl_hint"},
            "WorldState candidate",
        )
        state_type = candidate["state_type"]
        if state_type not in WORLD_STATE_TYPE_VALUES:
            raise LLMError("WorldState candidate.state_type が不正です。")
        _validate_world_state_scope_ref(candidate["scope"], "WorldState candidate.scope")

        summary_text = candidate["summary_text"]
        if not isinstance(summary_text, str):
            raise LLMError("WorldState candidate.summary_text は文字列である必要があります。")
        normalized_summary = summary_text.strip()
        if not normalized_summary:
            raise LLMError("WorldState candidate.summary_text は空にできません。")
        if "\n" in normalized_summary or "\r" in normalized_summary:
            raise LLMError("WorldState candidate.summary_text に改行を含めてはいけません。")
        if INTERNAL_IDENTIFIER_PATTERN.search(normalized_summary) is not None:
            raise LLMError("WorldState candidate.summary_text に内部識別子を含めてはいけません。")
        confidence_hint = candidate["confidence_hint"]
        salience_hint = candidate["salience_hint"]
        ttl_hint = candidate["ttl_hint"]
        if confidence_hint not in WORLD_STATE_HINT_VALUES:
            raise LLMError("WorldState candidate.confidence_hint が不正です。")
        if salience_hint not in WORLD_STATE_HINT_VALUES:
            raise LLMError("WorldState candidate.salience_hint が不正です。")
        if ttl_hint not in WORLD_STATE_TTL_HINT_VALUES:
            raise LLMError("WorldState candidate.ttl_hint が不正です。")

        dedupe_key = (state_type, str(candidate["scope"]).strip())
        if dedupe_key in seen_keys:
            raise LLMError("WorldState state_candidates に重複した state_type/scope の組を含めてはいけません。")
        seen_keys.add(dedupe_key)


def validate_visual_observation_contract(payload: dict[str, Any]) -> None:
    # 必須キー群
    _validate_exact_keys(payload, {"summary_text", "confidence_hint"}, "VisualObservation")

    # summary_text
    summary_text = payload["summary_text"]
    if not isinstance(summary_text, str):
        raise LLMError("VisualObservation summary_text は文字列である必要があります。")
    normalized_summary = summary_text.strip()
    if not normalized_summary:
        raise LLMError("VisualObservation summary_text は空にできません。")
    if "\n" in normalized_summary or "\r" in normalized_summary:
        raise LLMError("VisualObservation summary_text に改行を含めてはいけません。")
    if len(normalized_summary) > MAX_VISUAL_OBSERVATION_SUMMARY_LENGTH:
        raise LLMError("VisualObservation summary_text が最大長を超えています。")
    if INTERNAL_IDENTIFIER_PATTERN.search(normalized_summary) is not None:
        raise LLMError("VisualObservation summary_text に内部識別子を含めてはいけません。")
    # confidence_hint
    confidence_hint = payload["confidence_hint"]
    if confidence_hint not in WORLD_STATE_HINT_VALUES:
        raise LLMError("VisualObservation confidence_hint が不正です。")


def _recall_pack_candidate_refs_by_section(source_pack: dict[str, Any]) -> dict[str, set[str]]:
    # source pack
    candidate_sections = source_pack.get("candidate_sections", [])
    if not isinstance(candidate_sections, list):
        raise LLMError("RecallPackSelection source_pack.candidate_sections は配列である必要があります。")

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
            raise LLMError("RecallPackSelection source_pack section_name が不正です。")
        if section_name in seen_sections:
            raise LLMError("RecallPackSelection source_pack section_name は重複してはいけません。")
        seen_sections.add(section_name)

        candidates = section["candidates"]
        if not isinstance(candidates, list):
            raise LLMError("RecallPackSelection source_pack candidates は配列である必要があります。")
        for candidate in candidates:
            if not isinstance(candidate, dict):
                raise LLMError("RecallPackSelection source_pack candidate はオブジェクトである必要があります。")
            candidate_ref = candidate.get("candidate_ref")
            if not isinstance(candidate_ref, str) or not candidate_ref.strip():
                raise LLMError("RecallPackSelection source_pack candidate_ref が不正です。")
            normalized_ref = candidate_ref.strip()
            if normalized_ref in seen_candidate_refs:
                raise LLMError("RecallPackSelection source_pack candidate_ref は一意である必要があります。")
            refs_by_section[section_name].add(normalized_ref)
            seen_candidate_refs.add(normalized_ref)

    # 結果
    return refs_by_section


def _recall_pack_conflict_refs(source_pack: dict[str, Any]) -> set[str]:
    # source pack
    conflicts = source_pack.get("conflicts", [])
    if not isinstance(conflicts, list):
        raise LLMError("RecallPackSelection source_pack.conflicts は配列である必要があります。")

    # 収集
    refs: set[str] = set()
    for conflict in conflicts:
        if not isinstance(conflict, dict):
            raise LLMError("RecallPackSelection source_pack conflict はオブジェクトである必要があります。")
        conflict_ref = conflict.get("conflict_ref")
        if not isinstance(conflict_ref, str) or not conflict_ref.strip():
            raise LLMError("RecallPackSelection source_pack conflict_ref が不正です。")
        normalized_ref = conflict_ref.strip()
        if normalized_ref in refs:
            raise LLMError("RecallPackSelection source_pack conflict_ref は一意である必要があります。")
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
        raise LLMError("RecallPackSelection section_selection は配列である必要があります。")

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
            raise LLMError("RecallPackSelection section_name が不正です。")
        if section_name in seen_sections:
            raise LLMError("RecallPackSelection section_name は重複してはいけません。")
        seen_sections.add(section_name)

        candidate_refs = section_item["candidate_refs"]
        if not isinstance(candidate_refs, list) or not candidate_refs:
            raise LLMError("RecallPackSelection candidate_refs は空でない配列である必要があります。")

        local_seen_refs: set[str] = set()
        for candidate_ref in candidate_refs:
            if not isinstance(candidate_ref, str) or not candidate_ref.strip():
                raise LLMError("RecallPackSelection candidate_ref が不正です。")
            normalized_ref = candidate_ref.strip()
            if normalized_ref not in valid_candidate_refs_by_section[section_name]:
                raise LLMError("RecallPackSelection candidate_ref は source_pack 内の対応 section に属している必要があります。")
            if normalized_ref in local_seen_refs:
                raise LLMError("RecallPackSelection candidate_refs は同じ section 内で重複してはいけません。")
            if normalized_ref in seen_candidate_refs:
                raise LLMError("RecallPackSelection candidate_refs は section をまたいで重複してはいけません。")
            local_seen_refs.add(normalized_ref)
            seen_candidate_refs.add(normalized_ref)

    # conflict_summaries
    conflict_summaries = payload["conflict_summaries"]
    if not isinstance(conflict_summaries, list):
        raise LLMError("RecallPackSelection conflict_summaries は配列である必要があります。")

    seen_conflict_refs: set[str] = set()
    for conflict_item in conflict_summaries:
        _validate_exact_keys(
            conflict_item,
            {"conflict_ref", "summary_text"},
            "RecallPackSelection conflict_summary",
        )
        conflict_ref = conflict_item["conflict_ref"]
        if not isinstance(conflict_ref, str) or not conflict_ref.strip():
            raise LLMError("RecallPackSelection conflict_ref が不正です。")
        normalized_ref = conflict_ref.strip()
        if normalized_ref not in valid_conflict_refs:
            raise LLMError("RecallPackSelection conflict_ref は source_pack に存在している必要があります。")
        if normalized_ref in seen_conflict_refs:
            raise LLMError("RecallPackSelection conflict_ref は重複してはいけません。")
        seen_conflict_refs.add(normalized_ref)

        summary_text = conflict_item["summary_text"]
        if not isinstance(summary_text, str):
            raise LLMError("RecallPackSelection summary_text は文字列である必要があります。")
        normalized_summary = summary_text.strip()
        if not normalized_summary:
            raise LLMError("RecallPackSelection summary_text は空にできません。")
        if "\n" in normalized_summary or "\r" in normalized_summary:
            raise LLMError("RecallPackSelection summary_text に改行を含めてはいけません。")
        if INTERNAL_IDENTIFIER_PATTERN.search(normalized_summary) is not None:
            raise LLMError("RecallPackSelection summary_text に内部識別子を含めてはいけません。")
    if seen_conflict_refs != valid_conflict_refs:
        raise LLMError("RecallPackSelection conflict_summaries はすべての conflict_ref を網羅する必要があります。")


def _pending_intent_selection_candidate_refs(source_pack: dict[str, Any]) -> set[str]:
    # source pack
    candidates = source_pack.get("candidates", [])
    if not isinstance(candidates, list):
        raise LLMError("PendingIntentSelection source_pack.candidates は配列である必要があります。")

    # 収集
    refs: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise LLMError("PendingIntentSelection source_pack candidate はオブジェクトである必要があります。")
        candidate_ref = candidate.get("candidate_ref")
        if not isinstance(candidate_ref, str) or not candidate_ref.strip():
            raise LLMError("PendingIntentSelection source_pack candidate_ref が不正です。")
        normalized_ref = candidate_ref.strip()
        if normalized_ref in refs:
            raise LLMError("PendingIntentSelection source_pack candidate_ref は一意である必要があります。")
        refs.add(normalized_ref)

    # 結果
    return refs


def validate_pending_intent_selection_contract(payload: dict[str, Any], *, source_pack: dict[str, Any]) -> None:
    # 必須キー群
    _validate_exact_keys(payload, {"selected_candidate_ref", "selection_reason"}, "PendingIntentSelection")

    # candidate refs
    valid_candidate_refs = _pending_intent_selection_candidate_refs(source_pack)

    # selected_candidate_ref
    selected_candidate_ref = payload["selected_candidate_ref"]
    if not isinstance(selected_candidate_ref, str) or not selected_candidate_ref.strip():
        raise LLMError("PendingIntentSelection selected_candidate_ref が不正です。")
    normalized_ref = selected_candidate_ref.strip()
    if normalized_ref != "none" and normalized_ref not in valid_candidate_refs:
        raise LLMError("PendingIntentSelection selected_candidate_ref は source_pack に存在するか 'none' である必要があります。")

    # selection_reason
    selection_reason = payload["selection_reason"]
    if not isinstance(selection_reason, str):
        raise LLMError("PendingIntentSelection selection_reason は文字列である必要があります。")
    normalized_reason = selection_reason.strip()
    if not normalized_reason:
        raise LLMError("PendingIntentSelection selection_reason は空にできません。")
    if "\n" in normalized_reason or "\r" in normalized_reason:
        raise LLMError("PendingIntentSelection selection_reason に改行を含めてはいけません。")
    if INTERNAL_IDENTIFIER_PATTERN.search(normalized_reason) is not None:
        raise LLMError("PendingIntentSelection selection_reason に内部識別子を含めてはいけません。")


def validate_initiative_entry_check_contract(payload: dict[str, Any]) -> None:
    # 必須キー群
    _validate_exact_keys(payload, {"entry_kind", "entry_basis", "reason_summary"}, "InitiativeEntryCheck")

    # entry_kind
    entry_kind = payload["entry_kind"]
    if not isinstance(entry_kind, str) or entry_kind.strip() not in {"enter", "skip"}:
        raise LLMError("InitiativeEntryCheck entry_kind は enter または skip である必要があります。")
    normalized_entry_kind = entry_kind.strip()

    # entry_basis
    entry_basis = payload["entry_basis"]
    if not isinstance(entry_basis, str) or entry_basis.strip() not in INITIATIVE_ENTRY_BASIS_VALUES:
        raise LLMError("InitiativeEntryCheck entry_basis が不正です。")
    normalized_entry_basis = entry_basis.strip()
    if normalized_entry_kind == "enter" and normalized_entry_basis not in INITIATIVE_ENTRY_ENTER_BASIS_VALUES:
        raise LLMError("InitiativeEntryCheck enter には強い entry_basis が必要です。")

    # reason_summary
    reason_summary = payload["reason_summary"]
    if not isinstance(reason_summary, str):
        raise LLMError("InitiativeEntryCheck reason_summary は文字列である必要があります。")
    normalized_reason = reason_summary.strip()
    if not normalized_reason:
        raise LLMError("InitiativeEntryCheck reason_summary は空にできません。")
    if "\n" in normalized_reason or "\r" in normalized_reason:
        raise LLMError("InitiativeEntryCheck reason_summary に改行を含めてはいけません。")
    if INTERNAL_IDENTIFIER_PATTERN.search(normalized_reason) is not None:
        raise LLMError("InitiativeEntryCheck reason_summary に内部識別子を含めてはいけません。")
