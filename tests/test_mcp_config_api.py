import unittest
from copy import deepcopy

from otomekairo.defaults import build_default_state
from otomekairo.service.common import ServiceError
from otomekairo.service.config.mixin import ServiceConfigMixin


class DummyStore:
    def __init__(self) -> None:
        self.state = build_default_state()
        self.state["console_access_token"] = "token"
        self.events = []

    def read_state(self) -> dict:
        return deepcopy(self.state)

    def write_state(self, state: dict) -> None:
        self.state = deepcopy(state)

    def append_events(self, *, events: list[dict]) -> None:
        self.events.extend(deepcopy(events))


class DummyService(ServiceConfigMixin):
    def __init__(self) -> None:
        self.store = DummyStore()

    def _now_iso(self) -> str:
        return "2026-06-20T12:00:00+09:00"


class McpConfigApiTests(unittest.TestCase):
    def test_mcp_server_public_api_masks_env(self) -> None:
        service = DummyService()

        service.replace_mcp_server(
            "token",
            "mcp_server:elyth",
            {
                "enabled": True,
                "command": "npx",
                "args": ["-y", "elyth-mcp-server@latest"],
                "env": {
                    "ELYTH_API_BASE": "https://elythworld.com",
                    "ELYTH_API_KEY": "secret",
                },
            },
        )

        response = service.get_mcp_server("token", "mcp_server:elyth")
        mcp_server = response["mcp_server"]
        self.assertEqual(mcp_server["connector_kind"], "mcp_client")
        self.assertEqual(mcp_server["client_id"], "mcp-client-connector-main")
        self.assertEqual(mcp_server["transport"], "stdio")
        self.assertEqual(mcp_server["env"]["ELYTH_API_KEY"], {"value_present": True})
        self.assertNotEqual(mcp_server["env"]["ELYTH_API_KEY"], "secret")

    def test_mcp_servers_editor_state_returns_env_values(self) -> None:
        service = DummyService()

        response = service.replace_mcp_servers_editor_state(
            "token",
            {
                "mcp_servers": [
                    {
                        "mcp_server_id": "mcp_server:elyth",
                        "connector_kind": "mcp_client",
                        "client_id": "mcp-client-connector-main",
                        "enabled": True,
                        "transport": "stdio",
                        "command": "npx",
                        "args": ["-y", "elyth-mcp-server@latest"],
                        "cwd": None,
                        "env": {"ELYTH_API_KEY": "secret"},
                    }
                ]
            },
        )

        self.assertEqual(response["mcp_servers"][0]["env"]["ELYTH_API_KEY"], "secret")
        self.assertEqual(service.store.events[-1]["kind"], "mcp_servers_editor_state_write")

    def test_runtime_config_filters_mcp_servers_by_client_and_enabled(self) -> None:
        service = DummyService()
        service.replace_mcp_servers_editor_state(
            "token",
            {
                "mcp_servers": [
                    {
                        "mcp_server_id": "mcp_server:elyth",
                        "client_id": "mcp-client-connector-main",
                        "enabled": True,
                        "command": "npx",
                        "args": ["-y", "elyth-mcp-server@latest"],
                        "env": {"ELYTH_API_KEY": "secret"},
                    },
                    {
                        "mcp_server_id": "mcp_server:disabled",
                        "client_id": "mcp-client-connector-main",
                        "enabled": False,
                        "command": "npx",
                        "args": ["-y", "disabled"],
                        "env": {},
                    },
                    {
                        "mcp_server_id": "mcp_server:other",
                        "client_id": "mcp-client-connector-other",
                        "enabled": True,
                        "command": "npx",
                        "args": ["-y", "other"],
                        "env": {},
                    },
                ]
            },
        )

        response = service.get_connector_runtime_config("token", "mcp-client-connector-main")

        self.assertEqual(response["camera_sources"], [])
        self.assertEqual([item["mcp_server_id"] for item in response["mcp_servers"]], ["mcp_server:elyth"])
        self.assertEqual(response["mcp_servers"][0]["env"]["ELYTH_API_KEY"], "secret")
        self.assertEqual(service.store.events[-1]["mcp_server_count"], 1)

    def test_mcp_server_rejects_invalid_definition(self) -> None:
        service = DummyService()

        with self.assertRaises(ServiceError) as raised:
            service.replace_mcp_server(
                "token",
                "mcp_server:elyth",
                {
                    "enabled": True,
                    "transport": "sse",
                    "command": "npx",
                },
            )

        self.assertEqual(raised.exception.error_code, "unsupported_mcp_transport")


if __name__ == "__main__":
    unittest.main()
