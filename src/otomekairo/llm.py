from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from otomekairo.llm_contracts import (
    LLMError,
    validate_decision_contract,
    validate_memory_interpretation_contract,
    validate_memory_reflection_summary_contract,
)
from otomekairo.llm_mock import MockLLMClient
from otomekairo.llm_parsing import parse_json_object, parse_recall_hint_payload
from otomekairo.llm_prompts import (
    build_decision_messages,
    build_memory_interpretation_messages,
    build_memory_interpretation_repair_prompt,
    build_memory_reflection_summary_messages,
    build_memory_reflection_summary_repair_prompt,
    build_recall_hint_messages,
    build_reply_messages,
)
from otomekairo.llm_transport import complete_text, generate_embeddings as transport_generate_embeddings


# LiteLLM連携
@dataclass(slots=True)
class LLMClient:
    mock_client: MockLLMClient = field(default_factory=MockLLMClient)

    def generate_recall_hint(
        self,
        *,
        role_definition: dict,
        observation_text: str,
        recent_turns: list[dict],
        current_time: str,
    ) -> dict[str, Any]:
        # モック経路
        if self._is_mock_role_definition(role_definition):
            return self.mock_client.generate_recall_hint(role_definition, observation_text, recent_turns, current_time)

        # プロンプト構築
        messages = build_recall_hint_messages(
            observation_text=observation_text,
            recent_turns=recent_turns,
            current_time=current_time,
        )

        # 再試行
        last_contract_error: LLMError | None = None
        for attempt in range(2):
            content = complete_text(role_definition=role_definition, messages=messages)
            try:
                return parse_recall_hint_payload(content)
            except LLMError as exc:
                last_contract_error = exc
                if attempt >= 1:
                    raise

        # 失敗
        if last_contract_error is not None:
            raise last_contract_error
        raise LLMError("RecallHint generation failed without a parseable response.")

    def generate_decision(
        self,
        *,
        role_definition: dict,
        persona: dict,
        observation_text: str,
        recent_turns: list[dict],
        time_context: dict[str, Any],
        affect_context: dict[str, list[dict[str, Any]]],
        recall_hint: dict,
        recall_pack: dict[str, Any],
    ) -> dict[str, Any]:
        # モック経路
        if self._is_mock_role_definition(role_definition):
            return self.mock_client.generate_decision(
                role_definition,
                persona,
                observation_text,
                recent_turns,
                time_context,
                affect_context,
                recall_hint,
                recall_pack,
            )

        # プロンプト構築
        messages = build_decision_messages(
            persona=persona,
            observation_text=observation_text,
            recent_turns=recent_turns,
            time_context=time_context,
            affect_context=affect_context,
            recall_hint=recall_hint,
            recall_pack=recall_pack,
        )

        # 補完
        content = complete_text(role_definition=role_definition, messages=messages)
        payload = parse_json_object(content)
        validate_decision_contract(payload)
        return payload

    def generate_reply(
        self,
        *,
        role_definition: dict,
        persona: dict,
        observation_text: str,
        recent_turns: list[dict],
        time_context: dict[str, Any],
        affect_context: dict[str, list[dict[str, Any]]],
        recall_hint: dict,
        recall_pack: dict[str, Any],
        decision: dict,
    ) -> dict[str, Any]:
        # モック経路
        if self._is_mock_role_definition(role_definition):
            return self.mock_client.generate_reply(
                role_definition,
                persona,
                observation_text,
                recent_turns,
                time_context,
                affect_context,
                recall_hint,
                recall_pack,
                decision,
            )

        # プロンプト構築
        messages = build_reply_messages(
            persona=persona,
            observation_text=observation_text,
            recent_turns=recent_turns,
            time_context=time_context,
            affect_context=affect_context,
            recall_hint=recall_hint,
            recall_pack=recall_pack,
            decision=decision,
        )

        # 補完
        content = complete_text(role_definition=role_definition, messages=messages)
        reply_text = content.strip()
        if not reply_text:
            raise LLMError("Reply generation returned empty content.")

        # payload作成
        return {
            "reply_text": reply_text,
            "reply_style_notes": f"model={role_definition.get('model')}",
            "confidence_note": "litellm_model",
        }

    def generate_memory_interpretation(
        self,
        *,
        role_definition: dict,
        observation_text: str,
        recall_hint: dict,
        decision: dict,
        reply_text: str | None,
        current_time: str,
    ) -> dict[str, Any]:
        # モック経路
        if self._is_mock_role_definition(role_definition):
            return self.mock_client.generate_memory_interpretation(
                role_definition,
                observation_text,
                recall_hint,
                decision,
                reply_text,
            )

        # プロンプト構築
        messages = build_memory_interpretation_messages(
            observation_text=observation_text,
            recall_hint=recall_hint,
            decision=decision,
            reply_text=reply_text,
            current_time=current_time,
        )

        # 再試行
        last_contract_error: LLMError | None = None
        attempt_messages = list(messages)
        for attempt in range(2):
            content = complete_text(role_definition=role_definition, messages=attempt_messages)
            try:
                payload = parse_json_object(content)
                validate_memory_interpretation_contract(payload)
                return payload
            except LLMError as exc:
                last_contract_error = exc
                if attempt >= 1:
                    raise
                attempt_messages = [
                    *messages,
                    {
                        "role": "assistant",
                        "content": content,
                    },
                    {
                        "role": "user",
                        "content": build_memory_interpretation_repair_prompt(str(exc)),
                    },
                ]

        # 失敗
        if last_contract_error is not None:
            raise last_contract_error
        raise LLMError("MemoryInterpretation generation failed without a parseable response.")

    def generate_memory_reflection_summary(
        self,
        *,
        role_definition: dict,
        evidence_pack: dict[str, Any],
    ) -> dict[str, Any]:
        # モック経路
        if self._is_mock_role_definition(role_definition):
            return self.mock_client.generate_memory_reflection_summary(role_definition, evidence_pack)

        # プロンプト構築
        messages = build_memory_reflection_summary_messages(
            evidence_pack=evidence_pack,
        )

        # 再試行
        last_contract_error: LLMError | None = None
        attempt_messages = list(messages)
        for attempt in range(2):
            content = complete_text(role_definition=role_definition, messages=attempt_messages)
            try:
                payload = parse_json_object(content)
                validate_memory_reflection_summary_contract(payload)
                return payload
            except LLMError as exc:
                last_contract_error = exc
                if attempt >= 1:
                    raise
                attempt_messages = [
                    *messages,
                    {
                        "role": "assistant",
                        "content": content,
                    },
                    {
                        "role": "user",
                        "content": build_memory_reflection_summary_repair_prompt(str(exc)),
                    },
                ]

        # 失敗
        if last_contract_error is not None:
            raise last_contract_error
        raise LLMError("MemoryReflectionSummary generation failed without a parseable response.")

    def generate_embeddings(
        self,
        *,
        role_definition: dict,
        texts: list[str],
    ) -> list[list[float]]:
        # 空
        if not texts:
            return []

        # 次元
        embedding_dimension = self._embedding_dimension(role_definition)
        if not isinstance(embedding_dimension, int) or embedding_dimension <= 0:
            raise LLMError("embedding_dimension must be a positive integer.")

        # モック経路
        if self._is_mock_role_definition(role_definition):
            return self.mock_client.generate_embeddings(role_definition, texts, embedding_dimension)

        # model差分込みの transport へ委譲する。
        return transport_generate_embeddings(
            role_definition=role_definition,
            texts=texts,
            expected_dimension=embedding_dimension,
        )

    # 設定補助
    def _is_mock_role_definition(self, role_definition: dict) -> bool:
        # model=mock* は開発用の内蔵ロジックへ切り替える。
        model = role_definition.get("model")
        return isinstance(model, str) and model.strip().startswith("mock")

    def _embedding_dimension(self, role_definition: dict) -> int:
        return role_definition.get("embedding_dimension")
