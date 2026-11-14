from __future__ import annotations

import math
from typing import Any

from .config import ChangeDetection


class ChangeTracker:
    """Classify numeric differences using only one previous reading per channel."""

    def __init__(self, config: ChangeDetection):
        self.config = config
        self.previous: dict[str, tuple[float | int, float]] = {}

    def reset(self) -> None:
        self.previous.clear()

    def annotate(self, samples: list[dict[str, Any]], *, now: float) -> None:
        for sample in samples:
            channel = sample["channel_id"]
            if sample["status"] != "succeeded":
                self.previous.pop(channel, None)
                continue
            value = sample["payload"]["value"]
            previous = self.previous.get(channel)
            change = {"pattern": "initial", "previous_value": None, "delta": None, "elapsed_seconds": None}
            if previous is not None:
                old_value, old_time = previous
                elapsed = now - old_time
                if 0 < elapsed <= self.config.max_gap_seconds:
                    delta = value - old_value
                    threshold = self.config.channels[channel]
                    magnitude = abs(delta)
                    steady = magnitude <= threshold.steady_delta or math.isclose(magnitude, threshold.steady_delta, abs_tol=1e-9)
                    rapid = magnitude >= threshold.rapid_delta or math.isclose(magnitude, threshold.rapid_delta, abs_tol=1e-9)
                    pattern = "steady" if steady else "rapid_change" if rapid else "changing"
                    change = {"pattern": pattern, "previous_value": old_value,
                              "delta": delta, "elapsed_seconds": elapsed}
            sample["payload"]["change"] = change
            self.previous[channel] = value, now
