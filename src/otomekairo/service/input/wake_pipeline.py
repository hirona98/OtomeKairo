from __future__ import annotations

from typing import Any

from otomekairo.service.common import debug_log
from otomekairo.service.input.constants import WORLD_STATE_FOREGROUND_LIMIT


class ServiceInputWakePipelineMixin:
    def _run_wake_pipeline(
        self,
        *,
        state: dict[str, Any],
        started_at: str,
        trigger_kind: str,
        client_context: dict[str, Any],
        recent_turns: list[dict[str, Any]],
        selected_candidate: dict[str, Any] | None,
        pending_intent_selection: dict[str, Any] | None = None,
        cycle_id: str | None = None,
    ) -> tuple[dict[str, Any], str, dict[str, Any]]:
        cycle_label = self._debug_cycle_label(cycle_id)
        # 入力テキスト
        input_text = self._build_wake_input_text(
            state=state,
            client_context=client_context,
            selected_candidate=selected_candidate,
        )
        debug_log(
            "Wake",
            (
                f"{cycle_label} pipeline start selected_candidate="
                f"{selected_candidate.get('candidate_id') if isinstance(selected_candidate, dict) else '-'}"
            ),
            level="DEBUG",
        )

        # 起床ポリシー
        due = self._wake_is_due(state=state, current_time=started_at)
        if due["should_skip"]:
            debug_log("Wake", f"{cycle_label} skipped reason={self._clamp(due['reason_summary'])}")
            return (
                self._noop_pipeline(state=state, started_at=started_at, reason_summary=due["reason_summary"]),
                input_text,
                client_context,
            )

        # 定期観測
        client_context = self._run_wake_policy_observations(
            state=state,
            started_at=started_at,
            client_context=client_context,
            cycle_id=cycle_id,
        )
        input_text = self._build_wake_input_text(
            state=state,
            client_context=client_context,
            selected_candidate=selected_candidate,
        )
        if trigger_kind == "background_wake" and (
            self._user_response_cycle_active()
            or self._recent_turns_added_since(state=state, started_at=started_at)
        ):
            self._set_last_wake_at(started_at)
            reason_summary = "background wake の観測中にユーザー向け会話が進んだため、自発発話は行わない。"
            debug_log("Wake", f"{cycle_label} skipped user_response_changed")
            return (
                self._noop_pipeline(
                    state=state,
                    started_at=started_at,
                    reason_summary=reason_summary,
                ),
                input_text,
                client_context,
            )

        # クールダウン
        cooldown_reason = self._wake_cooldown_reason(current_time=started_at)
        if cooldown_reason is not None and not self._client_context_has_judgable_visual_observation(client_context):
            self._set_last_wake_at(started_at)
            debug_log("Wake", f"{cycle_label} skipped cooldown={self._clamp(cooldown_reason)}")
            return (
                self._noop_pipeline(state=state, started_at=started_at, reason_summary=cooldown_reason),
                input_text,
                client_context,
            )
        if cooldown_reason is not None:
            debug_log("Wake", f"{cycle_label} cooldown judged visual_observation={self._clamp(cooldown_reason)}")

        # 候補
        if selected_candidate is None:
            if not self._has_autonomous_initiative_context(
                state=state,
                current_time=started_at,
                client_context=client_context,
            ):
                retryable_observation_failure = self._client_context_has_retryable_wake_observation_failure(
                    client_context
                )
                if retryable_observation_failure:
                    self._set_wake_retry_after(started_at)
                else:
                    self._set_last_wake_at(started_at)
                if retryable_observation_failure:
                    reason_summary = "wake observation の vision source が未接続だったため、interval を消費せず短く再試行する。"
                elif (
                    isinstance(pending_intent_selection, dict)
                    and pending_intent_selection.get("selected_candidate_ref") == "none"
                    and isinstance(pending_intent_selection.get("selection_reason"), str)
                    and pending_intent_selection["selection_reason"].strip()
                ):
                    reason_summary = pending_intent_selection["selection_reason"].strip()
                else:
                    reason_summary = "起床機会は来たが、再評価すべき pending_intent 候補も自発評価に使う前景状態もまだ無い。"
                debug_log("Wake", f"{cycle_label} skipped no_candidate reason={self._clamp(reason_summary)}")
                return (
                    self._noop_pipeline(
                        state=state,
                        started_at=started_at,
                        reason_summary=reason_summary,
                    ),
                    input_text,
                    client_context,
                )
            self._set_last_wake_at(started_at)
            debug_log("Wake", f"{cycle_label} autonomous path no_selected_candidate")

        # 発話抑制
        if selected_candidate is not None:
            if self._was_recently_replied(
                dedupe_key=selected_candidate["dedupe_key"],
                current_time=started_at,
            ):
                self._set_last_wake_at(started_at)
                debug_log(
                    "Wake",
                    f"{cycle_label} skipped recently_replied candidate={selected_candidate.get('candidate_id')}",
                )
                return (
                    self._noop_pipeline(
                        state=state,
                        started_at=started_at,
                        reason_summary="同じ pending_intent 候補には最近 speech 済みのため、今回は再介入しない。",
                    ),
                    input_text,
                    client_context,
                )

            # トリガー集計
            self._set_last_wake_at(started_at)

        # 起床入力
        pipeline = self._run_input_pipeline(
            state=state,
            started_at=started_at,
            input_text=input_text,
            recent_turns=recent_turns,
            cycle_id=cycle_id,
            trigger_kind=trigger_kind,
            client_context=client_context,
            selected_candidate=selected_candidate,
            pending_intent_selection=pending_intent_selection,
        )
        return pipeline, input_text, client_context

    def _has_autonomous_initiative_context(
        self,
        *,
        state: dict[str, Any],
        current_time: str,
        client_context: dict[str, Any] | None = None,
    ) -> bool:
        if self._client_context_has_successful_wake_observation(client_context):
            return True
        drive_state_summary = self._summarize_drive_states(
            self._list_current_drive_states(
                state=state,
                current_time=current_time,
            )
        )
        if drive_state_summary:
            return True
        foreground_world_state = self._summarize_foreground_world_states(
            self._list_current_world_states(
                state=state,
                current_time=current_time,
                limit=WORLD_STATE_FOREGROUND_LIMIT,
            ),
            current_time=current_time,
        )
        if foreground_world_state:
            return True
        ongoing_action_summary = self._summarize_ongoing_action(
            self._current_ongoing_action(
                state=state,
                current_time=current_time,
            )
        )
        return isinstance(ongoing_action_summary, dict)

    def _client_context_has_successful_wake_observation(
        self,
        client_context: dict[str, Any] | None,
    ) -> bool:
        if not isinstance(client_context, dict):
            return False
        wake_observations = client_context.get("wake_observations")
        if not isinstance(wake_observations, list):
            return False
        visual_signals = self._compact_visual_observation_signals(
            client_context.get("visual_observation_signals")
        )
        if visual_signals:
            return any(self._visual_observation_signal_needs_wake_judgement(signal) for signal in visual_signals)
        for item in wake_observations:
            if not isinstance(item, dict) or item.get("status") != "succeeded":
                continue
            signal = self._compact_visual_observation_signal(item.get("visual_observation_signal"))
            if signal:
                return self._visual_observation_signal_needs_wake_judgement(signal)
            summary_text = item.get("visual_summary_text")
            if isinstance(summary_text, str) and summary_text.strip():
                return True
            image_count = item.get("image_count")
            if isinstance(image_count, int) and image_count > 0:
                return True
        return False

    def _client_context_has_judgable_visual_observation(
        self,
        client_context: dict[str, Any] | None,
    ) -> bool:
        if not isinstance(client_context, dict):
            return False
        signals = self._compact_visual_observation_signals(
            client_context.get("visual_observation_signals")
        )
        if any(self._visual_observation_signal_needs_wake_judgement(signal) for signal in signals):
            return True
        wake_observations = client_context.get("wake_observations")
        if not isinstance(wake_observations, list):
            return False
        for item in wake_observations:
            if not isinstance(item, dict):
                continue
            signal = self._compact_visual_observation_signal(item.get("visual_observation_signal"))
            if self._visual_observation_signal_needs_wake_judgement(signal):
                return True
        return False

    def _client_context_has_retryable_wake_observation_failure(
        self,
        client_context: dict[str, Any] | None,
    ) -> bool:
        if not isinstance(client_context, dict):
            return False
        wake_observations = client_context.get("wake_observations")
        if not isinstance(wake_observations, list) or not wake_observations:
            return False
        retryable_failure = False
        for item in wake_observations:
            if not isinstance(item, dict):
                continue
            if item.get("status") == "succeeded":
                return False
            reason_summary = item.get("reason_summary")
            if isinstance(reason_summary, str) and "対象 vision source が接続されていない" in reason_summary:
                retryable_failure = True
        return retryable_failure

    def _visual_observation_signal_needs_wake_judgement(self, signal: dict[str, Any] | None) -> bool:
        if not isinstance(signal, dict):
            return False
        return signal.get("change_state") in {
            "first_seen",
            "changed",
        }

    def _visual_observation_signal_is_judgable(self, signal: dict[str, Any] | None) -> bool:
        if not isinstance(signal, dict):
            return False
        return self._visual_observation_signal_needs_wake_judgement(signal)
