import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from otomekairo.llm.client import LLMClient
from otomekairo.llm.transport import CompletionResult
from otomekairo.llm.contexts import AutonomousStepContext, CurrentInput, DecisionContext, PersonaContext
from otomekairo.llm.contracts import (
    LLMError,
    build_decision_target_stances_for_kind,
    validate_autonomous_completion_review_contract,
    validate_decision_contract,
)
from otomekairo.llm.prompts import (
    _build_speech_system_prompt,
    build_autonomous_completion_review_messages,
    build_autonomous_step_messages,
    build_autonomous_step_repair_prompt,
    build_decision_messages,
    build_decision_repair_prompt,
    _build_decision_trigger_policy,
)


def _llm_client() -> LLMClient:
    client = LLMClient()
    client.push_usage_scope("test")
    return client


def _completion(text: str) -> CompletionResult:
    return CompletionResult(text=text, usage={})


def _persona_context() -> PersonaContext:
    return PersonaContext(
        display_name="Test",
        initiative_baseline={"level": "medium", "summary_text": "test"},
        persona_prompt_text="テスト人格。",
        expression_addon=None,
        use_policy="テスト判断に使う。",
    )


def _current_input() -> CurrentInput:
    return CurrentInput(
        sender_kind="person",
        sender_ref="person:test",
        source_kind="user_message",
        response_target_refs=("person:test",),
        interaction_context=None,
        text="通知を確認して。",
    )


def _mcp_capability_view() -> list[dict]:
    return [
        {
            "id": "mcp.call_tool",
            "available": True,
            "unavailable_reason": None,
            "mcp_servers": [
                {
                    "mcp_server_id": "elyth",
                    "available": True,
                    "tools": [
                        {
                            "name": "get_notifications",
                            "input_schema": {
                                "type": "object",
                                "properties": {"limit": {"type": "integer", "minimum": 1}},
                                "additionalProperties": False,
                            },
                        }
                    ],
                }
            ],
        }
    ]


def _decision_context(capability_decision_view: list[dict]) -> DecisionContext:
    return DecisionContext(
        input_text="通知を確認して。",
        current_input=_current_input(),
        trigger_kind="user_message",
        recent_turns=[],
        time_context={},
        affect_context={},
        drive_state_summary=None,
        foreground_world_state=None,
        activity_context=None,
        ongoing_action_summary=None,
        autonomous_run_summaries=None,
        capability_decision_view=capability_decision_view,
        initiative_context=None,
        capability_result_context=None,
        visual_observation_context=None,
        self_state_context=None,
        relationship_context=None,
        prediction_error_context=None,
        default_mode_context=None,
        workspace_context=None,
        recall_hint={},
        recall_pack={},
    )


def _capability_decision(capability_id: str, input_payload: dict) -> dict:
    return {
        "kind": "capability_request",
        "reason_code": "capability:test",
        "reason_summary": "必要な能力を実行する。",
        "requires_confirmation": False,
        "pending_intent": None,
        "capability_request": {
            "capability_id": capability_id,
            "input": input_payload,
        },
        "autonomous_run": None,
        "foreground_selection": {
            "primary_factor_ref": None,
            "supporting_factor_refs": [],
            "suppressed_factors": [],
            "summary_text": "能力要求を選んだ。",
        },
        "target_stances": build_decision_target_stances_for_kind(
            "capability_request",
            required_targets=("outward_speech", "self_activity"),
            reason_summary="必要な能力を実行する。",
        ),
    }


