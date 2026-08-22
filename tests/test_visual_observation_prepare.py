from __future__ import annotations

import base64
import io
import unittest
from unittest.mock import Mock

from PIL import Image

from otomekairo.llm.images import (
    VISUAL_OBSERVATION_MAX_EDGE,
    image_content_hash,
    prepare_visual_observation_image,
)
from otomekairo.service.input.visual import ServiceInputVisualMixin


def _jpeg_data_uri(*, width: int, height: int, color: tuple[int, int, int] = (12, 80, 160)) -> str:
    image = Image.new("RGB", (width, height), color)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


class VisualObservationPrepareTests(unittest.TestCase):
    def test_prepare_leaves_small_image_unchanged(self) -> None:
        data_uri = _jpeg_data_uri(width=320, height=240)
        self.assertEqual(prepare_visual_observation_image(data_uri), data_uri)

    def test_prepare_downscales_long_edge(self) -> None:
        data_uri = _jpeg_data_uri(width=2560, height=1440)
        prepared = prepare_visual_observation_image(data_uri)
        self.assertNotEqual(prepared, data_uri)
        raw = base64.b64decode(prepared.split(",", 1)[1])
        with Image.open(io.BytesIO(raw)) as image:
            self.assertLessEqual(max(image.size), VISUAL_OBSERVATION_MAX_EDGE)
            self.assertEqual(image.size[0], VISUAL_OBSERVATION_MAX_EDGE)

    def test_same_bytes_skip_llm_and_hash_change_calls(self) -> None:
        mixin = ServiceInputVisualMixin()
        mixin.llm = Mock()
        mixin.llm.generate_visual_observation_summary.return_value = {
            "summary_text": "机の上に本がある。",
            "confidence_hint": "medium",
            "change_state": "first_seen",
            "change_basis": "no_previous_observation",
            "change_reason_summary": "初回。",
        }
        mixin._build_selected_persona_context = Mock(return_value=Mock(to_prompt_payload=lambda: {}))
        mixin._build_visual_observation_source_pack = Mock(return_value={})
        mixin._visual_observation_input_kind = Mock(return_value="vision_capture_result")

        first = _jpeg_data_uri(width=640, height=480, color=(10, 20, 30))
        second_same = first
        third = _jpeg_data_uri(width=640, height=480, color=(200, 10, 10))
        state = {
            "selected_model_preset_id": "model_preset:test",
            "model_presets": {"model_preset:test": {"model": "mock"}},
        }
        observation = {"vision_source_id": "vision_source:desktop", "capability_id": "vision.capture"}

        _, first_summary = mixin._interpret_visual_observation(
            state=state,
            started_at="2026-08-18T12:00:00+09:00",
            trigger_kind="background_thinking",
            client_context={},
            observation_summary=dict(observation),
            input_text="観測",
            images=[first],
        )
        _, reused_summary = mixin._interpret_visual_observation(
            state=state,
            started_at="2026-08-18T12:10:00+09:00",
            trigger_kind="background_thinking",
            client_context={},
            observation_summary=dict(observation),
            input_text="観測",
            images=[second_same],
        )
        _, changed_summary = mixin._interpret_visual_observation(
            state=state,
            started_at="2026-08-18T12:20:00+09:00",
            trigger_kind="background_thinking",
            client_context={},
            observation_summary=dict(observation),
            input_text="観測",
            images=[third],
        )

        self.assertEqual(mixin.llm.generate_visual_observation_summary.call_count, 2)
        self.assertFalse(first_summary.get("image_interpretation_reused"))
        self.assertTrue(reused_summary.get("image_interpretation_reused"))
        self.assertEqual(reused_summary["change_state"], "stable")
        self.assertEqual(reused_summary["visual_summary_text"], "机の上に本がある。")
        self.assertFalse(changed_summary.get("image_interpretation_reused"))
        _, spoken_summary = mixin._interpret_visual_observation(
            state=state,
            started_at="2026-08-18T12:30:00+09:00",
            trigger_kind="background_thinking",
            client_context={},
            observation_summary=dict(observation),
            input_text="観測",
            images=[third],
            visual_observation_change_context={
                "last_prompted_observation_context": {
                    "summary_text": changed_summary["visual_summary_text"],
                }
            },
        )
        self.assertTrue(spoken_summary.get("image_interpretation_reused"))
        self.assertEqual(spoken_summary["change_state"], "same_as_recent_speech")
        self.assertEqual(spoken_summary["change_basis"], "recent_speech_repetition")
        self.assertEqual(image_content_hash(first), image_content_hash(second_same))
        self.assertNotEqual(image_content_hash(first), image_content_hash(third))
