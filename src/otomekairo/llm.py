from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable


# Block: Errors
class LLMError(Exception):
    pass


# Block: Config
INTENT_VALUES = {
    "smalltalk",
    "reminisce",
    "commitment_check",
    "consult",
    "check_state",
    "preference_query",
    "fact_query",
    "meta_relationship",
}

TIME_REFERENCE_VALUES = {
    "none",
    "recent",
    "past",
    "future",
    "persistent",
}


# Block: ContractValidation
def validate_recall_hint_contract(payload: dict[str, Any]) -> None:
    # Block: RequiredKeys
    required_keys = {
        "primary_intent",
        "secondary_intents",
        "confidence",
        "time_reference",
        "focus_scopes",
        "mentioned_entities",
        "mentioned_topics",
    }
    if set(payload.keys()) != required_keys:
        raise LLMError("RecallHint keys do not match the contract.")

    # Block: ValueChecks
    if payload["primary_intent"] not in INTENT_VALUES:
        raise LLMError("RecallHint primary_intent is invalid.")
    if payload["time_reference"] not in TIME_REFERENCE_VALUES:
        raise LLMError("RecallHint time_reference is invalid.")
    if not isinstance(payload["secondary_intents"], list):
        raise LLMError("RecallHint secondary_intents must be a list.")
    if payload["primary_intent"] in payload["secondary_intents"]:
        raise LLMError("RecallHint duplicates primary intent.")
    if not isinstance(payload["focus_scopes"], list):
        raise LLMError("RecallHint focus_scopes must be a list.")
    if not isinstance(payload["mentioned_entities"], list):
        raise LLMError("RecallHint mentioned_entities must be a list.")
    if not isinstance(payload["mentioned_topics"], list):
        raise LLMError("RecallHint mentioned_topics must be a list.")
    if not isinstance(payload["confidence"], (int, float)):
        raise LLMError("RecallHint confidence must be numeric.")


def validate_decision_contract(payload: dict[str, Any]) -> None:
    # Block: RequiredKeys
    required_keys = {
        "kind",
        "reason_code",
        "reason_summary",
        "requires_confirmation",
    }
    if set(payload.keys()) != required_keys:
        raise LLMError("Decision keys do not match the contract.")

    # Block: ValueChecks
    if payload["kind"] not in {"reply", "noop"}:
        raise LLMError("Decision kind is invalid.")
    if not isinstance(payload["reason_code"], str) or not payload["reason_code"].strip():
        raise LLMError("Decision reason_code must be a non-empty string.")
    if not isinstance(payload["reason_summary"], str) or not payload["reason_summary"].strip():
        raise LLMError("Decision reason_summary must be a non-empty string.")
    if not isinstance(payload["requires_confirmation"], bool):
        raise LLMError("Decision requires_confirmation must be a boolean.")


