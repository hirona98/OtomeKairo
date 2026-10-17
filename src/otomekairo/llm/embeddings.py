from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from otomekairo.llm.contracts import LLMError

if TYPE_CHECKING:
    from otomekairo.llm.client import LLMClient


EMBEDDING_BATCH_LIMIT = 64


def embed_text_batches(
    llm: LLMClient, *, model_config: dict[str, Any], texts: list[str],
) -> Iterator[tuple[int, list[list[float]]]]:
    """Keep each text intact and preserve input order across bounded requests."""
    for offset in range(0, len(texts), EMBEDDING_BATCH_LIMIT):
        batch = texts[offset:offset + EMBEDDING_BATCH_LIMIT]
        vectors = llm.generate_embeddings(model_config=model_config, texts=batch)
        # Validate each batch before its vectors can be saved or compared.
        if len(vectors) != len(batch):
            raise LLMError("Embedding の応答件数が入力件数と一致しません。")
        yield offset, vectors