class DecisionContractTests(unittest.TestCase):
    def test_decision_contract_requires_foreground_selection(self) -> None:
        payload = {
            "kind": "speech",
            "reason_code": "reply",
            "reason_summary": "ユーザー発話へ返す。",
            "requires_confirmation": False,
            "pending_intent": None,
            "capability_request": None,
            "autonomous_run": None,
        }

        with self.assertRaises(LLMError):
            validate_decision_contract(payload)

    def test_decision_contract_accepts_foreground_selection(self) -> None:
        payload = {
            "kind": "speech",
            "reason_code": "reply",
            "reason_summary": "ユーザー発話へ返す。",
            "requires_confirmation": False,
            "pending_intent": None,
            "capability_request": None,
            "autonomous_run": None,
            "foreground_selection": {
                "primary_factor_ref": "current_input:user_message",
                "supporting_factor_refs": [],
                "suppressed_factors": [],
                "summary_text": "ユーザー発話を主因にした。",
            },
            "target_stances": build_decision_target_stances_for_kind(
                "speech",
                required_targets=("outward_speech",),
                reason_summary="ユーザー発話へ返す。",
            ),
        }

        validate_decision_contract(payload)

    def test_decision_accepts_affect_context_workspace_factor_ref(self) -> None:
        payload = {
            "kind": "speech",
            "reason_code": "evaluation_acknowledgment",
            "reason_summary": "評価に落ち着いて返す。",
            "requires_confirmation": False,
            "pending_intent": None,
            "capability_request": None,
            "autonomous_run": None,
            "foreground_selection": {
                "primary_factor_ref": "current_input:user_message",
                "supporting_factor_refs": ["affect_context:recent_episode_affects:0"],
                "suppressed_factors": [],
                "summary_text": "評価への応答を主因にし、直近感情を補助にした。",
            },
            "target_stances": build_decision_target_stances_for_kind(
                "speech",
                required_targets=("outward_speech",),
                reason_summary="評価に落ち着いて返す。",
            ),
        }
        context = replace(
            _decision_context([]),
            workspace_context={
                "workspace_candidates": [
                    {
                        "factor_ref": "current_input:user_message",
                        "kind": "current_input",
                        "summary_text": "評価",
                    },
                    {
                        "factor_ref": "affect_context:recent_episode_affects:0",
                        "kind": "affect",
                        "summary_text": "直近の感情",
                    },
                ]
            },
        )

        with patch(
            "otomekairo.llm.client.complete_text",
            return_value=_completion(json.dumps(payload)),
        ):
            actual = _llm_client().generate_decision(
                model_config={"model": "real-model"},
                persona_context=_persona_context(),
                context=context,
            )

        self.assertEqual(actual, payload)

    def test_decision_rejects_unknown_affect_context_factor_ref(self) -> None:
        payload = {
            "kind": "speech",
            "reason_code": "evaluation_acknowledgment",
            "reason_summary": "評価に落ち着いて返す。",
            "requires_confirmation": False,
            "pending_intent": None,
            "capability_request": None,
            "autonomous_run": None,
            "foreground_selection": {
                "primary_factor_ref": "current_input:user_message",
                "supporting_factor_refs": ["affect_context:recent_episode_affects:0"],
                "suppressed_factors": [],
                "summary_text": "評価への応答を主因にし、直近感情を補助にした。",
            },
            "target_stances": build_decision_target_stances_for_kind(
                "speech",
                required_targets=("outward_speech",),
                reason_summary="評価に落ち着いて返す。",
            ),
        }
        context = replace(
            _decision_context([]),
            workspace_context={
                "workspace_candidates": [
                    {
                        "factor_ref": "current_input:user_message",
                        "kind": "current_input",
                        "summary_text": "評価",
                    }
                ]
            },
        )

        with patch(
            "otomekairo.llm.client.complete_text",
            return_value=_completion(json.dumps(payload)),
        ):
            with self.assertRaisesRegex(LLMError, r"不明な参照=affect_context:recent_episode_affects:0"):
                _llm_client().generate_decision(
                    model_config={"model": "real-model"},
                    persona_context=_persona_context(),
                    context=context,
                )

    def test_decision_contract_names_duplicate_foreground_factor_ref(self) -> None:
        payload = {
            "kind": "noop",
            "reason_code": "hold_speech",
            "reason_summary": "作業中なので問いかけない。",
            "requires_confirmation": False,
            "pending_intent": None,
            "capability_request": None,
            "autonomous_run": None,
            "foreground_selection": {
                "primary_factor_ref": "visual_observation:current",
                "supporting_factor_refs": ["visual_observation:current"],
                "suppressed_factors": [],
                "summary_text": "同じ観測を主役と補助に置いた。",
            },
            "target_stances": build_decision_target_stances_for_kind(
                "noop",
                required_targets=("outward_speech",),
                reason_summary="作業中なので問いかけない。",
            ),
        }

        with self.assertRaisesRegex(LLMError, r"重複=visual_observation:current"):
            validate_decision_contract(payload)

        payload["foreground_selection"] = {
            "primary_factor_ref": "visual_observation:current",
            "supporting_factor_refs": [],
            "suppressed_factors": [
                {
                    "factor_ref": "visual_observation:current",
                    "reason_summary": "身体注意は主題化しない。",
                }
            ],
            "summary_text": "同じ観測を主役と控えに置いた。",
        }
        with self.assertRaisesRegex(LLMError, r"重複=visual_observation:current"):
            validate_decision_contract(payload)

    def test_decision_contract_accepts_autonomous_run(self) -> None:
        payload = {
            "kind": "autonomous_run",
            "reason_code": "autonomous_run:start",
            "reason_summary": "継続する確認を目的として持つ。",
            "requires_confirmation": False,
            "pending_intent": None,
            "capability_request": None,
            "autonomous_run": {
                "objective_summary": "対象の確認を続ける。",
                "initial_step_summary": "最初の一手を判断する。",
                "coordination": {
                    "mode": "create_new",
                    "target_run_ids": [],
                    "reason_summary": "新しい目的として開始する。",
                },
            },
            "foreground_selection": {
                "primary_factor_ref": "capability:mcp.call_tool",
                "supporting_factor_refs": [],
                "suppressed_factors": [],
                "summary_text": "継続確認を主因にした。",
            },
            "target_stances": build_decision_target_stances_for_kind(
                "autonomous_run",
                required_targets=("outward_speech", "self_activity"),
                reason_summary="継続する確認を目的として持つ。",
            ),
        }

        validate_decision_contract(payload)

        payload["autonomous_run"]["mcp_server_id"] = "elyth"
        with self.assertRaises(LLMError):
            validate_decision_contract(payload)

    def test_decision_contract_requires_target_stances(self) -> None:
        payload = {
            "kind": "speech",
            "reason_code": "reply",
            "reason_summary": "ユーザー発話へ返す。",
            "requires_confirmation": False,
            "pending_intent": None,
            "capability_request": None,
            "autonomous_run": None,
            "foreground_selection": {
                "primary_factor_ref": "current_input:user_message",
                "supporting_factor_refs": [],
                "suppressed_factors": [],
                "summary_text": "ユーザー発話を主因にした。",
            },
        }

        with self.assertRaises(LLMError):
            validate_decision_contract(payload)

    def test_noop_with_standing_concern_requires_self_activity_hold(self) -> None:
        workspace = {
            "workspace_candidates": [
                {
                    "factor_ref": "standing_concern:elyth",
                    "kind": "standing_concern",
                    "summary_text": "ELYTH。",
                }
            ]
        }
        payload = {
            "kind": "noop",
            "reason_code": "hold_speech",
            "reason_summary": "作業中なので問いかけない。",
            "requires_confirmation": False,
            "pending_intent": None,
            "capability_request": None,
            "autonomous_run": None,
            "foreground_selection": {
                "primary_factor_ref": "standing_concern:elyth",
                "supporting_factor_refs": [],
                "suppressed_factors": [],
                "summary_text": "外向きだけ控えた。",
            },
            "target_stances": build_decision_target_stances_for_kind(
                "noop",
                required_targets=("outward_speech",),
                reason_summary="作業中なので問いかけない。",
            ),
        }

        with self.assertRaises(LLMError):
            validate_decision_contract(payload, workspace_context=workspace)

        payload["target_stances"] = build_decision_target_stances_for_kind(
            "noop",
            required_targets=("outward_speech", "self_activity"),
            reason_summary="今はその関心に関わらない。",
        )
        validate_decision_contract(payload, workspace_context=workspace)

    def test_speech_cannot_advance_self_activity(self) -> None:
        payload = {
            "kind": "speech",
            "reason_code": "reply",
            "reason_summary": "ユーザー発話へ返す。",
            "requires_confirmation": False,
            "pending_intent": None,
            "capability_request": None,
            "autonomous_run": None,
            "foreground_selection": {
                "primary_factor_ref": "current_input:user_message",
                "supporting_factor_refs": [],
                "suppressed_factors": [],
                "summary_text": "ユーザー発話を主因にした。",
            },
            "target_stances": [
                {
                    "target": "outward_speech",
                    "stance": "advance",
                    "reason_summary": "返す。",
                },
                {
                    "target": "self_activity",
                    "stance": "advance",
                    "reason_summary": "同時に関わる。",
                },
            ],
        }

        with self.assertRaises(LLMError):
            validate_decision_contract(payload)

    def test_self_activity_scope_rejects_speech(self) -> None:
        payload = {
            "kind": "speech",
            "reason_code": "reply",
            "reason_summary": "話しかける。",
            "requires_confirmation": False,
            "pending_intent": None,
            "capability_request": None,
            "autonomous_run": None,
            "foreground_selection": {
                "primary_factor_ref": "standing_concern:elyth",
                "supporting_factor_refs": [],
                "suppressed_factors": [],
                "summary_text": "関心を主役にした。",
            },
            "target_stances": build_decision_target_stances_for_kind(
                "speech",
                required_targets=("self_activity",),
                reason_summary="話しかける。",
            ),
        }

        with self.assertRaises(LLMError):
            validate_decision_contract(payload, comparison_scope="self_activity")

    def test_outward_scope_rejects_autonomous_run(self) -> None:
        payload = {
            "kind": "autonomous_run",
            "reason_code": "autonomous_run:start",
            "reason_summary": "その関心に関わる。",
            "requires_confirmation": False,
            "pending_intent": None,
            "capability_request": None,
            "autonomous_run": {
                "objective_summary": "その関心を確認する。",
                "initial_step_summary": "最初の一手を判断する。",
                "coordination": {
                    "mode": "create_new",
                    "target_run_ids": [],
                    "reason_summary": "新しい目的として開始する。",
                },
            },
            "foreground_selection": {
                "primary_factor_ref": "standing_concern:elyth",
                "supporting_factor_refs": [],
                "suppressed_factors": [],
                "summary_text": "関心を主役にした。",
            },
            "target_stances": build_decision_target_stances_for_kind(
                "autonomous_run",
                required_targets=("outward_speech", "self_activity"),
                reason_summary="その関心に関わる。",
            ),
        }

        with self.assertRaises(LLMError):
            validate_decision_contract(payload, comparison_scope="outward_speech")

    def test_decision_repairs_skill_id_used_as_capability_id(self) -> None:
        invalid = _capability_decision("elyth-check-notifications", {})
        valid = _capability_decision(
            "mcp.call_tool",
            {
                "mcp_server_id": "elyth",
                "tool_name": "get_notifications",
                "arguments": {"limit": 20},
            },
        )

        with patch(
            "otomekairo.llm.client.complete_text",
            side_effect=[_completion(json.dumps(invalid)), _completion(json.dumps(valid))],
        ) as complete:
            actual = _llm_client().generate_decision(
                model_config={"model": "real-model"},
                persona_context=_persona_context(),
                context=_decision_context(_mcp_capability_view()),
            )

        self.assertEqual(actual, valid)
        self.assertEqual(complete.call_count, 2)

    def test_decision_request_schema_uses_workspace_factor_refs(self) -> None:
        valid = {
            "kind": "noop",
            "reason_code": "hold",
            "reason_summary": "今回は見送る。",
            "requires_confirmation": False,
            "pending_intent": None,
            "capability_request": None,
            "autonomous_run": None,
            "foreground_selection": {
                "primary_factor_ref": "current_input:user_message",
                "supporting_factor_refs": [],
                "suppressed_factors": [],
                "summary_text": "入力を主役にした。",
            },
            "target_stances": build_decision_target_stances_for_kind(
                "noop",
                required_targets=("outward_speech",),
                reason_summary="今回は見送る。",
            ),
        }
        context = replace(
            _decision_context([]),
            comparison_scope="outward_speech",
            workspace_context={
                "workspace_candidates": [
                    {
                        "factor_ref": "current_input:user_message",
                        "kind": "current_input",
                        "summary_text": "入力。",
                    }
                ]
            },
        )

        with patch(
            "otomekairo.llm.client.complete_text",
            return_value=_completion(json.dumps(valid)),
        ) as complete:
            _llm_client().generate_decision(
                model_config={"model": "real-model"},
                persona_context=_persona_context(),
                context=context,
            )

        response_format = complete.call_args.kwargs["response_format"]
        foreground = response_format["json_schema"]["schema"]["properties"][
            "foreground_selection"
        ]["properties"]
        self.assertEqual(
            foreground["primary_factor_ref"]["enum"],
            ["current_input:user_message"],
        )
        self.assertEqual(
            response_format["json_schema"]["schema"]["properties"]["capability_request"],
            {"type": "null"},
        )

    def test_decision_repairs_unavailable_capability(self) -> None:
        invalid = _capability_decision(
            "agent_skill.run_script",
            {
                "source_id": "elyth-skills",
                "skill_id": "elyth-follow",
                "skill_sha256": "digest",
                "script_path": "scripts/run.py",
                "args": [],
                "stdin_text": None,
            },
        )
        valid = _capability_decision(
            "mcp.call_tool",
            {
                "mcp_server_id": "elyth",
                "tool_name": "get_notifications",
                "arguments": {},
            },
        )
        capability_view = [
            {
                "id": "agent_skill.run_script",
                "available": False,
                "unavailable_reason": "no_binding",
            },
            *_mcp_capability_view(),
        ]

        with patch(
            "otomekairo.llm.client.complete_text",
            side_effect=[_completion(json.dumps(invalid)), _completion(json.dumps(valid))],
        ) as complete:
            actual = _llm_client().generate_decision(
                model_config={"model": "real-model"},
                persona_context=_persona_context(),
                context=_decision_context(capability_view),
            )

        self.assertEqual(actual, valid)
        self.assertEqual(complete.call_count, 2)

    def test_decision_repairs_missing_manifest_input(self) -> None:
        invalid = _capability_decision(
            "agent_skill.run_script",
            {
                "source_id": "test-source",
                "skill_id": "echo-skill",
                "skill_sha256": "digest",
                "script_path": "scripts/echo.py",
                "stdin_text": None,
            },
        )
        valid_input = {
            **invalid["capability_request"]["input"],
            "args": [],
        }
        valid = _capability_decision("agent_skill.run_script", valid_input)
        capability_view = [
            {
                "id": "agent_skill.run_script",
                "available": True,
                "unavailable_reason": None,
            }
        ]

        with patch(
            "otomekairo.llm.client.complete_text",
            side_effect=[_completion(json.dumps(invalid)), _completion(json.dumps(valid))],
        ) as complete:
            actual = _llm_client().generate_decision(
                model_config={"model": "real-model"},
                persona_context=_persona_context(),
                context=_decision_context(capability_view),
            )

        self.assertEqual(actual, valid)
        self.assertEqual(complete.call_count, 2)

    def test_decision_repairs_unknown_mcp_tool_and_invalid_arguments(self) -> None:
        valid = _capability_decision(
            "mcp.call_tool",
            {
                "mcp_server_id": "elyth",
                "tool_name": "get_notifications",
                "arguments": {"limit": 20},
            },
        )
        invalid_inputs = (
            {
                "mcp_server_id": "elyth",
                "tool_name": "elyth-check-notifications",
                "arguments": {},
            },
            {
                "mcp_server_id": "elyth",
                "tool_name": "get_notifications",
                "arguments": {"limit": 0},
            },
        )

        for invalid_input in invalid_inputs:
            with self.subTest(invalid_input=invalid_input):
                invalid = _capability_decision("mcp.call_tool", invalid_input)
                with patch(
                    "otomekairo.llm.client.complete_text",
                    side_effect=[_completion(json.dumps(invalid)), _completion(json.dumps(valid))],
                ) as complete:
                    actual = _llm_client().generate_decision(
                        model_config={"model": "real-model"},
                        persona_context=_persona_context(),
                        context=_decision_context(_mcp_capability_view()),
                    )

                self.assertEqual(actual, valid)
                self.assertEqual(complete.call_count, 2)

    def test_context_validation_rejects_unavailable_vision_target_and_camera_operation(self) -> None:
        client = LLMClient()
        capability_view = [
            {
                "id": "camera.ptz",
                "available": True,
                "unavailable_reason": None,
                "vision_sources": [
                    {
                        "vision_source_id": "vision_source:camera",
                        "available": True,
                        "supported_operations": ["move_left"],
                        "supported_amounts": ["medium"],
                    }
                ],
            }
        ]

        invalid_inputs = (
            {
                "vision_source_id": "vision_source:missing",
                "operation": "move_left",
                "amount": "medium",
            },
            {
                "vision_source_id": "vision_source:camera",
                "operation": "move_right",
                "amount": "medium",
            },
        )
        for invalid_input in invalid_inputs:
            with self.subTest(invalid_input=invalid_input):
                with self.assertRaises(LLMError):
                    client._validate_capability_request_for_context(
                        request_payload={
                            "capability_id": "camera.ptz",
                            "input": invalid_input,
                        },
                        capability_decision_view=capability_view,
                        label="Decision capability_request",
                    )

    def test_autonomous_step_repairs_unknown_capability(self) -> None:
        invalid = {
            "action": {
                "kind": "capability_request",
                "capability_request": {
                    "capability_id": "elyth-check-notifications",
                    "input": {},
                },
                "speech": None,
            },
            "transition": {"kind": "continue", "next_run_at": None},
            "run_update": {"current_step_summary": "通知を確認する。", "history_summary": "開始した。"},
        }
        valid = {
            **invalid,
            "action": {
                "kind": "capability_request",
                "capability_request": {
                    "capability_id": "mcp.call_tool",
                    "input": {
                        "mcp_server_id": "elyth",
                        "tool_name": "get_notifications",
                        "arguments": {},
                    },
                },
                "speech": None,
            },
        }
        context = AutonomousStepContext(
            run={"run_id": "autonomous_run:test"},
            current_input=_current_input(),
            recent_turns=[],
            time_context={},
            foreground_world_state=None,
            activity_context=None,
            ongoing_action_summary=None,
            capability_decision_view=_mcp_capability_view(),
            last_result_context=None,
        )

        with patch(
            "otomekairo.llm.client.complete_text",
            side_effect=[_completion(json.dumps(invalid)), _completion(json.dumps(valid))],
        ) as complete:
            actual = _llm_client().generate_autonomous_step(
                model_config={"model": "real-model"},
                persona_context=_persona_context(),
                context=context,
            )

        self.assertEqual(actual, valid)
        self.assertEqual(complete.call_count, 2)

    def test_decision_fails_when_repair_is_still_invalid(self) -> None:
        invalid = _capability_decision("elyth-check-notifications", {})

        with patch(
            "otomekairo.llm.client.complete_text",
            return_value=_completion(json.dumps(invalid)),
        ) as complete:
            with self.assertRaisesRegex(LLMError, "CapabilityDecisionView"):
                _llm_client().generate_decision(
                    model_config={"model": "real-model"},
                    persona_context=_persona_context(),
                    context=_decision_context(_mcp_capability_view()),
                )

        self.assertEqual(complete.call_count, 2)

    def test_decision_validation_failed_log_includes_rejected_payload(self) -> None:
        invalid = {
            "kind": "noop",
            "reason_code": "hold_speech",
            "reason_summary": "作業中なので問いかけない。",
            "requires_confirmation": False,
            "pending_intent": None,
            "capability_request": None,
            "autonomous_run": None,
            "foreground_selection": {
                "primary_factor_ref": "visual_observation:current",
                "supporting_factor_refs": ["visual_observation:current"],
                "suppressed_factors": [],
                "summary_text": "同じ観測を主役と補助に置いた。",
            },
            "target_stances": build_decision_target_stances_for_kind(
                "noop",
                required_targets=("outward_speech",),
                reason_summary="作業中なので問いかけない。",
            ),
        }
        logs: list[tuple[str, str, str]] = []

        def capture(component: str, message: str, *, level: str = "INFO") -> None:
            logs.append((level, component, message))

        context = replace(_decision_context([]), comparison_scope="outward_speech")
        with (
            patch("otomekairo.llm.client.debug_log", side_effect=capture),
            patch(
                "otomekairo.llm.client.complete_text",
                return_value=_completion(json.dumps(invalid)),
            ),
        ):
            with self.assertRaisesRegex(LLMError, r"重複=visual_observation:current"):
                _llm_client().generate_decision(
                    model_config={"model": "real-model"},
                    persona_context=_persona_context(),
                    context=context,
                )

        warnings = [message for level, component, message in logs if level == "WARNING" and component == "LLM"]
        self.assertEqual(len(warnings), 2)
        for message in warnings:
            self.assertIn("decision:outward_speech validation_failed", message)
            self.assertIn("重複=visual_observation:current", message)
            self.assertIn("payload=", message)
            self.assertIn('"primary_factor_ref":"visual_observation:current"', message)
            self.assertIn('"supporting_factor_refs":["visual_observation:current"]', message)

    def test_decision_parse_failed_log_includes_rejected_content(self) -> None:
        logs: list[tuple[str, str, str]] = []

        def capture(component: str, message: str, *, level: str = "INFO") -> None:
            logs.append((level, component, message))

        with (
            patch("otomekairo.llm.client.debug_log", side_effect=capture),
            patch(
                "otomekairo.llm.client.complete_text",
                return_value=_completion("これは JSON ではありません。"),
            ),
        ):
            with self.assertRaises(LLMError):
                _llm_client().generate_decision(
                    model_config={"model": "real-model"},
                    persona_context=_persona_context(),
                    context=_decision_context([]),
                )

        warnings = [message for level, component, message in logs if level == "WARNING" and component == "LLM"]
        self.assertEqual(len(warnings), 2)
        for message in warnings:
            self.assertIn("decision parse_failed", message)
            self.assertIn("content=これは JSON ではありません。", message)

    def test_rejected_payload_redacts_secret_fields(self) -> None:
        compact = LLMClient()._debug_rejected_payload(
            {
                "kind": "capability_request",
                "capability_request": {
                    "capability_id": "mcp.call_tool",
                    "input": {
                        "mcp_server_id": "elyth",
                        "tool_name": "get_notifications",
                        "arguments": {"token": "secret-value"},
                    },
                },
                "api_key": "should-not-appear",
            }
        )

        self.assertIn('"kind":"capability_request"', compact)
        self.assertIn("[redacted]", compact)
        self.assertNotIn("secret-value", compact)
        self.assertNotIn("should-not-appear", compact)


