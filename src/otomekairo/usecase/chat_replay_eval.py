"""Build replay-oriented evaluation reports for recent chat cycles."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any


# Block: Report constants
REPORT_SCHEMA_VERSION = 2
ACTION_TYPE_ALIASES = {
    "enqueue_browse_task": "browse",
    "complete_browse_task": "browse",
    "control_camera_look": "look",
    "dispatch_notice": "notify",
    "emit_chat_response": "speak",
    "speak_ui_message": "speak",
}
ACTION_RESPONSE_CUES = {
    "browse": ("調べ", "検索", "確認", "再検索"),
    "look": ("見る", "視点", "カメラ", "確認"),
    "notify": ("知らせ", "通知", "伝え"),
    "speak": ("話", "返答", "伝え"),
}
FAILURE_RESPONSE_CUES = {
    "timeout": ("timeout", "タイムアウト", "待", "やり直", "再試行"),
    "network_unavailable": ("通信", "接続", "ネットワーク", "届かない"),
}
GENTLE_TONE_CUES = (
    "したよ",
    "するね",
    "だよ",
    "整理した",
    "教える",
    "確認したよ",
)
CAUTIOUS_TONE_CUES = (
    "まだ",
    "止まった",
    "待って",
    "やり直",
    "再試行",
    "できない",
    "必要",
    "timeout",
    "タイムアウト",
)


# Block: Report build
def build_chat_replay_eval_report(
    *,
    db_path: Path,
    limit: int,
) -> dict[str, Any]:
    if limit <= 0:
        raise RuntimeError("chat replay eval limit must be positive")
    with _connect_row_db(db_path) as connection:
        chat_cycles = _read_chat_cycles(
            connection=connection,
            limit=limit,
        )
        cycle_packets = _build_cycle_packets(
            connection=connection,
            chat_cycles=chat_cycles,
        )
    if not cycle_packets:
        return {
            "report_schema_version": REPORT_SCHEMA_VERSION,
            "cycle_count": 0,
            "cycles": [],
        }
    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "cycle_count": len(cycle_packets),
        "window": {
            "oldest_committed_at": cycle_packets[0]["committed_at"],
            "oldest_committed_at_utc_text": _utc_text(cycle_packets[0]["committed_at"]),
            "latest_committed_at": cycle_packets[-1]["committed_at"],
            "latest_committed_at_utc_text": _utc_text(cycle_packets[-1]["committed_at"]),
        },
        "overview": _build_overview(cycle_packets=cycle_packets),
        "cycles": cycle_packets,
    }


# Block: Report formatting
def format_chat_replay_eval_report(report: dict[str, Any]) -> str:
    cycle_count = int(report.get("cycle_count", 0))
    if cycle_count == 0:
        return "chat replay eval: no chat cycles"
    overview = _require_object(report.get("overview"), "report.overview must be object")
    window = _require_object(report.get("window"), "report.window must be object")
    lines = [
        "chat replay eval",
        (
            "window: "
            f"{window['oldest_committed_at_utc_text']} -> {window['latest_committed_at_utc_text']} "
            f"({cycle_count} cycles)"
        ),
        (
            "continuity: "
            f"dialogue_thread_reuse {overview['dialogue_thread_reuse_cycle_count']} "
            f"({overview['dialogue_thread_reuse_rate_percent']}%), "
            f"preference_alignment {overview['preference_alignment_cycle_count']} "
            f"({overview['preference_alignment_rate_percent']}%), "
            f"preference_restore {overview['preference_restore_cycle_count']} "
            f"({overview['preference_restore_rate_percent']}%)"
        ),
        (
            "mood: "
            f"carryover {overview['long_mood_carryover_cycle_count']} "
            f"({overview['long_mood_carryover_rate_percent']}%), "
            f"same_label {overview['long_mood_same_label_cycle_count']} "
            f"({overview['long_mood_same_label_rate_percent']}%), "
            f"top_transition {overview['top_long_mood_transition']}"
        ),
            (
                "response: "
                f"assistant_messages {overview['assistant_response_cycle_count']}, "
                f"date_recall {overview['response_date_recall_cycle_count']} "
                f"({overview['response_date_recall_rate_percent']}%)"
            ),
        (
            "behavior: "
            f"action_transparency {overview['response_action_transparency_cycle_count']}/"
            f"{overview['response_action_transparency_eligible_cycle_count']} "
            f"({overview['response_action_transparency_rate_percent']}%), "
            f"failure_explanation {overview['response_failure_explanation_cycle_count']}/"
            f"{overview['response_failure_explanation_eligible_cycle_count']} "
            f"({overview['response_failure_explanation_rate_percent']}%)"
        ),
        (
            "behavior2: "
            f"preference_reference {overview['response_preference_reference_cycle_count']}/"
            f"{overview['response_preference_reference_eligible_cycle_count']} "
            f"({overview['response_preference_reference_rate_percent']}%), "
            f"preference_violation {overview['response_preference_violation_cycle_count']}/"
            f"{overview['response_preference_violation_eligible_cycle_count']} "
            f"({overview['response_preference_violation_rate_percent']}%), "
            f"mood_tone_hint {overview['response_mood_tone_hint_cycle_count']}/"
            f"{overview['response_mood_tone_hint_eligible_cycle_count']} "
            f"({overview['response_mood_tone_hint_rate_percent']}%)"
        ),
    ]
    return "\n".join(lines)


# Block: Chat cycle read
def _read_chat_cycles(
    *,
    connection: sqlite3.Connection,
    limit: int,
) -> list[dict[str, Any]]:
    commit_rows = connection.execute(
        """
        SELECT commit_id, cycle_id, committed_at, commit_payload_json
        FROM commit_records
        WHERE json_extract(commit_payload_json, '$.processed_input_kind') = 'chat_message'
        ORDER BY commit_id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    chat_cycles: list[dict[str, Any]] = []
    for commit_row in reversed(commit_rows):
        commit_payload = json.loads(commit_row["commit_payload_json"])
        if not isinstance(commit_payload, dict):
            raise RuntimeError("commit_payload_json must decode to object")
        event_ids = _required_string_list(
            commit_payload.get("event_ids"),
            "chat replay commit_payload.event_ids must be string array",
        )
        placeholders = ",".join("?" for _ in event_ids)
        event_rows = connection.execute(
            f"""
            SELECT event_id, source, kind, observation_summary, action_summary, result_summary, created_at
            FROM events
            WHERE event_id IN ({placeholders})
            ORDER BY created_at ASC, event_id ASC
            """,
            tuple(event_ids),
        ).fetchall()
        action_rows = connection.execute(
            """
            SELECT action_type, status, failure_mode
            FROM action_history
            WHERE cycle_id = ?
            ORDER BY started_at ASC
            """,
            (str(commit_row["cycle_id"]),),
        ).fetchall()
        dialogue_thread_rows = connection.execute(
            f"""
            SELECT thread_key
            FROM event_threads
            WHERE event_id IN ({placeholders})
              AND thread_key LIKE 'dialogue:%'
            ORDER BY created_at DESC, thread_key ASC
            """,
            tuple(event_ids),
        ).fetchall()
        chat_cycles.append(
            {
                "cycle_id": str(commit_row["cycle_id"]),
                "commit_id": int(commit_row["commit_id"]),
                "committed_at": int(commit_row["committed_at"]),
                "user_text": _user_text_for_cycle(event_rows),
                "assistant_text": _assistant_text_for_cycle(event_rows),
                "action_types": [
                    _normalized_action_type(str(action_row["action_type"]))
                    for action_row in action_rows
                    if _normalized_action_type(str(action_row["action_type"])) is not None
                ],
                "failed_action_types": [
                    _normalized_action_type(str(action_row["action_type"]))
                    for action_row in action_rows
                    if str(action_row["status"]) == "failed"
                    and _normalized_action_type(str(action_row["action_type"])) is not None
                ],
                "failure_modes": _unique_non_empty_strings(
                    [
                        str(action_row["failure_mode"])
                        for action_row in action_rows
                        if action_row["failure_mode"] is not None
                    ]
                ),
                "dialogue_thread_keys": _unique_non_empty_strings(
                    [str(dialogue_thread_row["thread_key"]) for dialogue_thread_row in dialogue_thread_rows]
                ),
            }
        )
    return chat_cycles


