from __future__ import annotations

import secrets
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from otomekairo.event_stream import EventStreamRegistry, ServerWebSocket
from otomekairo.llm import LLMClient, LLMError
from otomekairo.memory import MemoryConsolidator
from otomekairo.recall import RecallBuilder
from otomekairo.store import FileStore


# Block: Constants
REQUIRED_ROLE_NAMES = {
    "reply_generation": "generation",
    "decision_generation": "generation",
    "recall_hint_generation": "generation",
    "memory_interpretation": "generation",
    "embedding": "embedding",
}
FUTURE_ACT_NOT_BEFORE_MINUTES = 30
FUTURE_ACT_EXPIRES_HOURS = 24
WAKE_REPLY_COOLDOWN_MINUTES = 30
BACKGROUND_WAKE_POLL_SECONDS = 5.0
BACKGROUND_DESKTOP_WATCH_POLL_SECONDS = 5.0
DESKTOP_WATCH_CAPTURE_TIMEOUT_MS = 5000


# Block: Errors
class ServiceError(Exception):
    def __init__(self, status_code: int, error_code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.message = message


# Block: Service
class OtomeKairoService:
    def __init__(self, root_dir: Path) -> None:
        # Block: Dependencies
        self.store = FileStore(root_dir)
        self.llm = LLMClient()
        self.recall = RecallBuilder(store=self.store, llm=self.llm)
        self.memory = MemoryConsolidator(store=self.store, llm=self.llm)
        self._runtime_state_lock = threading.RLock()
        self._wake_execution_lock = threading.Lock()
        self._desktop_watch_execution_lock = threading.Lock()
        self._future_act_candidates: list[dict[str, Any]] = []
        self._wake_runtime_state: dict[str, Any] = {
            "last_wake_at": None,
            "last_spontaneous_at": None,
            "cooldown_until": None,
            "reply_history_by_dedupe": {},
        }
        self._desktop_watch_runtime_state: dict[str, Any] = {
            "last_watch_at": None,
        }
        self._background_wake_stop_event: threading.Event | None = None
        self._background_wake_thread: threading.Thread | None = None
        self._background_desktop_watch_stop_event: threading.Event | None = None
        self._background_desktop_watch_thread: threading.Thread | None = None
        self._event_stream_registry = EventStreamRegistry()
        self._vision_capture_lock = threading.RLock()
        self._pending_vision_capture_requests: dict[str, dict[str, Any]] = {}
        self._stream_event_lock = threading.Lock()
        self._next_stream_event_value = 1

    def start_background_wake_scheduler(self) -> None:
        # Block: Existing
        with self._runtime_state_lock:
            if self._background_wake_thread is not None and self._background_wake_thread.is_alive():
                return

            stop_event = threading.Event()
            thread = threading.Thread(
                target=self._background_wake_loop,
                args=(stop_event,),
                name="otomekairo-background-wake",
                daemon=True,
            )
            self._background_wake_stop_event = stop_event
            self._background_wake_thread = thread

        # Block: Start
        thread.start()

    def stop_background_wake_scheduler(self) -> None:
        # Block: Snapshot
        with self._runtime_state_lock:
            stop_event = self._background_wake_stop_event
            thread = self._background_wake_thread
            self._background_wake_stop_event = None
            self._background_wake_thread = None

        # Block: Stop
        if stop_event is not None:
            stop_event.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)

    def start_background_desktop_watch(self) -> None:
        # Block: Existing
        with self._runtime_state_lock:
            if self._background_desktop_watch_thread is not None and self._background_desktop_watch_thread.is_alive():
                return

            stop_event = threading.Event()
            thread = threading.Thread(
                target=self._background_desktop_watch_loop,
                args=(stop_event,),
                name="otomekairo-background-desktop-watch",
                daemon=True,
            )
            self._background_desktop_watch_stop_event = stop_event
            self._background_desktop_watch_thread = thread

        # Block: Start
        thread.start()

    def stop_background_desktop_watch(self) -> None:
        # Block: Snapshot
        with self._runtime_state_lock:
            stop_event = self._background_desktop_watch_stop_event
            thread = self._background_desktop_watch_thread
            self._background_desktop_watch_stop_event = None
            self._background_desktop_watch_thread = None

        # Block: Stop
        if stop_event is not None:
            stop_event.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)

    def register_event_stream_connection(self, websocket: ServerWebSocket) -> str:
        # Block: Registry
        return self._event_stream_registry.add_connection(websocket)

    def handle_event_stream_message(self, session_id: str, payload: dict[str, Any]) -> None:
        # Block: Type
        message_type = payload.get("type")
        if message_type != "hello":
            raise ServiceError(400, "invalid_event_stream_message", "Only hello messages are supported.")

        # Block: Fields
        client_id = payload.get("client_id")
        caps = payload.get("caps", [])
        if not isinstance(client_id, str) or not client_id.strip():
            raise ServiceError(400, "invalid_client_id", "hello.client_id must be a non-empty string.")
        if not isinstance(caps, list):
            raise ServiceError(400, "invalid_caps", "hello.caps must be an array.")

        # Block: Caps
        normalized_caps: list[str] = []
        for cap in caps:
            if not isinstance(cap, str) or not cap.strip():
                raise ServiceError(400, "invalid_caps", "hello.caps must contain non-empty strings.")
            normalized_caps.append(cap.strip())

        # Block: Register
        self._event_stream_registry.register_hello(
            session_id,
            client_id=client_id.strip(),
            caps=normalized_caps,
        )

    def unregister_event_stream_connection(self, session_id: str) -> None:
        # Block: Registry
        self._event_stream_registry.remove_connection(session_id)

    def close_event_streams(self) -> None:
        # Block: Registry
        self._event_stream_registry.close_all()

    # Block: Bootstrap
    def probe_bootstrap(self) -> dict[str, Any]:
        state = self.store.read_state()
        return {
            "bootstrap_available": True,
            "https_required": True,
            "bootstrap_state": "ready_for_first_console",
        }

    def read_server_identity(self) -> dict[str, Any]:
        state = self.store.read_state()
        return {
            "server_id": state["server_id"],
            "server_display_name": state["server_display_name"],
            "api_version": state["api_version"],
            "console_access_token_issued": state["console_access_token"] is not None,
        }

    def register_first_console(self) -> dict[str, Any]:
        # Block: LoadState
        state = self.store.read_state()

        # Block: EnsureToken
        if state["console_access_token"] is None:
            state["console_access_token"] = self._new_console_token()
            self.store.write_state(state)

        return {
            "console_access_token": state["console_access_token"],
        }

    def reissue_console_access_token(self, token: str | None) -> dict[str, Any]:
        # Block: Authorization
        state = self._require_token(token)

        # Block: IssueToken
        state["console_access_token"] = self._new_console_token()
        self.store.write_state(state)
        return {
            "console_access_token": state["console_access_token"],
        }

    # Block: ReadApis
    def get_status(self, token: str | None) -> dict[str, Any]:
        # Block: Authorization
        state = self._require_token(token)

        # Block: Response
        return {
            "settings_snapshot": self._build_settings_snapshot(state),
            "runtime_summary": self._build_runtime_summary(state),
        }

    def get_config(self, token: str | None) -> dict[str, Any]:
        # Block: Authorization
        state = self._require_token(token)

        # Block: SelectedResources
        selected_preset = state["model_presets"][state["selected_model_preset_id"]]
        selected_profile_ids = {
            role_name: role_value["model_profile_id"]
            for role_name, role_value in selected_preset["roles"].items()
        }
        selected_profiles = {
            role_name: state["model_profiles"][profile_id]
            for role_name, profile_id in selected_profile_ids.items()
        }

        # Block: Response
        return {
            "settings_snapshot": self._build_settings_snapshot(state),
            "selected_persona": state["personas"][state["selected_persona_id"]],
            "selected_memory_set": state["memory_sets"][state["selected_memory_set_id"]],
            "selected_model_preset": selected_preset,
            "selected_model_profile_ids": selected_profile_ids,
            "selected_model_profiles": selected_profiles,
        }

    def get_editor_state(self, token: str | None) -> dict[str, Any]:
        state = self._require_token(token)
        return self._build_editor_state(state)

    def get_catalog(self, token: str | None) -> dict[str, Any]:
        # Block: Authorization
        state = self._require_token(token)

        # Block: Response
        return {
            "personas": self._catalog_entries(state["personas"], "persona_id"),
            "memory_sets": self._catalog_entries(state["memory_sets"], "memory_set_id"),
            "model_presets": self._catalog_entries(state["model_presets"], "model_preset_id"),
            "model_profiles": self._catalog_entries(state["model_profiles"], "model_profile_id"),
        }

    # Block: ConfigApis
    def patch_current(self, token: str | None, payload: dict) -> dict[str, Any]:
        state = self._require_token(token)
        should_clear_future_act = False

        if "selected_persona_id" in payload:
            persona_id = payload["selected_persona_id"]
            if persona_id not in state["personas"]:
                raise ServiceError(404, "persona_not_found", "The requested persona_id does not exist.")
            should_clear_future_act = should_clear_future_act or persona_id != state["selected_persona_id"]
            state["selected_persona_id"] = persona_id

        if "selected_memory_set_id" in payload:
            memory_set_id = payload["selected_memory_set_id"]
            if memory_set_id not in state["memory_sets"]:
                raise ServiceError(404, "memory_set_not_found", "The requested memory_set_id does not exist.")
            should_clear_future_act = should_clear_future_act or memory_set_id != state["selected_memory_set_id"]
            state["selected_memory_set_id"] = memory_set_id

        if "selected_model_preset_id" in payload:
            model_preset_id = payload["selected_model_preset_id"]
            if model_preset_id not in state["model_presets"]:
                raise ServiceError(404, "model_preset_not_found", "The requested model_preset_id does not exist.")
            self._validate_model_preset_definition(state, model_preset_id, state["model_presets"][model_preset_id])
            should_clear_future_act = should_clear_future_act or model_preset_id != state["selected_model_preset_id"]
            state["selected_model_preset_id"] = model_preset_id

        if "wake_policy" in payload:
            self._validate_wake_policy(payload["wake_policy"])
            state["wake_policy"] = payload["wake_policy"]

        if "memory_enabled" in payload:
            self._validate_memory_enabled(payload["memory_enabled"])
            state["memory_enabled"] = payload["memory_enabled"]

        if "desktop_watch" in payload:
            self._validate_desktop_watch(payload["desktop_watch"])
            state["desktop_watch"] = payload["desktop_watch"]

        self.store.write_state(state)
        if should_clear_future_act:
            self._clear_future_act_candidates()
        return self.get_config(token=state["console_access_token"])

    def select_persona(self, token: str | None, persona_id: str) -> dict[str, Any]:
        return self.patch_current(token, {"selected_persona_id": persona_id})

    def select_memory_set(self, token: str | None, memory_set_id: str) -> dict[str, Any]:
        return self.patch_current(token, {"selected_memory_set_id": memory_set_id})

    def update_wake_policy(self, token: str | None, wake_policy: dict) -> dict[str, Any]:
        return self.patch_current(token, {"wake_policy": wake_policy})

    def select_model_preset(self, token: str | None, model_preset_id: str) -> dict[str, Any]:
        return self.patch_current(token, {"selected_model_preset_id": model_preset_id})

    def get_persona(self, token: str | None, persona_id: str) -> dict[str, Any]:
        state = self._require_token(token)
        persona = state["personas"].get(persona_id)
        if persona is None:
            raise ServiceError(404, "persona_not_found", "The requested persona_id does not exist.")
        return {
            "persona": persona,
        }

    def replace_persona(self, token: str | None, persona_id: str, definition: dict) -> dict[str, Any]:
        state = self._require_token(token)
        self._validate_persona_definition(persona_id, definition)
        state["personas"][persona_id] = definition
        self.store.write_state(state)
        if persona_id == state["selected_persona_id"]:
            self._clear_future_act_candidates()
        return {
            "persona": state["personas"][persona_id],
        }

    def delete_persona(self, token: str | None, persona_id: str) -> dict[str, Any]:
        state = self._require_token(token)
        self._delete_resource(
            entries=state["personas"],
            entry_id=persona_id,
            selected_id=state["selected_persona_id"],
            not_found_code="persona_not_found",
            in_use_code="selected_persona_delete_forbidden",
            deleted_key="deleted_persona_id",
        )
        self.store.write_state(state)
        return {
            "deleted_persona_id": persona_id,
        }

    def get_memory_set(self, token: str | None, memory_set_id: str) -> dict[str, Any]:
        state = self._require_token(token)
        memory_set = state["memory_sets"].get(memory_set_id)
        if memory_set is None:
            raise ServiceError(404, "memory_set_not_found", "The requested memory_set_id does not exist.")
        return {
            "memory_set": memory_set,
        }

    def replace_memory_set(self, token: str | None, memory_set_id: str, definition: dict) -> dict[str, Any]:
        state = self._require_token(token)
        self._validate_memory_set_definition(memory_set_id, definition)
        state["memory_sets"][memory_set_id] = definition
        self.store.write_state(state)
        if memory_set_id == state["selected_memory_set_id"]:
            self._clear_future_act_candidates()
        return {
            "memory_set": state["memory_sets"][memory_set_id],
        }

    def delete_memory_set(self, token: str | None, memory_set_id: str) -> dict[str, Any]:
        state = self._require_token(token)
        self._delete_resource(
            entries=state["memory_sets"],
            entry_id=memory_set_id,
            selected_id=state["selected_memory_set_id"],
            not_found_code="memory_set_not_found",
            in_use_code="selected_memory_set_delete_forbidden",
            deleted_key="deleted_memory_set_id",
        )
        self.store.delete_memory_set_records(memory_set_id)
        self.store.write_state(state)
        return {
            "deleted_memory_set_id": memory_set_id,
        }

    def get_model_preset(self, token: str | None, model_preset_id: str) -> dict[str, Any]:
        state = self._require_token(token)
        model_preset = state["model_presets"].get(model_preset_id)
        if model_preset is None:
            raise ServiceError(404, "model_preset_not_found", "The requested model_preset_id does not exist.")
        return {
            "model_preset": model_preset,
        }

    def replace_model_preset(self, token: str | None, model_preset_id: str, definition: dict) -> dict[str, Any]:
        state = self._require_token(token)
        normalized_definition = self._normalize_model_preset_definition(definition)
        self._validate_model_preset_definition(state, model_preset_id, normalized_definition)
        state["model_presets"][model_preset_id] = normalized_definition
        self.store.write_state(state)
        if model_preset_id == state["selected_model_preset_id"]:
            self._clear_future_act_candidates()
        return {
            "model_preset": state["model_presets"][model_preset_id],
        }

    def delete_model_preset(self, token: str | None, model_preset_id: str) -> dict[str, Any]:
        state = self._require_token(token)
        self._delete_resource(
            entries=state["model_presets"],
            entry_id=model_preset_id,
            selected_id=state["selected_model_preset_id"],
            not_found_code="model_preset_not_found",
            in_use_code="selected_model_preset_delete_forbidden",
            deleted_key="deleted_model_preset_id",
        )
        self.store.write_state(state)
        return {
            "deleted_model_preset_id": model_preset_id,
        }

    def get_model_profile(self, token: str | None, model_profile_id: str) -> dict[str, Any]:
        state = self._require_token(token)
        model_profile = state["model_profiles"].get(model_profile_id)
        if model_profile is None:
            raise ServiceError(404, "model_profile_not_found", "The requested model_profile_id does not exist.")
        return {
            "model_profile": model_profile,
        }

    def replace_model_profile(self, token: str | None, model_profile_id: str, definition: dict) -> dict[str, Any]:
        state = self._require_token(token)
        self._validate_model_profile_definition(model_profile_id, definition)
        candidate_state = {
            **state,
            "model_profiles": {
                **state["model_profiles"],
                model_profile_id: definition,
            },
        }
        for preset_id, preset in state["model_presets"].items():
            self._validate_model_preset_definition(candidate_state, preset_id, preset)
        state["model_profiles"][model_profile_id] = definition
        self.store.write_state(state)
        if self._selected_model_preset_uses_profile(state, model_profile_id):
            self._clear_future_act_candidates()
        return {
            "model_profile": state["model_profiles"][model_profile_id],
        }

    def delete_model_profile(self, token: str | None, model_profile_id: str) -> dict[str, Any]:
        state = self._require_token(token)
        if model_profile_id not in state["model_profiles"]:
            raise ServiceError(404, "model_profile_not_found", "The requested model_profile_id does not exist.")
        for model_preset in state["model_presets"].values():
            for role_value in model_preset.get("roles", {}).values():
                if role_value.get("model_profile_id") == model_profile_id:
                    raise ServiceError(
                        409,
                        "model_profile_in_use",
                        "The requested model_profile_id is still referenced by a model_preset.",
                    )
        if len(state["model_profiles"]) <= 1:
            raise ServiceError(409, "last_resource_delete_forbidden", "At least one model_profile must remain.")
        del state["model_profiles"][model_profile_id]
        self.store.write_state(state)
        return {
            "deleted_model_profile_id": model_profile_id,
        }

    def replace_editor_state(self, token: str | None, definition: dict) -> dict[str, Any]:
        state = self._require_token(token)

        current = definition.get("current")
        personas = self._entries_by_id(definition.get("personas"), "persona_id", "personas")
        memory_sets = self._entries_by_id(definition.get("memory_sets"), "memory_set_id", "memory_sets")
        model_profiles = self._entries_by_id(definition.get("model_profiles"), "model_profile_id", "model_profiles")
        candidate_state = {
            **state,
            "model_profiles": model_profiles,
        }
        raw_model_presets = self._entries_by_id(definition.get("model_presets"), "model_preset_id", "model_presets")

        if not isinstance(current, dict):
            raise ServiceError(400, "invalid_editor_state_current", "current must be an object.")
        if not personas:
            raise ServiceError(400, "missing_personas", "editor-state requires at least one persona.")
        if not memory_sets:
            raise ServiceError(400, "missing_memory_sets", "editor-state requires at least one memory_set.")
        if not model_profiles:
            raise ServiceError(400, "missing_model_profiles", "editor-state requires at least one model_profile.")
        if not raw_model_presets:
            raise ServiceError(400, "missing_model_presets", "editor-state requires at least one model_preset.")

        for persona_id, persona in personas.items():
            self._validate_persona_definition(persona_id, persona)
        for memory_set_id, memory_set in memory_sets.items():
            self._validate_memory_set_definition(memory_set_id, memory_set)
        for model_profile_id, model_profile in model_profiles.items():
            self._validate_model_profile_definition(model_profile_id, model_profile)
        candidate_state["model_profiles"] = model_profiles
        # Block: ModelPresetNormalization
        model_presets = {
            model_preset_id: self._normalize_model_preset_definition(model_preset)
            for model_preset_id, model_preset in raw_model_presets.items()
        }
        for model_preset_id, model_preset in model_presets.items():
            self._validate_model_preset_definition(candidate_state, model_preset_id, model_preset)

        selected_persona_id = current.get("selected_persona_id")
        selected_memory_set_id = current.get("selected_memory_set_id")
        selected_model_preset_id = current.get("selected_model_preset_id")
        if selected_persona_id not in personas:
            raise ServiceError(404, "persona_not_found", "The selected_persona_id does not exist in personas.")
        if selected_memory_set_id not in memory_sets:
            raise ServiceError(404, "memory_set_not_found", "The selected_memory_set_id does not exist in memory_sets.")
        if selected_model_preset_id not in model_presets:
            raise ServiceError(404, "model_preset_not_found", "The selected_model_preset_id does not exist in model_presets.")

        self._validate_wake_policy(current.get("wake_policy"))
        self._validate_memory_enabled(current.get("memory_enabled"))
        self._validate_desktop_watch(current.get("desktop_watch"))

        state["selected_persona_id"] = selected_persona_id
        state["selected_memory_set_id"] = selected_memory_set_id
        state["selected_model_preset_id"] = selected_model_preset_id
        state["wake_policy"] = current["wake_policy"]
        state["memory_enabled"] = current["memory_enabled"]
        state["desktop_watch"] = current["desktop_watch"]
        state["personas"] = personas
        state["memory_sets"] = memory_sets
        state["model_profiles"] = model_profiles
        state["model_presets"] = model_presets
        self.store.write_state(state)
        self._clear_future_act_candidates()
        return self._build_editor_state(state)

    # Block: ObservationApi
    def observe_conversation(self, token: str | None, payload: dict) -> dict[str, Any]:
        # Block: Authorization
        state = self._require_token(token)

        # Block: Validation
        observation_text = payload.get("text")
        client_context = payload.get("client_context", {})
        if not isinstance(observation_text, str):
            raise ServiceError(400, "invalid_text", "The text field must be a string.")
        if not isinstance(client_context, dict):
            raise ServiceError(400, "invalid_client_context", "The client_context field must be an object.")

        # Block: Snapshot
        cycle_id = self._new_cycle_id()
        started_at = self._now_iso()
        recent_turns = self._load_recent_turns(state)
        runtime_summary = self._build_runtime_summary(state)
        settings_snapshot = self._build_settings_snapshot(state)

        try:
            # Block: Pipeline
            pipeline = self._run_observation_pipeline(
                state=state,
                started_at=started_at,
                observation_text=observation_text,
                recent_turns=recent_turns,
            )

            # Block: Success
            return self._complete_observation_success(
                cycle_id=cycle_id,
                started_at=started_at,
                state=state,
                settings_snapshot=settings_snapshot,
                runtime_summary=runtime_summary,
                observation_text=observation_text,
                client_context=client_context,
                recent_turns=recent_turns,
                pipeline=pipeline,
            )
        except (LLMError, KeyError, ValueError) as exc:
            # Block: FailurePersistence
            finished_at = self._now_iso()
            self._persist_cycle_failure(
                cycle_id=cycle_id,
                started_at=started_at,
                finished_at=finished_at,
                state=state,
                settings_snapshot=settings_snapshot,
                runtime_summary=runtime_summary,
                observation_text=observation_text,
                client_context=client_context,
                failure_reason=str(exc),
            )
            return {
                "cycle_id": cycle_id,
                "result_kind": "internal_failure",
                "reply": None,
            }

    def observe_wake(self, token: str | None, payload: dict) -> dict[str, Any]:
        # Block: Authorization
        state = self._require_token(token)

        # Block: Validation
        client_context = payload.get("client_context", {})
        if not isinstance(client_context, dict):
            raise ServiceError(400, "invalid_client_context", "The client_context field must be an object.")

        # Block: Execute
        return self._execute_wake_cycle(
            state=state,
            client_context=client_context,
            trigger_kind="wake",
        )

    def submit_vision_capture_response(self, token: str | None, payload: dict) -> dict[str, Any]:
        # Block: Authorization
        self._require_token(token)

        # Block: Fields
        request_id = payload.get("request_id")
        client_id = payload.get("client_id")
        images = payload.get("images", [])
        client_context = payload.get("client_context")
        error = payload.get("error")

        # Block: Validation
        if not isinstance(request_id, str) or not request_id.strip():
            raise ServiceError(400, "invalid_request_id", "request_id must be a non-empty string.")
        if not isinstance(client_id, str) or not client_id.strip():
            raise ServiceError(400, "invalid_client_id", "client_id must be a non-empty string.")
        if not isinstance(images, list):
            raise ServiceError(400, "invalid_images", "images must be an array.")
        if client_context is not None and not isinstance(client_context, dict):
            raise ServiceError(400, "invalid_client_context", "client_context must be an object.")
        if error is not None and not isinstance(error, str):
            raise ServiceError(400, "invalid_capture_error", "error must be a string or null.")

        # Block: ImageValidation
        normalized_images: list[str] = []
        for image in images:
            if not isinstance(image, str) or not image.strip():
                raise ServiceError(400, "invalid_images", "images must contain non-empty strings.")
            normalized_images.append(image.strip())

        # Block: StoreResponse
        with self._vision_capture_lock:
            pending = self._pending_vision_capture_requests.get(request_id.strip())
            if pending is None:
                return {}
            pending["response"] = {
                "request_id": request_id.strip(),
                "client_id": client_id.strip(),
                "images": normalized_images,
                "client_context": client_context or {},
                "error": error.strip() if isinstance(error, str) and error.strip() else None,
            }
            pending["event"].set()

        # Block: Result
        return {}

    def _execute_wake_cycle(
        self,
        *,
        state: dict[str, Any],
        client_context: dict[str, Any],
        trigger_kind: str,
    ) -> dict[str, Any]:
        # Block: SerializedExecution
        with self._wake_execution_lock:
            # Block: Snapshot
            cycle_id = self._new_cycle_id()
            started_at = self._now_iso()
            recent_turns = self._load_recent_turns(state)
            runtime_summary = self._build_runtime_summary(state)
            settings_snapshot = self._build_settings_snapshot(state)
            observation_text = self._build_wake_observation_text(
                client_context=client_context,
                selected_candidate=None,
            )

            try:
                # Block: Pipeline
                selected_candidate = self._select_due_future_act_candidate(
                    memory_set_id=state["selected_memory_set_id"],
                    current_time=started_at,
                )
                pipeline, observation_text = self._run_wake_pipeline(
                    state=state,
                    started_at=started_at,
                    client_context=client_context,
                    recent_turns=recent_turns,
                    selected_candidate=selected_candidate,
                )

                # Block: Success
                response = self._complete_observation_success(
                    cycle_id=cycle_id,
                    started_at=started_at,
                    state=state,
                    settings_snapshot=settings_snapshot,
                    runtime_summary=runtime_summary,
                    observation_text=observation_text,
                    client_context=client_context,
                    recent_turns=recent_turns,
                    pipeline=pipeline,
                    trigger_kind=trigger_kind,
                    observation_event_kind="wake",
                    observation_event_role="system",
                    consolidate_memory=False,
                )

                # Block: PostReply
                self._record_wake_outcome(
                    current_time=started_at,
                    decision=pipeline["decision"],
                    selected_candidate=selected_candidate,
                )
                return response
            except (LLMError, KeyError, ValueError) as exc:
                # Block: FailurePersistence
                finished_at = self._now_iso()
                self._persist_cycle_failure(
                    cycle_id=cycle_id,
                    started_at=started_at,
                    finished_at=finished_at,
                    state=state,
                    settings_snapshot=settings_snapshot,
                    runtime_summary=runtime_summary,
                    observation_text=observation_text,
                    client_context=client_context,
                    failure_reason=str(exc),
                    trigger_kind=trigger_kind,
                    observation_event_kind="wake",
                    observation_event_role="system",
                )
                return {
                    "cycle_id": cycle_id,
                    "result_kind": "internal_failure",
                    "reply": None,
                }

    def _background_wake_loop(self, stop_event: threading.Event) -> None:
        # Block: Loop
        while not stop_event.is_set():
            try:
                state = self.store.read_state()
                delay_seconds = self._background_wake_delay_seconds(state=state, current_time=self._now_iso())
                if delay_seconds > 0:
                    stop_event.wait(timeout=delay_seconds)
                    continue

                self._execute_wake_cycle(
                    state=state,
                    client_context={"source": "background_wake_scheduler"},
                    trigger_kind="wake",
                )
            except Exception:  # noqa: BLE001
                stop_event.wait(timeout=BACKGROUND_WAKE_POLL_SECONDS)

    def _background_wake_delay_seconds(self, *, state: dict[str, Any], current_time: str) -> float:
        # Block: Disabled
        wake_policy = state.get("wake_policy", {})
        if wake_policy.get("mode") != "interval":
            return BACKGROUND_WAKE_POLL_SECONDS

        # Block: FirstWake
        with self._runtime_state_lock:
            last_wake_at = self._wake_runtime_state.get("last_wake_at")
        if not isinstance(last_wake_at, str) or not last_wake_at:
            return 0.0

        # Block: Remaining
        interval_minutes = int(wake_policy.get("interval_minutes", 1))
        current_dt = self._parse_iso(current_time)
        due_at = self._parse_iso(last_wake_at) + timedelta(minutes=interval_minutes)
        remaining_seconds = (due_at - current_dt).total_seconds()
        if remaining_seconds <= 0:
            return 0.0

        # Block: PollCap
        return min(remaining_seconds, BACKGROUND_WAKE_POLL_SECONDS)

    def _background_desktop_watch_loop(self, stop_event: threading.Event) -> None:
        # Block: Loop
        while not stop_event.is_set():
            try:
                state = self.store.read_state()
                delay_seconds = self._background_desktop_watch_delay_seconds(
                    state=state,
                    current_time=self._now_iso(),
                )
                if delay_seconds > 0:
                    stop_event.wait(timeout=delay_seconds)
                    continue

                self._execute_desktop_watch_cycle(state=state)
            except Exception:  # noqa: BLE001
                stop_event.wait(timeout=BACKGROUND_DESKTOP_WATCH_POLL_SECONDS)

    def _background_desktop_watch_delay_seconds(
        self,
        *,
        state: dict[str, Any],
        current_time: str,
    ) -> float:
        # Block: Config
        desktop_watch = state.get("desktop_watch", {})
        target_client_id = desktop_watch.get("target_client_id")
        if not isinstance(desktop_watch, dict) or not desktop_watch.get("enabled"):
            return BACKGROUND_DESKTOP_WATCH_POLL_SECONDS
        if not isinstance(target_client_id, str) or not target_client_id.strip():
            return BACKGROUND_DESKTOP_WATCH_POLL_SECONDS
        if not self._event_stream_registry.has_capability(target_client_id.strip(), "vision.desktop"):
            return BACKGROUND_DESKTOP_WATCH_POLL_SECONDS

        # Block: FirstWatch
        with self._runtime_state_lock:
            last_watch_at = self._desktop_watch_runtime_state.get("last_watch_at")
        if not isinstance(last_watch_at, str) or not last_watch_at:
            return 0.0

        # Block: Remaining
        interval_seconds = int(desktop_watch.get("interval_seconds", 1))
        current_dt = self._parse_iso(current_time)
        due_at = self._parse_iso(last_watch_at) + timedelta(seconds=interval_seconds)
        remaining_seconds = (due_at - current_dt).total_seconds()
        if remaining_seconds <= 0:
            return 0.0

        # Block: PollCap
        return min(remaining_seconds, BACKGROUND_DESKTOP_WATCH_POLL_SECONDS)

    def _execute_desktop_watch_cycle(self, *, state: dict[str, Any]) -> None:
        # Block: SerializedExecution
        with self._desktop_watch_execution_lock:
            # Block: Target
            desktop_watch = state.get("desktop_watch", {})
            target_client_id = desktop_watch.get("target_client_id")
            if not isinstance(target_client_id, str) or not target_client_id.strip():
                return
            target_client_id = target_client_id.strip()
            if not self._event_stream_registry.has_capability(target_client_id, "vision.desktop"):
                return

            # Block: Timestamp
            started_at = self._now_iso()
            self._set_last_desktop_watch_at(started_at)

            # Block: Capture
            capture_response = self._request_desktop_watch_capture(target_client_id=target_client_id)
            if capture_response is None:
                return
            if not capture_response["images"]:
                return

            # Block: Observation
            selected_candidate = self._select_due_future_act_candidate(
                memory_set_id=state["selected_memory_set_id"],
                current_time=started_at,
            )
            client_context = self._build_desktop_watch_client_context(capture_response)
            observation_text = self._build_desktop_watch_observation_text(
                client_context=client_context,
                selected_candidate=selected_candidate,
            )

            # Block: Snapshot
            cycle_id = self._new_cycle_id()
            recent_turns = self._load_recent_turns(state)
            runtime_summary = self._build_runtime_summary(state)
            settings_snapshot = self._build_settings_snapshot(state)

            try:
                # Block: Pipeline
                pipeline = self._run_observation_pipeline(
                    state=state,
                    started_at=started_at,
                    observation_text=observation_text,
                    recent_turns=recent_turns,
                )

                # Block: Success
                self._complete_observation_success(
                    cycle_id=cycle_id,
                    started_at=started_at,
                    state=state,
                    settings_snapshot=settings_snapshot,
                    runtime_summary=runtime_summary,
                    observation_text=observation_text,
                    client_context=client_context,
                    recent_turns=recent_turns,
                    pipeline=pipeline,
                    trigger_kind="desktop_watch",
                    observation_event_kind="desktop_watch",
                    observation_event_role="system",
                    consolidate_memory=False,
                )
                self._record_wake_outcome(
                    current_time=started_at,
                    decision=pipeline["decision"],
                    selected_candidate=selected_candidate,
                )
                self._emit_desktop_watch_reply_event(
                    capture_response=capture_response,
                    pipeline=pipeline,
                )
            except (LLMError, KeyError, ValueError) as exc:
                # Block: Failure
                self._persist_cycle_failure(
                    cycle_id=cycle_id,
                    started_at=started_at,
                    finished_at=self._now_iso(),
                    state=state,
                    settings_snapshot=settings_snapshot,
                    runtime_summary=runtime_summary,
                    observation_text=observation_text,
                    client_context=client_context,
                    failure_reason=str(exc),
                    trigger_kind="desktop_watch",
                    observation_event_kind="desktop_watch",
                    observation_event_role="system",
                )

    def _run_observation_pipeline(
        self,
        *,
        state: dict[str, Any],
        started_at: str,
        observation_text: str,
        recent_turns: list[dict[str, Any]],
    ) -> dict[str, Any]:
        # Block: ModelSelection
        selected_preset = state["model_presets"][state["selected_model_preset_id"]]
        recall_role = selected_preset["roles"]["recall_hint_generation"]
        decision_role = selected_preset["roles"]["decision_generation"]
        reply_role = selected_preset["roles"]["reply_generation"]
        recall_profile = self._profile_for_role(state, selected_preset, "recall_hint_generation")
        decision_profile = self._profile_for_role(state, selected_preset, "decision_generation")
        reply_profile = self._profile_for_role(state, selected_preset, "reply_generation")
        persona = state["personas"][state["selected_persona_id"]]

        # Block: RecallHint
        recall_hint = self.llm.generate_recall_hint(
            profile=recall_profile,
            role_settings=recall_role,
            observation_text=observation_text,
            recent_turns=recent_turns,
            current_time=started_at,
        )

        # Block: RecallPack
        recall_pack = self.recall.build_recall_pack(
            state=state,
            observation_text=observation_text,
            recall_hint=recall_hint,
        )

        # Block: InternalContext
        time_context = self._build_time_context(current_time=started_at)
        affect_context = self._build_affect_context(
            state=state,
            recall_hint=recall_hint,
        )

        # Block: Decision
        decision = self.llm.generate_decision(
            profile=decision_profile,
            role_settings=decision_role,
            observation_text=observation_text,
            recent_turns=recent_turns,
            time_context=time_context,
            affect_context=affect_context,
            recall_hint=recall_hint,
            recall_pack=recall_pack,
        )

        # Block: Reply
        reply_payload: dict[str, Any] | None = None
        if decision["kind"] == "reply":
            reply_payload = self.llm.generate_reply(
                profile=reply_profile,
                role_settings=reply_role,
                persona=persona,
                observation_text=observation_text,
                recent_turns=recent_turns,
                time_context=time_context,
                affect_context=affect_context,
                recall_hint=recall_hint,
                recall_pack=recall_pack,
                decision=decision,
            )

        # Block: Result
        return {
            "recall_hint": recall_hint,
            "recall_pack": recall_pack,
            "time_context": time_context,
            "affect_context": affect_context,
            "decision": decision,
            "reply_payload": reply_payload,
        }

    def _run_wake_pipeline(
        self,
        *,
        state: dict[str, Any],
        started_at: str,
        client_context: dict[str, Any],
        recent_turns: list[dict[str, Any]],
        selected_candidate: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], str]:
        # Block: ObservationText
        observation_text = self._build_wake_observation_text(
            client_context=client_context,
            selected_candidate=selected_candidate,
        )

        # Block: WakePolicy
        due = self._wake_is_due(state=state, current_time=started_at)
        if due["should_skip"]:
            return self._noop_pipeline(started_at=started_at, reason_summary=due["reason_summary"]), observation_text

        # Block: Cooldown
        cooldown_reason = self._wake_cooldown_reason(current_time=started_at)
        if cooldown_reason is not None:
            self._set_last_wake_at(started_at)
            return self._noop_pipeline(started_at=started_at, reason_summary=cooldown_reason), observation_text

        # Block: Candidate
        if selected_candidate is None:
            self._set_last_wake_at(started_at)
            return (
                self._noop_pipeline(
                    started_at=started_at,
                    reason_summary="起床機会は来たが、再評価すべき future_act 候補はまだ無い。",
                ),
                observation_text,
            )

        # Block: ReplySuppression
        if self._was_recently_replied(
            dedupe_key=selected_candidate["dedupe_key"],
            current_time=started_at,
        ):
            self._set_last_wake_at(started_at)
            return (
                self._noop_pipeline(
                    started_at=started_at,
                    reason_summary="同じ future_act 候補には最近 reply 済みのため、今回は再介入しない。",
                ),
                observation_text,
            )

        # Block: TriggerAccounting
        self._set_last_wake_at(started_at)

        # Block: WakeObservation
        pipeline = self._run_observation_pipeline(
            state=state,
            started_at=started_at,
            observation_text=observation_text,
            recent_turns=recent_turns,
        )
        return pipeline, observation_text

    def _complete_observation_success(
        self,
        *,
        cycle_id: str,
        started_at: str,
        state: dict[str, Any],
        settings_snapshot: dict[str, Any],
        runtime_summary: dict[str, Any],
        observation_text: str,
        client_context: dict[str, Any],
        recent_turns: list[dict[str, Any]],
        pipeline: dict[str, Any],
        trigger_kind: str = "user_message",
        observation_event_kind: str = "observation",
        observation_event_role: str = "user",
        consolidate_memory: bool = True,
    ) -> dict[str, Any]:
        # Block: ResultSelection
        decision = pipeline["decision"]
        reply_payload = pipeline["reply_payload"]
        internal_result_kind = decision["kind"]
        result_kind = self._external_result_kind(internal_result_kind)
        finished_at = self._now_iso()
        future_act_summary = self._apply_future_act_candidate(
            cycle_id=cycle_id,
            memory_set_id=state["selected_memory_set_id"],
            decision=decision,
            occurred_at=finished_at,
        )

        # Block: Persistence
        events = self._persist_cycle_success(
            cycle_id=cycle_id,
            started_at=started_at,
            finished_at=finished_at,
            state=state,
            settings_snapshot=settings_snapshot,
            runtime_summary=runtime_summary,
            observation_text=observation_text,
            client_context=client_context,
            recent_turns=recent_turns,
            recall_hint=pipeline["recall_hint"],
            recall_pack=pipeline["recall_pack"],
            time_context=pipeline["time_context"],
            affect_context=pipeline["affect_context"],
            decision=decision,
            result_kind=result_kind,
            reply_payload=reply_payload,
            future_act_summary=future_act_summary,
            trigger_kind=trigger_kind,
            observation_event_kind=observation_event_kind,
            observation_event_role=observation_event_role,
        )

        # Block: MemoryTrace
        if consolidate_memory:
            self._finalize_memory_trace(
                cycle_id=cycle_id,
                finished_at=finished_at,
                state=state,
                observation_text=observation_text,
                events=events,
                pipeline=pipeline,
            )
        else:
            self._update_cycle_trace_memory_trace(
                cycle_id=cycle_id,
                memory_trace=self._skipped_memory_trace("wake_cycle"),
            )

        # Block: Response
        return {
            "cycle_id": cycle_id,
            "result_kind": result_kind,
            "reply": {"text": reply_payload["reply_text"]} if reply_payload else None,
        }

    def _finalize_memory_trace(
        self,
        *,
        cycle_id: str,
        finished_at: str,
        state: dict[str, Any],
        observation_text: str,
        events: list[dict[str, Any]],
        pipeline: dict[str, Any],
    ) -> None:
        # Block: TurnConsolidation
        try:
            memory_trace = self.memory.consolidate_turn(
                state=state,
                cycle_id=cycle_id,
                finished_at=finished_at,
                observation_text=observation_text,
                recall_hint=pipeline["recall_hint"],
                decision=pipeline["decision"],
                reply_payload=pipeline["reply_payload"],
                events=events,
            )
        except Exception as exc:  # noqa: BLE001
            memory_trace = self._failed_memory_trace(str(exc))
            self.store.append_events(
                events=[
                    self._build_memory_audit_event(
                        cycle_id=cycle_id,
                        memory_set_id=state["selected_memory_set_id"],
                        kind="memory_consolidation_failure",
                        created_at=self._now_iso(),
                        payload={"failure_reason": str(exc)},
                    )
                ]
            )

        # Block: ReflectionAudit
        self._append_reflective_failure_events(
            cycle_id=cycle_id,
            memory_set_id=state["selected_memory_set_id"],
            memory_trace=memory_trace,
        )

        # Block: MemoryTraceUpdate
        self._update_cycle_trace_memory_trace(cycle_id=cycle_id, memory_trace=memory_trace)

    def _failed_memory_trace(self, failure_reason: str) -> dict[str, Any]:
        # Block: Result
        return {
            "turn_consolidation_status": "failed",
            "episode_digest_id": None,
            "memory_action_count": 0,
            "affect_update_count": 0,
            "failure_reason": failure_reason,
            "reflective_consolidation": {
                "started": False,
                "result_status": "not_started",
                "trigger_reasons": [],
                "affected_memory_unit_ids": [],
                "failure_reason": None,
            },
        }

    def _skipped_memory_trace(self, reason: str) -> dict[str, Any]:
        # Block: Result
        return {
            "turn_consolidation_status": "skipped",
            "episode_digest_id": None,
            "memory_action_count": 0,
            "affect_update_count": 0,
            "failure_reason": None,
            "skip_reason": reason,
            "reflective_consolidation": {
                "started": False,
                "result_status": "not_started",
                "trigger_reasons": [],
                "affected_memory_unit_ids": [],
                "failure_reason": None,
            },
        }

    def _append_reflective_failure_events(
        self,
        *,
        cycle_id: str,
        memory_set_id: str,
        memory_trace: dict[str, Any],
    ) -> None:
        # Block: Lookup
        reflective_trace = memory_trace.get("reflective_consolidation", {})
        if reflective_trace.get("result_status") != "failed":
            return

        # Block: Audit
        self.store.append_events(
            events=[
                self._build_memory_audit_event(
                    cycle_id=cycle_id,
                    memory_set_id=memory_set_id,
                    kind="reflective_consolidation_failure",
                    created_at=self._now_iso(),
                    payload={
                        "failure_reason": reflective_trace.get("failure_reason"),
                        "trigger_reasons": reflective_trace.get("trigger_reasons", []),
                    },
                )
            ]
        )

    # Block: InspectionApis
    def list_cycle_summaries(self, token: str | None, limit: int) -> dict[str, Any]:
        # Block: Authorization
        self._require_token(token)

        # Block: List
        return {
            "cycle_summaries": self.store.list_cycle_summaries(limit),
        }

    def get_cycle_trace(self, token: str | None, cycle_id: str) -> dict[str, Any]:
        # Block: Authorization
        self._require_token(token)

        # Block: FindRecord
        trace = self.store.get_cycle_trace(cycle_id)
        if trace is not None:
            return trace

        raise ServiceError(404, "cycle_not_found", "The requested cycle_id does not exist.")

    # Block: Helpers
    def _require_token(self, token: str | None) -> dict:
        # Block: LoadState
        state = self.store.read_state()
        issued = state["console_access_token"]

        # Block: Validation
        if issued is None:
            raise ServiceError(401, "bootstrap_required", "A console_access_token has not been issued yet.")
        if token != issued:
            raise ServiceError(401, "invalid_token", "The console_access_token is missing or invalid.")
        return state

    def _profile_for_role(self, state: dict, model_preset: dict, role_name: str) -> dict:
        # Block: Lookup
        role_value = model_preset["roles"][role_name]
        profile_id = role_value["model_profile_id"]
        return state["model_profiles"][profile_id]

    def _summarize_recall_pack(self, recall_pack: dict[str, Any]) -> dict[str, int]:
        # Block: Summary
        return {
            "self_model": len(recall_pack["self_model"]),
            "user_model": len(recall_pack["user_model"]),
            "relationship_model": len(recall_pack["relationship_model"]),
            "active_topics": len(recall_pack["active_topics"]),
            "active_commitments": len(recall_pack["active_commitments"]),
            "episodic_evidence": len(recall_pack["episodic_evidence"]),
            "event_evidence": len(recall_pack["event_evidence"]),
            "conflicts": len(recall_pack["conflicts"]),
        }

    def _external_result_kind(self, internal_result_kind: str) -> str:
        # Block: Mapping
        if internal_result_kind == "future_act":
            return "noop"
        return internal_result_kind

    def _noop_pipeline(self, *, started_at: str, reason_summary: str) -> dict[str, Any]:
        # Block: Result
        return {
            "recall_hint": self._empty_recall_hint(),
            "recall_pack": self._empty_recall_pack(),
            "time_context": self._build_time_context(current_time=started_at),
            "affect_context": {
                "surface": [],
                "background": [],
            },
            "decision": {
                "kind": "noop",
                "reason_code": "wake_noop",
                "reason_summary": reason_summary,
                "requires_confirmation": False,
                "future_act": None,
            },
            "reply_payload": None,
        }

    def _empty_recall_hint(self) -> dict[str, Any]:
        # Block: Result
        return {
            "primary_intent": "smalltalk",
            "secondary_intents": [],
            "confidence": 0.0,
            "time_reference": "none",
            "focus_scopes": [],
            "mentioned_entities": [],
            "mentioned_topics": [],
        }

    def _empty_recall_pack(self) -> dict[str, Any]:
        # Block: Result
        return {
            "self_model": [],
            "user_model": [],
            "relationship_model": [],
            "active_topics": [],
            "active_commitments": [],
            "episodic_evidence": [],
            "event_evidence": [],
            "conflicts": [],
            "selected_memory_ids": [],
            "selected_episode_digest_ids": [],
            "association_selected_memory_ids": [],
            "association_selected_episode_digest_ids": [],
            "selected_event_ids": [],
            "candidate_count": 0,
        }

    def _future_act_trace_summary(
        self,
        *,
        cycle_id: str,
        decision: dict[str, Any],
    ) -> dict[str, Any] | None:
        # Block: Guard
        if decision.get("kind") != "future_act":
            return None

        future_act = decision.get("future_act")
        if not isinstance(future_act, dict):
            return None

        # Block: Result
        return {
            "source_cycle_id": cycle_id,
            "intent_kind": future_act.get("intent_kind"),
            "intent_summary": future_act.get("intent_summary"),
            "reason_summary": decision.get("reason_summary"),
            "dedupe_key": future_act.get("dedupe_key"),
        }

    def _select_due_future_act_candidate(
        self,
        *,
        memory_set_id: str,
        current_time: str,
    ) -> dict[str, Any] | None:
        # Block: LockedRead
        with self._runtime_state_lock:
            # Block: Prune
            self._prune_future_act_candidates(current_time=current_time)
            current_dt = self._parse_iso(current_time)

            # Block: Eligible
            eligible = []
            for candidate in self._future_act_candidates:
                if candidate.get("memory_set_id") != memory_set_id:
                    continue
                not_before = candidate.get("not_before")
                if isinstance(not_before, str) and not_before and self._parse_iso(not_before) > current_dt:
                    continue
                eligible.append(candidate)

            # Block: Empty
            if not eligible:
                return None

            # Block: Sort
            eligible.sort(
                key=lambda candidate: (
                    candidate.get("updated_at") or candidate.get("created_at") or "",
                    candidate.get("candidate_id") or "",
                )
            )
            return dict(eligible[0])

    def _apply_future_act_candidate(
        self,
        *,
        cycle_id: str,
        memory_set_id: str,
        decision: dict[str, Any],
        occurred_at: str,
    ) -> dict[str, Any] | None:
        # Block: Guard
        base_summary = self._future_act_trace_summary(cycle_id=cycle_id, decision=decision)
        if base_summary is None:
            return None

        # Block: LockedUpsert
        with self._runtime_state_lock:
            # Block: Prune
            self._prune_future_act_candidates(current_time=occurred_at)

            # Block: ExistingLookup
            existing = self._find_future_act_candidate(
                memory_set_id=memory_set_id,
                dedupe_key=base_summary["dedupe_key"],
                current_time=occurred_at,
            )
            not_before = self._future_act_not_before(occurred_at)
            expires_at = self._future_act_expires_at(occurred_at)

            # Block: Upsert
            if existing is None:
                candidate = {
                    "candidate_id": f"future_act_candidate:{uuid.uuid4().hex}",
                    "memory_set_id": memory_set_id,
                    "intent_kind": base_summary["intent_kind"],
                    "intent_summary": base_summary["intent_summary"],
                    "reason_summary": base_summary["reason_summary"],
                    "source_cycle_id": cycle_id,
                    "not_before": not_before,
                    "expires_at": expires_at,
                    "dedupe_key": base_summary["dedupe_key"],
                    "created_at": occurred_at,
                    "updated_at": occurred_at,
                }
                self._future_act_candidates.append(candidate)
                queue_action = "created"
            else:
                candidate = existing
                candidate.update(
                    {
                        "intent_kind": base_summary["intent_kind"],
                        "intent_summary": base_summary["intent_summary"],
                        "reason_summary": base_summary["reason_summary"],
                        "source_cycle_id": cycle_id,
                        "not_before": not_before,
                        "expires_at": expires_at,
                        "updated_at": occurred_at,
                    }
                )
                queue_action = "updated"

            # Block: Result
            return {
                **base_summary,
                "candidate_id": candidate["candidate_id"],
                "queue_action": queue_action,
                "not_before": candidate["not_before"],
                "expires_at": candidate["expires_at"],
            }

    def _record_wake_outcome(
        self,
        *,
        current_time: str,
        decision: dict[str, Any],
        selected_candidate: dict[str, Any] | None,
    ) -> None:
        # Block: Reply
        if decision.get("kind") == "reply":
            with self._runtime_state_lock:
                self._wake_runtime_state["last_spontaneous_at"] = current_time
                self._wake_runtime_state["cooldown_until"] = self._wake_cooldown_until(current_time)
                if selected_candidate is not None:
                    dedupe_key = selected_candidate.get("dedupe_key")
                    if isinstance(dedupe_key, str) and dedupe_key:
                        reply_history = self._wake_runtime_state.setdefault("reply_history_by_dedupe", {})
                        reply_history[dedupe_key] = current_time
                    self._remove_future_act_candidate(selected_candidate.get("candidate_id"))
            return

        # Block: FutureAct
        if decision.get("kind") == "future_act":
            return

    def _set_last_desktop_watch_at(self, current_time: str) -> None:
        # Block: Update
        with self._runtime_state_lock:
            self._desktop_watch_runtime_state["last_watch_at"] = current_time

    def _request_desktop_watch_capture(self, *, target_client_id: str) -> dict[str, Any] | None:
        # Block: Request
        request_id = f"vision_capture_request:{uuid.uuid4().hex}"
        pending = {
            "event": threading.Event(),
            "response": None,
            "target_client_id": target_client_id,
        }
        with self._vision_capture_lock:
            self._pending_vision_capture_requests[request_id] = pending

        # Block: Command
        sent = self._event_stream_registry.send_to_client(
            target_client_id,
            {
                "event_id": 0,
                "type": "vision.capture_request",
                "data": {
                    "request_id": request_id,
                    "source": "desktop",
                    "mode": "still",
                    "purpose": "desktop_watch",
                    "timeout_ms": DESKTOP_WATCH_CAPTURE_TIMEOUT_MS,
                },
            },
        )
        if not sent:
            with self._vision_capture_lock:
                self._pending_vision_capture_requests.pop(request_id, None)
            return None

        # Block: Wait
        pending["event"].wait(timeout=(DESKTOP_WATCH_CAPTURE_TIMEOUT_MS / 1000.0) + 1.0)

        # Block: Result
        with self._vision_capture_lock:
            result = pending["response"]
            self._pending_vision_capture_requests.pop(request_id, None)
            if not isinstance(result, dict):
                return None
            return result

    def _build_desktop_watch_client_context(self, capture_response: dict[str, Any]) -> dict[str, Any]:
        # Block: Source
        client_context = capture_response.get("client_context", {})
        if not isinstance(client_context, dict):
            client_context = {}

        # Block: Result
        return {
            "source": "desktop_watch",
            "client_id": capture_response.get("client_id"),
            "active_app": client_context.get("active_app"),
            "window_title": client_context.get("window_title"),
            "locale": client_context.get("locale"),
            "image_count": len(capture_response.get("images", [])),
        }

    def _build_desktop_watch_observation_text(
        self,
        *,
        client_context: dict[str, Any],
        selected_candidate: dict[str, Any] | None,
    ) -> str:
        # Block: Prefix
        parts = ["desktop_watch 観測。"]

        # Block: Context
        parts.extend(
            self._client_context_observation_parts(
                client_context=client_context,
                include_source=False,
                include_capture=True,
            )
        )

        # Block: Candidate
        if selected_candidate is not None:
            parts.append(self._wake_observation_text(selected_candidate))
            parts.append("いま保留中の会話候補を再評価したい。")

        # Block: Result
        return " ".join(parts)

    def _emit_desktop_watch_reply_event(
        self,
        *,
        capture_response: dict[str, Any],
        pipeline: dict[str, Any],
    ) -> None:
        # Block: Guard
        reply_payload = pipeline.get("reply_payload")
        if not isinstance(reply_payload, dict):
            return

        # Block: Client
        target_client_id = capture_response.get("client_id")
        if not isinstance(target_client_id, str) or not target_client_id.strip():
            return

        # Block: Context
        client_context = capture_response.get("client_context", {})
        if not isinstance(client_context, dict):
            client_context = {}
        window_title = client_context.get("window_title")
        active_app = client_context.get("active_app")
        summary = None
        if isinstance(window_title, str) and window_title.strip():
            summary = window_title.strip()
        elif isinstance(active_app, str) and active_app.strip():
            summary = active_app.strip()

        # Block: Event
        event = {
            "event_id": self._next_stream_event_id(),
            "type": "desktop_watch",
            "data": {
                "system_text": f"[desktop_watch] {summary}" if isinstance(summary, str) and summary else "[desktop_watch]",
                "message": reply_payload["reply_text"],
                "images": capture_response.get("images", []),
            },
        }
        self._event_stream_registry.send_to_client(target_client_id.strip(), event)

    def _next_stream_event_id(self) -> int:
        # Block: Counter
        with self._stream_event_lock:
            event_id = self._next_stream_event_value
            self._next_stream_event_value += 1

        # Block: Result
        return event_id

    def _set_last_wake_at(self, current_time: str) -> None:
        # Block: Update
        with self._runtime_state_lock:
            self._wake_runtime_state["last_wake_at"] = current_time

    def _wake_is_due(self, *, state: dict[str, Any], current_time: str) -> dict[str, Any]:
        # Block: Disabled
        wake_policy = state.get("wake_policy", {})
        if wake_policy.get("mode") != "interval":
            return {
                "should_skip": True,
                "reason_summary": "wake_policy が disabled のため、自発判断は止まっている。",
            }

        # Block: FirstWake
        with self._runtime_state_lock:
            last_wake_at = self._wake_runtime_state.get("last_wake_at")
        if not isinstance(last_wake_at, str) or not last_wake_at:
            return {
                "should_skip": False,
                "reason_summary": None,
            }

        # Block: Interval
        interval_minutes = wake_policy.get("interval_minutes", 0)
        current_dt = self._parse_iso(current_time)
        due_at = self._parse_iso(last_wake_at) + timedelta(minutes=int(interval_minutes))
        if current_dt < due_at:
            return {
                "should_skip": True,
                "reason_summary": "interval wake の次回時刻にまだ達していない。",
            }

        # Block: Due
        return {
            "should_skip": False,
            "reason_summary": None,
        }

    def _wake_cooldown_reason(self, *, current_time: str) -> str | None:
        # Block: Lookup
        with self._runtime_state_lock:
            cooldown_until = self._wake_runtime_state.get("cooldown_until")
        if not isinstance(cooldown_until, str) or not cooldown_until:
            return None

        # Block: Compare
        if self._parse_iso(current_time) < self._parse_iso(cooldown_until):
            return "直近の自発 reply から cooldown 中のため、今回は再介入しない。"

        # Block: Result
        return None

    def _was_recently_replied(self, *, dedupe_key: str, current_time: str) -> bool:
        # Block: Lookup
        with self._runtime_state_lock:
            reply_history = self._wake_runtime_state.setdefault("reply_history_by_dedupe", {})
            last_reply_at = reply_history.get(dedupe_key)
        if not isinstance(last_reply_at, str) or not last_reply_at:
            return False

        # Block: Compare
        current_dt = self._parse_iso(current_time)
        return current_dt - self._parse_iso(last_reply_at) < timedelta(minutes=WAKE_REPLY_COOLDOWN_MINUTES)

    def _wake_cooldown_until(self, current_time: str) -> str:
        # Block: Timestamp
        return (self._parse_iso(current_time) + timedelta(minutes=WAKE_REPLY_COOLDOWN_MINUTES)).isoformat()

    def _wake_observation_text(self, candidate: dict[str, Any]) -> str:
        # Block: Intent
        intent_kind = candidate.get("intent_kind", "conversation_follow_up")
        if intent_kind == "conversation_follow_up":
            return "約束の続きとして会話を再開したい。いま話しかける価値があるかを見たい。"

        # Block: Fallback
        return "定期起床。未完了の保留候補を再評価したい。"

    def _build_wake_observation_text(
        self,
        *,
        client_context: dict[str, Any],
        selected_candidate: dict[str, Any] | None,
    ) -> str:
        # Block: Prefix
        parts = ["定期起床。"]

        # Block: Context
        parts.extend(
            self._client_context_observation_parts(
                client_context=client_context,
                include_source=True,
                include_capture=False,
            )
        )

        # Block: Candidate
        if selected_candidate is not None:
            parts.append(self._wake_observation_text(selected_candidate))
            parts.append("いま保留中の会話候補を再評価したい。")

        # Block: Result
        return " ".join(parts)

    def _client_context_observation_parts(
        self,
        *,
        client_context: dict[str, Any],
        include_source: bool,
        include_capture: bool,
    ) -> list[str]:
        # Block: Fields
        source = self._client_context_text(client_context.get("source"), limit=48)
        active_app = self._client_context_text(client_context.get("active_app"), limit=80)
        window_title = self._client_context_text(client_context.get("window_title"), limit=120)
        locale = self._client_context_text(client_context.get("locale"), limit=32)
        parts: list[str] = []

        # Block: Source
        if include_source and isinstance(source, str):
            if source == "background_wake_scheduler":
                parts.append("観測源は background wake scheduler。")
            else:
                parts.append(f"観測源は {source}。")

        # Block: Foreground
        if isinstance(active_app, str):
            parts.append(f"前景アプリは {active_app}。")
        if isinstance(window_title, str):
            parts.append(f"ウィンドウタイトルは {window_title}。")

        # Block: Locale
        if isinstance(locale, str):
            parts.append(f"UIロケールは {locale}。")

        # Block: Capture
        if include_capture:
            image_count = client_context.get("image_count")
            if isinstance(image_count, int) and image_count > 0:
                parts.append(f"キャプチャ画像を {image_count} 件受け取った。")

        # Block: Result
        return parts

    def _client_context_text(self, value: Any, *, limit: int) -> str | None:
        # Block: Type
        if not isinstance(value, str):
            return None

        # Block: Normalize
        stripped = value.strip()
        if not stripped:
            return None

        # Block: Result
        return self._clamp(stripped, limit=limit)

    def _remove_future_act_candidate(self, candidate_id: Any) -> None:
        # Block: Guard
        if not isinstance(candidate_id, str) or not candidate_id:
            return

        # Block: Filter
        with self._runtime_state_lock:
            self._future_act_candidates = [
                candidate
                for candidate in self._future_act_candidates
                if candidate.get("candidate_id") != candidate_id
            ]

    def _find_future_act_candidate(
        self,
        *,
        memory_set_id: str,
        dedupe_key: str,
        current_time: str,
    ) -> dict[str, Any] | None:
        # Block: LockedScan
        with self._runtime_state_lock:
            # Block: CurrentTime
            current_dt = self._parse_iso(current_time)

            # Block: Scan
            for candidate in self._future_act_candidates:
                if candidate.get("memory_set_id") != memory_set_id:
                    continue
                if candidate.get("dedupe_key") != dedupe_key:
                    continue
                expires_at = candidate.get("expires_at")
                if isinstance(expires_at, str) and expires_at and self._parse_iso(expires_at) <= current_dt:
                    continue
                return candidate

            # Block: Empty
            return None

    def _prune_future_act_candidates(self, *, current_time: str) -> None:
        # Block: LockedFilter
        with self._runtime_state_lock:
            # Block: CurrentTime
            current_dt = self._parse_iso(current_time)

            # Block: Filter
            self._future_act_candidates = [
                candidate
                for candidate in self._future_act_candidates
                if not isinstance(candidate.get("expires_at"), str)
                or self._parse_iso(candidate["expires_at"]) > current_dt
            ]

    def _clear_future_act_candidates(self) -> None:
        # Block: Reset
        with self._runtime_state_lock:
            self._future_act_candidates = []
            self._wake_runtime_state = {
                "last_wake_at": None,
                "last_spontaneous_at": None,
                "cooldown_until": None,
                "reply_history_by_dedupe": {},
            }
            self._desktop_watch_runtime_state = {
                "last_watch_at": None,
            }

    def _future_act_not_before(self, occurred_at: str) -> str:
        # Block: Offset
        return (self._parse_iso(occurred_at) + timedelta(minutes=FUTURE_ACT_NOT_BEFORE_MINUTES)).isoformat()

    def _future_act_expires_at(self, occurred_at: str) -> str:
        # Block: Offset
        return (self._parse_iso(occurred_at) + timedelta(hours=FUTURE_ACT_EXPIRES_HOURS)).isoformat()

    def _selected_model_preset_uses_profile(self, state: dict[str, Any], model_profile_id: str) -> bool:
        # Block: Lookup
        selected_preset = state["model_presets"][state["selected_model_preset_id"]]
        for role_value in selected_preset.get("roles", {}).values():
            if role_value.get("model_profile_id") == model_profile_id:
                return True

        # Block: Empty
        return False

    def _summarize_affect_context(self, affect_context: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        # Block: Surface
        surface_labels = [
            item["affect_label"]
            for item in affect_context.get("surface", [])
            if isinstance(item.get("affect_label"), str)
        ]

        # Block: Background
        background_labels = [
            item["affect_label"]
            for item in affect_context.get("background", [])
            if isinstance(item.get("affect_label"), str)
        ]

        # Block: Result
        return {
            "surface_count": len(affect_context.get("surface", [])),
            "background_count": len(affect_context.get("background", [])),
            "surface_labels": surface_labels,
            "background_labels": background_labels,
        }

    def _recall_adopted_reason_summary(self, recall_pack: dict[str, Any]) -> str:
        # Block: Counts
        memory_count = len(recall_pack["selected_memory_ids"])
        digest_count = len(recall_pack["selected_episode_digest_ids"])
        association_memory_count = len(recall_pack["association_selected_memory_ids"])
        association_digest_count = len(recall_pack["association_selected_episode_digest_ids"])

        # Block: Empty
        if memory_count == 0 and digest_count == 0:
            return "構造レーンで採用候補は選ばれなかった。"

        # Block: AssociationOnly
        if memory_count == association_memory_count and digest_count == association_digest_count:
            return (
                "連想レーンで近傍候補を補助採用した。"
                f" association_memory_units={association_memory_count}, association_episode_digests={association_digest_count}"
            )

        # Block: Mixed
        if association_memory_count > 0 or association_digest_count > 0:
            return (
                "構造レーンを主軸にしつつ、連想レーンの近傍候補を補助採用した。"
                f" memory_units={memory_count}, episode_digests={digest_count},"
                f" association_memory_units={association_memory_count}, association_episode_digests={association_digest_count}"
            )

        # Block: Summary
        return (
            "構造レーンで scope、memory_type、status、commitment_state に基づいて候補を採用した。"
            f" memory_units={memory_count}, episode_digests={digest_count}"
        )

    def _recall_rejected_reason_summary(self, recall_pack: dict[str, Any]) -> str:
        # Block: Empty
        if recall_pack["candidate_count"] == 0:
            return "現時点では構造レーンにも連想レーンにも一致する長期記憶がなかった。"

        # Block: Association
        if recall_pack["association_selected_memory_ids"] or recall_pack["association_selected_episode_digest_ids"]:
            return "vector-only 候補は補助扱いに留め、文字列一致フォールバックは使っていない。"

        # Block: Summary
        return "section 上限と全体上限を優先し、文字列一致フォールバックは使っていない。"

    def _build_time_context(self, *, current_time: str) -> dict[str, Any]:
        # Block: TimestampParse
        current_dt = self._parse_iso(current_time)

        # Block: Result
        return {
            "current_time": current_time,
            "weekday": current_dt.strftime("%A").lower(),
            "part_of_day": self._part_of_day(current_dt.hour),
        }

    def _build_affect_context(
        self,
        *,
        state: dict[str, Any],
        recall_hint: dict[str, Any],
    ) -> dict[str, list[dict[str, Any]]]:
        # Block: Query
        records = self.store.list_affect_states_for_context(
            memory_set_id=state["selected_memory_set_id"],
            scope_filters=self._build_context_scope_filters(recall_hint),
            layers=["surface", "background"],
            limit=6,
        )

        # Block: Selection
        affect_context = {
            "surface": [],
            "background": [],
        }
        for record in records:
            layer = record.get("layer")
            if layer not in affect_context:
                continue
            if len(affect_context[layer]) >= 2:
                continue
            affect_context[layer].append(
                {
                    "target_scope_type": record["target_scope_type"],
                    "target_scope_key": record["target_scope_key"],
                    "affect_label": record["affect_label"],
                    "intensity": record["intensity"],
                    "updated_at": record["updated_at"],
                }
            )

        # Block: Result
        return affect_context

    def _build_context_scope_filters(self, recall_hint: dict[str, Any]) -> list[tuple[str, str]]:
        # Block: Defaults
        filters: list[tuple[str, str]] = [("user", "user")]
        primary_intent = recall_hint["primary_intent"]
        if primary_intent in {"commitment_check", "consult", "meta_relationship"}:
            filters.append(("relationship", "self|user"))

        # Block: FocusScopes
        filters.extend(self._parse_focus_scopes(recall_hint.get("focus_scopes", [])))

        # Block: Dedup
        deduped: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for scope_filter in filters:
            if scope_filter in seen:
                continue
            deduped.append(scope_filter)
            seen.add(scope_filter)

        # Block: Result
        return deduped

    def _parse_focus_scopes(self, scopes: list[Any]) -> list[tuple[str, str]]:
        # Block: Parse
        parsed: list[tuple[str, str]] = []
        for scope in scopes:
            if not isinstance(scope, str):
                continue
            normalized = scope.strip()
            if not normalized:
                continue
            if normalized in {"self", "user"}:
                parsed.append((normalized, normalized))
                continue
            scope_type, separator, scope_key = normalized.partition(":")
            if not separator or not scope_key:
                continue
            if scope_type not in {"relationship", "topic"}:
                continue
            parsed.append((scope_type, scope_key.strip()))

        # Block: Result
        return parsed

    def _part_of_day(self, hour: int) -> str:
        # Block: Range
        if 5 <= hour < 11:
            return "morning"
        if 11 <= hour < 17:
            return "daytime"
        if 17 <= hour < 22:
            return "evening"
        return "night"

    def _build_settings_snapshot(self, state: dict) -> dict:
        # Block: Snapshot
        return {
            "selected_persona_id": state["selected_persona_id"],
            "selected_memory_set_id": state["selected_memory_set_id"],
            "memory_enabled": state["memory_enabled"],
            "desktop_watch": state["desktop_watch"],
            "wake_policy": state["wake_policy"],
            "selected_model_preset_id": state["selected_model_preset_id"],
        }

    def _build_editor_state(self, state: dict) -> dict:
        return {
            "current": self._build_settings_snapshot(state),
            "personas": list(state["personas"].values()),
            "memory_sets": list(state["memory_sets"].values()),
            "model_presets": list(state["model_presets"].values()),
            "model_profiles": list(state["model_profiles"].values()),
        }

    def _build_runtime_summary(self, state: dict) -> dict:
        # Block: Snapshot
        persona = state["personas"][state["selected_persona_id"]]
        memory_set = state["memory_sets"][state["selected_memory_set_id"]]
        model_preset = state["model_presets"][state["selected_model_preset_id"]]
        return {
            "loaded_persona_ref": {
                "persona_id": persona["persona_id"],
                "display_name": persona["display_name"],
            },
            "loaded_memory_set_ref": {
                "memory_set_id": memory_set["memory_set_id"],
                "display_name": memory_set["display_name"],
            },
            "loaded_model_preset_ref": {
                "model_preset_id": model_preset["model_preset_id"],
                "display_name": model_preset["display_name"],
            },
            "connection_state": "ready",
            "wake_scheduler_active": self._background_wake_scheduler_active() and state["wake_policy"]["mode"] == "interval",
            "ongoing_action_exists": False,
        }

    def _background_wake_scheduler_active(self) -> bool:
        # Block: State
        with self._runtime_state_lock:
            return self._background_wake_thread is not None and self._background_wake_thread.is_alive()

    def _catalog_entries(self, entries: dict, id_key: str) -> list[dict]:
        # Block: Transform
        return [
            {
                id_key: value[id_key],
                "display_name": value.get("display_name", value[id_key]),
            }
            for value in entries.values()
        ]

    def _entries_by_id(self, entries: Any, id_key: str, field_name: str) -> dict[str, dict]:
        if not isinstance(entries, list):
            raise ServiceError(400, f"invalid_{field_name}", f"{field_name} must be an array.")

        result: dict[str, dict] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise ServiceError(400, f"invalid_{field_name}_entry", f"Each {field_name} entry must be an object.")
            entry_id = entry.get(id_key)
            if not isinstance(entry_id, str) or not entry_id:
                raise ServiceError(400, f"invalid_{field_name}_id", f"Each {field_name} entry requires {id_key}.")
            if entry_id in result:
                raise ServiceError(400, f"duplicate_{field_name}_id", f"{entry_id} is duplicated in {field_name}.")
            result[entry_id] = entry
        return result

    def _validate_wake_policy(self, wake_policy: dict) -> None:
        # Block: Shape
        if not isinstance(wake_policy, dict):
            raise ServiceError(400, "invalid_wake_policy", "wake_policy must be an object.")

        mode = wake_policy.get("mode")
        if mode not in {"disabled", "interval"}:
            raise ServiceError(400, "invalid_wake_policy_mode", "wake_policy.mode must be disabled or interval.")

        # Block: Interval
        if mode == "interval":
            interval_minutes = wake_policy.get("interval_minutes")
            if not isinstance(interval_minutes, int) or interval_minutes < 1:
                raise ServiceError(400, "invalid_interval_minutes", "interval_minutes must be an integer >= 1.")

    def _validate_memory_enabled(self, memory_enabled: Any) -> None:
        if not isinstance(memory_enabled, bool):
            raise ServiceError(400, "invalid_memory_enabled", "memory_enabled must be a boolean.")

    def _validate_desktop_watch(self, desktop_watch: Any) -> None:
        if not isinstance(desktop_watch, dict):
            raise ServiceError(400, "invalid_desktop_watch", "desktop_watch must be an object.")
        enabled = desktop_watch.get("enabled")
        interval_seconds = desktop_watch.get("interval_seconds")
        target_client_id = desktop_watch.get("target_client_id")
        if not isinstance(enabled, bool):
            raise ServiceError(400, "invalid_desktop_watch_enabled", "desktop_watch.enabled must be a boolean.")
        if not isinstance(interval_seconds, int) or interval_seconds < 1:
            raise ServiceError(
                400,
                "invalid_desktop_watch_interval_seconds",
                "desktop_watch.interval_seconds must be an integer >= 1.",
            )
        if target_client_id is not None and (not isinstance(target_client_id, str) or not target_client_id.strip()):
            raise ServiceError(
                400,
                "invalid_desktop_watch_target_client_id",
                "desktop_watch.target_client_id must be null or a non-empty string.",
            )

    def _validate_persona_definition(self, persona_id: str, definition: dict) -> None:
        if definition.get("persona_id") != persona_id:
            raise ServiceError(400, "persona_id_mismatch", "persona_id must match the path.")
        display_name = definition.get("display_name")
        if not isinstance(display_name, str) or not display_name.strip():
            raise ServiceError(400, "invalid_persona_display_name", "display_name is required.")
        core_persona = definition.get("core_persona")
        expression_style = definition.get("expression_style")
        if not isinstance(core_persona, dict):
            raise ServiceError(400, "invalid_core_persona", "core_persona must be an object.")
        if not isinstance(expression_style, dict):
            raise ServiceError(400, "invalid_expression_style", "expression_style must be an object.")
        tone = expression_style.get("tone")
        if not isinstance(tone, str) or not tone.strip():
            raise ServiceError(400, "invalid_persona_tone", "expression_style.tone is required.")
        for field_name in ("persona_text", "second_person_label", "addon_text"):
            if field_name in definition and definition[field_name] is not None and not isinstance(definition[field_name], str):
                raise ServiceError(400, f"invalid_{field_name}", f"{field_name} must be a string.")

    def _validate_memory_set_definition(self, memory_set_id: str, definition: dict) -> None:
        if definition.get("memory_set_id") != memory_set_id:
            raise ServiceError(400, "memory_set_id_mismatch", "memory_set_id must match the path.")
        display_name = definition.get("display_name")
        if not isinstance(display_name, str) or not display_name.strip():
            raise ServiceError(400, "invalid_memory_set_display_name", "display_name is required.")
        description = definition.get("description")
        if description is not None and not isinstance(description, str):
            raise ServiceError(400, "invalid_memory_set_description", "description must be a string.")

    def _validate_model_preset_definition(self, state: dict, model_preset_id: str, definition: dict) -> None:
        # Block: Shape
        if definition.get("model_preset_id") != model_preset_id:
            raise ServiceError(400, "model_preset_id_mismatch", "model_preset_id must match the path.")
        roles = definition.get("roles")
        if not isinstance(roles, dict):
            raise ServiceError(400, "invalid_model_preset_roles", "roles must be an object.")

        # Block: RequiredRoles
        for role_name, expected_kind in REQUIRED_ROLE_NAMES.items():
            if role_name not in roles:
                raise ServiceError(400, "missing_model_role", f"{role_name} is required.")
            role_definition = roles[role_name]
            if not isinstance(role_definition, dict):
                raise ServiceError(400, "invalid_model_role", f"{role_name} must be an object.")
            profile_id = role_definition.get("model_profile_id")
            if not isinstance(profile_id, str) or not profile_id:
                raise ServiceError(400, "invalid_model_role_profile", f"{role_name} requires model_profile_id.")
            profile = state["model_profiles"].get(profile_id)
            if profile is None:
                raise ServiceError(404, "model_profile_not_found", f"{profile_id} does not exist.")
            if profile["kind"] != expected_kind:
                raise ServiceError(400, "model_profile_kind_mismatch", f"{role_name} requires kind={expected_kind}.")

            # Block: OptionalReasoningEffort
            reasoning_effort = role_definition.get("reasoning_effort")
            if reasoning_effort is not None and not isinstance(reasoning_effort, str):
                raise ServiceError(400, "invalid_reasoning_effort", f"{role_name}.reasoning_effort must be a string.")

    def _normalize_model_preset_definition(self, definition: dict) -> dict:
        # Block: TopLevelClone
        normalized = {
            **definition,
        }
        roles = definition.get("roles")
        if not isinstance(roles, dict):
            return normalized

        # Block: RoleNormalization
        normalized_roles: dict[str, Any] = {}
        for role_name, role_definition in roles.items():
            if not isinstance(role_definition, dict):
                normalized_roles[role_name] = role_definition
                continue

            normalized_role = {
                **role_definition,
            }
            reasoning_effort = normalized_role.get("reasoning_effort")
            if isinstance(reasoning_effort, str):
                trimmed_reasoning_effort = reasoning_effort.strip()
                if trimmed_reasoning_effort:
                    normalized_role["reasoning_effort"] = trimmed_reasoning_effort
                else:
                    normalized_role.pop("reasoning_effort", None)
            elif reasoning_effort is None:
                normalized_role.pop("reasoning_effort", None)

            normalized_roles[role_name] = normalized_role

        # Block: Result
        normalized["roles"] = normalized_roles
        return normalized

    def _validate_model_profile_definition(self, model_profile_id: str, definition: dict) -> None:
        # Block: Shape
        if definition.get("model_profile_id") != model_profile_id:
            raise ServiceError(400, "model_profile_id_mismatch", "model_profile_id must match the path.")

        kind = definition.get("kind")
        model = definition.get("model")
        base_url = definition.get("base_url")
        auth = definition.get("auth")
        if kind not in {"generation", "embedding"}:
            raise ServiceError(400, "invalid_model_profile_kind", "kind must be generation or embedding.")
        if not isinstance(model, str) or not model.strip():
            raise ServiceError(400, "invalid_model", "model is required.")
        if base_url is not None and not isinstance(base_url, str):
            raise ServiceError(400, "invalid_model_base_url", "base_url must be a string.")
        if auth is not None and not isinstance(auth, dict):
            raise ServiceError(400, "invalid_model_auth", "auth must be an object.")

    def _delete_resource(
        self,
        *,
        entries: dict[str, dict],
        entry_id: str,
        selected_id: str,
        not_found_code: str,
        in_use_code: str,
        deleted_key: str,
    ) -> dict[str, Any]:
        if entry_id not in entries:
            raise ServiceError(404, not_found_code, f"The requested {deleted_key} does not exist.")
        if entry_id == selected_id:
            raise ServiceError(409, in_use_code, f"{entry_id} is currently selected and cannot be deleted.")
        if len(entries) <= 1:
            raise ServiceError(409, "last_resource_delete_forbidden", "At least one resource must remain.")
        del entries[entry_id]
        return {
            deleted_key: entry_id,
        }

    def _persist_cycle_success(
        self,
        *,
        cycle_id: str,
        started_at: str,
        finished_at: str,
        state: dict,
        settings_snapshot: dict,
        runtime_summary: dict,
        observation_text: str,
        client_context: dict,
        recent_turns: list[dict],
        recall_hint: dict,
        recall_pack: dict,
        time_context: dict,
        affect_context: dict[str, list[dict[str, Any]]],
        decision: dict,
        result_kind: str,
        reply_payload: dict | None,
        future_act_summary: dict[str, Any] | None,
        trigger_kind: str,
        observation_event_kind: str,
        observation_event_role: str,
    ) -> list[dict[str, Any]]:
        # Block: EventRecords
        selected_memory_set_id = state["selected_memory_set_id"]
        events = [
            {
                "event_id": f"event:{uuid.uuid4().hex}",
                "cycle_id": cycle_id,
                "memory_set_id": selected_memory_set_id,
                "kind": observation_event_kind,
                "role": observation_event_role,
                "text": observation_text,
                "created_at": started_at,
            },
            {
                "event_id": f"event:{uuid.uuid4().hex}",
                "cycle_id": cycle_id,
                "memory_set_id": selected_memory_set_id,
                "kind": "decision",
                "role": "system",
                "result_kind": decision["kind"],
                "external_result_kind": result_kind,
                "reason_code": decision["reason_code"],
                "reason_summary": decision["reason_summary"],
                "future_act_summary": future_act_summary,
                "created_at": finished_at,
            },
        ]
        if reply_payload is not None:
            events.append(
                {
                    "event_id": f"event:{uuid.uuid4().hex}",
                    "cycle_id": cycle_id,
                    "memory_set_id": selected_memory_set_id,
                    "kind": "reply",
                    "role": "assistant",
                    "text": reply_payload["reply_text"],
                    "created_at": finished_at,
                }
            )

        # Block: RetrievalRun
        retrieval_run = {
            "cycle_id": cycle_id,
            "selected_memory_set_id": selected_memory_set_id,
            "started_at": started_at,
            "finished_at": finished_at,
            "result_status": "succeeded",
            "recall_hint": recall_hint,
            "selected_episode_digest_ids": recall_pack["selected_episode_digest_ids"],
            "selected_event_ids": recall_pack["selected_event_ids"],
            "recall_pack_summary": self._summarize_recall_pack(recall_pack),
            "candidate_count": recall_pack["candidate_count"],
            "selected_memory_ids": recall_pack["selected_memory_ids"],
        }

        # Block: CycleSummary
        cycle_summary = {
            "cycle_id": cycle_id,
            "server_id": state["server_id"],
            "trigger_kind": trigger_kind,
            "started_at": started_at,
            "finished_at": finished_at,
            "selected_persona_id": state["selected_persona_id"],
            "selected_memory_set_id": state["selected_memory_set_id"],
            "selected_model_preset_id": state["selected_model_preset_id"],
            "result_kind": result_kind,
            "failed": False,
        }

        # Block: CycleTrace
        cycle_trace = {
            "cycle_id": cycle_id,
            "cycle_summary": cycle_summary,
            "observation_trace": {
                "trigger_kind": trigger_kind,
                "user_input_summary": self._clamp(observation_text),
                "client_context_summary": self._clamp(str(client_context)),
                "normalized_observation_summary": self._clamp(observation_text.strip()),
                "runtime_state_summary": runtime_summary,
            },
            "recall_trace": {
                "recall_hint_summary": recall_hint,
                "candidate_count": recall_pack["candidate_count"],
                "selected_memory_ids": recall_pack["selected_memory_ids"],
                "selected_episode_digest_ids": recall_pack["selected_episode_digest_ids"],
                "selected_event_ids": recall_pack["selected_event_ids"],
                "recall_pack_summary": self._summarize_recall_pack(recall_pack),
                "adopted_reason_summary": self._recall_adopted_reason_summary(recall_pack),
                "rejected_candidate_summary": self._recall_rejected_reason_summary(recall_pack),
            },
            "decision_trace": {
                "result_kind": decision["kind"],
                "reason_summary": decision["reason_summary"],
                "persona_summary": state["personas"][state["selected_persona_id"]]["display_name"],
                "memory_summary": state["memory_sets"][state["selected_memory_set_id"]]["display_name"],
                "current_context_summary": self._clamp(observation_text),
                "internal_context_summary": {
                    "time_context": time_context,
                    "affect_context_summary": self._summarize_affect_context(affect_context),
                    "recall_pack_summary": self._summarize_recall_pack(recall_pack),
                },
                "primary_candidate_kind": decision["kind"],
                "future_act_candidate_summary": future_act_summary,
            },
            "result_trace": {
                "result_kind": result_kind,
                "reply_summary": self._clamp(reply_payload["reply_text"]) if reply_payload else None,
                "noop_reason_summary": decision["reason_summary"] if decision["kind"] == "noop" else None,
                "future_act_summary": future_act_summary,
                "internal_failure_summary": None,
                "duration_ms": self._duration_ms(started_at, finished_at),
            },
            "memory_trace": {
                "turn_consolidation_status": "pending",
                "episode_digest_id": None,
                "memory_action_count": 0,
                "affect_update_count": 0,
                "failure_reason": None,
                "reflective_consolidation": None,
            },
        }

        # Block: Persist
        self.store.persist_cycle_records(
            events=events,
            retrieval_run=retrieval_run,
            cycle_summary=cycle_summary,
            cycle_trace=cycle_trace,
        )
        return events

    def _persist_cycle_failure(
        self,
        *,
        cycle_id: str,
        started_at: str,
        finished_at: str,
        state: dict,
        settings_snapshot: dict,
        runtime_summary: dict,
        observation_text: str,
        client_context: dict,
        failure_reason: str,
        trigger_kind: str = "user_message",
        observation_event_kind: str = "observation",
        observation_event_role: str = "user",
    ) -> None:
        # Block: EventRecords
        selected_memory_set_id = state["selected_memory_set_id"]
        events = [
            {
                "event_id": f"event:{uuid.uuid4().hex}",
                "cycle_id": cycle_id,
                "memory_set_id": selected_memory_set_id,
                "kind": observation_event_kind,
                "role": observation_event_role,
                "text": observation_text,
                "created_at": started_at,
            },
            {
                "event_id": f"event:{uuid.uuid4().hex}",
                "cycle_id": cycle_id,
                "memory_set_id": selected_memory_set_id,
                "kind": "recall_hint_failure",
                "role": "system",
                "failure_reason": failure_reason,
                "created_at": finished_at,
            },
        ]

        # Block: RetrievalRun
        retrieval_run = {
            "cycle_id": cycle_id,
            "selected_memory_set_id": selected_memory_set_id,
            "started_at": started_at,
            "finished_at": finished_at,
            "result_status": "failed",
            "failure_reason": failure_reason,
            "selected_episode_digest_ids": [],
            "selected_event_ids": [],
            "recall_pack_summary": None,
        }

        # Block: CycleSummary
        cycle_summary = {
            "cycle_id": cycle_id,
            "server_id": state["server_id"],
            "trigger_kind": trigger_kind,
            "started_at": started_at,
            "finished_at": finished_at,
            "selected_persona_id": state["selected_persona_id"],
            "selected_memory_set_id": state["selected_memory_set_id"],
            "selected_model_preset_id": state["selected_model_preset_id"],
            "result_kind": "internal_failure",
            "failed": True,
        }

        # Block: CycleTrace
        cycle_trace = {
            "cycle_id": cycle_id,
            "cycle_summary": cycle_summary,
            "observation_trace": {
                "trigger_kind": trigger_kind,
                "user_input_summary": self._clamp(observation_text),
                "client_context_summary": self._clamp(str(client_context)),
                "normalized_observation_summary": self._clamp(observation_text.strip()),
                "runtime_state_summary": runtime_summary,
            },
            "recall_trace": {
                "recall_hint_summary": None,
                "candidate_count": 0,
                "selected_memory_ids": [],
                "selected_episode_digest_ids": [],
                "selected_event_ids": [],
                "recall_pack_summary": None,
                "adopted_reason_summary": None,
                "rejected_candidate_summary": None,
            },
            "decision_trace": {
                "result_kind": "internal_failure",
                "reason_summary": failure_reason,
                "persona_summary": state["personas"][state["selected_persona_id"]]["display_name"],
                "memory_summary": state["memory_sets"][state["selected_memory_set_id"]]["display_name"],
                "current_context_summary": self._clamp(observation_text),
                "primary_candidate_kind": None,
            },
            "result_trace": {
                "result_kind": "internal_failure",
                "reply_summary": None,
                "noop_reason_summary": None,
                "future_act_summary": None,
                "internal_failure_summary": failure_reason,
                "duration_ms": self._duration_ms(started_at, finished_at),
            },
            "memory_trace": None,
        }

        # Block: Persist
        self.store.persist_cycle_records(
            events=events,
            retrieval_run=retrieval_run,
            cycle_summary=cycle_summary,
            cycle_trace=cycle_trace,
        )

    def _update_cycle_trace_memory_trace(self, *, cycle_id: str, memory_trace: dict[str, Any]) -> None:
        # Block: Lookup
        cycle_trace = self.store.get_cycle_trace(cycle_id)
        if cycle_trace is None:
            return

        # Block: Replace
        cycle_trace["memory_trace"] = memory_trace
        self.store.replace_cycle_trace(cycle_trace=cycle_trace)

    def _build_memory_audit_event(
        self,
        *,
        cycle_id: str,
        memory_set_id: str,
        kind: str,
        created_at: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        # Block: Event
        return {
            "event_id": f"event:{uuid.uuid4().hex}",
            "cycle_id": cycle_id,
            "memory_set_id": memory_set_id,
            "kind": kind,
            "role": "system",
            "created_at": created_at,
            **payload,
        }

    def _load_recent_turns(self, state: dict) -> list[dict]:
        # Block: WindowSetup
        now = datetime.now(UTC)
        threshold = now - timedelta(minutes=3)
        selected_preset = state["model_presets"][state["selected_model_preset_id"]]
        reply_role = selected_preset.get("roles", {}).get("reply_generation", {})
        turn_limit = reply_role.get("max_turns_window")
        if not isinstance(turn_limit, int) or turn_limit < 1:
            turn_limit = 6

        # Block: Lookup
        return self.store.load_recent_turns(
            memory_set_id=state["selected_memory_set_id"],
            since_iso=threshold.isoformat(),
            limit=turn_limit,
        )

    def _new_console_token(self) -> str:
        # Block: Token
        return f"tok_{secrets.token_urlsafe(24)}"

    def _new_cycle_id(self) -> str:
        # Block: Identifier
        return f"cycle:{uuid.uuid4().hex}"

    def _now_iso(self) -> str:
        # Block: Timestamp
        return datetime.now(UTC).isoformat()

    def _parse_iso(self, value: str) -> datetime:
        # Block: Timestamp
        return datetime.fromisoformat(value)

    def _duration_ms(self, started_at: str, finished_at: str) -> int:
        # Block: Duration
        started = self._parse_iso(started_at)
        finished = self._parse_iso(finished_at)
        return max(int((finished - started).total_seconds() * 1000), 0)

    def _clamp(self, value: str | None, limit: int = 160) -> str | None:
        # Block: Clamp
        if value is None:
            return None
        stripped = value.strip()
        if len(stripped) <= limit:
            return stripped
        return stripped[: limit - 1] + "…"