# Block: MockClient
@dataclass(slots=True)
class MockLLMClient:
    def generate_recall_hint(
        self,
        profile: dict,
        observation_text: str,
        recent_turns: list[dict],
        current_time: str,
    ) -> dict[str, Any]:
        # Block: ProviderCheck
        self._assert_mock_provider(profile)

        # Block: HeuristicIntent
        normalized = observation_text.strip()
        lower_text = normalized.lower()

        primary_intent = "smalltalk"
        secondary_intents: list[str] = []
        time_reference = "none"

        if any(token in normalized for token in ("この前", "昨日", "前に", "続き")):
            primary_intent = "reminisce"
            time_reference = "past"
        elif any(token in normalized for token in ("約束", "今度", "また話", "また今度")):
            primary_intent = "commitment_check"
            time_reference = "future"
        elif any(token in normalized for token in ("相談", "どうしたら", "悩", "困って")):
            primary_intent = "consult"
            time_reference = "recent"
        elif any(token in normalized for token in ("元気", "大丈夫", "調子", "眠れて")):
            primary_intent = "check_state"
            time_reference = "recent"
        elif any(token in normalized for token in ("好き", "嫌い", "食べたい", "食べ")):
            primary_intent = "preference_query"
            time_reference = "persistent"
        elif any(token in normalized for token in ("関係", "距離", "話しにく")):
            primary_intent = "meta_relationship"
            time_reference = "recent"
        elif lower_text.endswith("?") or "?" in lower_text:
            primary_intent = "fact_query"

        # Block: SecondaryIntent
        if primary_intent in {"consult", "check_state"} and recent_turns:
            secondary_intents.append("reminisce")

        # Block: FocusScope
        focus_scopes = ["user"]
        if primary_intent == "meta_relationship":
            focus_scopes.append("relationship:self|user")
        if primary_intent == "preference_query":
            focus_scopes.append("topic:preference")
        if primary_intent == "commitment_check":
            focus_scopes.append("relationship:self|user")

        # Block: Payload
        payload = {
            "primary_intent": primary_intent,
            "secondary_intents": secondary_intents[:2],
            "confidence": 0.7 if normalized else 0.1,
            "time_reference": time_reference,
            "focus_scopes": focus_scopes[:4],
            "mentioned_entities": [],
            "mentioned_topics": [],
        }
        validate_recall_hint_contract(payload)
        return payload

    def generate_decision(
        self,
        profile: dict,
        observation_text: str,
        recall_hint: dict,
    ) -> dict[str, Any]:
        # Block: ProviderCheck
        self._assert_mock_provider(profile)

        # Block: DecisionRule
        normalized = observation_text.strip()
        if not normalized:
            payload = {
                "kind": "noop",
                "reason_code": "empty_observation",
                "reason_summary": "Observation text was empty after normalization.",
                "requires_confirmation": False,
            }
        else:
            payload = {
                "kind": "reply",
                "reason_code": f"intent:{recall_hint['primary_intent']}",
                "reason_summary": "A normal conversation reply is appropriate for the current observation.",
                "requires_confirmation": recall_hint["primary_intent"] in {"fact_query", "meta_relationship"},
            }

        # Block: Validation
        validate_decision_contract(payload)
        return payload

    def generate_reply(
        self,
        profile: dict,
        persona: dict,
        observation_text: str,
        recall_hint: dict,
        decision: dict,
    ) -> dict[str, Any]:
        # Block: ProviderCheck
        self._assert_mock_provider(profile)

        # Block: ReplyRule
        tone = persona["expression_style"]["tone"]
        primary_intent = recall_hint["primary_intent"]
        text = observation_text.strip()

        if primary_intent == "consult":
            reply_text = f"状況は受け取ったよ。{text} の中で、今いちばん困っている点をもう少し教えて。"
        elif primary_intent == "commitment_check":
            reply_text = f"その流れは覚えている前提で話すね。{text} に関して、今回どこまで進めたい？"
        elif primary_intent == "reminisce":
            reply_text = f"その続きとして受け取ったよ。{text} のどの部分からつなげたい？"
        elif primary_intent == "preference_query":
            reply_text = f"好みの話として受け取ったよ。{text} について、今の気分も含めて聞かせて。"
        elif decision["requires_confirmation"]:
            reply_text = f"断定せずに確認したい。{text} について、いまの受け取りで合っている？"
        else:
            reply_text = f"{tone}に受け取ったよ。{text}"

        # Block: Payload
        return {
            "reply_text": reply_text,
            "reply_style_notes": f"tone={tone}",
            "confidence_note": "mock_provider",
        }

    # Block: Helpers
    def _assert_mock_provider(self, profile: dict) -> None:
        if profile.get("provider") != "mock":
            raise LLMError(f"Unsupported provider: {profile.get('provider')}")


