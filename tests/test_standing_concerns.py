import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from otomekairo.defaults import build_default_standing_concerns
from otomekairo.llm.contexts import CurrentInput
from otomekairo.service.input.pipeline import ServiceInputPipelineMixin
from otomekairo.service.standing_concerns import (
    extra_background_thinking_delay_seconds,
    list_due_standing_concerns,
    selected_standing_concern_ids,
    standing_concern_is_due,
)
from otomekairo.store.file_store import FileStore


class StandingConcernLogicTests(unittest.TestCase):
    def test_due_when_never_attended(self) -> None:
        self.assertTrue(
            standing_concern_is_due(
                enabled=True,
                min_interval_seconds=3600,
                last_attended_at=None,
                current_time="2026-08-13T12:00:00+09:00",
            )
        )

    def test_not_due_before_interval(self) -> None:
        self.assertFalse(
            standing_concern_is_due(
                enabled=True,
                min_interval_seconds=3600,
                last_attended_at="2026-08-13T11:30:00+09:00",
                current_time="2026-08-13T12:00:00+09:00",
            )
        )

    def test_due_after_interval(self) -> None:
        self.assertTrue(
            standing_concern_is_due(
                enabled=True,
                min_interval_seconds=3600,
                last_attended_at="2026-08-13T10:00:00+09:00",
                current_time="2026-08-13T12:00:00+09:00",
            )
        )

    def test_disabled_is_not_due(self) -> None:
        self.assertFalse(
            standing_concern_is_due(
                enabled=False,
                min_interval_seconds=3600,
                last_attended_at=None,
                current_time="2026-08-13T12:00:00+09:00",
            )
        )

    def test_list_due_filters_disabled_and_recent(self) -> None:
        due = list_due_standing_concerns(
            concerns=[
                {
                    "concern_id": "elyth",
                    "enabled": True,
                    "min_interval_seconds": 3600,
                    "concern_summary": "ELYTH",
                },
                {
                    "concern_id": "other",
                    "enabled": True,
                    "min_interval_seconds": 3600,
                    "concern_summary": "other",
                },
            ],
            last_attended_at_by_id={"other": "2026-08-13T11:30:00+09:00"},
            current_time="2026-08-13T12:00:00+09:00",
        )
        self.assertEqual([item["concern_id"] for item in due], ["elyth"])

    def test_extra_thinking_waits_for_near_regular_wake(self) -> None:
        delay = extra_background_thinking_delay_seconds(
            wake_mode="interval",
            wake_interval_seconds=300,
            last_wake_at="2026-08-13T11:59:00+09:00",
            due_concerns=[{"min_interval_seconds": 3600}],
            current_time="2026-08-13T12:00:00+09:00",
        )
        self.assertIsNone(delay)

    def test_extra_thinking_when_wake_disabled(self) -> None:
        delay = extra_background_thinking_delay_seconds(
            wake_mode="disabled",
            wake_interval_seconds=300,
            last_wake_at=None,
            due_concerns=[{"min_interval_seconds": 3600}],
            current_time="2026-08-13T12:00:00+09:00",
        )
        self.assertEqual(delay, 0.0)

    def test_extra_thinking_respects_cadence_after_recent_thinking(self) -> None:
        delay = extra_background_thinking_delay_seconds(
            wake_mode="disabled",
            wake_interval_seconds=300,
            last_wake_at="2026-08-13T11:58:00+09:00",
            due_concerns=[{"min_interval_seconds": 3600}],
            current_time="2026-08-13T12:00:00+09:00",
        )
        self.assertEqual(delay, 180.0)

    def test_extra_thinking_when_regular_wake_is_far(self) -> None:
        delay = extra_background_thinking_delay_seconds(
            wake_mode="interval",
            wake_interval_seconds=86400,
            last_wake_at="2026-08-13T00:00:00+09:00",
            due_concerns=[{"min_interval_seconds": 3600}],
            current_time="2026-08-13T12:00:00+09:00",
        )
        self.assertEqual(delay, 0.0)

    def test_attendance_requires_action_and_factor_selection(self) -> None:
        workspace = {
            "workspace_candidates": [
                {
                    "factor_ref": "standing_concern:elyth",
                    "kind": "standing_concern",
                    "metadata": {"concern_id": "elyth"},
                }
            ]
        }
        self.assertEqual(
            selected_standing_concern_ids(
                decision={
                    "kind": "autonomous_run",
                    "foreground_selection": {"primary_factor_ref": "standing_concern:elyth"},
                },
                workspace_context=workspace,
            ),
            ["elyth"],
        )
        self.assertEqual(
            selected_standing_concern_ids(
                decision={
                    "kind": "noop",
                    "foreground_selection": {"primary_factor_ref": "standing_concern:elyth"},
                },
                workspace_context=workspace,
            ),
            [],
        )
        self.assertEqual(
            selected_standing_concern_ids(
                decision={
                    "kind": "capability_request",
                    "foreground_selection": {"primary_factor_ref": "drive_state:x"},
                },
                workspace_context=workspace,
            ),
            [],
        )
        self.assertEqual(
            selected_standing_concern_ids(
                decision={
                    "kind": "speech",
                    "foreground_selection": {
                        "primary_factor_ref": "visual_observation_signal:desktop",
                    },
                    "separated_comparisons": {
                        "self_activity": {
                            "kind": "capability_request",
                            "foreground_selection": {
                                "primary_factor_ref": "standing_concern:elyth",
                                "supporting_factor_refs": [],
                            },
                        }
                    },
                },
                workspace_context=workspace,
            ),
            ["elyth"],
        )


