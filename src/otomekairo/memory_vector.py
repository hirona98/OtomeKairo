from __future__ import annotations

import hashlib
from typing import Any

from otomekairo.llm import LLMClient
from otomekairo.memory_utils import normalized_text_list
from otomekairo.store import FileStore


# Block: Indexer
class MemoryVectorIndexer:
    def __init__(self, *, store: FileStore, llm: LLMClient) -> None:
        # Block: Dependencies
        self.store = store
        self.llm = llm

    def sync(
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
        parts.extend(normalized_text_list(record.get("open_loops", []), limit=4))

        # Block: Result
        return "\n".join(part for part in parts if part)

    def _embedding_preset(self, embedding_profile_id: str, embedding_dimension: int) -> str:
        # Block: Identifier
        return f"{embedding_profile_id}:dim{embedding_dimension}"

    def _text_hash(self, value: str) -> str:
        # Block: Hash
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
