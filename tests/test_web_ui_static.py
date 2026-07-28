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

    def test_existing_api_still_requires_token(self) -> None:
        status, headers, body = self.request("/api/status")
        payload = json.loads(body.decode("utf-8"))

        self.assertEqual(status, 401)
        self.assertIn("application/json", headers["Content-Type"])
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "bootstrap_required")


if __name__ == "__main__":
    unittest.main()
