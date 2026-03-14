"""Deterministic smoke check for stable context projection, retrieval, and reply-render contract."""

from __future__ import annotations

from dataclasses import dataclass
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Callable

from otomekairo.gateway.cognition_client import CognitionPlanRequest, ReplyRenderRequest
from otomekairo.gateway.runtime_query_store import RuntimeQueryStore
from otomekairo.schema.settings import build_default_settings
from otomekairo.usecase.build_cognition_input import (
    _build_action_selection_context,
    _build_stable_preferences,
    _build_recent_dialog,
    _build_reply_render_input,
    _build_selected_memory_pack,
)
from otomekairo.usecase.cognition_prompt_messages import (
    build_plan_messages,
    build_reply_render_messages,
)


# Block: Report constants
REPORT_SCHEMA_VERSION = 4


# Block: Smoke store bundle
@dataclass(frozen=True, slots=True)
class StableContextContractSmokeStores:
    runtime_query_store: RuntimeQueryStore


# Block: Public smoke runner
def run_stable_context_contract_smoke(
    *,
    keep_db: bool,
    build_stores: Callable[[Path], StableContextContractSmokeStores],
) -> dict[str, Any]:
    temp_dir = Path(tempfile.mkdtemp(prefix="otomekairo-stable-context-"))
    db_path = temp_dir / "core.sqlite3"
    try:
        default_settings = build_default_settings()
        stores = build_stores(db_path)
        _seed_preference_history(db_path=db_path)
        cognition_state = stores.runtime_query_store.read_cognition_state(default_settings)
        stable_preference_items = list(cognition_state.stable_preference_items)
        stable_preferences = _build_stable_preferences(
            preference_items=stable_preference_items,
        )
        recent_dialog = _build_recent_dialog(
            recent_event_window=cognition_state.memory_snapshot["recent_event_window"],
        )
        selected_memory_pack = _build_selected_memory_pack(
            memory_bundle=cognition_state.memory_snapshot,
        )
        action_selection_context = _build_action_selection_context(
            current_observation={
                "input_kind": "chat_message",
            },
            memory_bundle=cognition_state.memory_snapshot,
            recent_dialog=recent_dialog,
            selected_memory_pack=selected_memory_pack,
            stable_preferences=stable_preferences,
            long_mood_state={
                "summary_text": "好奇心はあるが慎重",
                "primary_label": "curious",
                "baseline_label": "calm",
                "shock_label": "",
                "stability": 0.45,
                "source_affect_labels": ["curious"],
            },
        )
        reply_render_input = _build_reply_render_input(
            current_observation={
                "observation_text": "ホラー映画の話を続けて",
            },
            time_context={
                "current_time_local_text": "2026-03-13 19:30",
                "relative_reference_text": "今",
            },
            attention_snapshot={
                "primary_focus": {
                    "focus_kind": "dialogue",
                    "summary": "ユーザーの会話リクエスト",
                    "reason_codes": ["user_dialogue"],
                },
            },
            retrieval_context={
                "plan": {
                    "mode": "chat",
                    "queries": ["ホラー映画"],
                },
                "selected": {
                    "selected_counts": {
                        "episodic": 1,
                    },
                },
            },
            stable_self_state={
                "current_emotion_label": "curious",
                "goal_summaries": ["丁寧に会話を続ける"],
                "relationship_summaries": ["userとの対話関係"],
                "active_task_summaries": [],
                "waiting_task_summaries": [],
                "invariants": {
                    "forbidden_action_types": [],
                    "forbidden_action_styles": [],
                    "required_confirmation_for": [],
                    "protected_targets": [],
                },
            },
            stable_preferences=stable_preferences,
            long_mood_state={
                "summary_text": "好奇心はあるが慎重",
                "primary_label": "curious",
                "baseline_label": "calm",
                "shock_label": "",
                "stability": 0.45,
                "source_affect_labels": ["curious"],
            },
            recent_dialog=[
                {
                    "role": "user",
                    "text": "映画の話をしよう",
                    "relative_time_text": "1分前",
                },
            ],
            selected_memory_pack=selected_memory_pack,
            selection_profile={
                "interaction_style": {
                    "speech_tone": "warm",
                    "response_pace": "steady",
                },
            },
        )
        cognition_plan_messages = build_plan_messages(
            CognitionPlanRequest(
                cycle_id="cycle_smoke",
                input_kind="chat_message",
                cognition_input=_smoke_cognition_input(
                    stable_preferences=stable_preferences,
                    selected_memory_pack=selected_memory_pack,
                    recent_dialog=recent_dialog,
                ),
                completion_settings={
                    "temperature": 0.0,
                },
            )
        )
        messages = build_reply_render_messages(
            ReplyRenderRequest(
                cycle_id="cycle_smoke",
                input_kind="chat_message",
                reply_render_input=reply_render_input,
                reply_render_plan={
                    "intention_summary": "話題を自然に返す",
                    "decision_reason": "会話継続を優先",
                    "reply_mode": "answer",
                    "reply_reason": "dialogue continuity",
                    "memory_focus_summary": "最近の嗜好と会話履歴",
                    "memory_focus_kind": "preference",
                    "action_summaries": ["speak:会話継続"],
                },
                completion_settings={
                    "temperature": 0.0,
                },
            )
        )
        report = _build_report(
            db_path=db_path,
            keep_db=keep_db,
            memory_snapshot=cognition_state.memory_snapshot,
            stable_preference_items=stable_preference_items,
            stable_preferences=stable_preferences,
            selected_memory_pack=selected_memory_pack,
            action_selection_context=action_selection_context,
            cognition_plan_prompt_messages=cognition_plan_messages,
            reply_render_input=reply_render_input,
            prompt_messages=messages,
        )
        _validate_report(report)
        return report
    finally:
        if not keep_db:
            shutil.rmtree(temp_dir, ignore_errors=True)


