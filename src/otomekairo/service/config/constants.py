from __future__ import annotations


EVENT_STREAM_CAPABILITY_PERMISSIONS = (
    "observe_vision",
    "observe_desktop",
    "observe_camera",
    "control_camera_ptz",
    "use_mcp_tools",
)
PERSONA_INITIATIVE_BASELINES = {"low", "medium", "high"}
VISION_SOURCE_KINDS = {"desktop", "camera", "virtual"}
CAMERA_CONNECTOR_KINDS = {"tapo_c220"}
CAMERA_DEFAULT_CONNECTOR_KIND = "tapo_c220"
CAMERA_DEFAULT_CLIENT_ID = "tapo-c220-connector-main"
CAMERA_PTZ_OPERATIONS = ("move_up", "move_down", "move_left", "move_right")
