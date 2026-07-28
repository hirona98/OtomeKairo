from __future__ import annotations

import unittest
from copy import deepcopy

from otomekairo.defaults import build_default_state
from otomekairo.service.common import ServiceError
from otomekairo.service.config.mixin import ServiceConfigMixin


class DummyStore:
    def __init__(self) -> None:
        self.state = build_default_state()
        self.state["console_access_token"] = "token"
        self.events: list[dict] = []

    def read_state(self) -> dict:
        return deepcopy(self.state)

    def write_state(self, state: dict) -> None:
        self.state = deepcopy(state)

    def append_events(self, *, events: list[dict]) -> None:
        self.events.extend(deepcopy(events))


class DummyService(ServiceConfigMixin):
    def __init__(self) -> None:
        self.store = DummyStore()
        self.now = "2026-07-27T12:00:00.000001+09:00"

    def _now_iso(self) -> str:
        return self.now


class ConsoleClientConfigApiTests(unittest.TestCase):
    def test_connect_creates_server_owned_defaults(self) -> None:
        service = DummyService()

        response = service.connect_console_client("token", "console-main")

        self.assertEqual(response["client_id"], "console-main")
        self.assertEqual(response["last_connected_at"], service.now)
        self.assertEqual(response["settings"]["client_id"], "console-main")
        self.assertEqual(response["settings"]["process"]["console_api_port"], 55600)
        self.assertEqual(response["settings"]["display"]["avatar_position_x"], 0.0)
        self.assertEqual(response["settings"]["display"]["avatar_position_y"], 0.0)
        self.assertEqual(
            response["settings"]["avatar_presentations"][0]["model"],
            "default",
        )
        self.assertTrue(
            response["settings"]["avatar_presentations"][0]["shadow_exclusion_enabled"]
        )
        animations = response["settings"]["motion"]["animation_sets"][0]["animations"]
        self.assertEqual(len(animations), 215)
        self.assertEqual(animations[0]["animation_name"], "DT_01_wait_natural_F_001_FBX")

    def test_last_connected_uses_connect_operation_only(self) -> None:
        service = DummyService()
        service.connect_console_client("token", "console-first")
        service.now = "2026-07-27T12:00:01.000001+09:00"
        second = service.connect_console_client("token", "console-second")

        settings = deepcopy(second["settings"])
        settings["display"]["topmost"] = False
        service.replace_console_client_editor_state("token", "console-second", settings)

        response = service.get_last_connected_console_client_editor_state("token")

        self.assertEqual(response["client_id"], "console-second")
        self.assertEqual(response["last_connected_at"], second["last_connected_at"])
        self.assertFalse(response["settings"]["display"]["topmost"])

    def test_last_connected_fails_before_console_connects(self) -> None:
        service = DummyService()

        with self.assertRaises(ServiceError) as raised:
            service.get_last_connected_console_client_editor_state("token")

        self.assertEqual(
            raised.exception.error_code,
            "console_client_settings_not_found",
        )

    def test_patch_replaces_only_requested_top_level_section(self) -> None:
        service = DummyService()
        original = service.connect_console_client("token", "console-main")
        display = deepcopy(original["settings"]["display"])
        display["topmost"] = False

        response = service.patch_console_client_settings(
            "token",
            "console-main",
            {"display": display},
        )

        self.assertFalse(response["settings"]["display"]["topmost"])
        self.assertEqual(
            response["settings"]["desktop_capture"],
            original["settings"]["desktop_capture"],
        )

    def test_avatar_presentation_rejects_unknown_avatar(self) -> None:
        service = DummyService()
        response = service.connect_console_client("token", "console-main")
        settings = deepcopy(response["settings"])
        settings["avatar_presentations"][0]["avatar_id"] = "avatar:missing"

        with self.assertRaises(ServiceError) as raised:
            service.replace_console_client_editor_state(
                "token",
                "console-main",
                settings,
            )

        self.assertEqual(raised.exception.error_code, "avatar_not_found")

    def test_avatar_presentation_keeps_model_value_opaque(self) -> None:
        service = DummyService()
        response = service.connect_console_client("token", "console-main")
        settings = deepcopy(response["settings"])
        settings["avatar_presentations"][0]["model"] = r"C:\Models\alice.vrm"

        replaced = service.replace_console_client_editor_state(
            "token",
            "console-main",
            settings,
        )

        self.assertEqual(
            replaced["settings"]["avatar_presentations"][0]["model"],
            r"C:\Models\alice.vrm",
        )

    def test_avatar_presentation_rejects_empty_model(self) -> None:
        service = DummyService()
        response = service.connect_console_client("token", "console-main")
        settings = deepcopy(response["settings"])
        settings["avatar_presentations"][0]["model"] = " "

        with self.assertRaises(ServiceError) as raised:
            service.replace_console_client_editor_state(
                "token",
                "console-main",
                settings,
            )

        self.assertEqual(
            raised.exception.error_code,
            "invalid_console_avatar_presentations",
        )

    def test_avatar_presentation_rejects_resource_shape(self) -> None:
        service = DummyService()
        response = service.connect_console_client("token", "console-main")
        settings = deepcopy(response["settings"])
        presentation = settings["avatar_presentations"][0]
        presentation["resource"] = {"kind": "builtin", "name": "default"}
        del presentation["model"]

        with self.assertRaises(ServiceError) as raised:
            service.replace_console_client_editor_state(
                "token",
                "console-main",
                settings,
            )

        self.assertEqual(
            raised.exception.error_code,
            "invalid_console_avatar_presentation_fields",
        )

    def test_avatar_delete_prunes_console_presentation(self) -> None:
        service = DummyService()
        editor = service.get_avatar_speech_editor_state("token")
        second = deepcopy(editor["avatars"][0])
        second["avatar_id"] = "avatar:second"
        second["display_name"] = "2番目"
        editor["avatars"].append(second)
        service.replace_avatar_speech_editor_state("token", editor)
        connected = service.connect_console_client("token", "console-main")
        settings = deepcopy(connected["settings"])
        presentation = deepcopy(settings["avatar_presentations"][0])
        presentation["avatar_id"] = "avatar:second"
        settings["avatar_presentations"].append(presentation)
        service.replace_console_client_editor_state("token", "console-main", settings)

        editor["avatars"] = [editor["avatars"][0]]
        editor["selected_avatar_id"] = editor["avatars"][0]["avatar_id"]
        service.replace_avatar_speech_editor_state("token", editor)

        response = service.get_console_client_editor_state("token", "console-main")
        self.assertEqual(
            [item["avatar_id"] for item in response["settings"]["avatar_presentations"]],
            ["avatar:default"],
        )

    def test_conversation_display_name_is_shared_current_setting(self) -> None:
        service = DummyService()

        response = service.patch_current(
            "token",
            {"conversation_display_name": " 田中さん "},
        )

        self.assertEqual(
            response["settings_snapshot"]["conversation_display_name"],
            "田中さん",
        )
        self.assertEqual(
            service.get_editor_state("token")["current"]["conversation_display_name"],
            "田中さん",
        )


if __name__ == "__main__":
    unittest.main()
