from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from otomekairo.llm.client import LLMError
from otomekairo.service.common import debug_log
from otomekairo.service.input.source_owner import first_visual_source_owner


class ServiceInputActivityMixin:
    def _refresh_activity_context(
        self,
        *,
        state: dict[str, Any],
        started_at: str,
        input_text: str,
        current_input: dict[str, Any],
        recent_turns: list[dict[str, Any]],
        trigger_kind: str,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any] | None,
        visual_observation_context: dict[str, Any] | None,
        foreground_world_state: list[dict[str, Any]] | None,
        cycle_id: str | None,
        cycle_label: str,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        memory_set_id = state["selected_memory_set_id"]
        previous_state = self.store.get_current_activity_state(
            memory_set_id=memory_set_id,
            current_time=started_at,
        )
        source_pack = self._build_activity_source_pack(
            started_at=started_at,
            input_text=input_text,
            current_input=current_input,
            recent_turns=recent_turns,
            trigger_kind=trigger_kind,
            client_context=client_context,
            observation_summary=observation_summary,
            visual_observation_context=visual_observation_context,
            foreground_world_state=foreground_world_state,
            previous_activity_state=previous_state,
        )
        trace: dict[str, Any] = {
            "result_status": "skipped",
            "source_pack_summary": self._summarize_activity_source_pack(source_pack),
            "candidate_count": 0,
            "updated_count": 0,
            "expired_count": 0,
            "activity_context": self._summarize_activity_context(previous_state, current_time=started_at),
            "failure_reason": None,
        }
        if not self._should_generate_activity_state(source_pack):
            return trace["activity_context"], trace

        try:
            role_definition = state["model_presets"][state["selected_model_preset_id"]]["roles"]["input_interpretation"]
            payload = self.llm.generate_activity_state(
                role_definition=role_definition,
                source_pack=source_pack,
            )
            candidate = self._activity_candidate(payload)
            activity_state, expired_activity_id = self._normalize_activity_candidate(
                memory_set_id=memory_set_id,
                started_at=started_at,
                source_pack=source_pack,
                previous_state=previous_state,
                candidate=candidate,
                cycle_id=cycle_id,
            )
            refresh_summary = self.store.refresh_activity_state(
                memory_set_id=memory_set_id,
                current_time=started_at,
                activity_state=activity_state,
                expired_activity_id=expired_activity_id,
            )
            current_state = self.store.get_current_activity_state(
                memory_set_id=memory_set_id,
                current_time=started_at,
            )
            activity_context = self._summarize_activity_context(current_state, current_time=started_at)
            trace.update(
                {
                    "result_status": "succeeded",
                    "candidate_count": 1 if isinstance(candidate, dict) else 0,
                    "updated_count": int(refresh_summary.get("updated_count", 0)),
                    "expired_count": int(refresh_summary.get("expired_count", 0)),
                    "activity_context": activity_context,
                    "candidate_summary": self._compact_activity_candidate(candidate),
                }
            )
            debug_log(
                "Activity",
                (
                    f"{cycle_label} activity done status={trace['result_status']} "
                    f"candidates={trace['candidate_count']} updated={trace['updated_count']}"
                ),
                level="DEBUG",
            )
            return activity_context, trace
        except (LLMError, KeyError, TypeError, ValueError) as exc:
            trace.update(
                {
                    "result_status": "failed",
                    "failure_reason": str(exc),
                    "activity_context": self._summarize_activity_context(previous_state, current_time=started_at),
                }
            )
            debug_log("Activity", f"{cycle_label} activity failed reason={self._clamp(str(exc))}", level="WARNING")
            return trace["activity_context"], trace

    def _build_activity_source_pack(
        self,
        *,
        started_at: str,
        input_text: str,
        current_input: dict[str, Any],
        recent_turns: list[dict[str, Any]],
        trigger_kind: str,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any] | None,
        visual_observation_context: dict[str, Any] | None,
        foreground_world_state: list[dict[str, Any]] | None,
        previous_activity_state: dict[str, Any] | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "trigger_kind": trigger_kind,
            "time_context": self._build_time_context(current_time=started_at),
            "current_input": current_input,
            "current_input_summary": self._clamp(input_text.strip(), limit=200) or "",
            "recent_turns": recent_turns[-4:],
        }
        compact_client_context = self._activity_client_context(client_context)
        if compact_client_context:
            payload["client_context"] = compact_client_context
        compact_observation = self._activity_observation_summary(observation_summary)
        if compact_observation:
            payload["observation_summary"] = compact_observation
        if visual_observation_context:
            payload["visual_observation_context"] = visual_observation_context
        if foreground_world_state:
            payload["foreground_world_state"] = foreground_world_state[:4]
        previous_context = self._summarize_activity_context(previous_activity_state, current_time=started_at)
        if previous_context:
            payload["previous_activity_context"] = previous_context
        source_owner = self._activity_source_owner(
            client_context=client_context,
            observation_summary=observation_summary,
            visual_observation_context=visual_observation_context,
        )
        if source_owner is not None:
            payload["source_owner"] = source_owner
        return payload

    def _should_generate_activity_state(self, source_pack: dict[str, Any]) -> bool:
        if source_pack.get("visual_observation_context"):
            return True
        if source_pack.get("observation_summary"):
            return True
        if source_pack.get("previous_activity_context"):
            return True
        client_context = source_pack.get("client_context")
        if isinstance(client_context, dict) and any(
            isinstance(client_context.get(key), str) and client_context[key].strip()
            for key in ("active_app", "window_title", "wake_observation_summary")
        ):
            return True
        current_input = source_pack.get("current_input")
        return isinstance(current_input, dict) and current_input.get("sender") == "user"

    def _activity_client_context(self, client_context: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for key, limit in (
            ("source", 48),
            ("active_app", 80),
            ("window_title", 120),
            ("locale", 32),
            ("wake_observation_summary", 360),
        ):
            value = self._client_context_text(client_context.get(key), limit=limit)
            if value is not None:
                payload[key] = value
        visual_signals = self._compact_visual_observation_signals(
            client_context.get("visual_observation_signals")
        )
        if visual_signals:
            payload["visual_observations"] = visual_signals
            for signal in visual_signals:
                source_owner = signal.get("source_owner")
                if isinstance(source_owner, str) and source_owner.strip():
                    payload["source_owner"] = source_owner.strip()
                    break
        return payload

    def _activity_observation_summary(self, observation_summary: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(observation_summary, dict):
            return {}
        payload: dict[str, Any] = {}
        for key, limit in (
            ("source", 48),
            ("capability_id", 80),
            ("vision_source_id", 96),
            ("source_kind", 32),
            ("source_label", 80),
            ("active_app", 80),
            ("window_title", 120),
            ("visual_summary_text", 240),
            ("body_state_summary", 200),
            ("device_state_summary", 200),
            ("schedule_summary", 200),
            ("social_context_summary", 200),
            ("environment_summary", 200),
            ("location_summary", 200),
            ("status_text", 200),
            ("error", 160),
        ):
            value = self._client_context_text(observation_summary.get(key), limit=limit)
            if value is not None:
                payload[key] = value
        source_owner = first_visual_source_owner(payload)
        if source_owner is not None:
            payload["source_owner"] = source_owner
        image_interpreted = observation_summary.get("image_interpreted")
        if isinstance(image_interpreted, bool):
            payload["image_interpreted"] = image_interpreted
        return payload

    def _activity_source_owner(
        self,
        *,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any] | None,
        visual_observation_context: dict[str, Any] | None,
    ) -> str | None:
        owner = first_visual_source_owner(observation_summary, visual_observation_context)
        if owner is not None:
            return owner
        client_payload: dict[str, Any] | None = None
        visual_signals = self._compact_visual_observation_signals(
            client_context.get("visual_observation_signals")
        )
        for signal in visual_signals:
            owner = first_visual_source_owner(signal)
            if owner is not None:
                return owner
        if isinstance(client_context, dict):
            client_payload = {"source_kind": client_context.get("source_kind")}
        owner = first_visual_source_owner(client_payload)
        if owner is not None:
            return owner
        return None

    def _activity_candidate(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        candidates = payload.get("activity_candidates")
        if not isinstance(candidates, list) or not candidates:
            return None
        candidate = candidates[0]
        return candidate if isinstance(candidate, dict) else None

    def _normalize_activity_candidate(
        self,
        *,
        memory_set_id: str,
        started_at: str,
        source_pack: dict[str, Any],
        previous_state: dict[str, Any] | None,
        candidate: dict[str, Any] | None,
        cycle_id: str | None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        if not isinstance(candidate, dict):
            return None, None
        transition = str(candidate.get("transition", "none")).strip()
        if transition == "none":
            return None, None
        if transition == "end":
            previous_id = previous_state.get("activity_id") if isinstance(previous_state, dict) else None
            return None, previous_id if isinstance(previous_id, str) else None

        previous_activity = self._activity_previous_summary(previous_state, current_time=started_at)
        activity_id = previous_state.get("activity_id") if transition == "continue" and isinstance(previous_state, dict) else None
        if not isinstance(activity_id, str) or not activity_id.strip():
            activity_id = f"activity:{uuid.uuid4().hex}"
        started_source = previous_state.get("started_at") if transition == "continue" and isinstance(previous_state, dict) else None
        started_source = started_source if isinstance(started_source, str) and started_source.strip() else started_at
        ttl_seconds = self._activity_ttl_seconds(str(candidate["ttl_hint"]))
        return {
            "activity_id": activity_id,
            "memory_set_id": memory_set_id,
            "label": self._clamp(str(candidate["label"]).strip(), limit=120),
            "actor": str(candidate["actor"]).strip(),
            "target": self._clamp(str(candidate.get("target", "")).strip(), limit=120),
            "status": "active",
            "confidence": self._activity_score_from_hint(str(candidate["confidence_hint"])),
            "salience": self._activity_score_from_hint(str(candidate["salience_hint"])),
            "source_kinds": self._activity_source_kinds(source_pack),
            "source_refs": [cycle_id] if isinstance(cycle_id, str) and cycle_id.strip() else [],
            "reason_summary": self._clamp(str(candidate["reason_summary"]).strip(), limit=180),
            "started_at": started_source,
            "updated_at": started_at,
            "expires_at": (self._parse_iso(started_at) + timedelta(seconds=ttl_seconds)).isoformat(),
            "previous_activity": previous_activity,
        }, None

    def _activity_score_from_hint(self, hint: str) -> float:
        if hint == "high":
            return 0.86
        if hint == "medium":
            return 0.62
        return 0.34

    def _activity_ttl_seconds(self, hint: str) -> int:
        if hint == "long":
            return 7200
        if hint == "medium":
            return 2400
        return 900

    def _activity_source_kinds(self, source_pack: dict[str, Any]) -> list[str]:
        kinds: list[str] = []
        if source_pack.get("current_input"):
            kinds.append("current_input")
        if source_pack.get("client_context"):
            kinds.append("client_context")
        if source_pack.get("observation_summary"):
            kinds.append("observation_summary")
        if source_pack.get("visual_observation_context"):
            kinds.append("visual_observation_context")
        if source_pack.get("foreground_world_state"):
            kinds.append("world_state")
        return kinds

    def _summarize_activity_context(
        self,
        activity_state: dict[str, Any] | None,
        *,
        current_time: str,
    ) -> dict[str, Any] | None:
        if not isinstance(activity_state, dict):
            return None
        current_activity = self._activity_prompt_summary(activity_state, current_time=current_time)
        previous = activity_state.get("previous_activity")
        previous_activity = (
            self._activity_previous_prompt_summary(previous)
            if isinstance(previous, dict)
            else None
        )
        payload: dict[str, Any] = {}
        if current_activity:
            payload["current_activity"] = current_activity
        if previous_activity:
            payload["previous_activity"] = previous_activity
        return payload or None

    def _activity_prompt_summary(
        self,
        activity_state: dict[str, Any],
        *,
        current_time: str,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for key in ("label", "target"):
            value = activity_state.get(key)
            if isinstance(value, str) and value.strip():
                payload[key] = self._clamp(value.strip(), limit=120)
        actor = activity_state.get("actor")
        if isinstance(actor, str) and actor.strip():
            payload["actor"] = actor.strip()
        for key in ("confidence", "salience"):
            value = activity_state.get(key)
            if isinstance(value, (int, float)):
                payload[key] = round(float(value), 3)
        updated_at = activity_state.get("updated_at")
        if isinstance(updated_at, str) and updated_at.strip():
            payload["age_label"] = self._activity_age_label(updated_at, current_time=current_time)
        reason_summary = activity_state.get("reason_summary")
        if isinstance(reason_summary, str) and reason_summary.strip():
            payload["reason_summary"] = self._clamp(reason_summary.strip(), limit=160)
        return payload

    def _activity_previous_summary(
        self,
        activity_state: dict[str, Any] | None,
        *,
        current_time: str,
    ) -> dict[str, Any] | None:
        if not isinstance(activity_state, dict):
            return None
        payload = self._activity_prompt_summary(activity_state, current_time=current_time)
        if not payload:
            return None
        payload["ended_age_label"] = self._activity_age_label(
            str(activity_state.get("updated_at") or current_time),
            current_time=current_time,
        )
        return self._activity_previous_prompt_summary(payload)

    def _activity_previous_prompt_summary(self, activity_state: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for key in ("label", "actor", "target", "reason_summary", "ended_age_label"):
            value = activity_state.get(key)
            if isinstance(value, str) and value.strip():
                limit = 160 if key == "reason_summary" else 120
                payload[key] = self._clamp(value.strip(), limit=limit)
        for key in ("confidence", "salience"):
            value = activity_state.get(key)
            if isinstance(value, (int, float)):
                payload[key] = round(float(value), 3)
        return payload

    def _activity_age_label(self, updated_at: str, *, current_time: str) -> str:
        try:
            seconds = max(0, int((self._parse_iso(current_time) - self._parse_iso(updated_at)).total_seconds()))
        except ValueError:
            return "直近"
        if seconds < 90:
            return "直前"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes}分前"
        return f"{minutes // 60}時間前"

    def _summarize_activity_source_pack(self, source_pack: dict[str, Any]) -> dict[str, Any]:
        return {
            "trigger_kind": source_pack.get("trigger_kind"),
            "has_client_context": isinstance(source_pack.get("client_context"), dict),
            "has_observation_summary": isinstance(source_pack.get("observation_summary"), dict),
            "has_visual_observation_context": isinstance(source_pack.get("visual_observation_context"), dict),
            "has_previous_activity_context": isinstance(source_pack.get("previous_activity_context"), dict),
            "recent_turn_count": len(source_pack.get("recent_turns", [])) if isinstance(source_pack.get("recent_turns"), list) else 0,
        }

    def _compact_activity_candidate(self, candidate: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(candidate, dict):
            return None
        return {
            key: candidate.get(key)
            for key in (
                "label",
                "actor",
                "target",
                "confidence_hint",
                "salience_hint",
                "ttl_hint",
                "transition",
                "reason_summary",
            )
            if candidate.get(key) is not None
        }
