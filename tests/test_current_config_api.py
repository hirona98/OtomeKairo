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
    def test_default_background_wake_speech_frequency_level_is_standard(self) -> None:
        service = DummyService()

        response = service.get_config("token")

        self.assertEqual(response["settings_snapshot"]["background_wake_speech_frequency_level"], 5)

    def test_patch_current_accepts_background_wake_speech_frequency_bounds(self) -> None:
        service = DummyService()

        response = service.patch_current("token", {"background_wake_speech_frequency_level": 1})
        self.assertEqual(response["settings_snapshot"]["background_wake_speech_frequency_level"], 1)

        response = service.patch_current("token", {"background_wake_speech_frequency_level": 10})
        self.assertEqual(response["settings_snapshot"]["background_wake_speech_frequency_level"], 10)

    def test_patch_current_rejects_invalid_background_wake_speech_frequency_level(self) -> None:
        service = DummyService()

        for value in (0, 11, True, "5"):
            with self.subTest(value=value):
                with self.assertRaises(ServiceError) as raised:
                    service.patch_current("token", {"background_wake_speech_frequency_level": value})
                self.assertEqual(raised.exception.error_code, "invalid_background_wake_speech_frequency_level")


if __name__ == "__main__":
    unittest.main()
