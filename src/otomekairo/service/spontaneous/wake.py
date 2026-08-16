from __future__ import annotations

import threading
from datetime import timedelta
from typing import Any

from otomekairo.llm.client import LLMError
from otomekairo.interaction import InteractionContext
from otomekairo.recall.builder import RecallPackSelectionError
from otomekairo.service.capability import PreSendCheckFailureError
from otomekairo.service.common import (
    BACKGROUND_THINKING_POLL_SECONDS,
    INITIAL_VISUAL_CAPTURE_DELAY_SECONDS,
    WAKE_RECENT_DEDUPE_WINDOW_MINUTES,
    debug_log,
)
from otomekairo.service.spontaneous.pending_intent import PendingIntentSelectionError


class ServiceSpontaneousWakeMixin:
    def _emit_wake_assistant_message_event(
        self,
        *,
        cycle_id: str,
        trigger_kind: str,
        client_context: dict[str, Any],
        interaction_context: InteractionContext | None,
        pipeline: dict[str, Any],
    ) -> dict[str, Any] | None:
        if trigger_kind not in {"wake", "background_thinking"}:
            return None
        speech_payload = pipeline.get("speech_payload")
        if not isinstance(speech_payload, dict):
            return None
        sent, audio_delivery = self._emit_assistant_message_with_audio(
            event_data={
                "cycle_id": cycle_id,
                "source_kind": trigger_kind,
                "trigger_kind": trigger_kind,
                "persona_id": pipeline["persona_id"],
                "persona_display_name": pipeline["persona_display_name"],
                "interaction_ref": (
                    interaction_context.interaction_ref
                    if interaction_context is not None
                    else None
                ),
                "recipient_person_refs": (
                    list(interaction_context.participant_refs)
                    if interaction_context is not None
                    else []
                ),
                "system_text": f"[{trigger_kind}]",
            },
            speech_text=speech_payload["speech_text"],
        )
        debug_log(
            "Wake",
            (
                f"{self._short_cycle_id(cycle_id)} assistant_message sent={sent} "
                f"speech_chars={len(speech_payload['speech_text'])}"
            ),
            level="DEBUG",
        )
        return audio_delivery

    def _execute_wake_cycle(
        self,
        *,
        state: dict[str, Any],
        client_context: dict[str, Any],
        interaction_context: InteractionContext | None = None,
        trigger_kind: str,
        reference_payload: Any = None,
    ) -> dict[str, Any]:
        is_background = trigger_kind == "background_thinking"
        entered = (
            self._cycle_coordinator.try_enter_background()
            if is_background
            else False
        )
        if is_background and not entered:
            return {
                "cycle_id": None,
                "interaction_ref": None,
                "recipient_person_refs": [],
                "result_kind": "skipped",
                "speech": None,
                "capability_request": None,
                "autonomous_run": None,
                "reason_code": "foreground_cycle_active",
            }
        if not is_background:
            self._cycle_coordinator.enter_foreground()
        try:
            return self._execute_wake_cycle_inner(
                state=state,
                client_context=client_context,
                interaction_context=interaction_context,
                trigger_kind=trigger_kind,
                reference_payload=reference_payload,
            )
        finally:
            if is_background:
                self._cycle_coordinator.leave_background()
            else:
                self._cycle_coordinator.leave_foreground()

    def _execute_wake_cycle_inner(
        self,
        *,
        state: dict[str, Any],
        client_context: dict[str, Any],
        interaction_context: InteractionContext | None = None,
        trigger_kind: str,
        reference_payload: Any = None,
    ) -> dict[str, Any]:
        # 直列化実行
        with self._wake_execution_lock:
            input_event_kind = "background_thinking" if trigger_kind == "background_thinking" else "wake"
            cycle_id = self._new_cycle_id()
            started_at = self._now_iso()
            recent_turns = self._load_recent_turns(state, interaction_context)
            runtime_summary = self._build_runtime_summary(state)
            pending_intent_selection = self._empty_pending_intent_selection_trace()
            observation_summary: dict[str, Any] | None = None
            reference_context: dict[str, Any] | None = None
            input_text = self._build_wake_input_text(
                state=state,
                client_context=client_context,
                selected_candidate=None,
            )
            try:
                if trigger_kind == "wake":
                    client_context, observation_summary, reference_context = self._prepare_wake_reference_context(
                        state=state,
                        started_at=started_at,
                        input_text=input_text,
                        client_context=client_context,
                        reference_payload=reference_payload,
                    )
                    input_text = self._build_wake_input_text(
                        state=state,
                        client_context=client_context,
                        selected_candidate=None,
                    )
                # due 判定
                if trigger_kind == "background_thinking":
                    if not self._background_thinking_should_proceed(
                        state=state,
                        current_time=started_at,
                        client_context=client_context,
                    ):
                        due = self._wake_is_due(state=state, current_time=started_at)
                        debug_log("Wake", f"{self._short_cycle_id(cycle_id)} skip due reason={self._clamp(due['reason_summary'])}")
                        pipeline = self._noop_pipeline(
                            state=state,
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
                            interaction_context=interaction_context,
                            pipeline=pipeline,
                            trigger_kind=trigger_kind,
                            input_event_kind=input_event_kind,
                            input_event_role="system",
                            consolidate_memory=False,
                            pending_intent_selection=pending_intent_selection,
                            observation_summary=observation_summary,
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
                debug_log(
                    "Wake",
                    (
                        f"{self._short_cycle_id(cycle_id)} selection "
                        f"pool={pending_intent_selection.get('candidate_pool_count', 0)} "
                        f"eligible={pending_intent_selection.get('eligible_candidate_count', 0)} "
                        f"selected={pending_intent_selection.get('selected_candidate_ref') or '-'}"
                    ),
                )
                pipeline, input_text, client_context = self._run_wake_pipeline(
                    state=state,
                    started_at=started_at,
                    trigger_kind=trigger_kind,
                    client_context=client_context,
                    interaction_context=interaction_context,
                    recent_turns=recent_turns,
                    selected_candidate=selected_candidate,
                    pending_intent_selection=pending_intent_selection,
                    cycle_id=cycle_id,
                    observation_summary=observation_summary,
                    reference_context=reference_context,
                )

                # 成功
                response = self._complete_input_success(
                    cycle_id=cycle_id,
                    started_at=started_at,
                    state=state,
                    runtime_summary=runtime_summary,
                    input_text=input_text,
                    client_context=client_context,
                    interaction_context=interaction_context,
                    pipeline=pipeline,
                    trigger_kind=trigger_kind,
                    input_event_kind=input_event_kind,
                    input_event_role="system",
                    consolidate_memory=self._should_consolidate_spontaneous_cycle(
                        trigger_kind=trigger_kind,
                        pipeline=pipeline,
                        observation_summary=observation_summary,
                        client_context=client_context,
                    ),
                    pending_intent_selection=pending_intent_selection,
                    observation_summary=observation_summary,
                )
                self._broadcast_system_notice(response.get("system_notice"))

                # 発話後処理
                self._record_wake_outcome(
                    current_time=started_at,
                    decision=pipeline["decision"],
                    selected_candidate=selected_candidate,
                    client_context=client_context,
                )
                audio_delivery = self._emit_wake_assistant_message_event(
                    cycle_id=cycle_id,
                    trigger_kind=trigger_kind,
                    client_context=client_context,
                    interaction_context=interaction_context,
                    pipeline=pipeline,
                )
                if (
                    isinstance(audio_delivery, dict)
                    and isinstance(response.get("speech"), dict)
                ):
                    response["speech"]["audio_delivery"] = audio_delivery
                debug_log(
                    "Wake",
                    f"{self._short_cycle_id(cycle_id)} done result={response['result_kind']}",
                )
                return response
            except PendingIntentSelectionError as exc:
                debug_log(
                    "Wake",
                    (
                        f"{self._short_cycle_id(cycle_id)} failed stage={exc.failure_stage} "
                        f"error={type(exc).__name__}: {self._clamp(str(exc))}"
                    ),
                    level="ERROR",
                )
                return self._finalize_cycle_failure(
                    cycle_id=cycle_id,
                    started_at=started_at,
                    state=state,
                    runtime_summary=runtime_summary,
                    input_text=input_text,
                    client_context=client_context,
                    interaction_context=interaction_context,
                    failure_reason=str(exc),
                    trigger_kind=trigger_kind,
                    input_event_kind=input_event_kind,
                    input_event_role="system",
                    failure_event_kind="pending_intent_selection_failure",
                    failure_event_payload={
                        "failure_stage": exc.failure_stage,
                    },
                    pending_intent_selection=exc.pending_intent_selection,
                    observation_summary=observation_summary,
                )
            except RecallPackSelectionError as exc:
                debug_log(
                    "Wake",
                    (
                        f"{self._short_cycle_id(cycle_id)} failed stage={exc.failure_stage} "
                        f"error={type(exc).__name__}: {self._clamp(str(exc))}"
                    ),
                    level="ERROR",
                )
                return self._finalize_cycle_failure(
                    cycle_id=cycle_id,
                    started_at=started_at,
                    state=state,
                    runtime_summary=runtime_summary,
                    input_text=input_text,
                    client_context=client_context,
                    interaction_context=interaction_context,
                    failure_reason=str(exc),
                    trigger_kind=trigger_kind,
                    input_event_kind=input_event_kind,
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
                )
            except (LLMError, KeyError, ValueError) as exc:
                review_failure = isinstance(exc, PreSendCheckFailureError)
                capability_request_summary, ongoing_action_transition_summary = self._exception_capability_dispatch_trace(
                    exc
                )
                debug_log(
                    "Wake",
                    f"{self._short_cycle_id(cycle_id)} failed error={type(exc).__name__}: {self._clamp(str(exc))}",
                    level="ERROR",
                )
                review_notice = (
                    {
                        "source_kind": "pre_send_check",
                        "code": "pre_send_check_failure",
                        "message": "外部送信内容の安全確認を完了できなかったため、送信しませんでした。",
                        "conversation_visible": False,
                        "interaction_ref": None,
                        "recipient_person_refs": [],
                    }
                    if review_failure
                    else None
                )
                response = self._finalize_cycle_failure(
                    cycle_id=cycle_id,
                    started_at=started_at,
                    state=state,
                    runtime_summary=runtime_summary,
                    input_text=input_text,
                    client_context=client_context,
                    interaction_context=interaction_context,
                    failure_reason=str(exc),
                    trigger_kind=trigger_kind,
                    input_event_kind=input_event_kind,
                    input_event_role="system",
                    pending_intent_selection=pending_intent_selection,
                    observation_summary=observation_summary,
                    capability_request_summary=capability_request_summary,
                    ongoing_action_transition_summary=ongoing_action_transition_summary,
                    failure_event_kind=(
                        "pre_send_check_failure"
                        if review_failure
                        else None
                    ),
                    failure_event_payload=(
                        {"pre_send_check": exc.audit_summary}
                        if review_failure
                        else None
                    ),
                    system_notice=review_notice,
                )
                self._broadcast_system_notice(review_notice)
                return response

    def _execute_scheduled_background_thinking(self, *, state: dict[str, Any]) -> None:
        current_time = self._now_iso()
        unseen_sources = self._unseen_wake_observation_sources(state)
        if unseen_sources:
            return
        regular_due = self._wake_is_due(state=state, current_time=current_time)["should_skip"] is not True
        standing_due = bool(self._due_standing_concerns(state=state, current_time=current_time))
        if not (regular_due or standing_due):
            return
        client_context: dict[str, Any] = {"source": "background_thinking_scheduler"}
        self._execute_wake_cycle(
            state=state,
            client_context=client_context,
            trigger_kind="background_thinking",
        )

    def _background_thinking_loop(self, stop_event: threading.Event) -> None:
        # ループ
        while not stop_event.is_set():
            try:
                state = self.store.read_state()
                delay_seconds = self._background_thinking_delay_seconds(state=state, current_time=self._now_iso())
                if delay_seconds > 0:
                    self._wait_background_thinking_delay(timeout=delay_seconds)
                    continue
                self._execute_scheduled_background_thinking(state=state)
            except Exception as exc:  # noqa: BLE001
                debug_log("Wake", f"background thinking loop error={type(exc).__name__}: {self._clamp(str(exc))}", level="ERROR")
                self._wait_background_thinking_delay(timeout=BACKGROUND_THINKING_POLL_SECONDS)

    def _wait_background_thinking_delay(self, *, timeout: float) -> None:
        nudge = self._background_thinking_nudge
        if not nudge.is_set():
            nudge.wait(timeout=timeout)
        nudge.clear()

    def _nudge_background_thinking_scheduler(self) -> None:
        self._background_thinking_nudge.set()

    def _background_thinking_delay_seconds(self, *, state: dict[str, Any], current_time: str) -> float:
        # 無効時
        wake_policy = state.get("wake_policy", {})
        extra_delay_seconds = self._extra_standing_concern_thinking_delay_seconds(
            state=state,
            current_time=current_time,
        )
        if wake_policy.get("mode") != "interval":
            delay_seconds = BACKGROUND_THINKING_POLL_SECONDS
            if extra_delay_seconds is not None:
                delay_seconds = min(extra_delay_seconds, delay_seconds)
            return delay_seconds

        # 初回観測待ち
        initial_delay_seconds = self._wake_initial_delay_remaining_seconds(current_time=current_time)
        if initial_delay_seconds is not None:
            return min(initial_delay_seconds, BACKGROUND_THINKING_POLL_SECONDS)

        # 一時失敗後の再試行待ち
        retry_delay_seconds = self._wake_retry_delay_remaining_seconds(current_time=current_time)
        if retry_delay_seconds is not None:
            return min(retry_delay_seconds, BACKGROUND_THINKING_POLL_SECONDS)

        # 対象 vision source がこの process で未登録
        if self._unseen_wake_observation_sources(state):
            return BACKGROUND_THINKING_POLL_SECONDS

        # 初回定期思考
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
        if extra_delay_seconds is not None:
            remaining_seconds = min(remaining_seconds, extra_delay_seconds)

        # ポーリング上限
        return min(remaining_seconds, BACKGROUND_THINKING_POLL_SECONDS)

    def _record_wake_outcome(
        self,
        *,
        current_time: str,
        decision: dict[str, Any],
        selected_candidate: dict[str, Any] | None,
        client_context: dict[str, Any] | None = None,
    ) -> None:
        if self._decision_has_outward_speech(decision):
            with self._runtime_state_lock:
                self._wake_runtime_state["last_spontaneous_at"] = current_time
                if selected_candidate is not None:
                    dedupe_key = selected_candidate.get("dedupe_key")
                    if isinstance(dedupe_key, str) and dedupe_key:
                        speech_history = self._wake_runtime_state.setdefault("speech_history_by_dedupe", {})
                        speech_history[dedupe_key] = current_time
                    self._remove_pending_intent_candidate(selected_candidate.get("candidate_id"))
                self._record_visual_observation_prompted_locked(
                    current_time=current_time,
                    client_context=client_context,
                )
            return

        if selected_candidate is not None:
            self._record_pending_intent_candidate_evaluation(
                candidate_id=selected_candidate.get("candidate_id"),
                current_time=current_time,
            )

        if self._decision_has_any_kind(decision, {"pending_intent"}):
            return

    def _record_visual_observation_prompted_locked(
        self,
        *,
        current_time: str,
        client_context: dict[str, Any] | None,
    ) -> None:
        if not isinstance(client_context, dict):
            return
        signals: list[dict[str, Any]] = []
        value = client_context.get("visual_observation_signals")
        if isinstance(value, list):
            signals.extend(item for item in value if isinstance(item, dict))
        wake_observations = client_context.get("wake_observations")
        if isinstance(wake_observations, list):
            for observation in wake_observations:
                if not isinstance(observation, dict):
                    continue
                signal = observation.get("visual_observation_signal")
                if isinstance(signal, dict):
                    signals.append(signal)
        seen_observation_ids: set[str] = set()
        for signal in signals:
            if signal.get("change_state") not in {"first_seen", "changed"}:
                continue
            observation_id = signal.get("observation_id")
            observation_signature = signal.get("observation_signature")
            summary_text = signal.get("summary_text")
            if not isinstance(observation_id, str) or not observation_id.strip():
                continue
            normalized_id = observation_id.strip()
            if normalized_id in seen_observation_ids:
                continue
            if not isinstance(summary_text, str) or not summary_text.strip():
                continue
            runtime = self._wake_observation_runtime_state.get(normalized_id)
            if not isinstance(runtime, dict):
                continue
            if isinstance(observation_signature, str) and observation_signature.strip():
                runtime["last_prompted_observation_signature"] = observation_signature.strip()
            runtime["last_prompted_observation_summary"] = summary_text.strip()
            for signal_key, runtime_key in (
                ("vision_source_id", "last_prompted_vision_source_id"),
                ("source_kind", "last_prompted_source_kind"),
                ("source_label", "last_prompted_source_label"),
                ("active_app", "last_prompted_active_app"),
                ("window_title", "last_prompted_window_title"),
            ):
                value = signal.get(signal_key)
                if isinstance(value, str) and value.strip():
                    runtime[runtime_key] = value.strip()
            runtime["last_prompted_at"] = current_time
            seen_observation_ids.add(normalized_id)

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
            self._wake_runtime_state["retry_after"] = None

    def _set_wake_retry_after(self, current_time: str) -> None:
        # 一時失敗は interval を消費せず、短い再試行だけを待つ。
        retry_at = self._parse_iso(current_time) + timedelta(seconds=BACKGROUND_THINKING_POLL_SECONDS)
        with self._runtime_state_lock:
            self._wake_runtime_state["retry_after"] = retry_at.isoformat()

    def _sync_wake_policy_runtime_state(
        self,
        *,
        previous_wake_policy: dict[str, Any] | None,
        next_wake_policy: dict[str, Any] | None,
        current_time: str,
    ) -> None:
        previous_enabled = self._wake_policy_has_enabled_visual_capture(previous_wake_policy)
        next_enabled = self._wake_policy_has_enabled_visual_capture(next_wake_policy)
        with self._runtime_state_lock:
            # visual capture 有効化直後は 5 秒待ち、その後必ず初回処理へ進む。
            # 直前の last_wake_at が残ると interval 残りで初回が遅れるため、起点もリセットする。
            if next_enabled and not previous_enabled:
                self._wake_runtime_state["initial_delay_until"] = (
                    self._parse_iso(current_time) + timedelta(seconds=INITIAL_VISUAL_CAPTURE_DELAY_SECONDS)
                ).isoformat()
                self._wake_runtime_state["last_wake_at"] = None
                self._wake_runtime_state["retry_after"] = None
                return
            if not next_enabled:
                self._wake_runtime_state["initial_delay_until"] = None

    def _wake_policy_has_enabled_visual_capture(self, wake_policy: dict[str, Any] | None) -> bool:
        if not isinstance(wake_policy, dict) or wake_policy.get("mode") != "interval":
            return False
        observations = wake_policy.get("observations")
        if not isinstance(observations, list):
            return False
        for observation in observations:
            if not isinstance(observation, dict):
                continue
            if observation.get("enabled") is not True:
                continue
            if observation.get("capability_id") == "vision.capture":
                return True
        return False

    def _wake_initial_delay_remaining_seconds(self, *, current_time: str) -> float | None:
        with self._runtime_state_lock:
            initial_delay_until = self._wake_runtime_state.get("initial_delay_until")
        if not isinstance(initial_delay_until, str) or not initial_delay_until:
            return None
        remaining_seconds = (self._parse_iso(initial_delay_until) - self._parse_iso(current_time)).total_seconds()
        if remaining_seconds > 0:
            return remaining_seconds
        with self._runtime_state_lock:
            if self._wake_runtime_state.get("initial_delay_until") == initial_delay_until:
                self._wake_runtime_state["initial_delay_until"] = None
        return None

    def _wake_retry_delay_remaining_seconds(self, *, current_time: str) -> float | None:
        with self._runtime_state_lock:
            retry_after = self._wake_runtime_state.get("retry_after")
        if not isinstance(retry_after, str) or not retry_after:
            return None
        remaining_seconds = (self._parse_iso(retry_after) - self._parse_iso(current_time)).total_seconds()
        if remaining_seconds > 0:
            return remaining_seconds
        with self._runtime_state_lock:
            if self._wake_runtime_state.get("retry_after") == retry_after:
                self._wake_runtime_state["retry_after"] = None
        return None

    def _wake_is_due(self, *, state: dict[str, Any], current_time: str) -> dict[str, Any]:
        # 無効時
        wake_policy = state.get("wake_policy", {})
        if wake_policy.get("mode") != "interval":
            return {
                "should_skip": True,
                "reason_summary": "`wake_policy.mode=disabled` のため、自発判断は止まっている。",
            }

        # 初回観測待ち
        initial_delay_seconds = self._wake_initial_delay_remaining_seconds(current_time=current_time)
        if initial_delay_seconds is not None:
            return {
                "should_skip": True,
                "reason_summary": "visual capture 有効化直後のため、初回観測を 5 秒待っている。",
            }

        # 一時失敗後の再試行待ち
        retry_delay_seconds = self._wake_retry_delay_remaining_seconds(current_time=current_time)
        if retry_delay_seconds is not None:
            return {
                "should_skip": True,
                "reason_summary": "思考前観測 の一時失敗後の再試行待機中。",
            }

        # 対象 vision source がこの process で未登録
        if self._unseen_wake_observation_sources(state):
            return {
                "should_skip": True,
                "reason_summary": "思考前観測の対象 vision source が、この process でまだ一度も登録されていない。",
            }

        # 初回定期思考
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
                "reason_summary": "定期思考 の次回時刻にまだ達していない。",
            }

        # 期限到来
        return {
            "should_skip": False,
            "reason_summary": None,
        }

    def _was_recently_replied(self, *, dedupe_key: str, current_time: str) -> bool:
        # 検索
        with self._runtime_state_lock:
            speech_history = self._wake_runtime_state.setdefault("speech_history_by_dedupe", {})
            last_speech_at = speech_history.get(dedupe_key)
        if not isinstance(last_speech_at, str) or not last_speech_at:
            return False
        current_dt = self._parse_iso(current_time)
        return current_dt - self._parse_iso(last_speech_at) < timedelta(minutes=WAKE_RECENT_DEDUPE_WINDOW_MINUTES)

    def _wake_input_text(self, candidate: dict[str, Any]) -> str:
        # intent判定
        intent_kind = candidate.get("intent_kind", "conversation_follow_up")
        if intent_kind == "conversation_follow_up":
            return "約束の続きとして会話を再開したい。いま話しかける価値があるかを見たい。"
        return "定期思考。未完了の保留候補を再評価したい。"

    def _build_wake_input_text(
        self,
        *,
        state: dict[str, Any],
        client_context: dict[str, Any],
        selected_candidate: dict[str, Any] | None,
    ) -> str:
        # プレフィックス
        parts = ["定期思考。"]
        persona = state["personas"][state["selected_persona_id"]]
        parts.append(f"initiative_baseline は {persona['initiative_baseline']}。")
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
        else:
            parts.append(
                "観測、drive_state、直近文脈、候補を合わせて、"
                "関わる、保留する、見送る、能力を使うのどれが自然かを見る。"
            )
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
        locale = self._client_context_text(client_context.get("locale"), limit=32)
        parts: list[str] = []

        # source取得
        if include_source and isinstance(source, str):
            if source == "background_thinking_scheduler":
                parts.append("入力源は定期思考スケジューラ。")
            else:
                parts.append(f"入力源は {source}。")
        wake_reference = client_context.get("wake_reference")
        if isinstance(wake_reference, dict):
            label = self._client_context_text(wake_reference.get("label"), limit=120)
            content_kind = self._client_context_text(wake_reference.get("content_kind"), limit=32)
            media_type = self._client_context_text(wake_reference.get("media_type"), limit=80)
            if label is not None and content_kind is not None:
                parts.append(f"wake 参照は {label}。content_kind={content_kind}。")
            if media_type is not None:
                parts.append(f"wake 参照 media_type={media_type}。")
        initiative_entry_check = client_context.get("initiative_entry_check")
        if isinstance(initiative_entry_check, dict):
            entry_kind = self._client_context_text(initiative_entry_check.get("entry_kind"), limit=24)
            entry_basis = self._client_context_text(initiative_entry_check.get("entry_basis"), limit=48)
            reason_summary = self._client_context_text(initiative_entry_check.get("reason_summary"), limit=180)
            if entry_kind is not None and reason_summary is not None:
                basis_text = f" basis={entry_basis}。" if entry_basis is not None else "。"
                parts.append(f"自律入口判定は {entry_kind}{basis_text}理由は {reason_summary}")

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
        # LLM 入力や保存用の正本文字列は、長さでは切らない。
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        if not stripped:
            return None
        return stripped
