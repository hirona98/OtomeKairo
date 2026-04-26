from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from otomekairo.llm_contracts import (
    LLMContractError,
    LLMError,
    validate_decision_contract,
    validate_event_evidence_contract,
    validate_memory_interpretation_contract,
    validate_memory_reflection_summary_contract,
    validate_pending_intent_selection_contract,
    validate_recall_pack_selection_contract,
)
from otomekairo.llm_mock import MockLLMClient
from otomekairo.llm_parsing import parse_json_object, parse_recall_hint_payload
from otomekairo.llm_prompts import (
    build_decision_messages,
    build_event_evidence_messages,
    build_event_evidence_repair_prompt,
    build_memory_interpretation_messages,
    build_memory_interpretation_repair_prompt,
    build_memory_reflection_summary_messages,
    build_memory_reflection_summary_repair_prompt,
    build_pending_intent_selection_messages,
    build_pending_intent_selection_repair_prompt,
    build_recall_pack_selection_messages,
    build_recall_pack_selection_repair_prompt,
    build_recall_hint_messages,
    build_reply_messages,
)
from otomekairo.llm_transport import complete_text, generate_embeddings as transport_generate_embeddings
from otomekairo.service_common import debug_log


# 定数
LLM_DEBUG_TEXT_PREVIEW_LIMIT = 200


