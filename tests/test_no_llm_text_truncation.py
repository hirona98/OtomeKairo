import unittest

from otomekairo.evidence import EvidenceResolver
from otomekairo.llm.contexts import CurrentInput, InitiativeContext
from otomekairo.llm.prompts import _compact_speech_initiative_context
from otomekairo.memory.consolidator import MemoryConsolidator
from otomekairo.recall.builder import RecallBuilder
from otomekairo.recall.event_evidence import RecallEventEvidenceMixin
from otomekairo.service.input.inbound_observation import ServiceInputInboundObservationMixin
from otomekairo.service.input.pipeline import ServiceInputPipelineMixin
from otomekairo.service.spontaneous.wake import ServiceSpontaneousWakeMixin


class TextTruncationTests(unittest.TestCase):
    def test_speech_initiative_context_text_is_not_truncated(self) -> None:
        text = "a" * 240 + "末尾"
        context = InitiativeContext(
            trigger_kind="wake",
            opportunity_summary=text,
            initiative_entry_summary={"reason_summary": text},
            time_context_summary={},
            foreground_signal_summary={
                "reason_summary": text,
                "visual_observations": [{"summary_text": text}],
            },
            activity_context=None,
            initiative_baseline={},
            persona_context_summary={},
            runtime_state_summary={},
            recent_turn_summary=[],
            drive_summaries=[],
            pending_intent_summaries=[],
            world_state_summary=[],
            ongoing_action_summary=None,
            capability_summary={},
            candidate_families=[],
            selected_candidate_family=None,
            speech_timing_state={},
            suppression_summary={},
            speech_timing_summary=text,
        )

        payload = _compact_speech_initiative_context(context)

        self.assertEqual(payload["opportunity_summary"], text)
        self.assertEqual(payload["speech_timing_summary"], text)
        self.assertEqual(payload["initiative_entry_summary"]["reason_summary"], text)
        self.assertEqual(payload["foreground_signal_summary"]["reason_summary"], text)
        self.assertEqual(
            payload["foreground_signal_summary"]["visual_observations"][0]["summary_text"],
            text,
        )

    def test_client_context_text_is_not_truncated(self) -> None:
        service = ServiceSpontaneousWakeMixin()
        text = "b" * 240 + "末尾"

        self.assertEqual(service._client_context_text(text, limit=40), text)

    def test_workspace_context_text_is_not_truncated(self) -> None:
        service = ServiceInputPipelineMixin()
        text = "w" * 240 + "末尾"

        payload = service._build_workspace_context(
            current_input=CurrentInput(
                sender_kind="person",
                sender_ref="person:test",
                source_kind="user_message",
                response_target_refs=("person:test",),
                interaction_context=None,
                text=text,
            ),
            recall_pack={
                "active_commitments": [
                    {
                        "memory_unit_id": "memory_unit:long",
                        "summary_text": text,
                    }
                ],
            },
            drive_state_summary=None,
            foreground_world_state=[{"state_type": "environment", "scope": "world", "summary_text": text}],
            activity_context=None,
            ongoing_action_summary=None,
            autonomous_run_summaries=None,
            capability_decision_view=None,
            initiative_context=None,
            capability_result_context=None,
            visual_observation_context=None,
            self_state_context=None,
            relationship_context=None,
            prediction_error_context=None,
            default_mode_context=None,
        )

        summaries = [candidate["summary_text"] for candidate in payload["workspace_candidates"]]
        self.assertIn(text, summaries)
        self.assertTrue(all(summary.endswith("末尾") for summary in summaries if summary.startswith("w")))

    def test_workspace_activity_transition_text_is_not_truncated(self) -> None:
        service = ServiceInputPipelineMixin()
        text = "t" * 240 + "末尾"

        payload = service._build_workspace_context(
            current_input=CurrentInput(
                sender_kind="system",
                sender_ref=None,
                source_kind="background_thinking",
                response_target_refs=(),
                interaction_context=None,
                text="定期思考。",
            ),
            recall_pack={},
            drive_state_summary=None,
            foreground_world_state=None,
            activity_context={
                "current_activity": {
                    "label": "現在活動",
                    "actor": "person",
                    "transition": "switch",
                    "reason_summary": text,
                },
                "previous_activity": {
                    "label": "直前活動",
                    "actor": "person",
                    "duration_label": "約19時間",
                    "reason_summary": text,
                },
            },
            ongoing_action_summary=None,
            autonomous_run_summaries=None,
            capability_decision_view=None,
            initiative_context=None,
            capability_result_context=None,
            visual_observation_context=None,
            self_state_context=None,
            relationship_context=None,
            prediction_error_context=None,
            default_mode_context=None,
        )

        transition = next(
            candidate
            for candidate in payload["workspace_candidates"]
            if candidate["factor_ref"] == "activity:transition"
        )

        self.assertIn(text, transition["summary_text"])
        self.assertIn("末尾", transition["summary_text"])

    def test_memory_context_keeps_event_text_and_count_limit(self) -> None:
        consolidator = MemoryConsolidator.__new__(MemoryConsolidator)
        events = [
            {
                "kind": "speech",
                "role": "person",
                "reason_summary": f"理由{i}-" + ("c" * 220),
                "text": f"本文{i}-" + ("d" * 260) + "末尾",
            }
            for i in range(25)
        ]

        compact_events = [consolidator._compact_event_for_memory_context(event) for event in events]
        limited_events = consolidator._limit_memory_context_events(compact_events)

        self.assertEqual(len(limited_events), 24)
        self.assertEqual(limited_events[0]["text_summary"], events[0]["text"])
        self.assertTrue(limited_events[-1]["text_summary"].endswith("末尾"))

    def test_event_evidence_text_is_not_truncated(self) -> None:
        mixin = RecallEventEvidenceMixin()
        text = "e" * 220 + "末尾"
        reason = "f" * 220 + "理由末尾"

        payload = mixin._event_evidence_source_event(
            {
                "kind": "speech",
                "role": "person",
                "created_at": "2026-06-20T12:00:00+09:00",
                "text": text,
                "reason_summary": reason,
            }
        )

        self.assertEqual(payload["text"], text)
        self.assertEqual(payload["reason_summary"], reason)

    def test_memory_link_summary_is_not_truncated_for_selection_context(self) -> None:
        builder = RecallBuilder.__new__(RecallBuilder)
        related_summary = "g" * 220 + "末尾"
        summaries = builder._memory_link_summaries_by_memory_id(
            memory_links=[
                {
                    "label": "supports",
                    "source_memory_unit_id": "memory:1",
                    "target_memory_unit_id": "memory:2",
                    "target_memory_unit": {"summary_text": related_summary},
                }
            ],
            memory_unit_ids=["memory:1"],
        )

        representative = summaries["memory:1"]["representative_links"][0]
        self.assertEqual(representative["related_summary_text"], related_summary)
        self.assertTrue(representative["summary_text"].endswith("末尾"))

    def test_memory_link_context_text_is_not_truncated(self) -> None:
        builder = RecallBuilder.__new__(RecallBuilder)
        source_summary = "h" * 220 + "元末尾"
        target_summary = "i" * 220 + "先末尾"

        payload = builder._memory_link_trace_item(
            link={
                "memory_link_id": "link:1",
                "label": "supports",
                "source_memory_unit_id": "memory:1",
                "target_memory_unit_id": "memory:2",
                "source_memory_unit": {"summary_text": source_summary, "status": "confirmed"},
                "target_memory_unit": {"summary_text": target_summary, "status": "confirmed"},
            },
            label="supports",
            selected_ids={"memory:1"},
        )

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["source_summary_text"], source_summary)
        self.assertEqual(payload["target_summary_text"], target_summary)
        self.assertTrue(payload["summary_text"].endswith("先末尾"))

    def test_evidence_pack_event_text_is_not_truncated(self) -> None:
        resolver = EvidenceResolver.__new__(EvidenceResolver)
        text = "j" * 400 + "末尾"

        payload = resolver._event_evidence_item(
            {
                "event_id": "event:1",
                "kind": "speech",
                "role": "person",
                "created_at": "2026-06-20T12:00:00+09:00",
                "text": text,
            }
        )

        self.assertEqual(payload["text"], text)

    def test_inbound_observation_source_text_is_not_truncated(self) -> None:
        service = ServiceInputInboundObservationMixin()
        text = "k" * 800 + "末尾"
        structured_text = "l" * 1500 + "構造末尾"

        payload = service._inbound_observation_source_pack(
            mcp_server_id="elyth",
            tool_name="get_notifications",
            capability_response={
                "is_error": False,
                "content": [{"type": "text", "text": text}],
                "structured_content": {"body": structured_text},
            },
        )

        self.assertEqual(payload["content"][0]["text"], text)
        self.assertEqual(payload["structured_content"]["body"], structured_text)


if __name__ == "__main__":
    unittest.main()
