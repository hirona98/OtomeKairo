"""Minimal runtime loop for consuming pending inputs."""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from otomekairo import __version__
from otomekairo.gateway.cognition_client import CognitionClient
from otomekairo.infra.sqlite_state_store import SqliteStateStore
from otomekairo.schema.runtime_types import ActionHistoryRecord, PendingInputRecord, SettingsOverrideRecord
from otomekairo.schema.settings import SettingsValidationError, build_default_settings, decode_requested_value, get_setting_definition
from otomekairo.usecase.build_cognition_input import build_cognition_input
from otomekairo.usecase.run_cognition import run_cognition_for_chat_message


# Block: Runtime constants
DEFAULT_LEASE_HEARTBEAT_MS = 5_000
MINIMUM_LEASE_TTL_MS = 15_000
DEFAULT_LEASE_TTL_MS = 60_000


# Block: Runtime loop
class RuntimeLoop:
    def __init__(
        self,
        *,
        store: SqliteStateStore,
        owner_token: str,
        default_settings: dict[str, Any],
        cognition_client: CognitionClient,
        lease_heartbeat_ms: int = DEFAULT_LEASE_HEARTBEAT_MS,
        lease_ttl_ms: int = DEFAULT_LEASE_TTL_MS,
    ) -> None:
        # Block: Lease parameter validation
        if lease_heartbeat_ms <= 0:
            raise RuntimeError("lease_heartbeat_ms must be positive")
        if lease_heartbeat_ms > DEFAULT_LEASE_HEARTBEAT_MS:
            raise RuntimeError("lease_heartbeat_ms must be 5000 or less")
        if lease_ttl_ms < MINIMUM_LEASE_TTL_MS:
            raise RuntimeError("lease_ttl_ms must be 15000 or more")
        self._store = store
        self._owner_token = owner_token
        self._default_settings = default_settings
        self._cognition_client = cognition_client
        self._lease_heartbeat_ms = lease_heartbeat_ms
        self._lease_ttl_ms = lease_ttl_ms
        self._boot_reconciled = False

    # Block: Single iteration
    def run_once(self) -> bool:
        self._refresh_runtime_lease()
        if not self._boot_reconciled:
            self._store.materialize_next_boot_settings()
            self._boot_reconciled = True
        processed_settings = self._process_settings_override_once()
        if processed_settings:
            return True
        pending_input = self._store.claim_next_pending_input()
        if pending_input is None:
            processed_memory = self._process_memory_job_once()
            if processed_memory:
                return True
            return False
        cycle_id = _opaque_id("cycle")
        self._store.append_input_journal_for_pending_input(
            pending_input=pending_input,
            cycle_id=cycle_id,
        )
        resolved_at = _now_ms()
        ui_events, action_results, resolution_status, discard_reason = self._resolve_pending_input(
            pending_input=pending_input,
            cycle_id=cycle_id,
            resolved_at=resolved_at,
        )
        commit_payload = {
            "cycle_kind": "short",
            "trigger_reason": "external_input",
            "processed_input_id": pending_input.input_id,
            "processed_input_kind": pending_input.payload["input_kind"],
            "emitted_event_types": [ui_event["event_type"] for ui_event in ui_events],
            "executed_action_types": [action_result.action_type for action_result in action_results],
            "resolution_status": resolution_status,
        }
        self._store.finalize_pending_input_cycle(
            pending_input=pending_input,
            cycle_id=cycle_id,
            resolution_status=resolution_status,
            action_results=action_results,
            discard_reason=discard_reason,
            ui_events=ui_events,
            commit_payload=commit_payload,
        )
        return True

    # Block: Pending input resolution
    def _resolve_pending_input(
        self,
        *,
        pending_input: PendingInputRecord,
        cycle_id: str,
        resolved_at: int,
    ) -> tuple[list[dict[str, Any]], list[ActionHistoryRecord], str, str | None]:
        input_kind = pending_input.payload["input_kind"]
        if input_kind == "chat_message":
            state_snapshot = self._store.read_cognition_state(self._default_settings)
            cognition_input = build_cognition_input(
                pending_input=pending_input,
                cycle_id=cycle_id,
                resolved_at=resolved_at,
                state_snapshot=state_snapshot,
            )
            cognition_execution = run_cognition_for_chat_message(
                pending_input=pending_input,
                cycle_id=cycle_id,
                resolved_at=resolved_at,
                cognition_input=cognition_input,
                cognition_client=self._cognition_client,
                emit_ui_event=lambda ui_event: self._append_ui_event(cycle_id=cycle_id, ui_event=ui_event),
                consume_cancel=lambda message_id: self._consume_matching_cancel(
                    channel=pending_input.channel,
                    message_id=message_id,
                ),
            )
            return (
                cognition_execution.ui_events,
                cognition_execution.action_results,
                "consumed",
                None,
            )
        if input_kind == "cancel":
            return ([], [], "discarded", "cancel_target_not_found")
        ui_events, action_results = _unsupported_input_events(pending_input, resolved_at)
        self._append_ui_events(cycle_id=cycle_id, ui_events=ui_events)
        return (
            ui_events,
            action_results,
            "discarded",
            "unsupported_input_kind",
        )

    # Block: UI event append
    def _append_ui_event(self, *, cycle_id: str, ui_event: dict[str, Any]) -> None:
        self._refresh_runtime_lease()
        self._store.append_ui_outbound_event(
            channel=ui_event["channel"],
            event_type=ui_event["event_type"],
            payload=ui_event["payload"],
            source_cycle_id=cycle_id,
        )

    def _append_ui_events(self, *, cycle_id: str, ui_events: list[dict[str, Any]]) -> None:
        for ui_event in ui_events:
            self._append_ui_event(cycle_id=cycle_id, ui_event=ui_event)

    # Block: Active cancel handling
    def _consume_matching_cancel(self, *, channel: str, message_id: str) -> bool:
        self._refresh_runtime_lease()
        pending_input = self._store.claim_matching_cancel_input(
            channel=channel,
            target_message_id=message_id,
        )
        if pending_input is None:
            return False
        cycle_id = _opaque_id("cycle")
        resolved_at = _now_ms()
        self._store.append_input_journal_for_pending_input(
            pending_input=pending_input,
            cycle_id=cycle_id,
        )
        action_result = ActionHistoryRecord(
            result_id=_opaque_id("actres"),
            command_id=_opaque_id("cmd"),
            action_type="stop_active_message",
            command={
                "target_channel": channel,
                "target_message_id": message_id,
                "event_types": [],
            },
            started_at=resolved_at,
            finished_at=resolved_at,
            status="succeeded",
            failure_mode=None,
            observed_effects={
                "target_message_id": message_id,
                "stop_reason": "cancel_requested",
            },
            raw_result_ref=None,
            adapter_trace_ref=None,
        )
        self._store.finalize_pending_input_cycle(
            pending_input=pending_input,
            cycle_id=cycle_id,
            resolution_status="consumed",
            action_results=[action_result],
            discard_reason=None,
            ui_events=[],
            commit_payload={
                "cycle_kind": "short",
                "trigger_reason": "external_input",
                "processed_input_id": pending_input.input_id,
                "processed_input_kind": "cancel",
                "emitted_event_types": [],
                "executed_action_types": ["stop_active_message"],
                "resolution_status": "consumed",
            },
        )
        return True

    # Block: Settings iteration
    def _process_settings_override_once(self) -> bool:
        settings_override = self._store.claim_next_settings_override()
        if settings_override is None:
            return False
        cycle_id = _opaque_id("cycle")
        self._store.append_input_journal_for_settings_override(
            settings_override=settings_override,
            cycle_id=cycle_id,
        )
        final_status, reject_reason = _evaluate_settings_override(settings_override)
        self._store.finalize_settings_override(
            override_id=settings_override.override_id,
            key=settings_override.key,
            requested_value_json=settings_override.requested_value_json,
            apply_scope=settings_override.apply_scope,
            cycle_id=cycle_id,
            final_status=final_status,
            reject_reason=reject_reason,
        )
        return True

    # Block: Memory job iteration
    def _process_memory_job_once(self) -> bool:
        memory_job = self._store.claim_next_memory_job()
        if memory_job is None:
            return False
        if memory_job.job_kind != "write_memory":
            raise RuntimeError(f"unsupported memory job kind: {memory_job.job_kind}")
        self._store.complete_write_memory_job(memory_job=memory_job)
        return True

    # Block: Infinite loop
    def run_forever(self) -> None:
        try:
            while True:
                processed = self.run_once()
                if not processed:
                    self._sleep_until_next_idle_tick()
        finally:
            self._store.release_runtime_lease(owner_token=self._owner_token)

    # Block: Idle timing
    def _idle_tick_ms(self) -> int:
        effective_settings = self._store.read_effective_settings(self._default_settings)
        idle_tick_ms = effective_settings["runtime.idle_tick_ms"]
        if isinstance(idle_tick_ms, bool) or not isinstance(idle_tick_ms, int):
            raise RuntimeError("runtime.idle_tick_ms must be integer")
        if idle_tick_ms <= 0:
            raise RuntimeError("runtime.idle_tick_ms must be positive")
        return idle_tick_ms

    # Block: Idle wait with heartbeat
    def _sleep_until_next_idle_tick(self) -> None:
        remaining_ms = self._idle_tick_ms()
        while remaining_ms > 0:
            sleep_ms = min(remaining_ms, self._lease_heartbeat_ms)
            time.sleep(sleep_ms / 1000.0)
            remaining_ms -= sleep_ms
            if remaining_ms > 0:
                self._refresh_runtime_lease()

    # Block: Lease refresh
    def _refresh_runtime_lease(self) -> None:
        self._store.acquire_runtime_lease(
            owner_token=self._owner_token,
            lease_ttl_ms=self._lease_ttl_ms,
        )


