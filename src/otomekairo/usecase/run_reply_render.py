"""Render reply drafts for browser chat cycles."""

from __future__ import annotations

from typing import Any

from otomekairo.gateway.cognition_client import CognitionClient, ReplyRenderRequest


# Block: ブラウザチャット向け応答文レンダリング
def run_reply_render_for_browser_chat_input(
    *,
    cycle_id: str,
    input_kind: str,
    cognition_input: dict[str, Any],
    cognition_plan: dict[str, Any],
    completion_settings: dict[str, Any],
    cognition_client: CognitionClient,
) -> dict[str, Any]:
    request = ReplyRenderRequest(
        cycle_id=cycle_id,
        input_kind=input_kind,
        cognition_input=cognition_input,
        cognition_plan=cognition_plan,
        completion_settings=completion_settings,
    )
    speech_draft = cognition_client.render_reply(request).speech_draft
    return _validated_speech_draft(speech_draft)


# Block: 応答草案バリデーション
def _validated_speech_draft(speech_draft: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(speech_draft, dict):
        raise RuntimeError("speech_draft must be an object")
    speech_text = speech_draft.get("text")
    if not isinstance(speech_text, str) or not speech_text.strip():
        raise RuntimeError("speech_draft.text must be a non-empty string")
    language = speech_draft.get("language")
    if language != "ja":
        raise RuntimeError("speech_draft.language must be ja")
    delivery_mode = speech_draft.get("delivery_mode")
    if delivery_mode != "stream":
        raise RuntimeError("speech_draft.delivery_mode must be stream")
    return speech_draft
