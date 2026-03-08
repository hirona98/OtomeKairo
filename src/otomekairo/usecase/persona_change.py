"""Build persona change proposals and bounded updates from long-cycle evidence."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any


# Block: Persona change constants
PERSONA_WINDOW_MS = 14 * 24 * 60 * 60 * 1000
PERSONA_CYCLE_LIMIT = 200
TRAIT_KEYS = (
    "sociability",
    "caution",
    "curiosity",
    "persistence",
    "warmth",
    "assertiveness",
    "novelty_preference",
)
ACTION_TYPE_ALIASES = {
    "enqueue_browse_task": "browse",
    "complete_browse_task": "browse",
    "control_camera_look": "look",
    "dispatch_notice": "notify",
    "emit_chat_response": "speak",
}
ACTION_STYLE_BY_TYPE = {
    "speak": "conversational_response",
    "browse": "external_lookup",
    "notify": "push_notice",
    "look": "viewpoint_adjustment",
    "wait": "defer_action",
}
ACTION_TRAIT_SIGNAL_RULES: dict[tuple[str, str], tuple[tuple[str, float, str], ...]] = {
    ("browse", "succeeded"): (
        ("curiosity", 0.08, "外部探索を反復して成功させた"),
        ("novelty_preference", 0.06, "新しい情報探索を繰り返した"),
    ),
    ("browse", "failed"): (
        ("caution", 0.08, "外部探索の失敗が続いた"),
        ("novelty_preference", -0.04, "探索の失敗で広げすぎを抑えた"),
    ),
    ("browse", "stopped"): (
        ("caution", 0.06, "外部探索を途中停止する傾向が出た"),
    ),
    ("look", "succeeded"): (
        ("curiosity", 0.07, "視線確認を反復して選んだ"),
        ("assertiveness", 0.04, "自発観測を継続した"),
    ),
    ("look", "failed"): (
        ("caution", 0.07, "視線確認の失敗で慎重さが増えた"),
        ("assertiveness", -0.04, "観測の押し出しを弱めた"),
    ),
    ("look", "stopped"): (
        ("caution", 0.05, "視線確認を途中停止する傾向が出た"),
    ),
    ("notify", "succeeded"): (
        ("warmth", 0.07, "能動的な通知が安定して成功した"),
        ("assertiveness", 0.05, "伝達を自発的に選び続けた"),
    ),
    ("notify", "failed"): (
        ("caution", 0.06, "通知の失敗で慎重さが増えた"),
        ("warmth", -0.03, "不用意な通知を抑える方向へ寄った"),
    ),
    ("notify", "stopped"): (
        ("caution", 0.05, "通知を途中停止する傾向が出た"),
    ),
    ("speak", "succeeded"): (
        ("sociability", 0.05, "対話応答を安定して返した"),
        ("warmth", 0.04, "対話応答で関係維持を続けた"),
    ),
    ("speak", "failed"): (
        ("caution", 0.05, "対話応答の失敗で慎重さが増えた"),
        ("sociability", -0.03, "応答を押し出しすぎない方向へ寄った"),
    ),
    ("speak", "stopped"): (
        ("caution", 0.04, "対話応答を途中停止する傾向が出た"),
    ),
}
LONG_MOOD_LABEL_SIGNALS: dict[str, tuple[tuple[str, float, str], ...]] = {
    "curious": (("curiosity", 0.06, "持続感情が curiosity 寄りで安定した"),),
    "guarded": (("caution", 0.06, "持続感情が guarded 寄りで安定した"),),
    "tense": (("caution", 0.05, "持続感情が tense 寄りで安定した"),),
    "warm": (("warmth", 0.06, "持続感情が warm 寄りで安定した"),),
    "calm": (("persistence", 0.04, "持続感情が calm 寄りで安定した"),),
}
RELATION_KIND_SIGNALS: dict[str, tuple[tuple[str, float, str], ...]] = {
    "care": (
        ("warmth", 0.07, "関係記憶が care を維持した"),
        ("sociability", 0.03, "関係維持の接触が安定した"),
    ),
    "peer": (
        ("sociability", 0.05, "関係記憶が peer を維持した"),
    ),
    "strained": (
        ("caution", 0.06, "関係記憶で緊張が継続した"),
    ),
}


# Block: Persona change result
@dataclass(frozen=True, slots=True)
class PersonaChangeResult:
    personality_change_proposal: dict[str, Any]
    persona_updates: dict[str, Any] | None
    updated_personality: dict[str, Any] | None


# Block: Public evaluation
def evaluate_persona_change(
    *,
    connection: sqlite3.Connection,
    now_ms: int,
    current_personality: dict[str, Any],
    current_personality_updated_at: int,
) -> PersonaChangeResult:
    cutoff_at = now_ms - PERSONA_WINDOW_MS
    cycle_ids = _fetch_recent_cycle_ids(
        connection=connection,
        cutoff_at=cutoff_at,
    )
    cycle_event_ids_map, event_cycle_map = _fetch_cycle_event_links(
        connection=connection,
        cycle_ids=cycle_ids,
    )
    evidence = _gather_persona_evidence(
        connection=connection,
        cutoff_at=cutoff_at,
        cycle_ids=cycle_ids,
        cycle_event_ids_map=cycle_event_ids_map,
        event_cycle_map=event_cycle_map,
    )
    proposal = _build_personality_change_proposal(
        personality_updated_at=current_personality_updated_at,
        evidence=evidence,
    )
    persona_updates, updated_personality = _build_persona_updates(
        proposal=proposal,
        current_personality=current_personality,
        current_personality_updated_at=current_personality_updated_at,
    )
    return PersonaChangeResult(
        personality_change_proposal=proposal,
        persona_updates=persona_updates,
        updated_personality=updated_personality,
    )


# Block: Recent cycle fetch
def _fetch_recent_cycle_ids(
    *,
    connection: sqlite3.Connection,
    cutoff_at: int,
) -> list[str]:
    rows = connection.execute(
        """
        SELECT cycle_id
        FROM (
            SELECT cycle_id, MAX(observed_at) AS last_seen_at
            FROM (
                SELECT cycle_id, created_at AS observed_at
                FROM events
                WHERE created_at >= ?
                UNION ALL
                SELECT cycle_id, finished_at AS observed_at
                FROM action_history
                WHERE finished_at >= ?
            )
            GROUP BY cycle_id
            ORDER BY last_seen_at DESC
            LIMIT ?
        )
        ORDER BY cycle_id ASC
        """,
        (cutoff_at, cutoff_at, PERSONA_CYCLE_LIMIT),
    ).fetchall()
    return [str(row["cycle_id"]) for row in rows]


# Block: Cycle event links fetch
def _fetch_cycle_event_links(
    *,
    connection: sqlite3.Connection,
    cycle_ids: list[str],
) -> tuple[dict[str, list[str]], dict[str, str]]:
    if not cycle_ids:
        return ({}, {})
    rows = connection.execute(
        f"""
        SELECT event_id, cycle_id
        FROM events
        WHERE cycle_id IN ({_placeholders(len(cycle_ids))})
        ORDER BY cycle_id ASC, created_at DESC, event_id ASC
        """,
        tuple(cycle_ids),
    ).fetchall()
    cycle_event_ids_map = {
        cycle_id: []
        for cycle_id in cycle_ids
    }
    event_cycle_map: dict[str, str] = {}
    for row in rows:
        event_id = str(row["event_id"])
        cycle_id = str(row["cycle_id"])
        cycle_event_ids_map.setdefault(cycle_id, []).append(event_id)
        event_cycle_map[event_id] = cycle_id
    return (
        {
            cycle_id: _unique_strings(event_ids)
            for cycle_id, event_ids in cycle_event_ids_map.items()
        },
        event_cycle_map,
    )


# Block: Evidence gather
def _gather_persona_evidence(
    *,
    connection: sqlite3.Connection,
    cutoff_at: int,
    cycle_ids: list[str],
    cycle_event_ids_map: dict[str, list[str]],
    event_cycle_map: dict[str, str],
) -> dict[str, Any]:
    evidence = {
        "cycle_ids": list(cycle_ids),
        "trait_signals": {trait_name: [] for trait_name in TRAIT_KEYS},
        "preference_votes": {},
        "aversion_votes": {},
        "preferred_action_types": {},
        "preferred_observation_kinds": {},
        "avoided_action_styles": {},
    }
    if not cycle_ids:
        return evidence
    action_rows = _fetch_action_rows(
        connection=connection,
        cycle_ids=cycle_ids,
    )
    memory_state_rows = _fetch_memory_state_rows(
        connection=connection,
        cutoff_at=cutoff_at,
    )
    preference_rows = _fetch_preference_rows(
        connection=connection,
        cutoff_at=cutoff_at,
    )
    _collect_action_evidence(
        evidence=evidence,
        action_rows=action_rows,
        cycle_event_ids_map=cycle_event_ids_map,
    )
    _collect_memory_state_evidence(
        evidence=evidence,
        memory_state_rows=memory_state_rows,
        cycle_ids=set(cycle_ids),
        cycle_event_ids_map=cycle_event_ids_map,
        event_cycle_map=event_cycle_map,
    )
    _collect_preference_memory_evidence(
        evidence=evidence,
        preference_rows=preference_rows,
        cycle_ids=set(cycle_ids),
        event_cycle_map=event_cycle_map,
    )
    return evidence


# Block: Action row fetch
def _fetch_action_rows(
    *,
    connection: sqlite3.Connection,
    cycle_ids: list[str],
) -> list[sqlite3.Row]:
    return connection.execute(
        f"""
        SELECT cycle_id, action_type, status
        FROM action_history
        WHERE cycle_id IN ({_placeholders(len(cycle_ids))})
        ORDER BY finished_at DESC
        """,
        tuple(cycle_ids),
    ).fetchall()


# Block: Memory state row fetch
def _fetch_memory_state_rows(
    *,
    connection: sqlite3.Connection,
    cutoff_at: int,
) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT memory_kind,
               body_text,
               payload_json,
               confidence,
               evidence_event_ids_json,
               updated_at
        FROM memory_states
        WHERE searchable = 1
          AND updated_at >= ?
          AND memory_kind IN ('relation', 'long_mood_state', 'reflection_note')
        ORDER BY updated_at DESC
        LIMIT 200
        """,
        (cutoff_at,),
    ).fetchall()


