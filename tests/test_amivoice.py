import unittest

from otomekairo.audio.amivoice import AmiVoiceClient


class AmiVoiceClientTests(unittest.TestCase):
    def test_profile_id_is_sent_as_profile_reference(self) -> None:
        body = AmiVoiceClient()._build_multipart_body(
            boundary="boundary",
            pcm16le=b"\x00\x00",
            api_key="api-key",
            profile_id="service_profile-01",
        )

        self.assertIn(
            b"grammarFileNames=-a-general profileId=:service_profile-01",
            body,
        )


if __name__ == "__main__":
    unittest.main()
