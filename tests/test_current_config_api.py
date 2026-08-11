import unittest
from copy import deepcopy

from otomekairo.defaults import build_default_state
from otomekairo.service.common import ServiceError
from otomekairo.service.config.mixin import ServiceConfigMixin


class DummyStore:
    def __init__(self) -> None:
        self.state = build_default_state()
        self.state["console_access_token"] = "token"

    def read_state(self) -> dict:
        return deepcopy(self.state)

    def write_state(self, state: dict) -> None:
        self.state = deepcopy(state)


class DummyService(ServiceConfigMixin):
    def __init__(self) -> None:
        self.store = DummyStore()


class CurrentConfigApiTests(unittest.TestCase):
    def test_default_model_preset_uses_one_generation_config(self) -> None:
        service = DummyService()
        state = service.store.read_state()
        preset = state["model_presets"][state["selected_model_preset_id"]]

        self.assertNotIn("roles", preset)
        self.assertEqual(preset["max_output_tokens"], 4000)
        self.assertEqual(preset["timeout_seconds"], 90)
        self.assertFalse(preset["web_search_enabled"])
        self.assertNotIn("reasoning_effort", preset)
        self.assertEqual(
            state["pre_send_check_model_preset_id"],
            "model_preset:pre_send_check",
        )
        self.assertIn(
            "model_preset:pre_send_check",
            state["model_presets"],
        )
        self.assertNotEqual(
            state["pre_send_check_model_preset_id"],
            state["selected_model_preset_id"],
        )

    def test_model_preset_read_masks_top_level_api_key(self) -> None:
        service = DummyService()
        state = service.store.read_state()
        selected_id = state["selected_model_preset_id"]
        state["model_presets"][selected_id]["api_key"] = "secret"
        service.store.write_state(state)

        response = service.get_config("token")
        public_preset = response["selected_model_preset"]
        editor_preset = service._build_editor_state(service.store.read_state())["model_presets"][0]

        self.assertNotIn("api_key", public_preset)
        self.assertTrue(public_preset["api_key_present"])
        self.assertEqual(editor_preset["api_key"], "secret")

    def test_model_preset_rejects_removed_roles_field(self) -> None:
        service = DummyService()
        state = service.store.read_state()
        selected_id = state["selected_model_preset_id"]
        preset = deepcopy(state["model_presets"][selected_id])
        preset["roles"] = {}

        with self.assertRaises(ServiceError) as raised:
            service._validate_model_preset_definition(selected_id, preset)

        self.assertEqual(raised.exception.error_code, "unsupported_model_preset_fields")

    def test_model_preset_rejects_fractional_timeout_seconds(self) -> None:
        service = DummyService()
        state = service.store.read_state()
        selected_id = state["selected_model_preset_id"]
        preset = deepcopy(state["model_presets"][selected_id])
        preset["timeout_seconds"] = 1.5

        with self.assertRaises(ServiceError) as raised:
            service._validate_model_preset_definition(selected_id, preset)

        self.assertEqual(raised.exception.error_code, "invalid_timeout_seconds")

    def test_default_current_settings_use_standard_thinking_values(self) -> None:
        service = DummyService()

        response = service.get_config("token")

        self.assertEqual(response["settings_snapshot"]["thinking_speech_level"], 5)
        self.assertEqual(
            response["settings_snapshot"]["wake_policy"],
            {"mode": "disabled", "interval_seconds": 300},
        )

    def test_wake_policy_requires_interval_in_all_modes(self) -> None:
        service = DummyService()

        service._validate_wake_policy({"mode": "disabled", "interval_seconds": 600})

        with self.assertRaises(ServiceError) as raised:
            service._validate_wake_policy({"mode": "disabled"})
        self.assertEqual(
            raised.exception.error_code,
            "invalid_wake_policy_interval_seconds",
        )

    def test_patch_current_accepts_thinking_speech_bounds(self) -> None:
        service = DummyService()

        response = service.patch_current("token", {"thinking_speech_level": 1})
        self.assertEqual(response["settings_snapshot"]["thinking_speech_level"], 1)

        response = service.patch_current("token", {"thinking_speech_level": 10})
        self.assertEqual(response["settings_snapshot"]["thinking_speech_level"], 10)

    def test_pre_send_check_uses_dedicated_model_preset(self) -> None:
        service = DummyService()
        state = service.store.read_state()
        source_id = state["selected_model_preset_id"]
        review_id = "model_preset:pre_send_check"

        response = service.patch_current(
            "token",
            {"pre_send_check_model_preset_id": review_id},
        )

        self.assertEqual(
            response["settings_snapshot"]["pre_send_check_model_preset_id"],
            review_id,
        )
        self.assertEqual(response["settings_snapshot"]["selected_model_preset_id"], source_id)

        with self.assertRaises(ServiceError) as raised:
            service.patch_current(
                "token",
                {"pre_send_check_model_preset_id": source_id},
            )
        self.assertEqual(
            raised.exception.error_code,
            "invalid_pre_send_check_model_preset_id",
        )

        with self.assertRaises(ServiceError) as raised:
            service.delete_model_preset("token", review_id)
        self.assertEqual(
            raised.exception.error_code,
            "pre_send_check_model_preset_delete_forbidden",
        )

    def test_patch_current_rejects_invalid_thinking_speech_level(self) -> None:
        service = DummyService()

        for value in (0, 11, True, "5"):
            with self.subTest(value=value):
                with self.assertRaises(ServiceError) as raised:
                    service.patch_current("token", {"thinking_speech_level": value})
                self.assertEqual(raised.exception.error_code, "invalid_thinking_speech_level")


if __name__ == "__main__":
    unittest.main()
