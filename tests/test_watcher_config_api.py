import unittest
from copy import deepcopy

from otomekairo.defaults import build_default_state
from otomekairo.service.common import ServiceError
from otomekairo.service.config.mixin import ServiceConfigMixin


class DummyStore:
    def __init__(self) -> None:
        self.root_dir = "/tmp/otomekairo-test"
        self.state = build_default_state()
        self.state["console_access_token"] = "token"
        self.events = []

    def read_state(self) -> dict:
        return deepcopy(self.state)

    def write_state(self, state: dict) -> None:
        self.state = deepcopy(state)

    def append_events(self, *, events: list[dict]) -> None:
        self.events.extend(deepcopy(events))


class DummyService(ServiceConfigMixin):
    def __init__(self) -> None:
        self.store = DummyStore()

    def _now_iso(self) -> str:
        return "2026-07-05T12:00:00+09:00"


def camera_source(
    *,
    display_name: str = "C220",
    vision_source_id: str = "vision_source:stale",
    watcher_id: str = "watcher:stale",
) -> dict:
    return {
        "vision_source_id": vision_source_id,
        "enabled": True,
        "display_name": display_name,
        "connection": {
            "host": "192.0.2.10",
            "camera_username": "camera-user",
            "camera_password": "camera-password",
        },
        "watcher": {
            "enabled": True,
            "watcher_id": watcher_id,
            "kind": "tapo_c220_motion",
            "poll_interval_seconds": 60,
            "min_wake_interval_seconds": 60,
            "motion_ratio_threshold": 0.03,
            "pixel_diff_threshold": 25,
            "resize_width": 320,
        },
    }


class WatcherConfigApiTests(unittest.TestCase):
    def test_camera_source_without_watcher_gets_disabled_default_watcher(self) -> None:
        service = DummyService()
        source = camera_source(display_name="対面カメラ")
        source.pop("watcher")

        response = service.replace_camera_sources_editor_state(
            "token",
            {"camera_sources": [source]},
        )

        stored_source = response["camera_sources"][0]
        watcher = stored_source["watcher"]
        self.assertEqual(stored_source["vision_source_id"], "vision_source:対面カメラ")
        self.assertEqual(watcher["enabled"], False)
        self.assertEqual(watcher["watcher_id"], "watcher:対面カメラ")
        self.assertEqual(watcher["poll_interval_seconds"], 60)
        self.assertEqual(watcher["min_wake_interval_seconds"], 60)

    def test_camera_source_ids_are_generated_from_display_name(self) -> None:
        service = DummyService()

        response = service.replace_camera_sources_editor_state(
            "token",
            {"camera_sources": [camera_source(display_name="対面カメラ", vision_source_id="vision_source:old", watcher_id="watcher:old")]},
        )

        source = response["camera_sources"][0]
        self.assertEqual(source["vision_source_id"], "vision_source:対面カメラ")
        self.assertEqual(source["watcher"]["watcher_id"], "watcher:対面カメラ")

    def test_public_camera_source_masks_connection_and_keeps_watcher(self) -> None:
        service = DummyService()

        response = service.replace_camera_sources_editor_state(
            "token",
            {"camera_sources": [camera_source(display_name="C220")]},
        )

        source = service.get_camera_source("token", "vision_source:C220")["camera_source"]
        self.assertEqual(source["connection"]["camera_password_present"], True)
        self.assertNotIn("camera_password", source["connection"])
        self.assertEqual(source["display_name"], "C220")
        self.assertEqual(source["watcher"]["watcher_id"], "watcher:C220")

    def test_watcher_runtime_config_returns_secret_connection_and_snapshot_dir(self) -> None:
        service = DummyService()
        service.replace_camera_sources_editor_state("token", {"camera_sources": [camera_source(display_name="C220")]})

        response = service.get_watcher_runtime_config("token", "watcher:C220")

        self.assertEqual(response["watcher_id"], "watcher:C220")
        self.assertEqual(response["watcher"]["kind"], "tapo_c220_motion")
        self.assertEqual(response["camera_source"]["display_name"], "C220")
        self.assertNotIn("label", response["camera_source"])
        self.assertNotIn("enabled", response["camera_source"])
        self.assertEqual(response["camera_source"]["connection"]["camera_password"], "camera-password")
        self.assertEqual(response["snapshot_dir"], "/tmp/otomekairo-test/wake-references/watcher-C220")
        self.assertEqual(service.store.events[-1]["kind"], "watcher_runtime_config_read")

    def test_duplicate_generated_camera_source_id_is_rejected(self) -> None:
        service = DummyService()

        with self.assertRaises(ServiceError) as raised:
            service.replace_camera_sources_editor_state(
                "token",
                {
                    "camera_sources": [
                        camera_source(display_name="同じカメラ"),
                        camera_source(display_name="同じカメラ"),
                    ]
                },
            )

        self.assertEqual(raised.exception.error_code, "duplicate_camera_source_id")

    def test_invalid_watcher_definition_is_rejected(self) -> None:
        service = DummyService()
        source = camera_source()
        source["watcher"]["motion_ratio_threshold"] = 0

        with self.assertRaises(ServiceError) as raised:
            service.replace_camera_sources_editor_state("token", {"camera_sources": [source]})

        self.assertEqual(raised.exception.error_code, "invalid_camera_source_watcher")

    def test_watcher_jpeg_quality_is_not_configurable(self) -> None:
        service = DummyService()
        source = camera_source()
        source["watcher"]["jpeg_quality"] = 88

        with self.assertRaises(ServiceError) as raised:
            service.replace_camera_sources_editor_state("token", {"camera_sources": [source]})

        self.assertEqual(raised.exception.error_code, "unsupported_camera_source_watcher_field")

    def test_missing_watcher_runtime_config_returns_not_found(self) -> None:
        service = DummyService()

        with self.assertRaises(ServiceError) as raised:
            service.get_watcher_runtime_config("token", "watcher:missing")

        self.assertEqual(raised.exception.error_code, "watcher_runtime_config_not_found")


if __name__ == "__main__":
    unittest.main()
