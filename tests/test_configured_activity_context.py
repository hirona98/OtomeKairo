import json
import unittest

from otomekairo.llm.contexts import CurrentInput, DecisionContext, PersonaContext, SpeechContext
from otomekairo.llm.prompts import (
    build_decision_messages, build_speech_messages, build_speech_grounding_review_messages,
)
from otomekairo.service.input.pipeline import ServiceInputPipelineMixin


class ConfiguredActivityContextTests(unittest.TestCase):
    def test_conversation_generation_and_review_receive_configuration_separately_from_memory(self) -> None:
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
                    self.assertEqual(json.loads(internal.split("\n")[1])["internal_context"]["autonomous_run_summaries"], pending_runs)
        review = build_speech_grounding_review_messages(persona_context=persona, context=speech,
                                                        candidate_speech="交流の手段は現在使えません。")
        payload = json.loads(review[1]["content"].split("\n")[1])
        self.assertEqual(payload["configured_activity_topics"], topics)
        self.assertEqual(payload["autonomous_run_summaries"], pending_runs)
        self.assertEqual(payload["recall_pack"]["active_topics"], [])
