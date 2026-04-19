from __future__ import annotations

import hashlib
from typing import Any

from otomekairo.llm import LLMClient
from otomekairo.memory_utils import (
    NON_SEMANTIC_QUALIFIER_KEYS,
    build_memory_unit_semantic_text,
    normalized_text_list,
)
from otomekairo.store import FileStore


# 索引器
class MemoryVectorIndexer:
    def __init__(self, *, store: FileStore, llm: LLMClient) -> None:
        # 依存関係
        self.store = store
        self.llm = llm

    def sync(
        self,
        *,
        state: dict[str, Any],
        finished_at: str,
        episode: dict[str, Any] | None,
        memory_actions: list[dict[str, Any]],
    ) -> None:
        # 埋め込み設定
        selected_memory_set = state["memory_sets"][state["selected_memory_set_id"]]
        embedding_definition = selected_memory_set["embedding"]
        embedding_dimension = self._embedding_dimension(embedding_definition)

        # source群
        entries = self._build_vector_index_entries(
            finished_at=finished_at,
            episode=episode,
            memory_actions=memory_actions,
        )
        if not entries:
            return

        # 埋め込み群
        embeddings = self.llm.generate_embeddings(
            role_definition=embedding_definition,
            texts=[entry["source_text"] for entry in entries],
        )

        # payload群
        payloads = [
            {
                **entry,
                "embedding": embedding,
            }
            for entry, embedding in zip(entries, embeddings, strict=True)
        ]

        # 永続化
        self.store.upsert_vector_index_entries(
            entries=payloads,
            embedding_dimension=embedding_dimension,
        )

    def _build_vector_index_entries(
        self,
        *,
        finished_at: str,
        episode: dict[str, Any] | None,
        memory_actions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        # 状態
        entries: list[dict[str, Any]] = []
        seen_source_ids: set[tuple[str, str]] = set()

        # Episode要約
        if episode is not None:
            episode_entry = self._vector_entry_for_episode(
                finished_at=finished_at,
                record=episode,
            )
            if episode_entry is not None:
                entries.append(episode_entry)
                seen_source_ids.add(("episode", episode["episode_id"]))

        # 記憶単位群
        for action in memory_actions:
            memory_unit = action.get("memory_unit")
            if not isinstance(memory_unit, dict):
                continue
            source_key = ("memory_unit", memory_unit["memory_unit_id"])
            if source_key in seen_source_ids:
                continue
            memory_entry = self._vector_entry_for_memory_unit(
                finished_at=finished_at,
                record=memory_unit,
            )
            if memory_entry is None:
                continue
            entries.append(memory_entry)
            seen_source_ids.add(source_key)

        # 結果
        return entries

    def _vector_entry_for_episode(
        self,
        *,
        finished_at: str,
        record: dict[str, Any],
    ) -> dict[str, Any] | None:
        # sourceテキスト
        source_text = self._episode_source_text(record)
        if not source_text:
            return None

        # エントリ
        return {
            "memory_set_id": record["memory_set_id"],
            "source_kind": "episode",
            "source_id": record["episode_id"],
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
        record: dict[str, Any],
    ) -> dict[str, Any] | None:
        # sourceテキスト
        source_text = build_memory_unit_semantic_text(
            record,
            exclude_qualifier_keys=NON_SEMANTIC_QUALIFIER_KEYS,
        )
        if not source_text:
            return None

        # エントリ
        return {
            "memory_set_id": record["memory_set_id"],
            "source_kind": "memory_unit",
            "source_id": record["memory_unit_id"],
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

    def _episode_source_text(self, record: dict[str, Any]) -> str:
        # 部品群
        parts: list[str] = [record.get("summary_text", "").strip()]
        outcome_text = record.get("outcome_text")
        if isinstance(outcome_text, str) and outcome_text.strip():
            parts.append(outcome_text.strip())
        parts.extend(normalized_text_list(record.get("open_loops", []), limit=4))

        # 結果
        return "\n".join(part for part in parts if part)

    def _embedding_dimension(self, definition: dict[str, Any]) -> int:
        embedding_dimension = definition.get("embedding_dimension")
        if not isinstance(embedding_dimension, int) or embedding_dimension <= 0:
            raise ValueError("memory_set.embedding.embedding_dimension must be a positive integer.")
        return embedding_dimension

    def _text_hash(self, value: str) -> str:
        # ハッシュ
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
