from __future__ import annotations

from copy import deepcopy
import uuid
from typing import Any

from otomekairo.llm.client import LLMClient
from otomekairo.llm.contexts import build_persona_context
from otomekairo.memory.actions import MemoryActionResolver
from otomekairo.memory.correction import MemoryCorrectionReconciler
from otomekairo.memory.reflection.consolidator import ReflectiveConsolidator
from otomekairo.memory.utils import clamp_score, merged_event_ids, normalized_text_list, now_iso, optional_text
from otomekairo.memory.vector import MemoryVectorIndexer
from otomekairo.store.file_store import FileStore


# memory_interpretation に渡す events は補助文脈に留める。
MEMORY_CONTEXT_EVENT_LIMIT = 24
ACTIVE_COMMITMENT_STATES = {"open", "waiting_confirmation", "on_hold"}


# 統合器
class MemoryConsolidator:
    def __init__(self, *, store: FileStore, llm: LLMClient) -> None:
        # 依存関係
        self.store = store
        self.llm = llm
        self.action_resolver = MemoryActionResolver(store=store, llm=llm)
        self.correction = MemoryCorrectionReconciler(
            store=store,
            action_resolver=self.action_resolver,
        )
        self.vector_indexer = MemoryVectorIndexer(store=store, llm=llm)
        self.reflective = ReflectiveConsolidator(
            store=store,
            llm=llm,
            action_resolver=self.action_resolver,
            vector_indexer=self.vector_indexer,
        )

    def consolidate_turn(
        self,
        *,
        state: dict[str, Any],
        cycle_id: str,
        finished_at: str,
        input_text: str,
        recall_hint: dict[str, Any],
        decision: dict[str, Any],
        speech_payload: dict[str, Any] | None,
        events: list[dict[str, Any]],
        memory_context: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        # モデル選択
        selected_preset = state["model_presets"][state["selected_model_preset_id"]]
        selected_memory_set_id = state["selected_memory_set_id"]
        embedding_definition = state["memory_sets"][selected_memory_set_id]["embedding"]
        selected_persona = state["personas"][state["selected_persona_id"]]

        # 訂正候補
        correction_prepared = self.correction.prepare(
            memory_set_id=selected_memory_set_id,
            cycle_id=cycle_id,
            finished_at=finished_at,
        )

        correction_targets = [
            self.correction.compact_target(target)
            for target in correction_prepared.get("targets", [])
            if isinstance(target, dict)
        ]

        # 解釈
        interpretation = self.llm.generate_memory_interpretation(
            model_config=selected_preset,
            persona_context=build_persona_context(
                selected_persona,
                role="memory_interpretation",
            ),
            input_text=input_text,
            recall_hint=recall_hint,
            decision=decision,
            speech_text=speech_payload["speech_text"] if speech_payload else None,
            memory_context=self._build_memory_interpretation_context(
                memory_context=memory_context,
                events=events,
            ),
            current_time=finished_at,
            correction_targets=correction_targets or None,
        )
        provenance = self._turn_provenance(events=events, memory_context=memory_context)

        # Episode要約
        event_ids = [event["event_id"] for event in events]
        episode = self._build_episode(
            cycle_id=cycle_id,
            memory_set_id=selected_memory_set_id,
            finished_at=finished_at,
            event_ids=event_ids,
            payload=interpretation["episode"],
            provenance=provenance,
        )

        # 記憶アクション群
        memory_actions: list[dict[str, Any]] = []
        for candidate_payload in interpretation["candidate_memory_units"]:
            candidate = deepcopy(candidate_payload)
            if provenance:
                candidate["qualifiers_hint"] = {
                    **candidate.get("qualifiers_hint", {}),
                    **provenance,
                }
            memory_actions.extend(
                self.action_resolver.resolve_memory_actions(
                    memory_set_id=selected_memory_set_id,
                    finished_at=finished_at,
                    event_ids=event_ids,
                    cycle_ids=[cycle_id],
                    candidate=candidate,
                    embedding_definition=embedding_definition,
                )
            )

        # episode affect群
        episode_affects = [
            self._build_episode_affect(
                memory_set_id=selected_memory_set_id,
                episode_id=episode["episode_id"],
                finished_at=finished_at,
                payload=episode_affect,
            )
            for episode_affect in interpretation["episode_affects"]
        ]

        # 永続化
        affect_persist_result = self.store.persist_turn_consolidation(
            episode=episode,
            memory_actions=memory_actions,
            episode_affects=episode_affects,
            recall_hint=recall_hint,
        )

        # 結果
        return (
            {
                "turn_consolidation_status": "succeeded",
                "episode_id": episode["episode_id"],
                "episode_summary": episode["summary_text"],
                "episode_series_id": episode.get("episode_series_id"),
                "open_loops": episode.get("open_loops", []),
                "memory_action_count": len(memory_actions),
                "correction_reconciliation": correction_prepared["trace"],
                "episode_affect_count": len(episode_affects),
                "updated_memory_unit_ids": [
                    action["memory_unit_id"]
                    for action in memory_actions
                    if action.get("memory_unit_id")
                ],
                "episode_affects": [
                    {
                        "target_scope_type": affect["target_scope_type"],
                        "target_scope_key": affect["target_scope_key"],
                        "affect_label": affect["affect_label"],
                        "vad": affect["vad"],
                        "intensity": affect["intensity"],
                        "confidence": affect["confidence"],
                    }
                    for affect in episode_affects
                ],
                "mood_state_update": affect_persist_result["mood_state_update"],
                "affect_state_updates": affect_persist_result["affect_state_updates"],
                "memory_link_update": affect_persist_result["memory_link_update"],
                "entity_registry_update": affect_persist_result["entity_registry_update"],
                "failure_reason": None,
                "vector_index_sync": {
                    "result_status": "queued",
                    "failure_reason": None,
                },
                "relation_index_sync": self._relation_index_sync_trace("queued"),
                "reflective_consolidation": {
                    "started": False,
                    "result_status": "queued",
                    "trigger_reasons": [],
                    "affected_memory_unit_ids": [],
                    "summary_generation": {
                        "requested_scope_count": 0,
                        "succeeded_scope_count": 0,
                        "failed_scopes": [],
                        "dirty_scope_count": 0,
                        "dirty_reasons": [],
                        "llm_call_count": 0,
                    },
                    "drive_state_update": {
                        "result_status": "queued",
                        "active_drive_ids": [],
                        "removed_drive_ids": [],
                        "drive_summaries": [],
                        "scope_supports": [],
                    },
                    "affect_state_update": {
                        "result_status": "queued",
                        "created_affect_state_ids": [],
                        "updated_affect_state_ids": [],
                        "weakened_affect_state_ids": [],
                        "pruned_affect_state_ids": [],
                        "affect_state_summaries": [],
                    },
                    "memory_link_update": {
                        "result_status": "queued",
                        "link_count": 0,
                        "labels": {},
                        "memory_link_ids": [],
                    },
                    "failure_reason": None,
                },
                "drive_state_update": {
                    "result_status": "queued",
                    "active_drive_ids": [],
                    "removed_drive_ids": [],
                    "drive_summaries": [],
                    "scope_supports": [],
                },
            },
            self._build_postprocess_job(
                state=state,
                cycle_id=cycle_id,
                finished_at=finished_at,
                episode=episode,
                memory_actions=memory_actions,
                correction_context=self._build_correction_job_context(
                    input_text=input_text,
                    speech_payload=speech_payload,
                    decision=decision,
                    event_ids=event_ids,
                    cycle_id=cycle_id,
                    prepared=correction_prepared,
                    interpretation=interpretation,
                ),
            ),
        )

    def resolve_autonomous_run_commitments(
        self,
        *,
        state: dict[str, Any],
        run: dict[str, Any],
        terminal_status: str,
        finished_at: str,
        evidence_event_ids: list[str],
    ) -> dict[str, Any]:
        # autonomous_run に紐づいた commitment を terminal 状態へ進める。
        target_state = "done" if terminal_status == "completed" else "cancelled" if terminal_status == "cancelled" else None
        if target_state is None:
            return self._autonomous_run_commitment_resolution_trace(
                result_status="skipped",
                reason="non_terminal_status",
            )

        memory_set_id = str(run.get("memory_set_id") or state.get("selected_memory_set_id") or "").strip()
        linked_ids = [
            value
            for value in run.get("source_commitment_memory_unit_ids", [])
            if isinstance(value, str) and value.strip()
        ]
        if not memory_set_id or not linked_ids:
            return self._autonomous_run_commitment_resolution_trace(
                result_status="skipped",
                reason="no_linked_commitments",
            )

        units = self.store.list_memory_units_by_id(
            memory_set_id=memory_set_id,
            memory_unit_ids=linked_ids,
        )
        actions: list[dict[str, Any]] = []
        updated_ids: list[str] = []
        for unit in units:
            if unit.get("memory_type") != "commitment":
                continue
            if unit.get("commitment_state") not in ACTIVE_COMMITMENT_STATES:
                continue
            after = deepcopy(unit)
            after["commitment_state"] = target_state
            after["last_confirmed_at"] = finished_at if target_state == "done" else after.get("last_confirmed_at")
            after["evidence_event_ids"] = merged_event_ids(after.get("evidence_event_ids", []), evidence_event_ids)
            qualifiers = dict(after.get("qualifiers", {}))
            run_ids = [
                value
                for value in qualifiers.get("autonomous_run_ids", [])
                if isinstance(value, str) and value.strip()
            ]
            run_id = run.get("run_id")
            if isinstance(run_id, str) and run_id and run_id not in run_ids:
                run_ids.append(run_id)
            if run_ids:
                qualifiers["autonomous_run_ids"] = run_ids
            qualifiers["autonomous_run_terminal_status"] = terminal_status
            after["qualifiers"] = qualifiers
            actions.append(
                self.action_resolver.build_memory_action(
                    operation="refine",
                    memory_set_id=memory_set_id,
                    finished_at=finished_at,
                    memory_unit=after,
                    related_memory_unit_ids=[],
                    before_snapshot=unit,
                    after_snapshot=after,
                    reason=f"linked autonomous_run が {terminal_status} になったため、対応する commitment を {target_state} に更新した。",
                    event_ids=evidence_event_ids,
                )
            )
            updated_ids.append(str(unit["memory_unit_id"]))

        if not actions:
            return self._autonomous_run_commitment_resolution_trace(
                result_status="skipped",
                reason="no_active_linked_commitments",
            )

        persist_result = self.store.persist_turn_consolidation(
            episode=None,
            memory_actions=actions,
            episode_affects=[],
        )
        vector_status = "succeeded"
        vector_failure_reason = None
        try:
            self.vector_indexer.sync(
                state=state,
                finished_at=finished_at,
                episode=None,
                memory_actions=actions,
            )
        except Exception as exc:  # noqa: BLE001
            vector_status = "failed"
            vector_failure_reason = str(exc)
        relation_index_sync = self._sync_relation_index(
            memory_set_id=memory_set_id,
            updated_at=finished_at,
        )

        return {
            "result_status": "updated",
            "reason": None,
            "terminal_status": terminal_status,
            "target_commitment_state": target_state,
            "updated_memory_unit_ids": updated_ids,
            "memory_action_count": len(actions),
            "memory_link_update": persist_result.get("memory_link_update"),
            "entity_registry_update": persist_result.get("entity_registry_update"),
            "vector_index_sync": {
                "result_status": vector_status,
                "failure_reason": vector_failure_reason,
            },
            "relation_index_sync": relation_index_sync,
        }

    def _autonomous_run_commitment_resolution_trace(
        self,
        *,
        result_status: str,
        reason: str,
    ) -> dict[str, Any]:
        return {
            "result_status": result_status,
            "reason": reason,
            "terminal_status": None,
            "target_commitment_state": None,
            "updated_memory_unit_ids": [],
            "memory_action_count": 0,
            "memory_link_update": None,
            "entity_registry_update": None,
            "vector_index_sync": {
                "result_status": "not_started",
                "failure_reason": None,
            },
            "relation_index_sync": self._relation_index_sync_trace("not_started"),
        }

    def run_postprocess_job(self, *, job: dict[str, Any]) -> dict[str, Any]:
        # job状態
        state_snapshot = job["state_snapshot"]
        finished_at = job["turn_finished_at"]
        episode = job["episode"]
        memory_actions = job["memory_actions"]
        correction_actions, correction_trace = self._run_correction_reconciliation(
            job=job,
            finished_at=finished_at,
        )
        if correction_actions:
            self.store.persist_turn_consolidation(
                episode=None,
                memory_actions=correction_actions,
                episode_affects=[],
            )
        all_memory_actions = [*memory_actions, *correction_actions]

        # ベクトル索引
        vector_index_sync = {
            "result_status": "succeeded",
            "failure_reason": None,
        }
        try:
            self.vector_indexer.sync(
                state=state_snapshot,
                finished_at=finished_at,
                episode=episode,
                memory_actions=all_memory_actions,
            )
        except Exception as exc:  # noqa: BLE001
            vector_index_sync = {
                "result_status": "failed",
                "failure_reason": str(exc),
            }

        # 内省統合
        if vector_index_sync["result_status"] == "succeeded":
            reflective_result = self.reflective.run(
                state=state_snapshot,
                finished_at=finished_at,
                episode=episode,
                memory_actions=all_memory_actions,
            )
        else:
            reflective_result = self._not_started_reflective_result()

        # 関係索引は先行する補助処理の成否にかかわらず、保存済み正本から再生成する。
        relation_index_sync = self._sync_relation_index(
            memory_set_id=state_snapshot["selected_memory_set_id"],
            updated_at=now_iso(),
        )

        # 結果
        return {
            "vector_index_sync": vector_index_sync,
            "relation_index_sync": relation_index_sync,
            "correction_reconciliation": correction_trace,
            "reflective_consolidation": reflective_result,
        }

    def _sync_relation_index(self, *, memory_set_id: str, updated_at: str) -> dict[str, Any]:
        # relation_index failure は正本保存を取り消さない。
        try:
            return self.store.rebuild_relation_index(
                memory_set_id=memory_set_id,
                updated_at=updated_at,
            )
        except Exception as exc:  # noqa: BLE001
            return self._relation_index_sync_trace("failed", failure_reason=str(exc))

    def _relation_index_sync_trace(
        self,
        result_status: str,
        *,
        failure_reason: str | None = None,
    ) -> dict[str, Any]:
        return {
            "result_status": result_status,
            "edge_count": 0,
            "status_counts": {"active": 0, "weak": 0, "inactive": 0},
            "skipped_multi_party_count": 0,
            "skipped_invalid_count": 0,
            "failure_reason": failure_reason,
        }

    def _not_started_reflective_result(self) -> dict[str, Any]:
        return {
            "started": False,
            "result_status": "not_started",
            "trigger_reasons": [],
            "affected_memory_unit_ids": [],
            "summary_generation": {
                "requested_scope_count": 0,
                "succeeded_scope_count": 0,
                "failed_scopes": [],
                "dirty_scope_count": 0,
                "dirty_reasons": [],
                "llm_call_count": 0,
            },
            "drive_state_update": {
                "result_status": "not_started",
                "active_drive_ids": [],
                "removed_drive_ids": [],
                "drive_summaries": [],
                "scope_supports": [],
            },
            "affect_state_update": {
                "result_status": "not_started",
                "created_affect_state_ids": [],
                "updated_affect_state_ids": [],
                "weakened_affect_state_ids": [],
                "pruned_affect_state_ids": [],
                "affect_state_summaries": [],
            },
            "memory_link_update": {
                "result_status": "not_started",
                "link_count": 0,
                "labels": {},
                "memory_link_ids": [],
            },
            "failure_reason": None,
        }

    def _run_correction_reconciliation(
        self,
        *,
        job: dict[str, Any],
        finished_at: str,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        # correction jobなし
        correction_context = job.get("correction_reconciliation")
        if not isinstance(correction_context, dict):
            return [], self.correction.skipped_trace(reason="no_context")

        # 実行
        try:
            return self.correction.run(
                context=correction_context,
                finished_at=finished_at,
            )
        except Exception as exc:  # noqa: BLE001
            targets = correction_context.get("targets", [])
            return [], {
                "result_status": "failed",
                "selection_status": "failed",
                "target_candidate_count": len(targets) if isinstance(targets, list) else 0,
                "selected_target_count": 0,
                "selected_revision_ids": [],
                "correction_group_ids": [],
                "action_count": 0,
                "operation_counts": {},
                "actions": [],
                "failure_reason": str(exc),
            }

    def _build_postprocess_job(
        self,
        *,
        state: dict[str, Any],
        cycle_id: str,
        finished_at: str,
        episode: dict[str, Any],
        memory_actions: list[dict[str, Any]],
        correction_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        selected_memory_set_id = state["selected_memory_set_id"]
        selected_model_preset_id = state["selected_model_preset_id"]
        selected_persona_id = state["selected_persona_id"]
        selected_model_preset = state["model_presets"][selected_model_preset_id]
        return {
            "cycle_id": cycle_id,
            "memory_set_id": selected_memory_set_id,
            "queued_at": finished_at,
            "started_at": None,
            "finished_at": None,
            "result_status": "queued",
            "turn_finished_at": finished_at,
            "state_snapshot": {
                "selected_persona_id": selected_persona_id,
                "selected_memory_set_id": selected_memory_set_id,
                "selected_model_preset_id": selected_model_preset_id,
                "personas": {
                    selected_persona_id: deepcopy(state["personas"][selected_persona_id]),
                },
                "memory_sets": {
                    selected_memory_set_id: {
                        "embedding": deepcopy(
                            state["memory_sets"][selected_memory_set_id]["embedding"]
                        )
                    }
                },
                "model_presets": {
                    selected_model_preset_id: deepcopy(selected_model_preset),
                },
            },
            "episode": deepcopy(episode),
            "memory_actions": deepcopy(memory_actions),
            "correction_reconciliation": deepcopy(correction_context),
        }

    def _build_memory_interpretation_context(
        self,
        *,
        memory_context: dict[str, Any] | None,
        events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload = dict(memory_context) if isinstance(memory_context, dict) else {}
        compact_events = [self._compact_event_for_memory_context(event) for event in events]
        compact_events = [event for event in compact_events if event]
        if compact_events:
            limited_events = self._limit_memory_context_events(compact_events)
            payload["events"] = limited_events
            if len(limited_events) < len(compact_events):
                payload["events_truncated"] = {
                    "original_count": len(compact_events),
                    "included_count": len(limited_events),
                    "event_limit": MEMORY_CONTEXT_EVENT_LIMIT,
                }
        return payload

    def _build_correction_job_context(
        self,
        *,
        input_text: str,
        speech_payload: dict[str, Any] | None,
        decision: dict[str, Any],
        event_ids: list[str],
        cycle_id: str,
        prepared: dict[str, Any],
        interpretation: dict[str, Any],
    ) -> dict[str, Any] | None:
        # 候補なし
        targets = prepared.get("targets", [])
        if not isinstance(targets, list) or not targets:
            return None

        # job context
        return {
            "input_text": input_text,
            "speech_text": speech_payload.get("speech_text") if isinstance(speech_payload, dict) else None,
            "decision_summary": self._memory_decision_summary(decision),
            "event_ids": event_ids,
            "cycle_ids": [cycle_id],
            "targets": targets,
            "selection": self._memory_interpretation_correction_selection(interpretation),
        }

    def _memory_interpretation_correction_selection(
        self,
        interpretation: dict[str, Any],
    ) -> dict[str, Any] | None:
        selection = {
            "correction_status": interpretation.get("correction_status"),
            "selected_targets": interpretation.get("selected_targets"),
        }
        try:
            from otomekairo.llm.contracts import validate_memory_correction_reconciliation_contract

            validate_memory_correction_reconciliation_contract(selection)
        except Exception:  # noqa: BLE001
            return None
        return selection

    def _memory_decision_summary(self, decision: dict[str, Any]) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "reason_summary": decision.get("reason_summary"),
        }
        separated = decision.get("separated_comparisons")
        if isinstance(separated, dict):
            compact: dict[str, Any] = {}
            for key in ("self_activity", "outward_speech"):
                item = separated.get(key)
                if not isinstance(item, dict):
                    continue
                entry: dict[str, Any] = {}
                kind = item.get("kind")
                if isinstance(kind, str) and kind.strip():
                    entry["kind"] = kind.strip()
                reason = item.get("reason_summary")
                if isinstance(reason, str) and reason.strip():
                    entry["reason_summary"] = reason.strip()
                if entry:
                    compact[key] = entry
            if compact:
                summary["separated_comparisons"] = compact
                return summary
        kind = decision.get("kind")
        if isinstance(kind, str) and kind.strip():
            summary["kind"] = kind.strip()
        return summary

    def _limit_memory_context_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(events) > MEMORY_CONTEXT_EVENT_LIMIT:
            return [events[0], *events[-(MEMORY_CONTEXT_EVENT_LIMIT - 1) :]]
        return events

    def _compact_event_for_memory_context(self, event: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for key in (
            "kind",
            "role",
            "result_kind",
            "external_result_kind",
            "reason_code",
            "reason_summary",
            "pending_intent_summary",
            "interaction_ref",
            "speaker_ref",
            "participant_refs",
        ):
            value = event.get(key)
            if value is None:
                continue
            if isinstance(value, str):
                normalized = value.strip()
                if not normalized:
                    continue
                payload[key] = normalized
                continue
            if isinstance(value, (int, float, bool, list, dict)):
                payload[key] = value
        text = event.get("text")
        if isinstance(text, str) and text.strip():
            payload["text_summary"] = text.strip()
        return payload

    def _turn_provenance(
        self,
        *,
        events: list[dict[str, Any]],
        memory_context: dict[str, Any] | None,
    ) -> dict[str, list[str]]:
        # 開示判定で追跡できるよう、会話と人物の出所を構造化して保持する。
        interaction_refs: list[str] = []
        participant_refs: list[str] = []
        for event in events:
            interaction_ref = event.get("interaction_ref")
            if isinstance(interaction_ref, str) and interaction_ref and interaction_ref not in interaction_refs:
                interaction_refs.append(interaction_ref)
            raw_participant_refs = event.get("participant_refs")
            for person_ref in raw_participant_refs if isinstance(raw_participant_refs, list) else []:
                if (
                    isinstance(person_ref, str)
                    and person_ref.startswith("person:")
                    and person_ref not in participant_refs
                ):
                    participant_refs.append(person_ref)
        current_input = memory_context.get("current_input") if isinstance(memory_context, dict) else None
        if isinstance(current_input, dict):
            interaction_context = current_input.get("interaction_context")
            interaction_ref = (
                interaction_context.get("interaction_ref")
                if isinstance(interaction_context, dict)
                else None
            )
            if isinstance(interaction_ref, str) and interaction_ref and interaction_ref not in interaction_refs:
                interaction_refs.append(interaction_ref)
            raw_participant_refs = (
                interaction_context.get("participants")
                if isinstance(interaction_context, dict)
                else None
            )
            for participant in raw_participant_refs if isinstance(raw_participant_refs, list) else []:
                person_ref = participant.get("person_ref") if isinstance(participant, dict) else None
                if (
                    isinstance(person_ref, str)
                    and person_ref.startswith("person:")
                    and person_ref not in participant_refs
                ):
                    participant_refs.append(person_ref)
        provenance: dict[str, list[str]] = {}
        if interaction_refs:
            provenance["source_interaction_refs"] = interaction_refs
        if participant_refs:
            provenance["source_participant_refs"] = participant_refs
        return provenance

    def _build_episode(
        self,
        *,
        cycle_id: str,
        memory_set_id: str,
        finished_at: str,
        event_ids: list[str],
        payload: dict[str, Any],
        provenance: dict[str, list[str]],
    ) -> dict[str, Any]:
        # 正規化
        open_loops = normalized_text_list(payload.get("open_loops", []), limit=4)
        episode_series_id = self._resolve_episode_series_id(
            memory_set_id=memory_set_id,
            payload=payload,
            open_loops=open_loops,
        )

        # 記録
        record = {
            "episode_id": f"episode:{uuid.uuid4().hex}",
            "cycle_id": cycle_id,
            "memory_set_id": memory_set_id,
            "episode_type": payload["episode_type"],
            "episode_series_id": episode_series_id,
            "primary_scope_type": payload["primary_scope_type"],
            "primary_scope_key": payload["primary_scope_key"],
            "summary_text": payload["summary_text"].strip(),
            "outcome_text": optional_text(payload.get("outcome_text")),
            "open_loops": open_loops,
            "salience": clamp_score(payload["salience"]),
            "formed_at": finished_at,
            "linked_event_ids": event_ids,
        }
        record.update(provenance)
        return record

    def _resolve_episode_series_id(
        self,
        *,
        memory_set_id: str,
        payload: dict[str, Any],
        open_loops: list[str],
    ) -> str | None:
        # 明示指定
        explicit_series_id = optional_text(payload.get("episode_series_id"))
        if explicit_series_id is not None:
            return explicit_series_id

        # 候補検索
        recent_episodes = self.store.list_recent_episodes_for_series(
            memory_set_id=memory_set_id,
            primary_scope_type=payload["primary_scope_type"],
            primary_scope_key=payload["primary_scope_key"],
            limit=6,
        )
        if not recent_episodes:
            return None

        # open loop の継続を優先する。
        if open_loops:
            requested = {value for value in open_loops if isinstance(value, str)}
            for episode in recent_episodes:
                candidate_loops = {
                    value
                    for value in episode.get("open_loops", [])
                    if isinstance(value, str)
                }
                if requested & candidate_loops:
                    return episode.get("episode_series_id") or episode["episode_id"]

        # 明示 continuation 系だけ、最近の open loop episode を継続扱いにする。
        if payload["episode_type"] in {"commitment_followup", "action_result", "task_progress", "follow_up"}:
            for episode in recent_episodes:
                if episode.get("open_loops"):
                    return episode.get("episode_series_id") or episode["episode_id"]

        return None

    def _build_episode_affect(
        self,
        *,
        memory_set_id: str,
        episode_id: str,
        finished_at: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        # 記録
        return {
            "episode_affect_id": f"episode_affect:{uuid.uuid4().hex}",
            "memory_set_id": memory_set_id,
            "episode_id": episode_id,
            "target_scope_type": payload["target_scope_type"],
            "target_scope_key": payload["target_scope_key"],
            "affect_label": payload["affect_label"],
            "summary_text": optional_text(payload.get("summary_text")) or payload["affect_label"],
            "vad": self._build_vad(payload.get("vad")),
            "intensity": clamp_score(payload["intensity"]),
            "confidence": clamp_score(payload["confidence"]),
            "observed_at": finished_at,
            "created_at": finished_at,
        }

    def _build_vad(self, payload: Any) -> dict[str, float]:
        # 正規化
        if not isinstance(payload, dict):
            return {"v": 0.0, "a": 0.0, "d": 0.0}

        # 結果
        return {
            "v": self._clamp_vad_axis(payload.get("v")),
            "a": self._clamp_vad_axis(payload.get("a")),
            "d": self._clamp_vad_axis(payload.get("d")),
        }

    def _clamp_vad_axis(self, value: Any) -> float:
        # 正規化
        if not isinstance(value, (int, float)):
            return 0.0
        return max(-1.0, min(float(value), 1.0))
