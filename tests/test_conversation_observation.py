from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from otomekairo.service.app import OtomeKairoService


class ConversationObservationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.service = OtomeKairoService(Path(self.temp_dir.name))
        self.state = self.service.store.read_state()
        self.state["wake_policy"] = {
            "mode": "disabled", "interval_seconds": 300,
            "observations": [{
                "observation_id": "observation:desktop", "enabled": True,
                "capability_id": "vision.capture",
                "input": {"vision_source_id": "vision_source:desktop", "mode": "still"},
            }],
        }
        self.state["camera_sources"] = {
            "vision_source:camera": {"vision_source_id": "vision_source:camera", "enabled": True},
        }
        self.payload = {
            "message_id": "chat_message:observation-test", "text": "はじめまして",
            "interaction_context": {
                "interaction_ref": "interaction:test", "speaker_ref": "person:test",
                "participants": [{"person_ref": "person:test", "display_name": "テスト"}],
            },
        }

    def _conversation(self, *, desktop_status="succeeded", skip_reason="idle"):
        order = []
        captured = {}

        def observe(**kwargs):
            observation = kwargs["observation"]
            source = observation["input"]["vision_source_id"]
            order.append(source)
            status = desktop_status if source == "vision_source:desktop" else "succeeded"
            result = {
                "observation_id": observation["observation_id"],
                "capability_id": "vision.capture", "vision_source_id": source, "status": status,
            }
            if status == "skipped":
                result.update(skip_reason=skip_reason, reason_summary="観測を見送った。")
            elif status == "failed":
                result.update(failure_code="source_unavailable", reason_summary="未接続。")
            else:
                result.update(visual_summary_text="人が座っている。", image_count=1)
            return result

        def pipeline(**kwargs):
            order.append("pipeline")
            captured.update(kwargs)
            return {"decision": {"kind": "speech"}}

        with (
            patch.object(self.service, "_require_token", return_value=self.state),
            patch.object(self.service, "_run_wake_policy_observation", side_effect=observe),
            patch.object(self.service, "_run_input_pipeline", side_effect=pipeline),
            patch.object(self.service, "_complete_input_success", return_value={"result_kind": "speech"}),
        ):
            self.service._handle_conversation_cycle(None, self.payload)
        return order, captured

    def test_each_conversation_observes_both_sources_before_pipeline(self) -> None:
        for mode in ("disabled", "interval"):
            self.state["wake_policy"]["mode"] = mode
            for turn in range(2):
                with self.subTest(mode=mode, turn=turn):
                    order, captured = self._conversation()
                    self.assertEqual(order, ["vision_source:desktop", "vision_source:camera", "pipeline"])
                    self.assertEqual(captured["input_text"], self.payload["text"])
                    self.assertEqual(captured["trigger_kind"], "user_message")
                    self.assertEqual(len(captured["client_context"]["wake_observations"]), 2)

    def test_desktop_skip_matches_off_while_camera_remains(self) -> None:
        for reason in ("excluded_window_title", "idle"):
            with self.subTest(reason=reason):
                initial_runtime = deepcopy(self.service._wake_observation_runtime_state)
                _, skipped = self._conversation(desktop_status="skipped", skip_reason=reason)
                self.service._wake_observation_runtime_state = initial_runtime
                self.state["wake_policy"]["observations"][0]["enabled"] = False
                off_order, off = self._conversation()
                self.state["wake_policy"]["observations"][0]["enabled"] = True
                self.assertEqual(off_order, ["vision_source:camera", "pipeline"])
                decision_contexts = [
                    {k: v for k, v in result["client_context"].items() if k != "wake_observation_trace"}
                    for result in (skipped, off)
                ]
                self.assertEqual(*decision_contexts)
                audit = skipped["client_context"]["wake_observation_trace"]["wake_observations"]
                self.assertEqual(audit[0]["skip_reason"], reason)

    def test_unavailable_source_is_reported_as_failure_and_conversation_proceeds(self) -> None:
        _, captured = self._conversation(desktop_status="failed")
        observations = captured["client_context"]["wake_observations"]
        self.assertEqual(observations[0]["failure_code"], "source_unavailable")
        self.assertEqual(observations[1]["status"], "succeeded")

    def test_disabled_sources_are_not_captured(self) -> None:
        self.state["wake_policy"]["observations"][0]["enabled"] = False
        self.state["camera_sources"]["vision_source:camera"]["enabled"] = False
        order, captured = self._conversation()
        self.assertEqual(order, ["pipeline"])
        self.assertNotIn("wake_observations", captured["client_context"])

    def test_skipped_desktop_without_camera_matches_all_sources_off(self) -> None:
        self.state["camera_sources"]["vision_source:camera"]["enabled"] = False
        _, skipped = self._conversation(desktop_status="skipped")
        self.state["wake_policy"]["observations"][0]["enabled"] = False
        _, off = self._conversation()
        self.assertEqual(
            {k: v for k, v in skipped["client_context"].items() if k != "wake_observation_trace"},
            off["client_context"],
        )

    def test_successful_conversation_observations_are_available_for_reuse(self) -> None:
        _, captured = self._conversation(desktop_status="skipped")
        view = [{
            "id": "vision.capture", "available": True,
            "vision_sources": [{"vision_source_id": "vision_source:camera"}, {"vision_source_id": "vision_source:desktop"}],
        }]
        annotated = self.service._annotate_capability_decision_view_with_fresh_visual_context(
            capability_decision_view=view, foreground_world_state=[], world_state_trace=None,
            trigger_kind="user_message", client_context=captured["client_context"],
        )
        self.assertEqual(
            [item["vision_source_id"] for item in annotated[0]["fresh_world_state_by_vision_source"]],
            ["vision_source:camera"],
        )


if __name__ == "__main__":
    unittest.main()
