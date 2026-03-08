"""Build retrieval plan, candidate traces, and final memory bundle."""

from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any

from otomekairo.usecase.about_time_text import life_stage_label
from otomekairo.usecase.retrieval_collectors import collect_retrieval_candidates
from otomekairo.usecase.retrieval_common import local_text, relative_time_text, utc_text
from otomekairo.usecase.retrieval_plan import build_retrieval_plan
from otomekairo.usecase.retrieval_selector import select_retrieval_candidates


# Block: Retrieval artifacts
@dataclass(frozen=True, slots=True)
class RetrievalArtifacts:
    memory_bundle: dict[str, Any]
    retrieval_plan: dict[str, Any]
    candidates_json: dict[str, Any]
    selected_json: dict[str, Any]


# Block: Public builder
def build_retrieval_artifacts(
    *,
    memory_snapshot: dict[str, Any],
    retrieval_profile: dict[str, Any],
    current_observation: dict[str, Any],
    task_snapshot: dict[str, Any],
    resolved_at: int,
) -> RetrievalArtifacts:
    event_about_time_by_id = _event_about_time_by_id(memory_snapshot)
    state_about_time_by_id = _state_about_time_by_id(memory_snapshot)
    retrieval_plan = build_retrieval_plan(
        retrieval_profile=retrieval_profile,
        current_observation=current_observation,
        task_snapshot=task_snapshot,
    )
    candidate_collection = collect_retrieval_candidates(
        memory_snapshot=memory_snapshot,
        current_observation=current_observation,
        retrieval_plan=retrieval_plan,
    )
    selection_artifacts = select_retrieval_candidates(
        candidates=candidate_collection.candidates,
        retrieval_plan=retrieval_plan,
    )
    memory_bundle = {
        "working_memory_items": [
            _memory_entry_for_cognition(
                memory_entry,
                resolved_at=resolved_at,
                event_about_time_by_id=event_about_time_by_id,
                state_about_time_by_id=state_about_time_by_id,
            )
            for memory_entry in selection_artifacts.memory_bundle["working_memory_items"]
        ],
        "episodic_items": [
            _memory_entry_for_cognition(
                memory_entry,
                resolved_at=resolved_at,
                event_about_time_by_id=event_about_time_by_id,
                state_about_time_by_id=state_about_time_by_id,
            )
            for memory_entry in selection_artifacts.memory_bundle["episodic_items"]
        ],
        "semantic_items": [
            _memory_entry_for_cognition(
                memory_entry,
                resolved_at=resolved_at,
                event_about_time_by_id=event_about_time_by_id,
                state_about_time_by_id=state_about_time_by_id,
            )
            for memory_entry in selection_artifacts.memory_bundle["semantic_items"]
        ],
        "affective_items": [
            _memory_entry_for_cognition(
                memory_entry,
                resolved_at=resolved_at,
                event_about_time_by_id=event_about_time_by_id,
                state_about_time_by_id=state_about_time_by_id,
            )
            for memory_entry in selection_artifacts.memory_bundle["affective_items"]
        ],
        "relationship_items": [
            _memory_entry_for_cognition(
                memory_entry,
                resolved_at=resolved_at,
                event_about_time_by_id=event_about_time_by_id,
                state_about_time_by_id=state_about_time_by_id,
            )
            for memory_entry in selection_artifacts.memory_bundle["relationship_items"]
        ],
        "reflection_items": [
            _memory_entry_for_cognition(
                memory_entry,
                resolved_at=resolved_at,
                event_about_time_by_id=event_about_time_by_id,
                state_about_time_by_id=state_about_time_by_id,
            )
            for memory_entry in selection_artifacts.memory_bundle["reflection_items"]
        ],
        "recent_event_window": [
            _recent_event_for_cognition(
                event_entry,
                resolved_at=resolved_at,
                event_about_time_by_id=event_about_time_by_id,
            )
            for event_entry in selection_artifacts.memory_bundle["recent_event_window"]
        ],
    }
    return RetrievalArtifacts(
        memory_bundle=memory_bundle,
        retrieval_plan=retrieval_plan,
        candidates_json=_build_candidates_json(
            candidates=candidate_collection.candidates,
            collector_runs=candidate_collection.collector_runs,
        ),
        selected_json=selection_artifacts.selected_json,
    )