# LiteLLM連携
@dataclass(slots=True)
class LLMClient:
    mock_client: MockLLMClient = field(default_factory=MockLLMClient)

    def generate_recall_hint(
        self,
        *,
        role_definition: dict,
        input_text: str,
        recent_turns: list[dict],
        current_time: str,
    ) -> dict[str, Any]:
        operation = "recall_hint"
        debug_log(
            "LLM",
            (
                f"{operation} start mode={self._debug_mode(role_definition)} "
                f"model={self._debug_model(role_definition)} input_chars={len(input_text)} "
                f"recent_turns={len(recent_turns)}"
            ),
        )
        try:
            # モック経路
            if self._is_mock_role_definition(role_definition):
                payload = self.mock_client.generate_recall_hint(role_definition, input_text, recent_turns, current_time)
                debug_log(
                    "LLM",
                    (
                        f"{operation} done mode=mock focus={payload.get('primary_recall_focus')} "
                        f"confidence={payload.get('confidence')}"
                    ),
                )
                return payload

            # プロンプト構築
            messages = build_recall_hint_messages(
                input_text=input_text,
                recent_turns=recent_turns,
                current_time=current_time,
            )

            # 再試行
            last_contract_error: LLMError | None = None
            for attempt in range(2):
                debug_log("LLM", f"{operation} attempt={attempt + 1} request messages={len(messages)}")
                self._debug_messages_preview(operation=operation, attempt=attempt + 1, messages=messages)
                content = complete_text(role_definition=role_definition, messages=messages)
                self._debug_response_preview(operation=operation, attempt=attempt + 1, content=content)
                try:
                    payload = parse_recall_hint_payload(content)
                    debug_log(
                        "LLM",
                        (
                            f"{operation} done attempt={attempt + 1} response_chars={len(content)} "
                            f"focus={payload.get('primary_recall_focus')} confidence={payload.get('confidence')}"
                        ),
                    )
                    return payload
                except LLMError as exc:
                    last_contract_error = exc
                    debug_log(
                        "LLM",
                        f"{operation} parse_failed attempt={attempt + 1} error={self._debug_error(exc)}",
                    )
                    if attempt >= 1:
                        raise

            # 失敗
            if last_contract_error is not None:
                raise last_contract_error
            raise LLMError("RecallHint の生成に失敗しました。解析可能な応答が得られませんでした。")
        except Exception as exc:
            debug_log("LLM", f"{operation} failed error={type(exc).__name__}: {self._debug_error(exc)}")
            raise

    def generate_decision(
        self,
        *,
        role_definition: dict,
        persona: dict,
        input_text: str,
        recent_turns: list[dict],
        time_context: dict[str, Any],
        affect_context: dict[str, Any],
        drive_state_summary: list[dict[str, Any]] | None,
        ongoing_action_summary: dict[str, Any] | None,
        recall_hint: dict,
        recall_pack: dict[str, Any],
    ) -> dict[str, Any]:
        operation = "decision"
        debug_log(
            "LLM",
            (
                f"{operation} start mode={self._debug_mode(role_definition)} "
                f"model={self._debug_model(role_definition)} recent_turns={len(recent_turns)} "
                f"recall_candidates={recall_pack.get('candidate_count', 0)}"
            ),
        )
        try:
            # モック経路
            if self._is_mock_role_definition(role_definition):
                payload = self.mock_client.generate_decision(
                    role_definition,
                    persona,
                    input_text,
                    recent_turns,
                    time_context,
                    affect_context,
                    drive_state_summary,
                    ongoing_action_summary,
                    recall_hint,
                    recall_pack,
                )
                debug_log("LLM", f"{operation} done mode=mock kind={payload.get('kind')}")
                return payload

            # プロンプト構築
            messages = build_decision_messages(
                persona=persona,
                input_text=input_text,
                recent_turns=recent_turns,
                time_context=time_context,
                affect_context=affect_context,
                drive_state_summary=drive_state_summary,
                ongoing_action_summary=ongoing_action_summary,
                recall_hint=recall_hint,
                recall_pack=recall_pack,
            )

            # 補完
            debug_log("LLM", f"{operation} request messages={len(messages)}")
            self._debug_messages_preview(operation=operation, attempt=1, messages=messages)
            content = complete_text(role_definition=role_definition, messages=messages)
            self._debug_response_preview(operation=operation, attempt=1, content=content)
            payload = parse_json_object(content)
            validate_decision_contract(payload)
            debug_log("LLM", f"{operation} done response_chars={len(content)} kind={payload.get('kind')}")
            return payload
        except Exception as exc:
            debug_log("LLM", f"{operation} failed error={type(exc).__name__}: {self._debug_error(exc)}")
            raise

    def generate_reply(
        self,
        *,
        role_definition: dict,
        persona: dict,
        input_text: str,
        recent_turns: list[dict],
        time_context: dict[str, Any],
        affect_context: dict[str, Any],
        drive_state_summary: list[dict[str, Any]] | None,
        ongoing_action_summary: dict[str, Any] | None,
        recall_hint: dict,
        recall_pack: dict[str, Any],
        decision: dict,
    ) -> dict[str, Any]:
        operation = "reply"
        debug_log(
            "LLM",
            (
                f"{operation} start mode={self._debug_mode(role_definition)} "
                f"model={self._debug_model(role_definition)} decision_kind={decision.get('kind')}"
            ),
        )
        try:
            # モック経路
            if self._is_mock_role_definition(role_definition):
                payload = self.mock_client.generate_reply(
                    role_definition,
                    persona,
                    input_text,
                    recent_turns,
                    time_context,
                    affect_context,
                    drive_state_summary,
                    ongoing_action_summary,
                    recall_hint,
                    recall_pack,
                    decision,
                )
                debug_log("LLM", f"{operation} done mode=mock reply_chars={len(payload.get('reply_text', ''))}")
                return payload

            # プロンプト構築
            messages = build_reply_messages(
                persona=persona,
                input_text=input_text,
                recent_turns=recent_turns,
                time_context=time_context,
                affect_context=affect_context,
                drive_state_summary=drive_state_summary,
                ongoing_action_summary=ongoing_action_summary,
                recall_hint=recall_hint,
                recall_pack=recall_pack,
                decision=decision,
            )

            # 補完
            debug_log("LLM", f"{operation} request messages={len(messages)}")
            self._debug_messages_preview(operation=operation, attempt=1, messages=messages)
            content = complete_text(role_definition=role_definition, messages=messages)
            self._debug_response_preview(operation=operation, attempt=1, content=content)
            reply_text = content.strip()
            if not reply_text:
                raise LLMError("Reply の生成結果が空でした。")

            # payload作成
            payload = {
                "reply_text": reply_text,
                "reply_style_notes": f"model={role_definition.get('model')}",
                "confidence_note": "litellm_model",
            }
            debug_log("LLM", f"{operation} done response_chars={len(content)} reply_chars={len(reply_text)}")
            return payload
        except Exception as exc:
            debug_log("LLM", f"{operation} failed error={type(exc).__name__}: {self._debug_error(exc)}")
            raise

    def generate_memory_interpretation(
        self,
        *,
        role_definition: dict,
        input_text: str,
        recall_hint: dict,
        decision: dict,
        reply_text: str | None,
        current_time: str,
    ) -> dict[str, Any]:
        operation = "memory_interpretation"
        debug_log(
            "LLM",
            (
                f"{operation} start mode={self._debug_mode(role_definition)} "
                f"model={self._debug_model(role_definition)} input_chars={len(input_text)} "
                f"decision_kind={decision.get('kind')} reply_chars={len(reply_text or '')}"
            ),
        )
        # モック経路
        if self._is_mock_role_definition(role_definition):
            payload = self.mock_client.generate_memory_interpretation(
                role_definition,
                input_text,
                recall_hint,
                decision,
                reply_text,
            )
            debug_log("LLM", f"{operation} done mode=mock keys={self._debug_payload_keys(payload)}")
            return payload

        # プロンプト構築
        messages = build_memory_interpretation_messages(
            input_text=input_text,
            recall_hint=recall_hint,
            decision=decision,
            reply_text=reply_text,
            current_time=current_time,
        )
        return self._generate_structured_payload(
            role_definition=role_definition,
            messages=messages,
            validator=validate_memory_interpretation_contract,
            repair_prompt_builder=build_memory_interpretation_repair_prompt,
            failure_message="MemoryInterpretation の生成に失敗しました。解析可能な応答が得られませんでした。",
            operation=operation,
        )

    def generate_memory_reflection_summary(
        self,
        *,
        role_definition: dict,
        evidence_pack: dict[str, Any],
    ) -> dict[str, Any]:
        operation = "memory_reflection_summary"
        debug_log(
            "LLM",
            (
                f"{operation} start mode={self._debug_mode(role_definition)} "
                f"model={self._debug_model(role_definition)} evidence_keys={self._debug_payload_keys(evidence_pack)}"
            ),
        )
        # モック経路
        if self._is_mock_role_definition(role_definition):
            payload = self.mock_client.generate_memory_reflection_summary(role_definition, evidence_pack)
            debug_log("LLM", f"{operation} done mode=mock keys={self._debug_payload_keys(payload)}")
            return payload

        # プロンプト構築
        messages = build_memory_reflection_summary_messages(
            evidence_pack=evidence_pack,
        )
        return self._generate_structured_payload(
            role_definition=role_definition,
            messages=messages,
            validator=validate_memory_reflection_summary_contract,
            repair_prompt_builder=build_memory_reflection_summary_repair_prompt,
            failure_message="MemoryReflectionSummary の生成に失敗しました。解析可能な応答が得られませんでした。",
            operation=operation,
        )

    def generate_event_evidence(
        self,
        *,
        role_definition: dict,
        source_pack: dict[str, Any],
    ) -> dict[str, Any]:
        operation = "event_evidence"
        source_events = source_pack.get("events", []) if isinstance(source_pack, dict) else []
        debug_log(
            "LLM",
            (
                f"{operation} start mode={self._debug_mode(role_definition)} "
                f"model={self._debug_model(role_definition)} events={len(source_events) if isinstance(source_events, list) else 0}"
            ),
        )
        # モック経路
        if self._is_mock_role_definition(role_definition):
            payload = self.mock_client.generate_event_evidence(role_definition, source_pack)
            debug_log("LLM", f"{operation} done mode=mock keys={self._debug_payload_keys(payload)}")
            return payload

        # プロンプト構築
        messages = build_event_evidence_messages(
            source_pack=source_pack,
        )
        return self._generate_structured_payload(
            role_definition=role_definition,
            messages=messages,
            validator=validate_event_evidence_contract,
            repair_prompt_builder=build_event_evidence_repair_prompt,
            failure_message="EventEvidence の生成に失敗しました。解析可能な応答が得られませんでした。",
            wrap_validation_error=True,
            operation=operation,
        )

    def generate_recall_pack_selection(
        self,
        *,
        role_definition: dict,
        source_pack: dict[str, Any],
    ) -> dict[str, Any]:
        operation = "recall_pack_selection"
        candidates = source_pack.get("candidates", []) if isinstance(source_pack, dict) else []
        debug_log(
            "LLM",
            (
                f"{operation} start mode={self._debug_mode(role_definition)} "
                f"model={self._debug_model(role_definition)} candidates={len(candidates) if isinstance(candidates, list) else 0}"
            ),
        )
        # モック経路
        if self._is_mock_role_definition(role_definition):
            payload = self.mock_client.generate_recall_pack_selection(role_definition, source_pack)
            debug_log("LLM", f"{operation} done mode=mock keys={self._debug_payload_keys(payload)}")
            return payload

        # プロンプト構築
        messages = build_recall_pack_selection_messages(
            source_pack=source_pack,
        )
        return self._generate_structured_payload(
            role_definition=role_definition,
            messages=messages,
            validator=lambda payload: validate_recall_pack_selection_contract(payload, source_pack=source_pack),
            repair_prompt_builder=build_recall_pack_selection_repair_prompt,
            failure_message="RecallPackSelection の生成に失敗しました。解析可能な応答が得られませんでした。",
            wrap_validation_error=True,
            operation=operation,
        )

    def generate_pending_intent_selection(
        self,
        *,
        role_definition: dict,
        source_pack: dict[str, Any],
    ) -> dict[str, Any]:
        operation = "pending_intent_selection"
        candidates = source_pack.get("candidates", []) if isinstance(source_pack, dict) else []
        debug_log(
            "LLM",
            (
                f"{operation} start mode={self._debug_mode(role_definition)} "
                f"model={self._debug_model(role_definition)} candidates={len(candidates) if isinstance(candidates, list) else 0}"
            ),
        )
        # モック経路
        if self._is_mock_role_definition(role_definition):
            payload = self.mock_client.generate_pending_intent_selection(role_definition, source_pack)
            debug_log("LLM", f"{operation} done mode=mock keys={self._debug_payload_keys(payload)}")
            return payload

        # プロンプト構築
        messages = build_pending_intent_selection_messages(
            source_pack=source_pack,
        )
        return self._generate_structured_payload(
            role_definition=role_definition,
            messages=messages,
            validator=lambda payload: validate_pending_intent_selection_contract(payload, source_pack=source_pack),
            repair_prompt_builder=build_pending_intent_selection_repair_prompt,
            failure_message="PendingIntentSelection の生成に失敗しました。解析可能な応答が得られませんでした。",
            wrap_validation_error=True,
            operation=operation,
        )

    def generate_embeddings(
        self,
        *,
        role_definition: dict,
        texts: list[str],
    ) -> list[list[float]]:
        # 空
        if not texts:
            debug_log("LLM", "embeddings skipped empty_texts")
            return []

        # 次元
        embedding_dimension = self._embedding_dimension(role_definition)
        if not isinstance(embedding_dimension, int) or embedding_dimension <= 0:
            raise LLMError("embedding_dimension は正の整数である必要があります。")

        debug_log(
            "LLM",
            (
                f"embeddings start mode={self._debug_mode(role_definition)} "
                f"model={self._debug_model(role_definition)} texts={len(texts)} dimension={embedding_dimension}"
            ),
        )
        self._debug_embedding_texts_preview(texts)

        # モック経路
        if self._is_mock_role_definition(role_definition):
            vectors = self.mock_client.generate_embeddings(role_definition, texts, embedding_dimension)
            debug_log("LLM", f"embeddings done mode=mock vectors={len(vectors)}")
            return vectors

        # model差分込みの transport へ委譲する。
        vectors = transport_generate_embeddings(
            role_definition=role_definition,
            texts=texts,
            expected_dimension=embedding_dimension,
        )
        debug_log("LLM", f"embeddings done vectors={len(vectors)}")
        return vectors

    # 設定補助
    def _debug_model(self, role_definition: dict) -> str:
        # 秘密情報を含まない model 名だけを出す。
        model = role_definition.get("model")
        if not isinstance(model, str) or not model.strip():
            return "-"
        return model.strip()

    def _debug_mode(self, role_definition: dict) -> str:
        # 実行経路
        return "mock" if self._is_mock_role_definition(role_definition) else "transport"

    def _debug_error(self, exc: BaseException) -> str:
        # 長い応答本文をログへ出しすぎない。
        message = str(exc).replace("\n", " ").strip()
        if len(message) <= 240:
            return message
        return message[:239] + "…"

    def _debug_payload_keys(self, payload: dict[str, Any]) -> str:
        # payload の中身ではなくキーだけを出す。
        keys = sorted(str(key) for key in payload.keys())[:8]
        return ",".join(keys) if keys else "-"

    def _debug_text_preview(self, value: Any) -> str:
        # 元文字列の先頭 200 文字だけを出す。
        if not isinstance(value, str):
            return "-"
        return value[:LLM_DEBUG_TEXT_PREVIEW_LIMIT].replace("\r", "\\r").replace("\n", "\\n")

    def _debug_text_length(self, value: Any) -> int:
        # 文字列以外は 0 扱いにする。
        if not isinstance(value, str):
            return 0
        return len(value)

    def _debug_messages_preview(
        self,
        *,
        operation: str,
        attempt: int,
        messages: list[dict[str, Any]],
    ) -> None:
        # LLMへ送る message content の先頭だけを出す。
        total = len(messages)
        for index, message in enumerate(messages, start=1):
            role = message.get("role") if isinstance(message, dict) else None
            content = message.get("content") if isinstance(message, dict) else None
            debug_log(
                "LLM",
                (
                    f"{operation} send attempt={attempt} message={index}/{total} "
                    f"role={role if isinstance(role, str) else '-'} chars={self._debug_text_length(content)} "
                    f"text={self._debug_text_preview(content)}"
                ),
            )

    def _debug_response_preview(self, *, operation: str, attempt: int, content: str) -> None:
        # LLMから返った文字列の先頭だけを出す。
        debug_log(
            "LLM",
            (
                f"{operation} recv attempt={attempt} chars={len(content)} "
                f"text={self._debug_text_preview(content)}"
            ),
        )

    def _debug_embedding_texts_preview(self, texts: list[str]) -> None:
        # embeddings に送る文字列の先頭だけを出す。
        total = len(texts)
        for index, text in enumerate(texts, start=1):
            debug_log(
                "LLM",
                (
                    f"embeddings send text={index}/{total} chars={self._debug_text_length(text)} "
                    f"text={self._debug_text_preview(text)}"
                ),
            )

    def _is_mock_role_definition(self, role_definition: dict) -> bool:
        # model=mock* は開発用の内蔵ロジックへ切り替える。
        model = role_definition.get("model")
        return isinstance(model, str) and model.strip().startswith("mock")

    def _embedding_dimension(self, role_definition: dict) -> int:
        return role_definition.get("embedding_dimension")

    def _generate_structured_payload(
        self,
        *,
        role_definition: dict,
        messages: list[dict[str, Any]],
        validator: Callable[[dict[str, Any]], None],
        repair_prompt_builder: Callable[[str], str],
        failure_message: str,
        wrap_validation_error: bool = False,
        operation: str = "structured",
    ) -> dict[str, Any]:
        last_error: LLMError | None = None
        attempt_messages = list(messages)
        for attempt in range(2):
            debug_log("LLM", f"{operation} attempt={attempt + 1} request messages={len(attempt_messages)}")
            self._debug_messages_preview(operation=operation, attempt=attempt + 1, messages=attempt_messages)
            content = complete_text(role_definition=role_definition, messages=attempt_messages)
            self._debug_response_preview(operation=operation, attempt=attempt + 1, content=content)
            try:
                payload = parse_json_object(content)
                try:
                    validator(payload)
                    debug_log(
                        "LLM",
                        (
                            f"{operation} done attempt={attempt + 1} response_chars={len(content)} "
                            f"keys={self._debug_payload_keys(payload)}"
                        ),
                    )
                    return payload
                except LLMError as exc:
                    last_error = LLMContractError(str(exc)) if wrap_validation_error else exc
                    debug_log(
                        "LLM",
                        f"{operation} validation_failed attempt={attempt + 1} error={self._debug_error(last_error)}",
                    )
            except LLMError as exc:
                last_error = exc
                debug_log(
                    "LLM",
                    f"{operation} parse_failed attempt={attempt + 1} error={self._debug_error(exc)}",
                )

            if attempt >= 1:
                if last_error is not None:
                    raise last_error
                raise LLMError(failure_message)

            attempt_messages = [
                *messages,
                {
                    "role": "assistant",
                    "content": content,
                },
                {
                    "role": "user",
                    "content": repair_prompt_builder(str(last_error)),
                },
            ]

        if last_error is not None:
            debug_log("LLM", f"{operation} failed error={self._debug_error(last_error)}")
            raise last_error
        debug_log("LLM", f"{operation} failed error={failure_message}")
        raise LLMError(failure_message)
