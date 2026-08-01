import unittest
from copy import deepcopy

from otomekairo.defaults import build_default_state
from otomekairo.service.common import ServiceError
from otomekairo.service.config.mixin import ServiceConfigMixin


class DummyStore:
    def __init__(self) -> None:
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
        return "2026-07-27T12:00:00+09:00"


class AvatarSpeechConfigApiTests(unittest.TestCase):
    def test_default_editor_state_matches_console_settings(self) -> None:
        service = DummyService()

        response = service.get_avatar_speech_editor_state("token")

        self.assertEqual(response["selected_avatar_id"], "avatar:default")
        self.assertEqual(
            response["microphone_settings"],
            {
                "input_source": "local_microphone",
                "local_input_device": None,
                "console": None,
                "vad_probability_threshold": 0.5,
                "speaker_recognition_threshold": 0.6,
            },
        )
        avatar = response["avatars"][0]
        self.assertEqual(
            set(avatar),
            {"avatar_id", "display_name", "stt", "tts"},
        )
        self.assertEqual(avatar["tts"]["engine"], "voicevox")
        self.assertEqual(
            set(avatar["tts"]),
            {
                "enabled",
                "engine",
                "voicevox_config",
                "style_bert_vits2_config",
                "aivis_cloud_config",
            },
        )
        self.assertEqual(avatar["stt"]["engine"], "amivoice")
        self.assertEqual(avatar["stt"]["wake_words"], [])
        self.assertNotIn("language", avatar["stt"])
        self.assertIn("assist_text", avatar["tts"]["style_bert_vits2_config"])
        self.assertEqual(
            avatar["tts"]["aivis_cloud_config"]["output_format"],
            "wav",
        )
        self.assertEqual(service.store.events[-1]["kind"], "avatar_speech_editor_state_read")

    def test_public_read_masks_stt_and_tts_api_keys(self) -> None:
        service = DummyService()
        state = service.store.read_state()
        avatar = state["avatars"][state["selected_avatar_id"]]
        avatar["stt"]["api_key"] = "stt-secret"
        avatar["tts"]["aivis_cloud_config"]["api_key"] = "tts-secret"
        service.store.write_state(state)

        public_response = service.get_avatar_speech("token")
        editor_response = service.get_avatar_speech_editor_state("token")

        public_avatar = public_response["selected_avatar"]
        self.assertNotIn("api_key", public_avatar["stt"])
        self.assertTrue(public_avatar["stt"]["api_key_present"])
        public_aivis = public_avatar["tts"]["aivis_cloud_config"]
        self.assertNotIn("api_key", public_aivis)
        self.assertTrue(public_aivis["api_key_present"])
        self.assertEqual(editor_response["avatars"][0]["stt"]["api_key"], "stt-secret")
        self.assertEqual(
            editor_response["avatars"][0]["tts"]["aivis_cloud_config"]["api_key"],
            "tts-secret",
        )

    def test_replace_editor_state_updates_bundle_atomically(self) -> None:
        service = DummyService()
        definition = service.get_avatar_speech_editor_state("token")
        avatar = deepcopy(definition["avatars"][0])
        avatar["avatar_id"] = "avatar:second"
        avatar["display_name"] = "2番目"
        avatar["stt"]["profile_id"] = "service_profile-01"
        avatar["stt"]["api_key"] = "new-stt-secret"
        avatar["tts"]["engine"] = "aivis-cloud"
        avatar["tts"]["aivis_cloud_config"]["api_key"] = "new-tts-secret"
        definition["avatars"].append(avatar)
        definition["selected_avatar_id"] = avatar["avatar_id"]
        definition["microphone_settings"]["vad_probability_threshold"] = 0.55
        definition["microphone_settings"]["speaker_recognition_threshold"] = 0.65

        response = service.replace_avatar_speech_editor_state("token", definition)

        self.assertEqual(response, service.get_avatar_speech_editor_state("token"))
        self.assertEqual(response["selected_avatar_id"], "avatar:second")
        self.assertEqual(
            response["microphone_settings"]["vad_probability_threshold"],
            0.55,
        )
        self.assertEqual(response["avatars"][1]["display_name"], "2番目")
        self.assertEqual(response["avatars"][1]["stt"]["profile_id"], "service_profile-01")
        self.assertEqual(
            service.store.events[-2]["kind"],
            "avatar_speech_editor_state_write",
        )

    def test_invalid_editor_state_does_not_change_saved_state(self) -> None:
        cases: list[tuple[str, object, str]] = []
        service = DummyService()
        original = service.get_avatar_speech_editor_state("token")

        duplicate = deepcopy(original)
        duplicate["avatars"].append(deepcopy(duplicate["avatars"][0]))
        cases.append(("duplicate", duplicate, "duplicate_avatars_id"))

        missing_selected = deepcopy(original)
        missing_selected["selected_avatar_id"] = "avatar:missing"
        cases.append(("missing selected", missing_selected, "avatar_not_found"))

        invalid_microphone = deepcopy(original)
        invalid_microphone["microphone_settings"]["vad_probability_threshold"] = 0.05
        cases.append(
            ("invalid microphone", invalid_microphone, "invalid_microphone_settings")
        )

        fractional_microphone = deepcopy(original)
        fractional_microphone["microphone_settings"]["input_source"] = 1
        cases.append(
            (
                "fractional microphone",
                fractional_microphone,
                "invalid_microphone_settings",
            )
        )

        invalid_engine = deepcopy(original)
        invalid_engine["avatars"][0]["tts"]["engine"] = "unknown"
        cases.append(("invalid engine", invalid_engine, "unsupported_tts_engine"))

        invalid_profile_id = deepcopy(original)
        invalid_profile_id["avatars"][0]["stt"]["profile_id"] = ":service-profile"
        cases.append(("invalid profile ID", invalid_profile_id, "invalid_stt_settings"))

        for label, definition, expected_code in cases:
            with self.subTest(label=label):
                with self.assertRaises(ServiceError) as raised:
                    service.replace_avatar_speech_editor_state("token", definition)
                self.assertEqual(raised.exception.error_code, expected_code)
                self.assertEqual(
                    service.get_avatar_speech_editor_state("token"),
                    original,
                )


if __name__ == "__main__":
    unittest.main()