# Block: Preference row fetch
def _fetch_preference_rows(
    *,
    connection: sqlite3.Connection,
    cutoff_at: int,
) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT domain,
               polarity,
               status,
               confidence,
               target_entity_ref_json,
               evidence_event_ids_json,
               updated_at
        FROM preference_memory
        WHERE owner_scope = 'self'
          AND status = 'confirmed'
          AND updated_at >= ?
        ORDER BY updated_at DESC
        LIMIT 200
        """,
        (cutoff_at,),
    ).fetchall()


# Block: Action evidence collect
def _collect_action_evidence(
    *,
    evidence: dict[str, Any],
    action_rows: list[sqlite3.Row],
    cycle_event_ids_map: dict[str, list[str]],
) -> None:
    for row in action_rows:
        cycle_id = str(row["cycle_id"])
        evidence_event_ids = list(cycle_event_ids_map.get(cycle_id, []))
        action_type = _normalize_action_type(str(row["action_type"]))
        status = str(row["status"])
        if action_type is None:
            continue
        for trait_name, strength, reason in ACTION_TRAIT_SIGNAL_RULES.get((action_type, status), ()):
            _append_trait_signal(
                evidence=evidence,
                trait_name=trait_name,
                strength=strength,
                reason=reason,
                source_cycle_ids=[cycle_id],
                evidence_event_ids=evidence_event_ids,
            )
        if action_type not in {"browse", "look", "notify"}:
            continue
        if status == "succeeded":
            _append_vote(
                vote_map=evidence["preference_votes"],
                domain="action_type",
                target_key=action_type,
                confidence=0.70,
                evidence_count=1,
                source_cycle_ids=[cycle_id],
                evidence_event_ids=evidence_event_ids,
            )
            _append_rank_evidence(
                rank_map=evidence["preferred_action_types"],
                key=action_type,
                cycle_id=cycle_id,
                evidence_event_ids=evidence_event_ids,
            )
            observation_kind = _observation_kind_for_action(action_type)
            if observation_kind is not None:
                _append_rank_evidence(
                    rank_map=evidence["preferred_observation_kinds"],
                    key=observation_kind,
                    cycle_id=cycle_id,
                    evidence_event_ids=evidence_event_ids,
                )
            continue
        if status in {"failed", "stopped"}:
            _append_vote(
                vote_map=evidence["aversion_votes"],
                domain="action_type",
                target_key=action_type,
                confidence=0.75 if status == "failed" else 0.65,
                evidence_count=1,
                source_cycle_ids=[cycle_id],
                evidence_event_ids=evidence_event_ids,
            )
            _append_rank_evidence(
                rank_map=evidence["avoided_action_styles"],
                key=ACTION_STYLE_BY_TYPE[action_type],
                cycle_id=cycle_id,
                evidence_event_ids=evidence_event_ids,
            )


# Block: Memory state evidence collect
def _collect_memory_state_evidence(
    *,
    evidence: dict[str, Any],
    memory_state_rows: list[sqlite3.Row],
    cycle_ids: set[str],
    cycle_event_ids_map: dict[str, list[str]],
    event_cycle_map: dict[str, str],
) -> None:
    for row in memory_state_rows:
        source_cycle_ids = _related_cycle_ids(
            payload_json=row["payload_json"],
            evidence_event_ids_json=row["evidence_event_ids_json"],
            cycle_ids=cycle_ids,
            event_cycle_map=event_cycle_map,
        )
        if not source_cycle_ids:
            continue
        evidence_event_ids = _related_evidence_event_ids(
            payload_json=row["payload_json"],
            evidence_event_ids_json=row["evidence_event_ids_json"],
            cycle_ids=cycle_ids,
            cycle_event_ids_map=cycle_event_ids_map,
            event_cycle_map=event_cycle_map,
        )
        payload = _decoded_object_json(
            raw_json=row["payload_json"],
            field_name="memory_states.payload_json",
        )
        memory_kind = str(row["memory_kind"])
        confidence = _normalized_unit_score(
            row["confidence"],
            field_name="memory_states.confidence",
        )
        if memory_kind == "reflection_note":
            _collect_reflection_signals(
                evidence=evidence,
                payload=payload,
                confidence=confidence,
                source_cycle_ids=source_cycle_ids,
                evidence_event_ids=evidence_event_ids,
            )
            continue
        if memory_kind == "long_mood_state":
            _collect_long_mood_signals(
                evidence=evidence,
                payload=payload,
                confidence=confidence,
                source_cycle_ids=source_cycle_ids,
                evidence_event_ids=evidence_event_ids,
            )
            continue
        if memory_kind == "relation":
            _collect_relation_signals(
                evidence=evidence,
                payload=payload,
                confidence=confidence,
                source_cycle_ids=source_cycle_ids,
                evidence_event_ids=evidence_event_ids,
            )


# Block: Preference memory evidence collect
def _collect_preference_memory_evidence(
    *,
    evidence: dict[str, Any],
    preference_rows: list[sqlite3.Row],
    cycle_ids: set[str],
    event_cycle_map: dict[str, str],
) -> None:
    for row in preference_rows:
        source_cycle_ids = _cycle_ids_from_evidence_event_ids(
            evidence_event_ids_json=row["evidence_event_ids_json"],
            cycle_ids=cycle_ids,
            event_cycle_map=event_cycle_map,
        )
        if not source_cycle_ids:
            continue
        evidence_event_ids = _unique_strings(
            event_id
            for event_id in _decoded_string_list_json(
                raw_json=row["evidence_event_ids_json"],
                field_name="preference_memory.evidence_event_ids_json",
            )
            if event_id in event_cycle_map and event_cycle_map[event_id] in cycle_ids
        )
        target_ref = _decoded_object_json(
            raw_json=row["target_entity_ref_json"],
            field_name="preference_memory.target_entity_ref_json",
        )
        target_key = _preference_target_key(target_ref)
        domain = str(row["domain"])
        polarity = str(row["polarity"])
        confidence = _normalized_unit_score(
            row["confidence"],
            field_name="preference_memory.confidence",
        )
        if polarity == "like":
            _append_vote(
                vote_map=evidence["preference_votes"],
                domain=domain,
                target_key=target_key,
                confidence=confidence,
                evidence_count=1,
                source_cycle_ids=source_cycle_ids,
                evidence_event_ids=evidence_event_ids,
            )
            continue
        if polarity == "dislike":
            _append_vote(
                vote_map=evidence["aversion_votes"],
                domain=domain,
                target_key=target_key,
                confidence=confidence,
                evidence_count=1,
                source_cycle_ids=source_cycle_ids,
                evidence_event_ids=evidence_event_ids,
            )
            continue
        raise RuntimeError("preference_memory.polarity must be like/dislike")


# Block: Reflection signals
def _collect_reflection_signals(
    *,
    evidence: dict[str, Any],
    payload: dict[str, Any],
    confidence: float,
    source_cycle_ids: list[str],
    evidence_event_ids: list[str],
) -> None:
    if _payload_has_non_empty_value(payload, "avoid_pattern", "avoid_patterns", "judgment_patch"):
        _append_trait_signal(
            evidence=evidence,
            trait_name="caution",
            strength=0.04 + confidence * 0.04,
            reason="反省で avoid_pattern が反復した",
            source_cycle_ids=source_cycle_ids,
            evidence_event_ids=evidence_event_ids,
        )
    if _payload_has_non_empty_value(payload, "retry_hint", "retry_hints"):
        _append_trait_signal(
            evidence=evidence,
            trait_name="persistence",
            strength=0.04 + confidence * 0.04,
            reason="反省で retry_hint が反復した",
            source_cycle_ids=source_cycle_ids,
            evidence_event_ids=evidence_event_ids,
        )


# Block: Long mood signals
def _collect_long_mood_signals(
    *,
    evidence: dict[str, Any],
    payload: dict[str, Any],
    confidence: float,
    source_cycle_ids: list[str],
    evidence_event_ids: list[str],
) -> None:
    for label in _long_mood_labels(payload):
        for trait_name, strength, reason in LONG_MOOD_LABEL_SIGNALS.get(label, ()):
            _append_trait_signal(
                evidence=evidence,
                trait_name=trait_name,
                strength=strength * (0.70 + confidence * 0.30),
                reason=reason,
                source_cycle_ids=source_cycle_ids,
                evidence_event_ids=evidence_event_ids,
            )


# Block: Relation signals
def _collect_relation_signals(
    *,
    evidence: dict[str, Any],
    payload: dict[str, Any],
    confidence: float,
    source_cycle_ids: list[str],
    evidence_event_ids: list[str],
) -> None:
    relation_kind = _relation_kind(payload)
    if relation_kind in RELATION_KIND_SIGNALS:
        for trait_name, strength, reason in RELATION_KIND_SIGNALS[relation_kind]:
            _append_trait_signal(
                evidence=evidence,
                trait_name=trait_name,
                strength=strength * (0.70 + confidence * 0.30),
                reason=reason,
                source_cycle_ids=source_cycle_ids,
                evidence_event_ids=evidence_event_ids,
            )
    recent_tension = payload.get("recent_tension")
    if isinstance(recent_tension, (int, float)) and float(recent_tension) >= 0.60:
        _append_trait_signal(
            evidence=evidence,
            trait_name="caution",
            strength=0.05 + _normalized_unit_score(recent_tension, field_name="relation.recent_tension") * 0.03,
            reason="relation の recent_tension が継続した",
            source_cycle_ids=source_cycle_ids,
            evidence_event_ids=evidence_event_ids,
        )
    care_commitment = payload.get("care_commitment")
    if isinstance(care_commitment, (int, float)) and float(care_commitment) >= 0.70:
        _append_trait_signal(
            evidence=evidence,
            trait_name="warmth",
            strength=0.05 + _normalized_unit_score(care_commitment, field_name="relation.care_commitment") * 0.03,
            reason="relation の care_commitment が継続した",
            source_cycle_ids=source_cycle_ids,
            evidence_event_ids=evidence_event_ids,
        )


# Block: Proposal build
def _build_personality_change_proposal(
    *,
    personality_updated_at: int,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    trait_delta_candidates = [
        candidate
        for trait_name in TRAIT_KEYS
        if (candidate := _trait_delta_candidate(
            trait_name=trait_name,
            signal_rows=evidence["trait_signals"][trait_name],
        )) is not None
    ]
    trait_delta_candidates.sort(
        key=lambda candidate: abs(float(candidate["delta"])),
        reverse=True,
    )
    trait_delta_candidates = trait_delta_candidates[:3]
    preference_promotion_candidates = _promotion_entries(
        vote_map=evidence["preference_votes"],
        require_multiple_cycles=True,
    )
    aversion_promotion_candidates = _promotion_entries(
        vote_map=evidence["aversion_votes"],
        require_multiple_cycles=True,
    )
    habit_updates, habit_evidence_event_ids = _habit_updates(evidence=evidence)
    trait_deltas = [
        {
            "trait_name": str(candidate["trait_name"]),
            "delta": float(candidate["delta"]),
            "reason": str(candidate["reason"]),
            "evidence_count": int(candidate["evidence_count"]),
            "source_cycle_ids": list(candidate["source_cycle_ids"]),
        }
        for candidate in trait_delta_candidates
    ]
    preference_promotions = [
        {
            "domain": str(entry["domain"]),
            "target_key": str(entry["target_key"]),
            "weight": float(entry["weight"]),
            "evidence_count": int(entry["evidence_count"]),
        }
        for entry in preference_promotion_candidates
    ]
    aversion_promotions = [
        {
            "domain": str(entry["domain"]),
            "target_key": str(entry["target_key"]),
            "weight": float(entry["weight"]),
            "evidence_count": int(entry["evidence_count"]),
        }
        for entry in aversion_promotion_candidates
    ]
    evidence_event_ids = _unique_strings(
        [
            event_id
            for candidate in trait_delta_candidates
            for event_id in candidate["evidence_event_ids"]
        ]
        + [
            event_id
            for entry in preference_promotion_candidates
            for event_id in entry["evidence_event_ids"]
        ]
        + [
            event_id
            for entry in aversion_promotion_candidates
            for event_id in entry["evidence_event_ids"]
        ]
        + habit_evidence_event_ids
    )
    proposal = {
        "base_personality_updated_at": personality_updated_at,
        "trait_deltas": trait_deltas,
        "preference_promotions": preference_promotions,
        "aversion_promotions": aversion_promotions,
        "habit_updates": habit_updates,
        "evidence_event_ids": evidence_event_ids,
        "evidence_summary": _proposal_summary(
            trait_deltas=trait_deltas,
            preference_promotions=preference_promotions,
            aversion_promotions=aversion_promotions,
            habit_updates=habit_updates,
        ),
    }
    return proposal


# Block: Trait delta candidate
def _trait_delta_candidate(
    *,
    trait_name: str,
    signal_rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not signal_rows:
        return None
    positive_rows = [row for row in signal_rows if float(row["strength"]) > 0.0]
    negative_rows = [row for row in signal_rows if float(row["strength"]) < 0.0]
    dominant_rows = positive_rows
    opposing_rows = negative_rows
    if _signal_total_abs_strength(negative_rows) > _signal_total_abs_strength(positive_rows):
        dominant_rows = negative_rows
        opposing_rows = positive_rows
    if len(dominant_rows) < 3:
        return None
    source_cycle_ids = _unique_strings(
        cycle_id
        for row in dominant_rows
        for cycle_id in row["source_cycle_ids"]
    )
    if len(source_cycle_ids) < 2:
        return None
    dominant_strength = _signal_total_abs_strength(dominant_rows)
    opposing_strength = _signal_total_abs_strength(opposing_rows)
    coherence = 1.0 - (opposing_strength / max(dominant_strength + opposing_strength, 0.0001))
    if coherence < 0.55:
        return None
    average_strength = dominant_strength / float(len(dominant_rows))
    count_factor = min(1.0, len(dominant_rows) / 4.0)
    delta = average_strength * count_factor * coherence
    if delta < 0.03:
        return None
    evidence_event_ids = _unique_strings(
        event_id
        for row in dominant_rows
        for event_id in row["evidence_event_ids"]
    )
    if not evidence_event_ids:
        return None
    signed_delta = round(min(0.10, delta), 2)
    if dominant_rows and float(dominant_rows[0]["strength"]) < 0.0:
        signed_delta *= -1.0
    return {
        "trait_name": trait_name,
        "delta": signed_delta,
        "reason": " / ".join(_unique_strings(row["reason"] for row in dominant_rows)[:2]),
        "evidence_count": len(dominant_rows),
        "source_cycle_ids": source_cycle_ids,
        "evidence_event_ids": evidence_event_ids,
    }


# Block: Promotion entry build
def _promotion_entries(
    *,
    vote_map: dict[tuple[str, str], dict[str, Any]],
    require_multiple_cycles: bool,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for vote in vote_map.values():
        evidence_count = int(vote["evidence_count"])
        if evidence_count < 3:
            continue
        evidence_event_ids = _unique_strings(vote["evidence_event_ids"])
        if not evidence_event_ids:
            continue
        source_cycle_ids = _unique_strings(vote["source_cycle_ids"])
        if require_multiple_cycles and len(source_cycle_ids) < 2:
            continue
        average_confidence = float(vote["confidence_total"]) / float(evidence_count)
        weight = round(
            min(
                1.0,
                0.35 + max(0, evidence_count - 3) * 0.10 + average_confidence * 0.35,
            ),
            2,
        )
        entries.append(
            {
                "domain": str(vote["domain"]),
                "target_key": str(vote["target_key"]),
                "weight": weight,
                "evidence_count": evidence_count,
                "evidence_event_ids": evidence_event_ids,
            }
        )
    entries.sort(
        key=lambda entry: (
            -float(entry["weight"]),
            -int(entry["evidence_count"]),
            str(entry["domain"]),
            str(entry["target_key"]),
        )
    )
    return entries


# Block: Habit update build
def _habit_updates(*, evidence: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    habit_updates: dict[str, Any] = {}
    evidence_event_ids: list[str] = []
    preferred_action_entries = _ranked_habit_entries(
        rank_map=evidence["preferred_action_types"],
        minimum_evidence_count=4,
        minimum_cycle_count=2,
    )
    if preferred_action_entries:
        habit_updates["preferred_action_types"] = [
            entry["key"]
            for entry in preferred_action_entries
        ]
        evidence_event_ids.extend(
            event_id
            for entry in preferred_action_entries
            for event_id in entry["evidence_event_ids"]
        )
    preferred_observation_entries = _ranked_habit_entries(
        rank_map=evidence["preferred_observation_kinds"],
        minimum_evidence_count=4,
        minimum_cycle_count=2,
    )
    if preferred_observation_entries:
        habit_updates["preferred_observation_kinds"] = [
            entry["key"]
            for entry in preferred_observation_entries
        ]
        evidence_event_ids.extend(
            event_id
            for entry in preferred_observation_entries
            for event_id in entry["evidence_event_ids"]
        )
    avoided_action_entries = _ranked_habit_entries(
        rank_map=evidence["avoided_action_styles"],
        minimum_evidence_count=4,
        minimum_cycle_count=2,
    )
    if avoided_action_entries:
        habit_updates["avoided_action_styles"] = [
            entry["key"]
            for entry in avoided_action_entries
        ]
        evidence_event_ids.extend(
            event_id
            for entry in avoided_action_entries
            for event_id in entry["evidence_event_ids"]
        )
    return (habit_updates, _unique_strings(evidence_event_ids))


# Block: Proposal summary build
def _proposal_summary(
    *,
    trait_deltas: list[dict[str, Any]],
    preference_promotions: list[dict[str, Any]],
    aversion_promotions: list[dict[str, Any]],
    habit_updates: dict[str, Any],
) -> str:
    summary_parts: list[str] = []
    if trait_deltas:
        summary_parts.append(
            "trait:" + ",".join(str(delta["trait_name"]) for delta in trait_deltas)
        )
    if preference_promotions:
        summary_parts.append(
            "prefer:" + ",".join(str(entry["target_key"]) for entry in preference_promotions[:2])
        )
    if aversion_promotions:
        summary_parts.append(
            "avoid:" + ",".join(str(entry["target_key"]) for entry in aversion_promotions[:2])
        )
    preferred_action_types = habit_updates.get("preferred_action_types")
    if isinstance(preferred_action_types, list) and preferred_action_types:
        summary_parts.append("habit:" + ">".join(str(item) for item in preferred_action_types[:2]))
    if not summary_parts:
        return "人格変化に昇格する証拠は閾値未満"
    return " / ".join(summary_parts)


# Block: Persona updates build
def _build_persona_updates(
    *,
    proposal: dict[str, Any],
    current_personality: dict[str, Any],
    current_personality_updated_at: int,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if int(proposal["base_personality_updated_at"]) != current_personality_updated_at:
        return (None, None)
    if not _proposal_has_updates(proposal):
        return (None, None)
    updated_personality = {
        "trait_values": dict(current_personality["trait_values"]),
        "preferred_interaction_style": dict(current_personality["preferred_interaction_style"]),
        "learned_preferences": [
            dict(entry)
            for entry in current_personality["learned_preferences"]
        ],
        "learned_aversions": [
            dict(entry)
            for entry in current_personality["learned_aversions"]
        ],
        "habit_biases": {
            "preferred_action_types": list(current_personality["habit_biases"]["preferred_action_types"]),
            "preferred_observation_kinds": list(current_personality["habit_biases"]["preferred_observation_kinds"]),
            "avoided_action_styles": list(current_personality["habit_biases"]["avoided_action_styles"]),
        },
    }
    updated_trait_values = _bounded_trait_value_updates(
        proposal=proposal,
        updated_personality=updated_personality,
    )
    style_updates = _bounded_style_updates(
        proposal=proposal,
        updated_personality=updated_personality,
    )
    preference_promotions = _bounded_preference_updates(
        proposed_entries=proposal["preference_promotions"],
        current_entries=updated_personality["learned_preferences"],
    )
    aversion_promotions = _bounded_preference_updates(
        proposed_entries=proposal["aversion_promotions"],
        current_entries=updated_personality["learned_aversions"],
    )
    if preference_promotions:
        updated_personality["learned_preferences"] = _merged_preference_entries(
            current_entries=updated_personality["learned_preferences"],
            changed_entries=preference_promotions,
        )
        updated_personality["learned_aversions"] = _remove_matching_preference_entries(
            current_entries=updated_personality["learned_aversions"],
            changed_entries=preference_promotions,
        )
    if aversion_promotions:
        updated_personality["learned_aversions"] = _merged_preference_entries(
            current_entries=updated_personality["learned_aversions"],
            changed_entries=aversion_promotions,
        )
        updated_personality["learned_preferences"] = _remove_matching_preference_entries(
            current_entries=updated_personality["learned_preferences"],
            changed_entries=aversion_promotions,
        )
    habit_updates = _bounded_habit_updates(
        proposed_updates=proposal["habit_updates"],
        current_habits=updated_personality["habit_biases"],
    )
    if not any((updated_trait_values, style_updates, preference_promotions, aversion_promotions, habit_updates)):
        return (None, None)
    persona_updates: dict[str, Any] = {
        "base_personality_updated_at": int(proposal["base_personality_updated_at"]),
        "updated_trait_values": updated_trait_values,
        "preference_promotions": preference_promotions,
        "aversion_promotions": aversion_promotions,
        "habit_updates": habit_updates,
        "evidence_event_ids": _unique_strings(proposal["evidence_event_ids"]),
        "evidence_summary": str(proposal["evidence_summary"]),
    }
    if style_updates:
        persona_updates["style_updates"] = style_updates
    return (persona_updates, updated_personality)


# Block: Trait update apply
def _bounded_trait_value_updates(
    *,
    proposal: dict[str, Any],
    updated_personality: dict[str, Any],
) -> dict[str, float]:
    updated_trait_values: dict[str, float] = {}
    for trait_delta in proposal["trait_deltas"]:
        trait_name = str(trait_delta["trait_name"])
        current_value = _normalized_signed_score(
            updated_personality["trait_values"][trait_name],
            field_name=f"self_state.personality.trait_values.{trait_name}",
        )
        bounded_delta = max(-0.10, min(0.10, float(trait_delta["delta"])))
        next_value = max(-1.0, min(1.0, current_value + bounded_delta))
        if current_value * next_value < 0.0 and abs(current_value) > 0.15:
            next_value = 0.0
        next_value = round(next_value, 2)
        if next_value == round(current_value, 2):
            continue
        updated_personality["trait_values"][trait_name] = next_value
        updated_trait_values[trait_name] = next_value
    return updated_trait_values


# Block: Style update apply
def _bounded_style_updates(
    *,
    proposal: dict[str, Any],
    updated_personality: dict[str, Any],
) -> dict[str, str]:
    style_updates = proposal.get("style_updates")
    if not isinstance(style_updates, dict):
        return {}
    updated_styles: dict[str, str] = {}
    for key in ("speech_tone", "distance_style", "confirmation_style", "response_pace"):
        if key not in style_updates:
            continue
        value = style_updates[key]
        if not isinstance(value, str) or not value:
            raise RuntimeError("personality_change_proposal.style_updates values must be non-empty string")
        if updated_personality["preferred_interaction_style"][key] == value:
            continue
        updated_personality["preferred_interaction_style"][key] = value
        updated_styles[key] = value
    return updated_styles


# Block: Preference update apply
def _bounded_preference_updates(
    *,
    proposed_entries: list[Any],
    current_entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    current_map = {
        (str(entry["domain"]), str(entry["target_key"])): entry
        for entry in current_entries
    }
    changed_entries: list[dict[str, Any]] = []
    for raw_entry in proposed_entries:
        if not isinstance(raw_entry, dict):
            raise RuntimeError("personality change promotion entries must be objects")
        domain = str(raw_entry["domain"])
        target_key = str(raw_entry["target_key"])
        proposed_weight = _normalized_unit_score(
            raw_entry["weight"],
            field_name="personality change promotion weight",
        )
        proposed_evidence_count = _positive_integer(
            raw_entry["evidence_count"],
            field_name="personality change promotion evidence_count",
        )
        existing_entry = current_map.get((domain, target_key))
        current_weight = 0.0
        current_evidence_count = 0
        if existing_entry is not None:
            current_weight = _normalized_unit_score(
                existing_entry["weight"],
                field_name="self_state.personality preference weight",
            )
            current_evidence_count = _positive_integer(
                existing_entry["evidence_count"],
                field_name="self_state.personality preference evidence_count",
            )
        bounded_weight = round(
            max(
                0.0,
                min(1.0, current_weight + max(-0.15, min(0.15, proposed_weight - current_weight))),
            ),
            2,
        )
        next_entry = {
            "domain": domain,
            "target_key": target_key,
            "weight": bounded_weight,
            "evidence_count": max(current_evidence_count, proposed_evidence_count),
        }
        if existing_entry is not None and next_entry == existing_entry:
            continue
        changed_entries.append(next_entry)
    return changed_entries


# Block: Preference merge
def _merged_preference_entries(
    *,
    current_entries: list[dict[str, Any]],
    changed_entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged_map = {
        (str(entry["domain"]), str(entry["target_key"])): dict(entry)
        for entry in current_entries
    }
    for entry in changed_entries:
        merged_map[(str(entry["domain"]), str(entry["target_key"]))] = dict(entry)
    merged_entries = list(merged_map.values())
    merged_entries.sort(
        key=lambda entry: (
            -float(entry["weight"]),
            -int(entry["evidence_count"]),
            str(entry["domain"]),
            str(entry["target_key"]),
        )
    )
    return merged_entries


# Block: Preference remove
def _remove_matching_preference_entries(
    *,
    current_entries: list[dict[str, Any]],
    changed_entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    removed_keys = {
        (str(entry["domain"]), str(entry["target_key"]))
        for entry in changed_entries
    }
    return [
        dict(entry)
        for entry in current_entries
        if (str(entry["domain"]), str(entry["target_key"])) not in removed_keys
    ]


# Block: Habit update apply
def _bounded_habit_updates(
    *,
    proposed_updates: dict[str, Any],
    current_habits: dict[str, Any],
) -> dict[str, Any]:
    habit_updates: dict[str, Any] = {}
    for key in ("preferred_action_types", "preferred_observation_kinds", "avoided_action_styles"):
        if key not in proposed_updates:
            continue
        proposed_values = _unique_strings(proposed_updates[key])
        if not proposed_values:
            continue
        current_values = _unique_strings(current_habits[key])
        merged_values = proposed_values + [
            value
            for value in current_values
            if value not in proposed_values
        ]
        if merged_values == current_values:
            continue
        current_habits[key] = merged_values
        habit_updates[key] = merged_values
    return habit_updates


# Block: Proposal update presence
def _proposal_has_updates(proposal: dict[str, Any]) -> bool:
    return bool(
        proposal["trait_deltas"]
        or proposal["preference_promotions"]
        or proposal["aversion_promotions"]
        or proposal["habit_updates"]
        or proposal.get("style_updates")
    )


# Block: Trait signal append
def _append_trait_signal(
    *,
    evidence: dict[str, Any],
    trait_name: str,
    strength: float,
    reason: str,
    source_cycle_ids: list[str],
    evidence_event_ids: list[str],
) -> None:
    evidence["trait_signals"][trait_name].append(
        {
            "strength": strength,
            "reason": reason,
            "source_cycle_ids": _unique_strings(source_cycle_ids),
            "evidence_event_ids": _unique_strings(evidence_event_ids),
        }
    )


# Block: Vote append
def _append_vote(
    *,
    vote_map: dict[tuple[str, str], dict[str, Any]],
    domain: str,
    target_key: str,
    confidence: float,
    evidence_count: int,
    source_cycle_ids: list[str],
    evidence_event_ids: list[str],
) -> None:
    vote_key = (domain, target_key)
    if vote_key not in vote_map:
        vote_map[vote_key] = {
            "domain": domain,
            "target_key": target_key,
            "confidence_total": 0.0,
            "evidence_count": 0,
            "source_cycle_ids": [],
            "evidence_event_ids": [],
        }
    vote = vote_map[vote_key]
    vote["confidence_total"] += confidence
    vote["evidence_count"] += evidence_count
    vote["source_cycle_ids"] = _unique_strings(
        [*vote["source_cycle_ids"], *source_cycle_ids]
    )
    vote["evidence_event_ids"] = _unique_strings(
        [*vote["evidence_event_ids"], *evidence_event_ids]
    )


# Block: Rank evidence append
def _append_rank_evidence(
    *,
    rank_map: dict[str, dict[str, Any]],
    key: str,
    cycle_id: str,
    evidence_event_ids: list[str],
) -> None:
    if key not in rank_map:
        rank_map[key] = {
            "evidence_count": 0,
            "source_cycle_ids": [],
            "evidence_event_ids": [],
        }
    rank_map[key]["evidence_count"] += 1
    rank_map[key]["source_cycle_ids"] = _unique_strings(
        [*rank_map[key]["source_cycle_ids"], cycle_id]
    )
    rank_map[key]["evidence_event_ids"] = _unique_strings(
        [*rank_map[key]["evidence_event_ids"], *evidence_event_ids]
    )


# Block: Habit ranking
def _ranked_habit_entries(
    *,
    rank_map: dict[str, dict[str, Any]],
    minimum_evidence_count: int,
    minimum_cycle_count: int,
) -> list[dict[str, Any]]:
    ranked_items = [
        {
            "key": key,
            "evidence_count": int(value["evidence_count"]),
            "cycle_count": len(value["source_cycle_ids"]),
            "evidence_event_ids": _unique_strings(value["evidence_event_ids"]),
        }
        for key, value in rank_map.items()
        if int(value["evidence_count"]) >= minimum_evidence_count
        and len(value["source_cycle_ids"]) >= minimum_cycle_count
        and _unique_strings(value["evidence_event_ids"])
    ]
    ranked_items.sort(
        key=lambda item: (
            -int(item["evidence_count"]),
            -int(item["cycle_count"]),
            str(item["key"]),
        )
    )
    return ranked_items


# Block: Action type normalize
def _normalize_action_type(raw_action_type: str) -> str | None:
    if raw_action_type in ACTION_STYLE_BY_TYPE:
        return raw_action_type
    return ACTION_TYPE_ALIASES.get(raw_action_type)


# Block: Observation kind map
def _observation_kind_for_action(action_type: str) -> str | None:
    return {
        "browse": "web_search",
        "look": "camera_scene",
    }.get(action_type)


# Block: Related cycle ids
def _related_cycle_ids(
    *,
    payload_json: Any,
    evidence_event_ids_json: Any,
    cycle_ids: set[str],
    event_cycle_map: dict[str, str],
) -> list[str]:
    payload = _decoded_object_json(
        raw_json=payload_json,
        field_name="payload_json",
    )
    source_cycle_id = payload.get("source_cycle_id")
    related_cycle_ids: list[str] = []
    if isinstance(source_cycle_id, str) and source_cycle_id in cycle_ids:
        related_cycle_ids.append(source_cycle_id)
    related_cycle_ids.extend(
        _cycle_ids_from_evidence_event_ids(
            evidence_event_ids_json=evidence_event_ids_json,
            cycle_ids=cycle_ids,
            event_cycle_map=event_cycle_map,
        )
    )
    return _unique_strings(related_cycle_ids)


# Block: Related evidence event ids
def _related_evidence_event_ids(
    *,
    payload_json: Any,
    evidence_event_ids_json: Any,
    cycle_ids: set[str],
    cycle_event_ids_map: dict[str, list[str]],
    event_cycle_map: dict[str, str],
) -> list[str]:
    payload = _decoded_object_json(
        raw_json=payload_json,
        field_name="payload_json",
    )
    source_cycle_id = payload.get("source_cycle_id")
    related_event_ids = _decoded_string_list_json(
        raw_json=evidence_event_ids_json,
        field_name="evidence_event_ids_json",
    )
    if isinstance(source_cycle_id, str) and source_cycle_id in cycle_ids:
        related_event_ids.extend(cycle_event_ids_map.get(source_cycle_id, []))
    return _unique_strings(
        event_id
        for event_id in related_event_ids
        if event_id in event_cycle_map and event_cycle_map[event_id] in cycle_ids
    )


# Block: Evidence event cycle ids
def _cycle_ids_from_evidence_event_ids(
    *,
    evidence_event_ids_json: Any,
    cycle_ids: set[str],
    event_cycle_map: dict[str, str],
) -> list[str]:
    event_ids = _decoded_string_list_json(
        raw_json=evidence_event_ids_json,
        field_name="evidence_event_ids_json",
    )
    related_cycle_ids = [
        event_cycle_map[event_id]
        for event_id in event_ids
        if event_id in event_cycle_map and event_cycle_map[event_id] in cycle_ids
    ]
    return _unique_strings(related_cycle_ids)


# Block: Long mood labels
def _long_mood_labels(payload: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    primary_label = payload.get("primary_label")
    if isinstance(primary_label, str) and primary_label:
        labels.append(primary_label)
    label_values = payload.get("labels")
    if isinstance(label_values, list):
        for label in label_values:
            if isinstance(label, str) and label:
                labels.append(label)
    baseline = payload.get("baseline")
    if isinstance(baseline, dict):
        baseline_primary_label = baseline.get("primary_label")
        if isinstance(baseline_primary_label, str) and baseline_primary_label:
            labels.append(baseline_primary_label)
        baseline_labels = baseline.get("labels")
        if isinstance(baseline_labels, list):
            for label in baseline_labels:
                if isinstance(label, str) and label:
                    labels.append(label)
    return _unique_strings(labels)


# Block: Relation kind extract
def _relation_kind(payload: dict[str, Any]) -> str | None:
    relation_kind = payload.get("relation_kind")
    if isinstance(relation_kind, str) and relation_kind:
        return relation_kind
    return None


# Block: Preference target extract
def _preference_target_key(target_ref: dict[str, Any]) -> str:
    for key in ("target_key", "entity_id", "entity_name_norm", "entity_name_raw"):
        value = target_ref.get(key)
        if isinstance(value, str) and value:
            return value
    raise RuntimeError("preference_memory.target_entity_ref_json must include target_key/entity_id/entity_name_norm/entity_name_raw")


# Block: Payload presence helper
def _payload_has_non_empty_value(payload: dict[str, Any], *keys: str) -> bool:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return True
        if isinstance(value, list) and value:
            return True
    return False


# Block: Signal total strength
def _signal_total_abs_strength(signal_rows: list[dict[str, Any]]) -> float:
    return sum(abs(float(row["strength"])) for row in signal_rows)


# Block: Placeholder builder
def _placeholders(count: int) -> str:
    return ",".join("?" for _ in range(count))


# Block: JSON object decode
def _decoded_object_json(*, raw_json: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(raw_json, str) or not raw_json:
        raise RuntimeError(f"{field_name} must be non-empty JSON string")
    decoded = json.loads(raw_json)
    if not isinstance(decoded, dict):
        raise RuntimeError(f"{field_name} must decode to object")
    return decoded


# Block: JSON string list decode
def _decoded_string_list_json(*, raw_json: Any, field_name: str) -> list[str]:
    if not isinstance(raw_json, str) or not raw_json:
        raise RuntimeError(f"{field_name} must be non-empty JSON string")
    decoded = json.loads(raw_json)
    if not isinstance(decoded, list):
        raise RuntimeError(f"{field_name} must decode to list")
    values: list[str] = []
    for value in decoded:
        if not isinstance(value, str) or not value:
            raise RuntimeError(f"{field_name} must contain only non-empty strings")
        values.append(value)
    return values


# Block: Positive integer helper
def _positive_integer(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RuntimeError(f"{field_name} must be integer >= 1")
    return value


# Block: Unit score helper
def _normalized_unit_score(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"{field_name} must be numeric")
    numeric_value = float(value)
    if numeric_value < 0.0:
        return 0.0
    if numeric_value > 1.0:
        return 1.0
    return numeric_value


# Block: Signed score helper
def _normalized_signed_score(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"{field_name} must be numeric")
    numeric_value = float(value)
    if numeric_value < -1.0:
        return -1.0
    if numeric_value > 1.0:
        return 1.0
    return numeric_value


# Block: Unique string helper
def _unique_strings(values: list[Any] | tuple[Any, ...] | Any) -> list[str]:
    unique_values: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value:
            continue
        if value in unique_values:
            continue
        unique_values.append(value)
    return unique_values
