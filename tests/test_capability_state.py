from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from otomekairo.service.app import OtomeKairoService
from otomekairo.service.capability import CapabilityUnavailableError
from otomekairo.service.common import ServiceError


NOW = "2026-08-16T17:00:00+09:00"


class _ClosedSocket:
    def close(self) -> None:
        return


class VisionSourceCapabilityStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.service = OtomeKairoService(root_dir=Path(self.temp_dir.name))
        self.token = self.service.acquire_console_access_token()["console_access_token"]
        self.desktop_source_id = "vision_source:console:desktop"
        self.camera_source_id = "vision_source:camera"
        state = self.service.store.read_state()
        state["camera_sources"] = {
            self.camera_source_id: {
                "vision_source_id": self.camera_source_id,
                "enabled": True,
            }
        }
        self.service.store.write_state(state)
        self._register_source(self.desktop_source_id, "desktop", "console")
        self._register_source(self.camera_source_id, "camera", "camera-connector")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _register_source(self, source_id: str, kind: str, client_id: str) -> None:
        session_id = self.service._event_stream_registry.add_connection(_ClosedSocket())
        self.service._event_stream_registry.register_hello(
            session_id,
            client_id=client_id,
            client_kind="capability_connector",
            capabilities={"vision.capture": "1"},
            rejected_bindings=[],
            vision_sources=[
                {
                    "vision_source_id": source_id,
                    "capability_id": "vision.capture",
                    "kind": kind,
                    "label": kind,
                    "default_for": [kind],
                }
            ],
        )

    def test_vision_capture_rejects_capability_wide_pause(self) -> None:
        with self.assertRaises(ServiceError) as raised:
            self.service.patch_capability_state(
                self.token,
                "vision.capture",
                {"paused": True},
            )

        self.assertEqual(raised.exception.error_code, "invalid_capability_state")

    def test_pausing_desktop_does_not_pause_camera(self) -> None:
        result = self.service.patch_capability_state(
            self.token,
            "vision.capture",
            {"paused": True, "vision_source_id": self.desktop_source_id},
        )

        sources = {
            source["vision_source_id"]: source
            for source in result["capability"]["vision_sources"]
        }
        self.assertFalse(sources[self.desktop_source_id]["available"])
        self.assertTrue(sources[self.desktop_source_id]["paused"])
        self.assertTrue(sources[self.camera_source_id]["available"])
        self.assertFalse(sources[self.camera_source_id]["paused"])
        self.assertTrue(result["capability"]["available"])

        state = self.service.store.read_state()
        memory_set_id = state["selected_memory_set_id"]
        policy = self.service._capability_state_policy("vision.capture")
        with self.assertRaises(CapabilityUnavailableError):
            self.service._validate_capability_runtime_dispatchable(
                memory_set_id=memory_set_id,
                capability_id="vision.capture",
                current_time=NOW,
                state_policy=policy,
                vision_source_id=self.desktop_source_id,
            )
        self.service._validate_capability_runtime_dispatchable(
            memory_set_id=memory_set_id,
            capability_id="vision.capture",
            current_time=NOW,
            state_policy=policy,
            vision_source_id=self.camera_source_id,
        )

    def test_unknown_vision_source_is_rejected(self) -> None:
        with self.assertRaises(ServiceError) as raised:
            self.service.patch_capability_state(
                self.token,
                "vision.capture",
                {"paused": True, "vision_source_id": "vision_source:missing"},
            )

        self.assertEqual(raised.exception.error_code, "vision_source_not_found")
