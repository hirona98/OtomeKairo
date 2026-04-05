from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
import hashlib
import uuid
from typing import Any

from otomekairo.llm import LLMClient
from otomekairo.store import FileStore


# Block: Constants
ACTIVE_MEMORY_STATUSES = ("inferred", "confirmed")
REFLECTION_TRIGGER_CYCLE_INTERVAL = 8
REFLECTION_TRIGGER_HOURS = 24
REFLECTION_HIGH_SALIENCE_THRESHOLD = 0.8
REFLECTION_HIGH_SALIENCE_COUNT = 3
REFLECTION_DIGEST_LIMIT = 24
REFLECTION_MEMORY_LIMIT = 96
REFLECTION_MIN_SUMMARY_EVIDENCE = 2
REFLECTION_TOPIC_DORMANT_AFTER_DAYS = 14
REFLECTION_CONFIRMED_TOPIC_DORMANT_AFTER_DAYS = 30


# Block: Consolidator
class MemoryConsolidator:
    def __init__(self, *, store: FileStore, llm: LLMClient) -> None:
        # Block: Dependencies
        self.store = store
        self.llm = llm

    def consolidate_turn(
        self,
        *,
        state: dict[str, Any],
        cycle_id: str,
        finished_at: str,
        observation_text: str,
        recall_hint: dict[str, Any],
        decision: dict[str, Any],
        reply_payload: dict[str, Any] | None,
        events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        # Block: MemoryToggle
        if not state.get("memory_enabled", True):
            return {
                "episode_digest_id": None,
                "memory_action_count": 0,
                "affect_update_count": 0,
            }

        # Block: ModelSelection
        selected_preset = state["model_presets"][state["selected_model_preset_id"]]
        memory_role = selected_preset["roles"]["memory_interpretation"]
        memory_profile_id = memory_role["model_profile_id"]
        memory_profile = state["model_profiles"][memory_profile_id]

        # Block: Interpretation
        interpretation = self.llm.generate_memory_interpretation(
            profile=memory_profile,
            role_settings=memory_role,
            observation_text=observation_text,
            recall_hint=recall_hint,
            decision=decision,
            reply_text=reply_payload["reply_text"] if reply_payload else None,
            current_time=finished_at,
        )

        # Block: EpisodeDigest
        selected_memory_set_id = state["selected_memory_set_id"]
        event_ids = [event["event_id"] for event in events]
        episode_digest = self._build_episode_digest(
            cycle_id=cycle_id,
            memory_set_id=selected_memory_set_id,
            finished_at=finished_at,
            event_ids=event_ids,
            payload=interpretation["episode_digest"],
        )

        # Block: MemoryActions
        memory_actions: list[dict[str, Any]] = []
        for candidate in interpretation["candidate_memory_units"]:
            memory_actions.extend(
                self._resolve_memory_actions(
                    memory_set_id=selected_memory_set_id,
                    finished_at=finished_at,
                    event_ids=event_ids,
                    candidate=candidate,
                )
            )

        # Block: AffectUpdates
        affect_updates = [
            self._build_affect_update(
                memory_set_id=selected_memory_set_id,
                finished_at=finished_at,
                payload=affect_update,
            )
            for affect_update in interpretation["affect_updates"]
        ]

        # Block: Persistence
        self.store.persist_turn_consolidation(
            episode_digest=episode_digest,
            memory_actions=memory_actions,
            affect_updates=affect_updates,
        )

        # Block: VectorIndex
        self._sync_vector_index(
            state=state,
            finished_at=finished_at,
            episode_digest=episode_digest,
            memory_actions=memory_actions,
        )

        # Block: ReflectiveConsolidation
        reflective_result = {
            "started": False,
            "result_status": "not_triggered",
            "trigger_reasons": [],
            "affected_memory_unit_ids": [],
        }
        try:
            reflective_result = self._maybe_run_reflective_consolidation(
                state=state,
                finished_at=finished_at,
                episode_digest=episode_digest,
                memory_actions=memory_actions,
            )
        except Exception:
            reflective_result = {
                "started": False,
                "result_status": "failed",
                "trigger_reasons": [],
                "affected_memory_unit_ids": [],
            }

        # Block: Result
        return {
            "episode_digest_id": episode_digest["episode_digest_id"],
            "memory_action_count": len(memory_actions),
            "affect_update_count": len(affect_updates),
            "reflective_consolidation": reflective_result,
        }

    def _sync_vector_index(
        self,
        *,
        state: dict[str, Any],
        finished_at: str,
        episode_digest: dict[str, Any] | None,
        memory_actions: list[dict[str, Any]],
    ) -> None:
        # Block: EmbeddingRole
        selected_preset = state["model_presets"][state["selected_model_preset_id"]]
        embedding_role = selected_preset["roles"]["embedding"]
        embedding_profile_id = embedding_role["model_profile_id"]
        embedding_profile = state["model_profiles"][embedding_profile_id]
        embedding_dimension = embedding_role["embedding_dimension"]
        embedding_preset = self._embedding_preset(embedding_profile_id, embedding_dimension)

        # Block: Sources
        entries = self._build_vector_index_entries(
            finished_at=finished_at,
            embedding_preset=embedding_preset,
            episode_digest=episode_digest,
            memory_actions=memory_actions,
        )
        if not entries:
            return

        # Block: Embeddings
        embeddings = self.llm.generate_embeddings(
            profile=embedding_profile,
            role_settings=embedding_role,
            texts=[entry["source_text"] for entry in entries],
        )

        # Block: Payloads
        payloads = [
            {
                **entry,
                "embedding": embedding,
            }
            for entry, embedding in zip(entries, embeddings, strict=True)
        ]

        # Block: Persist
        self.store.upsert_vector_index_entries(
            entries=payloads,
            embedding_dimension=embedding_dimension,
        )

    def _build_vector_index_entries(
        self,
        *,
        finished_at: str,
        embedding_preset: str,
        episode_digest: dict[str, Any] | None,
        memory_actions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        # Block: State
        entries: list[dict[str, Any]] = []
        seen_source_ids: set[tuple[str, str]] = set()

        # Block: EpisodeDigest
        if episode_digest is not None:
            episode_entry = self._vector_entry_for_episode_digest(
                finished_at=finished_at,
                embedding_preset=embedding_preset,
                record=episode_digest,
            )
            if episode_entry is not None:
                entries.append(episode_entry)
                seen_source_ids.add(("episode_digest", episode_digest["episode_digest_id"]))

        # Block: MemoryUnits
        for action in memory_actions:
            memory_unit = action.get("memory_unit")
            if not isinstance(memory_unit, dict):
                continue
            source_key = ("memory_unit", memory_unit["memory_unit_id"])
            if source_key in seen_source_ids:
                continue
            memory_entry = self._vector_entry_for_memory_unit(
                finished_at=finished_at,
                embedding_preset=embedding_preset,
                record=memory_unit,
            )
            if memory_entry is None:
                continue
            entries.append(memory_entry)
            seen_source_ids.add(source_key)

        # Block: Result
        return entries

    def _vector_entry_for_episode_digest(
        self,
        *,
        finished_at: str,
        embedding_preset: str,
        record: dict[str, Any],
    ) -> dict[str, Any] | None:
        # Block: SourceText
        source_text = self._episode_digest_source_text(record)
        if not source_text:
            return None

        # Block: Entry
        return {
            "memory_set_id": record["memory_set_id"],
            "source_kind": "episode_digest",
            "source_id": record["episode_digest_id"],
            "embedding_preset": embedding_preset,
            "source_text": source_text,
            "scope_type": record["primary_scope_type"],
            "scope_key": record["primary_scope_key"],
            "source_type": record["episode_type"],
            "status": "active",
            "salience": record["salience"],
            "has_open_loops": bool(record.get("open_loops")),
            "updated_at": finished_at,
            "text_hash": self._text_hash(source_text),
        }

    def _vector_entry_for_memory_unit(
        self,
        *,
        finished_at: str,
        embedding_preset: str,
        record: dict[str, Any],
    ) -> dict[str, Any] | None:
        # Block: SourceText
        source_text = record.get("summary_text", "").strip()
        if not source_text:
            return None

        # Block: Entry
        return {
            "memory_set_id": record["memory_set_id"],
            "source_kind": "memory_unit",
            "source_id": record["memory_unit_id"],
            "embedding_preset": embedding_preset,
            "source_text": source_text,
            "scope_type": record["scope_type"],
            "scope_key": record["scope_key"],
            "source_type": record["memory_type"],
            "status": record["status"],
            "salience": record["salience"],
            "has_open_loops": False,
            "updated_at": finished_at,
            "text_hash": self._text_hash(source_text),
        }

    def _episode_digest_source_text(self, record: dict[str, Any]) -> str:
        # Block: Parts
        parts: list[str] = [record.get("summary_text", "").strip()]
        outcome_text = record.get("outcome_text")
        if isinstance(outcome_text, str) and outcome_text.strip():
            parts.append(outcome_text.strip())
        parts.extend(self._normalized_text_list(record.get("open_loops", []), limit=4))

        # Block: Result
        return "\n".join(part for part in parts if part)

    def _embedding_preset(self, embedding_profile_id: str, embedding_dimension: int) -> str:
        # Block: Identifier
        return f"{embedding_profile_id}:dim{embedding_dimension}"

    def _text_hash(self, value: str) -> str:
        # Block: Hash
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _build_episode_digest(
        self,
        *,
        cycle_id: str,
        memory_set_id: str,
        finished_at: str,
        event_ids: list[str],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        # Block: Record
        return {
            "episode_digest_id": f"episode_digest:{uuid.uuid4().hex}",
            "cycle_id": cycle_id,
            "memory_set_id": memory_set_id,
            "episode_type": payload["episode_type"],
            "primary_scope_type": payload["primary_scope_type"],
            "primary_scope_key": payload["primary_scope_key"],
            "summary_text": payload["summary_text"].strip(),
            "outcome_text": self._optional_text(payload.get("outcome_text")),
            "open_loops": self._normalized_text_list(payload.get("open_loops", []), limit=4),
            "salience": self._clamp_score(payload["salience"]),
            "formed_at": finished_at,
            "linked_event_ids": event_ids,
        }

    def _maybe_run_reflective_consolidation(
        self,
        *,
        state: dict[str, Any],
        finished_at: str,
        episode_digest: dict[str, Any],
        memory_actions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        # Block: TriggerCheck
        memory_set_id = state["selected_memory_set_id"]
        latest_run = self.store.get_latest_reflection_run(memory_set_id)
        trigger_reasons = self._reflective_trigger_reasons(
            memory_set_id=memory_set_id,
            finished_at=finished_at,
            latest_run=latest_run,
            episode_digest=episode_digest,
            memory_actions=memory_actions,
        )
        if not trigger_reasons:
            return {
                "started": False,
                "result_status": "not_triggered",
                "trigger_reasons": [],
                "affected_memory_unit_ids": [],
            }

        # Block: InputCollection
        started_at = self._now_iso()
        since_iso = latest_run["finished_at"] if isinstance(latest_run, dict) else None
        digests = self.store.list_episode_digests_for_reflection(
            memory_set_id=memory_set_id,
            since_iso=since_iso,
            limit=REFLECTION_DIGEST_LIMIT,
        )
        active_units = self.store.list_memory_units_for_reflection(
            memory_set_id=memory_set_id,
            statuses=list(ACTIVE_MEMORY_STATUSES),
            scope_types=["self", "user", "relationship", "topic"],
            limit=REFLECTION_MEMORY_LIMIT,
        )

        # Block: ActionBuild
        reflection_actions: list[dict[str, Any]] = []
        reflection_actions.extend(
            self._build_reflective_summary_actions(
                memory_set_id=memory_set_id,
                finished_at=finished_at,
                digests=digests,
                active_units=active_units,
            )
        )
        reflection_actions.extend(
            self._build_reflective_confirmation_actions(
                memory_set_id=memory_set_id,
                finished_at=finished_at,
                digests=digests,
                active_units=active_units,
            )
        )
        reflection_actions.extend(
            self._build_reflective_dormant_actions(
                memory_set_id=memory_set_id,
                finished_at=finished_at,
                digests=digests,
                active_units=active_units,
                excluded_memory_unit_ids={
                    action["memory_unit_id"]
                    for action in reflection_actions
                },
            )
        )

        # Block: Persistence
        finished_reflection_at = self._now_iso()
        affected_memory_unit_ids = self._unique_memory_unit_ids(reflection_actions)
        reflection_run = {
            "reflection_run_id": f"reflection_run:{uuid.uuid4().hex}",
            "memory_set_id": memory_set_id,
            "started_at": started_at,
            "finished_at": finished_reflection_at,
            "result_status": "updated" if reflection_actions else "no_change",
            "trigger_reasons": trigger_reasons,
            "source_episode_digest_ids": [digest["episode_digest_id"] for digest in digests],
            "affected_memory_unit_ids": affected_memory_unit_ids,
            "action_counts": self._action_counts(reflection_actions),
        }
        self.store.persist_reflection_run(
            reflection_run=reflection_run,
            memory_actions=reflection_actions,
        )

        # Block: VectorIndex
        self._sync_vector_index(
            state=state,
            finished_at=finished_reflection_at,
            episode_digest=None,
            memory_actions=reflection_actions,
        )

        # Block: Result
        return {
            "started": True,
            "result_status": reflection_run["result_status"],
            "trigger_reasons": trigger_reasons,
            "affected_memory_unit_ids": affected_memory_unit_ids,
        }

    def _reflective_trigger_reasons(
        self,
        *,
        memory_set_id: str,
        finished_at: str,
        latest_run: dict[str, Any] | None,
        episode_digest: dict[str, Any],
        memory_actions: list[dict[str, Any]],
    ) -> list[str]:
        # Block: Since
        since_iso = latest_run["finished_at"] if isinstance(latest_run, dict) else None
        reasons: list[str] = []

        # Block: CycleInterval
        cycle_count = self.store.count_cycle_summaries_since(
            memory_set_id=memory_set_id,
            since_iso=since_iso,
        )
        if cycle_count >= REFLECTION_TRIGGER_CYCLE_INTERVAL:
            reasons.append("chat_turn_interval")

        # Block: ElapsedTime
        if isinstance(since_iso, str) and self._hours_since(since_iso, finished_at) >= REFLECTION_TRIGGER_HOURS:
            reasons.append("elapsed_24h")

        # Block: HighSalience
        high_salience_count = self.store.count_high_salience_episode_digests_since(
            memory_set_id=memory_set_id,
            since_iso=since_iso,
            salience_threshold=REFLECTION_HIGH_SALIENCE_THRESHOLD,
        )
        if high_salience_count >= REFLECTION_HIGH_SALIENCE_COUNT:
            reasons.append("high_salience_cluster")

        # Block: CorrectionSignal
        if any(action["operation"] in {"supersede", "revoke"} for action in memory_actions):
            reasons.append("explicit_correction")

        # Block: RelationshipSignal
        relationship_signal = episode_digest["primary_scope_type"] == "relationship" and episode_digest["salience"] >= 0.65
        if not relationship_signal:
            relationship_signal = any(
                isinstance(action.get("memory_unit"), dict) and action["memory_unit"].get("scope_type") == "relationship"
                for action in memory_actions
            )
        if relationship_signal:
            reasons.append("relationship_change")

        # Block: Result
        deduped: list[str] = []
        for reason in reasons:
            if reason not in deduped:
                deduped.append(reason)
        return deduped

    def _build_reflective_summary_actions(
        self,
        *,
        memory_set_id: str,
        finished_at: str,
        digests: list[dict[str, Any]],
        active_units: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        # Block: Grouping
        digest_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        memory_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for digest in digests:
            scope_type = digest.get("primary_scope_type")
            scope_key = digest.get("primary_scope_key")
            if scope_type not in {"self", "user", "relationship", "topic"}:
                continue
            if not isinstance(scope_key, str) or not scope_key:
                continue
            digest_groups[(scope_type, scope_key)].append(digest)
        for unit in active_units:
            scope_type = unit.get("scope_type")
            scope_key = unit.get("scope_key")
            if scope_type not in {"self", "user", "relationship", "topic"}:
                continue
            if not isinstance(scope_key, str) or not scope_key:
                continue
            if unit.get("memory_type") in {"summary", "commitment"}:
                continue
            memory_groups[(scope_type, scope_key)].append(unit)

        # Block: ScopeScan
        actions: list[dict[str, Any]] = []
        scope_keys = sorted(set(digest_groups.keys()) | set(memory_groups.keys()))
        for scope_type, scope_key in scope_keys:
            scope_digests = digest_groups.get((scope_type, scope_key), [])
            scope_units = memory_groups.get((scope_type, scope_key), [])
            if not self._should_build_reflective_summary(
                scope_type=scope_type,
                scope_digests=scope_digests,
                scope_units=scope_units,
            ):
                continue

            candidate = self._build_reflective_summary_candidate(
                scope_type=scope_type,
                scope_key=scope_key,
                scope_digests=scope_digests,
                scope_units=scope_units,
            )
            evidence_event_ids = self._reflective_event_ids(
                scope_digests=scope_digests,
                scope_units=scope_units,
                limit=12,
            )
            actions.extend(
                self._resolve_memory_actions(
                    memory_set_id=memory_set_id,
                    finished_at=finished_at,
                    event_ids=evidence_event_ids,
                    candidate=candidate,
                )
            )

        # Block: Result
        return actions

    def _should_build_reflective_summary(
        self,
        *,
        scope_type: str,
        scope_digests: list[dict[str, Any]],
        scope_units: list[dict[str, Any]],
    ) -> bool:
        # Block: EvidenceCount
        evidence_count = len(scope_digests) + len(scope_units)
        if evidence_count < REFLECTION_MIN_SUMMARY_EVIDENCE:
            return False

        # Block: TopicGuard
        if scope_type == "topic":
            if len(scope_units) >= 2:
                return True
            return any(digest.get("open_loops") for digest in scope_digests)

        # Block: Result
        return True

    def _build_reflective_summary_candidate(
        self,
        *,
        scope_type: str,
        scope_key: str,
        scope_digests: list[dict[str, Any]],
        scope_units: list[dict[str, Any]],
    ) -> dict[str, Any]:
        # Block: Evidence
        memory_types = self._dominant_memory_types(scope_units)
        evidence_count = len(scope_digests) + len(scope_units)
        open_loop_count = sum(1 for digest in scope_digests if digest.get("open_loops"))

        # Block: Candidate
        return {
            "memory_type": "summary",
            "scope_type": scope_type,
            "scope_key": scope_key,
            "subject_ref": self._summary_subject_ref(scope_type, scope_key),
            "predicate": "long_term_pattern",
            "object_ref_or_value": f"{scope_type}:{scope_key}:summary",
            "summary_text": self._reflective_summary_text(
                scope_type=scope_type,
                scope_key=scope_key,
                memory_types=memory_types,
                open_loop_count=open_loop_count,
            ),
            "status": "confirmed",
            "commitment_state": None,
            "confidence": min(0.86, 0.62 + (0.05 * min(evidence_count, 4)) + (0.04 if open_loop_count > 0 else 0.0)),
            "salience": self._reflective_summary_salience(
                scope_type=scope_type,
                evidence_count=evidence_count,
                open_loop_count=open_loop_count,
            ),
            "valid_from": None,
            "valid_to": None,
            "qualifiers": {
                "summary_scope": scope_type,
                "source_memory_types": memory_types,
                "evidence_digest_count": len(scope_digests),
                "evidence_memory_count": len(scope_units),
                "open_loop_count": open_loop_count,
            },
            "reason": "reflective consolidation で複数の記憶から長期傾向を要約したため。",
        }

    def _build_reflective_confirmation_actions(
        self,
        *,
        memory_set_id: str,
        finished_at: str,
        digests: list[dict[str, Any]],
        active_units: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        # Block: ScopeDigestCounts
        scope_digest_counts = Counter(
            (digest.get("primary_scope_type"), digest.get("primary_scope_key"))
            for digest in digests
            if isinstance(digest.get("primary_scope_key"), str)
        )

        # Block: Selection
        actions: list[dict[str, Any]] = []
        for unit in active_units:
            if unit.get("status") != "inferred":
                continue
            if unit.get("memory_type") == "summary":
                continue

            event_count = len(unit.get("evidence_event_ids", []))
            scope_key = (unit.get("scope_type"), unit.get("scope_key"))
            scope_support = scope_digest_counts.get(scope_key, 0)
            if not (
                event_count >= 3
                or (event_count >= 2 and float(unit.get("confidence", 0.0)) >= 0.7)
                or scope_support >= 2
            ):
                continue

            updated_unit = {
                **unit,
                "status": "confirmed",
                "confidence": max(self._clamp_score(unit["confidence"]), 0.78),
                "salience": max(self._clamp_score(unit["salience"]), 0.55),
                "last_confirmed_at": finished_at,
            }
            evidence_event_ids = self._merged_event_ids(
                unit.get("evidence_event_ids", []),
                self._scope_digest_event_ids(
                    digests=digests,
                    scope_type=unit["scope_type"],
                    scope_key=unit["scope_key"],
                ),
            )
            actions.append(
                self._build_memory_action(
                    operation="reinforce",
                    memory_set_id=memory_set_id,
                    finished_at=finished_at,
                    memory_unit=updated_unit,
                    related_memory_unit_ids=[],
                    before_snapshot=unit,
                    after_snapshot=updated_unit,
                    reason="reflective consolidation で反復支持を確認し、inferred を confirmed へ引き上げたため。",
                    event_ids=evidence_event_ids,
                )
            )

        # Block: Result
        return actions

    def _build_reflective_dormant_actions(
        self,
        *,
        memory_set_id: str,
        finished_at: str,
        digests: list[dict[str, Any]],
        active_units: list[dict[str, Any]],
        excluded_memory_unit_ids: set[str],
    ) -> list[dict[str, Any]]:
        # Block: RecentTopicScopes
        recent_topic_scopes = {
            (digest.get("primary_scope_type"), digest.get("primary_scope_key"))
            for digest in digests
            if digest.get("primary_scope_type") == "topic" and isinstance(digest.get("primary_scope_key"), str)
        }

        # Block: OrderedUnits
        ordered_units = sorted(
            active_units,
            key=lambda unit: (
                self._timestamp_sort_key(unit.get("last_confirmed_at") or unit.get("formed_at")),
                float(unit.get("salience", 0.0)),
            ),
        )

        # Block: Selection
        actions: list[dict[str, Any]] = []
        for unit in ordered_units:
            if unit["memory_unit_id"] in excluded_memory_unit_ids:
                continue
            if unit.get("scope_type") != "topic":
                continue
            if unit.get("memory_type") == "commitment":
                continue
            if (unit.get("scope_type"), unit.get("scope_key")) in recent_topic_scopes:
                continue

            dormant_after_days = (
                REFLECTION_CONFIRMED_TOPIC_DORMANT_AFTER_DAYS
                if unit.get("status") == "confirmed"
                else REFLECTION_TOPIC_DORMANT_AFTER_DAYS
            )
            salience_threshold = 0.25 if unit.get("status") == "confirmed" else 0.4
            if float(unit.get("salience", 0.0)) > salience_threshold:
                continue
            if self._days_since(unit.get("last_confirmed_at") or unit.get("formed_at"), finished_at) < dormant_after_days:
                continue

            updated_unit = {
                **unit,
                "status": "dormant",
                "salience": min(self._clamp_score(unit["salience"]), 0.15),
            }
            actions.append(
                self._build_memory_action(
                    operation="dormant",
                    memory_set_id=memory_set_id,
                    finished_at=finished_at,
                    memory_unit=updated_unit,
                    related_memory_unit_ids=[],
                    before_snapshot=unit,
                    after_snapshot=updated_unit,
                    reason="reflective consolidation で低重要かつ長期間未再確認の topic を dormant 化したため。",
                    event_ids=unit.get("evidence_event_ids", []),
                )
            )

        # Block: Result
        return actions

    def _summary_subject_ref(self, scope_type: str, scope_key: str) -> str:
        # Block: Relationship
        if scope_type == "relationship":
            return "self|user"

        # Block: Result
        return scope_key

    def _dominant_memory_types(self, scope_units: list[dict[str, Any]]) -> list[str]:
        # Block: Count
        counts = Counter(
            unit["memory_type"]
            for unit in scope_units
            if isinstance(unit.get("memory_type"), str)
        )

        # Block: Result
        return [memory_type for memory_type, _ in counts.most_common(2)]

    def _reflective_summary_text(
        self,
        *,
        scope_type: str,
        scope_key: str,
        memory_types: list[str],
        open_loop_count: int,
    ) -> str:
        # Block: Topic
        if scope_type == "topic":
            topic_label = self._display_scope_key(scope_key)
            if open_loop_count > 0:
                return f"最近は {topic_label} に関する話題が未完了テーマとして続いている。"
            return f"最近は {topic_label} に関する話題が繰り返し現れている。"

        # Block: Relationship
        if scope_type == "relationship":
            if open_loop_count > 0:
                return "最近のあなたとのやり取りでは、継続中の確認事項や会話の流れが積み上がっている。"
            if "relation" in memory_types:
                return "最近のあなたとのやり取りでは、距離感や支え方に関する理解が少しずつ安定している。"
            return "最近のあなたとのやり取りでは、関係文脈が継続して積み上がっている。"

        # Block: Self
        if scope_type == "self":
            return "最近の自分側の応答では、受け止め方や関わり方に一定の傾向が見えている。"

        # Block: User
        theme_text = self._reflective_theme_text(memory_types)
        return f"最近のあなたに関するやり取りでは、{theme_text}の理解が少しずつ積み上がっている。"

    def _reflective_theme_text(self, memory_types: list[str]) -> str:
        # Block: Mapping
        labels = {
            "fact": "事実や状況",
            "preference": "好み",
            "relation": "関係性",
            "interpretation": "状態や受け止め",
            "summary": "長期傾向",
        }
        parts = [labels[memory_type] for memory_type in memory_types if memory_type in labels]
        if not parts:
            return "状態"
        if len(parts) == 1:
            return parts[0]
        return "や".join(parts)

    def _reflective_summary_salience(
        self,
        *,
        scope_type: str,
        evidence_count: int,
        open_loop_count: int,
    ) -> float:
        # Block: Base
        base = {
            "self": 0.46,
            "user": 0.5,
            "relationship": 0.56,
            "topic": 0.42,
        }.get(scope_type, 0.44)

        # Block: Result
        return min(
            0.78,
            base + (0.04 * min(evidence_count, 4)) + (0.04 if open_loop_count > 0 else 0.0),
        )

    def _reflective_event_ids(
        self,
        *,
        scope_digests: list[dict[str, Any]],
        scope_units: list[dict[str, Any]],
        limit: int,
    ) -> list[str]:
        # Block: Seed
        merged: list[str] = []
        for digest in scope_digests:
            merged = self._merged_event_ids(merged, digest.get("linked_event_ids", []))
            if len(merged) >= limit:
                return merged[:limit]
        for unit in scope_units:
            merged = self._merged_event_ids(merged, unit.get("evidence_event_ids", []))
            if len(merged) >= limit:
                return merged[:limit]

        # Block: Result
        return merged[:limit]

    def _scope_digest_event_ids(
        self,
        *,
        digests: list[dict[str, Any]],
        scope_type: str,
        scope_key: str,
    ) -> list[str]:
        # Block: Merge
        merged: list[str] = []
        for digest in digests:
            if digest.get("primary_scope_type") != scope_type:
                continue
            if digest.get("primary_scope_key") != scope_key:
                continue
            merged = self._merged_event_ids(merged, digest.get("linked_event_ids", []))

        # Block: Result
        return merged

    def _unique_memory_unit_ids(self, actions: list[dict[str, Any]]) -> list[str]:
        # Block: Collect
        unique_ids: list[str] = []
        for action in actions:
            memory_unit_id = action.get("memory_unit_id")
            if not isinstance(memory_unit_id, str):
                continue
            if memory_unit_id in unique_ids:
                continue
            unique_ids.append(memory_unit_id)

        # Block: Result
        return unique_ids

    def _action_counts(self, actions: list[dict[str, Any]]) -> dict[str, int]:
        # Block: Count
        counts = Counter(action["operation"] for action in actions)

        # Block: Result
        return dict(counts)

    def _display_scope_key(self, scope_key: str) -> str:
        # Block: TopicPrefix
        if scope_key.startswith("topic:"):
            return scope_key.split(":", 1)[1]

        # Block: Result
        return scope_key

    def _now_iso(self) -> str:
        # Block: Timestamp
        return datetime.now(UTC).isoformat()

    def _hours_since(self, older_iso: str, newer_iso: str) -> float:
        # Block: Delta
        older = self._parse_iso(older_iso)
        newer = self._parse_iso(newer_iso)
        return max(0.0, (newer - older).total_seconds() / 3600.0)

    def _days_since(self, older_iso: str | None, newer_iso: str) -> int:
        # Block: Guard
        if not isinstance(older_iso, str) or not older_iso:
            return 0

        # Block: Delta
        older = self._parse_iso(older_iso)
        newer = self._parse_iso(newer_iso)
        delta = newer - older
        if delta <= timedelta(0):
            return 0
        return delta.days

    def _timestamp_sort_key(self, value: Any) -> float:
        # Block: Parse
        if not isinstance(value, str) or not value:
            return float("inf")
        return self._parse_iso(value).timestamp()

    def _parse_iso(self, value: str) -> datetime:
        # Block: Normalize
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _resolve_memory_actions(
        self,
        *,
        memory_set_id: str,
        finished_at: str,
        event_ids: list[str],
        candidate: dict[str, Any],
    ) -> list[dict[str, Any]]:
        # Block: Lookup
        matches = self.store.find_memory_units_for_compare(
            memory_set_id=memory_set_id,
            memory_type=candidate["memory_type"],
            scope_type=candidate["scope_type"],
            scope_key=candidate["scope_key"],
            subject_ref=candidate["subject_ref"],
            predicate=candidate["predicate"],
        )

        # Block: CreatePath
        if not matches:
            new_unit = self._build_new_memory_unit(
                memory_set_id=memory_set_id,
                finished_at=finished_at,
                event_ids=event_ids,
                candidate=candidate,
            )
            return [
                self._build_memory_action(
                    operation="create",
                    memory_set_id=memory_set_id,
                    finished_at=finished_at,
                    memory_unit=new_unit,
                    related_memory_unit_ids=[],
                    before_snapshot=None,
                    after_snapshot=new_unit,
                    reason=candidate["reason"],
                    event_ids=event_ids,
                )
            ]

        # Block: PrimaryMatch
        existing = matches[0]

        # Block: SamePath
        if self._is_same_memory(existing, candidate):
            updated_unit = self._build_reinforced_memory_unit(
                existing=existing,
                candidate=candidate,
                finished_at=finished_at,
                event_ids=event_ids,
            )
            return [
                self._build_memory_action(
                    operation="reinforce",
                    memory_set_id=memory_set_id,
                    finished_at=finished_at,
                    memory_unit=updated_unit,
                    related_memory_unit_ids=[],
                    before_snapshot=existing,
                    after_snapshot=updated_unit,
                    reason=candidate["reason"],
                    event_ids=event_ids,
                )
            ]

        # Block: RefinePath
        if self._can_refine(existing, candidate):
            updated_unit = self._build_refined_memory_unit(
                existing=existing,
                candidate=candidate,
                finished_at=finished_at,
                event_ids=event_ids,
            )
            return [
                self._build_memory_action(
                    operation="refine",
                    memory_set_id=memory_set_id,
                    finished_at=finished_at,
                    memory_unit=updated_unit,
                    related_memory_unit_ids=[],
                    before_snapshot=existing,
                    after_snapshot=updated_unit,
                    reason=candidate["reason"],
                    event_ids=event_ids,
                )
            ]

        # Block: SupersedePath
        new_unit = self._build_new_memory_unit(
            memory_set_id=memory_set_id,
            finished_at=finished_at,
            event_ids=event_ids,
            candidate=candidate,
        )
        superseded_unit = {
            **existing,
            "status": "superseded",
            "salience": min(self._clamp_score(existing["salience"]), 0.2),
            "valid_to": finished_at,
        }
        return [
            self._build_memory_action(
                operation="supersede",
                memory_set_id=memory_set_id,
                finished_at=finished_at,
                memory_unit=superseded_unit,
                related_memory_unit_ids=[new_unit["memory_unit_id"]],
                before_snapshot=existing,
                after_snapshot=superseded_unit,
                reason=candidate["reason"],
                event_ids=event_ids,
            ),
            self._build_memory_action(
                operation="create",
                memory_set_id=memory_set_id,
                finished_at=finished_at,
                memory_unit=new_unit,
                related_memory_unit_ids=[existing["memory_unit_id"]],
                before_snapshot=None,
                after_snapshot=new_unit,
                reason=candidate["reason"],
                event_ids=event_ids,
            ),
        ]

    def _build_new_memory_unit(
        self,
        *,
        memory_set_id: str,
        finished_at: str,
        event_ids: list[str],
        candidate: dict[str, Any],
    ) -> dict[str, Any]:
        # Block: Record
        return {
            "memory_unit_id": f"memory_unit:{uuid.uuid4().hex}",
            "memory_set_id": memory_set_id,
            "memory_type": candidate["memory_type"],
            "scope_type": candidate["scope_type"],
            "scope_key": candidate["scope_key"],
            "subject_ref": candidate["subject_ref"],
            "predicate": candidate["predicate"],
            "object_ref_or_value": candidate.get("object_ref_or_value"),
            "summary_text": candidate["summary_text"].strip(),
            "status": candidate["status"],
            "commitment_state": candidate.get("commitment_state"),
            "confidence": self._clamp_score(candidate["confidence"]),
            "salience": self._clamp_score(candidate["salience"]),
            "formed_at": finished_at,
            "last_confirmed_at": finished_at if candidate["status"] == "confirmed" else None,
            "valid_from": candidate.get("valid_from"),
            "valid_to": candidate.get("valid_to"),
            "evidence_event_ids": event_ids,
            "qualifiers": candidate.get("qualifiers", {}),
        }

    def _build_reinforced_memory_unit(
        self,
        *,
        existing: dict[str, Any],
        candidate: dict[str, Any],
        finished_at: str,
        event_ids: list[str],
    ) -> dict[str, Any]:
        # Block: Status
        next_status = existing["status"]
        if existing["status"] == "inferred" and candidate["status"] == "confirmed":
            next_status = "confirmed"
        if existing["status"] == "dormant":
            next_status = candidate["status"]

        # Block: Record
        return {
            **existing,
            "summary_text": existing["summary_text"],
            "status": next_status,
            "commitment_state": candidate.get("commitment_state") or existing.get("commitment_state"),
            "confidence": max(self._clamp_score(existing["confidence"]), self._clamp_score(candidate["confidence"])),
            "salience": max(self._clamp_score(existing["salience"]), self._clamp_score(candidate["salience"])),
            "last_confirmed_at": finished_at,
            "evidence_event_ids": self._merged_event_ids(existing.get("evidence_event_ids", []), event_ids),
            "qualifiers": {
                **existing.get("qualifiers", {}),
                **candidate.get("qualifiers", {}),
            },
        }

    def _build_refined_memory_unit(
        self,
        *,
        existing: dict[str, Any],
        candidate: dict[str, Any],
        finished_at: str,
        event_ids: list[str],
    ) -> dict[str, Any]:
        # Block: Status
        next_status = existing["status"]
        if candidate["status"] == "confirmed":
            next_status = "confirmed"

        # Block: Record
        return {
            **existing,
            "object_ref_or_value": candidate.get("object_ref_or_value"),
            "summary_text": candidate["summary_text"].strip(),
            "status": next_status,
            "commitment_state": candidate.get("commitment_state") or existing.get("commitment_state"),
            "confidence": max(self._clamp_score(existing["confidence"]), self._clamp_score(candidate["confidence"])),
            "salience": max(self._clamp_score(existing["salience"]), self._clamp_score(candidate["salience"])),
            "last_confirmed_at": finished_at,
            "valid_from": candidate.get("valid_from") or existing.get("valid_from"),
            "valid_to": candidate.get("valid_to") or existing.get("valid_to"),
            "evidence_event_ids": self._merged_event_ids(existing.get("evidence_event_ids", []), event_ids),
            "qualifiers": {
                **existing.get("qualifiers", {}),
                **candidate.get("qualifiers", {}),
            },
        }

    def _build_memory_action(
        self,
        *,
        operation: str,
        memory_set_id: str,
        finished_at: str,
        memory_unit: dict[str, Any],
        related_memory_unit_ids: list[str],
        before_snapshot: dict[str, Any] | None,
        after_snapshot: dict[str, Any] | None,
        reason: str,
        event_ids: list[str],
    ) -> dict[str, Any]:
        # Block: Action
        return {
            "operation": operation,
            "revision_id": f"revision:{uuid.uuid4().hex}",
            "memory_set_id": memory_set_id,
            "memory_unit_id": memory_unit["memory_unit_id"],
            "occurred_at": finished_at,
            "related_memory_unit_ids": related_memory_unit_ids,
            "before_snapshot": before_snapshot,
            "after_snapshot": after_snapshot,
            "reason": reason,
            "evidence_event_ids": event_ids,
            "memory_unit": memory_unit,
        }

    def _build_affect_update(
        self,
        *,
        memory_set_id: str,
        finished_at: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        # Block: Record
        return {
            "affect_state_id": f"affect_state:{uuid.uuid4().hex}",
            "memory_set_id": memory_set_id,
            "layer": payload["layer"],
            "target_scope_type": payload["target_scope_type"],
            "target_scope_key": payload["target_scope_key"],
            "affect_label": payload["affect_label"],
            "intensity": self._clamp_score(payload["intensity"]),
            "observed_at": finished_at,
            "updated_at": finished_at,
        }

    def _is_same_memory(self, existing: dict[str, Any], candidate: dict[str, Any]) -> bool:
        # Block: ObjectCompare
        if existing.get("object_ref_or_value") != candidate.get("object_ref_or_value"):
            return False

        # Block: CommitmentCompare
        if existing.get("commitment_state") != candidate.get("commitment_state"):
            return False

        # Block: QualifierCompare
        return existing.get("qualifiers", {}) == candidate.get("qualifiers", {})

    def _can_refine(self, existing: dict[str, Any], candidate: dict[str, Any]) -> bool:
        # Block: PolarityGuard
        existing_polarity = existing.get("qualifiers", {}).get("polarity")
        candidate_polarity = candidate.get("qualifiers", {}).get("polarity")
        if existing_polarity is not None and candidate_polarity is not None and existing_polarity != candidate_polarity:
            return False

        # Block: ObjectGuard
        if existing.get("object_ref_or_value") != candidate.get("object_ref_or_value"):
            return False

        # Block: ContentCheck
        return existing.get("summary_text") != candidate["summary_text"].strip() or existing.get("qualifiers", {}) != candidate.get(
            "qualifiers",
            {},
        )

    def _merged_event_ids(self, existing_event_ids: list[Any], new_event_ids: list[str]) -> list[str]:
        # Block: Merge
        merged: list[str] = []
        for event_id in existing_event_ids + new_event_ids:
            if isinstance(event_id, str) and event_id not in merged:
                merged.append(event_id)
        return merged

    def _normalized_text_list(self, values: list[Any], *, limit: int) -> list[str]:
        # Block: Normalize
        normalized: list[str] = []
        for value in values:
            if not isinstance(value, str):
                continue
            stripped = value.strip()
            if stripped and stripped not in normalized:
                normalized.append(stripped)
            if len(normalized) >= limit:
                break
        return normalized

    def _optional_text(self, value: Any) -> str | None:
        # Block: Normalize
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        if not stripped:
            return None
        return stripped

    def _clamp_score(self, value: Any) -> float:
        # Block: Normalize
        if not isinstance(value, (int, float)):
            return 0.0
        return max(0.0, min(float(value), 1.0))
