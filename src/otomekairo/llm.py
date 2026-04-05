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

MEMORY_TYPE_VALUES = {
    "fact",
    "preference",
    "relation",
    "commitment",
    "interpretation",
    "summary",
}

MEMORY_STATUS_VALUES = {
    "inferred",
    "confirmed",
    "superseded",
    "revoked",
    "dormant",
}

COMMITMENT_STATE_VALUES = {
    "open",
    "waiting_confirmation",
    "on_hold",
    "done",
    "cancelled",
}

AFFECT_LAYER_VALUES = {
    "surface",
    "background",
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


def validate_memory_interpretation_contract(payload: dict[str, Any]) -> None:
    # Block: RequiredKeys
    required_keys = {
        "episode_digest",
        "candidate_memory_units",
        "affect_updates",
    }
    if set(payload.keys()) != required_keys:
        raise LLMError("MemoryInterpretation keys do not match the contract.")

    # Block: EpisodeDigestValidation
    episode_digest = payload["episode_digest"]
    required_episode_keys = {
        "episode_type",
        "primary_scope_type",
        "primary_scope_key",
        "summary_text",
        "outcome_text",
        "open_loops",
        "salience",
    }
    if not isinstance(episode_digest, dict) or set(episode_digest.keys()) != required_episode_keys:
        raise LLMError("MemoryInterpretation episode_digest is invalid.")
    if not isinstance(episode_digest["summary_text"], str) or not episode_digest["summary_text"].strip():
        raise LLMError("MemoryInterpretation episode_digest.summary_text is invalid.")
    if episode_digest["outcome_text"] is not None and not isinstance(episode_digest["outcome_text"], str):
        raise LLMError("MemoryInterpretation episode_digest.outcome_text is invalid.")
    if not isinstance(episode_digest["open_loops"], list):
        raise LLMError("MemoryInterpretation episode_digest.open_loops must be a list.")
    if not isinstance(episode_digest["salience"], (int, float)):
        raise LLMError("MemoryInterpretation episode_digest.salience must be numeric.")

    # Block: CandidateValidation
    if not isinstance(payload["candidate_memory_units"], list):
        raise LLMError("MemoryInterpretation candidate_memory_units must be a list.")
    for candidate in payload["candidate_memory_units"]:
        required_candidate_keys = {
            "memory_type",
            "scope_type",
            "scope_key",
            "subject_ref",
            "predicate",
            "object_ref_or_value",
            "summary_text",
            "status",
            "commitment_state",
            "confidence",
            "salience",
            "valid_from",
            "valid_to",
            "qualifiers",
            "reason",
        }
        if not isinstance(candidate, dict) or set(candidate.keys()) != required_candidate_keys:
            raise LLMError("MemoryInterpretation candidate_memory_unit is invalid.")
        if candidate["memory_type"] not in MEMORY_TYPE_VALUES:
            raise LLMError("MemoryInterpretation candidate_memory_unit.memory_type is invalid.")
        if candidate["status"] not in MEMORY_STATUS_VALUES:
            raise LLMError("MemoryInterpretation candidate_memory_unit.status is invalid.")
        if not isinstance(candidate["scope_type"], str) or not candidate["scope_type"].strip():
            raise LLMError("MemoryInterpretation candidate_memory_unit.scope_type is invalid.")
        if not isinstance(candidate["scope_key"], str) or not candidate["scope_key"].strip():
            raise LLMError("MemoryInterpretation candidate_memory_unit.scope_key is invalid.")
        if not isinstance(candidate["subject_ref"], str) or not candidate["subject_ref"].strip():
            raise LLMError("MemoryInterpretation candidate_memory_unit.subject_ref is invalid.")
        if not isinstance(candidate["predicate"], str) or not candidate["predicate"].strip():
            raise LLMError("MemoryInterpretation candidate_memory_unit.predicate is invalid.")
        if candidate["object_ref_or_value"] is not None and not isinstance(candidate["object_ref_or_value"], str):
            raise LLMError("MemoryInterpretation candidate_memory_unit.object_ref_or_value is invalid.")
        if not isinstance(candidate["summary_text"], str) or not candidate["summary_text"].strip():
            raise LLMError("MemoryInterpretation candidate_memory_unit.summary_text is invalid.")
        if candidate["commitment_state"] is not None and candidate["commitment_state"] not in COMMITMENT_STATE_VALUES:
            raise LLMError("MemoryInterpretation candidate_memory_unit.commitment_state is invalid.")
        if not isinstance(candidate["confidence"], (int, float)):
            raise LLMError("MemoryInterpretation candidate_memory_unit.confidence must be numeric.")
        if not isinstance(candidate["salience"], (int, float)):
            raise LLMError("MemoryInterpretation candidate_memory_unit.salience must be numeric.")
        if candidate["valid_from"] is not None and not isinstance(candidate["valid_from"], str):
            raise LLMError("MemoryInterpretation candidate_memory_unit.valid_from is invalid.")
        if candidate["valid_to"] is not None and not isinstance(candidate["valid_to"], str):
            raise LLMError("MemoryInterpretation candidate_memory_unit.valid_to is invalid.")
        if not isinstance(candidate["qualifiers"], dict):
            raise LLMError("MemoryInterpretation candidate_memory_unit.qualifiers must be an object.")
        if not isinstance(candidate["reason"], str) or not candidate["reason"].strip():
            raise LLMError("MemoryInterpretation candidate_memory_unit.reason is invalid.")

    # Block: AffectValidation
    if not isinstance(payload["affect_updates"], list):
        raise LLMError("MemoryInterpretation affect_updates must be a list.")
    for affect_update in payload["affect_updates"]:
        required_affect_keys = {
            "layer",
            "target_scope_type",
            "target_scope_key",
            "affect_label",
            "intensity",
        }
        if not isinstance(affect_update, dict) or set(affect_update.keys()) != required_affect_keys:
            raise LLMError("MemoryInterpretation affect_update is invalid.")
        if affect_update["layer"] not in AFFECT_LAYER_VALUES:
            raise LLMError("MemoryInterpretation affect_update.layer is invalid.")
        if not isinstance(affect_update["target_scope_type"], str) or not affect_update["target_scope_type"].strip():
            raise LLMError("MemoryInterpretation affect_update.target_scope_type is invalid.")
        if not isinstance(affect_update["target_scope_key"], str) or not affect_update["target_scope_key"].strip():
            raise LLMError("MemoryInterpretation affect_update.target_scope_key is invalid.")
        if not isinstance(affect_update["affect_label"], str) or not affect_update["affect_label"].strip():
            raise LLMError("MemoryInterpretation affect_update.affect_label is invalid.")
        if not isinstance(affect_update["intensity"], (int, float)):
            raise LLMError("MemoryInterpretation affect_update.intensity must be numeric.")


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
        self._assert_mock_model(profile)

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
        self._assert_mock_model(profile)

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
        self._assert_mock_model(profile)

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
            "confidence_note": "mock_model",
        }

    def generate_memory_interpretation(
        self,
        profile: dict,
        observation_text: str,
        recall_hint: dict,
        decision: dict,
        reply_text: str | None,
    ) -> dict[str, Any]:
        # Block: ProviderCheck
        self._assert_mock_model(profile)

        # Block: EpisodeDigest
        normalized = observation_text.strip()
        episode_digest = {
            "episode_type": self._mock_episode_type(recall_hint["primary_intent"]),
            "primary_scope_type": self._mock_primary_scope_type(recall_hint["primary_intent"]),
            "primary_scope_key": self._mock_primary_scope_key(recall_hint["primary_intent"]),
            "summary_text": normalized or "空の観測だった。",
            "outcome_text": reply_text or decision["reason_summary"],
            "open_loops": self._mock_open_loops(normalized, recall_hint["primary_intent"]),
            "salience": 0.72 if normalized else 0.2,
        }

        # Block: CandidateMemoryUnits
        candidate_memory_units = self._mock_candidate_memory_units(normalized)

        # Block: AffectUpdates
        affect_updates = self._mock_affect_updates(normalized)

        # Block: Payload
        payload = {
            "episode_digest": episode_digest,
            "candidate_memory_units": candidate_memory_units,
            "affect_updates": affect_updates,
        }
        validate_memory_interpretation_contract(payload)
        return payload

    def _mock_episode_type(self, primary_intent: str) -> str:
        # Block: Mapping
        if primary_intent in {"consult", "check_state"}:
            return "consultation"
        if primary_intent == "commitment_check":
            return "commitment_followup"
        if primary_intent == "preference_query":
            return "preference_talk"
        if primary_intent == "meta_relationship":
            return "relationship_check"
        return "conversation"

    def _mock_primary_scope_type(self, primary_intent: str) -> str:
        # Block: Mapping
        if primary_intent in {"commitment_check", "meta_relationship"}:
            return "relationship"
        return "user"

    def _mock_primary_scope_key(self, primary_intent: str) -> str:
        # Block: Mapping
        if primary_intent in {"commitment_check", "meta_relationship"}:
            return "self|user"
        return "user"

    def _mock_open_loops(self, normalized: str, primary_intent: str) -> list[str]:
        # Block: LoopRule
        if primary_intent in {"consult", "commitment_check", "reminisce"} and normalized:
            return [normalized[:80]]
        return []

    def _mock_candidate_memory_units(self, normalized: str) -> list[dict[str, Any]]:
        # Block: Empty
        if not normalized:
            return []

        # Block: Builders
        candidates: list[dict[str, Any]] = []

        if any(token in normalized for token in ("好き", "食べたい", "嫌い", "苦手")):
            candidates.append(
                {
                    "memory_type": "preference",
                    "scope_type": "user",
                    "scope_key": "user",
                    "subject_ref": "user",
                    "predicate": "likes",
                    "object_ref_or_value": self._mock_preference_object(normalized),
                    "summary_text": self._mock_preference_summary(normalized),
                    "status": "confirmed",
                    "commitment_state": None,
                    "confidence": 0.86,
                    "salience": 0.78,
                    "valid_from": None,
                    "valid_to": None,
                    "qualifiers": {
                        "polarity": self._mock_preference_polarity(normalized),
                    },
                    "reason": "発話中に好みや苦手の明示が含まれていたため。",
                }
            )

        if any(token in normalized for token in ("約束", "今度", "また話", "また今度", "後で")):
            candidates.append(
                {
                    "memory_type": "commitment",
                    "scope_type": "relationship",
                    "scope_key": "self|user",
                    "subject_ref": "self",
                    "predicate": "talk_again",
                    "object_ref_or_value": "topic:conversation",
                    "summary_text": "あなたと後で続きを話す流れが残っている。",
                    "status": "inferred",
                    "commitment_state": "open",
                    "confidence": 0.74,
                    "salience": 0.88,
                    "valid_from": None,
                    "valid_to": None,
                    "qualifiers": {},
                    "reason": "後続会話や約束を示す表現が含まれていたため。",
                }
            )

        if any(token in normalized for token in ("眠れて", "疲れ", "しんど", "つらい")):
            candidates.append(
                {
                    "memory_type": "interpretation",
                    "scope_type": "user",
                    "scope_key": "user",
                    "subject_ref": "user",
                    "predicate": "seems",
                    "object_ref_or_value": "state:tired",
                    "summary_text": "あなたは最近疲れや睡眠の問題を抱えていそうだ。",
                    "status": "inferred",
                    "commitment_state": None,
                    "confidence": 0.62,
                    "salience": 0.8,
                    "valid_from": None,
                    "valid_to": None,
                    "qualifiers": {
                        "domain": "health",
                    },
                    "reason": "体調や睡眠に関する示唆があったため。",
                }
            )

        return candidates

    def _mock_preference_object(self, normalized: str) -> str:
        # Block: Mapping
        if "辛" in normalized:
            return "food:spicy"
        if "甘" in normalized:
            return "food:sweet"
        if "食べ" in normalized:
            return "topic:food"
        return "preference:stated"

    def _mock_preference_summary(self, normalized: str) -> str:
        # Block: Mapping
        if "嫌い" in normalized or "苦手" in normalized:
            return "あなたには苦手な好みがある。"
        return "あなたにははっきりした好みがある。"

    def _mock_preference_polarity(self, normalized: str) -> str:
        # Block: Mapping
        if "嫌い" in normalized or "苦手" in normalized:
            return "negative"
        return "positive"

    def _mock_affect_updates(self, normalized: str) -> list[dict[str, Any]]:
        # Block: Builders
        updates: list[dict[str, Any]] = []
        if any(token in normalized for token in ("疲れ", "しんど", "つらい", "不安")):
            updates.append(
                {
                    "layer": "surface",
                    "target_scope_type": "user",
                    "target_scope_key": "user",
                    "affect_label": "concern",
                    "intensity": 0.72,
                }
            )
        if any(token in normalized for token in ("嬉しい", "楽しい", "安心")):
            updates.append(
                {
                    "layer": "surface",
                    "target_scope_type": "user",
                    "target_scope_key": "user",
                    "affect_label": "warmth",
                    "intensity": 0.65,
                }
            )
        return updates

    # Block: Helpers
    def _assert_mock_model(self, profile: dict) -> None:
        if profile.get("model") != "mock":
            raise LLMError(f"Unsupported mock model: {profile.get('model')}")


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
        # Block: MockPath
        if self._is_mock_profile(profile):
            return self.mock_client.generate_memory_interpretation(
                profile,
                observation_text,
                recall_hint,
                decision,
                reply_text,
            )

        # Block: PromptBuild
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

        # Block: Completion
        content = self._complete_text(profile=profile, role_settings=role_settings, messages=messages)
        payload = self._parse_json_object(content)
        validate_memory_interpretation_contract(payload)
        return payload

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

    def _build_memory_interpretation_system_prompt(self) -> str:
        # Block: Prompt
        return (
            "あなたは OtomeKairo の memory_interpretation です。\n"
            "会話 1 サイクルから episode_digest, candidate_memory_units, affect_updates を抽出し、JSON オブジェクト 1 個だけを返してください。\n"
            "Markdown、コードフェンス、説明文は禁止です。\n"
            "返すトップレベルキーは episode_digest, candidate_memory_units, affect_updates の 3 つだけです。\n"
            "candidate_memory_units は、今後の会話や判断に効く継続理解だけを入れてください。\n"
            "弱い雑談断片や一時判断は memory_unit にしないでください。\n"
            "memory_type は fact, preference, relation, commitment, interpretation, summary のいずれかです。\n"
            "status は inferred, confirmed, superseded, revoked, dormant のいずれかです。\n"
            "commitment_state は commitment のときだけ open, waiting_confirmation, on_hold, done, cancelled のいずれかを使い、それ以外では null にしてください。\n"
            "episode_digest.open_loops は短い文字列の配列にしてください。\n"
            "affect_updates は必要なときだけ返し、不要なら空配列にしてください。"
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
        # Block: Prompt
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
        return profile.get("model") == "mock"

    def _resolve_litellm_model(self, profile: dict) -> str:
        # Block: RawValue
        model = profile.get("model")
        if not isinstance(model, str) or not model.strip():
            raise LLMError("model_profile.model is missing.")
        return model.strip()

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
