import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from otomekairo.llm.client import LLMClient
from otomekairo.llm.contexts import AutonomousStepContext, CurrentInput, DecisionContext, PersonaContext
from otomekairo.llm.contracts import (
    LLMError,
    build_decision_target_stances_for_kind,
    validate_decision_contract,
)
from otomekairo.llm.prompts import (
    _build_speech_system_prompt,
    build_decision_messages,
    build_decision_repair_prompt,
    _build_decision_trigger_policy,
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
        self.assertIn("その関心に関われる手段が CapabilityDecisionView に available=true であるときだけ", system)
        self.assertIn("手段が無いときは今は関わらない", system)
        self.assertIn("target_stances は self_activity を 1 件だけ持ちます", system)
        self.assertNotIn("伝達、能力実行、保留、見送り、継続目的開始のどれが", system)
        self.assertNotIn("人物発話自体が未来実行", system)
        self.assertNotIn("outward_speech は毎回必須です", system)

    def test_outward_speech_prompt_asks_short_view_not_visit(self) -> None:
        system = self._system_prompt("outward_speech")
        self.assertIn("今、外へ短い見方を出すか", system)
        self.assertIn("speech / noop / pending_intent", system)
        self.assertIn("観測事実に基づく一文の状況認識", system)
        self.assertIn("助言、依頼、支援提案、休息促し、身体注意、画面への一般コメントは speech ではなく控える理由", system)
        self.assertIn("target_stances は outward_speech を 1 件だけ持ちます", system)
        self.assertNotIn("今見に行く自然さがあれば capability_request", system)
        self.assertNotIn("向きと CapabilityDecisionView の catalog から autonomous_run", system)
        self.assertNotIn("有限 MCP セッションは CapabilityDecisionView", system)

    def test_full_prompt_keeps_combined_question(self) -> None:
        system = self._system_prompt("full")
        self.assertIn("伝達、能力実行、保留、見送り、継続目的開始のどれが", system)
        self.assertIn("outward_speech は毎回必須です", system)

    def test_repair_prompt_follows_comparison_scope(self) -> None:
        self_repair = build_decision_repair_prompt("kind が不正です。", "self_activity")
        outward_repair = build_decision_repair_prompt("kind が不正です。", "outward_speech")
        self.assertIn("capability_request / autonomous_run / pending_intent / noop", self_repair)
        self.assertIn("target_stances は self_activity を 1 件だけ持ちます", self_repair)
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


if __name__ == "__main__":
    unittest.main()
