from __future__ import annotations

from typing import Any

from otomekairo.service.autonomous_run import AUTONOMOUS_RUN_ACTIVE_STATUSES
from otomekairo.service.periodic_thought_topics import (
    list_due_periodic_thought_topics,
    selected_periodic_thought_topic_ids,
    periodic_thought_topic_ids_from_runs,
)


class ServiceInputPeriodicThoughtTopicMixin:
    def _periodic_thought_topic_last_attended_map(self) -> dict[str, str]:
        with self._runtime_state_lock:
            stored = self._wake_runtime_state.setdefault("periodic_thought_topic_last_attended_at", {})
            if not isinstance(stored, dict):
                stored = {}
                self._wake_runtime_state["periodic_thought_topic_last_attended_at"] = stored
            return stored

    def _active_periodic_thought_topic_ids(self, *, state: dict[str, Any]) -> set[str]:
        memory_set_id = state.get("selected_memory_set_id")
        if not isinstance(memory_set_id, str) or not memory_set_id.strip():
            return set()
        runs = self.store.list_autonomous_runs(
            memory_set_id=memory_set_id,
            statuses=sorted(AUTONOMOUS_RUN_ACTIVE_STATUSES),
            limit=None,
        )
        return periodic_thought_topic_ids_from_runs(runs)

    def _due_periodic_thought_topics(self, *, state: dict[str, Any], current_time: str) -> list[dict[str, Any]]:
        return list_due_periodic_thought_topics(
            topics=state.get("periodic_thought_topics"),
            last_attended_at_by_id=self._periodic_thought_topic_last_attended_map(),
            current_time=current_time,
            active_topic_ids=self._active_periodic_thought_topic_ids(state=state),
        )

    def _periodic_thought_topic_runtime_snapshot(
        self,
        *,
        state: dict[str, Any],
        current_time: str,
    ) -> list[dict[str, Any]]:
        attended = self._periodic_thought_topic_last_attended_map()
        snapshot: list[dict[str, Any]] = []
        for topic in state.get("periodic_thought_topics") or []:
            if not isinstance(topic, dict):
                continue
            topic_id = topic.get("topic_id")
            if not isinstance(topic_id, str) or not topic_id.strip():
                continue
            last_attended_at = attended.get(topic_id)
            snapshot.append(
                {
                    "topic_id": topic_id,
                    "enabled": topic.get("enabled") is True,
                    "due": any(
                        due.get("topic_id") == topic_id
                        for due in self._due_periodic_thought_topics(state=state, current_time=current_time)
                    ),
                    "last_attended_at": last_attended_at if isinstance(last_attended_at, str) else None,
                }
            )
        return snapshot

    def _mark_periodic_thought_topics_attended(
        self,
        *,
        decision: dict[str, Any],
        workspace_context: dict[str, Any] | None,
        current_time: str,
    ) -> list[str]:
        selected_ids = selected_periodic_thought_topic_ids(
            decision=decision,
            workspace_context=workspace_context,
        )
        if not selected_ids:
            return []
        with self._runtime_state_lock:
            attended = self._wake_runtime_state.setdefault("periodic_thought_topic_last_attended_at", {})
            if not isinstance(attended, dict):
                attended = {}
                self._wake_runtime_state["periodic_thought_topic_last_attended_at"] = attended
            for topic_id in selected_ids:
                attended[topic_id] = current_time
        return selected_ids
