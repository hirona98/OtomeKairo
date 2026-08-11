import os
import unittest
from unittest.mock import patch

from otomekairo_mcp_client_connector.config import McpServerConfig
from otomekairo_mcp_client_connector.mcp_bridge import _server_params
from otomekairo_mcp_client_connector.stream import _trace_event_payload
from otomekairo_mcp_client_connector.trace import MASKED, mask_secrets


class McpConnectorSecurityBoundaryTests(unittest.TestCase):
    def test_child_process_receives_only_path_and_server_environment(self) -> None:
        server = McpServerConfig(
            mcp_server_id="elyth",
            transport="stdio",
            command="elyth-mcp",
            args=[],
            env={"ELYTH_TOKEN": "configured-secret"},
            cwd=None,
            url=None,
            headers={},
        )

        with patch.dict(
            os.environ,
            {"PATH": "/safe/bin", "PARENT_SECRET": "must-not-leak"},
            clear=True,
        ):
            params = _server_params(server)

        self.assertEqual(
            params.env,
            {"PATH": "/safe/bin", "ELYTH_TOKEN": "configured-secret"},
        )

    def test_mcp_arguments_are_removed_from_event_trace_payload(self) -> None:
        event = {
            "type": "mcp.call_tool_request",
            "data": {
                "request_id": "request:test",
                "mcp_server_id": "elyth",
                "tool_name": "create_post",
                "arguments": {"content": "private candidate"},
            },
        }

        traced = _trace_event_payload(event)

        self.assertNotIn("arguments", traced["data"])
        self.assertEqual(event["data"]["arguments"], {"content": "private candidate"})

    def test_remote_authorization_header_is_masked_in_trace(self) -> None:
        payload = {
            "mcp_servers": [
                {
                    "mcp_server_id": "elyth",
                    "headers": {"Authorization": "Bearer configured-secret"},
                }
            ]
        }

        masked = mask_secrets(payload)

        self.assertEqual(masked["mcp_servers"][0]["headers"]["Authorization"], MASKED)
        self.assertEqual(payload["mcp_servers"][0]["headers"]["Authorization"], "Bearer configured-secret")


if __name__ == "__main__":
    unittest.main()