class StandingConcernWorkspaceTests(unittest.TestCase):
    def test_workspace_includes_due_standing_concern(self) -> None:
        service = ServiceInputPipelineMixin()
        payload = service._build_workspace_context(
            current_input=CurrentInput(
                sender_kind="system",
                sender_ref=None,
                source_kind="background_thinking",
                response_target_refs=(),
                interaction_context=None,
                text="定期思考。",
            ),
            due_standing_concerns=[
                {
                    "concern_id": "elyth",
                    "enabled": True,
                    "min_interval_seconds": 3600,
                    "concern_summary": "ELYTHの場。届いている反応やリプライがあるかは気にかける。",
                }
            ],
            recall_pack={},
            drive_state_summary=None,
            foreground_world_state=None,
            activity_context=None,
            ongoing_action_summary=None,
            autonomous_run_summaries=None,
            capability_decision_view=None,
            initiative_context=None,
            capability_result_context=None,
            visual_observation_context=None,
            self_state_context=None,
            relationship_context=None,
            prediction_error_context=None,
            default_mode_context=None,
        )
        kinds = {candidate["kind"] for candidate in payload["workspace_candidates"]}
        self.assertIn("standing_concern", kinds)
        concern = next(
            candidate
            for candidate in payload["workspace_candidates"]
            if candidate["kind"] == "standing_concern"
        )
        self.assertEqual(concern["factor_ref"], "standing_concern:elyth")
        self.assertNotIn("確認せよ", concern["summary_text"])
        self.assertNotIn("定時", concern["summary_text"])


class StandingConcernStoreTests(unittest.TestCase):
    def test_new_store_has_default_standing_concerns(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileStore(Path(temp_dir))
            state = store.read_state()
            self.assertEqual(state["standing_concerns"], build_default_standing_concerns())

    def test_schema_version_eighteen_migrates_standing_concerns(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root_dir = Path(temp_dir)
            db_path = root_dir / "config.db"
            with sqlite3.connect(db_path) as conn:
                conn.executescript(
                    """
                    CREATE TABLE server_identity (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        server_id TEXT NOT NULL,
                        server_display_name TEXT NOT NULL,
                        api_version TEXT NOT NULL,
                        console_access_token TEXT
                    );
                    CREATE TABLE current_config (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        selected_persona_id TEXT NOT NULL,
                        selected_memory_set_id TEXT NOT NULL,
                        selected_model_preset_id TEXT NOT NULL,
                        pre_send_check_model_preset_id TEXT NOT NULL,
                        selected_avatar_id TEXT NOT NULL,
                        thinking_speech_level INTEGER NOT NULL DEFAULT 5,
                        selected_conversation_display_name_id TEXT,
                        wake_policy_json TEXT NOT NULL,
                        audio_output_settings_json TEXT NOT NULL,
                        microphone_settings_json TEXT NOT NULL
                    );
                    INSERT INTO server_identity VALUES (
                        1, 'server:test', 'OtomeKairo', '0.10.0', NULL
                    );
                    INSERT INTO current_config VALUES (
                        1,
                        'persona:default',
                        'memory_set:default',
                        'model_preset:default',
                        'model_preset:pre_send_check',
                        'avatar:default',
                        5,
                        NULL,
                        '{"mode":"disabled","interval_seconds":300}',
                        '{"destination":"otomekairo","local_output_device":null}',
                        '{"input_source":"local_microphone","local_input_device":null,"console":null,"vad_probability_threshold":0.5,"speaker_recognition_threshold":0.6}'
                    );
                    PRAGMA user_version = 18;
                    """
                )
            store = FileStore(root_dir)
            state = store.read_state()
            self.assertEqual(state["standing_concerns"], build_default_standing_concerns())
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 19)


class StandingConcernAttendanceMixinTests(unittest.TestCase):
    def test_mark_attended_updates_runtime_state(self) -> None:
        from otomekairo.service.input.standing_concern import ServiceInputStandingConcernMixin

        class Subject(ServiceInputStandingConcernMixin):
            def __init__(self) -> None:
                self._runtime_state_lock = threading.RLock()
                self._wake_runtime_state = {"standing_concern_last_attended_at": {}}

        subject = Subject()
        marked = subject._mark_standing_concerns_attended(
            decision={
                "kind": "capability_request",
                "foreground_selection": {
                    "primary_factor_ref": "standing_concern:elyth",
                    "supporting_factor_refs": [],
                },
            },
            workspace_context={
                "workspace_candidates": [
                    {
                        "factor_ref": "standing_concern:elyth",
                        "kind": "standing_concern",
                        "metadata": {"concern_id": "elyth"},
                    }
                ]
            },
            current_time="2026-08-13T12:00:00+09:00",
        )
        self.assertEqual(marked, ["elyth"])
        self.assertEqual(
            subject._standing_concern_last_attended_map()["elyth"],
            "2026-08-13T12:00:00+09:00",
        )


if __name__ == "__main__":
    unittest.main()
