from __future__ import annotations

from typing import Any

from otomekairo.capabilities import capability_manifests
from otomekairo.service.input.constants import VISUAL_SOURCE_LIMIT
from otomekairo.world_state.models import WorldStateTrace


class ServiceInputCapabilityContextMixin:
    def _annotate_capability_decision_view_with_fresh_visual_context(
        self,
        *,
        capability_decision_view: list[dict[str, Any]] | None,
        foreground_world_state: list[dict[str, Any]] | None,
        world_state_trace: WorldStateTrace | None,
        trigger_kind: str,
        client_context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]] | None:
        if not capability_decision_view:
            return capability_decision_view
        if trigger_kind == "user_message":
            return capability_decision_view
        reuse_world_state = self._foreground_world_state_for_capability_reuse(
            foreground_world_state=foreground_world_state,
            world_state_trace=world_state_trace,
            trigger_kind=trigger_kind,
        )
        wake_observation_sources = self._fresh_wake_observation_visual_sources(client_context)
        if not reuse_world_state and not wake_observation_sources:
            return capability_decision_view
        fresh_world_states = self._fresh_foreground_world_state_summaries(reuse_world_state)
        if not fresh_world_states and not wake_observation_sources:
            return capability_decision_view

        annotated: list[dict[str, Any]] = []
        changed = False
        for item in capability_decision_view:
            if not isinstance(item, dict):
                annotated.append(item)
                continue
            if item.get("id") != "vision.capture" or item.get("available") is not True:
                annotated.append(item)
                continue
            fresh_visual_sources = self._fresh_visual_world_states_for_sources(
                vision_sources=item.get("vision_sources"),
                fresh_world_states=fresh_world_states,
            )
            fresh_visual_sources = self._merge_fresh_visual_sources(
                fresh_visual_sources,
                wake_observation_sources,
            )
            if not fresh_visual_sources:
                annotated.append(item)
                continue
            annotated.append(
                {
                    **item,
                    "fresh_world_state_by_vision_source": fresh_visual_sources,
                    "fresh_world_state_policy": "同じ vision_source_id の新鮮な現在状態を再取得しない。",
                }
            )
            changed = True
        return annotated if changed else capability_decision_view

    def _foreground_world_state_for_capability_reuse(
        self,
        *,
        foreground_world_state: list[dict[str, Any]] | None,
        world_state_trace: WorldStateTrace | None,
        trigger_kind: str,
    ) -> list[dict[str, Any]]:
        if trigger_kind == "capability_result":
            return foreground_world_state or []
        if trigger_kind in {"wake", "background_thinking"}:
            return self._merge_foreground_world_state_for_reuse(
                foreground_world_state,
                world_state_trace.previous_foreground_world_state if world_state_trace is not None else None,
            )
        previous = world_state_trace.previous_foreground_world_state if world_state_trace is not None else None
        if isinstance(previous, list):
            return [item for item in previous if isinstance(item, dict)]
        return []

    def _merge_foreground_world_state_for_reuse(
        self,
        current: list[dict[str, Any]] | None,
        previous: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        for source in (current, previous):
            if not isinstance(source, list):
                continue
            for item in source:
                if not isinstance(item, dict):
                    continue
                key = self._foreground_world_state_reuse_key(item)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                merged.append(item)
        return merged

    def _foreground_world_state_reuse_key(self, item: dict[str, Any]) -> str:
        integration_key = item.get("integration_key")
        if isinstance(integration_key, str) and integration_key.strip():
            return f"integration_key:{integration_key.strip()}"
        scope = item.get("scope")
        if isinstance(scope, str) and scope.strip():
            return f"scope:{item.get('state_type') or ''}:{scope.strip()}"
        return "|".join(
            str(item.get(key) or "")
            for key in ("state_type", "summary_text")
        )

    def _fresh_wake_observation_visual_sources(
        self,
        client_context: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        if not isinstance(client_context, dict):
            return []
        observations = client_context.get("wake_observations")
        if not isinstance(observations, list):
            return []
        sources: list[dict[str, Any]] = []
        for observation in observations:
            if not isinstance(observation, dict):
                continue
            if (
                observation.get("status") != "succeeded"
                or observation.get("capability_id") != "vision.capture"
            ):
                continue
            vision_source_id = self._client_context_text(observation.get("vision_source_id"), limit=96)
            if vision_source_id is None:
                continue
            # 思考前観測は同じ cycle で取得済みの視覚観測として扱う。
            payload: dict[str, Any] = {
                "vision_source_id": vision_source_id,
                "age_label": "たった今",
                "fresh_source": "wake_observation",
            }
            for source_key, target_key, limit in (
                ("visual_summary_text", "summary_text", 120),
                ("source_label", "source_label", 80),
            ):
                value = self._client_context_text(observation.get(source_key), limit=limit)
                if value is not None:
                    payload[target_key] = value
            sources.append(payload)
        return sources[:VISUAL_SOURCE_LIMIT]

    def _merge_fresh_visual_sources(
        self,
        *source_lists: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged_by_source_id: dict[str, dict[str, Any]] = {}
        for source_list in source_lists:
            for item in source_list:
                if not isinstance(item, dict):
                    continue
                vision_source_id = item.get("vision_source_id")
                if not isinstance(vision_source_id, str) or not vision_source_id.strip():
                    continue
                key = vision_source_id.strip()
                existing = merged_by_source_id.get(key, {})
                merged_by_source_id[key] = {
                    **item,
                    **{
                        field: existing[field]
                        for field in ("summary_text", "source_label")
                        if field in existing and field not in item
                    },
                }
        return list(merged_by_source_id.values())[:VISUAL_SOURCE_LIMIT]

    def _fresh_foreground_world_state_summaries(
        self,
        foreground_world_state: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        fresh_states: list[dict[str, Any]] = []
        for summary in foreground_world_state:
            if not isinstance(summary, dict) or not self._foreground_world_state_is_fresh(summary):
                continue
            state_type = summary.get("state_type")
            if not isinstance(state_type, str) or not state_type.strip():
                continue
            summary_text = summary.get("summary_text")
            if not isinstance(summary_text, str) or not summary_text.strip():
                continue
            compact_summary = {
                "state_type": state_type.strip(),
                "scope": summary.get("scope"),
                "summary_text": summary_text.strip(),
                "age_label": summary.get("age_label"),
                "confidence": summary.get("confidence"),
                "salience": summary.get("salience"),
                "integration_key": summary.get("integration_key"),
            }
            fresh_states.append(compact_summary)
        return fresh_states

    def _fresh_visual_world_states_for_sources(
        self,
        *,
        vision_sources: Any,
        fresh_world_states: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not isinstance(vision_sources, list):
            return []
        visual_states_by_key = {
            state.get("integration_key"): state
            for state in fresh_world_states
            if isinstance(state, dict)
            and state.get("state_type") == "visual_context"
            and isinstance(state.get("integration_key"), str)
        }
        matches: list[dict[str, Any]] = []
        for source in vision_sources:
            if not isinstance(source, dict):
                continue
            source_id = self._client_context_text(source.get("vision_source_id"), limit=96)
            if source_id is None:
                continue
            # vision_source_id は registry の閉じた識別子なので、統合 key と直接照合する。
            state = visual_states_by_key.get(f"visual_context:{source_id}")
            if not isinstance(state, dict):
                continue
            payload = {
                "vision_source_id": source_id,
                "summary_text": state.get("summary_text"),
                "age_label": state.get("age_label"),
                "confidence": state.get("confidence"),
                "salience": state.get("salience"),
            }
            label = self._client_context_text(source.get("label"), limit=80)
            if label is not None:
                payload["source_label"] = label
            matches.append(payload)
        return matches[:VISUAL_SOURCE_LIMIT]

    def _foreground_world_state_is_fresh(self, summary: dict[str, Any]) -> bool:
        age_label = summary.get("age_label")
        if age_label == "たった今":
            return True
        if not isinstance(age_label, str) or not age_label.endswith("分前"):
            return False
        minute_text = age_label[:-2]
        if not minute_text.isdigit():
            return False
        return int(minute_text) <= 5

    def _build_capability_result_decision_context(
        self,
        *,
        trigger_kind: str,
        observation_summary: dict[str, Any] | None,
        capability_request_summary: dict[str, Any] | None,
        work_log: list[dict[str, Any]] | None = None,
        current_input: Any = None,
    ) -> dict[str, Any] | None:
        if trigger_kind != "capability_result":
            return None
        source_capability_id = self._capability_result_source_capability_id(
            observation_summary=observation_summary,
            capability_request_summary=capability_request_summary,
        )
        if source_capability_id is None:
            return None
        allowed_capability_ids = self._capability_result_allowed_followup_capability_ids(source_capability_id)
        orientation_kind = (
            "person"
            if getattr(current_input, "sender_kind", None) == "person"
            else "arrival"
        )
        payload: dict[str, Any] = {
            "source_capability_id": source_capability_id,
            "orientation_kind": orientation_kind,
            "allowed_followup_capability_ids": allowed_capability_ids,
            "followup_policy_summary": self._capability_result_followup_policy_summary(
                source_capability_id=source_capability_id,
                allowed_capability_ids=allowed_capability_ids,
                orientation_kind=orientation_kind,
            ),
        }
        source_request_summary = self._compact_capability_request_summary(capability_request_summary)
        if isinstance(source_request_summary, dict):
            payload["source_request_summary"] = source_request_summary
        followup_constraints = self._capability_result_followup_constraints(
            source_capability_id=source_capability_id,
            source_request_summary=source_request_summary,
        )
        if followup_constraints:
            payload["followup_constraints"] = followup_constraints
        compact_observation_summary = self._compact_capability_followup_observation_summary(observation_summary)
        if isinstance(compact_observation_summary, dict):
            payload["observation_summary"] = compact_observation_summary
        if work_log:
            payload["work_log"] = work_log
        observed_persons = self._observed_persons_from_mcp_observation(observation_summary)
        if observed_persons:
            payload["observed_persons"] = observed_persons
            payload["observed_person_refs"] = [person["person_ref"] for person in observed_persons]
        return payload

    def _observed_persons_from_mcp_observation(
        self,
        observation_summary: dict[str, Any] | None,
    ) -> list[dict[str, str]]:
        if not isinstance(observation_summary, dict):
            return []
        raw_persons = observation_summary.get("observed_persons")
        if not isinstance(raw_persons, list):
            return []
        persons: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in raw_persons:
            if not isinstance(item, dict):
                continue
            person_ref = item.get("person_ref")
            display_name = item.get("display_name")
            if not isinstance(person_ref, str) or not person_ref.startswith("person:mcp:"):
                continue
            if not isinstance(display_name, str) or not display_name.strip():
                continue
            if person_ref in seen:
                continue
            seen.add(person_ref)
            persons.append(
                {
                    "person_ref": person_ref,
                    "display_name": display_name.strip(),
                }
            )
        return persons

    def _register_mcp_observed_persons(
        self,
        *,
        state: dict[str, Any],
        observation_summary: dict[str, Any] | None,
        observed_at: str,
        evidence_event_ids: list[str],
    ) -> None:
        persons = self._observed_persons_from_mcp_observation(observation_summary)
        if not persons:
            return
        memory_set_id = state.get("selected_memory_set_id")
        if not isinstance(memory_set_id, str) or not memory_set_id.strip():
            return
        self.store.register_interaction_participants(
            memory_set_id=memory_set_id,
            participants=persons,
            observed_at=observed_at,
            evidence_event_ids=evidence_event_ids,
            source_kinds=["mcp_observed_person"],
        )

    def _capability_result_allowed_followup_capability_ids(self, source_capability_id: str) -> list[str]:
        allowed = [source_capability_id]
        state_policy = capability_manifests().get(source_capability_id, {}).get("state_policy", {})
        followup_requests = (
            state_policy.get("allow_followup_capability_requests")
            if isinstance(state_policy, dict)
            else None
        )
        if isinstance(followup_requests, list):
            for entry in followup_requests:
                if not isinstance(entry, dict):
                    continue
                capability_id = entry.get("capability_id")
                if not isinstance(capability_id, str) or not capability_id.strip():
                    continue
                normalized = capability_id.strip()
                if normalized not in allowed:
                    allowed.append(normalized)
        return allowed

    def _capability_result_followup_policy_summary(
        self,
        *,
        source_capability_id: str,
        allowed_capability_ids: list[str],
        orientation_kind: str = "arrival",
    ) -> str:
        if orientation_kind == "person":
            return (
                "向きは起点の人物発話である。"
                "allowed_followup_capability_ids に含まれる能力は同じ向きの続きとして使ってよい。"
                "speech は会話の続きであり、結果本文を向きにしない。"
            )
        if source_capability_id == "camera.ptz" and "vision.capture" in allowed_capability_ids:
            return (
                "camera.ptz result follow-up では同じ vision_source_id の vision.capture だけを追加で出せる。"
                "それ以外は受け取った result への speech / noop / pending_intent で閉じる。"
            )
        return (
            "source capability と異なる capability_request は出さず、"
            "受け取った result への speech / noop / pending_intent で閉じる。"
        )

    def _capability_result_followup_constraints(
        self,
        *,
        source_capability_id: str,
        source_request_summary: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        state_policy = capability_manifests().get(source_capability_id, {}).get("state_policy", {})
        followup_requests = (
            state_policy.get("allow_followup_capability_requests")
            if isinstance(state_policy, dict)
            else None
        )
        if not isinstance(followup_requests, list):
            return []
        constraints: list[dict[str, Any]] = []
        for entry in followup_requests:
            if not isinstance(entry, dict):
                continue
            capability_id = entry.get("capability_id")
            constraint = entry.get("constraint")
            if not isinstance(capability_id, str) or not capability_id.strip():
                continue
            payload: dict[str, Any] = {
                "capability_id": capability_id.strip(),
            }
            if isinstance(constraint, str) and constraint.strip():
                payload["constraint"] = constraint.strip()
            if (
                payload.get("constraint") == "same_vision_source_id"
                and isinstance(source_request_summary, dict)
            ):
                vision_source_id = source_request_summary.get("vision_source_id")
                if isinstance(vision_source_id, str) and vision_source_id.strip():
                    payload["vision_source_id"] = vision_source_id.strip()
            constraints.append(payload)
        return constraints

    def _capability_result_source_capability_id(
        self,
        *,
        observation_summary: dict[str, Any] | None,
        capability_request_summary: dict[str, Any] | None,
    ) -> str | None:
        for value in (
            observation_summary.get("capability_id") if isinstance(observation_summary, dict) else None,
            capability_request_summary.get("capability_id")
            if isinstance(capability_request_summary, dict)
            else None,
        ):
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None
