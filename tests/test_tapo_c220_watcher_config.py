from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from otomekairo_tapo_c220_watcher.config import ConfigError, resolve_watcher_id


class TapoC220WatcherConfigTests(unittest.TestCase):
    def test_resolve_watcher_id_includes_disabled_registered_watcher(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            self._write_config_db(
                data_dir,
                [
                    {
                        "vision_source_id": "vision_source:対面カメラ",
                        "display_name": "対面カメラ",
                        "watcher": {
                            "enabled": False,
                            "watcher_id": "watcher:対面カメラ",
                        },
                    }
                ],
            )
            resolved = resolve_watcher_id(
                environ={
                    "OTOMEKAIRO_CONFIG_DB_PATH": str(data_dir / "config.db"),
                }
            )
        self.assertEqual(resolved, "watcher:対面カメラ")

    def test_resolve_watcher_id_returns_none_when_unregistered(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            self._write_config_db(data_dir, [])
            resolved = resolve_watcher_id(
                environ={
                    "OTOMEKAIRO_CONFIG_DB_PATH": str(data_dir / "config.db"),
                }
            )
        self.assertIsNone(resolved)

    def test_resolve_watcher_id_requires_explicit_id_when_multiple(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            self._write_config_db(
                data_dir,
                [
                    {
                        "vision_source_id": "vision_source:A",
                        "watcher": {"enabled": False, "watcher_id": "watcher:A"},
                    },
                    {
                        "vision_source_id": "vision_source:B",
                        "watcher": {"enabled": True, "watcher_id": "watcher:B"},
                    },
                ],
            )
            with self.assertRaises(ConfigError):
                resolve_watcher_id(
                    environ={"OTOMEKAIRO_CONFIG_DB_PATH": str(data_dir / "config.db")}
                )

            resolved = resolve_watcher_id(
                environ={
                    "OTOMEKAIRO_CONFIG_DB_PATH": str(data_dir / "config.db"),
                    "OTOMEKAIRO_WATCHER_ID": "watcher:B",
                }
            )
        self.assertEqual(resolved, "watcher:B")

    def _write_config_db(self, data_dir: Path, camera_sources: list[dict]) -> None:
        with sqlite3.connect(data_dir / "config.db") as conn:
            conn.execute(
                """
                CREATE TABLE camera_sources (
                    vision_source_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL
                )
                """
            )
            for source in camera_sources:
                conn.execute(
                    "INSERT INTO camera_sources (vision_source_id, payload_json) VALUES (?, ?)",
                    [source["vision_source_id"], json.dumps(source, ensure_ascii=False)],
                )


if __name__ == "__main__":
    unittest.main()
