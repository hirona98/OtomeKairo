from __future__ import annotations

import threading
from datetime import timedelta
from typing import Any

from otomekairo.llm import LLMError
from otomekairo.recall import RecallPackSelectionError
from otomekairo.service_common import (
    BACKGROUND_WAKE_POLL_SECONDS,
    WAKE_REPLY_COOLDOWN_MINUTES,
    debug_log,
)
from otomekairo.service_spontaneous_pending_intent import PendingIntentSelectionError


class ServiceSpontaneousWakeMixin:
    def _emit_wake_assistant_message_event(
        self,
        *,
        cycle_id: str,
        trigger_kind: str,
        client_context: dict[str, Any],
        pipeline: dict[str, Any],
    ) -> None:
        if trigger_kind not in {"wake", "background_wake"}:
            return
        reply_payload = pipeline.get("reply_payload")
        if not isinstance(reply_payload, dict):
            debug_log("Wake", f"{self._short_cycle_id(cycle_id)} assistant_message skipped no_reply")
            return
        target_client_id = self._wake_assistant_message_target_client_id(client_context)
        if target_client_id is None:
            debug_log("Wake", f"{self._short_cycle_id(cycle_id)} assistant_message skipped no_client")
            return

        event = {
            "event_id": self._next_stream_event_id(),
            "type": "assistant_message",
            "data": {
                "cycle_id": cycle_id,
                "source_kind": trigger_kind,
                "trigger_kind": trigger_kind,
                "system_text": f"[{trigger_kind}]",
                "message": reply_payload["reply_text"],
            },
        }
        sent = self._event_stream_registry.send_to_client(target_client_id, event)
        debug_log(
            "Wake",
            (
                f"{self._short_cycle_id(cycle_id)} assistant_message sent={sent} "
                f"client={target_client_id} reply_chars={len(reply_payload['reply_text'])}"
            ),
        )

    def _wake_assistant_message_target_client_id(self, client_context: dict[str, Any]) -> str | None:
        client_id = self._client_context_text(client_context.get("client_id"), limit=128)
        if client_id is not None:
            return client_id

        wake_observations = client_context.get("wake_observations")
        if not isinstance(wake_observations, list):
            return None
        for observation in wake_observations:
            if not isinstance(observation, dict) or observation.get("status") != "succeeded":
                continue
            if observation.get("capability_id") != "vision.capture":
                continue
            vision_source_id = self._client_context_text(observation.get("vision_source_id"), limit=128)
            if vision_source_id is None:
                continue
            vision_source = self._event_stream_registry.get_vision_source(vision_source_id)
            if not isinstance(vision_source, dict):
                continue
            source_client_id = self._client_context_text(vision_source.get("client_id"), limit=128)
            if source_client_id is not None:
                return source_client_id
        return None

    def _execute_wake_cycle(
        self,
        *,
        state: dict[str, Any],
        client_context: dict[str, Any],
        trigger_kind: str,
    ) -> dict[str, Any]:
        # 直列化実行
        with self._wake_execution_lock:
            input_event_kind = "background_wake" if trigger_kind == "background_wake" else "wake"
            cycle_id = self._new_cycle_id()
            started_at = self._now_iso()
            recent_turns = self._load_recent_turns(state)
            runtime_summary = self._build_runtime_summary(state)
            pending_intent_selection = self._empty_pending_intent_selection_trace()
            input_text = self._build_wake_input_text(
                state=state,
                client_context=client_context,
                selected_candidate=None,
            )
            debug_log(
                "Wake",
                (
                    f"{self._short_cycle_id(cycle_id)} start trigger={trigger_kind} "
                    f"recent_turns={len(recent_turns)} context_keys={self._debug_context_keys(client_context)}"
                ),
            )

            try:
                # due / cooldown
                due = self._wake_is_due(state=state, current_time=started_at)
                if due["should_skip"]:
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
                        pipeline=pipeline,
                        trigger_kind=trigger_kind,
                        input_event_kind=input_event_kind,
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
                    recent_turns=recent_turns,
                    selected_candidate=selected_candidate,
                    pending_intent_selection=pending_intent_selection,
                    cycle_id=cycle_id,
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
                    input_event_kind=input_event_kind,
                    input_event_role="system",
                    consolidate_memory=self._should_consolidate_spontaneous_cycle(
                        trigger_kind=trigger_kind,
                        pipeline=pipeline,
                        observation_summary=None,
                        client_context=client_context,
                    ),
                    pending_intent_selection=pending_intent_selection,
                )

                # 返信後処理
                self._record_wake_outcome(
                    current_time=started_at,
                    decision=pipeline["decision"],
                    selected_candidate=selected_candidate,
                    client_context=client_context,
                )
                self._emit_wake_assistant_message_event(
                    cycle_id=cycle_id,
                    trigger_kind=trigger_kind,
                    client_context=client_context,
                    pipeline=pipeline,
                )
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
                )
                return self._finalize_cycle_failure(
                    cycle_id=cycle_id,
                    started_at=started_at,
                    state=state,
                    runtime_summary=runtime_summary,
                    input_text=input_text,
                    client_context=client_context,
                    failure_reason=str(exc),
                    trigger_kind=trigger_kind,
                    input_event_kind=input_event_kind,
                    input_event_role="system",
                    failure_event_kind="pending_intent_selection_failure",
                    failure_event_payload={
                        "failure_stage": exc.failure_stage,
                    },
                    pending_intent_selection=exc.pending_intent_selection,
                )
            except RecallPackSelectionError as exc:
                debug_log(
                    "Wake",
                    (
                        f"{self._short_cycle_id(cycle_id)} failed stage={exc.failure_stage} "
                        f"error={type(exc).__name__}: {self._clamp(str(exc))}"
                    ),
                )
                return self._finalize_cycle_failure(
                    cycle_id=cycle_id,
                    started_at=started_at,
                    state=state,
                    runtime_summary=runtime_summary,
                    input_text=input_text,
                    client_context=client_context,
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
                )
            except (LLMError, KeyError, ValueError) as exc:
                capability_request_summary, ongoing_action_transition_summary = self._exception_capability_dispatch_trace(
                    exc
                )
                debug_log(
                    "Wake",
                    f"{self._short_cycle_id(cycle_id)} failed error={type(exc).__name__}: {self._clamp(str(exc))}",
                )
                return self._finalize_cycle_failure(
                    cycle_id=cycle_id,
                    started_at=started_at,
                    state=state,
                    runtime_summary=runtime_summary,
                    input_text=input_text,
                    client_context=client_context,
                    failure_reason=str(exc),
                    trigger_kind=trigger_kind,
                    input_event_kind=input_event_kind,
                    input_event_role="system",
                    pending_intent_selection=pending_intent_selection,
                    capability_request_summary=capability_request_summary,
                    ongoing_action_transition_summary=ongoing_action_transition_summary,
                )

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
                    trigger_kind="background_wake",
                )
            except Exception as exc:  # noqa: BLE001
                debug_log("Wake", f"background loop error={type(exc).__name__}: {self._clamp(str(exc))}")
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

    def _record_wake_outcome(
        self,
        *,
        current_time: str,
        decision: dict[str, Any],
        selected_candidate: dict[str, Any] | None,
        client_context: dict[str, Any] | None = None,
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
                self._record_desktop_observation_prompted_locked(
                    current_time=current_time,
                    client_context=client_context,
                )
            return

        # 将来行動
        if decision.get("kind") == "pending_intent":
            return

    def _record_desktop_observation_prompted_locked(
        self,
        *,
        current_time: str,
        client_context: dict[str, Any] | None,
    ) -> None:
        if not isinstance(client_context, dict):
            return
        signal = client_context.get("desktop_observation_signal")
        if not isinstance(signal, dict) or signal.get("reply_eligibility") not in {
            "eligible",
        }:
            return
        observation_id = signal.get("observation_id")
        scene_signature = signal.get("scene_signature")
        if not isinstance(observation_id, str) or not observation_id.strip():
            return
        if not isinstance(scene_signature, str) or not scene_signature.strip():
            return
        runtime = self._wake_observation_runtime_state.get(observation_id.strip())
        if not isinstance(runtime, dict):
            return
        runtime["last_prompted_scene_signature"] = scene_signature.strip()
        runtime["last_prompted_at"] = current_time
        runtime.pop("pending_novel_scene", None)

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
            return "直近の自発 reply から cooldown 中のため、発話量と頻度を控えめに調整する。"
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
        state: dict[str, Any],
        client_context: dict[str, Any],
        selected_candidate: dict[str, Any] | None,
    ) -> str:
        # プレフィックス
        parts = ["定期起床。"]
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
            parts.append("drive_state と world_state を見て、今は前へ出る価値があるかを見たい。")
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
        wake_observation_summary = self._client_context_text(
            client_context.get("wake_observation_summary"),
            limit=360,
        )
        if isinstance(wake_observation_summary, str):
            parts.append(f"定期観測では、{wake_observation_summary}")
        desktop_signal = self._compact_desktop_observation_signal(
            client_context.get("desktop_observation_signal")
        )
        if isinstance(desktop_signal, dict):
            novelty_kind = desktop_signal.get("novelty_kind")
            reply_eligibility = desktop_signal.get("reply_eligibility")
            cooldown_active = desktop_signal.get("cooldown_active")
            reason_summary = desktop_signal.get("reason_summary")
            if isinstance(novelty_kind, str) and isinstance(reply_eligibility, str):
                cooldown_part = ""
                if isinstance(cooldown_active, bool):
                    cooldown_part = f", cooldown_active={str(cooldown_active).lower()}"
                parts.append(
                    f"desktop観測シグナルは novelty={novelty_kind}, reply_eligibility={reply_eligibility}{cooldown_part}。"
                )
            if isinstance(reason_summary, str):
                parts.append(f"desktop観測理由は {reason_summary}")

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
            image_summary_text = self._client_context_text(client_context.get("image_summary_text"), limit=160)
            if isinstance(image_summary_text, str):
                parts.append(f"画像観測では、{image_summary_text}")

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
