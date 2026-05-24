from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

from otomekairo.llm_contracts import LLMError
from otomekairo.llm_mock_capability import LLMMockCapabilityMixin
from otomekairo.llm_mock_decision import LLMMockDecisionMixin
from otomekairo.llm_mock_memory import LLMMockMemoryMixin
from otomekairo.llm_mock_recall import LLMMockRecallMixin
from otomekairo.llm_mock_reply import LLMMockReplyMixin
from otomekairo.llm_mock_world_state import LLMMockWorldStateMixin


# モッククライアント
@dataclass(slots=True)
class MockLLMClient(
    LLMMockRecallMixin,
    LLMMockCapabilityMixin,
    LLMMockDecisionMixin,
    LLMMockReplyMixin,
    LLMMockMemoryMixin,
    LLMMockWorldStateMixin,
):
    def generate_embeddings(
        self,
        role_definition: dict,
        texts: list[str],
        embedding_dimension: int,
    ) -> list[list[float]]:
        # model確認
        self._assert_mock_model(role_definition)

        # 結果
        return [
            self._mock_embedding_vector(text, embedding_dimension)
            for text in texts
        ]

    def _mock_embedding_vector(self, text: str, embedding_dimension: int) -> list[float]:
        # 空確認
        normalized = text.strip()
        if embedding_dimension <= 0:
            raise LLMError("embedding_dimension は正の値である必要があります。")
        if not normalized:
            return [0.0] * embedding_dimension

        # 蓄積
        values = [0.0] * embedding_dimension
        tokens = [normalized]
        if len(normalized) >= 2:
            tokens.extend(normalized[index : index + 2] for index in range(len(normalized) - 1))
        if len(normalized) >= 3:
            tokens.extend(normalized[index : index + 3] for index in range(len(normalized) - 2))

        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            primary_index = int.from_bytes(digest[:4], "little") % embedding_dimension
            secondary_index = int.from_bytes(digest[4:8], "little") % embedding_dimension
            primary_value = 0.5 + (digest[8] / 255.0)
            secondary_value = 0.5 + (digest[9] / 255.0)
            values[primary_index] += primary_value
            values[secondary_index] -= secondary_value * 0.25

        # 正規化
        norm = math.sqrt(sum(value * value for value in values))
        if norm <= 0.0:
            return [0.0] * embedding_dimension
        return [value / norm for value in values]

    def _mock_contains_any(self, text: str, terms: tuple[str, ...]) -> bool:
        # モック専用の簡易分岐
        return any(term in text for term in terms)

    def _assert_mock_model(self, role_definition: dict) -> None:
        # モデル確認
        model = role_definition.get("model")
        if isinstance(model, str) and model.strip().startswith("mock"):
            return
        raise LLMError(f"未対応の mock model です: {model}")
