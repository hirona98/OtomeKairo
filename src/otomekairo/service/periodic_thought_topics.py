from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any


PERIODIC_THOUGHT_TOPIC_FIELDS = {
    "topic_id",
    "enabled",
    "min_periodic_thinking_interval_seconds",
    "topic_summary",
}


def parse_periodic_thought_topic_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def periodic_thought_topic_is_due(
    *,
    enabled: bool,
    min_periodic_thinking_interval_seconds: int,
    last_attended_at: Any,
    current_time: str,
) -> bool:
    if enabled is not True:
        return False
    current_dt = parse_periodic_thought_topic_timestamp(current_time)
    if current_dt is None:
        return False
    last_attended_dt = parse_periodic_thought_topic_timestamp(last_attended_at)
    if last_attended_dt is None:
        return True
    return current_dt >= last_attended_dt + timedelta(seconds=int(min_periodic_thinking_interval_seconds))


def periodic_thought_topic_ids_from_runs(runs: list[dict[str, Any]] | None) -> set[str]:
    found: set[str] = set()
    for run in runs or []:
        if not isinstance(run, dict):
            continue
        raw_ids = run.get("periodic_thought_topic_ids")
        if not isinstance(raw_ids, list):
            continue
        for topic_id in raw_ids:
            if isinstance(topic_id, str) and topic_id.strip():
                found.add(topic_id.strip())
    return found


def list_due_periodic_thought_topics(
    *,
    topics: list[dict[str, Any]] | None,
    last_attended_at_by_id: dict[str, str] | None,
    current_time: str,
    active_topic_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    attended = last_attended_at_by_id if isinstance(last_attended_at_by_id, dict) else {}
    active = active_topic_ids if isinstance(active_topic_ids, set) else set()
    due: list[dict[str, Any]] = []
    for topic in topics or []:
        if not isinstance(topic, dict):
            continue
        topic_id = topic.get("topic_id")
        if not isinstance(topic_id, str) or not topic_id.strip():
            continue
        normalized_id = topic_id.strip()
        if normalized_id in active:
            continue
        if periodic_thought_topic_is_due(
            enabled=topic.get("enabled") is True,
            min_periodic_thinking_interval_seconds=int(
                topic.get("min_periodic_thinking_interval_seconds") or 0
            ),
            last_attended_at=attended.get(normalized_id),
            current_time=current_time,
        ):
            due.append(topic)
    return due


def periodic_thought_topic_factor_ref(topic_id: str) -> str:
    return f"periodic_thought_topic:{topic_id}"


def build_periodic_thought_topic_orientation_context(
    topics: list[dict[str, Any]] | None,
) -> dict[str, list[dict[str, str]]]:
    entries: list[dict[str, str]] = []
    for topic in topics or []:
        if not isinstance(topic, dict):
            continue
        topic_id = topic.get("topic_id")
        summary_text = topic.get("topic_summary")
        if (
            not isinstance(topic_id, str)
            or not topic_id.strip()
            or not isinstance(summary_text, str)
            or not summary_text.strip()
        ):
            continue
        entries.append(
            {
                "factor_ref": periodic_thought_topic_factor_ref(topic_id.strip()),
                "summary_text": summary_text.strip(),
            }
        )
    return {"periodic_thought_topics": entries}


def selected_periodic_thought_topic_ids(
    *,
    decision: dict[str, Any] | None,
    workspace_context: dict[str, Any] | None,
) -> list[str]:
    if not isinstance(decision, dict):
        return []
    source = decision
    separated = decision.get("separated_comparisons")
    if isinstance(separated, dict):
        self_decision = separated.get("self_activity")
        if isinstance(self_decision, dict):
            source = self_decision
    if source.get("kind") not in {"autonomous_run", "capability_request"}:
        return []
    selection = source.get("foreground_selection")
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
        if not isinstance(candidate, dict) or candidate.get("kind") != "periodic_thought_topic":
            continue
        factor_ref = candidate.get("factor_ref")
        if not isinstance(factor_ref, str) or factor_ref not in selected_refs:
            continue
        metadata = candidate.get("metadata")
        topic_id = metadata.get("topic_id") if isinstance(metadata, dict) else None
        if isinstance(topic_id, str) and topic_id.strip() and topic_id.strip() not in selected_ids:
            selected_ids.append(topic_id.strip())
    return selected_ids
