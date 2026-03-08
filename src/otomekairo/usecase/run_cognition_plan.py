"""Generate structured cognition plans for browser chat cycles."""

from __future__ import annotations

from typing import Any

from otomekairo.gateway.cognition_client import CognitionClient, CognitionPlanRequest


# Block: ブラウザチャット向け認知計画生成
def run_cognition_plan_for_browser_chat_input(
    *,
    cycle_id: str,
    input_kind: str,
    cognition_input: dict[str, Any],
    completion_settings: dict[str, Any],
    cognition_client: CognitionClient,
) -> dict[str, Any]:
    request = CognitionPlanRequest(
        cycle_id=cycle_id,
        input_kind=input_kind,
        cognition_input=cognition_input,
        completion_settings=completion_settings,
    )
    cognition_plan = cognition_client.generate_plan(request).cognition_plan
    return _validated_cognition_plan(cognition_plan)


# Block: 認知計画バリデーション
def _validated_cognition_plan(cognition_plan: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(cognition_plan, dict):
        raise RuntimeError("cognition_plan must be an object")
    required_keys = (
        "intention_summary",
        "decision_reason",
        "action_proposals",
        "step_hints",
        "memory_focus",
        "reflection_seed",
    )
    for key in required_keys:
        if key not in cognition_plan:
            raise RuntimeError(f"cognition_plan.{key} is required")
    intention_summary = cognition_plan["intention_summary"]
    decision_reason = cognition_plan["decision_reason"]
    action_proposals = cognition_plan["action_proposals"]
    step_hints = cognition_plan["step_hints"]
    memory_focus = cognition_plan["memory_focus"]
    reflection_seed = cognition_plan["reflection_seed"]
    if not isinstance(intention_summary, str) or not intention_summary.strip():
        raise RuntimeError("cognition_plan.intention_summary must be a non-empty string")
    if not isinstance(decision_reason, str) or not decision_reason.strip():
        raise RuntimeError("cognition_plan.decision_reason must be a non-empty string")
    if not isinstance(action_proposals, list):
        raise RuntimeError("cognition_plan.action_proposals must be a list")
    if not isinstance(step_hints, list):
        raise RuntimeError("cognition_plan.step_hints must be a list")
    if not isinstance(memory_focus, dict):
        raise RuntimeError("cognition_plan.memory_focus must be an object")
    if not isinstance(reflection_seed, dict):
        raise RuntimeError("cognition_plan.reflection_seed must be an object")
    message_id = reflection_seed.get("message_id")
    if not isinstance(message_id, str):
        raise RuntimeError("cognition_plan.reflection_seed.message_id must be a string")
    focus_kind = memory_focus.get("focus_kind")
    focus_summary = memory_focus.get("summary")
    if not isinstance(focus_kind, str) or not focus_kind:
        raise RuntimeError("cognition_plan.memory_focus.focus_kind must be a string")
    if not isinstance(focus_summary, str) or not focus_summary.strip():
        raise RuntimeError("cognition_plan.memory_focus.summary must be a non-empty string")
    return cognition_plan
