from __future__ import annotations

import unittest

from otomekairo.interaction import InteractionContext, ParticipantContext
from otomekairo.llm.contexts import CurrentInput
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
            current_input=self._current_input("person:current"),
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
            "reason_summary": "外向き伝達: 一言。 自身の活動: 場へ行く。",
            "target_stances": [
                {"target": "outward_speech", "stance": "advance", "reason_summary": "一言。"},
                {"target": "self_activity", "stance": "advance", "reason_summary": "場へ行く。"},
            ],
            "separated_comparisons": {
                "self_activity": {
                    "kind": "autonomous_run",
                    "reason_summary": "場へ行く。",
                    "autonomous_run": {"objective_summary": "場を見る"},
                },
                "outward_speech": {
                    "kind": "speech",
                    "reason_summary": "一言。",
                },
            },
        }

        result = service._apply_disclosure_review(
            model_config={},
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
            "場を見る",
        )
        self.assertEqual(decision["separated_comparisons"]["outward_speech"]["kind"], "noop")
        self.assertEqual(
            decision["separated_comparisons"]["outward_speech"]["reason_code"],
            "disclosure_review_withheld",
        )
        targets = {item["target"]: item["stance"] for item in decision["target_stances"]}
        self.assertEqual(targets["outward_speech"], "hold")
        self.assertEqual(targets["self_activity"], "advance")

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
