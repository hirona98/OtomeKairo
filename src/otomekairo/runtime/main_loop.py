"""Minimal runtime loop for consuming pending inputs."""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from otomekairo import __version__
from otomekairo.infra.sqlite_state_store import PendingInputRecord, SettingsOverrideRecord, SqliteStateStore
from otomekairo.schema.settings import SettingsValidationError, build_default_settings, decode_requested_value, get_setting_definition


# Block: Runtime constants
DEFAULT_LEASE_TTL_MS = 5_000


# Block: Runtime loop
class RuntimeLoop:
    def __init__(
        self,
        *,
        store: SqliteStateStore,
        owner_token: str,
        default_settings: dict[str, Any],
        lease_ttl_ms: int = DEFAULT_LEASE_TTL_MS,
    ) -> None:
        self._store = store
        self._owner_token = owner_token
        self._default_settings = default_settings
        self._lease_ttl_ms = lease_ttl_ms
        self._boot_reconciled = False

    # Block: Single iteration
    def run_once(self) -> bool:
        self._store.acquire_runtime_lease(
            owner_token=self._owner_token,
            lease_ttl_ms=self._lease_ttl_ms,
        )
        if not self._boot_reconciled:
            self._store.materialize_next_boot_settings()
            self._boot_reconciled = True
        processed_settings = self._process_settings_override_once()
        if processed_settings:
            return True
        pending_input = self._store.claim_next_pending_input()
        if pending_input is None:
            return False
        cycle_id = _opaque_id("cycle")
        resolved_at = _now_ms()
        ui_events, resolution_status, discard_reason = _build_ui_events(
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
            "resolution_status": resolution_status,
        }
        self._store.finalize_pending_input_cycle(
            input_id=pending_input.input_id,
            cycle_id=cycle_id,
            resolution_status=resolution_status,
            discard_reason=discard_reason,
            ui_events=ui_events,
            commit_payload=commit_payload,
        )
        return True

    # Block: Settings iteration
    def _process_settings_override_once(self) -> bool:
        settings_override = self._store.claim_next_settings_override()
        if settings_override is None:
            return False
        cycle_id = _opaque_id("cycle")
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

    # Block: Infinite loop
    def run_forever(self) -> None:
        try:
            while True:
                processed = self.run_once()
                if not processed:
                    time.sleep(self._idle_tick_seconds())
        finally:
            self._store.release_runtime_lease(owner_token=self._owner_token)

    # Block: Idle timing
    def _idle_tick_seconds(self) -> float:
        effective_settings = self._store.read_effective_settings(self._default_settings)
        idle_tick_ms = effective_settings["runtime.idle_tick_ms"]
        if isinstance(idle_tick_ms, bool) or not isinstance(idle_tick_ms, int):
            raise RuntimeError("runtime.idle_tick_ms must be integer")
        if idle_tick_ms <= 0:
            raise RuntimeError("runtime.idle_tick_ms must be positive")
        return idle_tick_ms / 1000.0


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


# Block: Event building
def _build_ui_events(
    *,
    pending_input: PendingInputRecord,
    cycle_id: str,
    resolved_at: int,
) -> tuple[list[dict[str, Any]], str, str | None]:
    input_kind = pending_input.payload["input_kind"]
    if input_kind == "chat_message":
        return (_chat_message_events(pending_input, cycle_id, resolved_at), "consumed", None)
    if input_kind == "cancel":
        return (_cancel_events(pending_input, cycle_id), "consumed", None)
    return (
        [
            {
                "channel": pending_input.channel,
                "event_type": "error",
                "payload": {
                    "error_code": "unsupported_input_kind",
                    "message": "未対応の入力種別です",
                    "retriable": False,
                },
            }
        ],
        "discarded",
        "unsupported_input_kind",
    )


def _chat_message_events(
    pending_input: PendingInputRecord,
    cycle_id: str,
    resolved_at: int,
) -> list[dict[str, Any]]:
    message_id = _opaque_id("msg")
    return [
        {
            "channel": pending_input.channel,
            "event_type": "status",
            "payload": {
                "status_code": "thinking",
                "label": "入力を処理しています",
                "cycle_id": cycle_id,
            },
        },
        {
            "channel": pending_input.channel,
            "event_type": "message",
            "payload": {
                "message_id": message_id,
                "role": "system_notice",
                "text": "入力を受け付けました。認知処理はこれから実装します。",
                "created_at": resolved_at,
                "source_cycle_id": cycle_id,
                "related_input_id": pending_input.input_id,
            },
        },
        {
            "channel": pending_input.channel,
            "event_type": "status",
            "payload": {
                "status_code": "idle",
                "label": "待機中",
                "cycle_id": cycle_id,
            },
        },
    ]


def _cancel_events(pending_input: PendingInputRecord, cycle_id: str) -> list[dict[str, Any]]:
    return [
        {
            "channel": pending_input.channel,
            "event_type": "notice",
            "payload": {
                "notice_code": "cancel_requested",
                "text": "停止要求を受け付けました",
            },
        },
        {
            "channel": pending_input.channel,
            "event_type": "status",
            "payload": {
                "status_code": "idle",
                "label": "待機中",
                "cycle_id": cycle_id,
            },
        },
    ]

# Block: Runtime helpers
def _default_db_path() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "core.sqlite3"


def _runtime_owner_token() -> str:
    return f"runtime_{uuid.uuid4().hex}"


def _lease_ttl_ms() -> int:
    return DEFAULT_LEASE_TTL_MS


def _opaque_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _now_ms() -> int:
    return int(time.time() * 1000)
