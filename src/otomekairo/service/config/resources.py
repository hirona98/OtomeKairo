from __future__ import annotations

import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from otomekairo.defaults import API_VERSION, build_default_console_client_settings
from otomekairo.service.common import ServiceError
from otomekairo.service.config.constants import (
    MCP_CONNECTOR_KINDS,
    MCP_DEFAULT_CLIENT_ID,
    MCP_DEFAULT_CONNECTOR_KIND,
    MCP_TRANSPORTS,
)


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
            # API互換版は永続設定ではなく、実行中コードの契約版を返す。
            "api_version": API_VERSION,
            "bootstrap_state": self._bootstrap_state(state),
            "console_access_token_issued": state["console_access_token"] is not None,
        }

    def acquire_console_access_token(self) -> dict[str, Any]:
        # 同時要求でも全クライアントへ同じ token を返すため、取得と初回発行を直列化する。
        with self._runtime_state_lock:
            state = self.store.read_state()
            if state["console_access_token"] is None:
                state["console_access_token"] = self._new_console_token()
                self.store.write_state(state)

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
            "conversation_display_names": [
                deepcopy(value)
                for value in state["conversation_display_names"].values()
            ],
            "selected_persona": deepcopy(state["personas"][state["selected_persona_id"]]),
            "selected_memory_set": self._public_memory_set(state["memory_sets"][state["selected_memory_set_id"]]),
            "selected_model_preset": self._public_model_preset(selected_preset),
        }

    def list_conversation_display_names(self, token: str | None) -> dict[str, Any]:
        self._require_token(token)
        return {
            "conversation_display_names": self.store.list_conversation_display_names()
        }

    def create_conversation_display_name(
        self,
        token: str | None,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_token(token)
        display_name = self._conversation_display_name_from_payload(payload)
        try:
            return self.store.create_conversation_display_name(
                conversation_display_name_id=f"conversation_display_name:{uuid.uuid4()}",
                display_name=display_name,
            )
        except ValueError as exc:
            if str(exc) == "duplicate_conversation_display_name":
                raise ServiceError(
                    409,
                    "duplicate_conversation_display_name",
                    "The conversation display name already exists.",
                ) from exc
            raise

    def update_conversation_display_name(
        self,
        token: str | None,
        conversation_display_name_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_token(token)
        display_name = self._conversation_display_name_from_payload(payload)
        try:
            definition = self.store.update_conversation_display_name(
                conversation_display_name_id=conversation_display_name_id,
                display_name=display_name,
            )
        except ValueError as exc:
            if str(exc) == "duplicate_conversation_display_name":
                raise ServiceError(
                    409,
                    "duplicate_conversation_display_name",
                    "The conversation display name already exists.",
                ) from exc
            raise
        if definition is None:
            raise ServiceError(
                404,
                "conversation_display_name_not_found",
                "The conversation display name does not exist.",
            )
        return definition

    def delete_conversation_display_name(
        self,
        token: str | None,
        conversation_display_name_id: str,
    ) -> dict[str, Any]:
        self._require_token(token)
        try:
            definition = self.store.delete_conversation_display_name(
                conversation_display_name_id
            )
        except ValueError as exc:
            if str(exc) == "conversation_display_name_in_use":
                raise ServiceError(
                    409,
                    "conversation_display_name_in_use",
                    "The conversation display name is in use.",
                ) from exc
            raise
        if definition is None:
            raise ServiceError(
                404,
                "conversation_display_name_not_found",
                "The conversation display name does not exist.",
            )
        return definition

    def _conversation_display_name_from_payload(self, payload: dict[str, Any]) -> str:
        if not isinstance(payload, dict) or set(payload) != {"display_name"}:
            raise ServiceError(
                400,
                "invalid_conversation_display_name",
                "display_name is required.",
            )
        display_name = payload.get("display_name")
        if not isinstance(display_name, str) or not display_name.strip():
            raise ServiceError(
                400,
                "invalid_conversation_display_name",
                "display_name must be a non-empty string.",
            )
        return display_name.strip()

    def get_editor_state(self, token: str | None) -> dict[str, Any]:
        # 認可
        state = self._require_token(token)
        self._append_editor_state_audit_event(state=state, operation="read")
        return self._build_editor_state(state)

    def get_avatar_speech(self, token: str | None) -> dict[str, Any]:
        # 通常の読み取りでは音声サービスの秘密値を返さない。
        state = self._require_token(token)
        selected_avatar = state["avatars"][state["selected_avatar_id"]]
        return {
            "selected_avatar_id": state["selected_avatar_id"],
            "audio_output_settings": deepcopy(state["audio_output_settings"]),
            "microphone_settings": deepcopy(state["microphone_settings"]),
            "selected_avatar": self._public_avatar_definition(selected_avatar),
        }

    def get_avatar_speech_editor_state(self, token: str | None) -> dict[str, Any]:
        # 設定編集面だけがSTT/TTSの秘密値を含む。
        state = self._require_token(token)
        self._append_avatar_speech_editor_state_audit_event(state=state, operation="read")
        return self._build_avatar_speech_editor_state(state)

    def get_stt_enabled(self, token: str | None) -> dict[str, Any]:
        # 運用トグル用。選択中アバターの stt.enabled だけを返す。
        state = self._require_token(token)
        selected_avatar_id = state["selected_avatar_id"]
        return {
            "enabled": bool(state["avatars"][selected_avatar_id]["stt"]["enabled"]),
            "selected_avatar_id": selected_avatar_id,
        }

    def replace_stt_enabled(
        self,
        token: str | None,
        definition: dict[str, Any],
    ) -> dict[str, Any]:
        # マイク運用トグルは stt.enabled 1 bit だけを更新する。
        state = self._require_token(token)
        if not isinstance(definition, dict) or set(definition) != {"enabled"}:
            raise ServiceError(
                400,
                "invalid_stt_enabled",
                "stt-enabled request must contain only enabled.",
            )
        enabled = definition.get("enabled")
        if not isinstance(enabled, bool):
            raise ServiceError(
                400,
                "invalid_stt_enabled",
                "enabled must be a boolean.",
            )
        selected_avatar_id = state["selected_avatar_id"]
        selected_avatar = state["avatars"][selected_avatar_id]
        previous_enabled = bool(selected_avatar["stt"]["enabled"])
        if previous_enabled != enabled:
            selected_avatar["stt"]["enabled"] = enabled
            self.store.write_state(state)
            self._reload_audio_runtime_settings()
            self._append_stt_enabled_audit_event(
                state=state,
                enabled=enabled,
            )
        return {
            "enabled": enabled,
            "selected_avatar_id": selected_avatar_id,
        }

    def get_tts_enabled(self, token: str | None) -> dict[str, Any]:
        # 運用トグル用。選択中アバターの tts.enabled だけを返す。
        state = self._require_token(token)
        selected_avatar_id = state["selected_avatar_id"]
        return {
            "enabled": bool(state["avatars"][selected_avatar_id]["tts"]["enabled"]),
            "selected_avatar_id": selected_avatar_id,
        }

    def replace_tts_enabled(
        self,
        token: str | None,
        definition: dict[str, Any],
    ) -> dict[str, Any]:
        # TTS 運用トグルは tts.enabled 1 bit だけを更新する。
        # 音声入力 lease は破棄しない（TtsRuntime は reserve 時に store を読む）。
        state = self._require_token(token)
        if not isinstance(definition, dict) or set(definition) != {"enabled"}:
            raise ServiceError(
                400,
                "invalid_tts_enabled",
                "tts-enabled request must contain only enabled.",
            )
        enabled = definition.get("enabled")
        if not isinstance(enabled, bool):
            raise ServiceError(
                400,
                "invalid_tts_enabled",
                "enabled must be a boolean.",
            )
        selected_avatar_id = state["selected_avatar_id"]
        selected_avatar = state["avatars"][selected_avatar_id]
        previous_enabled = bool(selected_avatar["tts"]["enabled"])
        if previous_enabled != enabled:
            selected_avatar["tts"]["enabled"] = enabled
            self.store.write_state(state)
            self._publish_audio_runtime_state()
            self._append_tts_enabled_audit_event(
                state=state,
                enabled=enabled,
            )
        return {
            "enabled": enabled,
            "selected_avatar_id": selected_avatar_id,
        }

    def replace_avatar_speech_editor_state(
        self,
        token: str | None,
        definition: dict[str, Any],
    ) -> dict[str, Any]:
        # 音声設定は独立したbundleとして一括検証してから保存する。
        state = self._require_token(token)
        supported_fields = {
            "selected_avatar_id",
            "audio_output_settings",
            "microphone_settings",
            "avatars",
        }
        unsupported_fields = sorted(set(definition) - supported_fields)
        if unsupported_fields:
            raise ServiceError(
                400,
                "unsupported_avatar_speech_editor_state_fields",
                f"avatar speech editor-state has unsupported fields: {', '.join(unsupported_fields)}.",
            )
        avatars = self._entries_by_id(definition.get("avatars"), "avatar_id", "avatars")
        if not avatars:
            raise ServiceError(400, "missing_avatars", "avatar speech editor-state requires at least one avatar.")
        normalized_avatars = {
            avatar_id: self._normalize_avatar_definition(avatar)
            for avatar_id, avatar in avatars.items()
        }
        for avatar_id, avatar in normalized_avatars.items():
            self._validate_avatar_definition(avatar_id, avatar)

        selected_avatar_id = definition.get("selected_avatar_id")
        if selected_avatar_id not in normalized_avatars:
            raise ServiceError(
                404,
                "avatar_not_found",
                "The selected_avatar_id does not exist in avatars.",
            )
        microphone_settings = self._normalize_microphone_settings(
            definition.get("microphone_settings")
        )
        self._validate_microphone_settings(microphone_settings)
        audio_output_settings = self._normalize_audio_output_settings(
            definition.get("audio_output_settings")
        )
        self._validate_audio_output_settings(audio_output_settings)

        state["selected_avatar_id"] = selected_avatar_id
        state["audio_output_settings"] = deepcopy(audio_output_settings)
        state["microphone_settings"] = deepcopy(microphone_settings)
        state["avatars"] = normalized_avatars
        for client_entry in state.get("console_client_settings", {}).values():
            settings = client_entry.get("settings")
            if not isinstance(settings, dict):
                continue
            presentations = settings.get("avatar_presentations")
            if not isinstance(presentations, list):
                continue
            settings["avatar_presentations"] = [
                presentation
                for presentation in presentations
                if isinstance(presentation, dict)
                and presentation.get("avatar_id") in normalized_avatars
            ]
        self.store.write_state(state)
        self._reload_audio_runtime_settings()
        self._publish_audio_runtime_state()
        self._append_avatar_speech_editor_state_audit_event(state=state, operation="write")
        return self._build_avatar_speech_editor_state(state)

    def get_catalog(self, token: str | None) -> dict[str, Any]:
        # 認可
        state = self._require_token(token)

        # 応答
        return {
            "personas": self._catalog_entries(state["personas"], "persona_id"),
            "memory_sets": self._catalog_entries(state["memory_sets"], "memory_set_id"),
            "model_presets": self._catalog_entries(state["model_presets"], "model_preset_id"),
        }

    def connect_console_client(self, token: str | None, client_id: str) -> dict[str, Any]:
        # 端末設定の取得と最終接続端末の更新を一つの接続操作にする。
        state = self._require_token(token)
        normalized_client_id = self._validate_console_client_id(client_id)
        entries = state.setdefault("console_client_settings", {})
        entry = entries.get(normalized_client_id)
        if not isinstance(entry, dict):
            settings = build_default_console_client_settings(normalized_client_id)
            self._validate_console_client_settings(normalized_client_id, settings)
            entry = {
                "last_connected_at": None,
                "settings": settings,
            }
            entries[normalized_client_id] = entry
        entry["last_connected_at"] = self._now_iso()
        self.store.write_state(state)
        return self._build_console_client_editor_state(normalized_client_id, entry)

    def get_console_client_editor_state(self, token: str | None, client_id: str) -> dict[str, Any]:
        state = self._require_token(token)
        normalized_client_id = self._validate_console_client_id(client_id)
        entry = state.get("console_client_settings", {}).get(normalized_client_id)
        if not isinstance(entry, dict):
            raise ServiceError(
                404,
                "console_client_settings_not_found",
                "The requested CocoroConsole client settings do not exist.",
            )
        return self._build_console_client_editor_state(normalized_client_id, entry)

    def get_last_connected_console_client_editor_state(self, token: str | None) -> dict[str, Any]:
        state = self._require_token(token)
        entries = state.get("console_client_settings", {})
        connected_entries = [
            (client_id, entry)
            for client_id, entry in entries.items()
            if isinstance(entry, dict)
            and isinstance(entry.get("last_connected_at"), str)
            and entry["last_connected_at"]
        ]
        if not connected_entries:
            raise ServiceError(
                404,
                "console_client_settings_not_found",
                "No CocoroConsole client has connected.",
            )
        client_id, entry = max(
            connected_entries,
            key=lambda item: (item[1]["last_connected_at"], item[0]),
        )
        return self._build_console_client_editor_state(client_id, entry)

    def replace_console_client_editor_state(
        self,
        token: str | None,
        client_id: str,
        definition: dict[str, Any],
    ) -> dict[str, Any]:
        state = self._require_token(token)
        normalized_client_id = self._validate_console_client_id(client_id)
        entries = state.get("console_client_settings", {})
        entry = entries.get(normalized_client_id)
        if not isinstance(entry, dict):
            raise ServiceError(
                404,
                "console_client_settings_not_found",
                "The requested CocoroConsole client settings do not exist.",
            )
        settings = deepcopy(definition)
        self._validate_console_client_settings(normalized_client_id, settings)
        self._validate_console_avatar_references(state, settings)
        entry["settings"] = settings
        self.store.write_state(state)
        return self._build_console_client_editor_state(normalized_client_id, entry)

    def patch_console_client_settings(
        self,
        token: str | None,
        client_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        state = self._require_token(token)
        normalized_client_id = self._validate_console_client_id(client_id)
        entries = state.get("console_client_settings", {})
        entry = entries.get(normalized_client_id)
        if not isinstance(entry, dict):
            raise ServiceError(
                404,
                "console_client_settings_not_found",
                "The requested CocoroConsole client settings do not exist.",
            )
        supported_fields = {
            "process",
            "display",
            "desktop_capture",
            "avatar_presentations",
            "motion",
        }
        unsupported_fields = sorted(set(payload) - supported_fields)
        if unsupported_fields or not payload:
            raise ServiceError(
                400,
                "unsupported_console_client_settings_fields",
                "CocoroConsole client settings patch must contain supported top-level sections.",
            )
        settings = deepcopy(entry["settings"])
        for field_name, value in payload.items():
            settings[field_name] = deepcopy(value)
        self._validate_console_client_settings(normalized_client_id, settings)
        self._validate_console_avatar_references(state, settings)
        entry["settings"] = settings
        self.store.write_state(state)
        return self._build_console_client_editor_state(normalized_client_id, entry)

    def _validate_console_avatar_references(
        self,
        state: dict[str, Any],
        settings: dict[str, Any],
    ) -> None:
        avatar_ids = set(state["avatars"])
        for presentation in settings["avatar_presentations"]:
            if presentation["avatar_id"] not in avatar_ids:
                raise ServiceError(
                    404,
                    "avatar_not_found",
                    "avatar_presentations contains an unknown avatar_id.",
                )

    def _build_console_client_editor_state(
        self,
        client_id: str,
        entry: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "client_id": client_id,
            "last_connected_at": entry.get("last_connected_at"),
            "settings": deepcopy(entry["settings"]),
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
            "thinking_speech_level",
            "wake_policy",
            "selected_conversation_display_name_id",
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
        if "thinking_speech_level" in payload:
            self._validate_thinking_speech_level(
                payload["thinking_speech_level"]
            )
            state["thinking_speech_level"] = payload[
                "thinking_speech_level"
            ]

        if "wake_policy" in payload:
            self._validate_wake_policy(payload["wake_policy"])
            state["wake_policy"] = payload["wake_policy"]

        if "selected_conversation_display_name_id" in payload:
            selected_display_name_id = payload["selected_conversation_display_name_id"]
            if selected_display_name_id is not None and (
                not isinstance(selected_display_name_id, str)
                or not selected_display_name_id
            ):
                raise ServiceError(
                    400,
                    "invalid_selected_conversation_display_name_id",
                    "selected_conversation_display_name_id must be a non-empty string or null.",
                )
            if (
                selected_display_name_id is not None
                and selected_display_name_id not in state["conversation_display_names"]
            ):
                raise ServiceError(
                    404,
                    "conversation_display_name_not_found",
                    "The selected conversation display name does not exist.",
                )
            state["selected_conversation_display_name_id"] = selected_display_name_id

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
        # 選択中人格が変わると音声起動ワードも変わる。
        if "selected_persona_id" in payload:
            self._reload_audio_runtime_settings()
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

    def list_camera_sources(self, token: str | None) -> dict[str, Any]:
        # 認可
        state = self._require_token(token)
        camera_sources = self._camera_sources_from_state(state)
        return {
            "camera_sources": [
                self._public_camera_source(camera_source)
                for camera_source in sorted(
                    camera_sources.values(),
                    key=lambda item: str(item.get("vision_source_id") or ""),
                )
            ],
        }

    def get_camera_source(self, token: str | None, vision_source_id: str) -> dict[str, Any]:
        # 認可
        state = self._require_token(token)
        camera_source = self._camera_sources_from_state(state).get(vision_source_id)
        if camera_source is None:
            raise ServiceError(404, "camera_source_not_found", "The requested vision_source_id does not exist.")
        return {
            "camera_source": self._public_camera_source(camera_source),
        }

    def delete_camera_source(self, token: str | None, vision_source_id: str) -> dict[str, Any]:
        # 認可
        state = self._require_token(token)
        camera_sources = self._camera_sources_from_state(state)
        if vision_source_id not in camera_sources:
            raise ServiceError(404, "camera_source_not_found", "The requested vision_source_id does not exist.")

        # 削除
        camera_sources.pop(vision_source_id, None)
        self.store.write_state(state)
        return {
            "deleted_vision_source_id": vision_source_id,
        }

    def get_camera_sources_editor_state(self, token: str | None) -> dict[str, Any]:
        # 認可
        state = self._require_token(token)
        self._append_camera_sources_editor_state_audit_event(state=state, operation="read")
        return self._build_camera_sources_editor_state(state)

    def replace_camera_sources_editor_state(self, token: str | None, definition: dict[str, Any]) -> dict[str, Any]:
        # 認可
        state = self._require_token(token)

        # 入力
        camera_sources = self._camera_source_entries_by_id(definition.get("camera_sources"))
        normalized_sources: dict[str, dict[str, Any]] = {}
        for vision_source_id, camera_source in camera_sources.items():
            stored_definition = self._normalize_camera_source_definition(vision_source_id, camera_source)
            self._validate_camera_source_definition(vision_source_id, stored_definition)
            normalized_sources[vision_source_id] = stored_definition
        self._validate_unique_camera_source_watcher_ids(normalized_sources)

        # 永続化
        state["camera_sources"] = normalized_sources
        self.store.write_state(state)
        self._append_camera_sources_editor_state_audit_event(state=state, operation="write")
        return self._build_camera_sources_editor_state(state)

    def list_mcp_servers(self, token: str | None) -> dict[str, Any]:
        # 認可
        state = self._require_token(token)
        mcp_servers = self._mcp_servers_from_state(state)
        return {
            "mcp_servers": [
                self._public_mcp_server(mcp_server)
                for mcp_server in sorted(
                    mcp_servers.values(),
                    key=lambda item: str(item.get("mcp_server_id") or ""),
                )
            ],
        }

    def get_mcp_server(self, token: str | None, mcp_server_id: str) -> dict[str, Any]:
        # 認可
        state = self._require_token(token)
        mcp_server = self._mcp_servers_from_state(state).get(mcp_server_id)
        if mcp_server is None:
            raise ServiceError(404, "mcp_server_not_found", "The requested mcp_server_id does not exist.")
        return {
            "mcp_server": self._public_mcp_server(mcp_server),
        }

    def replace_mcp_server(
        self,
        token: str | None,
        mcp_server_id: str,
        definition: dict[str, Any],
    ) -> dict[str, Any]:
        # 認可
        state = self._require_token(token)
        mcp_servers = self._mcp_servers_from_state(state)

        # 正規化と検証
        stored_definition = self._normalize_mcp_server_definition(mcp_server_id, definition)
        self._validate_mcp_server_definition(mcp_server_id, stored_definition)

        # 永続化
        mcp_servers[mcp_server_id] = deepcopy(stored_definition)
        self.store.write_state(state)
        return {
            "mcp_server": self._public_mcp_server(stored_definition),
        }

    def delete_mcp_server(self, token: str | None, mcp_server_id: str) -> dict[str, Any]:
        # 認可
        state = self._require_token(token)
        mcp_servers = self._mcp_servers_from_state(state)
        if mcp_server_id not in mcp_servers:
            raise ServiceError(404, "mcp_server_not_found", "The requested mcp_server_id does not exist.")

        # 削除
        mcp_servers.pop(mcp_server_id, None)
        self.store.write_state(state)
        return {
            "deleted_mcp_server_id": mcp_server_id,
        }

    def get_mcp_servers_editor_state(self, token: str | None) -> dict[str, Any]:
        # 認可
        state = self._require_token(token)
        self._append_mcp_servers_editor_state_audit_event(state=state, operation="read")
        return self._build_mcp_servers_editor_state(state)

    def replace_mcp_servers_editor_state(self, token: str | None, definition: dict[str, Any]) -> dict[str, Any]:
        # 認可
        state = self._require_token(token)

        # 入力
        mcp_servers = self._mcp_server_entries_by_id(definition.get("mcp_servers"))
        normalized_servers: dict[str, dict[str, Any]] = {}
        for mcp_server_id, mcp_server in mcp_servers.items():
            stored_definition = self._normalize_mcp_server_definition(mcp_server_id, mcp_server)
            self._validate_mcp_server_definition(mcp_server_id, stored_definition)
            normalized_servers[mcp_server_id] = stored_definition

        # 永続化
        state["mcp_servers"] = normalized_servers
        self.store.write_state(state)
        self._append_mcp_servers_editor_state_audit_event(state=state, operation="write")
        return self._build_mcp_servers_editor_state(state)

    def get_connector_runtime_config(self, token: str | None, client_id: str) -> dict[str, Any]:
        # 認可
        state = self._require_token(token)
        if not isinstance(client_id, str) or not client_id.strip():
            raise ServiceError(400, "invalid_connector_client_id", "client_id must be a non-empty string.")
        normalized_client_id = client_id.strip()
        camera_sources = [
            deepcopy(camera_source)
            for camera_source in self._camera_sources_from_state(state).values()
            if camera_source.get("client_id") == normalized_client_id and camera_source.get("enabled") is True
        ]
        mcp_servers = [
            self._mcp_server_definition_for_read(mcp_server)
            for mcp_server in self._mcp_servers_from_state(state).values()
            if mcp_server.get("client_id") == normalized_client_id and mcp_server.get("enabled") is True
        ]
        if not camera_sources and not mcp_servers:
            raise ServiceError(
                404,
                "connector_runtime_config_not_found",
                "The requested connector runtime config does not exist.",
            )
        self._append_connector_runtime_config_audit_event(
            state=state,
            client_id=normalized_client_id,
            camera_source_count=len(camera_sources),
            mcp_server_count=len(mcp_servers),
        )
        return {
            "client_id": normalized_client_id,
            "camera_sources": sorted(
                camera_sources,
                key=lambda item: str(item.get("vision_source_id") or ""),
            ),
            "mcp_servers": sorted(
                mcp_servers,
                key=lambda item: str(item.get("mcp_server_id") or ""),
            ),
        }

    def get_watcher_runtime_config(self, token: str | None, watcher_id: str) -> dict[str, Any]:
        # 認可
        state = self._require_token(token)
        if not isinstance(watcher_id, str) or not watcher_id.strip():
            raise ServiceError(400, "invalid_watcher_id", "watcher_id must be a non-empty string.")
        normalized_watcher_id = watcher_id.strip()
        camera_source = self._camera_source_for_watcher_id(state=state, watcher_id=normalized_watcher_id)
        if camera_source is None:
            raise ServiceError(
                404,
                "watcher_runtime_config_not_found",
                "The requested watcher runtime config does not exist.",
            )
        watcher = deepcopy(camera_source.get("watcher"))
        if not isinstance(watcher, dict):
            raise ServiceError(
                404,
                "watcher_runtime_config_not_found",
                "The requested watcher runtime config does not exist.",
            )
        self._append_watcher_runtime_config_audit_event(
            state=state,
            watcher_id=normalized_watcher_id,
            vision_source_id=str(camera_source.get("vision_source_id") or ""),
        )
        runtime_camera_source = deepcopy(camera_source)
        runtime_camera_source.pop("enabled", None)
        return {
            "watcher_id": normalized_watcher_id,
            "watcher": watcher,
            "camera_source": runtime_camera_source,
            "snapshot_dir": str(self._watcher_snapshot_dir(normalized_watcher_id)),
        }

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
            "thinking_speech_level",
            "wake_policy",
            "selected_conversation_display_name_id",
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
        thinking_speech_level = current.get("thinking_speech_level")
        if selected_persona_id not in personas:
            raise ServiceError(404, "persona_not_found", "The selected_persona_id does not exist in personas.")
        if selected_memory_set_id not in memory_sets:
            raise ServiceError(404, "memory_set_not_found", "The selected_memory_set_id does not exist in memory_sets.")
        if selected_model_preset_id not in model_presets:
            raise ServiceError(404, "model_preset_not_found", "The selected_model_preset_id does not exist in model_presets.")

        # 動作設定検証
        self._validate_thinking_speech_level(thinking_speech_level)
        self._validate_wake_policy(current.get("wake_policy"))
        selected_display_name_id = current.get("selected_conversation_display_name_id")
        if selected_display_name_id is not None and (
            not isinstance(selected_display_name_id, str)
            or not selected_display_name_id
        ):
            raise ServiceError(
                400,
                "invalid_selected_conversation_display_name_id",
                "selected_conversation_display_name_id must be a non-empty string or null.",
            )
        if (
            selected_display_name_id is not None
            and selected_display_name_id not in state["conversation_display_names"]
        ):
            raise ServiceError(
                404,
                "conversation_display_name_not_found",
                "The selected conversation display name does not exist.",
            )

        # 永続化
        state["selected_persona_id"] = selected_persona_id
        state["selected_memory_set_id"] = selected_memory_set_id
        state["selected_model_preset_id"] = selected_model_preset_id
        state["thinking_speech_level"] = thinking_speech_level
        state["wake_policy"] = current["wake_policy"]
        state["selected_conversation_display_name_id"] = selected_display_name_id
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
        # 人格設定の音声起動ワード変更を音声 runtime へ反映する。
        self._reload_audio_runtime_settings()
        self._append_editor_state_audit_event(state=state, operation="write")
        return self._build_editor_state(state)

    def _reload_audio_runtime_settings(self) -> None:
        audio_runtime = getattr(self, "_audio_runtime", None)
        if audio_runtime is not None:
            audio_runtime.reload_settings()

    def _build_settings_snapshot(self, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "selected_persona_id": state["selected_persona_id"],
            "selected_memory_set_id": state["selected_memory_set_id"],
            "wake_policy": deepcopy(state["wake_policy"]),
            "selected_model_preset_id": state["selected_model_preset_id"],
            "thinking_speech_level": state["thinking_speech_level"],
            "selected_conversation_display_name_id": state[
                "selected_conversation_display_name_id"
            ],
        }

    def _build_editor_state(self, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "current": self._build_settings_snapshot(state),
            "personas": [deepcopy(value) for value in state["personas"].values()],
            "memory_sets": [deepcopy(value) for value in state["memory_sets"].values()],
            "model_presets": [deepcopy(value) for value in state["model_presets"].values()],
        }

    def _build_avatar_speech_editor_state(self, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "selected_avatar_id": state["selected_avatar_id"],
            "audio_output_settings": deepcopy(state["audio_output_settings"]),
            "microphone_settings": deepcopy(state["microphone_settings"]),
            "avatars": [
                deepcopy(value)
                for value in sorted(
                    state["avatars"].values(),
                    key=lambda item: str(item.get("avatar_id") or ""),
                )
            ],
        }

    def _build_camera_sources_editor_state(self, state: dict[str, Any]) -> dict[str, Any]:
        camera_sources = self._camera_sources_from_state(state)
        return {
            "camera_sources": [
                deepcopy(value)
                for value in sorted(
                    camera_sources.values(),
                    key=lambda item: str(item.get("vision_source_id") or ""),
                )
            ],
        }

    def _camera_source_for_watcher_id(self, *, state: dict[str, Any], watcher_id: str) -> dict[str, Any] | None:
        for camera_source in self._camera_sources_from_state(state).values():
            if not isinstance(camera_source, dict):
                continue
            watcher = camera_source.get("watcher")
            if isinstance(watcher, dict) and watcher.get("watcher_id") == watcher_id:
                return camera_source
        return None

    def _watcher_snapshot_dir(self, watcher_id: str) -> Path:
        safe_name = "".join(
            character if character.isalnum() or character in {"-", "_"} else "-"
            for character in watcher_id
        ).strip("-")
        if not safe_name:
            safe_name = "watcher"
        return Path(self.store.root_dir) / "wake-references" / safe_name

    def _build_mcp_servers_editor_state(self, state: dict[str, Any]) -> dict[str, Any]:
        mcp_servers = self._mcp_servers_from_state(state)
        return {
            "mcp_servers": [
                self._mcp_server_definition_for_read(value)
                for value in sorted(
                    mcp_servers.values(),
                    key=lambda item: str(item.get("mcp_server_id") or ""),
                )
            ],
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
            # 選択中人格の音声起動ワード更新を音声 runtime へ反映する。
            if entries_key == "personas":
                self._reload_audio_runtime_settings()

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

    def _camera_source_entries_by_id(self, entries: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(entries, list):
            raise ServiceError(400, "invalid_camera_sources", "camera_sources must be an array.")

        result: dict[str, dict[str, Any]] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise ServiceError(400, "invalid_camera_source", "Each camera_source entry must be an object.")
            vision_source_id = self._camera_source_entry_id(entry)
            if vision_source_id in result:
                raise ServiceError(
                    400,
                    "duplicate_camera_source_id",
                    f"{vision_source_id} is duplicated in camera_sources.",
                )
            result[vision_source_id] = entry
        return result

    def _camera_source_entry_id(self, entry: dict[str, Any]) -> str:
        return self._camera_source_id_from_display_name(entry.get("display_name"))

    def _mcp_server_entries_by_id(self, entries: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(entries, list):
            raise ServiceError(400, "invalid_mcp_servers", "mcp_servers must be an array.")

        result: dict[str, dict[str, Any]] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise ServiceError(400, "invalid_mcp_server", "Each mcp_server entry must be an object.")
            mcp_server_id = entry.get("mcp_server_id")
            if not isinstance(mcp_server_id, str) or not mcp_server_id.strip():
                raise ServiceError(400, "invalid_mcp_server_field", "mcp_server.mcp_server_id must be a non-empty string.")
            # 設定正本の mcp_server_id は接頭辞なしの名前。
            normalized = mcp_server_id.strip()
            if normalized in result:
                raise ServiceError(
                    400,
                    "duplicate_mcp_server_id",
                    f"{normalized} is duplicated in mcp_servers.",
                )
            result[normalized] = entry
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

    def _append_avatar_speech_editor_state_audit_event(
        self,
        *,
        state: dict[str, Any],
        operation: str,
    ) -> None:
        # API keyや音声設定本文はauditへ記録しない。
        self.store.append_events(
            events=[
                {
                    "event_id": f"event:config_audit:{uuid.uuid4().hex}",
                    "cycle_id": "config:avatar-speech-editor-state",
                    "memory_set_id": state["selected_memory_set_id"],
                    "kind": f"avatar_speech_editor_state_{operation}",
                    "role": "system",
                    "created_at": self._now_iso(),
                    "selected_persona_id": state["selected_persona_id"],
                    "selected_memory_set_id": state["selected_memory_set_id"],
                    "selected_model_preset_id": state["selected_model_preset_id"],
                    "selected_avatar_id": state["selected_avatar_id"],
                    "avatar_count": len(state["avatars"]),
                }
            ]
        )

    def _append_stt_enabled_audit_event(
        self,
        *,
        state: dict[str, Any],
        enabled: bool,
    ) -> None:
        # 運用トグルの変更だけを audit に残し、秘密値は含めない。
        self.store.append_events(
            events=[
                {
                    "event_id": f"event:config_audit:{uuid.uuid4().hex}",
                    "cycle_id": "config:stt-enabled",
                    "memory_set_id": state["selected_memory_set_id"],
                    "kind": "stt_enabled_write",
                    "role": "system",
                    "created_at": self._now_iso(),
                    "selected_avatar_id": state["selected_avatar_id"],
                    "enabled": enabled,
                }
            ]
        )

    def _append_tts_enabled_audit_event(
        self,
        *,
        state: dict[str, Any],
        enabled: bool,
    ) -> None:
        # 運用トグルの変更だけを audit に残し、秘密値は含めない。
        self.store.append_events(
            events=[
                {
                    "event_id": f"event:config_audit:{uuid.uuid4().hex}",
                    "cycle_id": "config:tts-enabled",
                    "memory_set_id": state["selected_memory_set_id"],
                    "kind": "tts_enabled_write",
                    "role": "system",
                    "created_at": self._now_iso(),
                    "selected_avatar_id": state["selected_avatar_id"],
                    "enabled": enabled,
                }
            ]
        )

    def _publish_audio_runtime_state(self) -> None:
        # TTS など入力 lease を壊さない運用変更のあと、snapshot だけを配る。
        audio_runtime = getattr(self, "_audio_runtime", None)
        if audio_runtime is not None:
            audio_runtime.publish_state(force=True)

    def _append_camera_sources_editor_state_audit_event(self, *, state: dict[str, Any], operation: str) -> None:
        # 秘密値を含む camera source editor-state 本文は audit に残さない。
        self.store.append_events(
            events=[
                {
                    "event_id": f"event:config_audit:{uuid.uuid4().hex}",
                    "cycle_id": "config:camera-sources-editor-state",
                    "memory_set_id": state["selected_memory_set_id"],
                    "kind": f"camera_sources_editor_state_{operation}",
                    "role": "system",
                    "created_at": self._now_iso(),
                    "selected_persona_id": state["selected_persona_id"],
                    "selected_memory_set_id": state["selected_memory_set_id"],
                    "selected_model_preset_id": state["selected_model_preset_id"],
                    "camera_source_count": len(self._camera_sources_from_state(state)),
                }
            ]
        )

    def _append_mcp_servers_editor_state_audit_event(self, *, state: dict[str, Any], operation: str) -> None:
        # 秘密値を含む MCP server editor-state 本文は audit に残さない。
        self.store.append_events(
            events=[
                {
                    "event_id": f"event:config_audit:{uuid.uuid4().hex}",
                    "cycle_id": "config:mcp-servers-editor-state",
                    "memory_set_id": state["selected_memory_set_id"],
                    "kind": f"mcp_servers_editor_state_{operation}",
                    "role": "system",
                    "created_at": self._now_iso(),
                    "selected_persona_id": state["selected_persona_id"],
                    "selected_memory_set_id": state["selected_memory_set_id"],
                    "selected_model_preset_id": state["selected_model_preset_id"],
                    "mcp_server_count": len(self._mcp_servers_from_state(state)),
                }
            ]
        )

    def _append_connector_runtime_config_audit_event(
        self,
        *,
        state: dict[str, Any],
        client_id: str,
        camera_source_count: int,
        mcp_server_count: int,
    ) -> None:
        # 秘密値を含む runtime config 本文は audit に残さない。
        self.store.append_events(
            events=[
                {
                    "event_id": f"event:config_audit:{uuid.uuid4().hex}",
                    "cycle_id": "config:connector-runtime-config",
                    "memory_set_id": state["selected_memory_set_id"],
                    "kind": "connector_runtime_config_read",
                    "role": "system",
                    "created_at": self._now_iso(),
                    "selected_persona_id": state["selected_persona_id"],
                    "selected_memory_set_id": state["selected_memory_set_id"],
                    "selected_model_preset_id": state["selected_model_preset_id"],
                    "client_id": client_id,
                    "camera_source_count": camera_source_count,
                    "mcp_server_count": mcp_server_count,
                }
            ]
        )

    def _append_watcher_runtime_config_audit_event(
        self,
        *,
        state: dict[str, Any],
        watcher_id: str,
        vision_source_id: str,
    ) -> None:
        # 秘密値を含む watcher runtime config 本文は audit に残さない。
        self.store.append_events(
            events=[
                {
                    "event_id": f"event:config_audit:{uuid.uuid4().hex}",
                    "cycle_id": "config:watcher-runtime-config",
                    "memory_set_id": state["selected_memory_set_id"],
                    "kind": "watcher_runtime_config_read",
                    "role": "system",
                    "created_at": self._now_iso(),
                    "selected_persona_id": state["selected_persona_id"],
                    "selected_memory_set_id": state["selected_memory_set_id"],
                    "selected_model_preset_id": state["selected_model_preset_id"],
                    "watcher_id": watcher_id,
                    "vision_source_id": vision_source_id,
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
        # 通常読み取りでは生成モデルの秘密値を有無だけに変換する。
        public_definition = {
            **deepcopy(definition),
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

    def _public_avatar_definition(self, definition: dict[str, Any]) -> dict[str, Any]:
        public_definition = deepcopy(definition)
        stt = public_definition.get("stt")
        if isinstance(stt, dict):
            stt["api_key_present"] = bool(stt.get("api_key"))
            stt.pop("api_key", None)
        tts = public_definition.get("tts")
        if isinstance(tts, dict):
            aivis_cloud = tts.get("aivis_cloud_config")
            if isinstance(aivis_cloud, dict):
                aivis_cloud["api_key_present"] = bool(aivis_cloud.get("api_key"))
                aivis_cloud.pop("api_key", None)
        return public_definition

    def _public_embedding_definition(self, definition: dict[str, Any]) -> dict[str, Any]:
        public_definition = {
            **definition,
            "api_key_present": bool(definition.get("api_key")),
        }
        public_definition.pop("api_key", None)
        return public_definition

    def _camera_sources_from_state(self, state: dict[str, Any]) -> dict[str, dict[str, Any]]:
        camera_sources = state.get("camera_sources")
        if not isinstance(camera_sources, dict):
            state["camera_sources"] = {}
            return state["camera_sources"]
        return camera_sources

    def _validate_unique_camera_source_watcher_ids(self, camera_sources: dict[str, dict[str, Any]]) -> None:
        seen_ids: set[str] = set()
        for camera_source in camera_sources.values():
            watcher = camera_source.get("watcher") if isinstance(camera_source, dict) else None
            if not isinstance(watcher, dict):
                continue
            watcher_id = watcher.get("watcher_id")
            if not isinstance(watcher_id, str) or not watcher_id.strip():
                continue
            normalized_watcher_id = watcher_id.strip()
            if normalized_watcher_id in seen_ids:
                raise ServiceError(
                    400,
                    "duplicate_camera_source_watcher_id",
                    f"{normalized_watcher_id} is duplicated in camera_sources.",
                )
            seen_ids.add(normalized_watcher_id)

    def _mcp_servers_from_state(self, state: dict[str, Any]) -> dict[str, dict[str, Any]]:
        mcp_servers = state.get("mcp_servers")
        if not isinstance(mcp_servers, dict):
            state["mcp_servers"] = {}
            return state["mcp_servers"]
        return mcp_servers

    def _camera_source_is_enabled(self, vision_source_id: str) -> bool:
        state = self.store.read_state()
        camera_sources = state.get("camera_sources")
        if not isinstance(camera_sources, dict):
            return False
        camera_source = camera_sources.get(vision_source_id)
        return isinstance(camera_source, dict) and camera_source.get("enabled") is True

    def _public_camera_source(self, definition: dict[str, Any]) -> dict[str, Any]:
        public_definition = deepcopy(definition)
        connection = public_definition.get("connection")
        if isinstance(connection, dict):
            public_definition["connection"] = {
                "host_present": bool(connection.get("host")),
                "camera_username_present": bool(connection.get("camera_username")),
                "camera_password_present": bool(connection.get("camera_password")),
            }
        return public_definition

    def _public_mcp_server(self, definition: dict[str, Any]) -> dict[str, Any]:
        public_definition = self._mcp_server_definition_for_read(definition)
        env = public_definition.get("env")
        if isinstance(env, dict):
            public_definition["env"] = {
                key: {"value_present": bool(value)}
                for key, value in sorted(env.items())
            }
        return public_definition

    def _mcp_server_definition_for_read(self, definition: dict[str, Any]) -> dict[str, Any]:
        # 省略時の deny-all をすべての read API で明示する。
        readable_definition = deepcopy(definition)
        readable_definition.setdefault("enabled_tools", [])
        return readable_definition

    def _normalize_mcp_server_definition(self, mcp_server_id: str, definition: dict[str, Any]) -> dict[str, Any]:
        normalized = {
            "mcp_server_id": definition.get("mcp_server_id", mcp_server_id),
            "connector_kind": definition.get("connector_kind", MCP_DEFAULT_CONNECTOR_KIND),
            "client_id": definition.get("client_id", MCP_DEFAULT_CLIENT_ID),
            "enabled": definition.get("enabled"),
            "transport": definition.get("transport", "stdio"),
            "command": definition.get("command"),
            "args": definition.get("args", []),
            "cwd": definition.get("cwd"),
            "enabled_tools": definition.get("enabled_tools", []),
            "env": definition.get("env", {}),
        }
        for field_name in ("mcp_server_id", "connector_kind", "client_id", "transport", "command", "cwd"):
            value = normalized.get(field_name)
            if isinstance(value, str):
                normalized[field_name] = value.strip()
        args = normalized.get("args")
        if isinstance(args, list):
            normalized["args"] = [item.strip() if isinstance(item, str) else item for item in args]
        enabled_tools = normalized.get("enabled_tools")
        if isinstance(enabled_tools, list):
            normalized["enabled_tools"] = [
                item.strip() if isinstance(item, str) else item
                for item in enabled_tools
            ]
        env = normalized.get("env")
        if isinstance(env, dict):
            normalized["env"] = {
                key.strip() if isinstance(key, str) else key: value
                for key, value in env.items()
            }
        return normalized

    def _validate_mcp_server_definition(self, mcp_server_id: str, definition: dict[str, Any]) -> None:
        if not isinstance(definition, dict):
            raise ServiceError(400, "invalid_mcp_server", "mcp_server must be an object.")
        if definition.get("mcp_server_id") != mcp_server_id:
            raise ServiceError(400, "mcp_server_id_mismatch", "mcp_server_id must match the path.")
        if not isinstance(mcp_server_id, str) or not mcp_server_id.strip():
            raise ServiceError(400, "invalid_mcp_server_field", "mcp_server.mcp_server_id must be a non-empty string.")
        supported_fields = {
            "mcp_server_id",
            "connector_kind",
            "client_id",
            "enabled",
            "transport",
            "command",
            "args",
            "cwd",
            "enabled_tools",
            "env",
        }
        unsupported_fields = sorted(set(definition.keys()) - supported_fields)
        if unsupported_fields:
            raise ServiceError(
                400,
                "unsupported_mcp_server_field",
                f"mcp_server has unsupported fields: {', '.join(unsupported_fields)}.",
            )
        connector_kind = definition.get("connector_kind")
        if connector_kind not in MCP_CONNECTOR_KINDS:
            raise ServiceError(400, "unsupported_mcp_connector_kind", "mcp_server.connector_kind is not supported.")
        self._validate_mcp_required_text_field(definition, "client_id", "mcp_server.client_id")
        enabled = definition.get("enabled")
        if not isinstance(enabled, bool):
            raise ServiceError(400, "invalid_mcp_server_field", "mcp_server.enabled must be a boolean.")
        transport = definition.get("transport")
        if transport not in MCP_TRANSPORTS:
            raise ServiceError(400, "unsupported_mcp_transport", "mcp_server.transport is not supported.")
        self._validate_mcp_required_text_field(definition, "command", "mcp_server.command")
        args = definition.get("args")
        if not isinstance(args, list):
            raise ServiceError(400, "invalid_mcp_server_field", "mcp_server.args must be an array.")
        for item in args:
            if not isinstance(item, str) or not item.strip():
                raise ServiceError(400, "invalid_mcp_server_field", "mcp_server.args must contain non-empty strings.")
        cwd = definition.get("cwd")
        if cwd is not None and (not isinstance(cwd, str) or not cwd.strip()):
            raise ServiceError(400, "invalid_mcp_server_field", "mcp_server.cwd must be a non-empty string or null.")
        enabled_tools = definition.get("enabled_tools")
        if not isinstance(enabled_tools, list):
            raise ServiceError(400, "invalid_mcp_server_field", "mcp_server.enabled_tools must be an array.")
        seen_tool_names: set[str] = set()
        for tool_name in enabled_tools:
            if not isinstance(tool_name, str) or not tool_name.strip():
                raise ServiceError(
                    400,
                    "invalid_mcp_server_field",
                    "mcp_server.enabled_tools must contain non-empty strings.",
                )
            if tool_name in seen_tool_names:
                raise ServiceError(
                    400,
                    "invalid_mcp_server_field",
                    "mcp_server.enabled_tools must not contain duplicate tool names.",
                )
            seen_tool_names.add(tool_name)
        env = definition.get("env")
        if not isinstance(env, dict):
            raise ServiceError(400, "invalid_mcp_server_field", "mcp_server.env must be an object.")
        for key, value in env.items():
            if not isinstance(key, str) or not key.strip():
                raise ServiceError(400, "invalid_mcp_server_field", "mcp_server.env keys must be non-empty strings.")
            if not isinstance(value, str):
                raise ServiceError(400, "invalid_mcp_server_field", "mcp_server.env values must be strings.")

    def _mcp_tool_is_enabled(self, mcp_server_id: str, tool_name: str) -> bool:
        # tool 実行権限は接続中 catalog ではなく保存済み設定で判定する。
        state = self.store.read_state()
        mcp_server = self._mcp_servers_from_state(state).get(mcp_server_id)
        if not isinstance(mcp_server, dict) or mcp_server.get("enabled") is not True:
            return False
        enabled_tools = mcp_server.get("enabled_tools")
        if not isinstance(enabled_tools, list):
            return False
        return tool_name in enabled_tools

    def _validate_mcp_required_text_field(self, definition: dict[str, Any], key: str, label: str) -> None:
        value = definition.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ServiceError(400, "invalid_mcp_server_field", f"{label} must be a non-empty string.")

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
