from __future__ import annotations

import threading
import uuid
from copy import deepcopy
from datetime import timedelta
from typing import Any

from otomekairo.capabilities import capability_manifests
from otomekairo.service_common import debug_log


class ServiceCapabilityMixin:
    # LLM の capability_request decision を実行境界へ渡す。
    def _dispatch_decision_capability_request(
        self,
        *,
        state: dict[str, Any],
        current_time: str,
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
    ) -> dict[str, Any] | None:
        # manifest と input schema を先に確定する。
        manifests = capability_manifests()
        manifest = manifests.get(capability_id)
        if manifest is None:
            raise ValueError(f"Unknown capability: {capability_id}")
        self._validate_capability_payload(
            payload=input_payload,
            schema=manifest.get("input_schema"),
            label=f"{capability_id} input",
        )
        self._prune_pending_capability_requests(current_time=current_time)

        # binding と ongoing_action を検証し、内部実行記録を作る。
        target_client_id = self._select_capability_target_client(capability_id=capability_id)
        timeout_ms = int(manifest.get("timeout_ms") or 0)
        if timeout_ms <= 0:
            raise ValueError(f"Capability timeout_ms is invalid: {capability_id}")

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
            self._finish_capability_ongoing_action(
                request_record=request_record,
                current_time=self._now_iso(),
                terminal_kind="interrupted",
                terminal_reason=f"{capability_id} request を送れなかったため終了した。",
                final_step_summary=f"{capability_id} request の送信に失敗した。",
            )
            debug_log(
                component,
                f"capability dispatch failed request={request_record['request_id']} capability={capability_id}",
            )
            return None

        debug_log(
            component,
            (
                f"capability dispatched request={request_record['request_id']} "
                f"capability={capability_id} target_client={target_client_id} timeout_ms={timeout_ms}"
            ),
        )
        if not wait_for_response:
            return {
                "request_record": request_record,
                "capability_request_summary": self._capability_request_summary(request_record),
                "ongoing_action_transition_summary": self._capability_ongoing_action_transition_summary(
                    request_record=request_record,
                    current_time=current_time,
                    final_state="waiting_result",
                    reason_summary="capability request を配送し、結果待ちに入った。",
                ),
            }

        # desktop_watch の同期観測では、その場で result を待つ。
        pending["event"].wait(timeout=(timeout_ms / 1000.0) + 1.0)
        with self._capability_request_lock:
            result = pending["response"]
            self._pending_capability_requests.pop(request_record["request_id"], None)
        if not isinstance(result, dict):
            self._finish_capability_ongoing_action(
                request_record=request_record,
                current_time=self._now_iso(),
                terminal_kind="interrupted",
                terminal_reason=f"{capability_id} が timeout したため終了した。",
                final_step_summary=f"{capability_id} の結果待ちが timeout した。",
            )
            debug_log(
                component,
                f"capability timeout request={request_record['request_id']} capability={capability_id}",
            )
            return None
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

            response = {
                "request_id": request_id,
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

        # 非同期 result は現行第一段では ongoing_action の終了まで処理する。
        response["ongoing_action_transition_summary"] = self._finish_capability_ongoing_action(
            request_record=request_record,
            current_time=current_time,
            terminal_kind="completed" if result_payload.get("error") in {None, ""} else "interrupted",
            terminal_reason=self._capability_result_terminal_reason(
                capability_id=capability_id,
                result_payload=result_payload,
            ),
            final_step_summary=self._capability_result_terminal_step_summary(
                capability_id=capability_id,
                result_payload=result_payload,
            ),
        )
        return response

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
        if isinstance(existing, dict) and state_policy.get("blocks_parallel_capability"):
            raise ValueError("Another ongoing_action is already active.")

        action_id = f"ongoing_action:{uuid.uuid4().hex}"
        episode_series_id = f"episode_series:{uuid.uuid4().hex}"
        normalized_goal_summary = goal_summary or str(manifest.get("decision_description") or capability_id).strip()
        step_summary = f"{capability_id} の結果を待機している。"
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
                "expires_at": self._capability_ongoing_action_expires_at(
                    current_time=current_time,
                    timeout_ms=timeout_ms,
                ),
            }
        )
        return {
            "action_id": action_id,
            "goal_summary": normalized_goal_summary,
            "step_summary": step_summary,
            "episode_series_id": episode_series_id,
            "transition_kind": "started",
        }

    def _finish_capability_ongoing_action(
        self,
        *,
        request_record: Any,
        current_time: str,
        terminal_kind: str,
        terminal_reason: str,
        final_step_summary: str,
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
            step_summary=final_step_summary,
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
    ) -> dict[str, Any]:
        request_id = f"{capability_id.replace('.', '_')}_request:{uuid.uuid4().hex}"
        expires_at = self._capability_ongoing_action_expires_at(current_time=current_time, timeout_ms=timeout_ms)
        return {
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
        return payload

    def _capability_request_summary(self, request_record: Any) -> dict[str, Any] | None:
        if not isinstance(request_record, dict):
            return None
        return {
            "request_id": request_record.get("request_id"),
            "capability_id": request_record.get("capability_id"),
            "status": "dispatched",
            "timeout_ms": request_record.get("timeout_ms"),
        }

    def _capability_ongoing_action_transition_summary(
        self,
        *,
        request_record: dict[str, Any],
        current_time: str,
        final_state: str,
        reason_summary: str,
        step_summary: str | None = None,
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
        return {
            "action_id": action_id,
            "transition_sequence": transition_sequence,
            "final_state": final_state,
            "goal_summary": request_record.get("goal_summary"),
            "step_summary": step_summary or request_record.get("step_summary"),
            "episode_series_id": request_record.get("episode_series_id"),
            "last_capability_id": request_record.get("capability_id"),
            "reason_summary": reason_summary,
            "updated_at": current_time,
        }

    def _capability_ongoing_action_expires_at(self, *, current_time: str, timeout_ms: int) -> str:
        timeout_seconds = max(int(timeout_ms / 1000), 1)
        return (self._parse_iso(current_time) + timedelta(seconds=timeout_seconds + 30)).isoformat()

    def _capability_result_terminal_reason(self, *, capability_id: str, result_payload: dict[str, Any]) -> str:
        error = result_payload.get("error")
        if isinstance(error, str) and error.strip():
            return f"{capability_id} が error で終了した。 error={error.strip()}"
        return f"{capability_id} の結果を受け取った。"

    def _capability_result_terminal_step_summary(self, *, capability_id: str, result_payload: dict[str, Any]) -> str:
        error = result_payload.get("error")
        if isinstance(error, str) and error.strip():
            return f"{capability_id} が error で終了した。"
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
            self._finish_capability_ongoing_action(
                request_record=request_record,
                current_time=current_time,
                terminal_kind="interrupted",
                terminal_reason=f"{request_record.get('capability_id')} が timeout したため終了した。",
                final_step_summary=f"{request_record.get('capability_id')} の結果待ちが timeout した。",
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
