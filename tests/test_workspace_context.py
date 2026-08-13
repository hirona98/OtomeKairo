import unittest

from otomekairo.llm.contexts import CurrentInput
from otomekairo.llm.prompts import (
    _compact_default_mode_context,
    _compact_relationship_context,
)
from otomekairo.service.input.pipeline import (
    WORKSPACE_CANDIDATE_LIMIT,
    ServiceInputPipelineMixin,
)


class WorkspaceContextTests(unittest.TestCase):
    def test_workspace_context_includes_derived_view_candidates(self) -> None:
        service = ServiceInputPipelineMixin()
        text = "派生 view からの前景化候補"

        payload = service._build_workspace_context(
            current_input=CurrentInput(
                sender_kind="system",
                sender_ref=None,
                source_kind="wake",
                response_target_refs=(),
                interaction_context=None,
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
        self.assertIn(
            "relationship:memory_unit:relationship",
            [candidate["factor_ref"] for candidate in payload["workspace_candidates"]],
        )

    def test_workspace_context_keeps_all_relationship_items_as_candidates(self) -> None:
        service = ServiceInputPipelineMixin()
        relationship_items = [
            {
                "item_ref": f"memory_unit:{index}",
                "source": "recall_pack.relationship_model",
                "summary_text": f"関係{index}",
            }
            for index in range(6)
        ]

        payload = service._build_workspace_context(
            current_input=CurrentInput(
                sender_kind="system",
                sender_ref=None,
                source_kind="wake",
                response_target_refs=(),
                interaction_context=None,
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
            self_state_context=None,
            relationship_context={"relationship_items": relationship_items},
            prediction_error_context=None,
            default_mode_context=None,
        )

        refs = [candidate["factor_ref"] for candidate in payload["workspace_candidates"]]
        self.assertEqual(
            [f"relationship:memory_unit:{index}" for index in range(6)],
            [ref for ref in refs if ref.startswith("relationship:memory_unit:")],
        )

    def test_workspace_context_retains_relationship_candidates_after_limit(self) -> None:
        service = ServiceInputPipelineMixin()
        item_ref = "memory_unit:9c92fccea3194e0ca2366c391722584a"

        payload = service._build_workspace_context(
            current_input=CurrentInput(
                sender_kind="system",
                sender_ref=None,
                source_kind="wake",
                response_target_refs=(),
                interaction_context=None,
                text="自律判断機会",
            ),
            recall_pack={},
            drive_state_summary=None,
            foreground_world_state=None,
            activity_context=None,
            ongoing_action_summary=None,
            autonomous_run_summaries=None,
            capability_decision_view=[
                {
                    "id": f"capability.{index}",
                    "available": True,
                    "what_it_does": f"能力{index}",
                }
                for index in range(WORKSPACE_CANDIDATE_LIMIT)
            ],
            initiative_context=None,
            capability_result_context=None,
            visual_observation_context=None,
            self_state_context=None,
            relationship_context={
                "relationship_items": [
                    {
                        "item_ref": item_ref,
                        "source": "recall_pack.relationship_model",
                        "summary_text": "関係温度の現在 view",
                    }
                ],
            },
            prediction_error_context=None,
            default_mode_context=None,
        )

        refs = [candidate["factor_ref"] for candidate in payload["workspace_candidates"]]
        self.assertIn(f"relationship:{item_ref}", refs)
        self.assertGreater(payload["total_candidate_count"], WORKSPACE_CANDIDATE_LIMIT)
        self.assertGreater(len(payload["workspace_candidates"]), WORKSPACE_CANDIDATE_LIMIT)

    def test_relationship_context_prompt_omits_item_ref(self) -> None:
        compact = _compact_relationship_context(
            {
                "state_boundary": "relationship_context は現在 view である。",
                "relationship_items": [
                    {
                        "item_ref": "memory_unit:9c92fccea3194e0ca2366c391722584a",
                        "source": "recall_pack.relationship_model",
                        "summary_text": "関係温度の現在 view",
                    }
                ],
            }
        )

        self.assertEqual(
            compact["relationship_items"][0],
            {
                "source": "recall_pack.relationship_model",
                "summary_text": "関係温度の現在 view",
            },
        )
        self.assertNotIn("item_ref", compact["relationship_items"][0])

    def test_workspace_context_keeps_all_derived_view_items_as_candidates(self) -> None:
        service = ServiceInputPipelineMixin()

        payload = service._build_workspace_context(
            current_input=CurrentInput(
                sender_kind="system",
                sender_ref=None,
                source_kind="wake",
                response_target_refs=(),
                interaction_context=None,
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
                    {"summary_text": f"感覚{index}", "channel": f"visual:{index}"}
                    for index in range(4)
                ],
            },
            relationship_context={
                "affect_items": [
                    {"summary_text": f"関係感情{index}", "affect_label": "warm"}
                    for index in range(3)
                ],
            },
            prediction_error_context={
                "signals": [
                    {"summary_text": f"差分{index}", "changed": True}
                    for index in range(5)
                ],
            },
            default_mode_context={
                "resurfacing_candidates": [
                    {
                        "candidate_ref": f"default_mode:active_commitments:{index}",
                        "source": "recall_pack.active_commitments",
                        "summary_text": f"再浮上{index}",
                    }
                    for index in range(3)
                ],
            },
        )

        refs = [candidate["factor_ref"] for candidate in payload["workspace_candidates"]]
        self.assertEqual(
            [f"self_state:sensory_confidence:{index}" for index in range(4)],
            [ref for ref in refs if ref.startswith("self_state:sensory_confidence:")],
        )
        self.assertEqual(
            [f"relationship_affect:{index}" for index in range(3)],
            [ref for ref in refs if ref.startswith("relationship_affect:")],
        )
        self.assertEqual(
            [f"prediction_error:{index}" for index in range(5)],
            [ref for ref in refs if ref.startswith("prediction_error:")],
        )
        self.assertEqual(
            [f"default_mode:active_commitments:{index}" for index in range(3)],
            [ref for ref in refs if ref.startswith("default_mode:active_commitments:")],
        )

    def test_default_mode_context_prompt_omits_candidate_ref(self) -> None:
        compact = _compact_default_mode_context(
            {
                "state_boundary": "default_mode_context は再浮上候補である。",
                "resurfacing_candidates": [
                    {
                        "candidate_ref": "default_mode:relationship_model:memory_unit:9c92fccea3194e0ca2366c391722584a",
                        "source": "recall_pack.relationship_model",
                        "summary_text": "静かな再浮上",
                        "resurfacing_policy": "即発話せず、workspace の前景化候補として扱う。",
                    }
                ],
            }
        )

        self.assertEqual(
            compact["resurfacing_candidates"][0],
            {
                "source": "recall_pack.relationship_model",
                "summary_text": "静かな再浮上",
                "resurfacing_policy": "即発話せず、workspace の前景化候補として扱う。",
            },
        )
        self.assertNotIn("candidate_ref", compact["resurfacing_candidates"][0])

    def test_workspace_context_includes_activity_transition_candidate(self) -> None:
        service = ServiceInputPipelineMixin()

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
                    "label": "アプリケーション起動検討",
                    "actor": "person",
                    "target": "desktop",
                    "transition": "start",
                    "started_age_label": "直前",
                    "duration_label": "1分未満",
                    "age_label": "直前",
                    "reason_summary": "desktop で新しい操作が始まっている。",
                },
                "previous_activity": {
                    "label": "離席中",
                    "actor": "person",
                    "target": "workspace",
                    "started_age_label": "19時間前",
                    "duration_label": "約19時間",
                    "ended_age_label": "直前",
                    "reason_summary": "長く作業場に不在だった。",
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

        candidates = payload["workspace_candidates"]
        refs = [candidate["factor_ref"] for candidate in candidates]
        transition = next(candidate for candidate in candidates if candidate["factor_ref"] == "activity:transition")

        self.assertLess(refs.index("activity:transition"), refs.index("activity:current_activity"))
        self.assertEqual(transition["kind"], "activity_transition")
        self.assertEqual(transition["metadata"]["transition"], "start")
        self.assertEqual(transition["metadata"]["previous_duration_label"], "約19時間")
        self.assertIn("前活動の継続時間: 約19時間", transition["summary_text"])

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
