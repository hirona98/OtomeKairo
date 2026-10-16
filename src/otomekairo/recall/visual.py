from __future__ import annotations

from typing import Any

from otomekairo.llm.client import LLMError
from otomekairo.memory.utils import now_iso, source_text_hash


class RecallVisualMixin:
    def _search_visual_observation_vectors(self, **kwargs: Any) -> list[dict[str, Any]]:
        try:
            return self.store.search_visual_observation_vectors(**kwargs)
        except Exception as exc:
            raise LLMError(f"視覚記録の意味検索に失敗しました: {exc}") from exc

    def _sync_visual_observation_index(
        self, *, state: dict[str, Any], cycle_id: str,
    ) -> dict[str, int]:
        memory_set_id = state["selected_memory_set_id"]
        definition = state["memory_sets"][memory_set_id]["embedding"]
        dimension = self._embedding_dimension(definition)
        try:
            sources = self.store.list_visual_observation_vector_sources(
                memory_set_id=memory_set_id, cycle_id=cycle_id, embedding_dimension=dimension,
            )
            entries = []
            for source in sources:
                record = source["record"]
                text = record["detailed_summary_text"]
                if not isinstance(text, str) or not text.strip():
                    raise ValueError("visual observation must have a detailed description")
                text_hash = source_text_hash(text)
                if source["has_vector"] and source["text_hash"] == text_hash:
                    continue
                entries.append({"visual_observation_id": record["visual_observation_id"],
                                "memory_set_id": memory_set_id, "source_text": text,
                                "text_hash": text_hash, "updated_at": now_iso()})
            # 一つの観測説明を分割せず、観測単位で API のバッチを作る。
            for offset in range(0, len(entries), 64):
                batch = entries[offset:offset + 64]
                embeddings = self.llm.generate_embeddings(
                    model_config=definition, texts=[entry["source_text"] for entry in batch],
                )
                self.store.upsert_visual_observation_vectors(
                    entries=[{**entry, "embedding": embedding}
                             for entry, embedding in zip(batch, embeddings, strict=True)],
                    embedding_dimension=dimension,
                )
        except Exception as exc:
            raise LLMError(f"視覚記録の意味検索索引を更新できませんでした: {exc}") from exc
        return {"source_count": len(sources), "indexed_count": len(entries)}
