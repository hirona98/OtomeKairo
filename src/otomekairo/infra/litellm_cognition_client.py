"""LiteLLM-backed cognition client."""

from __future__ import annotations

import json
import os
from typing import Any

from otomekairo.gateway.cognition_client import (
    CognitionPlanRequest,
    CognitionPlanResponse,
    RetrievalSelectionRequest,
    RetrievalSelectionResponse,
    ReplyRenderRequest,
    ReplyRenderResponse,
)
from otomekairo.infra.logging_setup import configure_litellm_logger_bridge
from otomekairo.usecase.cognition_prompt_messages import (
    build_plan_messages,
    build_reply_render_messages,
    build_retrieval_selection_messages,
)


# Block: 対応しているチャット行動種別
SUPPORTED_CHAT_ACTION_TYPES = {"speak", "browse", "notify", "look", "wait"}


# Block: LiteLLM 認知クライアント
class LiteLLMCognitionClient:
    def __init__(self) -> None:
        self._litellm = _import_litellm_module()
        self._litellm.enable_json_schema_validation = True

    # Block: 想起候補選別の structured completion
    def select_retrieval_candidates(
        self,
        request: RetrievalSelectionRequest,
    ) -> RetrievalSelectionResponse:
        parsed_json = _run_structured_completion(
            litellm_module=self._litellm,
            completion_settings=request.completion_settings,
            messages=build_retrieval_selection_messages(request),
            response_format=_retrieval_selection_response_format(),
        )
        _validate_retrieval_selection_result(
            retrieval_selection_result=parsed_json,
            candidate_pack=request.candidate_pack,
        )
        return RetrievalSelectionResponse(retrieval_selection=parsed_json)

    # Block: 認知計画の structured completion
    def generate_plan(self, request: CognitionPlanRequest) -> CognitionPlanResponse:
        parsed_json = _run_structured_completion(
            litellm_module=self._litellm,
            completion_settings=request.completion_settings,
            messages=build_plan_messages(request),
            response_format=_cognition_plan_response_format(),
        )
        _validate_cognition_plan(parsed_json)
        return CognitionPlanResponse(cognition_plan=parsed_json)

    # Block: 応答文レンダリングの structured completion
    def render_reply(self, request: ReplyRenderRequest) -> ReplyRenderResponse:
        parsed_json = _run_structured_completion(
            litellm_module=self._litellm,
            completion_settings=request.completion_settings,
            messages=build_reply_render_messages(request),
            response_format=_reply_render_response_format(),
        )
        _validate_reply_render_result(parsed_json)
        return ReplyRenderResponse(speech_draft=parsed_json["speech_draft"])


# Block: Structured completion 実行
def _run_structured_completion(
    *,
    litellm_module: Any,
    completion_settings: dict[str, Any],
    messages: list[dict[str, Any]],
    response_format: dict[str, Any],
) -> dict[str, Any]:
    completion_arguments = {
        "model": str(completion_settings["model"]),
        "base_model": str(completion_settings["model"]),
        "messages": messages,
        "temperature": float(completion_settings["temperature"]),
        "max_tokens": int(completion_settings["max_output_tokens"]),
        "response_format": response_format,
    }
    api_key = str(completion_settings["api_key"])
    if api_key:
        completion_arguments["api_key"] = api_key
    api_base = str(completion_settings["base_url"])
    if api_base:
        completion_arguments["api_base"] = api_base
    response = litellm_module.completion(
        **completion_arguments,
    )
    return _parse_json_object(response)


# Block: LiteLLM import
def _import_litellm_module() -> Any:
    litellm_log_level = os.environ["LITELLM_LOG"]
    os.environ["LITELLM_LOG"] = "WARNING"
    import litellm

    os.environ["LITELLM_LOG"] = litellm_log_level
    configure_litellm_logger_bridge(
        litellm_log_level=litellm_log_level,
    )
    return litellm


# Block: 想起候補選別 response_format
def _retrieval_selection_response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "otomekairo_retrieval_selection",
            "strict": True,
            "schema": _retrieval_selection_schema(),
        },
    }


# Block: 認知計画 response_format
def _cognition_plan_response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "otomekairo_cognition_plan",
            "strict": True,
            "schema": _cognition_plan_schema(),
        },
    }


# Block: 応答文レンダリング response_format
def _reply_render_response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "otomekairo_reply_render",
            "strict": True,
            "schema": _reply_render_schema(),
        },
    }


