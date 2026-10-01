import unittest
from unittest.mock import patch

from otomekairo.audio.amivoice import (
    AMIVOICE_ENDPOINTS,
    AMIVOICE_TIMEOUT_SECONDS,
    AmiVoiceClient,
    AmiVoiceError,
)


class _Response:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> bool:
        return False


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

    def test_engine_selects_logging_endpoint(self) -> None:
        # ログなしとログありは endpoint だけで分かれ、リクエスト本体は同じ。
        client = AmiVoiceClient()
        for engine, endpoint in AMIVOICE_ENDPOINTS.items():
            with self.subTest(engine=engine):
                seen: dict[str, object] = {}

                def fake_urlopen(request, timeout, seen=seen):
                    seen["url"] = request.full_url
                    seen["timeout"] = timeout
                    seen["body"] = request.data
                    return _Response(
                        '{"code":"","message":"","text":"こんにちは","confidence":0.9}'.encode()
                    )

                with patch("otomekairo.audio.amivoice.urllib.request.urlopen", fake_urlopen):
                    result = client.recognize(
                        b"\x00\x00",
                        api_key="api-key",
                        profile_id="",
                        engine=engine,
                    )

                self.assertEqual(seen["url"], endpoint)
                self.assertEqual(seen["timeout"], AMIVOICE_TIMEOUT_SECONDS)
                self.assertIn(b"grammarFileNames=-a-general", seen["body"])
                self.assertNotIn(b"profileId=", seen["body"])
                self.assertEqual(result.text, "こんにちは")

    def test_unknown_engine_fails_before_request(self) -> None:
        client = AmiVoiceClient()

        def fail_urlopen(request, timeout):
            raise AssertionError("unsupported engine must not call AmiVoice")

        with patch("otomekairo.audio.amivoice.urllib.request.urlopen", fail_urlopen):
            with self.assertRaises(AmiVoiceError) as raised:
                client.recognize(
                    b"\x00\x00",
                    api_key="api-key",
                    profile_id="",
                    engine="amivoice-free",
                )

        self.assertEqual(raised.exception.code, "unsupported_engine")


if __name__ == "__main__":
    unittest.main()
