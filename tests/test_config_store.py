import sqlite3
import tempfile
import unittest
from pathlib import Path

from otomekairo.store.file_store import FileStore
from otomekairo.store.config import ConfigStore


class ConfigStoreTests(unittest.TestCase):
    def test_config_store_rejects_previous_schema_version(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root_dir = Path(temp_dir)
            with sqlite3.connect(root_dir / "config.db") as conn:
                conn.execute("PRAGMA user_version = 19")

            with self.assertRaisesRegex(RuntimeError, "Unsupported config.db schema version: 19"):
                FileStore(root_dir)

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
                    "display_name": "C220",
                    "connection": {
                        "host": "192.0.2.10",
                        "camera_username": "user",
                        "camera_password": "password",
                    },
                }
            }
            state["mcp_servers"] = {
                "e-stat": {
                    "mcp_server_id": "e-stat",
                    "connector_kind": "mcp_client",
                    "client_id": "mcp-client-connector-main",
                    "enabled": True,
                    "pre_send_check_enabled": True,
                    "transport": "stdio",
                    "command": "uvx",
                    "args": ["estat-mcp-server"],
                    "cwd": None,
                    "env": {"E_STAT_APP_ID": "secret"},
                }
            }
            state["selected_avatar_id"] = "avatar:default"
            state["microphone_settings"] = {
                "input_threshold_db": -35,
                "speaker_recognition_threshold": 0.65,
            }
            state["avatars"]["avatar:default"]["stt"]["api_key"] = "stt-secret"
            state["avatars"]["avatar:default"]["tts"]["aivis_cloud_config"][
                "api_key"
            ] = "tts-secret"
            display_name_id = "conversation_display_name:tanaka"
            store.create_conversation_display_name(
                conversation_display_name_id=display_name_id,
                display_name="田中さん",
            )
            state["selected_conversation_display_name_id"] = display_name_id
            state["console_client_settings"] = {
                "console-main": {
                    "last_connected_at": "2026-07-27T12:00:00+09:00",
                    "settings": {
                        "client_id": "console-main",
                        "process": {},
                    },
                }
            }
            store.write_state(state)

            reloaded_store = FileStore(root_dir)
            reloaded_state = reloaded_store.read_state()

            self.assertTrue((root_dir / "config.db").exists())
            self.assertTrue((root_dir / "memory.db").exists())
            self.assertFalse((root_dir / "server_state.json").exists())
            self.assertEqual(reloaded_state["console_access_token"], "token")
            self.assertEqual(
                reloaded_state["camera_sources"]["vision_source:main"]["connection"][
                    "camera_password"
                ],
                "password",
            )
            self.assertEqual(reloaded_state["mcp_servers"]["e-stat"]["env"]["E_STAT_APP_ID"], "secret")
            self.assertTrue(
                reloaded_state["mcp_servers"]["e-stat"]["pre_send_check_enabled"]
            )
            self.assertEqual(
                reloaded_state["microphone_settings"],
                {
                    "input_threshold_db": -35,
                    "speaker_recognition_threshold": 0.65,
                },
            )
            self.assertEqual(
                reloaded_state["avatars"]["avatar:default"]["stt"]["api_key"],
                "stt-secret",
            )
            self.assertEqual(
                reloaded_state["avatars"]["avatar:default"]["tts"][
                    "aivis_cloud_config"
                ]["api_key"],
                "tts-secret",
            )
            self.assertEqual(
                reloaded_state["selected_conversation_display_name_id"],
                display_name_id,
            )
            self.assertEqual(
                reloaded_state["conversation_display_names"][display_name_id][
                    "display_name"
                ],
                "田中さん",
            )
            self.assertEqual(
                reloaded_state["console_client_settings"]["console-main"][
                    "last_connected_at"
                ],
                "2026-07-27T12:00:00+09:00",
            )


def test_config_read_keeps_selection_and_definitions_in_one_snapshot(tmp_path, monkeypatch):
    reader = ConfigStore(tmp_path)
    writer = ConfigStore(tmp_path)
    before = writer.read_state()
    after = writer.read_state()
    after["server_display_name"] = "更新後の検証用サーバ"
    for collection, selected, id_key, replacement in (
        ("personas", "selected_persona_id", "persona_id", "persona:replacement"),
        ("memory_sets", "selected_memory_set_id", "memory_set_id", "memory_set:replacement"),
        ("model_presets", "selected_model_preset_id", "model_preset_id", "model_preset:replacement"),
    ):
        definition = after[collection].pop(after[selected])
        after[collection][replacement] = {**definition, id_key: replacement}
        after[selected] = replacement

    original = reader._read_conversation_display_names

    def update_between_selection_and_definitions(conn):
        # 別接続の更新を確定させても、進行中の読み取りは元の全体を保つ。
        writer.write_state(after)
        return original(conn)

    with monkeypatch.context() as patch:
        patch.setattr(reader, "_read_conversation_display_names", update_between_selection_and_definitions)
        assert reader.read_state() == before
    assert reader.read_state() == after


if __name__ == "__main__":
    unittest.main()
