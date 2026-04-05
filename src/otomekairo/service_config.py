from __future__ import annotations

from typing import Any

from otomekairo.event_stream import ServerWebSocket
from otomekairo.service_common import REQUIRED_ROLE_NAMES, ServiceError


# Block: ConfigMixin
class ServiceConfigMixin:
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

    def probe_bootstrap(self) -> dict[str, Any]:
        # Block: State
        self.store.read_state()
        return {
            "bootstrap_available": True,
            "https_required": True,
            "bootstrap_state": "ready_for_first_console",
        }

    def read_server_identity(self) -> dict[str, Any]:
        # Block: State
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

        # Block: Result
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
        # Block: Authorization
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

    def patch_current(self, token: str | None, payload: dict[str, Any]) -> dict[str, Any]:
        # Block: State
        state = self._require_token(token)
        should_clear_future_act = False

        # Block: SelectedPersona
        if "selected_persona_id" in payload:
            persona_id = payload["selected_persona_id"]
            if persona_id not in state["personas"]:
                raise ServiceError(404, "persona_not_found", "The requested persona_id does not exist.")
            should_clear_future_act = should_clear_future_act or persona_id != state["selected_persona_id"]
            state["selected_persona_id"] = persona_id

        # Block: SelectedMemorySet
        if "selected_memory_set_id" in payload:
            memory_set_id = payload["selected_memory_set_id"]
            if memory_set_id not in state["memory_sets"]:
                raise ServiceError(404, "memory_set_not_found", "The requested memory_set_id does not exist.")
            should_clear_future_act = should_clear_future_act or memory_set_id != state["selected_memory_set_id"]
            state["selected_memory_set_id"] = memory_set_id

        # Block: SelectedModelPreset
        if "selected_model_preset_id" in payload:
            model_preset_id = payload["selected_model_preset_id"]
            if model_preset_id not in state["model_presets"]:
                raise ServiceError(404, "model_preset_not_found", "The requested model_preset_id does not exist.")
            self._validate_model_preset_definition(state, model_preset_id, state["model_presets"][model_preset_id])
            should_clear_future_act = should_clear_future_act or model_preset_id != state["selected_model_preset_id"]
            state["selected_model_preset_id"] = model_preset_id

        # Block: ToggleFields
        if "wake_policy" in payload:
            self._validate_wake_policy(payload["wake_policy"])
            state["wake_policy"] = payload["wake_policy"]
        if "memory_enabled" in payload:
            self._validate_memory_enabled(payload["memory_enabled"])
            state["memory_enabled"] = payload["memory_enabled"]
        if "desktop_watch" in payload:
            self._validate_desktop_watch(payload["desktop_watch"])
            state["desktop_watch"] = payload["desktop_watch"]

        # Block: Persist
        self.store.write_state(state)
        if should_clear_future_act:
            self._clear_future_act_candidates()
        return self.get_config(token=state["console_access_token"])

    def select_persona(self, token: str | None, persona_id: str) -> dict[str, Any]:
        # Block: Delegate
        return self.patch_current(token, {"selected_persona_id": persona_id})

    def select_memory_set(self, token: str | None, memory_set_id: str) -> dict[str, Any]:
        # Block: Delegate
        return self.patch_current(token, {"selected_memory_set_id": memory_set_id})

    def update_wake_policy(self, token: str | None, wake_policy: dict[str, Any]) -> dict[str, Any]:
        # Block: Delegate
        return self.patch_current(token, {"wake_policy": wake_policy})

    def select_model_preset(self, token: str | None, model_preset_id: str) -> dict[str, Any]:
        # Block: Delegate
        return self.patch_current(token, {"selected_model_preset_id": model_preset_id})

    def get_persona(self, token: str | None, persona_id: str) -> dict[str, Any]:
        # Block: Authorization
        state = self._require_token(token)
        persona = state["personas"].get(persona_id)
        if persona is None:
            raise ServiceError(404, "persona_not_found", "The requested persona_id does not exist.")
        return {
            "persona": persona,
        }

    def replace_persona(self, token: str | None, persona_id: str, definition: dict[str, Any]) -> dict[str, Any]:
        # Block: Authorization
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
        # Block: Authorization
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
        # Block: Authorization
        state = self._require_token(token)
        memory_set = state["memory_sets"].get(memory_set_id)
        if memory_set is None:
            raise ServiceError(404, "memory_set_not_found", "The requested memory_set_id does not exist.")
        return {
            "memory_set": memory_set,
        }

    def replace_memory_set(self, token: str | None, memory_set_id: str, definition: dict[str, Any]) -> dict[str, Any]:
        # Block: Authorization
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
        # Block: Authorization
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
        # Block: Authorization
        state = self._require_token(token)
        model_preset = state["model_presets"].get(model_preset_id)
        if model_preset is None:
            raise ServiceError(404, "model_preset_not_found", "The requested model_preset_id does not exist.")
        return {
            "model_preset": model_preset,
        }

    def replace_model_preset(self, token: str | None, model_preset_id: str, definition: dict[str, Any]) -> dict[str, Any]:
        # Block: Authorization
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
        # Block: Authorization
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
        # Block: Authorization
        state = self._require_token(token)
        model_profile = state["model_profiles"].get(model_profile_id)
        if model_profile is None:
            raise ServiceError(404, "model_profile_not_found", "The requested model_profile_id does not exist.")
        return {
            "model_profile": model_profile,
        }

    def replace_model_profile(self, token: str | None, model_profile_id: str, definition: dict[str, Any]) -> dict[str, Any]:
        # Block: Authorization
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
        # Block: Authorization
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

    def replace_editor_state(self, token: str | None, definition: dict[str, Any]) -> dict[str, Any]:
        # Block: Authorization
        state = self._require_token(token)

        # Block: RawEntries
        current = definition.get("current")
        personas = self._entries_by_id(definition.get("personas"), "persona_id", "personas")
        memory_sets = self._entries_by_id(definition.get("memory_sets"), "memory_set_id", "memory_sets")
        model_profiles = self._entries_by_id(definition.get("model_profiles"), "model_profile_id", "model_profiles")
        candidate_state = {
            **state,
            "model_profiles": model_profiles,
        }
        raw_model_presets = self._entries_by_id(definition.get("model_presets"), "model_preset_id", "model_presets")

        # Block: ShapeChecks
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

        # Block: Validation
        for persona_id, persona in personas.items():
            self._validate_persona_definition(persona_id, persona)
        for memory_set_id, memory_set in memory_sets.items():
            self._validate_memory_set_definition(memory_set_id, memory_set)
        for model_profile_id, model_profile in model_profiles.items():
            self._validate_model_profile_definition(model_profile_id, model_profile)

        # Block: ModelPresetNormalization
        model_presets = {
            model_preset_id: self._normalize_model_preset_definition(model_preset)
            for model_preset_id, model_preset in raw_model_presets.items()
        }
        for model_preset_id, model_preset in model_presets.items():
            self._validate_model_preset_definition(candidate_state, model_preset_id, model_preset)

        # Block: CurrentSelection
        selected_persona_id = current.get("selected_persona_id")
        selected_memory_set_id = current.get("selected_memory_set_id")
        selected_model_preset_id = current.get("selected_model_preset_id")
        if selected_persona_id not in personas:
            raise ServiceError(404, "persona_not_found", "The selected_persona_id does not exist in personas.")
        if selected_memory_set_id not in memory_sets:
            raise ServiceError(404, "memory_set_not_found", "The selected_memory_set_id does not exist in memory_sets.")
        if selected_model_preset_id not in model_presets:
            raise ServiceError(404, "model_preset_not_found", "The selected_model_preset_id does not exist in model_presets.")

        # Block: ToggleValidation
        self._validate_wake_policy(current.get("wake_policy"))
        self._validate_memory_enabled(current.get("memory_enabled"))
        self._validate_desktop_watch(current.get("desktop_watch"))

        # Block: Persist
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

    def _require_token(self, token: str | None) -> dict[str, Any]:
        # Block: LoadState
        state = self.store.read_state()
        issued = state["console_access_token"]

        # Block: Validation
        if issued is None:
            raise ServiceError(401, "bootstrap_required", "A console_access_token has not been issued yet.")
        if token != issued:
            raise ServiceError(401, "invalid_token", "The console_access_token is missing or invalid.")
        return state

    def _selected_model_preset_uses_profile(self, state: dict[str, Any], model_profile_id: str) -> bool:
        # Block: Lookup
        selected_preset = state["model_presets"][state["selected_model_preset_id"]]
        for role_value in selected_preset.get("roles", {}).values():
            if role_value.get("model_profile_id") == model_profile_id:
                return True

        # Block: Empty
        return False

    def _build_settings_snapshot(self, state: dict[str, Any]) -> dict[str, Any]:
        # Block: Snapshot
        return {
            "selected_persona_id": state["selected_persona_id"],
            "selected_memory_set_id": state["selected_memory_set_id"],
            "memory_enabled": state["memory_enabled"],
            "desktop_watch": state["desktop_watch"],
            "wake_policy": state["wake_policy"],
            "selected_model_preset_id": state["selected_model_preset_id"],
        }

    def _build_editor_state(self, state: dict[str, Any]) -> dict[str, Any]:
        # Block: Result
        return {
            "current": self._build_settings_snapshot(state),
            "personas": list(state["personas"].values()),
            "memory_sets": list(state["memory_sets"].values()),
            "model_presets": list(state["model_presets"].values()),
            "model_profiles": list(state["model_profiles"].values()),
        }

    def _build_runtime_summary(self, state: dict[str, Any]) -> dict[str, Any]:
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

    def _catalog_entries(self, entries: dict[str, dict[str, Any]], id_key: str) -> list[dict[str, Any]]:
        # Block: Transform
        return [
            {
                id_key: value[id_key],
                "display_name": value.get("display_name", value[id_key]),
            }
            for value in entries.values()
        ]

    def _entries_by_id(self, entries: Any, id_key: str, field_name: str) -> dict[str, dict[str, Any]]:
        # Block: Shape
        if not isinstance(entries, list):
            raise ServiceError(400, f"invalid_{field_name}", f"{field_name} must be an array.")

        # Block: Collect
        result: dict[str, dict[str, Any]] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise ServiceError(400, f"invalid_{field_name}_entry", f"Each {field_name} entry must be an object.")
            entry_id = entry.get(id_key)
            if not isinstance(entry_id, str) or not entry_id:
                raise ServiceError(400, f"invalid_{field_name}_id", f"Each {field_name} entry requires {id_key}.")
            if entry_id in result:
                raise ServiceError(400, f"duplicate_{field_name}_id", f"{entry_id} is duplicated in {field_name}.")
            result[entry_id] = entry

        # Block: Result
        return result

    def _validate_wake_policy(self, wake_policy: dict[str, Any]) -> None:
        # Block: Shape
        if not isinstance(wake_policy, dict):
            raise ServiceError(400, "invalid_wake_policy", "wake_policy must be an object.")

        # Block: Mode
        mode = wake_policy.get("mode")
        if mode not in {"disabled", "interval"}:
            raise ServiceError(400, "invalid_wake_policy_mode", "wake_policy.mode must be disabled or interval.")

        # Block: Interval
        if mode == "interval":
            interval_minutes = wake_policy.get("interval_minutes")
            if not isinstance(interval_minutes, int) or interval_minutes < 1:
                raise ServiceError(400, "invalid_interval_minutes", "interval_minutes must be an integer >= 1.")

    def _validate_memory_enabled(self, memory_enabled: Any) -> None:
        # Block: Shape
        if not isinstance(memory_enabled, bool):
            raise ServiceError(400, "invalid_memory_enabled", "memory_enabled must be a boolean.")

    def _validate_desktop_watch(self, desktop_watch: Any) -> None:
        # Block: Shape
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

    def _validate_persona_definition(self, persona_id: str, definition: dict[str, Any]) -> None:
        # Block: Shape
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

    def _validate_memory_set_definition(self, memory_set_id: str, definition: dict[str, Any]) -> None:
        # Block: Shape
        if definition.get("memory_set_id") != memory_set_id:
            raise ServiceError(400, "memory_set_id_mismatch", "memory_set_id must match the path.")
        display_name = definition.get("display_name")
        if not isinstance(display_name, str) or not display_name.strip():
            raise ServiceError(400, "invalid_memory_set_display_name", "display_name is required.")
        description = definition.get("description")
        if description is not None and not isinstance(description, str):
            raise ServiceError(400, "invalid_memory_set_description", "description must be a string.")

    def _validate_model_preset_definition(self, state: dict[str, Any], model_preset_id: str, definition: dict[str, Any]) -> None:
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
            reasoning_effort = role_definition.get("reasoning_effort")
            if reasoning_effort is not None and not isinstance(reasoning_effort, str):
                raise ServiceError(400, "invalid_reasoning_effort", f"{role_name}.reasoning_effort must be a string.")

    def _normalize_model_preset_definition(self, definition: dict[str, Any]) -> dict[str, Any]:
        # Block: Clone
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

    def _validate_model_profile_definition(self, model_profile_id: str, definition: dict[str, Any]) -> None:
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
        entries: dict[str, dict[str, Any]],
        entry_id: str,
        selected_id: str,
        not_found_code: str,
        in_use_code: str,
        deleted_key: str,
    ) -> dict[str, Any]:
        # Block: NotFound
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
