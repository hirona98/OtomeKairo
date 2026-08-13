from __future__ import annotations

from typing import Any

from otomekairo.service.standing_concerns import (
    extra_background_thinking_delay_seconds,
    list_due_standing_concerns,
    selected_standing_concern_ids,
)


class ServiceInputStandingConcernMixin:
    def _standing_concern_last_attended_map(self) -> dict[str, str]:
        with self._runtime_state_lock:
            stored = self._wake_runtime_state.setdefault("standing_concern_last_attended_at", {})
            if not isinstance(stored, dict):
                stored = {}
                self._wake_runtime_state["standing_concern_last_attended_at"] = stored
            return stored

    def _due_standing_concerns(self, *, state: dict[str, Any], current_time: str) -> list[dict[str, Any]]:
        return list_due_standing_concerns(
            concerns=state.get("standing_concerns"),
            last_attended_at_by_id=self._standing_concern_last_attended_map(),
            current_time=current_time,
        )

    def _standing_concern_runtime_snapshot(
        self,
        *,
        state: dict[str, Any],
        current_time: str,
    ) -> list[dict[str, Any]]:
        attended = self._standing_concern_last_attended_map()
        snapshot: list[dict[str, Any]] = []
        for concern in state.get("standing_concerns") or []:
            if not isinstance(concern, dict):
                continue
            concern_id = concern.get("concern_id")
            if not isinstance(concern_id, str) or not concern_id.strip():
                continue
            last_attended_at = attended.get(concern_id)
            snapshot.append(
                {
                    "concern_id": concern_id,
                    "enabled": concern.get("enabled") is True,
                    "due": any(
                        due.get("concern_id") == concern_id
                        for due in self._due_standing_concerns(state=state, current_time=current_time)
                    ),
                    "last_attended_at": last_attended_at if isinstance(last_attended_at, str) else None,
                }
            )
        return snapshot

    def _extra_standing_concern_thinking_delay_seconds(
        self,
        *,
        state: dict[str, Any],
        current_time: str,
    ) -> float | None:
        wake_policy = state.get("wake_policy") if isinstance(state.get("wake_policy"), dict) else {}
        with self._runtime_state_lock:
            last_wake_at = self._wake_runtime_state.get("last_wake_at")
        return extra_background_thinking_delay_seconds(
            wake_mode=wake_policy.get("mode"),
            wake_interval_seconds=int(wake_policy.get("interval_seconds") or 1),
            last_wake_at=last_wake_at,
            due_concerns=self._due_standing_concerns(state=state, current_time=current_time),
            current_time=current_time,
        )

    def _mark_standing_concerns_attended(
        self,
        *,
        decision: dict[str, Any],
        workspace_context: dict[str, Any] | None,
        current_time: str,
    ) -> list[str]:
        selected_ids = selected_standing_concern_ids(
            decision=decision,
            workspace_context=workspace_context,
        )
        if not selected_ids:
            return []
        with self._runtime_state_lock:
            attended = self._wake_runtime_state.setdefault("standing_concern_last_attended_at", {})
            if not isinstance(attended, dict):
                attended = {}
                self._wake_runtime_state["standing_concern_last_attended_at"] = attended
            for concern_id in selected_ids:
                attended[concern_id] = current_time
        return selected_ids
