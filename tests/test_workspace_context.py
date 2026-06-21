import unittest

from otomekairo.llm.contexts import CurrentInput
from otomekairo.service.input.pipeline import ServiceInputPipelineMixin


class WorkspaceContextTests(unittest.TestCase):
    def test_workspace_context_includes_derived_view_candidates(self) -> None:
        service = ServiceInputPipelineMixin()
        text = "派生 view からの前景化候補"

        payload = service._build_workspace_context(
            current_input=CurrentInput(
                sender="system",
                source_kind="wake",
                response_target="none",
                text="自律判断機会",
            ),
            recall_pack={},
            drive_state_summary=None,
            foreground_world_state=None,
            activity_context=None,
            ongoing_action_summary=None,
            autonomous_run_summaries=None,
            capability_decision_view=None,
            initiative_context=None,
            capability_result_context=None,
            visual_observation_context=None,
            self_state_context={
                "sensory_confidence": [
                    {
                        "channel": "visual",
                        "summary_text": text,
                        "confidence_hint": "low",
                    }
                ],
            },
            relationship_context={
                "relationship_items": [
                    {
                        "item_ref": "memory_unit:relationship",
                        "source": "recall_pack.relationship_model",
                        "summary_text": text,
                    }
                ],
            },
            prediction_error_context={
                "signals": [
                    {
                        "summary_text": text,
                        "signal_kind": "world_state_difference",
                        "changed": True,
                    }
                ],
            },
            default_mode_context={
                "resurfacing_candidates": [
                    {
                        "candidate_ref": "default_mode:active_commitments:0",
                        "source": "recall_pack.active_commitments",
                        "summary_text": text,
                        "resurfacing_policy": "即発話しない。",
                    }
                ],
            },
        )

        kinds = {candidate["kind"] for candidate in payload["workspace_candidates"]}

        self.assertIn("self_state", kinds)
        self.assertIn("relationship", kinds)
        self.assertIn("prediction_error", kinds)
        self.assertIn("default_mode", kinds)

    def test_default_mode_context_keeps_resurfacing_as_candidate(self) -> None:
        service = ServiceInputPipelineMixin()
        text = "まだ気になっている未完了"

        payload = service._build_default_mode_context(
            recall_pack={
                "active_commitments": [
                    {
                        "memory_unit_id": "memory_unit:commitment",
                        "summary_text": text,
                    }
                ],
            },
            affect_context={"recent_episode_affects": []},
        )

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["resurfacing_candidates"][0]["summary_text"], text)
        self.assertEqual(
            payload["resurfacing_candidates"][0]["resurfacing_policy"],
            "即発話せず、workspace の前景化候補として扱う。",
        )


if __name__ == "__main__":
    unittest.main()
