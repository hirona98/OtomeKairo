import json
import unittest

from otomekairo.llm.contexts import CurrentInput, DecisionContext, PersonaContext, SpeechContext
from otomekairo.llm.prompts import (
    build_decision_messages, build_speech_messages,
    build_memory_candidate_review_messages, build_memory_retention_review_messages,
)
from otomekairo.service.input.pipeline import ServiceInputPipelineMixin


class ConfiguredActivityContextTests(unittest.TestCase):
    def test_memory_roles_receive_setting_evidence_without_turning_it_into_recalled_memory(self) -> None:
        topics = [{"topic_id": "topic:activity", "topic_summary": "公開情報から学び、交流する。"}]
        context = {"persona_context": {}, "input_text": "今の関心と活動は？", "episode": {}, "decision": {}, "speech_text": None,
                   "recall_hint": {}, "candidates": [], "correction_selection": {},
                   "memory_context": {"configured_activity_topics": topics, "recall_pack": {"active_topics": []}}}
        candidate = build_memory_candidate_review_messages(review_context=context)
        retention = build_memory_retention_review_messages(review_context=context)
        candidate_payload = json.loads(candidate[1]["content"].split("\n")[1])
        retention_payload = json.loads(retention[1]["content"].split("\n")[1])
        self.assertEqual(candidate_payload["memory_context"]["configured_activity_topics"], topics)
        self.assertEqual(candidate_payload["memory_context"]["recall_pack"]["active_topics"], [])
        self.assertEqual(retention_payload["evidence_context"]["configured_activity_topics"], topics)

    def test_expression_receives_wake_observations_before_world_state_adoption(self) -> None:
        current = CurrentInput(sender_kind="person", sender_ref="person:test", source_kind="user_message",
                               response_target_refs=("person:test",), interaction_context=None,
                               text="今のカメラ画像を説明して。")
        observation = {"vision_source_id": "camera:test", "fresh_source": "wake_observation",
                       "summary_text": "木の机に透明なカップが見える。", "age_label": "たった今"}
        persona = PersonaContext(display_name="Test", persona_prompt_text="テスト人格。",
                                 expression_addon=None, use_policy="判断の基底。")
        for capability in [
            {"id": "vision.capture", "fresh_world_state_by_vision_source": [observation]},
            {"id": "vision.capture", "available": True},
            {"id": "mcp.call_tool", "available": True},
        ]:
            with self.subTest(capability=capability):
                context = SpeechContext(
                    input_text=current.text, current_input=current, recent_turns=[], time_context={},
                    affect_context={}, drive_state_summary=None, foreground_world_state=[],
                    activity_context=None, ongoing_action_summary=None, initiative_context=None,
                    visual_observation_context=None, self_state_context=None, relationship_context=None,
                    prediction_error_context=None, workspace_context=None, recall_hint={}, recall_pack={},
                    decision={"kind": "speech"}, capability_decision_view=[capability],
                )
                messages = build_speech_messages(context=context, persona_context=persona)
                block = next(message["content"] for message in messages
                             if message["content"].startswith("<<<OTOMEKAIRO_CAPABILITY_DECISION_VIEW>>>"))
                self.assertEqual(json.loads(block.split("\n")[1]), [capability])

    def test_expression_receives_complete_conversation_affect_and_relationship_evidence(self) -> None:
        current = CurrentInput(sender_kind="person", sender_ref="person:test", source_kind="user_message",
                               response_target_refs=("person:test",), interaction_context=None,
                               text="このやり取りをどう受け止めた？")
        affect = {"mood_state": {"current_vad": {"v": 0.1, "a": 0.02, "d": 0.03}, "confidence": 0.3},
                  "affect_states": [], "recent_episode_affects": [{"target_scope_type": "self",
                      "target_scope_key": "self", "summary_text": "直前のやり取りへの穏やかな反応。" * 60,
                      "occurred_at": "2026-10-04T09:00:36+09:00"}]}
        relationship = {"relationship_items": [{"source": "recall_pack.relationship_model",
                         "summary_text": "相手が私の言葉を信頼していると述べた。"}]}
        turns = [{"role": "person", "event_id": "event:person-test", "speaker_ref": "person:test",
                  "text": "あなたの言葉を信頼しています。", "created_at": "2026-10-04T09:00:35+09:00"},
                 {"role": "assistant", "event_id": "event:self-test", "speaker_ref": "self",
                  "text": "事実を正確に受け止めることを大切にします。" * 60,
                  "created_at": "2026-10-04T09:00:36+09:00"}]
        context = SpeechContext(input_text=current.text, current_input=current, recent_turns=turns, time_context={},
            affect_context=affect, drive_state_summary=None, foreground_world_state=[], activity_context=None,
            ongoing_action_summary=None, initiative_context=None, visual_observation_context=None,
            self_state_context=None, relationship_context=relationship, prediction_error_context=None,
            workspace_context=None, recall_hint={}, recall_pack={}, decision={"kind": "speech"})
        persona = PersonaContext(display_name="Test", persona_prompt_text="落ち着いて対等に関わる。",
                                 expression_addon=None, use_policy="判断の基底。")
        expression = build_speech_messages(context=context, persona_context=persona)
        internal = next(m["content"] for m in expression
                        if m["content"].startswith("<<<OTOMEKAIRO_INTERNAL_CONTEXT>>>"))
        expression_payload = json.loads(internal.split("\n")[1])
        evidence = expression_payload["internal_context"]
        self.assertEqual(expression_payload["recent_turns"], turns)
        self.assertEqual(evidence["affect_context"], affect)
        self.assertEqual(evidence["relationship_context"], relationship)
        current_block = next(message["content"] for message in expression
                             if message["content"].startswith("<<<OTOMEKAIRO_CURRENT_INPUT>>>"))
        self.assertEqual(json.loads(current_block.split("\n")[1])["text"], current.text)

    def test_conversation_generation_receives_configuration_separately_from_memory(self) -> None:
        subject = ServiceInputPipelineMixin()
        topics = subject._configured_activity_topic_context(state={"periodic_thought_topics": [
            {"topic_id": "external", "enabled": True, "topic_summary": "外部の交流に関わる。",
             "min_periodic_thinking_interval_seconds": 150},
            {"topic_id": "disabled", "enabled": False, "topic_summary": "無効な活動。",
             "min_periodic_thinking_interval_seconds": 150},
        ]})
        self.assertEqual(topics, [{"topic_id": "external", "topic_summary": "外部の交流に関わる。"}])
        current = CurrentInput(sender_kind="person", sender_ref="person:test", source_kind="user_message",
                               response_target_refs=("person:test",), interaction_context=None,
                               text="今できる活動と実績を教えて。")
        common = dict(input_text=current.text, current_input=current, recent_turns=[], time_context={},
                      affect_context={}, drive_state_summary=None, foreground_world_state=None,
                      activity_context=None, ongoing_action_summary=None, initiative_context=None,
                      visual_observation_context=None, self_state_context=None, relationship_context=None,
                      prediction_error_context=None, workspace_context=None, recall_hint={},
                      recall_pack={"active_topics": []}, configured_activity_topics=topics)
        decision = DecisionContext(**common, trigger_kind="user_message", autonomous_run_summaries=None,
                                   capability_decision_view=[], capability_result_context=None,
                                   default_mode_context=None)
        pending_runs = [{"run_id": "run:test", "status": "paused",
                         "pause_reason": "paused_by_user_interaction",
                         "objective_summary": "後で一度報告する。"}]
        speech = SpeechContext(**common, decision={"kind": "speech"}, capability_decision_view=[],
                               autonomous_run_summaries=pending_runs)
        persona = PersonaContext(display_name="Test", persona_prompt_text="テスト人格。",
                                 expression_addon=None, use_policy="判断の基底。")
        for role, messages in [("decision", build_decision_messages(persona_context=persona, context=decision)),
                               ("speech", build_speech_messages(persona_context=persona, context=speech))]:
            with self.subTest(role=role):
                block = next(m["content"] for m in messages
                             if m["content"].startswith("<<<OTOMEKAIRO_CONFIGURED_ACTIVITY_TOPICS>>>"))
                self.assertEqual(json.loads(block.split("\n")[1]), topics)
                if role == "speech":
                    internal = next(m["content"] for m in messages
                                    if m["content"].startswith("<<<OTOMEKAIRO_INTERNAL_CONTEXT>>>"))
                    evidence = json.loads(internal.split("\n")[1])["internal_context"]
                    self.assertEqual(evidence["autonomous_run_summaries"], pending_runs)
                    self.assertEqual(evidence["recall_pack"]["active_topics"], [])
