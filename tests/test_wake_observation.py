from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

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
                    "capability_id": "vision.capture",
                    "kind": kind,
                    "default_for": [kind],
                }
            ],
        )
        return session_id

    def _run_periodic_observations(self, state: dict) -> dict:
        return self.service._run_wake_policy_observations(
            state=state,
            started_at=NOW,
            client_context={"source": "background_thinking_scheduler"},
            cycle_id=None,
            for_background_thinking=True,
        )

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

    def test_enabled_observation_without_hello_is_due_and_not_recorded(self) -> None:
        state = _interval_state()
        due = self.service._wake_is_due(state=state, current_time=NOW)

        self.assertFalse(due["should_skip"])
        self.assertEqual(
            self.service._background_thinking_delay_seconds(state=state, current_time=NOW),
            0.0,
        )
        observe = Mock()
        with patch.object(self.service, "_run_wake_policy_observation", observe):
            context = self._run_periodic_observations(state)
        observe.assert_not_called()
        self.assertEqual(context, {"source": "background_thinking_scheduler"})
        self.assertEqual(self.service._wake_observation_runtime_state, {})
        self.assertFalse(self.service._client_context_has_retryable_wake_observation_failure(context))
        self.service._consume_background_thinking_interval(trigger_kind="background_thinking", current_time=NOW)
        self.assertEqual(self.service._wake_runtime_state["last_wake_at"], NOW)
        self.assertIsNone(self.service._wake_runtime_state["retry_after"])

    def test_hello_desktop_makes_stale_desktop_id_due(self) -> None:
        state = _interval_state()
        self._register_source(
            vision_source_id="vision_source:console-live:desktop",
            kind="desktop",
            client_id="console-live",
        )

        due = self.service._wake_is_due(state=state, current_time=NOW)
        self.assertFalse(due["should_skip"])
        self.assertFalse(self.service._wake_observation_source_is_disconnected(_desktop_observation()))

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

        observation = self.service._enabled_wake_policy_observations(state)[0]
        self.assertTrue(self.service._wake_observation_source_is_disconnected(observation))
        self._register_source(
            vision_source_id="vision_source:対面カメラ",
            kind="camera",
            client_id="tapo-c220-connector-main",
        )
        self.assertFalse(self.service._wake_observation_source_is_disconnected(observation))
        due = self.service._wake_is_due(state=state, current_time=NOW)
        self.assertFalse(due["should_skip"])

    def test_seen_then_disconnect_is_due_and_not_recorded(self) -> None:
        state = _interval_state()
        session_id = self._register_source(
            vision_source_id="vision_source:console-x:desktop",
            kind="desktop",
            client_id="console-x",
        )
        self.service._event_stream_registry.remove_connection(session_id)

        due = self.service._wake_is_due(state=state, current_time=NOW)
        self.assertFalse(due["should_skip"])
        observe = Mock()
        with patch.object(self.service, "_run_wake_policy_observation", observe):
            context = self._run_periodic_observations(state)
        observe.assert_not_called()
        self.assertEqual(context, {"source": "background_thinking_scheduler"})
        self.assertEqual(self.service._wake_observation_runtime_state, {})

    def test_camera_without_hello_is_not_recorded(self) -> None:
        state = _interval_state(
            wake_policy={"mode": "interval", "interval_seconds": 300, "observations": []},
            camera_sources={
                "vision_source:対面カメラ": {
                    "vision_source_id": "vision_source:対面カメラ",
                    "enabled": True,
                }
            },
        )
        observe = Mock()
        with patch.object(self.service, "_run_wake_policy_observation", observe):
            context = self._run_periodic_observations(state)
        observe.assert_not_called()
        self.assertEqual(context, {"source": "background_thinking_scheduler"})
        self.assertFalse(self.service._wake_is_due(state=state, current_time=NOW)["should_skip"])

    def test_connected_camera_is_observed_when_desktop_is_disconnected(self) -> None:
        state = _interval_state(
            camera_sources={
                "vision_source:対面カメラ": {
                    "vision_source_id": "vision_source:対面カメラ",
                    "enabled": True,
                }
            }
        )
        self._register_source(
            vision_source_id="vision_source:対面カメラ",
            kind="camera",
            client_id="camera-connector",
        )
        observed: list[str] = []

        def observe(**kwargs):
            observed.append(kwargs["observation"]["input"]["vision_source_id"])
            return {
                "observation_id": kwargs["observation"]["observation_id"],
                "status": "succeeded",
                "visual_summary_text": "カメラを観測した。",
            }

        with patch.object(self.service, "_run_wake_policy_observation", side_effect=observe):
            context = self._run_periodic_observations(state)
        self.assertEqual(observed, ["vision_source:対面カメラ"])
        self.assertEqual(len(context["wake_observations"]), 1)
        self.assertEqual(len(context["wake_observation_trace"]["wake_observations"]), 1)

    def test_all_connected_sources_are_observed_in_order(self) -> None:
        state = _interval_state(
            camera_sources={
                "vision_source:対面カメラ": {
                    "vision_source_id": "vision_source:対面カメラ",
                    "enabled": True,
                }
            }
        )
        self._register_source(
            vision_source_id="vision_source:console-x:desktop",
            kind="desktop",
            client_id="console-x",
        )
        self._register_source(
            vision_source_id="vision_source:対面カメラ",
            kind="camera",
            client_id="camera-connector",
        )
        observed: list[str] = []

        def observe(**kwargs):
            observed.append(kwargs["observation"]["input"]["vision_source_id"])
            return {
                "observation_id": kwargs["observation"]["observation_id"],
                "status": "succeeded",
                "visual_summary_text": "観測した。",
            }

        with patch.object(self.service, "_run_wake_policy_observation", side_effect=observe):
            context = self._run_periodic_observations(state)
        self.assertEqual(observed, ["vision_source:console-x:desktop", "vision_source:対面カメラ"])
        self.assertEqual(len(context["wake_observations"]), 2)

    def test_disconnection_before_result_recording_is_omitted(self) -> None:
        state = _interval_state()
        session_id = self._register_source(
            vision_source_id="vision_source:console-x:desktop",
            kind="desktop",
            client_id="console-x",
        )

        def observe(**kwargs):
            self.service._event_stream_registry.remove_connection(session_id)
            return {
                "observation_id": kwargs["observation"]["observation_id"],
                "status": "failed",
                "failure_code": "source_unavailable",
                "reason_summary": "接続が切れた。",
            }

        with patch.object(self.service, "_run_wake_policy_observation", side_effect=observe):
            context = self._run_periodic_observations(state)
        self.assertEqual(context, {"source": "background_thinking_scheduler"})
        self.assertEqual(self.service._wake_observation_runtime_state, {})

    def test_ambiguous_connected_source_remains_failure(self) -> None:
        state = _interval_state()
        self._register_source(
            vision_source_id="vision_source:console-a:desktop",
            kind="desktop",
            client_id="console-a",
        )
        self._register_source(
            vision_source_id="vision_source:console-b:desktop",
            kind="desktop",
            client_id="console-b",
        )
        context = self._run_periodic_observations(state)
        self.assertEqual(context["wake_observations"][0]["status"], "failed")
        self.assertEqual(context["wake_observations"][0]["failure_code"], "source_unavailable")
        self.assertEqual(len(context["wake_observation_trace"]["wake_observations"]), 1)

    def test_partial_hello_observes_only_connected_source(self) -> None:
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

        due = self.service._wake_is_due(state=state, current_time=NOW)
        self.assertFalse(due["should_skip"])
        observed: list[str] = []

        def observe(**kwargs):
            observed.append(kwargs["observation"]["input"]["vision_source_id"])
            return {
                "observation_id": kwargs["observation"]["observation_id"],
                "status": "succeeded",
                "visual_summary_text": "画面を観測した。",
            }

        with patch.object(self.service, "_run_wake_policy_observation", side_effect=observe):
            context = self._run_periodic_observations(state)
        self.assertEqual(observed, ["vision_source:console-x:desktop"])
        self.assertEqual(len(context["wake_observations"]), 1)
        self.assertEqual(len(context["wake_observation_trace"]["wake_observations"]), 1)

    def test_periodic_thought_topic_starts_cycle_without_vision_source(self) -> None:
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

        self.assertTrue(
            self.service._background_thinking_should_proceed(
                state=state,
                current_time=NOW,
                client_context={},
            )
        )
        with patch.object(self.service, "_now_iso", return_value=NOW):
            self.service._execute_scheduled_background_thinking(state=state)
        self.assertEqual(len(started), 1)
        self.assertEqual(
            self.service._background_thinking_delay_seconds(state=state, current_time=NOW),
            0.0,
        )

    def test_inspection_has_no_waiting_source_ids(self) -> None:
        state = _interval_state()
        snapshot = self.service._snapshot_wake_runtime_state(state=state, current_time=NOW)
        self.assertNotIn("waiting_for_vision_source_ids", snapshot)

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
