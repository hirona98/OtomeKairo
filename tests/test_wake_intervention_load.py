from __future__ import annotations

import threading
import unittest
from datetime import datetime

from otomekairo.service.spontaneous.wake import ServiceSpontaneousWakeMixin


class DummyWakeService(ServiceSpontaneousWakeMixin):
    def __init__(self) -> None:
        self._runtime_state_lock = threading.RLock()
        self._wake_runtime_state = {
            "last_wake_at": None,
            "last_spontaneous_at": None,
            "initial_delay_until": None,
            "retry_after": None,
            "speech_history_by_dedupe": {},
        }

    def _parse_iso(self, value: str) -> datetime:
        return datetime.fromisoformat(value)


class WakeInterventionLoadTests(unittest.TestCase):
    def test_background_wake_is_suppressed_after_recent_spontaneous_speech(self) -> None:
        service = DummyWakeService()
        service._wake_runtime_state["last_spontaneous_at"] = "2026-06-21T15:52:00+09:00"
        state = {"wake_policy": {"mode": "interval", "interval_seconds": 60}}

        due = service._wake_is_due(
            state=state,
            current_time="2026-06-21T15:53:00+09:00",
            trigger_kind="background_wake",
        )

        self.assertTrue(due["should_skip"])
        self.assertTrue(due["consume_interval"])
        self.assertIn("介入負荷", due["reason_summary"])

    def test_regular_wake_is_not_suppressed_by_recent_spontaneous_speech(self) -> None:
        service = DummyWakeService()
        service._wake_runtime_state["last_spontaneous_at"] = "2026-06-21T15:52:00+09:00"
        state = {"wake_policy": {"mode": "interval", "interval_seconds": 60}}

        due = service._wake_is_due(
            state=state,
            current_time="2026-06-21T15:53:00+09:00",
            trigger_kind="wake",
        )

        self.assertFalse(due["should_skip"])


if __name__ == "__main__":
    unittest.main()
