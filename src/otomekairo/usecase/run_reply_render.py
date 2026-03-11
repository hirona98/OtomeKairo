"""Render reply drafts for browser chat cycles."""

from __future__ import annotations

from typing import Any

from otomekairo.gateway.cognition_client import CognitionClient, ReplyRenderRequest


# Block: ブラウザチャット向け応答文レンダリング
def run_reply_render_for_browser_chat_input(
    *,
    cycle_id: str,
    input_kind: str,
    reply_render_input: dict[str, Any],
    cognition_plan: dict[str, Any],
    completion_settings: dict[str, Any],
    cognition_client: CognitionClient,
) -> dict[str, Any]:
    request = ReplyRenderRequest(
        cycle_id=cycle_id,
        input_kind=input_kind,
        reply_render_input=reply_render_input,
        reply_render_plan=_build_reply_render_plan(cognition_plan),
        completion_settings=completion_settings,
    )
    speech_draft = cognition_client.render_reply(request).speech_draft
    return _validated_speech_draft(speech_draft)


# Block: 応答文レンダリング計画の抽出
def _build_reply_render_plan(cognition_plan: dict[str, Any]) -> dict[str, Any]:
    intention_summary = cognition_plan.get("intention_summary")
    decision_reason = cognition_plan.get("decision_reason")
    reply_policy = cognition_plan.get("reply_policy")
    memory_focus = cognition_plan.get("memory_focus")
    action_proposals = cognition_plan.get("action_proposals")
    if not isinstance(intention_summary, str) or not intention_summary.strip():
        raise RuntimeError("cognition_plan.intention_summary must be a non-empty string")
    if not isinstance(decision_reason, str) or not decision_reason.strip():
        raise RuntimeError("cognition_plan.decision_reason must be a non-empty string")
    if not isinstance(reply_policy, dict):
        raise RuntimeError("cognition_plan.reply_policy must be an object")
    if not isinstance(memory_focus, dict):
        raise RuntimeError("cognition_plan.memory_focus must be an object")
    if not isinstance(action_proposals, list):
        raise RuntimeError("cognition_plan.action_proposals must be a list")
    reply_mode = reply_policy.get("mode")
    reply_reason = reply_policy.get("reason")
    memory_focus_kind = memory_focus.get("focus_kind")
    memory_focus_summary = memory_focus.get("summary")
    if not isinstance(reply_mode, str) or not reply_mode:
        raise RuntimeError("cognition_plan.reply_policy.mode must be a non-empty string")
    if not isinstance(reply_reason, str) or not reply_reason:
        raise RuntimeError("cognition_plan.reply_policy.reason must be a non-empty string")
    if not isinstance(memory_focus_kind, str) or not memory_focus_kind:
        raise RuntimeError("cognition_plan.memory_focus.focus_kind must be a non-empty string")
    if not isinstance(memory_focus_summary, str) or not memory_focus_summary.strip():
        raise RuntimeError("cognition_plan.memory_focus.summary must be a non-empty string")
    return {
        "intention_summary": intention_summary.strip(),
        "decision_reason": decision_reason.strip(),
        "reply_mode": reply_mode,
        "reply_reason": reply_reason,
        "memory_focus_kind": memory_focus_kind,
        "memory_focus_summary": memory_focus_summary.strip(),
        "action_summaries": _action_summary_texts(action_proposals),
    }


# Block: 行動候補の要約
def _action_summary_texts(action_proposals: list[dict[str, Any]]) -> list[str]:
    action_summaries: list[str] = []
    for action_proposal in action_proposals[:5]:
        if not isinstance(action_proposal, dict):
            raise RuntimeError("cognition_plan.action_proposals must contain only objects")
        action_type = action_proposal.get("action_type")
        if not isinstance(action_type, str) or not action_type:
            raise RuntimeError("cognition_plan.action_proposals.action_type must be non-empty string")
        action_summaries.append(action_type)
    return action_summaries


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
