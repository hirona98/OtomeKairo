"""Pending-input and cycle commit port."""

from __future__ import annotations

from typing import Any, Protocol

from otomekairo.schema.runtime_types import (
    ActionHistoryRecord,
    PendingInputMutationRecord,
    PendingInputRecord,
    TaskStateMutationRecord,
    TaskStateRecord,
)


# Block: Cycle commit contract
class CycleCommitStore(Protocol):
    def enqueue_chat_message(
        self,
        *,
        text: str | None,
        client_message_id: str | None,
        attachments: list[dict[str, object]],
    ) -> dict[str, Any]:
        ...

    def enqueue_microphone_message(
        self,
        *,
        transcript_text: str,
        stt_provider: str,
        stt_language: str,
    ) -> dict[str, Any]:
        ...

    def enqueue_camera_observation(
        self,
        *,
        camera_connection_id: str,
        camera_display_name: str,
        capture_id: str,
        image_path: str,
        image_url: str,
        captured_at: int,
    ) -> dict[str, Any]:
        ...

    def enqueue_cancel(
        self,
        *,
        target_message_id: str | None,
    ) -> dict[str, Any]:
        ...

    def claim_next_pending_input(self) -> PendingInputRecord | None:
        ...

    def append_input_journal_for_pending_input(
        self,
        *,
        pending_input: PendingInputRecord,
        cycle_id: str,
    ) -> None:
        ...

    def finalize_pending_input_cycle(
        self,
        *,
        pending_input: PendingInputRecord,
        cycle_id: str,
        resolution_status: str,
        action_results: list[ActionHistoryRecord],
        task_mutations: list[TaskStateMutationRecord],
        pending_input_mutations: list[PendingInputMutationRecord],
        discard_reason: str | None,
        ui_events: list[dict[str, Any]],
        attention_snapshot: dict[str, Any] | None,
        commit_payload: dict[str, Any],
        camera_available: bool,
    ) -> int:
        ...

    def claim_next_waiting_browse_task(self) -> TaskStateRecord | None:
        ...

    def finalize_task_cycle(
        self,
        *,
        task: TaskStateRecord,
        cycle_id: str,
        final_status: str,
        action_results: list[ActionHistoryRecord],
        pending_input_mutations: list[PendingInputMutationRecord],
        ui_events: list[dict[str, Any]],
        commit_payload: dict[str, Any],
        camera_available: bool,
    ) -> int:
        ...

    def enqueue_idle_tick(self, *, idle_duration_ms: int) -> dict[str, Any]:
        ...

    def discard_queued_pending_input(
        self,
        *,
        input_id: str,
        discard_reason: str,
    ) -> None:
        ...

    def claim_matching_cancel_input(
        self,
        *,
        channel: str,
        target_message_id: str,
    ) -> PendingInputRecord | None:
        ...