class DecisionPromptScopeTests(unittest.TestCase):
    def _system_prompt(self, comparison_scope: str) -> str:
        context = replace(_decision_context([]), comparison_scope=comparison_scope)
        messages = build_decision_messages(
            persona_context=_persona_context(),
            context=context,
        )
        return messages[0]["content"]

    def test_self_activity_prompt_asks_orientation_not_reply(self) -> None:
        system = self._system_prompt("self_activity")
        self.assertIn("今、気にかけていることや継続中の自身の活動へ関わるか", system)
        self.assertIn("capability_request / autonomous_run / pending_intent / noop", system)
        self.assertIn("向きと CapabilityDecisionView の catalog から autonomous_run を始めてよい", system)
        self.assertIn("autonomous_run.objective_summary は向き自身の言葉です", system)
        self.assertIn("capability_request.input の自然文は、その能力の先の場へ向けた個の表現です", system)
        self.assertIn("capability_request.input は required_input と readiness.input_keys に対応する入れ子の JSON object です", system)
        self.assertIn("使わない排他キーもキーとして残し、値は null にします", system)
        self.assertIn("出力 JSON は structured schema の必須キーと enum に従います", system)
        self.assertIn("その関心に関われる手段が CapabilityDecisionView に available=true であるときだけ", system)
        self.assertIn("手段が無いときは今は関わらない", system)
        self.assertIn("target_stances は self_activity を 1 件だけ持ちます", system)
        self.assertNotIn("伝達、能力実行、保留、見送り、継続目的開始のどれが", system)
        self.assertNotIn("人物発話自体が未来実行", system)
        self.assertNotIn("outward_speech は毎回必須です", system)
        self.assertIn("AffectContext の affect_states と recent_episode_affects は WorkspaceContext の affect 候補です。", system)

    def test_outward_speech_prompt_asks_short_view_not_visit(self) -> None:
        system = self._system_prompt("outward_speech")
        self.assertIn("今、外へ短い見方を出すか", system)
        self.assertIn("speech / noop / pending_intent", system)
        self.assertIn("観測事実に基づく一文の状況認識", system)
        self.assertIn("助言、依頼、支援提案、休息促し、身体注意、画面への一般コメントは speech ではなく控える理由", system)
        self.assertIn("target_stances は outward_speech を 1 件だけ持ちます", system)
        self.assertIn("AffectContext の affect_states と recent_episode_affects は WorkspaceContext の affect 候補です。", system)
        self.assertNotIn("今見に行く自然さがあれば capability_request", system)
        self.assertNotIn("向きと CapabilityDecisionView の catalog から autonomous_run", system)
        self.assertNotIn("有限 MCP セッションは CapabilityDecisionView", system)

    def test_full_prompt_keeps_combined_question(self) -> None:
        system = self._system_prompt("full")
        self.assertIn("伝達、能力実行、保留、見送り、継続目的開始のどれが", system)
        self.assertIn("outward_speech は毎回必須です", system)
        self.assertIn("載っている self_activity は hold です。対話の継続は outward_speech です。", system)
        self.assertIn("Agent Skill の skill_id は capability_id でも MCP tool_name でもありません。", system)
        self.assertIn("使わない排他キーもキーとして残し、値は null にします", system)
        self.assertIn("capability_request.input は required_input と readiness.input_keys に対応する入れ子の JSON object です", system)
        self.assertIn("AffectContext の affect_states と recent_episode_affects は WorkspaceContext の affect 候補です。", system)
        self.assertIn("同じ作用を複数回行う、または観測のあとに同じ作用を繰り返す依頼は継続実行です", system)
        self.assertIn("複合依頼を単発の capability_request にする理由にはしません", system)
        self.assertIn("未完了の同じ作用を発話で先送りしません", system)
        self.assertIn("pending_intent は残作業の置き場ではありません", system)
        self.assertIn("active_commitments は未完了の理解です", system)
        self.assertIn("該当 run が無いなら autonomous_run を始めます", system)
        self.assertIn("その実行列へ新しい capability_request を重ねません", system)
        self.assertIn("別の継続実行を求め、該当 run が無いなら autonomous_run を始めてよい", system)

    def test_repair_prompt_follows_comparison_scope(self) -> None:
        self_repair = build_decision_repair_prompt("kind が不正です。", "self_activity")
        outward_repair = build_decision_repair_prompt("kind が不正です。", "outward_speech")
        self.assertIn("capability_request / autonomous_run / pending_intent / noop", self_repair)
        self.assertIn("target_stances は self_activity を 1 件だけ持ちます", self_repair)
        self.assertIn("使わない排他キーもキーとして残し、値は null にします", self_repair)
        self.assertIn("capability_request.input は required_input と readiness.input_keys に対応する入れ子の JSON object です", self_repair)
        self.assertNotIn("outward_speech は毎回必須です", self_repair)
        self.assertIn("speech / noop / pending_intent", outward_repair)
        self.assertIn("target_stances は outward_speech を 1 件だけ持ちます", outward_repair)
        self.assertNotIn("今見に行く自然さがあれば capability_request", outward_repair)

    def test_trigger_policy_splits_initiative_question(self) -> None:
        from otomekairo.llm.contexts import InitiativeContext

        initiative = InitiativeContext(
            trigger_kind="background_thinking",
            opportunity_summary="気にかけていることがしばらく前景に出ていない。",
            initiative_entry_summary=None,
            time_context_summary={},
            foreground_signal_summary={},
            activity_context=None,
            initiative_baseline={},
            persona_context_summary={},
            runtime_state_summary={},
            recent_turn_summary=[],
            drive_summaries=[],
            pending_intent_summaries=[],
            world_state_summary=[],
            ongoing_action_summary=None,
            capability_summary={},
            candidate_families=[],
            selected_candidate_family="autonomous",
            speech_timing_state={},
            suppression_summary={},
            speech_timing_summary="",
            speech_frequency_level=5,
        )
        self_policies = _build_decision_trigger_policy(
            initiative_context=initiative,
            capability_result_context=None,
            comparison_scope="self_activity",
        )
        outward_policies = _build_decision_trigger_policy(
            initiative_context=initiative,
            capability_result_context=None,
            comparison_scope="outward_speech",
        )
        self_text = "\n".join(self_policies)
        outward_text = "\n".join(outward_policies)
        self.assertIn("InitiativeContext は今回の自律判断機会の材料", self_text)
        self.assertIn("preferred_capability_id がある candidate_family は capability_request の提案", self_text)
        self.assertNotIn("standing_concern は実行指示ではありません", self_text)
        self.assertNotIn("向きと catalog から autonomous_run を始めてよい", self_text)
        self.assertNotIn("短い独り言", self_text)
        self.assertIn("speech は短い独り言", outward_text)
        self.assertIn("speech_frequency_level は 5", outward_text)
        self.assertNotIn("向きと catalog から autonomous_run", outward_text)
        self.assertNotIn("speech / noop / pending_intent / capability_request / autonomous_run から 1 つ", outward_text)

    def test_trigger_policy_is_empty_without_trigger_context(self) -> None:
        for comparison_scope in ("self_activity", "outward_speech"):
            with self.subTest(comparison_scope=comparison_scope):
                self.assertEqual(
                    _build_decision_trigger_policy(
                        initiative_context=None,
                        capability_result_context=None,
                        comparison_scope=comparison_scope,
                    ),
                    [],
                )

    def test_expression_prompt_keeps_autonomous_speech_as_situation_recognition(self) -> None:
        system = _build_speech_system_prompt()
        self.assertIn("観測事実に基づく状況認識", system)
        self.assertIn("助言、依頼、支援提案、休息促し、身体注意、評価は本文へ足しません", system)
        self.assertIn("具体的な固有名、表示対象名、作品名、ページ内容は主題化しません", system)

    def test_autonomous_step_prompt_treats_capability_input_as_external_expression(self) -> None:
        messages = build_autonomous_step_messages(
            persona_context=_persona_context(),
            context=AutonomousStepContext(
                run={"objective_summary": "向きへ関わる。"},
                current_input=_current_input(),
                recent_turns=[],
                time_context={},
                foreground_world_state=None,
                activity_context=None,
                ongoing_action_summary=None,
                capability_decision_view=[],
                last_result_context=None,
            ),
        )
        self.assertIn(
            "capability_request.input の自然文は、その能力の先の場へ向けた個の表現です",
            messages[0]["content"],
        )
        self.assertIn(
            "capability_request.input は required_input と readiness.input_keys に対応する入れ子の JSON object です",
            messages[0]["content"],
        )
        self.assertIn(
            "arguments など入れ子も object のまま書きます",
            messages[0]["content"],
        )

    def test_autonomous_step_repair_prompt_asks_nested_input_object(self) -> None:
        repair = build_autonomous_step_repair_prompt(
            "AutonomousStep action.capability_request.input は object である必要があります。"
        )
        self.assertIn(
            "capability_request.input は required_input と readiness.input_keys に対応する入れ子の JSON object です",
            repair,
        )
        self.assertIn("arguments など入れ子も object のまま書きます", repair)

    def test_completed_mcp_tool_followup_rejects_same_tool(self) -> None:
        client = LLMClient()
        capability_result_context = {
            "source_capability_id": "mcp.call_tool",
            "allowed_followup_capability_ids": ["mcp.call_tool"],
            "followup_constraints": [
                {
                    "capability_id": "mcp.call_tool",
                    "constraint": "exclude_completed_mcp_tool",
                    "mcp_server_id": "elyth",
                    "tool_name": "create_post",
                }
            ],
        }

        with self.assertRaises(LLMError) as raised:
            client._validate_decision_capability_result_context(
                payload=_capability_decision(
                    "mcp.call_tool",
                    {
                        "mcp_server_id": "elyth",
                        "tool_name": "create_post",
                        "arguments": {"content": "思索。"},
                    },
                ),
                capability_result_context=capability_result_context,
            )

        self.assertIn("elyth/create_post", str(raised.exception))
        self.assertIn("再実行を許可しません", str(raised.exception))
        self.assertIn("autonomous_run", str(raised.exception))
        self.assertIn("同じ作用なら autonomous_run", str(raised.exception))
        self.assertNotIn("pending_intent", str(raised.exception))

    def test_completed_mcp_tool_followup_allows_different_tool(self) -> None:
        client = LLMClient()
        client._validate_decision_capability_result_context(
            payload=_capability_decision(
                "mcp.call_tool",
                {
                    "mcp_server_id": "elyth",
                    "tool_name": "get_my_posts",
                    "arguments": {},
                },
            ),
            capability_result_context={
                "source_capability_id": "mcp.call_tool",
                "allowed_followup_capability_ids": ["mcp.call_tool"],
                "followup_constraints": [
                    {
                        "capability_id": "mcp.call_tool",
                        "constraint": "exclude_completed_mcp_tool",
                        "mcp_server_id": "elyth",
                        "tool_name": "create_post",
                    }
                ],
            },
        )

    def test_disallowed_followup_capability_points_to_autonomous_run(self) -> None:
        client = LLMClient()
        with self.assertRaises(LLMError) as raised:
            client._validate_decision_capability_result_context(
                payload=_capability_decision("vision.capture", {"vision_source_id": "vision_source:desktop"}),
                capability_result_context={
                    "source_capability_id": "mcp.call_tool",
                    "allowed_followup_capability_ids": ["mcp.call_tool"],
                },
            )

        message = str(raised.exception)
        self.assertIn("allowed_followup_capability_ids", message)
        self.assertIn("autonomous_run", message)
        self.assertIn("pending_intent は残作業の置き場ではありません", message)
        self.assertNotIn("speech / noop / pending_intent を返してください", message)


