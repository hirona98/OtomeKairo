from __future__ import annotations

import threading
import time
import unittest

from otomekairo.service.cycle_coordinator import CycleCoordinator


class CycleCoordinatorTests(unittest.TestCase):
    def test_background_cycle_is_skipped_while_foreground_is_active_or_waiting(self) -> None:
        coordinator = CycleCoordinator()
        second_entered = threading.Event()
        release_second = threading.Event()

        coordinator.enter_foreground()

        def second_foreground() -> None:
            coordinator.enter_foreground()
            second_entered.set()
            release_second.wait(timeout=1)
            coordinator.leave_foreground()

        thread = threading.Thread(target=second_foreground)
        thread.start()
        deadline = time.monotonic() + 1
        while coordinator.snapshot()["foreground_waiters"] != 1 and time.monotonic() < deadline:
            time.sleep(0.005)

        self.assertEqual(coordinator.snapshot()["foreground_waiters"], 1)
        self.assertFalse(coordinator.try_enter_background())

        coordinator.leave_foreground()
        self.assertTrue(second_entered.wait(timeout=1))
        self.assertFalse(coordinator.try_enter_background())

        release_second.set()
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        self.assertTrue(coordinator.try_enter_background())
        coordinator.leave_background()


if __name__ == "__main__":
    unittest.main()
