import os
import unittest
from unittest.mock import patch

from otomekairo_mcp_client_connector.config import McpServerConfig
from otomekairo_mcp_client_connector.mcp_bridge import _server_params
from otomekairo_mcp_client_connector.stream import _trace_event_payload


class McpConnectorSecurityBoundaryTests(unittest.TestCase):
    def test_child_process_receives_only_path_and_server_environment(self) -> None:
        server = McpServerConfig(
            mcp_server_id="elyth",
            command="elyth-mcp",
            args=[],
            env={"ELYTH_TOKEN": "configured-secret"},
            cwd=None,
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


if __name__ == "__main__":
    unittest.main()
