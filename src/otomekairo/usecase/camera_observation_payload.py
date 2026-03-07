"""Build canonical camera observation payloads."""

from __future__ import annotations

from typing import Any


# Block: Camera observation payload builder
def build_camera_observation_payload(
    *,
    capture_id: str,
    image_path: str,
    image_url: str,
    captured_at: int,
    trigger_reason: str,
) -> dict[str, Any]:
    if not isinstance(capture_id, str) or not capture_id:
        raise RuntimeError("capture_id must be non-empty string")
    if not isinstance(image_path, str) or not image_path:
        raise RuntimeError("image_path must be non-empty string")
    if not isinstance(image_url, str) or not image_url:
        raise RuntimeError("image_url must be non-empty string")
    if isinstance(captured_at, bool) or not isinstance(captured_at, int):
        raise RuntimeError("captured_at must be integer")
    if not isinstance(trigger_reason, str) or not trigger_reason:
        raise RuntimeError("trigger_reason must be non-empty string")
    return {
        "input_kind": "camera_observation",
        "trigger_reason": trigger_reason,
        "attachments": [
            {
                "attachment_kind": "camera_still_image",
                "media_kind": "image",
                "capture_id": capture_id,
                "mime_type": "image/jpeg",
                "storage_path": image_path,
                "content_url": image_url,
                "captured_at": captured_at,
            }
        ],
    }
