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
    build_initiative_entry_check_messages,
    build_speech_messages,
    build_visual_observation_messages,
    build_visual_observation_repair_prompt,
)


class DecisionPromptAutonomousWakeTests(unittest.TestCase):
    def test_background_thinking_prompt_presents_evaluation_not_speech_request(self) -> None:
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
            trigger_kind="background_thinking",
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
            speech_frequency_level=7,
        )
        context = DecisionContext(
            input_text="定期思考。",
            current_input=CurrentInput(
                sender="system",
                source_kind="background_thinking",
                response_target="none",
                text="定期思考。",
            ),
            trigger_kind="background_thinking",
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
        self.assertIn("定期思考による自己評価", context_prompt)
        self.assertIn("控える理由の材料", context_prompt)
        self.assertIn("意味レイヤー境界", system_prompt)
        self.assertIn("内部処理は次の意味レイヤー", system_prompt)
        self.assertIn("観測可能な活動事実を何が前景にあるかの説明", system_prompt)
        self.assertIn("外向き発話の見送りは構造化済みの抑制根拠", system_prompt)
        self.assertIn("自己申告された注意状態は、ユーザー発話の内容", system_prompt)
        self.assertIn("外向き発話の抑制は行動判断層", system_prompt)
        self.assertIn("活動事実として表現", system_prompt)
        self.assertIn("現在活動ラベルは活動事実として表現", system_prompt)
        self.assertIn("見送り理由は、反復抑制、直近で触れた内容", system_prompt)
        self.assertIn("活動事実は見送りの根拠名ではなく", system_prompt)
        self.assertIn("支援要求の有無、明示的な呼びかけの有無", system_prompt)
        self.assertIn("活動名、作業名、閲覧中、検討中、入力中、操作中などの活動事実", system_prompt)
        self.assertIn("外向き発話の抑制根拠になりません", system_prompt)
        self.assertIn("観測から集中や没頭を推定して", system_prompt)
        self.assertIn("判断理由にしない", system_prompt)
        self.assertIn("短い状況認識として閉じる speech", system_prompt)
        self.assertIn("支援提案とは別の軽い外向き行動", system_prompt)
        self.assertIn("background_thinking: 定期思考による自己評価", context_prompt)
        self.assertIn("ユーザーの反応を求めない独話的な短い状況認識", context_prompt)
        self.assertIn("発話自然度を 10 段階", context_prompt)
        self.assertIn("発話頻度レベル 7", context_prompt)
        self.assertIn("5 は標準頻度", context_prompt)
        self.assertIn("3 以下は控えめ基準", context_prompt)
        self.assertIn("変化があるから話すのではなく", context_prompt)
        self.assertIn("独話として残す意味が明確な変化だけ", context_prompt)
        self.assertIn("評価値は JSON や reason_summary に出力しない", context_prompt)
        self.assertIn("材料: visual_observations", context_prompt)
        self.assertIn("選択: speech", context_prompt)
        self.assertIn("節目: 同一活動内の意味的な節目", context_prompt)
        self.assertIn("発話境界: speech", context_prompt)
        self.assertIn("抑制境界: 作業中", context_prompt)
        self.assertIn("change_state=first_seen / changed は前景候補", context_prompt)
        self.assertIn("stable / same_as_recent_speech は反復抑制候補", context_prompt)
        self.assertIn("複数 source の first_seen / changed", context_prompt)
        self.assertIn("具体的な前景がある場合に発話頻度レベル 7 の speech 候補", context_prompt)
        self.assertIn("change_state=first_seen / changed だけでは speech を選ばず", context_prompt)
        self.assertIn("画面・対象・操作単位の変化は候補材料に留めてください", context_prompt)
        self.assertIn("活動名、作業名、閲覧中、検討中、入力中、操作中などの活動事実", context_prompt)
        self.assertIn("何が前景にあるかの材料", context_prompt)
        self.assertIn("それ自体を noop の主理由にしない", context_prompt)
        self.assertIn("speech / pending_intent / noop で比較", context_prompt)
        self.assertIn("活動モード遷移、同一活動内の意味的な節目", context_prompt)
        self.assertIn("短い状況認識として外へ出す新しい意味", context_prompt)
        self.assertIn("独話として一文で自然に閉じ", context_prompt)
        self.assertIn("具体的な抑制根拠が上回らない", context_prompt)
        self.assertIn("活動の段階、結果、保留、比較軸、未完了状態", context_prompt)
        self.assertIn("意味的に変わる場合だけ speech に寄せてください", context_prompt)
        self.assertIn("緊急性、支援必要性、会話開始としての必要性を条件にしない", context_prompt)
        self.assertIn("後で再評価する価値が残る", context_prompt)
        self.assertIn("反復、直近で同じ内容に触れた事実", context_prompt)
        self.assertIn("speech の価値を明確に上回る", context_prompt)
        self.assertIn("活動事実ではなく観測された前景差分そのもの", context_prompt)
        self.assertIn("新しい意味が薄い場合", context_prompt)
        self.assertIn("foreground_signal_summary.foreground_thinness=thin では", context_prompt)
        self.assertIn("独話として外へ出す新しい意味が弱い場合は noop または pending_intent", context_prompt)
        self.assertIn("集中、没頭、作業中、閲覧中、検討中、入力中、操作中", context_prompt)
        self.assertIn("活動の一環、作業の継続、遮る、割って入る、介入回避", context_prompt)
        self.assertIn("プライバシー境界、観測不足", context_prompt)
        self.assertIn("candidate_families に capability 提案", context_prompt)
        self.assertIn("対象の意味的な切り替わり、対象の絞り込み", context_prompt)
        self.assertIn("比較軸の変化、進行阻害", context_prompt)
        self.assertIn("同じ大きな流れの中の画面・対象・操作単位の変化", context_prompt)
        self.assertIn("それだけでは節目として扱わず", context_prompt)
        self.assertIn("活動の段階や結果に意味的な変化がある場合だけ speech 候補", context_prompt)
        self.assertIn("foreground_signal_summary.foreground_thinness=thin", context_prompt)
        self.assertIn("支援要求がないこと、外へ出る必要が薄いという一般的な推定", context_prompt)
        self.assertIn("観測から推定した集中や没頭", context_prompt)
        self.assertIn("それ単体では noop の主理由にしない", context_prompt)
        self.assertIn("観測事実に基づく一文の独話的な状況認識として成立する場合", context_prompt)
        self.assertIn("助言、依頼、支援提案、反応要求ではなく", context_prompt)
        self.assertIn("PersonaContext は距離感と表現補助", context_prompt)
        self.assertIn("薄い観測や表層的な前景変化を speech に押し上げない", context_prompt)
        self.assertIn("foreground_drive_summaries または構造値が強い場合だけ speech の支柱", context_prompt)
        self.assertIn("freshness_hint=stale、stability_hint=weak、signal_strength=0.0", context_prompt)
        self.assertIn("一般的な関係構築や休息促しを控える理由側", context_prompt)
        self.assertIn("反復に近い詳細更新", context_prompt)
        self.assertIn("画面・対象・操作単位の小さな変化", context_prompt)
        self.assertIn("観測対象の表層的な変化", context_prompt)
        self.assertIn("一般的な注意や助言に留まる内容", context_prompt)
        self.assertIn("それ単体では noop または pending_intent の材料", context_prompt)
        self.assertIn("活動が継続中であることは、この抑制理由に含めない", context_prompt)
        self.assertNotIn("単なる対象変更や作業の継続に留まらないか", combined)
        self.assertNotIn("節目として弱く扱ってください", combined)
        self.assertNotIn("外へ伝える必然性", combined)
        self.assertNotIn("直近で問題化された観点", combined)
        self.assertNotIn("関係、生活状態、活動モード遷移", combined)
        self.assertNotIn("不在から戻る", context_prompt)
        self.assertNotIn("戻って作業再開", context_prompt)
        self.assertNotIn("着席", context_prompt)
        self.assertNotIn("離席", context_prompt)
        self.assertNotIn("SNS", context_prompt)
        self.assertNotIn("タイムライン", context_prompt)
        self.assertNotIn("通知画面", context_prompt)
        self.assertNotIn("投稿詳細", context_prompt)
        self.assertNotIn("投稿文面", context_prompt)
        self.assertNotIn("ゲーム内", context_prompt)
        self.assertNotIn("ゲーム中", combined)
        self.assertNotIn("ゲームプレイ", combined)
        self.assertNotIn("X閲覧中", combined)
        self.assertNotIn("視覚変化だけを speech の主因", combined)
        self.assertNotIn("短い speech を第一候補", combined)
        self.assertNotIn("speech の第一候補", combined)
        self.assertNotIn("visual_observation 候補を主因", combined)
        self.assertNotIn("非ユーザー起点では、speech-ready", combined)
        self.assertNotIn("speech 義務", combined)
        self.assertNotIn("新規性だけ", combined)
        self.assertNotIn("background_thinking 判断表", combined)
        self.assertNotIn("判断対象にしません", combined)
        self.assertNotIn("内的注意状態を理由にしない", combined)
        self.assertNotIn("外向き介入が不要", combined)
        self.assertNotIn("割り込み抑制", combined)

    def test_initiative_entry_check_does_not_skip_by_same_activity_alone(self) -> None:
        persona_context = build_persona_context(
            {
                "display_name": "テスト",
                "initiative_baseline": "medium",
                "reference_style": {"user_natural_reference": "マスター"},
                "persona_prompt": "落ち着いて判断する。",
            },
            role="initiative_entry_check",
        )
        messages = build_initiative_entry_check_messages(
            persona_context=persona_context,
            source_pack={
                "input_context": {"trigger_kind": "background_thinking"},
                "activity_context": {
                    "previous_activity": {"label": "同じ活動", "target": "前の対象"},
                    "current_activity": {"label": "同じ活動", "target": "今の対象"},
                },
                "entry_policy": {
                    "allow_enter": True,
                    "allow_skip": True,
                    "enter_bases": ["activity_mode_transition", "strong_interest"],
                    "same_activity_detail_change_without_independent_meaning_is_skip": True,
                    "same_activity_detail_change_with_strong_interest_uses_strong_interest": True,
                },
            },
        )
        system_prompt = messages[0]["content"]
        user_prompt = messages[1]["content"]

        self.assertIn("具体的な前景変化や関係上の意味が薄い same_activity_detail_change", system_prompt)
        self.assertIn("同一活動内という分類だけでは skip にしない", system_prompt)
        self.assertIn("strong_interest として enter 候補に残してください", system_prompt)
        self.assertIn("speech / noop / pending_intent の最終選択", system_prompt)
        self.assertIn("same_activity_detail_change_without_independent_meaning_is_skip", user_prompt)
        self.assertIn("same_activity_detail_change_with_strong_interest_uses_strong_interest", user_prompt)
        self.assertNotIn("same_activity_detail_change_is_skip", user_prompt)
        self.assertNotIn("ゲーム中", system_prompt + "\n" + user_prompt)

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
            source_pack={"current_input": {"sender": "system", "text": "background thinking"}},
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
        self.assertIn("label は具体的な内容名や対象名ではなく", activity_system_prompt)
        self.assertIn("この role の担当は 活動推定層", build_activity_state_repair_prompt("invalid"))
        self.assertIn("この role の担当は 観測事実層", build_visual_observation_repair_prompt("invalid"))
        self.assertNotIn("ゲーム中", activity_system_prompt)
        self.assertNotIn("ゲームプレイ", activity_system_prompt)
        self.assertNotIn("X閲覧中", activity_system_prompt)
        self.assertNotIn("ゲーム中", visual_system_prompt)
        self.assertNotIn("ゲームプレイ", visual_system_prompt)
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
            source_kind="background_thinking",
            response_target="none",
            text="定期思考。",
        )
        context = SpeechContext(
            input_text="定期思考。",
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
        self.assertIn("反応を求めない短い独話的コメント", system_prompt)
        self.assertIn("1 文で閉じ", system_prompt)
        self.assertIn("助言、忠告、注意喚起、休息促し、評価", system_prompt)
        self.assertIn("質問、依頼、確認待ちを足さない", system_prompt)
        self.assertIn("内的注意状態や身体姿勢の細かな変化を主題化せず", system_prompt)
        self.assertIn("画面や活動の前景変化を短く述べるだけ", system_prompt)
        self.assertNotIn("decision に無い抑制理由", system_prompt)
        self.assertNotIn("集中", system_prompt)
        self.assertNotIn("没頭", system_prompt)


if __name__ == "__main__":
    unittest.main()