# Block: Replay packet build
def _build_cycle_packets(
    *,
    connection: sqlite3.Connection,
    chat_cycles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not chat_cycles:
        return []
    latest_committed_at = int(chat_cycles[-1]["committed_at"])
    preference_revisions = _read_preference_revisions(
        connection=connection,
    )
    long_mood_revisions = _read_long_mood_revisions(
        connection=connection,
    )
    next_commit_after_latest_chat = _next_commit_after(
        connection=connection,
        committed_at=latest_committed_at,
    )
    latest_replay_at = (
        next_commit_after_latest_chat
        if next_commit_after_latest_chat is not None
        else max(
            [latest_committed_at]
            + [int(revision["created_at"]) for revision in preference_revisions]
            + [int(revision["created_at"]) for revision in long_mood_revisions]
        )
    )
    replay_snapshots = _replay_state_snapshots(
        preference_revisions=preference_revisions,
        long_mood_revisions=long_mood_revisions,
        cutoffs=[int(chat_cycle["committed_at"]) for chat_cycle in chat_cycles] + [latest_replay_at],
    )
    seen_dialogue_threads: set[str] = set()
    cycle_packets: list[dict[str, Any]] = []
    for index, chat_cycle in enumerate(chat_cycles):
        before_snapshot = replay_snapshots[index]
        after_snapshot = replay_snapshots[index + 1]
        confirmed_keys_before = set(before_snapshot["confirmed_keys"])
        revoked_keys_before = set(before_snapshot["revoked_keys"])
        confirmed_keys_after = set(after_snapshot["confirmed_keys"])
        revoked_keys_after = set(after_snapshot["revoked_keys"])
        dialogue_thread_keys = list(chat_cycle["dialogue_thread_keys"])
        reused_dialogue_thread_keys = [
            dialogue_thread_key
            for dialogue_thread_key in dialogue_thread_keys
            if dialogue_thread_key in seen_dialogue_threads
        ]
        preference_alignment_keys = sorted(
            confirmed_keys_before.intersection(
                {
                    f"action_type:{action_type}:like"
                    for action_type in chat_cycle["action_types"]
                }
            )
        )
        restored_preference_keys = sorted(
            revoked_keys_before.intersection(confirmed_keys_after)
        )
        long_mood_before = _long_mood_summary(before_snapshot["long_mood"])
        long_mood_after = _long_mood_summary(after_snapshot["long_mood"])
        assistant_text = (
            str(chat_cycle["assistant_text"])
            if chat_cycle["assistant_text"] is not None
            else ""
        )
        response_behavior = _build_response_behavior_signals(
            action_types=list(chat_cycle["action_types"]),
            failed_action_types=list(chat_cycle["failed_action_types"]),
            failure_modes=list(chat_cycle["failure_modes"]),
            assistant_text=assistant_text,
            confirmed_preferences_before=before_snapshot["confirmed_entries"],
            revoked_preferences_before=before_snapshot["revoked_entries"],
            long_mood_before=long_mood_before,
        )
        committed_at = int(chat_cycle["committed_at"])
        cycle_packet = {
            "cycle_id": str(chat_cycle["cycle_id"]),
            "commit_id": int(chat_cycle["commit_id"]),
            "committed_at": committed_at,
            "committed_at_utc_text": _utc_text(committed_at),
            "user_text": str(chat_cycle["user_text"]),
            "assistant_text": assistant_text if assistant_text else None,
            "action_types": list(chat_cycle["action_types"]),
            "failed_action_types": list(chat_cycle["failed_action_types"]),
            "failure_modes": list(chat_cycle["failure_modes"]),
            "dialogue_thread_keys": dialogue_thread_keys,
            "reused_dialogue_thread_keys": reused_dialogue_thread_keys,
            "confirmed_preferences_before": sorted(confirmed_keys_before),
            "confirmed_preferences_after": sorted(confirmed_keys_after),
            "revoked_preferences_before": sorted(revoked_keys_before),
            "revoked_preferences_after": sorted(revoked_keys_after),
            "long_mood_before": long_mood_before,
            "long_mood_after": long_mood_after,
            "response_behavior_signals": response_behavior,
            "checks": {
                "dialogue_thread_reused": bool(reused_dialogue_thread_keys),
                "preference_aligned_action": bool(preference_alignment_keys),
                "preference_restored": bool(restored_preference_keys),
                "long_mood_carried": long_mood_before is not None and long_mood_after is not None,
                "long_mood_same_label": (
                    long_mood_before is not None
                    and long_mood_after is not None
                    and long_mood_before["primary_label"] == long_mood_after["primary_label"]
                ),
                "response_date_recalled": _response_date_recalled(
                    user_text=str(chat_cycle["user_text"]),
                    assistant_text=assistant_text,
                ),
                "response_action_transparent": bool(response_behavior["matched_action_cues"]),
                "response_failure_explained": bool(response_behavior["explained_failure_modes"]),
                "response_preference_referenced": bool(response_behavior["referenced_preference_keys"]),
                "response_preference_violated": bool(response_behavior["violated_preference_keys"]),
                "response_mood_tone_hinted": bool(response_behavior["matched_tone_hint"]),
            },
            "preference_alignment_keys": preference_alignment_keys,
            "restored_preference_keys": restored_preference_keys,
        }
        cycle_packets.append(cycle_packet)
        seen_dialogue_threads.update(dialogue_thread_keys)
    return cycle_packets


# Block: Replay state snapshots
def _replay_state_snapshots(
    *,
    preference_revisions: list[dict[str, Any]],
    long_mood_revisions: list[dict[str, Any]],
    cutoffs: list[int],
) -> list[dict[str, Any]]:
    preference_cursor = 0
    long_mood_cursor = 0
    current_preferences: dict[str, dict[str, Any]] = {}
    current_long_mood: dict[str, Any] | None = None
    snapshots: list[dict[str, Any]] = []
    for cutoff in cutoffs:
        while preference_cursor < len(preference_revisions):
            revision = preference_revisions[preference_cursor]
            if int(revision["created_at"]) > cutoff:
                break
            current_preferences[str(revision["entity_id"])] = dict(revision["after_json"])
            preference_cursor += 1
        while long_mood_cursor < len(long_mood_revisions):
            revision = long_mood_revisions[long_mood_cursor]
            if int(revision["created_at"]) > cutoff:
                break
            current_long_mood = dict(revision["after_json"])
            long_mood_cursor += 1
        snapshots.append(
            {
                "confirmed_keys": sorted(
                    _preference_keys_for_status(
                        preferences=current_preferences.values(),
                        status="confirmed",
                    )
                ),
                "confirmed_entries": _preference_entries_for_status(
                    preferences=current_preferences.values(),
                    status="confirmed",
                ),
                "revoked_keys": sorted(
                    _preference_keys_for_status(
                        preferences=current_preferences.values(),
                        status="revoked",
                    )
                ),
                "revoked_entries": _preference_entries_for_status(
                    preferences=current_preferences.values(),
                    status="revoked",
                ),
                "long_mood": dict(current_long_mood) if current_long_mood is not None else None,
            }
        )
    return snapshots


# Block: Overview build
def _build_overview(*, cycle_packets: list[dict[str, Any]]) -> dict[str, Any]:
    cycle_count = len(cycle_packets)
    dialogue_thread_reuse_cycle_count = _count_cycle_checks(
        cycle_packets=cycle_packets,
        check_name="dialogue_thread_reused",
    )
    preference_alignment_cycle_count = _count_cycle_checks(
        cycle_packets=cycle_packets,
        check_name="preference_aligned_action",
    )
    preference_restore_cycle_count = _count_cycle_checks(
        cycle_packets=cycle_packets,
        check_name="preference_restored",
    )
    long_mood_carryover_cycle_count = _count_cycle_checks(
        cycle_packets=cycle_packets,
        check_name="long_mood_carried",
    )
    long_mood_same_label_cycle_count = _count_cycle_checks(
        cycle_packets=cycle_packets,
        check_name="long_mood_same_label",
    )
    assistant_response_cycle_count = sum(
        1
        for cycle_packet in cycle_packets
        if cycle_packet["assistant_text"] is not None
    )
    response_date_recall_cycle_count = _count_cycle_checks(
        cycle_packets=cycle_packets,
        check_name="response_date_recalled",
    )
    response_action_transparency_eligible_cycle_count = sum(
        1
        for cycle_packet in cycle_packets
        if bool(cycle_packet["action_types"]) and cycle_packet["assistant_text"] is not None
    )
    response_action_transparency_cycle_count = _count_cycle_checks(
        cycle_packets=cycle_packets,
        check_name="response_action_transparent",
    )
    response_failure_explanation_eligible_cycle_count = sum(
        1
        for cycle_packet in cycle_packets
        if bool(cycle_packet["failed_action_types"]) and cycle_packet["assistant_text"] is not None
    )
    response_failure_explanation_cycle_count = _count_cycle_checks(
        cycle_packets=cycle_packets,
        check_name="response_failure_explained",
    )
    response_preference_reference_eligible_cycle_count = sum(
        1
        for cycle_packet in cycle_packets
        if bool(cycle_packet["response_behavior_signals"]["reference_candidates"])
        and cycle_packet["assistant_text"] is not None
    )
    response_preference_reference_cycle_count = _count_cycle_checks(
        cycle_packets=cycle_packets,
        check_name="response_preference_referenced",
    )
    response_preference_violation_eligible_cycle_count = sum(
        1
        for cycle_packet in cycle_packets
        if bool(cycle_packet["response_behavior_signals"]["violation_candidates"])
        and cycle_packet["assistant_text"] is not None
    )
    response_preference_violation_cycle_count = _count_cycle_checks(
        cycle_packets=cycle_packets,
        check_name="response_preference_violated",
    )
    response_mood_tone_hint_eligible_cycle_count = sum(
        1
        for cycle_packet in cycle_packets
        if cycle_packet["response_behavior_signals"]["tone_hint_label"] is not None
        and cycle_packet["assistant_text"] is not None
    )
    response_mood_tone_hint_cycle_count = _count_cycle_checks(
        cycle_packets=cycle_packets,
        check_name="response_mood_tone_hinted",
    )
    return {
        "dialogue_thread_reuse_cycle_count": dialogue_thread_reuse_cycle_count,
        "dialogue_thread_reuse_rate_percent": _ratio_percent(
            dialogue_thread_reuse_cycle_count,
            cycle_count,
        ),
        "preference_alignment_cycle_count": preference_alignment_cycle_count,
        "preference_alignment_rate_percent": _ratio_percent(
            preference_alignment_cycle_count,
            cycle_count,
        ),
        "preference_restore_cycle_count": preference_restore_cycle_count,
        "preference_restore_rate_percent": _ratio_percent(
            preference_restore_cycle_count,
            cycle_count,
        ),
        "long_mood_carryover_cycle_count": long_mood_carryover_cycle_count,
        "long_mood_carryover_rate_percent": _ratio_percent(
            long_mood_carryover_cycle_count,
            cycle_count,
        ),
        "long_mood_same_label_cycle_count": long_mood_same_label_cycle_count,
        "long_mood_same_label_rate_percent": _ratio_percent(
            long_mood_same_label_cycle_count,
            cycle_count,
        ),
        "assistant_response_cycle_count": assistant_response_cycle_count,
        "response_date_recall_cycle_count": response_date_recall_cycle_count,
        "response_date_recall_rate_percent": _ratio_percent(
            response_date_recall_cycle_count,
            cycle_count,
        ),
        "response_action_transparency_cycle_count": response_action_transparency_cycle_count,
        "response_action_transparency_eligible_cycle_count": response_action_transparency_eligible_cycle_count,
        "response_action_transparency_rate_percent": _ratio_percent(
            response_action_transparency_cycle_count,
            response_action_transparency_eligible_cycle_count,
        ),
        "response_failure_explanation_cycle_count": response_failure_explanation_cycle_count,
        "response_failure_explanation_eligible_cycle_count": response_failure_explanation_eligible_cycle_count,
        "response_failure_explanation_rate_percent": _ratio_percent(
            response_failure_explanation_cycle_count,
            response_failure_explanation_eligible_cycle_count,
        ),
        "response_preference_reference_cycle_count": response_preference_reference_cycle_count,
        "response_preference_reference_eligible_cycle_count": response_preference_reference_eligible_cycle_count,
        "response_preference_reference_rate_percent": _ratio_percent(
            response_preference_reference_cycle_count,
            response_preference_reference_eligible_cycle_count,
        ),
        "response_preference_violation_cycle_count": response_preference_violation_cycle_count,
        "response_preference_violation_eligible_cycle_count": response_preference_violation_eligible_cycle_count,
        "response_preference_violation_rate_percent": _ratio_percent(
            response_preference_violation_cycle_count,
            response_preference_violation_eligible_cycle_count,
        ),
        "response_mood_tone_hint_cycle_count": response_mood_tone_hint_cycle_count,
        "response_mood_tone_hint_eligible_cycle_count": response_mood_tone_hint_eligible_cycle_count,
        "response_mood_tone_hint_rate_percent": _ratio_percent(
            response_mood_tone_hint_cycle_count,
            response_mood_tone_hint_eligible_cycle_count,
        ),
        "top_long_mood_transition": _top_long_mood_transition(cycle_packets),
    }


# Block: Preference revisions read
def _read_preference_revisions(
    *,
    connection: sqlite3.Connection,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT entity_id, after_json, created_at
        FROM revisions
        WHERE entity_type = 'preference_memory'
        ORDER BY created_at ASC, revision_id ASC
        """
    ).fetchall()
    revisions: list[dict[str, Any]] = []
    for row in rows:
        after_json = json.loads(row["after_json"])
        if not isinstance(after_json, dict):
            raise RuntimeError("preference revision after_json must decode to object")
        revisions.append(
            {
                "entity_id": str(row["entity_id"]),
                "after_json": after_json,
                "created_at": int(row["created_at"]),
            }
        )
    return revisions


# Block: Long mood revisions read
def _read_long_mood_revisions(
    *,
    connection: sqlite3.Connection,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT entity_id, after_json, created_at
        FROM revisions
        WHERE entity_type = 'memory_states'
        ORDER BY created_at ASC, revision_id ASC
        """
    ).fetchall()
    revisions: list[dict[str, Any]] = []
    for row in rows:
        after_json = json.loads(row["after_json"])
        if not isinstance(after_json, dict):
            raise RuntimeError("memory_state revision after_json must decode to object")
        if str(after_json.get("memory_kind")) != "long_mood_state":
            continue
        revisions.append(
            {
                "entity_id": str(row["entity_id"]),
                "after_json": after_json,
                "created_at": int(row["created_at"]),
            }
        )
    return revisions


# Block: Next commit lookup
def _next_commit_after(
    *,
    connection: sqlite3.Connection,
    committed_at: int,
) -> int | None:
    row = connection.execute(
        """
        SELECT MIN(committed_at) AS next_committed_at
        FROM commit_records
        WHERE committed_at > ?
        """,
        (committed_at,),
    ).fetchone()
    if row is None or row["next_committed_at"] is None:
        return None
    return int(row["next_committed_at"])


# Block: Preference keys for status
def _preference_keys_for_status(
    *,
    preferences: Any,
    status: str,
) -> set[str]:
    keys: set[str] = set()
    for preference in preferences:
        if not isinstance(preference, dict):
            raise RuntimeError("preference replay state must contain only objects")
        if str(preference.get("status")) != status:
            continue
        target_entity_ref = _require_object(
            preference.get("target_entity_ref"),
            "preference target_entity_ref must be object",
        )
        keys.add(
            (
                f"{_required_non_empty_string(preference.get('domain'), 'preference domain must be string')}:"
                f"{_required_non_empty_string(target_entity_ref.get('target_key'), 'preference target_key must be string')}:"
                f"{_required_non_empty_string(preference.get('polarity'), 'preference polarity must be string')}"
            )
        )
    return keys


# Block: Preference entries for status
def _preference_entries_for_status(
    *,
    preferences: Any,
    status: str,
) -> list[dict[str, str]]:
    normalized_entries: list[dict[str, str]] = []
    for preference in preferences:
        if not isinstance(preference, dict):
            raise RuntimeError("preference replay state must contain only objects")
        if str(preference.get("status")) != status:
            continue
        target_entity_ref = _require_object(
            preference.get("target_entity_ref"),
            "preference target_entity_ref must be object",
        )
        domain = _required_non_empty_string(
            preference.get("domain"),
            "preference domain must be string",
        )
        target_key = _required_non_empty_string(
            target_entity_ref.get("target_key"),
            "preference target_key must be string",
        )
        polarity = _required_non_empty_string(
            preference.get("polarity"),
            "preference polarity must be string",
        )
        normalized_entries.append(
            {
                "domain": domain,
                "target_key": target_key,
                "polarity": polarity,
                "key": f"{domain}:{target_key}:{polarity}",
            }
        )
    normalized_entries.sort(key=lambda entry: (entry["domain"], entry["target_key"], entry["polarity"]))
    return normalized_entries


# Block: Long mood summary
def _long_mood_summary(long_mood_state: dict[str, Any] | None) -> dict[str, Any] | None:
    if long_mood_state is None:
        return None
    payload = _require_object(
        long_mood_state.get("payload"),
        "long_mood_state.payload must be object",
    )
    return {
        "primary_label": _required_non_empty_string(
            payload.get("primary_label"),
            "long_mood_state.payload.primary_label must be string",
        ),
        "labels": _required_string_list(
            payload.get("labels"),
            "long_mood_state.payload.labels must be string array",
        ),
        "stability": float(payload.get("stability", 0.0)),
    }


# Block: User text extract
def _user_text_for_cycle(event_rows: list[sqlite3.Row]) -> str:
    for event_row in event_rows:
        if str(event_row["kind"]) != "observation":
            continue
        summary_text = _event_summary_text(event_row)
        if summary_text.startswith("chat_message:"):
            return summary_text.removeprefix("chat_message:")
    raise RuntimeError("chat cycle observation event is missing")


# Block: Assistant text extract
def _assistant_text_for_cycle(event_rows: list[sqlite3.Row]) -> str | None:
    for event_row in reversed(event_rows):
        if str(event_row["kind"]) == "external_response":
            return _event_summary_text(event_row)
    return None


# Block: Event summary text
def _event_summary_text(event_row: sqlite3.Row) -> str:
    if event_row["observation_summary"] is not None:
        return str(event_row["observation_summary"])
    if event_row["action_summary"] is not None:
        return str(event_row["action_summary"])
    if event_row["result_summary"] is not None:
        return str(event_row["result_summary"])
    raise RuntimeError("event row must have summary text")


# Block: Cycle check count
def _count_cycle_checks(
    *,
    cycle_packets: list[dict[str, Any]],
    check_name: str,
) -> int:
    return sum(
        1
        for cycle_packet in cycle_packets
        if bool(cycle_packet["checks"][check_name])
    )


# Block: Response date recall
def _response_date_recalled(
    *,
    user_text: str,
    assistant_text: str,
) -> bool:
    user_dates = _iso_date_tokens(user_text)
    assistant_dates = _iso_date_tokens(assistant_text)
    return bool(user_dates and assistant_dates and user_dates.intersection(assistant_dates))


# Block: Response behavior signals
def _build_response_behavior_signals(
    *,
    action_types: list[str],
    failed_action_types: list[str],
    failure_modes: list[str],
    assistant_text: str,
    confirmed_preferences_before: list[dict[str, str]],
    revoked_preferences_before: list[dict[str, str]],
    long_mood_before: dict[str, Any] | None,
) -> dict[str, Any]:
    matched_action_cues = _matched_action_cues(
        action_types=action_types,
        assistant_text=assistant_text,
    )
    explained_failure_modes = _explained_failure_modes(
        failed_action_types=failed_action_types,
        failure_modes=failure_modes,
        assistant_text=assistant_text,
    )
    reference_candidates = [
        entry["key"]
        for entry in confirmed_preferences_before
        if str(entry["polarity"]) == "like"
    ]
    referenced_preference_keys = _matched_preference_keys(
        preferences=confirmed_preferences_before,
        assistant_text=assistant_text,
        action_types=action_types,
        allowed_polarity="like",
    )
    violation_candidates = [
        entry["key"]
        for entry in confirmed_preferences_before
        if str(entry["polarity"]) == "dislike" and str(entry["domain"]) != "action_type"
    ] + [
        entry["key"]
        for entry in revoked_preferences_before
        if str(entry["domain"]) != "action_type"
    ]
    violated_preference_keys = _matched_preference_keys(
        preferences=[
            entry
            for entry in confirmed_preferences_before + revoked_preferences_before
            if str(entry["domain"]) != "action_type"
        ],
        assistant_text=assistant_text,
        action_types=action_types,
        allowed_polarity=None,
    )
    if reference_candidates:
        violated_preference_keys = [
            entry_key for entry_key in violated_preference_keys if entry_key in violation_candidates
        ]
    else:
        violated_preference_keys = [
            entry_key for entry_key in violated_preference_keys if entry_key in violation_candidates
        ]
    tone_hint_label = _tone_hint_label(long_mood_before=long_mood_before)
    return {
        "matched_action_cues": matched_action_cues,
        "explained_failure_modes": explained_failure_modes,
        "reference_candidates": reference_candidates,
        "referenced_preference_keys": referenced_preference_keys,
        "violation_candidates": violation_candidates,
        "violated_preference_keys": violated_preference_keys,
        "tone_hint_label": tone_hint_label,
        "matched_tone_hint": _matched_tone_hint(
            assistant_text=assistant_text,
            tone_hint_label=tone_hint_label,
        ),
    }


# Block: Action cue match
def _matched_action_cues(
    *,
    action_types: list[str],
    assistant_text: str,
) -> list[str]:
    matched: list[str] = []
    for action_type in action_types:
        for cue in ACTION_RESPONSE_CUES.get(action_type, ()):
            if cue in assistant_text:
                matched.append(action_type)
                break
    return _unique_non_empty_strings(matched)


# Block: Failure explanation match
def _explained_failure_modes(
    *,
    failed_action_types: list[str],
    failure_modes: list[str],
    assistant_text: str,
) -> list[str]:
    matched_modes: list[str] = []
    if not failed_action_types:
        return matched_modes
    for failure_mode in failure_modes:
        cues = FAILURE_RESPONSE_CUES.get(failure_mode)
        if cues is not None and _contains_any(assistant_text, cues):
            matched_modes.append(failure_mode)
    if matched_modes:
        return _unique_non_empty_strings(matched_modes)
    for action_type in failed_action_types:
        if _contains_any(assistant_text, ACTION_RESPONSE_CUES.get(action_type, ())):
            matched_modes.append(f"action:{action_type}")
    return _unique_non_empty_strings(matched_modes)


# Block: Preference match
def _matched_preference_keys(
    *,
    preferences: list[dict[str, str]],
    assistant_text: str,
    action_types: list[str],
    allowed_polarity: str | None,
) -> list[str]:
    matched_keys: list[str] = []
    for preference in preferences:
        polarity = str(preference["polarity"])
        if allowed_polarity is not None and polarity != allowed_polarity:
            continue
        if _preference_matches_response(
            domain=str(preference["domain"]),
            target_key=str(preference["target_key"]),
            assistant_text=assistant_text,
            action_types=action_types,
        ):
            matched_keys.append(str(preference["key"]))
    return sorted(_unique_non_empty_strings(matched_keys))


# Block: Preference response match
def _preference_matches_response(
    *,
    domain: str,
    target_key: str,
    assistant_text: str,
    action_types: list[str],
) -> bool:
    if domain == "action_type":
        return target_key in action_types or _contains_any(
            assistant_text,
            ACTION_RESPONSE_CUES.get(target_key, ()),
        )
    if domain == "topic_keyword":
        return _normalized_keyword_text(target_key) in _normalized_keyword_text(assistant_text)
    if domain == "observation_kind":
        if target_key == "date_reference":
            return bool(_iso_date_tokens(assistant_text))
        return _normalized_keyword_text(target_key) in _normalized_keyword_text(assistant_text)
    return False


# Block: Tone hint label
def _tone_hint_label(*, long_mood_before: dict[str, Any] | None) -> str | None:
    if long_mood_before is None:
        return None
    primary_label = str(long_mood_before["primary_label"])
    if primary_label in {"calm", "warm", "curious"}:
        return "gentle"
    if primary_label in {"guarded", "tense", "frustrated"}:
        return "cautious"
    return None


# Block: Tone hint match
def _matched_tone_hint(
    *,
    assistant_text: str,
    tone_hint_label: str | None,
) -> bool:
    if tone_hint_label == "gentle":
        return _contains_any(assistant_text, GENTLE_TONE_CUES)
    if tone_hint_label == "cautious":
        return _contains_any(assistant_text, CAUTIOUS_TONE_CUES)
    return False


# Block: Mood transition summary
def _top_long_mood_transition(cycle_packets: list[dict[str, Any]]) -> str | None:
    transition_counts: dict[str, int] = {}
    for cycle_packet in cycle_packets:
        long_mood_before = cycle_packet["long_mood_before"]
        long_mood_after = cycle_packet["long_mood_after"]
        if long_mood_before is None or long_mood_after is None:
            continue
        transition_key = (
            f"{long_mood_before['primary_label']} -> {long_mood_after['primary_label']}"
        )
        transition_counts[transition_key] = transition_counts.get(transition_key, 0) + 1
    if not transition_counts:
        return None
    return sorted(
        transition_counts.items(),
        key=lambda entry: (-entry[1], entry[0]),
    )[0][0]


# Block: Cue contains helper
def _contains_any(text: str, cues: tuple[str, ...]) -> bool:
    return any(cue in text for cue in cues if cue)


# Block: Keyword normalization
def _normalized_keyword_text(text: str) -> str:
    return "".join(text.strip().lower().split())


# Block: Normalized action type
def _normalized_action_type(action_type: str) -> str | None:
    if action_type in {"browse", "look", "notify", "speak"}:
        return action_type
    return ACTION_TYPE_ALIASES.get(action_type)


# Block: UTC text
def _utc_text(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).isoformat()


# Block: Ratio helper
def _ratio_percent(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round((numerator / denominator) * 100, 1)


# Block: SQLite connection
def _connect_row_db(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


# Block: Validation helpers
def _required_non_empty_string(value: Any, error_text: str) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeError(error_text)
    return value


def _required_string_list(value: Any, error_text: str) -> list[str]:
    if not isinstance(value, list):
        raise RuntimeError(error_text)
    normalized: list[str] = []
    for item in value:
        normalized.append(_required_non_empty_string(item, error_text))
    return normalized


def _require_object(value: Any, error_text: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(error_text)
    return value


def _unique_non_empty_strings(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        stripped_value = value.strip()
        if not stripped_value or stripped_value in normalized:
            continue
        normalized.append(stripped_value)
    return normalized


# Block: ISO date tokens
def _iso_date_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    candidate = ""
    for character in text:
        if character.isdigit() or character == "-":
            candidate += character
            continue
        if _is_iso_date_token(candidate):
            tokens.add(candidate)
        candidate = ""
    if _is_iso_date_token(candidate):
        tokens.add(candidate)
    return tokens


def _is_iso_date_token(candidate: str) -> bool:
    if len(candidate) != 10:
        return False
    if candidate[4] != "-" or candidate[7] != "-":
        return False
    return candidate[:4].isdigit() and candidate[5:7].isdigit() and candidate[8:10].isdigit()
