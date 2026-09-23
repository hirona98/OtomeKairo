from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from otomekairo.service.app import OtomeKairoService


class BackgroundThinkingOnceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.service = OtomeKairoService(root_dir=Path(self.temp_dir.name))
        self.token = "test-token"
        state = self.service.store.read_state()
        state["console_access_token"] = self.token
        state["wake_policy"]["observations"] = []
        self.service.store.write_state(state)

    def test_manual_cycle_runs_with_saved_observations_even_without_schedule_due(self) -> None:
        for mode in ("disabled", "interval"):
            with self.subTest(mode=mode):
                state = self.service.store.read_state()
                state["wake_policy"]["mode"] = mode
                state["wake_policy"]["interval_seconds"] = 3600
                self.service.store.write_state(state)
                self.service._wake_runtime_state["retry_after"] = "2999-01-01T00:00:00+09:00"
                self.assertTrue(
                    self.service._wake_is_due(
                        state=state,
                        current_time=self.service._now_iso(),
                    )["should_skip"]
                )

                with (
                    patch.object(
                        self.service,
                        "_run_wake_policy_observations",
                        side_effect=lambda **kwargs: kwargs["client_context"],
                    ) as observations,
                    patch.object(
                        self.service,
                        "_run_autonomous_initiative_entry_check",
                        side_effect=lambda **kwargs: kwargs["client_context"],
                    ),
                    patch.object(self.service, "_has_autonomous_initiative_context", return_value=False),
                ):
                    result = self.service.trigger_background_thinking_once(self.token)

                self.assertEqual(result["result_kind"], "noop")
                observations.assert_called_once()
                self.assertTrue(observations.call_args.kwargs["for_background_thinking"])
                trace = self.service.store.get_cycle_trace(result["cycle_id"])
                self.assertEqual(trace["input_trace"]["current_input"]["source_kind"], "background_thinking")
                self.assertEqual(trace["input_trace"]["current_input"]["response_target_refs"], [])
                runtime = self.service._wake_runtime_state
                self.assertEqual(runtime["last_wake_at"], trace["cycle_summary"]["started_at"])
                self.assertEqual(runtime["interval_started_at"], runtime["last_wake_at"])
                self.assertIsNone(runtime["retry_after"])

    def test_manual_cycle_is_skipped_while_foreground_cycle_is_active(self) -> None:
        self.service._cycle_coordinator.enter_foreground()
        try:
            result = self.service.trigger_background_thinking_once(self.token)
        finally:
            self.service._cycle_coordinator.leave_foreground()

        self.assertEqual(result["result_kind"], "skipped")
        self.assertEqual(result["reason_code"], "foreground_cycle_active")
        self.assertIsNone(result["cycle_id"])
        self.assertIsNone(self.service._wake_runtime_state["last_wake_at"])

    def test_api_wake_keeps_background_thinking_interval(self) -> None:
        interval_started_at = self.service._wake_runtime_state["interval_started_at"]

        def run_wake(**kwargs):
            return (
                self.service._noop_pipeline(
                    state=kwargs["state"],
                    started_at=kwargs["started_at"],
                    reason_summary="判断を見送る。",
                ),
                "API 起床。",
                kwargs["client_context"],
            )

        with patch.object(self.service, "_run_wake_pipeline", side_effect=run_wake):
            result = self.service.trigger_wake(self.token, {})

        self.assertEqual(result["result_kind"], "noop")
        self.assertIsNone(self.service._wake_runtime_state["last_wake_at"])
        self.assertEqual(self.service._wake_runtime_state["interval_started_at"], interval_started_at)


if __name__ == "__main__":
    unittest.main()
