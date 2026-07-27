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
        self.assertIn("<title>CocoroAI</title>", html)
        self.assertIn("チャット", html)
        self.assertIn("人格設定", html)
        self.assertIn("システム", html)
        self.assertIn("判断機会ポリシー", html)
        self.assertIn('id="model-max-output-tokens"', html)
        self.assertIn('id="model-timeout-seconds"', html)
        self.assertNotIn('id="role-list"', html)
        self.assertIn('data-tab="watcher"', html)
        self.assertIn("登録済みカメラ", html)
        self.assertIn("差分比閾値（変化した画素の割合 0～1）", html)
        self.assertIn("画素差分閾値（1画素を変化扱いする明暗差 1～255）", html)
        self.assertIn('id="conversation-display-name" required', html)
        self.assertNotIn('id="persona-interlocutor-address-term"', html)
        self.assertNotIn('id="persona-interlocutor-reference"', html)
        self.assertNotIn("対象カメラ", html)
        self.assertNotIn("camera-watcher-motion-threshold", html)
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_web_ui_assets_are_served_without_token(self) -> None:
        js_status, js_headers, js_body = self.request("GET", "/ui/app.js")
        css_status, css_headers, css_body = self.request("GET", "/ui/styles.css")

        self.assertEqual(js_status, 200)
        self.assertIn("text/javascript", js_headers["Content-Type"])
        self.assertIn(b"/ui/api/status", js_body)
        self.assertIn(b"/ui/api/conversation", js_body)
        self.assertIn(b"const images = state.attachment ? [state.attachment.data] : [];", js_body)
        self.assertIn(b"state.editor.current.selected_persona_id = state.selectedPersonaId;", js_body)
        self.assertIn(b"state.editor.current.selected_memory_set_id = state.selectedMemorySetId;", js_body)
        self.assertIn(b"state.editor.current.selected_model_preset_id = state.selectedModelPresetId;", js_body)
        save_settings_body = js_body[
            js_body.index(b"async function saveSettings") : js_body.index(b"function renderSettings")
        ]
        self.assertNotIn(b"Promise.all", save_settings_body)
        self.assertNotIn(b"{ data: state.attachment.data }", js_body)
        self.assertNotIn(b"Authorization", js_body)
        self.assertIn(b'localStorage.getItem("otomekairo.person_ref")', js_body)
        self.assertIn(b"interaction_context", js_body)
        self.assertIn(b"conversation-interaction-ref", js_body)
        self.assertIn(b"display_name: displayName", js_body)
        self.assertNotIn(b"address_term", js_body)
        self.assertNotIn(b"interlocutor_address_term", js_body)
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

    def test_web_ui_sequential_settings_save_keeps_editor_changes_after_capability_saves(self) -> None:
        editor_status, _, editor_body = self.request("GET", "/ui/api/config/editor-state")
        camera_status, _, camera_body = self.request("GET", "/ui/api/config/camera-sources/editor-state")
        mcp_status, _, mcp_body = self.request("GET", "/ui/api/config/mcp-servers/editor-state")
        editor_payload = json.loads(editor_body.decode("utf-8"))["data"]
        camera_payload = json.loads(camera_body.decode("utf-8"))["data"]
        mcp_payload = json.loads(mcp_body.decode("utf-8"))["data"]
        expected_display_name = "model preset save regression"

        self.assertEqual(editor_status, 200)
        self.assertEqual(camera_status, 200)
        self.assertEqual(mcp_status, 200)

        for model_preset in editor_payload["model_presets"]:
            if model_preset["model_preset_id"] == editor_payload["current"]["selected_model_preset_id"]:
                model_preset["display_name"] = expected_display_name
                break
        else:
            self.fail("selected model preset was not present in editor-state")

        self.request(
            "PUT",
            "/ui/api/config/editor-state",
            body=json.dumps(editor_payload),
            headers={"Content-Type": "application/json"},
        )
        self.request(
            "PUT",
            "/ui/api/config/camera-sources/editor-state",
            body=json.dumps(camera_payload),
            headers={"Content-Type": "application/json"},
        )
        self.request(
            "PUT",
            "/ui/api/config/mcp-servers/editor-state",
            body=json.dumps(mcp_payload),
            headers={"Content-Type": "application/json"},
        )

        status, _, body = self.request("GET", "/ui/api/config/editor-state")
        reloaded_payload = json.loads(body.decode("utf-8"))["data"]
        selected_model_preset_id = reloaded_payload["current"]["selected_model_preset_id"]
        selected_model_preset = next(
            model_preset
            for model_preset in reloaded_payload["model_presets"]
            if model_preset["model_preset_id"] == selected_model_preset_id
        )

        self.assertEqual(status, 200)
        self.assertEqual(selected_model_preset["display_name"], expected_display_name)

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
