from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from typing import Any, Callable

from .config import TemperatureWake


class TemperatureNotifier:
    """One active wake and at most one pending temperature event."""

    def __init__(self, config: TemperatureWake, send: Callable[[dict[str, Any]], None]):
        self.config = config
        self.send = send
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pico-temperature-wake")
        self.future: Future | None = None
        self.pending: dict[str, Any] | None = None
        self.last_started: float | None = None
        self.rapid_direction: int | None = None

    def observe(self, report: dict[str, Any] | None, *, now: float) -> None:
        if self.future is not None and self.future.done():
            self.future.result()
            self.future = None
        if report is None or not self.config.enabled:
            self.pending = None
            self.rapid_direction = None
            return
        sample = next(item for item in report["samples"] if item["channel_id"] == "temperature")
        direction = None
        if sample["status"] == "succeeded" and sample["payload"]["change"]["pattern"] == "rapid_change":
            direction = 1 if sample["payload"]["change"]["delta"] > 0 else -1
            if direction != self.rapid_direction:
                self.pending = {"client_id": report["client_id"], "sensor_source_id": report["sensor_source_id"],
                                "sample": deepcopy(sample)}
        self.rapid_direction = direction
        if (self.pending is not None and self.future is None
                and (self.last_started is None or now - self.last_started >= self.config.min_interval_seconds)):
            event = self.pending
            self.pending = None
            self.last_started = now
            self.future = self.executor.submit(self.send, event)

    def close(self) -> None:
        self.executor.shutdown(wait=True, cancel_futures=True)
        if self.future is not None:
            self.future.result()
