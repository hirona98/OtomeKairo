from __future__ import annotations

import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "connector_runtime_config_ready.py"
SPEC = importlib.util.spec_from_file_location("connector_runtime_config_ready", SCRIPT_PATH)
assert SPEC is not None
preflight = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules["connector_runtime_config_ready"] = preflight
SPEC.loader.exec_module(preflight)


class ConnectorRuntimeConfigPreflightTests(unittest.TestCase):
    def test_tapo_skips_without_enabled_camera_source(self) -> None:
        status = preflight.decide_start(
            "tapo_c220",
            {"camera_sources": [], "mcp_servers": []},
        )

        self.assertEqual(status, preflight.SKIP)

    def test_tapo_starts_with_exactly_one_enabled_camera_source(self) -> None:
        status = preflight.decide_start(
            "tapo_c220",
            {
                "camera_sources": [
                    {
                        "vision_source_id": "vision_source:main",
                        "connector_kind": "tapo_c220",
                    }
                ],
                "mcp_servers": [],
            },
        )

        self.assertEqual(status, preflight.START)

    def test_tapo_rejects_multiple_enabled_camera_sources(self) -> None:
        with self.assertRaises(preflight.PreflightError):
            preflight.decide_start(
                "tapo_c220",
                {
                    "camera_sources": [
                        {"vision_source_id": "vision_source:one", "connector_kind": "tapo_c220"},
                        {"vision_source_id": "vision_source:two", "connector_kind": "tapo_c220"},
                    ],
                    "mcp_servers": [],
                },
            )

    def test_mcp_skips_without_enabled_mcp_server(self) -> None:
        status = preflight.decide_start(
            "mcp_client",
            {"camera_sources": [], "mcp_servers": []},
        )

        self.assertEqual(status, preflight.SKIP)

    def test_mcp_starts_with_one_or_more_enabled_mcp_servers(self) -> None:
        status = preflight.decide_start(
            "mcp_client",
            {
                "camera_sources": [],
                "mcp_servers": [
                    {"mcp_server_id": "mcp:elyth", "connector_kind": "mcp_client"},
                    {"mcp_server_id": "mcp:other", "connector_kind": "mcp_client"},
                ],
            },
        )

        self.assertEqual(status, preflight.START)

    def test_load_settings_uses_configured_client_id_and_env_token(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.local.json"
            config_path.write_text(
                """
                {
                  "server": {
                    "base_url": "https://127.0.0.1:55601",
                    "access_token_env": "CUSTOM_TOKEN"
                  },
                  "connector": {
                    "client_id": "custom-client"
                  }
                }
                """,
                encoding="utf-8",
            )

            settings = preflight.load_settings(
                config_path=config_path,
                default_client_id="default-client",
                environ={"CUSTOM_TOKEN": "token"},
            )

        self.assertEqual(settings.client_id, "custom-client")
        self.assertEqual(settings.access_token, "token")

    def test_load_settings_reads_token_from_config_db(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            with sqlite3.connect(data_dir / "config.db") as conn:
                conn.execute(
                    """
                    CREATE TABLE server_identity (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        server_id TEXT NOT NULL,
                        server_display_name TEXT NOT NULL,
                        api_version TEXT NOT NULL,
                        console_access_token TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO server_identity (
                        id, server_id, server_display_name, api_version, console_access_token
                    )
                    VALUES (1, 'server:test', 'OtomeKairo', '0.1.0', 'db-token')
                    """
                )

            settings = preflight.load_settings(
                config_path=None,
                default_client_id="default-client",
                environ={
                    "OTOMEKAIRO_DATA_DIR": str(data_dir),
                    "OTOMEKAIRO_SERVER_URL": "https://127.0.0.1:55601",
                },
            )

        self.assertEqual(settings.access_token, "db-token")


if __name__ == "__main__":
    unittest.main()
