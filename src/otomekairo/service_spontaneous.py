from __future__ import annotations

import threading
import uuid
from datetime import timedelta
from typing import Any

from otomekairo.llm import LLMContractError, LLMError
from otomekairo.recall import RecallPackSelectionError
from otomekairo.service_common import (
    BACKGROUND_DESKTOP_WATCH_POLL_SECONDS,
    BACKGROUND_WAKE_POLL_SECONDS,
    DESKTOP_WATCH_CAPTURE_TIMEOUT_MS,
    PENDING_INTENT_EXPIRES_HOURS,
    PENDING_INTENT_NOT_BEFORE_MINUTES,
    WAKE_REPLY_COOLDOWN_MINUTES,
    ServiceError,
)


class PendingIntentSelectionError(LLMError):
    def __init__(
        self,
        message: str,
        *,
        pending_intent_selection: dict[str, Any],
        failure_stage: str,
    ) -> None:
        super().__init__(message)
        self.pending_intent_selection = pending_intent_selection
        self.failure_stage = failure_stage


# 自発Mixin
class ServiceSpontaneousMixin:
    def trigger_wake(self, token: str | None, payload: dict[str, Any]) -> dict[str, Any]:
        # 認可
        state = self._require_token(token)

        # クライアントコンテキスト
        client_context = payload.get("client_context", {})
        if not isinstance(client_context, dict):
            raise ServiceError(400, "invalid_client_context", "The client_context field must be an object.")

        # 実行
        return self._execute_wake_cycle(
            state=state,
            client_context=client_context,
            trigger_kind="wake",
        )

    def submit_vision_capture_response(self, token: str | None, payload: dict[str, Any]) -> dict[str, Any]:
        # 認可
        self._require_token(token)

        # 項目
        request_id = payload.get("request_id")
        client_id = payload.get("client_id")
        images = payload.get("images", [])
        client_context = payload.get("client_context")
        error = payload.get("error")

        # 検証
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

        # 画像検証
        normalized_images: list[str] = []
        for image in images:
            if not isinstance(image, str) or not image.strip():
                raise ServiceError(400, "invalid_images", "images must contain non-empty strings.")
            normalized_images.append(image.strip())

        # 応答保存
        normalized_request_id = request_id.strip()
        normalized_client_id = client_id.strip()
        with self._vision_capture_lock:
            pending = self._pending_vision_capture_requests.get(normalized_request_id)
            if pending is None:
                return {}
            request_record = pending.get("request_record")
            target_client_id = request_record.get("target_client_id") if isinstance(request_record, dict) else None
            if target_client_id != normalized_client_id:
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
                "request_record": dict(request_record) if isinstance(request_record, dict) else None,
            }
            pending["event"].set()

        # 結果
        return {}

    def _execute_wake_cycle(
        self,
        *,
        state: dict[str, Any],
        client_context: dict[str, Any],
        trigger_kind: str,
    ) -> dict[str, Any]:
        # 直列化実行
        with self._wake_execution_lock:
            cycle_id = self._new_cycle_id()
            started_at = self._now_iso()
            recent_turns = self._load_recent_turns(state)
            runtime_summary = self._build_runtime_summary(state)
            pending_intent_selection = self._empty_pending_intent_selection_trace()
            input_text = self._build_wake_input_text(
                client_context=client_context,
                selected_candidate=None,
            )

            try:
                # due / cooldown
                due = self._wake_is_due(state=state, current_time=started_at)
                if due["should_skip"]:
                    pipeline = self._noop_pipeline(
                        started_at=started_at,
                        reason_summary=due["reason_summary"],
                    )
                    return self._complete_input_success(
                        cycle_id=cycle_id,
                        started_at=started_at,
                        state=state,
                        runtime_summary=runtime_summary,
                        input_text=input_text,
                        client_context=client_context,
                        pipeline=pipeline,
                        trigger_kind=trigger_kind,
                        input_event_kind="wake",
                        input_event_role="system",
                        consolidate_memory=False,
                        pending_intent_selection=pending_intent_selection,
                    )
                cooldown_reason = self._wake_cooldown_reason(current_time=started_at)
                if cooldown_reason is not None:
                    self._set_last_wake_at(started_at)
                    pipeline = self._noop_pipeline(
                        started_at=started_at,
                        reason_summary=cooldown_reason,
                    )
                    return self._complete_input_success(
                        cycle_id=cycle_id,
                        started_at=started_at,
                        state=state,
                        runtime_summary=runtime_summary,
                        input_text=input_text,
                        client_context=client_context,
                        pipeline=pipeline,
                        trigger_kind=trigger_kind,
                        input_event_kind="wake",
                        input_event_role="system",
                        consolidate_memory=False,
                        pending_intent_selection=pending_intent_selection,
                    )

                # パイプライン
                selection_result = self._select_due_pending_intent_candidate(
                    state=state,
                    trigger_kind=trigger_kind,
                    client_context=client_context,
                    recent_turns=recent_turns,
                    current_time=started_at,
                )
                selected_candidate = selection_result["selected_candidate"]
                pending_intent_selection = selection_result["pending_intent_selection"]
                pipeline, input_text = self._run_wake_pipeline(
                    state=state,
                    started_at=started_at,
                    client_context=client_context,
                    recent_turns=recent_turns,
                    selected_candidate=selected_candidate,
                    pending_intent_selection=pending_intent_selection,
                )

                # 成功
                response = self._complete_input_success(
                    cycle_id=cycle_id,
                    started_at=started_at,
                    state=state,
                    runtime_summary=runtime_summary,
                    input_text=input_text,
                    client_context=client_context,
                    pipeline=pipeline,
                    trigger_kind=trigger_kind,
                    input_event_kind="wake",
                    input_event_role="system",
                    consolidate_memory=False,
                    pending_intent_selection=pending_intent_selection,
                )

                # 返信後処理
                self._record_wake_outcome(
                    current_time=started_at,
                    decision=pipeline["decision"],
                    selected_candidate=selected_candidate,
                )
                return response
            except PendingIntentSelectionError as exc:
                # 失敗永続化
                finished_at = self._now_iso()
                self._persist_cycle_failure(
                    cycle_id=cycle_id,
                    started_at=started_at,
                    finished_at=finished_at,
                    state=state,
                    runtime_summary=runtime_summary,
                    input_text=input_text,
                    client_context=client_context,
                    failure_reason=str(exc),
                    trigger_kind=trigger_kind,
                    input_event_kind="wake",
                    input_event_role="system",
                    failure_event_kind="pending_intent_selection_failure",
                    failure_event_payload={
                        "failure_stage": exc.failure_stage,
                    },
                    pending_intent_selection=exc.pending_intent_selection,
                )
                self._emit_input_failure_logs(
                    cycle_id=cycle_id,
                    trigger_kind=trigger_kind,
                    input_text=input_text,
                    failure_reason=str(exc),
                    pending_intent_selection=exc.pending_intent_selection,
                )
                return {
                    "cycle_id": cycle_id,
                    "result_kind": "internal_failure",
                    "reply": None,
                }
            except RecallPackSelectionError as exc:
                # 失敗永続化
                finished_at = self._now_iso()
                self._persist_cycle_failure(
                    cycle_id=cycle_id,
                    started_at=started_at,
                    finished_at=finished_at,
                    state=state,
                    runtime_summary=runtime_summary,
                    input_text=input_text,
                    client_context=client_context,
                    failure_reason=str(exc),
                    trigger_kind=trigger_kind,
                    input_event_kind="wake",
                    input_event_role="system",
                    recall_trace=self._build_failure_recall_trace(
                        recall_hint=exc.recall_hint_summary,
                        recall_pack_selection=exc.recall_pack_selection,
                    ),
                    failure_event_kind="recall_pack_selection_failure",
                    failure_event_payload={
                        "failure_stage": exc.failure_stage,
                    },
                    pending_intent_selection=pending_intent_selection,
                )
                self._emit_input_failure_logs(
                    cycle_id=cycle_id,
                    trigger_kind=trigger_kind,
                    input_text=input_text,
                    failure_reason=str(exc),
                    pending_intent_selection=pending_intent_selection,
                )
                return {
                    "cycle_id": cycle_id,
                    "result_kind": "internal_failure",
                    "reply": None,
                }
            except (LLMError, KeyError, ValueError) as exc:
                # 失敗永続化
                finished_at = self._now_iso()
                self._persist_cycle_failure(
                    cycle_id=cycle_id,
                    started_at=started_at,
                    finished_at=finished_at,
                    state=state,
                    runtime_summary=runtime_summary,
                    input_text=input_text,
                    client_context=client_context,
                    failure_reason=str(exc),
                    trigger_kind=trigger_kind,
                    input_event_kind="wake",
                    input_event_role="system",
                    pending_intent_selection=pending_intent_selection,
                )
                self._emit_input_failure_logs(
                    cycle_id=cycle_id,
                    trigger_kind=trigger_kind,
                    input_text=input_text,
                    failure_reason=str(exc),
                    pending_intent_selection=pending_intent_selection,
                )
                return {
                    "cycle_id": cycle_id,
                    "result_kind": "internal_failure",
                    "reply": None,
                }

    def _background_wake_loop(self, stop_event: threading.Event) -> None:
        # ループ
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
        # 無効時
        wake_policy = state.get("wake_policy", {})
        if wake_policy.get("mode") != "interval":
            return BACKGROUND_WAKE_POLL_SECONDS

        # 初回起床
        with self._runtime_state_lock:
            last_wake_at = self._wake_runtime_state.get("last_wake_at")
        if not isinstance(last_wake_at, str) or not last_wake_at:
            return 0.0

        # 残り
        interval_seconds = int(wake_policy["interval_seconds"])
        current_dt = self._parse_iso(current_time)
        due_at = self._parse_iso(last_wake_at) + timedelta(seconds=interval_seconds)
        remaining_seconds = (due_at - current_dt).total_seconds()
        if remaining_seconds <= 0:
            return 0.0

        # ポーリング上限
        return min(remaining_seconds, BACKGROUND_WAKE_POLL_SECONDS)

    def _background_desktop_watch_loop(self, stop_event: threading.Event) -> None:
        # ループ
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
        # 設定
        desktop_watch = state.get("desktop_watch", {})
        if not isinstance(desktop_watch, dict) or not desktop_watch.get("enabled"):
            return BACKGROUND_DESKTOP_WATCH_POLL_SECONDS
        if self._desktop_watch_target_client_id() is None:
            return BACKGROUND_DESKTOP_WATCH_POLL_SECONDS

        # 初回監視
        with self._runtime_state_lock:
            last_watch_at = self._desktop_watch_runtime_state.get("last_watch_at")
        if not isinstance(last_watch_at, str) or not last_watch_at:
            return 0.0

        # 残り
        interval_seconds = int(desktop_watch.get("interval_seconds", 1))
        current_dt = self._parse_iso(current_time)
        due_at = self._parse_iso(last_watch_at) + timedelta(seconds=interval_seconds)
        remaining_seconds = (due_at - current_dt).total_seconds()
        if remaining_seconds <= 0:
            return 0.0

        # ポーリング上限
        return min(remaining_seconds, BACKGROUND_DESKTOP_WATCH_POLL_SECONDS)

    def _execute_desktop_watch_cycle(self, *, state: dict[str, Any]) -> None:
        # 直列化実行
        with self._desktop_watch_execution_lock:
            desktop_watch = state.get("desktop_watch", {})
            if not isinstance(desktop_watch, dict) or not desktop_watch.get("enabled"):
                return
            target_client_id = self._desktop_watch_target_client_id()
            if target_client_id is None:
                return

            # タイムスタンプ
            started_at = self._now_iso()

            # キャプチャ
            capture_response = self._request_desktop_watch_capture(
                memory_set_id=state["selected_memory_set_id"],
                target_client_id=target_client_id,
                current_time=started_at,
            )
            if capture_response is None:
                return
            if not capture_response["images"]:
                return

            # 成功タイムスタンプ
            self._set_last_desktop_watch_at(self._now_iso())

            client_context = self._build_desktop_watch_client_context(capture_response)
            observation_summary = self._desktop_watch_observation_summary(capture_response)
            capability_request_summary = self._capability_request_summary(capture_response.get("request_record"))
            ongoing_action_transition_summary = capture_response.get("ongoing_action_transition_summary")
            input_text = self._build_desktop_watch_input_text(client_context=client_context, selected_candidate=None)

            # スナップショット
            cycle_id = self._new_cycle_id()
            recent_turns = self._load_recent_turns(state)
            runtime_summary = self._build_runtime_summary(state)
            pending_intent_selection = self._empty_pending_intent_selection_trace()

            try:
                # 候補選択
                selection_result = self._select_due_pending_intent_candidate(
                    state=state,
                    trigger_kind="desktop_watch",
                    client_context=client_context,
                    recent_turns=recent_turns,
                    current_time=started_at,
                )
                selected_candidate = selection_result["selected_candidate"]
                pending_intent_selection = selection_result["pending_intent_selection"]
                input_text = self._build_desktop_watch_input_text(
                    client_context=client_context,
                    selected_candidate=selected_candidate,
                )

                # パイプライン
                pipeline = self._run_input_pipeline(
                    state=state,
                    started_at=started_at,
                    input_text=input_text,
                    recent_turns=recent_turns,
                )

                # 成功
                self._complete_input_success(
                    cycle_id=cycle_id,
                    started_at=started_at,
                    state=state,
                    runtime_summary=runtime_summary,
                    input_text=input_text,
                    client_context=client_context,
                    pipeline=pipeline,
                    trigger_kind="desktop_watch",
                    input_event_kind="desktop_watch",
                    input_event_role="system",
                    consolidate_memory=False,
                    pending_intent_selection=pending_intent_selection,
                    observation_summary=observation_summary,
                    capability_request_summary=capability_request_summary,
                    ongoing_action_transition_summary=ongoing_action_transition_summary,
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
            except PendingIntentSelectionError as exc:
                # 失敗
                self._persist_cycle_failure(
                    cycle_id=cycle_id,
                    started_at=started_at,
                    finished_at=self._now_iso(),
                    state=state,
                    runtime_summary=runtime_summary,
                    input_text=input_text,
                    client_context=client_context,
                    failure_reason=str(exc),
                    trigger_kind="desktop_watch",
                    input_event_kind="desktop_watch",
                    input_event_role="system",
                    failure_event_kind="pending_intent_selection_failure",
                    failure_event_payload={
                        "failure_stage": exc.failure_stage,
                    },
                    pending_intent_selection=exc.pending_intent_selection,
                    observation_summary=observation_summary,
                    capability_request_summary=capability_request_summary,
                    ongoing_action_transition_summary=ongoing_action_transition_summary,
                )
                self._emit_input_failure_logs(
                    cycle_id=cycle_id,
                    trigger_kind="desktop_watch",
                    input_text=input_text,
                    failure_reason=str(exc),
                    pending_intent_selection=exc.pending_intent_selection,
                )
            except RecallPackSelectionError as exc:
                # 失敗
                self._persist_cycle_failure(
                    cycle_id=cycle_id,
                    started_at=started_at,
                    finished_at=self._now_iso(),
                    state=state,
                    runtime_summary=runtime_summary,
                    input_text=input_text,
                    client_context=client_context,
                    failure_reason=str(exc),
                    trigger_kind="desktop_watch",
                    input_event_kind="desktop_watch",
                    input_event_role="system",
                    recall_trace=self._build_failure_recall_trace(
                        recall_hint=exc.recall_hint_summary,
                        recall_pack_selection=exc.recall_pack_selection,
                    ),
                    failure_event_kind="recall_pack_selection_failure",
                    failure_event_payload={
                        "failure_stage": exc.failure_stage,
                    },
                    pending_intent_selection=pending_intent_selection,
                    observation_summary=observation_summary,
                    capability_request_summary=capability_request_summary,
                    ongoing_action_transition_summary=ongoing_action_transition_summary,
                )
                self._emit_input_failure_logs(
                    cycle_id=cycle_id,
                    trigger_kind="desktop_watch",
                    input_text=input_text,
                    failure_reason=str(exc),
                    pending_intent_selection=pending_intent_selection,
                )
            except (LLMError, KeyError, ValueError) as exc:
                # 失敗
                self._persist_cycle_failure(
                    cycle_id=cycle_id,
                    started_at=started_at,
                    finished_at=self._now_iso(),
                    state=state,
                    runtime_summary=runtime_summary,
                    input_text=input_text,
                    client_context=client_context,
                    failure_reason=str(exc),
                    trigger_kind="desktop_watch",
                    input_event_kind="desktop_watch",
                    input_event_role="system",
                    pending_intent_selection=pending_intent_selection,
                    observation_summary=observation_summary,
                    capability_request_summary=capability_request_summary,
                    ongoing_action_transition_summary=ongoing_action_transition_summary,
                )
                self._emit_input_failure_logs(
                    cycle_id=cycle_id,
                    trigger_kind="desktop_watch",
                    input_text=input_text,
                    failure_reason=str(exc),
                    pending_intent_selection=pending_intent_selection,
                )

    def _pending_intent_trace_summary(
        self,
        *,
        cycle_id: str,
        decision: dict[str, Any],
    ) -> dict[str, Any] | None:
        # 確認
        if decision.get("kind") != "pending_intent":
            return None
        pending_intent = decision.get("pending_intent")
        if not isinstance(pending_intent, dict):
            return None

        # 結果
        return {
            "source_cycle_id": cycle_id,
            "intent_kind": pending_intent.get("intent_kind"),
            "intent_summary": pending_intent.get("intent_summary"),
            "reason_summary": decision.get("reason_summary"),
            "dedupe_key": pending_intent.get("dedupe_key"),
        }

    def _select_due_pending_intent_candidate(
        self,
        *,
        state: dict[str, Any],
        trigger_kind: str,
        client_context: dict[str, Any],
        recent_turns: list[dict[str, Any]],
        current_time: str,
    ) -> dict[str, Any]:
        # 初期状態
        trace = self._empty_pending_intent_selection_trace()
        memory_set_id = state["selected_memory_set_id"]

        # 候補群
        candidate_pool = self._pending_intent_candidate_pool(
            memory_set_id=memory_set_id,
            current_time=current_time,
        )
        trace["candidate_pool_count"] = len(candidate_pool)
        current_dt = self._parse_iso(current_time)
        eligible_candidates = [
            candidate
            for candidate in candidate_pool
            if not isinstance(candidate.get("not_before"), str)
            or not candidate["not_before"]
            or self._parse_iso(candidate["not_before"]) <= current_dt
        ]
        trace["eligible_candidate_count"] = len(eligible_candidates)
        if not eligible_candidates:
            return {
                "selected_candidate": None,
                "pending_intent_selection": trace,
            }

        # source pack
        try:
            source_pack = self._build_pending_intent_selection_source_pack(
                state=state,
                trigger_kind=trigger_kind,
                client_context=client_context,
                recent_turns=recent_turns,
                candidates=eligible_candidates,
                current_time=current_time,
            )
        except (KeyError, TypeError, ValueError) as exc:
            trace["result_status"] = "failed"
            trace["failure_reason"] = str(exc)
            raise PendingIntentSelectionError(
                str(exc),
                pending_intent_selection=trace,
                failure_stage="build_source_pack",
            ) from exc

        # 選択
        role_definition = state["model_presets"][state["selected_model_preset_id"]]["roles"]["pending_intent_selection"]
        try:
            payload = self.llm.generate_pending_intent_selection(
                role_definition=role_definition,
                source_pack=source_pack,
            )
        except LLMContractError as exc:
            trace["result_status"] = "failed"
            trace["failure_reason"] = str(exc)
            raise PendingIntentSelectionError(
                str(exc),
                pending_intent_selection=trace,
                failure_stage="contract_validation",
            ) from exc
        except LLMError as exc:
            trace["result_status"] = "failed"
            trace["failure_reason"] = str(exc)
            raise PendingIntentSelectionError(
                str(exc),
                pending_intent_selection=trace,
                failure_stage="llm_generation",
            ) from exc

        # 反映
        try:
            selection_result = self._apply_pending_intent_selection(
                payload=payload,
                source_pack=source_pack,
                candidates=eligible_candidates,
            )
        except (KeyError, TypeError, ValueError) as exc:
            trace["result_status"] = "failed"
            trace["failure_reason"] = str(exc)
            raise PendingIntentSelectionError(
                str(exc),
                pending_intent_selection=trace,
                failure_stage="apply_selection",
            ) from exc

        # 結果
        trace["selected_candidate_ref"] = selection_result["selected_candidate_ref"]
        trace["selection_reason"] = selection_result["selection_reason"]
        trace["result_status"] = "succeeded"
        selected_candidate = selection_result["selected_candidate"]
        if selected_candidate is not None:
            trace["selected_candidate_id"] = selected_candidate.get("candidate_id")
        return {
            "selected_candidate": selected_candidate,
            "pending_intent_selection": trace,
        }

    def _pending_intent_candidate_pool(
        self,
        *,
        memory_set_id: str,
        current_time: str,
    ) -> list[dict[str, Any]]:
        # ロック下読み取り
        with self._runtime_state_lock:
            self._prune_pending_intent_candidates(current_time=current_time)
            return [
                dict(candidate)
                for candidate in self._pending_intent_candidates
                if candidate.get("memory_set_id") == memory_set_id
            ]

    def _build_pending_intent_selection_source_pack(
        self,
        *,
        state: dict[str, Any],
        trigger_kind: str,
        client_context: dict[str, Any],
        recent_turns: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
        current_time: str,
    ) -> dict[str, Any]:
        return {
            "trigger_kind": trigger_kind,
            "input_context": self._build_pending_intent_selection_input_context(
                state=state,
                trigger_kind=trigger_kind,
                client_context=client_context,
                current_time=current_time,
            ),
            "recent_turns": self._pending_intent_selection_recent_turns(recent_turns),
            "selection_policy": {
                "allow_none": True,
                "max_selected_candidates": 1,
            },
            "candidates": [
                self._pending_intent_selection_candidate_source_item(
                    candidate_ref=f"candidate:{index}",
                    candidate=candidate,
                    current_time=current_time,
                )
                for index, candidate in enumerate(candidates, start=1)
            ],
        }

    def _build_pending_intent_selection_input_context(
        self,
        *,
        state: dict[str, Any],
        trigger_kind: str,
        client_context: dict[str, Any],
        current_time: str,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "source": self._client_context_text(client_context.get("source"), limit=48) or trigger_kind,
        }
        active_app = self._client_context_text(client_context.get("active_app"), limit=80)
        if active_app is not None:
            payload["active_app"] = active_app
        window_title = self._client_context_text(client_context.get("window_title"), limit=120)
        if window_title is not None:
            payload["window_title"] = window_title
        locale = self._client_context_text(client_context.get("locale"), limit=32)
        if locale is not None:
            payload["locale"] = locale
        image_count = client_context.get("image_count")
        if trigger_kind == "desktop_watch" and isinstance(image_count, int) and image_count >= 0:
            payload["image_count"] = image_count
        drive_state_summary = self._summarize_drive_states(
            self._list_current_drive_states(
                state=state,
                current_time=current_time,
            )
        )
        if drive_state_summary:
            payload["drive_state_summary"] = drive_state_summary
        ongoing_action_summary = self._summarize_ongoing_action(
            self._current_ongoing_action(
                state=state,
                current_time=current_time,
            )
        )
        if isinstance(ongoing_action_summary, dict):
            payload["ongoing_action_summary"] = ongoing_action_summary
        return payload

    def _pending_intent_selection_recent_turns(self, recent_turns: list[dict[str, Any]]) -> list[dict[str, str]]:
        compact_turns: list[dict[str, str]] = []
        for turn in recent_turns[-4:]:
            if not isinstance(turn, dict):
                continue
            role = turn.get("role")
            text = turn.get("text")
            if not isinstance(role, str) or not role.strip():
                continue
            if not isinstance(text, str) or not text.strip():
                continue
            compact_turns.append(
                {
                    "role": role.strip(),
                    "text": self._clamp(text.strip(), limit=120),
                }
            )
        return compact_turns

    def _pending_intent_selection_candidate_source_item(
        self,
        *,
        candidate_ref: str,
        candidate: dict[str, Any],
        current_time: str,
    ) -> dict[str, Any]:
        intent_kind = candidate.get("intent_kind")
        intent_summary = candidate.get("intent_summary")
        reason_summary = candidate.get("reason_summary")
        created_at = candidate.get("created_at")
        updated_at = candidate.get("updated_at") or created_at
        expires_at = candidate.get("expires_at")
        if not isinstance(intent_kind, str) or not intent_kind.strip():
            raise ValueError("pending_intent candidate.intent_kind is invalid.")
        if not isinstance(intent_summary, str) or not intent_summary.strip():
            raise ValueError("pending_intent candidate.intent_summary is invalid.")
        if not isinstance(reason_summary, str) or not reason_summary.strip():
            raise ValueError("pending_intent candidate.reason_summary is invalid.")
        if not isinstance(created_at, str) or not created_at.strip():
            raise ValueError("pending_intent candidate.created_at is invalid.")
        if not isinstance(updated_at, str) or not updated_at.strip():
            raise ValueError("pending_intent candidate.updated_at is invalid.")
        if not isinstance(expires_at, str) or not expires_at.strip():
            raise ValueError("pending_intent candidate.expires_at is invalid.")
        return {
            "candidate_ref": candidate_ref,
            "intent_kind": intent_kind.strip(),
            "intent_summary": self._clamp(intent_summary.strip(), limit=120),
            "reason_summary": self._clamp(reason_summary.strip(), limit=160),
            "minutes_since_created": self._pending_intent_selection_minutes_since(
                current_time=current_time,
                timestamp=created_at,
            ),
            "minutes_since_updated": self._pending_intent_selection_minutes_since(
                current_time=current_time,
                timestamp=updated_at,
            ),
            "minutes_until_expiry": self._pending_intent_selection_minutes_until(
                current_time=current_time,
                timestamp=expires_at,
            ),
        }

    def _pending_intent_selection_minutes_since(
        self,
        *,
        current_time: str,
        timestamp: str,
    ) -> int:
        delta_seconds = (self._parse_iso(current_time) - self._parse_iso(timestamp)).total_seconds()
        return max(0, int(delta_seconds // 60))

    def _pending_intent_selection_minutes_until(
        self,
        *,
        current_time: str,
        timestamp: str,
    ) -> int:
        delta_seconds = (self._parse_iso(timestamp) - self._parse_iso(current_time)).total_seconds()
        return max(0, int(delta_seconds // 60))

    def _apply_pending_intent_selection(
        self,
        *,
        payload: dict[str, Any],
        source_pack: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        # lookup
        candidate_lookup = {
            source_candidate["candidate_ref"]: dict(candidate)
            for source_candidate, candidate in zip(source_pack["candidates"], candidates, strict=True)
        }

        # 結果
        selected_candidate_ref = str(payload["selected_candidate_ref"]).strip()
        selection_reason = str(payload["selection_reason"]).strip()
        if selected_candidate_ref == "none":
            return {
                "selected_candidate_ref": "none",
                "selected_candidate": None,
                "selection_reason": selection_reason,
            }
        return {
            "selected_candidate_ref": selected_candidate_ref,
            "selected_candidate": candidate_lookup[selected_candidate_ref],
            "selection_reason": selection_reason,
        }

    def _apply_pending_intent_candidate(
        self,
        *,
        cycle_id: str,
        memory_set_id: str,
        decision: dict[str, Any],
        occurred_at: str,
    ) -> dict[str, Any] | None:
        # 確認
        base_summary = self._pending_intent_trace_summary(cycle_id=cycle_id, decision=decision)
        if base_summary is None:
            return None

        # ロック下upsert
        with self._runtime_state_lock:
            self._prune_pending_intent_candidates(current_time=occurred_at)
            existing = self._find_pending_intent_candidate(
                memory_set_id=memory_set_id,
                dedupe_key=base_summary["dedupe_key"],
                current_time=occurred_at,
            )
            not_before = self._pending_intent_not_before(occurred_at)
            expires_at = self._pending_intent_expires_at(occurred_at)
            if existing is None:
                candidate = {
                    "candidate_id": f"pending_intent_candidate:{uuid.uuid4().hex}",
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
                self._pending_intent_candidates.append(candidate)
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

            # 結果
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
        # 返信
        if decision.get("kind") == "reply":
            with self._runtime_state_lock:
                self._wake_runtime_state["last_spontaneous_at"] = current_time
                self._wake_runtime_state["cooldown_until"] = self._wake_cooldown_until(current_time)
                if selected_candidate is not None:
                    dedupe_key = selected_candidate.get("dedupe_key")
                    if isinstance(dedupe_key, str) and dedupe_key:
                        reply_history = self._wake_runtime_state.setdefault("reply_history_by_dedupe", {})
                        reply_history[dedupe_key] = current_time
                    self._remove_pending_intent_candidate(selected_candidate.get("candidate_id"))
            return

        # 将来行動
        if decision.get("kind") == "pending_intent":
            return

    def _set_last_desktop_watch_at(self, current_time: str) -> None:
        # 更新
        with self._runtime_state_lock:
            self._desktop_watch_runtime_state["last_watch_at"] = current_time

    def _request_desktop_watch_capture(
        self,
        *,
        memory_set_id: str,
        target_client_id: str,
        current_time: str,
    ) -> dict[str, Any] | None:
        # リクエスト
        request_id = f"vision_capture_request:{uuid.uuid4().hex}"
        action_seed = self._begin_desktop_watch_ongoing_action(
            memory_set_id=memory_set_id,
            current_time=current_time,
        )
        request_record = {
            "request_id": request_id,
            "target_client_id": target_client_id,
            "memory_set_id": memory_set_id,
            "capability_id": "vision.capture",
            "source": "desktop",
            "mode": "still",
            "timeout_ms": DESKTOP_WATCH_CAPTURE_TIMEOUT_MS,
            "action_id": action_seed.get("action_id") if isinstance(action_seed, dict) else None,
            "goal_summary": action_seed.get("goal_summary") if isinstance(action_seed, dict) else None,
            "step_summary": action_seed.get("step_summary") if isinstance(action_seed, dict) else None,
            "episode_series_id": action_seed.get("episode_series_id") if isinstance(action_seed, dict) else None,
            "ongoing_action_transition_kind": action_seed.get("transition_kind") if isinstance(action_seed, dict) else None,
        }
        pending = {
            "event": threading.Event(),
            "response": None,
            "request_record": request_record,
        }
        with self._vision_capture_lock:
            self._pending_vision_capture_requests[request_id] = pending

        # コマンド
        sent = self._event_stream_registry.send_to_client(
            target_client_id,
            {
                "event_id": 0,
                "type": "vision.capture_request",
                "data": {
                    "request_id": request_id,
                    "capability_id": request_record["capability_id"],
                    "source": request_record["source"],
                    "mode": request_record["mode"],
                    "timeout_ms": request_record["timeout_ms"],
                },
            },
        )
        if not sent:
            with self._vision_capture_lock:
                self._pending_vision_capture_requests.pop(request_id, None)
            self._finish_desktop_watch_ongoing_action(
                request_record=request_record,
                current_time=self._now_iso(),
                terminal_kind="interrupted",
                terminal_reason="desktop_watch の vision.capture request を送れなかったため終了した。",
                final_step_summary="vision.capture request の送信に失敗した。",
            )
            return None

        # 待機
        pending["event"].wait(timeout=(DESKTOP_WATCH_CAPTURE_TIMEOUT_MS / 1000.0) + 1.0)

        # 結果
        with self._vision_capture_lock:
            result = pending["response"]
            self._pending_vision_capture_requests.pop(request_id, None)
            if not isinstance(result, dict):
                self._finish_desktop_watch_ongoing_action(
                    request_record=request_record,
                    current_time=self._now_iso(),
                    terminal_kind="interrupted",
                    terminal_reason="desktop_watch の vision.capture が timeout したため終了した。",
                    final_step_summary="vision.capture の結果待ちが timeout した。",
                )
                return None
            result["ongoing_action_transition_summary"] = self._finish_desktop_watch_ongoing_action(
                request_record=request_record,
                current_time=self._now_iso(),
                terminal_kind="completed" if result.get("error") in {None, ""} else "interrupted",
                terminal_reason=self._desktop_watch_capture_terminal_reason(result),
                final_step_summary=self._desktop_watch_capture_terminal_step_summary(result),
            )
            return result

    def _build_desktop_watch_client_context(self, capture_response: dict[str, Any]) -> dict[str, Any]:
        # source取得
        client_context = capture_response.get("client_context", {})
        if not isinstance(client_context, dict):
            client_context = {}

        # 結果
        return {
            "source": "desktop_watch",
            "client_id": capture_response.get("client_id"),
            "active_app": client_context.get("active_app"),
            "window_title": client_context.get("window_title"),
            "locale": client_context.get("locale"),
            "image_count": len(capture_response.get("images", [])),
        }

    def _desktop_watch_observation_summary(self, capture_response: dict[str, Any]) -> dict[str, Any]:
        request_record = capture_response.get("request_record")
        summary = {
            "source": "desktop_watch",
            "capability_id": "vision.capture",
            "image_count": len(capture_response.get("images", [])),
            "image_interpreted": False,
        }
        if isinstance(request_record, dict) and isinstance(request_record.get("capability_id"), str):
            summary["capability_id"] = request_record["capability_id"]
        client_id = capture_response.get("client_id")
        if isinstance(client_id, str) and client_id.strip():
            summary["client_id"] = client_id.strip()
        client_context = capture_response.get("client_context", {})
        if isinstance(client_context, dict):
            for key in ("active_app", "window_title", "locale"):
                value = client_context.get(key)
                if isinstance(value, str) and value.strip():
                    summary[key] = value.strip()
        return summary

    def _capability_request_summary(self, request_record: Any) -> dict[str, Any] | None:
        if not isinstance(request_record, dict):
            return None
        return {
            "request_id": request_record.get("request_id"),
            "capability_id": request_record.get("capability_id"),
            "source": request_record.get("source"),
            "mode": request_record.get("mode"),
            "timeout_ms": request_record.get("timeout_ms"),
            "action_id": request_record.get("action_id"),
        }

    def _begin_desktop_watch_ongoing_action(
        self,
        *,
        memory_set_id: str,
        current_time: str,
    ) -> dict[str, Any] | None:
        existing = self.store.get_ongoing_action(
            memory_set_id=memory_set_id,
            current_time=current_time,
        )
        goal_summary = "desktop_watch で現在の画面状況を観測する。"
        step_summary = "vision.capture の結果を待機している。"
        if isinstance(existing, dict):
            last_capability_id = existing.get("last_capability_id")
            if isinstance(last_capability_id, str) and last_capability_id not in {"vision.capture"}:
                return None
            action_id = str(existing.get("action_id") or f"ongoing_action:{uuid.uuid4().hex}")
            episode_series_id = existing.get("episode_series_id")
            transition_kind = "continued"
        else:
            action_id = f"ongoing_action:{uuid.uuid4().hex}"
            episode_series_id = f"episode_series:{uuid.uuid4().hex}"
            transition_kind = "started"
        if not isinstance(episode_series_id, str) or not episode_series_id.strip():
            episode_series_id = f"episode_series:{uuid.uuid4().hex}"
        self.store.upsert_ongoing_action(
            ongoing_action={
                "action_id": action_id,
                "memory_set_id": memory_set_id,
                "goal_summary": goal_summary,
                "step_summary": step_summary,
                "status": "waiting_result",
                "episode_series_id": episode_series_id,
                "last_capability_id": "vision.capture",
                "updated_at": current_time,
                "expires_at": self._desktop_watch_ongoing_action_expires_at(current_time=current_time),
            }
        )
        return {
            "action_id": action_id,
            "goal_summary": goal_summary,
            "step_summary": step_summary,
            "episode_series_id": episode_series_id,
            "transition_kind": transition_kind,
        }

    def _finish_desktop_watch_ongoing_action(
        self,
        *,
        request_record: Any,
        current_time: str,
        terminal_kind: str,
        terminal_reason: str,
        final_step_summary: str,
    ) -> dict[str, Any] | None:
        if not isinstance(request_record, dict):
            return None
        memory_set_id = request_record.get("memory_set_id")
        action_id = request_record.get("action_id")
        if not isinstance(memory_set_id, str) or not memory_set_id.strip():
            return None
        if not isinstance(action_id, str) or not action_id.strip():
            return None
        self.store.clear_ongoing_action(memory_set_id=memory_set_id)
        transition_kind = request_record.get("ongoing_action_transition_kind")
        if transition_kind not in {"started", "continued"}:
            transition_kind = "started"
        goal_summary = request_record.get("goal_summary") or "desktop_watch で現在の画面状況を観測する。"
        episode_series_id = request_record.get("episode_series_id")
        return {
            "action_id": action_id,
            "transition_sequence": [transition_kind, terminal_kind],
            "final_state": terminal_kind,
            "goal_summary": goal_summary,
            "step_summary": final_step_summary,
            "episode_series_id": episode_series_id,
            "last_capability_id": request_record.get("capability_id"),
            "reason_summary": terminal_reason,
            "updated_at": current_time,
        }

    def _desktop_watch_ongoing_action_expires_at(self, *, current_time: str) -> str:
        timeout_seconds = max(int(DESKTOP_WATCH_CAPTURE_TIMEOUT_MS / 1000), 1)
        return (self._parse_iso(current_time) + timedelta(seconds=timeout_seconds + 30)).isoformat()

    def _desktop_watch_capture_terminal_reason(self, capture_response: dict[str, Any]) -> str:
        capture_error = capture_response.get("error")
        if isinstance(capture_error, str) and capture_error.strip():
            return f"desktop_watch の vision.capture が error で終了した。 error={capture_error.strip()}"
        image_count = len(capture_response.get("images", []))
        if image_count <= 0:
            return "desktop_watch の vision.capture は空の結果で完了した。"
        return "desktop_watch の vision.capture が完了し、観測結果を取り込んだ。"

    def _desktop_watch_capture_terminal_step_summary(self, capture_response: dict[str, Any]) -> str:
        capture_error = capture_response.get("error")
        if isinstance(capture_error, str) and capture_error.strip():
            return "vision.capture が error で終了した。"
        image_count = len(capture_response.get("images", []))
        if image_count <= 0:
            return "vision.capture の結果は空だった。"
        return "vision.capture の結果を受け取った。"

    def _build_desktop_watch_input_text(
        self,
        *,
        client_context: dict[str, Any],
        selected_candidate: dict[str, Any] | None,
    ) -> str:
        # プレフィックス
        parts = ["desktop_watch 観測。"]
        parts.extend(
            self._client_context_input_parts(
                client_context=client_context,
                include_source=False,
                include_capture=True,
            )
        )
        if selected_candidate is not None:
            parts.append(self._wake_input_text(selected_candidate))
            parts.append("いま保留中の会話候補を再評価したい。")
        return " ".join(parts)

    def _emit_desktop_watch_reply_event(
        self,
        *,
        capture_response: dict[str, Any],
        pipeline: dict[str, Any],
    ) -> None:
        # 確認
        reply_payload = pipeline.get("reply_payload")
        if not isinstance(reply_payload, dict):
            return

        # クライアント
        target_client_id = capture_response.get("client_id")
        if not isinstance(target_client_id, str) or not target_client_id.strip():
            return

        # コンテキスト
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

        # イベント
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

    def _desktop_watch_target_client_id(self) -> str | None:
        # 接続中で vision.capture を持つ client が 1 台だけなら採用する
        return self._event_stream_registry.find_single_client_with_capability("vision.capture")

    def _next_stream_event_id(self) -> int:
        # カウンター
        with self._stream_event_lock:
            event_id = self._next_stream_event_value
            self._next_stream_event_value += 1
        return event_id

    def _set_last_wake_at(self, current_time: str) -> None:
        # 更新
        with self._runtime_state_lock:
            self._wake_runtime_state["last_wake_at"] = current_time

    def _wake_is_due(self, *, state: dict[str, Any], current_time: str) -> dict[str, Any]:
        # 無効時
        wake_policy = state.get("wake_policy", {})
        if wake_policy.get("mode") != "interval":
            return {
                "should_skip": True,
                "reason_summary": "wake_policy が disabled のため、自発判断は止まっている。",
            }

        # 初回起床
        with self._runtime_state_lock:
            last_wake_at = self._wake_runtime_state.get("last_wake_at")
        if not isinstance(last_wake_at, str) or not last_wake_at:
            return {
                "should_skip": False,
                "reason_summary": None,
            }

        # 間隔
        interval_seconds = wake_policy["interval_seconds"]
        current_dt = self._parse_iso(current_time)
        due_at = self._parse_iso(last_wake_at) + timedelta(seconds=int(interval_seconds))
        if current_dt < due_at:
            return {
                "should_skip": True,
                "reason_summary": "interval wake の次回時刻にまだ達していない。",
            }

        # 期限到来
        return {
            "should_skip": False,
            "reason_summary": None,
        }

    def _wake_cooldown_reason(self, *, current_time: str) -> str | None:
        # 検索
        with self._runtime_state_lock:
            cooldown_until = self._wake_runtime_state.get("cooldown_until")
        if not isinstance(cooldown_until, str) or not cooldown_until:
            return None
        if self._parse_iso(current_time) < self._parse_iso(cooldown_until):
            return "直近の自発 reply から cooldown 中のため、今回は再介入しない。"
        return None

    def _was_recently_replied(self, *, dedupe_key: str, current_time: str) -> bool:
        # 検索
        with self._runtime_state_lock:
            reply_history = self._wake_runtime_state.setdefault("reply_history_by_dedupe", {})
            last_reply_at = reply_history.get(dedupe_key)
        if not isinstance(last_reply_at, str) or not last_reply_at:
            return False
        current_dt = self._parse_iso(current_time)
        return current_dt - self._parse_iso(last_reply_at) < timedelta(minutes=WAKE_REPLY_COOLDOWN_MINUTES)

    def _wake_cooldown_until(self, current_time: str) -> str:
        # タイムスタンプ
        return (self._parse_iso(current_time) + timedelta(minutes=WAKE_REPLY_COOLDOWN_MINUTES)).isoformat()

    def _wake_input_text(self, candidate: dict[str, Any]) -> str:
        # intent判定
        intent_kind = candidate.get("intent_kind", "conversation_follow_up")
        if intent_kind == "conversation_follow_up":
            return "約束の続きとして会話を再開したい。いま話しかける価値があるかを見たい。"
        return "定期起床。未完了の保留候補を再評価したい。"

    def _build_wake_input_text(
        self,
        *,
        client_context: dict[str, Any],
        selected_candidate: dict[str, Any] | None,
    ) -> str:
        # プレフィックス
        parts = ["定期起床。"]
        parts.extend(
            self._client_context_input_parts(
                client_context=client_context,
                include_source=True,
                include_capture=False,
            )
        )
        if selected_candidate is not None:
            parts.append(self._wake_input_text(selected_candidate))
            parts.append("いま保留中の会話候補を再評価したい。")
        return " ".join(parts)

    def _client_context_input_parts(
        self,
        *,
        client_context: dict[str, Any],
        include_source: bool,
        include_capture: bool,
    ) -> list[str]:
        # 項目
        source = self._client_context_text(client_context.get("source"), limit=48)
        active_app = self._client_context_text(client_context.get("active_app"), limit=80)
        window_title = self._client_context_text(client_context.get("window_title"), limit=120)
        locale = self._client_context_text(client_context.get("locale"), limit=32)
        parts: list[str] = []

        # source取得
        if include_source and isinstance(source, str):
            if source == "background_wake_scheduler":
                parts.append("入力源は background wake scheduler。")
            else:
                parts.append(f"入力源は {source}。")

        # 前景
        if isinstance(active_app, str):
            parts.append(f"前景アプリは {active_app}。")
        if isinstance(window_title, str):
            parts.append(f"ウィンドウタイトルは {window_title}。")

        # ロケール
        if isinstance(locale, str):
            parts.append(f"UIロケールは {locale}。")

        # キャプチャ
        if include_capture:
            image_count = client_context.get("image_count")
            if isinstance(image_count, int) and image_count > 0:
                parts.append(f"キャプチャ画像を {image_count} 件受け取った。")

        # 結果
        return parts

    def _client_context_text(self, value: Any, *, limit: int) -> str | None:
        # 型
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        if not stripped:
            return None
        return self._clamp(stripped, limit=limit)

    def _remove_pending_intent_candidate(self, candidate_id: Any) -> None:
        # 確認
        if not isinstance(candidate_id, str) or not candidate_id:
            return
        with self._runtime_state_lock:
            self._pending_intent_candidates = [
                candidate
                for candidate in self._pending_intent_candidates
                if candidate.get("candidate_id") != candidate_id
            ]

    def _find_pending_intent_candidate(
        self,
        *,
        memory_set_id: str,
        dedupe_key: str,
        current_time: str,
    ) -> dict[str, Any] | None:
        # ロック下走査
        with self._runtime_state_lock:
            current_dt = self._parse_iso(current_time)
            for candidate in self._pending_intent_candidates:
                if candidate.get("memory_set_id") != memory_set_id:
                    continue
                if candidate.get("dedupe_key") != dedupe_key:
                    continue
                expires_at = candidate.get("expires_at")
                if isinstance(expires_at, str) and expires_at and self._parse_iso(expires_at) <= current_dt:
                    continue
                return candidate
            return None

    def _prune_pending_intent_candidates(self, *, current_time: str) -> None:
        # ロック下絞り込み
        with self._runtime_state_lock:
            current_dt = self._parse_iso(current_time)
            self._pending_intent_candidates = [
                candidate
                for candidate in self._pending_intent_candidates
                if not isinstance(candidate.get("expires_at"), str)
                or self._parse_iso(candidate["expires_at"]) > current_dt
            ]

    def _clear_pending_intent_candidates(self) -> None:
        # リセット
        with self._runtime_state_lock:
            self._pending_intent_candidates = []
            self._wake_runtime_state = {
                "last_wake_at": None,
                "last_spontaneous_at": None,
                "cooldown_until": None,
                "reply_history_by_dedupe": {},
            }
            self._desktop_watch_runtime_state = {
                "last_watch_at": None,
            }

    def _pending_intent_not_before(self, occurred_at: str) -> str:
        # オフセット
        return (self._parse_iso(occurred_at) + timedelta(minutes=PENDING_INTENT_NOT_BEFORE_MINUTES)).isoformat()

    def _pending_intent_expires_at(self, occurred_at: str) -> str:
        # オフセット
        return (self._parse_iso(occurred_at) + timedelta(hours=PENDING_INTENT_EXPIRES_HOURS)).isoformat()
