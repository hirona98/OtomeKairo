import tempfile
import unittest
from pathlib import Path

from otomekairo.store.file_store import FileStore


class ConfigStoreTests(unittest.TestCase):
    def test_file_store_uses_config_db_without_server_state_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root_dir = Path(temp_dir)
            store = FileStore(root_dir)

            state = store.read_state()
            state["console_access_token"] = "token"
            state["camera_sources"] = {
                "vision_source:main": {
                    "vision_source_id": "vision_source:main",
                    "connector_kind": "tapo_c220",
                    "client_id": "tapo-c220-connector-main",
                    "kind": "camera",
                    "source_owner": "self",
                    "enabled": True,
                    "label": "C220",
                    "connection": {
                        "host": "192.0.2.10",
                        "camera_username": "user",
                        "camera_password": "password",
                    },
                }
            }
            state["mcp_servers"] = {
                "mcp_server:elyth": {
                    "mcp_server_id": "mcp_server:elyth",
                    "connector_kind": "mcp_client",
                    "client_id": "mcp-client-connector-main",
                    "enabled": True,
                    "label": "ELYTH",
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "elyth-mcp-server@latest"],
                    "cwd": None,
                    "env": {"ELYTH_API_KEY": "secret"},
                }
            }
            store.write_state(state)

            reloaded_store = FileStore(root_dir)
            reloaded_state = reloaded_store.read_state()

            self.assertTrue((root_dir / "config.db").exists())
            self.assertTrue((root_dir / "memory.db").exists())
            self.assertFalse((root_dir / "server_state.json").exists())
            self.assertEqual(reloaded_state["console_access_token"], "token")
            self.assertEqual(reloaded_state["camera_sources"]["vision_source:main"]["connection"]["camera_password"], "password")
            self.assertEqual(reloaded_state["mcp_servers"]["mcp_server:elyth"]["env"]["ELYTH_API_KEY"], "secret")


if __name__ == "__main__":
    unittest.main()
