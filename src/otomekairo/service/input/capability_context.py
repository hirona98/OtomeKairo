from __future__ import annotations

from typing import Any

from otomekairo.capabilities import (
    capability_readiness_world_state_digest,
    capability_world_state_type,
)
from otomekairo.world_state.models import WorldStateTrace


class ServiceInputCapabilityContextMixin:
    def _annotate_capability_decision_view_with_fresh_world_state(
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
        fresh_state_by_type = self._fresh_foreground_world_state_by_type(fresh_world_states)
        if not fresh_state_by_type and not wake_observation_sources:
            return capability_decision_view

        annotated: list[dict[str, Any]] = []
        changed = False
        for item in capability_decision_view:
            if not isinstance(item, dict):
                annotated.append(item)
                continue
            capability_id = item.get("id")
            state_type = (
                self._capability_fresh_world_state_type(capability_id)
                if isinstance(capability_id, str)
                else None
            )
            if capability_id == "vision.capture":
                if item.get("available") is True:
                    fresh_visual_sources = self._fresh_visual_world_states_for_sources(
                        vision_sources=item.get("vision_sources"),
                        fresh_world_states=fresh_world_states,
                    )
                    fresh_visual_sources = self._merge_fresh_visual_sources(
                        fresh_visual_sources,
                        wake_observation_sources,
                    )
                    if fresh_visual_sources:
                        annotated.append(
                            {
                                **item,
                                "fresh_world_state_by_vision_source": fresh_visual_sources,
                                "fresh_world_state_policy": "明示的なユーザー依頼なしでは同じ vision_source_id の現在状態を再取得しない。",
                            }
                        )
                        changed = True
                    else:
                        annotated.append(item)
                else:
                    annotated.append(item)
                continue
            fresh_state = fresh_state_by_type.get(state_type) if state_type is not None else None
            if item.get("available") is not True or fresh_state is None:
                annotated.append(item)
                continue
            readiness_digest = capability_readiness_world_state_digest(
                capability_id,
                fresh_state.get("state_type"),
            )
            annotated_item = {
                **item,
                "fresh_world_state_available": True,
                "fresh_world_state": fresh_state,
                "fresh_world_state_policy": "明示的なユーザー依頼なしでは同じ現在状態を再取得しない。",
            }
            if isinstance(readiness_digest, dict):
                annotated_item["fresh_world_state_readiness_digest"] = readiness_digest
            annotated.append(annotated_item)
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
        if trigger_kind in {"wake", "background_wake"}:
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
            # wake observation は同じ cycle で取得済みの視覚観測として扱う。
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
        return sources[:6]

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
        return list(merged_by_source_id.values())[:6]

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
                "summary_text": self._clamp(summary_text.strip(), limit=120),
                "age_label": summary.get("age_label"),
                "confidence": summary.get("confidence"),
                "salience": summary.get("salience"),
                "integration_key": summary.get("integration_key"),
            }
            fresh_states.append(compact_summary)
        return fresh_states

    def _fresh_foreground_world_state_by_type(
        self,
        fresh_world_states: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        fresh_state_by_type: dict[str, dict[str, Any]] = {}
        for compact_summary in fresh_world_states:
            state_type = compact_summary.get("state_type")
            if not isinstance(state_type, str) or not state_type.strip():
                continue
            existing = fresh_state_by_type.get(state_type.strip())
            if existing is None or (
                self._world_state_reuse_rank(compact_summary) > self._world_state_reuse_rank(existing)
            ):
                fresh_state_by_type[state_type.strip()] = compact_summary
        return fresh_state_by_type

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
            source_key = self._world_state_vision_source_key({"vision_source_id": source_id})
            if source_key is None:
                continue
            state = visual_states_by_key.get(f"visual_context:{source_key}")
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
        return matches[:6]

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

    def _world_state_reuse_rank(self, summary: dict[str, Any]) -> float:
        salience = summary.get("salience")
        confidence = summary.get("confidence")
        score = 0.0
        if isinstance(salience, (int, float)):
            score += float(salience)
        if isinstance(confidence, (int, float)):
            score += float(confidence)
        if summary.get("age_label") == "たった今":
            score += 0.2
        return score

    def _capability_fresh_world_state_type(self, capability_id: str) -> str | None:
        return capability_world_state_type(capability_id)

    def _build_capability_result_decision_context(
        self,
        *,
        trigger_kind: str,
        observation_summary: dict[str, Any] | None,
        capability_request_summary: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if trigger_kind != "capability_result":
            return None
        source_capability_id = self._capability_result_source_capability_id(
            observation_summary=observation_summary,
            capability_request_summary=capability_request_summary,
        )
        if source_capability_id is None:
            return None
        payload: dict[str, Any] = {
            "source_capability_id": source_capability_id,
            "allowed_followup_capability_ids": [source_capability_id],
            "followup_policy_summary": (
                "source capability と異なる capability_request は出さず、"
                "受け取った result への reply / noop / pending_intent で閉じる。"
            ),
        }
        source_request_summary = self._compact_capability_request_summary(capability_request_summary)
        if isinstance(source_request_summary, dict):
            payload["source_request_summary"] = source_request_summary
        compact_observation_summary = self._compact_capability_followup_observation_summary(observation_summary)
        if isinstance(compact_observation_summary, dict):
            payload["observation_summary"] = compact_observation_summary
        return payload

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
