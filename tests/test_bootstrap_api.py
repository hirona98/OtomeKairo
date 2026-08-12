import http.client
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from otomekairo.http_server import OtomeKairoHttpServer
from otomekairo.service.app import OtomeKairoService


class BootstrapHttpBoundaryTests(unittest.TestCase):
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
        token: str | None = None,
    ) -> tuple[int, dict]:
        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        try:
            body = "{}" if method == "POST" else None
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read().decode("utf-8"))
        finally:
            connection.close()

    def test_acquire_creates_token_and_returns_existing_token(self) -> None:
        first_status, first_payload = self.request(
            "POST",
            "/api/bootstrap/acquire-console-access-token",
        )
        second_status, second_payload = self.request(
            "POST",
            "/api/bootstrap/acquire-console-access-token",
        )

        self.assertEqual(first_status, 200)
        self.assertEqual(second_status, 200)
        first_token = first_payload["data"]["console_access_token"]
        self.assertTrue(first_token)
        self.assertEqual(second_payload["data"]["console_access_token"], first_token)

        status, payload = self.request("GET", "/api/status", token=first_token)
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])

    def test_acquire_returns_reissued_token(self) -> None:
        _, acquired = self.request("POST", "/api/bootstrap/acquire-console-access-token")
        old_token = acquired["data"]["console_access_token"]

        status, reissued = self.request(
            "POST",
            "/api/bootstrap/reissue-console-access-token",
            token=old_token,
        )
        self.assertEqual(status, 200)
        new_token = reissued["data"]["console_access_token"]
        self.assertNotEqual(new_token, old_token)

        _, recovered = self.request("POST", "/api/bootstrap/acquire-console-access-token")
        self.assertEqual(recovered["data"]["console_access_token"], new_token)

    def test_concurrent_acquire_returns_one_token(self) -> None:
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: self.service.acquire_console_access_token(), range(16)))

        self.assertEqual(len({result["console_access_token"] for result in results}), 1)

    def test_removed_first_console_route_is_not_found(self) -> None:
        status, payload = self.request("POST", "/api/bootstrap/register-first-console")

        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "route_not_found")


if __name__ == "__main__":
    unittest.main()
