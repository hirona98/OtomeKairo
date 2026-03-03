"""Execute queued browse tasks."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from otomekairo.gateway.search_client import SearchClient, SearchRequest
from otomekairo.schema.runtime_types import ActionHistoryRecord, TaskStateRecord


# Block: Browse task execution
@dataclass(frozen=True, slots=True)
class BrowseTaskExecution:
    ui_events: list[dict[str, Any]]
    action_results: list[ActionHistoryRecord]
    final_status: str


# Block: Browse task runner
def run_browse_task(
    *,
    task: TaskStateRecord,
    cycle_id: str,
    search_client: SearchClient,
    emit_ui_event: Callable[[dict[str, Any]], None],
) -> BrowseTaskExecution:
    if task.task_kind != "browse":
        raise RuntimeError("task.task_kind must be browse")
    if task.task_status != "active":
        raise RuntimeError("task.task_status must be active")
    query = _browse_query(task)
    target_channel = _browse_target_channel(task)
    started_at = _now_ms()
    ui_events: list[dict[str, Any]] = []

    # Block: Immediate event emitter
    def emit_event(event_type: str, payload: dict[str, Any]) -> None:
        ui_event = {
            "channel": target_channel,
            "event_type": event_type,
            "payload": payload,
        }
        ui_events.append(ui_event)
        emit_ui_event(ui_event)

    # Block: External wait status
    emit_event(
        "status",
        {
            "status_code": "waiting_external",
            "label": "外部検索を実行しています",
            "cycle_id": cycle_id,
        },
    )

    # Block: External search
    search_response = search_client.search(
        SearchRequest(
            cycle_id=cycle_id,
            task_id=task.task_id,
            query=query,
        )
    )
    finished_at = _now_ms()

    # Block: Result notice
    emit_event(
        "notice",
        {
            "notice_code": "browse_completed",
            "text": search_response.summary_text,
            "task_id": task.task_id,
        },
    )

    # Block: Idle status
    emit_event(
        "status",
        {
            "status_code": "idle",
            "label": "待機中",
            "cycle_id": cycle_id,
        },
    )

    # Block: Action history
    action_result = ActionHistoryRecord(
        result_id=_opaque_id("actres"),
        command_id=_opaque_id("cmd"),
        action_type="complete_browse_task",
        command={
            "target_channel": target_channel,
            "target": {
                "queue": "task_state",
                "channel": target_channel,
            },
            "event_types": [ui_event["event_type"] for ui_event in ui_events],
            "decision": "execute",
            "decision_reason": "task_resume_execute",
            "related_task_id": task.task_id,
            "command_type": "execute_browse_task",
            "parameters": {
                "query": query,
            },
        },
        started_at=started_at,
        finished_at=finished_at,
        status="succeeded",
        failure_mode=None,
        observed_effects={
            "emitted_event_types": [ui_event["event_type"] for ui_event in ui_events],
            "status_code_after": "idle",
            "related_task_id": task.task_id,
            "task_status_after": "completed",
            "summary_text": search_response.summary_text,
        },
        raw_result_ref=search_response.raw_result_ref,
        adapter_trace_ref=search_response.adapter_trace_ref,
    )
    return BrowseTaskExecution(
        ui_events=ui_events,
        action_results=[action_result],
        final_status="completed",
    )


# Block: Completion hint readers
def _browse_query(task: TaskStateRecord) -> str:
    query = task.completion_hint.get("query")
    if not isinstance(query, str) or not query.strip():
        raise RuntimeError("browse task completion_hint.query must be non-empty string")
    return query.strip()


def _browse_target_channel(task: TaskStateRecord) -> str:
    target_channel = task.completion_hint.get("target_channel")
    if not isinstance(target_channel, str) or not target_channel:
        raise RuntimeError("browse task completion_hint.target_channel must be non-empty string")
    return target_channel


# Block: Id helper
def _opaque_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


# Block: Time helper
def _now_ms() -> int:
    return int(time.time() * 1000)
