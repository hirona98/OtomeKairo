from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from otomekairo.llm import LLMError, MockLLMClient
from otomekairo.store import FileStore


# Block: Constants
EVENTS_FILE = "events.jsonl"
RETRIEVAL_RUNS_FILE = "retrieval_runs.jsonl"
CYCLE_SUMMARIES_FILE = "cycle_summaries.jsonl"
CYCLE_TRACES_FILE = "cycle_traces.jsonl"

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
        self.mock_llm = MockLLMClient()

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

        # Block: Response
        return {
            "settings_snapshot": self._build_settings_snapshot(state),
            "selected_persona": state["personas"][state["selected_persona_id"]],
            "selected_memory_set": state["memory_sets"][state["selected_memory_set_id"]],
            "selected_model_preset": selected_preset,
            "selected_model_profile_ids": selected_profile_ids,
        }

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
    def select_persona(self, token: str | None, persona_id: str) -> dict[str, Any]:
        state = self._require_token(token)
        if persona_id not in state["personas"]:
            raise ServiceError(404, "persona_not_found", "The requested persona_id does not exist.")
        state["selected_persona_id"] = persona_id
        self.store.write_state(state)
        return self.get_config(token=state["console_access_token"])

    def select_memory_set(self, token: str | None, memory_set_id: str) -> dict[str, Any]:
        state = self._require_token(token)
        if memory_set_id not in state["memory_sets"]:
            raise ServiceError(404, "memory_set_not_found", "The requested memory_set_id does not exist.")
        state["selected_memory_set_id"] = memory_set_id
        self.store.write_state(state)
        return self.get_config(token=state["console_access_token"])

    def update_wake_policy(self, token: str | None, wake_policy: dict) -> dict[str, Any]:
        state = self._require_token(token)
        self._validate_wake_policy(wake_policy)
        state["wake_policy"] = wake_policy
        self.store.write_state(state)
        return self.get_config(token=state["console_access_token"])

    def select_model_preset(self, token: str | None, model_preset_id: str) -> dict[str, Any]:
        state = self._require_token(token)
        if model_preset_id not in state["model_presets"]:
            raise ServiceError(404, "model_preset_not_found", "The requested model_preset_id does not exist.")
        self._validate_model_preset_definition(state, model_preset_id, state["model_presets"][model_preset_id])
        state["selected_model_preset_id"] = model_preset_id
        self.store.write_state(state)
        return self.get_config(token=state["console_access_token"])

    def replace_model_preset(self, token: str | None, model_preset_id: str, definition: dict) -> dict[str, Any]:
        state = self._require_token(token)
        self._validate_model_preset_definition(state, model_preset_id, definition)
        state["model_presets"][model_preset_id] = definition
        self.store.write_state(state)
        return {
            "model_preset": state["model_presets"][model_preset_id],
        }

    def replace_model_profile(self, token: str | None, model_profile_id: str, definition: dict) -> dict[str, Any]:
        state = self._require_token(token)
        self._validate_model_profile_definition(model_profile_id, definition)
        state["model_profiles"][model_profile_id] = definition
        self.store.write_state(state)
        return {
            "model_profile": state["model_profiles"][model_profile_id],
        }

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
        recent_turns = self._load_recent_turns()
        runtime_summary = self._build_runtime_summary(state)
        settings_snapshot = self._build_settings_snapshot(state)

        try:
            # Block: ModelSelection
            selected_preset = state["model_presets"][state["selected_model_preset_id"]]
            recall_profile = self._profile_for_role(state, selected_preset, "recall_hint_generation")
            decision_profile = self._profile_for_role(state, selected_preset, "decision_generation")
            reply_profile = self._profile_for_role(state, selected_preset, "reply_generation")
            persona = state["personas"][state["selected_persona_id"]]

            # Block: RecallHint
            recall_hint = self.mock_llm.generate_recall_hint(
                profile=recall_profile,
                observation_text=observation_text,
                recent_turns=recent_turns,
                current_time=started_at,
            )

            # Block: RecallPack
            recall_pack = {
                "self_model": [],
                "user_model": [],
                "relationship_model": [],
                "active_topics": [],
                "active_commitments": [],
                "episodic_evidence": [],
                "event_evidence": [],
                "conflicts": [],
                "selected_memory_ids": [],
                "candidate_count": 0,
            }

            # Block: Decision
            decision = self.mock_llm.generate_decision(
                profile=decision_profile,
                observation_text=observation_text,
                recall_hint=recall_hint,
            )

            # Block: Reply
            reply_payload: dict | None = None
            if decision["kind"] == "reply":
                reply_payload = self.mock_llm.generate_reply(
                    profile=reply_profile,
                    persona=persona,
                    observation_text=observation_text,
                    recall_hint=recall_hint,
                    decision=decision,
                )

            # Block: Result
            result_kind = decision["kind"]
            finished_at = self._now_iso()
            self._persist_cycle_success(
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
        records = self.store.read_jsonl(CYCLE_SUMMARIES_FILE)
        selected = list(reversed(records))[:limit]
        return {
            "cycle_summaries": selected,
        }

    def get_cycle_trace(self, token: str | None, cycle_id: str) -> dict[str, Any]:
        # Block: Authorization
        self._require_token(token)

        # Block: FindRecord
        traces = self.store.read_jsonl(CYCLE_TRACES_FILE)
        for trace in traces:
            if trace["cycle_id"] == cycle_id:
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

    def _build_settings_snapshot(self, state: dict) -> dict:
        # Block: Snapshot
        return {
            "selected_persona_id": state["selected_persona_id"],
            "selected_memory_set_id": state["selected_memory_set_id"],
            "wake_policy": state["wake_policy"],
            "selected_model_preset_id": state["selected_model_preset_id"],
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
            profile_id = roles[role_name].get("model_profile_id")
            if not isinstance(profile_id, str) or not profile_id:
                raise ServiceError(400, "invalid_model_role_profile", f"{role_name} requires model_profile_id.")
            profile = state["model_profiles"].get(profile_id)
            if profile is None:
                raise ServiceError(404, "model_profile_not_found", f"{profile_id} does not exist.")
            if profile["kind"] != expected_kind:
                raise ServiceError(400, "model_profile_kind_mismatch", f"{role_name} requires kind={expected_kind}.")

    def _validate_model_profile_definition(self, model_profile_id: str, definition: dict) -> None:
        # Block: Shape
        if definition.get("model_profile_id") != model_profile_id:
            raise ServiceError(400, "model_profile_id_mismatch", "model_profile_id must match the path.")

        kind = definition.get("kind")
        provider = definition.get("provider")
        model_name = definition.get("model_name")
        if kind not in {"generation", "embedding"}:
            raise ServiceError(400, "invalid_model_profile_kind", "kind must be generation or embedding.")
        if not isinstance(provider, str) or not provider:
            raise ServiceError(400, "invalid_model_provider", "provider is required.")
        if not isinstance(model_name, str) or not model_name:
            raise ServiceError(400, "invalid_model_name", "model_name is required.")

        # Block: NonMockProviders
        if provider != "mock":
            if not definition.get("base_url"):
                raise ServiceError(400, "missing_model_base_url", "base_url is required for non-mock providers.")
            auth = definition.get("auth")
            if not isinstance(auth, dict) or not auth:
                raise ServiceError(400, "missing_model_auth", "auth is required for non-mock providers.")

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
    ) -> None:
        # Block: EventRecords
        events = [
            {
                "event_id": f"event:{uuid.uuid4().hex}",
                "cycle_id": cycle_id,
                "kind": "observation",
                "role": "user",
                "text": observation_text,
                "created_at": started_at,
            },
            {
                "event_id": f"event:{uuid.uuid4().hex}",
                "cycle_id": cycle_id,
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
                    "kind": "reply",
                    "role": "assistant",
                    "text": reply_payload["reply_text"],
                    "created_at": finished_at,
                }
            )

        # Block: RetrievalRun
        retrieval_run = {
            "cycle_id": cycle_id,
            "started_at": started_at,
            "finished_at": finished_at,
            "result_status": "succeeded",
            "recall_hint": recall_hint,
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
                "adopted_reason_summary": "No long-term memory candidates were selected in the minimum slice.",
                "rejected_candidate_summary": "No candidates were retrieved in the minimum slice.",
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
        for event in events:
            self.store.append_jsonl(EVENTS_FILE, event)
        self.store.append_jsonl(RETRIEVAL_RUNS_FILE, retrieval_run)
        self.store.append_jsonl(CYCLE_SUMMARIES_FILE, cycle_summary)
        self.store.append_jsonl(CYCLE_TRACES_FILE, cycle_trace)

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
        events = [
            {
                "event_id": f"event:{uuid.uuid4().hex}",
                "cycle_id": cycle_id,
                "kind": "observation",
                "role": "user",
                "text": observation_text,
                "created_at": started_at,
            },
            {
                "event_id": f"event:{uuid.uuid4().hex}",
                "cycle_id": cycle_id,
                "kind": "recall_hint_failure",
                "role": "system",
                "failure_reason": failure_reason,
                "created_at": finished_at,
            },
        ]

        # Block: RetrievalRun
        retrieval_run = {
            "cycle_id": cycle_id,
            "started_at": started_at,
            "finished_at": finished_at,
            "result_status": "failed",
            "failure_reason": failure_reason,
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
        for event in events:
            self.store.append_jsonl(EVENTS_FILE, event)
        self.store.append_jsonl(RETRIEVAL_RUNS_FILE, retrieval_run)
        self.store.append_jsonl(CYCLE_SUMMARIES_FILE, cycle_summary)
        self.store.append_jsonl(CYCLE_TRACES_FILE, cycle_trace)

    def _load_recent_turns(self) -> list[dict]:
        # Block: WindowSetup
        now = datetime.now(UTC)
        threshold = now - timedelta(minutes=3)
        events = self.store.read_jsonl(EVENTS_FILE)

        # Block: Filtering
        turns = []
        for event in events:
            if event.get("kind") not in {"observation", "reply"}:
                continue
            created_at = self._parse_iso(event["created_at"])
            if created_at < threshold:
                continue
            turns.append(
                {
                    "role": event["role"],
                    "text": event["text"],
                    "created_at": event["created_at"],
                }
            )

        # Block: Truncate
        return turns[-6:]

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
