"""Deterministic smoke check for stable context projection and reply-render contract."""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from otomekairo import __version__
from otomekairo.gateway.cognition_client import ReplyRenderRequest
from otomekairo.infra.litellm_cognition_client import _build_reply_render_messages
from otomekairo.infra.sqlite_state_store import SqliteStateStore
from otomekairo.schema.settings import build_default_settings
from otomekairo.usecase.build_cognition_input import (
    _build_preference_selection_state,
    _build_reply_render_input,
)


# Block: Report constants
REPORT_SCHEMA_VERSION = 1


# Block: Public smoke runner
def run_stable_context_contract_smoke(*, keep_db: bool) -> dict[str, Any]:
    temp_dir = Path(tempfile.mkdtemp(prefix="otomekairo-stable-context-"))
    db_path = temp_dir / "core.sqlite3"
    try:
        default_settings = build_default_settings()
        store = SqliteStateStore(
            db_path=db_path,
            initializer_version=__version__,
        )
        store.initialize()
        _seed_preference_history(store=store)
        cognition_state = store.read_cognition_state(default_settings)
        stable_preference_items = list(cognition_state.stable_preference_items)
        preference_selection_state = _build_preference_selection_state(
            preference_items=stable_preference_items,
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
            confirmed_preferences=dict(preference_selection_state["confirmed_preferences"]),
            revoked_preferences=list(preference_selection_state["revoked_preferences"]),
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
            selected_memory_pack={
                "recent_context": ["最近は展示の話が多い"],
                "working_memory": [],
                "episodic": [],
                "facts": [],
                "affective": [],
                "relationship": [],
                "reflection": [],
            },
            selection_profile={
                "interaction_style": {
                    "speech_tone": "warm",
                    "response_pace": "steady",
                },
            },
        )
        messages = _build_reply_render_messages(
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
            stable_preference_items=stable_preference_items,
            reply_render_input=reply_render_input,
            prompt_messages=messages,
        )
        _validate_report(report)
        return report
    finally:
        if not keep_db:
            shutil.rmtree(temp_dir, ignore_errors=True)


# Block: Preference history seed
def _seed_preference_history(*, store: SqliteStateStore) -> None:
    now_ms = 1_710_000_000_000
    with store._connect() as connection:
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
        store._rebuild_stable_preference_projection(connection=connection)


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
            domain,
            polarity,
            status,
            confidence,
            evidence_event_ids_json,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
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
    stable_preference_items: list[dict[str, Any]],
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
    user_prompt = str(prompt_messages[1]["content"])
    revoked_prompt_targets = [
        entry["target_key"]
        for entry in reply_render_input["revoked_preferences"]
    ]
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
        "reply_render_input_carries_revoked_preferences": len(reply_render_input["revoked_preferences"]) == 8,
        "reply_render_prompt_mentions_revoked_preferences": (
            "取り消し済み嗜好:" in user_prompt
            and all(target_key in user_prompt for target_key in revoked_prompt_targets[:3])
        ),
    }
    report = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "checks": checks,
        "stable_preference_item_count": len(stable_preference_items),
        "bucket_counts": bucket_counts,
        "stable_keys": stable_keys,
        "reply_render_revoked_count": len(reply_render_input["revoked_preferences"]),
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

