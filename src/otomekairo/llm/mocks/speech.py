from __future__ import annotations

from typing import Any

from otomekairo.llm.contexts import InitiativeContext, SpeechContext
from otomekairo.llm.contracts import INITIATIVE_ENTRY_ENTER_BASIS_VALUES


class LLMMockSpeechMixin:
    def generate_speech(
        self,
        *,
        role_definition: dict,
        persona_context: Any,
        context: SpeechContext,
    ) -> dict[str, Any]:
        # model確認
        self._assert_mock_model(role_definition)
        input_text = context.input_text
        recent_turns = context.recent_turns
        time_context = context.time_context
        affect_context = context.affect_context
        initiative_context = context.initiative_context
        recall_hint = context.recall_hint
        recall_pack = context.recall_pack
        decision = context.decision

        # コンテキスト
        persona_prompt = str(getattr(persona_context, "persona_prompt_text", "") or "").strip()
        primary_recall_focus = recall_hint["primary_recall_focus"]
        secondary_recall_focuses = self._secondary_recall_focuses(recall_hint)
        text = input_text.strip()
        conflict_items = recall_pack.get("conflicts", [])
        commitment_items = recall_pack.get("active_commitments", [])
        relationship_items = recall_pack.get("relationship_model", [])
        user_items = recall_pack.get("user_model", [])
        topic_items = recall_pack.get("active_topics", [])
        episode_items = recall_pack.get("episodic_evidence", [])
        event_items = recall_pack.get("event_evidence", [])
        mood_state = affect_context.get("mood_state") or {}
        recent_episode_affects = affect_context.get("recent_episode_affects", [])
        conflict_item = conflict_items[0] if conflict_items else None
        commitment_item = commitment_items[0] if commitment_items else None
        relationship_item = relationship_items[0] if relationship_items else None
        user_item = user_items[0] if user_items else None
        topic_item = topic_items[0] if topic_items else None
        episode_item = episode_items[0] if episode_items else None
        event_item = event_items[0] if event_items else None
        recent_episode_affect = recent_episode_affects[0] if recent_episode_affects else None
        event_basis = self._event_evidence_basis_text(event_item)
        current_vad = mood_state.get("current_vad") or {}
        current_valence = float(current_vad.get("v", 0.0)) if isinstance(current_vad, dict) else 0.0

        caution_prefix = self._mock_caution_prefix(
            conflict_item=conflict_item,
            recent_episode_affect=recent_episode_affect,
            current_valence=current_valence,
        )
        continuity_prefix = self._mock_continuity_prefix(
            primary_recall_focus=primary_recall_focus,
            secondary_recall_focuses=secondary_recall_focuses,
            episode_item=episode_item,
            event_basis=event_basis,
            recent_turns=recent_turns,
        )
        initiative_speech = self._mock_initiative_speech_text(
            initiative_context=initiative_context,
            decision=decision,
        )
        speech_text = initiative_speech or self._mock_contextual_speech_text(
            text=text,
            decision=decision,
            primary_recall_focus=primary_recall_focus,
            caution_prefix=caution_prefix,
            continuity_prefix=continuity_prefix,
            conflict_item=conflict_item,
            commitment_item=commitment_item,
            relationship_item=relationship_item,
            user_item=user_item,
            topic_item=topic_item,
            episode_item=episode_item,
            event_basis=event_basis,
            recent_turns=recent_turns,
        )

        # payload作成
        return {
            "speech_text": speech_text,
            "speech_style_notes": (
                f"persona_prompt_present={bool(persona_prompt)}; part_of_day={time_context.get('part_of_day', 'unknown')}"
            ),
            "confidence_note": "mock_model",
        }

    def _mock_caution_prefix(
        self,
        *,
        conflict_item: dict[str, Any] | None,
        recent_episode_affect: dict[str, Any] | None,
        current_valence: float,
    ) -> str:
        if conflict_item is not None:
            return "今は少し慎重に受け取っている。"
        if recent_episode_affect is not None and recent_episode_affect["affect_label"] in {"不安", "緊張", "迷い", "concern"}:
            return "少し慎重に聞いているよ。"
        if current_valence <= -0.25:
            return "少し気を引き締めて聞いているよ。"
        return ""

    def _mock_continuity_prefix(
        self,
        *,
        primary_recall_focus: str,
        secondary_recall_focuses: list[str],
        episode_item: dict[str, Any] | None,
        event_basis: str | None,
        recent_turns: list[dict[str, Any]],
    ) -> str:
        if primary_recall_focus == "episodic" or "episodic" not in secondary_recall_focuses:
            return ""
        if episode_item is None and event_basis is None and not recent_turns:
            return ""
        return "前の流れも踏まえると、"

    def _mock_contextual_speech_text(
        self,
        *,
        text: str,
        decision: dict[str, Any],
        primary_recall_focus: str,
        caution_prefix: str,
        continuity_prefix: str,
        conflict_item: dict[str, Any] | None,
        commitment_item: dict[str, Any] | None,
        relationship_item: dict[str, Any] | None,
        user_item: dict[str, Any] | None,
        topic_item: dict[str, Any] | None,
        episode_item: dict[str, Any] | None,
        event_basis: str | None,
        recent_turns: list[dict[str, Any]],
    ) -> str:
        if decision["requires_confirmation"]:
            return self._mock_confirmation_speech_text(
                text=text,
                caution_prefix=caution_prefix,
                relationship_item=relationship_item,
                episode_item=episode_item,
                event_basis=event_basis,
                conflict_item=conflict_item,
            )
        if primary_recall_focus == "user" and any(token in text for token in ("相談", "どうしたら", "悩", "困って")):
            return self._mock_user_focus_speech_text(
                text=text,
                caution_prefix=caution_prefix,
                continuity_prefix=continuity_prefix,
                user_item=user_item,
            )
        if primary_recall_focus == "commitment":
            return self._mock_commitment_speech_text(
                text=text,
                caution_prefix=caution_prefix,
                commitment_item=commitment_item,
                event_basis=event_basis,
            )
        if primary_recall_focus == "episodic":
            return self._mock_episodic_speech_text(
                text=text,
                caution_prefix=caution_prefix,
                episode_item=episode_item,
                event_basis=event_basis,
            )
        if primary_recall_focus == "preference":
            return f"{caution_prefix}{continuity_prefix}好みの話として受け取ったよ。{text} について、今の気分も含めて聞かせて。"
        return self._mock_default_topic_speech_text(
            text=text,
            caution_prefix=caution_prefix,
            continuity_prefix=continuity_prefix,
            topic_item=topic_item,
            recent_turns=recent_turns,
        )

    def _mock_confirmation_speech_text(
        self,
        *,
        text: str,
        caution_prefix: str,
        relationship_item: dict[str, Any] | None,
        episode_item: dict[str, Any] | None,
        event_basis: str | None,
        conflict_item: dict[str, Any] | None,
    ) -> str:
        basis_text = None
        if relationship_item is not None:
            basis_text = relationship_item["summary_text"]
        elif episode_item is not None:
            basis_text = episode_item["summary_text"]
        elif event_basis is not None:
            basis_text = event_basis
        elif conflict_item is not None:
            basis_text = conflict_item["summary_text"]
        if basis_text is not None:
            return f"{caution_prefix}{basis_text} という流れで受け取っているけれど、{text} の理解はこれで合っている？"
        return f"{caution_prefix}{text} の受け取りを断定せず確認したい。いまの理解で合っている？"

    def _mock_user_focus_speech_text(
        self,
        *,
        text: str,
        caution_prefix: str,
        continuity_prefix: str,
        user_item: dict[str, Any] | None,
    ) -> str:
        if user_item is not None:
            return (
                f"{caution_prefix}{continuity_prefix}{user_item['summary_text']} も踏まえて聞くね。"
                f"{text} の中で、今いちばん困っている点をもう少し教えて。"
            )
        return f"{caution_prefix}{continuity_prefix}状況は受け取ったよ。{text} の中で、今いちばん困っている点をもう少し教えて。"

    def _mock_commitment_speech_text(
        self,
        *,
        text: str,
        caution_prefix: str,
        commitment_item: dict[str, Any] | None,
        event_basis: str | None,
    ) -> str:
        if commitment_item is not None:
            if "どこまで" in text:
                return f"{commitment_item['summary_text']} の続きとして受け取ったよ。いまはどの範囲まで進めたい？"
            return f"{commitment_item['summary_text']} の続きとして受け取ったよ。{text} について、今回はどこまで進めたい？"
        if event_basis is not None:
            return f"{event_basis} の続きとして受け取ったよ。{text} について、今回はどこまで進めたい？"
        return f"{caution_prefix}その流れは覚えている前提で話すね。{text} に関して、今回どこまで進めたい？"

    def _mock_episodic_speech_text(
        self,
        *,
        text: str,
        caution_prefix: str,
        episode_item: dict[str, Any] | None,
        event_basis: str | None,
    ) -> str:
        if episode_item is not None:
            return f"{episode_item['summary_text']} の流れとして受け取ったよ。{text} のどの部分からつなげたい？"
        if event_basis is not None:
            return f"{event_basis} の場面として受け取ったよ。{text} のどの部分からつなげたい？"
        return f"{caution_prefix}その続きとして受け取ったよ。{text} のどの部分からつなげたい？"

    def _mock_default_topic_speech_text(
        self,
        *,
        text: str,
        caution_prefix: str,
        continuity_prefix: str,
        topic_item: dict[str, Any] | None,
        recent_turns: list[dict[str, Any]],
    ) -> str:
        topic_prefix = ""
        if topic_item is not None:
            topic_prefix = f"{topic_item['summary_text']} の流れで、"
        elif recent_turns:
            topic_prefix = "前の流れをつなげつつ、"
        return f"{caution_prefix}{continuity_prefix}{topic_prefix}{text}として受け取ったよ。"

    def _event_evidence_basis_text(self, item: dict[str, Any] | None) -> str | None:
        # 空
        if item is None:
            return None

        # スロット群
        for key in ("decision_or_result", "topic", "anchor", "tone_or_note"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        # 結果
        return None

    def _should_mock_autonomous_initiative_speech(self, initiative_context: InitiativeContext | None) -> bool:
        if initiative_context is None:
            return False
        initiative_entry_summary = initiative_context.initiative_entry_summary
        if (
            isinstance(initiative_entry_summary, dict)
            and initiative_entry_summary.get("entry_kind") == "enter"
            and initiative_entry_summary.get("entry_basis") in INITIATIVE_ENTRY_ENTER_BASIS_VALUES
        ):
            return True
        drive_summaries = initiative_context.drive_summaries
        if isinstance(drive_summaries, list) and drive_summaries:
            return True
        world_state_summary = initiative_context.world_state_summary
        if isinstance(world_state_summary, list):
            for item in world_state_summary:
                if not isinstance(item, dict):
                    continue
                if item.get("state_type") in {"schedule", "social_context", "body"}:
                    return True
        ongoing_action_summary = initiative_context.ongoing_action_summary
        if isinstance(ongoing_action_summary, dict):
            status = str(ongoing_action_summary.get("status") or "").strip()
            if status and status != "waiting_result":
                return True
        return False

    def _mock_initiative_speech_text(
        self,
        *,
        initiative_context: InitiativeContext | None,
        decision: dict[str, Any],
    ) -> str | None:
        if initiative_context is None or decision.get("kind") != "speech":
            return None
        if initiative_context.trigger_kind not in {"wake", "background_wake"}:
            return None
        pending_intent_summaries = initiative_context.pending_intent_summaries
        if isinstance(pending_intent_summaries, list) and pending_intent_summaries:
            return None
        selected_family = self._selected_initiative_family_entry(initiative_context)
        if selected_family is not None and selected_family.family == "ongoing_action":
            ongoing_action_summary = initiative_context.ongoing_action_summary
            if isinstance(ongoing_action_summary, dict):
                step_summary = ongoing_action_summary.get("step_summary")
                if isinstance(step_summary, str) and step_summary.strip():
                    return f"{step_summary.strip()} が前景にあるから、ここは続きとして少し前へ進めるね。"
        initiative_entry_summary = initiative_context.initiative_entry_summary
        if isinstance(initiative_entry_summary, dict):
            reason_summary = initiative_entry_summary.get("reason_summary")
            if isinstance(reason_summary, str) and reason_summary.strip():
                return f"{reason_summary.strip()}。短く触れておくね。"
        world_state_summary = initiative_context.world_state_summary
        if isinstance(world_state_summary, list):
            for item in world_state_summary:
                if not isinstance(item, dict):
                    continue
                summary_text = item.get("summary_text")
                if not isinstance(summary_text, str) or not summary_text.strip():
                    continue
                if item.get("state_type") == "schedule":
                    return f"{summary_text.strip()} という前景があるから、今のうちにそっと声をかけておくね。"
                if item.get("state_type") in {"social_context", "body"}:
                    return f"{summary_text.strip()} と見えているから、今の様子に少し触れてみるね。"
        drive_summaries = initiative_context.drive_summaries
        if isinstance(drive_summaries, list):
            for item in drive_summaries:
                if not isinstance(item, dict):
                    continue
                summary_text = item.get("summary_text")
                if isinstance(summary_text, str) and summary_text.strip():
                    return f"{summary_text.strip()} が前景にあるから、今のうちに少しだけ前へ出てみるね。"
        return "今の文脈には少し前へ出る理由があると見て、そっと声をかけるね。"
