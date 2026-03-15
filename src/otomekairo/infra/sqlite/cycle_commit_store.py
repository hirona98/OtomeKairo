"""SQLite-backed pending-input and cycle commit adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from otomekairo.infra.sqlite.backend import SqliteBackend
from otomekairo.infra.sqlite.cycle_commit_impl import (
    append_input_journal_for_pending_input,
    claim_matching_cancel_input,
    claim_next_pending_input,
    claim_next_waiting_browse_task,
    discard_queued_pending_input,
    enqueue_camera_observation,
    enqueue_cancel,
    enqueue_chat_message,
    enqueue_idle_tick,
    enqueue_microphone_message,
    finalize_pending_input_cycle,
    finalize_task_cycle,
)
from otomekairo.schema.runtime_types import (
    ActionHistoryRecord,
    PendingInputMutationRecord,
    PendingInputRecord,
    TaskStateMutationRecord,
    TaskStateRecord,
)


# Block: Cycle commit adapter
@dataclass(frozen=True, slots=True)
class SqliteCycleCommitStore:
    backend: SqliteBackend

    def enqueue_chat_message(
        self,
        *,
        text: str | None,
        client_message_id: str | None,
        attachments: list[dict[str, object]],
    ) -> dict[str, Any]:
        return enqueue_chat_message(
            self.backend,
            text=text,
            client_message_id=client_message_id,
            attachments=attachments,
        )

    def enqueue_microphone_message(
        self,
        *,
        transcript_text: str,
        stt_provider: str,
        stt_language: str,
    ) -> dict[str, Any]:
        return enqueue_microphone_message(
            self.backend,
            transcript_text=transcript_text,
            stt_provider=stt_provider,
            stt_language=stt_language,
        )

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
        return enqueue_camera_observation(
            self.backend,
            camera_connection_id=camera_connection_id,
            camera_display_name=camera_display_name,
            capture_id=capture_id,
            image_path=image_path,
            image_url=image_url,
            captured_at=captured_at,
        )

    def enqueue_cancel(
        self,
        *,
        target_message_id: str | None,
    ) -> dict[str, Any]:
        return enqueue_cancel(self.backend, target_message_id=target_message_id)

    def claim_next_pending_input(self) -> PendingInputRecord | None:
        return claim_next_pending_input(self.backend)

    def append_input_journal_for_pending_input(
        self,
        *,
        pending_input: PendingInputRecord,
        cycle_id: str,
    ) -> None:
        append_input_journal_for_pending_input(
            self.backend,
            pending_input=pending_input,
            cycle_id=cycle_id,
        )

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
        return finalize_pending_input_cycle(
            self.backend,
            pending_input=pending_input,
            cycle_id=cycle_id,
            resolution_status=resolution_status,
            action_results=action_results,
            task_mutations=task_mutations,
            pending_input_mutations=pending_input_mutations,
            discard_reason=discard_reason,
            ui_events=ui_events,
            attention_snapshot=attention_snapshot,
            commit_payload=commit_payload,
            camera_available=camera_available,
        )

    def claim_next_waiting_browse_task(self) -> TaskStateRecord | None:
        return claim_next_waiting_browse_task(self.backend)

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
        return finalize_task_cycle(
            self.backend,
            task=task,
            cycle_id=cycle_id,
            final_status=final_status,
            action_results=action_results,
            pending_input_mutations=pending_input_mutations,
            ui_events=ui_events,
            commit_payload=commit_payload,
            camera_available=camera_available,
        )

    def enqueue_idle_tick(self, *, idle_duration_ms: int) -> dict[str, Any]:
        return enqueue_idle_tick(self.backend, idle_duration_ms=idle_duration_ms)

    def discard_queued_pending_input(
        self,
        *,
        input_id: str,
        discard_reason: str,
    ) -> None:
        discard_queued_pending_input(
            self.backend,
            input_id=input_id,
            discard_reason=discard_reason,
        )

    def claim_matching_cancel_input(
        self,
        *,
        channel: str,
        target_message_id: str,
    ) -> PendingInputRecord | None:
        return claim_matching_cancel_input(
            self.backend,
            channel=channel,
            target_message_id=target_message_id,
        )
