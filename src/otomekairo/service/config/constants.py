from __future__ import annotations


EVENT_STREAM_CAPABILITY_PERMISSIONS = (
    "observe_vision",
    "observe_desktop",
    "observe_camera",
    "control_camera_ptz",
    "use_mcp_tools",
)
# capability runtime と inspection が共有する機械判定値。
CAPABILITY_UNAVAILABLE_REASONS = frozenset(
    {
        "no_binding",
        "permission_denied",
        "paused",
        "busy",
        "unavailable",
        "dispatch_failed",
        "request_timeout",
        "parallel_blocked",
        "camera_source_disabled",
        "no_vision_source",
        "no_supported_control",
        "no_mcp_tool",
    }
)
PERSONA_INITIATIVE_BASELINES = {"low", "medium", "high"}
VISION_SOURCE_KINDS = {"desktop", "camera", "virtual"}
CAMERA_CONNECTOR_KINDS = {"tapo_c220"}
CAMERA_DEFAULT_CONNECTOR_KIND = "tapo_c220"
CAMERA_DEFAULT_CLIENT_ID = "tapo-c220-connector-main"
CAMERA_PTZ_OPERATIONS = ("move_up", "move_down", "move_left", "move_right")
MCP_CONNECTOR_KINDS = {"mcp_client"}
MCP_DEFAULT_CONNECTOR_KIND = "mcp_client"
MCP_DEFAULT_CLIENT_ID = "mcp-client-connector-main"
MCP_TRANSPORTS = {"stdio", "streamable_http"}
