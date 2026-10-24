import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from otomekairo.llm.client import LLMClient
from otomekairo.llm.contexts import (
    AutonomousStepContext,
    CurrentInput,
    DecisionContext,
    InitiativeCandidateFamily,
    InitiativeContext,
    PersonaContext,
)
from otomekairo.service.input.trace_compact import ServiceInputTraceCompactMixin
from otomekairo.llm.contracts import (
    LLMError,
    build_decision_target_stances_for_kind,
    required_decision_targets,
    validate_autonomous_activity_alignment_review_contract,
    validate_decision_contract,
)
from otomekairo.llm.prompts import (
    build_autonomous_activity_alignment_review_messages,
    build_autonomous_step_messages,
    build_autonomous_step_repair_prompt,
    build_decision_messages,
    build_decision_repair_prompt,
    _build_decision_trigger_policy,
)


def _persona_context() -> PersonaContext:
    return PersonaContext(
        display_name="Test",
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
        current_visual_observations=[],
        self_state_context=None,
        relationship_context=None,
        prediction_error_context=None,
        default_mode_context=None,
        workspace_context=None,
        recall_hint={},
        recall_pack={},
    )


def _wire_response(payload: dict) -> str:
    wire = json.loads(json.dumps(payload))
    request = wire.get("capability_request")
    if isinstance(request, dict) and isinstance(request.get("input"), dict):
        request["input"] = json.dumps(request["input"], ensure_ascii=False)
    action = wire.get("action")
    if isinstance(action, dict):
        nested = action.get("capability_request")
        if isinstance(nested, dict) and isinstance(nested.get("input"), dict):
            nested["input"] = json.dumps(nested["input"], ensure_ascii=False)
    return json.dumps(wire)


def _initiative_context(family: str, *, available: bool = True) -> InitiativeContext:
    return InitiativeContext(
        trigger_kind="background_thinking", opportunity_summary="定期思考。",
        initiative_entry_summary=None, time_context_summary={}, foreground_signal_summary={},
        activity_context=None, persona_context_summary={}, runtime_state_summary={},
        recent_turn_summary=[], drive_summaries=[], pending_intent_summaries=[],
        world_state_summary=[], ongoing_action_summary=None, capability_summary={},
        candidate_families=[InitiativeCandidateFamily(
            family=family, available=available, selected=False, priority_score=0.0,
        )], selected_candidate_family=None, speech_timing_state={}, suppression_summary={},
        speech_timing_summary="",
    )


