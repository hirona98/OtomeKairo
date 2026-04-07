from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib import error as urllib_error
from urllib import request as urllib_request

from otomekairo.llm_contracts import (
    INTENT_VALUES,
    TIME_REFERENCE_VALUES,
    LLMError,
    validate_decision_contract,
    validate_memory_interpretation_contract,
    validate_recall_hint_contract,
)
from otomekairo.llm_mock import MockLLMClient


# Constants
OPENROUTER_DEFAULT_API_BASE = "https://openrouter.ai/api/v1"
OPENROUTER_DEFAULT_TIMEOUT_SECONDS = 600


# LiteLLMFacade
@dataclass(slots=True)
class LLMClient:
    mock_client: MockLLMClient = field(default_factory=MockLLMClient)

    def generate_recall_hint(
        self,
        *,
        profile: dict,
        role_settings: dict,
        observation_text: str,
        recent_turns: list[dict],
        current_time: str,
    ) -> dict[str, Any]:
        # MockPath
        if self._is_mock_profile(profile):
            return self.mock_client.generate_recall_hint(profile, observation_text, recent_turns, current_time)

        # PromptBuild
        messages = [
            {
                "role": "system",
                "content": self._build_recall_hint_system_prompt(),
            },
            {
                "role": "user",
                "content": self._build_recall_hint_user_prompt(observation_text, recent_turns, current_time),
            },
        ]

        # Retry
        last_contract_error: LLMError | None = None
        for attempt in range(2):
            content = self._complete_text(profile=profile, role_settings=role_settings, messages=messages)
            try:
                return self._parse_recall_hint_payload(content)
            except LLMError as exc:
                last_contract_error = exc
                if attempt >= 1:
                    raise

        # Failure
        if last_contract_error is not None:
            raise last_contract_error
        raise LLMError("RecallHint generation failed without a parseable response.")

    def generate_decision(
        self,
        *,
        profile: dict,
        role_settings: dict,
        observation_text: str,
        recent_turns: list[dict],
        time_context: dict[str, Any],
        affect_context: dict[str, list[dict[str, Any]]],
        recall_hint: dict,
        recall_pack: dict[str, Any],
    ) -> dict[str, Any]:
        # MockPath
        if self._is_mock_profile(profile):
            return self.mock_client.generate_decision(
                profile,
                observation_text,
                recent_turns,
                time_context,
                affect_context,
                recall_hint,
                recall_pack,
            )

        # PromptBuild
        messages = [
            {
                "role": "system",
                "content": self._build_decision_system_prompt(),
            },
            {
                "role": "user",
                "content": self._build_decision_user_prompt(
                    observation_text=observation_text,
                    recent_turns=recent_turns,
                    time_context=time_context,
                    affect_context=affect_context,
                    recall_hint=recall_hint,
                    recall_pack=recall_pack,
                ),
            },
        ]

        # Completion
        content = self._complete_text(profile=profile, role_settings=role_settings, messages=messages)
        payload = self._parse_json_object(content)
        validate_decision_contract(payload)
        return payload

    def generate_reply(
        self,
        *,
        profile: dict,
        role_settings: dict,
        persona: dict,
        observation_text: str,
        recent_turns: list[dict],
        time_context: dict[str, Any],
        affect_context: dict[str, list[dict[str, Any]]],
        recall_hint: dict,
        recall_pack: dict[str, Any],
        decision: dict,
    ) -> dict[str, Any]:
        # MockPath
        if self._is_mock_profile(profile):
            return self.mock_client.generate_reply(
                profile,
                persona,
                observation_text,
                recent_turns,
                time_context,
                affect_context,
                recall_hint,
                recall_pack,
                decision,
            )

        # PromptBuild
        messages = [
            {
                "role": "system",
                "content": self._build_reply_system_prompt(persona),
            },
            {
                "role": "user",
                "content": self._build_reply_user_prompt(
                    observation_text=observation_text,
                    recent_turns=recent_turns,
                    time_context=time_context,
                    affect_context=affect_context,
                    recall_hint=recall_hint,
                    recall_pack=recall_pack,
                    decision=decision,
                ),
            },
        ]

        # Completion
        content = self._complete_text(profile=profile, role_settings=role_settings, messages=messages)
        reply_text = content.strip()
        if not reply_text:
            raise LLMError("Reply generation returned empty content.")

        # Payload
        return {
            "reply_text": reply_text,
            "reply_style_notes": f"model={profile.get('model')}",
            "confidence_note": "litellm_model",
        }

    def generate_memory_interpretation(
        self,
        *,
        profile: dict,
        role_settings: dict,
        observation_text: str,
        recall_hint: dict,
        decision: dict,
        reply_text: str | None,
        current_time: str,
    ) -> dict[str, Any]:
        # MockPath
        if self._is_mock_profile(profile):
            return self.mock_client.generate_memory_interpretation(
                profile,
                observation_text,
                recall_hint,
                decision,
                reply_text,
            )

        # PromptBuild
        messages = [
            {
                "role": "system",
                "content": self._build_memory_interpretation_system_prompt(),
            },
            {
                "role": "user",
                "content": self._build_memory_interpretation_user_prompt(
                    observation_text=observation_text,
                    recall_hint=recall_hint,
                    decision=decision,
                    reply_text=reply_text,
                    current_time=current_time,
                ),
            },
        ]

        # Retry
        last_contract_error: LLMError | None = None
        attempt_messages = list(messages)
        for attempt in range(2):
            content = self._complete_text(profile=profile, role_settings=role_settings, messages=attempt_messages)
            try:
                payload = self._parse_json_object(content)
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
                        "content": self._build_memory_interpretation_repair_prompt(str(exc)),
                    },
                ]

        # Failure
        if last_contract_error is not None:
            raise last_contract_error
        raise LLMError("MemoryInterpretation generation failed without a parseable response.")

    def generate_embeddings(
        self,
        *,
        profile: dict,
        role_settings: dict,
        texts: list[str],
    ) -> list[list[float]]:
        # Empty
        if not texts:
            return []

        # Dimension
        embedding_dimension = role_settings.get("embedding_dimension")
        if not isinstance(embedding_dimension, int) or embedding_dimension <= 0:
            raise LLMError("embedding_dimension must be a positive integer.")

        # MockPath
        if self._is_mock_profile(profile):
            return self.mock_client.generate_embeddings(profile, texts, embedding_dimension)

        # OpenRouterPath
        if self._is_openrouter_embedding_profile(profile):
            response = self._request_openrouter_embeddings(profile=profile, texts=texts)
            return self._extract_embedding_vectors(
                response,
                expected_count=len(texts),
                expected_dimension=embedding_dimension,
                source_label="OpenRouter",
            )

        # Import
        embedding = self._load_litellm_embedding()

        # RequestBuild
        request_kwargs: dict[str, Any] = {
            "model": self._resolve_litellm_model(profile),
            "input": texts,
        }
        api_base = profile.get("base_url")
        if isinstance(api_base, str) and api_base.strip():
            request_kwargs["api_base"] = api_base.strip()
        api_key = self._resolve_api_key(profile)
        if api_key is not None:
            request_kwargs["api_key"] = api_key

        # Request
        try:
            response = embedding(**request_kwargs)
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"LiteLLM embedding call failed: {exc}") from exc

        # Result
        return self._extract_embedding_vectors(
            response,
            expected_count=len(texts),
            expected_dimension=embedding_dimension,
            source_label="LiteLLM",
        )

    # LiteLLMCall
    def _complete_text(
        self,
        *,
        profile: dict,
        role_settings: dict,
        messages: list[dict[str, str]],
    ) -> str:
        # Import
        completion = self._load_litellm_completion()

        # RequestBuild
        request_kwargs: dict[str, Any] = {
            "model": self._resolve_litellm_model(profile),
            "messages": messages,
        }
        api_base = profile.get("base_url")
        if isinstance(api_base, str) and api_base.strip():
            request_kwargs["api_base"] = api_base.strip()
        api_key = self._resolve_api_key(profile)
        if api_key is not None:
            request_kwargs["api_key"] = api_key
        max_tokens = role_settings.get("max_tokens")
        if isinstance(max_tokens, int) and max_tokens > 0:
            request_kwargs["max_tokens"] = max_tokens
        reasoning_effort = role_settings.get("reasoning_effort")
        if isinstance(reasoning_effort, str) and reasoning_effort.strip():
            request_kwargs["reasoning_effort"] = reasoning_effort.strip()

        # Request
        try:
            response = completion(**request_kwargs)
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"LiteLLM call failed: {exc}") from exc

        # ResponseExtract
        return self._extract_response_text(response)

    def _load_litellm_completion(self) -> Callable[..., Any]:
        # Import
        try:
            from litellm import completion
        except ImportError as exc:
            raise LLMError("LiteLLM is not installed. Run ./scripts/setup_venv.sh to install dependencies.") from exc

        return completion

    def _load_litellm_embedding(self) -> Callable[..., Any]:
        # Import
        try:
            from litellm import embedding
        except ImportError as exc:
            raise LLMError("LiteLLM is not installed. Run ./scripts/setup_venv.sh to install dependencies.") from exc

        return embedding

    # PromptHelpers
    def _build_recall_hint_system_prompt(self) -> str:
        # Prompt
        return (
            "あなたは OtomeKairo の recall_hint_generation です。\n"
            "観測文を分析し、JSON オブジェクト 1 個だけを返してください。\n"
            "Markdown、コードフェンス、説明文は禁止です。\n"
            "primary_intent は次のいずれかです: "
            + ", ".join(sorted(INTENT_VALUES))
            + "\n"
            "time_reference は次のいずれかです: "
            + ", ".join(sorted(TIME_REFERENCE_VALUES))
            + "\n"
            "返すキーは必ず次の 7 個です:\n"
            "- primary_intent: string\n"
            "- secondary_intents: string[] (最大2件。primary_intent を含めない)\n"
            "- confidence: number\n"
            "- time_reference: string\n"
            "- focus_scopes: string[] (最大4件。self / user / relationship:<key> / topic:<key> に留める)\n"
            "- mentioned_entities: string[] (最大4件)\n"
            "- mentioned_topics: string[] (最大4件)\n"
            "第三者名や固有名は focus_scopes ではなく mentioned_entities に入れてください。\n"
            "不確実なときは conservative に smalltalk / none / 空配列を選んでください。"
        )

    def _build_recall_hint_user_prompt(
        self,
        observation_text: str,
        recent_turns: list[dict],
        current_time: str,
    ) -> str:
        # Prompt
        return (
            f"current_time: {current_time}\n"
            f"recent_turns:\n{self._format_recent_turns(recent_turns)}\n"
            f"observation_text:\n{observation_text.strip()}\n"
        )

    def _build_decision_system_prompt(self) -> str:
        # Prompt
        return (
            "あなたは OtomeKairo の decision_generation です。\n"
            "観測文に対して reply / noop / future_act のいずれかを決め、JSON オブジェクト 1 個だけを返してください。\n"
            "Markdown、コードフェンス、説明文は禁止です。\n"
            "入力には recent_turns と internal_context が含まれます。\n"
            "internal_context には TimeContext, AffectContext, RecallPack が入ります。\n"
            "recall_hint.secondary_intents は補助意図として、継続性や確認必要性の補助にだけ使ってください。\n"
            "RecallPack.conflicts があるときは requires_confirmation=true を優先してください。\n"
            "active_commitments, episodic_evidence, event_evidence は reply と future_act の継続根拠に使ってください。\n"
            "future_act は『今は返さないが、後で触れる価値がある』場合だけ選んでください。\n"
            "明示的な会話要求に自然に返せるなら reply を優先し、future_act を乱用しないでください。\n"
            "返すキーは必ず次の 5 個です:\n"
            "- kind: \"reply\" または \"noop\" または \"future_act\"\n"
            "- reason_code: string\n"
            "- reason_summary: string\n"
            "- requires_confirmation: boolean\n"
            "- future_act: null または object\n"
            "kind が future_act のときだけ future_act object を返してください。\n"
            "future_act object のキーは intent_kind, intent_summary, dedupe_key の 3 個に固定してください。\n"
            "kind が future_act のとき requires_confirmation は false にしてください。\n"
            "空文字や意味のない入力は noop を選んでください。"
        )

    def _build_decision_user_prompt(
        self,
        *,
        observation_text: str,
        recent_turns: list[dict],
        time_context: dict[str, Any],
        affect_context: dict[str, list[dict[str, Any]]],
        recall_hint: dict,
        recall_pack: dict[str, Any],
    ) -> str:
        # Prompt
        return (
            f"recent_turns:\n{self._format_recent_turns(recent_turns)}\n"
            "internal_context:\n"
            f"{self._format_internal_context(time_context, affect_context, recall_pack)}\n"
            f"observation_text:\n{observation_text.strip()}\n"
            "recall_hint:\n"
            f"{json.dumps(recall_hint, ensure_ascii=False)}\n"
        )

    def _build_reply_system_prompt(self, persona: dict) -> str:
        # PersonaFields
        display_name = persona.get("display_name", "OtomeKairo")
        persona_text = persona.get("persona_text", "")
        second_person_label = persona.get("second_person_label", "あなた")
        addon_text = persona.get("addon_text", "")
        core_persona = json.dumps(persona.get("core_persona", {}), ensure_ascii=False)
        expression_style = json.dumps(persona.get("expression_style", {}), ensure_ascii=False)

        # Prompt
        return (
            f"あなたは {display_name} として話します。\n"
            "返答は自然な日本語の本文だけを返してください。JSON、箇条書き、見出し、引用符は禁止です。\n"
            "入力には recent_turns と internal_context が含まれます。\n"
            "internal_context には TimeContext, AffectContext, RecallPack が入ります。\n"
            "recall_hint.secondary_intents は話題継続や温度調整の補助にだけ使い、主方針は primary_intent に従ってください。\n"
            "RecallPack の内容だけを根拠に、必要な範囲で自然に思い出や継続文脈を混ぜてください。\n"
            "RecallPack.event_evidence は 1-3 件の短い証拠要約として扱い、必要なときだけ自然に参照してください。\n"
            "RecallPack.conflicts があるときは断定を避け、短い確認質問に寄せてください。\n"
            f"persona_text: {persona_text}\n"
            f"second_person_label: {second_person_label}\n"
            f"addon_text: {addon_text}\n"
            f"core_persona: {core_persona}\n"
            f"expression_style: {expression_style}\n"
            "断定確認が必要な場合は、短く確認質問に寄せてください。"
        )

    def _build_reply_user_prompt(
        self,
        *,
        observation_text: str,
        recent_turns: list[dict],
        time_context: dict[str, Any],
        affect_context: dict[str, list[dict[str, Any]]],
        recall_hint: dict,
        recall_pack: dict[str, Any],
        decision: dict,
    ) -> str:
        # Prompt
        return (
            f"recent_turns:\n{self._format_recent_turns(recent_turns)}\n"
            "internal_context:\n"
            f"{self._format_internal_context(time_context, affect_context, recall_pack)}\n"
            f"observation_text:\n{observation_text.strip()}\n"
            "recall_hint:\n"
            f"{json.dumps(recall_hint, ensure_ascii=False)}\n"
            "decision:\n"
            f"{json.dumps(decision, ensure_ascii=False)}\n"
        )

    def _build_memory_interpretation_system_prompt(self) -> str:
        # Prompt
        return (
            "あなたは OtomeKairo の memory_interpretation です。\n"
            "会話 1 サイクルから episode_digest, candidate_memory_units, affect_updates を抽出し、JSON オブジェクト 1 個だけを返してください。\n"
            "Markdown、コードフェンス、説明文は禁止です。\n"
            "返すトップレベルキーは episode_digest, candidate_memory_units, affect_updates の 3 つだけです。\n"
            "キー名は完全一致させ、余計なキーを足してはいけません。\n"
            "candidate_memory_units は、今後の会話や判断に効く継続理解だけを入れてください。\n"
            "弱い雑談断片や一時判断は memory_unit にしないでください。\n"
            "明示された生活状況、習慣、役割、現在の継続状態は fact を優先してください。\n"
            "明示訂正で以前の理解を置き換えるなら、replacement 候補を返し qualifiers.negates_previous=true を付けてください。\n"
            "否定だけで置換内容がない場合だけ status=revoked を使ってください。\n"
            "false ではないが前面に出さない理解だけを status=dormant にしてください。\n"
            "弱い単発推測や event に留めるべき断片は candidate_memory_units に入れず、結果として noop になってよいです。\n"
            "qualifiers には必要なら source=explicit_statement|explicit_correction|inference, negates_previous, replace_prior, allow_parallel を入れてください。\n"
            "memory_type は fact, preference, relation, commitment, interpretation, summary のいずれかです。\n"
            "status は inferred, confirmed, superseded, revoked, dormant のいずれかです。\n"
            "commitment_state は commitment のときだけ open, waiting_confirmation, on_hold, done, cancelled のいずれかを使い、それ以外では null にしてください。\n"
            "episode_digest は episode_type, primary_scope_type, primary_scope_key, summary_text, outcome_text, open_loops, salience の 7 キーだけを持つ object にしてください。\n"
            "candidate_memory_units の各要素は memory_type, scope_type, scope_key, subject_ref, predicate, object_ref_or_value, summary_text, status, commitment_state, confidence, salience, valid_from, valid_to, qualifiers, reason の 15 キーだけを持つ object にしてください。\n"
            "affect_updates の各要素は layer, target_scope_type, target_scope_key, affect_label, intensity の 5 キーだけを持つ object にしてください。\n"
            "affect_updates.layer は surface または background のどちらかだけを使ってください。\n"
            "感情更新に自信がない場合や、軽い雑談で持続的な感情状態が読めない場合は affect_updates を空配列にしてください。\n"
            "episode_digest.open_loops は短い文字列の配列にしてください。\n"
            "outcome_text, object_ref_or_value, valid_from, valid_to は不要なら null を入れてください。\n"
            "candidate_memory_units と affect_updates は不要なら空配列にしてください。\n"
            "例:\n"
            "{\n"
            '  "episode_digest": {\n'
            '    "episode_type": "conversation",\n'
            '    "primary_scope_type": "user",\n'
            '    "primary_scope_key": "user:default",\n'
            '    "summary_text": "ユーザーが軽いテスト発話をした。",\n'
            '    "outcome_text": null,\n'
            '    "open_loops": [],\n'
            '    "salience": 0.35\n'
            "  },\n"
            '  "candidate_memory_units": [],\n'
            '  "affect_updates": []\n'
            "}"
        )

    def _build_memory_interpretation_user_prompt(
        self,
        *,
        observation_text: str,
        recall_hint: dict,
        decision: dict,
        reply_text: str | None,
        current_time: str,
    ) -> str:
        # Prompt
        return (
            f"current_time: {current_time}\n"
            f"observation_text:\n{observation_text.strip()}\n"
            "recall_hint:\n"
            f"{json.dumps(recall_hint, ensure_ascii=False)}\n"
            "decision:\n"
            f"{json.dumps(decision, ensure_ascii=False)}\n"
            "reply_text:\n"
            f"{reply_text or '(none)'}\n"
        )

    def _build_memory_interpretation_repair_prompt(self, validation_error: str) -> str:
        # Prompt
        return (
            "前回の出力は memory_interpretation 契約を満たしていませんでした。\n"
            f"validator_error: {validation_error}\n"
            "同じ意味を保ったまま、JSON オブジェクト 1 個だけを返し直してください。\n"
            "トップレベルキーは episode_digest, candidate_memory_units, affect_updates の 3 つだけです。\n"
            "episode_digest には episode_type, primary_scope_type, primary_scope_key, summary_text, outcome_text, open_loops, salience だけを入れてください。\n"
            "candidate_memory_units の各要素には memory_type, scope_type, scope_key, subject_ref, predicate, object_ref_or_value, summary_text, status, commitment_state, confidence, salience, valid_from, valid_to, qualifiers, reason だけを入れてください。\n"
            "affect_updates の各要素には layer, target_scope_type, target_scope_key, affect_label, intensity だけを入れてください。\n"
            "affect_updates.layer は surface または background だけです。\n"
            "感情更新に自信がないなら affect_updates は空配列にしてください。\n"
            "余計なキー、説明文、Markdown、コードフェンスは禁止です。"
        )

    def _parse_recall_hint_payload(self, content: str) -> dict[str, Any]:
        # Parse
        payload = self._parse_json_object(content)
        validate_recall_hint_contract(payload)

        # Result
        return payload

    # ResponseHelpers
    def _extract_response_text(self, response: Any) -> str:
        # ChoiceRead
        choices = getattr(response, "choices", None)
        if choices is None and isinstance(response, dict):
            choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMError("LiteLLM response did not include choices.")

        message = getattr(choices[0], "message", None)
        if message is None and isinstance(choices[0], dict):
            message = choices[0].get("message")
        if message is None:
            raise LLMError("LiteLLM response did not include message.")

        # ContentRead
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")

        # ContentNormalize
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return self._flatten_content_parts(content)
        raise LLMError("LiteLLM response content was empty.")

    def _flatten_content_parts(self, content: list[Any]) -> str:
        # Flatten
        text_parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                text_parts.append(part)
                continue
            if not isinstance(part, dict):
                continue
            if isinstance(part.get("text"), str):
                text_parts.append(part["text"])
                continue
            if part.get("type") == "text" and isinstance(part.get("text"), str):
                text_parts.append(part["text"])
                continue
        result = "".join(text_parts).strip()
        if not result:
            raise LLMError("LiteLLM response content parts were empty.")
        return result

    def _parse_json_object(self, content: str) -> dict[str, Any]:
        # DirectParse
        stripped = content.strip()
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            payload = None

        # FenceFallback
        if payload is None:
            normalized = stripped.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            try:
                payload = json.loads(normalized)
            except json.JSONDecodeError:
                payload = None

        # BraceFallback
        if payload is None:
            start = stripped.find("{")
            end = stripped.rfind("}")
            if start >= 0 and end > start:
                try:
                    payload = json.loads(stripped[start : end + 1])
                except json.JSONDecodeError as exc:
                    raise LLMError(f"LiteLLM JSON parse failed: {exc}") from exc

        # ShapeCheck
        if not isinstance(payload, dict):
            raise LLMError("LiteLLM did not return a JSON object.")
        return payload

    def _extract_embedding_vectors(
        self,
        response: Any,
        *,
        expected_count: int,
        expected_dimension: int | None = None,
        source_label: str = "LiteLLM",
    ) -> list[list[float]]:
        # DataRead
        data = getattr(response, "data", None)
        if data is None and isinstance(response, dict):
            data = response.get("data")
        if not isinstance(data, list) or len(data) != expected_count:
            raise LLMError(f"{source_label} embedding response did not include expected data.")

        # Parse
        vectors: list[list[float]] = []
        for item in data:
            vector = getattr(item, "embedding", None)
            if vector is None and isinstance(item, dict):
                vector = item.get("embedding")
            if not isinstance(vector, list) or not vector:
                raise LLMError(f"{source_label} embedding item did not include embedding.")
            parsed = [float(value) for value in vector]
            if expected_dimension is not None and len(parsed) != expected_dimension:
                raise LLMError(
                    f"{source_label} embedding dimension mismatch: expected {expected_dimension}, got {len(parsed)}."
                )
            vectors.append(parsed)

        # Result
        return vectors

    # ConfigHelpers
    def _is_mock_profile(self, profile: dict) -> bool:
        return profile.get("model") == "mock"

    def _is_openrouter_embedding_profile(self, profile: dict) -> bool:
        # ModelCheck
        model = profile.get("model")
        if isinstance(model, str) and model.strip().startswith("openrouter/"):
            return True

        # BaseUrlCheck
        api_base = profile.get("base_url")
        if isinstance(api_base, str) and "openrouter.ai" in api_base:
            return True

        # Default
        return False

    def _resolve_litellm_model(self, profile: dict) -> str:
        # RawValue
        model = profile.get("model")
        if not isinstance(model, str) or not model.strip():
            raise LLMError("model_profile.model is missing.")
        return model.strip()

    def _resolve_openrouter_embedding_model(self, profile: dict) -> str:
        # Normalize
        model = self._resolve_litellm_model(profile)
        if model.startswith("openrouter/"):
            return model.removeprefix("openrouter/")
        return model

    def _resolve_openrouter_api_base(self, profile: dict) -> str:
        # CustomBase
        api_base = profile.get("base_url")
        if isinstance(api_base, str) and api_base.strip():
            normalized = api_base.strip().rstrip("/")
            if "openrouter.ai" in normalized and "/api/v1" not in normalized:
                normalized = f"{normalized}/api/v1"
            if normalized.endswith("/embeddings"):
                return normalized.rsplit("/", 1)[0]
            return normalized

        # DefaultBase
        return OPENROUTER_DEFAULT_API_BASE

    def _request_openrouter_embeddings(
        self,
        *,
        profile: dict,
        texts: list[str],
    ) -> dict[str, Any]:
        # ApiKey
        api_key = self._resolve_api_key(profile)
        if api_key is None:
            raise LLMError("OpenRouter embedding requires auth token.")

        # RequestData
        api_base = self._resolve_openrouter_api_base(profile)
        payload = {
            "model": self._resolve_openrouter_embedding_model(profile),
            "input": texts,
            "encoding_format": "float",
        }
        request = urllib_request.Request(
            url=f"{api_base}/embeddings",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        # ResponseRead
        try:
            with urllib_request.urlopen(request, timeout=OPENROUTER_DEFAULT_TIMEOUT_SECONDS) as response:
                body = response.read().decode("utf-8")
        except urllib_error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            detail = self._extract_http_error_detail(error_body)
            raise LLMError(f"OpenRouter embedding call failed: {exc.code} {detail}") from exc
        except urllib_error.URLError as exc:
            raise LLMError(f"OpenRouter embedding call failed: {exc.reason}") from exc

        # Parse
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise LLMError(f"OpenRouter embedding response was not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise LLMError("OpenRouter embedding response did not return an object.")
        return payload

    def _extract_http_error_detail(self, error_body: str) -> str:
        # Empty
        stripped = error_body.strip()
        if not stripped:
            return "unknown_error"

        # JsonParse
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            payload = None

        # ErrorMessage
        if isinstance(payload, dict):
            error_value = payload.get("error")
            if isinstance(error_value, dict):
                message = error_value.get("message")
                if isinstance(message, str) and message.strip():
                    return message.strip()
            if isinstance(error_value, str) and error_value.strip():
                return error_value.strip()
            message = payload.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()

        # Fallback
        return stripped

    def _resolve_api_key(self, profile: dict) -> str | None:
        # AuthRead
        auth = profile.get("auth")
        if not isinstance(auth, dict):
            return None
        if auth.get("type") == "none":
            return None

        # TokenResolve
        for key in ("token", "api_key", "key"):
            value = auth.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _format_internal_context(
        self,
        time_context: dict[str, Any],
        affect_context: dict[str, list[dict[str, Any]]],
        recall_pack: dict[str, Any],
    ) -> str:
        # Payload
        payload = {
            "time_context": time_context,
            "affect_context": affect_context,
            "recall_pack": self._compact_recall_pack(recall_pack),
        }

        # Result
        return json.dumps(payload, ensure_ascii=False)

    def _compact_recall_pack(self, recall_pack: dict[str, Any]) -> dict[str, Any]:
        # Payload
        return {
            "self_model": [self._compact_memory_context_item(item) for item in recall_pack.get("self_model", [])],
            "user_model": [self._compact_memory_context_item(item) for item in recall_pack.get("user_model", [])],
            "relationship_model": [self._compact_memory_context_item(item) for item in recall_pack.get("relationship_model", [])],
            "active_topics": [self._compact_topic_context_item(item) for item in recall_pack.get("active_topics", [])],
            "active_commitments": [self._compact_memory_context_item(item) for item in recall_pack.get("active_commitments", [])],
            "episodic_evidence": [self._compact_digest_context_item(item) for item in recall_pack.get("episodic_evidence", [])],
            "event_evidence": [self._compact_event_evidence_item(item) for item in recall_pack.get("event_evidence", [])],
            "conflicts": [self._compact_conflict_context_item(item) for item in recall_pack.get("conflicts", [])],
        }

    def _compact_memory_context_item(self, item: dict[str, Any]) -> dict[str, Any]:
        # Payload
        payload = {
            "memory_type": item["memory_type"],
            "scope_type": item["scope_type"],
            "scope_key": item["scope_key"],
            "summary_text": item["summary_text"],
        }
        if item.get("commitment_state") is not None:
            payload["commitment_state"] = item["commitment_state"]
        if item.get("object_ref_or_value") is not None:
            payload["object_ref_or_value"] = item["object_ref_or_value"]
        if item.get("retrieval_lane") is not None:
            payload["retrieval_lane"] = item["retrieval_lane"]

        # Result
        return payload

    def _compact_topic_context_item(self, item: dict[str, Any]) -> dict[str, Any]:
        # DigestTopic
        if item.get("source_kind") == "episode_digest":
            return self._compact_digest_context_item(item)

        # MemoryTopic
        return self._compact_memory_context_item(item)

    def _compact_digest_context_item(self, item: dict[str, Any]) -> dict[str, Any]:
        # Payload
        payload = {
            "primary_scope_type": item["primary_scope_type"],
            "primary_scope_key": item["primary_scope_key"],
            "summary_text": item["summary_text"],
            "open_loops": item.get("open_loops", []),
        }
        if item.get("outcome_text") is not None:
            payload["outcome_text"] = item["outcome_text"]
        if item.get("retrieval_lane") is not None:
            payload["retrieval_lane"] = item["retrieval_lane"]

        # Result
        return payload

    def _compact_conflict_context_item(self, item: dict[str, Any]) -> dict[str, Any]:
        # Payload
        return {
            "summary_text": item["summary_text"],
            "compare_key": item["compare_key"],
        }

    def _compact_event_evidence_item(self, item: dict[str, Any]) -> dict[str, Any]:
        # Payload
        payload = {
            "kind": item["kind"],
        }
        for key in ("anchor", "topic", "decision_or_result", "tone_or_note"):
            value = item.get(key)
            if value is None:
                continue
            payload[key] = value

        # Result
        return payload

    def _format_recent_turns(self, recent_turns: list[dict]) -> str:
        # Empty
        if not recent_turns:
            return "(none)"

        # Lines
        lines = []
        for turn in recent_turns:
            role = turn.get("role", "unknown")
            text = str(turn.get("text", "")).strip()
            lines.append(f"- {role}: {text}")
        return "\n".join(lines)
