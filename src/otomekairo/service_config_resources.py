from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Any

from otomekairo.service_common import ServiceError


class ServiceConfigResourcesMixin:
    def probe_bootstrap(self) -> dict[str, Any]:
        # 状態
        state = self.store.read_state()
        return {
            "bootstrap_available": True,
            "https_required": True,
            "bootstrap_state": self._bootstrap_state(state),
        }

    def read_server_identity(self) -> dict[str, Any]:
        # 状態
        state = self.store.read_state()
        return {
            "server_id": state["server_id"],
            "server_display_name": state["server_display_name"],
            "api_version": state["api_version"],
            "bootstrap_state": self._bootstrap_state(state),
            "console_access_token_issued": state["console_access_token"] is not None,
        }

    def register_first_console(self) -> dict[str, Any]:
        # 読み込み状態
        state = self.store.read_state()

        # 初回登録済みの token は再表示しない。
        if state["console_access_token"] is not None:
            raise ServiceError(409, "first_console_already_registered", "The first console token has already been issued.")

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
            "selected_memory_set": self._public_memory_set(state["memory_sets"][state["selected_memory_set_id"]]),
            "selected_model_preset": self._public_model_preset(selected_preset),
        }

    def get_editor_state(self, token: str | None) -> dict[str, Any]:
        # 認可
        state = self._require_token(token)
        self._append_editor_state_audit_event(state=state, operation="read")
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
        previous_wake_policy = deepcopy(state["wake_policy"])
        should_clear_runtime_layers = False
        should_clear_drive_states = False
        supported_fields = {
            "selected_persona_id",
            "selected_memory_set_id",
            "selected_model_preset_id",
            "wake_policy",
        }
        unsupported_fields = sorted(set(payload.keys()) - supported_fields)
        if unsupported_fields:
            raise ServiceError(
                400,
                "unsupported_current_config_fields",
                f"current config has unsupported fields: {', '.join(unsupported_fields)}.",
            )

        # 選択済みpersona
        if "selected_persona_id" in payload:
            persona_id = payload["selected_persona_id"]
            if persona_id not in state["personas"]:
                raise ServiceError(404, "persona_not_found", "The requested persona_id does not exist.")
            persona_changed = persona_id != state["selected_persona_id"]
            should_clear_runtime_layers = should_clear_runtime_layers or persona_changed
            should_clear_drive_states = should_clear_drive_states or persona_changed
            state["selected_persona_id"] = persona_id

        # 選択済み記憶集合
        if "selected_memory_set_id" in payload:
            memory_set_id = payload["selected_memory_set_id"]
            if memory_set_id not in state["memory_sets"]:
                raise ServiceError(404, "memory_set_not_found", "The requested memory_set_id does not exist.")
            should_clear_runtime_layers = should_clear_runtime_layers or memory_set_id != state["selected_memory_set_id"]
            state["selected_memory_set_id"] = memory_set_id

        # 選択済みモデルプリセット
        if "selected_model_preset_id" in payload:
            model_preset_id = payload["selected_model_preset_id"]
            if model_preset_id not in state["model_presets"]:
                raise ServiceError(404, "model_preset_not_found", "The requested model_preset_id does not exist.")
            self._validate_model_preset_definition(model_preset_id, state["model_presets"][model_preset_id])
            should_clear_runtime_layers = should_clear_runtime_layers or model_preset_id != state["selected_model_preset_id"]
            state["selected_model_preset_id"] = model_preset_id

        # 動作設定
        if "wake_policy" in payload:
            self._validate_wake_policy(payload["wake_policy"])
            state["wake_policy"] = payload["wake_policy"]

        # 永続化
        self.store.write_state(state)
        if should_clear_runtime_layers:
            self._clear_runtime_state_layers(
                memory_set_ids=list(state["memory_sets"].keys()),
                clear_drive_states=should_clear_drive_states,
            )
        if "wake_policy" in payload:
            self._sync_wake_policy_runtime_state(
                previous_wake_policy=previous_wake_policy,
                next_wake_policy=state["wake_policy"],
                current_time=self._now_iso(),
            )
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
            normalizer=self._normalize_persona_definition,
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
            public_builder=self._public_memory_set,
        )

    def replace_memory_set(self, token: str | None, memory_set_id: str, definition: dict[str, Any]) -> dict[str, Any]:
        # 認可
        state = self._require_token(token)

        # 正規化と検証
        previous_definition = deepcopy(state["memory_sets"].get(memory_set_id))
        stored_definition = self._normalize_memory_set_definition(definition)
        self._validate_memory_set_definition(memory_set_id, stored_definition)

        # 永続化
        state["memory_sets"][memory_set_id] = deepcopy(stored_definition)
        self.store.write_state(state)
        if self._embedding_definition_changed(previous_definition, stored_definition):
            self.store.reset_memory_set_vector_index(memory_set_id)
        if memory_set_id == state["selected_memory_set_id"]:
            self._clear_runtime_state_layers(
                memory_set_ids=[memory_set_id],
                clear_drive_states=False,
            )

        # 応答
        return {
            "memory_set": self._public_memory_set(state["memory_sets"][memory_set_id]),
        }

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
        if not isinstance(memory_set_id, str) or not memory_set_id:
            raise ServiceError(400, "invalid_memory_set_id", "memory_set_id is required.")
        if memory_set_id in state["memory_sets"]:
            raise ServiceError(409, "memory_set_id_already_exists", "The destination memory_set_id already exists.")

        cloned_definition = {
            "memory_set_id": memory_set_id,
            "display_name": definition.get("display_name"),
            "embedding": deepcopy(state["memory_sets"][source_memory_set_id]["embedding"]),
        }
        cloned_definition = self._normalize_memory_set_definition(cloned_definition)
        self._validate_memory_set_definition(memory_set_id, cloned_definition)

        # 永続化
        state["memory_sets"][memory_set_id] = cloned_definition
        self.store.clone_memory_set_records(
            source_memory_set_id=source_memory_set_id,
            target_memory_set_id=memory_set_id,
        )
        self.store.write_state(state)
        return {
            "memory_set": self._public_memory_set(cloned_definition),
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
        previous_wake_policy = deepcopy(state["wake_policy"])
        previous_memory_sets = deepcopy(state["memory_sets"])

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
        personas = {
            persona_id: self._normalize_persona_definition(persona)
            for persona_id, persona in personas.items()
        }
        for persona_id, persona in personas.items():
            self._validate_persona_definition(persona_id, persona)
        memory_sets = {
            memory_set_id: self._normalize_memory_set_definition(memory_set)
            for memory_set_id, memory_set in memory_sets.items()
        }
        for memory_set_id, memory_set in memory_sets.items():
            self._validate_memory_set_definition(memory_set_id, memory_set)

        model_presets = {
            model_preset_id: self._normalize_model_preset_definition(model_preset)
            for model_preset_id, model_preset in raw_model_presets.items()
        }
        for model_preset_id, model_preset in model_presets.items():
            self._validate_model_preset_definition(model_preset_id, model_preset)

        # 現在の選択
        supported_current_fields = {
            "selected_persona_id",
            "selected_memory_set_id",
            "selected_model_preset_id",
            "wake_policy",
        }
        unsupported_current_fields = sorted(set(current.keys()) - supported_current_fields)
        if unsupported_current_fields:
            raise ServiceError(
                400,
                "unsupported_editor_state_current_fields",
                f"editor-state current has unsupported fields: {', '.join(unsupported_current_fields)}.",
            )
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

        # 永続化
        state["selected_persona_id"] = selected_persona_id
        state["selected_memory_set_id"] = selected_memory_set_id
        state["selected_model_preset_id"] = selected_model_preset_id
        state["wake_policy"] = current["wake_policy"]
        state["personas"] = personas
        state["memory_sets"] = memory_sets
        state["model_presets"] = model_presets
        self.store.write_state(state)
        for memory_set_id, memory_set in memory_sets.items():
            previous_definition = previous_memory_sets.get(memory_set_id)
            if self._embedding_definition_changed(previous_definition, memory_set):
                self.store.reset_memory_set_vector_index(memory_set_id)
        self._clear_runtime_state_layers(
            memory_set_ids=list(memory_sets.keys()),
            clear_drive_states=True,
        )
        self._sync_wake_policy_runtime_state(
            previous_wake_policy=previous_wake_policy,
            next_wake_policy=state["wake_policy"],
            current_time=self._now_iso(),
        )
        self._append_editor_state_audit_event(state=state, operation="write")
        return self._build_editor_state(state)

    def _build_settings_snapshot(self, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "selected_persona_id": state["selected_persona_id"],
            "selected_memory_set_id": state["selected_memory_set_id"],
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

    def _clear_runtime_state_layers(
        self,
        *,
        memory_set_ids: list[str],
        clear_drive_states: bool,
    ) -> None:
        self._clear_pending_intent_candidates()
        for memory_set_id in memory_set_ids:
            self.store.clear_world_states(memory_set_id=memory_set_id)
            self.store.clear_ongoing_action(memory_set_id=memory_set_id)
            if clear_drive_states:
                self.store.clear_drive_states(memory_set_id=memory_set_id)

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

    def _append_editor_state_audit_event(self, *, state: dict[str, Any], operation: str) -> None:
        # 秘密値を含む editor-state 本文は audit に残さない。
        self.store.append_events(
            events=[
                {
                    "event_id": f"event:config_audit:{uuid.uuid4().hex}",
                    "cycle_id": "config:editor-state",
                    "memory_set_id": state["selected_memory_set_id"],
                    "kind": f"editor_state_{operation}",
                    "role": "system",
                    "created_at": self._now_iso(),
                    "selected_persona_id": state["selected_persona_id"],
                    "selected_memory_set_id": state["selected_memory_set_id"],
                    "selected_model_preset_id": state["selected_model_preset_id"],
                    "persona_count": len(state["personas"]),
                    "memory_set_count": len(state["memory_sets"]),
                    "model_preset_count": len(state["model_presets"]),
                }
            ]
        )

    def _embedding_definition_changed(
        self,
        previous_definition: dict[str, Any] | None,
        current_definition: dict[str, Any],
    ) -> bool:
        if not isinstance(previous_definition, dict):
            return False
        return previous_definition.get("embedding") != current_definition.get("embedding")

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

    def _public_memory_set(self, definition: dict[str, Any]) -> dict[str, Any]:
        public_definition = deepcopy(definition)
        embedding = public_definition.get("embedding")
        if isinstance(embedding, dict):
            public_definition["embedding"] = self._public_embedding_definition(embedding)
        return public_definition

    def _public_embedding_definition(self, definition: dict[str, Any]) -> dict[str, Any]:
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
