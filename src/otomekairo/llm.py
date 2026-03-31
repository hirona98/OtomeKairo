from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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


# Block: PublicFacade
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
        self._validate_recall_hint(payload)
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
        if payload["kind"] not in {"reply", "noop"}:
            raise LLMError("Mock decision generated an invalid kind.")
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

    def _validate_recall_hint(self, payload: dict[str, Any]) -> None:
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

