import unittest

from otomekairo.llm.contexts import CurrentInput, InitiativeContext
from otomekairo.llm.prompts import _compact_speech_initiative_context
from otomekairo.memory.consolidator import MemoryConsolidator
from otomekairo.recall.builder import RecallBuilder
from otomekairo.recall.event_evidence import RecallEventEvidenceMixin
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
            intervention_state={},
            suppression_summary={},
            intervention_risk_summary=text,
        )

        payload = _compact_speech_initiative_context(context)

        self.assertEqual(payload["opportunity_summary"], text)
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
                sender="user",
                source_kind="user_message",
                response_target="user",
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
        )

        summaries = [candidate["summary_text"] for candidate in payload["workspace_candidates"]]
        self.assertIn(text, summaries)
        self.assertTrue(all(summary.endswith("末尾") for summary in summaries if summary.startswith("w")))

    def test_memory_context_keeps_event_text_and_count_limit(self) -> None:
        consolidator = MemoryConsolidator.__new__(MemoryConsolidator)
        events = [
            {
                "kind": "speech",
                "role": "user",
                "reason_summary": f"理由{i}-" + ("c" * 220),
                "text": f"本文{i}-" + ("d" * 260) + "末尾",
            }
            for i in range(13)
        ]

        compact_events = [consolidator._compact_event_for_memory_context(event) for event in events]
        limited_events = consolidator._limit_memory_context_events(compact_events)

        self.assertEqual(len(limited_events), 12)
        self.assertEqual(limited_events[0]["text_summary"], events[0]["text"])
        self.assertTrue(limited_events[-1]["text_summary"].endswith("末尾"))

    def test_event_evidence_text_is_not_truncated(self) -> None:
        mixin = RecallEventEvidenceMixin()
        text = "e" * 220 + "末尾"
        reason = "f" * 220 + "理由末尾"

        payload = mixin._event_evidence_source_event(
            {
                "kind": "speech",
                "role": "user",
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


if __name__ == "__main__":
    unittest.main()
