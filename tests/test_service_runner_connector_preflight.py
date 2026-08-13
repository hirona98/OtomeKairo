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

WATCHER_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "watcher_runtime_config_ready.py"
WATCHER_SPEC = importlib.util.spec_from_file_location("watcher_runtime_config_ready", WATCHER_SCRIPT_PATH)
assert WATCHER_SPEC is not None
watcher_preflight = importlib.util.module_from_spec(WATCHER_SPEC)
assert WATCHER_SPEC.loader is not None
sys.modules["watcher_runtime_config_ready"] = watcher_preflight
WATCHER_SPEC.loader.exec_module(watcher_preflight)


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
                    {"mcp_server_id": "e-stat", "connector_kind": "mcp_client"},
                    {"mcp_server_id": "other", "connector_kind": "mcp_client"},
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
                    VALUES (1, 'server:test', 'OtomeKairo', '0.2.0', 'db-token')
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


def _write_watcher_config_db(
    data_dir: Path,
    *,
    watcher_id: str,
    watcher_enabled: bool,
) -> None:
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
            VALUES (1, 'server:test', 'OtomeKairo', '0.2.0', 'db-token')
            """
        )
        conn.execute(
            """
            CREATE TABLE camera_sources (
                vision_source_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO camera_sources (vision_source_id, payload_json)
            VALUES (?, ?)
            """,
            [
                f"vision_source:{watcher_id.removeprefix('watcher:')}",
                (
                    "{"
                    f'"vision_source_id": "vision_source:{watcher_id.removeprefix("watcher:")}", '
                    f'"display_name": "{watcher_id.removeprefix("watcher:")}", '
                    '"enabled": true, '
                    '"watcher": {'
                    f'"enabled": {str(watcher_enabled).lower()}, '
                    f'"watcher_id": "{watcher_id}"'
                    "}"
                    "}"
                ),
            ],
        )


class WatcherRuntimeConfigPreflightTests(unittest.TestCase):
    def test_watcher_main_starts_when_watcher_is_enabled(self) -> None:
        original_fetch = watcher_preflight.fetch_runtime_config
        original_load = watcher_preflight.load_settings
        original_argv = sys.argv[:]
        try:
            sys.argv = ["watcher_runtime_config_ready.py"]
            watcher_preflight.load_settings = lambda: {"watcher_id": "watcher:camera"}
            watcher_preflight.fetch_runtime_config = lambda _: {
                "watcher": {"enabled": True},
                "camera_source": {"enabled": False},
            }

            status = watcher_preflight.main()
        finally:
            watcher_preflight.fetch_runtime_config = original_fetch
            watcher_preflight.load_settings = original_load
            sys.argv = original_argv

        self.assertEqual(status, watcher_preflight.START)

    def test_watcher_main_starts_when_watcher_is_disabled(self) -> None:
        # idle 常駐: enabled=false でも START し、プロセス内で runtime config を待つ。
        original_fetch = watcher_preflight.fetch_runtime_config
        original_load = watcher_preflight.load_settings
        original_argv = sys.argv[:]
        try:
            sys.argv = ["watcher_runtime_config_ready.py"]
            watcher_preflight.load_settings = lambda: {"watcher_id": "watcher:対面カメラ"}
            watcher_preflight.fetch_runtime_config = lambda _: {
                "watcher": {"enabled": False, "watcher_id": "watcher:対面カメラ"},
                "camera_source": {"display_name": "対面カメラ"},
            }

            status = watcher_preflight.main()
        finally:
            watcher_preflight.fetch_runtime_config = original_fetch
            watcher_preflight.load_settings = original_load
            sys.argv = original_argv

        self.assertEqual(status, watcher_preflight.START)

    def test_watcher_main_skips_when_no_registered_watcher(self) -> None:
        original_load = watcher_preflight.load_settings
        original_argv = sys.argv[:]
        try:
            sys.argv = ["watcher_runtime_config_ready.py"]

            def _raise_not_found(**_kwargs):
                raise watcher_preflight.RuntimeConfigNotFound("no registered watcher in config.db.")

            watcher_preflight.load_settings = _raise_not_found
            status = watcher_preflight.main()
        finally:
            watcher_preflight.load_settings = original_load
            sys.argv = original_argv

        self.assertEqual(status, watcher_preflight.SKIP)

    def test_watcher_load_settings_uses_db_watcher_id_and_env_token(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            _write_watcher_config_db(
                data_dir,
                watcher_id="watcher:camera",
                watcher_enabled=True,
            )

            original_environ = dict(watcher_preflight.os.environ)
            try:
                watcher_preflight.os.environ.clear()
                watcher_preflight.os.environ["OTOMEKAIRO_DATA_DIR"] = str(data_dir)
                watcher_preflight.os.environ["OTOMEKAIRO_SERVER_URL"] = "https://127.0.0.1:55601"
                settings = watcher_preflight.load_settings()
            finally:
                watcher_preflight.os.environ.clear()
                watcher_preflight.os.environ.update(original_environ)

        self.assertEqual(settings["watcher_id"], "watcher:camera")
        self.assertEqual(settings["access_token"], "db-token")

    def test_watcher_load_settings_uses_disabled_registered_watcher_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            _write_watcher_config_db(
                data_dir,
                watcher_id="watcher:対面カメラ",
                watcher_enabled=False,
            )

            original_environ = dict(watcher_preflight.os.environ)
            try:
                watcher_preflight.os.environ.clear()
                watcher_preflight.os.environ["OTOMEKAIRO_DATA_DIR"] = str(data_dir)
                watcher_preflight.os.environ["OTOMEKAIRO_SERVER_URL"] = "https://127.0.0.1:55601"
                settings = watcher_preflight.load_settings()
            finally:
                watcher_preflight.os.environ.clear()
                watcher_preflight.os.environ.update(original_environ)

        self.assertEqual(settings["watcher_id"], "watcher:対面カメラ")
        self.assertEqual(settings["access_token"], "db-token")

    def test_watcher_load_settings_skips_default_when_no_registered_watcher(self) -> None:
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
                    VALUES (1, 'server:test', 'OtomeKairo', '0.2.0', 'db-token')
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE camera_sources (
                        vision_source_id TEXT PRIMARY KEY,
                        payload_json TEXT NOT NULL
                    )
                    """
                )

            original_environ = dict(watcher_preflight.os.environ)
            try:
                watcher_preflight.os.environ.clear()
                watcher_preflight.os.environ["OTOMEKAIRO_DATA_DIR"] = str(data_dir)
                watcher_preflight.os.environ["OTOMEKAIRO_SERVER_URL"] = "https://127.0.0.1:55601"
                with self.assertRaises(watcher_preflight.RuntimeConfigNotFound):
                    watcher_preflight.load_settings()
            finally:
                watcher_preflight.os.environ.clear()
                watcher_preflight.os.environ.update(original_environ)


if __name__ == "__main__":
    unittest.main()
