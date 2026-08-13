from __future__ import annotations

from copy import deepcopy
from typing import Any

from otomekairo.llm.client import LLMError
from otomekairo.service.capability import CapabilityDispatchError
from otomekairo.service.common import debug_log
from otomekairo.service.inbound_observation import (
    inbound_observation_delay_seconds,
    inbound_observation_from_server,
    inbound_present_mcp_server_ids,
    list_due_inbound_observation_servers,
)

INBOUND_OBSERVATION_CONTENT_LIMIT = 8
INBOUND_OBSERVATION_TEXT_LIMIT = 400
INBOUND_OBSERVATION_STRUCTURED_LIMIT = 1200


class ServiceInputInboundObservationMixin:
    def _inbound_observation_last_attempt_map(self) -> dict[str, str]:
        with self._runtime_state_lock:
            stored = self._wake_runtime_state.setdefault("inbound_observation_last_attempt_at", {})
            if not isinstance(stored, dict):
                stored = {}
                self._wake_runtime_state["inbound_observation_last_attempt_at"] = stored
            return stored

    def _inbound_observation_delay_seconds(self, *, state: dict[str, Any], current_time: str) -> float | None:
        return inbound_observation_delay_seconds(
            mcp_servers=state.get("mcp_servers"),
            last_attempt_at_by_id=self._inbound_observation_last_attempt_map(),
            current_time=current_time,
        )

    def _due_inbound_observation_servers(self, *, state: dict[str, Any], current_time: str) -> list[dict[str, Any]]:
        return list_due_inbound_observation_servers(
            mcp_servers=state.get("mcp_servers"),
            last_attempt_at_by_id=self._inbound_observation_last_attempt_map(),
            current_time=current_time,
        )

    def _mark_inbound_observation_attempted(self, *, mcp_server_id: str, current_time: str) -> None:
        with self._runtime_state_lock:
            attempted = self._wake_runtime_state.setdefault("inbound_observation_last_attempt_at", {})
            if not isinstance(attempted, dict):
                attempted = {}
                self._wake_runtime_state["inbound_observation_last_attempt_at"] = attempted
            attempted[mcp_server_id] = current_time

    def _inbound_present_mcp_server_ids(self, client_context: dict[str, Any] | None) -> list[str]:
        if not isinstance(client_context, dict):
            return []
        present_ids = client_context.get("inbound_present_mcp_server_ids")
        if isinstance(present_ids, list) and present_ids:
            return [
                server_id.strip()
                for server_id in present_ids
                if isinstance(server_id, str) and server_id.strip()
            ]
        return inbound_present_mcp_server_ids(client_context.get("inbound_observations"))

    def _client_context_inbound_observations(self, client_context: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not isinstance(client_context, dict):
            return []
        observations = client_context.get("inbound_observations")
        if not isinstance(observations, list):
            return []
        return [item for item in observations if isinstance(item, dict)]

    def _run_due_inbound_observations(
        self,
        *,
        state: dict[str, Any],
        current_time: str,
    ) -> list[dict[str, Any]]:
        due_servers = self._due_inbound_observation_servers(state=state, current_time=current_time)
        if not due_servers:
            return []
        observations: list[dict[str, Any]] = []
        for server in due_servers:
            mcp_server_id = str(server.get("mcp_server_id") or "").strip()
            if not mcp_server_id:
                continue
            self._mark_inbound_observation_attempted(
                mcp_server_id=mcp_server_id,
                current_time=current_time,
            )
            observations.append(
                self._run_inbound_observation(
                    state=state,
                    server=server,
                    current_time=current_time,
                )
            )
        return observations

    def _run_inbound_observation(
        self,
        *,
        state: dict[str, Any],
        server: dict[str, Any],
        current_time: str,
    ) -> dict[str, Any]:
        mcp_server_id = str(server.get("mcp_server_id") or "").strip()
        observation = inbound_observation_from_server(server)
        tool_name = str(observation.get("tool_name") or "").strip()
        arguments = observation.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        summary = {
            "mcp_server_id": mcp_server_id,
            "tool_name": tool_name,
            "status": "failed",
            "inbound_present": False,
            "observation_summary": None,
            "reason_summary": None,
        }
        if self._event_stream_registry.get_mcp_tool_target(
            mcp_server_id=mcp_server_id,
            tool_name=tool_name,
        ) is None:
            summary["reason_summary"] = "設定した tool が接続中 catalog に無い。"
            debug_log("InboundObservation", f"{mcp_server_id}/{tool_name} skipped missing_catalog")
            return summary
        try:
            capability_response = self._dispatch_capability_request(
                memory_set_id=state["selected_memory_set_id"],
                capability_id="mcp.call_tool",
                input_payload={
                    "mcp_server_id": mcp_server_id,
                    "tool_name": tool_name,
                    "arguments": deepcopy(arguments),
                },
                current_time=current_time,
                goal_summary=f"inbound 観測 {mcp_server_id}/{tool_name}",
                wait_for_response=True,
                component="InboundObservation",
                track_ongoing_action=False,
                skip_pre_send_check=True,
            )
        except (CapabilityDispatchError, ValueError) as exc:
            summary["reason_summary"] = str(exc)
            debug_log(
                "InboundObservation",
                f"{mcp_server_id}/{tool_name} dispatch_failed error={type(exc).__name__}",
                level="WARNING",
            )
            return summary
        if not isinstance(capability_response, dict):
            summary["reason_summary"] = "capability response が空。"
            return summary
        if capability_response.get("is_error") is True or capability_response.get("status") == "failed":
            summary["reason_summary"] = "tool 結果が失敗だった。"
            debug_log("InboundObservation", f"{mcp_server_id}/{tool_name} tool_error")
            return summary
        source_pack = self._inbound_observation_source_pack(
            mcp_server_id=mcp_server_id,
            tool_name=tool_name,
            capability_response=capability_response,
        )
        try:
            judged = self.llm.generate_mcp_inbound_observation(
                model_config=state["model_presets"][state["selected_model_preset_id"]],
                persona_context=self._build_selected_persona_context(
                    state=state,
                    role="mcp_inbound_observation",
                ),
                source_pack=source_pack,
            )
        except LLMError as exc:
            summary["reason_summary"] = str(exc)
            debug_log(
                "InboundObservation",
                f"{mcp_server_id}/{tool_name} judgment_failed error={type(exc).__name__}",
                level="WARNING",
            )
            return summary
        summary["status"] = "observed"
        summary["inbound_present"] = judged.get("inbound_present") is True
        summary["observation_summary"] = judged.get("observation_summary")
        summary["reason_summary"] = judged.get("reason_summary")
        debug_log(
            "InboundObservation",
            f"{mcp_server_id}/{tool_name} present={summary['inbound_present']}",
        )
        return summary

    def _inbound_observation_source_pack(
        self,
        *,
        mcp_server_id: str,
        tool_name: str,
        capability_response: dict[str, Any],
    ) -> dict[str, Any]:
        content = capability_response.get("content")
        compact_content: list[dict[str, Any]] = []
        if isinstance(content, list):
            for item in content[:INBOUND_OBSERVATION_CONTENT_LIMIT]:
                if not isinstance(item, dict):
                    continue
                compact_item: dict[str, Any] = {}
                item_type = item.get("type")
                if isinstance(item_type, str) and item_type.strip():
                    compact_item["type"] = item_type.strip()
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    compact_item["text"] = self._clamp(text.strip(), limit=INBOUND_OBSERVATION_TEXT_LIMIT)
                if compact_item:
                    compact_content.append(compact_item)
        structured = capability_response.get("structured_content")
        compact_structured = None
        if isinstance(structured, dict):
            compact_structured = self._clamp_inbound_structured_content(structured)
        return {
            "mcp_server_id": mcp_server_id,
            "tool_name": tool_name,
            "is_error": capability_response.get("is_error") is True,
            "content": compact_content,
            "structured_content": compact_structured,
        }

    def _clamp_inbound_structured_content(self, value: dict[str, Any]) -> dict[str, Any]:
        compact: dict[str, Any] = {}
        for key, item in list(value.items())[:16]:
            if not isinstance(key, str) or not key.strip():
                continue
            if isinstance(item, str):
                compact[key.strip()] = self._clamp(item.strip(), limit=INBOUND_OBSERVATION_TEXT_LIMIT)
            elif isinstance(item, (int, float, bool)) or item is None:
                compact[key.strip()] = item
            elif isinstance(item, list):
                compact[key.strip()] = item[:INBOUND_OBSERVATION_CONTENT_LIMIT]
            elif isinstance(item, dict):
                compact[key.strip()] = {
                    nested_key: nested_value
                    for nested_key, nested_value in list(item.items())[:8]
                    if isinstance(nested_key, str)
                }
        rendered = str(compact)
        if len(rendered) > INBOUND_OBSERVATION_STRUCTURED_LIMIT:
            return {"summary_text": self._clamp(rendered, limit=INBOUND_OBSERVATION_TEXT_LIMIT)}
        return compact

    def _attach_inbound_observations(
        self,
        *,
        client_context: dict[str, Any],
        observations: list[dict[str, Any]],
        inbound_only: bool,
    ) -> dict[str, Any]:
        present_ids = inbound_present_mcp_server_ids(observations)
        next_context = {
            **client_context,
            "inbound_observations": observations,
            "inbound_present_mcp_server_ids": present_ids,
            "inbound_only": inbound_only is True,
        }
        return next_context
