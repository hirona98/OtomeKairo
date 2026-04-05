from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

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

        if "selected_persona_id" in payload:
            persona_id = payload["selected_persona_id"]
            if persona_id not in state["personas"]:
                raise ServiceError(404, "persona_not_found", "The requested persona_id does not exist.")
            state["selected_persona_id"] = persona_id

        if "selected_memory_set_id" in payload:
            memory_set_id = payload["selected_memory_set_id"]
            if memory_set_id not in state["memory_sets"]:
                raise ServiceError(404, "memory_set_not_found", "The requested memory_set_id does not exist.")
            state["selected_memory_set_id"] = memory_set_id

        if "selected_model_preset_id" in payload:
            model_preset_id = payload["selected_model_preset_id"]
            if model_preset_id not in state["model_presets"]:
                raise ServiceError(404, "model_preset_not_found", "The requested model_preset_id does not exist.")
            self._validate_model_preset_definition(state, model_preset_id, state["model_presets"][model_preset_id])
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

            # Block: Decision
            decision = self.llm.generate_decision(
                profile=decision_profile,
                role_settings=decision_role,
                observation_text=observation_text,
                recall_hint=recall_hint,
            )

            # Block: Reply
            reply_payload: dict | None = None
            if decision["kind"] == "reply":
                reply_payload = self.llm.generate_reply(
                    profile=reply_profile,
                    role_settings=reply_role,
                    persona=persona,
                    observation_text=observation_text,
                    recent_turns=recent_turns,
                    recall_hint=recall_hint,
                    decision=decision,
                )

            # Block: Result
            result_kind = decision["kind"]
            finished_at = self._now_iso()
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
                recall_hint=recall_hint,
                recall_pack=recall_pack,
                decision=decision,
                result_kind=result_kind,
                reply_payload=reply_payload,
            )

            # Block: TurnConsolidation
            try:
                self.memory.consolidate_turn(
                    state=state,
                    cycle_id=cycle_id,
                    finished_at=finished_at,
                    observation_text=observation_text,
                    recall_hint=recall_hint,
                    decision=decision,
                    reply_payload=reply_payload,
                    events=events,
                )
            except Exception:
                pass

            return {
                "cycle_id": cycle_id,
                "result_kind": result_kind,
                "reply": {"text": reply_payload["reply_text"]} if reply_payload else None,
            }
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
            "conflicts": len(recall_pack["conflicts"]),
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
            "wake_scheduler_active": state["wake_policy"]["mode"] == "interval",
            "ongoing_action_exists": False,
        }

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
        decision: dict,
        result_kind: str,
        reply_payload: dict | None,
    ) -> list[dict[str, Any]]:
        # Block: EventRecords
        selected_memory_set_id = state["selected_memory_set_id"]
        events = [
            {
                "event_id": f"event:{uuid.uuid4().hex}",
                "cycle_id": cycle_id,
                "memory_set_id": selected_memory_set_id,
                "kind": "observation",
                "role": "user",
                "text": observation_text,
                "created_at": started_at,
            },
            {
                "event_id": f"event:{uuid.uuid4().hex}",
                "cycle_id": cycle_id,
                "memory_set_id": selected_memory_set_id,
                "kind": "decision",
                "role": "system",
                "result_kind": result_kind,
                "reason_code": decision["reason_code"],
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
            "trigger_kind": "user_message",
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
                "trigger_kind": "user_message",
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
                "primary_candidate_kind": decision["kind"],
            },
            "result_trace": {
                "result_kind": result_kind,
                "reply_summary": self._clamp(reply_payload["reply_text"]) if reply_payload else None,
                "noop_reason_summary": decision["reason_summary"] if result_kind == "noop" else None,
                "future_act_summary": None,
                "internal_failure_summary": None,
                "duration_ms": self._duration_ms(started_at, finished_at),
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
    ) -> None:
        # Block: EventRecords
        selected_memory_set_id = state["selected_memory_set_id"]
        events = [
            {
                "event_id": f"event:{uuid.uuid4().hex}",
                "cycle_id": cycle_id,
                "memory_set_id": selected_memory_set_id,
                "kind": "observation",
                "role": "user",
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
            "trigger_kind": "user_message",
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
                "trigger_kind": "user_message",
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
        }

        # Block: Persist
        self.store.persist_cycle_records(
            events=events,
            retrieval_run=retrieval_run,
            cycle_summary=cycle_summary,
            cycle_trace=cycle_trace,
        )

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
