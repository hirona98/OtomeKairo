from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any


STANDING_CONCERN_FIELDS = {
    "concern_id",
    "enabled",
    "min_interval_seconds",
    "concern_summary",
}


def parse_standing_concern_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def standing_concern_is_due(
    *,
    enabled: bool,
    min_interval_seconds: int,
    last_attended_at: Any,
    current_time: str,
) -> bool:
    if enabled is not True:
        return False
    current_dt = parse_standing_concern_timestamp(current_time)
    if current_dt is None:
        return False
    last_attended_dt = parse_standing_concern_timestamp(last_attended_at)
    if last_attended_dt is None:
        return True
    return current_dt >= last_attended_dt + timedelta(seconds=int(min_interval_seconds))


def list_due_standing_concerns(
    *,
    concerns: list[dict[str, Any]] | None,
    last_attended_at_by_id: dict[str, str] | None,
    current_time: str,
) -> list[dict[str, Any]]:
    attended = last_attended_at_by_id if isinstance(last_attended_at_by_id, dict) else {}
    due: list[dict[str, Any]] = []
    for concern in concerns or []:
        if not isinstance(concern, dict):
            continue
        concern_id = concern.get("concern_id")
        if not isinstance(concern_id, str) or not concern_id.strip():
            continue
        if standing_concern_is_due(
            enabled=concern.get("enabled") is True,
            min_interval_seconds=int(concern.get("min_interval_seconds") or 0),
            last_attended_at=attended.get(concern_id.strip()),
            current_time=current_time,
        ):
            due.append(concern)
    return due


def extra_background_thinking_delay_seconds(
    *,
    wake_mode: Any,
    wake_interval_seconds: int,
    last_wake_at: Any,
    due_concerns: list[dict[str, Any]],
    current_time: str,
) -> float | None:
    if not due_concerns:
        return None
    current_dt = parse_standing_concern_timestamp(current_time)
    if current_dt is None:
        return None
    shortest_interval = min(int(concern["min_interval_seconds"]) for concern in due_concerns)
    last_wake_dt = parse_standing_concern_timestamp(last_wake_at)
    if wake_mode == "interval":
        if last_wake_dt is None:
            return None
        time_until_regular = (last_wake_dt + timedelta(seconds=int(wake_interval_seconds)) - current_dt).total_seconds()
        if time_until_regular <= shortest_interval:
            return None
    extra_cadence = min(int(wake_interval_seconds), shortest_interval)
    if last_wake_dt is None:
        return 0.0
    remaining = (last_wake_dt + timedelta(seconds=extra_cadence) - current_dt).total_seconds()
    if remaining <= 0:
        return 0.0
    return remaining


def standing_concern_factor_ref(concern_id: str) -> str:
    return f"standing_concern:{concern_id}"


def selected_standing_concern_ids(
    *,
    decision: dict[str, Any] | None,
    workspace_context: dict[str, Any] | None,
) -> list[str]:
    if not isinstance(decision, dict):
        return []
    if decision.get("kind") not in {"autonomous_run", "capability_request"}:
        return []
    selection = decision.get("foreground_selection")
    if not isinstance(selection, dict):
        return []
    selected_refs: set[str] = set()
    primary = selection.get("primary_factor_ref")
    if isinstance(primary, str) and primary.strip():
        selected_refs.add(primary.strip())
    supporting = selection.get("supporting_factor_refs")
    if isinstance(supporting, list):
        for factor_ref in supporting:
            if isinstance(factor_ref, str) and factor_ref.strip():
                selected_refs.add(factor_ref.strip())
    if not selected_refs:
        return []
    candidates = workspace_context.get("workspace_candidates") if isinstance(workspace_context, dict) else None
    if not isinstance(candidates, list):
        return []
    selected_ids: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, dict) or candidate.get("kind") != "standing_concern":
            continue
        factor_ref = candidate.get("factor_ref")
        if not isinstance(factor_ref, str) or factor_ref not in selected_refs:
            continue
        metadata = candidate.get("metadata")
        concern_id = metadata.get("concern_id") if isinstance(metadata, dict) else None
        if isinstance(concern_id, str) and concern_id.strip() and concern_id.strip() not in selected_ids:
            selected_ids.append(concern_id.strip())
    return selected_ids
