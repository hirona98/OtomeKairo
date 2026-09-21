from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from otomekairo.interaction import InteractionContext, ParticipantContext
from otomekairo.service.app import OtomeKairoService


class WakeConversationContinuityTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.service = OtomeKairoService(Path(directory.name))
        self.state = self.service.store.read_state()
        preset = self.state["model_presets"][self.state["selected_model_preset_id"]]
        preset["model"] = "mock"
        preset["prompt_window"] = {"recent_turn_minutes": 30, "recent_turn_limit": 10}
        self.memory_set_id = self.state["selected_memory_set_id"]
        self.state["memory_sets"][self.memory_set_id]["embedding"]["model"] = "mock"
        self.state["wake_policy"]["observations"] = []
        self.service.store.write_state(self.state)
        self.now = datetime.fromisoformat("2026-09-21T16:15:00+09:00")
        clock = patch("otomekairo.service.input.mixin.local_now", return_value=self.now)
        clock.start()
        self.addCleanup(clock.stop)

    def _append(
        self,
        suffix: str,
        *,
        interaction: str | None = "interaction:a",
        role: str = "person",
        text: str = "話の続き",
        created_at: str = "2026-09-21T16:14:00+09:00",
        memory_set_id: str | None = None,
        kind: str | None = None,
    ) -> None:
        person = "person:b" if interaction == "interaction:b" else "person:a"
        self.service.store.append_events(events=[{
            "event_id": f"event:{suffix}",
            "cycle_id": f"cycle:{suffix}",
            "memory_set_id": memory_set_id or self.memory_set_id,
            "kind": kind or ("speech" if role == "assistant" else "conversation_input"),
            "role": role,
            "text": text,
            "interaction_ref": interaction,
            "speaker_ref": "self" if role == "assistant" else person,
            "participant_refs": [person] if interaction else [],
            "created_at": created_at,
        }])

    def test_self_evaluation_loads_recent_experiences_with_provenance(self) -> None:
        self._append("old", created_at="2026-09-21T15:00:00+09:00")
        self._append("other-memory", memory_set_id="memory_set:other")
        self._append("observation", kind="background_thinking")
        self._append("a", text="何について話そうか")
        self._append("b", interaction="interaction:b", text="別の場の話")
        self._append("question", role="assistant", text="大事にしていることを教えてもらえますか")
        self._append("ambient", interaction=None, role="assistant", text="雨が上がった。")

        groups = self.service._load_recent_interactions(self.state)
        by_ref = {g["interaction_ref"]: g["turns"] for g in groups}
        self.assertEqual(set(by_ref), {"interaction:a", "interaction:b", None})
        self.assertEqual([t["event_id"] for t in by_ref["interaction:a"]], ["event:a", "event:question"])
        self.assertEqual(by_ref["interaction:b"][0]["speaker_ref"], "person:b")
        self.assertEqual(by_ref["interaction:a"][1]["participant_refs"], ["person:a"])
        self.assertEqual(by_ref["interaction:a"][1]["created_at"], "2026-09-21T16:14:00+09:00")
        self.assertEqual(by_ref[None][0]["event_id"], "event:ambient")
        interaction = InteractionContext(
            interaction_ref="interaction:a", speaker_ref="person:a",
            participants=(ParticipantContext(person_ref="person:a", display_name="A"),),
        )
        scoped = self.service._load_recent_turns(self.state, interaction)
        self.assertEqual([t["text"] for t in scoped], ["何について話そうか", "大事にしていることを教えてもらえますか"])
        self.assertEqual(self.service._load_recent_turns(self.state), [])

        preset = self.state["model_presets"][self.state["selected_model_preset_id"]]
        preset["prompt_window"]["recent_turn_limit"] = 2
        for i in range(5):
            self._append(f"busy-{i}", interaction="interaction:b")
        by_ref = {g["interaction_ref"]: g["turns"] for g in self.service._load_recent_interactions(self.state)}
        self.assertEqual([t["event_id"] for t in by_ref["interaction:a"]], ["event:a", "event:question"])
        self.assertEqual([t["event_id"] for t in by_ref["interaction:b"]], ["event:busy-3", "event:busy-4"])

    def test_unscoped_wake_detects_conversation_added_during_observation(self) -> None:
        self._append("before")
        self.assertFalse(self.service._recent_turns_added_since(
            state=self.state, started_at=self.now.isoformat(),
        ))
        self._append("after", interaction="interaction:b", created_at="2026-09-21T16:15:01+09:00")
        self.assertTrue(self.service._recent_turns_added_since(
            state=self.state, started_at=self.now.isoformat(),
        ))

    def test_background_cycle_passes_pending_question_to_outward_decision(self) -> None:
        self._assert_background_cycle_boundaries(separated=False)

    def test_separated_cycle_keeps_conversations_out_of_self_activity(self) -> None:
        self._assert_background_cycle_boundaries(separated=True)

    def _assert_background_cycle_boundaries(self, *, separated: bool) -> None:
        self._append("invitation", text="僕について知りたいことはある")
        self._append("question", role="assistant", text="大事にしていることを教えてもらえますか")
        expected_groups = self.service._load_recent_interactions(self.state)
        generate_decision = self.service.llm.generate_decision

        def choose_speech(**kwargs):
            result = generate_decision(**kwargs)
            if kwargs["context"].comparison_scope in {"full", "outward_speech"}:
                result.update(kind="speech", reason_summary="周囲の変化に短く触れる。",
                              pending_intent=None, capability_request=None, autonomous_run=None)
                for stance in result["target_stances"]:
                    stance["stance"] = "advance" if stance["target"] == "outward_speech" else "hold"
            return result

        with (
            patch.object(self.service, "_background_thinking_should_proceed", return_value=True),
            patch.object(self.service, "_has_autonomous_initiative_context", return_value=True),
            patch.object(self.service, "_now_iso", return_value=self.now.isoformat()),
            patch.object(self.service, "_should_compare_self_activity_separately", return_value=separated),
            patch.object(type(self.service.llm), "generate_decision",
                         side_effect=choose_speech) as decisions,
            patch.object(type(self.service.llm), "generate_input_interpretation",
                         wraps=self.service.llm.generate_input_interpretation) as interpretations,
            patch.object(type(self.service.llm), "generate_speech",
                         wraps=self.service.llm.generate_speech) as expressions,
            patch.object(type(self.service.llm), "generate_disclosure_review", side_effect=lambda **kwargs: {
                "outcome": "allow", "speech_text": kwargs["review_context"]["candidate_speech"],
                "reason_code": "no_private_details",
            }) as reviews,
        ):
            result = self.service._execute_wake_cycle(
                state=self.state, client_context={}, trigger_kind="background_thinking",
            )

        self.assertEqual(result["result_kind"], "speech", result)
        contexts = [call.kwargs["context"] for call in decisions.call_args_list]
        self.assertEqual(
            {c.comparison_scope for c in contexts},
            {"self_activity", "outward_speech"} if separated else {"full"},
        )
        outward = [c for c in contexts if c.comparison_scope in {"full", "outward_speech"}]
        self.assertTrue(outward)
        for context in outward:
            self.assertEqual(context.recent_turns, [])
            self.assertEqual(context.recent_interactions, expected_groups)
            self.assertEqual(context.current_input.sender_kind, "system")
            self.assertEqual(context.current_input.response_target_refs, ())
            self.assertIsNone(context.current_input.interaction_context)
            self.assertIn("conversation_context", {c["kind"] for c in context.workspace_context["workspace_candidates"]})
        for context in contexts:
            if context.comparison_scope == "self_activity":
                self.assertEqual(context.recent_turns, [])
                self.assertIsNone(context.recent_interactions)
                self.assertNotIn("conversation_context", {c["kind"] for c in context.workspace_context["workspace_candidates"]})
        self.assertTrue(interpretations.called)
        for call in interpretations.call_args_list:
            self.assertEqual(call.kwargs["recent_turns"], [])
        self.assertEqual(expressions.call_count, 1)
        speech_context = expressions.call_args.kwargs["context"]
        self.assertEqual(speech_context.recent_turns, [])
        self.assertFalse(hasattr(speech_context, "recent_interactions"))
        self.assertEqual(speech_context.initiative_context.recent_turn_summary, [])
        self.assertEqual(reviews.call_count, 1)
        self.assertEqual(
            [s["event_id"] for s in reviews.call_args.kwargs["review_context"]["other_person_sources"]],
            ["event:invitation", "event:question"],
        )
        trace = self.service.store.get_cycle_trace(result["cycle_id"])
        summary = trace["decision_trace"]["internal_context_summary"]["recent_interaction_summary"]
        self.assertEqual(summary, [{
            "interaction_ref": "interaction:a", "event_ids": ["event:invitation", "event:question"],
        }])


if __name__ == "__main__":
    unittest.main()
