from __future__ import annotations

from copy import deepcopy
from typing import Any

from otomekairo.capabilities import capability_manifests
from otomekairo.memory.utils import localize_timestamp_fields


class ServiceConfigInspectionMixin:
    def get_capability_inspection(self, token: str | None) -> dict[str, Any]:
        # 認可
        state = self._require_token(token)

        # 状態
        generated_at = self._now_iso()
        inspection = self._build_capability_inspection_snapshot(
            state=state,
            current_time=generated_at,
        )

        # 応答
        return {
            "generated_at": generated_at,
            **inspection,
        }

    def get_current_state_inspection(self, token: str | None) -> dict[str, Any]:
        # 認可
        state = self._require_token(token)

        # 状態
        generated_at = self._now_iso()
        snapshot = {
            "generated_at": generated_at,
            "settings_snapshot": self._build_settings_snapshot(state),
            "runtime_summary": self._build_runtime_summary(state),
            "runtime_detail": self._build_current_runtime_detail(
                state=state,
                current_time=generated_at,
            ),
            "current_state": self._build_current_state_snapshot(
                state=state,
                current_time=generated_at,
            ),
            "capability_inspection": self._build_capability_inspection_snapshot(
                state=state,
                current_time=generated_at,
            ),
        }
        return localize_timestamp_fields(snapshot)

    def _build_capability_inspection_snapshot(
        self,
        *,
        state: dict[str, Any],
        current_time: str,
    ) -> dict[str, Any]:
        manifests = capability_manifests()
        bindings = self._event_stream_registry.list_capability_bindings()
        accepted_bindings = bindings["accepted"]
        rejected_bindings = bindings["rejected"]
        vision_sources = bindings.get("vision_sources", [])
        active_ongoing_action = self._current_ongoing_action(
            state=state,
            current_time=current_time,
        )

        return {
            "capabilities": [
                self._build_capability_availability(
                    manifest=manifest,
                    current_time=current_time,
                    bound_client_ids=accepted_bindings.get(capability_id, []),
                    rejected_bindings=rejected_bindings,
                    vision_sources=vision_sources if capability_id == "vision.capture" else None,
                    active_ongoing_action=active_ongoing_action,
                )
                for capability_id, manifest in sorted(manifests.items())
            ],
            "rejected_bindings": rejected_bindings,
        }

    def _build_runtime_summary(self, state: dict[str, Any]) -> dict[str, Any]:
        current_time = self._now_iso()
        ongoing_action = self._current_ongoing_action(state=state, current_time=current_time)
        with self._runtime_state_lock:
            memory_job_in_progress = self._memory_postprocess_runtime_state.get("current_cycle_id") is not None
            visual_daily_in_progress = self._visual_daily_runtime_state.get("current_digest_id") is not None
        return {
            "connection_state": "ready",
            "wake_scheduler_active": self._background_wake_scheduler_active() and state["wake_policy"]["mode"] == "interval",
            "ongoing_action_exists": ongoing_action is not None,
            "memory_job_worker_active": self._background_memory_postprocess_worker_active(),
            "visual_daily_worker_active": self._background_visual_daily_worker_active(),
            "pending_memory_job_count": self.store.count_memory_postprocess_jobs(
                result_statuses=["queued", "running"],
            ),
            "memory_job_in_progress": memory_job_in_progress,
            "visual_daily_in_progress": visual_daily_in_progress,
        }

    def _build_current_runtime_detail(
        self,
        *,
        state: dict[str, Any],
        current_time: str,
    ) -> dict[str, Any]:
        return {
            "wake_runtime_state": self._snapshot_wake_runtime_state(current_time=current_time),
            "wake_policy_observations": self._snapshot_wake_policy_observations(state=state),
            "memory_postprocess_runtime_state": self._snapshot_memory_postprocess_runtime_state(),
            "visual_daily_runtime_state": self._snapshot_visual_daily_runtime_state(),
            "pending_capability_requests": self._list_pending_capability_request_summaries(current_time=current_time),
        }

    def _build_current_state_snapshot(
        self,
        *,
        state: dict[str, Any],
        current_time: str,
    ) -> dict[str, Any]:
        return {
            "foreground_world_states": self._list_current_world_states(
                state=state,
                current_time=current_time,
                limit=8,
            ),
            "activity_context": self._summarize_activity_context(
                self.store.get_current_activity_state(
                    memory_set_id=state["selected_memory_set_id"],
                    current_time=current_time,
                ),
                current_time=current_time,
            ),
            "drive_states": self._list_current_drive_states(
                state=state,
                current_time=current_time,
                limit=6,
            ),
            "ongoing_action": self._current_ongoing_action(
                state=state,
                current_time=current_time,
            ),
            "pending_intent_candidates": self._list_pending_intent_candidates_for_inspection(
                state=state,
                current_time=current_time,
                limit=8,
            ),
            "mood_state": self.store.get_mood_state(
                memory_set_id=state["selected_memory_set_id"],
                current_time=current_time,
            ),
            "affect_states": self.store.list_affect_states_for_context(
                memory_set_id=state["selected_memory_set_id"],
                limit=6,
            ),
            "entity_registry": [
                self._compact_entity_registry_record(record)
                for record in self.store.list_entity_registry_records(
                    memory_set_id=state["selected_memory_set_id"],
                    limit=12,
                )
            ],
            "visual_daily_summary": self._current_visual_daily_summary(state=state),
        }

    def _compact_entity_registry_record(self, record: dict[str, Any]) -> dict[str, Any]:
        # entity registry の inspection 表示
        return {
            "entity_ref": record.get("entity_ref"),
            "entity_type": record.get("entity_type"),
            "display_name": record.get("display_name"),
            "aliases": [
                alias
                for alias in record.get("aliases", [])
                if isinstance(alias, str)
            ][:6],
            "first_seen_at": record.get("first_seen_at"),
            "last_seen_at": record.get("last_seen_at"),
            "confidence": record.get("confidence"),
            "salience": record.get("salience"),
            "evidence_event_count": len(record.get("evidence_event_ids", [])),
            "supporting_memory_unit_count": len(record.get("supporting_memory_unit_ids", [])),
        }

    def get_visual_digest_inspection(
        self,
        token: str | None,
        *,
        limit: int,
        local_date: str | None = None,
    ) -> dict[str, Any]:
        # 認可
        state = self._require_token(token)
        memory_set_id = state["selected_memory_set_id"]
        digests = self.store.list_daily_visual_digests(
            memory_set_id=memory_set_id,
            local_date=local_date,
            limit=max(1, min(limit, 50)),
        )
        return {
            "selected_memory_set_id": memory_set_id,
            "daily_visual_digests": [self._compact_visual_daily_digest(digest) for digest in digests],
        }

    def _current_visual_daily_summary(self, *, state: dict[str, Any]) -> dict[str, Any] | None:
        # 直近 digest
        digests = self.store.list_daily_visual_digests(
            memory_set_id=state["selected_memory_set_id"],
            limit=1,
        )
        if not digests:
            return None
        digest = digests[0]
        return {
            "latest_local_date": digest.get("local_date"),
            "latest_digest_id": digest.get("digest_id"),
            "record_count": int(digest.get("record_count", 0) or 0),
            "group_count": int(digest.get("group_count", 0) or 0),
            "retained_count": int(digest.get("retained_count", 0) or 0),
            "compressed_count": int(digest.get("compressed_count", 0) or 0),
            "memory_candidate_count": len(digest.get("memory_candidate_summaries", [])),
            "memory_promotion": digest.get("memory_promotion", {}),
        }

    def _compact_visual_daily_digest(self, digest: dict[str, Any]) -> dict[str, Any]:
        # compact 表示
        return {
            "digest_id": digest.get("digest_id"),
            "local_date": digest.get("local_date"),
            "started_at": digest.get("started_at"),
            "finished_at": digest.get("finished_at"),
            "result_status": digest.get("result_status"),
            "record_count": int(digest.get("record_count", 0) or 0),
            "group_count": int(digest.get("group_count", 0) or 0),
            "retained_count": int(digest.get("retained_count", 0) or 0),
            "compressed_count": int(digest.get("compressed_count", 0) or 0),
            "memory_promotion": digest.get("memory_promotion", {}),
            "group_summaries": [
                self._compact_visual_daily_group_summary(item)
                for item in digest.get("group_summaries", [])
                if isinstance(item, dict)
            ][:10],
            "memory_candidate_summaries": [
                self._compact_visual_daily_memory_candidate(item)
                for item in digest.get("memory_candidate_summaries", [])
                if isinstance(item, dict)
            ][:10],
        }

    def _compact_visual_daily_group_summary(self, item: dict[str, Any]) -> dict[str, Any]:
        # group 表示
        return {
            "duplicate_group_id": item.get("duplicate_group_id"),
            "record_count": int(item.get("record_count", 0) or 0),
            "first_observed_at": item.get("first_observed_at"),
            "last_observed_at": item.get("last_observed_at"),
            "representative_visual_observation_id": item.get("representative_visual_observation_id"),
            "summary_text": self._clamp(item.get("summary_text"), limit=160),
            "retention_status": item.get("retention_status"),
        }

    def _compact_visual_daily_memory_candidate(self, item: dict[str, Any]) -> dict[str, Any]:
        # 候補表示
        return {
            "duplicate_group_id": item.get("duplicate_group_id"),
            "representative_visual_observation_id": item.get("representative_visual_observation_id"),
            "summary_text": self._clamp(item.get("summary_text"), limit=160),
            "reason_code": item.get("reason_code"),
        }

    def _snapshot_wake_runtime_state(self, *, current_time: str) -> dict[str, Any]:
        self._prune_pending_intent_candidates(current_time=current_time)
        with self._runtime_state_lock:
            speech_history = self._wake_runtime_state.get("speech_history_by_dedupe", {})
            return {
                "last_wake_at": self._wake_runtime_state.get("last_wake_at"),
                "last_spontaneous_at": self._wake_runtime_state.get("last_spontaneous_at"),
                "initial_delay_until": self._wake_runtime_state.get("initial_delay_until"),
                "retry_after": self._wake_runtime_state.get("retry_after"),
                "speech_history_count": len(speech_history) if isinstance(speech_history, dict) else 0,
            }

    def _snapshot_memory_postprocess_runtime_state(self) -> dict[str, Any]:
        with self._runtime_state_lock:
            return {
                "current_cycle_id": self._memory_postprocess_runtime_state.get("current_cycle_id"),
            }

    def _snapshot_visual_daily_runtime_state(self) -> dict[str, Any]:
        with self._runtime_state_lock:
            return {
                "current_digest_id": self._visual_daily_runtime_state.get("current_digest_id"),
            }

    def _snapshot_wake_policy_observations(self, *, state: dict[str, Any]) -> list[dict[str, Any]]:
        wake_policy = state.get("wake_policy")
        observations = wake_policy.get("observations") if isinstance(wake_policy, dict) else None
        if not isinstance(observations, list):
            return []
        interval_seconds = wake_policy.get("interval_seconds") if isinstance(wake_policy, dict) else None
        with self._runtime_state_lock:
            runtime_snapshot = {
                key: dict(value)
                for key, value in self._wake_observation_runtime_state.items()
                if isinstance(key, str) and isinstance(value, dict)
            }

        summaries: list[dict[str, Any]] = []
        for observation in observations:
            if not isinstance(observation, dict):
                continue
            observation_id = self._client_context_text(observation.get("observation_id"), limit=96)
            if observation_id is None:
                continue
            input_payload = observation.get("input")
            vision_source_id = None
            mode = None
            if isinstance(input_payload, dict):
                vision_source_id = self._client_context_text(input_payload.get("vision_source_id"), limit=96)
                mode = self._client_context_text(input_payload.get("mode"), limit=32)
            runtime = runtime_snapshot.get(observation_id, {})
            item: dict[str, Any] = {
                "observation_id": observation_id,
                "enabled": observation.get("enabled") is True,
                "capability_id": self._client_context_text(observation.get("capability_id"), limit=80),
                "vision_source_id": vision_source_id,
                "mode": mode,
                "interval_seconds": interval_seconds,
                "last_run_at": runtime.get("last_run_at"),
                "last_status": runtime.get("last_status"),
                "last_summary": runtime.get("last_summary"),
                "last_error": runtime.get("last_error"),
            }
            for runtime_key in (
                "last_request_id",
                "last_vision_source_id",
                "last_source_label",
                "last_active_app",
                "last_window_title",
                "last_image_count",
                "last_success_at",
                "last_observation_signature",
                "same_observation_count",
                "last_prompted_at",
                "last_prompted_observation_signature",
            ):
                value = runtime.get(runtime_key)
                if value is not None:
                    item[runtime_key] = value
            summaries.append(item)
        return summaries

    def _list_pending_intent_candidates_for_inspection(
        self,
        *,
        state: dict[str, Any],
        current_time: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        self._prune_pending_intent_candidates(current_time=current_time)
        memory_set_id = state["selected_memory_set_id"]
        with self._runtime_state_lock:
            items = [
                {
                    "candidate_id": candidate.get("candidate_id"),
                    "intent_kind": candidate.get("intent_kind"),
                    "intent_summary": candidate.get("intent_summary"),
                    "reason_summary": candidate.get("reason_summary"),
                    "source_cycle_id": candidate.get("source_cycle_id"),
                    "not_before": candidate.get("not_before"),
                    "expires_at": candidate.get("expires_at"),
                    "dedupe_key": candidate.get("dedupe_key"),
                    "created_at": candidate.get("created_at"),
                    "updated_at": candidate.get("updated_at"),
                }
                for candidate in self._pending_intent_candidates
                if candidate.get("memory_set_id") == memory_set_id
            ]
        items.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return items[:limit]

    def _list_pending_capability_request_summaries(self, *, current_time: str) -> list[dict[str, Any]]:
        self._prune_pending_capability_requests(current_time=current_time)
        with self._capability_request_lock:
            pending_records = list(self._pending_capability_requests.values())

        summaries: list[dict[str, Any]] = []
        for pending in pending_records:
            request_record = pending.get("request_record") if isinstance(pending, dict) else None
            summary = self._capability_request_summary(request_record, status="pending")
            if not isinstance(summary, dict) or not isinstance(request_record, dict):
                continue
            summary["target_client_id"] = request_record.get("target_client_id")
            summary["created_at"] = request_record.get("created_at")
            summary["expires_at"] = request_record.get("expires_at")
            summary["action_id"] = request_record.get("action_id")
            summary["goal_summary"] = request_record.get("goal_summary")
            summaries.append(summary)
        summaries.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return summaries

    def _list_current_drive_states(
        self,
        *,
        state: dict[str, Any],
        current_time: str | None = None,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        memory_set_id = state["selected_memory_set_id"]
        query_time = current_time or self._now_iso()
        return self.store.list_drive_states(
            memory_set_id=memory_set_id,
            current_time=query_time,
            limit=limit,
        )

    def _summarize_drive_states(self, drive_states: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        summaries: list[dict[str, Any]] = []
        for drive_state in drive_states[:3]:
            if not isinstance(drive_state, dict):
                continue
            summaries.append(
                {
                    "drive_id": drive_state.get("drive_id"),
                    "drive_kind": drive_state.get("drive_kind"),
                    "summary_text": drive_state.get("summary_text"),
                    "salience": drive_state.get("salience"),
                    "related_scope_refs": deepcopy(drive_state.get("related_scope_refs", [])),
                    "supporting_memory_unit_ids": deepcopy(drive_state.get("supporting_memory_unit_ids", [])),
                    "supporting_memory_types": deepcopy(drive_state.get("supporting_memory_types", [])),
                    "scope_support_kinds": deepcopy(drive_state.get("scope_support_kinds", [])),
                    "focus_scope_type": drive_state.get("focus_scope_type"),
                    "focus_scope_key": drive_state.get("focus_scope_key"),
                    "support_count": drive_state.get("support_count"),
                    "support_strength": drive_state.get("support_strength"),
                    "scope_alignment": drive_state.get("scope_alignment"),
                    "freshness_hint": drive_state.get("freshness_hint"),
                    "signal_strength": drive_state.get("signal_strength"),
                    "persona_alignment": drive_state.get("persona_alignment"),
                    "stability_hint": drive_state.get("stability_hint"),
                    "source_updated_at": drive_state.get("source_updated_at"),
                    "updated_at": drive_state.get("updated_at"),
                    "expires_at": drive_state.get("expires_at"),
                }
            )
        if not summaries:
            return None
        return summaries

    def _list_current_world_states(
        self,
        *,
        state: dict[str, Any],
        current_time: str | None = None,
        limit: int = 4,
    ) -> list[dict[str, Any]]:
        memory_set_id = state["selected_memory_set_id"]
        query_time = current_time or self._now_iso()
        return self.store.list_world_states(
            memory_set_id=memory_set_id,
            current_time=query_time,
            limit=limit,
        )

    def _summarize_foreground_world_states(
        self,
        world_states: list[dict[str, Any]],
        *,
        current_time: str | None = None,
    ) -> list[dict[str, Any]] | None:
        reference_time = current_time or self._now_iso()
        summaries: list[dict[str, Any]] = []
        for world_state in world_states[:4]:
            if not isinstance(world_state, dict):
                continue
            scope_type = world_state.get("scope_type")
            scope_key = world_state.get("scope_key")
            if not isinstance(scope_type, str) or not isinstance(scope_key, str):
                continue
            summaries.append(
                {
                    "state_type": world_state.get("state_type"),
                    "scope": self._world_state_scope_ref(scope_type=scope_type, scope_key=scope_key),
                    "summary_text": world_state.get("summary_text"),
                    "confidence": world_state.get("confidence"),
                    "salience": world_state.get("salience"),
                    "integration_key": world_state.get("integration_key"),
                    "age_label": self._world_state_age_label(
                        reference_time=reference_time,
                        observed_at=world_state.get("observed_at"),
                        updated_at=world_state.get("updated_at"),
                    ),
                }
            )
        if not summaries:
            return None
        return summaries

    def _current_ongoing_action(
        self,
        *,
        state: dict[str, Any],
        current_time: str | None = None,
    ) -> dict[str, Any] | None:
        memory_set_id = state["selected_memory_set_id"]
        query_time = current_time or self._now_iso()
        return self.store.get_ongoing_action(
            memory_set_id=memory_set_id,
            current_time=query_time,
        )

    def _summarize_ongoing_action(self, ongoing_action: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(ongoing_action, dict):
            return None
        return {
            "action_id": ongoing_action.get("action_id"),
            "goal_summary": ongoing_action.get("goal_summary"),
            "step_summary": ongoing_action.get("step_summary"),
            "status": ongoing_action.get("status"),
            "episode_series_id": ongoing_action.get("episode_series_id"),
            "last_capability_id": ongoing_action.get("last_capability_id"),
            "updated_at": ongoing_action.get("updated_at"),
            "expires_at": ongoing_action.get("expires_at"),
        }

    def _world_state_scope_ref(self, *, scope_type: str, scope_key: str) -> str:
        if scope_type in {"self", "user", "world"}:
            return scope_key
        if scope_type == "topic":
            return scope_key
        if scope_type in {"entity", "relationship"}:
            return f"{scope_type}:{scope_key}"
        return f"{scope_type}:{scope_key}"

    def _world_state_age_label(
        self,
        *,
        reference_time: str,
        observed_at: Any,
        updated_at: Any,
    ) -> str | None:
        timestamp = observed_at if isinstance(observed_at, str) and observed_at else updated_at
        if not isinstance(timestamp, str) or not timestamp:
            return None
        delta_seconds = max(
            0,
            int((self._parse_iso(reference_time) - self._parse_iso(timestamp)).total_seconds()),
        )
        if delta_seconds < 60:
            return "たった今"
        if delta_seconds < 3600:
            return f"{delta_seconds // 60}分前"
        return f"{delta_seconds // 3600}時間前"

    def _background_wake_scheduler_active(self) -> bool:
        with self._runtime_state_lock:
            return self._background_wake_thread is not None and self._background_wake_thread.is_alive()

    def _background_memory_postprocess_worker_active(self) -> bool:
        with self._runtime_state_lock:
            return (
                self._background_memory_postprocess_thread is not None
                and self._background_memory_postprocess_thread.is_alive()
            )

    def _background_visual_daily_worker_active(self) -> bool:
        with self._runtime_state_lock:
            return (
                self._background_visual_daily_thread is not None
                and self._background_visual_daily_thread.is_alive()
            )