# Block: Runtime construction
def build_runtime_loop(*, db_path: Path | None = None) -> RuntimeLoop:
    resolved_db_path = db_path or _default_db_path()
    store = SqliteStateStore(
        db_path=resolved_db_path,
        initializer_version=__version__,
    )
    store.initialize()
    return RuntimeLoop(
        store=store,
        owner_token=_runtime_owner_token(),
        default_settings=build_default_settings(),
        cognition_client=_build_default_cognition_client(),
        lease_heartbeat_ms=_lease_heartbeat_ms(),
        lease_ttl_ms=_lease_ttl_ms(),
    )


# Block: Settings evaluation
def _evaluate_settings_override(settings_override: SettingsOverrideRecord) -> tuple[str, str | None]:
    try:
        definition = get_setting_definition(settings_override.key)
    except SettingsValidationError:
        return ("rejected", "unknown_settings_key")
    if settings_override.apply_scope not in definition.apply_scopes:
        return ("rejected", "invalid_settings_scope")
    try:
        decode_requested_value(settings_override.key, settings_override.requested_value_json)
    except SettingsValidationError:
        return ("rejected", "invalid_settings_value")
    return ("applied", None)


# Block: Unsupported input handling
def _unsupported_input_events(
    pending_input: PendingInputRecord,
    resolved_at: int,
) -> tuple[list[dict[str, Any]], list[ActionHistoryRecord]]:
    input_kind = str(pending_input.payload["input_kind"])
    ui_events = [
        {
            "channel": pending_input.channel,
            "event_type": "error",
            "payload": {
                "error_code": "unsupported_input_kind",
                "message": "未対応の入力種別です",
                "retriable": False,
            },
        }
    ]
    action_results = [
        ActionHistoryRecord(
            result_id=_opaque_id("actres"),
            command_id=_opaque_id("cmd"),
            action_type="emit_input_error",
            command={
                "target_channel": pending_input.channel,
                "input_kind": input_kind,
                "event_types": ["error"],
            },
            started_at=resolved_at,
            finished_at=resolved_at + 1,
            status="succeeded",
            failure_mode=None,
            observed_effects={
                "emitted_event_types": ["error"],
                "error_code": "unsupported_input_kind",
            },
            raw_result_ref=None,
            adapter_trace_ref=None,
        )
    ]
    return (ui_events, action_results)

# Block: Runtime helpers
def _default_db_path() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "core.sqlite3"


def _runtime_owner_token() -> str:
    return f"runtime_{uuid.uuid4().hex}"


# Block: Lease timing helpers
def _lease_heartbeat_ms() -> int:
    return DEFAULT_LEASE_HEARTBEAT_MS


def _lease_ttl_ms() -> int:
    return DEFAULT_LEASE_TTL_MS


# Block: Cognition client factory
def _build_default_cognition_client() -> CognitionClient:
    from otomekairo.infra.litellm_cognition_client import LiteLLMCognitionClient

    return LiteLLMCognitionClient()


def _opaque_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _now_ms() -> int:
    return int(time.time() * 1000)
