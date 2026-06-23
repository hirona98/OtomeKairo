from __future__ import annotations

from typing import Any

from otomekairo.llm.contracts import INITIATIVE_ENTRY_ENTER_BASIS_VALUES
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
            reason_summary = "定期起床の観測中にユーザー向け会話が進んだため、自発発話は行わない。"
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

        # 候補
        if selected_candidate is None:
            client_context = self._run_autonomous_initiative_entry_check(
                state=state,
                current_time=started_at,
                trigger_kind=trigger_kind,
                client_context=client_context,
                recent_turns=recent_turns,
                cycle_id=cycle_id,
            )
            input_text = self._build_wake_input_text(
                state=state,
                client_context=client_context,
                selected_candidate=selected_candidate,
            )
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
                    reason_summary = "起床前観測 の vision source が未接続だったため、interval を消費せず短く再試行する。"
                elif (
                    isinstance(pending_intent_selection, dict)
                    and pending_intent_selection.get("selected_candidate_ref") == "none"
                    and isinstance(pending_intent_selection.get("selection_reason"), str)
                    and pending_intent_selection["selection_reason"].strip()
                ):
                    reason_summary = pending_intent_selection["selection_reason"].strip()
                elif self._initiative_entry_check_skip_reason(client_context) is not None:
                    reason_summary = self._initiative_entry_check_skip_reason(client_context) or ""
                else:
                    reason_summary = "起床機会は来たが、外向きの自律判断へ進める入口はまだ無い。"
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
        if self._client_context_has_initiative_entry(client_context):
            return True
        if self._client_context_has_judgable_visual_observation(client_context):
            return True
        drive_state_summary = self._summarize_drive_states(
            self._list_current_drive_states(
                state=state,
                current_time=current_time,
            )
        )
        if drive_state_summary:
            return True
        ongoing_action_summary = self._summarize_ongoing_action(
            self._current_ongoing_action(
                state=state,
                current_time=current_time,
            )
        )
        return isinstance(ongoing_action_summary, dict)

    def _run_autonomous_initiative_entry_check(
        self,
        *,
        state: dict[str, Any],
        current_time: str,
        trigger_kind: str,
        client_context: dict[str, Any],
        recent_turns: list[dict[str, Any]],
        cycle_id: str | None,
    ) -> dict[str, Any]:
        if self._has_direct_autonomous_initiative_entry(
            state=state,
            current_time=current_time,
        ):
            return client_context
        if self._client_context_has_judgable_visual_observation(client_context):
            return {
                **client_context,
                "autonomous_visual_observation_direct_entry": True,
            }
        foreground_world_state = self._summarize_foreground_world_states(
            self._list_current_world_states(
                state=state,
                current_time=current_time,
                limit=WORLD_STATE_FOREGROUND_LIMIT,
            ),
            current_time=current_time,
        )
        if not self._has_initiative_entry_check_material(
            client_context=client_context,
            foreground_world_state=foreground_world_state,
        ):
            return client_context

        source_pack = self._build_initiative_entry_check_source_pack(
            state=state,
            current_time=current_time,
            trigger_kind=trigger_kind,
            client_context=client_context,
            recent_turns=recent_turns,
            foreground_world_state=foreground_world_state,
        )
        role_definition = state["model_presets"][state["selected_model_preset_id"]]["roles"][
            "pending_intent_selection"
        ]
        persona_context = self._build_selected_persona_context(state=state, role="initiative_entry_check")
        payload = self.llm.generate_initiative_entry_check(
            role_definition=role_definition,
            persona_context=persona_context,
            source_pack=source_pack,
        )
        entry_kind = str(payload["entry_kind"]).strip()
        entry_basis = str(payload["entry_basis"]).strip()
        reason_summary = str(payload["reason_summary"]).strip()
        trace = {
            "entry_kind": entry_kind,
            "entry_basis": entry_basis,
            "reason_summary": reason_summary,
            "result_status": "succeeded",
        }
        debug_log(
            "Wake",
            (
                f"{self._debug_cycle_label(cycle_id)} initiative_entry "
                f"kind={entry_kind} basis={entry_basis} reason={self._clamp(reason_summary)}"
            ),
        )
        return {
            **client_context,
            "initiative_entry_check": trace,
        }

    def _has_direct_autonomous_initiative_entry(
        self,
        *,
        state: dict[str, Any],
        current_time: str,
    ) -> bool:
        drive_state_summary = self._summarize_drive_states(
            self._list_current_drive_states(
                state=state,
                current_time=current_time,
            )
        )
        if drive_state_summary:
            return True
        ongoing_action_summary = self._summarize_ongoing_action(
            self._current_ongoing_action(
                state=state,
                current_time=current_time,
            )
        )
        return isinstance(ongoing_action_summary, dict)

    def _has_initiative_entry_check_material(
        self,
        *,
        client_context: dict[str, Any],
        foreground_world_state: list[dict[str, Any]] | None,
    ) -> bool:
        if self._client_context_has_successful_wake_observation(client_context):
            return True
        return bool(foreground_world_state)

    def _build_initiative_entry_check_source_pack(
        self,
        *,
        state: dict[str, Any],
        current_time: str,
        trigger_kind: str,
        client_context: dict[str, Any],
        recent_turns: list[dict[str, Any]],
        foreground_world_state: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        persona_context = self._build_selected_persona_context(state=state, role="initiative_entry_check")
        input_context: dict[str, Any] = {
            "trigger_kind": trigger_kind,
            "current_time": current_time,
            "source": self._client_context_text(client_context.get("source"), limit=48) or trigger_kind,
        }
        for key, limit in (
            ("active_app", 80),
            ("window_title", 120),
            ("locale", 32),
            ("wake_observation_summary", 360),
        ):
            value = self._client_context_text(client_context.get(key), limit=limit)
            if value is not None:
                input_context[key] = value
        visual_observations = self._compact_visual_observation_signals(
            client_context.get("visual_observation_signals")
        )
        source_pack: dict[str, Any] = {
            "persona_context": persona_context.to_prompt_payload(),
            "input_context": input_context,
            "recent_turns": self._initiative_entry_check_recent_turns(recent_turns),
            "foreground_world_state": self._initiative_entry_check_world_state(foreground_world_state),
            "visual_observations": visual_observations,
            "entry_policy": {
                "allow_enter": True,
                "allow_skip": True,
                "enter_bases": sorted(INITIATIVE_ENTRY_ENTER_BASIS_VALUES),
                "observation_only_is_skip": True,
                "same_activity_detail_change_is_skip": True,
                "meaningful_activity_transition_is_enter_candidate": True,
            },
        }
        activity_context = self._summarize_activity_context(
            self.store.get_current_activity_state(
                memory_set_id=state["selected_memory_set_id"],
                current_time=current_time,
            ),
            current_time=current_time,
        )
        if isinstance(activity_context, dict):
            source_pack["activity_context"] = activity_context
        return source_pack

    def _initiative_entry_check_recent_turns(
        self,
        recent_turns: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
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
                    "text": text.strip(),
                }
            )
        return compact_turns

    def _initiative_entry_check_world_state(
        self,
        foreground_world_state: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        compact_items: list[dict[str, Any]] = []
        for item in foreground_world_state or []:
            if not isinstance(item, dict):
                continue
            compact_item: dict[str, Any] = {}
            for key, limit in (
                ("state_type", 48),
                ("scope_type", 48),
                ("scope_key", 96),
                ("summary_text", 180),
                ("reason_summary", 180),
                ("source_owner", 32),
                ("freshness_hint", 32),
            ):
                value = self._client_context_text(item.get(key), limit=limit)
                if value is not None:
                    compact_item[key] = value
            salience = item.get("salience")
            if isinstance(salience, (int, float)):
                compact_item["salience"] = round(max(0.0, min(float(salience), 1.0)), 2)
            if compact_item:
                compact_items.append(compact_item)
        return compact_items[:6]

    def _client_context_has_initiative_entry(
        self,
        client_context: dict[str, Any] | None,
    ) -> bool:
        if not isinstance(client_context, dict):
            return False
        entry_check = client_context.get("initiative_entry_check")
        if not isinstance(entry_check, dict) or entry_check.get("entry_kind") != "enter":
            return False
        return entry_check.get("entry_basis") in INITIATIVE_ENTRY_ENTER_BASIS_VALUES

    def _initiative_entry_check_skip_reason(
        self,
        client_context: dict[str, Any] | None,
    ) -> str | None:
        if not isinstance(client_context, dict):
            return None
        entry_check = client_context.get("initiative_entry_check")
        if not isinstance(entry_check, dict) or entry_check.get("entry_kind") != "skip":
            return None
        return self._client_context_text(entry_check.get("reason_summary"), limit=180)

    def _client_context_has_judgable_visual_observation(
        self,
        client_context: dict[str, Any] | None,
    ) -> bool:
        if not isinstance(client_context, dict):
            return False
        visual_signals = self._compact_visual_observation_signals(
            client_context.get("visual_observation_signals")
        )
        return any(self._visual_observation_signal_needs_wake_judgement(signal) for signal in visual_signals)

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
