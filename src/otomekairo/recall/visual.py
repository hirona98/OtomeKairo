from __future__ import annotations

from typing import Any

from otomekairo.llm.client import LLMError
from otomekairo.llm.embeddings import embed_text_batches
from otomekairo.memory.utils import now_iso, source_text_hash


class RecallVisualMixin:
    def _search_visual_observation_vectors(self, **kwargs: Any) -> list[dict[str, Any]]:
        try:
            return self.store.search_visual_observation_vectors(**kwargs)
        except Exception as exc:
            raise LLMError(f"視覚記録の意味検索に失敗しました: {exc}") from exc

    def _embed_association_inputs(
        self, *, state: dict[str, Any], cycle_id: str, query_texts: list[str],
    ) -> tuple[dict[str, int], list[list[float]]]:
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
            # 検索クエリと未索引の観測を同じ設定で埋め込み、全文と順序を保つ。
            texts = [*query_texts, *(entry["source_text"] for entry in entries)]
            query_embeddings: list[list[float]] = []
            for offset, vectors in embed_text_batches(self.llm, model_config=definition, texts=texts):
                indexed_entries = []
                for index, vector in enumerate(vectors, start=offset):
                    if index < len(query_texts):
                        query_embeddings.append(vector)
                    else:
                        indexed_entries.append({**entries[index - len(query_texts)], "embedding": vector})
                if indexed_entries:
                    self.store.upsert_visual_observation_vectors(
                        entries=indexed_entries, embedding_dimension=dimension,
                    )
        except Exception as exc:
            raise LLMError(f"視覚記録の意味検索索引を更新できませんでした: {exc}") from exc
        return {"source_count": len(sources), "indexed_count": len(entries)}, query_embeddings
