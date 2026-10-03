from __future__ import annotations

import unittest
from unittest.mock import Mock

from otomekairo.service.input.pipeline import ServiceInputPipelineMixin
from otomekairo.service.input.visual import ServiceInputVisualMixin


class VisualIdentityService(ServiceInputVisualMixin, ServiceInputPipelineMixin):
    pass


class VisualIdentityTests(unittest.TestCase):
    def test_visual_source_pack_preserves_only_structured_identification(self) -> None:
        service = VisualIdentityService()
        for persons in ([], [{"person_ref": "person:camera", "display_name": "撮影対象"}]):
            with self.subTest(persons=persons):
                pack = service._build_visual_observation_source_pack(
                    started_at="2026-10-02T17:00:00+09:00",
                    input_text="マスターがいる部屋を観測する。",
                    trigger_kind="capability_result", client_context={},
                    observation_summary={"capability_id": "vision.capture", "observed_persons": persons},
                    persona_context=Mock(),
                )
                self.assertEqual(pack["observed_persons"], persons)
