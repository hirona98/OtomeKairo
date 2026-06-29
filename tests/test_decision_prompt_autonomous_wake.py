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
            speech_timing_state={"background_trigger": True},
            suppression_summary={
                "suppression_level": "low",
                "visual_repetition_present": False,
            },
            speech_timing_summary="",
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

        system_fragments = (
            "現在の個として関わる、保留する、見送る、能力を使う",
            "noop は前へ出ない判断",
            "意味レイヤー境界",
            "内部処理は次の意味レイヤー",
            "観測可能な活動事実を何が前景にあるかの説明",
            "noop は構造化済みの抑制根拠",
            "自己申告された注意状態は、ユーザー発話の内容",
            "外向き発話の抑制は行動判断層",
            "活動事実として表現",
            "現在活動ラベルは活動事実として表現",
            "noop 理由は、反復抑制、直近で触れた内容",
            "活動事実は noop の根拠名ではなく",
            "抑制根拠は、明示的なユーザー発話",
            "観測不足、構造化済み抑制根拠",
            "活動名、作業名、閲覧中、検討中、入力中、操作中などの活動事実",
            "注意状態の推定、距離感の補助",
            "前景説明または補助材料として扱ってください",
            "観測から集中や没頭を推定して",
            "判断理由にしない",
            "短い状況認識として閉じる speech",
            "支援提案とは別の軽い外向き行動",
            "response_target=none の短い独り言",
            "会話継続や相手の反応を前提にしない",
            "観測差分、活動継続、画面変化は speech を義務づけません",
            "現在の個の短い見方として一言にまとまる場合は speech と比較",
        )
        context_fragments = (
            "定期思考による自己評価",
            "控える理由の材料",
            "background_thinking: 定期思考による自己評価",
            "現在の個の短い見方として一言にまとまる独り言",
            "短い独話として前へ出る自然さを 10 段階",
            "発話頻度レベル 7",
            "5 は標準",
            "3 以下は控えめ基準",
            "観測差分、thin、stable、changed、同一活動継続",
            "speech を義務づけません",
            "短い一言として自然にまとまる場合は speech と比較",
            "評価値は JSON や reason_summary に出力しない",
            "材料: visual_observations",
            "選択: speech",
            "同一活動内の扱い",
            "発話境界: speech",
            "抑制境界: noop を選ぶ場合",
            "change_state=first_seen / changed は前景候補",
            "stable は現在状態の継続シグナル",
            "same_as_recent_speech は直近重複の抑制候補",
            "first_seen / changed / stable は",
            "外界を理解するための観測事実",
            "同一活動内の画面・表示対象・操作単位の変化は",
            "speech / pending_intent / noop を比較する材料",
            "活動名、作業名、閲覧中、検討中、入力中、操作中などの活動事実",
            "何が前景にあるかの材料",
            "活動事実だけを speech の主理由にしない",
            "foreground_signal_summary.foreground_thinness=thin の同じ活動モード内の対象差し替え",
            "表示単位の移動、閲覧先変更、詳細画面への移動",
            "実況にはせず",
            "現在の個の短い見方や区切りとしてまとまる場合だけ speech と比較",
            "操作媒体、対象種別、身体動作の組み合わせが",
            "同じ活動モード内の対象差し替えでは説明できないほど変わる場合",
            "活動モードや状態の上位変化としても比較",
            "反復実況を避けつつ",
            "軽い節目として一言にまとまるかを比較",
            "現在の観測、活動の継続、変化、安定、切り替わり",
            "現在の個の短い見方として一言にまとまるときに選びます",
            "speech は会話開始ではなく、反応要求を含まない短い独り言として比較",
            "あとで再評価する材料だけを残す",
            "直近で同じ内容に触れた事実",
            "構造化済み抑制根拠がある場合",
            "短い独話として一言にまとまらない場合",
            "foreground_signal_summary.foreground_thinness=thin は自動 speech にしない",
            "軽い節目としてまとまる場合は speech と比較",
            "stable や同一活動継続は自動 speech にしない",
            "継続そのものに現在の個の短い見方が立つ場合は speech と比較",
            "noop の reason_summary は、該当する具体根拠名",
            "活動事実や距離感の補助だけを主理由にしない",
            "プライバシー境界、観測失敗、観測不足",
            "candidate_families に capability 提案",
            "同一活動内の画面・表示対象・操作単位の変化",
            "作業や閲覧の継続、安定状態は現在状態の材料",
            "foreground_signal_summary.foreground_thinness=thin",
            "noop を選ぶ場合は、明示された距離希望、直近重複",
            "独話としてまとまらないこと",
            "補助だけを reason_summary の主理由にしない",
            "観測事実に基づく一文の独話的な状況認識として作ってください",
            "助言、依頼、支援提案、反応要求ではなく",
            "background_thinking の speech は独り言として扱い",
            "相手の反応や会話継続を前提にしない",
            "PersonaContext は距離感と表現補助",
            "観測にない内容を speech に押し上げない",
            "drive_state は speech の補助材料",
            "freshness_hint=stale、stability_hint=weak、signal_strength=0.0",
            "薄い視覚前景と合わせる場合も補助材料",
            "反復に近い詳細更新",
            "同一活動内の画面・表示対象・操作単位の小さな変化",
            "観測対象の表層的な変化",
            "同じ活動モード内の対象名や表示内容だけの差し替え",
            "同じ活動モード内の対象差し替えでは説明できないほど変わる場合は、この抑制理由に含めない",
            "same_activity_detail_change は同じ活動モード内の詳細変化",
            "一般的な注意や助言に留まる内容",
            "自動 speech にせず、軽い節目としてまとまる場合だけ speech と比較",
            "直近発話との重複や独話としてまとまらないことが問題なら noop",
            "活動が継続中であることだけで speech を選ばず",
            "継続への短い見方が立つ場合は speech と比較",
        )
        for fragment in system_fragments:
            self.assertIn(fragment, system_prompt)
        for fragment in context_fragments:
            self.assertIn(fragment, context_prompt)
        self.assertNotIn("独話として外へ出す新しい意味が弱い", context_prompt)
        self.assertNotIn("speech の価値を明確に上回る", context_prompt)
        self.assertNotIn("具体的な抑制根拠が上回らない", context_prompt)
        self.assertNotIn("外へ出る必要が薄い", context_prompt)
        self.assertNotIn("外へ出す必要性がないこと", context_prompt)
        self.assertNotIn("発話すべき事由", context_prompt)
        self.assertNotIn("言及すべきほど", context_prompt)
        self.assertNotIn("意味ある区切り", context_prompt)
        self.assertNotIn("意味ある進行変化", context_prompt)
        self.assertNotIn("後で再評価する価値", context_prompt)
        self.assertNotIn("独り言の主題が安定しない", context_prompt)
        self.assertNotIn("speech 候補にしない", context_prompt)
        self.assertNotIn("それ単体では noop", context_prompt)
        self.assertNotIn("支援要求の有無、明示的な呼びかけの有無", system_prompt)
        self.assertNotIn("外向き発話の抑制根拠になりません", system_prompt)
        self.assertNotIn("支援要求がないこと", context_prompt)
        self.assertNotIn("明示的な呼びかけがないこと", context_prompt)
        self.assertNotIn("一文で言い切れる前景差分があれば speech と比較", context_prompt)
        self.assertNotIn("thin、stable、changed の観測も現在状態の材料", context_prompt)
        self.assertNotIn("具体的な抑制根拠がなければ短い独り言として speech と比較", context_prompt)
        self.assertNotIn("foreground_signal_summary.foreground_thinness=thin は noop 固定ではありません", context_prompt)
        self.assertNotIn("stable や同一活動継続は noop 固定ではありません", context_prompt)
        self.assertNotIn("具体名を主題化しない短い独り言の材料", context_prompt)
        self.assertNotIn("現在の個として外へ出す意味が成立", context_prompt)
        self.assertNotIn("外へ出す意味が成立しない限り内部理解", context_prompt)
        self.assertNotIn("観測差分だけでは外へ出す意味", context_prompt)
        self.assertNotIn("外へ出す意味の弱さ", context_prompt)
        self.assertNotIn("閲覧対象や比較軸の深まり", context_prompt)
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
        self.assertNotIn("割り込み", combined)
        self.assertNotIn("intervention", combined)
        self.assertNotIn("介入", combined)
        self.assertNotIn("支援必要性", combined)
        self.assertNotIn("会話開始としての必要性", combined)
        self.assertNotIn("発話するほど", combined)
        self.assertNotIn("必然性", combined)
        self.assertNotIn("邪魔", combined)
        self.assertNotIn("遠慮", combined)
        self.assertNotIn("中断してまで", combined)
        self.assertNotIn("介入してまで", combined)
        self.assertNotIn("発話の必要性", combined)
        self.assertNotIn("没入を妨げ", combined)
        self.assertNotIn("静かな見送り", combined)

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
        self.assertIn("操作媒体、対象種別、身体動作の組み合わせが", system_prompt)
        self.assertIn("同じ活動モード内の対象差し替えでは説明できないほど変わる場合", system_prompt)
        self.assertIn("same_activity_detail_change に分類しない", system_prompt)
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
        self.assertIn("具体的な固有名、表示対象名、作品名、ページ内容", system_prompt)
        self.assertIn("内的注意状態や身体姿勢の細かな変化も主題化しない", system_prompt)
        self.assertIn("抽象的な区切りや切り替わりを短く述べるだけ", system_prompt)
        self.assertNotIn("decision に無い抑制理由", system_prompt)
        self.assertNotIn("集中", system_prompt)
        self.assertNotIn("没頭", system_prompt)


if __name__ == "__main__":
    unittest.main()