class AutonomousCompletionReviewContractTests(unittest.TestCase):
    def test_contract_accepts_only_completion_review_shape(self) -> None:
        validate_autonomous_completion_review_contract(
            {
                "outcome": "allow_complete",
                "reason_summary": "目的は実績で満たされている。",
            }
        )
        with self.assertRaises(LLMError):
            validate_autonomous_completion_review_contract(
                {
                    "outcome": "allow",
                    "reason_summary": "不正な outcome。",
                }
            )

    def test_client_repairs_invalid_completion_review_once(self) -> None:
        invalid = {
            "outcome": "allow",
            "reason_summary": "契約外。",
        }
        valid = {
            "outcome": "continue_run",
            "reason_summary": "外界への作用がまだ必要である。",
        }
        with patch(
            "otomekairo.llm.client.complete_text",
            side_effect=[_completion(json.dumps(invalid)), _completion(json.dumps(valid))],
        ) as complete:
            actual = _llm_client().generate_autonomous_completion_review(
                model_config={"model": "real-model"},
                review_context={
                    "run": {"objective_summary": "投稿を1件作成する。"},
                    "candidate": {
                        "action_kind": "speech",
                        "run_update": {
                            "current_step_summary": "投稿準備を終えた。",
                            "history_summary": "投稿準備を終えた。",
                        },
                        "speech_text": "これから投稿します。",
                    },
                },
            )

        self.assertEqual(actual, valid)
        self.assertEqual(complete.call_count, 2)

    def test_review_prompt_distinguishes_effect_from_announcement(self) -> None:
        messages = build_autonomous_completion_review_messages(
            review_context={
                "run": {"objective_summary": "投稿を1件作成する。"},
                "candidate": {
                    "action_kind": "speech",
                    "run_update": {},
                    "speech_text": "これから投稿します。",
                },
            }
        )
        system = messages[0]["content"]
        self.assertIn("予定、準備、意思表明だけを作用の完了実績にしません", system)
        self.assertIn("allow_complete または continue_run", system)

    def test_autonomous_step_context_includes_completion_feedback(self) -> None:
        context = AutonomousStepContext(
            run={"run_id": "autonomous_run:test"},
            current_input=_current_input(),
            recent_turns=[],
            time_context={},
            foreground_world_state=None,
            activity_context=None,
            ongoing_action_summary=None,
            capability_decision_view=_mcp_capability_view(),
            last_result_context=None,
            completion_review_feedback="目的に沿う実行を続ける。",
        )
        messages = build_autonomous_step_messages(
            persona_context=_persona_context(),
            context=context,
        )

        self.assertIn("completion_review_feedback", messages[1]["content"])
        self.assertIn("目的に沿う実行を続ける。", messages[1]["content"])


if __name__ == "__main__":
    unittest.main()
