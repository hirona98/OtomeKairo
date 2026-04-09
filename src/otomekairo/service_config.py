from __future__ import annotations

from copy import deepcopy
from typing import Any

from otomekairo.event_stream import ServerWebSocket
from otomekairo.service_common import REQUIRED_ROLE_NAMES, ServiceError


# 設定Mixin
class ServiceConfigMixin:
    def register_event_stream_connection(self, websocket: ServerWebSocket) -> str:
        # レジストリ
        return self._event_stream_registry.add_connection(websocket)

    def handle_event_stream_message(self, session_id: str, payload: dict[str, Any]) -> None:
        # 型
        message_type = payload.get("type")
        if message_type != "hello":
            raise ServiceError(400, "invalid_event_stream_message", "Only hello messages are supported.")

        # 項目
        client_id = payload.get("client_id")
        caps = payload.get("caps", [])
        if not isinstance(client_id, str) or not client_id.strip():
            raise ServiceError(400, "invalid_client_id", "hello.client_id must be a non-empty string.")
        if not isinstance(caps, list):
            raise ServiceError(400, "invalid_caps", "hello.caps must be an array.")

        # 上限群
        normalized_caps: list[str] = []
        for cap in caps:
            if not isinstance(cap, str) or not cap.strip():
                raise ServiceError(400, "invalid_caps", "hello.caps must contain non-empty strings.")
            normalized_caps.append(cap.strip())

        # 登録
        self._event_stream_registry.register_hello(
            session_id,
            client_id=client_id.strip(),
            caps=normalized_caps,
        )

    def unregister_event_stream_connection(self, session_id: str) -> None:
        # レジストリ
        self._event_stream_registry.remove_connection(session_id)

    def close_event_streams(self) -> None:
        # レジストリ
        self._event_stream_registry.close_all()

    def probe_bootstrap(self) -> dict[str, Any]:
        # 状態
        self.store.read_state()
        return {
            "bootstrap_available": True,
            "https_required": True,
            "bootstrap_state": "ready_for_first_console",
        }

    def read_server_identity(self) -> dict[str, Any]:
        # 状態
        state = self.store.read_state()
        return {
            "server_id": state["server_id"],
            "server_display_name": state["server_display_name"],
            "api_version": state["api_version"],
            "console_access_token_issued": state["console_access_token"] is not None,
        }

    def register_first_console(self) -> dict[str, Any]:
        # 読み込み状態
        state = self.store.read_state()

        # bootstrap では未発行なら新規発行し、発行済みなら現在値を返すだけにする。
        if state["console_access_token"] is None:
            state["console_access_token"] = self._new_console_token()
            self.store.write_state(state)

        # 結果
        return {
            "console_access_token": state["console_access_token"],
        }

    def reissue_console_access_token(self, token: str | None) -> dict[str, Any]:
        # 認可
        state = self._require_token(token)

        # 再発行時は保持値を新トークンへ即時に置き換え、旧トークンは残さない。
        state["console_access_token"] = self._new_console_token()
        self.store.write_state(state)
        return {
            "console_access_token": state["console_access_token"],
        }

    def get_status(self, token: str | None) -> dict[str, Any]:
        # 認可
        state = self._require_token(token)

        # 応答
        return {
            "settings_snapshot": self._build_settings_snapshot(state),
            "runtime_summary": self._build_runtime_summary(state),
        }

    def get_config(self, token: str | None) -> dict[str, Any]:
        # 認可
        state = self._require_token(token)
        selected_preset = state["model_presets"][state["selected_model_preset_id"]]

        # 応答
        return {
            "settings_snapshot": self._build_settings_snapshot(state),
            "selected_persona": deepcopy(state["personas"][state["selected_persona_id"]]),
            "selected_memory_set": deepcopy(state["memory_sets"][state["selected_memory_set_id"]]),
            "selected_model_preset": self._public_model_preset(selected_preset),
        }

    def get_editor_state(self, token: str | None) -> dict[str, Any]:
        # 認可
        state = self._require_token(token)
        return self._build_editor_state(state)

    def get_catalog(self, token: str | None) -> dict[str, Any]:
        # 認可
        state = self._require_token(token)

        # 応答
        return {
            "personas": self._catalog_entries(state["personas"], "persona_id"),
            "memory_sets": self._catalog_entries(state["memory_sets"], "memory_set_id"),
            "model_presets": self._catalog_entries(state["model_presets"], "model_preset_id"),
        }

    def patch_current(self, token: str | None, payload: dict[str, Any]) -> dict[str, Any]:
        # 状態
        state = self._require_token(token)
        should_clear_pending_intents = False

        # 選択済みpersona
        if "selected_persona_id" in payload:
            persona_id = payload["selected_persona_id"]
            if persona_id not in state["personas"]:
                raise ServiceError(404, "persona_not_found", "The requested persona_id does not exist.")
            should_clear_pending_intents = should_clear_pending_intents or persona_id != state["selected_persona_id"]
            state["selected_persona_id"] = persona_id

        # 選択済み記憶集合
        if "selected_memory_set_id" in payload:
            memory_set_id = payload["selected_memory_set_id"]
            if memory_set_id not in state["memory_sets"]:
                raise ServiceError(404, "memory_set_not_found", "The requested memory_set_id does not exist.")
            should_clear_pending_intents = should_clear_pending_intents or memory_set_id != state["selected_memory_set_id"]
            state["selected_memory_set_id"] = memory_set_id

        # 選択済みモデルプリセット
        if "selected_model_preset_id" in payload:
            model_preset_id = payload["selected_model_preset_id"]
            if model_preset_id not in state["model_presets"]:
                raise ServiceError(404, "model_preset_not_found", "The requested model_preset_id does not exist.")
            self._validate_model_preset_definition(model_preset_id, state["model_presets"][model_preset_id])
            should_clear_pending_intents = should_clear_pending_intents or model_preset_id != state["selected_model_preset_id"]
            state["selected_model_preset_id"] = model_preset_id

        # 動作設定
        if "wake_policy" in payload:
            self._validate_wake_policy(payload["wake_policy"])
            state["wake_policy"] = payload["wake_policy"]
        if "memory_enabled" in payload:
            self._validate_memory_enabled(payload["memory_enabled"])
            state["memory_enabled"] = payload["memory_enabled"]
        if "desktop_watch" in payload:
            self._validate_desktop_watch(payload["desktop_watch"])
            state["desktop_watch"] = payload["desktop_watch"]

        # 永続化
        self.store.write_state(state)
        if should_clear_pending_intents:
            self._clear_pending_intent_candidates()
        return self.get_config(token=state["console_access_token"])

    def select_persona(self, token: str | None, persona_id: str) -> dict[str, Any]:
        return self.patch_current(token, {"selected_persona_id": persona_id})

    def select_memory_set(self, token: str | None, memory_set_id: str) -> dict[str, Any]:
        return self.patch_current(token, {"selected_memory_set_id": memory_set_id})

    def update_wake_policy(self, token: str | None, wake_policy: dict[str, Any]) -> dict[str, Any]:
        return self.patch_current(token, {"wake_policy": wake_policy})

    def select_model_preset(self, token: str | None, model_preset_id: str) -> dict[str, Any]:
        return self.patch_current(token, {"selected_model_preset_id": model_preset_id})

    def get_persona(self, token: str | None, persona_id: str) -> dict[str, Any]:
        return self._get_resource_entry(
            token=token,
            entries_key="personas",
            entry_id=persona_id,
            resource_key="persona",
            not_found_code="persona_not_found",
            not_found_message="The requested persona_id does not exist.",
        )

    def replace_persona(self, token: str | None, persona_id: str, definition: dict[str, Any]) -> dict[str, Any]:
        return self._replace_resource_entry(
            token=token,
            entries_key="personas",
            selected_id_key="selected_persona_id",
            entry_id=persona_id,
            definition=definition,
            resource_key="persona",
            validator=self._validate_persona_definition,
        )

    def delete_persona(self, token: str | None, persona_id: str) -> dict[str, Any]:
        return self._delete_resource_entry(
            token=token,
            entries_key="personas",
            selected_id_key="selected_persona_id",
            entry_id=persona_id,
            not_found_code="persona_not_found",
            in_use_code="selected_persona_delete_forbidden",
            deleted_key="deleted_persona_id",
        )

    def get_memory_set(self, token: str | None, memory_set_id: str) -> dict[str, Any]:
        return self._get_resource_entry(
            token=token,
            entries_key="memory_sets",
            entry_id=memory_set_id,
            resource_key="memory_set",
            not_found_code="memory_set_not_found",
            not_found_message="The requested memory_set_id does not exist.",
        )

    def replace_memory_set(self, token: str | None, memory_set_id: str, definition: dict[str, Any]) -> dict[str, Any]:
        return self._replace_resource_entry(
            token=token,
            entries_key="memory_sets",
            selected_id_key="selected_memory_set_id",
            entry_id=memory_set_id,
            definition=definition,
            resource_key="memory_set",
            validator=self._validate_memory_set_definition,
        )

    def clone_memory_set(self, token: str | None, definition: dict[str, Any]) -> dict[str, Any]:
        # 認可
        state = self._require_token(token)

        # 入力
        source_memory_set_id = definition.get("source_memory_set_id")
        if not isinstance(source_memory_set_id, str) or not source_memory_set_id:
            raise ServiceError(400, "invalid_source_memory_set_id", "source_memory_set_id is required.")
        if source_memory_set_id not in state["memory_sets"]:
            raise ServiceError(404, "source_memory_set_not_found", "The source memory_set_id does not exist.")

        memory_set_id = definition.get("memory_set_id")
        if memory_set_id in state["memory_sets"]:
            raise ServiceError(409, "memory_set_id_already_exists", "The destination memory_set_id already exists.")

        cloned_definition = {
            "memory_set_id": memory_set_id,
            "display_name": definition.get("display_name"),
            "description": definition.get("description"),
        }
        self._validate_memory_set_definition(memory_set_id, cloned_definition)

        # 永続化
        state["memory_sets"][memory_set_id] = cloned_definition
        self.store.clone_memory_set_records(
            source_memory_set_id=source_memory_set_id,
            target_memory_set_id=memory_set_id,
        )
        self.store.write_state(state)
        return {
            "memory_set": deepcopy(cloned_definition),
        }

    def delete_memory_set(self, token: str | None, memory_set_id: str) -> dict[str, Any]:
        return self._delete_resource_entry(
            token=token,
            entries_key="memory_sets",
            selected_id_key="selected_memory_set_id",
            entry_id=memory_set_id,
            not_found_code="memory_set_not_found",
            in_use_code="selected_memory_set_delete_forbidden",
            deleted_key="deleted_memory_set_id",
            after_delete=self.store.delete_memory_set_records,
        )

    def get_model_preset(self, token: str | None, model_preset_id: str) -> dict[str, Any]:
        return self._get_resource_entry(
            token=token,
            entries_key="model_presets",
            entry_id=model_preset_id,
            resource_key="model_preset",
            not_found_code="model_preset_not_found",
            not_found_message="The requested model_preset_id does not exist.",
            public_builder=self._public_model_preset,
        )

    def replace_model_preset(self, token: str | None, model_preset_id: str, definition: dict[str, Any]) -> dict[str, Any]:
        return self._replace_resource_entry(
            token=token,
            entries_key="model_presets",
            selected_id_key="selected_model_preset_id",
            entry_id=model_preset_id,
            definition=definition,
            resource_key="model_preset",
            validator=self._validate_model_preset_definition,
            normalizer=self._normalize_model_preset_definition,
            public_builder=self._public_model_preset,
        )

    def delete_model_preset(self, token: str | None, model_preset_id: str) -> dict[str, Any]:
        return self._delete_resource_entry(
            token=token,
            entries_key="model_presets",
            selected_id_key="selected_model_preset_id",
            entry_id=model_preset_id,
            not_found_code="model_preset_not_found",
            in_use_code="selected_model_preset_delete_forbidden",
            deleted_key="deleted_model_preset_id",
        )

    def replace_editor_state(self, token: str | None, definition: dict[str, Any]) -> dict[str, Any]:
        # 認可
        state = self._require_token(token)

        # 生値Entries
        current = definition.get("current")
        personas = self._entries_by_id(definition.get("personas"), "persona_id", "personas")
        memory_sets = self._entries_by_id(definition.get("memory_sets"), "memory_set_id", "memory_sets")
        raw_model_presets = self._entries_by_id(definition.get("model_presets"), "model_preset_id", "model_presets")

        # 形状Checks
        if not isinstance(current, dict):
            raise ServiceError(400, "invalid_editor_state_current", "current must be an object.")
        if not personas:
            raise ServiceError(400, "missing_personas", "editor-state requires at least one persona.")
        if not memory_sets:
            raise ServiceError(400, "missing_memory_sets", "editor-state requires at least one memory_set.")
        if not raw_model_presets:
            raise ServiceError(400, "missing_model_presets", "editor-state requires at least one model_preset.")

        # 検証
        for persona_id, persona in personas.items():
            self._validate_persona_definition(persona_id, persona)
        for memory_set_id, memory_set in memory_sets.items():
            self._validate_memory_set_definition(memory_set_id, memory_set)

        model_presets = {
            model_preset_id: self._normalize_model_preset_definition(model_preset)
            for model_preset_id, model_preset in raw_model_presets.items()
        }
        for model_preset_id, model_preset in model_presets.items():
            self._validate_model_preset_definition(model_preset_id, model_preset)

        # 現在の選択
        selected_persona_id = current.get("selected_persona_id")
        selected_memory_set_id = current.get("selected_memory_set_id")
        selected_model_preset_id = current.get("selected_model_preset_id")
        if selected_persona_id not in personas:
            raise ServiceError(404, "persona_not_found", "The selected_persona_id does not exist in personas.")
        if selected_memory_set_id not in memory_sets:
            raise ServiceError(404, "memory_set_not_found", "The selected_memory_set_id does not exist in memory_sets.")
        if selected_model_preset_id not in model_presets:
            raise ServiceError(404, "model_preset_not_found", "The selected_model_preset_id does not exist in model_presets.")

        # 動作設定検証
        self._validate_wake_policy(current.get("wake_policy"))
        self._validate_memory_enabled(current.get("memory_enabled"))
        self._validate_desktop_watch(current.get("desktop_watch"))

        # 永続化
        state["selected_persona_id"] = selected_persona_id
        state["selected_memory_set_id"] = selected_memory_set_id
        state["selected_model_preset_id"] = selected_model_preset_id
        state["wake_policy"] = current["wake_policy"]
        state["memory_enabled"] = current["memory_enabled"]
        state["desktop_watch"] = current["desktop_watch"]
        state["personas"] = personas
        state["memory_sets"] = memory_sets
        state["model_presets"] = model_presets
        self.store.write_state(state)
        self._clear_pending_intent_candidates()
        return self._build_editor_state(state)

    def _require_token(self, token: str | None) -> dict[str, Any]:
        # 読み込み状態
        state = self.store.read_state()
        issued = state["console_access_token"]

        # 検証
        if issued is None:
            raise ServiceError(401, "bootstrap_required", "A console_access_token has not been issued yet.")
        if token != issued:
            raise ServiceError(401, "invalid_token", "The console_access_token is missing or invalid.")
        return state

    def _build_settings_snapshot(self, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "selected_persona_id": state["selected_persona_id"],
            "selected_memory_set_id": state["selected_memory_set_id"],
            "memory_enabled": state["memory_enabled"],
            "desktop_watch": deepcopy(state["desktop_watch"]),
            "wake_policy": deepcopy(state["wake_policy"]),
            "selected_model_preset_id": state["selected_model_preset_id"],
        }

    def _build_editor_state(self, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "current": self._build_settings_snapshot(state),
            "personas": [deepcopy(value) for value in state["personas"].values()],
            "memory_sets": [deepcopy(value) for value in state["memory_sets"].values()],
            "model_presets": [deepcopy(value) for value in state["model_presets"].values()],
        }

    def _build_runtime_summary(self, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "connection_state": "ready",
            "wake_scheduler_active": self._background_wake_scheduler_active() and state["wake_policy"]["mode"] == "interval",
            "ongoing_action_exists": False,
        }

    def _background_wake_scheduler_active(self) -> bool:
        with self._runtime_state_lock:
            return self._background_wake_thread is not None and self._background_wake_thread.is_alive()

    def _catalog_entries(self, entries: dict[str, dict[str, Any]], id_key: str) -> list[dict[str, Any]]:
        return [
            {
                id_key: value[id_key],
                "display_name": value.get("display_name", value[id_key]),
            }
            for value in entries.values()
        ]

    def _get_resource_entry(
        self,
        *,
        token: str | None,
        entries_key: str,
        entry_id: str,
        resource_key: str,
        not_found_code: str,
        not_found_message: str,
        public_builder: Any | None = None,
    ) -> dict[str, Any]:
        # 認可
        state = self._require_token(token)
        entry = state[entries_key].get(entry_id)
        if entry is None:
            raise ServiceError(404, not_found_code, not_found_message)
        if public_builder is None:
            return {
                resource_key: deepcopy(entry),
            }
        return {
            resource_key: public_builder(entry),
        }

    def _replace_resource_entry(
        self,
        *,
        token: str | None,
        entries_key: str,
        selected_id_key: str,
        entry_id: str,
        definition: dict[str, Any],
        resource_key: str,
        validator: Any,
        normalizer: Any | None = None,
        public_builder: Any | None = None,
    ) -> dict[str, Any]:
        # 認可
        state = self._require_token(token)

        # 正規化と検証
        stored_definition = normalizer(definition) if normalizer is not None else definition
        validator(entry_id, stored_definition)

        # 永続化
        state[entries_key][entry_id] = deepcopy(stored_definition)
        self.store.write_state(state)
        if entry_id == state[selected_id_key]:
            self._clear_pending_intent_candidates()

        # 応答
        entry = state[entries_key][entry_id]
        if public_builder is None:
            return {
                resource_key: deepcopy(entry),
            }
        return {
            resource_key: public_builder(entry),
        }

    def _delete_resource_entry(
        self,
        *,
        token: str | None,
        entries_key: str,
        selected_id_key: str,
        entry_id: str,
        not_found_code: str,
        in_use_code: str,
        deleted_key: str,
        after_delete: Any | None = None,
    ) -> dict[str, Any]:
        # 認可
        state = self._require_token(token)
        self._delete_resource(
            entries=state[entries_key],
            entry_id=entry_id,
            selected_id=state[selected_id_key],
            not_found_code=not_found_code,
            in_use_code=in_use_code,
            deleted_key=deleted_key,
        )
        if after_delete is not None:
            after_delete(entry_id)
        self.store.write_state(state)
        return {
            deleted_key: entry_id,
        }

    def _entries_by_id(self, entries: Any, id_key: str, field_name: str) -> dict[str, dict[str, Any]]:
        if not isinstance(entries, list):
            raise ServiceError(400, f"invalid_{field_name}", f"{field_name} must be an array.")

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
        return result

    def _validate_wake_policy(self, wake_policy: dict[str, Any]) -> None:
        if not isinstance(wake_policy, dict):
            raise ServiceError(400, "invalid_wake_policy", "wake_policy must be an object.")

        mode = wake_policy.get("mode")
        if mode not in {"disabled", "interval"}:
            raise ServiceError(400, "invalid_wake_policy_mode", "wake_policy.mode must be disabled or interval.")

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

    def _validate_persona_definition(self, persona_id: str, definition: dict[str, Any]) -> None:
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
            value = definition.get(field_name)
            if value is not None and not isinstance(value, str):
                raise ServiceError(400, f"invalid_{field_name}", f"{field_name} must be a string.")

    def _validate_memory_set_definition(self, memory_set_id: Any, definition: dict[str, Any]) -> None:
        if not isinstance(memory_set_id, str) or not memory_set_id:
            raise ServiceError(400, "memory_set_id_mismatch", "memory_set_id must match the path.")
        if definition.get("memory_set_id") != memory_set_id:
            raise ServiceError(400, "memory_set_id_mismatch", "memory_set_id must match the path.")
        display_name = definition.get("display_name")
        if not isinstance(display_name, str) or not display_name.strip():
            raise ServiceError(400, "invalid_memory_set_display_name", "display_name is required.")
        description = definition.get("description")
        if description is not None and not isinstance(description, str):
            raise ServiceError(400, "invalid_memory_set_description", "description must be a string.")

    def _validate_model_preset_definition(self, model_preset_id: str, definition: dict[str, Any]) -> None:
        if definition.get("model_preset_id") != model_preset_id:
            raise ServiceError(400, "model_preset_id_mismatch", "model_preset_id must match the path.")
        display_name = definition.get("display_name")
        if not isinstance(display_name, str) or not display_name.strip():
            raise ServiceError(400, "invalid_model_preset_display_name", "display_name is required.")
        roles = definition.get("roles")
        if not isinstance(roles, dict):
            raise ServiceError(400, "invalid_model_preset_roles", "roles must be an object.")

        for role_name, expected_kind in REQUIRED_ROLE_NAMES.items():
            if role_name not in roles:
                raise ServiceError(400, "missing_model_role", f"{role_name} is required.")
            role_definition = roles[role_name]
            self._validate_model_role_definition(role_name, role_definition, expected_kind)

    def _validate_model_role_definition(self, role_name: str, definition: Any, expected_kind: str) -> None:
        if not isinstance(definition, dict):
            raise ServiceError(400, "invalid_model_role", f"{role_name} must be an object.")

        kind = definition.get("kind")
        provider = definition.get("provider")
        model = definition.get("model")
        endpoint_ref = definition.get("endpoint_ref")
        api_key = definition.get("api_key")
        reasoning_effort = definition.get("reasoning_effort")

        if kind not in {"generation", "embedding"}:
            raise ServiceError(400, "invalid_model_role_kind", f"{role_name}.kind is invalid.")
        if kind != expected_kind:
            raise ServiceError(400, "model_role_kind_mismatch", f"{role_name} requires kind={expected_kind}.")
        if not isinstance(provider, str) or not provider.strip():
            raise ServiceError(400, "invalid_model_role_provider", f"{role_name}.provider is required.")
        if not isinstance(model, str) or not model.strip():
            raise ServiceError(400, "invalid_model_role_model", f"{role_name}.model is required.")
        if not isinstance(endpoint_ref, str) or not endpoint_ref.strip():
            raise ServiceError(400, "invalid_model_role_endpoint_ref", f"{role_name}.endpoint_ref is required.")
        if not isinstance(api_key, str):
            raise ServiceError(400, "invalid_model_role_api_key", f"{role_name}.api_key must be a string.")
        if reasoning_effort is not None and not isinstance(reasoning_effort, str):
            raise ServiceError(400, "invalid_reasoning_effort", f"{role_name}.reasoning_effort must be a string.")

    def _normalize_model_preset_definition(self, definition: dict[str, Any]) -> dict[str, Any]:
        normalized = {
            **definition,
        }
        display_name = normalized.get("display_name")
        if isinstance(display_name, str):
            normalized["display_name"] = display_name.strip()

        roles = definition.get("roles")
        if not isinstance(roles, dict):
            return normalized

        normalized_roles: dict[str, Any] = {}
        for role_name, role_definition in roles.items():
            if not isinstance(role_definition, dict):
                normalized_roles[role_name] = role_definition
                continue
            normalized_role: dict[str, Any] = {}
            for field_name in ("kind", "provider", "model", "endpoint_ref", "api_key"):
                if field_name not in role_definition:
                    continue
                value = role_definition.get(field_name)
                if isinstance(value, str):
                    normalized_role[field_name] = value.strip()
                else:
                    normalized_role[field_name] = value
            reasoning_effort = role_definition.get("reasoning_effort")
            if isinstance(reasoning_effort, str):
                trimmed_reasoning_effort = reasoning_effort.strip()
                if trimmed_reasoning_effort:
                    normalized_role["reasoning_effort"] = trimmed_reasoning_effort
            normalized_roles[role_name] = normalized_role

        normalized["roles"] = normalized_roles
        return normalized

    def _public_model_preset(self, definition: dict[str, Any]) -> dict[str, Any]:
        public_definition = deepcopy(definition)
        roles = public_definition.get("roles", {})
        if not isinstance(roles, dict):
            return public_definition
        public_definition["roles"] = {
            role_name: self._public_model_role(role_definition)
            for role_name, role_definition in roles.items()
        }
        return public_definition

    def _public_model_role(self, definition: Any) -> Any:
        if not isinstance(definition, dict):
            return definition
        public_definition = {
            **definition,
            "api_key_present": bool(definition.get("api_key")),
        }
        public_definition.pop("api_key", None)
        return public_definition

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
