"""Build replay-oriented evaluation reports for recent chat cycles."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any


# Block: Report constants
REPORT_SCHEMA_VERSION = 3
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
        cycle_packets = _build_cycle_packets(chat_cycles=chat_cycles)
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
            f"({overview['dialogue_thread_reuse_rate_percent']}%)"
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
    chat_cycles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not chat_cycles:
        return []
    seen_dialogue_threads: set[str] = set()
    cycle_packets: list[dict[str, Any]] = []
    for chat_cycle in chat_cycles:
        dialogue_thread_keys = list(chat_cycle["dialogue_thread_keys"])
        reused_dialogue_thread_keys = [
            dialogue_thread_key
            for dialogue_thread_key in dialogue_thread_keys
            if dialogue_thread_key in seen_dialogue_threads
        ]
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
            "response_behavior_signals": response_behavior,
            "checks": {
                "dialogue_thread_reused": bool(reused_dialogue_thread_keys),
                "response_date_recalled": _response_date_recalled(
                    user_text=str(chat_cycle["user_text"]),
                    assistant_text=assistant_text,
                ),
                "response_action_transparent": bool(response_behavior["matched_action_cues"]),
                "response_failure_explained": bool(response_behavior["explained_failure_modes"]),
            },
        }
        cycle_packets.append(cycle_packet)
        seen_dialogue_threads.update(dialogue_thread_keys)
    return cycle_packets


# Block: Overview build
def _build_overview(*, cycle_packets: list[dict[str, Any]]) -> dict[str, Any]:
    cycle_count = len(cycle_packets)
    dialogue_thread_reuse_cycle_count = _count_cycle_checks(
        cycle_packets=cycle_packets,
        check_name="dialogue_thread_reused",
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
    return {
        "dialogue_thread_reuse_cycle_count": dialogue_thread_reuse_cycle_count,
        "dialogue_thread_reuse_rate_percent": _ratio_percent(
            dialogue_thread_reuse_cycle_count,
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
    return {
        "matched_action_cues": matched_action_cues,
        "explained_failure_modes": explained_failure_modes,
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


# Block: Cue contains helper
def _contains_any(text: str, cues: tuple[str, ...]) -> bool:
    return any(cue in text for cue in cues if cue)


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
