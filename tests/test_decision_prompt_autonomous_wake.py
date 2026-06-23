from __future__ import annotations

import unittest

from otomekairo.llm.contexts import (
    CurrentInput,
    DecisionContext,
    InitiativeCandidateFamily,
    InitiativeContext,
    SpeechContext,
    build_persona_context,
)
from otomekairo.llm.prompts import (
    build_activity_state_messages,
    build_activity_state_repair_prompt,
    build_decision_messages,
    build_speech_messages,
    build_visual_observation_messages,
    build_visual_observation_repair_prompt,
)


class DecisionPromptAutonomousWakeTests(unittest.TestCase):
    def test_background_wake_prompt_presents_evaluation_not_speech_request(self) -> None:
        persona_context = build_persona_context(
            {
                "display_name": "テスト",
                "initiative_baseline": "medium",
                "reference_style": {"user_natural_reference": "マスター"},
                "persona_prompt": "落ち着いて判断する。",
            },
            role="decision_generation",
        )
        initiative_context = InitiativeContext(
            trigger_kind="background_wake",
            opportunity_summary="自律判断の評価機会。",
            initiative_entry_summary={
                "entry_kind": "enter",
                "entry_basis": "activity_mode_transition",
                "reason_summary": "活動が切り替わった。",
            },
            time_context_summary={},
            foreground_signal_summary={
                "foreground_thinness": "thin",
                "visual_observations": [{"change_state": "changed"}],
            },
            activity_context=None,
            initiative_baseline={"level": "medium"},
            persona_context_summary={},
            runtime_state_summary={},
            recent_turn_summary=[],
            drive_summaries=[],
            pending_intent_summaries=[],
            world_state_summary=[],
            ongoing_action_summary=None,
            capability_summary={},
            candidate_families=[
                InitiativeCandidateFamily(
                    family="autonomous",
                    available=True,
                    selected=True,
                    priority_score=1.0,
                    reason_summary="評価対象が前景化している。",
                )
            ],
            selected_candidate_family="autonomous",
            intervention_state={"background_trigger": True},
            suppression_summary={
                "suppression_level": "low",
                "visual_repetition_present": False,
            },
            intervention_risk_summary="",
        )
        context = DecisionContext(
            input_text="定期起床。",
            current_input=CurrentInput(
                sender="system",
                source_kind="background_wake",
                response_target="none",
                text="定期起床。",
            ),
            trigger_kind="background_wake",
            recent_turns=[],
            time_context={},
            affect_context={},
            drive_state_summary=None,
            foreground_world_state=None,
            activity_context=None,
            ongoing_action_summary=None,
            autonomous_run_summaries=None,
            capability_decision_view=None,
            initiative_context=initiative_context,
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

        messages = build_decision_messages(persona_context=persona_context, context=context)
        system_prompt = messages[0]["content"]
        context_prompt = messages[1]["content"]
        combined = system_prompt + "\n" + context_prompt

        self.assertIn("現在の個として関わる、保留する、見送る、能力を使う", system_prompt)
        self.assertIn("noop は前へ出ない判断", system_prompt)
        self.assertIn("定期的な自己評価起点", context_prompt)
        self.assertIn("控える理由の材料", context_prompt)
        self.assertIn("意味レイヤー境界", system_prompt)
        self.assertIn("内部処理は次の意味レイヤー", system_prompt)
        self.assertIn("行動判断の理由は、観測可能な活動事実と構造化済みの抑制根拠", system_prompt)
        self.assertIn("自己申告された注意状態は、ユーザー発話の内容", system_prompt)
        self.assertIn("割り込み抑制は行動判断層", system_prompt)
        self.assertIn("活動事実として表現", system_prompt)
        self.assertIn("見送り理由は、反復抑制、直近で触れた内容", system_prompt)
        self.assertIn("change_state=first_seen / changed は wake 判断の前景候補", context_prompt)
        self.assertIn("WorkspaceContext の visual_observation 候補を主因", context_prompt)
        self.assertIn("短い speech を第一候補", context_prompt)
        self.assertNotIn("非ユーザー起点では、speech-ready", combined)
        self.assertNotIn("speech 義務", combined)
        self.assertNotIn("新規性だけ", combined)
        self.assertNotIn("noop の主理由", combined)
        self.assertNotIn("判断対象にしません", combined)
        self.assertNotIn("内的注意状態を理由にしない", combined)
        self.assertNotIn("集中", combined)
        self.assertNotIn("没頭", combined)

    def test_observation_prompts_use_shared_semantic_layer_boundary(self) -> None:
        persona = {
            "display_name": "テスト",
            "initiative_baseline": "medium",
            "reference_style": {"user_natural_reference": "マスター"},
            "persona_prompt": "落ち着いて判断する。",
        }
        activity_context = build_persona_context(persona, role="activity_state")
        activity_system_prompt = build_activity_state_messages(
            persona_context=activity_context,
            source_pack={"current_input": {"sender": "system", "text": "background wake"}},
        )[0]["content"]
        visual_context = build_persona_context(persona, role="visual_observation")
        visual_system_prompt = build_visual_observation_messages(
            persona_context=visual_context,
            source_pack={"image_input_kind": "vision_capture_result"},
            images=[],
        )[0]["content"]

        self.assertIn("この role の担当は 活動推定層", activity_system_prompt)
        self.assertIn("この role の担当は 観測事実層", visual_system_prompt)
        self.assertIn("行動判断層: decision_generation だけ", activity_system_prompt)
        self.assertIn("行動判断層: decision_generation だけ", visual_system_prompt)
        self.assertIn("出力値と reason_summary は担当レイヤーの材料で構成", activity_system_prompt)
        self.assertIn("出力値と reason_summary は担当レイヤーの材料で構成", visual_system_prompt)
        self.assertIn("この role の担当は 活動推定層", build_activity_state_repair_prompt("invalid"))
        self.assertIn("この role の担当は 観測事実層", build_visual_observation_repair_prompt("invalid"))
        self.assertNotIn("画面注視、入力操作、閲覧、ゲームプレイは活動モード", activity_system_prompt)
        self.assertNotIn("画面注視、入力操作、閲覧、ゲームプレイは見えている動作", visual_system_prompt)
        self.assertNotIn("集中", activity_system_prompt)
        self.assertNotIn("没頭", activity_system_prompt)
        self.assertNotIn("集中", visual_system_prompt)
        self.assertNotIn("没頭", visual_system_prompt)

    def test_expression_prompt_does_not_reopen_action_judgement(self) -> None:
        persona_context = build_persona_context(
            {
                "display_name": "テスト",
                "initiative_baseline": "medium",
                "reference_style": {"user_natural_reference": "マスター"},
                "persona_prompt": "落ち着いて判断する。",
            },
            role="expression_generation",
            include_expression=True,
        )
        current_input = CurrentInput(
            sender="system",
            source_kind="background_wake",
            response_target="none",
            text="定期起床。",
        )
        context = SpeechContext(
            input_text="定期起床。",
            current_input=current_input,
            recent_turns=[],
            time_context={},
            affect_context={},
            drive_state_summary=None,
            foreground_world_state=None,
            activity_context=None,
            ongoing_action_summary=None,
            initiative_context=None,
            visual_observation_context=None,
            self_state_context=None,
            relationship_context=None,
            prediction_error_context=None,
            workspace_context=None,
            recall_hint={},
            recall_pack={},
            decision={"kind": "speech", "reason_summary": "短く触れる。"},
        )

        system_prompt = build_speech_messages(persona_context=persona_context, context=context)[0]["content"]

        self.assertIn("この role の担当は 表現層", system_prompt)
        self.assertIn("decision.kind と foreground_selection を維持", system_prompt)
        self.assertIn("decision.reason_summary と internal_context の根拠", system_prompt)
        self.assertNotIn("decision に無い抑制理由", system_prompt)
        self.assertNotIn("集中", system_prompt)
        self.assertNotIn("没頭", system_prompt)


if __name__ == "__main__":
    unittest.main()
