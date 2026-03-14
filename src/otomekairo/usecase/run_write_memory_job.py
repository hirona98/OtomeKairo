"""Orchestrate write_memory job execution on top of store read/write helpers."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from otomekairo.gateway.unit_of_work import WriteMemoryExecutionStore
from otomekairo.schema.runtime_types import MemoryJobRecord
from otomekairo.usecase.write_memory_plan import (
    build_write_memory_plan,
    validate_write_memory_payload,
    validate_write_memory_plan,
)


# Block: Execution state
@dataclass(frozen=True, slots=True)
class WriteMemoryJobExecutionState:
    validated_payload: dict[str, Any]
    source_event_ids: list[str]
    cycle_id: str
    event_rows: list[sqlite3.Row]
    event_entries: list[dict[str, Any]]
    action_entries: list[dict[str, Any]]
    browse_fact_entries: list[dict[str, Any]]
    current_emotion: dict[str, Any]
    existing_long_mood_state: dict[str, Any] | None
    existing_preference_entries: list[dict[str, Any]]
    recent_dialogue_context: list[dict[str, Any]]


# Block: Public orchestration
def run_write_memory_job(
    *,
    connection: sqlite3.Connection,
    store: WriteMemoryExecutionStore,
    memory_job: MemoryJobRecord,
    now_ms: int,
) -> str:
    if memory_job.job_kind != "write_memory":
        raise RuntimeError("memory_job.job_kind must be write_memory")
    validated_payload = validate_write_memory_payload(memory_job.payload)
    store.ensure_claimed_memory_job_in_transaction(
        connection=connection,
        job_id=memory_job.job_id,
    )
    execution_state = store.load_write_memory_job_execution_state(
        connection=connection,
        memory_job=memory_job,
        validated_payload=validated_payload,
    )
    memory_write_plan = _build_validated_write_memory_plan(
        execution_state=execution_state,
        source_job_id=memory_job.job_id,
        applied_at=now_ms,
    )
    write_memory_apply_result = store.apply_write_memory_plan_in_transaction(
        connection=connection,
        memory_write_plan=memory_write_plan,
        created_at=now_ms,
    )
    store.enqueue_write_memory_followup_jobs_in_transaction(
        connection=connection,
        cycle_id=execution_state.cycle_id,
        event_rows=execution_state.event_rows,
        source_event_ids=execution_state.source_event_ids,
        embedding_targets=list(write_memory_apply_result["embedding_targets"]),
        created_at=now_ms,
    )
    store.mark_memory_job_completed_in_transaction(
        connection=connection,
        job_id=memory_job.job_id,
        completed_at=now_ms,
    )
    return str(write_memory_apply_result["memory_state_targets"][0]["entity_id"])


# Block: Plan build
def _build_validated_write_memory_plan(
    *,
    execution_state: WriteMemoryJobExecutionState,
    source_job_id: str,
    applied_at: int,
) -> dict[str, Any]:
    return validate_write_memory_plan(
        plan=build_write_memory_plan(
            source_job_id=source_job_id,
            payload=execution_state.validated_payload,
            event_entries=execution_state.event_entries,
            action_entries=execution_state.action_entries,
            browse_fact_entries=execution_state.browse_fact_entries,
            current_emotion=execution_state.current_emotion,
            existing_long_mood_state=execution_state.existing_long_mood_state,
            existing_preference_entries=execution_state.existing_preference_entries,
            recent_dialogue_context=execution_state.recent_dialogue_context,
            applied_at=applied_at,
        ),
        payload=execution_state.validated_payload,
    )
