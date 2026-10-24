from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from otomekairo.http_server import OtomeKairoHttpServer
from otomekairo.service.app import OtomeKairoService
from otomekairo.service.common import ServiceError
from otomekairo.store.config import ConfigStore


class DesktopCaptureFixture:
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.service = OtomeKairoService(root_dir=self.root)
        self.token = self.service.acquire_console_access_token()["console_access_token"]
        self.policy = {
            "capture_target": "active_window",
            "idle_timeout_minutes": 0,
            "exclude_patterns": ["非公開"],
        }

    def save_policy(self, policy: dict) -> dict:
        return self.service.replace_desktop_capture_settings(self.token, {"desktop_capture": policy})


class DesktopCaptureTests(DesktopCaptureFixture, unittest.TestCase):
    def test_policy_is_editable_without_console_and_survives_restart(self) -> None:
        defaults = self.service.get_desktop_capture_settings(self.token)["desktop_capture"]
        self.assertEqual(defaults["capture_target"], "primary_screen")
        self.assertEqual(defaults["idle_timeout_minutes"], 10)
        self.assertTrue(defaults["exclude_patterns"])

        self.assertEqual(self.save_policy(self.policy), {"desktop_capture": self.policy})
        state = ConfigStore(self.root).read_state()
        self.assertEqual(state["desktop_capture_settings"], self.policy)
        self.assertEqual(state["console_client_settings"], {})
        self.assertNotIn("desktop_capture_defaults", state)

    def test_console_connect_and_save_do_not_overwrite_common_policy(self) -> None:
        self.save_policy(self.policy)
        for client_id in ("console-first", "console-second"):
            console = self.service.connect_console_client(self.token, client_id)
            self.assertEqual(console["settings"]["desktop_capture"], {"enabled": False})
            console["settings"]["desktop_capture"]["enabled"] = True
            self.service.replace_console_client_editor_state(self.token, client_id, console["settings"])
            self.service.patch_console_client_settings(self.token, client_id, {"desktop_capture": {"enabled": False}})
        self.assertEqual(self.service.get_desktop_capture_settings(self.token)["desktop_capture"], self.policy)

    def test_policy_rejects_invalid_or_missing_fields(self) -> None:
        invalid_policies = [
            {**self.policy, "capture_target": target}
            for target in ("all_monitors", "", None, False, [], {})
        ] + [
            {**self.policy, "idle_timeout_minutes": value}
            for value in (-1, True, 0.5, "10", None)
        ] + [
            {**self.policy, "exclude_patterns": value}
            for value in (None, "非公開", [None], [1])
        ] + [
            {key: value for key, value in self.policy.items() if key != missing}
            for missing in self.policy
        ] + [{**self.policy, "enabled": True}]
        for policy in invalid_policies:
            with self.subTest(policy=policy), self.assertRaises(ServiceError):
                self.save_policy(policy)
        with self.assertRaises(ServiceError):
            self.service.replace_desktop_capture_settings(self.token, self.policy)

    def test_console_settings_reject_capture_policy(self) -> None:
        console = self.service.connect_console_client(self.token, "console-first")
        console["settings"]["desktop_capture"].update(self.policy)
        with self.assertRaises(ServiceError):
            self.service.replace_console_client_editor_state(self.token, "console-first", console["settings"])

    def dispatch(self, kind: str) -> tuple[dict, dict]:
        source = {
            "vision_source_id": f"vision_source:{kind}",
            "kind": kind,
            "label": kind,
        }
        with (
            patch.object(self.service, "_select_capability_target", return_value={"target_client_id": "console-first", "vision_source": source}),
            patch.object(self.service, "_validate_capability_runtime_dispatchable"),
            patch.object(self.service, "_set_capability_runtime_busy"),
            patch.object(self.service._event_stream_registry, "send_to_client", return_value=True) as send,
        ):
            result = self.service._dispatch_capability_request(
                memory_set_id=self.service.store.read_state()["selected_memory_set_id"],
                capability_id="vision.capture",
                input_payload={"vision_source_id": source["vision_source_id"], "mode": "still"},
                current_time=self.service._now_iso(),
                goal_summary="現在の画面を観測する。",
                wait_for_response=False,
                component="Test",
                track_ongoing_action=False,
            )
        return result["request_record"], send.call_args.args[1]

    def test_next_request_uses_saved_policy_and_in_flight_request_is_fixed(self) -> None:
        first_record, first_event = self.dispatch("desktop")
        first_policy = deepcopy(first_event["data"]["desktop_capture"])
        self.save_policy(self.policy)
        second_record, second_event = self.dispatch("desktop")
        self.assertEqual(second_event["data"]["desktop_capture"], self.policy)
        self.assertEqual(second_record["desktop_capture"], self.policy)
        self.assertEqual(first_record["desktop_capture"], first_policy)
        self.assertEqual(self.service._capability_request_event_data(first_record)["desktop_capture"], first_policy)
        self.assertNotIn("desktop_capture", second_record["input"])

    def test_camera_and_virtual_requests_do_not_include_desktop_policy(self) -> None:
        for kind in ("camera", "virtual"):
            with self.subTest(kind=kind):
                record, event = self.dispatch(kind)
                self.assertNotIn("desktop_capture", record)
                self.assertNotIn("desktop_capture", event["data"])

    def test_missing_policy_is_not_replaced_with_defaults(self) -> None:
        state = self.service.store.read_state()
        del state["desktop_capture_settings"]
        with patch.object(self.service.store, "read_state", return_value=state), self.assertRaises(KeyError):
            self.dispatch("desktop")


class DesktopCaptureHttpTests(DesktopCaptureFixture, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.server = OtomeKairoHttpServer(("127.0.0.1", 0), self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()

    def request(self, method: str, path: str, *, body: dict | None = None, token: str | None = None) -> tuple[int, dict]:
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        try:
            connection.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read().decode("utf-8"))
        finally:
            connection.close()

    def test_authenticated_api_and_browser_api_share_policy(self) -> None:
        status, _ = self.request("GET", "/api/config/desktop-capture")
        self.assertEqual(status, 401)
        for path, token in (("/api/config/desktop-capture", self.token), ("/ui/api/config/desktop-capture", None)):
            with self.subTest(path=path):
                status, result = self.request("PUT", path, body={"desktop_capture": self.policy}, token=token)
                self.assertEqual(status, 200)
                self.assertEqual(result["data"], {"desktop_capture": self.policy})
                status, result = self.request("GET", path, token=token)
                self.assertEqual(status, 200)
                self.assertEqual(result["data"]["desktop_capture"], self.policy)
        status, _ = self.request("PUT", "/api/config/desktop-capture", body={"desktop_capture": {}}, token=self.token)
        self.assertEqual(status, 400)

    def test_removed_defaults_routes_are_not_found(self) -> None:
        for prefix in ("/api", "/ui/api"):
            for method in ("GET", "PUT"):
                with self.subTest(prefix=prefix, method=method):
                    status, _ = self.request(method, f"{prefix}/config/desktop-capture-defaults", body={}, token=self.token)
                    self.assertEqual(status, 404)
