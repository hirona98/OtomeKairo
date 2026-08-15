import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from otomekairo.http_server import OtomeKairoHttpServer
from otomekairo.service.app import OtomeKairoService


class WebUiHttpBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.service = OtomeKairoService(root_dir=Path(self.temp_dir.name))
        self.server = OtomeKairoHttpServer(("127.0.0.1", 0), self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.server.server_address

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
        self.temp_dir.cleanup()

    def request(self, path: str) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_root_redirects_to_web_ui(self) -> None:
        status, headers, body = self.request("/")

        self.assertEqual(status, 302)
        self.assertEqual(headers["Location"], "/ui/")
        self.assertEqual(body, b"")

    def test_web_ui_assets_are_served_without_token(self) -> None:
        for path, content_type in (
            ("/ui/", "text/html"),
            ("/ui/app.js", "text/javascript"),
            ("/ui/audio-worklet.js", "text/javascript"),
            ("/ui/logs", "text/html"),
            ("/ui/logs.html", "text/html"),
            ("/ui/logs.js", "text/javascript"),
            ("/ui/cycles", "text/html"),
            ("/ui/cycles.html", "text/html"),
            ("/ui/cycles.js", "text/javascript"),
            ("/ui/styles.css", "text/css"),
        ):
            status, headers, body = self.request(path)
            self.assertEqual(status, 200)
            self.assertIn(content_type, headers["Content-Type"])
            self.assertTrue(body)

    def test_web_ui_api_uses_server_token_without_browser_token(self) -> None:
        status, headers, body = self.request("/ui/api/status")
        payload = json.loads(body.decode("utf-8"))

        self.assertEqual(status, 200)
        self.assertIn("application/json", headers["Content-Type"])
        self.assertTrue(payload["ok"])

    def test_web_ui_reads_saved_avatar_speech_settings_without_browser_token(self) -> None:
        state = self.service.store.read_state()
        state["microphone_settings"]["vad_probability_threshold"] = 0.7
        state["microphone_settings"]["speaker_recognition_threshold"] = 0.75
        self.service.store.write_state(state)

        status, headers, body = self.request("/ui/api/config/avatar-speech")
        payload = json.loads(body.decode("utf-8"))

        self.assertEqual(status, 200)
        self.assertIn("application/json", headers["Content-Type"])
        self.assertEqual(
            payload["data"]["microphone_settings"]["vad_probability_threshold"],
            0.7,
        )
        self.assertEqual(
            payload["data"]["microphone_settings"]["speaker_recognition_threshold"],
            0.75,
        )

    def test_existing_api_still_requires_token(self) -> None:
        status, headers, body = self.request("/api/status")
        payload = json.loads(body.decode("utf-8"))

        self.assertEqual(status, 401)
        self.assertIn("application/json", headers["Content-Type"])
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "bootstrap_required")

    def test_web_ui_cycle_inspection_routes_without_browser_token(self) -> None:
        # 一覧は token をブラウザへ渡さず読める。
        status, headers, body = self.request("/ui/api/inspection/cycle-summaries?limit=5")
        payload = json.loads(body.decode("utf-8"))

        self.assertEqual(status, 200)
        self.assertIn("application/json", headers["Content-Type"])
        self.assertTrue(payload["ok"])
        self.assertIn("cycle_summaries", payload["data"])

        # 存在しない cycle は wire と同じ cycle_not_found。
        missing_status, _, missing_body = self.request(
            "/ui/api/inspection/cycles/cycle%3Amissing/cognitive-context"
        )
        missing_payload = json.loads(missing_body.decode("utf-8"))
        self.assertEqual(missing_status, 404)
        self.assertFalse(missing_payload["ok"])
        self.assertEqual(missing_payload["error"]["code"], "cycle_not_found")

        missing_trace_status, _, missing_trace_body = self.request(
            "/ui/api/inspection/cycles/cycle%3Amissing"
        )
        missing_trace_payload = json.loads(missing_trace_body.decode("utf-8"))
        self.assertEqual(missing_trace_status, 404)
        self.assertEqual(missing_trace_payload["error"]["code"], "cycle_not_found")


if __name__ == "__main__":
    unittest.main()