# Block: LiteLLMFacade
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
        # Block: MockPath
        if self._is_mock_profile(profile):
            return self.mock_client.generate_recall_hint(profile, observation_text, recent_turns, current_time)

        # Block: PromptBuild
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

        # Block: Completion
        content = self._complete_text(profile=profile, role_settings=role_settings, messages=messages)
        payload = self._parse_json_object(content)
        validate_recall_hint_contract(payload)
        return payload

    def generate_decision(
        self,
        *,
        profile: dict,
        role_settings: dict,
        observation_text: str,
        recall_hint: dict,
    ) -> dict[str, Any]:
        # Block: MockPath
        if self._is_mock_profile(profile):
            return self.mock_client.generate_decision(profile, observation_text, recall_hint)

        # Block: PromptBuild
        messages = [
            {
                "role": "system",
                "content": self._build_decision_system_prompt(),
            },
            {
                "role": "user",
                "content": self._build_decision_user_prompt(observation_text, recall_hint),
            },
        ]

        # Block: Completion
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
        recall_hint: dict,
        decision: dict,
    ) -> dict[str, Any]:
        # Block: MockPath
        if self._is_mock_profile(profile):
            return self.mock_client.generate_reply(profile, persona, observation_text, recall_hint, decision)

        # Block: PromptBuild
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
                    recall_hint=recall_hint,
                    decision=decision,
                ),
            },
        ]

        # Block: Completion
        content = self._complete_text(profile=profile, role_settings=role_settings, messages=messages)
        reply_text = content.strip()
        if not reply_text:
            raise LLMError("Reply generation returned empty content.")

        # Block: Payload
        return {
            "reply_text": reply_text,
            "reply_style_notes": f"provider={profile.get('provider')}",
            "confidence_note": "litellm_provider",
        }

    # Block: LiteLLMCall
    def _complete_text(
        self,
        *,
        profile: dict,
        role_settings: dict,
        messages: list[dict[str, str]],
    ) -> str:
        # Block: Import
        completion = self._load_litellm_completion()

        # Block: RequestBuild
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

        # Block: Request
        try:
            response = completion(**request_kwargs)
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"LiteLLM call failed: {exc}") from exc

        # Block: ResponseExtract
        return self._extract_response_text(response)

    def _load_litellm_completion(self) -> Callable[..., Any]:
        # Block: Import
        try:
            from litellm import completion
        except ImportError as exc:
            raise LLMError("LiteLLM is not installed. Run ./scripts/setup_venv.sh to install dependencies.") from exc

        return completion

    # Block: PromptHelpers
    def _build_recall_hint_system_prompt(self) -> str:
        # Block: Prompt
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
            "- focus_scopes: string[]\n"
            "- mentioned_entities: string[]\n"
            "- mentioned_topics: string[]\n"
            "不確実なときは conservative に smalltalk / none / 空配列を選んでください。"
        )

    def _build_recall_hint_user_prompt(
        self,
        observation_text: str,
        recent_turns: list[dict],
        current_time: str,
    ) -> str:
        # Block: Prompt
        return (
            f"current_time: {current_time}\n"
            f"recent_turns:\n{self._format_recent_turns(recent_turns)}\n"
            f"observation_text:\n{observation_text.strip()}\n"
        )

    def _build_decision_system_prompt(self) -> str:
        # Block: Prompt
        return (
            "あなたは OtomeKairo の decision_generation です。\n"
            "観測文に対して reply するか noop にするかを決め、JSON オブジェクト 1 個だけを返してください。\n"
            "Markdown、コードフェンス、説明文は禁止です。\n"
            "返すキーは必ず次の 4 個です:\n"
            "- kind: \"reply\" または \"noop\"\n"
            "- reason_code: string\n"
            "- reason_summary: string\n"
            "- requires_confirmation: boolean\n"
            "空文字や意味のない入力は noop を選んでください。"
        )

    def _build_decision_user_prompt(self, observation_text: str, recall_hint: dict) -> str:
        # Block: Prompt
        return (
            f"observation_text:\n{observation_text.strip()}\n"
            "recall_hint:\n"
            f"{json.dumps(recall_hint, ensure_ascii=False)}\n"
        )

    def _build_reply_system_prompt(self, persona: dict) -> str:
        # Block: PersonaFields
        display_name = persona.get("display_name", "OtomeKairo")
        persona_text = persona.get("persona_text", "")
        second_person_label = persona.get("second_person_label", "あなた")
        addon_text = persona.get("addon_text", "")
        core_persona = json.dumps(persona.get("core_persona", {}), ensure_ascii=False)
        expression_style = json.dumps(persona.get("expression_style", {}), ensure_ascii=False)

        # Block: Prompt
        return (
            f"あなたは {display_name} として話します。\n"
            "返答は自然な日本語の本文だけを返してください。JSON、箇条書き、見出し、引用符は禁止です。\n"
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
        recall_hint: dict,
        decision: dict,
    ) -> str:
        # Block: Prompt
        return (
            f"recent_turns:\n{self._format_recent_turns(recent_turns)}\n"
            f"observation_text:\n{observation_text.strip()}\n"
            "recall_hint:\n"
            f"{json.dumps(recall_hint, ensure_ascii=False)}\n"
            "decision:\n"
            f"{json.dumps(decision, ensure_ascii=False)}\n"
        )

    # Block: ResponseHelpers
    def _extract_response_text(self, response: Any) -> str:
        # Block: ChoiceRead
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

        # Block: ContentRead
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")

        # Block: ContentNormalize
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return self._flatten_content_parts(content)
        raise LLMError("LiteLLM response content was empty.")

    def _flatten_content_parts(self, content: list[Any]) -> str:
        # Block: Flatten
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
        # Block: DirectParse
        stripped = content.strip()
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            payload = None

        # Block: FenceFallback
        if payload is None:
            normalized = stripped.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            try:
                payload = json.loads(normalized)
            except json.JSONDecodeError:
                payload = None

        # Block: BraceFallback
        if payload is None:
            start = stripped.find("{")
            end = stripped.rfind("}")
            if start >= 0 and end > start:
                try:
                    payload = json.loads(stripped[start : end + 1])
                except json.JSONDecodeError as exc:
                    raise LLMError(f"LiteLLM JSON parse failed: {exc}") from exc

        # Block: ShapeCheck
        if not isinstance(payload, dict):
            raise LLMError("LiteLLM did not return a JSON object.")
        return payload

    # Block: ConfigHelpers
    def _is_mock_profile(self, profile: dict) -> bool:
        return profile.get("provider") == "mock"

    def _resolve_litellm_model(self, profile: dict) -> str:
        # Block: RawValues
        provider = profile.get("provider")
        model_name = profile.get("model_name")
        if not isinstance(provider, str) or not provider.strip():
            raise LLMError("model_profile.provider is missing.")
        if not isinstance(model_name, str) or not model_name.strip():
            raise LLMError("model_profile.model_name is missing.")

        # Block: ExistingPrefix
        if "/" in model_name:
            return model_name

        # Block: ProviderPrefix
        if provider == "openai_compatible":
            return f"openai/{model_name}"
        return f"{provider}/{model_name}"

    def _resolve_api_key(self, profile: dict) -> str | None:
        # Block: AuthRead
        auth = profile.get("auth")
        if not isinstance(auth, dict):
            return None
        if auth.get("type") == "none":
            return None

        # Block: TokenResolve
        for key in ("token", "api_key", "key"):
            value = auth.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _format_recent_turns(self, recent_turns: list[dict]) -> str:
        # Block: Empty
        if not recent_turns:
            return "(none)"

        # Block: Lines
        lines = []
        for turn in recent_turns:
            role = turn.get("role", "unknown")
            text = str(turn.get("text", "")).strip()
            lines.append(f"- {role}: {text}")
        return "\n".join(lines)
