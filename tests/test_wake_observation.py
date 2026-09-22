from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from otomekairo.event_stream import EventStreamRegistry
from otomekairo.service.app import OtomeKairoService
from otomekairo.service.common import BACKGROUND_THINKING_POLL_SECONDS


NOW = "2026-08-14T12:00:00+09:00"


class _ClosedSocket:
    def close(self) -> None:
        return


def _desktop_observation(vision_source_id: str = "vision_source:console-x:desktop") -> dict:
    return {
        "observation_id": "wake_observation:desktop",
        "enabled": True,
        "capability_id": "vision.capture",
        "input": {"vision_source_id": vision_source_id, "mode": "still"},
    }


def _interval_state(**overrides) -> dict:
    state = {
        "wake_policy": {
            "mode": "interval",
            "interval_seconds": 300,
            "observations": [_desktop_observation()],
        },
        "camera_sources": {},
        "periodic_thought_topics": [],
    }
    state.update(overrides)
    return state


class SeenVisionSourceRegistryTests(unittest.TestCase):
    def test_hello_remembers_source_after_disconnect(self) -> None:
        registry = EventStreamRegistry()
        session_id = registry.add_connection(_ClosedSocket())
        registry.register_hello(
            session_id,
            client_id="console-1",
            client_kind="cocoro_console",
            capabilities={"vision.capture": "1"},
            rejected_bindings=[],
            vision_sources=[
                {
                    "vision_source_id": "vision_source:console-1:desktop",
                    "kind": "desktop",
                    "default_for": ["desktop"],
                }
            ],
        )
        registry.remove_connection(session_id)

        self.assertTrue(registry.has_seen_vision_source("vision_source:console-1:desktop"))
        self.assertTrue(registry.has_seen_vision_source_kind("desktop"))
        self.assertIsNone(registry.get_vision_source("vision_source:console-1:desktop"))


class WakeObservationSourceReadyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        with patch.object(OtomeKairoService, "_now_iso", return_value="2026-08-14T11:55:00+09:00"):
            self.service = OtomeKairoService(root_dir=Path(self.temp_dir.name))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _register_source(self, *, vision_source_id: str, kind: str, client_id: str) -> str:
        session_id = self.service._event_stream_registry.add_connection(_ClosedSocket())
        self.service._event_stream_registry.register_hello(
            session_id,
            client_id=client_id,
            client_kind="capability_connector" if kind == "camera" else "cocoro_console",
            capabilities={"vision.capture": "1"},
            rejected_bindings=[],
            vision_sources=[
                {
                    "vision_source_id": vision_source_id,
                    "kind": kind,
                    "default_for": [kind],
                }
            ],
        )
        return session_id

    def test_no_observations_first_thinking_is_due_after_interval(self) -> None:
        state = _interval_state(wake_policy={"mode": "interval", "interval_seconds": 300})
        due = self.service._wake_is_due(state=state, current_time=NOW)

        self.assertFalse(due["should_skip"])
        self.assertEqual(
            self.service._background_thinking_delay_seconds(state=state, current_time=NOW),
            0.0,
        )

    def test_first_thinking_waits_full_interval(self) -> None:
        state = _interval_state(wake_policy={"mode": "interval", "interval_seconds": 300})
        for current_time in ("2026-08-14T11:55:00+09:00", "2026-08-14T11:59:59+09:00"):
            with self.subTest(current_time=current_time):
                self.assertTrue(self.service._wake_is_due(state=state, current_time=current_time)["should_skip"])
                self.assertGreater(
                    self.service._background_thinking_delay_seconds(state=state, current_time=current_time),
                    0.0,
                )
        self.assertIsNone(self.service._wake_runtime_state["last_wake_at"])

    def test_due_topic_does_not_shorten_periodic_thinking(self) -> None:
        state = _interval_state(
            wake_policy={"mode": "interval", "interval_seconds": 3600, "observations": []},
            periodic_thought_topics=[{
                "topic_id": "elyth",
                "enabled": True,
                "min_periodic_thinking_interval_seconds": 300,
                "topic_summary": "ELYTH",
            }],
        )
        self.assertTrue(self.service._due_periodic_thought_topics(state=state, current_time=NOW))
        self.assertTrue(self.service._wake_is_due(state=state, current_time=NOW)["should_skip"])
        self.assertEqual(
            self.service._background_thinking_delay_seconds(state=state, current_time=NOW),
            BACKGROUND_THINKING_POLL_SECONDS,
        )
        started: list[dict] = []
        self.service._execute_wake_cycle = lambda **kwargs: started.append(kwargs)
        with patch.object(self.service, "_now_iso", return_value=NOW):
            self.service._execute_scheduled_background_thinking(state=state)
        self.assertEqual(started, [])

    def test_enabling_periodic_thinking_starts_full_interval(self) -> None:
        state = _interval_state(wake_policy={"mode": "interval", "interval_seconds": 300})
        self.service._set_last_wake_at("2026-08-14T10:00:00+09:00")
        self.service._sync_wake_policy_runtime_state(
            previous_wake_policy={"mode": "disabled"},
            next_wake_policy=state["wake_policy"],
            current_time=NOW,
        )
        self.assertTrue(self.service._wake_is_due(state=state, current_time="2026-08-14T12:04:59+09:00")["should_skip"])
        self.assertFalse(self.service._wake_is_due(state=state, current_time="2026-08-14T12:05:00+09:00")["should_skip"])
        self.assertEqual(self.service._wake_runtime_state["last_wake_at"], "2026-08-14T10:00:00+09:00")

    def test_enabling_observation_preserves_interval(self) -> None:
        state = _interval_state()
        self._register_source(vision_source_id="vision_source:console-x:desktop", kind="desktop", client_id="console-x")
        self.service._sync_wake_policy_runtime_state(
            previous_wake_policy={"mode": "interval", "interval_seconds": 300},
            next_wake_policy=state["wake_policy"],
            current_time="2026-08-14T11:56:00+09:00",
        )
        self.assertTrue(self.service._wake_is_due(state=state, current_time="2026-08-14T11:56:05+09:00")["should_skip"])
        self.assertFalse(self.service._wake_is_due(state=state, current_time=NOW)["should_skip"])

    def test_consuming_interval_and_reset_start_new_interval(self) -> None:
        state = _interval_state(wake_policy={"mode": "interval", "interval_seconds": 300})
        self.service._set_last_wake_at(NOW)
        self.assertEqual(self.service._wake_runtime_state["interval_started_at"], NOW)
        self.assertTrue(self.service._wake_is_due(state=state, current_time=NOW)["should_skip"])
        with patch.object(self.service, "_now_iso", return_value="2026-08-14T12:10:00+09:00"):
            self.service._clear_pending_intent_candidates()
        self.assertTrue(self.service._wake_is_due(state=state, current_time="2026-08-14T12:14:59+09:00")["should_skip"])
        self.assertFalse(self.service._wake_is_due(state=state, current_time="2026-08-14T12:15:00+09:00")["should_skip"])

    def test_enabled_observation_without_hello_is_not_due(self) -> None:
        state = _interval_state()
        due = self.service._wake_is_due(state=state, current_time=NOW)

        self.assertTrue(due["should_skip"])
        self.assertIn("一度も登録されていない", due["reason_summary"] or "")
        self.assertEqual(
            self.service._background_thinking_delay_seconds(state=state, current_time=NOW),
            BACKGROUND_THINKING_POLL_SECONDS,
        )
        self.assertEqual(
            self.service._unseen_wake_observation_sources(state),
            ["vision_source:console-x:desktop"],
        )

    def test_hello_desktop_makes_stale_desktop_id_due(self) -> None:
        state = _interval_state()
        self._register_source(
            vision_source_id="vision_source:console-live:desktop",
            kind="desktop",
            client_id="console-live",
        )

        due = self.service._wake_is_due(state=state, current_time=NOW)
        self.assertFalse(due["should_skip"])
        self.assertEqual(self.service._unseen_wake_observation_sources(state), [])

    def test_hello_camera_makes_dynamic_camera_observation_due(self) -> None:
        state = _interval_state(
            wake_policy={"mode": "interval", "interval_seconds": 300, "observations": []},
            camera_sources={
                "vision_source:対面カメラ": {
                    "vision_source_id": "vision_source:対面カメラ",
                    "enabled": True,
                }
            },
        )

        self.assertEqual(
            self.service._unseen_wake_observation_sources(state),
            ["vision_source:対面カメラ"],
        )
        self._register_source(
            vision_source_id="vision_source:対面カメラ",
            kind="camera",
            client_id="tapo-c220-connector-main",
        )
        self.assertEqual(self.service._unseen_wake_observation_sources(state), [])
        due = self.service._wake_is_due(state=state, current_time=NOW)
        self.assertFalse(due["should_skip"])

    def test_seen_then_disconnect_is_due_and_observation_unresolvable(self) -> None:
        state = _interval_state()
        session_id = self._register_source(
            vision_source_id="vision_source:console-x:desktop",
            kind="desktop",
            client_id="console-x",
        )
        self.service._event_stream_registry.remove_connection(session_id)

        due = self.service._wake_is_due(state=state, current_time=NOW)
        self.assertFalse(due["should_skip"])
        self.assertIsNone(
            self.service._resolve_wake_policy_observation_input(
                capability_id="vision.capture",
                input_payload={"vision_source_id": "vision_source:console-x:desktop", "mode": "still"},
            )
        )

    def test_partial_hello_still_waits(self) -> None:
        state = _interval_state(
            camera_sources={
                "vision_source:対面カメラ": {
                    "vision_source_id": "vision_source:対面カメラ",
                    "enabled": True,
                }
            }
        )
        self._register_source(
            vision_source_id="vision_source:console-live:desktop",
            kind="desktop",
            client_id="console-live",
        )

        self.assertEqual(
            self.service._unseen_wake_observation_sources(state),
            ["vision_source:対面カメラ"],
        )
        due = self.service._wake_is_due(state=state, current_time=NOW)
        self.assertTrue(due["should_skip"])

    def test_periodic_thought_topic_due_does_not_start_cycle_while_unseen(self) -> None:
        state = _interval_state(
            periodic_thought_topics=[
                {
                    "topic_id": "elyth",
                    "enabled": True,
                    "min_periodic_thinking_interval_seconds": 1,
                    "topic_summary": "ELYTH",
                }
            ]
        )
        started: list[dict] = []
        self.service._execute_wake_cycle = lambda **kwargs: started.append(kwargs)

        self.assertFalse(
            self.service._background_thinking_should_proceed(
                state=state,
                current_time=NOW,
                client_context={},
            )
        )
        self.service._execute_scheduled_background_thinking(state=state)
        self.assertEqual(started, [])
        self.assertEqual(
            self.service._background_thinking_delay_seconds(state=state, current_time=NOW),
            BACKGROUND_THINKING_POLL_SECONDS,
        )

    def test_inspection_lists_waiting_source_ids(self) -> None:
        state = _interval_state()
        snapshot = self.service._snapshot_wake_runtime_state(state=state, current_time=NOW)
        self.assertEqual(
            snapshot["waiting_for_vision_source_ids"],
            ["vision_source:console-x:desktop"],
        )

        self._register_source(
            vision_source_id="vision_source:console-live:desktop",
            kind="desktop",
            client_id="console-live",
        )
        snapshot = self.service._snapshot_wake_runtime_state(state=state, current_time=NOW)
        self.assertNotIn("waiting_for_vision_source_ids", snapshot)

    def test_hello_nudges_scheduler(self) -> None:
        session_id = self.service.register_event_stream_connection(_ClosedSocket())
        self.service._background_thinking_nudge.clear()
        self.service.handle_event_stream_message(
            session_id,
            {
                "type": "hello",
                "client_id": "console-nudge",
                "client_kind": "cocoro_console",
                "caps": [{"id": "vision.capture", "version": "1"}],
                "vision_sources": [
                    {
                        "vision_source_id": "vision_source:console-nudge:desktop",
                        "capability_id": "vision.capture",
                        "kind": "desktop",
                        "label": "desktop",
                        "aliases": ["desktop"],
                        "default_for": ["desktop"],
                        "required_permissions": ["observe_vision"],
                    }
                ],
            },
        )
        self.assertTrue(self.service._background_thinking_nudge.is_set())
