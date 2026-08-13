from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from otomekairo.defaults import build_default_disabled_mcp_inbound_observation
from otomekairo.service.standing_concerns import parse_standing_concern_timestamp


INBOUND_OBSERVATION_FIELDS = {
    "enabled",
    "interval_seconds",
    "tool_name",
    "arguments",
}


def inbound_observation_from_server(server: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(server, dict):
        return build_default_disabled_mcp_inbound_observation()
    observation = server.get("inbound_observation")
    if not isinstance(observation, dict):
        return build_default_disabled_mcp_inbound_observation()
    return observation


def inbound_observation_factor_ref(mcp_server_id: str) -> str:
    return f"inbound_observation:{mcp_server_id.strip()}"


def inbound_observation_is_due(
    *,
    enabled: bool,
    interval_seconds: int,
    last_attempt_at: Any,
    current_time: str,
) -> bool:
    if enabled is not True:
        return False
    current_dt = parse_standing_concern_timestamp(current_time)
    if current_dt is None:
        return False
    last_attempt_dt = parse_standing_concern_timestamp(last_attempt_at)
    if last_attempt_dt is None:
        return True
    return current_dt >= last_attempt_dt + timedelta(seconds=int(interval_seconds))


def inbound_observation_is_configured(server: dict[str, Any] | None) -> bool:
    if not isinstance(server, dict) or server.get("enabled") is not True:
        return False
    session = server.get("autonomous_session")
    observation = inbound_observation_from_server(server)
    if (
        not isinstance(session, dict)
        or session.get("enabled") is not True
        or session.get("background_enabled") is not True
    ):
        return False
    if observation.get("enabled") is not True:
        return False
    tool_name = observation.get("tool_name")
    return isinstance(tool_name, str) and bool(tool_name.strip())


def list_due_inbound_observation_servers(
    *,
    mcp_servers: dict[str, Any] | None,
    last_attempt_at_by_id: dict[str, str] | None,
    current_time: str,
) -> list[dict[str, Any]]:
    attended = last_attempt_at_by_id if isinstance(last_attempt_at_by_id, dict) else {}
    due: list[dict[str, Any]] = []
    servers = mcp_servers if isinstance(mcp_servers, dict) else {}
    for server in servers.values():
        if not inbound_observation_is_configured(server):
            continue
        observation = inbound_observation_from_server(server)
        mcp_server_id = str(server.get("mcp_server_id") or "").strip()
        if not mcp_server_id:
            continue
        if inbound_observation_is_due(
            enabled=True,
            interval_seconds=int(observation.get("interval_seconds") or 0),
            last_attempt_at=attended.get(mcp_server_id),
            current_time=current_time,
        ):
            due.append(server)
    return due


def list_configured_inbound_observation_servers(
    *,
    mcp_servers: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    servers = mcp_servers if isinstance(mcp_servers, dict) else {}
    return [
        server
        for server in servers.values()
        if inbound_observation_is_configured(server)
    ]


def inbound_observation_delay_seconds(
    *,
    mcp_servers: dict[str, Any] | None,
    last_attempt_at_by_id: dict[str, str] | None,
    current_time: str,
) -> float | None:
    configured = list_configured_inbound_observation_servers(mcp_servers=mcp_servers)
    if not configured:
        return None
    current_dt = parse_standing_concern_timestamp(current_time)
    if current_dt is None:
        return None
    attended = last_attempt_at_by_id if isinstance(last_attempt_at_by_id, dict) else {}
    remaining_values: list[float] = []
    for server in configured:
        mcp_server_id = str(server.get("mcp_server_id") or "").strip()
        last_attempt_dt = parse_standing_concern_timestamp(attended.get(mcp_server_id))
        if last_attempt_dt is None:
            return 0.0
        observation = inbound_observation_from_server(server)
        remaining = (
            last_attempt_dt
            + timedelta(seconds=int(observation.get("interval_seconds") or 0))
            - current_dt
        ).total_seconds()
        remaining_values.append(max(0.0, remaining))
    if not remaining_values:
        return None
    return min(remaining_values)


def inbound_present_mcp_server_ids(observations: Any) -> list[str]:
    if not isinstance(observations, list):
        return []
    present_ids: list[str] = []
    for observation in observations:
        if not isinstance(observation, dict) or observation.get("inbound_present") is not True:
            continue
        mcp_server_id = observation.get("mcp_server_id")
        if isinstance(mcp_server_id, str) and mcp_server_id.strip() and mcp_server_id.strip() not in present_ids:
            present_ids.append(mcp_server_id.strip())
    return present_ids


def inbound_present_mcp_server_ids_from_workspace(
    workspace_context: dict[str, Any] | None,
) -> list[str]:
    if not isinstance(workspace_context, dict):
        return []
    candidates = workspace_context.get("workspace_candidates")
    if not isinstance(candidates, list):
        return []
    present_ids: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, dict) or candidate.get("kind") != "inbound_observation":
            continue
        metadata = candidate.get("metadata")
        mcp_server_id = metadata.get("mcp_server_id") if isinstance(metadata, dict) else None
        if isinstance(mcp_server_id, str) and mcp_server_id.strip() and mcp_server_id.strip() not in present_ids:
            present_ids.append(mcp_server_id.strip())
    return present_ids


def parse_inbound_timestamp(value: Any) -> datetime | None:
    return parse_standing_concern_timestamp(value)