def _capability_decision(capability_id: str, input_payload: dict) -> dict:
    return {"speech_text": None,
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
    def test_context_targets_reach_schema_and_prompt_in_one_completion(self) -> None:
        cases = [("full", None, None, ("outward_speech",))]
        for kind in ("periodic_thought_topic", "ongoing_action", "autonomous_run"):
            cases.append(("full", {"workspace_candidates": [{
                "kind": kind, "factor_ref": "activity:test", "summary_text": "活動候補。",
            }]}, None, ("outward_speech", "self_activity")))
        for family in ("autonomous", "ongoing_action"):
            for available in (True, False):
                targets = ("outward_speech", "self_activity") if available else ("outward_speech",)
                cases.append(("full", None, _initiative_context(family, available=available), targets))
        for scope in ("self_activity", "outward_speech"):
            cases.append((scope, None, _initiative_context("autonomous"), (scope,)))

        for scope, workspace, initiative, targets in cases:
            with self.subTest(scope=scope, workspace=workspace, initiative=initiative):
                context = replace(_decision_context([]), comparison_scope=scope,
                                  workspace_context=workspace, initiative_context=initiative)
                candidate = _capability_decision("mcp.call_tool", {})
                candidate.update(kind="noop", capability_request=None)
                if workspace is not None:
                    candidate["foreground_selection"]["primary_factor_ref"] = "activity:test"
                candidate["target_stances"] = build_decision_target_stances_for_kind(
                    "noop", required_targets=targets, reason_summary="今回は見送る。",
                )
                with patch("otomekairo.llm.client.complete_text", return_value=_wire_response(candidate)) as complete:
                    result = LLMClient().generate_decision(
                        model_config={"model": "real-test"}, persona_context=_persona_context(), context=context,
                    )
                self.assertEqual(result, candidate)
                complete.assert_called_once()
                request = complete.call_args.kwargs
                stances = request["response_format"]["json_schema"]["schema"]["properties"]["target_stances"]
                self.assertEqual(stances["minItems"], len(targets))
                expected = f"今回の文脈で必須の target_stances.target は {json.dumps(list(targets))} です。"
                self.assertIn(expected, request["messages"][0]["content"])

    def test_missing_context_target_repairs_with_same_schema_and_required_targets(self) -> None:
        context = replace(_decision_context([]), initiative_context=_initiative_context("autonomous"))
        valid = _capability_decision("mcp.call_tool", {})
        valid.update(kind="speech", speech_text="確認が終了しました。", capability_request=None)
        valid["target_stances"] = build_decision_target_stances_for_kind(
            "speech", required_targets=("outward_speech", "self_activity"), reason_summary="結果を伝える。",
        )
        missing = json.loads(json.dumps(valid))
        missing["target_stances"] = missing["target_stances"][:1]
        with patch("otomekairo.llm.client.complete_text", side_effect=[_wire_response(missing), _wire_response(valid)]) as complete:
            result = LLMClient().generate_decision(
                model_config={"model": "real-test"}, persona_context=_persona_context(), context=context,
            )
        self.assertEqual(result, valid)
        self.assertEqual(complete.call_count, 2)
        first, second = [call.kwargs for call in complete.call_args_list]
        self.assertEqual(first["response_format"], second["response_format"])
        self.assertEqual(first["response_format"]["json_schema"]["schema"]["properties"]["target_stances"]["minItems"], 2)
        expected = '今回の文脈で必須の target_stances.target は ["outward_speech", "self_activity"] です。'
        self.assertIn(expected, first["messages"][0]["content"])
        self.assertIn(expected, second["messages"][-1]["content"])
        self.assertIn("missing=self_activity", second["messages"][-1]["content"])

    def test_kind_specific_required_targets_are_preserved_after_generation(self) -> None:
        self.assertEqual(required_decision_targets(), ("outward_speech",))
        for kind in ("capability_request", "autonomous_run", "pending_intent"):
            with self.subTest(kind=kind):
                self.assertEqual(required_decision_targets(kind=kind), ("outward_speech", "self_activity"))
        missing = _capability_decision("mcp.call_tool", {})
        missing["target_stances"] = missing["target_stances"][:1]
        with self.assertRaisesRegex(LLMError, "missing=self_activity"):
            validate_decision_contract(missing)

    def test_scoped_comparison_rejects_extra_or_duplicate_targets(self) -> None:
        for scope in ("self_activity", "outward_speech"):
            for extra in ("self_activity", "outward_speech"):
                with self.subTest(scope=scope, extra=extra):
                    payload = _capability_decision("mcp.call_tool", {})
                    payload.update(kind="noop", capability_request=None)
                    payload["target_stances"] = build_decision_target_stances_for_kind(
                        "noop", required_targets=(scope, extra), reason_summary="今回は見送る。",
                    )
                    with self.assertRaises(LLMError):
                        validate_decision_contract(payload, comparison_scope=scope)

    def test_unknown_factor_repair_receives_actual_workspace_ids(self) -> None:
        context = replace(_decision_context([]), comparison_scope="outward_speech", workspace_context={
            "workspace_candidates": [{"factor_ref": "current_input:user_message"},
                                     {"factor_ref": "memory:person_model:0"}],
        })
        valid = {"speech_text": "検証用の発話です。",
            "kind": "speech", "reason_code": "reply", "reason_summary": "現在の好みを答える。",
            "requires_confirmation": False, "pending_intent": None, "capability_request": None,
            "autonomous_run": None, "foreground_selection": {
                "primary_factor_ref": "current_input:user_message",
                "supporting_factor_refs": ["memory:person_model:0"], "suppressed_factors": [],
                "summary_text": "現在の人物理解を使う。",
            }, "target_stances": build_decision_target_stances_for_kind(
                "speech", required_targets=("outward_speech",), reason_summary="問いに答える。"),
        }
        invalid = json.loads(json.dumps(valid))
        invalid["foreground_selection"]["supporting_factor_refs"] = ["memory:person_model:memory_unit:other"]
        with patch("otomekairo.llm.client.complete_text", side_effect=[json.dumps(invalid), json.dumps(valid)]) as complete:
            result = LLMClient().generate_decision(
                model_config={"model": "real-model"}, persona_context=_persona_context(), context=context,
            )
        self.assertEqual(result["foreground_selection"]["supporting_factor_refs"], ["memory:person_model:0"])
        self.assertEqual(complete.call_count, 2)
        repair = complete.call_args_list[1].kwargs["messages"][-1]["content"]
        self.assertIn('利用できる factor_ref 一覧=["current_input:user_message", "memory:person_model:0"]', repair)

    def test_full_decision_returns_first_valid_candidate_in_one_completion(self) -> None:
        # LLMが選んだ判断の受け渡しと呼び出し回数を検証し、自然文の精度は評価しない。
        for kind, text in (
            ("speech", "通知機能について教えて。"),
            ("capability_request", "通知を一度確認して教えて。"),
            ("autonomous_run", "1分後に読書を終えて作業へ戻るよう声をかけて。"),
        ):
            with self.subTest(kind=kind):
                candidate = _capability_decision("mcp.call_tool", {
                    "mcp_server_id": "elyth", "tool_name": "get_notifications", "arguments": {},
                })
                candidate["kind"] = kind
                candidate["speech_text"] = "検証用の発話です。" if kind == "speech" else None
                candidate["reason_summary"] = "今回の依頼に応じる。"
                if kind != "capability_request":
                    candidate["capability_request"] = None
                if kind == "autonomous_run":
                    candidate["autonomous_run"] = {
                        "objective_summary": "1分後に読書を終えて作業へ戻るよう声をかける。",
                        "initial_step_summary": "声かけ時刻を決めて待機する。",
                        "coordination": {
                            "mode": "create_new", "target_run_ids": [],
                            "reason_summary": "新しい依頼として開始する。",
                        },
                    }
                candidate["target_stances"] = build_decision_target_stances_for_kind(
                    kind, required_targets=("outward_speech", "self_activity"),
                    reason_summary="今回の依頼に応じる。",
                )
                context = replace(
                    _decision_context(_mcp_capability_view()), input_text=text,
                    current_input=replace(_current_input(), text=text),
                    time_context={
                        "current_time_text": "現在時刻: 2026年6月20日 10時59分00秒", "weekday": "saturday",
                        "part_of_day": "morning",
                    },
                    autonomous_run_summaries=[{
                        "run_id": f"autonomous_run:{index}", "status": "waiting_timer",
                        "objective_summary": "指定時刻に一度知らせる。",
                    } for index in range(21)],
                )
                with patch("otomekairo.llm.client.complete_text", return_value=_wire_response(candidate)) as complete:
                    result = LLMClient().generate_decision(
                        model_config={"model": "real-test"},
                        persona_context=_persona_context(), context=context,
                    )
                self.assertEqual(result, candidate)
                complete.assert_called_once()
                self.assertEqual(complete.call_args.kwargs["response_format"]["json_schema"]["name"], "decision")
                payloads = [json.loads(message["content"].splitlines()[1])
                            for message in complete.call_args.kwargs["messages"] if message["role"] == "user"]
                internal = next(payload["internal_context"] for payload in payloads
                                if isinstance(payload, dict) and "internal_context" in payload)
                self.assertEqual(internal["time_context"], context.time_context)
                self.assertEqual(internal["autonomous_run_summaries"], context.autonomous_run_summaries)
                self.assertIn(context.current_input.to_prompt_payload(), payloads)

    def test_decision_contract_requires_foreground_selection(self) -> None:
        payload = {"speech_text": "検証用の発話です。",
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
        payload = {"speech_text": "検証用の発話です。",
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
        payload = {"speech_text": "検証用の発話です。",
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
            return_value=json.dumps(payload),
        ):
            actual = LLMClient().generate_decision(
                model_config={"model": "real-model"},
                persona_context=_persona_context(),
                context=context,
            )

        self.assertEqual(actual, payload)

    def test_decision_rejects_unknown_affect_context_factor_ref(self) -> None:
        payload = {"speech_text": "検証用の発話です。",
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
            return_value=json.dumps(payload),
        ):
            with self.assertRaisesRegex(LLMError, r"不明な参照=affect_context:recent_episode_affects:0"):
                LLMClient().generate_decision(
                    model_config={"model": "real-model"},
                    persona_context=_persona_context(),
                    context=context,
                )

    def test_decision_contract_names_duplicate_foreground_factor_ref(self) -> None:
        payload = {"speech_text": None,
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
        payload = {"speech_text": None,
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
        payload = {"speech_text": "検証用の発話です。",
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

    def test_noop_with_periodic_thought_topic_requires_self_activity_hold(self) -> None:
        workspace = {
            "workspace_candidates": [
                {
                    "factor_ref": "periodic_thought_topic:elyth",
                    "kind": "periodic_thought_topic",
                    "summary_text": "ELYTH。",
                }
            ]
        }
        payload = {"speech_text": None,
            "kind": "noop",
            "reason_code": "hold_speech",
            "reason_summary": "作業中なので問いかけない。",
            "requires_confirmation": False,
            "pending_intent": None,
            "capability_request": None,
            "autonomous_run": None,
            "foreground_selection": {
                "primary_factor_ref": "periodic_thought_topic:elyth",
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
        payload = {"speech_text": "検証用の発話です。",
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
        payload = {"speech_text": "検証用の発話です。",
            "kind": "speech",
            "reason_code": "reply",
            "reason_summary": "話しかける。",
            "requires_confirmation": False,
            "pending_intent": None,
            "capability_request": None,
            "autonomous_run": None,
            "foreground_selection": {
                "primary_factor_ref": "periodic_thought_topic:elyth",
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
        payload = {"speech_text": None,
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
                "primary_factor_ref": "periodic_thought_topic:elyth",
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
            side_effect=[_wire_response(invalid), _wire_response(valid)],
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
            "vision.capture",
            {
                "vision_source_id": "vision_source:desktop",
                "mode": "still",
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
                "id": "vision.capture",
                "available": False,
                "unavailable_reason": "no_binding",
            },
            *_mcp_capability_view(),
        ]

        with patch(
            "otomekairo.llm.client.complete_text",
            side_effect=[_wire_response(invalid), _wire_response(valid)],
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
            "mcp.call_tool",
            {
                "mcp_server_id": "elyth",
                "tool_name": "get_notifications",
            },
        )
        valid_input = {
            **invalid["capability_request"]["input"],
            "arguments": {},
        }
        valid = _capability_decision("mcp.call_tool", valid_input)
        capability_view = _mcp_capability_view()

        with patch(
            "otomekairo.llm.client.complete_text",
            side_effect=[_wire_response(invalid), _wire_response(valid)],
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
                    side_effect=[_wire_response(invalid), _wire_response(valid)],
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
            last_result_context=None, affect_context={},
        )

        with patch(
            "otomekairo.llm.client.complete_text",
            side_effect=[_wire_response(invalid), _wire_response(valid)],
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
            return_value=_wire_response(invalid),
        ) as complete:
            with self.assertRaisesRegex(LLMError, "CapabilityDecisionView"):
                LLMClient().generate_decision(
                    model_config={"model": "real-model"},
                    persona_context=_persona_context(),
                    context=_decision_context(_mcp_capability_view()),
                )

        self.assertEqual(complete.call_count, 2)

    def test_decision_validation_failed_log_includes_rejected_payload(self) -> None:
        invalid = {"speech_text": None,
            "kind": "noop",
            "reason_code": "hold_speech",
            "reason_summary": "作業中なので問いかけない。",
            "requires_confirmation": False,
            "pending_intent": None,
            "capability_request": None,
            "autonomous_run": None,
            "foreground_selection": {
                "primary_factor_ref": "visual_observation:current",
                "supporting_factor_refs": [],
                "suppressed_factors": [],
                "summary_text": "候補に存在しない観測を選んだ。",
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
                return_value=json.dumps(invalid),
            ),
        ):
            with self.assertRaisesRegex(LLMError, r"不明な参照=visual_observation:current"):
                LLMClient().generate_decision(
                    model_config={"model": "real-model"},
                    persona_context=_persona_context(),
                    context=context,
                )

        warnings = [message for level, component, message in logs if level == "WARNING" and component == "LLM"]
        self.assertEqual(len(warnings), 2)
        for message in warnings:
            self.assertIn("decision:outward_speech validation_failed", message)
            self.assertIn("不明な参照=visual_observation:current", message)
            self.assertIn("payload=", message)
            self.assertIn('"primary_factor_ref":"visual_observation:current"', message)
            self.assertIn('"supporting_factor_refs":[]', message)

    def test_outward_decision_returns_first_valid_candidate_with_source_context(self) -> None:
        # 生成結果と資料の受け渡し、呼び出し回数を確認し、自然文の精度は評価しない。
        for trigger_kind in ("wake", "background_thinking"):
            for kind in ("speech", "noop", "pending_intent"):
                with self.subTest(trigger_kind=trigger_kind, kind=kind):
                    current = replace(
                        _current_input(), sender_kind="system", sender_ref=None,
                        source_kind=trigger_kind, response_target_refs=(), text="自己評価。",
                    )
                    context = replace(
                        _decision_context([]), comparison_scope="outward_speech",
                        trigger_kind=trigger_kind, input_text=current.text, current_input=current,
                        recent_turns=[
                            {"role": "person", "text": "様子を教えて。"},
                            {"role": "assistant", "text": "机の前でPCを操作している人物が見える。"},
                        ],
                        recent_interactions=[{
                            "interaction_ref": "interaction:other",
                            "turns": [{"role": "assistant", "text": "依頼を受け取った。"}],
                        }],
                        current_visual_observations=[{"visual_summary_text": "PCを操作している人物が見える。"}],
                        workspace_context={"workspace_candidates": [{
                            "factor_ref": "conversation_context:current", "kind": "conversation_context",
                            "summary_text": "直近のやり取りを比較する。",
                        }]},
                    )
                    candidate = {"speech_text": "検証用の発話です。" if kind == "speech" else None,
                        "kind": kind, "reason_code": "outward_comparison",
                        "reason_summary": "直近の会話と今伝える意味を比較した。",
                        "requires_confirmation": False, "capability_request": None, "autonomous_run": None,
                        "pending_intent": {
                            "intent_kind": "outward_speech", "intent_summary": "あとで伝える意味を再評価する。",
                            "dedupe_key": "outward:test",
                        } if kind == "pending_intent" else None,
                        "foreground_selection": {
                            "primary_factor_ref": "conversation_context:current", "supporting_factor_refs": [],
                            "suppressed_factors": [], "summary_text": "直近のやり取りを主因にした。",
                        },
                        "target_stances": build_decision_target_stances_for_kind(
                            kind, required_targets=("outward_speech",), reason_summary="直近の会話を踏まえる。",
                        ),
                    }
                    with patch("otomekairo.llm.client.complete_text", return_value=_wire_response(candidate)) as complete:
                        result = LLMClient().generate_decision(
                            model_config={"model": "real-test"},
                            persona_context=_persona_context(), context=context,
                        )
                    self.assertEqual(result, candidate)
                    complete.assert_called_once()
                    self.assertEqual(complete.call_args.kwargs["response_format"]["json_schema"]["name"], "decision_outward_speech")
                    payloads = [json.loads(message["content"].splitlines()[1])
                                for message in complete.call_args.kwargs["messages"] if message["role"] == "user"]
                    source = next(payload for payload in payloads if isinstance(payload, dict) and "internal_context" in payload)
                    self.assertEqual(source["recent_turns"], context.recent_turns)
                    self.assertEqual(source["recent_interactions"], context.recent_interactions)
                    self.assertEqual(source["internal_context"]["workspace_context"], context.workspace_context)
                    self.assertEqual(source["internal_context"]["current_visual_observations"], context.current_visual_observations)
                    self.assertIn(current.to_prompt_payload(), payloads)

    def test_decision_parse_failed_log_includes_rejected_content(self) -> None:
        logs: list[tuple[str, str, str]] = []

        def capture(component: str, message: str, *, level: str = "INFO") -> None:
            logs.append((level, component, message))

        with (
            patch("otomekairo.llm.client.debug_log", side_effect=capture),
            patch(
                "otomekairo.llm.client.complete_text",
                return_value="これは JSON ではありません。",
            ),
        ):
            with self.assertRaises(LLMError):
                LLMClient().generate_decision(
                    model_config={"model": "real-model"},
                    persona_context=_persona_context(),
                    context=_decision_context([]),
                )

        warnings = [message for level, component, message in logs if level == "WARNING" and component == "LLM"]
        self.assertEqual(len(warnings), 2)
        for message in warnings:
            self.assertIn("decision parse_failed", message)
            self.assertIn("content=これは JSON ではありません。", message)

    def test_decision_error_log_keeps_full_exception_text(self) -> None:
        long_message = "Invalid schema for response_format " + ("x" * 400) + "TAIL"
        logs: list[tuple[str, str, str]] = []

        def capture(component: str, message: str, *, level: str = "INFO") -> None:
            logs.append((level, component, message))

        with (
            patch("otomekairo.llm.client.debug_log", side_effect=capture),
            patch("otomekairo.llm.client.complete_text", side_effect=LLMError(long_message)),
        ):
            with self.assertRaises(LLMError):
                LLMClient().generate_decision(
                    model_config={"model": "real-model"},
                    persona_context=_persona_context(),
                    context=_decision_context([]),
                )

        errors = [message for level, component, message in logs if level == "ERROR" and component == "LLM"]
        self.assertEqual(errors, [f"decision failed error=LLMError: {long_message}"])

    def test_warning_debug_error_stays_character_limited(self) -> None:
        message = "schema " + ("y" * 400) + "TAIL"
        text = LLMClient()._debug_error(LLMError(message), level="WARNING")
        self.assertEqual(len(text), 240)
        self.assertTrue(text.endswith("…"))
        self.assertNotIn("TAIL", text)

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
        self.assertIn("今、候補に出ている活動や継続中の自身の活動へ関わるか", system)
        self.assertIn("capability_request / autonomous_run / pending_intent / noop", system)
        self.assertIn("活動と CapabilityDecisionView の catalog から autonomous_run を始めてよい", system)
        self.assertIn("autonomous_run.objective_summary は今回の関与の範囲と完了条件を、現在の個の言葉で書きます", system)
        self.assertIn("一度の能力実行とその結果判断で済むなら capability_request", system)
        self.assertIn("capability_request.input の自然文は、その能力の先の場へ向けた現在の個の表現です", system)
        self.assertIn("capability_request.input は required_input と readiness.input_keys に対応する JSON object を 1 個の JSON 文字列として書いてください。", system)
        self.assertIn("使わない排他キーもキーとして残し、値は null にします", system)
        self.assertIn("kind が capability_request のとき capability_request は object、pending_intent と autonomous_run は null です", system)
        self.assertIn("その活動に関われる手段が CapabilityDecisionView に available=true であるときだけ", system)
        self.assertIn("手段が無いときは今は関わらない", system)
        self.assertIn("target_stances は self_activity を 1 件だけ持ちます", system)
        self.assertIn("控える理由を説明できる候補を primary factor に選んでください", system)
        self.assertNotIn("会話の間合いは conversation_context", system)
        self.assertNotIn("伝達、能力実行、保留、見送り、継続目的開始のどれが", system)
        self.assertNotIn("人物発話自体が未来実行", system)
        self.assertNotIn("outward_speech は毎回必須です", system)
        self.assertIn("AffectContext の affect_states と recent_episode_affects は WorkspaceContext の affect 候補です。", system)

    def test_outward_speech_prompt_asks_short_view_not_visit(self) -> None:
        system = self._system_prompt("outward_speech")
        self.assertIn("今、外へ短い見方を出すか", system)
        self.assertIn("未回答の問いがまだ相手の番なら", system)
        self.assertIn("各周期で、未回答の問いがまだ相手の番か", system)
        self.assertIn("やり取りが閉じた、または切り替わったと読めたあとは", system)
        self.assertIn("返答待ちを続ける理由にも、待ちを終える理由にもしません", system)
        self.assertNotIn("相手が次に話す番として", system)
        self.assertNotIn("独り言も相手には次の発話として聞こえる", system)
        self.assertIn("speech / noop / pending_intent", system)
        self.assertIn("観測事実に基づく一文の状況認識", system)
        self.assertIn("助言、依頼、支援提案、休息促し、身体注意、画面への一般コメントは speech ではなく控える理由", system)
        self.assertIn("target_stances は outward_speech を 1 件だけ持ちます", system)
        self.assertIn("控える理由を説明できる候補を primary factor に選んでください", system)
        self.assertIn("構造化済み抑制は suppression、会話の間合いは conversation_context 候補を使えます", system)
        self.assertIn("AffectContext の affect_states と recent_episode_affects は WorkspaceContext の affect 候補です。", system)
        self.assertNotIn("今見に行く自然さがあれば capability_request", system)
        self.assertNotIn("活動と CapabilityDecisionView の catalog から autonomous_run", system)
        self.assertNotIn("有限 MCP セッションは CapabilityDecisionView", system)

    def test_full_prompt_keeps_combined_question(self) -> None:
        system = self._system_prompt("full")
        self.assertIn("伝達、能力実行、保留、見送り、継続目的開始のどれが", system)
        self.assertIn("outward_speech は毎回必須です", system)
        self.assertIn("各周期で、未回答の問いがまだ相手の番か", system)
        self.assertNotIn("相手が次に話す番として", system)
        self.assertIn("控える理由を説明できる候補を primary factor に選んでください", system)
        self.assertIn("構造化済み抑制は suppression、会話の間合いは conversation_context 候補を使えます", system)
        self.assertIn("載っている self_activity は hold です。対話の継続は outward_speech です。", system)
        self.assertIn("Agent Skill の skill_id は capability_id でも MCP tool_name でもありません。", system)
        self.assertIn("使わない排他キーもキーとして残し、値は null にします", system)
        self.assertIn("capability_request.input は required_input と readiness.input_keys に対応する JSON object を 1 個の JSON 文字列として書いてください。", system)
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
        self.assertIn("capability_request.input は required_input と readiness.input_keys に対応する JSON object を 1 個の JSON 文字列として書いてください。", self_repair)
        self.assertNotIn("outward_speech は毎回必須です", self_repair)
        self.assertIn("speech / noop / pending_intent", outward_repair)
        self.assertIn("target_stances は outward_speech を 1 件だけ持ちます", outward_repair)
        self.assertNotIn("今見に行く自然さがあれば capability_request", outward_repair)

    def test_api_request_uses_own_policy_even_at_lowest_speech_frequency(self) -> None:
        current_input = CurrentInput(
            sender_kind="system", sender_ref=None, source_kind="wake",
            response_target_refs=(), interaction_context=None,
            text="Codex からの依頼。修正が終了したと伝えてほしい。",
            is_api_request=True,
        )
        initiative = InitiativeContext(
            trigger_kind="wake", opportunity_summary="API起床。",
            initiative_entry_summary=None, time_context_summary={},
            foreground_signal_summary={}, activity_context=None, persona_context_summary={},
            runtime_state_summary={}, recent_turn_summary=[], drive_summaries=[],
            pending_intent_summaries=[], world_state_summary=[], ongoing_action_summary=None,
            capability_summary={}, candidate_families=[], selected_candidate_family=None,
            speech_timing_state={}, suppression_summary={}, speech_timing_summary="",
            speech_frequency_level=1,
        )
        context = replace(
            _decision_context([]), current_input=current_input,
            input_text=current_input.text, trigger_kind="wake", initiative_context=initiative,
        )
        with patch("otomekairo.llm.prompts._speech_frequency_policy", side_effect=AssertionError("frequency")):
            messages = build_decision_messages(persona_context=_persona_context(), context=context)
        request_policy = _build_decision_trigger_policy(
            initiative_context=initiative, capability_result_context=None, is_api_request=True,
        )
        self.assertTrue(all(policy in messages[-2]["content"] for policy in request_policy))
        self.assertIn('"is_api_request":true', messages[-1]["content"])
        self.assertIn(current_input.text, messages[-1]["content"])
        with patch("otomekairo.llm.prompts._speech_frequency_policy", return_value="frequency policy") as frequency:
            policies = _build_decision_trigger_policy(
                initiative_context=initiative, capability_result_context=None, is_api_request=False,
            )
        frequency.assert_called_once_with(1)
        self.assertIn("frequency policy", policies)

    def test_trigger_policy_splits_initiative_question(self) -> None:
        from otomekairo.llm.contexts import InitiativeContext

        initiative = InitiativeContext(
            trigger_kind="background_thinking",
            opportunity_summary="定期思考トピックがしばらく前景に出ていない。",
            initiative_entry_summary=None,
            time_context_summary={},
            foreground_signal_summary={},
            activity_context=None,
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
        self.assertNotIn("periodic_thought_topic は実行指示ではありません", self_text)
        self.assertNotIn("向きと catalog から autonomous_run を始めてよい", self_text)
        self.assertNotIn("短い独り言", self_text)
        self.assertIn("speech は短い独り言", outward_text)
        self.assertIn("speech_frequency_level は 5", outward_text)
        self.assertIn("1 から 3 は控えめです", outward_text)
        self.assertIn("8 から 10 は、重要性や今必要な続きであることを求めません", outward_text)
        self.assertIn("10 はその上端です", outward_text)
        self.assertIn("話せる材料があること自体は、発話を選ぶ理由にしません", outward_text)
        self.assertNotIn("speech_frequency_level", self_text)
        self.assertNotIn("向きと catalog から autonomous_run", outward_text)
        self.assertNotIn("speech / noop / pending_intent / capability_request / autonomous_run から 1 つ", outward_text)

    def test_compact_initiative_trace_keeps_speech_frequency_level(self) -> None:
        class _Compact(ServiceInputTraceCompactMixin):
            def _clamp(self, value: str | None, limit: int = 160) -> str | None:
                if value is None:
                    return None
                stripped = value.strip()
                if len(stripped) <= limit:
                    return stripped
                return stripped[: limit - 1] + "…"

            def _compact_visual_observation_signals(self, value: object) -> list[dict[str, object]]:
                return []

        initiative = InitiativeContext(
            trigger_kind="background_thinking",
            opportunity_summary="短い見方を比べる。",
            initiative_entry_summary=None,
            time_context_summary={},
            foreground_signal_summary={},
            activity_context=None,
            persona_context_summary={},
            runtime_state_summary={},
            recent_turn_summary=[],
            drive_summaries=[],
            pending_intent_summaries=[],
            world_state_summary=[],
            ongoing_action_summary=None,
            capability_summary={},
            candidate_families=[],
            selected_candidate_family=None,
            speech_timing_state={},
            suppression_summary={},
            speech_timing_summary="",
            speech_frequency_level=10,
        )
        summary = _Compact()._compact_initiative_context_summary(
            initiative_context=initiative,
            pending_intent_selection=None,
        )
        self.assertEqual(summary["speech_frequency_level"], 10)

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
                last_result_context=None, affect_context={},
            ),
        )
        self.assertIn(
            "capability_request.input の自然文は、その能力の先の場へ向けた現在の個の表現です",
            messages[0]["content"],
        )
        self.assertIn(
            "capability_request.input は required_input と readiness.input_keys に対応する JSON object を 1 個の JSON 文字列として書いてください。",
            messages[0]["content"],
        )
        self.assertIn(
            "arguments など入れ子も、その object の中に書きます。",
            messages[0]["content"],
        )

    def test_autonomous_step_repair_prompt_asks_nested_input_object(self) -> None:
        repair = build_autonomous_step_repair_prompt(
            "AutonomousStep action.capability_request.input は object である必要があります。"
        )
        self.assertIn(
            "capability_request.input は required_input と readiness.input_keys に対応する JSON object を 1 個の JSON 文字列として書いてください。",
            repair,
        )
        self.assertIn("arguments など入れ子も、その object の中に書きます。", repair)

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


if __name__ == "__main__":
    unittest.main()


class AutonomousActivityAlignmentReviewTests(unittest.TestCase):
    def test_review_contract_and_prompt(self) -> None:
        review_context = {
            "current_input": {"sender_kind": "system"},
            "decision": {"autonomous_run": {"objective_summary": "活動を確認する。"}},
            "primary_candidate": {"kind": "periodic_thought_topic"},
            "supporting_candidates": [{"kind": "drive_state", "scopes": {"focus_scope_type": "person"}}],
            "periodic_thought_topics": [],
        }
        messages = build_autonomous_activity_alignment_review_messages(review_context=review_context)
        self.assertIn("補助候補は背景材料", messages[0]["content"])
        self.assertIn("primary_candidate", messages[1]["content"])
        validate_autonomous_activity_alignment_review_contract(
            {"outcome": "reject", "reason_summary": "活動に無関係な条件がある。"}
        )
        with self.assertRaises(LLMError):
            validate_autonomous_activity_alignment_review_contract(
                {"outcome": "allow_start", "reason_summary": "契約外。"}
            )

    def test_review_uses_structured_transport_and_mock_fails_closed(self) -> None:
        with patch("otomekairo.llm.client.complete_text", return_value=json.dumps(
            {"outcome": "allow", "reason_summary": "活動の範囲に沿う。"}
        )) as complete:
            result = LLMClient().generate_autonomous_activity_alignment_review(
                model_config={"model": "real-model"}, review_context={"decision": {}}
            )
        self.assertEqual(result["outcome"], "allow")
        self.assertEqual(complete.call_count, 1)
        with self.assertRaises(LLMError):
            LLMClient().generate_autonomous_activity_alignment_review(
                model_config={"model": "mock-test"}, review_context={}
            )