# Block: 想起候補選別スキーマ
def _retrieval_selection_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["selected_item_refs", "selection_reason"],
        "properties": {
            "selected_item_refs": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "string",
                    "minLength": 1,
                },
            },
            "selection_reason": {
                "type": "string",
                "minLength": 1,
            },
        },
    }


# Block: 認知計画スキーマ
def _cognition_plan_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "intention_summary",
            "decision_reason",
            "action_proposals",
            "step_hints",
            "reply_policy",
            "memory_focus",
            "reflection_seed",
        ],
        "properties": {
            "intention_summary": {
                "type": "string",
                "minLength": 1,
            },
            "decision_reason": {
                "type": "string",
                "minLength": 1,
            },
            "action_proposals": {
                "type": "array",
                "items": _action_proposal_schema(),
            },
            "step_hints": {
                "type": "array",
                "items": {},
            },
            "reply_policy": {
                "type": "object",
                "additionalProperties": False,
                "required": ["mode", "reason"],
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["render", "none"],
                    },
                    "reason": {
                        "type": "string",
                        "minLength": 1,
                    },
                },
            },
            "memory_focus": {
                "type": "object",
                "additionalProperties": True,
                "required": ["focus_kind", "summary"],
                "properties": {
                    "focus_kind": {
                        "type": "string",
                        "minLength": 1,
                    },
                    "summary": {
                        "type": "string",
                        "minLength": 1,
                    },
                },
            },
            "reflection_seed": {
                "type": "object",
                "additionalProperties": True,
            },
        },
    }


# Block: 応答文レンダリング結果スキーマ
def _reply_render_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["speech_draft"],
        "properties": {
            "speech_draft": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "language", "delivery_mode"],
                "properties": {
                    "text": {
                        "type": "string",
                        "minLength": 1,
                    },
                    "language": {
                        "type": "string",
                        "minLength": 1,
                    },
                    "delivery_mode": {
                        "type": "string",
                        "const": "stream",
                    },
                },
            },
        },
    }


# Block: 行動候補スキーマ
def _action_proposal_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": True,
        "required": ["action_type", "priority"],
        "properties": {
            "action_type": {
                "type": "string",
                "enum": sorted(SUPPORTED_CHAT_ACTION_TYPES),
            },
            "priority": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
            },
            "target_channel": {
                "type": "string",
                "const": "browser_chat",
            },
            "query": {
                "type": "string",
                "minLength": 1,
            },
            "direction": {
                "type": "string",
                "enum": ["left", "right", "up", "down"],
            },
            "camera_connection_id": {
                "type": "string",
                "minLength": 1,
            },
            "preset_id": {
                "type": "string",
                "minLength": 1,
            },
            "preset_name": {
                "type": "string",
                "minLength": 1,
            },
        },
        "allOf": [
            {
                "if": {
                    "properties": {
                        "action_type": {
                            "enum": ["speak", "notify"],
                        },
                    },
                    "required": ["action_type"],
                },
                "then": {
                    "required": ["target_channel"],
                },
            },
            {
                "if": {
                    "properties": {
                        "action_type": {
                            "const": "browse",
                        },
                    },
                    "required": ["action_type"],
                },
                "then": {
                    "required": ["query"],
                },
            },
            {
                "if": {
                    "properties": {
                        "action_type": {
                            "const": "look",
                        },
                    },
                    "required": ["action_type"],
                },
                "then": {
                    "required": ["camera_connection_id"],
                    "anyOf": [
                        {"required": ["direction"]},
                        {"required": ["preset_id"]},
                        {"required": ["preset_name"]},
                    ],
                },
            },
        ],
    }


# Block: Completion 応答の JSON 化
def _parse_json_object(response: Any) -> dict[str, Any]:
    response_text = _extract_response_text(response)
    try:
        parsed_json = json.loads(response_text)
    except json.JSONDecodeError as error:
        raise RuntimeError("LiteLLM response is not valid JSON") from error
    if not isinstance(parsed_json, dict):
        raise RuntimeError("LiteLLM cognition_result must be a JSON object")
    return parsed_json


# Block: Completion 応答本文抽出
def _extract_response_text(response: Any) -> str:
    if not hasattr(response, "choices") or not response.choices:
        raise RuntimeError("LiteLLM response choices are missing")
    message = getattr(response.choices[0], "message", None)
    if message is None:
        raise RuntimeError("LiteLLM response message is missing")
    content = getattr(message, "content", None)
    if isinstance(content, str):
        response_text = content.strip()
    elif isinstance(content, list):
        text_parts = [part.get("text", "") for part in content if isinstance(part, dict)]
        response_text = "".join(text_parts).strip()
    else:
        response_text = ""
    if not response_text:
        raise RuntimeError("LiteLLM response content is empty")
    return response_text


