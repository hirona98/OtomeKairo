from __future__ import annotations

import threading
import uuid
from datetime import timedelta
from typing import Any

from otomekairo.llm import LLMError
from otomekairo.service_common import (
    BACKGROUND_DESKTOP_WATCH_POLL_SECONDS,
    BACKGROUND_WAKE_POLL_SECONDS,
    DESKTOP_WATCH_CAPTURE_TIMEOUT_MS,
    FUTURE_ACT_EXPIRES_HOURS,
    FUTURE_ACT_NOT_BEFORE_MINUTES,
    WAKE_REPLY_COOLDOWN_MINUTES,
    ServiceError,
)


# SpontaneousMixin
class ServiceSpontaneousMixin:
    def observe_wake(self, token: str | None, payload: dict[str, Any]) -> dict[str, Any]:
        # Authorization
        state = self._require_token(token)

        # ClientContext
        client_context = payload.get("client_context", {})
        if not isinstance(client_context, dict):
            raise ServiceError(400, "invalid_client_context", "The client_context field must be an object.")

        # Execute
        return self._execute_wake_cycle(
            state=state,
            client_context=client_context,
            trigger_kind="wake",
        )

    def submit_vision_capture_response(self, token: str | None, payload: dict[str, Any]) -> dict[str, Any]:
        # Authorization
        self._require_token(token)

        # Fields
        request_id = payload.get("request_id")
        client_id = payload.get("client_id")
        images = payload.get("images", [])
        client_context = payload.get("client_context")
        error = payload.get("error")

        # Validation
        if not isinstance(request_id, str) or not request_id.strip():
            raise ServiceError(400, "invalid_request_id", "request_id must be a non-empty string.")
        if not isinstance(client_id, str) or not client_id.strip():
            raise ServiceError(400, "invalid_client_id", "client_id must be a non-empty string.")
        if not isinstance(images, list):
            raise ServiceError(400, "invalid_images", "images must be an array.")
        if client_context is not None and not isinstance(client_context, dict):
            raise ServiceError(400, "invalid_client_context", "client_context must be an object.")
        if error is not None and not isinstance(error, str):
            raise ServiceError(400, "invalid_capture_error", "error must be a string or null.")

        # ImageValidation
        normalized_images: list[str] = []
        for image in images:
            if not isinstance(image, str) or not image.strip():
                raise ServiceError(400, "invalid_images", "images must contain non-empty strings.")
            normalized_images.append(image.strip())

        # StoreResponse
        normalized_request_id = request_id.strip()
        normalized_client_id = client_id.strip()
        with self._vision_capture_lock:
            pending = self._pending_vision_capture_requests.get(normalized_request_id)
            if pending is None:
                return {}
            if pending.get("target_client_id") != normalized_client_id:
                raise ServiceError(
                    409,
                    "capture_client_id_mismatch",
                    "client_id does not match the pending capture target.",
                )
            pending["response"] = {
                "request_id": normalized_request_id,
                "client_id": normalized_client_id,
                "images": normalized_images,
                "client_context": client_context or {},
                "error": error.strip() if isinstance(error, str) and error.strip() else None,
            }
            pending["event"].set()

        # Result
        return {}

    def _execute_wake_cycle(
        self,
        *,
        state: dict[str, Any],
        client_context: dict[str, Any],
        trigger_kind: str,
    ) -> dict[str, Any]:
        # SerializedExecution
        with self._wake_execution_lock:
            cycle_id = self._new_cycle_id()
            started_at = self._now_iso()
            recent_turns = self._load_recent_turns(state)
            runtime_summary = self._build_runtime_summary(state)
            settings_snapshot = self._build_settings_snapshot(state)
            observation_text = self._build_wake_observation_text(
                client_context=client_context,
                selected_candidate=None,
            )

            try:
                # Pipeline
                selected_candidate = self._select_due_future_act_candidate(
                    memory_set_id=state["selected_memory_set_id"],
                    current_time=started_at,
                )
                pipeline, observation_text = self._run_wake_pipeline(
                    state=state,
                    started_at=started_at,
                    client_context=client_context,
                    recent_turns=recent_turns,
                    selected_candidate=selected_candidate,
                )

                # Success
                response = self._complete_observation_success(
                    cycle_id=cycle_id,
                    started_at=started_at,
                    state=state,
                    settings_snapshot=settings_snapshot,
                    runtime_summary=runtime_summary,
                    observation_text=observation_text,
                    client_context=client_context,
                    recent_turns=recent_turns,
                    pipeline=pipeline,
                    trigger_kind=trigger_kind,
                    observation_event_kind="wake",
                    observation_event_role="system",
                    consolidate_memory=False,
                )

                # PostReply
                self._record_wake_outcome(
                    current_time=started_at,
                    decision=pipeline["decision"],
                    selected_candidate=selected_candidate,
                )
                return response
            except (LLMError, KeyError, ValueError) as exc:
                # FailurePersistence
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
                    trigger_kind=trigger_kind,
                    observation_event_kind="wake",
                    observation_event_role="system",
                )
                self._emit_observation_failure_logs(
                    cycle_id=cycle_id,
                    trigger_kind=trigger_kind,
                    observation_text=observation_text,
                    failure_reason=str(exc),
                )
                return {
                    "cycle_id": cycle_id,
                    "result_kind": "internal_failure",
                    "reply": None,
                }

    def _background_wake_loop(self, stop_event: threading.Event) -> None:
        # Loop
        while not stop_event.is_set():
            try:
                state = self.store.read_state()
                delay_seconds = self._background_wake_delay_seconds(state=state, current_time=self._now_iso())
                if delay_seconds > 0:
                    stop_event.wait(timeout=delay_seconds)
                    continue
                self._execute_wake_cycle(
                    state=state,
                    client_context={"source": "background_wake_scheduler"},
                    trigger_kind="wake",
                )
            except Exception:  # noqa: BLE001
                stop_event.wait(timeout=BACKGROUND_WAKE_POLL_SECONDS)

    def _background_wake_delay_seconds(self, *, state: dict[str, Any], current_time: str) -> float:
        # Disabled
        wake_policy = state.get("wake_policy", {})
        if wake_policy.get("mode") != "interval":
            return BACKGROUND_WAKE_POLL_SECONDS

        # FirstWake
        with self._runtime_state_lock:
            last_wake_at = self._wake_runtime_state.get("last_wake_at")
        if not isinstance(last_wake_at, str) or not last_wake_at:
            return 0.0

        # Remaining
        interval_minutes = int(wake_policy.get("interval_minutes", 1))
        current_dt = self._parse_iso(current_time)
        due_at = self._parse_iso(last_wake_at) + timedelta(minutes=interval_minutes)
        remaining_seconds = (due_at - current_dt).total_seconds()
        if remaining_seconds <= 0:
            return 0.0

        # PollCap
        return min(remaining_seconds, BACKGROUND_WAKE_POLL_SECONDS)

    def _background_desktop_watch_loop(self, stop_event: threading.Event) -> None:
        # Loop
        while not stop_event.is_set():
            try:
                state = self.store.read_state()
                delay_seconds = self._background_desktop_watch_delay_seconds(
                    state=state,
                    current_time=self._now_iso(),
                )
                if delay_seconds > 0:
                    stop_event.wait(timeout=delay_seconds)
                    continue
                self._execute_desktop_watch_cycle(state=state)
            except Exception:  # noqa: BLE001
                stop_event.wait(timeout=BACKGROUND_DESKTOP_WATCH_POLL_SECONDS)

    def _background_desktop_watch_delay_seconds(
        self,
        *,
        state: dict[str, Any],
        current_time: str,
    ) -> float:
        # Config
        desktop_watch = state.get("desktop_watch", {})
        target_client_id = desktop_watch.get("target_client_id")
        if not isinstance(desktop_watch, dict) or not desktop_watch.get("enabled"):
            return BACKGROUND_DESKTOP_WATCH_POLL_SECONDS
        if not isinstance(target_client_id, str) or not target_client_id.strip():
            return BACKGROUND_DESKTOP_WATCH_POLL_SECONDS
        if not self._event_stream_registry.has_capability(target_client_id.strip(), "vision.desktop"):
            return BACKGROUND_DESKTOP_WATCH_POLL_SECONDS

        # FirstWatch
        with self._runtime_state_lock:
            last_watch_at = self._desktop_watch_runtime_state.get("last_watch_at")
        if not isinstance(last_watch_at, str) or not last_watch_at:
            return 0.0

        # Remaining
        interval_seconds = int(desktop_watch.get("interval_seconds", 1))
        current_dt = self._parse_iso(current_time)
        due_at = self._parse_iso(last_watch_at) + timedelta(seconds=interval_seconds)
        remaining_seconds = (due_at - current_dt).total_seconds()
        if remaining_seconds <= 0:
            return 0.0

        # PollCap
        return min(remaining_seconds, BACKGROUND_DESKTOP_WATCH_POLL_SECONDS)

    def _execute_desktop_watch_cycle(self, *, state: dict[str, Any]) -> None:
        # SerializedExecution
        with self._desktop_watch_execution_lock:
            desktop_watch = state.get("desktop_watch", {})
            target_client_id = desktop_watch.get("target_client_id")
            if not isinstance(target_client_id, str) or not target_client_id.strip():
                return
            target_client_id = target_client_id.strip()
            if not self._event_stream_registry.has_capability(target_client_id, "vision.desktop"):
                return

            # Timestamp
            started_at = self._now_iso()

            # Capture
            capture_response = self._request_desktop_watch_capture(target_client_id=target_client_id)
            if capture_response is None:
                return
            if not capture_response["images"]:
                return

            # SuccessTimestamp
            self._set_last_desktop_watch_at(self._now_iso())

            # Observation
            selected_candidate = self._select_due_future_act_candidate(
                memory_set_id=state["selected_memory_set_id"],
                current_time=started_at,
            )
            client_context = self._build_desktop_watch_client_context(capture_response)
            observation_text = self._build_desktop_watch_observation_text(
                client_context=client_context,
                selected_candidate=selected_candidate,
            )

            # Snapshot
            cycle_id = self._new_cycle_id()
            recent_turns = self._load_recent_turns(state)
            runtime_summary = self._build_runtime_summary(state)
            settings_snapshot = self._build_settings_snapshot(state)

            try:
                # Pipeline
                pipeline = self._run_observation_pipeline(
                    state=state,
                    started_at=started_at,
                    observation_text=observation_text,
                    recent_turns=recent_turns,
                )

                # Success
                self._complete_observation_success(
                    cycle_id=cycle_id,
                    started_at=started_at,
                    state=state,
                    settings_snapshot=settings_snapshot,
                    runtime_summary=runtime_summary,
                    observation_text=observation_text,
                    client_context=client_context,
                    recent_turns=recent_turns,
                    pipeline=pipeline,
                    trigger_kind="desktop_watch",
                    observation_event_kind="desktop_watch",
                    observation_event_role="system",
                    consolidate_memory=False,
                )
                self._record_wake_outcome(
                    current_time=started_at,
                    decision=pipeline["decision"],
                    selected_candidate=selected_candidate,
                )
                self._emit_desktop_watch_reply_event(
                    capture_response=capture_response,
                    pipeline=pipeline,
                )
            except (LLMError, KeyError, ValueError) as exc:
                # Failure
                self._persist_cycle_failure(
                    cycle_id=cycle_id,
                    started_at=started_at,
                    finished_at=self._now_iso(),
                    state=state,
                    settings_snapshot=settings_snapshot,
                    runtime_summary=runtime_summary,
                    observation_text=observation_text,
                    client_context=client_context,
                    failure_reason=str(exc),
                    trigger_kind="desktop_watch",
                    observation_event_kind="desktop_watch",
                    observation_event_role="system",
                )
                self._emit_observation_failure_logs(
                    cycle_id=cycle_id,
                    trigger_kind="desktop_watch",
                    observation_text=observation_text,
                    failure_reason=str(exc),
                )

    def _future_act_trace_summary(
        self,
        *,
        cycle_id: str,
        decision: dict[str, Any],
    ) -> dict[str, Any] | None:
        # Guard
        if decision.get("kind") != "future_act":
            return None
        future_act = decision.get("future_act")
        if not isinstance(future_act, dict):
            return None

        # Result
        return {
            "source_cycle_id": cycle_id,
            "intent_kind": future_act.get("intent_kind"),
            "intent_summary": future_act.get("intent_summary"),
            "reason_summary": decision.get("reason_summary"),
            "dedupe_key": future_act.get("dedupe_key"),
        }

    def _select_due_future_act_candidate(
        self,
        *,
        memory_set_id: str,
        current_time: str,
    ) -> dict[str, Any] | None:
        # LockedRead
        with self._runtime_state_lock:
            self._prune_future_act_candidates(current_time=current_time)
            current_dt = self._parse_iso(current_time)
            eligible = []
            for candidate in self._future_act_candidates:
                if candidate.get("memory_set_id") != memory_set_id:
                    continue
                not_before = candidate.get("not_before")
                if isinstance(not_before, str) and not_before and self._parse_iso(not_before) > current_dt:
                    continue
                eligible.append(candidate)
            if not eligible:
                return None
            eligible.sort(
                key=lambda candidate: (
                    candidate.get("updated_at") or candidate.get("created_at") or "",
                    candidate.get("candidate_id") or "",
                )
            )
            return dict(eligible[0])

    def _apply_future_act_candidate(
        self,
        *,
        cycle_id: str,
        memory_set_id: str,
        decision: dict[str, Any],
        occurred_at: str,
    ) -> dict[str, Any] | None:
        # Guard
        base_summary = self._future_act_trace_summary(cycle_id=cycle_id, decision=decision)
        if base_summary is None:
            return None

        # LockedUpsert
        with self._runtime_state_lock:
            self._prune_future_act_candidates(current_time=occurred_at)
            existing = self._find_future_act_candidate(
                memory_set_id=memory_set_id,
                dedupe_key=base_summary["dedupe_key"],
                current_time=occurred_at,
            )
            not_before = self._future_act_not_before(occurred_at)
            expires_at = self._future_act_expires_at(occurred_at)
            if existing is None:
                candidate = {
                    "candidate_id": f"future_act_candidate:{uuid.uuid4().hex}",
                    "memory_set_id": memory_set_id,
                    "intent_kind": base_summary["intent_kind"],
                    "intent_summary": base_summary["intent_summary"],
                    "reason_summary": base_summary["reason_summary"],
                    "source_cycle_id": cycle_id,
                    "not_before": not_before,
                    "expires_at": expires_at,
                    "dedupe_key": base_summary["dedupe_key"],
                    "created_at": occurred_at,
                    "updated_at": occurred_at,
                }
                self._future_act_candidates.append(candidate)
                queue_action = "created"
            else:
                candidate = existing
                candidate.update(
                    {
                        "intent_kind": base_summary["intent_kind"],
                        "intent_summary": base_summary["intent_summary"],
                        "reason_summary": base_summary["reason_summary"],
                        "source_cycle_id": cycle_id,
                        "not_before": not_before,
                        "expires_at": expires_at,
                        "updated_at": occurred_at,
                    }
                )
                queue_action = "updated"

            # Result
            return {
                **base_summary,
                "candidate_id": candidate["candidate_id"],
                "queue_action": queue_action,
                "not_before": candidate["not_before"],
                "expires_at": candidate["expires_at"],
            }

    def _record_wake_outcome(
        self,
        *,
        current_time: str,
        decision: dict[str, Any],
        selected_candidate: dict[str, Any] | None,
    ) -> None:
        # Reply
        if decision.get("kind") == "reply":
            with self._runtime_state_lock:
                self._wake_runtime_state["last_spontaneous_at"] = current_time
                self._wake_runtime_state["cooldown_until"] = self._wake_cooldown_until(current_time)
                if selected_candidate is not None:
                    dedupe_key = selected_candidate.get("dedupe_key")
                    if isinstance(dedupe_key, str) and dedupe_key:
                        reply_history = self._wake_runtime_state.setdefault("reply_history_by_dedupe", {})
                        reply_history[dedupe_key] = current_time
                    self._remove_future_act_candidate(selected_candidate.get("candidate_id"))
            return

        # FutureAct
        if decision.get("kind") == "future_act":
            return

    def _set_last_desktop_watch_at(self, current_time: str) -> None:
        # Update
        with self._runtime_state_lock:
            self._desktop_watch_runtime_state["last_watch_at"] = current_time

    def _request_desktop_watch_capture(self, *, target_client_id: str) -> dict[str, Any] | None:
        # Request
        request_id = f"vision_capture_request:{uuid.uuid4().hex}"
        pending = {
            "event": threading.Event(),
            "response": None,
            "target_client_id": target_client_id,
        }
        with self._vision_capture_lock:
            self._pending_vision_capture_requests[request_id] = pending

        # Command
        sent = self._event_stream_registry.send_to_client(
            target_client_id,
            {
                "event_id": 0,
                "type": "vision.capture_request",
                "data": {
                    "request_id": request_id,
                    "source": "desktop",
                    "mode": "still",
                    "purpose": "desktop_watch",
                    "timeout_ms": DESKTOP_WATCH_CAPTURE_TIMEOUT_MS,
                },
            },
        )
        if not sent:
            with self._vision_capture_lock:
                self._pending_vision_capture_requests.pop(request_id, None)
            return None

        # Wait
        pending["event"].wait(timeout=(DESKTOP_WATCH_CAPTURE_TIMEOUT_MS / 1000.0) + 1.0)

        # Result
        with self._vision_capture_lock:
            result = pending["response"]
            self._pending_vision_capture_requests.pop(request_id, None)
            if not isinstance(result, dict):
                return None
            return result

    def _build_desktop_watch_client_context(self, capture_response: dict[str, Any]) -> dict[str, Any]:
        # Source
        client_context = capture_response.get("client_context", {})
        if not isinstance(client_context, dict):
            client_context = {}

        # Result
        return {
            "source": "desktop_watch",
            "client_id": capture_response.get("client_id"),
            "active_app": client_context.get("active_app"),
            "window_title": client_context.get("window_title"),
            "locale": client_context.get("locale"),
            "image_count": len(capture_response.get("images", [])),
        }

    def _build_desktop_watch_observation_text(
        self,
        *,
        client_context: dict[str, Any],
        selected_candidate: dict[str, Any] | None,
    ) -> str:
        # Prefix
        parts = ["desktop_watch 観測。"]
        parts.extend(
            self._client_context_observation_parts(
                client_context=client_context,
                include_source=False,
                include_capture=True,
            )
        )
        if selected_candidate is not None:
            parts.append(self._wake_observation_text(selected_candidate))
            parts.append("いま保留中の会話候補を再評価したい。")
        return " ".join(parts)

    def _emit_desktop_watch_reply_event(
        self,
        *,
        capture_response: dict[str, Any],
        pipeline: dict[str, Any],
    ) -> None:
        # Guard
        reply_payload = pipeline.get("reply_payload")
        if not isinstance(reply_payload, dict):
            return

        # Client
        target_client_id = capture_response.get("client_id")
        if not isinstance(target_client_id, str) or not target_client_id.strip():
            return

        # Context
        client_context = capture_response.get("client_context", {})
        if not isinstance(client_context, dict):
            client_context = {}
        window_title = client_context.get("window_title")
        active_app = client_context.get("active_app")
        summary = None
        if isinstance(window_title, str) and window_title.strip():
            summary = window_title.strip()
        elif isinstance(active_app, str) and active_app.strip():
            summary = active_app.strip()

        # Event
        event = {
            "event_id": self._next_stream_event_id(),
            "type": "desktop_watch",
            "data": {
                "system_text": f"[desktop_watch] {summary}" if isinstance(summary, str) and summary else "[desktop_watch]",
                "message": reply_payload["reply_text"],
                "images": capture_response.get("images", []),
            },
        }
        self._event_stream_registry.send_to_client(target_client_id.strip(), event)

    def _next_stream_event_id(self) -> int:
        # Counter
        with self._stream_event_lock:
            event_id = self._next_stream_event_value
            self._next_stream_event_value += 1
        return event_id

    def _set_last_wake_at(self, current_time: str) -> None:
        # Update
        with self._runtime_state_lock:
            self._wake_runtime_state["last_wake_at"] = current_time

    def _wake_is_due(self, *, state: dict[str, Any], current_time: str) -> dict[str, Any]:
        # Disabled
        wake_policy = state.get("wake_policy", {})
        if wake_policy.get("mode") != "interval":
            return {
                "should_skip": True,
                "reason_summary": "wake_policy が disabled のため、自発判断は止まっている。",
            }

        # FirstWake
        with self._runtime_state_lock:
            last_wake_at = self._wake_runtime_state.get("last_wake_at")
        if not isinstance(last_wake_at, str) or not last_wake_at:
            return {
                "should_skip": False,
                "reason_summary": None,
            }

        # Interval
        interval_minutes = wake_policy.get("interval_minutes", 0)
        current_dt = self._parse_iso(current_time)
        due_at = self._parse_iso(last_wake_at) + timedelta(minutes=int(interval_minutes))
        if current_dt < due_at:
            return {
                "should_skip": True,
                "reason_summary": "interval wake の次回時刻にまだ達していない。",
            }

        # Due
        return {
            "should_skip": False,
            "reason_summary": None,
        }

    def _wake_cooldown_reason(self, *, current_time: str) -> str | None:
        # Lookup
        with self._runtime_state_lock:
            cooldown_until = self._wake_runtime_state.get("cooldown_until")
        if not isinstance(cooldown_until, str) or not cooldown_until:
            return None
        if self._parse_iso(current_time) < self._parse_iso(cooldown_until):
            return "直近の自発 reply から cooldown 中のため、今回は再介入しない。"
        return None

    def _was_recently_replied(self, *, dedupe_key: str, current_time: str) -> bool:
        # Lookup
        with self._runtime_state_lock:
            reply_history = self._wake_runtime_state.setdefault("reply_history_by_dedupe", {})
            last_reply_at = reply_history.get(dedupe_key)
        if not isinstance(last_reply_at, str) or not last_reply_at:
            return False
        current_dt = self._parse_iso(current_time)
        return current_dt - self._parse_iso(last_reply_at) < timedelta(minutes=WAKE_REPLY_COOLDOWN_MINUTES)

    def _wake_cooldown_until(self, current_time: str) -> str:
        # Timestamp
        return (self._parse_iso(current_time) + timedelta(minutes=WAKE_REPLY_COOLDOWN_MINUTES)).isoformat()

    def _wake_observation_text(self, candidate: dict[str, Any]) -> str:
        # Intent
        intent_kind = candidate.get("intent_kind", "conversation_follow_up")
        if intent_kind == "conversation_follow_up":
            return "約束の続きとして会話を再開したい。いま話しかける価値があるかを見たい。"
        return "定期起床。未完了の保留候補を再評価したい。"

    def _build_wake_observation_text(
        self,
        *,
        client_context: dict[str, Any],
        selected_candidate: dict[str, Any] | None,
    ) -> str:
        # Prefix
        parts = ["定期起床。"]
        parts.extend(
            self._client_context_observation_parts(
                client_context=client_context,
                include_source=True,
                include_capture=False,
            )
        )
        if selected_candidate is not None:
            parts.append(self._wake_observation_text(selected_candidate))
            parts.append("いま保留中の会話候補を再評価したい。")
        return " ".join(parts)

    def _client_context_observation_parts(
        self,
        *,
        client_context: dict[str, Any],
        include_source: bool,
        include_capture: bool,
    ) -> list[str]:
        # Fields
        source = self._client_context_text(client_context.get("source"), limit=48)
        active_app = self._client_context_text(client_context.get("active_app"), limit=80)
        window_title = self._client_context_text(client_context.get("window_title"), limit=120)
        locale = self._client_context_text(client_context.get("locale"), limit=32)
        parts: list[str] = []

        # Source
        if include_source and isinstance(source, str):
            if source == "background_wake_scheduler":
                parts.append("観測源は background wake scheduler。")
            else:
                parts.append(f"観測源は {source}。")

        # Foreground
        if isinstance(active_app, str):
            parts.append(f"前景アプリは {active_app}。")
        if isinstance(window_title, str):
            parts.append(f"ウィンドウタイトルは {window_title}。")

        # Locale
        if isinstance(locale, str):
            parts.append(f"UIロケールは {locale}。")

        # Capture
        if include_capture:
            image_count = client_context.get("image_count")
            if isinstance(image_count, int) and image_count > 0:
                parts.append(f"キャプチャ画像を {image_count} 件受け取った。")

        # Result
        return parts

    def _client_context_text(self, value: Any, *, limit: int) -> str | None:
        # Type
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        if not stripped:
            return None
        return self._clamp(stripped, limit=limit)

    def _remove_future_act_candidate(self, candidate_id: Any) -> None:
        # Guard
        if not isinstance(candidate_id, str) or not candidate_id:
            return
        with self._runtime_state_lock:
            self._future_act_candidates = [
                candidate
                for candidate in self._future_act_candidates
                if candidate.get("candidate_id") != candidate_id
            ]

    def _find_future_act_candidate(
        self,
        *,
        memory_set_id: str,
        dedupe_key: str,
        current_time: str,
    ) -> dict[str, Any] | None:
        # LockedScan
        with self._runtime_state_lock:
            current_dt = self._parse_iso(current_time)
            for candidate in self._future_act_candidates:
                if candidate.get("memory_set_id") != memory_set_id:
                    continue
                if candidate.get("dedupe_key") != dedupe_key:
                    continue
                expires_at = candidate.get("expires_at")
                if isinstance(expires_at, str) and expires_at and self._parse_iso(expires_at) <= current_dt:
                    continue
                return candidate
            return None

    def _prune_future_act_candidates(self, *, current_time: str) -> None:
        # LockedFilter
        with self._runtime_state_lock:
            current_dt = self._parse_iso(current_time)
            self._future_act_candidates = [
                candidate
                for candidate in self._future_act_candidates
                if not isinstance(candidate.get("expires_at"), str)
                or self._parse_iso(candidate["expires_at"]) > current_dt
            ]

    def _clear_future_act_candidates(self) -> None:
        # Reset
        with self._runtime_state_lock:
            self._future_act_candidates = []
            self._wake_runtime_state = {
                "last_wake_at": None,
                "last_spontaneous_at": None,
                "cooldown_until": None,
                "reply_history_by_dedupe": {},
            }
            self._desktop_watch_runtime_state = {
                "last_watch_at": None,
            }

    def _future_act_not_before(self, occurred_at: str) -> str:
        # Offset
        return (self._parse_iso(occurred_at) + timedelta(minutes=FUTURE_ACT_NOT_BEFORE_MINUTES)).isoformat()

    def _future_act_expires_at(self, occurred_at: str) -> str:
        # Offset
        return (self._parse_iso(occurred_at) + timedelta(hours=FUTURE_ACT_EXPIRES_HOURS)).isoformat()
