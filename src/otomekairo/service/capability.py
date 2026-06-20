from __future__ import annotations

import re
import threading
import uuid
from copy import deepcopy
from datetime import timedelta
from typing import Any

from otomekairo.capabilities import capability_manifests, capability_readiness_input_digest
from otomekairo.service.common import debug_log


class CapabilityDispatchError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        capability_request_summary: dict[str, Any] | None = None,
        ongoing_action_transition_summary: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.capability_request_summary = capability_request_summary
        self.ongoing_action_transition_summary = ongoing_action_transition_summary


class ServiceCapabilityMixin:
    def recover_capability_runtime_state_after_startup(self) -> None:
        # capability request の照合表は process-local なので、再起動後に結果待ちは成立しない。
        current_time = self._now_iso()
        state = self.store.read_state()
        memory_sets = state.get("memory_sets")
        if not isinstance(memory_sets, dict):
            return

        cleared_actions: list[str] = []
        for memory_set_id in memory_sets:
            if not isinstance(memory_set_id, str) or not memory_set_id.strip():
                continue
            ongoing_action = self.store.get_ongoing_action(
                memory_set_id=memory_set_id,
                current_time=current_time,
            )
            if not isinstance(ongoing_action, dict):
                continue
            if ongoing_action.get("status") != "waiting_result":
                continue
            capability_id = str(ongoing_action.get("last_capability_id") or "").strip()
            if not capability_id:
                continue
            self.store.clear_ongoing_action(memory_set_id=memory_set_id)
            action_id = str(ongoing_action.get("action_id") or "").strip()
            cleared_actions.append(action_id or memory_set_id)

        if cleared_actions:
            debug_log(
                "Capability",
                f"startup cleared orphaned waiting ongoing_actions count={len(cleared_actions)}",
            )

    # LLM の capability_request decision を実行境界へ渡す。
    def _dispatch_decision_capability_request(
        self,
        *,
        state: dict[str, Any],
        current_time: str,
        source_current_input: dict[str, Any],
        assistant_message_target_client_id: str | None,
        decision: dict[str, Any],
    ) -> dict[str, Any]:
        request_payload = decision.get("capability_request")
        if not isinstance(request_payload, dict):
            raise ValueError("Decision capability_request is invalid.")
        capability_id = request_payload.get("capability_id")
        input_payload = request_payload.get("input")
        if not isinstance(capability_id, str) or not capability_id.strip():
            raise ValueError("Decision capability_request.capability_id is invalid.")
        if not isinstance(input_payload, dict):
            raise ValueError("Decision capability_request.input must be an object.")

        result = self._dispatch_capability_request(
            memory_set_id=state["selected_memory_set_id"],
            capability_id=capability_id.strip(),
            input_payload=input_payload,
            current_time=current_time,
            goal_summary=str(decision.get("reason_summary") or "").strip(),
            wait_for_response=False,
            component="Capability",
            source_current_input=source_current_input,
            assistant_message_target_client_id=assistant_message_target_client_id,
        )
        if result is None:
            raise ValueError("Capability request dispatch failed.")
        return result

    def _dispatch_capability_request(
        self,
        *,
        memory_set_id: str,
        capability_id: str,
        input_payload: dict[str, Any],
        current_time: str,
        goal_summary: str,
        wait_for_response: bool,
        component: str,
        source_current_input: dict[str, Any] | None = None,
        assistant_message_target_client_id: str | None = None,
        track_ongoing_action: bool = True,
        autonomous_run_id: str | None = None,
    ) -> dict[str, Any] | None:
        # manifest と input schema を先に確定する。
        manifests = capability_manifests()
        manifest = manifests.get(capability_id)
        if manifest is None:
            raise ValueError(f"Unknown capability: {capability_id}")
        state_policy = self._capability_state_policy(capability_id)
        self._validate_capability_payload(
            payload=input_payload,
            schema=manifest.get("input_schema"),
            label=f"{capability_id} input",
        )
        self._prune_pending_capability_requests(current_time=current_time)
        self._validate_capability_runtime_dispatchable(
            memory_set_id=memory_set_id,
            capability_id=capability_id,
            current_time=current_time,
            state_policy=state_policy,
        )

        # binding と ongoing_action を検証し、内部実行記録を作る。
        try:
            target = self._select_capability_target(
                capability_id=capability_id,
                input_payload=input_payload,
            )
        except ValueError as exc:
            unavailable_reason = "no_binding" if "no_binding" in str(exc) else "unavailable"
            self._mark_capability_runtime_failure(
                capability_id=capability_id,
                current_time=current_time,
                failure_summary=str(exc),
                unavailable_reason=unavailable_reason,
                unavailable_seconds=int(state_policy.get("unavailable_seconds_on_dispatch_failure") or 0),
            )
            raise
        target_client_id = target["target_client_id"]
        vision_source = target.get("vision_source")
        mcp_server = target.get("mcp_server")
        mcp_tool = target.get("mcp_tool")
        timeout_ms = int(manifest.get("timeout_ms") or 0)
        if timeout_ms <= 0:
            raise ValueError(f"Capability timeout_ms is invalid: {capability_id}")

        action_seed = None
        if track_ongoing_action:
            action_seed = self._begin_capability_ongoing_action(
                memory_set_id=memory_set_id,
                capability_id=capability_id,
                manifest=manifest,
                current_time=current_time,
                timeout_ms=timeout_ms,
                goal_summary=goal_summary,
            )
        request_record = self._build_capability_request_record(
            memory_set_id=memory_set_id,
            capability_id=capability_id,
            target_client_id=target_client_id,
            input_payload=input_payload,
            timeout_ms=timeout_ms,
            current_time=current_time,
            manifest=manifest,
            action_seed=action_seed,
            wait_for_response=wait_for_response,
            vision_source=vision_source if isinstance(vision_source, dict) else None,
            mcp_server=mcp_server if isinstance(mcp_server, dict) else None,
            mcp_tool=mcp_tool if isinstance(mcp_tool, dict) else None,
            source_current_input=source_current_input,
            assistant_message_target_client_id=assistant_message_target_client_id,
            autonomous_run_id=autonomous_run_id,
        )
        pending = {
            "event": threading.Event(),
            "response": None,
            "request_record": request_record,
            "wait_for_response": wait_for_response,
        }
        with self._capability_request_lock:
            self._pending_capability_requests[request_record["request_id"]] = pending

        # client へ capability request を配送する。
        sent = self._event_stream_registry.send_to_client(
            target_client_id,
            {
                "event_id": self._next_stream_event_id(),
                "type": self._capability_request_event_type(capability_id),
                "data": self._capability_request_event_data(request_record),
            },
        )
        if not sent:
            with self._capability_request_lock:
                self._pending_capability_requests.pop(request_record["request_id"], None)
            self._clear_capability_runtime_busy(
                capability_id=capability_id,
                request_id=request_record["request_id"],
                action_id=request_record.get("action_id"),
            )
            self._mark_capability_runtime_failure(
                capability_id=capability_id,
                current_time=self._now_iso(),
                failure_summary=self._capability_dispatch_transition_reason_summary(reason_code="dispatch_failed"),
                unavailable_reason="dispatch_failed",
                unavailable_seconds=int(state_policy.get("unavailable_seconds_on_dispatch_failure") or 0),
            )
            transition_summary = self._finish_capability_ongoing_action(
                request_record=request_record,
                current_time=self._now_iso(),
                terminal_kind="interrupted",
                reason_code="dispatch_failed",
                terminal_reason=self._capability_dispatch_transition_reason_summary(
                    reason_code="dispatch_failed",
                ),
                final_step_summary=f"{capability_id} request の送信に失敗した。",
                transition_source="capability_dispatch",
                detail_summary=self._capability_dispatch_transition_detail_summary(
                    capability_id=capability_id,
                    reason_code="dispatch_failed",
                ),
            )
            debug_log(
                component,
                f"capability dispatch failed request={request_record['request_id']} capability={capability_id}",
                level="ERROR",
            )
            raise CapabilityDispatchError(
                f"Capability request dispatch failed: {capability_id}",
                capability_request_summary=self._capability_request_summary(
                    request_record,
                    status="dispatch_failed",
                ),
                ongoing_action_transition_summary=transition_summary,
            )

        debug_log(
            component,
            (
                f"capability dispatched request={request_record['request_id']} "
                f"capability={capability_id} target_client={target_client_id} timeout_ms={timeout_ms}"
            ),
        )
        self._set_capability_runtime_busy(request_record=request_record)
        if not wait_for_response:
            return {
                "request_record": request_record,
                "capability_request_summary": self._capability_request_summary(request_record),
                "ongoing_action_transition_summary": self._capability_ongoing_action_transition_summary(
                    request_record=request_record,
                    current_time=current_time,
                    final_state="waiting_result",
                    reason_summary=self._capability_dispatch_transition_reason_summary(
                        reason_code="request_dispatched",
                    ),
                    reason_code="request_dispatched",
                    transition_source="capability_dispatch",
                ),
            }

        # 同期実行では、その場で result を待つ。
        pending["event"].wait(timeout=(timeout_ms / 1000.0) + 1.0)
        with self._capability_request_lock:
            result = pending["response"]
            self._pending_capability_requests.pop(request_record["request_id"], None)
        if not isinstance(result, dict):
            self._clear_capability_runtime_busy(
                capability_id=capability_id,
                request_id=request_record["request_id"],
                action_id=request_record.get("action_id"),
            )
            self._mark_capability_runtime_failure(
                capability_id=capability_id,
                current_time=self._now_iso(),
                failure_summary=self._capability_dispatch_transition_reason_summary(reason_code="request_timeout"),
                unavailable_reason="request_timeout",
                unavailable_seconds=int(state_policy.get("unavailable_seconds_on_timeout") or 0),
            )
            transition_summary = self._finish_capability_ongoing_action(
                request_record=request_record,
                current_time=self._now_iso(),
                terminal_kind="interrupted",
                reason_code="request_timeout",
                terminal_reason=self._capability_dispatch_transition_reason_summary(
                    reason_code="request_timeout",
                ),
                final_step_summary=f"{capability_id} の結果待ちが timeout した。",
                transition_source="capability_dispatch",
                detail_summary=self._capability_dispatch_transition_detail_summary(
                    capability_id=capability_id,
                    reason_code="request_timeout",
                ),
            )
            debug_log(
                component,
                f"capability timeout request={request_record['request_id']} capability={capability_id}",
                level="WARNING",
            )
            raise CapabilityDispatchError(
                f"Capability request timed out: {capability_id}",
                capability_request_summary=self._capability_request_summary(
                    request_record,
                    status="request_timeout",
                ),
                ongoing_action_transition_summary=transition_summary,
            )
        return result

    def _accept_capability_result(
        self,
        *,
        capability_id: str,
        request_id: str,
        client_id: str,
        result_payload: dict[str, Any],
        current_time: str,
    ) -> dict[str, Any] | None:
        # result endpoint から来た payload を manifest の result schema で検証する。
        manifest = capability_manifests().get(capability_id)
        if manifest is None:
            raise ValueError(f"Unknown capability: {capability_id}")
        self._validate_capability_payload(
            payload=result_payload,
            schema=manifest.get("result_schema"),
            label=f"{capability_id} result",
        )
        self._prune_pending_capability_requests(current_time=current_time)

        # request_id と配送先 client を照合する。
        with self._capability_request_lock:
            pending = self._pending_capability_requests.get(request_id)
            if pending is None:
                return None
            request_record = pending.get("request_record")
            if not isinstance(request_record, dict):
                self._pending_capability_requests.pop(request_id, None)
                return None
            target_client_id = request_record.get("target_client_id")
            if target_client_id != client_id:
                raise ValueError("capability client_id does not match the pending target.")
            if request_record.get("capability_id") != capability_id:
                raise ValueError("capability_id does not match the pending request.")
            self._validate_capability_result_source(
                capability_id=capability_id,
                request_record=request_record,
                result_payload=result_payload,
            )

            self._clear_capability_runtime_busy(
                capability_id=capability_id,
                request_id=request_id,
                action_id=request_record.get("action_id"),
            )
            response = {
                "request_id": request_id,
                "capability_id": capability_id,
                "client_id": client_id,
                **result_payload,
                "request_record": dict(request_record),
            }
            if pending.get("wait_for_response"):
                pending["response"] = response
                pending_event = pending.get("event")
                if hasattr(pending_event, "set"):
                    pending_event.set()
                return response
            self._pending_capability_requests.pop(request_id, None)

        return response

    def _select_capability_target(
        self,
        *,
        capability_id: str,
        input_payload: dict[str, Any],
    ) -> dict[str, Any]:
        if capability_id == "mcp.call_tool":
            return self._select_mcp_call_tool_target(input_payload=input_payload)
        if capability_id == "camera.ptz":
            return self._select_camera_ptz_target(input_payload=input_payload)
        if capability_id != "vision.capture":
            return {
                "target_client_id": self._select_capability_target_client(capability_id=capability_id),
                "vision_source": None,
            }
        vision_source_id = input_payload.get("vision_source_id")
        if not isinstance(vision_source_id, str) or not vision_source_id.strip():
            raise ValueError("Capability is unavailable: vision.capture no_binding vision_source_id")
        vision_source = self._event_stream_registry.get_vision_source(vision_source_id.strip())
        if not isinstance(vision_source, dict):
            raise ValueError(f"Capability is unavailable: vision.capture no_binding {vision_source_id.strip()}")
        if (
            vision_source.get("kind") == "camera"
            and vision_source.get("source_owner") == "self"
            and not self._camera_source_is_enabled(vision_source_id.strip())
        ):
            raise ValueError(f"Capability is unavailable: vision.capture camera_source_disabled {vision_source_id.strip()}")
        client_id = vision_source.get("client_id")
        if not isinstance(client_id, str) or not client_id.strip():
            raise ValueError(f"Capability is unavailable: vision.capture no_binding {vision_source_id.strip()}")
        return {
            "target_client_id": client_id.strip(),
            "vision_source": vision_source,
        }

    def _select_mcp_call_tool_target(self, *, input_payload: dict[str, Any]) -> dict[str, Any]:
        mcp_server_id = input_payload.get("mcp_server_id")
        tool_name = input_payload.get("tool_name")
        arguments = input_payload.get("arguments")
        if not isinstance(mcp_server_id, str) or not mcp_server_id.strip():
            raise ValueError("Capability is unavailable: mcp.call_tool no_binding mcp_server_id")
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError("Capability is unavailable: mcp.call_tool no_binding tool_name")
        if not isinstance(arguments, dict):
            raise ValueError("mcp.call_tool arguments must be an object.")
        normalized_server_id = mcp_server_id.strip()
        normalized_tool_name = tool_name.strip()
        target = self._event_stream_registry.get_mcp_tool_target(
            mcp_server_id=normalized_server_id,
            tool_name=normalized_tool_name,
        )
        if not isinstance(target, dict):
            raise ValueError(f"Capability is unavailable: mcp.call_tool no_binding {normalized_server_id}/{normalized_tool_name}")
        tool = target.get("tool")
        if not isinstance(tool, dict):
            raise ValueError(f"Capability is unavailable: mcp.call_tool no_tool {normalized_server_id}/{normalized_tool_name}")
        input_schema = tool.get("inputSchema")
        if isinstance(input_schema, dict):
            self._validate_capability_schema_value(
                value=arguments,
                schema=input_schema,
                path=f"mcp.call_tool.arguments.{normalized_tool_name}",
            )
        client_id = target.get("client_id")
        if not isinstance(client_id, str) or not client_id.strip():
            raise ValueError(f"Capability is unavailable: mcp.call_tool no_binding {normalized_server_id}/{normalized_tool_name}")
        return {
            "target_client_id": client_id.strip(),
            "vision_source": None,
            "mcp_server": target.get("mcp_server"),
            "mcp_tool": tool,
        }

    def _select_camera_ptz_target(self, *, input_payload: dict[str, Any]) -> dict[str, Any]:
        vision_source_id = input_payload.get("vision_source_id")
        if not isinstance(vision_source_id, str) or not vision_source_id.strip():
            raise ValueError("Capability is unavailable: camera.ptz no_binding vision_source_id")
        normalized_source_id = vision_source_id.strip()
        vision_source = self._event_stream_registry.get_vision_source(normalized_source_id)
        if not isinstance(vision_source, dict):
            raise ValueError(f"Capability is unavailable: camera.ptz no_binding {normalized_source_id}")
        client_id = vision_source.get("client_id")
        if not isinstance(client_id, str) or not client_id.strip():
            raise ValueError(f"Capability is unavailable: camera.ptz no_binding {normalized_source_id}")
        if not self._event_stream_registry.has_capability(client_id.strip(), "camera.ptz"):
            raise ValueError(f"Capability is unavailable: camera.ptz no_binding {normalized_source_id}")
        if vision_source.get("kind") != "camera":
            raise ValueError(f"Capability is unavailable: camera.ptz source_not_camera {normalized_source_id}")
        if vision_source.get("source_owner") != "self":
            raise ValueError(f"Capability is unavailable: camera.ptz source_not_self {normalized_source_id}")
        if not self._camera_source_is_enabled(normalized_source_id):
            raise ValueError(f"Capability is unavailable: camera.ptz camera_source_disabled {normalized_source_id}")
        control = self._camera_ptz_source_control(vision_source)
        if control is None:
            raise ValueError(f"Capability is unavailable: camera.ptz no_supported_control {normalized_source_id}")
        operation = input_payload.get("operation")
        amount = input_payload.get("amount")
        operations = control.get("operations", [])
        amounts = control.get("amounts", [])
        if not isinstance(operation, str) or operation not in operations:
            raise ValueError(f"Capability is unavailable: camera.ptz unsupported_operation {normalized_source_id}")
        if not isinstance(amount, str) or amount not in amounts:
            raise ValueError(f"Capability is unavailable: camera.ptz unsupported_amount {normalized_source_id}")
        return {
            "target_client_id": client_id.strip(),
            "vision_source": vision_source,
        }

    def _camera_ptz_source_control(self, vision_source: dict[str, Any]) -> dict[str, list[str]] | None:
        supported_controls = vision_source.get("supported_controls")
        if not isinstance(supported_controls, dict):
            return None
        control = supported_controls.get("camera.ptz")
        if not isinstance(control, dict):
            return None
        operations = [
            operation
            for operation in control.get("operations", [])
            if isinstance(operation, str) and operation
        ]
        amounts = [
            amount
            for amount in control.get("amounts", [])
            if isinstance(amount, str) and amount
        ]
        if not operations or not amounts:
            return None
        return {
            "operations": operations,
            "amounts": amounts,
        }

    def _select_capability_target_client(self, *, capability_id: str) -> str:
        bindings = self._event_stream_registry.list_capability_bindings()
        accepted_bindings = bindings.get("accepted", {})
        client_ids = accepted_bindings.get(capability_id, []) if isinstance(accepted_bindings, dict) else []
        if len(client_ids) == 1:
            return client_ids[0]
        if not client_ids:
            raise ValueError(f"Capability is unavailable: {capability_id} no_binding")
        raise ValueError(f"Capability target is ambiguous: {capability_id}")

    def _begin_capability_ongoing_action(
        self,
        *,
        memory_set_id: str,
        capability_id: str,
        manifest: dict[str, Any],
        current_time: str,
        timeout_ms: int,
        goal_summary: str,
    ) -> dict[str, Any] | None:
        # state_policy に従い、capability request と ongoing_action を結びつける。
        state_policy = manifest.get("state_policy", {})
        if not isinstance(state_policy, dict) or not state_policy.get("creates_ongoing_action"):
            return None
        existing = self.store.get_ongoing_action(
            memory_set_id=memory_set_id,
            current_time=current_time,
        )
        normalized_goal_summary = goal_summary or str(manifest.get("decision_description") or capability_id).strip()
        step_summary = f"{capability_id} の結果を待機している。"
        expires_at = self._capability_ongoing_action_expires_at(
            current_time=current_time,
            timeout_ms=timeout_ms,
        )
        if isinstance(existing, dict):
            if state_policy.get("blocks_parallel_capability") and existing.get("status") == "waiting_result":
                raise ValueError("Another ongoing_action is already active.")
            action_id = str(existing.get("action_id") or "").strip() or f"ongoing_action:{uuid.uuid4().hex}"
            episode_series_id = (
                str(existing.get("episode_series_id") or "").strip() or f"episode_series:{uuid.uuid4().hex}"
            )
            continued_goal_summary = str(existing.get("goal_summary") or normalized_goal_summary).strip() or normalized_goal_summary
            self.store.upsert_ongoing_action(
                ongoing_action={
                    **existing,
                    "action_id": action_id,
                    "memory_set_id": memory_set_id,
                    "goal_summary": continued_goal_summary,
                    "step_summary": step_summary,
                    "status": "waiting_result",
                    "episode_series_id": episode_series_id,
                    "last_capability_id": capability_id,
                    "updated_at": current_time,
                    "expires_at": expires_at,
                }
            )
            return {
                "action_id": action_id,
                "goal_summary": continued_goal_summary,
                "step_summary": step_summary,
                "episode_series_id": episode_series_id,
                "transition_kind": "continued",
            }

        action_id = f"ongoing_action:{uuid.uuid4().hex}"
        episode_series_id = f"episode_series:{uuid.uuid4().hex}"
        self.store.upsert_ongoing_action(
            ongoing_action={
                "action_id": action_id,
                "memory_set_id": memory_set_id,
                "goal_summary": normalized_goal_summary,
                "step_summary": step_summary,
                "status": "waiting_result",
                "episode_series_id": episode_series_id,
                "last_capability_id": capability_id,
                "updated_at": current_time,
                "expires_at": expires_at,
            }
        )
        return {
            "action_id": action_id,
            "goal_summary": normalized_goal_summary,
            "step_summary": step_summary,
            "episode_series_id": episode_series_id,
            "transition_kind": "started",
        }

    def _activate_capability_ongoing_action(
        self,
        *,
        request_record: Any,
        current_time: str,
        active_step_summary: str,
    ) -> dict[str, Any] | None:
        if not isinstance(request_record, dict):
            return None
        memory_set_id = request_record.get("memory_set_id")
        action_id = request_record.get("action_id")
        if not isinstance(memory_set_id, str) or not memory_set_id.strip():
            return None
        if not isinstance(action_id, str) or not action_id.strip():
            return None

        current_action = self.store.get_ongoing_action(
            memory_set_id=memory_set_id,
            current_time=current_time,
        )
        if not isinstance(current_action, dict) or current_action.get("action_id") != action_id:
            return None

        updated_action = {
            **current_action,
            "status": "active",
            "step_summary": active_step_summary,
            "updated_at": current_time,
        }
        self.store.upsert_ongoing_action(ongoing_action=updated_action)
        return updated_action

    def _finish_capability_ongoing_action(
        self,
        *,
        request_record: Any,
        current_time: str,
        terminal_kind: str,
        reason_code: str | None = None,
        terminal_reason: str,
        final_step_summary: str,
        transition_source: str | None = None,
        decision_kind: str | None = None,
        result_error: bool | None = None,
        detail_summary: str | None = None,
    ) -> dict[str, Any] | None:
        # 別 action を誤って消さないよう action_id が一致するときだけ閉じる。
        if not isinstance(request_record, dict):
            return None
        memory_set_id = request_record.get("memory_set_id")
        action_id = request_record.get("action_id")
        if not isinstance(memory_set_id, str) or not memory_set_id.strip():
            return None
        if not isinstance(action_id, str) or not action_id.strip():
            return None

        current_action = self.store.get_ongoing_action(
            memory_set_id=memory_set_id,
            current_time=current_time,
        )
        if isinstance(current_action, dict) and current_action.get("action_id") == action_id:
            self.store.clear_ongoing_action(memory_set_id=memory_set_id)

        return self._capability_ongoing_action_transition_summary(
            request_record=request_record,
            current_time=current_time,
            final_state=terminal_kind,
            reason_summary=terminal_reason,
            reason_code=reason_code,
            step_summary=final_step_summary,
            transition_source=transition_source,
            decision_kind=decision_kind,
            result_error=result_error,
            detail_summary=detail_summary,
        )

    def _build_capability_request_record(
        self,
        *,
        memory_set_id: str,
        capability_id: str,
        target_client_id: str,
        input_payload: dict[str, Any],
        timeout_ms: int,
        current_time: str,
        manifest: dict[str, Any],
        action_seed: dict[str, Any] | None,
        wait_for_response: bool,
        vision_source: dict[str, Any] | None = None,
        mcp_server: dict[str, Any] | None = None,
        mcp_tool: dict[str, Any] | None = None,
        source_current_input: dict[str, Any] | None = None,
        assistant_message_target_client_id: str | None = None,
        autonomous_run_id: str | None = None,
    ) -> dict[str, Any]:
        request_id = f"{capability_id.replace('.', '_')}_request:{uuid.uuid4().hex}"
        expires_at = self._capability_ongoing_action_expires_at(current_time=current_time, timeout_ms=timeout_ms)
        record = {
            "request_id": request_id,
            "target_client_id": target_client_id,
            "memory_set_id": memory_set_id,
            "capability_id": capability_id,
            "input": deepcopy(input_payload),
            "timeout_ms": timeout_ms,
            "risk_level": manifest.get("risk_level"),
            "created_at": current_time,
            "expires_at": expires_at,
            "action_id": action_seed.get("action_id") if isinstance(action_seed, dict) else None,
            "goal_summary": action_seed.get("goal_summary") if isinstance(action_seed, dict) else None,
            "step_summary": action_seed.get("step_summary") if isinstance(action_seed, dict) else None,
            "episode_series_id": action_seed.get("episode_series_id") if isinstance(action_seed, dict) else None,
            "ongoing_action_transition_kind": (
                action_seed.get("transition_kind") if isinstance(action_seed, dict) else None
            ),
            "wait_for_response": wait_for_response,
        }
        if isinstance(source_current_input, dict):
            record["source_current_input"] = deepcopy(source_current_input)
        normalized_assistant_message_target_client_id = self._normalize_capability_client_id(
            assistant_message_target_client_id
        )
        if normalized_assistant_message_target_client_id is not None:
            record["assistant_message_target_client_id"] = normalized_assistant_message_target_client_id
        if isinstance(autonomous_run_id, str) and autonomous_run_id.strip():
            record["autonomous_run_id"] = autonomous_run_id.strip()
        if capability_id in {"vision.capture", "camera.ptz"} and isinstance(vision_source, dict):
            source_id = vision_source.get("vision_source_id")
            source_kind = vision_source.get("kind")
            source_owner = vision_source.get("source_owner")
            source_label = vision_source.get("label")
            if isinstance(source_id, str) and source_id.strip():
                record["vision_source_id"] = source_id.strip()
            if isinstance(source_kind, str) and source_kind.strip():
                record["source_kind"] = source_kind.strip()
            if isinstance(source_owner, str) and source_owner.strip():
                record["source_owner"] = source_owner.strip()
            if isinstance(source_label, str) and source_label.strip():
                record["source_label"] = source_label.strip()
            record["vision_source"] = deepcopy(vision_source)
        if capability_id == "mcp.call_tool":
            mcp_server_id = input_payload.get("mcp_server_id")
            tool_name = input_payload.get("tool_name")
            if isinstance(mcp_server_id, str) and mcp_server_id.strip():
                record["mcp_server_id"] = mcp_server_id.strip()
            if isinstance(tool_name, str) and tool_name.strip():
                record["tool_name"] = tool_name.strip()
            if isinstance(mcp_server, dict):
                record["mcp_server"] = deepcopy(mcp_server)
            if isinstance(mcp_tool, dict):
                record["mcp_tool"] = deepcopy(mcp_tool)
        return record

    def _capability_request_event_type(self, capability_id: str) -> str:
        return f"{capability_id}_request"

    def _capability_request_event_data(self, request_record: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "request_id": request_record["request_id"],
            "capability_id": request_record["capability_id"],
            "timeout_ms": request_record["timeout_ms"],
        }
        input_payload = request_record.get("input")
        if isinstance(input_payload, dict):
            payload.update(deepcopy(input_payload))
        if request_record.get("capability_id") in {"vision.capture", "camera.ptz"}:
            for source_key in ("source_kind", "source_owner", "source_label"):
                value = request_record.get(source_key)
                if isinstance(value, str) and value.strip():
                    payload[source_key] = value.strip()
        return payload

    def _capability_request_summary(
        self,
        request_record: Any,
        *,
        status: str = "dispatched",
    ) -> dict[str, Any] | None:
        if not isinstance(request_record, dict):
            return None
        capability_id = request_record.get("capability_id")
        summary = {
            "request_id": request_record.get("request_id"),
            "capability_id": capability_id,
            "status": status,
            "timeout_ms": request_record.get("timeout_ms"),
        }
        input_payload = request_record.get("input")
        readiness_digest = (
            capability_readiness_input_digest(capability_id, input_payload)
            if isinstance(capability_id, str)
            else None
        )
        if isinstance(readiness_digest, dict):
            summary["readiness_digest"] = readiness_digest
        if capability_id in {"vision.capture", "camera.ptz"}:
            for source_key in ("vision_source_id", "source_kind", "source_owner", "source_label"):
                value = request_record.get(source_key)
                if isinstance(value, str) and value.strip():
                    summary[source_key] = value.strip()
        if capability_id == "camera.ptz" and isinstance(input_payload, dict):
            for input_key in ("operation", "amount"):
                value = input_payload.get(input_key)
                if isinstance(value, str) and value.strip():
                    summary[input_key] = value.strip()
        if capability_id == "mcp.call_tool":
            for input_key in ("mcp_server_id", "tool_name"):
                value = request_record.get(input_key)
                if isinstance(value, str) and value.strip():
                    summary[input_key] = value.strip()
        source_current_input = request_record.get("source_current_input")
        if isinstance(source_current_input, dict):
            summary["source_current_input"] = deepcopy(source_current_input)
        autonomous_run_id = request_record.get("autonomous_run_id")
        if isinstance(autonomous_run_id, str) and autonomous_run_id.strip():
            summary["autonomous_run_id"] = autonomous_run_id.strip()
        return summary

    def _request_record_assistant_message_target_client_id(self, request_record: Any) -> str | None:
        if not isinstance(request_record, dict):
            return None
        return self._normalize_capability_client_id(request_record.get("assistant_message_target_client_id"))

    def _normalize_capability_client_id(self, value: Any) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        return value.strip()

    def _validate_capability_result_source(
        self,
        *,
        capability_id: str,
        request_record: dict[str, Any],
        result_payload: dict[str, Any],
    ) -> None:
        if capability_id == "mcp.call_tool":
            self._validate_mcp_call_tool_result_source(
                request_record=request_record,
                result_payload=result_payload,
            )
            return
        if capability_id == "camera.ptz":
            self._validate_camera_ptz_result_source(
                request_record=request_record,
                result_payload=result_payload,
            )
            return
        if capability_id != "vision.capture":
            return
        client_context = result_payload.get("client_context")
        if not isinstance(client_context, dict):
            raise ValueError("vision.capture result.client_context must be an object.")
        expected_fields = {
            "vision_source_id": request_record.get("vision_source_id"),
            "source_kind": request_record.get("source_kind"),
            "source_label": request_record.get("source_label"),
        }
        for field_name, expected_value in expected_fields.items():
            if not isinstance(expected_value, str) or not expected_value.strip():
                raise ValueError(f"vision.capture request_record.{field_name} is missing.")
            actual_value = client_context.get(field_name)
            if not isinstance(actual_value, str) or actual_value.strip() != expected_value.strip():
                raise ValueError(f"vision.capture result.client_context.{field_name} does not match the request.")

    def _validate_mcp_call_tool_result_source(
        self,
        *,
        request_record: dict[str, Any],
        result_payload: dict[str, Any],
    ) -> None:
        for field_name in ("mcp_server_id", "tool_name"):
            expected_value = request_record.get(field_name)
            actual_value = result_payload.get(field_name)
            if not isinstance(expected_value, str) or not expected_value.strip():
                raise ValueError(f"mcp.call_tool request_record.{field_name} is missing.")
            if not isinstance(actual_value, str) or actual_value.strip() != expected_value.strip():
                raise ValueError(f"mcp.call_tool result.{field_name} does not match the request.")

    def _validate_camera_ptz_result_source(
        self,
        *,
        request_record: dict[str, Any],
        result_payload: dict[str, Any],
    ) -> None:
        input_payload = request_record.get("input")
        if not isinstance(input_payload, dict):
            raise ValueError("camera.ptz request_record.input is missing.")
        for field_name in ("operation", "amount"):
            expected_value = input_payload.get(field_name)
            actual_value = result_payload.get(field_name)
            if not isinstance(expected_value, str) or not expected_value.strip():
                raise ValueError(f"camera.ptz request_record.input.{field_name} is missing.")
            if not isinstance(actual_value, str) or actual_value.strip() != expected_value.strip():
                raise ValueError(f"camera.ptz result.{field_name} does not match the request.")

        client_context = result_payload.get("client_context")
        if client_context is None:
            return
        if not isinstance(client_context, dict):
            raise ValueError("camera.ptz result.client_context must be an object or null.")
        expected_fields = {
            "vision_source_id": request_record.get("vision_source_id"),
            "source_kind": request_record.get("source_kind"),
            "source_label": request_record.get("source_label"),
        }
        for field_name, expected_value in expected_fields.items():
            if not isinstance(expected_value, str) or not expected_value.strip():
                raise ValueError(f"camera.ptz request_record.{field_name} is missing.")
            actual_value = client_context.get(field_name)
            if not isinstance(actual_value, str) or actual_value.strip() != expected_value.strip():
                raise ValueError(f"camera.ptz result.client_context.{field_name} does not match the request.")

    def _capability_state_policy(self, capability_id: str) -> dict[str, Any]:
        manifest = capability_manifests().get(capability_id, {})
        state_policy = manifest.get("state_policy", {})
        if not isinstance(state_policy, dict):
            return {}
        return state_policy

    def _capability_runtime_state_entry(self, capability_id: str) -> dict[str, Any]:
        with self._runtime_state_lock:
            return self._capability_runtime_state.setdefault(
                capability_id,
                {
                    "paused": False,
                    "busy_request_id": None,
                    "busy_action_id": None,
                    "last_failure_at": None,
                    "last_failure_summary": None,
                    "last_result_at": None,
                    "last_result_summary": None,
                    "unavailable_reason": None,
                    "unavailable_until": None,
                },
            )

    def _capability_runtime_state_snapshot(
        self,
        *,
        capability_id: str,
        current_time: str,
        active_ongoing_action: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._runtime_state_lock:
            entry = dict(self._capability_runtime_state_entry(capability_id))
            unavailable_until = entry.get("unavailable_until")
            if isinstance(unavailable_until, str) and unavailable_until and unavailable_until <= current_time:
                entry["unavailable_until"] = None
                entry["unavailable_reason"] = None
            self._capability_runtime_state[capability_id] = dict(entry)
        if (
            isinstance(active_ongoing_action, dict)
            and active_ongoing_action.get("status") == "waiting_result"
            and active_ongoing_action.get("last_capability_id") == capability_id
        ):
            if not isinstance(entry.get("busy_action_id"), str) or not entry.get("busy_action_id"):
                entry["busy_action_id"] = active_ongoing_action.get("action_id")
        entry["busy"] = bool(entry.get("busy_request_id") or entry.get("busy_action_id"))
        unavailable_until = entry.get("unavailable_until")
        entry["unavailable_active"] = (
            isinstance(unavailable_until, str) and bool(unavailable_until) and unavailable_until > current_time
        )
        return entry

    def _set_capability_runtime_paused(
        self,
        *,
        capability_id: str,
        paused: bool,
    ) -> None:
        with self._runtime_state_lock:
            entry = self._capability_runtime_state_entry(capability_id)
            entry["paused"] = paused

    def _validate_capability_runtime_dispatchable(
        self,
        *,
        memory_set_id: str,
        capability_id: str,
        current_time: str,
        state_policy: dict[str, Any],
    ) -> None:
        dispatch_block = self._capability_runtime_dispatch_block(
            memory_set_id=memory_set_id,
            capability_id=capability_id,
            current_time=current_time,
            state_policy=state_policy,
        )
        if dispatch_block is None:
            return
        reason_code, detail = dispatch_block
        if reason_code == "paused":
            raise ValueError(f"Capability is paused: {capability_id}")
        if reason_code == "temporarily_unavailable":
            raise ValueError(f"Capability is temporarily unavailable: {capability_id} {detail}")
        if reason_code == "busy":
            raise ValueError(f"Capability is busy: {capability_id}")

    def _capability_runtime_dispatch_block(
        self,
        *,
        memory_set_id: str,
        capability_id: str,
        current_time: str,
        state_policy: dict[str, Any],
    ) -> tuple[str, str | None] | None:
        active_ongoing_action = self.store.get_ongoing_action(
            memory_set_id=memory_set_id,
            current_time=current_time,
        )
        runtime_state = self._capability_runtime_state_snapshot(
            capability_id=capability_id,
            current_time=current_time,
            active_ongoing_action=active_ongoing_action,
        )
        if runtime_state.get("paused") is True:
            return ("paused", None)
        if runtime_state.get("unavailable_active") is True:
            unavailable_reason = str(runtime_state.get("unavailable_reason") or "unavailable").strip()
            return ("temporarily_unavailable", unavailable_reason)
        if bool(state_policy.get("blocks_parallel_capability")) and runtime_state.get("busy") is True:
            return ("busy", None)
        return None

    def _set_capability_runtime_busy(self, *, request_record: dict[str, Any]) -> None:
        capability_id = str(request_record.get("capability_id") or "").strip()
        if not capability_id:
            return
        request_id = str(request_record.get("request_id") or "").strip() or None
        action_id = str(request_record.get("action_id") or "").strip() or None
        with self._runtime_state_lock:
            entry = self._capability_runtime_state_entry(capability_id)
            entry["busy_request_id"] = request_id
            entry["busy_action_id"] = action_id

    def _clear_capability_runtime_busy(
        self,
        *,
        capability_id: str,
        request_id: str | None = None,
        action_id: str | None = None,
    ) -> None:
        with self._runtime_state_lock:
            entry = self._capability_runtime_state_entry(capability_id)
            if request_id is None or entry.get("busy_request_id") == request_id:
                entry["busy_request_id"] = None
            if action_id is None or entry.get("busy_action_id") == action_id:
                entry["busy_action_id"] = None

    def _mark_capability_runtime_failure(
        self,
        *,
        capability_id: str,
        current_time: str,
        failure_summary: str,
        unavailable_reason: str | None = None,
        unavailable_seconds: int = 0,
    ) -> None:
        with self._runtime_state_lock:
            entry = self._capability_runtime_state_entry(capability_id)
            entry["last_failure_at"] = current_time
            entry["last_failure_summary"] = failure_summary.strip()
            if unavailable_reason and unavailable_seconds > 0:
                entry["unavailable_reason"] = unavailable_reason
                entry["unavailable_until"] = (
                    self._parse_iso(current_time) + timedelta(seconds=unavailable_seconds)
                ).isoformat()

    def _mark_capability_runtime_success(
        self,
        *,
        capability_id: str,
        current_time: str,
        result_summary: str,
    ) -> None:
        with self._runtime_state_lock:
            entry = self._capability_runtime_state_entry(capability_id)
            entry["last_result_at"] = current_time
            entry["last_result_summary"] = result_summary.strip()
            entry["unavailable_reason"] = None
            entry["unavailable_until"] = None

    def _capability_transition_kind(self, *, final_state: str) -> str:
        if final_state == "waiting_result":
            return "continued"
        if final_state == "on_hold":
            return "on_hold"
        if final_state == "interrupted":
            return "interrupted"
        return "finished"

    def _capability_ongoing_action_transition_summary(
        self,
        *,
        request_record: dict[str, Any],
        current_time: str,
        final_state: str,
        reason_summary: str,
        reason_code: str | None = None,
        step_summary: str | None = None,
        transition_source: str | None = None,
        decision_kind: str | None = None,
        result_error: bool | None = None,
        detail_summary: str | None = None,
    ) -> dict[str, Any] | None:
        action_id = request_record.get("action_id")
        if not isinstance(action_id, str) or not action_id.strip():
            return None
        transition_kind = request_record.get("ongoing_action_transition_kind")
        if transition_kind not in {"started", "continued"}:
            transition_kind = "started"
        transition_sequence = [transition_kind]
        if final_state != "waiting_result":
            transition_sequence.append(final_state)
        payload = {
            "action_id": action_id,
            "transition_sequence": transition_sequence,
            "transition_kind": self._capability_transition_kind(final_state=final_state),
            "final_state": final_state,
            "goal_summary": request_record.get("goal_summary"),
            "step_summary": step_summary or request_record.get("step_summary"),
            "episode_series_id": request_record.get("episode_series_id"),
            "last_capability_id": request_record.get("capability_id"),
            "reason_summary": reason_summary,
            "updated_at": current_time,
        }
        normalized_reason_code = self._capability_transition_reason_code(reason_code)
        if normalized_reason_code is not None:
            payload["reason_code"] = normalized_reason_code
        normalized_source = self._capability_transition_source(transition_source)
        if normalized_source is not None:
            payload["transition_source"] = normalized_source
        if isinstance(decision_kind, str) and decision_kind.strip():
            payload["decision_kind"] = decision_kind.strip()
        if isinstance(result_error, bool):
            payload["result_error"] = result_error
        normalized_detail = self._capability_transition_detail_summary(detail_summary)
        if normalized_detail is not None:
            payload["detail_summary"] = normalized_detail
        return payload

    def _capability_transition_reason_code(self, reason_code: Any) -> str | None:
        if not isinstance(reason_code, str) or not reason_code.strip():
            return None
        return reason_code.strip()

    def _capability_transition_source(self, transition_source: Any) -> str | None:
        if not isinstance(transition_source, str) or not transition_source.strip():
            return None
        return transition_source.strip()

    def _capability_transition_detail_summary(self, detail_summary: Any) -> str | None:
        if not isinstance(detail_summary, str) or not detail_summary.strip():
            return None
        return detail_summary.strip()

    def _capability_dispatch_transition_reason_summary(self, *, reason_code: str) -> str:
        if reason_code == "dispatch_failed":
            return "capability request の配送に失敗し、継続を中断した。"
        if reason_code == "request_timeout":
            return "capability result の待機が timeout し、継続を中断した。"
        return "capability request を配送し、結果待ちに入った。"

    def _capability_dispatch_transition_detail_summary(
        self,
        *,
        capability_id: str,
        reason_code: str,
    ) -> str | None:
        if reason_code == "dispatch_failed":
            return f"{capability_id} request の送信に失敗した。"
        if reason_code == "request_timeout":
            return f"{capability_id} の結果待ちが timeout した。"
        return None

    def _capability_terminal_transition_reason_summary(
        self,
        *,
        reason_code: str,
        result_error: bool,
    ) -> str:
        if reason_code == "followup_pending_intent":
            return "capability result を受け、今は pending_intent へ切り替えて後で再評価する。"
        if reason_code == "followup_speech":
            if result_error:
                return "capability result の error を受け、speech で継続を中断した。"
            return "capability result を受け、speech で継続を完了した。"
        if reason_code == "followup_noop":
            if result_error:
                return "capability result の error を受け、noop で継続を中断した。"
            return "capability result を受け、noop で継続を完了した。"
        if reason_code == "followup_failed":
            return "capability result 後の判断に失敗し、継続を中断した。"
        if reason_code == "result_empty":
            return "capability result が空で、継続を完了した。"
        if reason_code == "result_error":
            return "capability result の error を受け、継続を中断した。"
        return "capability result を受けて継続を完了した。"

    def _capability_ongoing_action_expires_at(self, *, current_time: str, timeout_ms: int) -> str:
        timeout_seconds = max(int(timeout_ms / 1000), 1)
        return (self._parse_iso(current_time) + timedelta(seconds=timeout_seconds + 30)).isoformat()

    def _capability_result_terminal_reason(self, *, capability_id: str, result_payload: dict[str, Any]) -> str:
        error = result_payload.get("error")
        if isinstance(error, str) and error.strip():
            return self._capability_terminal_transition_reason_summary(
                reason_code="result_error",
                result_error=True,
            )
        if capability_id == "vision.capture":
            image_count = result_payload.get("images")
            if isinstance(image_count, list) and not image_count:
                return self._capability_terminal_transition_reason_summary(
                    reason_code="result_empty",
                    result_error=False,
                )
        if capability_id == "camera.ptz":
            status = result_payload.get("status")
            if status in {"rejected", "failed"}:
                return self._capability_terminal_transition_reason_summary(
                    reason_code="result_error",
                    result_error=True,
                )
        if capability_id == "mcp.call_tool":
            if result_payload.get("status") == "failed" or result_payload.get("is_error") is True:
                return self._capability_terminal_transition_reason_summary(
                    reason_code="result_error",
                    result_error=True,
                )
        return self._capability_terminal_transition_reason_summary(
            reason_code="result_received",
            result_error=False,
        )

    def _capability_result_terminal_step_summary(self, *, capability_id: str, result_payload: dict[str, Any]) -> str:
        error = result_payload.get("error")
        if isinstance(error, str) and error.strip():
            return f"{capability_id} が error で終了した。"
        if capability_id == "camera.ptz" and result_payload.get("status") in {"rejected", "failed"}:
            return f"{capability_id} が {result_payload.get('status')} で終了した。"
        if capability_id == "mcp.call_tool" and (
            result_payload.get("status") == "failed" or result_payload.get("is_error") is True
        ):
            return "mcp.call_tool が error で終了した。"
        return f"{capability_id} の結果を受け取った。"

    def _prune_pending_capability_requests(self, *, current_time: str) -> None:
        # 遅延 result を受け続けないよう期限切れ request を破棄する。
        expired: list[dict[str, Any]] = []
        with self._capability_request_lock:
            for request_id, pending in list(self._pending_capability_requests.items()):
                request_record = pending.get("request_record")
                if not isinstance(request_record, dict):
                    self._pending_capability_requests.pop(request_id, None)
                    continue
                expires_at = request_record.get("expires_at")
                if isinstance(expires_at, str) and expires_at > current_time:
                    continue
                self._pending_capability_requests.pop(request_id, None)
                expired.append(dict(request_record))
        for request_record in expired:
            capability_id = str(request_record.get("capability_id") or "").strip()
            if capability_id:
                self._clear_capability_runtime_busy(
                    capability_id=capability_id,
                    request_id=request_record.get("request_id"),
                    action_id=request_record.get("action_id"),
                )
                state_policy = self._capability_state_policy(capability_id)
                self._mark_capability_runtime_failure(
                    capability_id=capability_id,
                    current_time=current_time,
                    failure_summary=self._capability_dispatch_transition_reason_summary(reason_code="request_timeout"),
                    unavailable_reason="request_timeout",
                    unavailable_seconds=int(state_policy.get("unavailable_seconds_on_timeout") or 0),
                )
            self._finish_capability_ongoing_action(
                request_record=request_record,
                current_time=current_time,
                terminal_kind="interrupted",
                reason_code="request_timeout",
                terminal_reason=self._capability_dispatch_transition_reason_summary(
                    reason_code="request_timeout",
                ),
                final_step_summary=f"{request_record.get('capability_id')} の結果待ちが timeout した。",
                transition_source="capability_dispatch",
                detail_summary=self._capability_dispatch_transition_detail_summary(
                    capability_id=str(request_record.get("capability_id") or ""),
                    reason_code="request_timeout",
                ),
            )

    def _validate_capability_payload(self, *, payload: Any, schema: Any, label: str) -> None:
        # 現行 manifest で使う JSON Schema の最小 subset だけを検証する。
        if not isinstance(schema, dict):
            raise ValueError(f"{label} schema is invalid.")
        self._validate_capability_schema_value(value=payload, schema=schema, path=label)

    def _validate_capability_schema_value(self, *, value: Any, schema: dict[str, Any], path: str) -> None:
        expected_type = schema.get("type")
        if expected_type is not None and not self._capability_schema_type_matches(value, expected_type):
            raise ValueError(f"{path} type is invalid.")
        enum_values = schema.get("enum")
        if isinstance(enum_values, list) and value not in enum_values:
            raise ValueError(f"{path} value is not allowed.")
        pattern = schema.get("pattern")
        if isinstance(value, str) and isinstance(pattern, str) and re.search(pattern, value) is None:
            raise ValueError(f"{path} value does not match pattern.")
        if isinstance(value, dict):
            properties = schema.get("properties", {})
            required_names = schema.get("required", [])
            if isinstance(required_names, list):
                for required_name in required_names:
                    if isinstance(required_name, str) and required_name not in value:
                        raise ValueError(f"{path}.{required_name} is required.")
            if schema.get("additionalProperties") is False and isinstance(properties, dict):
                extra_keys = sorted(set(value) - set(properties))
                if extra_keys:
                    raise ValueError(f"{path} has unsupported properties: {', '.join(extra_keys)}")
            if isinstance(properties, dict):
                for key, child_schema in properties.items():
                    if key not in value:
                        continue
                    if isinstance(child_schema, dict):
                        self._validate_capability_schema_value(
                            value=value[key],
                            schema=child_schema,
                            path=f"{path}.{key}",
                        )
        if isinstance(value, list):
            max_items = schema.get("maxItems")
            if max_items is not None:
                if not isinstance(max_items, int) or isinstance(max_items, bool) or max_items < 0:
                    raise ValueError(f"{path} schema maxItems is invalid.")
                if len(value) > max_items:
                    raise ValueError(f"{path} has too many items.")
            item_schema = schema.get("items")
            if isinstance(item_schema, dict):
                for index, item in enumerate(value):
                    self._validate_capability_schema_value(
                        value=item,
                        schema=item_schema,
                        path=f"{path}[{index}]",
                    )

    def _capability_schema_type_matches(self, value: Any, expected_type: Any) -> bool:
        if isinstance(expected_type, list):
            return any(self._capability_schema_type_matches(value, item) for item in expected_type)
        if expected_type == "object":
            return isinstance(value, dict)
        if expected_type == "array":
            return isinstance(value, list)
        if expected_type == "string":
            return isinstance(value, str)
        if expected_type == "null":
            return value is None
        if expected_type == "boolean":
            return isinstance(value, bool)
        if expected_type == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if expected_type == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        return True
