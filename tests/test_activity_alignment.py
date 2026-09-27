from __future__ import annotations

import unittest
from dataclasses import replace

from otomekairo.llm.contexts import CurrentInput, DecisionContext
from otomekairo.llm.contracts import LLMError
from otomekairo.service.input.decision_comparison import ServiceInputDecisionComparisonMixin


def _context() -> DecisionContext:
    return DecisionContext(
        input_text="自己評価。",
        current_input=CurrentInput(
            sender_kind="system", sender_ref=None, source_kind="background_thinking",
            response_target_refs=(), interaction_context=None, text="自己評価。",
        ),
        trigger_kind="background_thinking",
        recent_turns=[], time_context={}, affect_context={}, drive_state_summary=None,
        foreground_world_state=None, activity_context=None, ongoing_action_summary=None,
        autonomous_run_summaries=None, capability_decision_view=None, initiative_context=None,
        capability_result_context=None, visual_observation_context=None, self_state_context=None,
        relationship_context=None, prediction_error_context=None, default_mode_context=None,
        workspace_context={"workspace_candidates": [
            {"factor_ref": "periodic_thought_topic:elyth", "kind": "periodic_thought_topic",
             "summary_text": "ELYTH の状態を見て関わる。"},
            {"factor_ref": "periodic_thought_topic:other", "kind": "periodic_thought_topic",
             "summary_text": "別の場で活動する。"},
            {"factor_ref": "drive_state:master", "kind": "drive_state",
             "summary_text": "マスターとの関係を大切にする。",
             "metadata": {"focus_scope_type": "person", "focus_scope_key": "person:master"}},
        ]},
        recall_hint={}, recall_pack={}, comparison_scope="self_activity",
    )


def _decision(kind: str) -> dict:
    return {
        "kind": kind,
        "reason_summary": "活動について判断する。",
        "foreground_selection": {
            "primary_factor_ref": "periodic_thought_topic:elyth",
            "supporting_factor_refs": ["drive_state:master"],
        },
        "autonomous_run": {"objective_summary": "ELYTH を確認する。"} if kind == "autonomous_run" else None,
    }


class _LLM:
    def __init__(self, decisions: list[dict], reviews: list[str]) -> None:
        self.decisions = list(decisions)
        self.reviews = list(reviews)
        self.decision_contexts: list[DecisionContext] = []
        self.review_contexts: list[dict] = []

    def generate_decision(self, *, model_config, persona_context, context):
        _ = model_config, persona_context
        self.decision_contexts.append(context)
        return self.decisions.pop(0)

    def generate_autonomous_activity_alignment_review(self, *, model_config, review_context):
        _ = model_config
        self.review_contexts.append(review_context)
        return {"outcome": self.reviews.pop(0), "reason_summary": "活動との関係を確認した。"}


class _Service(ServiceInputDecisionComparisonMixin):
    def __init__(self, llm: _LLM) -> None:
        self.llm = llm

    def _build_self_activity_decision_context(self, **kwargs):
        _ = kwargs
        return _context()

    def _build_outward_speech_decision_context(self, **kwargs):
        _ = kwargs
        return replace(_context(), comparison_scope="outward_speech")

    def _decision_kind_log(self, decision):
        return str(decision.get("separated_comparisons", {}).get("self_activity", {}).get("kind"))

    def _clamp(self, value, limit=240):
        _ = limit
        return value


class ActivityAlignmentTests(unittest.TestCase):
    def _run(self, llm: _LLM):
        return _Service(llm)._run_separated_activity_decisions(
            model_config={}, persona_context=None, cycle_label="test",
        )

    def test_run_is_reviewed_once_with_primary_and_supporting_scopes(self) -> None:
        llm = _LLM([_decision("autonomous_run"), _decision("noop")], ["allow"])
        result = self._run(llm)
        self.assertEqual(len(llm.decision_contexts), 2)
        self.assertEqual(len(llm.review_contexts), 1)
        review = llm.review_contexts[0]
        self.assertEqual(review["primary_candidate"]["kind"], "periodic_thought_topic")
        self.assertEqual(review["supporting_candidates"][0]["scopes"]["focus_scope_key"], "person:master")
        self.assertEqual(len(review["periodic_thought_topics"]), 1)
        self.assertEqual(review["periodic_thought_topics"][0]["factor_ref"], "periodic_thought_topic:elyth")
        self.assertEqual(result["activity_alignment_reviews"][0]["outcome"], "allow")

    def test_rejected_run_redecides_self_without_reviewing_noop(self) -> None:
        llm = _LLM(
            [_decision("autonomous_run"), _decision("noop"), _decision("noop")],
            ["reject"],
        )
        result = self._run(llm)
        self.assertEqual(len(llm.decision_contexts), 3)
        self.assertEqual(len(llm.review_contexts), 1)
        self.assertIsNotNone(llm.decision_contexts[1].activity_alignment_feedback)
        self.assertEqual(result["separated_comparisons"]["self_activity"]["kind"], "noop")

    def test_rejected_run_redecides_self_without_reviewing_capability(self) -> None:
        llm = _LLM(
            [_decision("autonomous_run"), _decision("capability_request"), _decision("noop")],
            ["reject"],
        )
        result = self._run(llm)
        self.assertEqual(len(llm.review_contexts), 1)
        self.assertEqual(result["separated_comparisons"]["self_activity"]["kind"], "capability_request")

    def test_second_rejection_stops_before_outward_decision(self) -> None:
        llm = _LLM([_decision("autonomous_run"), _decision("autonomous_run")], ["reject", "reject"])
        with self.assertRaises(LLMError):
            self._run(llm)
        self.assertEqual(len(llm.decision_contexts), 2)
        self.assertEqual(len(llm.review_contexts), 2)

    def test_initial_noop_needs_no_review(self) -> None:
        llm = _LLM([_decision("noop"), _decision("noop")], [])
        self._run(llm)
        self.assertEqual(len(llm.review_contexts), 0)
        self.assertEqual(len(llm.decision_contexts), 2)


if __name__ == "__main__":
    unittest.main()
