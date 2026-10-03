from __future__ import annotations

import unittest
from unittest.mock import patch

from otomekairo.llm.client import LLMClient
from otomekairo.llm.contracts import LLMError, validate_state_grounding_review_contract
from otomekairo.llm.schemas import state_grounding_review_response_format


class StateGroundingReviewTests(unittest.TestCase):
    def test_provider_schema_and_validator_use_the_same_evidence_kinds(self) -> None:
        schema = state_grounding_review_response_format()["json_schema"]["schema"]
        kinds = schema["properties"]["decisions"]["items"]["properties"]["evidence_kind"]["enum"]
        for kind in kinds:
            validate_state_grounding_review_contract({"decisions": [{"index": 0, "evidence_kind": kind, "reason_summary": "対応する根拠。"}]}, candidate_count=1)
    def test_camera_observation_requires_matching_person_reference(self) -> None:
        client = LLMClient()
        candidate = {"activity_candidates": [{"actor": "person"}]}
        review = {"decisions": [{"index": 0, "evidence_kind": "supported_observation", "reason_summary": "画像の動作。"}]}
        for refs, expected in (([], []), (["person:test"], candidate["activity_candidates"])):
            with self.subTest(refs=refs), patch.object(LLMClient, "_generate_structured_payload", return_value=review):
                result = client._review_state_candidates(
                    model_config={"model": "test"}, state_kind="activity_state",
                    source_pack={"source_owner": "self", "activity_subject": {"actor_ref": "person:test"}, "observed_person_refs": refs},
                    candidate=candidate, candidate_key="activity_candidates",
                )
            self.assertEqual(result, {"activity_candidates": expected})

    def test_only_reviewed_supported_candidates_reach_state_storage(self) -> None:
        client = LLMClient()
        candidate = {"state_candidates": [{"candidate_ref": "state_source:environment"}, {"candidate_ref": "state_source:location"}]}
        review = {"decisions": [
            {"index": 1, "evidence_kind": "supported_report", "reason_summary": "本人の報告。"},
            {"index": 0, "evidence_kind": "question", "reason_summary": "根拠なし。"},
        ]}
        with patch.object(LLMClient, "_generate_structured_payload", return_value=review) as generate:
            result = client._review_state_candidates(
                model_config={"model": "test"}, state_kind="world_state", source_pack={},
                candidate=candidate, candidate_key="state_candidates",
            )
        self.assertEqual(result, {"state_candidates": [candidate["state_candidates"][1]]})
        kwargs = generate.call_args.kwargs
        self.assertEqual(kwargs["response_format"], state_grounding_review_response_format())
        kwargs["validator"](review)
        with self.assertRaises(LLMError):
            kwargs["validator"]({"decisions": [review["decisions"][0]]})

    def test_empty_candidates_need_no_review_call(self) -> None:
        client = LLMClient()
        with patch.object(LLMClient, "_generate_structured_payload") as generate:
            self.assertEqual(client._review_state_candidates(
                model_config={"model": "test"}, state_kind="activity_state", source_pack={},
                candidate={"activity_candidates": []}, candidate_key="activity_candidates",
            ), {"activity_candidates": []})
        generate.assert_not_called()

    def test_duplicate_review_indices_fail(self) -> None:
        with self.assertRaises(LLMError):
            validate_state_grounding_review_contract({"decisions": [
                {"index": 0, "evidence_kind": "supported_report", "reason_summary": "根拠あり。"},
                {"index": 0, "evidence_kind": "question", "reason_summary": "根拠なし。"},
            ]}, candidate_count=2)