# Block: Preference history seed
def _seed_preference_history(*, db_path: Path) -> None:
    now_ms = 1_710_000_000_000
    with _connect_row_db(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        for index in range(12):
            _insert_preference_row(
                connection=connection,
                preference_id=f"pref_like_{index}",
                owner_scope="self",
                domain="topic_keyword",
                polarity="like",
                status="confirmed",
                target_key=f"展示{index}",
                confidence=0.98 - index * 0.02,
                created_at=now_ms - 10_000 - index,
                updated_at=now_ms - 10_000 - index,
            )
            _insert_preference_row(
                connection=connection,
                preference_id=f"pref_dislike_{index}",
                owner_scope="self",
                domain="topic_keyword",
                polarity="dislike",
                status="confirmed",
                target_key=f"苦手話題{index}",
                confidence=0.97 - index * 0.02,
                created_at=now_ms - 20_000 - index,
                updated_at=now_ms - 20_000 - index,
            )
            _insert_preference_row(
                connection=connection,
                preference_id=f"pref_revoked_{index}",
                owner_scope="self",
                domain="topic_keyword",
                polarity="dislike",
                status="revoked",
                target_key=f"撤回話題{index}",
                confidence=0.96 - index * 0.02,
                created_at=now_ms - 30_000 - index,
                updated_at=now_ms - 30_000 - index,
            )
        _insert_preference_row(
            connection=connection,
            preference_id="pref_latest_candidate",
            owner_scope="self",
            domain="topic_keyword",
            polarity="like",
            status="candidate",
            target_key="展示0",
            confidence=0.99,
            created_at=now_ms,
            updated_at=now_ms,
        )
        _insert_preference_row(
            connection=connection,
            preference_id="pref_other_entity",
            owner_scope="other_entity",
            domain="topic_keyword",
            polarity="like",
            status="confirmed",
            target_key="他人好み",
            confidence=0.95,
                created_at=now_ms - 40_000,
                updated_at=now_ms - 40_000,
        )
        _rebuild_stable_preference_projection(connection=connection)


# Block: Stable preference projection rebuild
def _rebuild_stable_preference_projection(
    *,
    connection: sqlite3.Connection,
) -> None:
    connection.execute("DELETE FROM stable_preference_projection")
    seen_keys: set[tuple[str, str, str, str]] = set()
    for row in connection.execute(
        """
        SELECT
            preference_id,
            owner_scope,
            target_entity_ref_json,
            target_key,
            domain,
            polarity,
            status,
            confidence,
            evidence_event_ids_json,
            created_at,
            updated_at
        FROM preference_memory
        ORDER BY updated_at DESC, created_at DESC, preference_id DESC
        """
    ).fetchall():
        projection_key = (
            str(row["owner_scope"]),
            str(row["domain"]),
            str(row["target_key"]),
            str(row["polarity"]),
        )
        if projection_key in seen_keys:
            continue
        seen_keys.add(projection_key)
        _sync_stable_preference_projection(
            connection=connection,
            preference_row=row,
        )


# Block: Stable preference projection sync
def _sync_stable_preference_projection(
    *,
    connection: sqlite3.Connection,
    preference_row: sqlite3.Row,
) -> None:
    owner_scope = str(preference_row["owner_scope"])
    target_key = str(preference_row["target_key"])
    domain = str(preference_row["domain"])
    polarity = str(preference_row["polarity"])
    status = str(preference_row["status"])
    if owner_scope != "self" or status not in {"confirmed", "revoked"}:
        connection.execute(
            """
            DELETE FROM stable_preference_projection
            WHERE owner_scope = ?
              AND domain = ?
              AND target_key = ?
              AND polarity = ?
            """,
            (owner_scope, domain, target_key, polarity),
        )
        return
    connection.execute(
        """
        INSERT INTO stable_preference_projection (
            owner_scope,
            target_entity_ref_json,
            target_key,
            domain,
            polarity,
            preference_id,
            status,
            confidence,
            evidence_event_ids_json,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(owner_scope, domain, target_key, polarity)
        DO UPDATE SET
            target_entity_ref_json = excluded.target_entity_ref_json,
            preference_id = excluded.preference_id,
            status = excluded.status,
            confidence = excluded.confidence,
            evidence_event_ids_json = excluded.evidence_event_ids_json,
            created_at = excluded.created_at,
            updated_at = excluded.updated_at
        """,
        (
            owner_scope,
            str(preference_row["target_entity_ref_json"]),
            target_key,
            domain,
            polarity,
            str(preference_row["preference_id"]),
            status,
            float(preference_row["confidence"]),
            str(preference_row["evidence_event_ids_json"]),
            int(preference_row["created_at"]),
            int(preference_row["updated_at"]),
        ),
    )


# Block: Preference row insert
def _insert_preference_row(
    *,
    connection: sqlite3.Connection,
    preference_id: str,
    owner_scope: str,
    domain: str,
    polarity: str,
    status: str,
    target_key: str,
    confidence: float,
    created_at: int,
    updated_at: int,
) -> None:
    connection.execute(
        """
        INSERT INTO preference_memory (
            preference_id,
            owner_scope,
            target_entity_ref_json,
            target_key,
            domain,
            polarity,
            status,
            confidence,
            evidence_event_ids_json,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            preference_id,
            owner_scope,
            json.dumps(
                {
                    "target_key": target_key,
                },
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ),
            target_key,
            domain,
            polarity,
            status,
            confidence,
            json.dumps(["evt_smoke"], ensure_ascii=True, separators=(",", ":")),
            created_at,
            updated_at,
        ),
    )


# Block: Report build
def _build_report(
    *,
    db_path: Path,
    keep_db: bool,
    memory_snapshot: dict[str, Any],
    stable_preference_items: list[dict[str, Any]],
    stable_preferences: dict[str, Any],
    selected_memory_pack: dict[str, Any],
    action_selection_context: dict[str, Any],
    cognition_plan_prompt_messages: list[dict[str, Any]],
    reply_render_input: dict[str, Any],
    prompt_messages: list[dict[str, Any]],
) -> dict[str, Any]:
    bucket_counts = {
        "confirmed_like": 0,
        "confirmed_dislike": 0,
        "revoked": 0,
    }
    stable_keys: list[str] = []
    for preference_item in stable_preference_items:
        payload = preference_item["payload"]
        stable_keys.append(
            f"{payload['status']}:{payload['polarity']}:{payload['target_entity_ref']['target_key']}"
        )
        if payload["status"] == "confirmed" and payload["polarity"] == "like":
            bucket_counts["confirmed_like"] += 1
        elif payload["status"] == "confirmed" and payload["polarity"] == "dislike":
            bucket_counts["confirmed_dislike"] += 1
        elif payload["status"] == "revoked":
            bucket_counts["revoked"] += 1
    planner_user_prompt = str(cognition_plan_prompt_messages[1]["content"])
    planner_system_prompt = str(cognition_plan_prompt_messages[0]["content"])
    reply_user_prompt = str(prompt_messages[1]["content"])
    retrieval_preference_keys = [
        preference_item["payload"]["target_entity_ref"]["target_key"]
        for preference_item in memory_snapshot["preference_items"]
        if isinstance(preference_item, dict)
        and str(preference_item.get("memory_kind")) == "preference"
        and isinstance(preference_item.get("payload"), dict)
        and isinstance(preference_item["payload"].get("target_entity_ref"), dict)
        and isinstance(preference_item["payload"]["target_entity_ref"].get("target_key"), str)
    ]
    revoked_prompt_targets = [
        entry["target_key"]
        for entry in stable_preferences["revoked"]
    ]
    preference_texts = selected_memory_pack["preference"]
    relationship_texts = selected_memory_pack["relationship"]
    checks = {
        "stable_projection_bucket_limits_respected": bucket_counts == {
            "confirmed_like": 8,
            "confirmed_dislike": 8,
            "revoked": 8,
        },
        "stable_projection_candidate_excluded": (
            "candidate:like:展示0" not in stable_keys
            and "confirmed:like:展示0" not in stable_keys
            and "confirmed:like:展示1" in stable_keys
        ),
        "stable_projection_other_entity_excluded": (
            all("他人好み" not in stable_key for stable_key in stable_keys)
        ),
        "stable_preferences_flow_into_retrieval_preference_slot": (
            "展示1" in retrieval_preference_keys
            and "展示9" in retrieval_preference_keys
            and "苦手話題1" in retrieval_preference_keys
            and "苦手話題9" in retrieval_preference_keys
            and "撤回話題1" in retrieval_preference_keys
            and "撤回話題9" in retrieval_preference_keys
        ),
        "reply_render_input_carries_stable_preferences": (
            len(reply_render_input["stable_preferences"]["likes"]) == 8
            and len(reply_render_input["stable_preferences"]["dislikes"]) == 8
            and len(reply_render_input["stable_preferences"]["revoked"]) == 8
        ),
        "selected_memory_pack_separates_preferences": (
            bool(preference_texts)
            and all("撤回済みの苦手" in text or "好み:" in text or "苦手:" in text for text in preference_texts[:4])
            and not relationship_texts
        ),
        "action_selection_context_carries_preference_texts": (
            bool(action_selection_context["preference_texts"])
            and not action_selection_context["relationship_texts"]
        ),
        "planner_prompt_mentions_revoked_preferences": (
            "取り消し済み嗜好:" in planner_user_prompt
            and all(target_key in planner_user_prompt for target_key in revoked_prompt_targets[:3])
        ),
        "planner_prompt_does_not_use_legacy_persona_preferences": (
            "学習済みの好み:" not in planner_system_prompt
            and "学習済みの回避:" not in planner_system_prompt
        ),
        "reply_render_prompt_mentions_revoked_preferences": (
            "取り消し済み嗜好:" in reply_user_prompt
            and all(target_key in reply_user_prompt for target_key in revoked_prompt_targets[:3])
        ),
    }
    report = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "checks": checks,
        "stable_preference_item_count": len(stable_preference_items),
        "bucket_counts": bucket_counts,
        "stable_keys": stable_keys,
        "retrieval_preference_keys": retrieval_preference_keys,
        "selected_preference_texts": preference_texts,
        "selected_relationship_texts": relationship_texts,
        "action_selection_preference_text_count": len(action_selection_context["preference_texts"]),
        "reply_render_revoked_count": len(reply_render_input["stable_preferences"]["revoked"]),
    }
    if keep_db:
        report["db_path"] = str(db_path)
    return report


# Block: Report validation
def _validate_report(report: dict[str, Any]) -> None:
    failed_checks = [
        check_name
        for check_name, passed in report["checks"].items()
        if bool(passed) is False
    ]
    if failed_checks:
        raise RuntimeError(
            "stable_context_contract_smoke failed: " + ", ".join(sorted(failed_checks))
        )


# Block: Minimal cognition input for planner prompt smoke
def _smoke_cognition_input(
    *,
    stable_preferences: dict[str, Any],
    selected_memory_pack: dict[str, Any],
    recent_dialog: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "time_context": {
            "current_time_local_text": "2026-03-13 19:30",
            "timezone_name": "Asia/Tokyo",
            "relative_reference_text": "今",
        },
        "self_snapshot": {
            "current_emotion": {
                "primary_label": "curious",
            },
            "invariants": {
                "forbidden_action_types": [],
                "forbidden_action_styles": [],
                "required_confirmation_for": [],
                "protected_targets": [],
            },
            "long_term_goals": {
                "goals": [
                    {
                        "summary": "丁寧に会話を続ける",
                    },
                ],
            },
        },
        "stable_self_state": {
            "current_emotion_label": "curious",
            "goal_summaries": ["丁寧に会話を続ける"],
            "relationship_summaries": ["userとの対話関係"],
            "active_task_summaries": [],
            "waiting_task_summaries": [],
            "invariants": {
                "forbidden_action_types": [],
                "forbidden_action_styles": [],
                "required_confirmation_for": [],
                "protected_targets": [],
            },
        },
        "stable_preferences": stable_preferences,
        "long_mood_state": {
            "summary_text": "好奇心はあるが慎重",
            "primary_label": "curious",
            "baseline_label": "calm",
            "shock_label": "",
            "stability": 0.45,
            "source_affect_labels": ["curious"],
        },
        "behavior_settings": {
            "second_person_label": "あなた",
            "speech_style": "warm",
            "response_pace": "steady",
            "proactivity_level": "balanced",
            "browse_preference": "neutral",
            "notify_preference": "neutral",
            "verbosity_bias": "balanced",
            "system_prompt": "",
            "addon_prompt": "",
        },
        "selection_profile": {
            "trait_values": {
                "sociability": 0.52,
                "caution": 0.61,
                "curiosity": 0.74,
                "persistence": 0.55,
                "warmth": 0.70,
                "assertiveness": 0.40,
                "novelty_preference": 0.63,
            },
            "interaction_style": {
                "speech_tone": "warm",
                "distance_style": "friendly",
                "confirmation_style": "light",
                "response_pace": "steady",
            },
            "relationship_priorities": [
                {
                    "target_ref": "entity:user",
                    "reason_tag": "conversation",
                    "priority_weight": 0.8,
                },
            ],
            "habit_biases": {
                "preferred_action_types": ["speak"],
                "preferred_observation_kinds": ["dialogue"],
                "avoided_action_styles": [],
            },
            "emotion_bias": {
                "caution_bias": 0.1,
                "approach_bias": 0.2,
                "avoidance_bias": 0.0,
                "speech_intensity_bias": 0.1,
            },
            "drive_bias": {
                "task_progress_bias": 0.1,
                "exploration_bias": 0.2,
                "maintenance_bias": 0.0,
                "social_bias": 0.3,
            },
        },
        "body_snapshot": {
            "posture": {
                "mode": "idle",
            },
            "sensor_availability": {
                "camera": False,
                "microphone": True,
            },
            "load": {
                "task_queue_pressure": 0.10,
                "interaction_load": 0.20,
            },
        },
        "world_snapshot": {
            "situation_summary": "ブラウザ会話中",
        },
        "drive_snapshot": {
            "priority_effects": {
                "task_progress_bias": 0.10,
                "exploration_bias": 0.20,
                "maintenance_bias": 0.00,
                "social_bias": 0.30,
            },
        },
        "task_snapshot": {
            "active_tasks": [],
            "waiting_external_tasks": [],
        },
        "attention_snapshot": {
            "primary_focus": {
                "focus_kind": "dialogue",
                "summary": "ユーザーの会話リクエスト",
                "reason_codes": ["user_dialogue"],
            },
        },
        "selected_memory_pack": selected_memory_pack,
        "recent_dialog": recent_dialog,
        "retrieval_context": {
            "plan": {
                "mode": "chat",
                "queries": ["ホラー映画"],
            },
            "selected": {
                "selected_counts": {
                    "preference": 1,
                },
            },
        },
        "policy_snapshot": {
            "runtime_policy": {
                "camera_enabled": False,
                "camera_available": False,
                "camera_candidate_count": 0,
                "microphone_enabled": True,
            },
            "input_evaluation": {
                "input_role": "dialogue",
                "attention_priority": "high",
                "factuality": "unverified_user_report",
                "should_reply_in_channel": True,
                "can_override_persona": False,
                "must_preserve_invariants": True,
            },
        },
        "camera_candidates": [],
        "skill_candidates": [],
        "current_observation": {
            "input_kind": "chat_message",
            "observation_text": "ホラー映画の話を続けて",
            "captured_at_local_text": "2026-03-13 19:30",
            "relative_time_text": "今",
            "attachment_count": 0,
            "attachment_summary_text": "なし",
            "attachments": [],
        },
    }


# Block: SQLite row connection
def _connect_row_db(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection
