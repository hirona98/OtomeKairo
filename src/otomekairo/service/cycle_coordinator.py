from __future__ import annotations

import threading


class CycleCoordinator:
    """会話系サイクルをFIFOで直列化し、待機中は背景サイクルを開始させない。"""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._next_ticket = 0
        self._serving_ticket = 0
        self._foreground_waiters = 0
        self._active = False

    def enter_foreground(self) -> None:
        # ticket順に1サイクルだけ実行する。
        with self._condition:
            ticket = self._next_ticket
            self._next_ticket += 1
            self._foreground_waiters += 1
            while self._active or ticket != self._serving_ticket:
                self._condition.wait()
            self._foreground_waiters -= 1
            self._active = True

    def leave_foreground(self) -> None:
        with self._condition:
            self._active = False
            self._serving_ticket += 1
            self._condition.notify_all()

    def try_enter_background(self) -> bool:
        # 会話が実行中または待機中なら、その周期の背景処理を見送る。
        with self._condition:
            if self._active or self._foreground_waiters > 0:
                return False
            self._active = True
            return True

    def leave_background(self) -> None:
        with self._condition:
            self._active = False
            self._condition.notify_all()

    def snapshot(self) -> dict[str, int | bool]:
        with self._condition:
            return {
                "active": self._active,
                "foreground_waiters": self._foreground_waiters,
            }
