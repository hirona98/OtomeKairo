"""Deterministic cognition client for the current minimal runtime."""

from __future__ import annotations

from otomekairo.gateway.cognition_client import CognitionRequest, CognitionResponse


# Block: Deterministic cognition client
class DeterministicCognitionClient:
    def complete(self, request: CognitionRequest) -> CognitionResponse:
        current_observation = request.cognition_input["current_observation"]
        selection_profile = request.cognition_input["selection_profile"]
        current_emotion = request.cognition_input["persona_snapshot"]["current_emotion"]
        observed_text = str(current_observation["text"])
        speech_tone = str(selection_profile["interaction_style"]["speech_tone"])
        primary_label = str(current_emotion["primary_label"])
        response_text = (
            f"{_tone_prefix(speech_tone)}"
            f"いまは {primary_label} の状態で、"
            f"「{observed_text}」を受け取りました。"
            f"{_persona_tail(selection_profile)}"
        )
        return CognitionResponse(
            response_text=response_text,
            response_role="assistant",
        )


# Block: Speech helpers
def _tone_prefix(speech_tone: str) -> str:
    if speech_tone == "gentle":
        return "やわらかく受け止めます。"
    if speech_tone == "direct":
        return "率直に受け止めます。"
    return "落ち着いて受け止めます。"


def _persona_tail(selection_profile: dict[str, object]) -> str:
    trait_values = selection_profile["trait_values"]
    curiosity = _trait_value(trait_values, "curiosity")
    caution = _trait_value(trait_values, "caution")
    warmth = _trait_value(trait_values, "warmth")
    if curiosity >= 0.4:
        return "まずは周辺の文脈も確かめながら考えます。"
    if caution >= 0.4:
        return "急がず、前提を確かめながら進めます。"
    if warmth >= 0.4:
        return "相手との関係も意識しながら返します。"
    return "ここから次の判断を組み立てます。"


def _trait_value(trait_values: object, key: str) -> float:
    if not isinstance(trait_values, dict):
        return 0.0
    value = trait_values.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    numeric_value = float(value)
    if numeric_value < -1.0:
        return -1.0
    if numeric_value > 1.0:
        return 1.0
    return numeric_value
