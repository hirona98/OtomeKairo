"""Execute minimal cognition flow."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from otomekairo.gateway.camera_controller import CameraController
from otomekairo.gateway.camera_sensor import CameraSensor
from otomekairo.gateway.cognition_client import CognitionClient
from otomekairo.gateway.speech_synthesizer import SpeechSynthesizer
from otomekairo.schema.runtime_types import (
    ActionHistoryRecord,
    PendingInputMutationRecord,
    PendingInputRecord,
    TaskStateMutationRecord,
)
from otomekairo.usecase.dispatch_action_command import ActionDispatchResult, dispatch_chat_action_command
from otomekairo.usecase.run_cognition_plan import run_cognition_plan_for_browser_chat_input
from otomekairo.usecase.run_reply_render import run_reply_render_for_browser_chat_input
from otomekairo.usecase.validate_action import validate_chat_response_action


# Block: モジュールロガー
logger = logging.getLogger(__name__)


# Block: 認知実行結果
@dataclass(frozen=True, slots=True)
class CognitionExecution:
    cognition_input: dict[str, Any]
    cognition_result: dict[str, Any]
    ui_events: list[dict[str, Any]]
    action_results: list[ActionHistoryRecord]
    task_mutations: list[TaskStateMutationRecord]
    pending_input_mutations: list[PendingInputMutationRecord]


# Block: ブラウザチャット向け認知実行
def run_cognition_for_browser_chat_input(
    *,
    pending_input: PendingInputRecord,
    cycle_id: str,
    resolved_at: int,
    cognition_input: dict[str, Any],
    effective_settings: dict[str, Any],
    cognition_client: CognitionClient,
    camera_controller: CameraController,
    camera_sensor: CameraSensor,
    speech_synthesizer: SpeechSynthesizer,
    emit_ui_event: Callable[[dict[str, Any]], None],
    consume_cancel: Callable[[str], bool],
) -> CognitionExecution:
    completion_settings = _build_completion_settings(effective_settings)
    input_kind = str(pending_input.payload["input_kind"])
    message_id = _opaque_id("msg")
    ui_events: list[dict[str, Any]] = []

    # Block: 即時 UI イベント送信
    def emit_event(event_type: str, payload: dict[str, Any]) -> None:
        ui_event = {
            "channel": pending_input.channel,
            "event_type": event_type,
            "payload": payload,
        }
        ui_events.append(ui_event)
        emit_ui_event(ui_event)

    # Block: 初期ステータス送信
    emit_event(
        "status",
        {
            "status_code": "thinking",
            "label": _initial_status_label(pending_input),
            "cycle_id": cycle_id,
        },
    )

    # Block: 構造化された認知計画
    cognition_plan = run_cognition_plan_for_browser_chat_input(
        cycle_id=cycle_id,
        input_kind=input_kind,
        cognition_input=cognition_input,
        completion_settings=completion_settings,
        cognition_client=cognition_client,
    )
    logger.debug(
        "cognition plan generated",
        extra={
            "cycle_id": cycle_id,
            "input_id": pending_input.input_id,
            "input_kind": input_kind,
            "intention_summary": cognition_plan["intention_summary"],
            "memory_focus_kind": cognition_plan["memory_focus"]["focus_kind"],
            "action_proposal_count": len(cognition_plan["action_proposals"]),
        },
    )
    # Block: 応答文レンダリング
    speech_draft: dict[str, Any] | None = None
    response_text = ""
    if _requires_reply_render(cognition_plan):
        speech_draft = run_reply_render_for_browser_chat_input(
            cycle_id=cycle_id,
            input_kind=input_kind,
            cognition_input=cognition_input,
            cognition_plan=cognition_plan,
            completion_settings=completion_settings,
            cognition_client=cognition_client,
        )
        response_text = str(speech_draft["text"]).strip()
    cognition_result = _compose_cognition_result(
        cognition_plan=cognition_plan,
        speech_draft=speech_draft,
    )
    _merge_reflection_seed(
        cognition_result=cognition_result,
        pending_input=pending_input,
        cycle_id=cycle_id,
        message_id=message_id,
    )
    validated_action = validate_chat_response_action(
        pending_channel=pending_input.channel,
        message_id=message_id,
        cognition_input=cognition_input,
        cognition_result=cognition_result,
        response_text=response_text,
    )
    active_message_id = (
        str(validated_action.proposal["message_id"])
        if validated_action.proposal is not None
        else message_id
    )
    action_command = validated_action.action_command
    logger.info(
        "action decision resolved",
        extra={
            "cycle_id": cycle_id,
            "input_id": pending_input.input_id,
            "decision": validated_action.decision,
            "decision_reason": validated_action.decision_reason,
            "selected_action_type": (
                validated_action.proposal["action_type"]
                if validated_action.proposal is not None
                else None
            ),
            "command_type": (
                action_command["command_type"]
                if action_command is not None
                else None
            ),
        },
    )
    dispatch_result = _dispatch_result_for_decision(
        pending_input=pending_input,
        cycle_id=cycle_id,
        resolved_at=resolved_at,
        action_command=action_command,
        decision=validated_action.decision,
        response_text=response_text,
        message_id=active_message_id,
        emit_ui_event=emit_event,
        consume_cancel=lambda: consume_cancel(active_message_id),
        camera_controller=camera_controller,
        camera_sensor=camera_sensor,
        speech_synthesizer=speech_synthesizer,
        effective_settings=effective_settings,
    )
    cognition_result["reflection_seed"]["token_count"] = int(
        dispatch_result.observed_effects.get("token_count", 0)
    )
    cognition_result["reflection_seed"]["was_cancelled"] = bool(
        dispatch_result.observed_effects.get("was_cancelled", False)
    )
    logger.debug(
        "action dispatch finished",
        extra={
            "cycle_id": cycle_id,
            "input_id": pending_input.input_id,
            "action_type": dispatch_result.action_type,
            "dispatch_status": dispatch_result.status,
            "emitted_event_types": dispatch_result.emitted_event_types,
            "followup_input_count": len(dispatch_result.pending_input_mutations),
        },
    )

    # Block: 行動履歴の組み立て
    emitted_event_types = dispatch_result.emitted_event_types
    command_payload = _action_history_command(
        pending_input=pending_input,
        validated_action=validated_action,
        action_command=action_command,
        emitted_event_types=emitted_event_types,
    )
    observed_effects = {
        "emitted_event_types": emitted_event_types,
        **dispatch_result.observed_effects,
        "validator_decision": validated_action.decision,
        "validator_reason": validated_action.decision_reason,
        "action_candidate_score": validated_action.action_candidate_score,
    }
    if validated_action.proposal is not None:
        observed_effects["selected_action_type"] = str(validated_action.proposal["action_type"])
    action_results = [
        ActionHistoryRecord(
            result_id=_opaque_id("actres"),
            command_id=_action_command_id(
                decision=validated_action.decision,
                action_command=action_command,
            ),
            action_type=dispatch_result.action_type,
            command=command_payload,
            started_at=resolved_at,
            finished_at=dispatch_result.finished_at,
            status=dispatch_result.status,
            failure_mode=dispatch_result.failure_mode,
            observed_effects=observed_effects,
            raw_result_ref=dispatch_result.raw_result_ref,
            adapter_trace_ref={
                "cognition_plan": cognition_plan,
                "cognition_result": cognition_result,
                "action_command": action_command,
                "action_candidate_score": validated_action.action_candidate_score,
                "dispatch_trace": dispatch_result.adapter_trace_ref,
                **({"speech_draft": speech_draft} if speech_draft is not None else {}),
            },
        )
    ]
    return CognitionExecution(
        cognition_input=cognition_input,
        cognition_result=cognition_result,
        ui_events=ui_events,
        action_results=action_results,
        task_mutations=dispatch_result.task_mutations,
        pending_input_mutations=dispatch_result.pending_input_mutations,
    )


# Block: ID 生成
def _opaque_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


# Block: Completion 設定の正規化
def _build_completion_settings(effective_settings: dict[str, Any]) -> dict[str, Any]:
    model = effective_settings.get("llm.model")
    api_key = effective_settings.get("llm.api_key")
    base_url = effective_settings.get("llm.base_url")
    temperature = effective_settings.get("llm.temperature")
    max_output_tokens = effective_settings.get("llm.max_output_tokens")
    if not isinstance(model, str) or not model:
        raise RuntimeError("llm.model must be a non-empty string")
    if not isinstance(api_key, str):
        raise RuntimeError("llm.api_key must be a string")
    if not isinstance(base_url, str):
        raise RuntimeError("llm.base_url must be a string")
    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
        raise RuntimeError("llm.temperature must be numeric")
    if isinstance(max_output_tokens, bool) or not isinstance(max_output_tokens, int):
        raise RuntimeError("llm.max_output_tokens must be integer")
    return {
        "model": model,
        "api_key": api_key,
        "base_url": base_url,
        "temperature": float(temperature),
        "max_output_tokens": max_output_tokens,
    }


# Block: 認知結果の合成
def _compose_cognition_result(
    *,
    cognition_plan: dict[str, Any],
    speech_draft: dict[str, Any] | None,
) -> dict[str, Any]:
    cognition_result = {
        **cognition_plan,
    }
    if speech_draft is not None:
        cognition_result["speech_draft"] = speech_draft
    return cognition_result


# Block: 応答文レンダリング要否
def _requires_reply_render(cognition_plan: dict[str, Any]) -> bool:
    reply_policy = cognition_plan.get("reply_policy")
    if not isinstance(reply_policy, dict):
        raise RuntimeError("cognition_plan.reply_policy must be an object")
    reply_mode = reply_policy.get("mode")
    if reply_mode not in {"render", "none"}:
        raise RuntimeError("cognition_plan.reply_policy.mode must be render or none")
    action_proposals = cognition_plan.get("action_proposals")
    if not isinstance(action_proposals, list):
        raise RuntimeError("cognition_plan.action_proposals must be a list")
    visible_action_types = set()
    for proposal in action_proposals:
        if not isinstance(proposal, dict):
            raise RuntimeError("cognition_plan.action_proposals must contain only objects")
        action_type = proposal.get("action_type")
        if not isinstance(action_type, str) or not action_type:
            raise RuntimeError("cognition_plan.action_proposals.action_type must be a non-empty string")
        if action_type in {"speak", "notify", "look", "browse", "wait"}:
            visible_action_types.add(action_type)
    if reply_mode == "render":
        if not visible_action_types:
            raise RuntimeError("cognition_plan.reply_policy.mode=render requires visible action proposals")
        return True
    if {"speak", "notify"} & visible_action_types:
        raise RuntimeError("cognition_plan.reply_policy.mode=none is invalid for speak or notify")
    return False


# Block: 初期ステータス文言
def _initial_status_label(pending_input: PendingInputRecord) -> str:
    input_kind = str(pending_input.payload["input_kind"])
    if input_kind == "chat_message":
        return "入力を処理しています"
    if input_kind == "microphone_message":
        return "音声入力を処理しています"
    if input_kind == "camera_observation":
        return "カメラ画像を観測しています"
    if input_kind == "network_result":
        return "検索結果をもとに応答を準備しています"
    if input_kind == "idle_tick":
        return "アイドル状態を点検しています"
    raise RuntimeError("unsupported input_kind for cognition status")


# Block: Reflection seed マージ
def _merge_reflection_seed(
    *,
    cognition_result: dict[str, Any],
    pending_input: PendingInputRecord,
    cycle_id: str,
    message_id: str,
) -> None:
    existing_seed = cognition_result.get("reflection_seed")
    merged_seed = dict(existing_seed) if isinstance(existing_seed, dict) else {}
    merged_seed["cycle_id"] = cycle_id
    merged_seed["input_kind"] = str(pending_input.payload["input_kind"])
    merged_seed["message_id"] = message_id
    merged_seed["token_count"] = 0
    merged_seed["was_cancelled"] = False
    cognition_result["reflection_seed"] = merged_seed


# Block: 行動履歴種別の決定
def _action_history_type(*, decision: str, command_type: str | None) -> str:
    if decision == "hold":
        return "hold_chat_response"
    if decision == "reject":
        return "reject_chat_response"
    if command_type == "enqueue_browse_task":
        return "enqueue_browse_task"
    if command_type == "dispatch_notice":
        return "dispatch_notice"
    if command_type == "control_camera_look":
        return "control_camera_look"
    return "emit_chat_response"


# Block: Dispatch 振り分け
def _dispatch_result_for_decision(
    *,
    pending_input: PendingInputRecord,
    cycle_id: str,
    resolved_at: int,
    action_command: dict[str, Any] | None,
    decision: str,
    response_text: str,
    message_id: str,
    emit_ui_event: Callable[[str, dict[str, Any]], None],
    consume_cancel: Callable[[], bool],
    camera_controller: CameraController,
    camera_sensor: CameraSensor,
    speech_synthesizer: SpeechSynthesizer,
    effective_settings: dict[str, Any],
) -> ActionDispatchResult:
    if decision != "execute":
        emitted_event_types: list[str] = []
        final_message_emitted = False
        if decision == "hold" and response_text:
            emit_ui_event(
                "message",
                {
                    "message_id": message_id,
                    "role": "assistant",
                    "text": response_text,
                    "created_at": _now_ms(),
                    "source_cycle_id": cycle_id,
                    "related_input_id": pending_input.input_id,
                },
            )
            emitted_event_types.append("message")
            final_message_emitted = True
        emit_ui_event(
            "status",
            {
                "status_code": "idle",
                "label": "待機中",
                "cycle_id": cycle_id,
            },
        )
        emitted_event_types.append("status")
        command_type = (
            str(action_command["command_type"])
            if action_command is not None
            else None
        )
        return ActionDispatchResult(
            action_type=_action_history_type(
                decision=decision,
                command_type=command_type,
            ),
            emitted_event_types=emitted_event_types,
            observed_effects={
                "status_code_after": "idle",
                "final_message_emitted": final_message_emitted,
                **({"message_id": message_id} if final_message_emitted else {}),
            },
            task_mutations=[],
            pending_input_mutations=[],
            finished_at=_now_ms(),
            status="succeeded",
            failure_mode=None,
            raw_result_ref=None,
            adapter_trace_ref=None,
        )
    if action_command is None:
        raise RuntimeError("execute decision requires action_command")
    return dispatch_chat_action_command(
        pending_input={
            "input_id": pending_input.input_id,
            "channel": pending_input.channel,
        },
        cycle_id=cycle_id,
        resolved_at=resolved_at,
        action_command=action_command,
        emit_ui_event=lambda ui_event: emit_ui_event(ui_event["event_type"], ui_event["payload"]),
        consume_cancel=lambda _: consume_cancel(),
        camera_controller=camera_controller,
        camera_sensor=camera_sensor,
        speech_synthesizer=speech_synthesizer,
        effective_settings=effective_settings,
    )


# Block: 行動履歴コマンド生成
def _action_history_command(
    *,
    pending_input: PendingInputRecord,
    validated_action: Any,
    action_command: dict[str, Any] | None,
    emitted_event_types: list[str],
) -> dict[str, Any]:
    if validated_action.decision != "execute" or action_command is None:
        command_payload = {
            "target_channel": pending_input.channel,
            "event_types": emitted_event_types,
            "decision": validated_action.decision,
            "decision_reason": validated_action.decision_reason,
            "related_input_id": pending_input.input_id,
        }
        if validated_action.proposal is not None:
            command_payload["proposal_ref"] = str(validated_action.proposal["proposal_id"])
            if "message" in emitted_event_types:
                command_payload["message_id"] = str(validated_action.proposal["message_id"])
                command_payload["role"] = "assistant"
        return command_payload
    return {
        **action_command,
        "event_types": emitted_event_types,
        "decision": validated_action.decision,
        "decision_reason": validated_action.decision_reason,
        "related_input_id": pending_input.input_id,
    }


# Block: 行動コマンド ID
def _action_command_id(*, decision: str, action_command: dict[str, Any] | None) -> str:
    if decision == "execute":
        if action_command is None:
            raise RuntimeError("execute decision requires action_command")
        return str(action_command["command_id"])
    return _opaque_id("cmd")


# Block: 現在時刻ヘルパー
def _now_ms() -> int:
    return int(time.time() * 1000)
