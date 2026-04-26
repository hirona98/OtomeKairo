from __future__ import annotations

from copy import deepcopy
from typing import Any

from otomekairo.capabilities import capability_manifests
from otomekairo.event_stream import ServerWebSocket
from otomekairo.service_common import REQUIRED_MODEL_ROLE_NAMES, ServiceError


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

        # binding 候補
        manifests = capability_manifests()
        seen_at = self._now_iso()
        accepted_capabilities: dict[str, str] = {}
        rejected_bindings: list[dict[str, Any]] = []
        for cap in caps:
            if not isinstance(cap, dict):
                raise ServiceError(400, "invalid_caps", "hello.caps must contain capability objects.")
            capability_id = cap.get("id")
            offered_version = cap.get("version")
            if not isinstance(capability_id, str) or not capability_id.strip():
                raise ServiceError(400, "invalid_caps", "hello.caps[].id must be a non-empty string.")
            if not isinstance(offered_version, str) or not offered_version.strip():
                raise ServiceError(400, "invalid_caps", "hello.caps[].version must be a non-empty string.")

            capability_id = capability_id.strip()
            offered_version = offered_version.strip()
            manifest = manifests.get(capability_id)
            if manifest is None:
                rejected_bindings.append(
                    self._build_rejected_capability_binding(
                        client_id=client_id.strip(),
                        capability_id=capability_id,
                        offered_version=offered_version,
                        rejection_reason="unknown_capability",
                        seen_at=seen_at,
                    )
                )
                continue
            if offered_version != manifest["version"]:
                rejected_bindings.append(
                    self._build_rejected_capability_binding(
                        client_id=client_id.strip(),
                        capability_id=capability_id,
                        offered_version=offered_version,
                        rejection_reason="unsupported_version",
                        seen_at=seen_at,
                    )
                )
                continue
            accepted_capabilities[capability_id] = offered_version

        # 登録
        self._event_stream_registry.register_hello(
            session_id,
            client_id=client_id.strip(),
            capabilities=accepted_capabilities,
            rejected_bindings=rejected_bindings,
        )

    def get_capability_inspection(self, token: str | None) -> dict[str, Any]:
        # 認可
        self._require_token(token)

        # 状態
        manifests = capability_manifests()
        bindings = self._event_stream_registry.list_capability_bindings()
        accepted_bindings = bindings["accepted"]
        rejected_bindings = bindings["rejected"]

        # 応答
        return {
            "generated_at": self._now_iso(),
            "capabilities": [
                self._build_capability_availability(
                    manifest=manifest,
                    bound_client_ids=accepted_bindings.get(capability_id, []),
                    rejected_bindings=rejected_bindings,
                )
                for capability_id, manifest in sorted(manifests.items())
            ],
            "rejected_bindings": rejected_bindings,
        }

    def unregister_event_stream_connection(self, session_id: str) -> None:
        # レジストリ
        self._event_stream_registry.remove_connection(session_id)

    def close_event_streams(self) -> None:
        # レジストリ
        self._event_stream_registry.close_all()

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

    def _bootstrap_state(self, state: dict[str, Any]) -> str:
        # token 発行有無だけを外向き状態にする。
        if state["console_access_token"] is None:
            return "unregistered"
        return "registered"

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

    def _build_rejected_capability_binding(
        self,
        *,
        client_id: str,
        capability_id: str,
        offered_version: str,
        rejection_reason: str,
        seen_at: str,
    ) -> dict[str, Any]:
        return {
            "client_id": client_id,
            "capability_id": capability_id,
            "offered_version": offered_version,
            "rejection_reason": rejection_reason,
            "seen_at": seen_at,
        }

    def _build_capability_availability(
        self,
        *,
        manifest: dict[str, Any],
        bound_client_ids: list[str],
        rejected_bindings: list[dict[str, Any]],
    ) -> dict[str, Any]:
        capability_id = manifest["id"]
        related_rejections = [
            binding
            for binding in rejected_bindings
            if binding.get("capability_id") == capability_id
        ]
        binding_status = "no_binding"
        if bound_client_ids:
            binding_status = "bound"
        elif related_rejections:
            binding_status = "rejected_only"

        required_permissions = list(manifest.get("required_permissions", []))
        missing_permissions: list[str] = []
        available = bool(bound_client_ids) and not missing_permissions
        unavailable_reason = None
        if not available:
            unavailable_reason = "permission_denied" if missing_permissions else "no_binding"

        return {
            "capability_id": capability_id,
            "manifest_version": manifest["version"],
            "kind": manifest["kind"],
            "available": available,
            "unavailable_reason": unavailable_reason,
            "binding": {
                "status": binding_status,
                "eligible_client_count": len(bound_client_ids),
                "bound_client_ids": list(bound_client_ids),
            },
            "permissions": {
                "required": required_permissions,
                "missing": missing_permissions,
            },
            "state": {
                "paused": False,
                "cooldown_until": None,
                "last_failure_at": None,
                "last_failure_summary": None,
            },
        }

    def patch_current(self, token: str | None, payload: dict[str, Any]) -> dict[str, Any]:
        # 状態
        state = self._require_token(token)
        should_clear_runtime_layers = False
        should_clear_drive_states = False

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
        if "desktop_watch" in payload:
            desktop_watch = self._normalize_desktop_watch(payload["desktop_watch"])
            self._validate_desktop_watch(desktop_watch)
            state["desktop_watch"] = desktop_watch

        # 永続化
        self.store.write_state(state)
        if should_clear_runtime_layers:
            self._clear_runtime_state_layers(
                memory_set_ids=list(state["memory_sets"].keys()),
                clear_drive_states=should_clear_drive_states,
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
        desktop_watch = self._normalize_desktop_watch(current.get("desktop_watch"))
        self._validate_desktop_watch(desktop_watch)

        # 永続化
        state["selected_persona_id"] = selected_persona_id
        state["selected_memory_set_id"] = selected_memory_set_id
        state["selected_model_preset_id"] = selected_model_preset_id
        state["wake_policy"] = current["wake_policy"]
        state["desktop_watch"] = desktop_watch
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
            "desktop_watch": self._normalize_desktop_watch(state["desktop_watch"]),
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
        current_time = self._now_iso()
        ongoing_action = self._current_ongoing_action(state=state, current_time=current_time)
        with self._runtime_state_lock:
            memory_job_in_progress = self._memory_postprocess_runtime_state.get("current_cycle_id") is not None
        return {
            "connection_state": "ready",
            "wake_scheduler_active": self._background_wake_scheduler_active() and state["wake_policy"]["mode"] == "interval",
            "ongoing_action_exists": ongoing_action is not None,
            "memory_job_worker_active": self._background_memory_postprocess_worker_active(),
            "pending_memory_job_count": self.store.count_memory_postprocess_jobs(
                result_statuses=["queued", "running"],
            ),
            "memory_job_in_progress": memory_job_in_progress,
        }

    def _list_current_drive_states(
        self,
        *,
        state: dict[str, Any],
        current_time: str | None = None,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        memory_set_id = state["selected_memory_set_id"]
        query_time = current_time or self._now_iso()
        return self.store.list_drive_states(
            memory_set_id=memory_set_id,
            current_time=query_time,
            limit=limit,
        )

    def _summarize_drive_states(self, drive_states: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        summaries: list[dict[str, Any]] = []
        for drive_state in drive_states[:3]:
            if not isinstance(drive_state, dict):
                continue
            summaries.append(
                {
                    "drive_id": drive_state.get("drive_id"),
                    "summary_text": drive_state.get("summary_text"),
                    "salience": drive_state.get("salience"),
                    "related_scope_refs": deepcopy(drive_state.get("related_scope_refs", [])),
                    "supporting_memory_unit_ids": deepcopy(drive_state.get("supporting_memory_unit_ids", [])),
                    "updated_at": drive_state.get("updated_at"),
                    "expires_at": drive_state.get("expires_at"),
                }
            )
        if not summaries:
            return None
        return summaries

    def _current_ongoing_action(
        self,
        *,
        state: dict[str, Any],
        current_time: str | None = None,
    ) -> dict[str, Any] | None:
        memory_set_id = state["selected_memory_set_id"]
        query_time = current_time or self._now_iso()
        return self.store.get_ongoing_action(
            memory_set_id=memory_set_id,
            current_time=query_time,
        )

    def _summarize_ongoing_action(self, ongoing_action: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(ongoing_action, dict):
            return None
        return {
            "action_id": ongoing_action.get("action_id"),
            "goal_summary": ongoing_action.get("goal_summary"),
            "step_summary": ongoing_action.get("step_summary"),
            "status": ongoing_action.get("status"),
            "episode_series_id": ongoing_action.get("episode_series_id"),
            "last_capability_id": ongoing_action.get("last_capability_id"),
            "updated_at": ongoing_action.get("updated_at"),
            "expires_at": ongoing_action.get("expires_at"),
        }

    def _clear_runtime_state_layers(
        self,
        *,
        memory_set_ids: list[str],
        clear_drive_states: bool,
    ) -> None:
        self._clear_pending_intent_candidates()
        for memory_set_id in memory_set_ids:
            self.store.clear_ongoing_action(memory_set_id=memory_set_id)
            if clear_drive_states:
                self.store.clear_drive_states(memory_set_id=memory_set_id)

    def _background_wake_scheduler_active(self) -> bool:
        with self._runtime_state_lock:
            return self._background_wake_thread is not None and self._background_wake_thread.is_alive()

    def _background_memory_postprocess_worker_active(self) -> bool:
        with self._runtime_state_lock:
            return (
                self._background_memory_postprocess_thread is not None
                and self._background_memory_postprocess_thread.is_alive()
            )

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

        allowed_fields = {"mode"}
        if mode == "interval":
            allowed_fields.add("interval_seconds")
            interval_seconds = wake_policy.get("interval_seconds")
            if not isinstance(interval_seconds, int) or interval_seconds < 1:
                raise ServiceError(
                    400,
                    "invalid_wake_policy_interval_seconds",
                    "wake_policy.interval_seconds must be an integer >= 1.",
                )

        unsupported_fields = sorted(set(wake_policy.keys()) - allowed_fields)
        if unsupported_fields:
            raise ServiceError(
                400,
                "unsupported_wake_policy_fields",
                f"wake_policy has unsupported fields: {', '.join(unsupported_fields)}.",
            )

    def _validate_desktop_watch(self, desktop_watch: Any) -> None:
        if not isinstance(desktop_watch, dict):
            raise ServiceError(400, "invalid_desktop_watch", "desktop_watch must be an object.")
        enabled = desktop_watch.get("enabled")
        interval_seconds = desktop_watch.get("interval_seconds")
        if not isinstance(enabled, bool):
            raise ServiceError(400, "invalid_desktop_watch_enabled", "desktop_watch.enabled must be a boolean.")
        if not isinstance(interval_seconds, int) or interval_seconds < 1:
            raise ServiceError(
                400,
                "invalid_desktop_watch_interval_seconds",
                "desktop_watch.interval_seconds must be an integer >= 1.",
            )

    def _normalize_desktop_watch(self, desktop_watch: Any) -> Any:
        if not isinstance(desktop_watch, dict):
            return desktop_watch
        normalized: dict[str, Any] = {}
        enabled = desktop_watch.get("enabled")
        interval_seconds = desktop_watch.get("interval_seconds")
        if isinstance(enabled, bool):
            normalized["enabled"] = enabled
        if isinstance(interval_seconds, int):
            normalized["interval_seconds"] = interval_seconds
        return normalized

    def _validate_persona_definition(self, persona_id: str, definition: dict[str, Any]) -> None:
        if definition.get("persona_id") != persona_id:
            raise ServiceError(400, "persona_id_mismatch", "persona_id must match the path.")
        unsupported_fields = sorted(
            set(definition.keys()) - {"persona_id", "display_name", "persona_prompt", "expression_addon"}
        )
        if unsupported_fields:
            raise ServiceError(
                400,
                "unsupported_persona_field",
                f"{unsupported_fields[0]} is not supported in persona definitions.",
            )
        display_name = definition.get("display_name")
        if not isinstance(display_name, str) or not display_name.strip():
            raise ServiceError(400, "invalid_persona_display_name", "display_name is required.")
        persona_prompt = definition.get("persona_prompt")
        if not isinstance(persona_prompt, str) or not persona_prompt.strip():
            raise ServiceError(400, "invalid_persona_prompt", "persona_prompt is required.")
        expression_addon = definition.get("expression_addon")
        if expression_addon is not None and not isinstance(expression_addon, str):
            raise ServiceError(400, "invalid_expression_addon", "expression_addon must be a string.")

    def _normalize_persona_definition(self, definition: dict[str, Any]) -> dict[str, Any]:
        normalized = {
            **definition,
        }
        for field_name in ("display_name", "persona_prompt", "expression_addon"):
            value = normalized.get(field_name)
            if not isinstance(value, str):
                continue
            normalized[field_name] = value.strip()
        return normalized

    def _validate_memory_set_definition(self, memory_set_id: Any, definition: dict[str, Any]) -> None:
        if not isinstance(memory_set_id, str) or not memory_set_id:
            raise ServiceError(400, "memory_set_id_mismatch", "memory_set_id must match the path.")
        if definition.get("memory_set_id") != memory_set_id:
            raise ServiceError(400, "memory_set_id_mismatch", "memory_set_id must match the path.")
        display_name = definition.get("display_name")
        if not isinstance(display_name, str) or not display_name.strip():
            raise ServiceError(400, "invalid_memory_set_display_name", "display_name is required.")
        self._validate_embedding_definition("memory_set.embedding", definition.get("embedding"))

    def _validate_model_preset_definition(self, model_preset_id: str, definition: dict[str, Any]) -> None:
        if definition.get("model_preset_id") != model_preset_id:
            raise ServiceError(400, "model_preset_id_mismatch", "model_preset_id must match the path.")
        display_name = definition.get("display_name")
        if not isinstance(display_name, str) or not display_name.strip():
            raise ServiceError(400, "invalid_model_preset_display_name", "display_name is required.")
        prompt_window = definition.get("prompt_window")
        self._validate_prompt_window(prompt_window)
        roles = definition.get("roles")
        if not isinstance(roles, dict):
            raise ServiceError(400, "invalid_model_preset_roles", "roles must be an object.")

        for role_name in REQUIRED_MODEL_ROLE_NAMES:
            if role_name not in roles:
                raise ServiceError(400, "missing_model_role", f"{role_name} is required.")
            role_definition = roles[role_name]
            self._validate_model_role_definition(role_name, role_definition)

    def _validate_model_role_definition(self, role_name: str, definition: Any) -> None:
        if not isinstance(definition, dict):
            raise ServiceError(400, "invalid_model_role", f"{role_name} must be an object.")

        model = definition.get("model")
        api_base = definition.get("api_base")
        api_key = definition.get("api_key")
        reasoning_effort = definition.get("reasoning_effort")
        max_output_tokens = definition.get("max_output_tokens")
        web_search_enabled = definition.get("web_search_enabled")

        if not isinstance(model, str) or not model.strip():
            raise ServiceError(400, "invalid_model_role_model", f"{role_name}.model is required.")
        if api_base is not None and not isinstance(api_base, str):
            raise ServiceError(400, "invalid_model_role_api_base", f"{role_name}.api_base must be a string.")
        if not isinstance(api_key, str):
            raise ServiceError(400, "invalid_model_role_api_key", f"{role_name}.api_key must be a string.")
        if reasoning_effort is not None and not isinstance(reasoning_effort, str):
            raise ServiceError(400, "invalid_reasoning_effort", f"{role_name}.reasoning_effort must be a string.")
        if not isinstance(max_output_tokens, int) or max_output_tokens < 1:
            raise ServiceError(
                400,
                "invalid_max_output_tokens",
                f"{role_name}.max_output_tokens must be an integer >= 1.",
            )
        if not isinstance(web_search_enabled, bool):
            raise ServiceError(
                400,
                "invalid_web_search_enabled",
                f"{role_name}.web_search_enabled must be a boolean.",
            )

    def _normalize_model_preset_definition(self, definition: dict[str, Any]) -> dict[str, Any]:
        normalized = {
            **definition,
        }
        display_name = normalized.get("display_name")
        if isinstance(display_name, str):
            normalized["display_name"] = display_name.strip()
        prompt_window = definition.get("prompt_window")
        if isinstance(prompt_window, dict):
            normalized["prompt_window"] = self._normalize_prompt_window(prompt_window)

        roles = definition.get("roles")
        if not isinstance(roles, dict):
            return normalized

        normalized_roles: dict[str, Any] = {}
        for role_name, role_definition in roles.items():
            if not isinstance(role_definition, dict):
                normalized_roles[role_name] = role_definition
                continue
            normalized_role: dict[str, Any] = {}
            for field_name in ("model", "api_base", "api_key"):
                if field_name not in role_definition:
                    continue
                value = role_definition.get(field_name)
                if isinstance(value, str):
                    trimmed_value = value.strip()
                    if field_name == "api_base" and not trimmed_value:
                        continue
                    normalized_role[field_name] = trimmed_value
                else:
                    normalized_role[field_name] = value
            reasoning_effort = role_definition.get("reasoning_effort")
            if isinstance(reasoning_effort, str):
                trimmed_reasoning_effort = reasoning_effort.strip()
                if trimmed_reasoning_effort:
                    normalized_role["reasoning_effort"] = trimmed_reasoning_effort
            max_output_tokens = role_definition.get("max_output_tokens")
            if isinstance(max_output_tokens, int):
                normalized_role["max_output_tokens"] = max_output_tokens
            web_search_enabled = role_definition.get("web_search_enabled")
            if isinstance(web_search_enabled, bool):
                normalized_role["web_search_enabled"] = web_search_enabled
            normalized_roles[role_name] = normalized_role

        normalized["roles"] = normalized_roles
        return normalized

    def _normalize_memory_set_definition(self, definition: dict[str, Any]) -> dict[str, Any]:
        normalized = {
            **definition,
        }
        display_name = normalized.get("display_name")
        if isinstance(display_name, str):
            normalized["display_name"] = display_name.strip()

        embedding = definition.get("embedding")
        if isinstance(embedding, dict):
            normalized["embedding"] = self._normalize_embedding_definition(embedding)
        return normalized

    def _normalize_embedding_definition(self, definition: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        for field_name in ("model", "api_base", "api_key"):
            if field_name not in definition:
                continue
            value = definition.get(field_name)
            if isinstance(value, str):
                trimmed_value = value.strip()
                if field_name == "api_base" and not trimmed_value:
                    continue
                normalized[field_name] = trimmed_value
            else:
                normalized[field_name] = value
        embedding_dimension = definition.get("embedding_dimension")
        if isinstance(embedding_dimension, int):
            normalized["embedding_dimension"] = embedding_dimension
        return normalized

    def _validate_prompt_window(self, prompt_window: Any) -> None:
        if not isinstance(prompt_window, dict):
            raise ServiceError(400, "invalid_prompt_window", "prompt_window must be an object.")

        recent_turn_limit = prompt_window.get("recent_turn_limit")
        recent_turn_minutes = prompt_window.get("recent_turn_minutes")
        if not isinstance(recent_turn_limit, int) or recent_turn_limit < 1:
            raise ServiceError(
                400,
                "invalid_recent_turn_limit",
                "prompt_window.recent_turn_limit must be an integer >= 1.",
            )
        if not isinstance(recent_turn_minutes, int) or recent_turn_minutes < 1:
            raise ServiceError(
                400,
                "invalid_recent_turn_minutes",
                "prompt_window.recent_turn_minutes must be an integer >= 1.",
            )

    def _normalize_prompt_window(self, prompt_window: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        for field_name in ("recent_turn_limit", "recent_turn_minutes"):
            value = prompt_window.get(field_name)
            if isinstance(value, int):
                normalized[field_name] = value
        return normalized

    def _embedding_definition_changed(
        self,
        previous_definition: dict[str, Any] | None,
        current_definition: dict[str, Any],
    ) -> bool:
        if not isinstance(previous_definition, dict):
            return False
        return previous_definition.get("embedding") != current_definition.get("embedding")

    def _validate_embedding_definition(self, field_path: str, definition: Any) -> None:
        if not isinstance(definition, dict):
            raise ServiceError(400, "invalid_embedding_definition", f"{field_path} must be an object.")

        model = definition.get("model")
        api_base = definition.get("api_base")
        api_key = definition.get("api_key")
        embedding_dimension = definition.get("embedding_dimension")

        if not isinstance(model, str) or not model.strip():
            raise ServiceError(400, "invalid_embedding_model", f"{field_path}.model is required.")
        if api_base is not None and not isinstance(api_base, str):
            raise ServiceError(400, "invalid_embedding_api_base", f"{field_path}.api_base must be a string.")
        if not isinstance(api_key, str):
            raise ServiceError(400, "invalid_embedding_api_key", f"{field_path}.api_key must be a string.")
        if not isinstance(embedding_dimension, int) or embedding_dimension < 1:
            raise ServiceError(
                400,
                "invalid_embedding_dimension",
                f"{field_path}.embedding_dimension must be an integer >= 1.",
            )

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