# Block: 認知計画バリデーション
def _validate_cognition_plan(cognition_plan: dict[str, Any]) -> None:
    required_keys = {
        "intention_summary",
        "decision_reason",
        "action_proposals",
        "step_hints",
        "reply_policy",
        "memory_focus",
        "reflection_seed",
    }
    missing_keys = [key for key in sorted(required_keys) if key not in cognition_plan]
    if missing_keys:
        raise RuntimeError(f"LiteLLM cognition_plan keys are missing: {','.join(missing_keys)}")
    intention_summary = cognition_plan["intention_summary"]
    if not isinstance(intention_summary, str) or not intention_summary.strip():
        raise RuntimeError("LiteLLM cognition_plan.intention_summary must be a non-empty string")
    decision_reason = cognition_plan["decision_reason"]
    if not isinstance(decision_reason, str) or not decision_reason.strip():
        raise RuntimeError("LiteLLM cognition_plan.decision_reason must be a non-empty string")
    action_proposals = cognition_plan["action_proposals"]
    if not isinstance(action_proposals, list):
        raise RuntimeError("LiteLLM cognition_plan.action_proposals must be a list")
    _validate_action_proposals(action_proposals)
    if not isinstance(cognition_plan["step_hints"], list):
        raise RuntimeError("LiteLLM cognition_plan.step_hints must be a list")
    reply_policy = cognition_plan["reply_policy"]
    if not isinstance(reply_policy, dict):
        raise RuntimeError("LiteLLM cognition_plan.reply_policy must be an object")
    reply_mode = reply_policy.get("mode")
    if reply_mode not in {"render", "none"}:
        raise RuntimeError("LiteLLM cognition_plan.reply_policy.mode must be render or none")
    reply_reason = reply_policy.get("reason")
    if not isinstance(reply_reason, str) or not reply_reason.strip():
        raise RuntimeError("LiteLLM cognition_plan.reply_policy.reason must be a non-empty string")
    memory_focus = cognition_plan["memory_focus"]
    if not isinstance(memory_focus, dict):
        raise RuntimeError("LiteLLM cognition_plan.memory_focus must be an object")
    focus_kind = memory_focus.get("focus_kind")
    if not isinstance(focus_kind, str) or not focus_kind:
        raise RuntimeError("LiteLLM cognition_plan.memory_focus.focus_kind must be a string")
    focus_summary = memory_focus.get("summary")
    if not isinstance(focus_summary, str) or not focus_summary.strip():
        raise RuntimeError("LiteLLM cognition_plan.memory_focus.summary must be a non-empty string")
    reflection_seed = cognition_plan["reflection_seed"]
    if not isinstance(reflection_seed, dict):
        raise RuntimeError("LiteLLM cognition_plan.reflection_seed must be an object")
    message_id = reflection_seed.get("message_id")
    if not isinstance(message_id, str):
        raise RuntimeError("LiteLLM cognition_plan.reflection_seed.message_id must be a string")


# Block: 応答文レンダリング結果バリデーション
def _validate_reply_render_result(reply_render_result: dict[str, Any]) -> None:
    if "speech_draft" not in reply_render_result:
        raise RuntimeError("LiteLLM reply_render_result.speech_draft is required")
    speech_draft = reply_render_result["speech_draft"]
    if not isinstance(speech_draft, dict):
        raise RuntimeError("LiteLLM reply_render_result.speech_draft must be an object")
    speech_text = speech_draft.get("text")
    if not isinstance(speech_text, str) or not speech_text.strip():
        raise RuntimeError("LiteLLM reply_render_result.speech_draft.text must be a non-empty string")
    language = speech_draft.get("language")
    if language != "ja":
        raise RuntimeError("LiteLLM reply_render_result.speech_draft.language must be ja")
    delivery_mode = speech_draft.get("delivery_mode")
    if delivery_mode != "stream":
        raise RuntimeError("LiteLLM reply_render_result.speech_draft.delivery_mode must be stream")


