import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from otomekairo.http_server import OtomeKairoHttpServer
from otomekairo.service.app import OtomeKairoService


class WebUiStaticTests(unittest.TestCase):
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

    def request(
        self,
        method: str,
        path: str,
        *,
        body: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            payload = response.read()
            return response.status, dict(response.getheaders()), payload
        finally:
            connection.close()

    def test_root_redirects_to_web_ui(self) -> None:
        status, headers, body = self.request("GET", "/")

        self.assertEqual(status, 302)
        self.assertEqual(headers["Location"], "/ui/")
        self.assertEqual(body, b"")

    def test_ui_without_trailing_slash_redirects_to_web_ui(self) -> None:
        status, headers, body = self.request("GET", "/ui")

        self.assertEqual(status, 302)
        self.assertEqual(headers["Location"], "/ui/")
        self.assertEqual(body, b"")

    def test_web_ui_index_is_served_without_token(self) -> None:
        status, headers, body = self.request("GET", "/ui/")

        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        html = body.decode("utf-8")
        self.assertIn("<title>OtomeKairo</title>", html)
        self.assertIn("チャット", html)
        self.assertIn("人格設定", html)
        self.assertIn("システム", html)
        self.assertIn("判断機会ポリシー", html)
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_web_ui_assets_are_served_without_token(self) -> None:
        js_status, js_headers, js_body = self.request("GET", "/ui/app.js")
        css_status, css_headers, css_body = self.request("GET", "/ui/styles.css")

        self.assertEqual(js_status, 200)
        self.assertIn("text/javascript", js_headers["Content-Type"])
        self.assertIn(b"/ui/api/status", js_body)
        self.assertIn(b"/ui/api/conversation", js_body)
        self.assertIn(b"const images = state.attachment ? [state.attachment.data] : [];", js_body)
        self.assertNotIn(b"{ data: state.attachment.data }", js_body)
        self.assertNotIn(b"Authorization", js_body)
        self.assertNotIn(b"localStorage", js_body)
        self.assertEqual(css_status, 200)
        self.assertIn("text/css", css_headers["Content-Type"])
        self.assertIn(b".topbar", css_body)
        self.assertIn(b"#4873cf", css_body)

    def test_web_ui_api_uses_server_token_without_browser_token(self) -> None:
        status, headers, body = self.request("GET", "/ui/api/status")

        self.assertEqual(status, 200)
        self.assertIn("application/json", headers["Content-Type"])
        payload = json.loads(body.decode("utf-8"))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["runtime_summary"]["connection_state"], "ready")

    def test_web_ui_api_initializes_console_token_for_existing_api(self) -> None:
        ui_status, _, _ = self.request("GET", "/ui/api/status")
        state = self.service.store.read_state()
        token = state["console_access_token"]

        status, _, body = self.request(
            "GET",
            "/api/status",
            headers={"Authorization": f"Bearer {token}"},
        )
        payload = json.loads(body.decode("utf-8"))

        self.assertEqual(ui_status, 200)
        self.assertIsInstance(token, str)
        self.assertTrue(token.startswith("tok_"))
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])

    def test_web_ui_conversation_uses_server_token_without_browser_token(self) -> None:
        captured = {}

        def handle_conversation(token: str | None, payload: dict) -> dict:
            captured["token"] = token
            captured["payload"] = payload
            return {
                "result_kind": "speech",
                "speech": {"text": "応答しました。"},
            }

        self.service.handle_conversation = handle_conversation

        status, headers, body = self.request(
            "POST",
            "/ui/api/conversation",
            body=json.dumps({"text": "こんにちは", "client_context": {"source": "test"}}),
            headers={"Content-Type": "application/json"},
        )
        payload = json.loads(body.decode("utf-8"))

        self.assertEqual(status, 200)
        self.assertIn("application/json", headers["Content-Type"])
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["speech"]["text"], "応答しました。")
        self.assertIsInstance(captured["token"], str)
        self.assertTrue(captured["token"].startswith("tok_"))
        self.assertEqual(captured["payload"]["text"], "こんにちは")

    def test_existing_api_still_requires_token(self) -> None:
        status, headers, body = self.request("GET", "/api/status")

        self.assertEqual(status, 401)
        self.assertIn("application/json", headers["Content-Type"])
        payload = json.loads(body.decode("utf-8"))
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "bootstrap_required")

    def test_existing_api_accepts_issued_token(self) -> None:
        register_status, _, register_body = self.request(
            "POST",
            "/api/bootstrap/register-first-console",
            body="{}",
            headers={"Content-Type": "application/json"},
        )
        token = json.loads(register_body.decode("utf-8"))["data"]["console_access_token"]

        status, _, body = self.request(
            "GET",
            "/api/status",
            headers={"Authorization": f"Bearer {token}"},
        )
        payload = json.loads(body.decode("utf-8"))

        self.assertEqual(register_status, 201)
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["runtime_summary"]["connection_state"], "ready")


if __name__ == "__main__":
    unittest.main()