# Block: Candidate builders
def _build_candidates_json(
    *,
    candidates: list[dict[str, Any]],
    collector_runs: list[dict[str, Any]],
) -> dict[str, Any]:
    category_counts: dict[str, int] = {}
    unique_refs: set[str] = set()
    for candidate in candidates:
        slot_name = str(candidate["slot"])
        category_counts[slot_name] = category_counts.get(slot_name, 0) + 1
        unique_refs.add(str(candidate["item_ref"]))
    return {
        "total_candidate_count": len(candidates),
        "unique_candidate_count": len(unique_refs),
        "category_counts": category_counts,
        "non_empty_categories": [
            category_name
            for category_name, count in category_counts.items()
            if count > 0
        ],
        "collector_runs": collector_runs,
    }


# Block: Cognition formatting
def _memory_entry_for_cognition(
    memory_entry: dict[str, Any],
    *,
    resolved_at: int,
    event_about_time_by_id: dict[str, dict[str, Any]],
    state_about_time_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    updated_at = int(memory_entry["updated_at"])
    created_at = int(memory_entry["created_at"])
    last_confirmed_at = int(memory_entry["last_confirmed_at"])
    projected_entry = {
        **memory_entry,
        "created_at_utc_text": _utc_text(created_at),
        "created_at_local_text": _local_text(created_at),
        "updated_at_utc_text": _utc_text(updated_at),
        "updated_at_local_text": _local_text(updated_at),
        "last_confirmed_at_utc_text": _utc_text(last_confirmed_at),
        "last_confirmed_at_local_text": _local_text(last_confirmed_at),
        "relative_time_text": _relative_time_text(resolved_at, updated_at),
    }
    about_time_hint_text = _state_or_event_about_time_hint_text(
        memory_entry=memory_entry,
        event_about_time_by_id=event_about_time_by_id,
        state_about_time_by_id=state_about_time_by_id,
    )
    if about_time_hint_text is not None:
        projected_entry["about_time_hint_text"] = about_time_hint_text
    return projected_entry


def _recent_event_for_cognition(
    event_entry: dict[str, Any],
    *,
    resolved_at: int,
    event_about_time_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    created_at = int(event_entry["created_at"])
    projected_event = {
        **event_entry,
        "created_at_utc_text": _utc_text(created_at),
        "created_at_local_text": _local_text(created_at),
        "relative_time_text": _relative_time_text(resolved_at, created_at),
    }
    about_time_hint_text = _event_about_time_hint_text(
        event_about_time_by_id.get(str(event_entry["event_id"]))
    )
    if about_time_hint_text is not None:
        projected_event["about_time_hint_text"] = about_time_hint_text
    dialog_role = _recent_dialog_role(event_entry)
    if dialog_role is not None:
        projected_event["dialog_role"] = dialog_role
        projected_event["dialog_text"] = _recent_dialog_text(event_entry)
    return projected_event


# Block: イベント時制索引
def _event_about_time_by_id(memory_snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    indexed_rows: dict[str, dict[str, Any]] = {}
    for event_about_time in memory_snapshot.get("event_about_time", []):
        if not isinstance(event_about_time, dict):
            raise ValueError("memory_snapshot.event_about_time must contain only objects")
        event_id = event_about_time.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("memory_snapshot.event_about_time.event_id must be non-empty string")
        if event_id not in indexed_rows:
            indexed_rows[event_id] = event_about_time
    return indexed_rows


# Block: 状態時制索引
def _state_about_time_by_id(memory_snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    indexed_rows: dict[str, dict[str, Any]] = {}
    for state_about_time in memory_snapshot.get("state_about_time", []):
        if not isinstance(state_about_time, dict):
            raise ValueError("memory_snapshot.state_about_time must contain only objects")
        memory_state_id = state_about_time.get("memory_state_id")
        if not isinstance(memory_state_id, str) or not memory_state_id:
            raise ValueError("memory_snapshot.state_about_time.memory_state_id must be non-empty string")
        if memory_state_id not in indexed_rows:
            indexed_rows[memory_state_id] = state_about_time
    return indexed_rows


# Block: 状態またはイベントの時制ヒント
def _state_or_event_about_time_hint_text(
    *,
    memory_entry: dict[str, Any],
    event_about_time_by_id: dict[str, dict[str, Any]],
    state_about_time_by_id: dict[str, dict[str, Any]],
) -> str | None:
    state_about_time = state_about_time_by_id.get(str(memory_entry["memory_state_id"]))
    if state_about_time is not None:
        return _event_about_time_hint_text(state_about_time)
    related_event_id = _related_event_id(memory_entry)
    if related_event_id is None:
        return None
    return _event_about_time_hint_text(event_about_time_by_id.get(related_event_id))


# Block: 関連 event id 解決
def _related_event_id(memory_entry: dict[str, Any]) -> str | None:
    payload = memory_entry.get("payload")
    if isinstance(payload, dict):
        event_id = payload.get("event_id")
        if isinstance(event_id, str) and event_id:
            return event_id
    if str(memory_entry.get("memory_kind")) == "episodic_event":
        return str(memory_entry["memory_state_id"])
    return None


# Block: 会話イベント判定
def _recent_dialog_role(event_entry: dict[str, Any]) -> str | None:
    kind = str(event_entry["kind"])
    summary_text = str(event_entry["summary_text"])
    if kind == "external_response":
        return "assistant"
    if kind == "observation" and summary_text.startswith(("chat_message:", "microphone_message:")):
        return "user"
    return None


# Block: 会話イベント本文
def _recent_dialog_text(event_entry: dict[str, Any]) -> str:
    kind = str(event_entry["kind"])
    summary_text = str(event_entry["summary_text"]).strip()
    if kind == "external_response":
        return summary_text
    if summary_text.startswith("chat_message:"):
        chat_text = summary_text.removeprefix("chat_message:").strip()
        if chat_text.startswith("camera_images:"):
            image_count = chat_text.removeprefix("camera_images:").strip()
            if image_count.isdigit():
                return f"[画像 {image_count} 枚]"
            return "[画像付き入力]"
        if chat_text:
            return chat_text
        return "[空入力]"
    if summary_text.startswith("microphone_message:"):
        speech_text = summary_text.removeprefix("microphone_message:").strip()
        if speech_text:
            return speech_text
    raise RuntimeError("recent dialog text is only supported for user observation or external_response")


# Block: イベント時制ヒント整形
def _event_about_time_hint_text(event_about_time: dict[str, Any] | None) -> str | None:
    if not isinstance(event_about_time, dict):
        return None
    hint_parts: list[str] = []
    about_year_start = event_about_time.get("about_year_start")
    about_year_end = event_about_time.get("about_year_end")
    if isinstance(about_year_start, int):
        if isinstance(about_year_end, int) and about_year_end != about_year_start:
            hint_parts.append(f"{about_year_start}-{about_year_end}年")
        else:
            hint_parts.append(f"{about_year_start}年")
    else:
        date_range_text = _event_about_time_date_range_text(event_about_time)
        if date_range_text is not None:
            hint_parts.append(date_range_text)
    life_stage = event_about_time.get("life_stage")
    if isinstance(life_stage, str) and life_stage:
        hint_parts.append(life_stage_label(life_stage))
    if not hint_parts:
        return None
    return " / ".join(hint_parts)


# Block: イベント時制日付範囲
def _event_about_time_date_range_text(event_about_time: dict[str, Any]) -> str | None:
    about_start_ts = event_about_time.get("about_start_ts")
    about_end_ts = event_about_time.get("about_end_ts")
    if isinstance(about_start_ts, int):
        start_text = _date_text(about_start_ts)
        if isinstance(about_end_ts, int) and about_end_ts != about_start_ts:
            return f"{start_text}..{_date_text(about_end_ts)}"
        return start_text
    if isinstance(about_end_ts, int):
        return _date_text(about_end_ts)
    return None

# Block: 日付テキスト
def _date_text(unix_ms: int) -> str:
    return datetime.fromtimestamp(unix_ms / 1000, tz=timezone.utc).astimezone().strftime("%Y-%m-%d")


# Block: Time helpers
def _utc_text(unix_ms: int) -> str:
    return utc_text(unix_ms)


def _local_text(unix_ms: int) -> str:
    return local_text(unix_ms)


def _relative_time_text(now_ms: int, past_ms: int) -> str:
    return relative_time_text(now_ms, past_ms)
