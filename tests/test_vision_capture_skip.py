from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from otomekairo.service.app import OtomeKairoService
from otomekairo.service.common import ServiceError
from otomekairo.service.spontaneous.capability_payload import (
    VISION_CAPTURE_SKIPPED_EXCLUDED_WINDOW_TITLE,
    VISION_CAPTURE_SKIPPED_IDLE,
    capability_result_has_error,
    vision_capture_skip_reason,
    vision_capture_skip_reason_summary,
)


PNG_DATA_URI = "data:image/png;base64,AAAA"


class VisionCaptureSkipClassificationTests(unittest.TestCase):
    def test_closed_skip_codes_map_to_skip_reason(self) -> None:
        self.assertEqual(
            vision_capture_skip_reason(VISION_CAPTURE_SKIPPED_EXCLUDED_WINDOW_TITLE),
            "excluded_window_title",
        )
        self.assertEqual(vision_capture_skip_reason(VISION_CAPTURE_SKIPPED_IDLE), "idle")
        self.assertIsNone(vision_capture_skip_reason("capture failed"))
        self.assertIsNone(vision_capture_skip_reason("capture skipped (excluded window title) extra"))
        self.assertIsNone(vision_capture_skip_reason(None))

    def test_skip_codes_are_not_result_errors(self) -> None:
        self.assertFalse(
            capability_result_has_error(
                capability_id="vision.capture",
                result_payload={"error": VISION_CAPTURE_SKIPPED_EXCLUDED_WINDOW_TITLE},
            )
        )
        self.assertFalse(
            capability_result_has_error(
                capability_id="vision.capture",
                result_payload={"error": VISION_CAPTURE_SKIPPED_IDLE},
            )
        )
        self.assertTrue(
            capability_result_has_error(
                capability_id="vision.capture",
                result_payload={"error": "desktop capture failed"},
            )
        )
        self.assertTrue(
            capability_result_has_error(
                capability_id="camera.ptz",
                result_payload={"error": VISION_CAPTURE_SKIPPED_IDLE},
            )
        )


class VisionCaptureSkipResultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.service = OtomeKairoService(root_dir=Path(self.temp_dir.name))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_skip_code_with_image_is_invalid(self) -> None:
        with self.assertRaises(ServiceError) as raised:
            self.service._normalize_capability_result_payload(
                capability_id="vision.capture",
                result_payload={
                    "images": [PNG_DATA_URI],
                    "error": VISION_CAPTURE_SKIPPED_EXCLUDED_WINDOW_TITLE,
                },
            )
        self.assertEqual(raised.exception.error_code, "invalid_capability_result")

    def test_skip_code_with_empty_images_is_accepted(self) -> None:
        payload = self.service._normalize_capability_result_payload(
            capability_id="vision.capture",
            result_payload={
                "images": [],
                "error": VISION_CAPTURE_SKIPPED_EXCLUDED_WINDOW_TITLE,
            },
        )
        self.assertEqual(payload["images"], [])
        self.assertEqual(payload["error"], VISION_CAPTURE_SKIPPED_EXCLUDED_WINDOW_TITLE)

    def test_wake_observation_excluded_window_is_skipped(self) -> None:
        summary = self.service._wake_policy_observation_success_summary(
            observation={
                "observation_id": "observation:main_desktop",
                "capability_id": "vision.capture",
                "input": {"vision_source_id": "vision_source:console:desktop"},
            },
            capability_response={"request_id": "vision_capture_request:skip"},
            observation_summary={
                "error": VISION_CAPTURE_SKIPPED_EXCLUDED_WINDOW_TITLE,
                "image_count": 0,
                "vision_source_id": "vision_source:console:desktop",
            },
            capability_request_summary=None,
        )
        self.assertEqual(summary["status"], "skipped")
        self.assertEqual(summary["skip_reason"], "excluded_window_title")
        self.assertEqual(
            summary["reason_summary"],
            vision_capture_skip_reason_summary("excluded_window_title"),
        )
        self.assertNotIn("failure_code", summary)
        self.assertNotIn("error", summary)

        recorded = self.service._record_wake_policy_observation_runtime_state(
            summary=summary,
            current_time="2026-08-14T12:00:00+09:00",
        )
        self.assertEqual(recorded["status"], "skipped")
        runtime = self.service._wake_observation_runtime_state["observation:main_desktop"]
        self.assertEqual(runtime["last_status"], "skipped")
        self.assertEqual(
            runtime["last_summary"],
            vision_capture_skip_reason_summary("excluded_window_title"),
        )
        self.assertIsNone(runtime["last_error"])
        self.assertNotIn("last_success_at", runtime)

    def test_wake_observation_idle_is_skipped(self) -> None:
        summary = self.service._wake_policy_observation_success_summary(
            observation={
                "observation_id": "observation:main_desktop",
                "capability_id": "vision.capture",
                "input": {"vision_source_id": "vision_source:console:desktop"},
            },
            capability_response={},
            observation_summary={"error": VISION_CAPTURE_SKIPPED_IDLE, "image_count": 0},
            capability_request_summary=None,
        )
        self.assertEqual(summary["status"], "skipped")
        self.assertEqual(summary["skip_reason"], "idle")

    def test_unknown_error_remains_failed(self) -> None:
        summary = self.service._wake_policy_observation_success_summary(
            observation={
                "observation_id": "observation:main_desktop",
                "capability_id": "vision.capture",
                "input": {"vision_source_id": "vision_source:console:desktop"},
            },
            capability_response={},
            observation_summary={"error": "desktop capture failed"},
            capability_request_summary=None,
        )
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["failure_code"], "capability_result_failed")
        recorded = self.service._record_wake_policy_observation_runtime_state(
            summary=summary,
            current_time="2026-08-14T12:00:00+09:00",
        )
        self.assertEqual(recorded["status"], "failed")
        runtime = self.service._wake_observation_runtime_state["observation:main_desktop"]
        self.assertEqual(runtime["last_status"], "failed")
        self.assertEqual(runtime["last_error"], "desktop capture failed")

    def test_followup_and_input_text_do_not_treat_skip_as_error(self) -> None:
        result_payload = {
            "images": [],
            "error": VISION_CAPTURE_SKIPPED_EXCLUDED_WINDOW_TITLE,
            "capability_id": "vision.capture",
        }
        self.assertEqual(
            self.service._capability_result_followup_reason_code(
                capability_id="vision.capture",
                decision={"kind": "noop"},
                result_payload=result_payload,
            ),
            "followup_noop",
        )
        self.assertEqual(
            self.service._capability_result_followup_reason_code(
                capability_id="vision.capture",
                decision={"kind": "capability_request"},
                result_payload=result_payload,
            ),
            "result_skipped",
        )
        self.assertFalse(
            capability_result_has_error(
                capability_id="vision.capture",
                result_payload=result_payload,
            )
        )
        terminal_reason = self.service._capability_result_terminal_reason(
            capability_id="vision.capture",
            result_payload=result_payload,
        )
        self.assertIn("見送り", terminal_reason)
        self.assertNotIn("error", terminal_reason)
        input_text = self.service._build_capability_result_input_text(
            client_context={},
            capability_response=result_payload,
        )
        self.assertIn("除外ウィンドウのため視覚観測を見送った。", input_text)
        self.assertNotIn("結果は error だった", input_text)
        self.assertNotIn("観測結果は空だった", input_text)

    def test_wake_observation_summary_text_uses_skipped(self) -> None:
        text = self.service._wake_policy_observation_summary_text(
            [
                {
                    "observation_id": "observation:main_desktop",
                    "source_label": "メイン画面",
                    "status": "skipped",
                    "reason_summary": vision_capture_skip_reason_summary("excluded_window_title"),
                }
            ]
        )
        self.assertIn("skipped", text)
        self.assertNotIn("failed", text)

    def test_wake_skip_does_not_refresh_world_or_activity(self) -> None:
        called: list[str] = []
        self.service._prepare_capability_result_context = lambda **kwargs: called.append("prepare")
        self.service._refresh_world_state_context = lambda **kwargs: called.append("world")
        self.service._refresh_activity_context = lambda **kwargs: called.append("activity")
        summary = self.service._apply_wake_policy_observation_result(
            state={"selected_memory_set_id": "memory_set:test"},
            started_at="2026-08-14T12:00:00+09:00",
            observation={
                "observation_id": "observation:main_desktop",
                "capability_id": "vision.capture",
                "input": {"vision_source_id": "vision_source:console:desktop"},
            },
            capability_response={
                "capability_id": "vision.capture",
                "error": VISION_CAPTURE_SKIPPED_EXCLUDED_WINDOW_TITLE,
                "images": [],
            },
            cycle_id=None,
        )
        self.assertEqual(summary["status"], "skipped")
        self.assertEqual(called, [])

    def test_wake_skip_finishes_ongoing_action_without_result_error(self) -> None:
        transition = self.service._finish_wake_policy_observation_ongoing_action(
            request_record={
                "memory_set_id": "memory_set:test",
                "action_id": "action:test",
                "capability_id": "vision.capture",
            },
            current_time="2026-08-14T12:00:00+09:00",
            capability_id="vision.capture",
            capability_response={"error": VISION_CAPTURE_SKIPPED_EXCLUDED_WINDOW_TITLE},
            observation_summary={"capability_id": "vision.capture", "image_count": 0},
            failure_reason=None,
        )
        self.assertIsNotNone(transition)
        self.assertEqual(transition["final_state"], "completed")
        self.assertIs(transition["result_error"], False)
        self.assertIn("見送った", transition["reason_summary"])
