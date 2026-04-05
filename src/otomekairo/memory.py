from __future__ import annotations

import hashlib
import uuid
from typing import Any

from otomekairo.llm import LLMClient
from otomekairo.store import FileStore


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

        # Block: Result
        return {
            "episode_digest_id": episode_digest["episode_digest_id"],
            "memory_action_count": len(memory_actions),
            "affect_update_count": len(affect_updates),
        }

    def _sync_vector_index(
        self,
        *,
        state: dict[str, Any],
        finished_at: str,
        episode_digest: dict[str, Any],
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
        episode_digest: dict[str, Any],
        memory_actions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        # Block: State
        entries: list[dict[str, Any]] = []
        seen_source_ids: set[tuple[str, str]] = set()

        # Block: EpisodeDigest
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