# Block: 想起候補選別結果バリデーション
def _validate_retrieval_selection_result(
    *,
    retrieval_selection_result: dict[str, Any],
    candidate_pack: dict[str, Any],
) -> None:
    selected_item_refs = retrieval_selection_result.get("selected_item_refs")
    selection_reason = retrieval_selection_result.get("selection_reason")
    if not isinstance(selected_item_refs, list) or not selected_item_refs:
        raise RuntimeError("LiteLLM retrieval_selection_result.selected_item_refs must be a non-empty list")
    if not isinstance(selection_reason, str) or not selection_reason.strip():
        raise RuntimeError("LiteLLM retrieval_selection_result.selection_reason must be a non-empty string")
    candidate_entries = candidate_pack.get("candidate_entries")
    if not isinstance(candidate_entries, list) or not candidate_entries:
        raise RuntimeError("candidate_pack.candidate_entries must be a non-empty list")
    known_refs = {
        str(candidate_entry["item_ref"])
        for candidate_entry in candidate_entries
        if isinstance(candidate_entry, dict)
    }
    seen_refs: set[str] = set()
    for selected_item_ref in selected_item_refs:
        if not isinstance(selected_item_ref, str) or not selected_item_ref:
            raise RuntimeError("LiteLLM retrieval_selection_result.selected_item_refs must contain non-empty strings")
        if selected_item_ref not in known_refs:
            raise RuntimeError("LiteLLM retrieval_selection_result.selected_item_refs must reference known candidates")
        if selected_item_ref in seen_refs:
            raise RuntimeError("LiteLLM retrieval_selection_result.selected_item_refs must not contain duplicates")
        seen_refs.add(selected_item_ref)


# Block: 行動候補バリデーション
def _validate_action_proposals(action_proposals: list[Any]) -> None:
    for proposal in action_proposals:
        if not isinstance(proposal, dict):
            raise RuntimeError("LiteLLM cognition_result.action_proposals must contain only objects")
        action_type = proposal.get("action_type")
        if not isinstance(action_type, str) or not action_type:
            raise RuntimeError("LiteLLM cognition_result.action_proposals.action_type must be a non-empty string")
        if action_type not in SUPPORTED_CHAT_ACTION_TYPES:
            raise RuntimeError("LiteLLM cognition_result.action_proposals.action_type is not supported")
        priority = proposal.get("priority")
        if isinstance(priority, bool) or not isinstance(priority, (int, float)):
            raise RuntimeError("LiteLLM cognition_result.action_proposals.priority must be numeric")
        if float(priority) < 0.0 or float(priority) > 1.0:
            raise RuntimeError("LiteLLM cognition_result.action_proposals.priority must be within 0.0..1.0")
        if action_type in {"speak", "notify"}:
            target_channel = proposal.get("target_channel")
            if target_channel != "browser_chat":
                raise RuntimeError("LiteLLM cognition_result.action_proposals.speak_or_notify must target browser_chat")
        if action_type == "browse":
            query = proposal.get("query")
            if not isinstance(query, str) or not query.strip():
                raise RuntimeError("LiteLLM cognition_result.action_proposals.browse requires non-empty query")
        if action_type == "look":
            _validate_look_action(proposal)
        elif "target_channel" in proposal:
            target_channel = proposal["target_channel"]
            if not isinstance(target_channel, str) or not target_channel:
                raise RuntimeError("LiteLLM cognition_result.action_proposals.target_channel must be a string")


# Block: Look action validation
def _validate_look_action(proposal: dict[str, Any]) -> None:
    camera_connection_id = proposal.get("camera_connection_id")
    if not isinstance(camera_connection_id, str) or not camera_connection_id.strip():
        raise RuntimeError("LiteLLM cognition_result.action_proposals.look.camera_connection_id is required")
    direction = proposal.get("direction")
    preset_id = proposal.get("preset_id")
    preset_name = proposal.get("preset_name")
    if isinstance(direction, str) and direction.strip():
        if direction.strip() not in {"left", "right", "up", "down"}:
            raise RuntimeError("LiteLLM cognition_result.action_proposals.look.direction is invalid")
        if preset_id is not None or preset_name is not None:
            raise RuntimeError("LiteLLM cognition_result.action_proposals.look must not mix direction and preset")
        return
    if isinstance(preset_id, str) and preset_id.strip():
        if preset_name is not None:
            raise RuntimeError("LiteLLM cognition_result.action_proposals.look must specify only one preset field")
        return
    if isinstance(preset_name, str) and preset_name.strip():
        return
    raise RuntimeError("LiteLLM cognition_result.action_proposals.look requires direction or preset")
