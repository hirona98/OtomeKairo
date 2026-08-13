import json
import unittest
from unittest.mock import patch

from otomekairo.llm.client import LLMClient
from otomekairo.llm.contexts import AutonomousStepContext, CurrentInput, DecisionContext, PersonaContext
from otomekairo.llm.contracts import (
    LLMError,
    build_decision_target_stances_for_kind,
    validate_decision_contract,
)


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

    def test_decision_contract_accepts_finite_mcp_session(self) -> None:
        payload = {
            "kind": "autonomous_run",
            "reason_code": "mcp_session:start",
            "reason_summary": "対象 MCP を有限回操作する。",
            "requires_confirmation": False,
            "pending_intent": None,
            "capability_request": None,
            "autonomous_run": {
                "objective_summary": "対象 MCP 内の確認を完了する。",
                "initial_step_summary": "最初の tool を判断する。",
                "mcp_server_id": "elyth",
                "coordination": {
                    "mode": "create_new",
                    "target_run_ids": [],
                    "reason_summary": "新しい有限セッションを始める。",
                },
            },
            "foreground_selection": {
                "primary_factor_ref": "capability:mcp.call_tool",
                "supporting_factor_refs": [],
                "suppressed_factors": [],
                "summary_text": "MCP 操作を主因にした。",
            },
            "target_stances": build_decision_target_stances_for_kind(
                "autonomous_run",
                required_targets=("outward_speech", "self_activity"),
                reason_summary="対象 MCP を有限回操作する。",
            ),
        }

        validate_decision_contract(payload)

        payload["autonomous_run"]["mcp_server_id"] = ""
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
                    "summary_text": "ELYTHの場。",
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
            reason_summary="今その場へ関わらない。",
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
                    "reason_summary": "同時に場へ行く。",
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
            "reason_code": "mcp_session:start",
            "reason_summary": "場を見に行く。",
            "requires_confirmation": False,
            "pending_intent": None,
            "capability_request": None,
            "autonomous_run": {
                "objective_summary": "場を確認する。",
                "initial_step_summary": "最初の tool を判断する。",
                "mcp_server_id": "elyth",
                "coordination": {
                    "mode": "create_new",
                    "target_run_ids": [],
                    "reason_summary": "新しい有限セッションを始める。",
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
                reason_summary="場を見に行く。",
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
            side_effect=[json.dumps(invalid), json.dumps(valid)],
        ) as complete:
            actual = LLMClient().generate_decision(
                model_config={"model": "real-model"},
                persona_context=_persona_context(),
                context=_decision_context(_mcp_capability_view()),
            )

        self.assertEqual(actual, valid)
        self.assertEqual(complete.call_count, 2)

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
            side_effect=[json.dumps(invalid), json.dumps(valid)],
        ) as complete:
            actual = LLMClient().generate_decision(
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
            side_effect=[json.dumps(invalid), json.dumps(valid)],
        ) as complete:
            actual = LLMClient().generate_decision(
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
                    side_effect=[json.dumps(invalid), json.dumps(valid)],
                ) as complete:
                    actual = LLMClient().generate_decision(
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
            side_effect=[json.dumps(invalid), json.dumps(valid)],
        ) as complete:
            actual = LLMClient().generate_autonomous_step(
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
            return_value=json.dumps(invalid),
        ) as complete:
            with self.assertRaisesRegex(LLMError, "CapabilityDecisionView"):
                LLMClient().generate_decision(
                    model_config={"model": "real-model"},
                    persona_context=_persona_context(),
                    context=_decision_context(_mcp_capability_view()),
                )

        self.assertEqual(complete.call_count, 2)


if __name__ == "__main__":
    unittest.main()
