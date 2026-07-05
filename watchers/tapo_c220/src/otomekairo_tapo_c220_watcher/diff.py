from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DiffResult:
    changed_ratio: float
    changed: bool


class FrameDiffer:
    def __init__(
        self,
        *,
        resize_width: int,
        pixel_diff_threshold: int,
        motion_ratio_threshold: float,
    ) -> None:
        self.resize_width = resize_width
        self.pixel_diff_threshold = pixel_diff_threshold
        self.motion_ratio_threshold = motion_ratio_threshold
        self.previous_frame: object | None = None

    def compare(self, frame: object) -> DiffResult:
        prepared = self._prepare(frame)
        if self.previous_frame is None:
            self.previous_frame = prepared
            return DiffResult(changed_ratio=0.0, changed=False)

        import cv2  # type: ignore[import-not-found]

        delta = cv2.absdiff(self.previous_frame, prepared)
        changed_pixels = cv2.countNonZero(cv2.threshold(delta, self.pixel_diff_threshold, 255, cv2.THRESH_BINARY)[1])
        total_pixels = int(delta.shape[0] * delta.shape[1])
        changed_ratio = float(changed_pixels) / float(total_pixels) if total_pixels > 0 else 0.0
        self.previous_frame = prepared
        return DiffResult(
            changed_ratio=changed_ratio,
            changed=changed_ratio >= self.motion_ratio_threshold,
        )

    def save_snapshot(self, *, frame: object, path: Path, jpeg_quality: int) -> None:
        import cv2  # type: ignore[import-not-found]

        path.parent.mkdir(parents=True, exist_ok=True)
        ok = cv2.imwrite(str(path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality])
        if not ok:
            raise RuntimeError("snapshot_write_failed")

    def _prepare(self, frame: object) -> object:
        import cv2  # type: ignore[import-not-found]

        height, width = frame.shape[:2]
        if width <= 0 or height <= 0:
            raise RuntimeError("invalid_frame_shape")
        target_width = self.resize_width
        target_height = max(1, int(height * (target_width / width)))
        resized = cv2.resize(frame, (target_width, target_height), interpolation=cv2.INTER_AREA)
        return cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
