from __future__ import annotations

import unittest

from otomekairo.interaction import InteractionContext, ParticipantContext
from otomekairo.llm.contexts import CurrentInput, PersonaContext
from otomekairo.service.input.decision_comparison import ServiceInputDecisionComparisonMixin
from otomekairo.service.input.pipeline import ServiceInputPipelineMixin


class ReviewLLM:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.calls: list[dict] = []

    def generate_disclosure_review(self, *, model_config: dict, review_context: dict) -> dict:
        self.calls.append(
            {
                "model_config": model_config,
                "review_context": review_context,
            }
        )
        return dict(self.result)


class DisclosureService(ServiceInputDecisionComparisonMixin, ServiceInputPipelineMixin):
    def __init__(self, llm: ReviewLLM) -> None:
        self.llm = llm


class DisclosureReviewTests(unittest.TestCase):
    def test_decision_only_conversation_is_reviewed_without_recalled_memory(self) -> None:
        llm = ReviewLLM({
            "outcome": "rewrite", "speech_text": "雨が上がりました。",
            "reason_code": "removed_other_conversation",
        })
        service = DisclosureService(llm)
        groups = [{
            "interaction_ref": "interaction:other",
            "turns": [{
                "event_id": "event:private", "role": "person",
                "speaker_ref": "person:other", "participant_refs": ["person:other"],
                "text": "他の場で交わした非公開の話。", "created_at": "2026-09-21T16:14:00+09:00",
            }],
        }]
        for current_input in (
            self._current_input("person:current"),
            CurrentInput(sender_kind="system", sender_ref=None, source_kind="background_thinking",
                         response_target_refs=(), interaction_context=None, text="自己評価。"),
        ):
            with self.subTest(sender_kind=current_input.sender_kind):
                result = service._apply_disclosure_review(
                    model_config={}, persona_context=self._persona_context(), current_input=current_input,
                    recall_pack={}, recent_interactions=groups,
                    speech_payload={"speech_text": "判断理由を経由して他の会話に触れた候補。"},
                    decision={"kind": "speech"},
                )
                self.assertEqual(result["speech_text"], "雨が上がりました。")
                self.assertEqual(result["disclosure_review"]["reviewed_source_refs"], ["event:private"])
                self.assertNotIn("非公開", str(result["disclosure_review"]))
                source = llm.calls[-1]["review_context"]["other_person_sources"][0]
                self.assertEqual(source["interaction_ref"], "interaction:other")
                self.assertEqual(source["text"], groups[0]["turns"][0]["text"])

    def test_grouped_conversation_review_failure_propagates(self) -> None:
        class FailingReview(ReviewLLM):
            def generate_disclosure_review(self, **kwargs):
                raise RuntimeError("review unavailable")

        service = DisclosureService(FailingReview({}))
        with self.assertRaisesRegex(RuntimeError, "review unavailable"):
            service._apply_disclosure_review(
                model_config={}, persona_context=self._persona_context(),
                current_input=self._current_input("person:current"), recall_pack={},
                recent_interactions=[{"interaction_ref": "interaction:other", "turns": [{
                    "event_id": "event:private", "participant_refs": ["person:other"],
                }]}],
                speech_payload={"speech_text": "候補。"}, decision={"kind": "speech"},
            )

    def test_other_person_provenance_triggers_rewrite_and_minimal_audit(self) -> None:
        llm = ReviewLLM(
            {
                "outcome": "rewrite",
                "speech_text": "一般化して答えます。",
                "reason_code": "removed_other_person_detail",
            }
        )
        service = DisclosureService(llm)
        decision = {"kind": "speech"}

        result = service._apply_disclosure_review(
            model_config={"model": "test"},
            persona_context=self._persona_context(),
            current_input=self._current_input("person:current"),
            recall_pack={
                "person_model": [
                    {
                        "memory_unit_id": "memory_unit:other-private",
                        "summary_text": "監査結果へ複写しない本文",
                        "qualifiers": {
                            "source_participant_refs": ["person:other"],
                        },
                    }
                ]
            },
            speech_payload={"speech_text": "他者の情報を含む候補です。"},
            decision=decision,
        )

        assert result is not None
        self.assertEqual(result["speech_text"], "一般化して答えます。")
        self.assertEqual(result["disclosure_review"]["outcome"], "rewrite")
        self.assertEqual(
            result["disclosure_review"]["reviewed_source_refs"],
            ["memory_unit:other-private"],
        )
        self.assertNotIn("監査結果へ複写しない本文", str(result["disclosure_review"]))
        self.assertEqual(len(llm.calls), 1)
        self.assertIn("persona_context", llm.calls[0]["review_context"])
        self.assertNotIn("expression_addon", llm.calls[0]["review_context"]["persona_context"])

    def test_current_person_provenance_does_not_add_review_call(self) -> None:
        llm = ReviewLLM(
            {
                "outcome": "allow",
                "speech_text": "未使用",
                "reason_code": "unused",
            }
        )
        service = DisclosureService(llm)
        speech_payload = {"speech_text": "そのまま返します。"}

        result = service._apply_disclosure_review(
            model_config={},
            persona_context=self._persona_context(),
            current_input=self._current_input("person:current"),
            recent_interactions=[{
                "interaction_ref": "interaction:current",
                "turns": [{"event_id": "event:current", "participant_refs": ["person:current"]}],
            }],
            recall_pack={
                "person_model": [
                    {
                        "memory_unit_id": "memory_unit:current",
                        "source_participant_refs": ["person:current"],
                    }
                ]
            },
            speech_payload=speech_payload,
            decision={"kind": "speech"},
        )

        self.assertIs(result, speech_payload)
        self.assertEqual(llm.calls, [])

    def test_withhold_turns_the_decision_into_noop(self) -> None:
        llm = ReviewLLM(
            {
                "outcome": "withhold",
                "speech_text": None,
                "reason_code": "private_other_person_detail",
            }
        )
        service = DisclosureService(llm)
        decision = {"kind": "speech"}

        result = service._apply_disclosure_review(
            model_config={},
            persona_context=self._persona_context(),
            current_input=self._current_input("person:current"),
            recall_pack={
                "episodic_evidence": [
                    {
                        "episode_id": "episode:other",
                        "source_participant_refs": ["person:other"],
                    }
                ]
            },
            speech_payload={"speech_text": "配送してはいけない候補"},
            decision=decision,
        )

        self.assertIsNone(result)
        self.assertEqual(decision["kind"], "noop")
        self.assertEqual(decision["reason_code"], "disclosure_review_withheld")

    def test_withhold_keeps_self_activity_when_comparisons_are_separated(self) -> None:
        llm = ReviewLLM(
            {
                "outcome": "withhold",
                "speech_text": None,
                "reason_code": "private_other_person_detail",
            }
        )
        service = DisclosureService(llm)
        decision = {
            "reason_summary": "外向き伝達: 一言。 自身の活動: 関わる。",
            "target_stances": [
                {"target": "outward_speech", "stance": "advance", "reason_summary": "一言。"},
                {"target": "self_activity", "stance": "advance", "reason_summary": "関わる。"},
            ],
            "separated_comparisons": {
                "self_activity": {
                    "kind": "autonomous_run",
                    "reason_summary": "関わる。",
                    "autonomous_run": {"objective_summary": "その関心に関わる"},
                },
                "outward_speech": {
                    "kind": "speech",
                    "reason_summary": "一言。",
                },
            },
        }

        result = service._apply_disclosure_review(
            model_config={},
            persona_context=self._persona_context(),
            current_input=self._current_input("person:current"),
            recall_pack={
                "episodic_evidence": [
                    {
                        "episode_id": "episode:other",
                        "source_participant_refs": ["person:other"],
                    }
                ]
            },
            speech_payload={"speech_text": "配送してはいけない候補"},
            decision=decision,
        )

        self.assertIsNone(result)
        self.assertNotIn("kind", decision)
        self.assertEqual(
            decision["separated_comparisons"]["self_activity"]["autonomous_run"]["objective_summary"],
            "その関心に関わる",
        )
        self.assertEqual(decision["separated_comparisons"]["outward_speech"]["kind"], "noop")
        self.assertEqual(
            decision["separated_comparisons"]["outward_speech"]["reason_code"],
            "disclosure_review_withheld",
        )
        targets = {item["target"]: item["stance"] for item in decision["target_stances"]}
        self.assertEqual(targets["outward_speech"], "hold")
        self.assertEqual(targets["self_activity"], "advance")

    def _persona_context(self) -> PersonaContext:
        return PersonaContext(
            display_name="test",
            initiative_baseline={"level": "medium", "summary_text": "中庸"},
            persona_prompt_text="テスト人格。",
            expression_addon=None,
            use_policy="書き換えの距離感と言い回しの補助に使う。開示可否と候補集合を変えない。",
        )

    def _current_input(self, person_ref: str) -> CurrentInput:
        return CurrentInput(
            sender_kind="person",
            sender_ref=person_ref,
            source_kind="user_message",
            response_target_refs=(person_ref,),
            interaction_context=InteractionContext(
                interaction_ref=f"interaction:{person_ref}",
                speaker_ref=person_ref,
                participants=(ParticipantContext(person_ref=person_ref, display_name="田中"),),
            ),
            text="質問",
        )


if __name__ == "__main__":
    unittest.main()
