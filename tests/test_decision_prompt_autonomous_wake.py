from __future__ import annotations

import unittest

from otomekairo.llm.contexts import (
    CurrentInput,
    DecisionContext,
    InitiativeCandidateFamily,
    InitiativeContext,
    build_persona_context,
)
from otomekairo.llm.prompts import build_decision_messages


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
        self.assertNotIn("非ユーザー起点では、speech-ready", combined)
        self.assertNotIn("speech 義務", combined)
        self.assertNotIn("新規性だけ", combined)


if __name__ == "__main__":
    unittest.main()
